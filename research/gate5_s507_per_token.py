"""
Gate 5 — s507 Per-Token P&L Breakdown (Concentration Risk Check)
================================================================

Checks whether s507's P&L is concentrated in a few tokens (like the
PIPPIN issue that plagued s501). Uses trade log from v4 engine.

Steps:
1. Run s507 via v4 engine to get trades
2. Group trades by token
3. Report per-token P&L, trade count, win rate
4. Flag concentration risk (>50% P&L from one token)
"""

import sys
import os
sys.path.insert(0, '/workspace/crypto_backtest')
os.chdir('/workspace/crypto_backtest')

import json
import numpy as np
import pandas as pd
from pathlib import Path

# ── Step 1: Run s507 via v4 engine ────────────────────────────────────

print("="*70)
print("  GATE 5 — s507 Per-Token Concentration Risk Analysis")
print("="*70)

# Run the backtest to ensure fresh trades
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

CAPITAL = 100_000
MONTHS = 12

config = PortfolioConfig(
    capital=CAPITAL,
    exchange='binance',
    skip_walk_forward=False,
    raw_mode=True,
)

spec = StrategySpec(
    strategy_id='s507',
    weight=1.0,
    max_positions=50,
    market='perp',
    strategy_type='per_token',
)
config.strategies = [spec]
strategy_specs = {'s507': spec}

tokens = discover_tokens('perp')
print(f"\n  Tokens in universe: {len(tokens)}")

print(f"  Precomputing signals for s507...")
signals = precompute_strategy_signals(spec, tokens, config, MONTHS)
precomputed = {'s507': signals}
print(f"  Tokens with signals: {len(signals)}")

print(f"  Running simulation...")
state = simulate_portfolio(precomputed, strategy_specs, config)
trades = state.position_manager.closed_trades
print(f"  Total trades: {len(trades)}")

metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)

# ── Step 2: Extract trade data ────────────────────────────────────────

records = []
for t in trades:
    side = 'long' if t.direction == 1 else 'short'
    total_fees = t.entry_fee + t.exit_fee
    records.append({
        'token': t.token,
        'side': side,
        'net_pnl': t.pnl,
        'fees': total_fees,
        'funding': t.funding_cost,
        'entry_bar': t.entry_bar,
        'exit_bar': t.exit_bar,
        'margin_usd': t.margin_usd,
        'bars_held': t.hold_bars,
        'exit_reason': t.exit_reason,
    })

df = pd.DataFrame(records)
if df.empty:
    print("\n  NO TRADES — cannot analyze concentration risk.")
    sys.exit(1)

# ── Step 3: Per-token analysis ────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  PER-TOKEN P&L BREAKDOWN")
print(f"{'='*70}")

token_stats = df.groupby('token').agg(
    total_pnl=('net_pnl', 'sum'),
    trade_count=('net_pnl', 'count'),
    wins=('net_pnl', lambda x: (x > 0).sum()),
    avg_pnl=('net_pnl', 'mean'),
    max_win=('net_pnl', 'max'),
    max_loss=('net_pnl', 'min'),
    avg_size=('margin_usd', 'mean'),
    total_fees=('fees', 'sum'),
    total_funding=('funding', 'sum'),
).sort_values('total_pnl', ascending=False)

token_stats['win_rate'] = token_stats['wins'] / token_stats['trade_count']

total_net_pnl = df['net_pnl'].sum()
token_stats['pnl_pct'] = token_stats['total_pnl'] / abs(total_net_pnl) * 100

print(f"\n  Total Net P&L: ${total_net_pnl:+,.0f}")
print(f"  Unique tokens traded: {len(token_stats)}")
print(f"  Total trades: {len(df)}")

print(f"\n  {'Token':>10s}  {'Net P&L':>10s}  {'% of Total':>10s}  {'Trades':>7s}  "
      f"{'Win%':>6s}  {'Avg P&L':>9s}  {'Max Win':>9s}  {'Max Loss':>9s}  {'Avg Size':>9s}")
print(f"  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*7}  {'─'*6}  {'─'*9}  {'─'*9}  {'─'*9}  {'─'*9}")

for token, row in token_stats.iterrows():
    print(f"  {token:>10s}  ${row['total_pnl']:>+9,.0f}  {row['pnl_pct']:>+9.1f}%  "
          f"{row['trade_count']:>7.0f}  {row['win_rate']:>5.0%}  "
          f"${row['avg_pnl']:>+8,.0f}  ${row['max_win']:>+8,.0f}  "
          f"${row['max_loss']:>+8,.0f}  ${row['avg_size']:>8,.0f}")

# ── Step 4: Concentration risk assessment ─────────────────────────────

print(f"\n{'='*70}")
print(f"  CONCENTRATION RISK ASSESSMENT")
print(f"{'='*70}")

# Check top-1 token concentration
top1 = token_stats.iloc[0]
top1_pct = top1['pnl_pct']
print(f"\n  Top token: {token_stats.index[0]} contributes {top1_pct:+.1f}% of total P&L")

# Check top-3 concentration
top3_pnl = token_stats.head(3)['total_pnl'].sum()
top3_pct = top3_pnl / abs(total_net_pnl) * 100
print(f"  Top 3 tokens contribute {top3_pct:+.1f}% of total P&L")

# Check top-5 concentration
top5_pnl = token_stats.head(5)['total_pnl'].sum()
top5_pct = top5_pnl / abs(total_net_pnl) * 100
print(f"  Top 5 tokens contribute {top5_pct:+.1f}% of total P&L")

# HHI (Herfindahl-Hirschman Index) on P&L shares
pnl_shares = (token_stats['total_pnl'] / abs(total_net_pnl)).values
hhi = np.sum(pnl_shares ** 2) * 10000  # Scale to 0-10000
print(f"\n  HHI (P&L concentration): {hhi:.0f}")
print(f"    <1000 = diversified, 1000-2500 = moderate, >2500 = concentrated")

# Winners vs losers at token level
winners = token_stats[token_stats['total_pnl'] > 0]
losers = token_stats[token_stats['total_pnl'] <= 0]
print(f"\n  Tokens with positive P&L: {len(winners)}")
print(f"  Tokens with negative P&L: {len(losers)}")
if len(winners) > 0:
    print(f"  Total winner P&L: ${winners['total_pnl'].sum():+,.0f}")
if len(losers) > 0:
    print(f"  Total loser P&L:  ${losers['total_pnl'].sum():+,.0f}")

# ── Step 5: Side analysis ────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  LONG vs SHORT BREAKDOWN")
print(f"{'='*70}")

for side in ['long', 'short']:
    side_df = df[df['side'] == side]
    if side_df.empty:
        print(f"\n  {side.upper()}: no trades")
        continue
    print(f"\n  {side.upper()}:")
    print(f"    Trades: {len(side_df)}")
    print(f"    Net P&L: ${side_df['net_pnl'].sum():+,.0f}")
    print(f"    Win rate: {(side_df['net_pnl'] > 0).mean():.0%}")
    print(f"    Avg P&L: ${side_df['net_pnl'].mean():+,.0f}")

# ── Step 6: Temporal analysis ────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  MONTHLY TOKEN DISTRIBUTION")
print(f"{'='*70}")

# entry_bar is an integer index; use exit_bar as proxy for time ordering
# Group by entry_bar ranges (approximate monthly: ~720 bars = 30 days of 1h)
df['month_bin'] = df['entry_bar'] // 720
monthly = df.groupby('month_bin').agg(
    trades=('net_pnl', 'count'),
    net_pnl=('net_pnl', 'sum'),
    unique_tokens=('token', 'nunique'),
).sort_index()

print(f"\n  {'Period':>10s}  {'Trades':>7s}  {'Net P&L':>10s}  {'Tokens':>7s}")
print(f"  {'─'*10}  {'─'*7}  {'─'*10}  {'─'*7}")
for month_bin, row in monthly.iterrows():
    label = f"~M{int(month_bin)+1}"
    print(f"  {label:>10s}  {row['trades']:>7.0f}  ${row['net_pnl']:>+9,.0f}  {row['unique_tokens']:>7.0f}")

# ── VERDICT ───────────────────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  VERDICT")
print(f"{'='*70}")

concentration_flags = []
if abs(top1_pct) > 50:
    concentration_flags.append(
        f"CRITICAL: Top token ({token_stats.index[0]}) = {top1_pct:+.1f}% of P&L (>50%)")
if abs(top1_pct) > 30:
    concentration_flags.append(
        f"WARNING: Top token ({token_stats.index[0]}) = {top1_pct:+.1f}% of P&L (>30%)")
if abs(top3_pct) > 80:
    concentration_flags.append(
        f"WARNING: Top 3 tokens = {top3_pct:+.1f}% of P&L (>80%)")
if hhi > 2500:
    concentration_flags.append(f"WARNING: HHI={hhi:.0f} indicates high concentration")
if len(token_stats) < 5:
    concentration_flags.append(f"WARNING: Only {len(token_stats)} unique tokens traded")

if concentration_flags:
    print(f"\n  CONCENTRATION RISK DETECTED:")
    for flag in concentration_flags:
        print(f"    - {flag}")
else:
    print(f"\n  PASS: No concentration risk detected")
    print(f"    - Top token: {top1_pct:+.1f}% (threshold: 50%)")
    print(f"    - Top 3: {top3_pct:+.1f}% (threshold: 80%)")
    print(f"    - HHI: {hhi:.0f} (threshold: 2500)")
    print(f"    - Tokens traded: {len(token_stats)}")

print(f"\n{'='*70}")
