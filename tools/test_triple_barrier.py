#!/usr/bin/env python3
"""Validation: Triple barrier exit (bear_target_mult + bear_max_hold) on s58 portfolio.

Tests combinations of:
- bear_target_mult: [0 (disabled), 3.0, 5.0, 8.0] ATR
- bear_max_hold: [0 (disabled), 48, 96, 168] bars (2d, 4d, 7d)

Baseline: no bear adjustments (target=999, max_hold=720).
"""
import gc
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


def run_test(bear_target: float, bear_hold: int, months: int = 12):
    """Run s58 portfolio backtest with triple barrier params."""
    strategies = [
        StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="combined"),
        StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined"),
    ]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=40,
        concentration_limit=1.0,
    )

    data_end = infer_data_end_date("combined")

    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
        for token, sig in signals.items():
            sig.bear_target_mult = bear_target
            sig.bear_max_hold = bear_hold
        all_precomputed[spec.strategy_id] = signals

    strategy_specs = {s.strategy_id: s for s in strategies}
    state = simulate_portfolio(all_precomputed, strategy_specs, config)
    metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

    # Count bear exits
    trades = state.position_manager.closed_trades
    target_exits = sum(1 for t in trades if t.exit_reason == "target")
    max_hold_exits = sum(1 for t in trades if t.exit_reason == "max_hold")

    return {
        "bear_target": bear_target,
        "bear_hold": bear_hold,
        "months": months,
        "return_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "calmar": metrics.calmar_ratio,
        "sharpe": metrics.sharpe_ratio,
        "sortino": metrics.sortino_ratio,
        "trades": metrics.total_trades,
        "win_rate": metrics.win_rate_pct,
        "target_exits": target_exits,
        "max_hold_exits": max_hold_exits,
    }


if __name__ == "__main__":
    bear_targets = [0, 3.0, 5.0, 8.0]
    bear_holds = [0, 48, 96, 168]
    periods = [12, 3]

    all_results = []
    for period in periods:
        print(f"\n{'='*80}")
        print(f"  {period}-MONTH PERIOD")
        print(f"{'='*80}")
        print(f"  {'bt':>4s}  {'bh':>4s}  {'ret':>9s}  {'DD':>8s}  {'calmar':>7s}  "
              f"{'sharpe':>7s}  {'sortino':>7s}  {'trades':>6s}  {'wr':>6s}  "
              f"{'tp_exit':>7s}  {'mh_exit':>7s}  {'time':>5s}")
        print(f"  {'-'*4}  {'-'*4}  {'-'*9}  {'-'*8}  {'-'*7}  "
              f"{'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*5}")

        for bt in bear_targets:
            for bh in bear_holds:
                # Skip combos where both are disabled (except baseline)
                if bt == 0 and bh != 0:
                    continue
                if bt != 0 and bh == 0:
                    # Test target-only too
                    pass

                t0 = time.time()
                try:
                    r = run_test(bt, bh, period)
                    elapsed = time.time() - t0
                    all_results.append(r)
                    tag = " << BASE" if bt == 0 and bh == 0 else ""
                    print(f"  {bt:4.1f}  {bh:4d}  {r['return_pct']:+8.1f}%  "
                          f"{r['max_dd_pct']:+7.2f}%  {r['calmar']:7.2f}  "
                          f"{r['sharpe']:7.2f}  {r['sortino']:7.2f}  "
                          f"{r['trades']:6d}  {r['win_rate']:5.1f}%  "
                          f"{r['target_exits']:7d}  {r['max_hold_exits']:7d}  "
                          f"{elapsed:4.1f}s{tag}")
                except Exception as e:
                    import traceback
                    print(f"  {bt:4.1f}  {bh:4d}  ERROR: {e}")
                    traceback.print_exc()
                gc.collect()

    # Best configs
    print(f"\n{'='*80}")
    print("  BEST CONFIGS BY CALMAR (per period)")
    print(f"{'='*80}")
    for period in periods:
        period_results = [r for r in all_results if r["months"] == period]
        baseline = [r for r in period_results if r["bear_target"] == 0 and r["bear_hold"] == 0]
        if not baseline:
            continue
        bl = baseline[0]
        sorted_r = sorted(period_results, key=lambda x: x["calmar"], reverse=True)
        print(f"\n  {period}-month (baseline calmar={bl['calmar']:.2f}, ret={bl['return_pct']:+.1f}%):")
        for r in sorted_r[:5]:
            delta_c = r["calmar"] - bl["calmar"]
            delta_r = r["return_pct"] - bl["return_pct"]
            delta_dd = r["max_dd_pct"] - bl["max_dd_pct"]
            print(f"    bt={r['bear_target']:4.1f} bh={r['bear_hold']:4d}  "
                  f"calmar={r['calmar']:7.2f} (Δ{delta_c:+.2f})  "
                  f"ret={r['return_pct']:+.1f}% (Δ{delta_r:+.1f}%)  "
                  f"DD={r['max_dd_pct']:+.2f}% (Δ{delta_dd:+.2f}pp)")
