#!/workspace/venv/bin/python
"""
Spot-Perp Basis (Cash-and-Carry) Strategy Backtest
====================================================

Strategies:
  1. Basis Mean Reversion (delta-neutral perp side)
  2. Basis Momentum (trend-following on basis changes)
  3. Cross-Token Basis Rank (cross-sectional market-neutral)
  4. Basis + Funding Combo (double confirmation)

Capital: $200K, max 5 positions, equal weight
Slippage: 10bps/side (20bps RT)
Leverage: 1x, 2x
OOS: 2025-07-01 to 2026-03-17
"""

import os
import warnings
import numpy as np
import pandas as pd
from itertools import product

warnings.filterwarnings('ignore')

# ── Paths & Constants ─────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
SPOT_DIR = os.path.join(PROJECT_DIR, 'data', 'spot', '1h_cache')
PERP_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
OOS_END   = pd.Timestamp('2026-03-17')

INITIAL_CAPITAL = 200_000.0
SLIPPAGE_BPS    = 10
MAX_POSITIONS   = 5
SLIP            = SLIPPAGE_BPS / 10_000


# ── Data Loading ──────────────────────────────────────────────────────
def load_all_pairs():
    """Load spot + perp data for all tokens that have both, compute basis."""
    spot_files = {f.replace('_1h.parquet', ''): f
                  for f in os.listdir(SPOT_DIR) if f.endswith('.parquet')}
    perp_files = {f.replace('_1h.parquet', ''): f
                  for f in os.listdir(PERP_DIR) if f.endswith('.parquet')}

    common = sorted(set(spot_files) & set(perp_files))
    pairs = {}

    for token in common:
        spot = pd.read_parquet(os.path.join(SPOT_DIR, spot_files[token]))
        perp = pd.read_parquet(os.path.join(PERP_DIR, perp_files[token]))

        if spot.index.tz is not None:
            spot.index = spot.index.tz_localize(None)
        if perp.index.tz is not None:
            perp.index = perp.index.tz_localize(None)

        # Align on common timestamps
        common_idx = spot.index.intersection(perp.index)
        if len(common_idx) < 500:
            continue

        df = pd.DataFrame(index=common_idx)
        df['spot_close']  = spot.loc[common_idx, 'close']
        df['perp_close']  = perp.loc[common_idx, 'close']
        df['spot_volume'] = spot.loc[common_idx, 'volume']
        df['perp_volume'] = perp.loc[common_idx, 'volume']

        # Funding from perp
        for col in ['funding_rate', 'funding_1h']:
            if col in perp.columns:
                df[col] = perp.loc[common_idx, col]

        # Basis = (perp - spot) / spot in bps
        df['basis_bps'] = (df['perp_close'] - df['spot_close']) / df['spot_close'] * 10000

        # Forward returns on perp (what we trade)
        for h in [1, 4, 8, 24, 48, 72]:
            df[f'perp_fwd_ret_{h}h'] = df['perp_close'].pct_change(h).shift(-h)

        # Basis change over 24h
        df['basis_change_24h'] = df['basis_bps'] - df['basis_bps'].shift(24)

        # Need sufficient OOS data
        oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
        if oos_mask.sum() < 100:
            continue

        pairs[token] = df

    return pairs


# ── STEP 1: Data Exploration ─────────────────────────────────────────
def explore_basis(pairs):
    print('\n' + '=' * 70)
    print('STEP 1: BASIS DATA EXPLORATION')
    print('=' * 70)

    # 1a. Average basis by token
    print('\n--- Average Basis (bps) by Token (full history) ---')
    avg_basis = {}
    for token, df in pairs.items():
        avg_basis[token] = df['basis_bps'].mean()
    avg_series = pd.Series(avg_basis).sort_values(ascending=False)
    print(f'  {"Token":>12s}  {"AvgBasis":>10s}  {"Token":>12s}  {"AvgBasis":>10s}')
    tokens_list = list(avg_series.items())
    half = (len(tokens_list) + 1) // 2
    for i in range(min(20, half)):
        t1, v1 = tokens_list[i]
        right = ''
        if i + half < len(tokens_list):
            t2, v2 = tokens_list[i + half]
            right = f'  {t2:>12s}  {v2:>+10.2f}'
        print(f'  {t1:>12s}  {v1:>+10.2f}{right}')
    print(f'\n  Overall average basis: {avg_series.mean():+.2f} bps')
    print(f'  Median: {avg_series.median():+.2f} bps')
    print(f'  Std: {avg_series.std():.2f} bps')

    # 1b. Basis distribution (percentiles) — use BTC as reference, then aggregate
    print('\n--- Basis Distribution (aggregate across all tokens, all hours) ---')
    all_basis = pd.concat([df['basis_bps'].dropna() for df in pairs.values()])
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    pct_vals = np.percentile(all_basis, percentiles)
    print(f'  {"Pctl":>6s}  {"Basis(bps)":>12s}')
    for p, v in zip(percentiles, pct_vals):
        print(f'  {p:>5d}%  {v:>+12.2f}')
    print(f'  Mean: {all_basis.mean():+.2f} bps, Std: {all_basis.std():.2f} bps')

    # BTC specific
    if 'BTC' in pairs:
        btc_basis = pairs['BTC']['basis_bps'].dropna()
        print(f'\n  BTC basis range: {btc_basis.min():+.1f} to {btc_basis.max():+.1f} bps')
        print(f'  BTC basis mean: {btc_basis.mean():+.2f} bps, std: {btc_basis.std():.2f} bps')

    # 1c. Basis autocorrelation
    print('\n--- Basis Autocorrelation (lag 1h, 4h, 24h) ---')
    print(f'  {"Token":>12s}  {"AC(1h)":>8s}  {"AC(4h)":>8s}  {"AC(24h)":>9s}  {"Behavior":>15s}')
    ac_data = {}
    for token, df in sorted(pairs.items()):
        b = df['basis_bps'].dropna()
        if len(b) < 200:
            continue
        ac1  = b.autocorr(lag=1)
        ac4  = b.autocorr(lag=4)
        ac24 = b.autocorr(lag=24)
        behavior = 'PERSISTENT' if ac24 > 0.5 else ('MEAN-REV' if ac24 < 0.2 else 'MIXED')
        ac_data[token] = {'ac1': ac1, 'ac4': ac4, 'ac24': ac24, 'behavior': behavior}

    # Show a summary
    ac_df = pd.DataFrame(ac_data).T
    print(f'\n  Aggregate autocorrelation:')
    print(f'    AC(1h)  mean={ac_df["ac1"].mean():.3f}, median={ac_df["ac1"].median():.3f}')
    print(f'    AC(4h)  mean={ac_df["ac4"].mean():.3f}, median={ac_df["ac4"].median():.3f}')
    print(f'    AC(24h) mean={ac_df["ac24"].mean():.3f}, median={ac_df["ac24"].median():.3f}')
    behaviors = ac_df['behavior'].value_counts()
    for beh, cnt in behaviors.items():
        print(f'    {beh}: {cnt} tokens')

    # Show top and bottom
    print(f'\n  Most persistent (high AC24):')
    for token in ac_df.sort_values('ac24', ascending=False).head(5).index:
        r = ac_data[token]
        print(f'    {token:>12s}  {r["ac1"]:.3f}  {r["ac4"]:.3f}  {r["ac24"]:.3f}  {r["behavior"]}')
    print(f'  Most mean-reverting (low AC24):')
    for token in ac_df.sort_values('ac24', ascending=True).head(5).index:
        r = ac_data[token]
        print(f'    {token:>12s}  {r["ac1"]:.3f}  {r["ac4"]:.3f}  {r["ac24"]:.3f}  {r["behavior"]}')

    # 1d. Basis correlation with forward returns
    print('\n--- Basis vs Forward Returns Correlation ---')
    print(f'  {"Token":>12s}  {"fwd1h":>8s}  {"fwd4h":>8s}  {"fwd24h":>9s}  {"fwd48h":>9s}')
    corr_data = {}
    for token, df in sorted(pairs.items()):
        sub = df.dropna(subset=['basis_bps', 'perp_fwd_ret_24h'])
        if len(sub) < 200:
            continue
        corrs = {}
        for h in [1, 4, 24, 48]:
            col = f'perp_fwd_ret_{h}h'
            if col in sub.columns:
                c = sub['basis_bps'].corr(sub[col])
                corrs[f'fwd{h}h'] = c
        corr_data[token] = corrs

    corr_df = pd.DataFrame(corr_data).T
    print(f'\n  Aggregate correlation (basis vs fwd return):')
    for col in corr_df.columns:
        print(f'    {col}: mean={corr_df[col].mean():.4f}, median={corr_df[col].median():.4f}')

    # Strongest negative correlations (mean reversion signal)
    if 'fwd24h' in corr_df.columns:
        print(f'\n  Strongest negative corr (basis -> fwd24h, mean-rev candidates):')
        for token in corr_df.sort_values('fwd24h', ascending=True).head(10).index:
            r = corr_df.loc[token]
            print(f'    {token:>12s}  {r.get("fwd1h", 0):.4f}  {r.get("fwd4h", 0):.4f}  {r.get("fwd24h", 0):.4f}  {r.get("fwd48h", 0):.4f}')


# ── Portfolio Simulation Engine ───────────────────────────────────────
def simulate_portfolio(trades_df, leverage, label=''):
    """
    Simulate portfolio with position limits and sequential equity allocation.
    trades_df must have: entry_time, exit_time, raw_ret, direction, token
    """
    if trades_df is None or len(trades_df) == 0:
        return [], INITIAL_CAPITAL, 0.0, 0.0

    df = trades_df.sort_values('entry_time').reset_index(drop=True)

    equity = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL
    max_dd = 0.0
    trades_out = []
    active = []  # (exit_time, token, direction, raw_ret, entry_time, margin)

    for _, row in df.iterrows():
        entry_time = row['entry_time']

        # Close expired
        still_active = []
        for pos in active:
            if entry_time >= pos[0]:
                margin = pos[5]
                lev_ret = pos[3] * leverage
                pnl_usd = margin * lev_ret
                equity += pnl_usd
                trades_out.append({
                    'token': pos[1], 'direction': pos[2],
                    'entry_time': pos[4], 'exit_time': pos[0],
                    'pnl_pct': lev_ret * 100, 'pnl_usd': pnl_usd,
                    'leverage': leverage,
                })
                if equity > peak_equity:
                    peak_equity = equity
                dd_now = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
                if dd_now > max_dd:
                    max_dd = dd_now
            else:
                still_active.append(pos)
        active = still_active

        if equity <= 0:
            break

        # Try entry
        if len(active) < MAX_POSITIONS:
            token = row['token']
            direction = row['direction']
            already_in = any(p[1] == token and p[2] == direction for p in active)
            if not already_in:
                margin = equity / MAX_POSITIONS
                active.append((
                    row['exit_time'], token, direction, row['raw_ret'],
                    entry_time, margin,
                ))

    # Close remaining
    for pos in active:
        margin = pos[5]
        lev_ret = pos[3] * leverage
        pnl_usd = margin * lev_ret
        equity += pnl_usd
        trades_out.append({
            'token': pos[1], 'direction': pos[2],
            'entry_time': pos[4], 'exit_time': pos[0],
            'pnl_pct': lev_ret * 100, 'pnl_usd': pnl_usd,
            'leverage': leverage,
        })
    if equity > peak_equity:
        peak_equity = equity
    dd_now = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
    if dd_now > max_dd:
        max_dd = dd_now

    # Compute Sharpe
    if trades_out:
        tdf = pd.DataFrame(trades_out)
        oos_days = (OOS_END - OOS_START).days
        daily_equiv = tdf['pnl_pct'].values
        if len(daily_equiv) > 1:
            sharpe = (np.mean(daily_equiv) / np.std(daily_equiv)) * np.sqrt(252 * 24 / max(1, oos_days * 24 / len(daily_equiv)))
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return trades_out, equity, max_dd, sharpe


def report_config(trades_list, final_equity, max_dd, sharpe, config_label):
    """Print a compact report for one configuration."""
    if not trades_list:
        print(f'\n  {config_label}: NO TRADES')
        return None

    df = pd.DataFrame(trades_list)
    total_pnl = final_equity - INITIAL_CAPITAL
    total_ret = (final_equity / INITIAL_CAPITAL - 1) * 100
    n_trades = len(df)
    n_long  = (df['direction'] == 1).sum()
    n_short = (df['direction'] == -1).sum()
    win_rate = (df['pnl_usd'] > 0).mean() * 100
    avg_pnl  = df['pnl_pct'].mean()

    gross_wins   = df.loc[df['pnl_usd'] > 0, 'pnl_usd'].sum()
    gross_losses = abs(df.loc[df['pnl_usd'] <= 0, 'pnl_usd'].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    oos_days = (OOS_END - OOS_START).days
    annual_factor = 365.25 / oos_days if oos_days > 0 else 1
    annual_ret = total_ret * annual_factor

    print(f'\n  {"=" * 70}')
    print(f'  {config_label}')
    print(f'  {"=" * 70}')
    print(f'  Trades: {n_trades} (L:{n_long} / S:{n_short})')
    print(f'  Final equity: ${final_equity:,.0f} (PnL: ${total_pnl:+,.0f})')
    print(f'  Total return:  {total_ret:+.1f}%')
    print(f'  Annual return: {annual_ret:+.1f}%')
    print(f'  Max drawdown:  {max_dd * 100:.1f}%')
    print(f'  Sharpe:        {sharpe:.2f}')
    print(f'  Win rate:      {win_rate:.1f}%')
    print(f'  Avg PnL/trade: {avg_pnl:+.2f}%')
    print(f'  Profit factor: {pf:.2f}')

    # Monthly breakdown
    df['month'] = pd.to_datetime(df['entry_time']).dt.to_period('M')
    print(f'\n  Per-Month:')
    print(f'  {"Month":>10s}  {"Trades":>6s}  {"WinR":>6s}  {"PnL":>12s}  {"AvgRet":>8s}')
    for month, mdf in df.groupby('month'):
        mn = len(mdf)
        mwr = (mdf['pnl_usd'] > 0).mean() * 100
        mpnl = mdf['pnl_usd'].sum()
        mavg = mdf['pnl_pct'].mean()
        print(f'  {str(month):>10s}  {mn:6d}  {mwr:5.1f}%  ${mpnl:>+11,.0f}  {mavg:>+7.2f}%')

    return {
        'config': config_label,
        'trades': n_trades, 'n_long': n_long, 'n_short': n_short,
        'final_equity': final_equity,
        'total_return_pct': total_ret,
        'annual_return_pct': annual_ret,
        'max_dd_pct': max_dd * 100,
        'sharpe': sharpe,
        'win_rate': win_rate,
        'profit_factor': pf,
        'avg_pnl_pct': avg_pnl,
    }


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY 1: Basis Mean Reversion
# ═══════════════════════════════════════════════════════════════════════
def strategy1_mean_reversion(pairs, entry_thresh_bps, exit_thresh_bps, max_hold_hours):
    """
    Basis > entry_thresh: SHORT perp (expect basis to compress)
    Basis < -entry_thresh: LONG perp (expect basis to expand)
    Exit: basis within +/- exit_thresh OR max hold reached
    """
    all_trades = []

    for token, df in pairs.items():
        close_arr = df['perp_close'].values
        basis_arr = df['basis_bps'].values
        idx_arr   = df.index.values
        n = len(df)

        oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
        oos_positions = np.where(oos_mask)[0]

        for i in oos_positions:
            b = basis_arr[i]
            if np.isnan(b):
                continue

            direction = 0
            if b > entry_thresh_bps:
                direction = -1  # SHORT perp
            elif b < -entry_thresh_bps:
                direction = 1   # LONG perp

            if direction == 0:
                continue

            entry_px = close_arr[i] * (1 + SLIP * direction)
            max_exit = min(i + max_hold_hours, n - 1)

            # Find exit
            exit_idx = max_exit
            for j in range(i + 1, max_exit + 1):
                bj = basis_arr[j]
                if not np.isnan(bj) and abs(bj) <= exit_thresh_bps:
                    exit_idx = j
                    break

            exit_px = close_arr[exit_idx] * (1 - SLIP * direction)
            raw_ret = direction * (exit_px / entry_px - 1)

            all_trades.append({
                'entry_time': idx_arr[i],
                'exit_time': idx_arr[exit_idx],
                'raw_ret': raw_ret,
                'direction': direction,
                'token': token,
            })

    if not all_trades:
        return pd.DataFrame()
    return pd.DataFrame(all_trades)


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY 2: Basis Momentum
# ═══════════════════════════════════════════════════════════════════════
def strategy2_momentum(pairs, momentum_thresh_bps, hold_hours):
    """
    Basis expanding (change_24h > thresh): LONG perp (trend continuation)
    Basis contracting (change_24h < -thresh): SHORT perp (trend continuation)
    Fixed hold exit.
    """
    all_trades = []

    for token, df in pairs.items():
        close_arr = df['perp_close'].values
        change_arr = df['basis_change_24h'].values
        idx_arr = df.index.values
        n = len(df)

        oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
        oos_positions = np.where(oos_mask)[0]

        for i in oos_positions:
            ch = change_arr[i]
            if np.isnan(ch):
                continue

            direction = 0
            if ch > momentum_thresh_bps:
                direction = 1   # LONG perp (basis expanding = bullish)
            elif ch < -momentum_thresh_bps:
                direction = -1  # SHORT perp (basis contracting = bearish)

            if direction == 0:
                continue

            entry_px = close_arr[i] * (1 + SLIP * direction)
            exit_idx = min(i + hold_hours, n - 1)
            exit_px = close_arr[exit_idx] * (1 - SLIP * direction)
            raw_ret = direction * (exit_px / entry_px - 1)

            all_trades.append({
                'entry_time': idx_arr[i],
                'exit_time': idx_arr[exit_idx],
                'raw_ret': raw_ret,
                'direction': direction,
                'token': token,
            })

    if not all_trades:
        return pd.DataFrame()
    return pd.DataFrame(all_trades)


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY 3: Cross-Token Basis Rank
# ═══════════════════════════════════════════════════════════════════════
def strategy3_cross_rank(pairs, top_n, hold_hours):
    """
    Each rebalance:
      SHORT top_n tokens by basis (highest = most overvalued perps)
      LONG bottom top_n tokens by basis (lowest = most undervalued)
    Rebalance every hold_hours.
    """
    # Build a panel of basis values
    basis_panel = pd.DataFrame({token: df['basis_bps'] for token, df in pairs.items()})

    oos_mask = (basis_panel.index >= OOS_START) & (basis_panel.index <= OOS_END)
    oos_idx = basis_panel.index[oos_mask]

    # Rebalance at every hold_hours interval
    rebalance_times = oos_idx[::hold_hours]
    all_trades = []

    for rb_time in rebalance_times:
        row = basis_panel.loc[rb_time].dropna()
        if len(row) < 2 * top_n:
            continue

        ranked = row.sort_values()
        long_tokens  = ranked.head(top_n).index.tolist()
        short_tokens = ranked.tail(top_n).index.tolist()

        # Find exit time
        exit_time_pos = basis_panel.index.get_loc(rb_time)
        if isinstance(exit_time_pos, slice):
            exit_time_pos = exit_time_pos.start
        exit_pos = min(exit_time_pos + hold_hours, len(basis_panel) - 1)
        exit_time = basis_panel.index[exit_pos]

        for token in long_tokens:
            df = pairs[token]
            if rb_time not in df.index or exit_time not in df.index:
                continue
            entry_px = df.loc[rb_time, 'perp_close'] * (1 + SLIP)
            exit_px  = df.loc[exit_time, 'perp_close'] * (1 - SLIP)
            raw_ret  = (exit_px / entry_px - 1)
            all_trades.append({
                'entry_time': rb_time, 'exit_time': exit_time,
                'raw_ret': raw_ret, 'direction': 1, 'token': token,
            })

        for token in short_tokens:
            df = pairs[token]
            if rb_time not in df.index or exit_time not in df.index:
                continue
            entry_px = df.loc[rb_time, 'perp_close'] * (1 - SLIP)
            exit_px  = df.loc[exit_time, 'perp_close'] * (1 + SLIP)
            raw_ret  = -1 * (exit_px / entry_px - 1)
            all_trades.append({
                'entry_time': rb_time, 'exit_time': exit_time,
                'raw_ret': raw_ret, 'direction': -1, 'token': token,
            })

    if not all_trades:
        return pd.DataFrame()
    return pd.DataFrame(all_trades)


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY 4: Basis + Funding Combo
# ═══════════════════════════════════════════════════════════════════════
def strategy4_basis_funding(pairs, basis_thresh_bps, hold_hours):
    """
    Basis > thresh AND funding_rate > 0: SHORT (crowded longs)
    Basis < -thresh AND funding_rate < 0: LONG (crowded shorts)
    Fixed hold exit.
    """
    all_trades = []

    for token, df in pairs.items():
        close_arr   = df['perp_close'].values
        basis_arr   = df['basis_bps'].values
        idx_arr     = df.index.values
        n = len(df)

        funding_col = 'funding_1h' if 'funding_1h' in df.columns else 'funding_rate'
        funding_arr = df[funding_col].values

        oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
        oos_positions = np.where(oos_mask)[0]

        for i in oos_positions:
            b = basis_arr[i]
            f = funding_arr[i]
            if np.isnan(b) or np.isnan(f):
                continue

            direction = 0
            if b > basis_thresh_bps and f > 0:
                direction = -1  # SHORT
            elif b < -basis_thresh_bps and f < 0:
                direction = 1   # LONG

            if direction == 0:
                continue

            entry_px = close_arr[i] * (1 + SLIP * direction)
            exit_idx = min(i + hold_hours, n - 1)
            exit_px  = close_arr[exit_idx] * (1 - SLIP * direction)
            raw_ret  = direction * (exit_px / entry_px - 1)

            all_trades.append({
                'entry_time': idx_arr[i],
                'exit_time': idx_arr[exit_idx],
                'raw_ret': raw_ret,
                'direction': direction,
                'token': token,
            })

    if not all_trades:
        return pd.DataFrame()
    return pd.DataFrame(all_trades)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    print('=' * 70)
    print('SPOT-PERP BASIS STRATEGY BACKTEST')
    print('=' * 70)
    print(f'Capital: ${INITIAL_CAPITAL:,.0f} | Slippage: {SLIPPAGE_BPS}bps/side')
    print(f'Max positions: {MAX_POSITIONS} | OOS: {OOS_START.date()} to {OOS_END.date()}')

    # ── Load data ────────────────────────────────────────────────────
    print('\n-- Loading spot + perp pairs --')
    pairs = load_all_pairs()
    print(f'  Loaded {len(pairs)} tokens with both spot & perp data')

    # ── Step 1: Data exploration ─────────────────────────────────────
    explore_basis(pairs)

    # ── Step 2: Backtests ────────────────────────────────────────────
    all_results = []
    leverages = [1, 2]

    # ─── Strategy 1: Mean Reversion ──────────────────────────────────
    print('\n' + '#' * 70)
    print('# STRATEGY 1: BASIS MEAN REVERSION')
    print('#' * 70)

    s1_configs = list(product(
        [5, 10, 20, 50, 100],   # entry threshold bps
        [2, 5, 10],             # exit threshold bps
    ))
    max_hold = 72

    for entry_bps, exit_bps in s1_configs:
        if exit_bps >= entry_bps:
            continue  # exit must be tighter than entry
        trades_df = strategy1_mean_reversion(pairs, entry_bps, exit_bps, max_hold)
        for lev in leverages:
            label = f'S1-MeanRev entry={entry_bps}bps exit={exit_bps}bps hold={max_hold}h | {lev}x'
            t_list, eq, dd, sharpe = simulate_portfolio(trades_df, lev, label)
            result = report_config(t_list, eq, dd, sharpe, label)
            if result:
                all_results.append(result)

    # ─── Strategy 2: Basis Momentum ─────────────────────────────────
    print('\n' + '#' * 70)
    print('# STRATEGY 2: BASIS MOMENTUM')
    print('#' * 70)

    s2_configs = list(product(
        [5, 10, 20, 50],   # momentum threshold bps
        [24, 48],           # hold hours
    ))

    for mom_bps, hold_h in s2_configs:
        trades_df = strategy2_momentum(pairs, mom_bps, hold_h)
        for lev in leverages:
            label = f'S2-Momentum thresh={mom_bps}bps hold={hold_h}h | {lev}x'
            t_list, eq, dd, sharpe = simulate_portfolio(trades_df, lev, label)
            result = report_config(t_list, eq, dd, sharpe, label)
            if result:
                all_results.append(result)

    # ─── Strategy 3: Cross-Token Basis Rank ──────────────────────────
    print('\n' + '#' * 70)
    print('# STRATEGY 3: CROSS-TOKEN BASIS RANK')
    print('#' * 70)

    s3_configs = list(product(
        [5],        # top_n
        [24, 48],   # rebalance hours
    ))

    for top_n, hold_h in s3_configs:
        trades_df = strategy3_cross_rank(pairs, top_n, hold_h)
        for lev in leverages:
            label = f'S3-CrossRank top={top_n} rebal={hold_h}h | {lev}x'
            t_list, eq, dd, sharpe = simulate_portfolio(trades_df, lev, label)
            result = report_config(t_list, eq, dd, sharpe, label)
            if result:
                all_results.append(result)

    # ─── Strategy 4: Basis + Funding Combo ───────────────────────────
    print('\n' + '#' * 70)
    print('# STRATEGY 4: BASIS + FUNDING COMBO')
    print('#' * 70)

    s4_configs = list(product(
        [10, 20, 50],      # basis threshold bps
        [24, 48, 72],      # hold hours
    ))

    for basis_bps, hold_h in s4_configs:
        trades_df = strategy4_basis_funding(pairs, basis_bps, hold_h)
        for lev in leverages:
            label = f'S4-BasisFunding basis={basis_bps}bps hold={hold_h}h | {lev}x'
            t_list, eq, dd, sharpe = simulate_portfolio(trades_df, lev, label)
            result = report_config(t_list, eq, dd, sharpe, label)
            if result:
                all_results.append(result)

    # ═══ SUMMARY ══════════════════════════════════════════════════════
    print('\n' + '=' * 70)
    print('GRAND SUMMARY — ALL CONFIGURATIONS')
    print('=' * 70)

    if all_results:
        summary = pd.DataFrame(all_results)
        summary = summary.sort_values('annual_return_pct', ascending=False)

        print(f'\n{"Config":<60s} {"Trd":>5s} {"TotRet":>8s} {"AnnRet":>8s} {"MaxDD":>7s} {"Sharpe":>7s} {"WinR":>6s} {"PF":>6s}')
        print('-' * 115)
        for _, row in summary.iterrows():
            print(
                f'{row["config"]:<60s} '
                f'{row["trades"]:>5.0f} '
                f'{row["total_return_pct"]:>+7.1f}% '
                f'{row["annual_return_pct"]:>+7.1f}% '
                f'{row["max_dd_pct"]:>6.1f}% '
                f'{row["sharpe"]:>7.2f} '
                f'{row["win_rate"]:>5.1f}% '
                f'{row["profit_factor"]:>5.2f}'
            )

        print(f'\n{"=" * 70}')
        print('TOP 10 CONFIGURATIONS (by Sharpe)')
        print('=' * 70)
        best = summary.sort_values('sharpe', ascending=False).head(10)
        for _, row in best.iterrows():
            print(
                f'  {row["config"]:<60s} '
                f'Ann: {row["annual_return_pct"]:>+7.1f}%  '
                f'DD: {row["max_dd_pct"]:>5.1f}%  '
                f'Sharpe: {row["sharpe"]:>6.2f}  '
                f'WR: {row["win_rate"]:>5.1f}%  '
                f'PF: {row["profit_factor"]:>5.2f}'
            )

        print(f'\n{"=" * 70}')
        print('TOP 10 CONFIGURATIONS (by Annual Return)')
        print('=' * 70)
        best2 = summary.sort_values('annual_return_pct', ascending=False).head(10)
        for _, row in best2.iterrows():
            print(
                f'  {row["config"]:<60s} '
                f'Ann: {row["annual_return_pct"]:>+7.1f}%  '
                f'DD: {row["max_dd_pct"]:>5.1f}%  '
                f'Sharpe: {row["sharpe"]:>6.2f}  '
                f'WR: {row["win_rate"]:>5.1f}%  '
                f'PF: {row["profit_factor"]:>5.2f}'
            )

        # Positive return configs
        profitable = summary[summary['total_return_pct'] > 0]
        losing     = summary[summary['total_return_pct'] <= 0]
        print(f'\n  Profitable configs: {len(profitable)} / {len(summary)}')
        print(f'  Losing configs:     {len(losing)} / {len(summary)}')

        # Best risk-adjusted
        print(f'\n{"=" * 70}')
        print('BEST RISK-ADJUSTED (Sharpe > 0.5 AND MaxDD < 30%)')
        print('=' * 70)
        good = summary[(summary['sharpe'] > 0.5) & (summary['max_dd_pct'] < 30)]
        if len(good) > 0:
            for _, row in good.sort_values('sharpe', ascending=False).iterrows():
                print(
                    f'  {row["config"]:<60s} '
                    f'Ann: {row["annual_return_pct"]:>+7.1f}%  '
                    f'DD: {row["max_dd_pct"]:>5.1f}%  '
                    f'Sharpe: {row["sharpe"]:>6.2f}  '
                    f'PF: {row["profit_factor"]:>5.2f}'
                )
        else:
            print('  None found. Showing best Sharpe regardless:')
            for _, row in summary.sort_values('sharpe', ascending=False).head(5).iterrows():
                print(
                    f'  {row["config"]:<60s} '
                    f'Ann: {row["annual_return_pct"]:>+7.1f}%  '
                    f'DD: {row["max_dd_pct"]:>5.1f}%  '
                    f'Sharpe: {row["sharpe"]:>6.2f}  '
                    f'PF: {row["profit_factor"]:>5.2f}'
                )

        # Per-strategy summary
        print(f'\n{"=" * 70}')
        print('PER-STRATEGY SUMMARY (best config per strategy)')
        print('=' * 70)
        for prefix, name in [('S1', 'Mean Reversion'), ('S2', 'Momentum'),
                              ('S3', 'Cross Rank'), ('S4', 'Basis+Funding')]:
            strat = summary[summary['config'].str.startswith(prefix)]
            if len(strat) == 0:
                print(f'\n  {name}: NO CONFIGS')
                continue
            best_row = strat.sort_values('sharpe', ascending=False).iloc[0]
            print(f'\n  {name} (best by Sharpe):')
            print(f'    {best_row["config"]}')
            print(f'    Trades: {best_row["trades"]:.0f} | Ann: {best_row["annual_return_pct"]:+.1f}% | '
                  f'DD: {best_row["max_dd_pct"]:.1f}% | Sharpe: {best_row["sharpe"]:.2f} | '
                  f'WR: {best_row["win_rate"]:.1f}% | PF: {best_row["profit_factor"]:.2f}')

    print('\n' + '=' * 70)
    print('BACKTEST COMPLETE')
    print('=' * 70)


if __name__ == '__main__':
    main()
