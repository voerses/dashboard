"""
s501 R172 V4 Portfolio — Full Validation Script
================================================

Validates s501 (Class B portfolio strategy) through the v4 engine with:
1. Maximum OOS period backtest with monthly P&L breakdown
2. Walk-forward validation
3. Multiple time windows for robustness

Config: pos=50, lev=1.25, hold=4, edge=0.50, conviction=ranked, entry_resolution=1

Usage:
    /workspace/venv/bin/python research/s501_v4_validation.py
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


def run_validation(tokens, months=12, end_date=None, skip_wf=False,
                   max_positions=50, capital=100_000, label="default",
                   print_monthly=False):
    """Run s501 validation with given parameters."""
    config = PortfolioConfig(
        capital=capital,
        exchange='binance',
        skip_walk_forward=skip_wf,
        conviction_mode='ranked',
        train_bars=2160 if not skip_wf else 0,   # 90 days
        recal_bars=720 if not skip_wf else 0,     # 30 days
        purge_bars=24 if not skip_wf else 0,      # 1 day
    )

    spec = StrategySpec(
        strategy_id='s501',
        market='perp',
        strategy_type='portfolio',
        max_positions=max_positions,
        entry_resolution=1,  # 1m cross detection for BB breakout entries
    )

    t0 = time.perf_counter()
    signals = precompute_portfolio_signals(spec, tokens, config, months, end_date=end_date)
    t1 = time.perf_counter()

    if not signals:
        print(f"  [{label}] No signals produced!")
        return None

    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)
    t2 = time.perf_counter()

    metrics = compute_metrics(state.equity_snapshots, capital)
    if metrics is None:
        print(f"  [{label}] Insufficient equity data")
        return None

    metrics['tokens_with_signals'] = len(signals)
    metrics['total_tokens'] = len(tokens)
    metrics['signal_time'] = t1 - t0
    metrics['sim_time'] = t2 - t1
    metrics['realized_pnl'] = float(state.realized_pnl)
    metrics['total_fees'] = float(state.total_fees)
    metrics['total_funding'] = float(state.total_funding)
    metrics['margin_calls'] = state.margin_calls
    metrics['label'] = label
    metrics['skip_wf'] = skip_wf
    metrics['n_trades'] = len(state.position_manager.closed_trades)

    if print_monthly:
        monthly_breakdown(state, capital)

    return metrics


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

    # Verdict
    passes = []
    fails = []
    if m['sharpe'] >= 2.0: passes.append('Sharpe')
    else: fails.append(f"Sharpe {m['sharpe']:.2f} < 2.0")
    if m['calmar'] >= 0.5: passes.append('Calmar')
    else: fails.append(f"Calmar {m['calmar']:.2f} < 0.5")
    if m['max_dd'] >= -25: passes.append('MaxDD')
    else: fails.append(f"MaxDD {m['max_dd']:.1f}% > -25%")
    if m['total_return'] > 0: passes.append('Return')
    else: fails.append(f"Return {m['total_return']:.1f}% <= 0")
    if m['pf'] >= 1.3: passes.append('PF')
    else: fails.append(f"PF {m['pf']:.2f} < 1.3")

    verdict = "PASS" if len(fails) == 0 else "FAIL"
    print(f"  Verdict:   {verdict} ({len(passes)}/{len(passes)+len(fails)})")
    if fails:
        for f in fails:
            print(f"    FAIL: {f}")


def main():
    print("=" * 70)
    print("  s501 V4 — pos=50, lev=1.25, hold=4, edge=0.50, ranked")
    print("=" * 70)

    all_tokens = get_all_tradeable()
    print(f"\nFull universe: {len(all_tokens)} tokens")

    results = {}

    # ── Test 1: Maximum OOS period with monthly breakdown ──
    # Use 36 months (3 years) — engine trims per-token to available data
    print("\n" + "=" * 70)
    print("  TEST 1: Maximum OOS Period (36 months) + Monthly Breakdown")
    print("=" * 70)
    m = run_validation(all_tokens, months=36, skip_wf=True,
                      max_positions=50, label="max_oos_36m",
                      print_monthly=True)
    print_metrics(m, "36-Month OOS (No Walk-Forward)")
    results['max_oos_36m'] = m

    # ── Test 2: 24 months with monthly breakdown ──
    print("\n" + "=" * 70)
    print("  TEST 2: 24-Month Period + Monthly Breakdown")
    print("=" * 70)
    m = run_validation(all_tokens, months=24, skip_wf=True,
                      max_positions=50, label="oos_24m",
                      print_monthly=True)
    print_metrics(m, "24-Month OOS (No Walk-Forward)")
    results['oos_24m'] = m

    # ── Test 3: 12 months (baseline comparison) ──
    print("\n" + "=" * 70)
    print("  TEST 3: 12-Month Baseline")
    print("=" * 70)
    m = run_validation(all_tokens, months=12, skip_wf=True,
                      max_positions=50, label="oos_12m",
                      print_monthly=True)
    print_metrics(m, "12-Month OOS (No Walk-Forward)")
    results['oos_12m'] = m

    # ── Test 4: Walk-forward (18 months = 3mo train + 15mo test) ──
    print("\n" + "=" * 70)
    print("  TEST 4: Walk-Forward (18 months)")
    print("=" * 70)
    m = run_validation(all_tokens, months=18, skip_wf=False,
                      max_positions=50, label="wf_18m",
                      print_monthly=True)
    print_metrics(m, "18-Month Walk-Forward")
    results['wf_18m'] = m

    # ── Summary ──
    print("\n" + "=" * 70)
    print("  VALIDATION SUMMARY")
    print("=" * 70)
    print(f"\n{'Label':<20} {'Return':>9} {'MaxDD':>7} {'Sharpe':>7} "
          f"{'Calmar':>7} {'Trades':>7} {'Verdict':>8}")
    print("-" * 70)
    for label, m in results.items():
        if m is None:
            print(f"{label:<20} {'FAILED':>9}")
            continue
        v = "PASS" if (m['sharpe'] >= 2.0 and m['calmar'] >= 0.5
                       and m['max_dd'] >= -25 and m['total_return'] > 0) else "FAIL"
        print(f"{label:<20} {m['total_return']:>+8.1f}% {m['max_dd']:>6.1f}% "
              f"{m['sharpe']:>7.2f} {m['calmar']:>7.2f} {m.get('n_trades', 0):>7d} {v:>8}")

    # Save results
    os.makedirs('results', exist_ok=True)
    output = {k: v for k, v in results.items() if v is not None}
    with open('results/s501_v4_validation.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to results/s501_v4_validation.json")


if __name__ == '__main__':
    main()
