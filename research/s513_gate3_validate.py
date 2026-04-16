#!/usr/bin/env python3
"""
s513 Triple-Trigger Swing — Gate 3 Prototype Validation
========================================================

Task 1: Verify L12M metrics match expected (~194 trades, ~+195%, Sharpe ~2.56,
         Calmar ~11.87, MaxDD ~-16.6% at 3x leverage).
Task 2: Run all 3 windows (L12M/L6M/L3M) at 3x leverage.

Gate 3 thresholds: Sharpe >2.0, Calmar >3.0, MaxDD >-25%, positive return,
                   30+ trades in L12M.

Usage:
    /workspace/venv/bin/python research/s513_gate3_validate.py
"""

import sys
import os
import time
import dataclasses

import pandas as pd

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report

STRATEGY_ID = 's513'
MARKET = 'perp'
CAPITAL = 100_000

windows = {
    'L12M': {'months': 12, 'end': '2026-04-01'},
    'L6M':  {'months': 6,  'end': '2026-04-01'},
    'L3M':  {'months': 3,  'end': '2026-04-01'},
}

# Gate 3 thresholds
THRESHOLDS = {
    'sharpe_min': 2.0,
    'calmar_min': 3.0,
    'max_dd_floor': -25.0,
    'ann_return_min': 0.0,
    'trades_min_l12m': 30,
}


def run_window(name, months, end_date_str):
    """Run s513 backtest for a single time window."""
    end_date = pd.Timestamp(end_date_str)

    config = PortfolioConfig(
        capital=CAPITAL,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
        max_portfolio_positions=40,
    )

    spec = StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=config.max_portfolio_positions,
        market=MARKET,
        strategy_type='per_token',
        sizing_overrides={},
        regime_params=None,
        max_concurrent_per_token=1,
        dd_scaling=[],
        entry_resolution=0,
    )

    tokens = discover_tokens(MARKET)
    print(f"\n{'='*70}")
    print(f"  {name}: {months}M lookback ending {end_date_str}")
    print(f"  Universe: {len(tokens)} tokens, market={MARKET}")
    print(f"{'='*70}")

    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    t1 = time.time()
    print(f"  Signals: {len(signals)} tokens ({t1 - t0:.1f}s)")

    if not signals:
        print(f"  [{name}] No signals produced!")
        return None

    strategy_specs = {STRATEGY_ID: spec}
    config_run = dataclasses.replace(config, strategies=[spec])

    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)
    t2 = time.time()
    print(f"  Simulation: {len(state.position_manager.closed_trades)} trades ({t2 - t1:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    print_report(metrics, extra_info, CAPITAL, [STRATEGY_ID])

    return metrics, extra_info, name


def check_thresholds(metrics, name):
    """Check Gate 3 thresholds and return pass/fail."""
    results = {}
    results['sharpe'] = (metrics.sharpe_ratio >= THRESHOLDS['sharpe_min'],
                         metrics.sharpe_ratio, THRESHOLDS['sharpe_min'])
    results['calmar'] = (metrics.calmar_ratio >= THRESHOLDS['calmar_min'],
                         metrics.calmar_ratio, THRESHOLDS['calmar_min'])
    results['max_dd'] = (metrics.max_drawdown_pct > THRESHOLDS['max_dd_floor'],
                         metrics.max_drawdown_pct, THRESHOLDS['max_dd_floor'])
    results['ann_return'] = (metrics.annualized_return_pct > THRESHOLDS['ann_return_min'],
                             metrics.annualized_return_pct, THRESHOLDS['ann_return_min'])
    if name == 'L12M':
        results['trades'] = (metrics.total_trades >= THRESHOLDS['trades_min_l12m'],
                             metrics.total_trades, THRESHOLDS['trades_min_l12m'])

    all_pass = all(v[0] for v in results.values())

    print(f"\n  Gate 3 Threshold Check -- {name}:")
    for key, (passed, actual, threshold) in results.items():
        status = "PASS" if passed else "** FAIL **"
        print(f"    {key:12s}: {actual:>8.2f}  (threshold: {threshold:>8.2f})  [{status}]")

    overall = "PASS" if all_pass else "** FAIL **"
    print(f"    {'OVERALL':12s}: [{overall}]")
    return all_pass


def main():
    print("=" * 70)
    print("  Gate 3 Prototype Validate -- s513 Triple-Trigger Swing")
    print("  Thresholds: Sharpe>2, Calmar>3, MaxDD>-25%, AnnRet>0%, Trades>30")
    print("=" * 70)

    all_results = {}
    all_pass = True

    for name, w in windows.items():
        result = run_window(name, w['months'], w['end'])
        if result is None:
            print(f"\n  ** {name}: FAILED (no signals) **")
            all_pass = False
            continue
        metrics, extra_info, _ = result
        passed = check_thresholds(metrics, name)
        all_results[name] = (metrics, extra_info, passed)
        if not passed:
            all_pass = False

    print(f"\n{'='*70}")
    print(f"  GATE 3 FINAL VERDICT: {'PASS -- all windows clear' if all_pass else '** FAIL -- see above **'}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
