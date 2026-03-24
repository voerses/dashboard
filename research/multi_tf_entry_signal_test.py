"""
Multi-Timeframe Entry Signal Research
======================================
Tests 6 signals combining daily trend with intraday timing for long + short entries.
BTC spot + perp, ETH spot. IS/OOS 60/40 split.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
import warnings
warnings.filterwarnings('ignore')

# ─── Data loading ───────────────────────────────────────────────────────────

BTC_SPOT = '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet'
ETH_SPOT = '/workspace/crypto_backtest/data/spot/1h_cache/ETH_1h.parquet'
BTC_PERP = '/workspace/crypto_backtest/data/perp/1h_cache/BTC_1h.parquet'

def load_data(path):
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # Remove duplicates
    df = df[~df.index.duplicated(keep='first')]
    return df

def resample_to_4h(df_1h):
    """Resample 1h OHLCV to 4h bars."""
    return df_1h.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

def resample_to_daily(df_1h):
    """Resample 1h OHLCV to daily bars."""
    return df_1h.resample('1D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

# ─── Technical indicators ──────────────────────────────────────────────────

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def rolling_high(series, period):
    return series.rolling(period).max()

def rolling_low(series, period):
    return series.rolling(period).min()

# ─── Trade simulator ───────────────────────────────────────────────────────

def simulate_trades(df_1h, signals, fee_bps=10, is_perp=False, funding_col=None):
    """
    Simulate trades on 1h bars given entry signals.

    signals: DataFrame with columns:
        - 'entry_time': datetime of entry bar
        - 'direction': +1 (long) or -1 (short)
        - 'trail_atr_mult': ATR multiplier for trailing stop
        - 'max_hold_hours': max bars to hold
        - 'stop_price': optional hard stop price (NaN if none)
        - 'target_price': optional target price (NaN if none)

    Returns DataFrame of completed trades.
    """
    fee = fee_bps / 10000.0  # one-way fee
    results = []

    for idx, sig in signals.iterrows():
        entry_time = sig['entry_time']
        direction = sig['direction']
        trail_mult = sig['trail_atr_mult']
        max_hold = int(sig['max_hold_hours'])
        hard_stop = sig.get('stop_price', np.nan)
        target = sig.get('target_price', np.nan)

        # Find entry bar
        entry_loc = df_1h.index.searchsorted(entry_time)
        if entry_loc >= len(df_1h):
            continue

        entry_price = df_1h.iloc[entry_loc]['close']
        entry_cost = entry_price * fee

        # Compute ATR at entry for trailing stop
        lookback = max(0, entry_loc - 14)
        if entry_loc - lookback < 5:
            continue
        atr_slice = df_1h.iloc[lookback:entry_loc+1]
        tr_vals = np.maximum(
            atr_slice['high'].values - atr_slice['low'].values,
            np.maximum(
                np.abs(atr_slice['high'].values - np.roll(atr_slice['close'].values, 1)),
                np.abs(atr_slice['low'].values - np.roll(atr_slice['close'].values, 1))
            )
        )
        current_atr = np.mean(tr_vals[-14:]) if len(tr_vals) >= 14 else np.mean(tr_vals)
        trail_dist = trail_mult * current_atr

        # Trail logic
        if direction == 1:
            best_price = entry_price
            trail_stop = entry_price - trail_dist
        else:
            best_price = entry_price
            trail_stop = entry_price + trail_dist

        exit_price = None
        exit_time = None
        exit_reason = 'max_hold'

        end_loc = min(entry_loc + max_hold, len(df_1h))

        funding_paid = 0.0

        for i in range(entry_loc + 1, end_loc):
            bar = df_1h.iloc[i]

            # Funding for perps
            if is_perp and funding_col and funding_col in df_1h.columns:
                fr = df_1h.iloc[i][funding_col]
                if not np.isnan(fr):
                    # Long pays funding when positive, short receives
                    funding_paid += direction * fr * entry_price

            # Check hard stop
            if not np.isnan(hard_stop):
                if direction == 1 and bar['low'] <= hard_stop:
                    exit_price = hard_stop
                    exit_time = df_1h.index[i]
                    exit_reason = 'hard_stop'
                    break
                elif direction == -1 and bar['high'] >= hard_stop:
                    exit_price = hard_stop
                    exit_time = df_1h.index[i]
                    exit_reason = 'hard_stop'
                    break

            # Check target
            if not np.isnan(target):
                if direction == 1 and bar['high'] >= target:
                    exit_price = target
                    exit_time = df_1h.index[i]
                    exit_reason = 'target'
                    break
                elif direction == -1 and bar['low'] <= target:
                    exit_price = target
                    exit_time = df_1h.index[i]
                    exit_reason = 'target'
                    break

            # Update trailing stop
            if direction == 1:
                if bar['high'] > best_price:
                    best_price = bar['high']
                    trail_stop = best_price - trail_dist
                if bar['low'] <= trail_stop:
                    exit_price = trail_stop
                    exit_time = df_1h.index[i]
                    exit_reason = 'trail_stop'
                    break
            else:
                if bar['low'] < best_price:
                    best_price = bar['low']
                    trail_stop = best_price + trail_dist
                if bar['high'] >= trail_stop:
                    exit_price = trail_stop
                    exit_time = df_1h.index[i]
                    exit_reason = 'trail_stop'
                    break

        if exit_price is None:
            # Max hold exit
            if end_loc < len(df_1h):
                exit_price = df_1h.iloc[end_loc - 1]['close']
                exit_time = df_1h.index[end_loc - 1]
            else:
                exit_price = df_1h.iloc[-1]['close']
                exit_time = df_1h.index[-1]

        exit_cost = exit_price * fee

        if direction == 1:
            pnl = (exit_price - entry_price) - entry_cost - exit_cost - funding_paid
        else:
            pnl = (entry_price - exit_price) - entry_cost - exit_cost - funding_paid

        pnl_pct = pnl / entry_price

        hold_hours = (exit_time - entry_time).total_seconds() / 3600 if exit_time else max_hold

        results.append({
            'entry_time': entry_time,
            'exit_time': exit_time,
            'direction': direction,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'hold_hours': hold_hours,
            'exit_reason': exit_reason,
            'funding_paid': funding_paid,
        })

    return pd.DataFrame(results)


# ─── Metrics computation ──────────────────────────────────────────────────

def compute_metrics(trades_df):
    """Compute strategy metrics from trades DataFrame."""
    if len(trades_df) == 0:
        return {
            'trades': 0, 'win_rate': 0, 'avg_pnl_pct': 0,
            'profit_factor': 0, 'sharpe': 0, 'max_consec_loss': 0,
            'total_pnl_pct': 0, 'avg_hold_hours': 0,
            'max_dd_pct': 0, 'median_pnl_pct': 0,
        }

    wins = trades_df[trades_df['pnl_pct'] > 0]
    losses = trades_df[trades_df['pnl_pct'] <= 0]

    win_rate = len(wins) / len(trades_df)
    avg_pnl = trades_df['pnl_pct'].mean()
    total_pnl = trades_df['pnl_pct'].sum()

    gross_profit = wins['pnl_pct'].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses['pnl_pct'].sum()) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0

    # Sharpe (annualized from per-trade returns)
    if trades_df['pnl_pct'].std() > 0:
        avg_hold = trades_df['hold_hours'].mean()
        trades_per_year = 8760 / max(avg_hold, 1)
        sharpe = (trades_df['pnl_pct'].mean() / trades_df['pnl_pct'].std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0

    # Max consecutive losses
    is_loss = (trades_df['pnl_pct'] <= 0).astype(int).values
    max_consec = 0
    current = 0
    for v in is_loss:
        if v == 1:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0

    # Max drawdown on cumulative equity
    cum_pnl = trades_df['pnl_pct'].cumsum()
    running_max = cum_pnl.cummax()
    dd = cum_pnl - running_max
    max_dd = dd.min()

    return {
        'trades': len(trades_df),
        'win_rate': round(win_rate, 4),
        'avg_pnl_pct': round(avg_pnl * 100, 4),
        'median_pnl_pct': round(trades_df['pnl_pct'].median() * 100, 4),
        'total_pnl_pct': round(total_pnl * 100, 2),
        'profit_factor': round(pf, 3),
        'sharpe': round(sharpe, 3),
        'max_consec_loss': max_consec,
        'avg_hold_hours': round(trades_df['hold_hours'].mean(), 1),
        'max_dd_pct': round(max_dd * 100, 2),
    }


def is_oos_split(trades_df, is_ratio=0.6):
    """Split trades into IS and OOS by time."""
    if len(trades_df) == 0:
        return pd.DataFrame(), pd.DataFrame()
    trades_df = trades_df.sort_values('entry_time')
    split_idx = int(len(trades_df) * is_ratio)
    return trades_df.iloc[:split_idx].copy(), trades_df.iloc[split_idx:].copy()


def kill_check(is_metrics, oos_metrics):
    """Apply kill criteria. Returns (passed, reasons)."""
    reasons = []

    if oos_metrics['trades'] < 30:
        reasons.append(f"OOS trades={oos_metrics['trades']} < 30")
    if oos_metrics['win_rate'] < 0.48:
        reasons.append(f"OOS win_rate={oos_metrics['win_rate']:.3f} < 0.48")
    if oos_metrics['profit_factor'] < 1.05:
        reasons.append(f"OOS PF={oos_metrics['profit_factor']:.3f} < 1.05")

    if is_metrics['profit_factor'] > 0:
        pf_drop = 1 - (oos_metrics['profit_factor'] / max(is_metrics['profit_factor'], 1e-9))
        if pf_drop > 0.5:
            reasons.append(f"PF drop={pf_drop:.1%} > 50%")

    return len(reasons) == 0, reasons


# ─── Signal generators ─────────────────────────────────────────────────────

def signal_1_trend_pullback_long(df_1h):
    """Signal 1: Trend + Pullback (Long)
    Daily: 20d EMA > 50d EMA (uptrend)
    4h: RSI(14) dips below 40 then recovers above 40 -> long entry
    Exit: trail at 2x ATR(14) on 4h, max hold 168h
    """
    df_daily = resample_to_daily(df_1h)
    df_4h = resample_to_4h(df_1h)

    # Daily trend
    df_daily['ema20'] = ema(df_daily['close'], 20)
    df_daily['ema50'] = ema(df_daily['close'], 50)
    df_daily['uptrend'] = (df_daily['ema20'] > df_daily['ema50']).astype(int)

    # 4h RSI
    df_4h['rsi'] = rsi(df_4h['close'], 14)
    df_4h['rsi_prev'] = df_4h['rsi'].shift(1)

    # RSI dips below 40 then recovers above 40
    df_4h['dip_recover'] = ((df_4h['rsi_prev'] < 40) & (df_4h['rsi'] >= 40)).astype(int)

    # Map daily trend to 4h bars
    df_4h['date'] = df_4h.index.date
    trend_map = df_daily['uptrend'].to_dict()
    df_4h['uptrend'] = df_4h['date'].map(lambda d: trend_map.get(pd.Timestamp(d), 0))

    # Entry signals
    entries = df_4h[(df_4h['dip_recover'] == 1) & (df_4h['uptrend'] == 1)].copy()

    signals = pd.DataFrame({
        'entry_time': entries.index,
        'direction': 1,
        'trail_atr_mult': 2.0,
        'max_hold_hours': 168,
        'stop_price': np.nan,
        'target_price': np.nan,
    })

    return signals


def signal_2_trend_pullback_short(df_1h):
    """Signal 2: Trend + Pullback (Short)
    Daily: 20d EMA < 50d EMA (downtrend)
    4h: RSI(14) spikes above 60 then drops below 60 -> short entry
    Exit: trail at 2x ATR(14) on 4h, max hold 168h
    """
    df_daily = resample_to_daily(df_1h)
    df_4h = resample_to_4h(df_1h)

    df_daily['ema20'] = ema(df_daily['close'], 20)
    df_daily['ema50'] = ema(df_daily['close'], 50)
    df_daily['downtrend'] = (df_daily['ema20'] < df_daily['ema50']).astype(int)

    df_4h['rsi'] = rsi(df_4h['close'], 14)
    df_4h['rsi_prev'] = df_4h['rsi'].shift(1)

    df_4h['spike_drop'] = ((df_4h['rsi_prev'] > 60) & (df_4h['rsi'] <= 60)).astype(int)

    df_4h['date'] = df_4h.index.date
    trend_map = df_daily['downtrend'].to_dict()
    df_4h['downtrend'] = df_4h['date'].map(lambda d: trend_map.get(pd.Timestamp(d), 0))

    entries = df_4h[(df_4h['spike_drop'] == 1) & (df_4h['downtrend'] == 1)].copy()

    signals = pd.DataFrame({
        'entry_time': entries.index,
        'direction': -1,
        'trail_atr_mult': 2.0,
        'max_hold_hours': 168,
        'stop_price': np.nan,
        'target_price': np.nan,
    })

    return signals


def signal_3_momentum_breakout_long(df_1h):
    """Signal 3: Momentum Breakout (Long)
    Daily: close > 20d high (breakout)
    1h: first 1h candle that closes above breakout level with volume > 1.5x avg -> long
    Exit: stop at breakout level (breakeven), trail at 3x ATR, max hold 336h
    """
    df_daily = resample_to_daily(df_1h)

    # 20d rolling high (excluding current bar)
    df_daily['high_20'] = df_daily['high'].shift(1).rolling(20).max()
    df_daily['breakout'] = (df_daily['close'] > df_daily['high_20']).astype(int)
    df_daily['breakout_prev'] = df_daily['breakout'].shift(1).fillna(0)

    # New breakout: current bar is breakout, previous was not
    df_daily['new_breakout'] = ((df_daily['breakout'] == 1) & (df_daily['breakout_prev'] == 0)).astype(int)

    # 1h volume average
    df_1h_copy = df_1h.copy()
    df_1h_copy['vol_avg'] = df_1h_copy['volume'].rolling(20).mean()
    df_1h_copy['vol_ratio'] = df_1h_copy['volume'] / df_1h_copy['vol_avg']

    breakout_days = df_daily[df_daily['new_breakout'] == 1]

    signals_list = []
    for day_ts, row in breakout_days.iterrows():
        breakout_level = row['high_20']
        # Look at intraday bars on the breakout day and next day
        start = day_ts
        end = day_ts + pd.Timedelta(days=2)
        intraday = df_1h_copy[(df_1h_copy.index >= start) & (df_1h_copy.index < end)]

        for ts, bar in intraday.iterrows():
            if bar['close'] > breakout_level and bar['vol_ratio'] > 1.5:
                signals_list.append({
                    'entry_time': ts,
                    'direction': 1,
                    'trail_atr_mult': 3.0,
                    'max_hold_hours': 336,
                    'stop_price': breakout_level,
                    'target_price': np.nan,
                })
                break  # Only first qualifying bar

    return pd.DataFrame(signals_list) if signals_list else pd.DataFrame(
        columns=['entry_time', 'direction', 'trail_atr_mult', 'max_hold_hours', 'stop_price', 'target_price'])


def signal_4_momentum_breakdown_short(df_1h):
    """Signal 4: Momentum Breakdown (Short)
    Daily: close < 20d low (breakdown)
    1h: first 1h candle that closes below breakdown level with volume > 1.5x avg -> short
    Exit: stop at breakdown level, trail at 3x ATR, max hold 336h
    """
    df_daily = resample_to_daily(df_1h)

    df_daily['low_20'] = df_daily['low'].shift(1).rolling(20).min()
    df_daily['breakdown'] = (df_daily['close'] < df_daily['low_20']).astype(int)
    df_daily['breakdown_prev'] = df_daily['breakdown'].shift(1).fillna(0)
    df_daily['new_breakdown'] = ((df_daily['breakdown'] == 1) & (df_daily['breakdown_prev'] == 0)).astype(int)

    df_1h_copy = df_1h.copy()
    df_1h_copy['vol_avg'] = df_1h_copy['volume'].rolling(20).mean()
    df_1h_copy['vol_ratio'] = df_1h_copy['volume'] / df_1h_copy['vol_avg']

    breakdown_days = df_daily[df_daily['new_breakdown'] == 1]

    signals_list = []
    for day_ts, row in breakdown_days.iterrows():
        breakdown_level = row['low_20']
        start = day_ts
        end = day_ts + pd.Timedelta(days=2)
        intraday = df_1h_copy[(df_1h_copy.index >= start) & (df_1h_copy.index < end)]

        for ts, bar in intraday.iterrows():
            if bar['close'] < breakdown_level and bar['vol_ratio'] > 1.5:
                signals_list.append({
                    'entry_time': ts,
                    'direction': -1,
                    'trail_atr_mult': 3.0,
                    'max_hold_hours': 336,
                    'stop_price': breakdown_level,
                    'target_price': np.nan,
                })
                break

    return pd.DataFrame(signals_list) if signals_list else pd.DataFrame(
        columns=['entry_time', 'direction', 'trail_atr_mult', 'max_hold_hours', 'stop_price', 'target_price'])


def signal_5_macd_volume(df_1h):
    """Signal 5: MACD + Volume Confirmation
    4h: MACD(12,26,9) crosses signal line
    1h: volume on cross bar > 2x 20-period average
    Direction: MACD cross up = long, cross down = short
    Exit: opposite MACD cross OR trail at 2.5x ATR, max hold 168h
    """
    df_4h = resample_to_4h(df_1h)

    macd_line, signal_line = macd(df_4h['close'])
    df_4h['macd'] = macd_line
    df_4h['signal'] = signal_line
    df_4h['macd_prev'] = df_4h['macd'].shift(1)
    df_4h['signal_prev'] = df_4h['signal'].shift(1)

    # Cross up: macd was below signal, now above
    df_4h['cross_up'] = ((df_4h['macd_prev'] < df_4h['signal_prev']) &
                          (df_4h['macd'] >= df_4h['signal'])).astype(int)
    # Cross down
    df_4h['cross_down'] = ((df_4h['macd_prev'] > df_4h['signal_prev']) &
                            (df_4h['macd'] <= df_4h['signal'])).astype(int)

    # Map to 1h for volume confirmation
    df_1h_copy = df_1h.copy()
    df_1h_copy['vol_avg_20'] = df_1h_copy['volume'].rolling(20).mean()

    signals_list = []

    for ts in df_4h[df_4h['cross_up'] == 1].index:
        # Find the 1h bar closest to this 4h bar
        mask = (df_1h_copy.index >= ts) & (df_1h_copy.index < ts + pd.Timedelta(hours=4))
        bars = df_1h_copy[mask]
        for bar_ts, bar in bars.iterrows():
            if bar['volume'] > 2.0 * bar['vol_avg_20'] and bar['vol_avg_20'] > 0:
                signals_list.append({
                    'entry_time': bar_ts,
                    'direction': 1,
                    'trail_atr_mult': 2.5,
                    'max_hold_hours': 168,
                    'stop_price': np.nan,
                    'target_price': np.nan,
                })
                break

    for ts in df_4h[df_4h['cross_down'] == 1].index:
        mask = (df_1h_copy.index >= ts) & (df_1h_copy.index < ts + pd.Timedelta(hours=4))
        bars = df_1h_copy[mask]
        for bar_ts, bar in bars.iterrows():
            if bar['volume'] > 2.0 * bar['vol_avg_20'] and bar['vol_avg_20'] > 0:
                signals_list.append({
                    'entry_time': bar_ts,
                    'direction': -1,
                    'trail_atr_mult': 2.5,
                    'max_hold_hours': 168,
                    'stop_price': np.nan,
                    'target_price': np.nan,
                })
                break

    if signals_list:
        df_signals = pd.DataFrame(signals_list).sort_values('entry_time').reset_index(drop=True)
    else:
        df_signals = pd.DataFrame(
            columns=['entry_time', 'direction', 'trail_atr_mult', 'max_hold_hours', 'stop_price', 'target_price'])

    return df_signals


def signal_6_support_resistance_bounce(df_1h):
    """Signal 6: Support/Resistance Bounce
    Compute 20-period pivot highs/lows on daily bars
    1h: price touches within 0.5% of pivot level + reversal candle
    Direction: bounce off support = long, rejection at resistance = short
    Exit: opposite pivot level as target, stop at 1.5x ATR, max hold 240h
    """
    df_daily = resample_to_daily(df_1h)

    # Pivot highs: high is highest in 10 bars on each side
    pivots_high = []
    pivots_low = []
    highs = df_daily['high'].values
    lows = df_daily['low'].values
    dates = df_daily.index

    lookback = 10  # 10 bars each side for pivot

    for i in range(lookback, len(df_daily) - lookback):
        window_high = highs[i - lookback:i + lookback + 1]
        if highs[i] == window_high.max():
            pivots_high.append((dates[i], highs[i]))

        window_low = lows[i - lookback:i + lookback + 1]
        if lows[i] == window_low.min():
            pivots_low.append((dates[i], lows[i]))

    # Helper: check for hammer (bullish reversal) or shooting star (bearish reversal)
    def is_hammer(bar):
        """Bullish reversal: small body at top, long lower wick."""
        body = abs(bar['close'] - bar['open'])
        total = bar['high'] - bar['low']
        if total == 0:
            return False
        lower_wick = min(bar['close'], bar['open']) - bar['low']
        return lower_wick > 2 * body and body < 0.3 * total

    def is_shooting_star(bar):
        """Bearish reversal: small body at bottom, long upper wick."""
        body = abs(bar['close'] - bar['open'])
        total = bar['high'] - bar['low']
        if total == 0:
            return False
        upper_wick = bar['high'] - max(bar['close'], bar['open'])
        return upper_wick > 2 * body and body < 0.3 * total

    signals_list = []
    threshold = 0.005  # 0.5%

    # For each 1h bar, check if near a known pivot
    df_1h_copy = df_1h.copy()
    df_1h_atr = atr(df_1h_copy, 14)

    # Only check pivots that are in the past relative to each bar
    # Build arrays for fast lookup
    pivot_high_levels = np.array([p[1] for p in pivots_high])
    pivot_high_dates = [p[0] for p in pivots_high]
    pivot_low_levels = np.array([p[1] for p in pivots_low])
    pivot_low_dates = [p[0] for p in pivots_low]

    used_entries = set()  # Avoid duplicate entries at same pivot level

    for i in range(20, len(df_1h_copy)):
        bar = df_1h_copy.iloc[i]
        bar_time = df_1h_copy.index[i]
        bar_date = bar_time.date()
        current_atr = df_1h_atr.iloc[i] if i < len(df_1h_atr) else np.nan

        if np.isnan(current_atr) or current_atr == 0:
            continue

        # Check support levels (pivot lows)
        for j, (pdate, plevel) in enumerate(zip(pivot_low_dates, pivot_low_levels)):
            if pdate.date() >= bar_date:
                continue
            # Check if price is within 0.5% of support
            if abs(bar['low'] - plevel) / plevel < threshold:
                if is_hammer(bar):
                    key = (bar_time.date(), round(plevel, 0))
                    if key not in used_entries:
                        # Find nearest resistance for target
                        future_resistances = [p[1] for p in pivots_high if p[0].date() < bar_date and p[1] > bar['close']]
                        target = min(future_resistances) if future_resistances else np.nan

                        signals_list.append({
                            'entry_time': bar_time,
                            'direction': 1,
                            'trail_atr_mult': 1.5,  # tighter trail
                            'max_hold_hours': 240,
                            'stop_price': bar['close'] - 1.5 * current_atr,
                            'target_price': target,
                        })
                        used_entries.add(key)
                        break

        # Check resistance levels (pivot highs)
        for j, (pdate, plevel) in enumerate(zip(pivot_high_dates, pivot_high_levels)):
            if pdate.date() >= bar_date:
                continue
            if abs(bar['high'] - plevel) / plevel < threshold:
                if is_shooting_star(bar):
                    key = (bar_time.date(), round(plevel, 0))
                    if key not in used_entries:
                        future_supports = [p[1] for p in pivots_low if p[0].date() < bar_date and p[1] < bar['close']]
                        target = max(future_supports) if future_supports else np.nan

                        signals_list.append({
                            'entry_time': bar_time,
                            'direction': -1,
                            'trail_atr_mult': 1.5,
                            'max_hold_hours': 240,
                            'stop_price': bar['close'] + 1.5 * current_atr,
                            'target_price': target,
                        })
                        used_entries.add(key)
                        break

    if signals_list:
        df_signals = pd.DataFrame(signals_list).sort_values('entry_time').reset_index(drop=True)
    else:
        df_signals = pd.DataFrame(
            columns=['entry_time', 'direction', 'trail_atr_mult', 'max_hold_hours', 'stop_price', 'target_price'])

    return df_signals


# ─── Main execution ─────────────────────────────────────────────────────────

def run_signal_test(signal_func, signal_name, df_1h, fee_bps=10, is_perp=False):
    """Run a single signal and return metrics."""
    print(f"\n{'='*60}")
    print(f"  {signal_name}")
    print(f"{'='*60}")

    signals = signal_func(df_1h)
    print(f"  Generated {len(signals)} entry signals")

    if len(signals) == 0:
        return None, None, None, None

    # Count by direction
    if 'direction' in signals.columns:
        longs = (signals['direction'] == 1).sum()
        shorts = (signals['direction'] == -1).sum()
        print(f"  Longs: {longs}, Shorts: {shorts}")

    funding_col = 'funding_1h' if is_perp else None
    trades = simulate_trades(df_1h, signals, fee_bps=fee_bps, is_perp=is_perp, funding_col=funding_col)
    print(f"  Completed {len(trades)} trades")

    if len(trades) == 0:
        return None, None, None, None

    # Split IS/OOS
    is_trades, oos_trades = is_oos_split(trades, 0.6)

    # Overall metrics
    all_metrics = compute_metrics(trades)
    is_metrics = compute_metrics(is_trades)
    oos_metrics = compute_metrics(oos_trades)

    # Per-direction metrics
    long_trades = trades[trades['direction'] == 1]
    short_trades = trades[trades['direction'] == -1]
    long_metrics = compute_metrics(long_trades) if len(long_trades) > 0 else None
    short_metrics = compute_metrics(short_trades) if len(short_trades) > 0 else None

    # IS/OOS per direction
    long_is, long_oos = is_oos_split(long_trades, 0.6) if len(long_trades) > 0 else (pd.DataFrame(), pd.DataFrame())
    short_is, short_oos = is_oos_split(short_trades, 0.6) if len(short_trades) > 0 else (pd.DataFrame(), pd.DataFrame())

    long_is_m = compute_metrics(long_is) if len(long_is) > 0 else None
    long_oos_m = compute_metrics(long_oos) if len(long_oos) > 0 else None
    short_is_m = compute_metrics(short_is) if len(short_is) > 0 else None
    short_oos_m = compute_metrics(short_oos) if len(short_oos) > 0 else None

    # Kill check
    passed, reasons = kill_check(is_metrics, oos_metrics)

    print(f"\n  ALL   | Trades: {all_metrics['trades']:4d} | WR: {all_metrics['win_rate']:.3f} | PF: {all_metrics['profit_factor']:.3f} | Sharpe: {all_metrics['sharpe']:.2f} | Total: {all_metrics['total_pnl_pct']:+.1f}%")
    print(f"  IS    | Trades: {is_metrics['trades']:4d} | WR: {is_metrics['win_rate']:.3f} | PF: {is_metrics['profit_factor']:.3f} | Sharpe: {is_metrics['sharpe']:.2f} | Total: {is_metrics['total_pnl_pct']:+.1f}%")
    print(f"  OOS   | Trades: {oos_metrics['trades']:4d} | WR: {oos_metrics['win_rate']:.3f} | PF: {oos_metrics['profit_factor']:.3f} | Sharpe: {oos_metrics['sharpe']:.2f} | Total: {oos_metrics['total_pnl_pct']:+.1f}%")

    if long_metrics:
        print(f"  LONG  | Trades: {long_metrics['trades']:4d} | WR: {long_metrics['win_rate']:.3f} | PF: {long_metrics['profit_factor']:.3f} | Sharpe: {long_metrics['sharpe']:.2f}")
    if short_metrics:
        print(f"  SHORT | Trades: {short_metrics['trades']:4d} | WR: {short_metrics['win_rate']:.3f} | PF: {short_metrics['profit_factor']:.3f} | Sharpe: {short_metrics['sharpe']:.2f}")

    if passed:
        print(f"  >> PASSED kill criteria")
    else:
        print(f"  >> KILLED: {'; '.join(reasons)}")

    # Trades per month
    if len(trades) > 0:
        date_range_months = (trades['entry_time'].max() - trades['entry_time'].min()).days / 30.44
        trades_per_month = len(trades) / max(date_range_months, 1)
        print(f"  Trades/month: {trades_per_month:.1f}")

    result = {
        'signal': signal_name,
        'all': all_metrics,
        'is': is_metrics,
        'oos': oos_metrics,
        'long': long_metrics,
        'short': short_metrics,
        'long_is': long_is_m,
        'long_oos': long_oos_m,
        'short_is': short_is_m,
        'short_oos': short_oos_m,
        'passed': passed,
        'kill_reasons': reasons,
        'trades_per_month': trades_per_month if len(trades) > 0 else 0,
    }

    return result, trades, is_trades, oos_trades


def run_all_signals(df_1h, asset_name, fee_bps=10, is_perp=False):
    """Run all 6 signals on a dataset."""
    print(f"\n{'#'*70}")
    print(f"  MULTI-TIMEFRAME ENTRY SIGNALS: {asset_name}")
    print(f"  Data: {len(df_1h)} bars, {df_1h.index.min()} to {df_1h.index.max()}")
    print(f"  Fees: {fee_bps} bps round trip, Perp: {is_perp}")
    print(f"{'#'*70}")

    signal_funcs = [
        (signal_1_trend_pullback_long, "S1: Trend+Pullback Long"),
        (signal_2_trend_pullback_short, "S2: Trend+Pullback Short"),
        (signal_3_momentum_breakout_long, "S3: Momentum Breakout Long"),
        (signal_4_momentum_breakdown_short, "S4: Momentum Breakdown Short"),
        (signal_5_macd_volume, "S5: MACD+Volume (Both)"),
        (signal_6_support_resistance_bounce, "S6: Support/Resistance Bounce (Both)"),
    ]

    all_results = {}
    all_trades = {}

    for func, name in signal_funcs:
        try:
            result, trades, is_t, oos_t = run_signal_test(func, name, df_1h, fee_bps=fee_bps, is_perp=is_perp)
            if result:
                all_results[name] = result
                all_trades[name] = trades
        except Exception as e:
            print(f"\n  ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()

    return all_results, all_trades


def compute_combined_equity(all_trades_dict):
    """Compute combined long+short equity curve metrics."""
    combined = []
    for name, trades in all_trades_dict.items():
        if trades is not None and len(trades) > 0:
            combined.append(trades)

    if not combined:
        return None

    all_trades = pd.concat(combined, ignore_index=True).sort_values('entry_time')

    # Compute correlation between long and short trade returns
    long_t = all_trades[all_trades['direction'] == 1].copy()
    short_t = all_trades[all_trades['direction'] == -1].copy()

    if len(long_t) > 10 and len(short_t) > 10:
        # Monthly returns for correlation
        long_t['month'] = long_t['entry_time'].dt.to_period('M')
        short_t['month'] = short_t['entry_time'].dt.to_period('M')

        long_monthly = long_t.groupby('month')['pnl_pct'].sum()
        short_monthly = short_t.groupby('month')['pnl_pct'].sum()

        common = long_monthly.index.intersection(short_monthly.index)
        if len(common) > 5:
            corr = long_monthly[common].corr(short_monthly[common])
        else:
            corr = np.nan
    else:
        corr = np.nan

    combined_metrics = compute_metrics(all_trades)
    combined_metrics['long_short_correlation'] = round(corr, 3) if not np.isnan(corr) else 'N/A'
    combined_metrics['num_long'] = len(long_t)
    combined_metrics['num_short'] = len(short_t)

    return combined_metrics


# ─── Run everything ──────────────────────────────────────────────────────────

if __name__ == '__main__':

    # Load data
    print("Loading data...")
    btc_spot = load_data(BTC_SPOT)
    eth_spot = load_data(ETH_SPOT)
    btc_perp = load_data(BTC_PERP)

    # Run on BTC spot
    btc_results, btc_trades = run_all_signals(btc_spot, "BTC Spot", fee_bps=10, is_perp=False)

    # Run passing signals on ETH
    passing_signals = {k: v for k, v in btc_results.items() if v['passed']}

    print(f"\n\n{'='*70}")
    print(f"  SIGNALS PASSING KILL CRITERIA ON BTC: {len(passing_signals)}/{len(btc_results)}")
    print(f"{'='*70}")
    for name in passing_signals:
        print(f"  - {name}")

    # Run on BTC perp for comparison (shorts need perp)
    btc_perp_results, btc_perp_trades = run_all_signals(btc_perp, "BTC Perp", fee_bps=7, is_perp=True)

    # Run on ETH spot
    eth_results, eth_trades = run_all_signals(eth_spot, "ETH Spot", fee_bps=10, is_perp=False)

    # Combined equity analysis
    print(f"\n\n{'='*70}")
    print(f"  COMBINED EQUITY ANALYSIS")
    print(f"{'='*70}")

    btc_combined = compute_combined_equity(btc_trades)
    if btc_combined:
        print(f"\n  BTC Spot Combined (all signals):")
        print(f"    Trades: {btc_combined['trades']} (L:{btc_combined['num_long']} / S:{btc_combined['num_short']})")
        print(f"    WR: {btc_combined['win_rate']:.3f} | PF: {btc_combined['profit_factor']:.3f} | Sharpe: {btc_combined['sharpe']:.2f}")
        print(f"    Total PnL: {btc_combined['total_pnl_pct']:+.1f}% | MaxDD: {btc_combined['max_dd_pct']:.1f}%")
        print(f"    Long-Short Correlation: {btc_combined['long_short_correlation']}")

    eth_combined = compute_combined_equity(eth_trades)
    if eth_combined:
        print(f"\n  ETH Spot Combined (all signals):")
        print(f"    Trades: {eth_combined['trades']} (L:{eth_combined['num_long']} / S:{eth_combined['num_short']})")
        print(f"    WR: {eth_combined['win_rate']:.3f} | PF: {eth_combined['profit_factor']:.3f} | Sharpe: {eth_combined['sharpe']:.2f}")
        print(f"    Total PnL: {eth_combined['total_pnl_pct']:+.1f}% | MaxDD: {eth_combined['max_dd_pct']:.1f}%")
        print(f"    Long-Short Correlation: {eth_combined['long_short_correlation']}")

    btc_perp_combined = compute_combined_equity(btc_perp_trades)
    if btc_perp_combined:
        print(f"\n  BTC Perp Combined (all signals):")
        print(f"    Trades: {btc_perp_combined['trades']} (L:{btc_perp_combined['num_long']} / S:{btc_perp_combined['num_short']})")
        print(f"    WR: {btc_perp_combined['win_rate']:.3f} | PF: {btc_perp_combined['profit_factor']:.3f} | Sharpe: {btc_perp_combined['sharpe']:.2f}")
        print(f"    Total PnL: {btc_perp_combined['total_pnl_pct']:+.1f}% | MaxDD: {btc_perp_combined['max_dd_pct']:.1f}%")
        print(f"    Long-Short Correlation: {btc_perp_combined['long_short_correlation']}")

    # ─── Save all results for report generation ────────────────────────────

    all_data = {
        'btc_spot': btc_results,
        'btc_perp': btc_perp_results,
        'eth_spot': eth_results,
        'btc_combined': btc_combined,
        'eth_combined': eth_combined,
        'btc_perp_combined': btc_perp_combined,
    }

    # Print summary table
    print(f"\n\n{'='*70}")
    print(f"  SUMMARY TABLE")
    print(f"{'='*70}")
    print(f"{'Signal':<40} {'Asset':<10} {'Trades':>6} {'WR':>6} {'PF':>6} {'Sharpe':>7} {'OOS PF':>7} {'Pass':>5}")
    print("-" * 90)

    for asset_name, results in [('BTC Spot', btc_results), ('BTC Perp', btc_perp_results), ('ETH Spot', eth_results)]:
        for sig_name, r in results.items():
            passed_str = "YES" if r['passed'] else "NO"
            print(f"{sig_name:<40} {asset_name:<10} {r['all']['trades']:>6} {r['all']['win_rate']:>6.3f} {r['all']['profit_factor']:>6.3f} {r['all']['sharpe']:>7.2f} {r['oos']['profit_factor']:>7.3f} {passed_str:>5}")

    # Find best signal
    print(f"\n\n{'='*70}")
    print(f"  BEST SIGNAL IDENTIFICATION")
    print(f"{'='*70}")

    best_signal = None
    best_score = -999

    for asset_name, results in [('BTC Spot', btc_results), ('BTC Perp', btc_perp_results), ('ETH Spot', eth_results)]:
        for sig_name, r in results.items():
            if r['passed']:
                # Score: OOS Sharpe * OOS PF * sqrt(OOS trades)
                score = r['oos']['sharpe'] * r['oos']['profit_factor'] * np.sqrt(r['oos']['trades'])
                if score > best_score:
                    best_score = score
                    best_signal = (asset_name, sig_name, r)

    if best_signal:
        asset, name, r = best_signal
        print(f"\n  Best: {name} on {asset}")
        print(f"  Score: {best_score:.2f}")
        print(f"  OOS: Trades={r['oos']['trades']}, WR={r['oos']['win_rate']:.3f}, PF={r['oos']['profit_factor']:.3f}, Sharpe={r['oos']['sharpe']:.2f}")
    else:
        print("\n  No signal passed all kill criteria.")
        # Find the best failing signal
        for asset_name, results in [('BTC Spot', btc_results), ('BTC Perp', btc_perp_results)]:
            for sig_name, r in results.items():
                score = r['oos']['sharpe'] * max(r['oos']['profit_factor'], 0.01) * np.sqrt(max(r['oos']['trades'], 1))
                if score > best_score:
                    best_score = score
                    best_signal = (asset_name, sig_name, r)
        if best_signal:
            asset, name, r = best_signal
            print(f"\n  Best (failing): {name} on {asset}")
            print(f"  OOS: Trades={r['oos']['trades']}, WR={r['oos']['win_rate']:.3f}, PF={r['oos']['profit_factor']:.3f}, Sharpe={r['oos']['sharpe']:.2f}")
            print(f"  Kill reasons: {'; '.join(r['kill_reasons'])}")

    # Save all_data for report
    # Serialize for JSON (handle non-serializable types)
    def make_serializable(obj):
        if isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        elif isinstance(obj, (np.bool_,)):
            return bool(obj)
        elif isinstance(obj, list):
            return [make_serializable(i) for i in obj]
        return obj

    with open('/workspace/crypto_backtest/research/multi_tf_entry_signal_data.json', 'w') as f:
        json.dump(make_serializable(all_data), f, indent=2, default=str)

    print("\n\nResults saved to multi_tf_entry_signal_data.json")
    print("Done.")
