"""
Comprehensive technical indicator library - 109 indicators across 6 categories.
"""
import numpy as np
import pandas as pd

def compute_all_indicators(df):
    """Compute 109 technical indicators from OHLCV data."""
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)
    o = df['open'].values.astype(float)
    v = df['volume'].values.astype(float)
    n = len(c)

    indicators = pd.DataFrame(index=df.index)

    # ===== MOMENTUM (25 indicators) =====
    for p in [7, 14, 21]:
        indicators[f'rsi_{p}'] = _rsi(c, p)
        indicators[f'roc_{p}'] = _roc(c, p)
        indicators[f'momentum_{p}'] = c / np.roll(c, p) - 1

    for fast, slow in [(12, 26), (8, 21), (5, 13)]:
        ema_fast = _ema(c, fast)
        ema_slow = _ema(c, slow)
        macd = ema_fast - ema_slow
        signal = _ema(macd, 9)
        indicators[f'macd_{fast}_{slow}'] = macd
        indicators[f'macd_signal_{fast}_{slow}'] = signal
        indicators[f'macd_hist_{fast}_{slow}'] = macd - signal

    indicators['cci_14'] = _cci(h, l, c, 14)
    indicators['cci_20'] = _cci(h, l, c, 20)
    indicators['williams_r_14'] = _williams_r(h, l, c, 14)
    indicators['stoch_k_14'] = _stochastic_k(h, l, c, 14)
    indicators['stoch_d_14'] = _ema(_stochastic_k(h, l, c, 14), 3)
    indicators['tsi'] = _tsi(c)
    indicators['ultimate_osc'] = _ultimate_oscillator(h, l, c)

    # ===== TREND (20 indicators) =====
    for p in [10, 20, 50, 100, 200]:
        indicators[f'sma_{p}'] = _sma(c, p)
        indicators[f'ema_{p}'] = _ema(c, p)

    indicators['adx_14'] = _adx(h, l, c, 14)
    indicators['plus_di'] = _plus_di(h, l, c, 14)
    indicators['minus_di'] = _minus_di(h, l, c, 14)
    indicators['di_diff'] = indicators['plus_di'] - indicators['minus_di']

    # Ichimoku
    indicators['ichimoku_conv'] = (_rolling_max(h, 9) + _rolling_min(l, 9)) / 2
    indicators['ichimoku_base'] = (_rolling_max(h, 26) + _rolling_min(l, 26)) / 2
    indicators['ichimoku_span_a'] = (indicators['ichimoku_conv'] + indicators['ichimoku_base']) / 2
    indicators['ichimoku_span_b'] = (_rolling_max(h, 52) + _rolling_min(l, 52)) / 2
    indicators['ichimoku_cloud_dist'] = (c - indicators['ichimoku_span_a']) / c

    # Price relative to MAs
    for p in [20, 50, 200]:
        indicators[f'price_to_sma_{p}'] = c / _sma(c, p) - 1

    # ===== VOLATILITY (20 indicators) =====
    for p in [14, 20, 30]:
        indicators[f'atr_{p}'] = _atr(h, l, c, p)
        indicators[f'atr_pct_{p}'] = _atr(h, l, c, p) / c

    for p in [20]:
        sma = _sma(c, p)
        std = _rolling_std(c, p)
        indicators[f'bb_upper_{p}'] = sma + 2 * std
        indicators[f'bb_lower_{p}'] = sma - 2 * std
        indicators[f'bb_width_{p}'] = 4 * std / sma
        indicators[f'bb_pct_{p}'] = (c - (sma - 2 * std)) / (4 * std + 1e-10)

    for p in [10, 20, 30, 60]:
        indicators[f'realized_vol_{p}'] = _rolling_std(np.log(c / np.roll(c, 1)), p) * np.sqrt(365)

    indicators['garman_klass_vol'] = _garman_klass(o, h, l, c, 20)
    indicators['parkinson_vol'] = _parkinson_vol(h, l, 20)

    # ===== VOLUME (15 indicators) =====
    for p in [10, 20, 50]:
        indicators[f'volume_sma_{p}'] = _sma(v, p)
        indicators[f'volume_ratio_{p}'] = v / (_sma(v, p) + 1)

    indicators['obv'] = _obv(c, v)
    indicators['obv_slope_10'] = _roc(_obv(c, v), 10)
    indicators['mfi_14'] = _mfi(h, l, c, v, 14)
    indicators['vwap_dev'] = _vwap_deviation(h, l, c, v, 20)
    indicators['ad_line'] = _ad_line(h, l, c, v)
    indicators['chaikin_mf'] = _chaikin_money_flow(h, l, c, v, 20)

    # ===== STATISTICAL (20 indicators) =====
    for p in [10, 20, 30]:
        returns = np.log(c / np.roll(c, 1))
        indicators[f'skewness_{p}'] = _rolling_skew(returns, p)
        indicators[f'kurtosis_{p}'] = _rolling_kurt(returns, p)

    for p in [5, 10, 20]:
        indicators[f'autocorr_{p}'] = _rolling_autocorr(c, p)

    indicators['hurst_50'] = _rolling_hurst(c, 50)
    indicators['hurst_100'] = _rolling_hurst(c, 100)

    # Z-scores
    for p in [20, 50]:
        indicators[f'zscore_{p}'] = (c - _sma(c, p)) / (_rolling_std(c, p) + 1e-10)

    indicators['entropy_20'] = _rolling_entropy(c, 20)
    indicators['entropy_50'] = _rolling_entropy(c, 50)

    # ===== PATTERN (9 indicators) =====
    indicators['higher_high'] = (h > np.roll(h, 1)).astype(float)
    indicators['lower_low'] = (l < np.roll(l, 1)).astype(float)
    indicators['inside_bar'] = ((h < np.roll(h, 1)) & (l > np.roll(l, 1))).astype(float)
    indicators['outside_bar'] = ((h > np.roll(h, 1)) & (l < np.roll(l, 1))).astype(float)
    indicators['body_ratio'] = abs(c - o) / (h - l + 1e-10)
    indicators['upper_shadow'] = (h - np.maximum(c, o)) / (h - l + 1e-10)
    indicators['lower_shadow'] = (np.minimum(c, o) - l) / (h - l + 1e-10)
    indicators['gap_up'] = (o > np.roll(h, 1)).astype(float)
    indicators['gap_down'] = (o < np.roll(l, 1)).astype(float)

    # Clean up
    indicators = indicators.replace([np.inf, -np.inf], np.nan)
    indicators = indicators.ffill().fillna(0)

    return indicators

# ===== Helper functions =====

def _sma(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = np.mean(data[i - period + 1:i + 1])
    return result

def _ema(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    # Find first valid window of `period` non-NaN values
    start = -1
    for i in range(len(data) - period + 1):
        window = data[i:i + period]
        if not np.any(np.isnan(window)):
            start = i + period - 1
            result[start] = np.mean(window)
            break
    if start < 0:
        return result
    for i in range(start + 1, len(data)):
        if np.isnan(data[i]):
            result[i] = result[i - 1]  # carry forward
        else:
            result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result

def _rsi(data, period):
    deltas = np.diff(data, prepend=data[0])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = _ema(gains, period)
    avg_loss = _ema(losses, period)
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - 100 / (1 + rs)

def _roc(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period, len(data)):
        if data[i - period] != 0:
            result[i] = (data[i] / data[i - period] - 1) * 100
    return result

def _cci(high, low, close, period):
    tp = (high + low + close) / 3
    sma = _sma(tp, period)
    mad = np.full_like(tp, np.nan)
    for i in range(period - 1, len(tp)):
        mad[i] = np.mean(np.abs(tp[i - period + 1:i + 1] - sma[i]))
    return (tp - sma) / (0.015 * mad + 1e-10)

def _williams_r(high, low, close, period):
    result = np.full_like(close, np.nan, dtype=float)
    for i in range(period - 1, len(close)):
        hh = np.max(high[i - period + 1:i + 1])
        ll = np.min(low[i - period + 1:i + 1])
        result[i] = -100 * (hh - close[i]) / (hh - ll + 1e-10)
    return result

def _stochastic_k(high, low, close, period):
    result = np.full_like(close, np.nan, dtype=float)
    for i in range(period - 1, len(close)):
        hh = np.max(high[i - period + 1:i + 1])
        ll = np.min(low[i - period + 1:i + 1])
        result[i] = 100 * (close[i] - ll) / (hh - ll + 1e-10)
    return result

def _tsi(close, long_period=25, short_period=13):
    deltas = np.diff(close, prepend=close[0])
    double_smooth = _ema(_ema(deltas, long_period), short_period)
    double_smooth_abs = _ema(_ema(np.abs(deltas), long_period), short_period)
    return 100 * double_smooth / (double_smooth_abs + 1e-10)

def _ultimate_oscillator(high, low, close, p1=7, p2=14, p3=28):
    bp = close - np.minimum(low, np.roll(close, 1))
    tr = np.maximum(high, np.roll(close, 1)) - np.minimum(low, np.roll(close, 1))
    avg1 = _sma(bp, p1) / (_sma(tr, p1) + 1e-10)
    avg2 = _sma(bp, p2) / (_sma(tr, p2) + 1e-10)
    avg3 = _sma(bp, p3) / (_sma(tr, p3) + 1e-10)
    return 100 * (4 * avg1 + 2 * avg2 + avg3) / 7

def _adx(high, low, close, period):
    pdi = _plus_di(high, low, close, period)
    mdi = _minus_di(high, low, close, period)
    dx = 100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-10)
    return _ema(dx, period)

def _plus_di(high, low, close, period):
    up = high - np.roll(high, 1)
    down = np.roll(low, 1) - low
    pdm = np.where((up > down) & (up > 0), up, 0)
    tr = _true_range(high, low, close)
    return 100 * _ema(pdm, period) / (_ema(tr, period) + 1e-10)

def _minus_di(high, low, close, period):
    up = high - np.roll(high, 1)
    down = np.roll(low, 1) - low
    mdm = np.where((down > up) & (down > 0), down, 0)
    tr = _true_range(high, low, close)
    return 100 * _ema(mdm, period) / (_ema(tr, period) + 1e-10)

def _true_range(high, low, close):
    prev_close = np.roll(close, 1)
    return np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))

def _atr(high, low, close, period):
    tr = _true_range(high, low, close)
    return _ema(tr, period)

def _rolling_std(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = np.std(data[i - period + 1:i + 1])
    return result

def _rolling_max(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = np.max(data[i - period + 1:i + 1])
    return result

def _rolling_min(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = np.min(data[i - period + 1:i + 1])
    return result

def _garman_klass(o, h, l, c, period):
    log_hl = np.log(h / l) ** 2
    log_co = np.log(c / o) ** 2
    gk = 0.5 * log_hl - (2 * np.log(2) - 1) * log_co
    result = np.full_like(c, np.nan, dtype=float)
    for i in range(period - 1, len(c)):
        result[i] = np.sqrt(np.mean(gk[i - period + 1:i + 1]) * 365)
    return result

def _parkinson_vol(h, l, period):
    log_hl = np.log(h / l) ** 2
    result = np.full_like(h, np.nan, dtype=float)
    for i in range(period - 1, len(h)):
        result[i] = np.sqrt(np.mean(log_hl[i - period + 1:i + 1]) / (4 * np.log(2)) * 365)
    return result

def _obv(close, volume):
    result = np.zeros_like(close)
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            result[i] = result[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            result[i] = result[i - 1] - volume[i]
        else:
            result[i] = result[i - 1]
    return result

def _mfi(high, low, close, volume, period):
    tp = (high + low + close) / 3
    mf = tp * volume
    pos_mf = np.where(np.diff(tp, prepend=tp[0]) > 0, mf, 0)
    neg_mf = np.where(np.diff(tp, prepend=tp[0]) < 0, mf, 0)
    pos_sum = _sma(pos_mf, period) * period
    neg_sum = _sma(neg_mf, period) * period
    return 100 - 100 / (1 + pos_sum / (neg_sum + 1e-10))

def _vwap_deviation(high, low, close, volume, period):
    tp = (high + low + close) / 3
    vwap = _sma(tp * volume, period) / (_sma(volume, period) + 1e-10)
    return (close - vwap) / (vwap + 1e-10)

def _ad_line(high, low, close, volume):
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    result = np.cumsum(clv * volume)
    return result

def _chaikin_money_flow(high, low, close, volume, period):
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    result = np.full_like(close, np.nan, dtype=float)
    for i in range(period - 1, len(close)):
        result[i] = np.sum(clv[i - period + 1:i + 1] * volume[i - period + 1:i + 1]) / (np.sum(volume[i - period + 1:i + 1]) + 1e-10)
    return result

def _rolling_skew(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1:i + 1]
        m = np.mean(window)
        s = np.std(window)
        if s > 1e-10:
            result[i] = np.mean(((window - m) / s) ** 3)
    return result

def _rolling_kurt(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1:i + 1]
        m = np.mean(window)
        s = np.std(window)
        if s > 1e-10:
            result[i] = np.mean(((window - m) / s) ** 4) - 3
    return result

def _rolling_autocorr(data, period):
    returns = np.diff(np.log(data + 1e-10), prepend=0)
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period * 2, len(data)):
        window = returns[i - period * 2:i]
        x = window[:period]
        y = window[period:]
        mx, my = np.mean(x), np.mean(y)
        sx, sy = np.std(x), np.std(y)
        if sx > 1e-10 and sy > 1e-10:
            result[i] = np.mean((x - mx) * (y - my)) / (sx * sy)
    return result

def _rolling_hurst(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1:i + 1]
        if np.std(window) < 1e-10:
            continue
        # R/S method
        mean = np.mean(window)
        cumdev = np.cumsum(window - mean)
        R = np.max(cumdev) - np.min(cumdev)
        S = np.std(window)
        if S > 0 and R > 0:
            result[i] = np.log(R / S) / np.log(period)
    return result

def _rolling_entropy(data, period):
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1:i + 1]
        returns = np.diff(window) / (window[:-1] + 1e-10)
        # Discretize into bins
        bins = np.linspace(returns.min() - 1e-10, returns.max() + 1e-10, 10)
        hist, _ = np.histogram(returns, bins=bins)
        probs = hist / (hist.sum() + 1e-10)
        probs = probs[probs > 0]
        result[i] = -np.sum(probs * np.log2(probs + 1e-10))
    return result
