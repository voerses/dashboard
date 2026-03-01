"""
Multi-Timeframe Swing Trading Strategy (MTFA)
=============================================

Architecture (from research):
  Layer 1 — REGIME  (Daily):  HMM/rules → trending / mean-reverting / crisis
  Layer 2 — SIGNAL  (Daily):  Momentum or mean-reversion signals per regime
  Layer 3 — SETUP   (4H):    Pullback/breakout confirmation on 4H chart
  Layer 4 — ENTRY   (1H):    Microstructure timing (VPIN, taker ratio, VWAP dev)

Microstructure signals ranked by academic evidence (Easley et al., Cornell):
  Tier 1 (must): VPIN, Realized Vol (HAR model), Amihud Illiquidity
  Tier 2 (high): Taker Buy Ratio, VWAP Deviation, Intraday Skewness
  Tier 3 (supplementary): Volume Herfindahl
  Drop: Intraday Kurtosis (no predictive value)

Position Sizing: Quarter-Kelly + volatility parity + liquidity tiers
  - Max 15-20 concurrent positions
  - Per-trade risk: 0.5-1% of equity
  - Max portfolio heat: 15%
  - ATR-based stops: 2x ATR(14) on 4H

Key insight: "Robustness comes from structure, not complexity" (QuantPedia)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import time
from datetime import datetime
from pathlib import Path

from liquid_universe import LIQUID_TOKENS, TIER1, TIER2, TIER3, get_tier


# =============================================================================
# Part 1: 4H Bar Aggregation from 1-min data
# =============================================================================

def _download_1h_month(symbol, year, month):
    """Download one month of 1-hour klines from Binance Vision. Much faster than 1m."""
    import urllib.request, zipfile, io
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}USDT/1h/{symbol}USDT-1h-{year}-{month:02d}.zip"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f, header=None, names=[
                    'open_time', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote', 'ignore'
                ])
                for col in ['open', 'high', 'low', 'close', 'volume',
                           'quote_volume', 'taker_buy_base', 'taker_buy_quote']:
                    df[col] = df[col].astype(float)
                df['trades'] = df['trades'].astype(int)

                if df['open_time'].iloc[0] > 1e15:
                    df['datetime'] = pd.to_datetime(df['open_time'], unit='us')
                else:
                    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')

                df.set_index('datetime', inplace=True)
                return df
    except Exception:
        return None


def aggregate_1h_to_4h(df_1h):
    """Resample 1-hour bars to 4-hour OHLCV bars."""
    if df_1h is None or len(df_1h) == 0:
        return pd.DataFrame()

    ohlcv = df_1h.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'quote_volume': 'sum',
        'trades': 'sum',
        'taker_buy_base': 'sum',
    }).dropna(subset=['open'])

    # Compute 4H taker buy ratio and VWAP
    total_vol = ohlcv['volume']
    ohlcv['taker_buy_ratio_4h'] = np.where(
        total_vol > 0, ohlcv['taker_buy_base'] / total_vol, 0.5
    )
    ohlcv['vwap_4h'] = np.where(
        total_vol > 0, (ohlcv['close'] * ohlcv['volume']) / total_vol, ohlcv['close']
    )

    return ohlcv


def build_4h_bars(ticker, start_year=2024, cache_dir='real_data/4h_cache'):
    """
    Download 1-HOUR klines from Binance Vision and aggregate to 4H bars.
    62x faster than downloading 1-min data (720 vs 44,640 rows per month).
    Also saves raw 1H bars for entry timing (3-timeframe: Daily → 4H → 1H).
    """
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs('real_data/1h_cache', exist_ok=True)
    cache_path_4h = os.path.join(cache_dir, f'{ticker}_4h.parquet')
    cache_path_1h = os.path.join('real_data/1h_cache', f'{ticker}_1h.parquet')

    # Return cached if both exist
    if os.path.exists(cache_path_4h) and os.path.exists(cache_path_1h):
        return pd.read_parquet(cache_path_4h)

    all_1h = []
    end_year = datetime.now().year

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            if year == end_year and month > datetime.now().month:
                break

            df_1h = _download_1h_month(ticker, year, month)
            if df_1h is not None and len(df_1h) > 10:
                all_1h.append(df_1h)

            time.sleep(0.05)

    if not all_1h:
        return pd.DataFrame()

    # Concat all 1H bars
    all_1h_df = pd.concat(all_1h)
    all_1h_df = all_1h_df[~all_1h_df.index.duplicated(keep='last')]
    all_1h_df.sort_index(inplace=True)

    # Save 1H bars (for entry timing layer)
    all_1h_df.to_parquet(cache_path_1h)

    # Aggregate to 4H and save (for setup confirmation layer)
    result_4h = aggregate_1h_to_4h(all_1h_df)
    result_4h.to_parquet(cache_path_4h)

    return result_4h


# =============================================================================
# Part 2: Daily Signal Layer — Regime-Adaptive
# =============================================================================

def compute_daily_indicators(df):
    """Compute all daily-level indicators for signal generation."""
    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    volume = df['volume'].values.astype(float)
    n = len(close)

    out = pd.DataFrame(index=df.index)
    out['close'] = close
    out['volume'] = volume

    # --- Trend Indicators ---
    # EMAs
    s = pd.Series(close)
    out['ema_10'] = s.ewm(span=10, adjust=False).mean().values
    out['ema_20'] = s.ewm(span=20, adjust=False).mean().values
    out['ema_50'] = s.ewm(span=50, adjust=False).mean().values

    # MACD
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    out['macd'] = (ema12 - ema26).values
    out['macd_signal'] = pd.Series(out['macd']).ewm(span=9, adjust=False).mean().values
    out['macd_hist'] = out['macd'] - out['macd_signal']

    # ADX (vectorized)
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    plus_dm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                       np.maximum(high[1:] - high[:-1], 0), 0)
    minus_dm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                        np.maximum(low[:-1] - low[1:], 0), 0)

    period = 14
    atr = np.full(n, np.nan)
    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    adx = np.full(n, np.nan)

    if n > period + 1:
        atr_val = np.mean(tr[:period])
        pdm_val = np.mean(plus_dm[:period])
        mdm_val = np.mean(minus_dm[:period])

        for i in range(period, len(tr)):
            atr_val = (atr_val * (period - 1) + tr[i]) / period
            pdm_val = (pdm_val * (period - 1) + plus_dm[i]) / period
            mdm_val = (mdm_val * (period - 1) + minus_dm[i]) / period

            atr[i + 1] = atr_val
            with np.errstate(invalid='ignore', divide='ignore'):
                plus_di[i + 1] = 100 * pdm_val / atr_val if atr_val > 0 else 0
                minus_di[i + 1] = 100 * mdm_val / atr_val if atr_val > 0 else 0

        # DX -> ADX
        dx = np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10) * 100
        # Smooth ADX
        valid_dx = dx[~np.isnan(dx)]
        if len(valid_dx) > period:
            adx_val = np.mean(valid_dx[:period])
            j = 0
            for i in range(n):
                if not np.isnan(dx[i]):
                    if j >= period:
                        adx_val = (adx_val * (period - 1) + dx[i]) / period
                        adx[i] = adx_val
                    j += 1

    out['adx'] = adx
    out['atr'] = atr

    # RSI (14)
    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0)
    losses = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gains).ewm(span=14, adjust=False).mean().values
    avg_loss = pd.Series(losses).ewm(span=14, adjust=False).mean().values
    with np.errstate(invalid='ignore', divide='ignore'):
        rs = avg_gain / np.maximum(avg_loss, 1e-10)
    out['rsi'] = 100 - 100 / (1 + rs)

    # Bollinger Bands
    bb_period = 20
    sma20 = pd.Series(close).rolling(bb_period).mean().values
    bb_std = pd.Series(close).rolling(bb_period).std().values
    out['bb_upper'] = sma20 + 2 * bb_std
    out['bb_lower'] = sma20 - 2 * bb_std
    out['bb_width'] = (out['bb_upper'] - out['bb_lower']) / np.maximum(sma20, 1e-10)
    out['bb_pct'] = (close - out['bb_lower']) / np.maximum(out['bb_upper'] - out['bb_lower'], 1e-10)

    # Donchian Channel (20)
    out['donchian_high'] = pd.Series(high).rolling(20).max().values
    out['donchian_low'] = pd.Series(low).rolling(20).min().values

    # Returns
    ret_1d = np.log(close / np.roll(close, 1))
    ret_1d[0] = 0
    out['ret_1d'] = ret_1d

    ret_5d = np.log(close / np.roll(close, 5))
    ret_5d[:5] = 0
    out['ret_5d'] = ret_5d

    ret_10d = np.log(close / np.roll(close, 10))
    ret_10d[:10] = 0
    out['ret_10d'] = ret_10d

    ret_20d = np.log(close / np.roll(close, 20))
    ret_20d[:20] = 0
    out['ret_20d'] = ret_20d

    # Realized volatility (daily — from close-to-close)
    out['vol_20d'] = pd.Series(out['ret_1d']).rolling(20).std().values * np.sqrt(252)

    # Volume metrics
    vol_sma = pd.Series(volume).rolling(20).mean().values
    out['vol_ratio'] = volume / np.maximum(vol_sma, 1e-10)

    # Garman-Klass volatility
    log_hl = np.log(high / np.maximum(low, 1e-10))
    log_co = np.log(close / np.maximum(np.roll(close, 1), 1e-10))
    gk = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    out['gk_vol'] = pd.Series(gk).rolling(20).mean().values * np.sqrt(252)

    return out


def detect_regime_fast(indicators, enriched_features=None):
    """
    Fast regime detection using rules (post-ETF optimized).
    Returns array of regime labels: 'trending_up', 'trending_down',
    'mean_reverting', 'crisis', 'accumulation'
    """
    n = len(indicators)
    regimes = np.full(n, 'mean_reverting', dtype=object)

    adx = indicators['adx'].values
    ema_20 = indicators['ema_20'].values
    ema_50 = indicators['ema_50'].values
    vol_20d = indicators['vol_20d'].values
    rsi = indicators['rsi'].values

    # Use realized vol from 1-min data if available (more accurate)
    if enriched_features is not None and 'realized_vol' in enriched_features.columns:
        rv = enriched_features['realized_vol'].reindex(indicators.index).values
        rv_median = np.nanmedian(rv[rv > 0]) if np.any(rv > 0) else 0.5
    else:
        rv = vol_20d
        rv_median = np.nanmedian(rv[~np.isnan(rv)]) if np.any(~np.isnan(rv)) else 0.5

    for i in range(50, n):
        # Crisis: vol spike > 2.5x median
        if rv[i] > 2.5 * rv_median and not np.isnan(rv[i]):
            regimes[i] = 'crisis'
        # Low vol accumulation: vol < 0.5x median
        elif rv[i] < 0.5 * rv_median and not np.isnan(rv[i]):
            regimes[i] = 'accumulation'
        # Trending: ADX > 25 and EMA alignment
        elif not np.isnan(adx[i]) and adx[i] > 25:
            if ema_20[i] > ema_50[i]:
                regimes[i] = 'trending_up'
            else:
                regimes[i] = 'trending_down'
        else:
            regimes[i] = 'mean_reverting'

    return regimes


def generate_daily_signals(indicators, regimes, enriched=None):
    """
    Generate regime-adaptive daily signals.

    In trending regime: momentum signals (MA alignment, MACD, breakout)
    In mean-reverting: reversion signals (RSI extremes, BB reversion)
    In crisis: reduce exposure (short bias if downtrend)
    In accumulation: accumulate on dips (vol compression → breakout)

    Returns signal array in [-1, 1] (short to long)
    """
    n = len(indicators)
    signals = np.zeros(n)
    signal_strength = np.zeros(n)

    close = indicators['close'].values
    ema_10 = indicators['ema_10'].values
    ema_20 = indicators['ema_20'].values
    ema_50 = indicators['ema_50'].values
    macd_hist = indicators['macd_hist'].values
    rsi = indicators['rsi'].values
    bb_pct = indicators['bb_pct'].values
    adx = indicators['adx'].values
    vol_ratio = indicators['vol_ratio'].values
    donchian_high = indicators['donchian_high'].values
    donchian_low = indicators['donchian_low'].values

    # Microstructure overlays (from 1-min aggregated data)
    has_micro = enriched is not None
    if has_micro:
        vpin = enriched['vpin'].reindex(indicators.index).fillna(0.5).values
        taker = enriched['taker_buy_ratio'].reindex(indicators.index).fillna(0.5).values
        amihud = enriched['amihud_1m'].reindex(indicators.index).fillna(0).values
        rv = enriched['realized_vol'].reindex(indicators.index).fillna(0).values
        skew = enriched['intraday_skew'].reindex(indicators.index).fillna(0).values
    else:
        vpin = np.full(n, 0.5)
        taker = np.full(n, 0.5)
        amihud = np.zeros(n)
        rv = np.zeros(n)
        skew = np.zeros(n)

    for i in range(50, n):
        regime = regimes[i]
        sig = 0.0
        strength = 0.0

        if regime == 'trending_up':
            # Post-ETF: trending regimes have NEGATIVE edge for both momentum and pullback
            # Data shows: skip trending regimes, or trade very selectively
            sig = 0
            strength = 0

            # Only exception: extreme pullback in strong uptrend (mean-reversion within trend)
            if rsi[i] < 30 and bb_pct[i] < 0.1:
                sig = 0.6  # Extreme oversold in uptrend = strong buy
                strength = 0.5

        elif regime == 'trending_down':
            # Post-ETF: don't short crypto, and don't try to catch falling knives
            sig = 0
            strength = 0

            # Only exception: extreme capitulation (RSI < 20, below BB)
            if rsi[i] < 20 and bb_pct[i] < 0:
                sig = 0.7  # Capitulation buy
                strength = 0.4

        elif regime == 'mean_reverting':
            # Mean reversion signals
            votes = 0
            total = 0

            # RSI extremes
            if rsi[i] < 30:
                votes += 1.5  # Oversold → buy
            elif rsi[i] > 70:
                votes -= 1.5  # Overbought → sell
            total += 1.5

            # Bollinger Band reversion
            if bb_pct[i] < 0.05:
                votes += 1.0  # Below lower band → buy
            elif bb_pct[i] > 0.95:
                votes -= 1.0  # Above upper band → sell
            total += 1.0

            # Volume confirmation (reversion more reliable on high volume)
            if vol_ratio[i] > 1.5:
                votes *= 1.3
            total += 0

            sig = np.clip(votes / total, -1, 1)
            strength = 0.7  # Mean reversion signals are moderate confidence

        elif regime == 'crisis':
            # Reduce exposure, slight short bias
            sig = -0.3  # Defensive
            strength = 0.3

            # Exception: extreme oversold in crisis = contrarian buy
            if rsi[i] < 20 and bb_pct[i] < 0:
                sig = 0.5  # Contrarian long
                strength = 0.4

        elif regime == 'accumulation':
            # Vol compression → potential breakout
            sig = 0.3  # Slight long bias (breakouts tend upward in crypto)
            strength = 0.5

            # Bollinger squeeze (width < 20-day min)
            if i > 20:
                bb_width = indicators['bb_width'].values
                if bb_width[i] < np.nanmin(bb_width[max(0, i-20):i]):
                    sig = 0.6  # Strong squeeze
                    strength = 0.7

        # --- Microstructure Adjustments ---
        if has_micro:
            # VPIN adjustment: High VPIN (>0.7) = informed trading, reduce size
            if vpin[i] > 0.7:
                strength *= 0.6  # Reduce confidence when informed traders active

            # Taker buy ratio confirmation
            if sig > 0 and taker[i] > 0.55:
                strength *= 1.2  # Buy signal confirmed by taker flow
            elif sig < 0 and taker[i] < 0.45:
                strength *= 1.2  # Sell signal confirmed

            # Amihud illiquidity penalty
            amihud_median = np.nanmedian(amihud[max(0, i-60):i]) if i > 60 else 0
            if amihud[i] > 3 * amihud_median and amihud_median > 0:
                strength *= 0.5  # Very illiquid, reduce size

            # Skewness signal (negative skew tokens outperform — Amaya et al.)
            if skew[i] < -1.0:
                sig += 0.1  # Slight positive tilt for negative skew

        signals[i] = np.clip(sig, -1, 1)
        signal_strength[i] = np.clip(strength, 0, 1)

    return signals, signal_strength


# =============================================================================
# Part 3: 4H Setup Confirmation Layer
# =============================================================================

def compute_4h_indicators(df_4h):
    """Compute indicators on 4H bars for setup confirmation."""
    if df_4h is None or len(df_4h) < 50:
        return None

    close = df_4h['close'].values.astype(float)
    high = df_4h['high'].values.astype(float)
    low = df_4h['low'].values.astype(float)

    out = pd.DataFrame(index=df_4h.index)
    out['close'] = close

    # 4H EMAs (20 and 50 period = ~3.3 and ~8.3 days)
    s = pd.Series(close)
    out['ema_20_4h'] = s.ewm(span=20, adjust=False).mean().values
    out['ema_50_4h'] = s.ewm(span=50, adjust=False).mean().values

    # 4H RSI
    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0)
    losses = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gains).ewm(span=14, adjust=False).mean().values
    avg_loss = pd.Series(losses).ewm(span=14, adjust=False).mean().values
    with np.errstate(invalid='ignore', divide='ignore'):
        rs = avg_gain / np.maximum(avg_loss, 1e-10)
    out['rsi_4h'] = 100 - 100 / (1 + rs)

    # 4H MACD
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    out['macd_4h'] = (ema12 - ema26).values
    out['macd_signal_4h'] = pd.Series(out['macd_4h']).ewm(span=9, adjust=False).mean().values
    out['macd_hist_4h'] = out['macd_4h'] - out['macd_signal_4h']

    # 4H ATR for stop-loss
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    atr = np.full(len(close), np.nan)
    if len(tr) >= 14:
        atr_val = np.mean(tr[:14])
        for i in range(14, len(tr)):
            atr_val = (atr_val * 13 + tr[i]) / 14
            atr[i + 1] = atr_val
    out['atr_4h'] = atr

    # Fibonacci retracement levels from recent swing
    for i in range(50, len(close)):
        swing_high = np.max(high[max(0, i-50):i+1])
        swing_low = np.min(low[max(0, i-50):i+1])
        fib_range = swing_high - swing_low
        # 0.618 retracement from high
        out.loc[out.index[i], 'fib_618'] = swing_high - 0.618 * fib_range

    return out


def check_4h_setup(daily_signal, daily_regime, indicators_4h, date):
    """
    Check if 4H chart confirms the daily signal.
    Returns setup_score (0-1) and entry parameters.
    """
    if indicators_4h is None or len(indicators_4h) == 0:
        return 0.7, {}  # No 4H data → use daily signal at reduced confidence

    # Find the most recent 4H bar before/at this date
    # (6 bars per day)
    day_end = pd.Timestamp(date) + pd.Timedelta(hours=23, minutes=59)
    day_start = pd.Timestamp(date)
    mask = (indicators_4h.index >= day_start) & (indicators_4h.index <= day_end)
    day_bars = indicators_4h.loc[mask]

    if len(day_bars) == 0:
        # Try the last available bar
        prior = indicators_4h[indicators_4h.index <= day_end]
        if len(prior) == 0:
            return 0.7, {}
        day_bars = prior.iloc[-6:]  # Last ~1 day of 4H bars

    last_bar = day_bars.iloc[-1]
    setup_score = 0.0
    entry_params = {}

    if daily_signal > 0:  # Looking for long setup
        checks = 0
        passed = 0

        # Check 1: 4H EMA alignment (20 > 50)
        if 'ema_20_4h' in last_bar and 'ema_50_4h' in last_bar:
            checks += 1
            if last_bar['ema_20_4h'] > last_bar['ema_50_4h']:
                passed += 1

        # Check 2: 4H RSI in buy zone (not overbought)
        if 'rsi_4h' in last_bar:
            checks += 1
            if 35 < last_bar['rsi_4h'] < 65:
                passed += 1  # Healthy zone
            elif last_bar['rsi_4h'] <= 35:
                passed += 1.5  # Oversold = strong setup

        # Check 3: MACD histogram positive or turning
        if 'macd_hist_4h' in last_bar:
            checks += 1
            if last_bar['macd_hist_4h'] > 0:
                passed += 1
            elif len(day_bars) > 1 and last_bar['macd_hist_4h'] > day_bars.iloc[-2].get('macd_hist_4h', 0):
                passed += 0.5  # Turning positive

        # Check 4: Pullback to 4H EMA 20 (high-probability)
        if 'ema_20_4h' in last_bar and 'close' in last_bar:
            checks += 1
            dist = abs(last_bar['close'] - last_bar['ema_20_4h']) / max(last_bar['ema_20_4h'], 1e-10)
            if dist < 0.015:  # Within 1.5%
                passed += 1.5  # Premium signal

        # Check 5: Near Fib 0.618 level
        if 'fib_618' in last_bar and 'close' in last_bar:
            checks += 0.5
            fib_dist = abs(last_bar['close'] - last_bar['fib_618']) / max(last_bar['fib_618'], 1e-10)
            if fib_dist < 0.02:
                passed += 0.5

        setup_score = passed / max(checks, 1)

        # Entry parameters
        if 'atr_4h' in last_bar and not np.isnan(last_bar['atr_4h']):
            entry_params['stop_distance'] = 3.0 * last_bar['atr_4h']  # 3x ATR stop
            entry_params['target_distance'] = 4.5 * last_bar['atr_4h']  # 4.5x ATR target (1.5 R:R)
        if 'vwap_4h' in last_bar:
            entry_params['limit_price'] = last_bar['vwap_4h']  # Enter at VWAP

    elif daily_signal < 0:  # Looking for short setup
        checks = 0
        passed = 0

        if 'ema_20_4h' in last_bar and 'ema_50_4h' in last_bar:
            checks += 1
            if last_bar['ema_20_4h'] < last_bar['ema_50_4h']:
                passed += 1

        if 'rsi_4h' in last_bar:
            checks += 1
            if last_bar['rsi_4h'] > 60:
                passed += 1  # Overbought zone for shorts

        if 'macd_hist_4h' in last_bar:
            checks += 1
            if last_bar['macd_hist_4h'] < 0:
                passed += 1

        setup_score = passed / max(checks, 1)

        if 'atr_4h' in last_bar and not np.isnan(last_bar['atr_4h']):
            entry_params['stop_distance'] = 2.0 * last_bar['atr_4h']
            entry_params['target_distance'] = 3.0 * last_bar['atr_4h']

    return np.clip(setup_score, 0, 1), entry_params


# =============================================================================
# Part 4: Position Sizing — Quarter-Kelly + Volatility Parity
# =============================================================================

def compute_position_size(ticker, signal, strength, setup_score, equity,
                          vol_20d, portfolio_heat, max_heat=0.15):
    """
    Fractional Kelly + vol parity + liquidity tier constraints.
    Returns position size in USD.
    """
    tier, tier_mult = get_tier(ticker)
    if tier == 0:
        return 0

    # Base: half-Kelly for T1 (most liquid), quarter-Kelly for T2/T3
    edge = abs(signal) * strength * setup_score
    if edge < 0.1:  # Minimum edge threshold
        return 0

    # T1 gets half-Kelly (sufficient liquidity), T2/T3 quarter-Kelly
    kelly_mult = 0.5 if tier == 1 else 0.25
    kelly_fraction = kelly_mult * edge

    # Volatility parity: target 1% daily risk per position
    target_daily_risk = 0.01
    if vol_20d > 0 and not np.isnan(vol_20d):
        daily_vol = vol_20d / np.sqrt(252)
        vol_adjusted = target_daily_risk / max(daily_vol, 0.005)
    else:
        vol_adjusted = 1.0

    # Combine
    raw_size = equity * kelly_fraction * vol_adjusted

    # Tier cap — more generous for liquid tokens
    tier_caps = {1: 0.08, 2: 0.05, 3: 0.025}  # Max % of equity
    max_position = equity * tier_caps.get(tier, 0.01)
    raw_size = min(raw_size, max_position)

    # Per-trade risk cap: max 2% of equity
    raw_size = min(raw_size, equity * 0.02)

    # Portfolio heat check (total open risk)
    remaining_heat = max(max_heat - portfolio_heat, 0)
    if remaining_heat <= 0:
        return 0

    # Scale by remaining heat budget
    heat_scaled = min(raw_size, equity * remaining_heat)

    return max(heat_scaled, 0)


# =============================================================================
# Part 5: Multi-Timeframe Backtest Engine
# =============================================================================

class MTFSwingBacktest:
    """
    Multi-Timeframe Swing Trading Backtest Engine.

    Simulates the full MTFA pipeline:
      Daily: regime detection + signal generation
      4H: setup confirmation
      Entry: microstructure-informed position sizing
      Management: ATR-based stops, trailing profit targets
    """

    def __init__(self, capital=200_000, fee_rate=0.001, slippage_bps=5,
                 min_hold_days=3, max_hold_days=30,
                 max_positions=15, max_heat=0.15):
        self.capital = capital
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps
        self.min_hold_days = min_hold_days
        self.max_hold_days = max_hold_days
        self.max_positions = max_positions
        self.max_heat = max_heat

    def run(self, daily_data, enriched_data=None, data_4h=None, ticker='BTC'):
        """
        Run backtest for a single token.

        Args:
            daily_data: DataFrame with OHLCV (date index)
            enriched_data: DataFrame with 1-min derived features (optional)
            data_4h: DataFrame with 4H bars + indicators (optional)
            ticker: Token ticker for position sizing

        Returns:
            dict with performance metrics and trade log
        """
        self._current_ticker = ticker
        if len(daily_data) < 100:
            return None

        # Step 1: Compute daily indicators
        indicators = compute_daily_indicators(daily_data)

        # Step 2: Detect regime
        regimes = detect_regime_fast(indicators, enriched_data)

        # Step 3: Generate daily signals
        signals, strengths = generate_daily_signals(indicators, regimes, enriched_data)

        # Step 4: Compute 4H indicators (if available)
        indicators_4h = compute_4h_indicators(data_4h) if data_4h is not None else None

        # Step 5: Simulate trading
        equity = self.capital
        position = 0  # Current position in units
        entry_price = 0
        entry_date = None
        entry_regime = 'mean_reverting'
        stop_price = 0
        target_price = 0
        trades = []
        equity_curve = np.zeros(len(indicators))
        regime_log = []

        for i in range(50, len(indicators)):
            date = indicators.index[i]
            close = indicators['close'].iloc[i]
            high_today = daily_data['high'].iloc[i] if 'high' in daily_data.columns else close
            low_today = daily_data['low'].iloc[i] if 'low' in daily_data.columns else close

            # Update equity
            if position != 0:
                pnl = position * (close - entry_price)
                current_equity = equity + pnl
            else:
                current_equity = equity

            equity_curve[i] = current_equity

            # --- Exit Logic ---
            if position != 0:
                hold_days = (date - entry_date).days if entry_date else 0

                exit_signal = False
                exit_reason = ''

                # Stop-loss hit (check against intraday low/high)
                if position > 0 and low_today <= stop_price:
                    exit_signal = True
                    exit_reason = 'stop_loss'
                    exit_price = stop_price
                elif position < 0 and high_today >= stop_price:
                    exit_signal = True
                    exit_reason = 'stop_loss'
                    exit_price = stop_price

                # Target hit
                elif position > 0 and high_today >= target_price and target_price > 0:
                    exit_signal = True
                    exit_reason = 'target'
                    exit_price = target_price
                elif position < 0 and low_today <= target_price and target_price > 0:
                    exit_signal = True
                    exit_reason = 'target'
                    exit_price = target_price

                # Signal reversal (after min hold)
                elif hold_days >= self.min_hold_days:
                    if position > 0 and signals[i] < -0.3:
                        exit_signal = True
                        exit_reason = 'signal_reversal'
                        exit_price = close
                    elif position < 0 and signals[i] > 0.3:
                        exit_signal = True
                        exit_reason = 'signal_reversal'
                        exit_price = close

                # Max hold time
                elif hold_days >= self.max_hold_days:
                    exit_signal = True
                    exit_reason = 'max_hold'
                    exit_price = close

                # Regime change to unfavorable → exit
                elif regimes[i] in ('crisis', 'trending_down') and position > 0:
                    exit_signal = True
                    exit_reason = 'regime_exit'
                    exit_price = close

                if exit_signal:
                    # Apply slippage and fees
                    slip = exit_price * self.slippage_bps / 10000
                    if position > 0:
                        exit_price -= slip
                    else:
                        exit_price += slip

                    pnl = position * (exit_price - entry_price)
                    fee = abs(position) * exit_price * self.fee_rate
                    net_pnl = pnl - fee

                    equity += net_pnl
                    trades.append({
                        'entry_date': entry_date,
                        'exit_date': date,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'position': position,
                        'pnl': net_pnl,
                        'return_pct': net_pnl / (abs(position) * entry_price) * 100,
                        'hold_days': hold_days,
                        'exit_reason': exit_reason,
                        'regime': entry_regime,  # Log entry regime, not exit
                    })
                    position = 0
                    entry_price = 0
                    stop_price = 0
                    target_price = 0

                # Trailing stop: move stop to breakeven after 1.5x ATR move
                elif position > 0:
                    atr = indicators['atr'].iloc[i]
                    if not np.isnan(atr) and close > entry_price + 1.5 * atr:
                        stop_price = max(stop_price, entry_price)  # Breakeven stop

            # --- Entry Logic ---
            if position == 0 and abs(signals[i]) > 0.2 and strengths[i] > 0.3:
                # Check 4H setup
                setup_score, entry_params = check_4h_setup(
                    signals[i], regimes[i], indicators_4h, date
                )

                # Only enter if setup confirms (score > 0.4)
                if setup_score < 0.4:
                    continue

                # Position sizing
                portfolio_heat = 0  # TODO: track across portfolio
                vol_20d = indicators['vol_20d'].iloc[i]
                pos_usd = compute_position_size(
                    self._current_ticker,
                    signals[i], strengths[i], setup_score, equity,
                    vol_20d, portfolio_heat, self.max_heat
                )

                if pos_usd < 100:  # Minimum position size
                    continue

                # Determine direction
                direction = 1 if signals[i] > 0 else -1

                # Entry price with slippage
                entry_price = close
                slip = entry_price * self.slippage_bps / 10000
                if direction > 0:
                    entry_price += slip
                else:
                    entry_price -= slip

                # Position size in units
                position = direction * pos_usd / entry_price

                # Fee
                fee = abs(position) * entry_price * self.fee_rate
                equity -= fee

                entry_date = date
                entry_regime = regimes[i]

                # Stop and target — wider for crypto (3x ATR stop, 4.5x ATR target = 1.5 R:R)
                atr = indicators['atr'].iloc[i]
                stop_dist = entry_params.get('stop_distance', 3.0 * atr if not np.isnan(atr) else entry_price * 0.07)
                target_dist = entry_params.get('target_distance', 4.5 * atr if not np.isnan(atr) else entry_price * 0.12)

                if direction > 0:
                    stop_price = entry_price - stop_dist
                    target_price = entry_price + target_dist
                else:
                    stop_price = entry_price + stop_dist
                    target_price = entry_price - target_dist

        # Close any remaining position
        if position != 0:
            close_price = indicators['close'].iloc[-1]
            pnl = position * (close_price - entry_price)
            fee = abs(position) * close_price * self.fee_rate
            equity += pnl - fee

        # Compute metrics
        equity_curve[equity_curve == 0] = self.capital
        total_return = (equity - self.capital) / self.capital * 100

        if not trades:
            return {
                'total_return': total_return,
                'sharpe': 0,
                'n_trades': 0,
                'win_rate': 0,
                'avg_hold_days': 0,
                'max_drawdown': 0,
                'trades': [],
                'equity_curve': equity_curve,
                'regimes': regimes,
            }

        # Daily returns for Sharpe
        ec_valid = equity_curve[equity_curve > 0]
        if len(ec_valid) > 1:
            daily_rets = np.diff(ec_valid) / ec_valid[:-1]
            sharpe = np.mean(daily_rets) / max(np.std(daily_rets), 1e-10) * np.sqrt(252)
        else:
            sharpe = 0

        # Win rate
        wins = [t for t in trades if t['pnl'] > 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0

        # Max drawdown
        peak = np.maximum.accumulate(ec_valid)
        dd = (ec_valid - peak) / peak
        max_dd = dd.min() * 100

        # Average hold time
        avg_hold = np.mean([t['hold_days'] for t in trades])

        # Profit factor
        gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
        profit_factor = gross_profit / max(gross_loss, 1) if gross_loss > 0 else float('inf')

        # Regime breakdown
        regime_counts = {}
        for t in trades:
            r = t['regime']
            if r not in regime_counts:
                regime_counts[r] = {'n': 0, 'pnl': 0, 'wins': 0}
            regime_counts[r]['n'] += 1
            regime_counts[r]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                regime_counts[r]['wins'] += 1

        return {
            'total_return': total_return,
            'sharpe': sharpe,
            'n_trades': len(trades),
            'win_rate': win_rate,
            'avg_hold_days': avg_hold,
            'max_drawdown': max_dd,
            'profit_factor': profit_factor,
            'equity': equity,
            'trades': trades,
            'equity_curve': equity_curve,
            'regimes': regimes,
            'regime_breakdown': regime_counts,
        }


# =============================================================================
# Part 6: Portfolio-Level Runner
# =============================================================================

def run_mtf_backtest(tokens=None, capital=200_000, start_year=2024,
                     use_4h=True, use_enriched=True, verbose=True):
    """
    Run the full multi-timeframe swing backtest across all liquid tokens.
    """
    if tokens is None:
        tokens = LIQUID_TOKENS

    print("=" * 80)
    print("MULTI-TIMEFRAME SWING TRADING BACKTEST")
    print("=" * 80)
    print(f"Strategy: Regime-Adaptive MTFA Swing (Daily signal → 4H setup → Entry)")
    print(f"Tokens: {len(tokens)}")
    print(f"Capital: ${capital:,.0f}")
    print(f"Period: {start_year} → present")
    print(f"4H data: {'yes' if use_4h else 'no'}")
    print(f"Microstructure: {'yes' if use_enriched else 'no'}")
    print()

    # Load enriched data
    enriched_parquet = 'real_data/all_tokens_enriched.parquet'
    enriched_df = None
    if use_enriched and os.path.exists(enriched_parquet):
        enriched_df = pd.read_parquet(enriched_parquet)
        if 'date' in enriched_df.columns:
            enriched_df['date'] = pd.to_datetime(enriched_df['date'])
            enriched_df.set_index('date', inplace=True)

    engine = MTFSwingBacktest(
        capital=capital,
        fee_rate=0.001,
        slippage_bps=5,
        min_hold_days=3,
        max_hold_days=30,
        max_positions=15,
        max_heat=0.15,
    )

    results = {}
    t0 = time.time()

    for idx, ticker in enumerate(tokens, 1):
        if verbose:
            print(f"  [{idx}/{len(tokens)}] {ticker}...", end=' ', flush=True)

        # Load daily data
        daily_path = f'real_data/{ticker}_daily.csv'
        if not os.path.exists(daily_path):
            if verbose:
                print("no data")
            continue

        df_daily = pd.read_csv(daily_path, index_col=0, parse_dates=True)

        # Filter to start_year+
        start_date = f'{start_year}-01-01'
        df_daily = df_daily[df_daily.index >= start_date]
        if len(df_daily) < 100:
            if verbose:
                print(f"too short ({len(df_daily)} days)")
            continue

        # Get enriched features for this token
        token_enriched = None
        if enriched_df is not None and 'ticker' in enriched_df.columns:
            token_enriched = enriched_df[enriched_df['ticker'] == ticker].copy()
            if 'ticker' in token_enriched.columns:
                token_enriched = token_enriched.drop(columns=['ticker'])
        elif enriched_df is not None:
            # Maybe already filtered
            token_enriched = enriched_df

        # Load 4H data
        data_4h = None
        if use_4h:
            cache_4h = f'real_data/4h_cache/{ticker}_4h.parquet'
            if os.path.exists(cache_4h):
                data_4h = pd.read_parquet(cache_4h)

        # Run backtest
        result = engine.run(df_daily, token_enriched, data_4h, ticker=ticker)

        if result is not None:
            result['ticker'] = ticker
            result['tier'] = get_tier(ticker)[0]
            results[ticker] = result

            if verbose:
                print(f"Sharpe={result['sharpe']:.2f}  "
                      f"Return={result['total_return']:.1f}%  "
                      f"Trades={result['n_trades']}  "
                      f"WinRate={result['win_rate']:.0f}%  "
                      f"MaxDD={result['max_drawdown']:.1f}%")
        else:
            if verbose:
                print("insufficient data")

    elapsed = time.time() - t0

    # Summary
    if results:
        print(f"\n{'='*80}")
        print("PORTFOLIO SUMMARY")
        print(f"{'='*80}")

        sharpes = [r['sharpe'] for r in results.values()]
        returns = [r['total_return'] for r in results.values()]
        win_rates = [r['win_rate'] for r in results.values() if r['n_trades'] > 0]

        print(f"  Tokens tested: {len(results)}")
        print(f"  Avg Sharpe: {np.mean(sharpes):.3f}  (Median: {np.median(sharpes):.3f})")
        print(f"  Avg Return: {np.mean(returns):.1f}%  (Median: {np.median(returns):.1f}%)")
        print(f"  Avg Win Rate: {np.mean(win_rates):.1f}%")
        print(f"  Profitable: {sum(1 for r in returns if r > 0)}/{len(returns)}")

        # Best/worst
        sorted_by_sharpe = sorted(results.items(), key=lambda x: x[1]['sharpe'], reverse=True)
        print(f"\n  Top 5 by Sharpe:")
        for ticker, r in sorted_by_sharpe[:5]:
            print(f"    {ticker:8s} Sharpe={r['sharpe']:.2f}  Ret={r['total_return']:.1f}%  "
                  f"Trades={r['n_trades']}  WR={r['win_rate']:.0f}%")

        print(f"\n  Bottom 5 by Sharpe:")
        for ticker, r in sorted_by_sharpe[-5:]:
            print(f"    {ticker:8s} Sharpe={r['sharpe']:.2f}  Ret={r['total_return']:.1f}%  "
                  f"Trades={r['n_trades']}  WR={r['win_rate']:.0f}%")

        # Regime breakdown (aggregate)
        print(f"\n  Regime Performance:")
        all_regime = {}
        for r in results.values():
            for regime, stats in r.get('regime_breakdown', {}).items():
                if regime not in all_regime:
                    all_regime[regime] = {'n': 0, 'pnl': 0, 'wins': 0}
                all_regime[regime]['n'] += stats['n']
                all_regime[regime]['pnl'] += stats['pnl']
                all_regime[regime]['wins'] += stats['wins']

        for regime, stats in sorted(all_regime.items()):
            wr = stats['wins'] / stats['n'] * 100 if stats['n'] > 0 else 0
            print(f"    {regime:20s}: {stats['n']:4d} trades  "
                  f"PnL=${stats['pnl']:>10,.0f}  WR={wr:.0f}%")

        # Tier breakdown
        print(f"\n  Tier Performance:")
        for tier_num in [1, 2, 3]:
            tier_results = [r for r in results.values() if r.get('tier') == tier_num]
            if tier_results:
                avg_s = np.mean([r['sharpe'] for r in tier_results])
                avg_r = np.mean([r['total_return'] for r in tier_results])
                print(f"    Tier {tier_num}: {len(tier_results)} tokens  "
                      f"Avg Sharpe={avg_s:.2f}  Avg Return={avg_r:.1f}%")

        print(f"\n  Elapsed: {elapsed:.1f}s")

    return results


# =============================================================================
# Part 7: Data Availability Check
# =============================================================================

def check_data_readiness():
    """Check what data we have and what's missing for the full MTFA strategy."""
    print("=" * 80)
    print("DATA READINESS CHECK for Multi-Timeframe Swing Strategy")
    print("=" * 80)

    # Daily data
    daily_count = 0
    for t in LIQUID_TOKENS:
        if os.path.exists(f'real_data/{t}_daily.csv'):
            daily_count += 1
    print(f"\n[1] Daily OHLCV: {daily_count}/{len(LIQUID_TOKENS)} tokens")

    # Enriched parquet
    enriched_path = 'real_data/all_tokens_enriched.parquet'
    if os.path.exists(enriched_path):
        df = pd.read_parquet(enriched_path)
        n_tokens = df['ticker'].nunique() if 'ticker' in df.columns else 0
        feature_cols = [c for c in df.columns if c not in
                       ['date', 'open', 'high', 'low', 'close', 'volume', 'ticker']]
        print(f"[2] Enriched daily features: {n_tokens} tokens, {len(feature_cols)} features")
        print(f"    Features: {feature_cols}")
    else:
        print("[2] Enriched daily features: MISSING — run fetch_1m_data.py first")

    # 1m cache
    cache_dir = 'real_data/1m_cache'
    if os.path.exists(cache_dir):
        cached = len([f for f in os.listdir(cache_dir) if f.endswith('.parquet')])
        print(f"[3] 1-min feature cache: {cached} tokens")
    else:
        print("[3] 1-min feature cache: MISSING")

    # 4H bars
    cache_4h = 'real_data/4h_cache'
    if os.path.exists(cache_4h):
        cached_4h = len([f for f in os.listdir(cache_4h) if f.endswith('.parquet')])
        print(f"[4] 4H bars: {cached_4h}/{len(LIQUID_TOKENS)} tokens")
    else:
        cached_4h = 0
        print(f"[4] 4H bars: MISSING — need to download")

    # Summary
    print(f"\n{'='*80}")
    print("READINESS SUMMARY")
    print(f"{'='*80}")

    ready_daily = daily_count >= 50
    ready_enriched = os.path.exists(enriched_path)
    ready_4h = cached_4h >= 40

    print(f"  Daily signals layer:  {'READY' if ready_daily else 'NEED DATA'}")
    print(f"  Microstructure layer: {'READY' if ready_enriched else 'NEED fetch_1m_data.py'}")
    print(f"  4H setup layer:      {'READY' if ready_4h else 'NEED to build 4H bars'}")
    print()

    if not ready_4h:
        print("  ACTION NEEDED: Build 4H bars from 1-min data")
        print("  Run: python mtf_swing_strategy.py --build-4h")
        print("  This re-downloads 1-min data and aggregates to 4H bars.")
        print(f"  Estimated time: ~{len(LIQUID_TOKENS) * 2} minutes")

    can_run = ready_daily
    print(f"\n  Can run backtest now (daily-only mode): {'YES' if can_run else 'NO'}")
    print(f"  Can run backtest (full MTFA mode):       {'YES' if ready_daily and ready_enriched and ready_4h else 'NO'}")

    return {
        'daily': ready_daily,
        'enriched': ready_enriched,
        '4h': ready_4h,
        'can_run_basic': can_run,
        'can_run_full': ready_daily and ready_enriched and ready_4h,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Multi-Timeframe Swing Strategy')
    parser.add_argument('--check', action='store_true', help='Check data readiness')
    parser.add_argument('--build-4h', action='store_true', help='Build 4H bars from 1-min data')
    parser.add_argument('--run', action='store_true', help='Run backtest')
    parser.add_argument('--tokens', nargs='+', help='Specific tokens')
    parser.add_argument('--no-4h', action='store_true', help='Skip 4H setup layer')
    parser.add_argument('--no-enriched', action='store_true', help='Skip microstructure features')
    parser.add_argument('--start-year', type=int, default=2024, help='Start year')
    parser.add_argument('--capital', type=int, default=200_000, help='Starting capital')
    args = parser.parse_args()

    if args.check:
        check_data_readiness()

    elif args.build_4h:
        from fetch_1m_data import BINANCE_VISION_TOKENS
        print(f"Building 4H bars for {len(BINANCE_VISION_TOKENS)} tokens...")
        os.makedirs('real_data/4h_cache', exist_ok=True)

        for i, ticker in enumerate(BINANCE_VISION_TOKENS, 1):
            print(f"  [{i}/{len(BINANCE_VISION_TOKENS)}] {ticker}...", end=' ', flush=True)
            cache_path = f'real_data/4h_cache/{ticker}_4h.parquet'
            if os.path.exists(cache_path):
                df = pd.read_parquet(cache_path)
                print(f"cached ({len(df)} bars)")
                continue
            df = build_4h_bars(ticker, start_year=args.start_year)
            if len(df) > 0:
                print(f"{len(df)} bars")
            else:
                print("no data")

    elif args.run:
        tokens = args.tokens if args.tokens else None
        results = run_mtf_backtest(
            tokens=tokens,
            capital=args.capital,
            start_year=args.start_year,
            use_4h=not args.no_4h,
            use_enriched=not args.no_enriched,
        )

    else:
        # Default: check then run
        readiness = check_data_readiness()
        print()
        if readiness['can_run_basic']:
            print("Running backtest (daily + enriched, no 4H)...")
            results = run_mtf_backtest(
                use_4h=False,
                use_enriched=readiness['enriched'],
                start_year=args.start_year,
            )
