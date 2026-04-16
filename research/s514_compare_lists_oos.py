#!/usr/bin/env python3
"""s514 OOS Monthly Comparison — BOTH_28 vs ROBUST_50 vs MAX_PNL_92.

Runs month-by-month OOS with compounding equity for three token lists,
then prints a side-by-side comparison table.

Usage:
    /workspace/venv/bin/python research/s514_compare_lists_oos.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000

BOTH_POSITIVE_28 = {
    'EIGEN', 'PENGU', 'INJ', 'LTC', 'ARB', 'SAND', 'AAVE', 'GUN',
    'RAYSOL', 'LINK', 'HBAR', 'RENDER', 'ETH', 'PIXEL', 'DOGE',
    'WLD', 'VANRY', 'ADA', 'AIOT', 'UNI', 'BTC', 'IP', 'NEAR',
    'ETC', 'MYX', 'PIPPIN', 'ZEN', 'RESOLV',
}

ROBUST_50 = {
    'ETH', 'BTC', 'ZEN', 'RENDER', 'EIGEN', 'AAVE', 'PIXEL', 'LTC', 'LINK',
    'VANRY', 'ADA', 'BAS', 'PHA', 'INJ', 'DOGE', 'HBAR', 'H', 'NEAR', 'ARB',
    'BANANAS31', 'SOL', 'AXS', 'JUP', 'CAKE', 'COS', 'APE', 'TON', 'UNI',
    'LDO', 'G', 'ENJ', 'VIRTUAL', 'STEEM', 'BCH', 'GALA', 'ALGO', 'WLD',
    'AVAX', 'SUI', 'ANKR', 'NEO', 'RESOLV', 'CRV', 'SAND', 'FLOW', 'BAN',
    'F', 'DOT', 'DASH', 'MINA',
}

MAX_PNL_92 = {
    'ETH', 'RESOLV', 'H', 'BTC', 'INJ', 'BAS', 'DOGE', 'ZEN', 'NEAR',
    'RENDER', 'EIGEN', 'HBAR', 'PHA', 'ARB', 'AAVE', 'AXS', 'PIXEL', 'APE',
    'BANANAS31', 'SOL', 'JUP', 'LTC', 'CAKE', 'COS', 'VANRY', 'TIA', 'TON',
    'UNI', 'G', 'ENJ', 'XRP', 'LDO', 'LINK', 'ADA', 'VIRTUAL', 'WLD',
    'STEEM', 'BCH', 'ALGO', 'GALA', 'AVAX', 'SUI', 'ANKR', 'NEO', 'CRV',
    'SAND', 'FLOW', 'BAN', 'F', 'DOT', 'DASH', 'FIL', 'AKT', 'APT',
    'MOODENG', 'CFX', 'XMR', 'MKR', 'KAVA', 'FET', 'GRASS', 'WIF', 'ENA',
    'BNB', 'KAS', 'DYDX', 'ENS', 'CHZ', 'DENT', 'TRX', 'RVN', 'IP', 'BLUR',
    'GUN', 'FORM', 'PENGU', 'ESP', 'D', 'ZK', 'OGN', 'PENDLE', 'XLM',
    'MORPHO', 'ETHFI', 'C', 'HYPE', 'TURBO', 'CC', 'SEI', 'LIGHT', 'STRK',
    'ONDO',
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


def run_oos_monthly_filtered(token_set, oos_months, label):
    """Run month-by-month OOS with compounding equity, filtered to token_set."""
    data_end = infer_data_end_date(MARKET)
    running_capital = CAPITAL
    results = []

    print(f"\n{'='*70}")
    print(f"  OOS MONTHLY -- {label}")
    print(f"  {oos_months} months, compounding, filtered to {len(token_set)} tokens")
    print(f"{'='*70}")

    tokens = discover_tokens(MARKET)

    for i in range(oos_months):
        month_offset = oos_months - 1 - i
        end_date = data_end - pd.DateOffset(months=month_offset)
        end_date = end_date + pd.offsets.MonthEnd(0)
        clamped = end_date > data_end
        end_date = min(end_date, data_end)

        label_suffix = " (partial)" if clamped else ""
        print(f"\n  Month {i+1}/{oos_months}: capital=${running_capital:,.0f}, "
              f"end_date={end_date.strftime('%Y-%m-%d')}{label_suffix}")

        spec = make_spec(token_set)
        config = PortfolioConfig(
            capital=running_capital,
            exchange='binance',
            skip_walk_forward=True,
            conviction_mode='ranked',
            max_portfolio_positions=min(len(token_set), 50),
            strategies=[spec],
        )

        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, 1, end_date=end_date)
        filtered_signals = {t: s for t, s in signals.items() if t in token_set}
        t1 = time.time()

        if not filtered_signals:
            print(f"    No signals for this month ({t1-t0:.1f}s)")
            results.append({
                "month": end_date.strftime("%Y-%m"),
                "start_capital": running_capital,
                "final_equity": running_capital,
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "trades": 0,
            })
            continue

        strategy_specs = {STRATEGY_ID: spec}
        state = simulate_portfolio({STRATEGY_ID: filtered_signals}, strategy_specs, config)
        metrics, extra_info, eq_daily = compute_portfolio_metrics(state, running_capital)

        final_equity = extra_info["final_equity"]
        total_return = (final_equity / running_capital - 1) * 100
        max_dd = metrics.max_drawdown_pct
        t2 = time.time()

        print(f"    {len(filtered_signals)} tokens, {metrics.total_trades} trades, "
              f"ret={total_return:+.1f}%, dd={max_dd:.1f}% ({t2-t0:.1f}s)")

        results.append({
            "month": end_date.strftime("%Y-%m"),
            "start_capital": running_capital,
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "trades": metrics.total_trades,
        })

        running_capital = final_equity

    return results, running_capital


def main():
    print("=" * 70)
    print("  s514 OOS Monthly Comparison -- 3 Token Lists")
    print("=" * 70)

    lists = [
        (BOTH_POSITIVE_28, "BOTH_28"),
        (ROBUST_50, "ROBUST_50"),
        (MAX_PNL_92, "MAX_PNL_92"),
    ]

    all_results = {}
    all_final = {}

    for token_set, label in lists:
        results, final_eq = run_oos_monthly_filtered(token_set, 12, label)
        all_results[label] = results
        all_final[label] = final_eq

    # Side-by-side comparison table
    print(f"\n{'='*90}")
    print("  SIDE-BY-SIDE OOS MONTHLY COMPARISON")
    print(f"{'='*90}")

    labels = [l for _, l in lists]
    header = f"  {'Month':>10s}"
    for l in labels:
        header += f"  | {'Return':>8s}  {'DD':>7s}"
    print(header)
    print(f"  {'-'*10}" + "  | " + ("  ".join([f"{'-'*8}  {'-'*7}"] * len(labels))))

    # Gather months from first list
    months = [r["month"] for r in all_results[labels[0]]]

    for mi, month in enumerate(months):
        row = f"  {month:>10s}"
        for l in labels:
            r = all_results[l][mi]
            row += f"  | {r['total_return_pct']:>+7.1f}%  {r['max_drawdown_pct']:>6.1f}%"
        print(row)

    # Totals
    print(f"  {'-'*10}" + "  | " + ("  ".join([f"{'-'*8}  {'-'*7}"] * len(labels))))
    row = f"  {'TOTAL':>10s}"
    for l in labels:
        cum = (all_final[l] / CAPITAL - 1) * 100
        row += f"  | {cum:>+7.1f}%  {'':>7s}"
    print(row)

    # Summary stats
    print(f"\n{'='*90}")
    print("  SUMMARY STATISTICS")
    print(f"{'='*90}")
    print(f"  {'Metric':<25s}", end="")
    for l in labels:
        print(f"  {l:>15s}", end="")
    print()
    print(f"  {'-'*25}" + "  ".join([f"  {'-'*15}"] * len(labels)))

    # Total return
    print(f"  {'Total Return':<25s}", end="")
    for l in labels:
        cum = (all_final[l] / CAPITAL - 1) * 100
        print(f"  {cum:>+14.1f}%", end="")
    print()

    # Final equity
    print(f"  {'Final Equity':<25s}", end="")
    for l in labels:
        print(f"  ${all_final[l]:>13,.0f}", end="")
    print()

    # Negative months
    print(f"  {'Negative Months':<25s}", end="")
    for l in labels:
        neg = sum(1 for r in all_results[l] if r["total_return_pct"] < 0)
        print(f"  {neg:>15d}", end="")
    print()

    # Worst month
    print(f"  {'Worst Month':<25s}", end="")
    for l in labels:
        worst = min(r["total_return_pct"] for r in all_results[l])
        worst_m = min(all_results[l], key=lambda r: r["total_return_pct"])["month"]
        print(f"  {worst:>+7.1f}% {worst_m}", end="")
    print()

    # Best month
    print(f"  {'Best Month':<25s}", end="")
    for l in labels:
        best = max(r["total_return_pct"] for r in all_results[l])
        best_m = max(all_results[l], key=lambda r: r["total_return_pct"])["month"]
        print(f"  {best:>+7.1f}% {best_m}", end="")
    print()

    # Worst intra-month DD
    print(f"  {'Worst Intra-Month DD':<25s}", end="")
    for l in labels:
        worst_dd = max(r["max_drawdown_pct"] for r in all_results[l])
        dd_m = max(all_results[l], key=lambda r: r["max_drawdown_pct"])["month"]
        print(f"  {worst_dd:>7.1f}% {dd_m}", end="")
    print()

    # Total trades
    print(f"  {'Total Trades':<25s}", end="")
    for l in labels:
        total_t = sum(r["trades"] for r in all_results[l])
        print(f"  {total_t:>15d}", end="")
    print()

    # Avg monthly return
    print(f"  {'Avg Monthly Return':<25s}", end="")
    for l in labels:
        avg = sum(r["total_return_pct"] for r in all_results[l]) / len(all_results[l])
        print(f"  {avg:>+14.1f}%", end="")
    print()

    print(f"\n{'='*90}")
    print("  Done.")


if __name__ == '__main__':
    main()
