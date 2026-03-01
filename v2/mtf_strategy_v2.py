"""
Multi-Timeframe Fat-Tail Swing Strategy v2 — 1H:4H:Daily Stack
================================================================

Industry-standard 1:4:24 ratio:
  Daily — Trend direction + regime detection (ETF cycle, funding rates)
    4H  — Setup confirmation (pullback, squeeze, mean-reversion zone)
    1H  — Entry timing (volume burst, taker flow, VWAP)

Three fat-tail capture strategies:
  1. Dual-Momentum Trend Following — ride trends with trailing ATR stops
  2. Volatility Breakout (squeeze) — enter on BB squeeze expansion
  3. Mean Reversion with Convex Exits — fade extremes, trail winners

All strategies optimize for PAYOFF RATIO (3:1 to 5:1), not win rate.

Performance: Vectorized signal generation + numpy-based position simulation.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import time
from pathlib import Path

from liquid_universe import LIQUID_TOKENS, TIER1, TIER2, TIER3, get_tier

# Try to import numba for JIT acceleration of simulation loops
try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    def njit(*args, **kwargs):
        """Fallback: no-op decorator when numba is not installed."""
        def decorator(func):
            return func
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator


# =============================================================================
# Fast Rolling Helpers (avoid pandas overhead)
# =============================================================================

def _rolling_mean(arr, w):
    """O(n) rolling mean via cumsum."""
    cs = np.cumsum(np.nan_to_num(arr, 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(arr), np.nan)
    out[w-1:] = (cs[w:] - cs[:-w]) / w
    return out


def _rolling_std(arr, w):
    """O(n) rolling std via cumsum."""
    a = np.nan_to_num(arr, 0.0)
    cs = np.insert(np.cumsum(a), 0, 0.0)
    cs2 = np.insert(np.cumsum(a**2), 0, 0.0)
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = (s2 - s**2 / w) / max(w - 1, 1)
    var = np.maximum(var, 0)
    out = np.full(len(arr), np.nan)
    out[w-1:] = np.sqrt(var)
    return out


def _ema(arr, span):
    """Vectorized EMA."""
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values


# =============================================================================
# Timeframe Aggregation
# =============================================================================

def aggregate_to_timeframe(df_1h, hours=4):
    """Aggregate 1H bars to any higher timeframe."""
    ohlcv = df_1h.resample(f'{hours}h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum',
    }).dropna(subset=['open'])

    if 'taker_buy_base' in df_1h.columns:
        tb = df_1h.resample(f'{hours}h').agg({'taker_buy_base': 'sum'})
        tb = tb.reindex(ohlcv.index)
        ohlcv['taker_buy_ratio'] = np.where(
            ohlcv['volume'] > 0, tb['taker_buy_base'].fillna(0) / ohlcv['volume'].clip(lower=1e-10), 0.5)
    if 'quote_volume' in df_1h.columns:
        ohlcv['quote_volume'] = df_1h.resample(f'{hours}h')['quote_volume'].sum()
    if 'trades' in df_1h.columns:
        ohlcv['trades'] = df_1h.resample(f'{hours}h')['trades'].sum()
    return ohlcv


# =============================================================================
# Indicator Computation (returns dict of numpy arrays for speed)
# =============================================================================

def compute_indicators_fast(close, high, low, volume, taker_buy=None, quote_vol=None):
    """Compute all indicators as numpy arrays. No pandas DataFrames."""
    n = len(close)

    ema_10 = _ema(close, 10)
    ema_20 = _ema(close, 20)
    ema_50 = _ema(close, 50)

    # MACD
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    macd_signal = _ema(macd, 9)
    macd_hist = macd - macd_signal

    # RSI
    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema(gains, 14)
    avg_loss = _ema(losses, 14)
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100 - 100 / (1 + rs)

    # Bollinger Bands
    sma20 = _rolling_mean(close, 20)
    bb_std = _rolling_std(close, 20)
    bb_upper = sma20 + 2 * bb_std
    bb_lower = sma20 - 2 * bb_std
    bb_width = 4 * bb_std / np.maximum(sma20, 1e-10)
    bb_pct = (close - np.nan_to_num(bb_lower, 0)) / np.maximum(4 * np.nan_to_num(bb_std, 1), 1e-10)

    # ATR
    tr = np.zeros(n)
    tr[1:] = np.maximum(high[1:] - low[1:],
                         np.maximum(np.abs(high[1:] - close[:-1]),
                                    np.abs(low[1:] - close[:-1])))
    atr = _ema(tr, 14)

    # ADX
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    plus_dm[1:] = np.where((high[1:]-high[:-1]) > (low[:-1]-low[1:]),
                            np.maximum(high[1:]-high[:-1], 0), 0)
    minus_dm[1:] = np.where((low[:-1]-low[1:]) > (high[1:]-high[:-1]),
                             np.maximum(low[:-1]-low[1:], 0), 0)
    smooth_atr = _ema(tr, 14)
    plus_di = 100 * _ema(plus_dm, 14) / np.maximum(smooth_atr, 1e-10)
    minus_di = 100 * _ema(minus_dm, 14) / np.maximum(smooth_atr, 1e-10)
    dx = np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10) * 100
    adx = _ema(dx, 14)

    # Volume ratio
    vol_sma = _rolling_mean(volume, 20)
    vol_ratio = volume / np.maximum(np.nan_to_num(vol_sma, 1), 1e-10)

    # Returns & volatility
    ret_1 = np.log(close / np.maximum(np.roll(close, 1), 1e-10))
    ret_1[0] = 0
    vol_20 = _rolling_std(ret_1, 20)

    # Donchian
    donch_high = pd.Series(high).rolling(20).max().values
    donch_low = pd.Series(low).rolling(20).min().values

    # Taker buy ratio
    if taker_buy is not None:
        taker = np.where(volume > 0, taker_buy / volume, 0.5)
    else:
        taker = np.full(n, 0.5)

    return {
        'close': close, 'high': high, 'low': low, 'volume': volume,
        'ema_10': ema_10, 'ema_20': ema_20, 'ema_50': ema_50,
        'macd': macd, 'macd_signal': macd_signal, 'macd_hist': macd_hist,
        'rsi': rsi, 'bb_upper': bb_upper, 'bb_lower': bb_lower,
        'bb_width': bb_width, 'bb_pct': bb_pct,
        'atr': atr, 'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'vol_ratio': vol_ratio, 'ret_1': ret_1, 'vol_20': vol_20,
        'donch_high': donch_high, 'donch_low': donch_low,
        'taker': taker,
    }


def _align_higher_to_lower(higher_idx, higher_vals, lower_idx):
    """Forward-fill higher timeframe values to lower timeframe index."""
    s = pd.Series(higher_vals, index=higher_idx)
    return s.reindex(lower_idx, method='ffill').values.copy()  # copy for mutability


# =============================================================================
# Daily Regime Detection (vectorized)
# =============================================================================

def detect_daily_regime(ind_d):
    """Vectorized regime detection on daily indicators."""
    n = len(ind_d['adx'])
    adx = ind_d['adx']
    ema_20 = ind_d['ema_20']
    ema_50 = ind_d['ema_50']
    vol_20 = ind_d['vol_20']

    valid_vol = vol_20[~np.isnan(vol_20)]
    vol_p75 = np.percentile(valid_vol, 75) if len(valid_vol) > 20 else 0.04
    vol_p25 = np.percentile(valid_vol, 25) if len(valid_vol) > 20 else 0.015

    # Default: range
    regimes = np.full(n, 3, dtype=np.int8)  # 0=crisis 1=quiet 2=uptrend 3=range 4=downtrend

    valid = ~np.isnan(adx) & ~np.isnan(vol_20)
    crisis = valid & (vol_20 > vol_p75 * 2)
    quiet = valid & ~crisis & (vol_20 < vol_p25 * 0.7)
    strong = valid & ~crisis & ~quiet & (adx > 25)
    uptrend = strong & (ema_20 > ema_50)
    downtrend = strong & ~uptrend

    regimes[crisis] = 0
    regimes[quiet] = 1
    regimes[uptrend] = 2
    regimes[downtrend] = 4

    # First 20 bars: range
    regimes[:20] = 3
    return regimes


# Regime constants
CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4


# =============================================================================
# Vectorized Signal Generation
# =============================================================================

def _generate_dual_momentum_signals(ind_1h, ind_4h, ind_d,
                                      regime_1h, idx_1h, idx_4h, idx_d):
    """
    Vectorized dual-momentum signal generation.
    Returns: entry_mask (bool), direction (+1 long)
    """
    n = len(ind_1h['close'])

    # Daily filters mapped to 1H
    ema50_d = _align_higher_to_lower(idx_d, ind_d['ema_50'], idx_1h)
    adx_d = _align_higher_to_lower(idx_d, ind_d['adx'], idx_1h)
    ret_d = ind_d['ret_1']
    mom_12d = pd.Series(ret_d).rolling(12).sum().values
    mom_12d_1h = _align_higher_to_lower(idx_d, mom_12d, idx_1h)

    # 4H filters mapped to 1H
    ema20_4h = _align_higher_to_lower(idx_4h, ind_4h['ema_20'], idx_1h)
    close_4h = _align_higher_to_lower(idx_4h, ind_4h['close'], idx_1h)
    rsi_4h = _align_higher_to_lower(idx_4h, ind_4h['rsi'], idx_1h)

    close = ind_1h['close']
    vol_ratio = ind_1h['vol_ratio']
    rsi = ind_1h['rsi']

    # All conditions vectorized
    regime_ok = (regime_1h == UPTREND)
    above_ema50 = close > np.nan_to_num(ema50_d, 0)
    adx_strong = np.nan_to_num(adx_d, 0) > 20
    mom_positive = np.nan_to_num(mom_12d_1h, 0) > 0

    # 4H pullback to EMA20
    pullback_pct = np.where(
        np.nan_to_num(ema20_4h, 1) > 0,
        (np.nan_to_num(close_4h, 0) - np.nan_to_num(ema20_4h, 0)) / np.maximum(np.nan_to_num(ema20_4h, 1), 1e-10),
        999
    )
    pullback_ok = (pullback_pct <= 0.01) & (pullback_pct >= -0.04)
    rsi_4h_ok = (np.nan_to_num(rsi_4h, 50) >= 35) & (np.nan_to_num(rsi_4h, 50) <= 60)

    # 1H confirmation
    vol_ok = vol_ratio > 1.2
    rsi_1h_ok = (rsi >= 30) & (rsi <= 55)

    # Combine all
    entry = regime_ok & above_ema50 & adx_strong & mom_positive & \
            pullback_ok & rsi_4h_ok & vol_ok & rsi_1h_ok
    entry[:200] = False

    return entry


def _generate_vol_breakout_signals(ind_1h, ind_4h, ind_d,
                                    regime_1h, idx_1h, idx_4h, idx_d):
    """
    Vectorized volatility breakout signal generation.
    Returns: entry_mask, direction (+1 long, -1 short)
    """
    n = len(ind_1h['close'])

    # 4H BB width mapped to 1H
    bb_width_4h = _align_higher_to_lower(idx_4h, ind_4h['bb_width'], idx_1h)
    bb_upper_4h = _align_higher_to_lower(idx_4h, ind_4h['bb_upper'], idx_1h)
    bb_lower_4h = _align_higher_to_lower(idx_4h, ind_4h['bb_lower'], idx_1h)
    close_4h = _align_higher_to_lower(idx_4h, ind_4h['close'], idx_1h)

    ema20_d = _align_higher_to_lower(idx_d, ind_d['ema_20'], idx_1h)
    ema50_d = _align_higher_to_lower(idx_d, ind_d['ema_50'], idx_1h)

    # BB width percentile
    valid_bbw = bb_width_4h[~np.isnan(bb_width_4h)]
    bbw_p20 = np.percentile(valid_bbw, 20) if len(valid_bbw) > 120 else 0.02

    # Squeeze detection: rolling count of bars in squeeze (JIT-accelerated)
    in_squeeze = np.nan_to_num(bb_width_4h, 999) < bbw_p20
    squeeze_bars = _count_squeeze_bars_jit(in_squeeze)

    # Squeeze just ended: was in squeeze last bar, not now, was there >= 12 bars (2 days)
    squeeze_ended = (~in_squeeze) & (np.roll(in_squeeze, 1)) & (np.roll(squeeze_bars, 1) >= 12)
    squeeze_ended[:4] = False

    # Direction from daily EMA
    daily_bullish = np.nan_to_num(ema20_d, 0) > np.nan_to_num(ema50_d, 0)

    # Breakout above/below BB
    above_bb = np.nan_to_num(close_4h, 0) > np.nan_to_num(bb_upper_4h, 999)
    below_bb = np.nan_to_num(close_4h, 0) < np.nan_to_num(bb_lower_4h, -999)

    # Volume + taker — need strong confirmation for breakout
    vol_ok = ind_1h['vol_ratio'] > 2.0  # 2x average volume
    taker = ind_1h['taker']
    taker_long = taker > 0.55  # clear buyer dominance
    taker_short = taker < 0.45  # clear seller dominance

    not_crisis = regime_1h != CRISIS

    entry_long = squeeze_ended & daily_bullish & above_bb & vol_ok & taker_long & not_crisis
    entry_short = squeeze_ended & (~daily_bullish) & below_bb & vol_ok & taker_short & not_crisis
    entry_long[:200] = False
    entry_short[:200] = False

    direction = np.zeros(n, dtype=np.int8)
    direction[entry_long] = 1
    direction[entry_short] = -1

    entry = entry_long | entry_short
    return entry, direction


def _generate_mean_reversion_signals(ind_1h, ind_4h, ind_d,
                                      regime_1h, idx_1h, idx_4h, idx_d):
    """
    Vectorized mean-reversion signal generation.
    """
    n = len(ind_1h['close'])

    rsi_4h = _align_higher_to_lower(idx_4h, ind_4h['rsi'], idx_1h)
    bb_pct_4h = _align_higher_to_lower(idx_4h, ind_4h['bb_pct'], idx_1h)
    macd_hist_4h = _align_higher_to_lower(idx_4h, ind_4h['macd_hist'], idx_1h)

    rsi = ind_1h['rsi']
    taker = ind_1h['taker']

    # Regime: only range or quiet
    regime_ok = (regime_1h == RANGE) | (regime_1h == QUIET)

    # 4H oversold — tighter filter to reduce false entries
    rsi_4h_os = np.nan_to_num(rsi_4h, 50) < 35
    bb_4h_low = np.nan_to_num(bb_pct_4h, 0.5) < 0.15

    # MACD turning up (momentum shifting)
    macd_h = np.nan_to_num(macd_hist_4h, 0)
    macd_turning = macd_h > np.roll(macd_h, 1)

    # 1H confirmation — must be genuinely oversold
    rsi_1h_os = rsi < 35
    rsi_turning = rsi > np.roll(rsi, 1)
    taker_ok = taker >= 0.52  # clear buyer aggression

    # Volume confirmation — need at least average volume
    vol_ok = ind_1h['vol_ratio'] >= 1.0

    entry = regime_ok & rsi_4h_os & bb_4h_low & macd_turning & \
            rsi_1h_os & rsi_turning & taker_ok & vol_ok
    entry[:200] = False

    return entry


# =============================================================================
# Numba-JIT Accelerated Simulation Core
# =============================================================================

@njit(cache=True)
def _simulate_core_jit(close, high, low, atr, entry_mask, direction,
                       stop_mult, trail_mult, target_mult,
                       regime, exit_regime_mask, min_hold, max_hold,
                       rsi, rsi_exit_level, use_rsi,
                       fee_rate, slippage_bps,
                       initial_capital, tier,
                       convex_exit, mean_target_vals, use_mean_target,
                       no_stop_bars, edge_override,
                       kelly_mult, cap_pct):
    """
    Numba-JIT compiled trade simulation loop.
    Returns arrays: pnl[], return_pct[], hold_hours[], exit_code[], position_usd[]
    exit_code: 0=stop, 1=target_5r, 2=target, 3=regime, 4=overbought, 5=mean_reached, 6=max_hold
    """
    n = len(close)
    # Pre-allocate output arrays (max possible trades = n)
    max_trades = n // 2
    out_pnl = np.empty(max_trades, dtype=np.float64)
    out_ret = np.empty(max_trades, dtype=np.float64)
    out_hold = np.empty(max_trades, dtype=np.int64)
    out_exit = np.empty(max_trades, dtype=np.int8)
    out_pos = np.empty(max_trades, dtype=np.float64)
    trade_count = 0

    equity = initial_capital
    position = 0.0
    entry_price = 0.0
    entry_bar = 0
    stop_price = 0.0
    highest = 0.0
    lowest = 999999.0
    initial_risk = 0.0

    for i in range(200, n):
        # EXIT
        if position != 0.0:
            bars_held = i - entry_bar
            d = direction[entry_bar] if entry_bar < n else 1

            if d == 1:
                if high[i] > highest:
                    highest = high[i]
            else:
                if low[i] < lowest:
                    lowest = low[i]

            cur_atr = atr[i]
            if np.isnan(cur_atr):
                cur_atr = abs(entry_price) * 0.02

            # Trailing stop update
            if convex_exit:
                if bars_held >= 48 and d == 1:
                    trail = highest - 2.0 * cur_atr
                    if trail > stop_price:
                        stop_price = trail
                elif bars_held >= 12 and d == 1:
                    if highest > entry_price + 1.5 * initial_risk:
                        be_trail = entry_price + 0.3 * initial_risk
                        if be_trail > stop_price:
                            stop_price = be_trail
            else:
                if bars_held >= no_stop_bars:
                    if d == 1:
                        trail = highest - trail_mult * cur_atr
                        if trail > stop_price:
                            stop_price = trail
                    else:
                        trail = lowest + trail_mult * cur_atr
                        if trail < stop_price:
                            stop_price = trail

            exit_signal = False
            exit_code = -1
            exit_price = close[i]

            stop_active = bars_held >= no_stop_bars or convex_exit

            if stop_active and d == 1 and low[i] <= stop_price:
                exit_signal = True
                exit_code = 0
                exit_price = stop_price
            elif stop_active and d == -1 and high[i] >= stop_price:
                exit_signal = True
                exit_code = 0
                exit_price = stop_price
            elif convex_exit and d == 1 and close[i] > entry_price + target_mult * initial_risk:
                exit_signal = True
                exit_code = 1
            elif not convex_exit and d == 1 and high[i] >= entry_price + target_mult * cur_atr:
                exit_signal = True
                exit_code = 2
                exit_price = entry_price + target_mult * cur_atr
            elif exit_regime_mask[i] and bars_held > 6:
                exit_signal = True
                exit_code = 3
            elif use_rsi and d == 1 and rsi[i] > rsi_exit_level and bars_held >= min_hold:
                exit_signal = True
                exit_code = 4
            elif convex_exit and use_mean_target:
                mt = mean_target_vals[i]
                if not np.isnan(mt) and d == 1 and close[i] >= mt and bars_held >= min_hold:
                    if close[i] < entry_price + 2.0 * initial_risk:
                        exit_signal = True
                        exit_code = 5
            if not exit_signal and bars_held >= max_hold:
                exit_signal = True
                exit_code = 6

            if exit_signal:
                slip = exit_price * slippage_bps / 10000.0
                if d == 1:
                    exit_price -= slip
                    pnl = position * (exit_price - entry_price)
                else:
                    exit_price += slip
                    pnl = position * (entry_price - exit_price)
                fee = abs(position * exit_price) * fee_rate
                net_pnl = pnl - fee
                equity += net_pnl

                pos_usd = abs(position * entry_price)
                if trade_count < max_trades:
                    out_pnl[trade_count] = net_pnl
                    out_ret[trade_count] = net_pnl / max(pos_usd, 1.0) * 100.0
                    out_hold[trade_count] = bars_held
                    out_exit[trade_count] = exit_code
                    out_pos[trade_count] = pos_usd
                    trade_count += 1
                position = 0.0

        # ENTRY
        if position == 0.0 and entry_mask[i]:
            d = direction[i] if i < n else 1
            if d == 0:
                d = 1

            cur_atr_e = atr[i]
            if np.isnan(cur_atr_e):
                cur_atr_e = close[i] * 0.02
            vol = cur_atr_e / max(close[i], 1e-10)

            # Inline position sizing (numba can't call Python functions)
            if tier == 0 or edge_override < 0.10:
                continue
            kelly_frac = kelly_mult * edge_override
            vol_adj = 0.02 / max(vol, 0.005) if vol > 0.0 else 1.0
            raw = equity * kelly_frac * vol_adj
            max_cap = equity * cap_pct
            max_trade = equity * 0.05
            pos_usd = min(raw, max_cap, max_trade)
            if pos_usd < 0.0:
                pos_usd = 0.0
            if pos_usd < 200.0:
                continue

            entry_price = close[i] + (close[i] * slippage_bps / 10000.0 * d)
            position = pos_usd / max(entry_price, 1e-10) * d
            entry_bar = i
            highest = high[i]
            lowest = low[i]
            fee = abs(position * entry_price) * fee_rate
            equity -= fee

            initial_risk = stop_mult * cur_atr_e
            if d == 1:
                stop_price = entry_price - initial_risk
            else:
                stop_price = entry_price + initial_risk

    # Close remaining position
    if position != 0.0:
        d_final = 1 if position > 0.0 else -1
        if d_final == 1:
            pnl = position * (close[n-1] - entry_price)
        else:
            pnl = abs(position) * (entry_price - close[n-1])
        fee = abs(position * close[n-1]) * fee_rate
        equity += pnl - fee

    return out_pnl[:trade_count], out_ret[:trade_count], out_hold[:trade_count], \
           out_exit[:trade_count], out_pos[:trade_count], equity


@njit(cache=True)
def _count_squeeze_bars_jit(in_squeeze):
    """Numba-JIT squeeze bar counter."""
    n = len(in_squeeze)
    out = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if in_squeeze[i]:
            out[i] = out[i-1] + 1
        else:
            out[i] = 0
    return out


# Exit code -> reason string mapping (outside numba)
_EXIT_REASONS = {0: 'stop', 1: 'target_5r', 2: 'target', 3: 'regime',
                 4: 'overbought', 5: 'mean_reached', 6: 'max_hold'}


# =============================================================================
# Vectorized Position Simulation
# =============================================================================

def _simulate_trades(close, high, low, atr, entry_mask, direction,
                     stop_mult, trail_mult, target_mult,
                     regime, exit_regimes, min_hold, max_hold,
                     rsi=None, rsi_exit_level=75,
                     fee_rate=0.001, slippage_bps=5,
                     initial_capital=66667, ticker='BTC',
                     convex_exit=False, mean_target_vals=None,
                     no_stop_bars=0, edge_override=0.35):
    """
    Simulate trades from vectorized entry signals.
    Dispatches to numba JIT core when available, otherwise pure Python fallback.
    """
    tier, _ = get_tier(ticker)
    if tier == 0:
        return [], initial_capital

    n = len(close)

    # Build exit regime mask (bool array for numba — can't pass Python sets)
    exit_regime_mask = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        if regime[i] in exit_regimes:
            exit_regime_mask[i] = True

    # RSI handling
    use_rsi = rsi is not None
    if rsi is None:
        rsi = np.full(n, 50.0)

    # Mean target handling
    use_mean_target = mean_target_vals is not None
    if mean_target_vals is None:
        mean_target_vals = np.full(n, np.nan)

    # Position sizing params
    kelly_mult = 0.5 if tier == 1 else 0.25
    cap_pct = 0.12 if tier == 1 else (0.08 if tier == 2 else 0.04)

    # Ensure correct types for numba
    entry_mask_arr = np.asarray(entry_mask, dtype=np.bool_)
    direction_arr = np.asarray(direction, dtype=np.int8)

    # Call JIT core
    pnl_arr, ret_arr, hold_arr, exit_arr, pos_arr, final_equity = _simulate_core_jit(
        close, high, low, atr, entry_mask_arr, direction_arr,
        float(stop_mult), float(trail_mult), float(target_mult),
        regime, exit_regime_mask, int(min_hold), int(max_hold),
        rsi, float(rsi_exit_level), use_rsi,
        float(fee_rate), float(slippage_bps),
        float(initial_capital), int(tier),
        convex_exit, mean_target_vals, use_mean_target,
        int(no_stop_bars), float(edge_override),
        float(kelly_mult), float(cap_pct),
    )

    # Convert arrays back to list of dicts (for compatibility with rest of codebase)
    trades = []
    for j in range(len(pnl_arr)):
        trades.append({
            'pnl': float(pnl_arr[j]),
            'return_pct': float(ret_arr[j]),
            'hold_hours': int(hold_arr[j]),
            'exit_reason': _EXIT_REASONS.get(int(exit_arr[j]), 'unknown'),
            'position_usd': float(pos_arr[j]),
        })

    return trades, final_equity


def _compute_position_size(tier, edge, equity, vol):
    """Fast position sizing — quarter/half Kelly + vol parity."""
    if tier == 0 or edge < 0.10:
        return 0.0
    kelly_mult = 0.5 if tier == 1 else 0.25
    kelly_frac = kelly_mult * edge
    vol_adj = 0.02 / max(vol, 0.005) if vol > 0 and not np.isnan(vol) else 1.0  # target 2% daily risk
    raw = equity * kelly_frac * vol_adj
    cap = 0.12 if tier == 1 else (0.08 if tier == 2 else 0.04)  # wider caps
    raw = min(raw, equity * cap, equity * 0.05)  # max 5% per trade
    return max(raw, 0)


# =============================================================================
# Combined Backtest Engine
# =============================================================================

def backtest_token(ticker, df_1h, capital=200_000,
                   fee_rate=0.001, slippage_bps=5,
                   strategies=('dual_momentum', 'vol_breakout', 'mean_reversion')):
    """
    Backtest a single token using all three strategies on 1H:4H:Daily stack.
    Optimized: indicators computed once, signals vectorized.
    """
    if df_1h is None or len(df_1h) < 500:
        return None

    # Build timeframes
    df_4h = aggregate_to_timeframe(df_1h, hours=4)
    df_daily = aggregate_to_timeframe(df_1h, hours=24)

    if len(df_4h) < 100 or len(df_daily) < 30:
        return None

    # Extract numpy arrays
    close_1h = df_1h['close'].values.astype(np.float64)
    high_1h = df_1h['high'].values.astype(np.float64)
    low_1h = df_1h['low'].values.astype(np.float64)
    vol_1h = df_1h['volume'].values.astype(np.float64)
    taker_1h = df_1h['taker_buy_base'].values.astype(np.float64) if 'taker_buy_base' in df_1h.columns else None

    close_4h = df_4h['close'].values.astype(np.float64)
    high_4h = df_4h['high'].values.astype(np.float64)
    low_4h = df_4h['low'].values.astype(np.float64)
    vol_4h = df_4h['volume'].values.astype(np.float64)
    taker_4h = df_4h['taker_buy_base'].values.astype(np.float64) if 'taker_buy_base' in df_4h.columns else None

    close_d = df_daily['close'].values.astype(np.float64)
    high_d = df_daily['high'].values.astype(np.float64)
    low_d = df_daily['low'].values.astype(np.float64)
    vol_d = df_daily['volume'].values.astype(np.float64)

    # Compute indicators ONCE
    ind_1h = compute_indicators_fast(close_1h, high_1h, low_1h, vol_1h, taker_1h)
    ind_4h = compute_indicators_fast(close_4h, high_4h, low_4h, vol_4h, taker_4h)
    ind_d = compute_indicators_fast(close_d, high_d, low_d, vol_d)

    idx_1h = df_1h.index
    idx_4h = df_4h.index
    idx_d = df_daily.index

    # Detect regime on daily -> map to 1H
    regimes_d = detect_daily_regime(ind_d)
    regime_1h = _align_higher_to_lower(idx_d, regimes_d.astype(float), idx_1h).astype(np.int8)
    regime_1h = np.nan_to_num(regime_1h, nan=RANGE).astype(np.int8)

    # 4H EMA20 for mean reversion target
    ema20_4h_1h = _align_higher_to_lower(idx_4h, ind_4h['ema_20'], idx_1h)

    all_trades = []
    total_equity = capital
    n_strats = len(strategies)
    alloc_per = capital / n_strats
    strat_results = {}

    for strat_name in strategies:
        if strat_name == 'dual_momentum':
            entry = _generate_dual_momentum_signals(
                ind_1h, ind_4h, ind_d, regime_1h, idx_1h, idx_4h, idx_d)
            direction = np.ones(len(close_1h), dtype=np.int8)  # long only
            trades, eq = _simulate_trades(
                close_1h, high_1h, low_1h, ind_1h['atr'], entry, direction,
                stop_mult=5.0, trail_mult=4.0, target_mult=999,  # wide initial stop, trail only
                regime=regime_1h, exit_regimes={CRISIS, DOWNTREND},
                min_hold=18, max_hold=720,
                rsi=ind_1h['rsi'], rsi_exit_level=999,
                fee_rate=fee_rate, slippage_bps=slippage_bps,
                initial_capital=alloc_per, ticker=ticker,
                no_stop_bars=12, edge_override=0.40,  # 12h protection, higher conviction
            )

        elif strat_name == 'vol_breakout':
            entry, direction = _generate_vol_breakout_signals(
                ind_1h, ind_4h, ind_d, regime_1h, idx_1h, idx_4h, idx_d)
            trades, eq = _simulate_trades(
                close_1h, high_1h, low_1h, ind_1h['atr'], entry, direction,
                stop_mult=5.0, trail_mult=4.0, target_mult=999,  # very wide for breakout explosions
                regime=regime_1h, exit_regimes={CRISIS},
                min_hold=6, max_hold=480,
                fee_rate=fee_rate, slippage_bps=slippage_bps,
                initial_capital=alloc_per, ticker=ticker,
                no_stop_bars=12, edge_override=0.45,  # 12h protection like dual_mom
            )

        elif strat_name == 'mean_reversion':
            entry = _generate_mean_reversion_signals(
                ind_1h, ind_4h, ind_d, regime_1h, idx_1h, idx_4h, idx_d)
            direction = np.ones(len(close_1h), dtype=np.int8)  # long only
            trades, eq = _simulate_trades(
                close_1h, high_1h, low_1h, ind_1h['atr'], entry, direction,
                stop_mult=2.0, trail_mult=2.0, target_mult=5.0,  # wider initial stop
                regime=regime_1h, exit_regimes={CRISIS, DOWNTREND},
                min_hold=18, max_hold=240,
                rsi=ind_1h['rsi'], rsi_exit_level=999,
                fee_rate=fee_rate, slippage_bps=slippage_bps,
                initial_capital=alloc_per, ticker=ticker,
                convex_exit=True, mean_target_vals=ema20_4h_1h,
                edge_override=0.50,  # highest conviction for oversold entries
            )
        else:
            continue

        for t in trades:
            t['strategy'] = strat_name
        strat_results[strat_name] = {'trades': trades, 'equity': eq, 'pnl': eq - alloc_per}
        all_trades.extend(trades)
        total_equity += (eq - alloc_per)

    # Kurtosis of daily returns
    if len(close_d) > 30:
        daily_rets = np.diff(np.log(close_d))
        kurt = float(pd.Series(daily_rets).kurtosis())
    else:
        kurt = 3.0

    total_return = (total_equity - capital) / capital * 100
    wins = [t for t in all_trades if t['pnl'] > 0]
    win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    losers = [t for t in all_trades if t['pnl'] <= 0]
    avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1
    payoff_ratio = avg_win / max(avg_loss, 1)
    gross_profit = sum(t['pnl'] for t in all_trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in all_trades if t['pnl'] < 0))
    profit_factor = gross_profit / max(gross_loss, 1)

    return {
        'ticker': ticker,
        'tier': get_tier(ticker)[0],
        'total_return': total_return,
        'n_trades': len(all_trades),
        'win_rate': win_rate,
        'payoff_ratio': payoff_ratio,
        'profit_factor': profit_factor,
        'equity': total_equity,
        'kurtosis': kurt,
        'strategy_breakdown': {
            k: {'n_trades': len(v['trades']), 'pnl': v['pnl']}
            for k, v in strat_results.items()
        },
        'trades': all_trades,
    }


# =============================================================================
# Portfolio Runner
# =============================================================================

def run_mtf_v2(tokens=None, capital=200_000, verbose=True,
               strategies=('dual_momentum', 'vol_breakout', 'mean_reversion')):
    """Run the 1H:4H:Daily backtest across all tokens."""
    if tokens is None:
        tokens = LIQUID_TOKENS

    print("=" * 80)
    print("MTF FAT-TAIL SWING STRATEGY v2 — 1H:4H:Daily Stack")
    print("=" * 80)
    print(f"  Trend: Daily bars | Setup: 4H bars | Entry: 1H bars")
    print(f"  Strategies: {', '.join(strategies)}")
    print(f"  Tokens: {len(tokens)} | Capital: ${capital:,.0f}")
    print(f"  Target: Payoff ratio 3:1 to 5:1 | Win rate 35-45%")
    print()

    results = {}
    t0 = time.time()

    for idx, ticker in enumerate(tokens, 1):
        if verbose:
            print(f"  [{idx}/{len(tokens)}] {ticker}...", end=' ', flush=True)

        h1_path = f'real_data/1h_cache/{ticker}_1h.parquet'
        if not os.path.exists(h1_path):
            if verbose:
                print("no 1H data")
            continue

        df_1h = pd.read_parquet(h1_path)
        if len(df_1h) < 500:
            if verbose:
                print(f"too short ({len(df_1h)} bars)")
            continue

        result = backtest_token(ticker, df_1h, capital=capital, strategies=strategies)

        if result is not None:
            results[ticker] = result
            if verbose:
                strat_str = ' | '.join(
                    f"{k[:6]}:{v['n_trades']}t/${v['pnl']:+,.0f}"
                    for k, v in result['strategy_breakdown'].items()
                )
                print(f"Ret={result['total_return']:+.1f}%  "
                      f"Trades={result['n_trades']}  "
                      f"WR={result['win_rate']:.0f}%  "
                      f"Payoff={result['payoff_ratio']:.1f}x  "
                      f"PF={result['profit_factor']:.2f}  "
                      f"Kurt={result['kurtosis']:.1f}  "
                      f"[{strat_str}]")
        else:
            if verbose:
                print("insufficient data")

    elapsed = time.time() - t0

    if not results:
        print("No results.")
        return results

    _print_portfolio_summary(results, capital, elapsed)
    return results


def _print_portfolio_summary(results, capital, elapsed):
    """Print portfolio summary."""
    print(f"\n{'='*80}")
    print("PORTFOLIO SUMMARY")
    print(f"{'='*80}")

    returns = [r['total_return'] for r in results.values()]
    win_rates = [r['win_rate'] for r in results.values() if r['n_trades'] > 0]
    payoffs = [r['payoff_ratio'] for r in results.values() if r['n_trades'] > 0]
    pfs = [r['profit_factor'] for r in results.values() if r['n_trades'] > 0]

    total_pnl = sum(r['equity'] - capital for r in results.values())
    total_trades = sum(r['n_trades'] for r in results.values())

    print(f"  Tokens tested: {len(results)}")
    print(f"  Total PnL: ${total_pnl:+,.0f}")
    print(f"  Total trades: {total_trades}")
    print(f"  Avg Return: {np.mean(returns):+.1f}%  (Median: {np.median(returns):+.1f}%)")
    if win_rates:
        print(f"  Avg Win Rate: {np.mean(win_rates):.1f}%")
    if payoffs:
        print(f"  Avg Payoff Ratio: {np.mean(payoffs):.2f}x  (target: 3-5x)")
    if pfs:
        print(f"  Avg Profit Factor: {np.mean(pfs):.2f}")
    print(f"  Profitable tokens: {sum(1 for r in returns if r > 0)}/{len(returns)}")

    # Strategy breakdown
    print(f"\n  Strategy Performance:")
    strat_totals = {}
    for r in results.values():
        for sname, sdata in r.get('strategy_breakdown', {}).items():
            if sname not in strat_totals:
                strat_totals[sname] = {'n_trades': 0, 'pnl': 0}
            strat_totals[sname]['n_trades'] += sdata['n_trades']
            strat_totals[sname]['pnl'] += sdata['pnl']

    for sname, sdata in sorted(strat_totals.items()):
        print(f"    {sname:20s}: {sdata['n_trades']:4d} trades  PnL=${sdata['pnl']:>+12,.0f}")

    # Top/bottom 5
    sorted_results = sorted(results.items(), key=lambda x: x[1]['total_return'], reverse=True)
    print(f"\n  Top 5 tokens:")
    for tk, r in sorted_results[:5]:
        print(f"    {tk:8s} Ret={r['total_return']:+.1f}%  "
              f"Trades={r['n_trades']}  WR={r['win_rate']:.0f}%  "
              f"Payoff={r['payoff_ratio']:.1f}x  Kurt={r['kurtosis']:.1f}")
    print(f"\n  Bottom 5 tokens:")
    for tk, r in sorted_results[-5:]:
        print(f"    {tk:8s} Ret={r['total_return']:+.1f}%  "
              f"Trades={r['n_trades']}  WR={r['win_rate']:.0f}%  "
              f"Payoff={r['payoff_ratio']:.1f}x  Kurt={r['kurtosis']:.1f}")

    # Fat tail tokens
    kurt_sorted = sorted(results.items(), key=lambda x: x[1]['kurtosis'], reverse=True)
    print(f"\n  Fattest tails (highest kurtosis):")
    for tk, r in kurt_sorted[:10]:
        print(f"    {tk:8s} Kurt={r['kurtosis']:.1f}  Ret={r['total_return']:+.1f}%  "
              f"Trades={r['n_trades']}  Payoff={r['payoff_ratio']:.1f}x")

    # Tier breakdown
    print(f"\n  Tier Performance:")
    for tier_num in [1, 2, 3]:
        tier_r = [r for r in results.values() if r['tier'] == tier_num]
        if tier_r:
            avg_ret = np.mean([r['total_return'] for r in tier_r])
            tier_pnl = sum(r['equity'] - capital for r in tier_r)
            print(f"    Tier {tier_num}: {len(tier_r)} tokens  "
                  f"Avg Return={avg_ret:+.1f}%  PnL=${tier_pnl:+,.0f}")

    print(f"\n  Elapsed: {elapsed:.1f}s")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokens', nargs='+')
    parser.add_argument('--capital', type=int, default=200_000)
    parser.add_argument('--strategy', nargs='+',
                        default=['dual_momentum', 'vol_breakout', 'mean_reversion'])
    args = parser.parse_args()

    tokens = args.tokens if args.tokens else None
    run_mtf_v2(tokens=tokens, capital=args.capital, strategies=tuple(args.strategy))
