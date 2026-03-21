#!/usr/bin/env python3
"""Sweep exit_resolution across all live strategies.

Precomputes signals ONCE per strategy, then runs simulations at each resolution.
Sequential single-process — reliable and memory-efficient.

Usage:
    python sweep_exit_resolution.py [--months 3] [--capital 200000]
"""
import sys
import os
import time
import json
import gc
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import (
    build_unified_index, SimulationState, _process_exits,
    _process_entries, _process_margin_calls, _record_equity_snapshot,
    _close_all_remaining,
)
from v4.report import compute_portfolio_metrics
from v4.minute_exits import MinuteExitCache, process_minute_exits


# Live strategy configs from configs/multi_v4_paper.json
LIVE_STRATEGIES = {
    "s56":  {"market": "perp",     "max_positions": 15, "pump_filter_funding_zscore": 3.0},
    "s57":  {"market": "combined", "max_positions": 15, "pump_filter_funding_zscore": 3.0},
    "s62":  {"market": "perp",     "max_positions": 15},
    "s65":  {"market": "perp",     "max_positions": 15, "pump_filter_funding_zscore": 3.0},
    "s72":  {"market": "perp",     "max_positions": 15, "pump_filter_funding_zscore": 3.0},
    "s98":  {"market": "perp",     "max_positions": 15, "pump_filter_funding_zscore": 3.0},
    "s106": {"market": "perp",     "max_positions": 15, "pump_filter_funding_zscore": 3.0},
    "s107": {"market": "perp",     "max_positions": 15, "pump_filter_funding_zscore": 3.0},
}

RESOLUTIONS = [0, 5, 15, 30]


def run_simulation(all_signals, unified_ts, bar_maps, config, strategy_specs,
                   exit_resolution, capital):
    """Run simulation with precomputed signals at a given exit resolution."""
    n_bars = len(unified_ts)
    state = SimulationState(initial_capital=capital)
    rng = np.random.RandomState(config.seed)
    minute_cache = MinuteExitCache(resolution=exit_resolution, max_tokens=10) if exit_resolution else None

    for global_bar in range(n_bars):
        if minute_cache is not None:
            process_minute_exits(state, all_signals, bar_maps, global_bar,
                                 unified_ts, config, strategy_specs, minute_cache)
        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)

    del minute_cache
    gc.collect()

    metrics, extra, _ = compute_portfolio_metrics(state, capital)
    total_ret = (extra["final_equity"] / capital - 1) * 100

    return {
        "total_return_pct": round(total_ret, 1),
        "sharpe": round(metrics.sharpe_ratio, 2),
        "sortino": round(metrics.sortino_ratio, 2),
        "calmar": round(metrics.calmar_ratio, 2),
        "max_dd_pct": round(metrics.max_drawdown_pct, 1),
        "trades": metrics.total_trades,
        "win_rate_pct": round(metrics.win_rate_pct, 1),
        "profit_factor": round(metrics.profit_factor, 2),
        "avg_hold_hrs": round(metrics.avg_hold_hours, 1),
        "final_equity": round(extra["final_equity"], 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Sweep exit resolutions for live strategies")
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--capital", type=int, default=200_000)
    parser.add_argument("--strategies", type=str, default="all",
                        help="Comma-separated strategy IDs or 'all'")
    args = parser.parse_args()

    if args.strategies == "all":
        strategies = list(LIVE_STRATEGIES.keys())
    else:
        strategies = [s.strip() for s in args.strategies.split(",")]

    data_end = infer_data_end_date("perp")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {args.months} months, Capital: ${args.capital:,}")
    print(f"Strategies: {strategies}")
    print(f"Resolutions: {RESOLUTIONS}")
    total_runs = len(strategies) * len(RESOLUTIONS)
    print(f"Total runs: {total_runs}")
    print(flush=True)

    all_results = []
    run_num = 0

    for sid in strategies:
        cfg = LIVE_STRATEGIES[sid]
        print(f"{'='*70}")
        print(f"  Strategy: {sid} (market={cfg['market']}, max_pos={cfg['max_positions']})")
        print(f"{'='*70}", flush=True)

        spec = StrategySpec(
            strategy_id=sid, weight=1.0,
            max_positions=cfg["max_positions"], market=cfg["market"],
            strategy_type="per_token",
            pump_filter_funding_zscore=cfg.get("pump_filter_funding_zscore", 0.0),
        )
        config = PortfolioConfig(
            capital=args.capital, max_portfolio_positions=cfg["max_positions"],
            concentration_limit=1.0, adv_cap_pct=0.05, seed=42,
            stress_adv_multiplier=0.3,
        )
        strategy_specs = {sid: spec}

        print(f"  Precomputing signals...", end=" ", flush=True)
        t0 = time.time()
        tokens = discover_tokens(spec.market)
        all_signals = {
            sid: precompute_strategy_signals(spec, tokens, config, args.months, end_date=data_end)
        }
        unified_ts, bar_maps = build_unified_index(all_signals)
        print(f"{len(all_signals[sid])} tokens, {len(unified_ts)} bars ({time.time()-t0:.0f}s)", flush=True)

        for res in RESOLUTIONS:
            run_num += 1
            res_label = f"{res}m" if res > 0 else "hourly"
            print(f"  [{run_num}/{total_runs}] {sid} @ {res_label}...", end=" ", flush=True)
            t0 = time.time()
            try:
                result = run_simulation(all_signals, unified_ts, bar_maps, config,
                                        strategy_specs, res, args.capital)
                elapsed = time.time() - t0
                print(f"Return={result['total_return_pct']:+.1f}%  Sharpe={result['sharpe']:.2f}  "
                      f"DD={result['max_dd_pct']:.1f}%  Trades={result['trades']}  ({elapsed:.0f}s)", flush=True)
                result["strategy"] = sid
                result["resolution"] = res
                all_results.append(result)
            except Exception as e:
                elapsed = time.time() - t0
                print(f"FAILED: {e} ({elapsed:.0f}s)", flush=True)
                all_results.append({
                    "strategy": sid, "resolution": res,
                    "total_return_pct": None, "sharpe": None, "sortino": None,
                    "calmar": None, "max_dd_pct": None, "trades": None,
                    "win_rate_pct": None, "profit_factor": None,
                    "avg_hold_hrs": None, "final_equity": None,
                })

        del all_signals, unified_ts, bar_maps
        gc.collect()
        print(flush=True)

    # Save incremental results
    out_path = Path("results/v4/exit_resolution_sweep.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)

    # Build comparison table
    df = pd.DataFrame(all_results)
    print(f"\n{'='*100}")
    print(f"  EXIT RESOLUTION SWEEP RESULTS — {args.months}mo lookback, ${args.capital:,} capital")
    print(f"{'='*100}\n")

    for sid in strategies:
        sdf = df[df["strategy"] == sid].copy().sort_values("resolution")
        if sdf.empty:
            continue
        print(f"  {sid}:")
        print(f"  {'Res':>8s}  {'Return':>8s}  {'Sharpe':>7s}  {'Sortino':>8s}  {'Calmar':>7s}  "
              f"{'MaxDD':>7s}  {'Trades':>6s}  {'WinR%':>6s}  {'PF':>6s}  {'Hold':>6s}")
        print(f"  {'-'*80}")
        for _, row in sdf.iterrows():
            res_label = f"{int(row['resolution'])}m" if row["resolution"] > 0 else "hourly"
            if row["total_return_pct"] is None:
                print(f"  {res_label:>8s}  {'FAILED':>8s}")
                continue
            best_sharpe = sdf["sharpe"].max()
            marker = " <-- BEST" if row["sharpe"] == best_sharpe else ""
            print(f"  {res_label:>8s}  {row['total_return_pct']:>+7.1f}%  {row['sharpe']:>7.2f}  "
                  f"{row['sortino']:>8.2f}  {row['calmar']:>7.2f}  {row['max_dd_pct']:>6.1f}%  "
                  f"{row['trades']:>6.0f}  {row['win_rate_pct']:>5.1f}%  {row['profit_factor']:>5.2f}  "
                  f"{row['avg_hold_hrs']:>5.1f}h{marker}")
        print()

    print(f"\n{'='*70}")
    print(f"  RECOMMENDATION — Best exit_resolution per strategy (by Sharpe)")
    print(f"{'='*70}")
    print(f"  {'Strategy':>10s}  {'Best Res':>8s}  {'Sharpe':>7s}  {'Return':>8s}  {'MaxDD':>7s}  {'vs Hourly':>10s}")
    print(f"  {'-'*60}")

    for sid in strategies:
        sdf = df[df["strategy"] == sid].dropna(subset=["sharpe"])
        if sdf.empty:
            continue
        best = sdf.loc[sdf["sharpe"].idxmax()]
        hourly = sdf[sdf["resolution"] == 0]
        hourly_sharpe = hourly["sharpe"].iloc[0] if not hourly.empty else 0
        delta = best["sharpe"] - hourly_sharpe
        res_label = f"{int(best['resolution'])}m" if best["resolution"] > 0 else "hourly"
        delta_str = f"{delta:+.2f}" if delta != 0 else "same"
        print(f"  {sid:>10s}  {res_label:>8s}  {best['sharpe']:>7.2f}  "
              f"{best['total_return_pct']:>+7.1f}%  {best['max_dd_pct']:>6.1f}%  {delta_str:>10s}")

    print(f"\n  Results saved to {out_path}")
    print(flush=True)


if __name__ == "__main__":
    main()
