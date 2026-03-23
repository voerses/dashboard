#!/usr/bin/env python3
"""
OI Divergence Strategy -- Full Portfolio Backtest
=================================================
Uses Bybit hourly OI data aligned with Binance perp price data.

LONG signals:
  - Accumulation: OI rising + price flat/falling (oi_chg_24h > 5%, price_chg_24h < 0%)

SHORT signals:
  - Euphoria: OI rising + price rising sharply (oi_chg_24h > 10%, price_chg_24h > 5%)

Filters tested: OI z-score > 2.0, OI acceleration
Exit modes: fixed 24h hold, ATR trailing stop, signal-based exit
Leverage: sweep 1x, 3x, 5x, 7x
Capital: $200K, slippage 10bps, max 5 positions

Data: Bybit OI (Jan 2024 -- Mar 2026) + Binance perp 1h price (14 tokens, ~27 months)
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from typing import Optional

warnings.filterwarnings('ignore')

# -- Paths --
PROJECT_DIR = '/workspace/crypto_backtest'
OI_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', 'bybit_oi')
PRICE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

TOKENS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE',
    'ADA', 'AVAX', 'LINK', 'DOT',
    'ARB', 'OP', 'SUI', 'APT', 'NEAR',
]

SLIPPAGE_BPS = 10
CAPITAL = 200_000
MAX_POSITIONS = 5


# -- Data Loading --
def load_token_data(token: str) -> Optional[pd.DataFrame]:
    oi_path = os.path.join(OI_DIR, f'{token}_oi_1h.csv')
    price_path = os.path.join(PRICE_DIR, f'{token}_1h.parquet')
    if not os.path.exists(oi_path) or not os.path.exists(price_path):
        return None

    oi_df = pd.read_csv(oi_path)
    oi_df['datetime'] = pd.to_datetime(oi_df['datetime'], utc=True)
    oi_df = oi_df.set_index('datetime')[['open_interest']].sort_index()
    oi_df = oi_df[~oi_df.index.duplicated(keep='first')]

    price_df = pd.read_parquet(price_path)
    if price_df.index.tz is None:
        price_df.index = price_df.index.tz_localize('UTC')
    price_df = price_df[['open', 'high', 'low', 'close', 'volume']].sort_index()
    price_df = price_df[~price_df.index.duplicated(keep='first')]

    merged = oi_df.join(price_df, how='inner').dropna()
    if len(merged) < 200:
        return None
    merged['token'] = token
    return merged


def load_all_data() -> pd.DataFrame:
    frames = []
    for token in TOKENS:
        df = load_token_data(token)
        if df is not None:
            frames.append(df)
            print(f'  [{token}] {len(df):,} rows | '
                  f'{df.index.min().strftime("%Y-%m-%d")} to {df.index.max().strftime("%Y-%m-%d")}')
    if not frames:
        print('ERROR: No data loaded!')
        sys.exit(1)
    all_data = pd.concat(frames)
    print(f'\n  Total: {len(all_data):,} rows across {len(frames)} tokens')
    return all_data


# -- Feature Engineering --
def compute_features(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Compute features per token. Returns dict of token -> DataFrame."""
    token_frames = {}
    for token, gdf in df.groupby('token'):
        g = gdf.sort_index().copy()
        g['oi_chg_4h'] = g['open_interest'].pct_change(4)
        g['oi_chg_24h'] = g['open_interest'].pct_change(24)
        g['oi_chg_1h'] = g['open_interest'].pct_change(1)
        g['price_chg_4h'] = g['close'].pct_change(4)
        g['price_chg_24h'] = g['close'].pct_change(24)

        oi_chg_1h = g['oi_chg_1h']
        roll_mean = oi_chg_1h.rolling(168, min_periods=48).mean()
        roll_std = oi_chg_1h.rolling(168, min_periods=48).std()
        g['oi_zscore'] = (oi_chg_1h - roll_mean) / roll_std.replace(0, np.nan)
        g['oi_accel'] = g['oi_chg_4h'] - (g['oi_chg_24h'] / 6)

        tr = pd.concat([
            g['high'] - g['low'],
            (g['high'] - g['close'].shift(1)).abs(),
            (g['low'] - g['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        g['atr_14'] = tr.rolling(14).mean()

        # Pre-compute signal components
        g['long_base'] = (g['oi_chg_24h'] > 0.05) & (g['price_chg_24h'] < 0.0)
        g['short_base'] = (g['oi_chg_24h'] > 0.10) & (g['price_chg_24h'] > 0.05)
        g['zscore_ok'] = g['oi_zscore'] > 2.0
        g['accel_ok'] = g['oi_accel'] > 0

        token_frames[token] = g
    return token_frames


# -- Portfolio-level event-driven backtest --
def run_backtest(
    token_frames: dict[str, pd.DataFrame],
    leverage: float = 3.0,
    exit_mode: str = 'fixed_24h',
    atr_trail_mult: float = 2.0,
    max_hold_hours: int = 48,
    use_zscore: bool = False,
    use_accel: bool = False,
    capital: float = CAPITAL,
    max_positions: int = MAX_POSITIONS,
    slippage_bps: float = SLIPPAGE_BPS,
) -> dict:
    """Event-driven portfolio backtest with proper capital management."""
    slip_factor = slippage_bps / 10_000

    # Build unified hourly timeline from the first token
    all_times = sorted(set().union(*(set(tf.index) for tf in token_frames.values())))
    all_times = pd.DatetimeIndex(all_times).sort_values()

    # Pre-build signal and price dicts for fast lookup
    # For each token: arrays of close, high, low, atr, long_signal, short_signal
    token_arrays = {}
    for token, tdf in token_frames.items():
        long_mask = tdf['long_base'].copy()
        short_mask = tdf['short_base'].copy()
        if use_zscore:
            long_mask = long_mask & tdf['zscore_ok']
            short_mask = short_mask & tdf['zscore_ok']
        if use_accel:
            long_mask = long_mask & tdf['accel_ok']
            short_mask = short_mask & tdf['accel_ok']

        token_arrays[token] = {
            'close': tdf['close'],
            'high': tdf['high'],
            'low': tdf['low'],
            'atr': tdf['atr_14'],
            'long_sig': long_mask,
            'short_sig': short_mask,
            'oi_chg': tdf['oi_chg_24h'].abs(),
        }

    # Portfolio state
    cash = capital
    positions = []  # list of dicts: {token, direction, entry_price, entry_time, size_usd, leverage, atr, best_price}
    trades = []
    equity_snapshots = []  # (time, equity)

    # Sample equity every 4 hours to keep it manageable
    sample_interval = 4
    bar_count = 0

    for t in all_times:
        # -- 1. Process exits --
        new_positions = []
        for pos in positions:
            token = pos['token']
            ta = token_arrays[token]
            if t not in ta['close'].index:
                new_positions.append(pos)
                continue

            current_close = ta['close'].loc[t]
            current_high = ta['high'].loc[t]
            current_low = ta['low'].loc[t]
            hours_held = (t - pos['entry_time']).total_seconds() / 3600

            exit_reason = None

            if exit_mode == 'fixed_24h':
                if hours_held >= 24:
                    exit_reason = 'fixed_24h'

            elif exit_mode == 'atr_trail':
                d = pos['direction']
                if d == 1:
                    if current_high > pos['best_price']:
                        pos['best_price'] = current_high
                    stop = pos['best_price'] - atr_trail_mult * pos['atr']
                    if current_low <= stop:
                        exit_reason = 'atr_trail'
                else:
                    if current_low < pos['best_price']:
                        pos['best_price'] = current_low
                    stop = pos['best_price'] + atr_trail_mult * pos['atr']
                    if current_high >= stop:
                        exit_reason = 'atr_trail'
                if hours_held >= max_hold_hours:
                    exit_reason = 'max_hold'

            elif exit_mode == 'signal_exit':
                if pos['direction'] == 1 and t in ta['short_sig'].index and ta['short_sig'].loc[t]:
                    exit_reason = 'signal_reversal'
                elif pos['direction'] == -1 and t in ta['long_sig'].index and ta['long_sig'].loc[t]:
                    exit_reason = 'signal_reversal'
                if hours_held >= max_hold_hours:
                    exit_reason = 'max_hold'

            # Liquidation check: if unrealized loss exceeds margin (size_usd)
            raw_ret = (current_close / pos['entry_price'] - 1) * pos['direction']
            unrealized_pnl = raw_ret * pos['size_usd'] * leverage
            if unrealized_pnl <= -pos['size_usd'] * 0.90:  # 90% of margin lost
                exit_reason = 'liquidation'

            if exit_reason:
                exit_price = current_close * (1 - slip_factor * pos['direction'])
                raw_ret = (exit_price / pos['entry_price'] - 1) * pos['direction']
                pnl = raw_ret * pos['size_usd'] * leverage
                # Cap loss at margin (can't lose more than your margin)
                pnl = max(pnl, -pos['size_usd'])
                cash += pos['size_usd'] + pnl
                trades.append({
                    'token': token,
                    'direction': pos['direction'],
                    'entry_price': pos['entry_price'],
                    'exit_price': exit_price,
                    'entry_time': pos['entry_time'],
                    'exit_time': t,
                    'size_usd': pos['size_usd'],
                    'leverage': leverage,
                    'pnl': pnl,
                    'raw_ret': raw_ret,
                    'exit_reason': exit_reason,
                })
            else:
                new_positions.append(pos)

        positions = new_positions

        # -- 2. Process entries --
        if len(positions) < max_positions and cash > 5000:
            candidates = []
            for token in TOKENS:
                if token not in token_arrays:
                    continue
                ta = token_arrays[token]
                if t not in ta['close'].index:
                    continue
                # Skip if already holding this token
                if any(p['token'] == token for p in positions):
                    continue

                atr_val = ta['atr'].loc[t]
                if pd.isna(atr_val) or atr_val <= 0:
                    continue

                if ta['long_sig'].loc[t]:
                    oi_str = ta['oi_chg'].loc[t] if t in ta['oi_chg'].index else 0
                    candidates.append((token, 1, ta['close'].loc[t], atr_val, oi_str))
                elif ta['short_sig'].loc[t]:
                    oi_str = ta['oi_chg'].loc[t] if t in ta['oi_chg'].index else 0
                    candidates.append((token, -1, ta['close'].loc[t], atr_val, oi_str))

            # Sort by OI divergence strength
            candidates.sort(key=lambda x: x[4], reverse=True)

            # Compute current equity for sizing
            total_equity = cash
            for pos in positions:
                token = pos['token']
                ta = token_arrays[token]
                if t in ta['close'].index:
                    cp = ta['close'].loc[t]
                else:
                    cp = pos['entry_price']
                raw_r = (cp / pos['entry_price'] - 1) * pos['direction']
                urpnl = raw_r * pos['size_usd'] * leverage
                urpnl = max(urpnl, -pos['size_usd'])
                total_equity += pos['size_usd'] + urpnl

            for token, direction, price, atr_val, _ in candidates:
                if len(positions) >= max_positions:
                    break

                # Size: fraction of equity per slot
                size_usd = min(
                    cash * 0.90,
                    total_equity / max_positions,
                    capital * 0.30,  # max 30% of initial capital per slot
                )
                if size_usd < 2000:
                    continue

                entry_price = price * (1 + slip_factor * direction)
                cash -= size_usd

                positions.append({
                    'token': token,
                    'direction': direction,
                    'entry_price': entry_price,
                    'entry_time': t,
                    'size_usd': size_usd,
                    'atr': atr_val,
                    'best_price': entry_price,
                })

        # -- 3. Record equity snapshot --
        bar_count += 1
        if bar_count % sample_interval == 0:
            total_equity = cash
            for pos in positions:
                token = pos['token']
                ta = token_arrays[token]
                if t in ta['close'].index:
                    cp = ta['close'].loc[t]
                else:
                    cp = pos['entry_price']
                raw_r = (cp / pos['entry_price'] - 1) * pos['direction']
                urpnl = raw_r * pos['size_usd'] * leverage
                urpnl = max(urpnl, -pos['size_usd'])
                total_equity += pos['size_usd'] + urpnl
            equity_snapshots.append((t, total_equity))

    # Close remaining positions
    for pos in positions:
        token = pos['token']
        ta = token_arrays[token]
        last_price = ta['close'].iloc[-1]
        exit_price = last_price * (1 - slip_factor * pos['direction'])
        raw_ret = (exit_price / pos['entry_price'] - 1) * pos['direction']
        pnl = raw_ret * pos['size_usd'] * leverage
        pnl = max(pnl, -pos['size_usd'])
        cash += pos['size_usd'] + pnl
        trades.append({
            'token': token,
            'direction': pos['direction'],
            'entry_price': pos['entry_price'],
            'exit_price': exit_price,
            'entry_time': pos['entry_time'],
            'exit_time': all_times[-1],
            'size_usd': pos['size_usd'],
            'leverage': leverage,
            'pnl': pnl,
            'raw_ret': raw_ret,
            'exit_reason': 'eod_close',
        })

    return {
        'trades': trades,
        'equity_curve': equity_snapshots,
        'capital': capital,
        'leverage': leverage,
        'exit_mode': exit_mode,
    }


# -- Metrics --
def compute_metrics(result: dict) -> dict:
    trades = result['trades']
    eq = result['equity_curve']
    capital = result['capital']

    default = {'total_return_pct': 0, 'annual_return_pct': 0, 'max_dd_pct': 0,
               'sharpe': 0, 'num_trades': 0, 'win_rate': 0, 'profit_factor': 0,
               'avg_hold_hours': 0, 'long_trades': 0, 'short_trades': 0,
               'long_pnl': 0, 'short_pnl': 0, 'n_days': 0, 'monthly': {},
               'final_equity': capital, 'avg_win': 0, 'avg_loss': 0}

    if not eq or not trades:
        return default

    eq_df = pd.DataFrame(eq, columns=['time', 'equity']).set_index('time')
    eq_daily = eq_df['equity'].resample('D').last().ffill().dropna()

    if len(eq_daily) < 2:
        return default

    final_equity = eq_daily.iloc[-1]
    total_return = (final_equity / capital - 1) * 100
    n_days = len(eq_daily)
    n_years = n_days / 365.25

    if n_years > 0 and final_equity > 0:
        annual_return = ((final_equity / capital) ** (1 / n_years) - 1) * 100
    else:
        annual_return = total_return / max(n_years, 0.1)

    daily_rets = eq_daily.pct_change().dropna()
    # Filter out extreme daily returns from equity going near zero
    daily_rets = daily_rets.replace([np.inf, -np.inf], np.nan).dropna()

    if len(daily_rets) > 1 and daily_rets.std() > 0:
        sharpe = (daily_rets.mean() / daily_rets.std()) * np.sqrt(365.25)
    else:
        sharpe = 0

    running_max = eq_daily.cummax()
    drawdowns = (eq_daily - running_max) / running_max
    max_dd = drawdowns.min() * 100

    pnls = [t['pnl'] for t in trades]
    num_trades = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / num_trades * 100 if num_trades > 0 else 0
    avg_win = np.mean([p for p in pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([abs(p) for p in pnls if p <= 0]) if (num_trades - wins) > 0 else 0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p < 0)
    profit_factor = gross_profit / max(gross_loss, 1)
    avg_hold_hours = np.mean([
        (t['exit_time'] - t['entry_time']).total_seconds() / 3600 for t in trades
    ])
    long_trades = sum(1 for t in trades if t['direction'] == 1)
    short_trades = sum(1 for t in trades if t['direction'] == -1)
    long_pnl = sum(t['pnl'] for t in trades if t['direction'] == 1)
    short_pnl = sum(t['pnl'] for t in trades if t['direction'] == -1)

    monthly = {}
    for period, grp in eq_daily.groupby(eq_daily.index.to_period('M')):
        if len(grp) < 2:
            continue
        pct = (grp.iloc[-1] / grp.iloc[0] - 1) * 100
        dd = ((grp - grp.cummax()) / grp.cummax()).min() * 100
        monthly[str(period)] = {'return_pct': pct, 'dd_pct': dd}

    return {
        'total_return_pct': total_return,
        'annual_return_pct': annual_return,
        'final_equity': final_equity,
        'max_dd_pct': max_dd,
        'sharpe': sharpe,
        'num_trades': num_trades,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'avg_hold_hours': avg_hold_hours,
        'long_trades': long_trades,
        'short_trades': short_trades,
        'long_pnl': long_pnl,
        'short_pnl': short_pnl,
        'n_days': n_days,
        'monthly': monthly,
    }


# -- Main --
def main():
    print('=' * 78)
    print('  OI DIVERGENCE STRATEGY -- FULL PORTFOLIO BACKTEST')
    print('=' * 78)

    print('\n[1] Loading data...')
    df = load_all_data()
    token_frames = compute_features(df)

    # Signal counts preview
    total_long = sum(int(tf['long_base'].sum()) for tf in token_frames.values())
    total_short = sum(int(tf['short_base'].sum()) for tf in token_frames.values())
    total_long_z = sum(int((tf['long_base'] & tf['zscore_ok']).sum()) for tf in token_frames.values())
    total_short_z = sum(int((tf['short_base'] & tf['zscore_ok']).sum()) for tf in token_frames.values())
    total_long_a = sum(int((tf['long_base'] & tf['accel_ok']).sum()) for tf in token_frames.values())
    total_short_a = sum(int((tf['short_base'] & tf['accel_ok']).sum()) for tf in token_frames.values())

    print(f'\n  Signal counts (no filters):       LONG={total_long:,}  SHORT={total_short:,}')
    print(f'  Signal counts (z-score > 2.0):     LONG={total_long_z:,}  SHORT={total_short_z:,}')
    print(f'  Signal counts (accel filter):      LONG={total_long_a:,}  SHORT={total_short_a:,}')

    # -- Sweep --
    exit_modes = [
        ('fixed_24h', 'fixed_24h', 2.0, 48),
        ('atr_trail_2x', 'atr_trail', 2.0, 72),
        ('atr_trail_3x', 'atr_trail', 3.0, 72),
        ('signal_exit', 'signal_exit', 2.0, 72),
    ]
    leverages = [1, 3, 5, 7]
    filter_combos = [
        ('no_filter', False, False),
        ('zscore', True, False),
        ('accel', False, True),
        ('both', True, True),
    ]

    configs = []
    for lev in leverages:
        for exit_name, exit_mode, atr_mult, max_hold in exit_modes:
            for filt_name, use_z, use_a in filter_combos:
                label = f'{lev}x_{exit_name}_{filt_name}'
                configs.append((label, {
                    'leverage': lev,
                    'exit_mode': exit_mode,
                    'atr_trail_mult': atr_mult,
                    'max_hold_hours': max_hold,
                    'use_zscore': use_z,
                    'use_accel': use_a,
                }))

    print(f'\n[2] Running {len(configs)} configurations...')
    all_metrics = []

    for i, (label, kwargs) in enumerate(configs):
        result = run_backtest(token_frames, **kwargs)
        m = compute_metrics(result)
        m['label'] = label
        m['leverage'] = kwargs['leverage']
        m['exit_mode_name'] = kwargs['exit_mode']
        m['zscore_filter'] = kwargs.get('use_zscore', False)
        m['accel_filter'] = kwargs.get('use_accel', False)
        all_metrics.append(m)
        if (i + 1) % 16 == 0:
            print(f'  ... {i+1}/{len(configs)} done')

    print(f'  ... {len(configs)}/{len(configs)} done')

    results_df = pd.DataFrame(all_metrics)
    results_df = results_df.sort_values('sharpe', ascending=False)

    # -- SWEEP RESULTS --
    print('\n' + '=' * 78)
    print('  SWEEP RESULTS (sorted by Sharpe, top 30)')
    print('=' * 78)
    print(f'  {"Config":<35s} {"Return":>8s} {"Annual":>8s} {"MaxDD":>8s} '
          f'{"Sharpe":>7s} {"Trades":>7s} {"WinR":>6s} {"PF":>6s}')
    print(f'  {"-"*35} {"-"*8} {"-"*8} {"-"*8} {"-"*7} {"-"*7} {"-"*6} {"-"*6}')

    for _, row in results_df.head(30).iterrows():
        print(f'  {row["label"]:<35s} '
              f'{row["total_return_pct"]:>+7.1f}% '
              f'{row["annual_return_pct"]:>+7.1f}% '
              f'{row["max_dd_pct"]:>+7.1f}% '
              f'{row["sharpe"]:>7.2f} '
              f'{row["num_trades"]:>7d} '
              f'{row["win_rate"]:>5.1f}% '
              f'{row["profit_factor"]:>6.2f}')

    # -- TOP 5 DETAILED --
    print('\n' + '=' * 78)
    print('  TOP 5 CONFIGURATIONS -- DETAILED')
    print('=' * 78)

    top5_labels = results_df.head(5)['label'].tolist()
    for rank, label in enumerate(top5_labels, 1):
        cfg_idx = next(i for i, (l, _) in enumerate(configs) if l == label)
        result = run_backtest(token_frames, **configs[cfg_idx][1])
        m = compute_metrics(result)

        print(f'\n  --- #{rank}: {label} ---')
        print(f'  Total Return:     {m["total_return_pct"]:>+8.1f}%')
        print(f'  Annual Return:    {m["annual_return_pct"]:>+8.1f}%')
        print(f'  Final Equity:     ${m["final_equity"]:>12,.0f}')
        print(f'  Max Drawdown:     {m["max_dd_pct"]:>+8.1f}%')
        print(f'  Sharpe Ratio:     {m["sharpe"]:>8.2f}')
        print(f'  Trades:           {m["num_trades"]:>8d}')
        print(f'  Win Rate:         {m["win_rate"]:>8.1f}%')
        print(f'  Profit Factor:    {m["profit_factor"]:>8.2f}')
        print(f'  Avg Hold (hours): {m["avg_hold_hours"]:>8.1f}')
        print(f'  Long:  {m["long_trades"]:>5d} trades, PnL ${m["long_pnl"]:>+10,.0f}')
        print(f'  Short: {m["short_trades"]:>5d} trades, PnL ${m["short_pnl"]:>+10,.0f}')

        if m['monthly']:
            print(f'\n  Monthly:')
            print(f'    {"Month":>10s}  {"Return":>8s}  {"MaxDD":>8s}')
            for month in sorted(m['monthly'].keys()):
                vals = m['monthly'][month]
                print(f'    {month:>10s}  {vals["return_pct"]:>+7.1f}%  {vals["dd_pct"]:>+7.1f}%')

        # Per-token PnL
        token_pnl = {}
        token_count = {}
        for t in result['trades']:
            token_pnl[t['token']] = token_pnl.get(t['token'], 0) + t['pnl']
            token_count[t['token']] = token_count.get(t['token'], 0) + 1

        if token_pnl:
            print(f'\n  Per-token:')
            print(f'    {"Token":>8s}  {"N":>5s}  {"PnL":>12s}')
            for tk in sorted(token_pnl.keys(), key=lambda x: token_pnl[x], reverse=True):
                print(f'    {tk:>8s}  {token_count[tk]:>5d}  ${token_pnl[tk]:>+11,.0f}')

    # -- LEVERAGE COMPARISON --
    print('\n' + '=' * 78)
    print('  LEVERAGE COMPARISON (fixed_24h, no_filter)')
    print('=' * 78)
    lev_rows = results_df[
        results_df['label'].str.contains('fixed_24h_no_filter')
    ].sort_values('leverage')
    print(f'  {"Lev":>4s}  {"Return":>8s}  {"Annual":>8s}  {"MaxDD":>8s}  '
          f'{"Sharpe":>7s}  {"Trades":>7s}  {"WinR":>6s}')
    for _, row in lev_rows.iterrows():
        print(f'  {row["leverage"]:>3.0f}x  '
              f'{row["total_return_pct"]:>+7.1f}%  '
              f'{row["annual_return_pct"]:>+7.1f}%  '
              f'{row["max_dd_pct"]:>+7.1f}%  '
              f'{row["sharpe"]:>7.2f}  '
              f'{row["num_trades"]:>7d}  '
              f'{row["win_rate"]:>5.1f}%')

    # -- EXIT MODE COMPARISON --
    print('\n' + '=' * 78)
    print('  EXIT MODE COMPARISON (5x leverage, no_filter)')
    print('=' * 78)
    exit_rows = results_df[
        results_df['label'].str.match(r'^5x_.*_no_filter$')
    ]
    print(f'  {"Exit":<20s}  {"Return":>8s}  {"Annual":>8s}  {"MaxDD":>8s}  '
          f'{"Sharpe":>7s}  {"Trades":>7s}')
    for _, row in exit_rows.iterrows():
        exit_name = row['label'].replace('5x_', '').replace('_no_filter', '')
        print(f'  {exit_name:<20s}  '
              f'{row["total_return_pct"]:>+7.1f}%  '
              f'{row["annual_return_pct"]:>+7.1f}%  '
              f'{row["max_dd_pct"]:>+7.1f}%  '
              f'{row["sharpe"]:>7.2f}  '
              f'{row["num_trades"]:>7d}')

    # -- FILTER COMPARISON --
    print('\n' + '=' * 78)
    print('  FILTER COMPARISON (5x leverage, fixed_24h)')
    print('=' * 78)
    filt_rows = results_df[
        results_df['label'].str.match(r'^5x_fixed_24h_')
    ]
    print(f'  {"Filter":<20s}  {"Return":>8s}  {"Annual":>8s}  {"MaxDD":>8s}  '
          f'{"Sharpe":>7s}  {"Trades":>7s}')
    for _, row in filt_rows.iterrows():
        parts = row['label'].split('_')
        fname = '_'.join(parts[3:])
        print(f'  {fname:<20s}  '
              f'{row["total_return_pct"]:>+7.1f}%  '
              f'{row["annual_return_pct"]:>+7.1f}%  '
              f'{row["max_dd_pct"]:>+7.1f}%  '
              f'{row["sharpe"]:>7.2f}  '
              f'{row["num_trades"]:>7d}')

    # -- DIRECTION ANALYSIS --
    print('\n' + '=' * 78)
    print('  DIRECTION ANALYSIS (best config)')
    print('=' * 78)
    best_label = results_df.iloc[0]['label']
    best_cfg_idx = next(i for i, (l, _) in enumerate(configs) if l == best_label)
    best_result = run_backtest(token_frames, **configs[best_cfg_idx][1])
    for direction, dir_name in [(1, 'LONG'), (-1, 'SHORT')]:
        dir_trades = [t for t in best_result['trades'] if t['direction'] == direction]
        if not dir_trades:
            print(f'\n  {dir_name}: No trades')
            continue
        pnls = [t['pnl'] for t in dir_trades]
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)
        wr = wins / len(pnls) * 100
        print(f'  {dir_name}: {len(dir_trades)} trades, WR={wr:.1f}%, '
              f'Total PnL=${total_pnl:+,.0f}, Avg=${np.mean(pnls):+,.0f}')

    # -- TARGET CHECK --
    print('\n' + '=' * 78)
    print('  TARGET CHECK')
    print('=' * 78)
    best = results_df.iloc[0]
    print(f'  Best config: {best["label"]}')
    print(f'  Annual Return >= 300%: {"PASS" if best["annual_return_pct"] >= 300 else "FAIL"} '
          f'({best["annual_return_pct"]:+.1f}%)')
    print(f'  Max DD <= 20%:         {"PASS" if abs(best["max_dd_pct"]) <= 20 else "FAIL"} '
          f'({best["max_dd_pct"]:.1f}%)')
    print(f'  Sharpe:                {best["sharpe"]:.2f}')

    target_met = results_df[
        (results_df['annual_return_pct'] >= 300) &
        (results_df['max_dd_pct'] >= -20)
    ]
    if len(target_met) > 0:
        print(f'\n  {len(target_met)} config(s) meet BOTH targets:')
        for _, row in target_met.iterrows():
            print(f'    {row["label"]}: annual={row["annual_return_pct"]:+.1f}%, '
                  f'DD={row["max_dd_pct"]:.1f}%, sharpe={row["sharpe"]:.2f}')
    else:
        print(f'\n  No config meets both targets. Best by category:')
        best_ret = results_df.loc[results_df['annual_return_pct'].idxmax()]
        best_dd = results_df.loc[results_df['max_dd_pct'].idxmax()]
        best_sharpe = results_df.loc[results_df['sharpe'].idxmax()]
        print(f'    Highest annual:  {best_ret["label"]} = '
              f'{best_ret["annual_return_pct"]:+.1f}% (DD={best_ret["max_dd_pct"]:.1f}%)')
        print(f'    Smallest DD:     {best_dd["label"]} = '
              f'{best_dd["max_dd_pct"]:.1f}% (annual={best_dd["annual_return_pct"]:+.1f}%)')
        print(f'    Best Sharpe:     {best_sharpe["label"]} = '
              f'{best_sharpe["sharpe"]:.2f} '
              f'(annual={best_sharpe["annual_return_pct"]:+.1f}%, DD={best_sharpe["max_dd_pct"]:.1f}%)')

    print('\n' + '=' * 78)


if __name__ == '__main__':
    main()
