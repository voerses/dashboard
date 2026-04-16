"""
s503_ml_entry_filter_v2.py — Enhanced CatBoost with 1m Microstructure Features

FIXES from v1:
  - vol_ratio_at_cross: fixed normalization bug (was dividing by 1e-9)
  - Added proper clipping to prevent extreme outliers

NEW 1m MICROSTRUCTURE FEATURES:
  1. rvol_at_cross       — 1m volume at cross / rolling 60m median volume (FIXED)
  2. atr_expansion_1m    — fast ATR(5) / slow ATR(30) on 1m bars at cross point
  3. vwap_deviation       — (cross_price - VWAP_60m) / ATR — how far from fair value
  4. price_acceleration   — 2nd derivative of price at cross (rate of momentum change)
  5. nr7_flag             — narrowest range of last 7 4H bars before breakout
  6. volume_surge_1m      — max 1m volume in 5 bars pre-cross / 60m median
  7. bid_ask_proxy        — (high-low)/close on cross 1m bar (spread proxy)
  8. momentum_persistence — fraction of last 10 1m bars moving in breakout direction

ALSO:
  - CatBoost regression (continuous PnL target) alongside classification
  - Top-quantile analysis: select trades by predicted PnL, not just win probability

Usage:
    /workspace/venv/bin/python research/s503_ml_entry_filter_v2.py
"""

import os
import sys
import warnings
import time
import json

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor, Pool
from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    confusion_matrix,
    brier_score_loss,
    mean_absolute_error,
)

warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = "/workspace/crypto_backtest/data/perp"
DIR_1M = os.path.join(DATA_DIR, "1m_cache")
DIR_1H = os.path.join(DATA_DIR, "1h_cache")

SPLIT_DATE = pd.Timestamp("2025-04-03", tz=None)

BB_PERIOD = 20
BB_STD = 2.0
MOM_BARS_4H = 42

MAX_HOLD_HOURS = 4
ATR_PERIOD = 14
TP_ATR_MULT = 1.5
TRAIL_ATR_MULT = 2.0
PURGE_GAP_HOURS = 24

FEATURE_COLS = [
    # ── Pre-signal (known at 4H boundary) ────────────────────────────
    "bb_bandwidth_pct",
    "bbw_percentile",
    "squeeze_bars",
    "nr7_flag",              # NEW: narrowest range of last 7 4H bars
    "atr_percentile",
    "rsi_1h",
    "rsi_4h",
    "ema_trend_1h",
    "mom_strength",
    "prior_4h_return",
    "prior_4h_vol_ratio",
    "atr_expansion",
    "hour_of_day",
    "day_of_week",
    # ── At-cross 1m microstructure (known at cross moment) ───────────
    "bb_dist_pct",
    "cross_minute",
    "cross_hour_offset",
    "rvol_at_cross",         # FIXED: proper normalization
    "volume_surge_1m",       # NEW: max vol in 5 bars pre-cross / median
    "cross_speed",
    "price_acceleration",    # NEW: 2nd derivative of price
    "atr_expansion_1m",      # NEW: fast/slow ATR ratio on 1m
    "vwap_deviation",        # NEW: distance from 1m VWAP
    "bid_ask_proxy",         # NEW: spread proxy from 1m bar
    "momentum_persistence",  # NEW: fraction of recent 1m bars in direction
    "prior_hour_return",
    "prior_hour_range_pct",
    "bars_since_last_signal",
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def load_parquet(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


def resample_1h_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    ohlcv = df_1h[["open", "high", "low", "close", "volume"]].copy()
    df_4h = ohlcv.resample("4h", offset="0h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    df_4h = df_4h.dropna(subset=["close"])
    return df_4h


def compute_bb(df_4h: pd.DataFrame) -> pd.DataFrame:
    close = df_4h["close"]
    sma = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).std()
    df_4h = df_4h.copy()
    df_4h["bb_upper"] = sma + BB_STD * std
    df_4h["bb_lower"] = sma - BB_STD * std
    df_4h["bb_mid"] = sma
    df_4h["prior_bb_upper"] = df_4h["bb_upper"].shift(1)
    df_4h["prior_bb_lower"] = df_4h["bb_lower"].shift(1)
    return df_4h


def compute_mom_4h(df_4h: pd.DataFrame) -> pd.Series:
    return (df_4h["close"] - df_4h["close"].shift(MOM_BARS_4H)) / df_4h["close"].shift(
        MOM_BARS_4H
    ).clip(lower=1e-10)


def compute_atr_1h(df_1h: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    h, l, c = df_1h["high"], df_1h["low"], df_1h["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def detect_4h_boundary_signals(df_1h, df_4h):
    mom = compute_mom_4h(df_4h)
    signals = []
    for i in range(1, len(df_4h)):
        ts = df_4h.index[i]
        bb_upper = df_4h["prior_bb_upper"].iloc[i]
        bb_lower = df_4h["prior_bb_lower"].iloc[i]
        m = mom.iloc[i - 1]
        if pd.isna(bb_upper) or pd.isna(bb_lower) or pd.isna(m):
            continue
        if m > 0 and bb_upper > 0:
            signals.append({"boundary_ts": ts, "direction": 1, "bb_level": bb_upper})
        elif m < 0 and bb_lower > 0:
            signals.append({"boundary_ts": ts, "direction": -1, "bb_level": bb_lower})
    if not signals:
        return pd.DataFrame()
    df_sig = pd.DataFrame(signals).set_index("boundary_ts")
    return df_sig


def find_forward_1m_cross(boundary_ts, direction, bb_level, df_1m, max_hours=4):
    """
    Search FORWARD from 4H boundary through 1m bars for BB cross.
    Returns dict with cross info + 1m microstructure features, or None.
    """
    search_end = boundary_ts + pd.Timedelta(hours=max_hours)
    m1_window = df_1m.loc[boundary_ts:search_end - pd.Timedelta(minutes=1)]

    if len(m1_window) < 10:
        return None

    closes = m1_window["close"].values
    volumes = m1_window["volume"].values
    highs = m1_window["high"].values
    lows = m1_window["low"].values
    timestamps = m1_window.index

    # Find first 1m close crossing BB level
    if direction == 1:
        cross_mask = closes > bb_level
    else:
        cross_mask = closes < bb_level

    cross_indices = np.where(cross_mask)[0]
    if len(cross_indices) == 0:
        return None

    cross_idx = cross_indices[0]
    cross_close = closes[cross_idx]
    cross_ts = timestamps[cross_idx]

    # Honest entry: NEXT minute after cross
    entry_idx = cross_idx + 1
    if entry_idx >= len(closes):
        return None

    entry_price = closes[entry_idx]
    entry_ts = timestamps[entry_idx]

    # ── BB distance ──────────────────────────────────────────────────
    bb_dist_pct = abs(cross_close - bb_level) / max(bb_level, 1e-10) * 100

    # Hour offset in the 4H window (0-3)
    hours_from_boundary = (cross_ts - boundary_ts).total_seconds() / 3600
    cross_hour_offset = min(int(hours_from_boundary), 3)

    # Cross minute within its hour
    cross_minute = cross_ts.minute

    # ── FIXED: RVOL at cross ────────────────────────────────────────
    # Use median of prior 60 1m bars (more robust than mean)
    lookback_start = max(0, cross_idx - 60)
    if cross_idx > 5:
        prior_vols = volumes[lookback_start:cross_idx]
        prior_vol_median = np.median(prior_vols) if len(prior_vols) > 0 else np.nan
    else:
        prior_vol_median = np.nan

    if prior_vol_median is not None and not np.isnan(prior_vol_median) and prior_vol_median > 0:
        rvol_at_cross = float(np.clip(volumes[cross_idx] / prior_vol_median, 0, 100))
    else:
        rvol_at_cross = np.nan

    # ── Volume surge: max vol in 5 bars pre-cross / 60m median ──────
    if cross_idx >= 5 and prior_vol_median is not None and not np.isnan(prior_vol_median) and prior_vol_median > 0:
        pre_cross_max_vol = np.max(volumes[max(0, cross_idx - 5):cross_idx])
        volume_surge_1m = float(np.clip(pre_cross_max_vol / prior_vol_median, 0, 100))
    else:
        volume_surge_1m = np.nan

    # ── Cross speed (1st derivative): 1m return at cross ────────────
    if cross_idx > 0:
        cross_speed = (closes[cross_idx] - closes[cross_idx - 1]) / max(closes[cross_idx - 1], 1e-10) * 100
    else:
        cross_speed = 0.0

    # ── Price acceleration (2nd derivative) ─────────────────────────
    if cross_idx >= 2:
        ret_1 = (closes[cross_idx] - closes[cross_idx - 1]) / max(closes[cross_idx - 1], 1e-10)
        ret_2 = (closes[cross_idx - 1] - closes[cross_idx - 2]) / max(closes[cross_idx - 2], 1e-10)
        price_acceleration = (ret_1 - ret_2) * 10000  # in bps
    else:
        price_acceleration = 0.0

    # ── ATR expansion on 1m (fast/slow ratio) ───────────────────────
    if cross_idx >= 31:
        # True range on 1m bars (need 30 bars + 1 prior close, so cross_idx >= 31)
        tr_1m = np.maximum(
            highs[cross_idx - 30:cross_idx] - lows[cross_idx - 30:cross_idx],
            np.maximum(
                np.abs(highs[cross_idx - 30:cross_idx] - closes[cross_idx - 31:cross_idx - 1]),
                np.abs(lows[cross_idx - 30:cross_idx] - closes[cross_idx - 31:cross_idx - 1])
            )
        )
        fast_atr = np.mean(tr_1m[-5:])    # ATR(5)
        slow_atr = np.mean(tr_1m)          # ATR(30)
        atr_expansion_1m = float(np.clip(fast_atr / max(slow_atr, 1e-10), 0, 20))
    else:
        atr_expansion_1m = np.nan

    # ── VWAP deviation ──────────────────────────────────────────────
    if cross_idx >= 60:
        # VWAP over prior 60 minutes
        lookback = slice(cross_idx - 60, cross_idx)
        typical_price = (highs[lookback] + lows[lookback] + closes[lookback]) / 3
        vwap_vol = volumes[lookback]
        total_vol = np.sum(vwap_vol)
        if total_vol > 0:
            vwap = np.sum(typical_price * vwap_vol) / total_vol
            # Normalize by local ATR
            local_atr = np.mean(highs[lookback] - lows[lookback])
            if local_atr > 0:
                vwap_deviation = (cross_close - vwap) / local_atr
                vwap_deviation = float(np.clip(vwap_deviation, -10, 10))
            else:
                vwap_deviation = np.nan
        else:
            vwap_deviation = np.nan
    else:
        vwap_deviation = np.nan

    # ── Bid-ask proxy: spread from 1m bar ───────────────────────────
    bar_range = highs[cross_idx] - lows[cross_idx]
    bid_ask_proxy = bar_range / max(closes[cross_idx], 1e-10) * 100  # as pct
    bid_ask_proxy = float(np.clip(bid_ask_proxy, 0, 10))

    # ── Momentum persistence ────────────────────────────────────────
    if cross_idx >= 10:
        recent_rets = np.diff(closes[cross_idx - 10:cross_idx + 1])
        if direction == 1:
            momentum_persistence = float(np.mean(recent_rets > 0))
        else:
            momentum_persistence = float(np.mean(recent_rets < 0))
    else:
        momentum_persistence = np.nan

    return {
        "cross_ts": cross_ts,
        "entry_ts": entry_ts,
        "entry_price": entry_price,
        "cross_minute": cross_minute,
        "cross_hour_offset": cross_hour_offset,
        "bb_dist_pct": bb_dist_pct,
        "rvol_at_cross": rvol_at_cross,
        "volume_surge_1m": volume_surge_1m,
        "cross_speed": cross_speed,
        "price_acceleration": price_acceleration,
        "atr_expansion_1m": atr_expansion_1m,
        "vwap_deviation": vwap_deviation,
        "bid_ask_proxy": bid_ask_proxy,
        "momentum_persistence": momentum_persistence,
    }


def compute_trade_outcome(entry_price, entry_ts, direction, df_1m, atr):
    """Simulate trade outcome using ATR-based exits. Returns (winner, pnl)."""
    hold_end = entry_ts + pd.Timedelta(hours=MAX_HOLD_HOURS)
    m1_hold = df_1m.loc[entry_ts:hold_end]
    if len(m1_hold) < 10:
        return 0, 0.0

    hold_closes = m1_hold["close"].values
    hold_highs = m1_hold["high"].values
    hold_lows = m1_hold["low"].values

    if atr is not None and atr > 0:
        tp_level = entry_price + direction * TP_ATR_MULT * atr
        initial_sl = entry_price - direction * TRAIL_ATR_MULT * atr
        trail_dist = TRAIL_ATR_MULT * atr

        best_price = entry_price
        partial_taken = False
        pnl = 0.0

        for i in range(1, len(hold_closes)):
            bar_high, bar_low = hold_highs[i], hold_lows[i]
            if direction == 1:
                best_price = max(best_price, bar_high)
                trail_sl = best_price - trail_dist
                stop = max(initial_sl, trail_sl) if partial_taken else initial_sl
                if not partial_taken and bar_high >= tp_level:
                    pnl += 0.5 * (tp_level - entry_price) / entry_price
                    partial_taken = True
                if bar_low <= stop:
                    remaining = 0.5 if partial_taken else 1.0
                    pnl += remaining * (stop - entry_price) / entry_price
                    break
            else:
                best_price = min(best_price, bar_low)
                trail_sl = best_price + trail_dist
                stop = min(initial_sl, trail_sl) if partial_taken else initial_sl
                if not partial_taken and bar_low <= tp_level:
                    pnl += 0.5 * (entry_price - tp_level) / entry_price
                    partial_taken = True
                if bar_high >= stop:
                    remaining = 0.5 if partial_taken else 1.0
                    pnl += remaining * (entry_price - stop) / entry_price
                    break
        else:
            remaining = 0.5 if partial_taken else 1.0
            if direction == 1:
                pnl += remaining * (hold_closes[-1] - entry_price) / entry_price
            else:
                pnl += remaining * (entry_price - hold_closes[-1]) / entry_price

        return (1 if pnl > 0 else 0), pnl
    else:
        if direction == 1:
            max_move = (hold_highs.max() - entry_price) / entry_price
        else:
            max_move = (entry_price - hold_lows.min()) / entry_price
        return (1 if max_move > 0.005 else 0), max_move * direction


def precompute_token_features(df_1h, df_4h):
    """Precompute all context features for a token (known before signal)."""
    pc = {}

    # BB bandwidth
    bbw = (df_4h["bb_upper"] - df_4h["bb_lower"]) / df_4h["bb_mid"].clip(lower=1e-10) * 100
    bbw_shifted = bbw.shift(1)
    pc["bb_bandwidth_pct"] = bbw_shifted.reindex(df_1h.index, method="ffill")

    bbw_pctile = bbw_shifted.rolling(120, min_periods=20).apply(
        lambda x: (x.iloc[-1] <= x).mean() * 100 if len(x) > 0 else np.nan, raw=False
    )
    pc["bbw_percentile"] = bbw_pctile.reindex(df_1h.index, method="ffill")

    # Squeeze bars
    bbw_diff = bbw_shifted.diff()
    squeeze_count = pd.Series(0.0, index=df_4h.index)
    for j in range(1, len(squeeze_count)):
        if bbw_diff.iloc[j] < 0:
            squeeze_count.iloc[j] = squeeze_count.iloc[j - 1] + 1
        else:
            squeeze_count.iloc[j] = 0
    pc["squeeze_bars"] = squeeze_count.reindex(df_1h.index, method="ffill")

    # NR7: narrowest 4H range of last 7 bars
    range_4h = (df_4h["high"] - df_4h["low"]).shift(1)  # prior bar range
    min_range_7 = range_4h.rolling(7, min_periods=7).min()
    nr7_flag = (range_4h == min_range_7).astype(float)
    pc["nr7_flag"] = nr7_flag.reindex(df_1h.index, method="ffill")

    # ATR on 1H
    atr_1h = compute_atr_1h(df_1h)
    atr_shifted = atr_1h.shift(1)
    atr_pctile = atr_shifted.rolling(100, min_periods=20).apply(
        lambda x: (x.iloc[-1] <= x).mean() * 100 if len(x) > 0 else np.nan, raw=False
    )
    pc["atr_percentile"] = atr_pctile
    pc["atr_1h_raw"] = atr_1h

    atr_sma = atr_shifted.rolling(20, min_periods=10).mean()
    pc["atr_expansion"] = atr_shifted / atr_sma.clip(lower=1e-10)

    # RSI 1H
    close_1h = df_1h["close"]
    delta = close_1h.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs = gain / loss.clip(lower=1e-10)
    pc["rsi_1h"] = (100 - (100 / (1 + rs))).shift(1)

    # RSI 4H
    close_4h = df_4h["close"]
    delta_4h = close_4h.diff()
    gain_4h = delta_4h.clip(lower=0).rolling(14, min_periods=14).mean()
    loss_4h = (-delta_4h.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs_4h = gain_4h / loss_4h.clip(lower=1e-10)
    rsi_4h = (100 - (100 / (1 + rs_4h))).shift(1)
    pc["rsi_4h"] = rsi_4h.reindex(df_1h.index, method="ffill")

    # EMA trend
    ema50 = close_1h.ewm(span=50, min_periods=50).mean()
    ema200 = close_1h.ewm(span=200, min_periods=200).mean()
    pc["ema_trend_1h"] = (ema50 > ema200).astype(float).shift(1)

    # Momentum
    mom_abs = compute_mom_4h(df_4h).abs().shift(1)
    pc["mom_strength"] = mom_abs.reindex(df_1h.index, method="ffill")

    # Prior 4H return
    prior_4h_ret = (df_4h["close"] - df_4h["close"].shift(1)) / df_4h["close"].shift(1).clip(lower=1e-10) * 100
    pc["prior_4h_return"] = prior_4h_ret.shift(1).reindex(df_1h.index, method="ffill")

    # Prior 4H volume ratio
    vol_4h = df_4h["volume"]
    vol_4h_sma = vol_4h.rolling(20, min_periods=10).mean()
    pc["prior_4h_vol_ratio"] = (vol_4h / vol_4h_sma.clip(lower=1e-9)).shift(1).reindex(df_1h.index, method="ffill")

    # Prior hour return and range
    close_1h_shifted = close_1h.shift(1)
    open_1h = df_1h["open"]
    pc["prior_hour_return"] = ((open_1h - close_1h_shifted) / close_1h_shifted.clip(lower=1e-10) * 100).shift(1)
    prior_range = (df_1h["high"] - df_1h["low"]) / close_1h.clip(lower=1e-10) * 100
    pc["prior_hour_range_pct"] = prior_range.shift(1)

    return pc


def safe_get(series, ts):
    if series is None:
        return np.nan
    if ts in series.index:
        return float(series.loc[ts])
    mask = series.index <= ts
    if mask.any():
        return float(series.loc[series.index[mask][-1]])
    return np.nan


class PurgedTimeSeriesSplit:
    def __init__(self, n_splits=5, purge_gap_hours=24):
        self.n_splits = n_splits
        self.purge_gap_hours = purge_gap_hours

    def split(self, X, timestamps):
        n = len(X)
        fold_size = n // (self.n_splits + 1)
        for i in range(self.n_splits):
            train_end = fold_size * (i + 1)
            val_start = train_end
            val_end = min(train_end + fold_size, n)
            if timestamps is not None and self.purge_gap_hours > 0:
                val_start_ts = timestamps[val_start]
                purge_cutoff = val_start_ts - pd.Timedelta(hours=self.purge_gap_hours)
                train_mask = timestamps[:train_end] < purge_cutoff
                train_idx = np.where(train_mask)[0]
            else:
                train_idx = np.arange(train_end)
            val_idx = np.arange(val_start, val_end)
            if len(train_idx) < 50 or len(val_idx) < 20:
                continue
            yield train_idx, val_idx


# ── Main Pipeline ──────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    print("=" * 80)
    print("s503 ML Entry Filter v2 — Enhanced 1m Microstructure Features")
    print("=" * 80)

    # ── Discover tokens ──────────────────────────────────────────────
    tokens_1m = {f.replace("_1m.parquet", "") for f in os.listdir(DIR_1M) if f.endswith(".parquet")}
    tokens_1h = {f.replace("_1h.parquet", "") for f in os.listdir(DIR_1H) if f.endswith(".parquet")}
    common_tokens = sorted(tokens_1m & tokens_1h)
    print(f"\nTokens with both 1m and 1h data: {len(common_tokens)}")

    # ── Signal Detection + Feature Extraction ────────────────────────
    all_records = []
    token_counts = {}
    skipped = 0

    for i, token in enumerate(common_tokens):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"\n  [{i+1}/{len(common_tokens)}] {token}...")

        try:
            df_1h = load_parquet(os.path.join(DIR_1H, f"{token}_1h.parquet"))
            df_1m = load_parquet(os.path.join(DIR_1M, f"{token}_1m.parquet"))
        except Exception:
            skipped += 1
            continue

        if len(df_1h) < (BB_PERIOD * 4 + MOM_BARS_4H + 200):
            skipped += 1
            continue

        df_4h = resample_1h_to_4h(df_1h)
        if len(df_4h) < BB_PERIOD + MOM_BARS_4H + 5:
            skipped += 1
            continue

        df_4h = compute_bb(df_4h)
        signals = detect_4h_boundary_signals(df_1h, df_4h)
        if signals.empty:
            continue

        pc = precompute_token_features(df_1h, df_4h)
        last_sig_ts = None
        n_sigs = 0

        for boundary_ts, row in signals.iterrows():
            direction = int(row["direction"])
            bb_level = row["bb_level"]

            cross_info = find_forward_1m_cross(boundary_ts, direction, bb_level, df_1m)
            if cross_info is None:
                continue

            entry_price = cross_info["entry_price"]
            entry_ts = cross_info["entry_ts"]

            atr_val = safe_get(pc.get("atr_1h_raw"), boundary_ts)
            atr = atr_val if not np.isnan(atr_val) else None

            winner, pnl = compute_trade_outcome(entry_price, entry_ts, direction, df_1m, atr)

            hours_since = (boundary_ts - last_sig_ts).total_seconds() / 3600 if last_sig_ts else 9999.0
            last_sig_ts = boundary_ts

            record = {
                "timestamp": boundary_ts,
                "token": token,
                "direction": direction,
                "entry_price": entry_price,
                # Pre-signal features
                "bb_bandwidth_pct": safe_get(pc["bb_bandwidth_pct"], boundary_ts),
                "bbw_percentile": safe_get(pc["bbw_percentile"], boundary_ts),
                "squeeze_bars": safe_get(pc["squeeze_bars"], boundary_ts),
                "nr7_flag": safe_get(pc["nr7_flag"], boundary_ts),
                "atr_percentile": safe_get(pc["atr_percentile"], boundary_ts),
                "rsi_1h": safe_get(pc["rsi_1h"], boundary_ts),
                "rsi_4h": safe_get(pc["rsi_4h"], boundary_ts),
                "ema_trend_1h": safe_get(pc["ema_trend_1h"], boundary_ts),
                "mom_strength": safe_get(pc["mom_strength"], boundary_ts),
                "prior_4h_return": safe_get(pc["prior_4h_return"], boundary_ts),
                "prior_4h_vol_ratio": safe_get(pc["prior_4h_vol_ratio"], boundary_ts),
                "atr_expansion": safe_get(pc["atr_expansion"], boundary_ts),
                "hour_of_day": boundary_ts.hour,
                "day_of_week": boundary_ts.dayofweek,
                # At-cross 1m microstructure (all from cross_info)
                "bb_dist_pct": cross_info["bb_dist_pct"],
                "cross_minute": cross_info["cross_minute"],
                "cross_hour_offset": cross_info["cross_hour_offset"],
                "rvol_at_cross": cross_info["rvol_at_cross"],
                "volume_surge_1m": cross_info["volume_surge_1m"],
                "cross_speed": cross_info["cross_speed"],
                "price_acceleration": cross_info["price_acceleration"],
                "atr_expansion_1m": cross_info["atr_expansion_1m"],
                "vwap_deviation": cross_info["vwap_deviation"],
                "bid_ask_proxy": cross_info["bid_ask_proxy"],
                "momentum_persistence": cross_info["momentum_persistence"],
                "prior_hour_return": safe_get(pc["prior_hour_return"], boundary_ts),
                "prior_hour_range_pct": safe_get(pc["prior_hour_range_pct"], boundary_ts),
                "bars_since_last_signal": hours_since,
                # Target
                "winner": winner,
                "trade_pnl": pnl,
            }
            all_records.append(record)
            n_sigs += 1

        if n_sigs > 0:
            token_counts[token] = n_sigs

    print(f"\n  Skipped: {skipped} tokens")
    print(f"  Tokens with signals: {len(token_counts)}")
    print(f"  Total signals: {len(all_records)}")

    if len(all_records) < 200:
        print("\nERROR: Too few records. Aborting.")
        return

    # ── Build DataFrame ──────────────────────────────────────────────
    df = pd.DataFrame(all_records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    print(f"\n{'='*80}")
    print("Dataset Summary")
    print(f"{'='*80}")
    print(f"Total: {len(df)}, Date: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Winners: {df['winner'].sum()} ({df['winner'].mean()*100:.1f}%)")
    print(f"Long: {(df['direction']==1).sum()}, Short: {(df['direction']==-1).sum()}")
    print(f"Avg PnL: {df['trade_pnl'].mean()*100:.4f}%, Median: {df['trade_pnl'].median()*100:.4f}%")
    print(f"Tokens: {df['token'].nunique()}")

    # Check for extreme values in new features
    print(f"\n  Feature stats (new 1m features):")
    for col in ["rvol_at_cross", "volume_surge_1m", "atr_expansion_1m", "vwap_deviation",
                 "price_acceleration", "bid_ask_proxy", "momentum_persistence", "nr7_flag"]:
        s = df[col]
        print(f"    {col:25s}  mean={s.mean():.3f}  std={s.std():.3f}  "
              f"min={s.min():.3f}  max={s.max():.3f}  nan={s.isna().sum()}")

    # ── Train/Test Split ─────────────────────────────────────────────
    train_mask = df["timestamp"] < SPLIT_DATE
    test_mask = df["timestamp"] >= SPLIT_DATE
    df_train = df[train_mask].copy()
    df_test = df[test_mask].copy()

    print(f"\n{'='*80}")
    print(f"Train/Test Split (cutoff: {SPLIT_DATE})")
    print(f"{'='*80}")
    print(f"Train: {len(df_train)} ({df_train['winner'].mean()*100:.1f}% WR, {df_train['trade_pnl'].mean()*100:.4f}% avg PnL)")
    print(f"OOS:   {len(df_test)} ({df_test['winner'].mean()*100:.1f}% WR, {df_test['trade_pnl'].mean()*100:.4f}% avg PnL)")

    if len(df_train) < 100 or len(df_test) < 50:
        print("\nERROR: Insufficient data. Aborting.")
        return

    # ── Feature Prep ─────────────────────────────────────────────────
    X_train = df_train[FEATURE_COLS].copy()
    y_train = df_train["winner"].values
    y_train_pnl = df_train["trade_pnl"].values
    X_test = df_test[FEATURE_COLS].copy()
    y_test = df_test["winner"].values
    y_test_pnl = df_test["trade_pnl"].values

    train_medians = X_train.median()
    X_train = X_train.fillna(train_medians)
    X_test = X_test.fillna(train_medians)
    train_ts = pd.DatetimeIndex(df_train["timestamp"].values)

    # ── Adversarial Validation ───────────────────────────────────────
    print(f"\n{'='*80}")
    print("Adversarial Validation")
    print(f"{'='*80}")
    X_adv = pd.concat([X_train, X_test], axis=0).values
    y_adv = np.array([0]*len(X_train) + [1]*len(X_test))
    adv_model = CatBoostClassifier(iterations=100, depth=4, learning_rate=0.1, verbose=0, random_seed=42)
    n_adv = len(X_adv)
    perm = np.random.RandomState(42).permutation(n_adv)
    n_half = n_adv // 2
    adv_model.fit(X_adv[perm[:n_half]], y_adv[perm[:n_half]])
    adv_probs = adv_model.predict_proba(X_adv[perm[n_half:]])[:, 1]
    adv_auc = roc_auc_score(y_adv[perm[n_half:]], adv_probs)
    print(f"Adversarial AUC: {adv_auc:.4f}  (0.50=no shift, >0.65=significant)")
    if adv_auc > 0.65:
        adv_imp = pd.Series(adv_model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
        print("  WARNING: Significant shift! Top drivers:")
        for f, v in adv_imp.head(5).items():
            print(f"    {f:25s}  {v:.2f}")

    # ══════════════════════════════════════════════════════════════════
    # MODEL 1: CatBoost CLASSIFIER (binary win/lose)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("MODEL 1: CatBoost Classifier (Purged CV)")
    print(f"{'='*80}")

    param_grid = [
        {"iterations": 500, "depth": 4, "learning_rate": 0.05, "l2_leaf_reg": 3.0, "min_data_in_leaf": 20},
        {"iterations": 800, "depth": 5, "learning_rate": 0.03, "l2_leaf_reg": 5.0, "min_data_in_leaf": 30},
        {"iterations": 300, "depth": 3, "learning_rate": 0.1,  "l2_leaf_reg": 1.0, "min_data_in_leaf": 15},
        {"iterations": 600, "depth": 6, "learning_rate": 0.05, "l2_leaf_reg": 7.0, "min_data_in_leaf": 25},
    ]

    purged_cv = PurgedTimeSeriesSplit(n_splits=5, purge_gap_hours=PURGE_GAP_HOURS)
    X_train_arr = X_train.values
    X_test_arr = X_test.values

    best_auc = -1
    best_params = None
    best_fold_aucs = None

    for pi, params in enumerate(param_grid):
        fold_aucs = []
        for train_idx, val_idx in purged_cv.split(X_train_arr, train_ts):
            clf = CatBoostClassifier(random_seed=42, verbose=0, early_stopping_rounds=30,
                                     eval_metric="AUC", **params)
            clf.fit(Pool(X_train_arr[train_idx], y_train[train_idx]),
                    eval_set=Pool(X_train_arr[val_idx], y_train[val_idx]), verbose=False)
            probs = clf.predict_proba(X_train_arr[val_idx])[:, 1]
            try:
                auc = roc_auc_score(y_train[val_idx], probs)
            except ValueError:
                auc = 0.5
            fold_aucs.append(auc)
        mean_auc = np.mean(fold_aucs)
        print(f"  Config {pi+1}: AUC = {mean_auc:.4f} +/- {np.std(fold_aucs):.4f}  {[f'{a:.3f}' for a in fold_aucs]}")
        if mean_auc > best_auc:
            best_auc = mean_auc
            best_params = params
            best_fold_aucs = fold_aucs

    print(f"\nBest CV AUC: {best_auc:.4f} +/- {np.std(best_fold_aucs):.4f}")
    print(f"Best params: {best_params}")

    # Fit final classifier
    n_tr = len(X_train_arr)
    split_at = int(n_tr * 0.85)
    final_clf = CatBoostClassifier(random_seed=42, verbose=0, early_stopping_rounds=50,
                                    eval_metric="AUC", **best_params)
    final_clf.fit(Pool(X_train_arr[:split_at], y_train[:split_at]),
                  eval_set=Pool(X_train_arr[split_at:], y_train[split_at:]), verbose=False)

    # OOS classification
    test_probs = final_clf.predict_proba(X_test_arr)[:, 1]
    test_preds = (test_probs >= 0.5).astype(int)
    test_auc = roc_auc_score(y_test, test_probs)

    print(f"\nOOS ROC-AUC: {test_auc:.4f}")
    print(classification_report(y_test, test_preds, target_names=["Loser", "Winner"]))

    # Feature importance
    clf_imp = pd.Series(final_clf.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print("CatBoost Classifier Feature Importances:")
    for f, v in clf_imp.items():
        bar = "#" * int(v / max(clf_imp.max(), 1) * 30)
        print(f"  {f:25s}  {v:6.2f}  {bar}")

    # ══════════════════════════════════════════════════════════════════
    # MODEL 2: CatBoost REGRESSOR (continuous PnL target)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("MODEL 2: CatBoost Regressor (Predict PnL)")
    print(f"{'='*80}")

    reg_params = [
        {"iterations": 500, "depth": 4, "learning_rate": 0.05, "l2_leaf_reg": 5.0, "min_data_in_leaf": 30},
        {"iterations": 800, "depth": 5, "learning_rate": 0.03, "l2_leaf_reg": 10.0, "min_data_in_leaf": 40},
        {"iterations": 300, "depth": 3, "learning_rate": 0.1,  "l2_leaf_reg": 3.0, "min_data_in_leaf": 20},
    ]

    best_mae = 999
    best_reg_params = None
    for pi, params in enumerate(reg_params):
        fold_maes = []
        for train_idx, val_idx in purged_cv.split(X_train_arr, train_ts):
            reg = CatBoostRegressor(random_seed=42, verbose=0, early_stopping_rounds=30,
                                     eval_metric="MAE", **params)
            reg.fit(Pool(X_train_arr[train_idx], y_train_pnl[train_idx]),
                    eval_set=Pool(X_train_arr[val_idx], y_train_pnl[val_idx]), verbose=False)
            preds = reg.predict(X_train_arr[val_idx])
            mae = mean_absolute_error(y_train_pnl[val_idx], preds)
            fold_maes.append(mae)
        mean_mae = np.mean(fold_maes)
        print(f"  Config {pi+1}: MAE = {mean_mae:.6f} +/- {np.std(fold_maes):.6f}")
        if mean_mae < best_mae:
            best_mae = mean_mae
            best_reg_params = params

    print(f"\nBest CV MAE: {best_mae:.6f}")

    # Fit final regressor
    final_reg = CatBoostRegressor(random_seed=42, verbose=0, early_stopping_rounds=50,
                                   eval_metric="MAE", **best_reg_params)
    final_reg.fit(Pool(X_train_arr[:split_at], y_train_pnl[:split_at]),
                  eval_set=Pool(X_train_arr[split_at:], y_train_pnl[split_at:]), verbose=False)

    test_pnl_pred = final_reg.predict(X_test_arr)
    oos_mae = mean_absolute_error(y_test_pnl, test_pnl_pred)
    # Correlation between predicted and actual PnL
    pnl_corr = np.corrcoef(test_pnl_pred, y_test_pnl)[0, 1]
    print(f"OOS MAE: {oos_mae:.6f}")
    print(f"OOS PnL correlation (pred vs actual): {pnl_corr:.4f}")

    reg_imp = pd.Series(final_reg.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print("\nCatBoost Regressor Feature Importances:")
    for f, v in reg_imp.items():
        bar = "#" * int(v / max(reg_imp.max(), 1) * 30)
        print(f"  {f:25s}  {v:6.2f}  {bar}")

    # ══════════════════════════════════════════════════════════════════
    # THRESHOLD ANALYSIS — Classifier
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("THRESHOLD ANALYSIS — Classifier (win probability)")
    print(f"{'='*80}")
    baseline_wr = y_test.mean() * 100
    total_test = len(y_test)

    print(f"\nBaseline: {total_test} trades, WR={baseline_wr:.1f}%, avg PnL={y_test_pnl.mean()*100:.4f}%")
    print(f"\n{'Thresh':>7s} | {'Kept':>7s} | {'%Kept':>6s} | {'WR':>6s} | {'Lift':>7s} | "
          f"{'AvgPnL':>9s} | {'TotPnL':>10s} | {'PF':>6s} | {'MaxDD':>7s}")
    print("-" * 90)

    for threshold in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept < 10:
            continue
        wr = y_test[mask].mean() * 100
        lift = wr - baseline_wr
        filt_pnl = y_test_pnl[mask]
        avg_pnl = filt_pnl.mean() * 100
        total_pnl = filt_pnl.sum() * 100
        gw = filt_pnl[filt_pnl > 0].sum()
        gl = abs(filt_pnl[filt_pnl <= 0].sum())
        pf = gw / max(gl, 1e-10)
        # Quick DD estimate
        eq = 100000.0
        peak = eq
        mdd = 0.0
        for r in filt_pnl:
            eq += eq * 0.02 * r
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak)
        pct_kept = n_kept / total_test * 100
        print(f"  {threshold:>5.2f} | {n_kept:>7d} | {pct_kept:>5.1f}% | {wr:>5.1f}% | "
              f"{lift:>+6.1f}pp | {avg_pnl:>+8.4f}% | {total_pnl:>+9.1f}% | "
              f"{pf:>5.2f} | {mdd*100:>6.1f}%")

    # ══════════════════════════════════════════════════════════════════
    # QUANTILE ANALYSIS — Regressor (predicted PnL)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("QUANTILE ANALYSIS — Regressor (select by predicted PnL)")
    print(f"{'='*80}")

    print(f"\n{'Quantile':>10s} | {'Kept':>7s} | {'%Kept':>6s} | {'WR':>6s} | {'Lift':>7s} | "
          f"{'AvgPnL':>9s} | {'TotPnL':>10s} | {'PF':>6s}")
    print("-" * 80)

    for q in [0.0, 0.25, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90]:
        if q == 0.0:
            cutoff = -999
            label = "ALL"
        else:
            cutoff = np.quantile(test_pnl_pred, q)
            label = f"top {(1-q)*100:.0f}%"
        mask = test_pnl_pred >= cutoff
        n_kept = mask.sum()
        if n_kept < 10:
            continue
        wr = y_test[mask].mean() * 100
        lift = wr - baseline_wr
        filt_pnl = y_test_pnl[mask]
        avg_pnl = filt_pnl.mean() * 100
        total_pnl = filt_pnl.sum() * 100
        gw = filt_pnl[filt_pnl > 0].sum()
        gl = abs(filt_pnl[filt_pnl <= 0].sum())
        pf = gw / max(gl, 1e-10)
        print(f"  {label:>8s} | {n_kept:>7d} | {n_kept/total_test*100:>5.1f}% | {wr:>5.1f}% | "
              f"{lift:>+6.1f}pp | {avg_pnl:>+8.4f}% | {total_pnl:>+9.1f}% | {pf:>5.2f}")

    # ══════════════════════════════════════════════════════════════════
    # COMBINED: Classifier + Regressor ensemble
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("COMBINED: Classifier prob + Regressor PnL (both must agree)")
    print(f"{'='*80}")

    for clf_thresh in [0.50, 0.55, 0.60]:
        for reg_q in [0.50, 0.60, 0.70]:
            reg_cutoff = np.quantile(test_pnl_pred, reg_q)
            mask = (test_probs >= clf_thresh) & (test_pnl_pred >= reg_cutoff)
            n_kept = mask.sum()
            if n_kept < 30:
                continue
            wr = y_test[mask].mean() * 100
            lift = wr - baseline_wr
            filt_pnl = y_test_pnl[mask]
            avg_pnl = filt_pnl.mean() * 100
            gw = filt_pnl[filt_pnl > 0].sum()
            gl = abs(filt_pnl[filt_pnl <= 0].sum())
            pf = gw / max(gl, 1e-10)
            print(f"  clf>={clf_thresh:.2f} & reg>=q{reg_q:.0%}: {n_kept:>6d} trades, "
                  f"WR={wr:.1f}% ({lift:+.1f}pp), avg={avg_pnl:+.4f}%, PF={pf:.2f}")

    # ══════════════════════════════════════════════════════════════════
    # WINNER vs LOSER — Feature Deep Dive (OOS)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("WINNER vs LOSER — Feature Comparison (OOS)")
    print(f"{'='*80}")

    winners = df_test[df_test["winner"] == 1]
    losers = df_test[df_test["winner"] == 0]

    print(f"\n{'Feature':25s} | {'Winners':>10s} | {'Losers':>10s} | {'Delta':>10s} | {'Cohen d':>8s} | {'Sig':>4s}")
    print("-" * 80)
    for col in FEATURE_COLS:
        w_mean = winners[col].mean()
        l_mean = losers[col].mean()
        delta = w_mean - l_mean
        pooled_std = df_test[col].std()
        cohens_d = abs(delta) / max(pooled_std, 1e-10)
        sig = "***" if cohens_d > 0.10 else "**" if cohens_d > 0.05 else "*" if cohens_d > 0.02 else ""
        print(f"  {col:23s} | {w_mean:>10.4f} | {l_mean:>10.4f} | {delta:>+10.4f} | {cohens_d:>7.4f} | {sig:>4s}")

    # ── SHAP ─────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SHAP Feature Importance")
    print(f"{'='*80}")
    try:
        import shap
        explainer = shap.TreeExplainer(final_clf)
        sample_n = min(2000, len(X_train_arr))
        sample_idx = np.random.RandomState(42).choice(len(X_train_arr), sample_n, replace=False)
        shap_vals = explainer.shap_values(X_train_arr[sample_idx])
        shap_imp = pd.Series(np.abs(shap_vals).mean(axis=0), index=FEATURE_COLS).sort_values(ascending=False)
        print("\nClassifier SHAP (mean |SHAP|):")
        for f, v in shap_imp.items():
            bar = "#" * int(v / max(shap_imp.max(), 1e-10) * 30)
            print(f"  {f:25s}  {v:.5f}  {bar}")
    except Exception as e:
        print(f"  SHAP failed: {e}")

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("FINAL SUMMARY")
    print(f"{'='*80}")
    print(f"\nClassifier: CV AUC={best_auc:.4f}, OOS AUC={test_auc:.4f}, gap={best_auc-test_auc:+.4f}")
    print(f"Regressor:  CV MAE={best_mae:.6f}, OOS MAE={oos_mae:.6f}, PnL corr={pnl_corr:.4f}")
    print(f"Adversarial AUC: {adv_auc:.4f}")
    print(f"\nBaseline: {total_test} trades, WR={baseline_wr:.1f}%, avg PnL={y_test_pnl.mean()*100:.4f}%")

    # Find best practical threshold
    best_metric = -999
    best_thresh_info = None
    for threshold in np.arange(0.40, 0.80, 0.05):
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept >= max(30, total_test * 0.05):
            filt_pnl = y_test_pnl[mask]
            avg = filt_pnl.mean()
            wr = y_test[mask].mean()
            metric = avg * 10000 + (wr - y_test.mean()) * 100
            if metric > best_metric:
                best_metric = metric
                best_thresh_info = (threshold, n_kept, wr*100, avg*100, filt_pnl.sum()*100)

    if best_thresh_info:
        t, n, wr, avg, tot = best_thresh_info
        print(f"\nBest classifier threshold: p>={t:.2f}")
        print(f"  Kept: {n} ({n/total_test*100:.1f}%), WR={wr:.1f}%, avg PnL={avg:+.4f}%, total={tot:+.1f}%")

    # Save
    os.makedirs("results/v4", exist_ok=True)
    results = {
        "model": "CatBoost_v2_enhanced_1m_features",
        "clf_cv_auc": float(best_auc),
        "clf_oos_auc": float(test_auc),
        "reg_cv_mae": float(best_mae),
        "reg_oos_mae": float(oos_mae),
        "reg_pnl_corr": float(pnl_corr),
        "adversarial_auc": float(adv_auc),
        "n_train": len(df_train),
        "n_test": len(df_test),
        "n_features": len(FEATURE_COLS),
        "features": FEATURE_COLS,
        "clf_feature_importances": {k: float(v) for k, v in clf_imp.items()},
        "reg_feature_importances": {k: float(v) for k, v in reg_imp.items()},
    }
    with open("results/v4/s503_ml_entry_filter_v2.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to results/v4/s503_ml_entry_filter_v2.json")
    print(f"Runtime: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
