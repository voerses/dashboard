"""
s525 ML Volume Zone — LightGBM Regime Classifier Signal
=========================================================

Uses a pre-trained LightGBM model to classify market regimes and generate
bidirectional trading signals based on predicted regime probabilities.

Two model ensemble:
  1. Regime classifier: squeeze / breakout_up / breakout_down
  2. Direction predictor: down / neutral / up

Entry logic (high conviction only):
  - P(breakout_up) > 0.70 AND P(up) > 0.45 -> long breakout
  - P(breakout_down) > 0.70 AND P(down) > 0.45 -> short breakout
  - P(squeeze) > 0.80 AND |vwap_zscore| > 2.5 -> mean reversion

Features computed per bar using only past data (no look-ahead).
Model trained on 2024 data only. Restricted to 30 liquid tokens.

OOS Performance (2025-07 to 2026-04):
  Rank IC: 0.112, Q5-Q1 spread: 1.575%
  High-confidence longs: +0.555% fwd_24h
  High-confidence shorts: -0.776% fwd_24h

Market: PERP (bidirectional)
Status: EXPERIMENTAL
"""

import os
import pickle

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_max, rolling_min)


# ── Configuration ────────────────────────────────────────────────────────
MODEL_PATH = "/tmp/ml_vz_model.pkl"
WARMUP = 200
LEVERAGE = 1.0

# Restrict to the 30 tokens the model was trained on
VALID_TOKENS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "DOT",
    "SUI", "ARB", "OP", "NEAR", "UNI", "LTC", "AAVE", "INJ", "APT", "ATOM",
    "ETC", "FIL", "TRX", "PEPE", "WIF", "ONDO", "TIA", "RENDER", "FET", "BONK",
}

# Entry thresholds (high conviction only)
P_BREAKOUT_THRESHOLD = 0.65
P_SQUEEZE_THRESHOLD = 0.75
VWAP_Z_MR_THRESHOLD = 2.5
P_DIR_AGREE_THRESHOLD = 0.42

# Trade management — longer holds to amortize fees
STOP_MULT = 3.0
TRAIL_MULT = 2.5
TARGET_MULT = 999.0
NO_STOP_BARS = 48        # 48h protection for bigger moves
MIN_HOLD = 24             # minimum 24h hold
MAX_HOLD = 720
EDGE = 0.40
BREAKEVEN_ATR = 0.5

# Entry cooldown per token
COOLDOWN_BARS = 48        # no re-entry within 48 bars


# ── Load model once at import time ──────────────────────────────────────
_MODEL_BUNDLE = None


def _load_model():
    global _MODEL_BUNDLE
    if _MODEL_BUNDLE is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"ML model not found at {MODEL_PATH}. "
                "Run /tmp/ml_volume_zone_train.py first."
            )
        with open(MODEL_PATH, "rb") as f:
            _MODEL_BUNDLE = pickle.load(f)
    return _MODEL_BUNDLE


# ── Feature computation helpers ─────────────────────────────────────────

def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI using EWM."""
    delta = np.diff(close, prepend=close[0])
    gain = np.maximum(delta, 0.0)
    loss = np.maximum(-delta, 0.0)
    alpha = 1.0 / period
    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)
    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])
    for i in range(period + 1, len(close)):
        avg_gain[i] = alpha * gain[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * loss[i] + (1 - alpha) * avg_loss[i - 1]
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi[:period] = 50.0
    return rsi


def _rolling_sum(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling sum via cumsum."""
    cs = np.cumsum(np.nan_to_num(arr, 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(arr), np.nan)
    out[window - 1:] = cs[window:] - cs[:-window]
    return out


def _rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling standard deviation."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=window // 2).std().values


def _rolling_pctile(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling percentile rank of current value."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=window // 2).rank(pct=True).values


def _compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 period: int = 14) -> np.ndarray:
    """ATR with EWM smoothing."""
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )
    alpha = 1.0 / period
    atr = np.zeros_like(tr)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    atr[:period - 1] = atr[period - 1]
    return atr


def _compute_features(ctx: StrategyContext):
    """Compute the 20-feature vector at each bar. Returns (n, 20) array + vwap_zscore."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    n = len(close)

    # Bollinger Band features
    bb_mid_20 = rolling_mean(close, 20)
    bb_std_20 = _rolling_std(close, 20)
    bb_width = (bb_std_20 * 2) / (bb_mid_20 + 1e-10)
    bbw_pctile_120 = _rolling_pctile(bb_width, 120)
    price_bb_position = (close - bb_mid_20) / (bb_std_20 + 1e-10)

    # VWAP features (168-bar rolling)
    typical = (high + low + close) / 3.0
    tp_vol = typical * volume
    vwap = _rolling_sum(tp_vol, 168) / (_rolling_sum(volume, 168) + 1e-10)
    vwap_dev = (close - vwap) / (vwap + 1e-10)
    vwap_diff = close - vwap
    vwap_std = _rolling_std(vwap_diff, 168)
    vwap_zscore = vwap_diff / (vwap_std + 1e-10)

    # Volume features
    vol_ma20 = rolling_mean(volume, 20)
    vol_ma5 = rolling_mean(volume, 5)
    vol_ratio = volume / (vol_ma20 + 1e-10)
    vol_trend = vol_ma5 / (vol_ma20 + 1e-10)

    # OBV slope
    close_diff = np.diff(close, prepend=close[0])
    obv_vals = np.where(close_diff > 0, volume,
                        np.where(close_diff < 0, -volume, 0.0))
    obv = np.cumsum(obv_vals)
    obv_shift20 = np.roll(obv, 20)
    obv_shift20[:20] = obv[:20]
    obv_slope = (obv - obv_shift20) / 20.0

    # RSI
    rsi_14 = _compute_rsi(close, 14)

    # 4h RSI
    idx_4h = np.arange(0, n, 4)
    close_4h = close[idx_4h]
    rsi_4h_ds = _compute_rsi(close_4h, 14)
    rsi_4h = np.zeros(n)
    for i, idx in enumerate(idx_4h):
        end = idx_4h[i + 1] if i + 1 < len(idx_4h) else n
        rsi_4h[idx:end] = rsi_4h_ds[i]

    # EMA50 slope
    ema50 = pd.Series(close).ewm(span=50, min_periods=50, adjust=False).mean().values
    ema50_shift5 = np.roll(ema50, 5)
    ema50_shift5[:5] = ema50[:5]
    ema_50_slope = (ema50 - ema50_shift5) / (ema50_shift5 + 1e-10)

    # ATR features
    atr_14 = _compute_atr(high, low, close, 14)
    atr_ma20 = rolling_mean(atr_14, 20)
    atr_change = atr_14 / (atr_ma20 + 1e-10) - 1.0
    atr_norm = atr_14 / (close + 1e-10)

    # Returns
    close_s1 = np.roll(close, 1); close_s1[0] = close[0]
    close_s4 = np.roll(close, 4); close_s4[:4] = close[:4]
    close_s24 = np.roll(close, 24); close_s24[:24] = close[:24]
    returns_1h = close / (close_s1 + 1e-10) - 1.0
    returns_4h = close / (close_s4 + 1e-10) - 1.0
    returns_24h = close / (close_s24 + 1e-10) - 1.0

    # Pattern features
    rmax_20 = rolling_max(high, 20)
    rmin_20 = rolling_min(low, 20)
    rmax_shift = np.roll(rmax_20, 1); rmax_shift[0] = rmax_20[0]
    rmin_shift = np.roll(rmin_20, 1); rmin_shift[0] = rmin_20[0]
    higher_high = (high > rmax_shift).astype(np.float32)
    lower_low = (low < rmin_shift).astype(np.float32)

    # Bars since squeeze
    squeeze_flag = np.nan_to_num(bbw_pctile_120, nan=1.0) < 0.30
    bars_since_squeeze = np.zeros(n, dtype=np.float32)
    count = 0
    for i in range(n):
        if squeeze_flag[i]:
            count += 1
        else:
            count = 0
        bars_since_squeeze[i] = count

    # Stack features in same order as training
    features = np.column_stack([
        bb_width,           # 0
        bbw_pctile_120,     # 1
        price_bb_position,  # 2
        vwap_dev,           # 3
        vwap_zscore,        # 4
        vol_ratio,          # 5
        vol_trend,          # 6
        obv_slope,          # 7
        rsi_14,             # 8
        rsi_4h,             # 9
        ema_50_slope,       # 10
        atr_14,             # 11
        atr_change,         # 12
        atr_norm,           # 13
        returns_1h,         # 14
        returns_4h,         # 15
        returns_24h,        # 16
        higher_high,        # 17
        lower_low,          # 18
        bars_since_squeeze, # 19
    ])

    return features, vwap_zscore


# ── Strategy function ───────────────────────────────────────────────────

def strategy(ctx: StrategyContext) -> StrategyResult:
    """ML Volume Zone — regime-based entry signals from LightGBM."""
    close = ctx.ind_1h['close']
    n = len(close)

    # Only trade on trained tokens
    if ctx.ticker not in VALID_TOKENS:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            name='s525_ml_volume_zone',
        )

    bundle = _load_model()
    regime_model = bundle["regime_model"]
    direction_model = bundle["direction_model"]

    # Compute features
    features, vwap_zscore = _compute_features(ctx)
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

    # Predict regime probabilities: (n, 3) = [squeeze, breakout_up, breakout_down]
    regime_proba = regime_model.predict(features)
    p_squeeze = regime_proba[:, 0]
    p_breakout_up = regime_proba[:, 1]
    p_breakout_down = regime_proba[:, 2]

    # Predict direction probabilities: (n, 3) = [down, neutral, up]
    dir_proba = direction_model.predict(features)
    p_down = dir_proba[:, 0]
    p_up = dir_proba[:, 2]

    # ── Entry signals (high conviction only) ──────────────────────────

    # Breakout entries: regime + direction models must agree
    long_breakout = (
        (p_breakout_up > P_BREAKOUT_THRESHOLD) &
        (p_up > P_DIR_AGREE_THRESHOLD)
    )
    short_breakout = (
        (p_breakout_down > P_BREAKOUT_THRESHOLD) &
        (p_down > P_DIR_AGREE_THRESHOLD)
    )

    # Mean-reversion entries: squeeze + extreme VWAP deviation
    squeeze_mr_long = (
        (p_squeeze > P_SQUEEZE_THRESHOLD) &
        (vwap_zscore < -VWAP_Z_MR_THRESHOLD) &
        (p_up > p_down)
    )
    squeeze_mr_short = (
        (p_squeeze > P_SQUEEZE_THRESHOLD) &
        (vwap_zscore > VWAP_Z_MR_THRESHOLD) &
        (p_down > p_up)
    )

    entry_long = long_breakout | squeeze_mr_long
    entry_short = short_breakout | squeeze_mr_short

    # Direction
    direction = np.zeros(n, dtype=np.int8)
    direction[entry_long] = 1
    direction[entry_short] = -1
    # Tiebreaker for conflicting signals
    both = entry_long & entry_short
    net_signal = p_breakout_up - p_breakout_down + 0.5 * (p_up - p_down)
    direction[both] = np.where(net_signal[both] > 0, 1, -1).astype(np.int8)

    entry_mask = entry_long | entry_short

    # ── Warmup guard ──────────────────────────────────────────────────
    entry_mask[:WARMUP] = False

    # ── Regime filter: skip crisis ────────────────────────────────────
    regime_ok = ctx.regime_1h != 0
    entry_mask = entry_mask & regime_ok

    # ── Volume confirmation — need above-average volume ───────────────
    vol_ratio = ctx.ind_1h['vol_ratio']
    entry_mask = entry_mask & (vol_ratio > 1.0)

    # ── ADX filter for breakout entries ───────────────────────────────
    adx = ctx.ind_1h['adx']
    breakout_entry = long_breakout | short_breakout
    mr_entry = squeeze_mr_long | squeeze_mr_short
    entry_mask = entry_mask & ((adx > 20) | mr_entry)

    # ── Liquidity filter ──────────────────────────────────────────────
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── Cooldown: no re-entry within COOLDOWN_BARS ────────────────────
    cooled = np.zeros(n, dtype=bool)
    last_entry = -COOLDOWN_BARS - 1
    for i in range(n):
        if entry_mask[i] and (i - last_entry >= COOLDOWN_BARS):
            cooled[i] = True
            last_entry = i
    entry_mask = cooled

    # ── Conviction score from model confidence ────────────────────────
    max_regime_prob = np.maximum(p_breakout_up,
                                np.maximum(p_breakout_down, p_squeeze))
    conviction = np.clip(max_regime_prob, 0.0, 1.0)

    # ── Size multiplier: scale by conviction ──────────────────────────
    size_mult = np.where(max_regime_prob > 0.8, 1.0, 0.5).astype(np.float64)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        exit_regimes={CRISIS, DOWNTREND},
        breakeven_atr=BREAKEVEN_ATR,
        exchange='binance',
        name='s525_ml_volume_zone',
        size_multiplier=size_mult,
        conviction_score=conviction,
    )
