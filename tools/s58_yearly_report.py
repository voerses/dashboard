#!/usr/bin/env python3
"""Run s58 backtest for a specific year and produce report with fee breakdown."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime

from strategies.s58_multi_strategy_portfolio import (
    _get_momentum_trades, _get_carry_trades, _load_v3
)
from v3.universe import get_fee_rate, get_all_tradeable


def run_filtered(capital=200_000, year=2025, exchange='binance'):
    """Run s58 backtest, filter trades to a single year, simulate portfolio."""
    portfolio_mod = _load_v3('portfolio')
    universe_mod = _load_v3('universe')
    simulate_portfolio = portfolio_mod.simulate_portfolio

    perp_tokens = universe_mod.get_all_tradeable('perp')
    print(f"\nS58 Portfolio — {year} only")
    print(f"  Capital: ${capital:,.0f} | Tokens: {len(perp_tokens)}")

    # Extract all trades (walk-forward needs full history for training)
    import time
    t0 = time.time()
    print(f"  Extracting momentum trades (s56)...", end='', flush=True)
    mom_trades = _get_momentum_trades(perp_tokens, 'data', capital, exchange)
    mom_total = sum(len(t) for t in mom_trades.values())
    print(f" {len(mom_trades)} tokens, {mom_total} trades ({time.time()-t0:.0f}s)")

    t1 = time.time()
    print(f"  Extracting carry trades (s57)...", end='', flush=True)
    carry_trades = _get_carry_trades(perp_tokens, 'data', capital, exchange)
    carry_total = sum(len(t) for t in carry_trades.values())
    print(f" {len(carry_trades)} tokens, {carry_total} trades ({time.time()-t1:.0f}s)")

    # Pool + filter to target year
    all_token_trades = {}
    filtered_count = 0
    total_pre_filter = 0

    for key, trades in mom_trades.items():
        token = key.replace('_mom', '')
        year_trades = [t for t in trades if t['entry_time'].year == year]
        total_pre_filter += len(trades)
        filtered_count += len(year_trades)
        if year_trades:
            all_token_trades.setdefault(token, []).extend(year_trades)

    for key, trades in carry_trades.items():
        token = key.replace('_carry', '')
        year_trades = [t for t in trades if t['entry_time'].year == year]
        total_pre_filter += len(trades)
        filtered_count += len(year_trades)
        if year_trades:
            all_token_trades.setdefault(token, []).extend(year_trades)

    total = sum(len(t) for t in all_token_trades.values())
    print(f"  Filtered: {total} trades in {year} "
          f"(from {total_pre_filter} total, {len(all_token_trades)} tokens)")

    if not all_token_trades:
        print("  No trades. Exiting.")
        return None

    # Simulate portfolio
    fee_rate = get_fee_rate(exchange, 'perp', 'taker')
    print(f"  Fee rate: {fee_rate*100:.3f}%")
    print(f"  Simulating portfolio...", end='', flush=True)
    portfolio_eq, accepted, skipped, sim_info = simulate_portfolio(
        all_token_trades, capital, max_token_pct=0.15,
        min_position_usd=1000, fee_rate=fee_rate)
    print(f" {len(accepted)} accepted, {len(skipped)} skipped")

    return {
        'portfolio_equity': portfolio_eq,
        'accepted_trades': accepted,
        'skipped_trades': skipped,
        'info': sim_info,
        'fee_rate': fee_rate,
        'year': year,
    }


def report(results, capital=200_000):
    portfolio_eq = results['portfolio_equity']
    accepted = results['accepted_trades']
    info = results['info']
    fee_rate = results['fee_rate']
    year = results['year']

    # --- Compute fee breakdown ---
    total_pnl = 0
    total_entry_fees = 0
    total_exit_fees = 0
    total_funding = 0
    total_trades = 0
    wins = 0
    mom_trades = 0
    mom_pnl = 0
    carry_trades = 0
    carry_pnl = 0

    monthly = defaultdict(lambda: {'pnl': 0, 'trades': 0, 'wins': 0,
                                    'entry_fees': 0, 'exit_fees': 0, 'funding': 0})

    for t in accepted:
        scale = t.get('portfolio_scale', 1.0)
        pos_usd = t.get('portfolio_position_usd', t.get('position_usd', 0))
        trade_pnl = t.get('pnl', 0) * scale
        funding = t.get('funding_cost', 0) * scale

        entry_fee = pos_usd * fee_rate
        exit_fee = pos_usd * fee_rate

        total_pnl += trade_pnl
        total_entry_fees += entry_fee
        total_exit_fees += exit_fee
        total_funding += funding
        total_trades += 1
        if trade_pnl > 0:
            wins += 1

        strategy = t.get('strategy', '')
        if strategy == 'momentum':
            mom_trades += 1
            mom_pnl += trade_pnl
        else:
            carry_trades += 1
            carry_pnl += trade_pnl

        exit_time = t.get('exit_time', '')
        if hasattr(exit_time, 'month'):
            m = exit_time.month
        else:
            continue
        mo = monthly[m]
        mo['pnl'] += trade_pnl
        mo['trades'] += 1
        mo['entry_fees'] += entry_fee
        mo['exit_fees'] += exit_fee
        mo['funding'] += funding
        if trade_pnl > 0:
            mo['wins'] += 1

    # --- Equity curve stats ---
    dates = sorted(portfolio_eq.keys())
    eq_values = [float(portfolio_eq[d]) for d in dates]
    peak_eq = eq_values[0]
    max_dd_abs = 0
    max_dd_pct = 0
    for eq in eq_values:
        if eq > peak_eq:
            peak_eq = eq
        dd = peak_eq - eq
        if dd > max_dd_abs:
            max_dd_abs = dd
            max_dd_pct = dd / peak_eq * 100

    # Peak deployed
    events = []
    for t in accepted:
        entry_time = t.get('entry_time', '')
        exit_time = t.get('exit_time', '')
        locked = t.get('portfolio_position_usd', t.get('position_usd', 0))
        if not entry_time or not exit_time or locked <= 0:
            continue
        events.append((entry_time, locked))
        events.append((exit_time, -locked))
    events.sort(key=lambda x: x[0])
    current_locked = 0
    peak_deployed = 0
    for dt, amount in events:
        current_locked += amount
        if current_locked > peak_deployed:
            peak_deployed = current_locked

    wr = wins / total_trades * 100 if total_trades > 0 else 0
    total_fees = total_entry_fees + total_exit_fees + abs(total_funding)

    print(f"\n{'='*80}")
    print(f"S58 MULTI-STRATEGY PORTFOLIO — {year} REPORT")
    print(f"{'='*80}")
    print(f"  Starting Capital:   ${capital:>12,.0f}")
    print(f"  Final Equity:       ${eq_values[-1]:>12,.0f}")
    print(f"  Net P&L:            ${eq_values[-1] - capital:>12,.0f}")
    print(f"  Gross P&L:          ${total_pnl:>12,.0f}")
    print(f"  Peak Deployed:      ${peak_deployed:>12,.0f}  "
          f"({peak_deployed/capital*100:.0f}% of starting capital)")
    print(f"  Max Drawdown:       ${max_dd_abs:>12,.0f}  ({max_dd_pct:.1f}%)")
    print(f"  Win Rate:           {wr:>11.1f}%")
    print(f"  Total Trades:       {total_trades:>12,}")
    print(f"  Skipped (no cash):  {info.get('skip_reasons', {}).get('no_cash', 0):>12,}")
    print(f"  Skipped (conc.):    {info.get('skip_reasons', {}).get('concentration', 0):>12,}")
    print(f"  Fee Rate:           {fee_rate*100:.3f}%")

    print(f"\n  Fee Breakdown:")
    print(f"    Entry Fees:       ${total_entry_fees:>12,.0f}")
    print(f"    Exit Fees:        ${total_exit_fees:>12,.0f}")
    print(f"    Funding Costs:    ${total_funding:>12,.0f}")
    print(f"    Total Fees:       ${total_fees:>12,.0f}")
    print(f"    Fees as % of P&L: {total_fees / max(abs(total_pnl), 1) * 100:.1f}%")

    print(f"\n  Strategy Breakdown:")
    print(f"    Momentum: {mom_trades:>6,} trades, PnL ${mom_pnl:>12,.0f}")
    print(f"    Carry:    {carry_trades:>6,} trades, PnL ${carry_pnl:>12,.0f}")

    # Monthly breakdown
    month_names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    print(f"\n{'─'*80}")
    print(f"  {'Month':<6} {'P&L ($)':>12} {'Entry Fee':>10} {'Exit Fee':>10} "
          f"{'Funding':>10} {'Trades':>7} {'WR':>5}")
    print(f"{'─'*80}")
    for m in sorted(monthly.keys()):
        mo = monthly[m]
        wr_m = mo['wins'] / mo['trades'] * 100 if mo['trades'] > 0 else 0
        print(f"  {month_names[m]:<6} ${mo['pnl']:>11,.0f} ${mo['entry_fees']:>9,.0f} "
              f"${mo['exit_fees']:>9,.0f} ${mo['funding']:>9,.0f} "
              f"{mo['trades']:>7,} {wr_m:>4.0f}%")
    print(f"{'─'*80}")
    print(f"  {'TOTAL':<6} ${total_pnl:>11,.0f} ${total_entry_fees:>9,.0f} "
          f"${total_exit_fees:>9,.0f} ${total_funding:>9,.0f} "
          f"{total_trades:>7,} {wr:>4.0f}%")
    print(f"{'='*80}")

    # Monthly equity progression
    monthly_eq = {}
    for d in dates:
        m = d.month if hasattr(d, 'month') else int(str(d)[5:7])
        monthly_eq[m] = float(portfolio_eq[d])

    print(f"\n  Monthly Equity Progression:")
    print(f"  {'Month':<6} {'Equity':>14} {'Gain ($)':>12}")
    print(f"  {'─'*34}")
    prev = capital
    for m in sorted(monthly_eq.keys()):
        eq = monthly_eq[m]
        print(f"  {month_names[m]:<6} ${eq:>13,.0f} ${eq - prev:>11,.0f}")
        prev = eq
    print()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, default=2025)
    parser.add_argument('--capital', type=float, default=200_000)
    args = parser.parse_args()

    results = run_filtered(capital=args.capital, year=args.year)
    if results:
        report(results, args.capital)
