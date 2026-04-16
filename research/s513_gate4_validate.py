#!/usr/bin/env python3
"""
s513 Triple-Trigger Swing — Gate 4 Walk-Forward Validation
============================================================

Walk-forward test across multiple time windows and market regimes.

Gate 4 thresholds: Sharpe >2.0, Calmar >3.0, MaxDD >-25%
ALL primary windows (L24M, L12M, L6M) must pass.
Regime-specific windows are informational.

Gate 3 baseline (L12M): Sharpe 2.61, Calmar 12.07, MaxDD -15.0%, 194 trades.

Usage:
    /workspace/venv/bin/python research/s513_gate4_validate.py
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

# Primary windows — ALL must pass
primary_windows = {
    'L24M': {'months': 24, 'end': '2026-04-01'},
    'L12M': {'months': 12, 'end': '2026-04-01'},
    'L6M':  {'months': 6,  'end': '2026-04-01'},
}

# Regime-specific windows — informational, not gated
regime_windows = {
    'Bull 2024H1':     {'months': 6,  'end': '2024-07-01'},
    'Chop 2024H2':     {'months': 6,  'end': '2025-01-01'},
    'Recovery 2025H1': {'months': 6,  'end': '2025-07-01'},
    'Recent 2025H2':   {'months': 6,  'end': '2026-01-01'},
    'Latest 2026Q1':   {'months': 3,  'end': '2026-04-01'},
}

# Gate 4 thresholds
THRESHOLDS = {
    'sharpe_min': 2.0,
    'calmar_min': 3.0,
    'max_dd_floor': -25.0,
}

# Gate 3 baseline for degradation check
GATE3_BASELINE = {
    'sharpe': 2.61,
    'calmar': 12.07,
    'max_dd': -15.0,
    'trades': 194,
    'ann_return': 195.0,
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
    """Check Gate 4 thresholds and return pass/fail."""
    results = {}
    results['sharpe'] = (metrics.sharpe_ratio >= THRESHOLDS['sharpe_min'],
                         metrics.sharpe_ratio, THRESHOLDS['sharpe_min'])
    results['calmar'] = (metrics.calmar_ratio >= THRESHOLDS['calmar_min'],
                         metrics.calmar_ratio, THRESHOLDS['calmar_min'])
    results['max_dd'] = (metrics.max_drawdown_pct > THRESHOLDS['max_dd_floor'],
                         metrics.max_drawdown_pct, THRESHOLDS['max_dd_floor'])

    all_pass = all(v[0] for v in results.values())

    print(f"\n  Gate 4 Threshold Check -- {name}:")
    for key, (passed, actual, threshold) in results.items():
        status = "PASS" if passed else "** FAIL **"
        print(f"    {key:12s}: {actual:>8.2f}  (threshold: {threshold:>8.2f})  [{status}]")

    overall = "PASS" if all_pass else "** FAIL **"
    print(f"    {'OVERALL':12s}: [{overall}]")
    return all_pass


def main():
    print("=" * 70)
    print("  Gate 4 Walk-Forward Validate -- s513 Triple-Trigger Swing")
    print("  Thresholds: Sharpe>2, Calmar>3, MaxDD>-25%")
    print("  ALL primary windows (L24M, L12M, L6M) must PASS")
    print("=" * 70)

    all_results = {}
    primary_pass = True

    # ── Primary windows (gated) ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PRIMARY WINDOWS (must pass)")
    print("=" * 70)

    for name, w in primary_windows.items():
        result = run_window(name, w['months'], w['end'])
        if result is None:
            print(f"\n  ** {name}: FAILED (no signals) **")
            primary_pass = False
            continue
        metrics, extra_info, _ = result
        passed = check_thresholds(metrics, name)
        all_results[name] = (metrics, extra_info, passed)
        if not passed:
            primary_pass = False

    # ── Regime-specific windows (informational) ──────────────────────
    print("\n" + "=" * 70)
    print("  REGIME-SPECIFIC WINDOWS (informational)")
    print("=" * 70)

    for name, w in regime_windows.items():
        result = run_window(name, w['months'], w['end'])
        if result is None:
            print(f"\n  ** {name}: no signals (may lack data) **")
            all_results[name] = None
            continue
        metrics, extra_info, _ = result
        check_thresholds(metrics, name)
        all_results[name] = (metrics, extra_info, None)  # None = informational

    # ── Summary table ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  SUMMARY TABLE")
    print(f"{'='*70}")
    print(f"  {'Window':<22s} {'Sharpe':>8s} {'Calmar':>8s} {'MaxDD%':>8s} {'AnnRet%':>8s} {'Trades':>7s} {'Result':>8s}")
    print(f"  {'-'*22} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*8}")

    for name, data in all_results.items():
        if data is None:
            print(f"  {name:<22s} {'N/A':>8s} {'N/A':>8s} {'N/A':>8s} {'N/A':>8s} {'N/A':>7s} {'NO DATA':>8s}")
            continue
        metrics, extra_info, passed = data
        is_primary = name in primary_windows

        if passed is True:
            result_str = "PASS"
        elif passed is False:
            result_str = "FAIL"
        else:
            # informational — check thresholds silently
            s_ok = metrics.sharpe_ratio >= THRESHOLDS['sharpe_min']
            c_ok = metrics.calmar_ratio >= THRESHOLDS['calmar_min']
            d_ok = metrics.max_drawdown_pct > THRESHOLDS['max_dd_floor']
            result_str = "pass" if (s_ok and c_ok and d_ok) else "fail"

        print(f"  {name:<22s} {metrics.sharpe_ratio:>8.2f} {metrics.calmar_ratio:>8.2f} "
              f"{metrics.max_drawdown_pct:>8.1f} {metrics.annualized_return_pct:>8.1f} "
              f"{metrics.total_trades:>7d} {result_str:>8s}")

    # ── Gate 3 vs Gate 4 degradation check ───────────────────────────
    print(f"\n{'='*70}")
    print("  DEGRADATION CHECK: Gate 3 (raw) vs Gate 4 (L12M)")
    print(f"{'='*70}")

    if 'L12M' in all_results and all_results['L12M'] is not None:
        m = all_results['L12M'][0]
        print(f"  {'Metric':<15s} {'Gate 3':>10s} {'Gate 4':>10s} {'Delta':>10s} {'Pct':>8s}")
        print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")

        for label, g3_val, g4_val, fmt in [
            ('Sharpe', GATE3_BASELINE['sharpe'], m.sharpe_ratio, '.2f'),
            ('Calmar', GATE3_BASELINE['calmar'], m.calmar_ratio, '.2f'),
            ('MaxDD%', GATE3_BASELINE['max_dd'], m.max_drawdown_pct, '.1f'),
            ('Trades', GATE3_BASELINE['trades'], m.total_trades, '.0f'),
        ]:
            delta = g4_val - g3_val
            pct = (delta / abs(g3_val) * 100) if g3_val != 0 else 0
            print(f"  {label:<15s} {g3_val:>10{fmt}} {g4_val:>10{fmt}} {delta:>+10{fmt}} {pct:>+7.1f}%")

        # Flag significant degradation (>20%)
        sharpe_deg = (m.sharpe_ratio - GATE3_BASELINE['sharpe']) / GATE3_BASELINE['sharpe'] * 100
        if sharpe_deg < -20:
            print(f"\n  ** WARNING: Sharpe degraded {sharpe_deg:.1f}% from Gate 3 **")
        else:
            print(f"\n  Sharpe change: {sharpe_deg:+.1f}% (acceptable if > -20%)")
    else:
        print("  L12M result not available for comparison.")

    # ── Negative return / Sharpe <0 check ────────────────────────────
    print(f"\n{'='*70}")
    print("  PROBLEM WINDOW CHECK")
    print(f"{'='*70}")
    problems = []
    for name, data in all_results.items():
        if data is None:
            continue
        metrics = data[0]
        if metrics.annualized_return_pct < 0:
            problems.append(f"  {name}: negative return ({metrics.annualized_return_pct:.1f}%)")
        if metrics.sharpe_ratio < 0:
            problems.append(f"  {name}: negative Sharpe ({metrics.sharpe_ratio:.2f})")
    if problems:
        print("\n".join(problems))
    else:
        print("  No windows with negative return or negative Sharpe.")

    # ── Final verdict ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    verdict = "PASS -- all primary windows clear" if primary_pass else "** FAIL -- see above **"
    print(f"  GATE 4 FINAL VERDICT: {verdict}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
