"""
s502 Baseline Comparison — Hourly vs 1m Entry Resolution
=========================================================

Compares s502 (Honest BB Breakout) performance with:
  1. entry_resolution=0 (hourly only baseline)
  2. entry_resolution=1 (1m honest entry)

Also runs L12M/L6M/L3M windows for the 1m entry version.

Usage:
    /workspace/venv/bin/python research/s502_baseline_comparison.py
"""

import sys
import os
import time
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'v4'))

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def compute_metrics(equity_snapshots, capital=100_000):
    """Compute strategy metrics from equity snapshots."""
    eq = np.array([s[1] for s in equity_snapshots], dtype=np.float64)
    ts = np.array([s[0] for s in equity_snapshots])
    n = len(eq)

    if n < 100:
        return None

    total_ret = (eq[-1] / eq[0] - 1)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = dd.min()

    # Hourly returns -> annualized
    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-10)
    mean_ret = np.mean(rets)
    std_ret = np.std(rets)
    sharpe = mean_ret / max(std_ret, 1e-10) * np.sqrt(8760)

    neg_rets = rets[rets < 0]
    downside = np.sqrt(np.mean(neg_rets**2)) if len(neg_rets) > 0 else 1e-10
    sortino = mean_ret / downside * np.sqrt(8760)

    calmar = total_ret / max(abs(max_dd), 1e-10)

    # Win rate (hourly)
    win_rate = np.mean(rets > 0) if len(rets) > 0 else 0

    # Profit factor
    gross_profit = np.sum(rets[rets > 0])
    gross_loss = abs(np.sum(rets[rets < 0]))
    pf = gross_profit / max(gross_loss, 1e-10)

    return {
        'total_return': total_ret * 100,
        'max_dd': max_dd * 100,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'pf': pf,
        'win_rate': win_rate * 100,
        'n_bars': n,
        'final_equity': float(eq[-1]),
        'start': str(ts[0]),
        'end': str(ts[-1]),
    }


def monthly_breakdown(state, capital=100_000):
    """Print monthly P&L breakdown from equity snapshots."""
    eq_snaps = state.equity_snapshots
    if len(eq_snaps) < 100:
        print("  Insufficient data for monthly breakdown")
        return

    # Build DataFrame from equity snapshots
    ts = [s[0] for s in eq_snaps]
    eq = [s[1] for s in eq_snaps]
    df = pd.DataFrame({'equity': eq}, index=pd.DatetimeIndex(ts))

    # Group by month
    monthly = df.resample('MS').agg({'equity': ['first', 'last']})
    monthly.columns = ['eq_start', 'eq_end']
    monthly['return_pct'] = (monthly['eq_end'] / monthly['eq_start'] - 1) * 100
    monthly['return_usd'] = monthly['eq_end'] - monthly['eq_start']

    # Compute running peak and drawdown per month
    eq_arr = df['equity'].values
    peak = np.maximum.accumulate(eq_arr)
    dd = (eq_arr - peak) / peak

    dd_series = pd.Series(dd, index=df.index)
    monthly['max_dd'] = dd_series.resample('MS').min() * 100

    # Map trades to months via bar index -> equity snapshot timestamp
    trades = state.position_manager.closed_trades
    trade_months = {}
    trade_pnls = {}
    n_snaps = len(ts)
    for t in trades:
        bar = min(t.exit_bar, n_snaps - 1)
        exit_time = ts[bar]
        m_key = pd.Timestamp(exit_time).replace(day=1, hour=0, minute=0,
                                                 second=0, microsecond=0)
        trade_months[m_key] = trade_months.get(m_key, 0) + 1
        if m_key not in trade_pnls:
            trade_pnls[m_key] = []
        trade_pnls[m_key].append(t.pnl)

    print(f"\n{'Month':<12} {'Return':>9} {'$ P&L':>10} {'MaxDD':>7} "
          f"{'Trades':>7} {'WR':>6} {'Equity':>12}")
    print("-" * 75)

    total_trades = 0
    months_positive = 0
    months_total = 0

    for idx, row in monthly.iterrows():
        m_key = idx.to_pydatetime().replace(tzinfo=None)
        m_key = pd.Timestamp(m_key)
        n_trades = trade_months.get(m_key, 0)
        pnls = trade_pnls.get(m_key, [])
        wr = (sum(1 for p in pnls if p > 0) / len(pnls) * 100) if pnls else 0.0

        total_trades += n_trades
        months_total += 1
        if row['return_pct'] > 0:
            months_positive += 1

        print(f"{idx.strftime('%Y-%m'):<12} {row['return_pct']:>+8.1f}% "
              f"${row['return_usd']:>+9,.0f} {row['max_dd']:>6.1f}% "
              f"{n_trades:>7d} {wr:>5.1f}% ${row['eq_end']:>11,.0f}")

    print("-" * 75)
    print(f"{'TOTAL':<12} {(monthly['eq_end'].iloc[-1] / monthly['eq_start'].iloc[0] - 1) * 100:>+8.1f}% "
          f"${monthly['eq_end'].iloc[-1] - monthly['eq_start'].iloc[0]:>+9,.0f} "
          f"{'':>7s} {total_trades:>7d} {'':>6s} ${monthly['eq_end'].iloc[-1]:>11,.0f}")
    print(f"\nMonths positive: {months_positive}/{months_total} "
          f"({months_positive/max(months_total,1)*100:.0f}%)")


def run_backtest(tokens, months=12, entry_resolution=1, label="default"):
    """Run s502 backtest with given parameters."""
    config = PortfolioConfig(
        capital=100_000,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
    )

    spec = StrategySpec(
        strategy_id='s502',
        market='perp',
        strategy_type='portfolio',
        max_positions=50,
        entry_resolution=entry_resolution,
    )

    print(f"\n{'='*70}")
    print(f"  Running: {label}")
    print(f"  entry_resolution={entry_resolution}, months={months}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    signals = precompute_portfolio_signals(spec, tokens, config, months)
    t1 = time.perf_counter()

    if not signals:
        print(f"  [{label}] No signals produced!")
        return None, None

    state = simulate_portfolio({'s502': signals}, {'s502': spec}, config)
    t2 = time.perf_counter()

    metrics = compute_metrics(state.equity_snapshots, 100_000)
    if metrics is None:
        print(f"  [{label}] Insufficient equity data")
        return None, None

    metrics['tokens_with_signals'] = len(signals)
    metrics['total_tokens'] = len(tokens)
    metrics['signal_time'] = t1 - t0
    metrics['sim_time'] = t2 - t1
    metrics['realized_pnl'] = float(state.realized_pnl)
    metrics['total_fees'] = float(state.total_fees)
    metrics['total_funding'] = float(state.total_funding)
    metrics['margin_calls'] = state.margin_calls
    metrics['label'] = label
    metrics['entry_resolution'] = entry_resolution
    metrics['months'] = months
    metrics['n_trades'] = len(state.position_manager.closed_trades)

    return metrics, state


def print_metrics(m, header=""):
    """Pretty-print metrics."""
    if m is None:
        print(f"  {header}: FAILED")
        return
    print(f"\n{'='*60}")
    print(f"  {header}")
    print(f"{'='*60}")
    print(f"  Return:    {m['total_return']:+.1f}%")
    print(f"  Max DD:    {m['max_dd']:.1f}%")
    print(f"  Sharpe:    {m['sharpe']:.2f}")
    print(f"  Sortino:   {m['sortino']:.2f}")
    print(f"  Calmar:    {m['calmar']:.2f}")
    print(f"  PF:        {m['pf']:.2f}")
    print(f"  Win rate:  {m['win_rate']:.1f}%")
    print(f"  Trades:    {m.get('n_trades', 'N/A')}")
    print(f"  Tokens:    {m['tokens_with_signals']}/{m['total_tokens']}")
    print(f"  Fees:      ${m['total_fees']:,.0f}")
    print(f"  Funding:   ${m['total_funding']:,.0f}")
    print(f"  Margin calls: {m['margin_calls']}")
    print(f"  Period:    {m['start']} to {m['end']}")
    print(f"  Time:      {m['signal_time']:.1f}s signals + {m['sim_time']:.1f}s sim")


def main():
    print("=" * 70)
    print("  s502 Baseline Comparison — Hourly vs 1m Entry Resolution")
    print("=" * 70)

    all_tokens = get_all_tradeable()
    print(f"\nFull universe: {len(all_tokens)} tokens")

    results = {}

    # ══════════════════════════════════════════════════════════════════════
    # TEST 1: entry_resolution=0 (hourly baseline), 12 months
    # ══════════════════════════════════════════════════════════════════════
    m, state = run_backtest(all_tokens, months=12, entry_resolution=0,
                            label="hourly_baseline_12m")
    print_metrics(m, "s502 Hourly Baseline (entry_resolution=0, 12M)")
    if state is not None:
        monthly_breakdown(state, 100_000)
    results['hourly_baseline_12m'] = m

    # ══════════════════════════════════════════════════════════════════════
    # TEST 2: entry_resolution=1 (1m honest entry), 12 months
    # ══════════════════════════════════════════════════════════════════════
    m, state = run_backtest(all_tokens, months=12, entry_resolution=1,
                            label="1m_entry_12m")
    print_metrics(m, "s502 1m Honest Entry (entry_resolution=1, 12M)")
    if state is not None:
        monthly_breakdown(state, 100_000)
    results['1m_entry_12m'] = m

    # ══════════════════════════════════════════════════════════════════════
    # COMPARISON: Hourly vs 1m Entry (12M)
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  COMPARISON: Hourly vs 1m Entry (12 Months)")
    print("=" * 70)
    h = results.get('hourly_baseline_12m')
    e = results.get('1m_entry_12m')
    if h is not None and e is not None:
        print(f"\n{'Metric':<18} {'Hourly (0)':>14} {'1m Entry (1)':>14} {'Delta':>12}")
        print("-" * 62)
        for key, fmt in [
            ('total_return', '{:+.1f}%'),
            ('sharpe', '{:.2f}'),
            ('sortino', '{:.2f}'),
            ('calmar', '{:.2f}'),
            ('max_dd', '{:.1f}%'),
            ('pf', '{:.2f}'),
            ('win_rate', '{:.1f}%'),
            ('n_trades', '{:d}'),
            ('total_fees', '${:,.0f}'),
            ('total_funding', '${:,.0f}'),
        ]:
            hv = h[key]
            ev = e[key]
            if isinstance(hv, (int, np.integer)):
                delta = f"{ev - hv:+d}"
            else:
                delta = f"{ev - hv:+.2f}"
            print(f"  {key:<16} {fmt.format(hv):>14} {fmt.format(ev):>14} {delta:>12}")
    else:
        print("  One or both runs failed -- cannot compare")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 3: 1m entry across multiple windows (L12M/L6M/L3M)
    # ══════════════════════════════════════════════════════════════════════
    for months, label in [(12, "1m_entry_L12M"), (6, "1m_entry_L6M"), (3, "1m_entry_L3M")]:
        if months == 12 and '1m_entry_12m' in results and results['1m_entry_12m'] is not None:
            # Already ran 12M above, reuse
            results[label] = results['1m_entry_12m']
            print(f"\n  (Reusing 12M result for {label})")
            continue
        m, state = run_backtest(all_tokens, months=months, entry_resolution=1,
                                label=label)
        print_metrics(m, f"s502 1m Entry ({months}M Window)")
        if state is not None:
            monthly_breakdown(state, 100_000)
        results[label] = m

    # ══════════════════════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  SUMMARY — All Runs")
    print("=" * 70)
    print(f"\n{'Label':<22} {'Return':>9} {'MaxDD':>7} {'Sharpe':>7} "
          f"{'Sortino':>8} {'Calmar':>7} {'PF':>6} {'WR':>6} {'Trades':>7}")
    print("-" * 85)
    for label, m in results.items():
        if m is None:
            print(f"  {label:<20} {'FAILED':>9}")
            continue
        print(f"  {label:<20} {m['total_return']:>+8.1f}% {m['max_dd']:>6.1f}% "
              f"{m['sharpe']:>7.2f} {m['sortino']:>8.2f} {m['calmar']:>7.2f} "
              f"{m['pf']:>6.2f} {m['win_rate']:>5.1f}% {m.get('n_trades', 0):>7d}")

    # ══════════════════════════════════════════════════════════════════════
    # SAVE RESULTS
    # ══════════════════════════════════════════════════════════════════════
    os.makedirs('results/v4', exist_ok=True)
    output = {k: v for k, v in results.items() if v is not None}
    with open('results/v4/s502_baseline_comparison.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to results/v4/s502_baseline_comparison.json")


if __name__ == '__main__':
    main()
