#!/workspace/venv/bin/python
"""Exit parameter sweep — wide range for corrected simulator (bar-close exits).

Tests wider stop_mult, trail_mult, and breakeven_atr combinations to find
optimal parameters now that exits happen at bar close (not trigger price).

The previous exit ablation was tuned on the buggy simulator where stops
executed at trigger prices. With bar-close exits, wider stops/trails may
reduce the frequency of adverse bar-close slippage.
"""
import gc
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


@dataclass
class ExitConfig:
    label: str
    stop_mult_override: float = 0.0   # 0 = keep strategy default
    trail_mult_override: float = 0.0  # 0 = keep strategy default
    breakeven_atr: float = 0.5
    trail_schedule: bool = False      # False = no progressive schedule


# Wide parameter sweep: stop_mult × trail_mult × breakeven_atr
CONFIGS = [
    # === Baseline (current deployed) ===
    ExitConfig("s3 t1.5 BE0.5",    stop_mult_override=3.0, trail_mult_override=1.5, breakeven_atr=0.5),

    # === Wide trail, keep stop ===
    ExitConfig("s3 t3.0 noBE",     stop_mult_override=3.0, trail_mult_override=3.0, breakeven_atr=0.0),
    ExitConfig("s3 t3.0 BE0.5",    stop_mult_override=3.0, trail_mult_override=3.0, breakeven_atr=0.5),
    ExitConfig("s3 t4.0 noBE",     stop_mult_override=3.0, trail_mult_override=4.0, breakeven_atr=0.0),

    # === Very wide (regime/time dominant) — best from prior sweep ===
    ExitConfig("s8 t4 noBE",       stop_mult_override=8.0, trail_mult_override=4.0, breakeven_atr=0.0),
    ExitConfig("s8 t4 BE0.5",      stop_mult_override=8.0, trail_mult_override=4.0, breakeven_atr=0.5),
    ExitConfig("s8 t4 BE1.0",      stop_mult_override=8.0, trail_mult_override=4.0, breakeven_atr=1.0),
    ExitConfig("s8 t5 noBE",       stop_mult_override=8.0, trail_mult_override=5.0, breakeven_atr=0.0),
    ExitConfig("s8 t6 noBE",       stop_mult_override=8.0, trail_mult_override=6.0, breakeven_atr=0.0),

    # === Ultra wide (stops essentially disabled) ===
    ExitConfig("s10 t5 noBE",      stop_mult_override=10.0, trail_mult_override=5.0, breakeven_atr=0.0),
    ExitConfig("s10 t6 noBE",      stop_mult_override=10.0, trail_mult_override=6.0, breakeven_atr=0.0),
    ExitConfig("s10 t8 noBE",      stop_mult_override=10.0, trail_mult_override=8.0, breakeven_atr=0.0),
    ExitConfig("s15 t8 noBE",      stop_mult_override=15.0, trail_mult_override=8.0, breakeven_atr=0.0),

    # === Effectively no mechanical exits (regime + max_hold only) ===
    ExitConfig("s99 t99 noBE",     stop_mult_override=99.0, trail_mult_override=99.0, breakeven_atr=0.0),
]


def load_portfolios_from_config(config_path: str):
    with open(config_path) as f:
        cfg = json.load(f)

    portfolios = {}
    for pf in cfg["portfolios"]:
        name = pf["pool_name"]
        strategies = []
        for s in pf["strategies"]:
            strategies.append(StrategySpec.from_dict(s))
        portfolios[name] = {
            "strategies": strategies,
            "max_portfolio_positions": pf.get("max_portfolio_positions", 40),
            "concentration_limit": pf.get("concentration_limit", 1.0),
        }
    return portfolios


def apply_exit_config(signals_dict, ec: ExitConfig):
    """Apply exit overrides and return saved originals for restoration."""
    saved = []
    for token, sig in signals_dict.items():
        orig = {
            "breakeven_atr": sig.breakeven_atr,
            "trail_schedule": sig.trail_schedule,
            "trail_mult": sig.trail_mult,
            "stop_mult": sig.stop_mult,
        }
        saved.append((token, orig))

        sig.breakeven_atr = ec.breakeven_atr
        if not ec.trail_schedule:
            sig.trail_schedule = None
        if ec.trail_mult_override > 0:
            sig.trail_mult = np.full_like(sig.trail_mult, ec.trail_mult_override)
        if ec.stop_mult_override > 0:
            sig.stop_mult = np.full_like(sig.stop_mult, ec.stop_mult_override)
    return saved


def restore_signals(signals_dict, saved):
    """Restore original signal values after override."""
    for token, orig in saved:
        sig = signals_dict[token]
        sig.breakeven_atr = orig["breakeven_atr"]
        sig.trail_schedule = orig["trail_schedule"]
        sig.trail_mult = orig["trail_mult"]
        sig.stop_mult = orig["stop_mult"]


def run_portfolio_period(pf_name, pf_def, period, data_end, all_results):
    """Run all configs for one portfolio and one period."""
    strategies = pf_def["strategies"]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=pf_def["max_portfolio_positions"],
        concentration_limit=pf_def["concentration_limit"],
    )

    # Precompute signals once
    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, period, end_date=data_end)
        all_precomputed[spec.strategy_id] = signals

    strats = ", ".join(s.strategy_id for s in strategies)
    print(f"\n{'='*120}")
    print(f"  {period}-MONTH — {pf_name}")
    print(f"  Strategies: {strats}  max_pos={pf_def['max_portfolio_positions']}  conc={pf_def['concentration_limit']}")
    print(f"{'='*120}")
    print(f"  {'Config':<20s}  {'ret':>10s}  {'DD':>8s}  {'calmar':>9s}  "
          f"{'sharpe':>7s}  {'sortino':>7s}  {'PF':>6s}  {'trades':>6s}  {'wr':>6s}  {'time':>5s}")
    print(f"  {'-'*20}  {'-'*10}  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*5}")
    sys.stdout.flush()

    strategy_specs = {s.strategy_id: s for s in strategies}

    for ec in CONFIGS:
        t0 = time.time()
        saved_all = {}
        try:
            for sid, signals in all_precomputed.items():
                saved_all[sid] = apply_exit_config(signals, ec)

            state = simulate_portfolio(all_precomputed, strategy_specs, config)
            metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

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
                "profit_factor": metrics.profit_factor,
                "trades": metrics.total_trades,
                "win_rate": metrics.win_rate_pct,
            }
            all_results.append(r)
            tag = " <<" if ec.label == "s3.0 t1.5 BE0.5" else ""
            print(f"  {ec.label:<20s}  {r['return_pct']:+9.1f}%  "
                  f"{r['max_dd_pct']:+7.2f}%  {r['calmar']:9.2f}  "
                  f"{r['sharpe']:7.2f}  {r['sortino']:7.2f}  "
                  f"{r['profit_factor']:6.2f}  {r['trades']:6d}  {r['win_rate']:5.1f}%  "
                  f"{elapsed:4.1f}s{tag}")
        except Exception as e:
            for sid, saved in saved_all.items():
                restore_signals(all_precomputed[sid], saved)
            print(f"  {ec.label:<20s}  ERROR: {e}")
        sys.stdout.flush()

    del all_precomputed
    gc.collect()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+", type=int, default=[12, 4],
                        help="Months to test (default: 12 4)")
    parser.add_argument("--portfolios", nargs="+", default=None,
                        help="Portfolio names to test (default: representative subset)")
    args = parser.parse_args()

    periods = args.periods
    config_path = PROJECT_ROOT / "configs" / "multi_v4_paper.json"
    all_portfolios = load_portfolios_from_config(str(config_path))

    # Default: ALL portfolios from config
    if args.portfolios:
        portfolios = {k: v for k, v in all_portfolios.items() if k in args.portfolios}
    else:
        portfolios = all_portfolios

    total_runs = len(portfolios) * len(CONFIGS) * len(periods)
    print(f"Testing {len(portfolios)} portfolios x {len(CONFIGS)} configs x {len(periods)} periods = {total_runs} runs")
    print(f"Portfolios: {', '.join(portfolios.keys())}")
    print(f"Periods: {periods}")
    sys.stdout.flush()

    data_end = infer_data_end_date("combined")
    all_results = []

    for period in periods:
        for pf_name, pf_def in portfolios.items():
            try:
                run_portfolio_period(pf_name, pf_def, period, data_end, all_results)
            except Exception as e:
                print(f"\n  PORTFOLIO ERROR {pf_name} {period}mo: {e}")
                import traceback; traceback.print_exc()
            gc.collect()

    # =========================================================================
    # RANKINGS
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  RANKINGS BY CALMAR (top 5 per portfolio per period)")
    print(f"{'#'*120}")

    for period in periods:
        for pf_name in portfolios:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            if not pf_results:
                continue
            bl = [r for r in pf_results if r["label"] == "s3.0 t1.5 BE0.5"]
            bl_c = bl[0]["calmar"] if bl else 0

            print(f"\n  --- {pf_name} ({period}mo) ---")
            for i, r in enumerate(pf_results[:5]):
                dc = r["calmar"] - bl_c
                marker = " **CUR**" if r["label"] == "s3.0 t1.5 BE0.5" else ""
                print(f"    {i+1}. {r['label']:<20s}  cal={r['calmar']:9.2f} (Δ{dc:+9.2f})  "
                      f"ret={r['return_pct']:+10.1f}%  DD={r['max_dd_pct']:+7.2f}%  "
                      f"PF={r['profit_factor']:.2f}{marker}")

    # =========================================================================
    # GRAND RECOMMENDATION — harmonic rank score across periods
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  RECOMMENDATION: Best exit config per portfolio (harmonic rank score)")
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
                    config_scores[label] = {"score": 0.0, "ranks": []}
                config_scores[label]["score"] += 1.0 / (rank + 1)
                config_scores[label]["ranks"].append((period, rank + 1, r["return_pct"], r["max_dd_pct"]))

        best = sorted(config_scores.items(), key=lambda x: -x[1]["score"])
        bl_score = config_scores.get("s3.0 t1.5 BE0.5", {}).get("score", 0)
        winner = best[0]
        cur_rank = next((i+1 for i, (l,_) in enumerate(best) if l == "s3.0 t1.5 BE0.5"), "?")
        ranks_str = "  ".join(f"{p}mo:#{r}" for p, r, _, _ in winner[1]["ranks"])
        print(f"  {pf_name:<20s}  BEST: {winner[0]:<20s} (score={winner[1]['score']:.2f})  "
              f"CURRENT rank={cur_rank} (score={bl_score:.2f})  [{ranks_str}]")

    # Save results
    out_path = PROJECT_ROOT / "results" / "v4" / "exit_sweep_wide.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=lambda x: float(x))
    print(f"\nResults saved to {out_path}")
