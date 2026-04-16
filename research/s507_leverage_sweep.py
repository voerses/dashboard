#!/usr/bin/env python3
"""
s507 Leverage Sweep — Find Calmar-Optimal Leverage
====================================================

Runs s507_ls_div_fixed_tp at multiple leverage levels (1x-5x) on L12M,
then runs OOS monthly at the best candidates (2x, 3x).

s507 wraps s506_ls_divergence which has LEVERAGE=1.0 as a module-level constant.
We monkey-patch s506_mod.LEVERAGE before each run to test different leverage levels.

Usage:
    /workspace/venv/bin/python research/s507_leverage_sweep.py
"""

import sys
import os
import time
import dataclasses
import importlib

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'v4'))
os.chdir(PROJECT_ROOT)

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report
import strategies.s506_ls_divergence as s506_mod

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

STRATEGY_ID = 's507'
MARKET = 'perp'
CAPITAL = 100_000
LEVERAGE_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
OOS_LEVERAGE_CANDIDATES = [2.0, 3.0]  # Run OOS monthly at these
OOS_MONTHS = 12


def make_config(capital=CAPITAL):
    return PortfolioConfig(
        capital=capital,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
        max_portfolio_positions=40,
    )


def make_spec():
    return StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=40,
        market=MARKET,
        strategy_type='per_token',
        sizing_overrides={},
        regime_params=None,
        max_concurrent_per_token=1,
        dd_scaling=[],
        entry_resolution=0,
    )


# ---------------------------------------------------------------------------
#  Leverage sweep (L12M)
# ---------------------------------------------------------------------------

def run_at_leverage(lev, tokens, end_date):
    """Run s507 at a given leverage level, return metrics."""
    # Monkey-patch leverage
    s506_mod.LEVERAGE = lev

    # Force re-import of s507 to pick up new leverage
    if 'strategies.s507_ls_div_fixed_tp' in sys.modules:
        del sys.modules['strategies.s507_ls_div_fixed_tp']
    importlib.import_module('strategies.s507_ls_div_fixed_tp')

    config = make_config()
    spec = make_spec()

    print(f"\n  Precomputing signals at {lev}x leverage...")
    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, 12, end_date=end_date)
    print(f"  Signals: {len(signals)} tokens ({time.time() - t0:.1f}s)")

    if not signals:
        print(f"  No signals at {lev}x!")
        return None

    strategy_specs = {STRATEGY_ID: spec}
    config_run = dataclasses.replace(config, strategies=[spec])

    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)
    print(f"  Trades: {len(state.position_manager.closed_trades)}")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    return metrics, extra_info


def run_oos_monthly_at_leverage(lev, tokens_cache=None):
    """Run month-by-month OOS at a given leverage, compounding."""
    s506_mod.LEVERAGE = lev
    if 'strategies.s507_ls_div_fixed_tp' in sys.modules:
        del sys.modules['strategies.s507_ls_div_fixed_tp']
    importlib.import_module('strategies.s507_ls_div_fixed_tp')

    data_end = infer_data_end_date(MARKET)
    running_capital = CAPITAL
    monthly_results = []

    for i in range(OOS_MONTHS):
        month_offset = OOS_MONTHS - 1 - i
        end_date = data_end - pd.DateOffset(months=month_offset)
        end_date = end_date + pd.offsets.MonthEnd(0)
        end_date = min(end_date, data_end)

        config = make_config(running_capital)
        spec = make_spec()

        print(f"  Month {i+1}/{OOS_MONTHS}: capital=${running_capital:,.0f}, end={end_date.strftime('%Y-%m-%d')}")

        tokens = discover_tokens(MARKET)
        signals = precompute_strategy_signals(spec, tokens, config, 1, end_date=end_date)
        if not signals:
            monthly_results.append({
                'month': end_date.strftime('%Y-%m'),
                'start_capital': running_capital,
                'final_equity': running_capital,
                'return_pct': 0.0,
                'max_dd_pct': 0.0,
                'trades': 0,
            })
            continue

        strategy_specs = {STRATEGY_ID: spec}
        config_run = dataclasses.replace(config, strategies=[spec], capital=running_capital)

        state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)
        metrics, extra_info, eq_daily = compute_portfolio_metrics(state, running_capital)

        final_eq = extra_info.get('final_equity', running_capital) if isinstance(extra_info, dict) else getattr(extra_info, 'final_equity', running_capital)
        ret_pct = metrics.get('total_return_pct', 0) if isinstance(metrics, dict) else getattr(metrics, 'total_return_pct', 0)
        dd_pct = metrics.get('max_drawdown_pct', 0) if isinstance(metrics, dict) else getattr(metrics, 'max_drawdown_pct', 0)
        n_trades = metrics.get('total_trades', 0) if isinstance(metrics, dict) else getattr(metrics, 'total_trades', 0)

        monthly_results.append({
            'month': end_date.strftime('%Y-%m'),
            'start_capital': running_capital,
            'final_equity': final_eq,
            'return_pct': ret_pct,
            'max_dd_pct': dd_pct,
            'trades': n_trades,
        })

        running_capital = final_eq

    return monthly_results, running_capital


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  s507 LEVERAGE SWEEP — Calmar-Optimal Leverage Search")
    print("=" * 70)

    tokens = discover_tokens(MARKET)
    data_end = infer_data_end_date(MARKET)
    print(f"  Universe: {len(tokens)} tokens, market={MARKET}")
    print(f"  Data end: {data_end}")
    print(f"  Capital:  ${CAPITAL:,.0f}")
    print(f"  Leverage levels: {LEVERAGE_LEVELS}")

    orig_leverage = s506_mod.LEVERAGE

    # --- Part 1: Leverage Sweep ---
    sweep_results = []
    for lev in LEVERAGE_LEVELS:
        print(f"\n{'='*70}")
        print(f"  LEVERAGE: {lev}x")
        print(f"{'='*70}")
        t0 = time.time()
        result = run_at_leverage(lev, tokens, end_date=data_end)
        elapsed = time.time() - t0
        if result is None:
            sweep_results.append({'leverage': lev, 'error': True})
            continue

        metrics, extra_info = result
        sweep_results.append({
            'leverage': lev,
            'sharpe': metrics.sharpe_ratio,
            'calmar': metrics.calmar_ratio,
            'sortino': metrics.sortino_ratio,
            'ann_return_pct': metrics.annualized_return_pct,
            'total_return_pct': metrics.total_return_pct,
            'max_dd_pct': metrics.max_drawdown_pct,
            'trades': metrics.total_trades,
            'win_rate': metrics.win_rate_pct,
            'profit_factor': metrics.profit_factor,
            'avg_hold_hours': metrics.avg_hold_hours,
            'final_equity': extra_info['final_equity'],
            'elapsed': elapsed,
        })
        print(f"  Sharpe={metrics.sharpe_ratio:.2f}  Calmar={metrics.calmar_ratio:.2f}  "
              f"Return={metrics.total_return_pct:+.1f}%  MaxDD={metrics.max_drawdown_pct:.1f}%  "
              f"Trades={metrics.total_trades}  ({elapsed:.1f}s)")

    # --- Print sweep table ---
    print(f"\n\n{'='*70}")
    print("  LEVERAGE SWEEP RESULTS (L12M)")
    print(f"{'='*70}")
    print(f"  {'Lev':>5s}  {'Sharpe':>7s}  {'Calmar':>7s}  {'Sortino':>8s}  "
          f"{'Return%':>8s}  {'MaxDD%':>7s}  {'Trades':>7s}  {'WinRate':>8s}  {'PF':>6s}  {'FinalEq':>12s}")
    print(f"  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*6}  {'-'*12}")

    for r in sweep_results:
        if r.get('error'):
            print(f"  {r['leverage']:>5.1f}x  ERROR")
            continue
        print(f"  {r['leverage']:>4.1f}x  {r['sharpe']:>7.2f}  {r['calmar']:>7.2f}  {r['sortino']:>8.2f}  "
              f"{r['total_return_pct']:>+7.1f}%  {r['max_dd_pct']:>6.1f}%  {r['trades']:>7d}  "
              f"{r['win_rate']:>7.1f}%  {r['profit_factor']:>6.2f}  ${r['final_equity']:>11,.0f}")

    # Find Calmar-optimal
    valid = [r for r in sweep_results if not r.get('error')]
    if valid:
        best_calmar = max(valid, key=lambda r: r['calmar'])
        best_sharpe = max(valid, key=lambda r: r['sharpe'])
        print(f"\n  ** Calmar-optimal: {best_calmar['leverage']}x "
              f"(Calmar={best_calmar['calmar']:.2f}, Return={best_calmar['total_return_pct']:+.1f}%, "
              f"MaxDD={best_calmar['max_dd_pct']:.1f}%)")
        print(f"  ** Sharpe-optimal: {best_sharpe['leverage']}x "
              f"(Sharpe={best_sharpe['sharpe']:.2f})")

    # --- Part 2: OOS Monthly at candidate leverages ---
    for lev in OOS_LEVERAGE_CANDIDATES:
        print(f"\n\n{'='*70}")
        print(f"  OOS MONTHLY AT {lev}x LEVERAGE (12 months, compounding)")
        print(f"{'='*70}")
        t0 = time.time()
        monthly, final_equity = run_oos_monthly_at_leverage(lev)
        elapsed = time.time() - t0

        print(f"\n  {'Month':>10s}  {'Capital':>12s}  {'Return':>8s}  {'MaxDD':>8s}  {'Trades':>7s}  {'Equity':>12s}")
        print(f"  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*12}")
        for m in monthly:
            print(f"  {m['month']:>10s}  ${m['start_capital']:>11,.0f}  {m['return_pct']:>+7.1f}%  "
                  f"{m['max_dd_pct']:>7.1f}%  {m['trades']:>7d}  ${m['final_equity']:>11,.0f}")
        cum_ret = (final_equity / CAPITAL - 1) * 100
        print(f"  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*12}")
        print(f"  {'TOTAL':>10s}  ${CAPITAL:>11,.0f}  {cum_ret:>+7.1f}%  {'':>8s}  {'':>7s}  ${final_equity:>11,.0f}")
        print(f"  ({elapsed:.1f}s)")

    # --- Final comparison ---
    print(f"\n\n{'='*70}")
    print("  COMPARISON vs s513 OOS (+41%)")
    print(f"{'='*70}")
    for lev in OOS_LEVERAGE_CANDIDATES:
        # Find OOS results -- we ran them above
        pass
    print("  (Compare OOS total returns above vs s513's +41% benchmark)")

    # Restore
    s506_mod.LEVERAGE = orig_leverage
    print("\nDone.")


if __name__ == '__main__':
    main()
