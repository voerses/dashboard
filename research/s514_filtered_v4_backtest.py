#!/usr/bin/env python3
"""s514 Filtered V4 Portfolio Backtest — Train/Test Token Selection Comparison.

Runs proper V4 portfolio backtests with filtered token sets:
1. 28 both-positive tokens (L12M, L6M)
2. 58 train-positive tokens (L12M, L6M)
3. Original 29 tokens (L12M)
4. OOS monthly on best-performing list

Usage:
    /workspace/venv/bin/python research/s514_filtered_v4_backtest.py
"""

import sys
import os
import time
import dataclasses

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report

STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000

BOTH_POSITIVE_28 = {
    'EIGEN', 'PENGU', 'INJ', 'LTC', 'ARB', 'SAND', 'AAVE', 'GUN',
    'RAYSOL', 'LINK', 'HBAR', 'RENDER', 'ETH', 'PIXEL', 'DOGE',
    'WLD', 'VANRY', 'ADA', 'AIOT', 'UNI', 'BTC', 'IP', 'NEAR',
    'ETC', 'MYX', 'PIPPIN', 'ZEN', 'RESOLV',
}

TRAIN_POSITIVE_58 = BOTH_POSITIVE_28 | {
    'BIO', 'MORPHO', 'H', 'PHA', 'BAS', 'BANANAS31',
    'MINA', 'APE', 'B', 'F', 'CAKE', 'SOL', 'ENJ', 'JUP', 'COS', 'BR', 'TON',
    'NEO', 'G', 'STEEM', 'BCH', 'ANKR', 'ALGO', 'GALA', 'BAN', 'AVAX', 'SUI',
    'FLOW', 'HUMA', 'LDO',
}

ORIGINAL_29 = {
    'AAVE', 'ADA', 'APT', 'ARB', 'ATOM', 'AVAX', 'BNB', 'BTC', 'DOGE',
    'DOT', 'ETH', 'FIL', 'IMX', 'INJ', 'LINK', 'LTC', 'NEAR', 'ONDO',
    'OP', 'SEI', 'SOL', 'SUI', 'TIA', 'TRX', 'UNI', 'WIF', 'XRP',
}


def make_spec(token_set):
    """Create StrategySpec for s514 with appropriate position limits."""
    return StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=min(len(token_set), 50),
        market=MARKET,
        strategy_type='per_token',
        max_concurrent_per_token=1,
        dd_scaling=[],
    )


def make_config(token_set):
    """Create PortfolioConfig for filtered backtest."""
    spec = make_spec(token_set)
    return PortfolioConfig(
        capital=CAPITAL,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
        max_portfolio_positions=min(len(token_set), 50),
        strategies=[spec],
    ), spec


def run_filtered(token_set, months, end_date_str, label):
    """Run a single filtered backtest."""
    end_date = pd.Timestamp(end_date_str)
    config, spec = make_config(token_set)

    tokens = discover_tokens(MARKET)
    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    t1 = time.time()

    # Filter signals to only selected tokens
    filtered_signals = {t: s for t, s in signals.items() if t in token_set}

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Tokens with signals: {len(filtered_signals)}/{len(token_set)} requested")
    print(f"  Signal compute: {t1 - t0:.1f}s")
    print(f"{'='*70}")

    if not filtered_signals:
        print("  No signals for selected tokens!")
        return None, None

    strategy_specs = {STRATEGY_ID: spec}
    state = simulate_portfolio({STRATEGY_ID: filtered_signals}, strategy_specs, config)
    t2 = time.time()
    print(f"  Simulation: {len(state.position_manager.closed_trades)} trades ({t2 - t1:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    print_report(metrics, extra_info, CAPITAL, [STRATEGY_ID])

    return metrics, extra_info


def run_oos_monthly_filtered(token_set, oos_months, label):
    """Run month-by-month OOS with compounding equity, filtered to token_set."""
    data_end = infer_data_end_date(MARKET)
    running_capital = CAPITAL
    results = []

    print(f"\n{'='*70}")
    print(f"  OOS MONTHLY — {label}")
    print(f"  {oos_months} months, compounding, filtered to {len(token_set)} tokens")
    print(f"{'='*70}")

    for i in range(oos_months):
        month_offset = oos_months - 1 - i
        end_date = data_end - pd.DateOffset(months=month_offset)
        end_date = end_date + pd.offsets.MonthEnd(0)
        clamped = end_date > data_end
        end_date = min(end_date, data_end)

        label_suffix = " (partial)" if clamped else ""
        print(f"\n  OOS Month {i+1}/{oos_months}: capital=${running_capital:,.0f}, "
              f"end_date={end_date.strftime('%Y-%m-%d')}{label_suffix}")

        # Build config with current capital
        spec = make_spec(token_set)
        config = PortfolioConfig(
            capital=running_capital,
            exchange='binance',
            skip_walk_forward=True,
            conviction_mode='ranked',
            max_portfolio_positions=min(len(token_set), 50),
            strategies=[spec],
        )

        tokens = discover_tokens(MARKET)
        signals = precompute_strategy_signals(spec, tokens, config, 1, end_date=end_date)
        filtered_signals = {t: s for t, s in signals.items() if t in token_set}

        if not filtered_signals:
            print(f"    No signals for month {i+1}")
            results.append({
                "month": end_date.strftime("%Y-%m"),
                "start_capital": running_capital,
                "final_equity": running_capital,
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
            })
            continue

        strategy_specs = {STRATEGY_ID: spec}
        state = simulate_portfolio({STRATEGY_ID: filtered_signals}, strategy_specs, config)
        metrics, extra_info, eq_daily = compute_portfolio_metrics(state, running_capital)

        final_equity = extra_info["final_equity"]
        total_return = (final_equity / running_capital - 1) * 100
        max_dd = metrics.max_drawdown_pct

        results.append({
            "month": end_date.strftime("%Y-%m"),
            "start_capital": running_capital,
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "trades": metrics.total_trades,
        })

        running_capital = final_equity

    # Print per-month table
    cumulative_return = (running_capital / CAPITAL - 1) * 100
    print(f"\n{'='*70}")
    print(f"  OOS MONTHLY RESULTS — {label}")
    print(f"{'='*70}")
    print(f"  {'Month':>10s}  {'Capital':>12s}  {'Return':>8s}  {'MaxDD':>8s}  {'Trades':>7s}  {'Equity':>12s}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*12}")
    for r in results:
        ret = r["total_return_pct"]
        dd = r["max_drawdown_pct"]
        trades = r.get("trades", 0)
        print(f"  {r['month']:>10s}  ${r['start_capital']:>11,.0f}  {ret:>+7.1f}%  "
              f"{dd:>7.1f}%  {trades:>7d}  ${r['final_equity']:>11,.0f}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*12}")
    print(f"  {'TOTAL':>10s}  ${CAPITAL:>11,.0f}  {cumulative_return:>+7.1f}%  "
          f"{'':>8s}  {'':>7s}  ${running_capital:>11,.0f}")
    print(f"{'='*70}")

    return results, running_capital


def main():
    print("=" * 70)
    print("  s514 Filtered V4 Portfolio Backtest — Token Selection Comparison")
    print("=" * 70)

    configs = [
        (BOTH_POSITIVE_28, 12, '2026-04-01', 'BOTH_POSITIVE 28 tokens - L12M'),
        (BOTH_POSITIVE_28, 6,  '2026-04-01', 'BOTH_POSITIVE 28 tokens - L6M (test period)'),
        (TRAIN_POSITIVE_58, 12, '2026-04-01', 'TRAIN_POSITIVE 58 tokens - L12M'),
        (TRAIN_POSITIVE_58, 6,  '2026-04-01', 'TRAIN_POSITIVE 58 tokens - L6M (test period)'),
        (ORIGINAL_29, 12, '2026-04-01', 'ORIGINAL 29 tokens - L12M'),
    ]

    results = {}
    for token_set, months, end, label in configs:
        m, ei = run_filtered(token_set, months, end, label)
        if m:
            results[label] = (m, ei)

    # Summary table
    print(f"\n{'='*90}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*90}")
    print(f"{'Config':<48} {'Sharpe':>7} {'Calmar':>7} {'MaxDD':>8} {'Return':>10} {'Trades':>7}")
    print("-" * 90)
    for label, (m, ei) in results.items():
        print(f"{label:<48} {m.sharpe_ratio:>7.2f} {m.calmar_ratio:>7.2f} "
              f"{m.max_drawdown_pct:>7.1f}% {m.total_return_pct:>+9.1f}% {m.total_trades:>7}")

    # Find best by Sharpe
    if results:
        best_label = max(results.keys(), key=lambda k: results[k][0].sharpe_ratio)
        print(f"\n  BEST BY SHARPE: {best_label} (Sharpe={results[best_label][0].sharpe_ratio:.2f})")

    # Run OOS monthly on the best list
    # Map labels back to token sets
    label_to_set = {
        'BOTH_POSITIVE 28 tokens - L12M': BOTH_POSITIVE_28,
        'BOTH_POSITIVE 28 tokens - L6M (test period)': BOTH_POSITIVE_28,
        'TRAIN_POSITIVE 58 tokens - L12M': TRAIN_POSITIVE_58,
        'TRAIN_POSITIVE 58 tokens - L6M (test period)': TRAIN_POSITIVE_58,
        'ORIGINAL 29 tokens - L12M': ORIGINAL_29,
    }

    if results:
        best_set = label_to_set[best_label]
        oos_results, final_eq = run_oos_monthly_filtered(
            best_set, 12, f"Best={best_label}"
        )

    print("\nDone.")


if __name__ == '__main__':
    main()
