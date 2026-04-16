"""
s514 Token Quality Analysis
============================
Runs s514 on all 229 tokens, extracts per-token P&L from the trade log,
and analyzes what characteristics distinguish winners from losers.

Usage:
    /workspace/venv/bin/python research/s514_token_quality_analysis.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
import numpy as np

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio

STRATEGY_ID = 's514'
CAPITAL = 100_000

# ── 1. Run full portfolio backtest ─────────────────────────────────────────

spec = StrategySpec(
    strategy_id=STRATEGY_ID,
    weight=1.0,
    max_positions=200,
    market='perp',
    strategy_type='per_token',
    max_concurrent_per_token=1,
    dd_scaling=[],
)
config = PortfolioConfig(
    capital=CAPITAL,
    exchange='binance',
    skip_walk_forward=True,
    conviction_mode='ranked',
    max_portfolio_positions=200,
    strategies=[spec],
)

tokens = discover_tokens('perp')
end_date = pd.Timestamp('2026-04-01')

print(f"Discovering tokens... {len(tokens)} found")
t0 = time.time()
signals = precompute_strategy_signals(spec, tokens, config, 12, end_date=end_date)
t1 = time.time()
print(f"Signal computation: {t1 - t0:.1f}s  |  {len(signals)} tokens with signals")

strategy_specs = {STRATEGY_ID: spec}
state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config)
t2 = time.time()
print(f"Simulation: {t2 - t1:.1f}s")

# ── 2. Extract per-token stats from closed trades ──────────────────────────

trades = state.position_manager.closed_trades
print(f"Total closed trades: {len(trades)}")

token_stats = {}
for trade in trades:
    token = trade.token
    if token not in token_stats:
        token_stats[token] = {'pnl': 0.0, 'trades': 0, 'wins': 0, 'pnl_list': [],
                              'funding': 0.0, 'hold_bars_list': []}
    token_stats[token]['pnl'] += trade.pnl
    token_stats[token]['trades'] += 1
    token_stats[token]['pnl_list'].append(trade.pnl)
    token_stats[token]['funding'] += trade.funding_cost
    token_stats[token]['hold_bars_list'].append(trade.hold_bars)
    if trade.pnl > 0:
        token_stats[token]['wins'] += 1

rows = []
for token, s in token_stats.items():
    rows.append({
        'token': token,
        'pnl': s['pnl'],
        'trades': s['trades'],
        'wins': s['wins'],
        'win_rate': s['wins'] / s['trades'] if s['trades'] > 0 else 0,
        'avg_pnl': s['pnl'] / s['trades'] if s['trades'] > 0 else 0,
        'pnl_std': np.std(s['pnl_list']) if len(s['pnl_list']) > 1 else 0,
        'funding': s['funding'],
        'avg_hold': np.mean(s['hold_bars_list']),
    })

df = pd.DataFrame(rows).sort_values('pnl', ascending=False).reset_index(drop=True)

# ── 3. Per-token P&L ranking ──────────────────────────────────────────────

print("\n" + "=" * 80)
print("  PER-TOKEN P&L RANKING (L12M, s514)")
print("=" * 80)
print(f"\n{'Token':<12} {'P&L':>10} {'Trades':>8} {'WR%':>8} {'AvgPnL':>10} {'StdPnL':>10} {'Fund':>10} {'AvgHold':>8}")
print("-" * 80)
for _, row in df.iterrows():
    marker = " ***" if row['pnl'] < -500 else ""
    print(f"{row['token']:<12} ${row['pnl']:>9,.0f} {row['trades']:>8} "
          f"{row['win_rate']*100:>7.1f}% ${row['avg_pnl']:>9,.0f} "
          f"${row['pnl_std']:>9,.0f} ${row['funding']:>9,.0f} {row['avg_hold']:>7.1f}{marker}")

# ── 4. Summary stats ──────────────────────────────────────────────────────

profitable = df[df['pnl'] > 0]
losing = df[df['pnl'] <= 0]
print(f"\nProfitable tokens: {len(profitable)} (total P&L: ${profitable['pnl'].sum():,.0f})")
print(f"Losing tokens:     {len(losing)} (total P&L: ${losing['pnl'].sum():,.0f})")
print(f"Net P&L:           ${df['pnl'].sum():,.0f}")
print(f"Total trades:      {df['trades'].sum()}")

# ── 5. Original 29 vs new tokens ──────────────────────────────────────────

original_29 = {'AAVE', 'ADA', 'APT', 'ARB', 'ATOM', 'AVAX', 'BNB', 'BTC', 'DOGE', 'DOT',
               'ETH', 'FIL', 'IMX', 'INJ', 'LINK', 'LTC', 'MATIC', 'MKR', 'NEAR', 'ONDO',
               'OP', 'SEI', 'SOL', 'SUI', 'TIA', 'TRX', 'UNI', 'WIF', 'XRP'}
df['in_original'] = df['token'].isin(original_29)

print(f"\n{'='*60}")
print("  ORIGINAL 29 vs NEW TOKENS")
print(f"{'='*60}")
orig = df[df['in_original']]
new = df[~df['in_original']]
print(f"Original 29: {len(orig)} traded, P&L ${orig['pnl'].sum():,.0f}, "
      f"avg WR {orig['win_rate'].mean()*100:.1f}%, avg P&L/token ${orig['pnl'].mean():,.0f}")
print(f"New tokens:  {len(new)} traded, P&L ${new['pnl'].sum():,.0f}, "
      f"avg WR {new['win_rate'].mean()*100:.1f}%, avg P&L/token ${new['pnl'].mean():,.0f}")

# ── 6. Load L/S characteristics per token ─────────────────────────────────

ls_df = pd.read_parquet('data/alternative/binance_metrics/all_symbols_daily_ls.parquet')

token_characteristics = {}
for symbol in ls_df['symbol'].unique():
    token = symbol.replace('USDT', '')
    sub = ls_df[ls_df['symbol'] == symbol].sort_values('date')
    if len(sub) < 60:
        continue

    div = sub['count_toptrader_ls_ratio'].values - sub['count_ls_ratio'].values

    # Rolling 30-day z-score stats
    div_series = pd.Series(div)
    div_mean = div_series.rolling(30, min_periods=20).mean()
    div_std = div_series.rolling(30, min_periods=20).std()
    div_z = ((div_series - div_mean) / div_std).dropna()

    token_characteristics[token] = {
        'data_days': len(sub),
        'div_mean': np.nanmean(div),
        'div_std': np.nanstd(div),
        'z_gt_2_5_pct': (np.abs(div_z) > 2.5).mean() * 100,
        'z_gt_2_0_pct': (np.abs(div_z) > 2.0).mean() * 100,
        'toptrader_vol': sub['count_toptrader_ls_ratio'].std(),
        'global_vol': sub['count_ls_ratio'].std(),
        'mean_oi_value': sub['sum_open_interest_value'].mean() if 'sum_open_interest_value' in sub.columns else 0,
    }

char_df = pd.DataFrame.from_dict(token_characteristics, orient='index')
char_df.index.name = 'token'
char_df = char_df.reset_index()

merged = df.merge(char_df, on='token', how='left')

# ── 7. What predicts token quality? ───────────────────────────────────────

print(f"\n{'='*60}")
print("  WHAT PREDICTS TOKEN QUALITY?")
print(f"{'='*60}")

for col in ['data_days', 'div_std', 'z_gt_2_5_pct', 'z_gt_2_0_pct',
            'toptrader_vol', 'global_vol', 'mean_oi_value']:
    if col not in merged.columns:
        continue
    valid = merged.dropna(subset=[col])
    winners = valid[valid['pnl'] > 0][col]
    losers = valid[valid['pnl'] <= 0][col]
    if len(winners) == 0 or len(losers) == 0:
        continue
    w_med = winners.median()
    l_med = losers.median()
    ratio_str = f"{w_med/l_med:.2f}x" if l_med != 0 else "inf"
    print(f"\n{col}:")
    print(f"  Winners median: {w_med:.4f}  (n={len(winners)})")
    print(f"  Losers median:  {l_med:.4f}  (n={len(losers)})")
    print(f"  Ratio: {ratio_str}")

# Correlation with P&L
print(f"\n{'='*60}")
print("  CORRELATION WITH P&L")
print(f"{'='*60}")
for col in ['data_days', 'div_std', 'z_gt_2_5_pct', 'toptrader_vol',
            'global_vol', 'mean_oi_value', 'trades']:
    if col in merged.columns:
        corr = merged[['pnl', col]].dropna().corr().iloc[0, 1]
        print(f"  {col:<20s}  r = {corr:+.3f}")

# ── 8. Filter analysis ───────────────────────────────────────────────────

print(f"\n{'='*60}")
print("  FILTER ANALYSIS: OI Thresholds")
print(f"{'='*60}")
for oi_thresh in [1e7, 5e7, 1e8, 5e8, 1e9, 2e9, 5e9]:
    filtered = merged[merged['mean_oi_value'] >= oi_thresh]
    if len(filtered) > 0:
        pnl = filtered['pnl'].sum()
        n = len(filtered)
        wr = (filtered['pnl'] > 0).mean() * 100
        avg = filtered['pnl'].mean()
        print(f"  OI >= ${oi_thresh/1e6:>6.0f}M: {n:>4} tokens, "
              f"P&L ${pnl:>10,.0f}, {wr:>5.0f}% profitable, avg ${avg:>8,.0f}/token")

print(f"\n  FILTER ANALYSIS: Data Days Thresholds")
print(f"  {'-'*55}")
for days_thresh in [100, 200, 300, 500, 750, 1000, 1500]:
    filtered = merged[merged['data_days'] >= days_thresh]
    if len(filtered) > 0:
        pnl = filtered['pnl'].sum()
        n = len(filtered)
        wr = (filtered['pnl'] > 0).mean() * 100
        avg = filtered['pnl'].mean()
        print(f"  Days >= {days_thresh:>5}: {n:>4} tokens, "
              f"P&L ${pnl:>10,.0f}, {wr:>5.0f}% profitable, avg ${avg:>8,.0f}/token")

print(f"\n  FILTER ANALYSIS: Divergence Volatility Thresholds")
print(f"  {'-'*55}")
for vol_thresh in [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5]:
    filtered = merged[merged['div_std'] >= vol_thresh]
    if len(filtered) > 0:
        pnl = filtered['pnl'].sum()
        n = len(filtered)
        wr = (filtered['pnl'] > 0).mean() * 100
        avg = filtered['pnl'].mean()
        print(f"  DivStd >= {vol_thresh:.2f}: {n:>4} tokens, "
              f"P&L ${pnl:>10,.0f}, {wr:>5.0f}% profitable, avg ${avg:>8,.0f}/token")

# ── 9. Combined filter sweep ─────────────────────────────────────────────

print(f"\n{'='*60}")
print("  COMBINED FILTER SWEEP")
print(f"{'='*60}")
best_pnl = -1e9
best_params = {}
for oi_min in [0, 5e7, 1e8, 5e8, 1e9]:
    for days_min in [0, 200, 500, 750]:
        for div_min in [0, 0.02, 0.05, 0.1]:
            m = merged.copy()
            if oi_min > 0:
                m = m[m['mean_oi_value'] >= oi_min]
            if days_min > 0:
                m = m[m['data_days'] >= days_min]
            if div_min > 0:
                m = m[m['div_std'] >= div_min]
            if len(m) < 10:
                continue
            pnl = m['pnl'].sum()
            n = len(m)
            wr = (m['pnl'] > 0).mean() * 100
            avg = m['pnl'].mean()
            if pnl > best_pnl:
                best_pnl = pnl
                best_params = {'oi_min': oi_min, 'days_min': days_min,
                               'div_min': div_min, 'n': n, 'wr': wr, 'avg': avg}

print(f"  Best combined filter:")
print(f"    OI >= ${best_params.get('oi_min', 0)/1e6:.0f}M, "
      f"Days >= {best_params.get('days_min', 0)}, "
      f"DivStd >= {best_params.get('div_min', 0):.2f}")
print(f"    {best_params.get('n', 0)} tokens, P&L ${best_pnl:,.0f}, "
      f"{best_params.get('wr', 0):.0f}% profitable, "
      f"avg ${best_params.get('avg', 0):,.0f}/token")

# Also show the top 5 combined filters
print(f"\n  Top 5 combined filters (by total P&L, min 10 tokens):")
print(f"  {'OI_min':>12} {'Days':>6} {'DivStd':>8} {'N':>5} {'P&L':>12} {'WR%':>6} {'Avg':>10}")
print(f"  {'-'*60}")
results = []
for oi_min in [0, 5e7, 1e8, 5e8, 1e9]:
    for days_min in [0, 200, 500, 750]:
        for div_min in [0, 0.02, 0.05, 0.1]:
            m = merged.copy()
            if oi_min > 0:
                m = m[m['mean_oi_value'] >= oi_min]
            if days_min > 0:
                m = m[m['data_days'] >= days_min]
            if div_min > 0:
                m = m[m['div_std'] >= div_min]
            if len(m) < 10:
                continue
            results.append({
                'oi_min': oi_min, 'days_min': days_min, 'div_min': div_min,
                'n': len(m), 'pnl': m['pnl'].sum(),
                'wr': (m['pnl'] > 0).mean() * 100,
                'avg': m['pnl'].mean(),
            })

results.sort(key=lambda x: x['pnl'], reverse=True)
for r in results[:5]:
    print(f"  ${r['oi_min']/1e6:>10.0f}M {r['days_min']:>6} {r['div_min']:>8.2f} "
          f"{r['n']:>5} ${r['pnl']:>11,.0f} {r['wr']:>5.0f}% ${r['avg']:>9,.0f}")

# ── 10. Top and bottom 30 with characteristics ────────────────────────────

print(f"\n{'='*80}")
print("  TOP 30 TOKENS")
print(f"{'='*80}")
top30 = merged.nlargest(30, 'pnl')
print(f"{'Token':<10} {'P&L':>10} {'Trades':>7} {'WR%':>6} {'Days':>6} "
      f"{'OI($M)':>10} {'DivStd':>8} {'Orig':>5}")
print("-" * 65)
for _, r in top30.iterrows():
    oi_m = r.get('mean_oi_value', 0)
    oi_m = oi_m / 1e6 if pd.notna(oi_m) else 0
    days = r.get('data_days', 0)
    days = days if pd.notna(days) else 0
    divstd = r.get('div_std', 0)
    divstd = divstd if pd.notna(divstd) else 0
    print(f"{r['token']:<10} ${r['pnl']:>9,.0f} {r['trades']:>7} "
          f"{r['win_rate']*100:>5.1f}% {days:>6.0f} "
          f"${oi_m:>9.1f} {divstd:>8.4f} "
          f"{'Y' if r['in_original'] else 'N':>5}")

print(f"\n{'='*80}")
print("  BOTTOM 30 TOKENS")
print(f"{'='*80}")
bot30 = merged.nsmallest(30, 'pnl')
print(f"{'Token':<10} {'P&L':>10} {'Trades':>7} {'WR%':>6} {'Days':>6} "
      f"{'OI($M)':>10} {'DivStd':>8} {'Orig':>5}")
print("-" * 65)
for _, r in bot30.iterrows():
    oi_m = r.get('mean_oi_value', 0)
    oi_m = oi_m / 1e6 if pd.notna(oi_m) else 0
    days = r.get('data_days', 0)
    days = days if pd.notna(days) else 0
    divstd = r.get('div_std', 0)
    divstd = divstd if pd.notna(divstd) else 0
    print(f"{r['token']:<10} ${r['pnl']:>9,.0f} {r['trades']:>7} "
          f"{r['win_rate']*100:>5.1f}% {days:>6.0f} "
          f"${oi_m:>9.1f} {divstd:>8.4f} "
          f"{'Y' if r['in_original'] else 'N':>5}")

# ── 11. Recommended filter list ──────────────────────────────────────────

# Apply best filter and list recommended tokens
best_oi = best_params.get('oi_min', 0)
best_days = best_params.get('days_min', 0)
best_div = best_params.get('div_min', 0)

recommended = merged.copy()
if best_oi > 0:
    recommended = recommended[recommended['mean_oi_value'] >= best_oi]
if best_days > 0:
    recommended = recommended[recommended['data_days'] >= best_days]
if best_div > 0:
    recommended = recommended[recommended['div_std'] >= best_div]
recommended = recommended.sort_values('pnl', ascending=False)

print(f"\n{'='*80}")
print(f"  RECOMMENDED TOKEN LIST (OI>=${best_oi/1e6:.0f}M, Days>={best_days}, DivStd>={best_div})")
print(f"  {len(recommended)} tokens, total P&L: ${recommended['pnl'].sum():,.0f}")
print(f"{'='*80}")
token_list = recommended['token'].tolist()
# Print in rows of 10
for i in range(0, len(token_list), 10):
    print("  " + ", ".join(token_list[i:i+10]))

print(f"\n  Python list:")
print(f"  TOKENS = {token_list}")

print(f"\nDone.")
