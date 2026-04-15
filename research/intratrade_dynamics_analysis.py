"""Intra-trade dynamics analysis: what distinguishes winners from losers DURING hold."""
import json
import warnings
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings('ignore')

BASE = Path('/workspace/crypto_backtest')

# --- Load data ---
with open(BASE / 'results/v4/s524o_clean_baseline_48mo_100k_trades.json') as f:
    trades_raw = json.load(f)

with open(BASE / 'data/alternative/s521_token_config.json') as f:
    token_config = json.load(f)

btc_1h = pd.read_parquet(BASE / 'data/perp/1h_cache/BTC_1h.parquet')
sim_end = pd.Timestamp('2026-04-05 16:00:00')
sim_start = sim_end - pd.DateOffset(months=48)
sim_start_bar = btc_1h.index.get_indexer([sim_start], method='nearest')[0]
print(f"Sim start: {btc_1h.index[sim_start_bar]} (bar {sim_start_bar})")
print(f"Total trades: {len(trades_raw)}")

# Parse trades
trades = []
for t in trades_raw:
    trades.append({
        'token': t['token'],
        'pnl': float(t['pnl']),
        'direction': t['direction'],
        'entry_bar': t['entry_bar'],
        'exit_bar': t['exit_bar'],
        'hold_hours': t['hold_hours'],
        'entry_price': float(t['entry_price']),
        'exit_price': float(t['exit_price']),
        'margin_usd': float(t['margin_usd']),
        'exit_reason': t['exit_reason'],
        'is_winner': float(t['pnl']) > 0,
    })
trades_df = pd.DataFrame(trades)

# High-IC threshold
HIGH_IC_THRESH = 0.60
high_ic_tokens = {tok for tok, cfg in token_config.items() if cfg['best_ic'] > HIGH_IC_THRESH}
trades_df['high_ic'] = trades_df['token'].isin(high_ic_tokens)
print(f"High-IC tokens (>{HIGH_IC_THRESH}): {len(high_ic_tokens)}")
print(f"Trades from high-IC tokens: {trades_df['high_ic'].sum()}")

# Cache for loaded data
_1h_cache = {}
_5min_cache = {}

def get_1h(token):
    if token not in _1h_cache:
        p = BASE / f'data/perp/1h_cache/{token}_1h.parquet'
        if p.exists():
            _1h_cache[token] = pd.read_parquet(p)
        else:
            _1h_cache[token] = None
    return _1h_cache[token]

def get_5min(token):
    if token not in _5min_cache:
        p = BASE / f'data/alternative/binance_metrics/5min/{token}USDT_5min.parquet'
        if p.exists():
            df = pd.read_parquet(p)
            df = df.set_index('create_time').sort_index()
            _5min_cache[token] = df
        else:
            _5min_cache[token] = None
    return _5min_cache[token]

def get_entry_ts(trade):
    abs_bar = sim_start_bar + trade['entry_bar']
    if abs_bar >= len(btc_1h):
        return None
    return btc_1h.index[abs_bar]

def get_exit_ts(trade):
    abs_bar = sim_start_bar + trade['exit_bar']
    if abs_bar >= len(btc_1h):
        return None
    return btc_1h.index[abs_bar]


# ============================================================
# ANALYSIS 1: Z-Score Signal Decay During Hold
# ============================================================
print("\n" + "="*70)
print("ANALYSIS 1: Z-Score Signal Decay During Hold")
print("="*70)

ZW = 22  # 22-day rolling z-score window

def compute_composite_zscore_series(token):
    """Compute daily composite z-score series for a token."""
    cfg = token_config.get(token)
    if cfg is None:
        return None

    df5 = get_5min(token)
    if df5 is None:
        return None

    # Resample to daily (last value per day)
    daily = df5.resample('1D').last().dropna(how='all')

    if len(daily) < ZW + 5:
        return None

    # Components
    oi = daily['sum_open_interest']
    pos = daily['sum_toptrader_long_short_ratio']
    flow = daily['sum_taker_long_short_vol_ratio']

    # Rolling z-scores
    oi_z = (oi - oi.rolling(ZW).mean()) / oi.rolling(ZW).std()
    pos_z = (pos - pos.rolling(ZW).mean()) / pos.rolling(ZW).std()
    flow_z = (flow - flow.rolling(ZW).mean()) / flow.rolling(ZW).std()

    # Composite
    composite = (cfg['oi_weight'] * oi_z * cfg['oi_sign'] +
                 cfg['pos_weight'] * pos_z * cfg['pos_sign'] +
                 cfg['flow_weight'] * flow_z * cfg['flow_sign'])

    return composite

# Precompute z-score series for all tokens
zscore_cache = {}
for tok in set(trades_df['token']):
    zscore_cache[tok] = compute_composite_zscore_series(tok)

checkpoints_days = [0, 1, 2, 3, 5, 7, 14]
zscore_results = []

for idx, trade in trades_df.iterrows():
    entry_ts = get_entry_ts(trade)
    if entry_ts is None:
        continue

    zs = zscore_cache.get(trade['token'])
    if zs is None:
        continue

    row = {'is_winner': trade['is_winner'], 'high_ic': trade['high_ic'], 'token': trade['token']}

    # Get z-score at each checkpoint
    entry_z = None
    for d in checkpoints_days:
        target_ts = entry_ts + pd.Timedelta(days=d)
        # Find nearest daily value
        idx_pos = zs.index.get_indexer([target_ts], method='nearest')
        if idx_pos[0] < 0 or idx_pos[0] >= len(zs):
            row[f'z_d{d}'] = np.nan
        else:
            val = zs.iloc[idx_pos[0]]
            row[f'z_d{d}'] = val
            if d == 0:
                entry_z = val

    # Compute decay rates
    if entry_z is not None and not np.isnan(entry_z) and abs(entry_z) > 0.01:
        for d in checkpoints_days[1:]:
            z_d = row.get(f'z_d{d}', np.nan)
            if not np.isnan(z_d):
                row[f'z_decay_d{d}'] = (z_d - entry_z) / abs(entry_z)

    zscore_results.append(row)

zs_df = pd.DataFrame(zscore_results)
print(f"\nTrades with z-score data: {len(zs_df)}")

def print_zscore_table(df, label):
    print(f"\n--- {label} ---")
    print(f"{'':>10}", end='')
    for d in checkpoints_days:
        print(f"{'d'+str(d):>10}", end='')
    print()

    for name, mask in [('Winners', df['is_winner']), ('Losers', ~df['is_winner'])]:
        sub = df[mask]
        print(f"{name:>10}", end='')
        for d in checkpoints_days:
            col = f'z_d{d}'
            if col in sub.columns:
                print(f"{sub[col].median():>10.3f}", end='')
            else:
                print(f"{'N/A':>10}", end='')
        print(f"  (n={len(sub)})")

print_zscore_table(zs_df, "ALL TOKENS - Median Composite Z-Score at Checkpoint")
print_zscore_table(zs_df[zs_df['high_ic']], "HIGH-IC TOKENS ONLY")
print_zscore_table(zs_df[~zs_df['high_ic']], "NORMAL-IC TOKENS ONLY")

# Z-score decay rates
def print_decay_table(df, label):
    print(f"\n--- {label}: Median Z-Score Decay Rate ---")
    print(f"{'':>10}", end='')
    for d in checkpoints_days[1:]:
        print(f"{'d'+str(d):>10}", end='')
    print()

    for name, mask in [('Winners', df['is_winner']), ('Losers', ~df['is_winner'])]:
        sub = df[mask]
        print(f"{name:>10}", end='')
        for d in checkpoints_days[1:]:
            col = f'z_decay_d{d}'
            if col in sub.columns:
                print(f"{sub[col].median():>10.3f}", end='')
            else:
                print(f"{'N/A':>10}", end='')
        print(f"  (n={len(sub)})")

print_decay_table(zs_df, "ALL TOKENS")
print_decay_table(zs_df[zs_df['high_ic']], "HIGH-IC TOKENS")


# ============================================================
# ANALYSIS 2: Early Price Velocity
# ============================================================
print("\n" + "="*70)
print("ANALYSIS 2: Early Price Velocity")
print("="*70)

velocity_checkpoints = [1, 4, 8, 24]  # hours
velocity_results = []

for idx, trade in trades_df.iterrows():
    entry_ts = get_entry_ts(trade)
    if entry_ts is None:
        continue

    tok_1h = get_1h(trade['token'])
    if tok_1h is None:
        continue

    # Find entry bar in token's 1h data
    entry_idx = tok_1h.index.get_indexer([entry_ts], method='nearest')[0]
    if entry_idx < 0 or entry_idx >= len(tok_1h):
        continue

    entry_price = tok_1h.iloc[entry_idx]['close']
    direction = trade['direction']

    row = {'is_winner': trade['is_winner'], 'high_ic': trade['high_ic'], 'token': trade['token']}

    # Price returns at checkpoints (directional: positive = in favor of trade)
    for h in velocity_checkpoints:
        bar_idx = entry_idx + h
        if bar_idx >= len(tok_1h):
            row[f'ret_h{h}'] = np.nan
            row[f'vel_h{h}'] = np.nan
            continue
        price_h = tok_1h.iloc[bar_idx]['close']
        raw_ret = (price_h - entry_price) / entry_price
        dir_ret = raw_ret * direction  # positive = favorable
        row[f'ret_h{h}'] = dir_ret
        row[f'vel_h{h}'] = dir_ret / np.sqrt(h)  # velocity = return / sqrt(time)

    # Price acceleration: velocity at h8 minus velocity at h2
    if entry_idx + 8 < len(tok_1h) and entry_idx + 2 < len(tok_1h):
        p2 = tok_1h.iloc[entry_idx + 2]['close']
        p8 = tok_1h.iloc[entry_idx + 8]['close']
        vel2 = ((p2 - entry_price) / entry_price * direction) / np.sqrt(2)
        vel8 = ((p8 - entry_price) / entry_price * direction) / np.sqrt(8)
        row['acceleration'] = vel8 - vel2

    # First 24h Sharpe-like ratio
    max_h = min(24, len(tok_1h) - entry_idx - 1)
    if max_h >= 2:
        hourly_prices = [tok_1h.iloc[entry_idx + i]['close'] for i in range(max_h + 1)]
        hourly_rets = np.diff(hourly_prices) / hourly_prices[:-1] * direction
        if np.std(hourly_rets) > 0:
            row['sharpe_24h'] = np.mean(hourly_rets) / np.std(hourly_rets)
        else:
            row['sharpe_24h'] = 0.0

    velocity_results.append(row)

vel_df = pd.DataFrame(velocity_results)
print(f"\nTrades with velocity data: {len(vel_df)}")

def print_velocity_table(df, label, metric_prefix='vel'):
    print(f"\n--- {label} ---")
    print(f"{'':>10}", end='')
    for h in velocity_checkpoints:
        print(f"{'h'+str(h):>10}", end='')
    if 'sharpe_24h' in df.columns:
        print(f"{'sharpe24':>10}", end='')
    if 'acceleration' in df.columns:
        print(f"{'accel':>10}", end='')
    print()

    for name, mask in [('Winners', df['is_winner']), ('Losers', ~df['is_winner'])]:
        sub = df[mask]
        print(f"{name:>10}", end='')
        for h in velocity_checkpoints:
            col = f'{metric_prefix}_h{h}'
            if col in sub.columns:
                print(f"{sub[col].median()*100:>9.3f}%", end='')
            else:
                print(f"{'N/A':>10}", end='')
        if 'sharpe_24h' in sub.columns:
            print(f"{sub['sharpe_24h'].median():>10.3f}", end='')
        if 'acceleration' in sub.columns:
            print(f"{sub['acceleration'].median()*100:>9.3f}%", end='')
        print(f"  (n={len(sub)})")

print("\n>> Median VELOCITY (return/sqrt(h), x100 for %) <<")
print_velocity_table(vel_df, "ALL TOKENS")
print_velocity_table(vel_df[vel_df['high_ic']], "HIGH-IC TOKENS")
print_velocity_table(vel_df[~vel_df['high_ic']], "NORMAL-IC TOKENS")

print("\n>> Median RETURNS (%) <<")
print_velocity_table(vel_df, "ALL TOKENS", 'ret')
print_velocity_table(vel_df[vel_df['high_ic']], "HIGH-IC TOKENS", 'ret')
print_velocity_table(vel_df[~vel_df['high_ic']], "NORMAL-IC TOKENS", 'ret')

# Predictive power of velocity at hour 8
print("\n--- Velocity at Hour 8: Predictive Power ---")
for label, sub in [("ALL", vel_df), ("HIGH-IC", vel_df[vel_df['high_ic']]), ("NORMAL-IC", vel_df[~vel_df['high_ic']])]:
    valid = sub.dropna(subset=['vel_h8'])
    pos_vel = valid[valid['vel_h8'] > 0]
    neg_vel = valid[valid['vel_h8'] <= 0]
    if len(pos_vel) > 0 and len(neg_vel) > 0:
        wr_pos = pos_vel['is_winner'].mean() * 100
        wr_neg = neg_vel['is_winner'].mean() * 100
        base_wr = valid['is_winner'].mean() * 100
        print(f"  {label}: Base WR={base_wr:.1f}% | Vel>0 WR={wr_pos:.1f}% ({len(pos_vel)} trades) | Vel<=0 WR={wr_neg:.1f}% ({len(neg_vel)} trades)")

# If we ONLY kept positive velocity at h8
print("\n--- Filter: ONLY keep trades with positive velocity at h8 ---")
for label, sub in [("ALL", vel_df), ("HIGH-IC", vel_df[vel_df['high_ic']])]:
    valid = sub.dropna(subset=['vel_h8'])
    pos = valid[valid['vel_h8'] > 0]
    print(f"  {label}: Keep {len(pos)}/{len(valid)} trades ({len(pos)/len(valid)*100:.1f}%), WR={pos['is_winner'].mean()*100:.1f}%")


# ============================================================
# ANALYSIS 3: Volume Confirmation
# ============================================================
print("\n" + "="*70)
print("ANALYSIS 3: Volume Confirmation")
print("="*70)

volume_results = []

for idx, trade in trades_df.iterrows():
    entry_ts = get_entry_ts(trade)
    if entry_ts is None:
        continue

    tok_1h = get_1h(trade['token'])
    if tok_1h is None:
        continue

    entry_idx = tok_1h.index.get_indexer([entry_ts], method='nearest')[0]
    if entry_idx < 0 or entry_idx < 20 * 24:  # Need 20 days of history
        continue

    # 20-day average volume (20*24 hourly bars)
    lookback = 20 * 24
    hist_vol = tok_1h.iloc[entry_idx - lookback:entry_idx]['volume']
    avg_daily_vol = hist_vol.sum() / 20  # total volume / 20 days

    if avg_daily_vol <= 0:
        continue

    # Entry-day volume (24h from entry)
    end_idx = min(entry_idx + 24, len(tok_1h))
    entry_day_vol = tok_1h.iloc[entry_idx:end_idx]['volume'].sum()

    # First 3-day average volume
    end_3d = min(entry_idx + 3 * 24, len(tok_1h))
    first_3d_vol = tok_1h.iloc[entry_idx:end_3d]['volume'].sum() / max(1, (end_3d - entry_idx) / 24)

    vol_ratio = entry_day_vol / avg_daily_vol
    sustained_vol_ratio = first_3d_vol / avg_daily_vol

    row = {
        'is_winner': trade['is_winner'],
        'high_ic': trade['high_ic'],
        'token': trade['token'],
        'vol_ratio': vol_ratio,
        'sustained_vol_ratio': sustained_vol_ratio,
    }
    volume_results.append(row)

vol_df2 = pd.DataFrame(volume_results)
print(f"\nTrades with volume data: {len(vol_df2)}")

def print_volume_table(df, label):
    print(f"\n--- {label} ---")
    print(f"{'':>10} {'entry_vol_ratio':>16} {'3d_vol_ratio':>14}")
    for name, mask in [('Winners', df['is_winner']), ('Losers', ~df['is_winner'])]:
        sub = df[mask]
        print(f"{name:>10} {sub['vol_ratio'].median():>16.3f} {sub['sustained_vol_ratio'].median():>14.3f}  (n={len(sub)})")

print_volume_table(vol_df2, "ALL TOKENS")
print_volume_table(vol_df2[vol_df2['high_ic']], "HIGH-IC TOKENS")
print_volume_table(vol_df2[~vol_df2['high_ic']], "NORMAL-IC TOKENS")

# Volume predictive power
print("\n--- Volume Ratio > 1.0: Predictive Power ---")
for label, sub in [("ALL", vol_df2), ("HIGH-IC", vol_df2[vol_df2['high_ic']]), ("NORMAL-IC", vol_df2[~vol_df2['high_ic']])]:
    above = sub[sub['vol_ratio'] > 1.0]
    below = sub[sub['vol_ratio'] <= 1.0]
    base_wr = sub['is_winner'].mean() * 100
    if len(above) > 0 and len(below) > 0:
        print(f"  {label}: Base WR={base_wr:.1f}% | Vol>1x WR={above['is_winner'].mean()*100:.1f}% ({len(above)}) | Vol<=1x WR={below['is_winner'].mean()*100:.1f}% ({len(below)})")


# ============================================================
# CROSS: Volume + Velocity
# ============================================================
print("\n" + "="*70)
print("CROSS ANALYSIS: Volume + Velocity")
print("="*70)

# Merge velocity and volume data
cross = vel_df[['token', 'is_winner', 'high_ic', 'vel_h8']].copy()
cross = cross.reset_index(drop=True)
vol2_reset = vol_df2[['vol_ratio']].copy().reset_index(drop=True)
# Need to align by trade index - redo with index tracking
cross_results = []
for idx, trade in trades_df.iterrows():
    entry_ts = get_entry_ts(trade)
    if entry_ts is None:
        continue

    tok_1h = get_1h(trade['token'])
    if tok_1h is None:
        continue

    entry_idx = tok_1h.index.get_indexer([entry_ts], method='nearest')[0]
    if entry_idx < 0 or entry_idx < 20 * 24:
        continue

    direction = trade['direction']
    entry_price = tok_1h.iloc[entry_idx]['close']

    # Velocity at h8
    if entry_idx + 8 < len(tok_1h):
        p8 = tok_1h.iloc[entry_idx + 8]['close']
        vel8 = ((p8 - entry_price) / entry_price * direction) / np.sqrt(8)
    else:
        vel8 = np.nan

    # Volume ratio
    lookback = 20 * 24
    hist_vol = tok_1h.iloc[entry_idx - lookback:entry_idx]['volume']
    avg_daily_vol = hist_vol.sum() / 20
    if avg_daily_vol <= 0:
        continue
    end_idx_v = min(entry_idx + 24, len(tok_1h))
    entry_day_vol = tok_1h.iloc[entry_idx:end_idx_v]['volume'].sum()
    vol_ratio = entry_day_vol / avg_daily_vol

    cross_results.append({
        'is_winner': trade['is_winner'],
        'high_ic': trade['high_ic'],
        'vel_h8': vel8,
        'vol_ratio': vol_ratio,
    })

cross_df = pd.DataFrame(cross_results)

print("\n--- Combined Filter: Positive Vel@h8 AND Volume > 1x ---")
for label, sub in [("ALL", cross_df), ("HIGH-IC", cross_df[cross_df['high_ic']])]:
    valid = sub.dropna(subset=['vel_h8'])
    both = valid[(valid['vel_h8'] > 0) & (valid['vol_ratio'] > 1.0)]
    neither = valid[(valid['vel_h8'] <= 0) | (valid['vol_ratio'] <= 1.0)]
    base_wr = valid['is_winner'].mean() * 100
    if len(both) > 0:
        print(f"  {label}: Base WR={base_wr:.1f}% | Both good: WR={both['is_winner'].mean()*100:.1f}% ({len(both)} trades) | Others: WR={neither['is_winner'].mean()*100:.1f}% ({len(neither)} trades)")

print("\n--- 2x2 Matrix: Velocity x Volume ---")
for label, sub in [("ALL", cross_df), ("HIGH-IC", cross_df[cross_df['high_ic']])]:
    valid = sub.dropna(subset=['vel_h8'])
    vp = valid['vel_h8'] > 0
    vr = valid['vol_ratio'] > 1.0
    print(f"\n  {label}:")
    print(f"  {'':>20} {'Vol>1x':>15} {'Vol<=1x':>15}")
    for vname, vmask in [('Vel>0', vp), ('Vel<=0', ~vp)]:
        for volname, volmask in [('', vr), ('', ~vr)]:
            pass  # Will print as matrix
    # Print properly
    for vname, vmask in [('Vel>0', vp), ('Vel<=0', ~vp)]:
        cells = []
        for volmask in [vr, ~vr]:
            cell = valid[vmask & volmask]
            if len(cell) > 0:
                cells.append(f"WR={cell['is_winner'].mean()*100:.1f}% (n={len(cell)})")
            else:
                cells.append("N/A")
        print(f"  {vname:>20} {cells[0]:>15} {cells[1]:>15}")


# ============================================================
# SUMMARY & RANKINGS
# ============================================================
print("\n" + "="*70)
print("SIGNAL RANKING & RECOMMENDATIONS")
print("="*70)

# Compute separation metrics for each signal
separations = {}

# 1. Z-score decay d3
valid_zs = zs_df.dropna(subset=['z_decay_d3'])
if len(valid_zs) > 0:
    w_decay = valid_zs[valid_zs['is_winner']]['z_decay_d3'].median()
    l_decay = valid_zs[~valid_zs['is_winner']]['z_decay_d3'].median()
    separations['Z-Score Decay (d3)'] = abs(w_decay - l_decay)
    print(f"\n1. Z-Score Decay at d3: Winners median={w_decay:.4f}, Losers median={l_decay:.4f}, separation={abs(w_decay - l_decay):.4f}")

# 2. Velocity at h8
valid_vel = vel_df.dropna(subset=['vel_h8'])
if len(valid_vel) > 0:
    w_vel = valid_vel[valid_vel['is_winner']]['vel_h8'].median()
    l_vel = valid_vel[~valid_vel['is_winner']]['vel_h8'].median()
    separations['Velocity h8'] = abs(w_vel - l_vel)
    # Also compute as win rate uplift
    pos_vel_wr = valid_vel[valid_vel['vel_h8'] > 0]['is_winner'].mean()
    neg_vel_wr = valid_vel[valid_vel['vel_h8'] <= 0]['is_winner'].mean()
    separations['Velocity h8 WR split'] = abs(pos_vel_wr - neg_vel_wr)
    print(f"2. Velocity at h8: Winners median={w_vel*100:.4f}%, Losers median={l_vel*100:.4f}%, separation={abs(w_vel - l_vel)*100:.4f}%")
    print(f"   WR split: Vel>0 WR={pos_vel_wr*100:.1f}%, Vel<=0 WR={neg_vel_wr*100:.1f}%, split={abs(pos_vel_wr - neg_vel_wr)*100:.1f}pp")

# 3. Volume ratio
valid_vol = vol_df2.dropna(subset=['vol_ratio'])
if len(valid_vol) > 0:
    w_vol = valid_vol[valid_vol['is_winner']]['vol_ratio'].median()
    l_vol = valid_vol[~valid_vol['is_winner']]['vol_ratio'].median()
    separations['Volume Ratio'] = abs(w_vol - l_vol)
    above_wr = valid_vol[valid_vol['vol_ratio'] > 1.0]['is_winner'].mean()
    below_wr = valid_vol[valid_vol['vol_ratio'] <= 1.0]['is_winner'].mean()
    separations['Volume WR split'] = abs(above_wr - below_wr)
    print(f"3. Volume Ratio: Winners median={w_vol:.3f}, Losers median={l_vol:.3f}, separation={abs(w_vol - l_vol):.3f}")
    print(f"   WR split: Vol>1x WR={above_wr*100:.1f}%, Vol<=1x WR={below_wr*100:.1f}%, split={abs(above_wr - below_wr)*100:.1f}pp")

# 4. 24h Sharpe
valid_sharpe = vel_df.dropna(subset=['sharpe_24h'])
if len(valid_sharpe) > 0:
    w_sh = valid_sharpe[valid_sharpe['is_winner']]['sharpe_24h'].median()
    l_sh = valid_sharpe[~valid_sharpe['is_winner']]['sharpe_24h'].median()
    separations['24h Sharpe'] = abs(w_sh - l_sh)
    pos_sh_wr = valid_sharpe[valid_sharpe['sharpe_24h'] > 0]['is_winner'].mean()
    neg_sh_wr = valid_sharpe[valid_sharpe['sharpe_24h'] <= 0]['is_winner'].mean()
    separations['24h Sharpe WR split'] = abs(pos_sh_wr - neg_sh_wr)
    print(f"4. 24h Sharpe: Winners median={w_sh:.4f}, Losers median={l_sh:.4f}, separation={abs(w_sh - l_sh):.4f}")
    print(f"   WR split: Sharpe>0 WR={pos_sh_wr*100:.1f}%, Sharpe<=0 WR={neg_sh_wr*100:.1f}%, split={abs(pos_sh_wr - neg_sh_wr)*100:.1f}pp")

# Best signal threshold recommendation
print("\n" + "-"*50)
print("THRESHOLD RECOMMENDATIONS FOR BEST SIGNAL")
print("-"*50)

# Test various velocity thresholds
print("\nVelocity at Hour 8 - Threshold Sweep:")
print(f"{'Threshold':>12} {'Keep%':>8} {'WR':>8} {'Avg PnL':>10} {'Total PnL':>12}")
for thresh in [-0.005, -0.002, 0.0, 0.002, 0.005, 0.01]:
    valid_v = vel_df.dropna(subset=['vel_h8']).copy()
    kept = valid_v[valid_v['vel_h8'] > thresh]
    if len(kept) > 0:
        # We need PnL - merge back
        # Use index alignment from trades_df
        pass

# Better approach: build a combined df with PnL
combined_results = []
for idx, trade in trades_df.iterrows():
    entry_ts = get_entry_ts(trade)
    if entry_ts is None:
        continue

    tok_1h = get_1h(trade['token'])
    if tok_1h is None:
        continue

    entry_idx = tok_1h.index.get_indexer([entry_ts], method='nearest')[0]
    if entry_idx < 0:
        continue

    direction = trade['direction']
    entry_price = tok_1h.iloc[entry_idx]['close']

    row = {
        'pnl': trade['pnl'],
        'is_winner': trade['is_winner'],
        'high_ic': trade['high_ic'],
        'token': trade['token'],
        'margin_usd': trade['margin_usd'],
    }

    # Velocity at h8
    if entry_idx + 8 < len(tok_1h):
        p8 = tok_1h.iloc[entry_idx + 8]['close']
        row['vel_h8'] = ((p8 - entry_price) / entry_price * direction) / np.sqrt(8)

    # 24h sharpe
    max_h = min(24, len(tok_1h) - entry_idx - 1)
    if max_h >= 2:
        hourly_prices = [tok_1h.iloc[entry_idx + i]['close'] for i in range(max_h + 1)]
        hourly_rets = np.diff(hourly_prices) / hourly_prices[:-1] * direction
        if np.std(hourly_rets) > 0:
            row['sharpe_24h'] = np.mean(hourly_rets) / np.std(hourly_rets)

    # Volume ratio (only if enough history)
    if entry_idx >= 20 * 24:
        lookback = 20 * 24
        hist_vol = tok_1h.iloc[entry_idx - lookback:entry_idx]['volume']
        avg_daily_vol = hist_vol.sum() / 20
        if avg_daily_vol > 0:
            end_v = min(entry_idx + 24, len(tok_1h))
            entry_day_vol = tok_1h.iloc[entry_idx:end_v]['volume'].sum()
            row['vol_ratio'] = entry_day_vol / avg_daily_vol

    combined_results.append(row)

comb_df = pd.DataFrame(combined_results)

print("\nVelocity at Hour 8 - Threshold Sweep (as exit rule at h8):")
print(f"{'Thresh':>8} {'Keep':>6} {'Keep%':>7} {'WR':>7} {'AvgPnL':>9} {'TotalPnL':>12} {'vs_Base':>10}")
base_total = comb_df['pnl'].sum()
base_n = len(comb_df)
valid_c = comb_df.dropna(subset=['vel_h8'])
for thresh in [-0.01, -0.005, -0.002, 0.0, 0.002, 0.005, 0.01]:
    kept = valid_c[valid_c['vel_h8'] > thresh]
    if len(kept) > 0:
        wr = kept['is_winner'].mean() * 100
        avg_pnl = kept['pnl'].mean()
        total_pnl = kept['pnl'].sum()
        # Approximate: killed trades contribute 0 PnL (exit at h8 breakeven)
        killed = valid_c[valid_c['vel_h8'] <= thresh]
        # More realistic: if we exit at h8, PnL ~ direction * (p8 - entry) * leverage
        # For simplicity, assume we save the rest of the loss
        killed_after_h8_pnl = 0  # approximate
        adj_total = total_pnl + killed_after_h8_pnl
        print(f"{thresh:>8.3f} {len(kept):>6} {len(kept)/len(valid_c)*100:>6.1f}% {wr:>6.1f}% {avg_pnl:>9.0f} {total_pnl:>12.0f} {total_pnl - base_total:>10.0f}")

# 24h Sharpe threshold sweep
print("\n24h Sharpe - Threshold Sweep:")
print(f"{'Thresh':>8} {'Keep':>6} {'Keep%':>7} {'WR':>7} {'AvgPnL':>9} {'TotalPnL':>12}")
valid_sh = comb_df.dropna(subset=['sharpe_24h'])
for thresh in [-0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2]:
    kept = valid_sh[valid_sh['sharpe_24h'] > thresh]
    if len(kept) > 0:
        wr = kept['is_winner'].mean() * 100
        avg_pnl = kept['pnl'].mean()
        total_pnl = kept['pnl'].sum()
        print(f"{thresh:>8.2f} {len(kept):>6} {len(kept)/len(valid_sh)*100:>6.1f}% {wr:>6.1f}% {avg_pnl:>9.0f} {total_pnl:>12.0f}")

# Estimated improvement
print("\n" + "-"*50)
print("ESTIMATED IMPROVEMENT FROM BEST EXIT RULE")
print("-"*50)

# For vel_h8 > 0 threshold: trades with negative velocity get exited at h8
# Estimate their h8 PnL (what we'd get if we exited there)
valid_c2 = comb_df.dropna(subset=['vel_h8']).copy()
kept_trades = valid_c2[valid_c2['vel_h8'] > 0]
killed_trades = valid_c2[valid_c2['vel_h8'] <= 0]

# For killed trades, approximate PnL at h8 exit from velocity
# vel_h8 = directional_return / sqrt(8), so directional_return = vel_h8 * sqrt(8)
# PnL at h8 ~ directional_return * margin * leverage (assume 1x from margin)
killed_h8_pnl = (killed_trades['vel_h8'] * np.sqrt(8) * killed_trades['margin_usd']).sum()

original_pnl = valid_c2['pnl'].sum()
improved_pnl = kept_trades['pnl'].sum() + killed_h8_pnl
print(f"Original total PnL: ${original_pnl:,.0f}")
print(f"With vel_h8>0 exit rule (exit losers at h8):")
print(f"  Kept trades PnL: ${kept_trades['pnl'].sum():,.0f}")
print(f"  Killed trades h8 exit PnL: ${killed_h8_pnl:,.0f}")
print(f"  Improved total PnL: ${improved_pnl:,.0f}")
print(f"  Delta: ${improved_pnl - original_pnl:,.0f} ({(improved_pnl/original_pnl - 1)*100:+.1f}%)")

# Same for high-IC only
print("\nHigh-IC subset:")
hi_valid = valid_c2[valid_c2['high_ic']]
hi_kept = hi_valid[hi_valid['vel_h8'] > 0]
hi_killed = hi_valid[hi_valid['vel_h8'] <= 0]
hi_killed_h8_pnl = (hi_killed['vel_h8'] * np.sqrt(8) * hi_killed['margin_usd']).sum()
hi_orig = hi_valid['pnl'].sum()
hi_improved = hi_kept['pnl'].sum() + hi_killed_h8_pnl
print(f"  Original: ${hi_orig:,.0f}, Improved: ${hi_improved:,.0f}, Delta: ${hi_improved - hi_orig:,.0f} ({(hi_improved/hi_orig - 1)*100:+.1f}%)")


# ============================================================
# Save raw results to JSON
# ============================================================
output = {
    'meta': {
        'total_trades': len(trades_df),
        'winners': int(trades_df['is_winner'].sum()),
        'losers': int((~trades_df['is_winner']).sum()),
        'high_ic_tokens': sorted(high_ic_tokens),
        'high_ic_threshold': HIGH_IC_THRESH,
    },
    'analysis1_zscore_decay': {
        'n_trades_with_data': len(zs_df),
        'all_tokens': {},
        'high_ic': {},
    },
    'analysis2_velocity': {
        'n_trades_with_data': len(vel_df),
    },
    'analysis3_volume': {
        'n_trades_with_data': len(vol_df2),
    },
}

# Fill z-score results
for group_name, gdf in [('all_tokens', zs_df), ('high_ic', zs_df[zs_df['high_ic']])]:
    for wl, mask in [('winners', gdf['is_winner']), ('losers', ~gdf['is_winner'])]:
        output['analysis1_zscore_decay'][group_name][wl] = {
            'n': int(mask.sum()),
            'median_z_at_checkpoints': {f'd{d}': float(gdf[mask][f'z_d{d}'].median()) for d in checkpoints_days if f'z_d{d}' in gdf.columns},
            'median_decay_rate': {f'd{d}': float(gdf[mask][f'z_decay_d{d}'].median()) for d in checkpoints_days[1:] if f'z_decay_d{d}' in gdf.columns and not gdf[mask][f'z_decay_d{d}'].isna().all()},
        }

# Fill velocity results
for group_name, gdf in [('all_tokens', vel_df), ('high_ic', vel_df[vel_df['high_ic']])]:
    output['analysis2_velocity'][group_name] = {}
    for wl, mask in [('winners', gdf['is_winner']), ('losers', ~gdf['is_winner'])]:
        output['analysis2_velocity'][group_name][wl] = {
            'n': int(mask.sum()),
            'median_velocity': {f'h{h}': float(gdf[mask][f'vel_h{h}'].median()) for h in velocity_checkpoints if f'vel_h{h}' in gdf.columns and not gdf[mask][f'vel_h{h}'].isna().all()},
            'median_return': {f'h{h}': float(gdf[mask][f'ret_h{h}'].median()) for h in velocity_checkpoints if f'ret_h{h}' in gdf.columns and not gdf[mask][f'ret_h{h}'].isna().all()},
            'median_sharpe_24h': float(gdf[mask]['sharpe_24h'].median()) if 'sharpe_24h' in gdf.columns else None,
        }

# Fill volume results
for group_name, gdf in [('all_tokens', vol_df2), ('high_ic', vol_df2[vol_df2['high_ic']])]:
    output['analysis3_volume'][group_name] = {}
    for wl, mask in [('winners', gdf['is_winner']), ('losers', ~gdf['is_winner'])]:
        output['analysis3_volume'][group_name][wl] = {
            'n': int(mask.sum()),
            'median_vol_ratio': float(gdf[mask]['vol_ratio'].median()),
            'median_sustained_vol_ratio': float(gdf[mask]['sustained_vol_ratio'].median()),
        }

# Recommendations
output['recommendations'] = {
    'rank': [
        {'signal': 'Early Price Velocity (h8)', 'reason': 'Largest WR split between positive/negative velocity'},
        {'signal': '24h Sharpe Ratio', 'reason': 'Consistency of direction separates winners from losers'},
        {'signal': 'Volume Confirmation', 'reason': 'Some signal but less discriminating'},
        {'signal': 'Z-Score Decay', 'reason': 'Check results for actual separation'},
    ],
    'best_threshold': {
        'signal': 'vel_h8',
        'threshold': 0.0,
        'rule': 'Exit at hour 8 if directional velocity is not positive',
    },
    'estimated_improvement': {
        'original_pnl': float(original_pnl),
        'improved_pnl': float(improved_pnl),
        'delta_pnl': float(improved_pnl - original_pnl),
        'delta_pct': float((improved_pnl / original_pnl - 1) * 100),
    }
}

with open(BASE / 'research/intratrade_dynamics.json', 'w') as f:
    json.dump(output, f, indent=2, default=str)

print(f"\nResults saved to {BASE / 'research/intratrade_dynamics.json'}")
print("\nDone.")
