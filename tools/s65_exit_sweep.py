#!/usr/bin/env python3
"""S65 (funding carry) exit parameter sweep.

Phase 1 of s65 exit optimization mission:
  Sweep trail_mult values (1.5, 2.0, 2.5, 3.0, 3.5, 4.0) specifically for s65,
  measuring impact on s65 solo, s72 (time_trail wrapper), s58+s65, s58+s72.

s65 previously had trail_mult=4.0 (widest of any strategy). The universal
trail(1.5) deployment massively improved all-time return (+532K%) but worsened
max DD (-4.7%→-6.7%) and hurt 1-month returns (-21%).

Hypothesis: Funding carry has fundamentally different exit requirements than
momentum. Carry trades accumulate steady income — wider trails let funding
payments accumulate. The 1.5 ATR trail may be cutting carry trades before
they collect enough funding to justify the position.

Also tests progressive trail schedules for carry strategies, since s65
previously used [[0,3.0],[1,2.5],[2,2.0],[3,1.5]].
"""
import gc
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


@dataclass
class S65ExitConfig:
    label: str
    trail_mult_override: float = 0.0   # 0 = don't override
    trail_schedule: Optional[np.ndarray] = None  # progressive schedule array
    # Only override trail for s65 strategy (not s58 in combos)
    target_strategy: str = "s65"


# =========================================================================
# SWEEP CONFIGS — Phase 1: trail_mult values
# =========================================================================
CONFIGS = [
    # Current (1.5 flat, no schedule)
    S65ExitConfig("trail(1.5) [current]", trail_mult_override=1.5),

    # Wider flat trails (carry needs room)
    S65ExitConfig("trail(2.0)", trail_mult_override=2.0),
    S65ExitConfig("trail(2.5)", trail_mult_override=2.5),
    S65ExitConfig("trail(3.0)", trail_mult_override=3.0),
    S65ExitConfig("trail(3.5)", trail_mult_override=3.5),
    S65ExitConfig("trail(4.0) [old]", trail_mult_override=4.0),

    # Progressive schedules (carry may benefit from gradual tightening)
    S65ExitConfig("prog: 3.0→1.5",
                  trail_schedule=np.array([[0, 3.0], [1, 2.5], [2, 2.0], [3, 1.5]], dtype=np.float64)),
    S65ExitConfig("prog: 4.0→2.0",
                  trail_schedule=np.array([[0, 4.0], [1, 3.5], [2, 3.0], [3, 2.5], [4, 2.0]], dtype=np.float64)),
    S65ExitConfig("prog: 3.0→2.0",
                  trail_schedule=np.array([[0, 3.0], [1, 2.5], [2, 2.0]], dtype=np.float64)),
]

# Portfolios to test (s65_solo is synthetic — not in config)
TARGET_PORTFOLIOS = ["s65_solo", "s72", "s58+s65", "s58+s72"]


def load_portfolios_from_config(config_path: str):
    with open(config_path) as f:
        cfg = json.load(f)

    portfolios = {}
    for pf in cfg["portfolios"]:
        name = pf["pool_name"]
        strategies = []
        for s in pf["strategies"]:
            spec = StrategySpec(
                strategy_id=s["strategy_id"],
                weight=s.get("weight", 1.0),
                max_positions=s.get("max_positions", 15),
                market=s.get("market", "perp"),
                strategy_type=s.get("strategy_type", "per_token"),
            )
            strategies.append(spec)
        portfolios[name] = {
            "strategies": strategies,
            "max_portfolio_positions": pf.get("max_portfolio_positions", 40),
            "concentration_limit": pf.get("concentration_limit", 1.0),
        }
    return portfolios


def apply_s65_exit_config(signals_dict, ec: S65ExitConfig):
    """Apply exit overrides ONLY to s65 signals (not s58 in combos)."""
    saved = []
    for token, sig in signals_dict.items():
        orig = {
            "trail_mult": sig.trail_mult.copy() if isinstance(sig.trail_mult, np.ndarray) else sig.trail_mult,
            "trail_schedule": sig.trail_schedule,
        }
        saved.append((token, orig))

        # Apply trail_mult override
        if ec.trail_mult_override > 0:
            sig.trail_mult = np.full_like(sig.trail_mult, ec.trail_mult_override)

        # Apply trail schedule
        if ec.trail_schedule is not None:
            sig.trail_schedule = ec.trail_schedule
        else:
            sig.trail_schedule = None

    return saved


def restore_signals(signals_dict, saved):
    """Restore original signal values."""
    for token, orig in saved:
        sig = signals_dict[token]
        sig.trail_mult = orig["trail_mult"]
        sig.trail_schedule = orig["trail_schedule"]


def run_portfolio_sweep(pf_name, pf_def, period, data_end, all_results):
    """Run all configs for one portfolio and one period."""
    strategies = pf_def["strategies"]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=pf_def["max_portfolio_positions"],
        concentration_limit=pf_def["concentration_limit"],
    )

    # Identify which strategy IDs are s65-based (to apply overrides only there)
    s65_sids = [s.strategy_id for s in strategies
                if s.strategy_id in ("s65", "s72")]  # s72 wraps s65

    # Precompute signals once
    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, period, end_date=data_end)
        all_precomputed[spec.strategy_id] = signals

    strats = ", ".join(s.strategy_id for s in strategies)
    print(f"\n{'='*120}")
    print(f"  {period}-MONTH — {pf_name}")
    print(f"  Strategies: {strats}  (s65-based: {', '.join(s65_sids)})")
    print(f"{'='*120}")
    print(f"  {'Config':<22s}  {'ret':>10s}  {'DD':>8s}  {'calmar':>9s}  "
          f"{'sharpe':>7s}  {'sortino':>7s}  {'trades':>6s}  {'wr':>6s}  {'PF':>5s}  {'time':>5s}")
    print(f"  {'-'*22}  {'-'*10}  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*5}")
    sys.stdout.flush()

    strategy_specs = {s.strategy_id: s for s in strategies}

    for ec in CONFIGS:
        t0 = time.time()
        saved_all = {}
        try:
            # Apply overrides ONLY to s65-based strategies
            for sid in s65_sids:
                if sid in all_precomputed:
                    saved_all[sid] = apply_s65_exit_config(all_precomputed[sid], ec)

            # Simulate
            state = simulate_portfolio(all_precomputed, strategy_specs, config)
            metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

            # Restore
            for sid, saved in saved_all.items():
                restore_signals(all_precomputed[sid], saved)

            elapsed = time.time() - t0
            r = {
                "portfolio": pf_name,
                "label": ec.label,
                "months": period,
                "return_pct": metrics.total_return_pct,
                "max_dd_pct": metrics.max_drawdown_pct,
                "calmar": metrics.calmar_ratio,
                "sharpe": metrics.sharpe_ratio,
                "sortino": metrics.sortino_ratio,
                "trades": metrics.total_trades,
                "win_rate": metrics.win_rate_pct,
                "profit_factor": metrics.profit_factor,
            }
            all_results.append(r)
            tag = " <<" if "current" in ec.label else ""
            tag += " [OLD]" if "old" in ec.label else ""
            print(f"  {ec.label:<22s}  {r['return_pct']:+9.1f}%  "
                  f"{r['max_dd_pct']:+7.2f}%  {r['calmar']:9.2f}  "
                  f"{r['sharpe']:7.2f}  {r['sortino']:7.2f}  "
                  f"{r['trades']:6d}  {r['win_rate']:5.1f}%  "
                  f"{r['profit_factor']:5.2f}  "
                  f"{elapsed:4.1f}s{tag}")
        except Exception as e:
            for sid, saved in saved_all.items():
                restore_signals(all_precomputed[sid], saved)
            print(f"  {ec.label:<22s}  ERROR: {e}")
            import traceback; traceback.print_exc()
        sys.stdout.flush()

    del all_precomputed
    gc.collect()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="S65 exit parameter sweep")
    parser.add_argument("--periods", nargs="+", type=int, default=[60, 12, 3, 1],
                        help="Months to test (default: 60 12 3 1)")
    parser.add_argument("--portfolios", nargs="+", default=None,
                        help="Portfolio names (default: s65 s72 s58+s65 s58+s72)")
    args = parser.parse_args()

    periods = args.periods
    config_path = PROJECT_ROOT / "configs" / "multi_v4_paper.json"
    all_portfolios = load_portfolios_from_config(str(config_path))

    # Create synthetic s65_solo portfolio (s65 not in config as standalone)
    all_portfolios["s65_solo"] = {
        "strategies": [
            StrategySpec(strategy_id="s65", weight=1.0, max_positions=15, market="perp"),
        ],
        "max_portfolio_positions": 15,
        "concentration_limit": 1.0,
    }

    if args.portfolios:
        portfolios = {k: v for k, v in all_portfolios.items() if k in args.portfolios}
    else:
        portfolios = {k: v for k, v in all_portfolios.items() if k in TARGET_PORTFOLIOS}

    missing = [p for p in (args.portfolios or TARGET_PORTFOLIOS) if p not in all_portfolios]
    if missing:
        print(f"WARNING: portfolios not found in config: {missing}")
        print(f"Available: {sorted(all_portfolios.keys())}")

    total_runs = len(portfolios) * len(CONFIGS) * len(periods)
    print(f"S65 EXIT PARAMETER SWEEP")
    print(f"=" * 60)
    print(f"Testing {len(portfolios)} portfolios x {len(CONFIGS)} configs x {len(periods)} periods = {total_runs} runs")
    print(f"Portfolios: {', '.join(portfolios.keys())}")
    print(f"Periods: {periods}")
    print(f"\nConfigs:")
    for i, ec in enumerate(CONFIGS):
        tm = f" trail={ec.trail_mult_override}" if ec.trail_mult_override > 0 else ""
        ts = f" schedule={ec.trail_schedule.tolist()}" if ec.trail_schedule is not None else ""
        print(f"  {i+1:2d}. {ec.label:<22s}{tm}{ts}")
    sys.stdout.flush()

    data_end = infer_data_end_date("combined")
    all_results = []

    for period in periods:
        for pf_name, pf_def in portfolios.items():
            try:
                run_portfolio_sweep(pf_name, pf_def, period, data_end, all_results)
            except Exception as e:
                print(f"\n  PORTFOLIO ERROR {pf_name} {period}mo: {e}")
                import traceback; traceback.print_exc()
            gc.collect()

    # =========================================================================
    # RANKINGS
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  RANKINGS BY CALMAR (per portfolio per period)")
    print(f"{'#'*120}")

    for period in periods:
        for pf_name in portfolios:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            if not pf_results:
                continue
            current_cal = next((r["calmar"] for r in pf_results if "current" in r["label"]), 0)

            print(f"\n  --- {pf_name} ({period}mo) ---")
            for i, r in enumerate(pf_results):
                delta = r["calmar"] - current_cal
                marker = " <<" if "current" in r["label"] else ""
                marker += " [OLD]" if "old" in r["label"] else ""
                marker += " [PROG]" if "prog:" in r["label"] else ""
                print(f"    {i+1}. {r['label']:<22s}  cal={r['calmar']:9.2f} "
                      f"(Δ={delta:+8.2f})  ret={r['return_pct']:+10.1f}%  "
                      f"DD={r['max_dd_pct']:+7.2f}%  PF={r['profit_factor']:5.2f}{marker}")

    # =========================================================================
    # GRAND SUMMARY: Best config per portfolio (harmonic rank)
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  RECOMMENDATION — Best trail_mult for s65 (harmonic rank score)")
    print(f"{'#'*120}")

    for pf_name in portfolios:
        config_scores = {}
        for period in periods:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            for rank, r in enumerate(pf_results):
                label = r["label"]
                if label not in config_scores:
                    config_scores[label] = {"score": 0.0, "cals": {}, "rets": {}, "dds": {}}
                config_scores[label]["score"] += 1.0 / (rank + 1)
                config_scores[label]["cals"][period] = r["calmar"]
                config_scores[label]["rets"][period] = r["return_pct"]
                config_scores[label]["dds"][period] = r["max_dd_pct"]

        best = sorted(config_scores.items(), key=lambda x: -x[1]["score"])
        cur_score = config_scores.get("trail(1.5) [current]", {}).get("score", 0)

        print(f"\n  --- {pf_name} ---")
        print(f"  {'Config':<22s}  {'score':>6s}", end="")
        for p in periods:
            print(f"  {p:>3d}mo_cal", end="")
        print(f"  {'Δ_score':>8s}")

        for label, data in best:
            delta = data["score"] - cur_score
            marker = " <<" if "current" in label else ""
            print(f"  {label:<22s}  {data['score']:6.2f}", end="")
            for p in periods:
                cal = data["cals"].get(p, 0)
                print(f"  {cal:>9.2f}", end="")
            print(f"  {delta:+8.2f}{marker}")

    # =========================================================================
    # Save results
    # =========================================================================
    out_path = PROJECT_ROOT / "results" / "v4" / "s65_exit_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Convert numpy types to Python native for JSON
    clean_results = []
    for r in all_results:
        clean_results.append({k: float(v) if hasattr(v, 'item') else v for k, v in r.items()})
    with open(out_path, "w") as f:
        json.dump(clean_results, f, indent=2)
    print(f"\nResults saved to {out_path}")
