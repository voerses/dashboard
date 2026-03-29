"""s501 Monthly P&L Breakdown
==============================

Runs the s501 backtest over the last 12 months and breaks down performance
by calendar month: return %, max drawdown, number of trades, win rate,
fees, and funding.

Also identifies the worst month and its biggest losing trades.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir('/workspace/crypto_backtest')

import numpy as np
import pandas as pd
from collections import defaultdict

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def main():
    # ------------------------------------------------------------------
    # 1. Setup and run backtest
    # ------------------------------------------------------------------
    capital = 100_000
    months = 12

    config = PortfolioConfig(
        capital=capital,
        exchange='binance',
        skip_walk_forward=True,
    )
    spec = StrategySpec(
        strategy_id='s501',
        market='perp',
        strategy_type='portfolio',
        max_positions=5,
        entry_resolution=1,
    )

    tokens = get_all_tradeable('perp')
    print(f"Universe: {len(tokens)} perp tokens")
    print(f"Capital: ${capital:,}")
    print(f"Months: {months}")
    print(f"Running backtest...")

    signals = precompute_portfolio_signals(spec, tokens, config, months=months)
    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)

    trades = state.position_manager.closed_trades
    snapshots = state.equity_snapshots  # list of (timestamp, mtm_equity)

    print(f"Backtest complete: {len(trades)} trades, {len(snapshots)} equity snapshots")
    print(f"Final equity: ${state.portfolio_equity:,.0f} ({(state.portfolio_equity / capital - 1) * 100:+.1f}%)")
    print()

    # ------------------------------------------------------------------
    # 2. Build equity timeseries as pandas Series
    # ------------------------------------------------------------------
    if not snapshots:
        print("ERROR: No equity snapshots recorded.")
        return

    eq_timestamps = pd.DatetimeIndex([s[0] for s in snapshots])
    eq_values = np.array([s[1] for s in snapshots], dtype=float)
    equity_series = pd.Series(eq_values, index=eq_timestamps, name='equity')

    # Build a bar-index-to-timestamp mapping for assigning trades to months
    bar_to_ts = {i: eq_timestamps[i] for i in range(len(eq_timestamps))}

    # ------------------------------------------------------------------
    # 3. Assign each trade to a month (by exit timestamp)
    # ------------------------------------------------------------------
    trades_by_month = defaultdict(list)
    for t in trades:
        exit_bar = t.exit_bar
        if exit_bar in bar_to_ts:
            exit_ts = bar_to_ts[exit_bar]
        elif exit_bar < len(eq_timestamps):
            exit_ts = eq_timestamps[exit_bar]
        else:
            exit_ts = eq_timestamps[-1]
        month_key = exit_ts.to_period('M')
        trades_by_month[month_key].append(t)

    # ------------------------------------------------------------------
    # 4. Monthly equity stats
    # ------------------------------------------------------------------
    equity_monthly = equity_series.resample('MS').agg(['first', 'last', 'min', 'max'])

    # Compute monthly return and max drawdown from sub-monthly equity curve
    monthly_stats = []
    grouped = equity_series.groupby(equity_series.index.to_period('M'))

    for period, group in grouped:
        start_eq = group.iloc[0]
        end_eq = group.iloc[-1]
        monthly_ret = (end_eq / start_eq - 1) * 100

        # Max drawdown within the month
        running_max = group.cummax()
        drawdowns = (group - running_max) / running_max * 100
        max_dd = drawdowns.min()

        # Trade stats for this month
        month_trades = trades_by_month.get(period, [])
        n_trades = len(month_trades)

        if n_trades > 0:
            pnls = np.array([t.pnl for t in month_trades])
            win_rate = (pnls > 0).mean() * 100
            total_fees = sum(t.entry_fee + t.exit_fee for t in month_trades)
            total_funding = sum(t.funding_cost for t in month_trades)
            avg_margin = np.mean([t.margin_usd for t in month_trades])
            total_pnl = pnls.sum()
        else:
            win_rate = 0.0
            total_fees = 0.0
            total_funding = 0.0
            avg_margin = 0.0
            total_pnl = 0.0

        monthly_stats.append({
            'month': str(period),
            'start_eq': start_eq,
            'end_eq': end_eq,
            'return_pct': monthly_ret,
            'max_dd_pct': max_dd,
            'n_trades': n_trades,
            'win_rate': win_rate,
            'fees': total_fees,
            'funding': total_funding,
            'avg_margin': avg_margin,
            'total_pnl': total_pnl,
        })

    # ------------------------------------------------------------------
    # 5. Print monthly table
    # ------------------------------------------------------------------
    print("=" * 120)
    print(f"{'Month':<10} {'Return%':>8} {'MaxDD%':>8} {'Trades':>7} {'WinRate%':>9} "
          f"{'Fees':>10} {'Funding':>10} {'Net PnL':>12} {'End Equity':>12}")
    print("-" * 120)

    for s in monthly_stats:
        print(f"{s['month']:<10} {s['return_pct']:>+7.2f}% {s['max_dd_pct']:>+7.2f}% "
              f"{s['n_trades']:>7d} {s['win_rate']:>8.1f}% "
              f"${s['fees']:>9,.0f} ${s['funding']:>9,.0f} "
              f"${s['total_pnl']:>11,.0f} ${s['end_eq']:>11,.0f}")

    print("-" * 120)

    # Overall summary
    total_trades = sum(s['n_trades'] for s in monthly_stats)
    total_months = len(monthly_stats)
    avg_trades_per_month = total_trades / total_months if total_months > 0 else 0
    all_margins = [t.margin_usd for t in trades]
    overall_avg_margin = np.mean(all_margins) if all_margins else 0

    print(f"\nOverall Summary:")
    print(f"  Total trades:          {total_trades}")
    print(f"  Trades/month:          {avg_trades_per_month:.1f}")
    print(f"  Avg trade size:        ${overall_avg_margin:,.0f} (margin_usd)")
    print(f"  Total fees:            ${state.total_fees:,.0f}")
    print(f"  Total funding:         ${state.total_funding:,.0f}")
    print(f"  Final equity:          ${state.portfolio_equity:,.0f}")
    print(f"  Total return:          {(state.portfolio_equity / capital - 1) * 100:+.2f}%")

    # ------------------------------------------------------------------
    # 6. Worst month analysis
    # ------------------------------------------------------------------
    worst = min(monthly_stats, key=lambda s: s['return_pct'])
    print(f"\n{'=' * 80}")
    print(f"WORST MONTH: {worst['month']}")
    print(f"  Return: {worst['return_pct']:+.2f}%  |  Max DD: {worst['max_dd_pct']:+.2f}%")
    print(f"  Trades: {worst['n_trades']}  |  Win Rate: {worst['win_rate']:.1f}%")
    print(f"  Fees: ${worst['fees']:,.0f}  |  Funding: ${worst['funding']:,.0f}")
    print(f"{'=' * 80}")

    worst_period = None
    for period in trades_by_month:
        if str(period) == worst['month']:
            worst_period = period
            break

    if worst_period and trades_by_month[worst_period]:
        worst_trades = trades_by_month[worst_period]
        # Sort by PnL ascending to show biggest losers first
        sorted_trades = sorted(worst_trades, key=lambda t: t.pnl)
        n_show = min(10, len(sorted_trades))
        print(f"\nBiggest losing trades in {worst['month']}:")
        print(f"  {'Token':<12} {'Dir':>4} {'Entry$':>10} {'Exit$':>10} "
              f"{'PnL':>10} {'Margin':>8} {'Hold':>5} {'Reason':<15}")
        print(f"  {'-' * 80}")

        for t in sorted_trades[:n_show]:
            dir_str = "LONG" if t.direction == 1 else "SHORT"
            print(f"  {t.token:<12} {dir_str:>4} {t.entry_price:>10.4f} {t.exit_price:>10.4f} "
                  f"${t.pnl:>9,.0f} ${t.margin_usd:>7,.0f} {t.hold_bars:>5d} {t.exit_reason:<15}")

        # Also show the winning trades to give context
        winners = [t for t in worst_trades if t.pnl > 0]
        losers = [t for t in worst_trades if t.pnl <= 0]
        print(f"\n  Winners: {len(winners)} trades, total PnL: ${sum(t.pnl for t in winners):,.0f}")
        print(f"  Losers:  {len(losers)} trades, total PnL: ${sum(t.pnl for t in losers):,.0f}")

    # ------------------------------------------------------------------
    # 7. Best month for comparison
    # ------------------------------------------------------------------
    best = max(monthly_stats, key=lambda s: s['return_pct'])
    print(f"\n{'=' * 80}")
    print(f"BEST MONTH: {best['month']}")
    print(f"  Return: {best['return_pct']:+.2f}%  |  Max DD: {best['max_dd_pct']:+.2f}%")
    print(f"  Trades: {best['n_trades']}  |  Win Rate: {best['win_rate']:.1f}%")
    print(f"  Fees: ${best['fees']:,.0f}  |  Funding: ${best['funding']:,.0f}")
    print(f"{'=' * 80}")


if __name__ == '__main__':
    main()
