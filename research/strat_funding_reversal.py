#!/workspace/venv/bin/python
"""
Funding Rate Reversal — Bidirectional Strategy Backtest
========================================================

LONG ENTRY (contrarian):
  - funding_zscore_72h < -2.0 (extreme negative = shorts overcrowded)
  - ret_24h <= 0 (price falling — contrarian filter)
  - regime != CRISIS

SHORT ENTRY (illiquidity spike):
  - amihud_zscore_72h > 2.0 (liquidity drying up = bearish)
  - ret_4h < 0 (price confirming)
  - regime != CRISIS

Exit configs:
  A: Fixed hold (8h, 24h, 48h)
  B: 2x ATR trail, no-stop for first 8h, max 48h hold
  C: Signal-based (long: exit when funding_zscore > 0; short: exit when amihud_zscore < 0)

Leverage: 1x, 3x, 5x, 7x
Capital: $200K, slippage 10bps, max 5 positions simultaneously.
OOS: 2025-07-01 to 2026-03-17. Per-month breakdown.
Target: 300%+ annual, <20% DD.
"""

import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
OOS_END = pd.Timestamp('2026-03-17')

INITIAL_CAPITAL = 200_000.0
SLIPPAGE_BPS = 10
MAX_POSITIONS = 5


# ── Data Loading & Feature Engineering ──────────────────────────────────
def load_and_prepare_all():
    """Load all tokens, compute features and signals."""
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.parquet')]
    token_data = {}

    for f in files:
        token = f.replace('_1h.parquet', '')
        df = pd.read_parquet(os.path.join(CACHE_DIR, f))
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        if len(df[df.index < OOS_START]) < 200:
            continue
        if len(df[df.index >= OOS_START]) < 100:
            continue
        oos_vol = df.loc[df.index >= OOS_START, 'volume']
        if oos_vol.mean() < 50_000:
            continue

        df = _compute_features(df)
        df = _generate_signals(df)
        token_data[token] = df

    return token_data


def _compute_features(df):
    df = df.copy()

    df['ret_1h'] = df['close'].pct_change(1)
    df['ret_4h'] = df['close'].pct_change(4)
    df['ret_24h'] = df['close'].pct_change(24)

    funding_col = 'funding_1h' if 'funding_1h' in df.columns else 'funding_rate'
    fm = df[funding_col].rolling(72, min_periods=24).mean()
    fs = df[funding_col].rolling(72, min_periods=24).std().replace(0, np.nan)
    df['funding_zscore_72h'] = (df[funding_col] - fm) / fs

    abs_ret = df['ret_1h'].abs()
    safe_vol = df['volume'].replace(0, np.nan)
    amihud_1h = abs_ret / safe_vol
    df['amihud_24h'] = amihud_1h.rolling(24, min_periods=12).mean()
    am = df['amihud_24h'].rolling(72, min_periods=24).mean()
    astd = df['amihud_24h'].rolling(72, min_periods=24).std().replace(0, np.nan)
    df['amihud_zscore_72h'] = (df['amihud_24h'] - am) / astd

    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr_14'] = tr.rolling(14, min_periods=7).mean()

    rh = df['close'].rolling(168, min_periods=24).max()
    dd = (df['close'] / rh) - 1
    v24 = df['ret_1h'].rolling(24, min_periods=12).std()
    v168 = df['ret_1h'].rolling(168, min_periods=48).std().replace(0, np.nan)
    df['is_crisis'] = (dd < -0.15) & ((v24 / v168) > 3.0)

    return df


def _generate_signals(df):
    df = df.copy()
    df['signal_long'] = (
        (df['funding_zscore_72h'] < -2.0) &
        (df['ret_24h'] <= 0) &
        (~df['is_crisis'])
    )
    df['signal_short'] = (
        (df['amihud_zscore_72h'] > 2.0) &
        (df['ret_4h'] < 0) &
        (~df['is_crisis'])
    )
    return df


# ── Vectorized exit computation per token ──────────────────────────────
def compute_exits_fixed(df, signal_col, direction, hold_hours, slip):
    """Vectorized: for each signal, compute exit at fixed hold."""
    oos = df[(df.index >= OOS_START) & (df.index <= OOS_END)]
    entries = oos[oos[signal_col]].copy()
    if len(entries) == 0:
        return pd.DataFrame()

    close_arr = df['close'].values
    idx_arr = df.index.values

    results = []
    for entry_idx_pos in range(len(df)):
        if df.index[entry_idx_pos] < OOS_START or df.index[entry_idx_pos] > OOS_END:
            continue
        if not df.iloc[entry_idx_pos][signal_col]:
            continue

        entry_px = close_arr[entry_idx_pos] * (1 + slip * direction)
        exit_idx = min(entry_idx_pos + hold_hours, len(df) - 1)
        exit_px = close_arr[exit_idx] * (1 - slip * direction)
        exit_time = idx_arr[exit_idx]
        hrs = exit_idx - entry_idx_pos

        raw_ret = direction * (exit_px / entry_px - 1)
        results.append({
            'entry_time': idx_arr[entry_idx_pos],
            'exit_time': exit_time,
            'entry_price': entry_px,
            'exit_price': exit_px,
            'raw_ret': raw_ret,
            'hold_hours': hrs,
            'exit_reason': f'fixed_{hold_hours}h',
            'direction': direction,
        })

    return pd.DataFrame(results) if results else pd.DataFrame()


def compute_exits_fixed_fast(df, signal_col, direction, hold_hours, slip):
    """Fully vectorized fixed hold exit computation."""
    oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
    sig_mask = oos_mask & df[signal_col]
    entry_positions = np.where(sig_mask.values)[0]

    if len(entry_positions) == 0:
        return pd.DataFrame()

    close_arr = df['close'].values
    idx_arr = df.index.values
    n = len(df)

    exit_positions = np.minimum(entry_positions + hold_hours, n - 1)
    entry_prices = close_arr[entry_positions] * (1 + slip * direction)
    exit_prices = close_arr[exit_positions] * (1 - slip * direction)
    raw_rets = direction * (exit_prices / entry_prices - 1)
    hold_hrs = exit_positions - entry_positions

    return pd.DataFrame({
        'entry_time': idx_arr[entry_positions],
        'exit_time': idx_arr[exit_positions],
        'entry_price': entry_prices,
        'exit_price': exit_prices,
        'raw_ret': raw_rets,
        'hold_hours': hold_hrs,
        'exit_reason': f'fixed_{hold_hours}h',
        'direction': direction,
    })


def compute_exits_atr_trail_fast(df, signal_col, direction, slip):
    """ATR trail: no stop for 8h, 2x ATR trail after, max 48h."""
    oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
    sig_mask = oos_mask & df[signal_col]
    entry_positions = np.where(sig_mask.values)[0]

    if len(entry_positions) == 0:
        return pd.DataFrame()

    close_arr = df['close'].values
    atr_arr = df['atr_14'].values
    idx_arr = df.index.values
    n = len(df)

    results = []
    for ep in entry_positions:
        entry_px = close_arr[ep] * (1 + slip * direction)
        max_exit = min(ep + 48, n - 1)

        exited = False
        if direction == 1:
            peak = entry_px
            for j in range(ep + 1, max_exit + 1):
                px = close_arr[j]
                if px > peak:
                    peak = px
                hrs = j - ep
                if hrs >= 8:
                    atr = atr_arr[j]
                    if np.isnan(atr) or atr == 0:
                        atr = px * 0.01
                    if px <= peak - 2 * atr:
                        exit_px = px * (1 - slip * direction)
                        results.append((idx_arr[ep], idx_arr[j], entry_px, exit_px,
                                       direction * (exit_px / entry_px - 1), hrs, 'atr_trail_stop'))
                        exited = True
                        break
        else:
            trough = entry_px
            for j in range(ep + 1, max_exit + 1):
                px = close_arr[j]
                if px < trough:
                    trough = px
                hrs = j - ep
                if hrs >= 8:
                    atr = atr_arr[j]
                    if np.isnan(atr) or atr == 0:
                        atr = px * 0.01
                    if px >= trough + 2 * atr:
                        exit_px = px * (1 - slip * direction)
                        results.append((idx_arr[ep], idx_arr[j], entry_px, exit_px,
                                       direction * (exit_px / entry_px - 1), hrs, 'atr_trail_stop'))
                        exited = True
                        break

        if not exited:
            exit_px = close_arr[max_exit] * (1 - slip * direction)
            hrs = max_exit - ep
            results.append((idx_arr[ep], idx_arr[max_exit], entry_px, exit_px,
                           direction * (exit_px / entry_px - 1), hrs, 'max_hold_48h'))

    if not results:
        return pd.DataFrame()

    rdf = pd.DataFrame(results, columns=['entry_time', 'exit_time', 'entry_price', 'exit_price',
                                          'raw_ret', 'hold_hours', 'exit_reason'])
    rdf['direction'] = direction
    return rdf


def compute_exits_signal_fast(df, signal_col, direction, slip):
    """Signal-based exit: long exits when funding_zscore>0, short when amihud_zscore<0."""
    oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
    sig_mask = oos_mask & df[signal_col]
    entry_positions = np.where(sig_mask.values)[0]

    if len(entry_positions) == 0:
        return pd.DataFrame()

    close_arr = df['close'].values
    idx_arr = df.index.values
    n = len(df)

    if direction == 1:
        exit_signal_arr = df['funding_zscore_72h'].values
        exit_cond = lambda v: not np.isnan(v) and v > 0
    else:
        exit_signal_arr = df['amihud_zscore_72h'].values
        exit_cond = lambda v: not np.isnan(v) and v < 0

    max_hold = 168
    results = []

    for ep in entry_positions:
        entry_px = close_arr[ep] * (1 + slip * direction)
        max_exit = min(ep + max_hold, n - 1)

        exited = False
        for j in range(ep + 1, max_exit + 1):
            if exit_cond(exit_signal_arr[j]):
                exit_px = close_arr[j] * (1 - slip * direction)
                hrs = j - ep
                reason = 'funding_normalized' if direction == 1 else 'amihud_normalized'
                results.append((idx_arr[ep], idx_arr[j], entry_px, exit_px,
                               direction * (exit_px / entry_px - 1), hrs, reason))
                exited = True
                break

        if not exited:
            exit_px = close_arr[max_exit] * (1 - slip * direction)
            hrs = max_exit - ep
            results.append((idx_arr[ep], idx_arr[max_exit], entry_px, exit_px,
                           direction * (exit_px / entry_px - 1), hrs, 'max_hold_168h'))

    if not results:
        return pd.DataFrame()

    rdf = pd.DataFrame(results, columns=['entry_time', 'exit_time', 'entry_price', 'exit_price',
                                          'raw_ret', 'hold_hours', 'exit_reason'])
    rdf['direction'] = direction
    return rdf


# ── Portfolio simulation with position limits ──────────────────────────
def simulate_portfolio(all_potential_trades, leverage):
    """
    Given a DataFrame of potential trades (entry_time, exit_time, raw_ret, direction, token),
    simulate portfolio with position limits, sequential equity allocation, drawdown tracking.
    """
    if len(all_potential_trades) == 0:
        return [], INITIAL_CAPITAL, 0.0

    slip = SLIPPAGE_BPS / 10_000
    df = all_potential_trades.sort_values('entry_time').reset_index(drop=True)

    equity = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL
    max_dd = 0.0
    trades = []

    # active: list of (exit_time, token, direction, raw_ret, entry_time, margin, entry_price, exit_price, hold_hours, exit_reason)
    active = []

    for _, row in df.iterrows():
        entry_time = row['entry_time']

        # Close expired positions
        still_active = []
        for pos in active:
            if entry_time >= pos[0]:
                margin = pos[5]
                lev_ret = pos[3] * leverage
                pnl_usd = margin * lev_ret
                equity += pnl_usd
                trades.append({
                    'token': pos[1], 'direction': pos[2],
                    'entry_time': pos[4], 'exit_time': pos[0],
                    'entry_price': pos[6], 'exit_price': pos[7],
                    'pnl_pct': lev_ret * 100, 'pnl_usd': pnl_usd,
                    'leverage': leverage, 'hold_hours': pos[8],
                    'exit_reason': pos[9],
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
                    row['entry_price'], row['exit_price'],
                    row['hold_hours'], row['exit_reason'],
                ))

    # Close remaining
    for pos in active:
        margin = pos[5]
        lev_ret = pos[3] * leverage
        pnl_usd = margin * lev_ret
        equity += pnl_usd
        trades.append({
            'token': pos[1], 'direction': pos[2],
            'entry_time': pos[4], 'exit_time': pos[0],
            'entry_price': pos[6], 'exit_price': pos[7],
            'pnl_pct': lev_ret * 100, 'pnl_usd': pnl_usd,
            'leverage': leverage, 'hold_hours': pos[8],
            'exit_reason': pos[9],
        })
    if equity > peak_equity:
        peak_equity = equity
    dd_now = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
    if dd_now > max_dd:
        max_dd = dd_now

    return trades, equity, max_dd


def build_potential_trades(token_data, exit_mode, hold_hours):
    """Build all potential trades across all tokens for a given exit mode."""
    slip = SLIPPAGE_BPS / 10_000
    all_trades = []

    for token, df in token_data.items():
        # Long trades
        if exit_mode == 'fixed':
            lt = compute_exits_fixed_fast(df, 'signal_long', 1, hold_hours, slip)
        elif exit_mode == 'atr_trail':
            lt = compute_exits_atr_trail_fast(df, 'signal_long', 1, slip)
        elif exit_mode == 'signal':
            lt = compute_exits_signal_fast(df, 'signal_long', 1, slip)

        if len(lt) > 0:
            lt['token'] = token
            all_trades.append(lt)

        # Short trades
        if exit_mode == 'fixed':
            st = compute_exits_fixed_fast(df, 'signal_short', -1, hold_hours, slip)
        elif exit_mode == 'atr_trail':
            st = compute_exits_atr_trail_fast(df, 'signal_short', -1, slip)
        elif exit_mode == 'signal':
            st = compute_exits_signal_fast(df, 'signal_short', -1, slip)

        if len(st) > 0:
            st['token'] = token
            all_trades.append(st)

    if not all_trades:
        return pd.DataFrame()
    return pd.concat(all_trades, ignore_index=True)


# ── Reporting ───────────────────────────────────────────────────────────
def report_results(trades_list, final_equity, max_dd, config_label):
    if not trades_list:
        print(f'\n  {config_label}: NO TRADES')
        return {}

    df = pd.DataFrame(trades_list)
    total_pnl = final_equity - INITIAL_CAPITAL
    total_ret = (final_equity / INITIAL_CAPITAL - 1) * 100
    n_trades = len(df)
    n_long = (df['direction'] == 1).sum()
    n_short = (df['direction'] == -1).sum()

    win_rate = (df['pnl_usd'] > 0).mean() * 100
    long_wr = (df.loc[df['direction'] == 1, 'pnl_usd'] > 0).mean() * 100 if n_long > 0 else 0
    short_wr = (df.loc[df['direction'] == -1, 'pnl_usd'] > 0).mean() * 100 if n_short > 0 else 0

    avg_pnl_pct = df['pnl_pct'].mean()
    avg_win = df.loc[df['pnl_usd'] > 0, 'pnl_pct'].mean() if (df['pnl_usd'] > 0).any() else 0
    avg_loss = df.loc[df['pnl_usd'] <= 0, 'pnl_pct'].mean() if (df['pnl_usd'] <= 0).any() else 0

    gross_wins = df.loc[df['pnl_usd'] > 0, 'pnl_usd'].sum()
    gross_losses = abs(df.loc[df['pnl_usd'] <= 0, 'pnl_usd'].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    oos_days = (OOS_END - OOS_START).days
    annual_factor = 365.25 / oos_days if oos_days > 0 else 1
    annual_ret = total_ret * annual_factor

    avg_hold = df['hold_hours'].mean()

    print(f'\n  {"="*65}')
    print(f'  {config_label}')
    print(f'  {"="*65}')
    print(f'  Trades: {n_trades} (L:{n_long} / S:{n_short})')
    print(f'  Final equity: ${final_equity:,.0f} (PnL: ${total_pnl:+,.0f})')
    print(f'  Total return:  {total_ret:+.1f}%')
    print(f'  Annual return: {annual_ret:+.1f}%')
    print(f'  Max drawdown:  {max_dd*100:.1f}%')
    print(f'  Win rate:      {win_rate:.1f}% (L:{long_wr:.1f}% / S:{short_wr:.1f}%)')
    print(f'  Avg PnL/trade: {avg_pnl_pct:+.2f}%')
    print(f'  Avg win:       {avg_win:+.2f}% | Avg loss: {avg_loss:+.2f}%')
    print(f'  Profit factor: {pf:.2f}')
    print(f'  Avg hold:      {avg_hold:.1f}h')

    df['month'] = pd.to_datetime(df['entry_time']).dt.to_period('M')
    print(f'\n  Per-Month Breakdown:')
    print(f'  {"Month":>10s}  {"Trades":>6s}  {"L":>3s}  {"S":>3s}  {"WinR":>6s}  {"PnL":>12s}  {"AvgRet":>8s}')
    for month, mdf in df.groupby('month'):
        mn = len(mdf)
        ml = (mdf['direction'] == 1).sum()
        ms = (mdf['direction'] == -1).sum()
        mwr = (mdf['pnl_usd'] > 0).mean() * 100
        mpnl = mdf['pnl_usd'].sum()
        mavg = mdf['pnl_pct'].mean()
        print(f'  {str(month):>10s}  {mn:6d}  {ml:3d}  {ms:3d}  {mwr:5.1f}%  ${mpnl:>+11,.0f}  {mavg:>+7.2f}%')

    print(f'\n  Top Tokens by PnL:')
    token_pnl = df.groupby('token').agg(
        trades=('pnl_usd', 'count'),
        total_pnl=('pnl_usd', 'sum'),
        avg_ret=('pnl_pct', 'mean'),
        win_rate=('pnl_usd', lambda x: (x > 0).mean() * 100),
    ).sort_values('total_pnl', ascending=False)
    print(f'  {"Token":>8s}  {"Trades":>6s}  {"PnL":>12s}  {"AvgRet":>8s}  {"WinR":>6s}')
    for token, row in token_pnl.head(15).iterrows():
        print(f'  {token:>8s}  {row["trades"]:6.0f}  ${row["total_pnl"]:>+11,.0f}  {row["avg_ret"]:>+7.2f}%  {row["win_rate"]:5.1f}%')

    # Worst tokens
    print(f'\n  Bottom Tokens by PnL:')
    for token, row in token_pnl.tail(10).iterrows():
        print(f'  {token:>8s}  {row["trades"]:6.0f}  ${row["total_pnl"]:>+11,.0f}  {row["avg_ret"]:>+7.2f}%  {row["win_rate"]:5.1f}%')

    print(f'\n  Exit Reasons:')
    for reason, rdf in df.groupby('exit_reason'):
        print(f'    {reason}: {len(rdf)} trades, avg ret {rdf["pnl_pct"].mean():+.2f}%')

    return {
        'config': config_label,
        'trades': n_trades,
        'n_long': n_long,
        'n_short': n_short,
        'final_equity': final_equity,
        'total_return_pct': total_ret,
        'annual_return_pct': annual_ret,
        'max_dd_pct': max_dd * 100,
        'win_rate': win_rate,
        'profit_factor': pf,
        'avg_pnl_pct': avg_pnl_pct,
        'avg_hold_hours': avg_hold,
    }


def print_signal_stats(token_data):
    total_long = 0
    total_short = 0
    total_hours = 0
    long_by_token = {}
    short_by_token = {}

    for token, df in token_data.items():
        oos = df[(df.index >= OOS_START) & (df.index <= OOS_END)]
        n_long = oos['signal_long'].sum()
        n_short = oos['signal_short'].sum()
        total_long += n_long
        total_short += n_short
        total_hours += len(oos)
        if n_long > 0:
            long_by_token[token] = int(n_long)
        if n_short > 0:
            short_by_token[token] = int(n_short)

    print(f'\n  Signal Statistics (OOS: {OOS_START.date()} to {OOS_END.date()})')
    print(f'  Tokens analyzed: {len(token_data)}')
    print(f'  Total hourly bars: {total_hours:,}')
    print(f'  LONG signals:  {total_long:,} ({total_long/max(total_hours,1)*100:.2f}% of bars)')
    print(f'  SHORT signals: {total_short:,} ({total_short/max(total_hours,1)*100:.2f}% of bars)')
    print(f'  Unique tokens with LONG signals:  {len(long_by_token)}')
    print(f'  Unique tokens with SHORT signals: {len(short_by_token)}')

    print(f'\n  Top 10 LONG signal tokens:')
    for token, count in sorted(long_by_token.items(), key=lambda x: -x[1])[:10]:
        print(f'    {token:>12s}: {count} signals')

    print(f'\n  Top 10 SHORT signal tokens:')
    for token, count in sorted(short_by_token.items(), key=lambda x: -x[1])[:10]:
        print(f'    {token:>12s}: {count} signals')


def main():
    print('='*70)
    print('FUNDING RATE REVERSAL — BIDIRECTIONAL STRATEGY BACKTEST')
    print('='*70)
    print(f'Capital: ${INITIAL_CAPITAL:,.0f} | Slippage: {SLIPPAGE_BPS}bps | Max positions: {MAX_POSITIONS}')
    print(f'OOS period: {OOS_START.date()} to {OOS_END.date()}')

    print('\n-- Loading and preparing data --')
    token_data = load_and_prepare_all()
    print(f'  Loaded {len(token_data)} tokens with sufficient data')

    print('\n-- Signal Analysis --')
    print_signal_stats(token_data)

    # Precompute potential trades for each exit mode (reused across leverages)
    print('\n-- Precomputing trade outcomes --')

    exit_configs = {
        'fixed_8':    ('fixed', 8),
        'fixed_24':   ('fixed', 24),
        'fixed_48':   ('fixed', 48),
        'atr_trail':  ('atr_trail', 48),
        'signal':     ('signal', 168),
    }

    potential_by_config = {}
    for key, (mode, hours) in exit_configs.items():
        print(f'  Computing {key}...')
        pt = build_potential_trades(token_data, mode, hours)
        potential_by_config[key] = pt
        print(f'    {len(pt)} potential trades')

    # Run portfolio simulation for each config x leverage
    configs_display = [
        ('fixed_8', 'Exit-A: Fixed 8h'),
        ('fixed_24', 'Exit-A: Fixed 24h'),
        ('fixed_48', 'Exit-A: Fixed 48h'),
        ('atr_trail', 'Exit-B: 2xATR trail (no-stop 8h, max 48h)'),
        ('signal', 'Exit-C: Signal-based'),
    ]
    leverages = [1, 3, 5, 7]
    all_results = []

    print('\n' + '='*70)
    print('BACKTEST RESULTS')
    print('='*70)

    for lev in leverages:
        print(f'\n{"#"*70}')
        print(f'# LEVERAGE: {lev}x')
        print(f'{"#"*70}')

        for config_key, label in configs_display:
            config_label = f'{label} | {lev}x leverage'
            pt = potential_by_config[config_key]
            trades, final_eq, max_dd = simulate_portfolio(pt, lev)
            result = report_results(trades, final_eq, max_dd, config_label)
            if result:
                all_results.append(result)

    # Summary
    print('\n' + '='*70)
    print('SUMMARY — ALL CONFIGURATIONS')
    print('='*70)

    if all_results:
        summary = pd.DataFrame(all_results)
        print(f'\n{"Config":<50s}  {"Trades":>6s}  {"Return":>8s}  {"Annual":>8s}  {"MaxDD":>7s}  {"WinR":>6s}  {"PF":>6s}')
        print('-' * 100)
        for _, row in summary.iterrows():
            print(
                f'{row["config"]:<50s}  '
                f'{row["trades"]:6.0f}  '
                f'{row["total_return_pct"]:>+7.1f}%  '
                f'{row["annual_return_pct"]:>+7.1f}%  '
                f'{row["max_dd_pct"]:>6.1f}%  '
                f'{row["win_rate"]:>5.1f}%  '
                f'{row["profit_factor"]:>5.2f}'
            )

        print(f'\n{"="*70}')
        print('BEST CONFIGURATIONS (by annual return)')
        print('='*70)
        best = summary.sort_values('annual_return_pct', ascending=False).head(5)
        for _, row in best.iterrows():
            meets_target = row['annual_return_pct'] >= 300 and row['max_dd_pct'] <= 20
            flag = ' *** TARGET MET ***' if meets_target else ''
            print(
                f'  {row["config"]:<50s}  '
                f'Ann: {row["annual_return_pct"]:>+7.1f}%  '
                f'DD: {row["max_dd_pct"]:>5.1f}%  '
                f'WR: {row["win_rate"]:>5.1f}%  '
                f'PF: {row["profit_factor"]:>5.2f}'
                f'{flag}'
            )

        print(f'\n{"="*70}')
        print('TARGET CHECK: 300%+ annual, <20% DD')
        print('='*70)
        meets = summary[(summary['annual_return_pct'] >= 300) & (summary['max_dd_pct'] <= 20)]
        if len(meets) > 0:
            print(f'  {len(meets)} configuration(s) meet the target:')
            for _, row in meets.iterrows():
                print(f'    {row["config"]}: {row["annual_return_pct"]:+.1f}% annual, {row["max_dd_pct"]:.1f}% DD')
        else:
            print('  No configurations meet both targets.')
            summary['score'] = summary['annual_return_pct'] - summary['max_dd_pct'] * 10
            closest = summary.sort_values('score', ascending=False).head(3)
            print('  Closest configs:')
            for _, row in closest.iterrows():
                print(f'    {row["config"]}: {row["annual_return_pct"]:+.1f}% annual, {row["max_dd_pct"]:.1f}% DD')

    print('\n' + '='*70)
    print('BACKTEST COMPLETE')
    print('='*70)


if __name__ == '__main__':
    main()
