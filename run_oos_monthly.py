#!/usr/bin/env python3
"""True OOS month-by-month backtest: recompute signals each month with hard data cap.

For each month, signals are computed using ONLY data available up to the end of
that month. No forward-looking bias — each month's signal computation cannot
see any data beyond its own end date.
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import (
    build_unified_index, SimulationState,
    _process_exits, _process_entries, _process_margin_calls,
    _record_equity_snapshot, _close_all_remaining,
)
from v4.report import compute_portfolio_metrics


def run_single_month(strategy_id, spec, config, tokens, end_date, lookback_months=14):
    """Run backtest with signals capped at end_date, return equity snapshots."""
    all_signals = {
        strategy_id: precompute_strategy_signals(
            spec, tokens, config, lookback_months, end_date=end_date,
        )
    }
    n_tokens = len(all_signals[strategy_id])
    if n_tokens == 0:
        return None, 0

    strategy_specs = {strategy_id: spec}
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)
    state = SimulationState(initial_capital=config.capital)
    rng = np.random.RandomState(config.seed)

    for global_bar in range(n_bars):
        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)

    if not state.equity_snapshots:
        return None, n_tokens

    ts_list = [s[0] for s in state.equity_snapshots]
    eq_list = [s[1] for s in state.equity_snapshots]
    eq_series = pd.Series(eq_list, index=pd.DatetimeIndex(ts_list))
    eq_daily = eq_series.resample('D').last().ffill()
    return eq_daily, n_tokens


def main():
    strategy_id = "s501"
    capital = 200_000

    data_end = infer_data_end_date("perp")
    print(f"Strategy: {strategy_id}")
    print(f"Latest data: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Capital: ${capital:,.0f}")
    print()

    spec = StrategySpec(
        strategy_id=strategy_id,
        weight=1.0,
        max_positions=15,
        market='perp',
        strategy_type='portfolio',
        adv_sizing_enabled=True,
        adv_sizing_base=75_000_000,
    )

    config = PortfolioConfig(
        capital=capital,
        max_portfolio_positions=15,
        concentration_limit=0.20,
        adv_cap_pct=0.05,
        seed=42,
        max_sizing_equity=2_000_000,
        stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )

    tokens = discover_tokens(spec.market)
    print(f"Token universe: {len(tokens)} tokens")

    # Generate month-end dates for the last 12 months
    month_ends = pd.date_range(
        end=data_end.normalize(),
        periods=13,  # 13 to get 12 OOS months
        freq='ME',
    )

    print(f"\n{'='*72}")
    print(f"  TRUE OOS MONTH-BY-MONTH BACKTEST (signals recomputed each month)")
    print(f"{'='*72}")
    print(f"  Each month: signals computed with data capped at month-end.")
    print(f"  No data beyond the cap date is visible to the strategy.")
    print(f"{'='*72}\n")

    results = []
    cumulative_return = 0.0
    total_time = 0.0

    for i in range(1, len(month_ends)):
        end = pd.Timestamp(month_ends[i])
        month_label = end.strftime('%Y-%m')

        t0 = time.time()
        eq_daily, n_tokens = run_single_month(
            strategy_id, spec, config, tokens, end_date=end, lookback_months=14,
        )
        elapsed = time.time() - t0
        total_time += elapsed

        if eq_daily is None or len(eq_daily) < 2:
            print(f"  {month_label}:  NO DATA  ({elapsed:.1f}s)")
            results.append((month_label, 0.0, 0.0, 0, elapsed))
            continue

        # Extract ONLY this month's performance from the equity curve
        month_mask = eq_daily.index.to_period('M') == end.to_period('M')
        month_eq = eq_daily[month_mask]

        if len(month_eq) < 2:
            # Use last value before this month as starting point
            before_month = eq_daily[eq_daily.index < month_eq.index[0]]
            if len(before_month) > 0:
                start_val = before_month.iloc[-1]
            else:
                start_val = capital
            end_val = month_eq.iloc[-1] if len(month_eq) > 0 else start_val
        else:
            # Start of month equity = last value from previous month (or initial capital)
            before_month = eq_daily[eq_daily.index < month_eq.index[0]]
            start_val = before_month.iloc[-1] if len(before_month) > 0 else capital
            end_val = month_eq.iloc[-1]

        month_return = (end_val / start_val - 1) * 100 if start_val > 0 else 0.0

        # Max drawdown this month
        if len(month_eq) >= 2:
            peak = month_eq.cummax()
            dd = ((month_eq - peak) / peak).min() * 100
        else:
            dd = 0.0

        cumulative_return = (eq_daily.iloc[-1] / capital - 1) * 100

        print(
            f"  {month_label}:  {month_return:>+7.1f}%  "
            f"DD={dd:>6.1f}%  "
            f"tokens={n_tokens:<3d}  "
            f"cap={end.strftime('%Y-%m-%d')}  "
            f"({elapsed:.1f}s)"
        )
        results.append((month_label, month_return, dd, n_tokens, elapsed))

    # Summary
    monthly_returns = [r[1] for r in results]
    positive_months = sum(1 for r in monthly_returns if r > 0)
    negative_months = sum(1 for r in monthly_returns if r < 0)
    flat_months = sum(1 for r in monthly_returns if r == 0)
    avg_return = np.mean(monthly_returns) if monthly_returns else 0
    worst_month = min(monthly_returns) if monthly_returns else 0
    best_month = max(monthly_returns) if monthly_returns else 0
    worst_dd = min(r[2] for r in results) if results else 0

    print(f"\n{'='*72}")
    print(f"  SUMMARY")
    print(f"{'='*72}")
    print(f"  Months: {positive_months} up / {negative_months} down / {flat_months} flat")
    print(f"  Avg monthly return:   {avg_return:>+7.2f}%")
    print(f"  Best month:           {best_month:>+7.1f}%")
    print(f"  Worst month:          {worst_month:>+7.1f}%")
    print(f"  Worst monthly DD:     {worst_dd:>7.1f}%")
    print(f"  Total wall-clock:     {total_time:>7.1f}s")
    print(f"{'='*72}")


if __name__ == "__main__":
    print(
        "\n  DEPRECATED: run_oos_monthly.py is deprecated. "
        "Use the --oos-monthly flag in v4/portfolio_backtest.py instead.\n"
        "  Example: python v4/portfolio_backtest.py --strategy s501 --market perp --oos-monthly --months 6\n"
        "  Continuing in 3 seconds...\n",
        file=sys.stderr,
    )
    if "--help" in sys.argv or "-h" in sys.argv:
        sys.exit(0)
    time.sleep(3)
    main()
