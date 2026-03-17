#!/usr/bin/env python3
"""Exit mechanism ablation v2 — comprehensive across ALL running portfolios.

Reads portfolio definitions from configs/multi_v4_paper.json.
Tests top exit configs from v1 + baseline across multiple periods.
Memory-efficient: precomputes signals once per (strategy, market, period),
saves originals and restores after override.
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
    breakeven_atr: float = 0.0
    trail_schedule: bool = True
    chandelier_lookback: int = 0
    bear_target_mult: float = 0.0
    bear_max_hold: int = 0
    trail_mult_override: float = 0.0


CONFIGS = [
    ExitConfig("CURRENT (BE+prog)", breakeven_atr=0.5, trail_schedule=True),
    ExitConfig("BARE (no mods)", breakeven_atr=0.0, trail_schedule=False),
    ExitConfig("ONLY BE(0.5)", breakeven_atr=0.5, trail_schedule=False),
    ExitConfig("ONLY prog_trail", breakeven_atr=0.0, trail_schedule=True),
    ExitConfig("chand(16)+BE", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=16),
    ExitConfig("chand(24)+BE", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=24),
    ExitConfig("trail(1.5)+BE", breakeven_atr=0.5, trail_schedule=False, trail_mult_override=1.5),
    ExitConfig("trail(2.0)+BE", breakeven_atr=0.5, trail_schedule=False, trail_mult_override=2.0),
    ExitConfig("ONLY trail(1.5)", breakeven_atr=0.0, trail_schedule=False, trail_mult_override=1.5),
    ExitConfig("ONLY trail(2.0)", breakeven_atr=0.0, trail_schedule=False, trail_mult_override=2.0),
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
    """Apply exit overrides and return a list of (token, originals) for restoration."""
    saved = []
    for token, sig in signals_dict.items():
        orig = {
            "breakeven_atr": sig.breakeven_atr,
            "chandelier_lookback": sig.chandelier_lookback,
            "bear_target_mult": sig.bear_target_mult,
            "bear_max_hold": sig.bear_max_hold,
            "trail_schedule": sig.trail_schedule,
            "trail_mult": sig.trail_mult,  # numpy array, just keep reference
        }
        saved.append((token, orig))

        sig.breakeven_atr = ec.breakeven_atr
        sig.chandelier_lookback = ec.chandelier_lookback
        sig.bear_target_mult = ec.bear_target_mult
        sig.bear_max_hold = ec.bear_max_hold
        if not ec.trail_schedule:
            sig.trail_schedule = None
        if ec.trail_mult_override > 0:
            sig.trail_mult = np.full_like(sig.trail_mult, ec.trail_mult_override)
    return saved


def restore_signals(signals_dict, saved):
    """Restore original signal values after override."""
    for token, orig in saved:
        sig = signals_dict[token]
        sig.breakeven_atr = orig["breakeven_atr"]
        sig.chandelier_lookback = orig["chandelier_lookback"]
        sig.bear_target_mult = orig["bear_target_mult"]
        sig.bear_max_hold = orig["bear_max_hold"]
        sig.trail_schedule = orig["trail_schedule"]
        sig.trail_mult = orig["trail_mult"]


def run_portfolio_period(pf_name, pf_def, period, data_end, all_results):
    """Run all configs for one portfolio and one period."""
    strategies = pf_def["strategies"]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=pf_def["max_portfolio_positions"],
        concentration_limit=pf_def["concentration_limit"],
    )

    # Precompute signals once for this portfolio+period
    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, period, end_date=data_end)
        all_precomputed[spec.strategy_id] = signals

    strats = ", ".join(s.strategy_id for s in strategies)
    print(f"\n{'='*115}")
    print(f"  {period}-MONTH — {pf_name}")
    print(f"  Strategies: {strats}  max_pos={pf_def['max_portfolio_positions']}  conc={pf_def['concentration_limit']}")
    print(f"{'='*115}")
    print(f"  {'Config':<22s}  {'ret':>10s}  {'DD':>8s}  {'calmar':>9s}  "
          f"{'sharpe':>7s}  {'sortino':>7s}  {'trades':>6s}  {'wr':>6s}  {'time':>5s}")
    print(f"  {'-'*22}  {'-'*10}  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*5}")
    sys.stdout.flush()

    strategy_specs = {s.strategy_id: s for s in strategies}

    for ec in CONFIGS:
        t0 = time.time()
        try:
            # Apply overrides
            saved_all = {}
            for sid, signals in all_precomputed.items():
                saved_all[sid] = apply_exit_config(signals, ec)

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
            }
            all_results.append(r)
            tag = " <<" if ec.label.startswith("CURRENT") else ""
            print(f"  {ec.label:<22s}  {r['return_pct']:+9.1f}%  "
                  f"{r['max_dd_pct']:+7.2f}%  {r['calmar']:9.2f}  "
                  f"{r['sharpe']:7.2f}  {r['sortino']:7.2f}  "
                  f"{r['trades']:6d}  {r['win_rate']:5.1f}%  "
                  f"{elapsed:4.1f}s{tag}")
        except Exception as e:
            # Restore even on error
            for sid, saved in saved_all.items():
                restore_signals(all_precomputed[sid], saved)
            print(f"  {ec.label:<22s}  ERROR: {e}")
        sys.stdout.flush()

    # Free memory
    del all_precomputed
    gc.collect()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+", type=int, default=[24, 12, 6, 3, 1],
                        help="Months to test")
    parser.add_argument("--portfolios", nargs="+", default=None,
                        help="Portfolio names to test (default: all)")
    args = parser.parse_args()

    periods = args.periods
    config_path = PROJECT_ROOT / "configs" / "multi_v4_paper.json"
    all_portfolios = load_portfolios_from_config(str(config_path))

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
    print(f"\n\n{'#'*115}")
    print(f"  RANKINGS BY CALMAR (top 5 per portfolio per period)")
    print(f"{'#'*115}")

    for period in periods:
        for pf_name in portfolios:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            if not pf_results:
                continue
            bl = [r for r in pf_results if r["label"].startswith("CURRENT")]
            bl_c = bl[0]["calmar"] if bl else 0

            print(f"\n  --- {pf_name} ({period}mo) ---")
            for i, r in enumerate(pf_results[:5]):
                dc = r["calmar"] - bl_c
                marker = " **CUR**" if r["label"].startswith("CURRENT") else ""
                better = " +" if dc > 0.5 and not r["label"].startswith("CURRENT") else ""
                print(f"    {i+1}. {r['label']:<22s}  cal={r['calmar']:9.2f} (Δ{dc:+9.2f})  "
                      f"ret={r['return_pct']:+10.1f}%  DD={r['max_dd_pct']:+7.2f}%{marker}{better}")

    # =========================================================================
    # CROSS-PERIOD CONSISTENCY
    # =========================================================================
    print(f"\n\n{'#'*115}")
    print(f"  CROSS-PERIOD CONSISTENCY (top-3 in 2+ periods)")
    print(f"{'#'*115}")

    for pf_name in portfolios:
        config_ranks = {}
        for period in periods:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            for rank, r in enumerate(pf_results):
                label = r["label"]
                if label not in config_ranks:
                    config_ranks[label] = []
                config_ranks[label].append((period, rank + 1, r["calmar"]))

        consistent = []
        for label, ranks in config_ranks.items():
            top3_count = sum(1 for _, rank, _ in ranks if rank <= 3)
            avg_rank = sum(rank for _, rank, _ in ranks) / len(ranks)
            if top3_count >= 2:
                consistent.append((label, top3_count, avg_rank, ranks))

        consistent.sort(key=lambda x: (-x[1], x[2]))

        print(f"\n  --- {pf_name} ---")
        if not consistent:
            print(f"    No config ranks top-3 in 2+ periods")
        for label, top3_count, avg_rank, ranks in consistent:
            rank_str = "  ".join(f"{p}mo:#{r}" for p, r, _ in ranks)
            print(f"    {label:<22s}  top3 in {top3_count}/{len(periods)} periods  avg_rank={avg_rank:.1f}  [{rank_str}]")

    # =========================================================================
    # GRAND RECOMMENDATION
    # =========================================================================
    print(f"\n\n{'#'*115}")
    print(f"  RECOMMENDATION: Best exit config per portfolio (harmonic rank score)")
    print(f"{'#'*115}")

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
                    config_scores[label] = 0.0
                config_scores[label] += 1.0 / (rank + 1)

        best = sorted(config_scores.items(), key=lambda x: -x[1])
        bl_score = config_scores.get("CURRENT (BE+prog)", 0)
        winner = best[0]
        cur_rank = next((i+1 for i, (l,_) in enumerate(best) if l.startswith("CURRENT")), "?")
        print(f"  {pf_name:<20s}  BEST: {winner[0]:<22s} (score={winner[1]:.2f})  "
              f"CURRENT rank={cur_rank} (score={bl_score:.2f})")
