#!/usr/bin/env python3
"""S58 backtest for the last 3 months."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
import numpy as np
import pandas as pd
from collections import defaultdict

from strategies.s58_multi_strategy_portfolio import (
    _get_momentum_trades, _get_carry_trades, _load_v3
)
from v3.universe import get_fee_rate, get_all_tradeable

capital = 200_000
exchange = 'binance'
cutoff_start = pd.Timestamp('2025-12-01')
cutoff_end = pd.Timestamp('2026-03-09')

portfolio_mod = _load_v3('portfolio')
universe_mod = _load_v3('universe')
simulate_portfolio = portfolio_mod.simulate_portfolio
perp_tokens = universe_mod.get_all_tradeable('perp')

t0 = time.time()
print('Extracting momentum trades...', end='', flush=True)
mom_trades = _get_momentum_trades(perp_tokens, 'data', capital, exchange)
print(f' {sum(len(t) for t in mom_trades.values())} ({time.time()-t0:.0f}s)')

t1 = time.time()
print('Extracting carry trades...', end='', flush=True)
carry_trades = _get_carry_trades(perp_tokens, 'data', capital, exchange)
print(f' {sum(len(t) for t in carry_trades.values())} ({time.time()-t1:.0f}s)')

all_token_trades = {}
for key, trades in mom_trades.items():
    token = key.replace('_mom', '')
    filtered = [t for t in trades if cutoff_start <= t['entry_time'] < cutoff_end]
    if filtered:
        all_token_trades.setdefault(token, []).extend(filtered)
for key, trades in carry_trades.items():
    token = key.replace('_carry', '')
    filtered = [t for t in trades if cutoff_start <= t['entry_time'] < cutoff_end]
    if filtered:
        all_token_trades.setdefault(token, []).extend(filtered)

total = sum(len(t) for t in all_token_trades.values())
print(f'Filtered: {total} trades (Dec 2025 - Mar 2026), {len(all_token_trades)} tokens')

fee_rate = get_fee_rate(exchange, 'perp', 'taker')
portfolio_eq, accepted, skipped, sim_info = simulate_portfolio(
    all_token_trades, capital, max_token_pct=0.15,
    min_position_usd=1000, fee_rate=fee_rate)
print(f'Accepted: {len(accepted)}, Skipped: {len(skipped)}')

dates = sorted(portfolio_eq.keys())
eq_values = [float(portfolio_eq[d]) for d in dates]

# Max drawdown
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

# Monthly breakdown
monthly = defaultdict(lambda: {'pnl': 0, 'trades': 0, 'wins': 0,
                                'entry_fees': 0, 'exit_fees': 0, 'funding': 0})
for t in accepted:
    exit_time = t.get('exit_time')
    if not exit_time or not hasattr(exit_time, 'month'):
        continue
    key = f'{exit_time.year}-{exit_time.month:02d}'
    scale = t.get('portfolio_scale', 1.0)
    pos_usd = t.get('portfolio_position_usd', t.get('position_usd', 0))
    trade_pnl = t.get('pnl', 0) * scale
    mo = monthly[key]
    mo['pnl'] += trade_pnl
    mo['trades'] += 1
    if trade_pnl > 0:
        mo['wins'] += 1
    mo['entry_fees'] += pos_usd * fee_rate
    mo['exit_fees'] += pos_usd * fee_rate
    mo['funding'] += t.get('funding_cost', 0) * scale

mom_pnl = sum(t['pnl'] * t.get('portfolio_scale', 1.0)
              for t in accepted if t.get('strategy') == 'momentum')
carry_pnl = sum(t['pnl'] * t.get('portfolio_scale', 1.0)
                for t in accepted if t.get('strategy') == 'carry')
mom_n = sum(1 for t in accepted if t.get('strategy') == 'momentum')
carry_n = sum(1 for t in accepted if t.get('strategy') == 'carry')

# Peak deployed
events = []
for t in accepted:
    entry = t.get('entry_time')
    exit_t = t.get('exit_time')
    locked = t.get('portfolio_position_usd', t.get('position_usd', 0))
    if entry and exit_t and locked > 0:
        events.append((entry, locked))
        events.append((exit_t, -locked))
events.sort(key=lambda x: x[0])
current_locked = 0
peak_deployed = 0
for dt, amount in events:
    current_locked += amount
    if current_locked > peak_deployed:
        peak_deployed = current_locked

total_pnl = sum(t['pnl'] * t.get('portfolio_scale', 1.0) for t in accepted)
total_entry = sum(t.get('portfolio_position_usd', t.get('position_usd', 0)) * fee_rate
                  for t in accepted)
total_exit = total_entry
total_funding = sum(t.get('funding_cost', 0) * t.get('portfolio_scale', 1.0)
                    for t in accepted)

print(f'\n{"="*80}')
print(f'S58 LAST 3 MONTHS (Dec 2025 - Mar 2026)')
print(f'{"="*80}')
print(f'  Starting Capital:   ${capital:>12,.0f}')
print(f'  Final Equity:       ${eq_values[-1]:>12,.0f}')
print(f'  Net P&L:            ${eq_values[-1] - capital:>12,.0f}')
print(f'  Peak Deployed:      ${peak_deployed:>12,.0f}  ({peak_deployed/capital*100:.0f}% of capital)')
print(f'  Max Drawdown:       ${max_dd_abs:>12,.0f}  ({max_dd_pct:.1f}% of peak equity)')
print(f'  Max DD vs Capital:  {max_dd_abs/capital*100:>11.1f}%  (of starting ${capital:,.0f})')
print(f'  Total Trades:       {len(accepted):>12,}')
print(f'  Skipped (no cash):  {sim_info.get("skip_reasons", {}).get("no_cash", 0):>12,}')
print(f'  Skipped (conc.):    {sim_info.get("skip_reasons", {}).get("concentration", 0):>12,}')
print()
print(f'  Fee Breakdown:')
print(f'    Entry Fees:       ${total_entry:>12,.0f}')
print(f'    Exit Fees:        ${total_exit:>12,.0f}')
print(f'    Funding:          ${total_funding:>12,.0f}')
print(f'    Total Fees:       ${total_entry + total_exit + abs(total_funding):>12,.0f}')
print()
print(f'  Strategy Split:')
print(f'    Momentum: {mom_n:>6,} trades, PnL ${mom_pnl:>10,.0f}')
print(f'    Carry:    {carry_n:>6,} trades, PnL ${carry_pnl:>10,.0f}')

print(f'\n{"─"*80}')
print(f'  {"Month":<10} {"P&L":>12} {"Entry Fee":>10} {"Exit Fee":>10} '
      f'{"Funding":>10} {"Trades":>7} {"WR":>5}')
print(f'{"─"*80}')
for key in sorted(monthly.keys()):
    mo = monthly[key]
    wr = mo['wins'] / mo['trades'] * 100 if mo['trades'] > 0 else 0
    print(f'  {key:<10} ${mo["pnl"]:>11,.0f} ${mo["entry_fees"]:>9,.0f} '
          f'${mo["exit_fees"]:>9,.0f} ${mo["funding"]:>9,.0f} '
          f'{mo["trades"]:>7,} {wr:>4.0f}%')

print(f'\n  Monthly Equity:')
monthly_eq = {}
for d in dates:
    key = f'{d.year}-{d.month:02d}'
    monthly_eq[key] = float(portfolio_eq[d])
prev = capital
for key in sorted(monthly_eq.keys()):
    eq = monthly_eq[key]
    sign = '+' if eq - prev >= 0 else ''
    print(f'    {key}: ${eq:>12,.0f}  ({sign}${eq-prev:>10,.0f})')
    prev = eq

# Weekly detail
print(f'\n  Weekly Detail:')
print(f'  {"Week":<14} {"Equity":>12} {"P&L":>10}')
print(f'  {"─"*38}')
from datetime import timedelta
week_start = dates[0]
while week_start <= dates[-1]:
    week_end = week_start + timedelta(days=7)
    week_eq = [float(portfolio_eq[d]) for d in dates if week_start <= d < week_end]
    if week_eq:
        label = f'{week_start.strftime("%b %d")}'
        # Find start eq (previous week end or capital)
        prev_days = [d for d in dates if d < week_start]
        start_eq = float(portfolio_eq[prev_days[-1]]) if prev_days else capital
        print(f'  {label:<14} ${week_eq[-1]:>11,.0f} ${week_eq[-1]-start_eq:>9,.0f}')
    week_start = week_end

print(f'\n{"="*80}')
