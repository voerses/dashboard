"""
Signal Lab — Fast signal evaluation framework for crypto backtesting.

Instead of running full backtests on 149 tokens (hours), compute the Information
Coefficient (IC) of individual signals and combine the best ones (minutes).

IC = Spearman rank correlation between signal value today and forward return.
|IC| > 0.03 is useful. IC > 0.05 is strong. IC > 0.10 is exceptional.

Usage: python signal_lab.py
"""

import sys
import os
import time
import warnings

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

warnings.filterwarnings("ignore", category=RuntimeWarning)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.makedirs("outputs_v2", exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "real_data")
ETF_DATE = pd.Timestamp("2024-01-10")
MIN_HISTORY_DAYS = 730
FORWARD_WINDOWS = [1, 5, 20]
PRIMARY_FORWARD = 5  # main IC computed against 5d forward return


# ===========================================================================
# Data Loading
# ===========================================================================
def load_all_tokens(data_dir: str, min_days: int = MIN_HISTORY_DAYS) -> pd.DataFrame:
    """Load all CSVs into a single long-format DataFrame with a 'token' column."""
    frames = []
    files = sorted(f for f in os.listdir(data_dir) if f.endswith("_daily.csv"))
    for fname in files:
        token = fname.replace("_daily.csv", "")
        path = os.path.join(data_dir, fname)
        df = pd.read_csv(path, parse_dates=["date"])
        if len(df) < min_days:
            continue
        df["token"] = token
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["token", "date"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    print(f"Loaded {combined['token'].nunique()} tokens with {min_days}+ days of data")
    print(f"Total rows: {len(combined):,}")
    return combined


def load_btc(data_dir: str) -> pd.DataFrame:
    """Load BTC data separately for cross-asset signals."""
    path = os.path.join(data_dir, "BTC_daily.csv")
    btc = pd.read_csv(path, parse_dates=["date"])
    btc.sort_values("date", inplace=True)
    btc.reset_index(drop=True, inplace=True)
    return btc


# ===========================================================================
# Forward Returns (vectorized per-group)
# ===========================================================================
def compute_forward_returns(df: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    """Add fwd_Nd columns = (close[t+N] / close[t]) - 1, computed per token."""
    for w in windows:
        df[f"fwd_{w}d"] = df.groupby("token")["close"].transform(
            lambda s: s.shift(-w) / s - 1
        )
    return df


# ===========================================================================
# Signal Definitions
# ===========================================================================
# Each signal function takes (group: DataFrame for one token, btc: DataFrame)
# and returns a Series aligned to group.index.


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).mean()


def _std(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).std()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """Average Directional Index."""
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # zero out where the other is larger
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr = _atr(high, low, close, period)
    plus_di = 100 * _ema(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm, period) / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _ema(dx, period)


# --- Signal registry ---
SIGNAL_FUNCTIONS: dict[str, callable] = {}


def register_signal(name: str):
    """Decorator to register a signal function."""
    def decorator(fn):
        SIGNAL_FUNCTIONS[name] = fn
        return fn
    return decorator


# ---- Momentum / Trend ----

@register_signal("ret_5d")
def sig_ret_5d(g, btc):
    return g["close"].pct_change(5)

@register_signal("ret_10d")
def sig_ret_10d(g, btc):
    return g["close"].pct_change(10)

@register_signal("ret_20d")
def sig_ret_20d(g, btc):
    return g["close"].pct_change(20)

@register_signal("ret_60d")
def sig_ret_60d(g, btc):
    return g["close"].pct_change(60)

@register_signal("ema_cross")
def sig_ema_cross(g, btc):
    return _ema(g["close"], 12) / _ema(g["close"], 26) - 1

@register_signal("macd_signal")
def sig_macd_signal(g, btc):
    macd_line = _ema(g["close"], 12) - _ema(g["close"], 26)
    signal_line = _ema(macd_line, 9)
    return macd_line - signal_line

@register_signal("adx_20")
def sig_adx_20(g, btc):
    return _adx(g["high"], g["low"], g["close"], 20)

@register_signal("donchian_pos")
def sig_donchian_pos(g, btc):
    high_20 = g["high"].rolling(20, min_periods=20).max()
    low_20 = g["low"].rolling(20, min_periods=20).min()
    rng = (high_20 - low_20).replace(0, np.nan)
    return (g["close"] - low_20) / rng


# ---- Mean Reversion ----

@register_signal("zscore_20")
def sig_zscore_20(g, btc):
    sma = _sma(g["close"], 20)
    std = _std(g["close"], 20)
    return (g["close"] - sma) / std.replace(0, np.nan)

@register_signal("zscore_50")
def sig_zscore_50(g, btc):
    sma = _sma(g["close"], 50)
    std = _std(g["close"], 50)
    return (g["close"] - sma) / std.replace(0, np.nan)

@register_signal("rsi_14")
def sig_rsi_14(g, btc):
    return _rsi(g["close"], 14) - 50

@register_signal("bb_pct")
def sig_bb_pct(g, btc):
    sma = _sma(g["close"], 20)
    std = _std(g["close"], 20)
    upper = sma + 2 * std
    lower = sma - 2 * std
    rng = (upper - lower).replace(0, np.nan)
    return (g["close"] - lower) / rng


# ---- Volatility ----

@register_signal("rvol_ratio")
def sig_rvol_ratio(g, btc):
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_10 = log_ret.rolling(10, min_periods=10).std()
    vol_60 = log_ret.rolling(60, min_periods=60).std()
    return vol_10 / vol_60.replace(0, np.nan)

@register_signal("vol_of_vol")
def sig_vol_of_vol(g, btc):
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_10 = log_ret.rolling(10, min_periods=10).std()
    return vol_10.rolling(20, min_periods=20).std()

@register_signal("garman_klass")
def sig_garman_klass(g, btc):
    log_hl = np.log(g["high"] / g["low"])
    log_co = np.log(g["close"] / g["open"])
    gk_daily = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    return gk_daily.rolling(20, min_periods=20).mean()

@register_signal("parkinson")
def sig_parkinson(g, btc):
    log_hl = np.log(g["high"] / g["low"])
    pk_daily = log_hl ** 2 / (4 * np.log(2))
    return pk_daily.rolling(20, min_periods=20).mean()

@register_signal("range_compression")
def sig_range_compression(g, btc):
    atr5 = _atr(g["high"], g["low"], g["close"], 5)
    atr20 = _atr(g["high"], g["low"], g["close"], 20)
    return atr5 / atr20.replace(0, np.nan)


# ---- Volume / Microstructure ----

@register_signal("volume_momentum")
def sig_volume_momentum(g, btc):
    sma_vol = _sma(g["volume"], 20)
    return g["volume"] / sma_vol.replace(0, np.nan)

@register_signal("obv_slope")
def sig_obv_slope(g, btc):
    sign = np.sign(g["close"].diff())
    obv = (sign * g["volume"]).cumsum()
    # slope over 10 days via simple diff
    return obv.diff(10) / (obv.rolling(10, min_periods=10).std().replace(0, np.nan))

@register_signal("amihud")
def sig_amihud(g, btc):
    abs_ret = g["close"].pct_change().abs()
    dollar_vol = g["close"] * g["volume"]
    daily_illiq = abs_ret / dollar_vol.replace(0, np.nan)
    return daily_illiq.rolling(10, min_periods=10).mean()

@register_signal("close_location")
def sig_close_location(g, btc):
    rng = (g["high"] - g["low"]).replace(0, np.nan)
    return (g["close"] - g["low"]) / rng

@register_signal("vpin_approx")
def sig_vpin_approx(g, btc):
    """Approximate VPIN: |buy_vol - sell_vol| / total_vol over 20 bars.
    Buy/sell classified by close vs midpoint of high-low."""
    mid = (g["high"] + g["low"]) / 2
    buy_frac = (g["close"] - mid) / (g["high"] - g["low"]).replace(0, np.nan)
    buy_frac = buy_frac.clip(-1, 1)
    buy_vol = (0.5 + 0.5 * buy_frac) * g["volume"]
    sell_vol = g["volume"] - buy_vol
    imbalance = (buy_vol - sell_vol).abs()
    total = g["volume"].rolling(20, min_periods=20).sum()
    return imbalance.rolling(20, min_periods=20).sum() / total.replace(0, np.nan)


# ---- Cross-Asset (BTC) ----

@register_signal("btc_beta_20")
def sig_btc_beta_20(g, btc):
    token_ret = g["close"].pct_change()
    btc_merged = g[["date"]].merge(btc[["date", "btc_ret"]], on="date", how="left")
    btc_ret = btc_merged["btc_ret"].values
    btc_s = pd.Series(btc_ret, index=g.index)
    cov = token_ret.rolling(20, min_periods=20).cov(btc_s)
    var_btc = btc_s.rolling(20, min_periods=20).var()
    return cov / var_btc.replace(0, np.nan)

@register_signal("btc_corr_20")
def sig_btc_corr_20(g, btc):
    token_ret = g["close"].pct_change()
    btc_merged = g[["date"]].merge(btc[["date", "btc_ret"]], on="date", how="left")
    btc_ret = btc_merged["btc_ret"].values
    btc_s = pd.Series(btc_ret, index=g.index)
    return token_ret.rolling(20, min_periods=20).corr(btc_s)

@register_signal("btc_lead_5d")
def sig_btc_lead_5d(g, btc):
    btc_5d = btc[["date"]].copy()
    btc_5d["btc_ret_5d"] = btc["close"].pct_change(5)
    merged = g[["date"]].merge(btc_5d, on="date", how="left")
    return pd.Series(merged["btc_ret_5d"].values, index=g.index)

@register_signal("relative_strength")
def sig_relative_strength(g, btc):
    token_ret_20 = g["close"].pct_change(20)
    btc_20 = btc[["date"]].copy()
    btc_20["btc_ret_20d"] = btc["close"].pct_change(20)
    merged = g[["date"]].merge(btc_20, on="date", how="left")
    btc_r = pd.Series(merged["btc_ret_20d"].values, index=g.index)
    return token_ret_20 - btc_r


# ---- Contrarian / Crowding ----

@register_signal("mean_reversion_5d")
def sig_mean_reversion_5d(g, btc):
    return -1 * g["close"].pct_change(5)

@register_signal("vol_adjusted_momentum")
def sig_vol_adjusted_momentum(g, btc):
    ret_20 = g["close"].pct_change(20)
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_20 = log_ret.rolling(20, min_periods=20).std()
    return ret_20 / vol_20.replace(0, np.nan)

@register_signal("liquidity_adjusted_ret")
def sig_liquidity_adjusted_ret(g, btc):
    ret_20 = g["close"].pct_change(20)
    dollar_vol = g["close"] * g["volume"]
    avg_dv = dollar_vol.rolling(20, min_periods=20).mean()
    return ret_20 * np.sqrt(avg_dv.clip(lower=0))


# ===========================================================================
# NEW SIGNALS — Expansion Wave 1 (added 2026-03-01)
# Priority signals from Strategy Catalog, Advanced Signals Catalog,
# Indicator Catalog, and Quant Strategies Research.
# ===========================================================================

# ---- TSMOM Variants (Academic Sharpe 1.5-2.17) ----

@register_signal("tsmom_28d")
def sig_tsmom_28d(g, btc):
    """Time-Series Momentum: 28-day lookback (Han, Kang & Ryu 2023).
    Optimal lookback for crypto. Returns tercile rank of own trailing return."""
    ret_28 = g["close"].pct_change(28)
    # Rolling percentile rank of own return (0-1)
    return ret_28.rolling(252, min_periods=60).rank(pct=True)

@register_signal("tsmom_14d")
def sig_tsmom_14d(g, btc):
    """Shorter TSMOM variant — 14-day lookback."""
    ret_14 = g["close"].pct_change(14)
    return ret_14.rolling(180, min_periods=60).rank(pct=True)

@register_signal("tsmom_7d")
def sig_tsmom_7d(g, btc):
    """Short TSMOM — 7-day lookback for faster signals."""
    ret_7 = g["close"].pct_change(7)
    return ret_7.rolling(120, min_periods=40).rank(pct=True)

@register_signal("vol_weighted_tsmom")
def sig_vol_weighted_tsmom(g, btc):
    """Volume-Weighted TSMOM (Huang, Sangiorgi & Urquhart 2024, Sharpe 2.17).
    Weight returns by relative volume."""
    ret_1 = g["close"].pct_change(1)
    vol_ratio = g["volume"] / g["volume"].rolling(20, min_periods=20).mean().replace(0, np.nan)
    vw_ret = ret_1 * vol_ratio
    return vw_ret.rolling(28, min_periods=14).sum()

@register_signal("tsmom_acceleration")
def sig_tsmom_acceleration(g, btc):
    """Momentum acceleration: rate of change of momentum.
    Captures when trends are speeding up vs slowing down."""
    ret_14 = g["close"].pct_change(14)
    ret_14_prev = ret_14.shift(7)
    return ret_14 - ret_14_prev


# ---- Adaptive / Modern Trend Indicators ----

@register_signal("kama_20")
def sig_kama_20(g, btc):
    """Kaufman Adaptive Moving Average (KAMA).
    Adapts speed based on noise ratio — fast in trends, slow in chop."""
    close = g["close"]
    n = len(close)
    fast_sc = 2.0 / (2 + 1)   # fast EMA constant (period 2)
    slow_sc = 2.0 / (30 + 1)  # slow EMA constant (period 30)
    period = 20
    kama = close.copy() * np.nan
    if n <= period:
        return kama
    kama.iloc[period - 1] = close.iloc[period - 1]
    for i in range(period, n):
        direction = abs(close.iloc[i] - close.iloc[i - period])
        volatility = close.diff().abs().iloc[i - period + 1:i + 1].sum()
        if volatility == 0:
            er = 0
        else:
            er = direction / volatility  # efficiency ratio
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2  # smoothing constant
        kama.iloc[i] = kama.iloc[i - 1] + sc * (close.iloc[i] - kama.iloc[i - 1])
    return (close - kama) / kama.replace(0, np.nan)  # deviation from KAMA

@register_signal("hull_ma_dev")
def sig_hull_ma_dev(g, btc):
    """Hull Moving Average deviation — faster-responding MA."""
    close = g["close"]
    half_wma = close.rolling(10, min_periods=10).mean()
    full_wma = close.rolling(20, min_periods=20).mean()
    diff = 2 * half_wma - full_wma
    hull = diff.rolling(4, min_periods=4).mean()  # sqrt(20) ≈ 4
    return (close - hull) / hull.replace(0, np.nan)

@register_signal("supertrend_dir")
def sig_supertrend_dir(g, btc):
    """Supertrend direction — trend-following indicator.
    Returns +1 in uptrend, -1 in downtrend."""
    close = g["close"]
    high = g["high"]
    low = g["low"]
    atr = _atr(high, low, close, 10)
    mid = (high + low) / 2
    upper = mid + 3 * atr
    lower = mid - 3 * atr
    direction = pd.Series(1, index=close.index, dtype=float)
    for i in range(1, len(close)):
        if close.iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
    return direction

@register_signal("di_crossover")
def sig_di_crossover(g, btc):
    """Directional Indicator crossover: +DI minus -DI.
    Positive = bullish trend, negative = bearish trend."""
    plus_dm = g["high"].diff().clip(lower=0)
    minus_dm = (-g["low"].diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr = _atr(g["high"], g["low"], g["close"], 14)
    plus_di = 100 * _ema(plus_dm, 14) / atr.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm, 14) / atr.replace(0, np.nan)
    return plus_di - minus_di

@register_signal("aroon_osc")
def sig_aroon_osc(g, btc):
    """Aroon Oscillator: measures how recently the highest high vs lowest low occurred.
    +100 = strong uptrend, -100 = strong downtrend."""
    period = 25
    aroon_up = g["high"].rolling(period, min_periods=period).apply(
        lambda x: x.argmax() / (period - 1) * 100, raw=True
    )
    aroon_dn = g["low"].rolling(period, min_periods=period).apply(
        lambda x: x.argmin() / (period - 1) * 100, raw=True
    )
    return aroon_up - aroon_dn


# ---- Momentum Quality / Crash Protection ----

@register_signal("momentum_quality")
def sig_momentum_quality(g, btc):
    """Momentum Quality: ret * consistency. High return + smooth path = high quality.
    Frog-in-the-Pan concept (Da, Gurun & Warachka 2014)."""
    ret_20 = g["close"].pct_change(20)
    # Count fraction of positive daily returns in the window
    daily_ret = g["close"].pct_change()
    pos_frac = daily_ret.rolling(20, min_periods=20).apply(lambda x: (x > 0).mean(), raw=True)
    return ret_20 * pos_frac  # big smooth up moves score highest

@register_signal("frog_in_pan")
def sig_frog_in_pan(g, btc):
    """Frog-in-the-Pan: slow, steady momentum beats explosive moves.
    FIP = sign(ret) * (fraction of days matching sign of total return)."""
    ret_20 = g["close"].pct_change(20)
    daily_ret = g["close"].pct_change()
    sign_ret = np.sign(ret_20)
    # Fraction of days where daily return matches the overall direction
    def fip_calc(window):
        if len(window) < 20:
            return np.nan
        total_sign = np.sign(window.sum())
        if total_sign == 0:
            return 0
        return (np.sign(window) == total_sign).mean()
    consistency = daily_ret.rolling(20, min_periods=20).apply(fip_calc, raw=True)
    return sign_ret * consistency

@register_signal("vol_scaled_momentum")
def sig_vol_scaled_momentum(g, btc):
    """Barroso & Santa-Clara (2015) momentum crash protection.
    Scale momentum by inverse of recent realized vol."""
    ret_20 = g["close"].pct_change(20)
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_20 = log_ret.rolling(20, min_periods=20).std()
    # Target vol of 10% annualized
    target_vol = 0.10 / np.sqrt(252)
    scale = target_vol / vol_20.replace(0, np.nan)
    scale = scale.clip(0.2, 5.0)  # prevent extreme scaling
    return ret_20 * scale

@register_signal("momentum_reversal")
def sig_momentum_reversal(g, btc):
    """Short-term reversal vs medium-term momentum.
    Positive when medium-term is up but short-term pulled back — potential continuation."""
    ret_5 = g["close"].pct_change(5)
    ret_20 = g["close"].pct_change(20)
    return ret_20 - ret_5  # medium momentum minus recent return


# ---- Statistical Distribution Signals ----

@register_signal("rolling_skew")
def sig_rolling_skew(g, btc):
    """Rolling skewness of returns (20d).
    Positive skew = right tail (breakout potential), negative = crash risk."""
    daily_ret = g["close"].pct_change()
    return daily_ret.rolling(20, min_periods=20).skew()

@register_signal("rolling_kurtosis")
def sig_rolling_kurtosis(g, btc):
    """Rolling excess kurtosis (20d).
    High kurtosis = fat tails = more extreme moves coming."""
    daily_ret = g["close"].pct_change()
    return daily_ret.rolling(20, min_periods=20).kurt()

@register_signal("return_autocorr")
def sig_return_autocorr(g, btc):
    """Rolling lag-1 autocorrelation of returns (20d).
    Positive = trending, negative = mean-reverting, zero = random."""
    daily_ret = g["close"].pct_change()
    return daily_ret.rolling(20, min_periods=20).apply(
        lambda x: pd.Series(x).autocorr(lag=1), raw=False
    )

@register_signal("hurst_exponent")
def sig_hurst_exponent(g, btc):
    """Simplified Hurst exponent via variance ratio.
    H > 0.5 = trending (momentum works), H < 0.5 = mean-reverting."""
    log_ret = np.log(g["close"] / g["close"].shift(1))
    # Variance ratio: var(2-period) / (2 * var(1-period))
    var_1 = log_ret.rolling(20, min_periods=20).var()
    ret_2 = log_ret.rolling(2, min_periods=2).sum()
    var_2 = ret_2.rolling(20, min_periods=20).var()
    vr = var_2 / (2 * var_1).replace(0, np.nan)
    return vr  # > 1 = trending, < 1 = reverting

@register_signal("max_dd_speed")
def sig_max_dd_speed(g, btc):
    """Maximum drawdown speed over 20 days.
    How fast the worst drawdown happened — measures crash risk."""
    close = g["close"]
    rolling_max = close.rolling(20, min_periods=5).max()
    dd = (close - rolling_max) / rolling_max.replace(0, np.nan)
    return dd  # most negative = deepest drawdown


# ---- Volume / Flow Signals ----

@register_signal("volume_trend")
def sig_volume_trend(g, btc):
    """Volume trend: ratio of recent to older volume.
    Rising volume often precedes price moves."""
    vol_5 = g["volume"].rolling(5, min_periods=5).mean()
    vol_20 = g["volume"].rolling(20, min_periods=20).mean()
    return vol_5 / vol_20.replace(0, np.nan) - 1

@register_signal("price_volume_divergence")
def sig_price_volume_divergence(g, btc):
    """Price-volume divergence: price trending up but volume declining = weak.
    Price trending up with volume rising = strong."""
    ret_10 = g["close"].pct_change(10)
    vol_change = g["volume"].pct_change(10)
    return ret_10 * vol_change  # aligned = positive, divergent = negative

@register_signal("mfi_14")
def sig_mfi_14(g, btc):
    """Money Flow Index (MFI) — volume-weighted RSI.
    More informative than plain RSI because it includes volume."""
    typical = (g["high"] + g["low"] + g["close"]) / 3
    mf = typical * g["volume"]
    pos_mf = mf.where(typical > typical.shift(1), 0)
    neg_mf = mf.where(typical < typical.shift(1), 0)
    pos_sum = pos_mf.rolling(14, min_periods=14).sum()
    neg_sum = neg_mf.rolling(14, min_periods=14).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - 100 / (1 + mfr) - 50  # center around 0

@register_signal("ad_line_slope")
def sig_ad_line_slope(g, btc):
    """Accumulation/Distribution line slope (10d).
    Measures money flow into/out of the asset."""
    clv = ((g["close"] - g["low"]) - (g["high"] - g["close"])) / \
          (g["high"] - g["low"]).replace(0, np.nan)
    ad = (clv * g["volume"]).cumsum()
    ad_std = ad.rolling(10, min_periods=10).std().replace(0, np.nan)
    return ad.diff(10) / ad_std

@register_signal("taker_imbalance")
def sig_taker_imbalance(g, btc):
    """Approximate taker buy/sell imbalance from close position in bar range.
    Close near high = buying pressure, close near low = selling."""
    rng = (g["high"] - g["low"]).replace(0, np.nan)
    buy_frac = (g["close"] - g["low"]) / rng
    # 5-day average of buy fraction
    return buy_frac.rolling(5, min_periods=5).mean() - 0.5

@register_signal("dollar_volume_momentum")
def sig_dollar_volume_momentum(g, btc):
    """Dollar volume momentum — rising dollar volume = increasing interest."""
    dv = g["close"] * g["volume"]
    dv_5 = dv.rolling(5, min_periods=5).mean()
    dv_20 = dv.rolling(20, min_periods=20).mean()
    return dv_5 / dv_20.replace(0, np.nan) - 1


# ---- Volatility Regime Signals ----

@register_signal("yang_zhang")
def sig_yang_zhang(g, btc):
    """Yang-Zhang volatility estimator — most efficient for OHLC data.
    Combines overnight, open-close, and Rogers-Satchell components."""
    log_oc = np.log(g["open"] / g["close"].shift(1))  # overnight
    log_co = np.log(g["close"] / g["open"])  # open-to-close
    log_ho = np.log(g["high"] / g["open"])
    log_lo = np.log(g["low"] / g["open"])
    rs = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)  # Rogers-Satchell
    overnight_var = log_oc.rolling(20, min_periods=20).var()
    close_var = log_co.rolling(20, min_periods=20).var()
    rs_var = rs.rolling(20, min_periods=20).mean()
    k = 0.34 / (1.34 + (20 + 1) / (20 - 1))
    yz = np.sqrt(overnight_var + k * close_var + (1 - k) * rs_var)
    return yz

@register_signal("vol_term_structure")
def sig_vol_term_structure(g, btc):
    """Volatility term structure: short vol / long vol.
    > 1 = backwardation (vol spike), < 1 = contango (calm)."""
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_5 = log_ret.rolling(5, min_periods=5).std()
    vol_60 = log_ret.rolling(60, min_periods=60).std()
    return vol_5 / vol_60.replace(0, np.nan)

@register_signal("bb_squeeze")
def sig_bb_squeeze(g, btc):
    """Bollinger Band squeeze intensity — how compressed is volatility?
    Low values = potential breakout incoming."""
    bb_width = 2 * _std(g["close"], 20) / _sma(g["close"], 20).replace(0, np.nan)
    # Normalize by its own history
    return bb_width / bb_width.rolling(120, min_periods=60).mean().replace(0, np.nan)

@register_signal("realized_vs_parkinson")
def sig_realized_vs_parkinson(g, btc):
    """Ratio of close-to-close vol to Parkinson (range-based) vol.
    Divergence signals jump activity or overnight moves."""
    log_ret = np.log(g["close"] / g["close"].shift(1))
    cc_vol = log_ret.rolling(20, min_periods=20).std()
    log_hl = np.log(g["high"] / g["low"])
    pk_vol = np.sqrt((log_hl ** 2 / (4 * np.log(2))).rolling(20, min_periods=20).mean())
    return cc_vol / pk_vol.replace(0, np.nan)


# ---- Cross-Asset / BTC Signals ----

@register_signal("btc_dominance_proxy")
def sig_btc_dominance_proxy(g, btc):
    """BTC dominance proxy: BTC return vs token return.
    When BTC outperforms = risk-off, when token outperforms = risk-on."""
    token_ret_10 = g["close"].pct_change(10)
    btc_10 = btc[["date"]].copy()
    btc_10["btc_ret_10d"] = btc["close"].pct_change(10)
    merged = g[["date"]].merge(btc_10, on="date", how="left")
    btc_r = pd.Series(merged["btc_ret_10d"].values, index=g.index)
    return token_ret_10 - btc_r

@register_signal("btc_corr_60")
def sig_btc_corr_60(g, btc):
    """Longer-term BTC correlation (60d).
    Low correlation = potential diversifier, high = beta play."""
    token_ret = g["close"].pct_change()
    btc_merged = g[["date"]].merge(btc[["date", "btc_ret"]], on="date", how="left")
    btc_ret = btc_merged["btc_ret"].values
    btc_s = pd.Series(btc_ret, index=g.index)
    return token_ret.rolling(60, min_periods=40).corr(btc_s)

@register_signal("btc_corr_change")
def sig_btc_corr_change(g, btc):
    """Change in BTC correlation: short vs long window.
    Increasing correlation = regime shift (herding)."""
    token_ret = g["close"].pct_change()
    btc_merged = g[["date"]].merge(btc[["date", "btc_ret"]], on="date", how="left")
    btc_ret = btc_merged["btc_ret"].values
    btc_s = pd.Series(btc_ret, index=g.index)
    corr_10 = token_ret.rolling(10, min_periods=10).corr(btc_s)
    corr_60 = token_ret.rolling(60, min_periods=40).corr(btc_s)
    return corr_10 - corr_60

@register_signal("btc_vol_regime")
def sig_btc_vol_regime(g, btc):
    """BTC volatility regime: high BTC vol = risk-off environment.
    Useful as a filter — avoid entries during BTC turmoil."""
    btc_merged = g[["date"]].merge(btc[["date", "close"]], on="date", how="left", suffixes=("", "_btc"))
    btc_close = pd.Series(btc_merged["close_btc"].values, index=g.index)
    btc_ret = btc_close.pct_change()
    btc_vol = btc_ret.rolling(20, min_periods=20).std()
    btc_vol_med = btc_vol.rolling(120, min_periods=60).median()
    return btc_vol / btc_vol_med.replace(0, np.nan)  # > 1 = elevated vol regime


# ---- Microstructure Signals ----

@register_signal("corwin_schultz")
def sig_corwin_schultz(g, btc):
    """Corwin-Schultz (2012) spread estimator from high-low prices.
    Estimates bid-ask spread without needing tick data."""
    high = g["high"]
    low = g["low"]
    # Beta = sum of squared log(H/L) over 2 consecutive bars
    log_hl = np.log(high / low)
    log_hl_sq = log_hl ** 2
    beta = log_hl_sq + log_hl_sq.shift(1)
    # Gamma = log(max(H_t, H_{t-1}) / min(L_t, L_{t-1}))^2
    h2 = high.rolling(2, min_periods=2).max()
    l2 = low.rolling(2, min_periods=2).min()
    gamma = np.log(h2 / l2) ** 2
    # Alpha
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / (3 - 2 * np.sqrt(2)) - np.sqrt(gamma / (3 - 2 * np.sqrt(2)))
    alpha = alpha.clip(lower=0)
    spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return spread.rolling(20, min_periods=10).mean()

@register_signal("kyle_lambda")
def sig_kyle_lambda(g, btc):
    """Kyle's Lambda approximation — price impact per unit of volume.
    Higher = more illiquid, lower = more liquid."""
    abs_ret = g["close"].pct_change().abs()
    signed_vol = np.sign(g["close"].diff()) * np.sqrt(g["volume"].clip(lower=0))
    abs_signed = signed_vol.abs().replace(0, np.nan)
    lam = abs_ret / abs_signed
    return lam.rolling(20, min_periods=10).mean()

@register_signal("roll_spread")
def sig_roll_spread(g, btc):
    """Roll (1984) spread estimator from serial covariance of returns.
    Negative autocovariance = bid-ask bounce = spread."""
    ret = g["close"].pct_change()
    cov = ret.rolling(20, min_periods=20).apply(
        lambda x: pd.Series(x).autocorr(lag=1) * pd.Series(x).var(), raw=False
    )
    # Spread = 2 * sqrt(-cov) when cov < 0
    spread = np.where(cov < 0, 2 * np.sqrt(-cov), 0)
    return pd.Series(spread, index=g.index)

@register_signal("trade_intensity")
def sig_trade_intensity(g, btc):
    """Trade intensity proxy: volume per unit of price range.
    High intensity = lots of volume in tight range = accumulation/distribution."""
    rng = (g["high"] - g["low"]).replace(0, np.nan)
    intensity = g["volume"] / rng
    avg_intensity = intensity.rolling(20, min_periods=20).mean()
    return intensity / avg_intensity.replace(0, np.nan) - 1


# ---- Multi-Timeframe / Higher-Order Signals ----

@register_signal("weekly_momentum")
def sig_weekly_momentum(g, btc):
    """5-day (weekly) momentum — captures the weekly trading cycle."""
    return g["close"].pct_change(5)

@register_signal("monthly_momentum")
def sig_monthly_momentum(g, btc):
    """21-day (monthly) momentum."""
    return g["close"].pct_change(21)

@register_signal("quarterly_momentum")
def sig_quarterly_momentum(g, btc):
    """63-day (quarterly) momentum — captures longer allocation cycles."""
    return g["close"].pct_change(63)

@register_signal("monthly_vs_quarterly")
def sig_monthly_vs_quarterly(g, btc):
    """Momentum acceleration across timeframes.
    Monthly momentum minus quarterly trend — captures acceleration."""
    ret_21 = g["close"].pct_change(21)
    ret_63 = g["close"].pct_change(63)
    return ret_21 - ret_63 / 3  # normalize quarterly to monthly scale

@register_signal("new_high_distance")
def sig_new_high_distance(g, btc):
    """Distance from 60-day high (%).
    Near 0 = at new highs (momentum), very negative = deep pullback."""
    high_60 = g["high"].rolling(60, min_periods=20).max()
    return (g["close"] - high_60) / high_60.replace(0, np.nan)

@register_signal("new_low_distance")
def sig_new_low_distance(g, btc):
    """Distance from 60-day low (%).
    Near 0 = at new lows (weakness), very positive = strong bounce."""
    low_60 = g["low"].rolling(60, min_periods=20).min()
    return (g["close"] - low_60) / low_60.replace(0, np.nan)


# ---- Regime Detection Signals ----

@register_signal("trend_strength")
def sig_trend_strength(g, btc):
    """Combined trend strength: ADX * sign of DI crossover.
    Positive = strong uptrend, negative = strong downtrend, near-zero = no trend."""
    adx = _adx(g["high"], g["low"], g["close"], 14)
    plus_dm = g["high"].diff().clip(lower=0)
    minus_dm = (-g["low"].diff()).clip(lower=0)
    plus_dm_c = plus_dm.copy()
    minus_dm_c = minus_dm.copy()
    plus_dm_c[plus_dm < minus_dm] = 0
    minus_dm_c[minus_dm < plus_dm] = 0
    atr = _atr(g["high"], g["low"], g["close"], 14)
    plus_di = 100 * _ema(plus_dm_c, 14) / atr.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm_c, 14) / atr.replace(0, np.nan)
    return adx * np.sign(plus_di - minus_di)

@register_signal("regime_vol_ratio")
def sig_regime_vol_ratio(g, btc):
    """Vol regime: ratio of current vol to 6-month median.
    > 1.5 = crisis, < 0.7 = quiet, around 1 = normal."""
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_10 = log_ret.rolling(10, min_periods=10).std()
    vol_med = vol_10.rolling(120, min_periods=60).median()
    return vol_10 / vol_med.replace(0, np.nan)

@register_signal("ema_stack")
def sig_ema_stack(g, btc):
    """EMA stack alignment: measures how aligned the EMA layers are.
    All EMAs in order (10>20>50) = strong trend. Score: -1 to +1."""
    ema10 = _ema(g["close"], 10)
    ema20 = _ema(g["close"], 20)
    ema50 = _ema(g["close"], 50)
    score = pd.Series(0.0, index=g.index)
    score += (ema10 > ema20).astype(float) - (ema10 < ema20).astype(float)
    score += (ema20 > ema50).astype(float) - (ema20 < ema50).astype(float)
    score += (g["close"] > ema10).astype(float) - (g["close"] < ema10).astype(float)
    return score / 3  # normalize to [-1, +1]

@register_signal("price_vs_200ma")
def sig_price_vs_200ma(g, btc):
    """Price relative to 200-day MA — classic regime indicator.
    Above = bull market, below = bear market."""
    ma200 = _sma(g["close"], 200)
    return (g["close"] - ma200) / ma200.replace(0, np.nan)


# ===========================================================================
# Signal Computation Engine
# ===========================================================================
def compute_all_signals(df: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    """Compute all registered signals for every token-day. Returns df with signal columns."""
    # Pre-compute BTC returns for cross-asset signals
    btc = btc.copy()
    btc["btc_ret"] = btc["close"].pct_change()

    signal_names = list(SIGNAL_FUNCTIONS.keys())
    print(f"\nComputing {len(signal_names)} signals...")

    # Initialize signal columns
    for name in signal_names:
        df[name] = np.nan

    # Compute per token group
    tokens = df["token"].unique()
    t0 = time.time()
    for i, token in enumerate(tokens):
        mask = df["token"] == token
        g = df.loc[mask].copy()
        for name, fn in SIGNAL_FUNCTIONS.items():
            try:
                vals = fn(g, btc)
                df.loc[mask, name] = vals.values if hasattr(vals, "values") else vals
            except Exception:
                pass  # leave as NaN
        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            print(f"  {i + 1}/{len(tokens)} tokens ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    print(f"  Done: {len(tokens)} tokens in {elapsed:.1f}s")
    return df


# ===========================================================================
# IC Computation
# ===========================================================================
def spearman_ic(signal: pd.Series, forward_ret: pd.Series) -> float:
    """Spearman rank correlation (IC) between signal and forward return."""
    valid = signal.notna() & forward_ret.notna() & np.isfinite(signal) & np.isfinite(forward_ret)
    s = signal[valid]
    r = forward_ret[valid]
    if len(s) < 30:
        return np.nan
    corr, _ = scipy_stats.spearmanr(s, r)
    return corr


def compute_monthly_ic(
    df: pd.DataFrame, signal_name: str, fwd_col: str, mask: pd.Series
) -> pd.Series:
    """Compute IC per month for a signal (cross-sectional: rank tokens each day, then monthly avg)."""
    sub = df.loc[mask, ["date", signal_name, fwd_col]].dropna()
    if len(sub) < 60:
        return pd.Series(dtype=float)
    sub["month"] = sub["date"].dt.to_period("M")
    monthly_ics = []
    for _, mdf in sub.groupby("month"):
        if len(mdf) < 10:
            continue
        ic = scipy_stats.spearmanr(mdf[signal_name], mdf[fwd_col])[0]
        monthly_ics.append(ic)
    return pd.Series(monthly_ics)


def compute_turnover(df: pd.DataFrame, signal_name: str) -> float:
    """Average daily rank change of a signal across tokens."""
    sub = df[["date", "token", signal_name]].dropna()
    if len(sub) < 100:
        return np.nan
    # Rank cross-sectionally each day
    sub["rank"] = sub.groupby("date")[signal_name].rank(pct=True)
    # Average absolute daily rank change per token
    sub["rank_change"] = sub.groupby("token")["rank"].diff().abs()
    return sub["rank_change"].mean()


def evaluate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Compute IC metrics for every signal."""
    fwd_col = f"fwd_{PRIMARY_FORWARD}d"
    signal_names = list(SIGNAL_FUNCTIONS.keys())

    mask_pre = df["date"] < ETF_DATE
    mask_post = df["date"] >= ETF_DATE

    results = []
    print(f"\nEvaluating {len(signal_names)} signals against {fwd_col}...")

    for name in signal_names:
        sig = df[name]
        fwd = df[fwd_col]

        # Overall IC
        valid = sig.notna() & fwd.notna() & np.isfinite(sig) & np.isfinite(fwd)
        ic_all = spearman_ic(sig, fwd)

        # Pre/post ETF IC
        ic_pre = spearman_ic(sig[mask_pre], fwd[mask_pre])
        ic_post = spearman_ic(sig[mask_post], fwd[mask_post])
        ic_change = (ic_post - ic_pre) if (not np.isnan(ic_pre) and not np.isnan(ic_post)) else np.nan

        # IC stability (t-stat of monthly ICs)
        monthly_ics = compute_monthly_ic(df, name, fwd_col, valid)
        if len(monthly_ics) > 2:
            ic_mean = monthly_ics.mean()
            ic_std = monthly_ics.std()
            ic_stability = ic_mean / (ic_std / np.sqrt(len(monthly_ics))) if ic_std > 0 else np.nan
        else:
            ic_stability = np.nan

        # Turnover
        turnover = compute_turnover(df, name)

        # Count observations
        n_obs = valid.sum()

        results.append({
            "signal": name,
            "IC_all": ic_all,
            "IC_pre_etf": ic_pre,
            "IC_post_etf": ic_post,
            "IC_change": ic_change,
            "IC_stability_tstat": ic_stability,
            "turnover": turnover,
            "n_obs": int(n_obs),
        })

    return pd.DataFrame(results)


# ===========================================================================
# Composite Signal (IC-weighted combination)
# ===========================================================================
def compute_composite(df: pd.DataFrame, eval_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Build IC-weighted composite signal from the top_n signals by |IC_post_etf|."""
    ranked = eval_df.dropna(subset=["IC_post_etf"]).copy()
    ranked["abs_ic"] = ranked["IC_post_etf"].abs()
    ranked = ranked.sort_values("abs_ic", ascending=False).head(top_n)

    print(f"\n--- Composite Signal (top {top_n} by |IC_post_etf|) ---")
    signal_names = ranked["signal"].tolist()
    ic_weights = ranked["IC_post_etf"].values

    # Normalize: cross-sectional z-score each signal per day, then IC-weight
    composite = pd.Series(0.0, index=df.index)
    for name, w in zip(signal_names, ic_weights):
        sig = df[name].copy()
        # Cross-sectional z-score per day
        mean = df.groupby("date")[name].transform("mean")
        std = df.groupby("date")[name].transform("std")
        z = (sig - mean) / std.replace(0, np.nan)
        z = z.clip(-3, 3).fillna(0)
        composite += w * z
        print(f"  {name:30s}  IC_weight={w:+.4f}")

    df["composite"] = composite
    fwd_col = f"fwd_{PRIMARY_FORWARD}d"
    mask_post = df["date"] >= ETF_DATE
    ic_composite = spearman_ic(composite[mask_post], df.loc[mask_post, fwd_col])
    print(f"\n  Composite IC (post-ETF): {ic_composite:.4f}")

    # Also compute for other forward windows
    for w in FORWARD_WINDOWS:
        fc = f"fwd_{w}d"
        ic_w = spearman_ic(composite[mask_post], df.loc[mask_post, fc])
        print(f"  Composite IC (post-ETF, {w}d fwd): {ic_w:.4f}")

    return df


# ===========================================================================
# Display & Output
# ===========================================================================
def print_results(eval_df: pd.DataFrame):
    """Print formatted evaluation results."""
    pd.set_option("display.float_format", "{:.4f}".format)
    pd.set_option("display.max_rows", 100)
    pd.set_option("display.width", 140)

    sorted_df = eval_df.sort_values("IC_post_etf", key=abs, ascending=False)

    print("\n" + "=" * 120)
    print("SIGNAL EVALUATION RESULTS")
    print(f"Sorted by |IC_post_etf| (rank correlation with {PRIMARY_FORWARD}d forward return)")
    print("=" * 120)
    print(sorted_df.to_string(index=False))

    # Highlight regime changes
    print("\n" + "-" * 80)
    print("REGIME ANALYSIS (Pre-ETF vs Post-ETF)")
    print("-" * 80)

    improved = sorted_df[sorted_df["IC_change"] > 0.01].sort_values("IC_change", ascending=False)
    degraded = sorted_df[sorted_df["IC_change"] < -0.01].sort_values("IC_change")
    flipped = sorted_df[
        (sorted_df["IC_pre_etf"] * sorted_df["IC_post_etf"] < 0)
        & (sorted_df["IC_pre_etf"].abs() > 0.01)
        & (sorted_df["IC_post_etf"].abs() > 0.01)
    ]

    if len(improved) > 0:
        print("\nSIGNALS THAT IMPROVED POST-ETF:")
        for _, row in improved.iterrows():
            print(f"  {row['signal']:30s}  pre={row['IC_pre_etf']:+.4f}  post={row['IC_post_etf']:+.4f}  change={row['IC_change']:+.4f}")

    if len(degraded) > 0:
        print("\nSIGNALS THAT DEGRADED POST-ETF:")
        for _, row in degraded.iterrows():
            print(f"  {row['signal']:30s}  pre={row['IC_pre_etf']:+.4f}  post={row['IC_post_etf']:+.4f}  change={row['IC_change']:+.4f}")

    if len(flipped) > 0:
        print("\nSIGNALS THAT FLIPPED SIGN:")
        for _, row in flipped.iterrows():
            print(f"  {row['signal']:30s}  pre={row['IC_pre_etf']:+.4f}  post={row['IC_post_etf']:+.4f}")

    # Stability ranking
    print("\n" + "-" * 80)
    print("MOST STABLE SIGNALS (by |IC t-stat|)")
    print("-" * 80)
    stable = sorted_df.dropna(subset=["IC_stability_tstat"]).copy()
    stable["abs_tstat"] = stable["IC_stability_tstat"].abs()
    stable = stable.sort_values("abs_tstat", ascending=False).head(10)
    for _, row in stable.iterrows():
        direction = "+" if row["IC_stability_tstat"] > 0 else "-"
        print(f"  {row['signal']:30s}  t-stat={row['IC_stability_tstat']:+.2f}  IC_post={row['IC_post_etf']:+.4f}")


def save_results(eval_df: pd.DataFrame, output_dir: str = "outputs_v2"):
    """Save evaluation results to CSV."""
    path = os.path.join(output_dir, "signal_evaluation.csv")
    eval_df.sort_values("IC_post_etf", key=abs, ascending=False).to_csv(path, index=False)
    print(f"\nResults saved to {path}")


# ===========================================================================
# Main
# ===========================================================================
def main():
    t_start = time.time()

    # Load data
    df = load_all_tokens(DATA_DIR, min_days=MIN_HISTORY_DAYS)
    btc = load_btc(DATA_DIR)

    # Compute forward returns
    df = compute_forward_returns(df, FORWARD_WINDOWS)

    # Compute all signals
    df = compute_all_signals(df, btc)

    # Evaluate signals
    eval_df = evaluate_signals(df)

    # Print results
    print_results(eval_df)

    # Composite signal
    df = compute_composite(df, eval_df, top_n=10)

    # Save
    save_results(eval_df)

    elapsed = time.time() - t_start
    print(f"\nTotal runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
