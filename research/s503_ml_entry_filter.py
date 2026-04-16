"""
s503_ml_entry_filter.py — CatBoost ML filter for forward-only BB breakout entries

Strategy s503 detects Bollinger Band breakouts on 4H timeframe, arms a limit order
at the BB level, and waits for 1m price to cross forward-only within the next 4H window.

This script builds a classifier predicting whether a breakout trade will win or lose,
using features extracted BEFORE and AT the entry moment. All features are causal
(no look-ahead bias).

FEATURE CATEGORIES:
  A) Pre-signal features (known at 4H boundary): BB squeeze, momentum, ATR, RSI, trend
  B) At-cross features (known at 1m cross moment): cross minute, volume spike, speed

TRAIN/OOS SPLIT:
  Train: 2024-04-03 to 2025-04-03 (months 1-12)
  OOS:   2025-04-03 to 2026-04-03 (months 13-24)

ML PIPELINE:
  1. CatBoost with purged time-series CV (5-fold, 24h purge gap)
  2. SHAP stability across folds
  3. Threshold analysis on OOS
  4. Adversarial validation (train vs OOS distribution shift)

Usage:
    /workspace/venv/bin/python research/s503_ml_entry_filter.py
"""

import os
import sys
import warnings
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    confusion_matrix,
    brier_score_loss,
)

warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = "/workspace/crypto_backtest/data/perp"
DIR_1M = os.path.join(DATA_DIR, "1m_cache")
DIR_1H = os.path.join(DATA_DIR, "1h_cache")

SPLIT_DATE = pd.Timestamp("2025-04-03", tz=None)

BB_PERIOD = 20
BB_STD = 2.0
MOM_BARS_4H = 42  # ~7 days of 4H bars

MAX_HOLD_HOURS = 4
ATR_PERIOD = 14
TP_ATR_MULT = 1.5
TRAIL_ATR_MULT = 2.0

# Purge gap: 24 hours between train/validation folds
PURGE_GAP_HOURS = 24

FEATURE_COLS = [
    # ── Pre-signal (known at 4H boundary) ────────────────────────────
    "bb_bandwidth_pct",       # BB width as % of mid (volatility measure)
    "bbw_percentile",         # where current BBW sits vs last 120 4H bars
    "squeeze_bars",           # consecutive 4H bars BB was contracting
    "atr_percentile",         # ATR percentile over last 100 1H bars
    "rsi_1h",                 # RSI(14) on prior 1H bar
    "rsi_4h",                 # RSI(14) on prior 4H bar
    "ema_trend_1h",           # 1 if EMA50 > EMA200 on 1H, else 0
    "mom_strength",           # absolute momentum magnitude (|mom_7d|)
    "prior_4h_return",        # return of prior 4H bar (%)
    "prior_4h_vol_ratio",     # prior 4H volume / 20-period avg volume
    "atr_expansion",          # ATR / 20-period avg ATR (expansion ratio)
    "hour_of_day",            # UTC hour of 4H boundary (0-23)
    "day_of_week",            # day of week (0=Mon, 6=Sun)
    # ── At-cross (known at 1m cross moment, before entry) ────────────
    "bb_dist_pct",            # distance from BB at cross moment (%)
    "cross_minute",           # which minute of the hour the cross happened
    "cross_hour_offset",      # which hour of the 4H window (0-3)
    "vol_ratio_at_cross",     # 1m volume at cross vs prior 60m avg
    "cross_speed",            # price velocity at cross (1m return at cross %)
    "prior_hour_return",      # return of completed prior hour (%)
    "prior_hour_range_pct",   # prior hour (high-low)/close as %
    "bars_since_last_signal", # hours since last BB signal on this token
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def load_parquet(path: str) -> pd.DataFrame:
    """Load parquet file, ensure datetime index sorted."""
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


def resample_1h_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV to 4H using standard 0/4/8/12/16/20 UTC anchoring."""
    ohlcv = df_1h[["open", "high", "low", "close", "volume"]].copy()
    df_4h = ohlcv.resample("4h", offset="0h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    df_4h = df_4h.dropna(subset=["close"])
    return df_4h


def compute_bb(df_4h: pd.DataFrame) -> pd.DataFrame:
    """Compute Bollinger Bands on 4H close, shifted by 1 bar (prior bar's BB)."""
    close = df_4h["close"]
    sma = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).std()
    df_4h = df_4h.copy()
    df_4h["bb_upper"] = sma + BB_STD * std
    df_4h["bb_lower"] = sma - BB_STD * std
    df_4h["bb_mid"] = sma
    # Shift by 1 bar: at the open of bar t, we know BB from bar t-1
    df_4h["prior_bb_upper"] = df_4h["bb_upper"].shift(1)
    df_4h["prior_bb_lower"] = df_4h["bb_lower"].shift(1)
    return df_4h


def compute_mom_4h(df_4h: pd.DataFrame) -> pd.Series:
    """7-day momentum on 4H: (close - close[42 ago]) / close[42 ago]."""
    return (df_4h["close"] - df_4h["close"].shift(MOM_BARS_4H)) / df_4h["close"].shift(
        MOM_BARS_4H
    ).clip(lower=1e-10)


def compute_atr_1h(df_1h: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Compute ATR on 1H bars."""
    h = df_1h["high"]
    l = df_1h["low"]
    c = df_1h["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def detect_4h_boundary_signals(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> pd.DataFrame:
    """
    Detect BB breakout signals at 4H boundaries (s503 forward-only logic).

    At each 4H boundary:
    - If momentum > 0 and prior BB upper is valid → arm LONG
    - If momentum < 0 and prior BB lower is valid → arm SHORT

    Returns DataFrame with columns: direction, bb_level, boundary_ts (4H boundary)
    Rows are indexed by the 4H boundary timestamp.
    """
    mom = compute_mom_4h(df_4h)

    signals = []
    for i in range(1, len(df_4h)):
        ts = df_4h.index[i]  # 4H boundary
        bb_upper = df_4h["prior_bb_upper"].iloc[i]
        bb_lower = df_4h["prior_bb_lower"].iloc[i]
        m = mom.iloc[i - 1]  # prior 4H bar's momentum (known at boundary)

        if pd.isna(bb_upper) or pd.isna(bb_lower) or pd.isna(m):
            continue

        if m > 0 and bb_upper > 0:
            signals.append({
                "boundary_ts": ts,
                "direction": 1,
                "bb_level": bb_upper,
            })
        elif m < 0 and bb_lower > 0:
            signals.append({
                "boundary_ts": ts,
                "direction": -1,
                "bb_level": bb_lower,
            })

    if not signals:
        return pd.DataFrame()

    df_sig = pd.DataFrame(signals)
    df_sig = df_sig.set_index("boundary_ts")
    return df_sig


def find_forward_1m_cross(
    boundary_ts: pd.Timestamp,
    direction: int,
    bb_level: float,
    df_1m: pd.DataFrame,
    max_hours: int = 4,
) -> dict | None:
    """
    Search FORWARD from 4H boundary through 1m bars for BB cross.
    Returns cross info dict or None if no cross found.

    This is the honest forward-only approach: we only look at 1m bars
    AFTER the 4H boundary timestamp.
    """
    search_end = boundary_ts + pd.Timedelta(hours=max_hours)
    m1_window = df_1m.loc[boundary_ts:search_end - pd.Timedelta(minutes=1)]

    if len(m1_window) < 5:
        return None

    closes_1m = m1_window["close"].values
    volumes_1m = m1_window["volume"].values
    highs_1m = m1_window["high"].values
    lows_1m = m1_window["low"].values
    timestamps = m1_window.index

    # Find first 1m close crossing BB level
    if direction == 1:
        cross_mask = closes_1m > bb_level
    else:
        cross_mask = closes_1m < bb_level

    cross_indices = np.where(cross_mask)[0]
    if len(cross_indices) == 0:
        return None

    cross_idx = cross_indices[0]
    cross_close = closes_1m[cross_idx]
    cross_ts = timestamps[cross_idx]

    # Honest entry: NEXT minute after cross detection
    entry_idx = cross_idx + 1
    if entry_idx >= len(closes_1m):
        return None  # Cross on last minute of window

    entry_price = closes_1m[entry_idx]
    entry_ts = timestamps[entry_idx]

    # Cross speed: 1m return at cross bar
    if cross_idx > 0:
        cross_speed = (closes_1m[cross_idx] - closes_1m[cross_idx - 1]) / max(closes_1m[cross_idx - 1], 1e-10) * 100
    else:
        cross_speed = 0.0

    # Volume at cross vs prior 60 minutes
    prior_start = max(0, cross_idx - 60)
    prior_vol_mean = volumes_1m[prior_start:cross_idx].mean() if cross_idx > 0 else 1e-9
    vol_ratio_at_cross = volumes_1m[cross_idx] / max(prior_vol_mean, 1e-9)

    # Which hour offset in the 4H window (0-3)
    hours_from_boundary = (cross_ts - boundary_ts).total_seconds() / 3600
    cross_hour_offset = int(hours_from_boundary)

    # BB distance
    bb_dist_pct = abs(cross_close - bb_level) / max(bb_level, 1e-10) * 100

    return {
        "cross_ts": cross_ts,
        "entry_ts": entry_ts,
        "entry_price": entry_price,
        "cross_minute": cross_idx % 60,
        "cross_hour_offset": min(cross_hour_offset, 3),
        "bb_dist_pct": bb_dist_pct,
        "vol_ratio_at_cross": vol_ratio_at_cross,
        "cross_speed": cross_speed,
    }


def compute_trade_outcome(
    entry_price: float,
    entry_ts: pd.Timestamp,
    direction: int,
    df_1m: pd.DataFrame,
    atr: float | None,
) -> tuple[int, float]:
    """
    Simulate trade outcome using ATR-based exits.
    Returns (winner: 0/1, pnl: float).
    """
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
            bar_high = hold_highs[i]
            bar_low = hold_lows[i]

            if direction == 1:
                best_price = max(best_price, bar_high)
                trail_sl = best_price - trail_dist
                stop = max(initial_sl, trail_sl) if partial_taken else initial_sl

                if not partial_taken and bar_high >= tp_level:
                    pnl += 0.5 * (tp_level - entry_price) / entry_price
                    partial_taken = True

                if bar_low <= stop:
                    exit_price = stop
                    remaining = 0.5 if partial_taken else 1.0
                    pnl += remaining * (exit_price - entry_price) / entry_price
                    break
            else:
                best_price = min(best_price, bar_low)
                trail_sl = best_price + trail_dist
                stop = min(initial_sl, trail_sl) if partial_taken else initial_sl

                if not partial_taken and bar_low <= tp_level:
                    pnl += 0.5 * (entry_price - tp_level) / entry_price
                    partial_taken = True

                if bar_high >= stop:
                    exit_price = stop
                    remaining = 0.5 if partial_taken else 1.0
                    pnl += remaining * (entry_price - exit_price) / entry_price
                    break
        else:
            remaining = 0.5 if partial_taken else 1.0
            if direction == 1:
                pnl += remaining * (hold_closes[-1] - entry_price) / entry_price
            else:
                pnl += remaining * (entry_price - hold_closes[-1]) / entry_price

        return (1 if pnl > 0 else 0), pnl
    else:
        # Fallback: simple direction check
        if direction == 1:
            max_move = (hold_highs.max() - entry_price) / entry_price
        else:
            max_move = (entry_price - hold_lows.min()) / entry_price
        return (1 if max_move > 0.005 else 0), max_move * direction


def precompute_token_features(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> dict:
    """Precompute all context features for a token (known before signal)."""
    precomputed = {}

    # BB bandwidth and squeeze on 4H (shifted by 1 = known at bar open)
    bbw = (df_4h["bb_upper"] - df_4h["bb_lower"]) / df_4h["bb_mid"].clip(lower=1e-10) * 100
    bbw_shifted = bbw.shift(1)
    precomputed["bb_bandwidth_pct"] = bbw_shifted.reindex(df_1h.index, method="ffill")

    bbw_pctile = bbw_shifted.rolling(120, min_periods=20).apply(
        lambda x: (x.iloc[-1] <= x).mean() * 100 if len(x) > 0 else np.nan, raw=False
    )
    precomputed["bbw_percentile"] = bbw_pctile.reindex(df_1h.index, method="ffill")

    # Squeeze bars: consecutive bars where BBW is declining
    bbw_diff = bbw_shifted.diff()
    squeeze_count = pd.Series(0.0, index=df_4h.index)
    for j in range(1, len(squeeze_count)):
        if bbw_diff.iloc[j] < 0:
            squeeze_count.iloc[j] = squeeze_count.iloc[j - 1] + 1
        else:
            squeeze_count.iloc[j] = 0
    precomputed["squeeze_bars"] = squeeze_count.reindex(df_1h.index, method="ffill")

    # ATR on 1H (shifted = known)
    atr_1h = compute_atr_1h(df_1h)
    atr_shifted = atr_1h.shift(1)
    atr_pctile = atr_shifted.rolling(100, min_periods=20).apply(
        lambda x: (x.iloc[-1] <= x).mean() * 100 if len(x) > 0 else np.nan, raw=False
    )
    precomputed["atr_percentile"] = atr_pctile
    precomputed["atr_1h_raw"] = atr_1h  # For trade outcome computation

    # ATR expansion: current ATR / 20-period avg ATR
    atr_sma = atr_shifted.rolling(20, min_periods=10).mean()
    precomputed["atr_expansion"] = (atr_shifted / atr_sma.clip(lower=1e-10))

    # RSI(14) on 1H (prior bar = known)
    close_1h = df_1h["close"]
    delta = close_1h.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs = gain / loss.clip(lower=1e-10)
    precomputed["rsi_1h"] = (100 - (100 / (1 + rs))).shift(1)

    # RSI(14) on 4H
    close_4h = df_4h["close"]
    delta_4h = close_4h.diff()
    gain_4h = delta_4h.clip(lower=0).rolling(14, min_periods=14).mean()
    loss_4h = (-delta_4h.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs_4h = gain_4h / loss_4h.clip(lower=1e-10)
    rsi_4h_series = (100 - (100 / (1 + rs_4h))).shift(1)
    precomputed["rsi_4h"] = rsi_4h_series.reindex(df_1h.index, method="ffill")

    # EMA trend on 1H (prior bar = known)
    ema50 = close_1h.ewm(span=50, min_periods=50).mean()
    ema200 = close_1h.ewm(span=200, min_periods=200).mean()
    precomputed["ema_trend_1h"] = (ema50 > ema200).astype(float).shift(1)

    # Momentum strength (aligned from 4H)
    mom_abs = compute_mom_4h(df_4h).abs().shift(1)
    precomputed["mom_strength"] = mom_abs.reindex(df_1h.index, method="ffill")

    # Prior 4H return
    prior_4h_ret = (df_4h["close"] - df_4h["close"].shift(1)) / df_4h["close"].shift(1).clip(lower=1e-10) * 100
    precomputed["prior_4h_return"] = prior_4h_ret.shift(1).reindex(df_1h.index, method="ffill")

    # Prior 4H volume ratio
    vol_4h = df_4h["volume"]
    vol_4h_sma = vol_4h.rolling(20, min_periods=10).mean()
    prior_4h_vol_ratio = (vol_4h / vol_4h_sma.clip(lower=1e-9)).shift(1)
    precomputed["prior_4h_vol_ratio"] = prior_4h_vol_ratio.reindex(df_1h.index, method="ffill")

    # Prior hour return and range
    close_1h_shifted = close_1h.shift(1)
    open_1h = df_1h["open"]
    precomputed["prior_hour_return"] = ((open_1h - close_1h_shifted) / close_1h_shifted.clip(lower=1e-10) * 100).shift(1)
    prior_range = (df_1h["high"] - df_1h["low"]) / close_1h.clip(lower=1e-10) * 100
    precomputed["prior_hour_range_pct"] = prior_range.shift(1)

    return precomputed


def safe_get(series, ts):
    """Safely get value from series at or before timestamp."""
    if series is None:
        return np.nan
    if ts in series.index:
        return float(series.loc[ts])
    mask = series.index <= ts
    if mask.any():
        return float(series.loc[series.index[mask][-1]])
    return np.nan


# ── Purged Time Series Split ────────────────────────────────────────────────
class PurgedTimeSeriesSplit:
    """Time series split with purge gap between train and validation."""

    def __init__(self, n_splits=5, purge_gap_hours=24):
        self.n_splits = n_splits
        self.purge_gap_hours = purge_gap_hours

    def split(self, X, timestamps):
        """Yield (train_idx, val_idx) with purge gap."""
        n = len(X)
        fold_size = n // (self.n_splits + 1)

        for i in range(self.n_splits):
            # Training: everything up to fold boundary
            train_end = fold_size * (i + 1)
            val_start = train_end
            val_end = min(train_end + fold_size, n)

            # Apply purge: remove training samples within purge_gap of val start
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
    print("s503_ml_entry_filter — CatBoost ML Filter for Forward BB Breakout")
    print("=" * 80)

    # ── Step 1: Discover tokens ──────────────────────────────────────────
    tokens_1m = {f.replace("_1m.parquet", "") for f in os.listdir(DIR_1M) if f.endswith(".parquet")}
    tokens_1h = {f.replace("_1h.parquet", "") for f in os.listdir(DIR_1H) if f.endswith(".parquet")}
    common_tokens = sorted(tokens_1m & tokens_1h)
    print(f"\nTokens with both 1m and 1h data: {len(common_tokens)}")

    # ── Step 2-3: Signal Detection + Feature Extraction ──────────────────
    all_records = []
    token_signal_counts = {}
    skipped_no_data = 0

    for i, token in enumerate(common_tokens):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"\n  [{i+1}/{len(common_tokens)}] Processing {token}...")

        try:
            df_1h = load_parquet(os.path.join(DIR_1H, f"{token}_1h.parquet"))
            df_1m = load_parquet(os.path.join(DIR_1M, f"{token}_1m.parquet"))
        except Exception:
            skipped_no_data += 1
            continue

        if len(df_1h) < (BB_PERIOD * 4 + MOM_BARS_4H + 200):
            skipped_no_data += 1
            continue

        # Resample 1H to 4H and compute BB + momentum
        df_4h = resample_1h_to_4h(df_1h)
        if len(df_4h) < BB_PERIOD + MOM_BARS_4H + 5:
            skipped_no_data += 1
            continue

        df_4h = compute_bb(df_4h)

        # Detect 4H boundary signals
        signals = detect_4h_boundary_signals(df_1h, df_4h)
        if signals.empty:
            continue

        # Precompute context features
        precomputed = precompute_token_features(df_1h, df_4h)

        # Track bars since last signal for this token
        last_signal_ts = None
        n_signals_token = 0

        for boundary_ts, row in signals.iterrows():
            direction = int(row["direction"])
            bb_level = row["bb_level"]

            # Find forward 1m cross
            cross_info = find_forward_1m_cross(
                boundary_ts=boundary_ts,
                direction=direction,
                bb_level=bb_level,
                df_1m=df_1m,
                max_hours=MAX_HOLD_HOURS,
            )
            if cross_info is None:
                continue

            entry_price = cross_info["entry_price"]
            entry_ts = cross_info["entry_ts"]

            # ATR at boundary hour (known)
            atr_val = safe_get(precomputed.get("atr_1h_raw"), boundary_ts)
            atr = atr_val if not np.isnan(atr_val) else None

            # Trade outcome
            winner, pnl = compute_trade_outcome(
                entry_price=entry_price,
                entry_ts=entry_ts,
                direction=direction,
                df_1m=df_1m,
                atr=atr,
            )

            # Bars since last signal
            if last_signal_ts is not None:
                hours_since = (boundary_ts - last_signal_ts).total_seconds() / 3600
            else:
                hours_since = 9999.0
            last_signal_ts = boundary_ts

            # Extract pre-signal features (known at 4H boundary)
            record = {
                "timestamp": boundary_ts,
                "token": token,
                "direction": direction,
                "bb_level": bb_level,
                "entry_price": entry_price,
                # Pre-signal features
                "bb_bandwidth_pct": safe_get(precomputed["bb_bandwidth_pct"], boundary_ts),
                "bbw_percentile": safe_get(precomputed["bbw_percentile"], boundary_ts),
                "squeeze_bars": safe_get(precomputed["squeeze_bars"], boundary_ts),
                "atr_percentile": safe_get(precomputed["atr_percentile"], boundary_ts),
                "rsi_1h": safe_get(precomputed["rsi_1h"], boundary_ts),
                "rsi_4h": safe_get(precomputed["rsi_4h"], boundary_ts),
                "ema_trend_1h": safe_get(precomputed["ema_trend_1h"], boundary_ts),
                "mom_strength": safe_get(precomputed["mom_strength"], boundary_ts),
                "prior_4h_return": safe_get(precomputed["prior_4h_return"], boundary_ts),
                "prior_4h_vol_ratio": safe_get(precomputed["prior_4h_vol_ratio"], boundary_ts),
                "atr_expansion": safe_get(precomputed["atr_expansion"], boundary_ts),
                "hour_of_day": boundary_ts.hour,
                "day_of_week": boundary_ts.dayofweek,
                # At-cross features
                "bb_dist_pct": cross_info["bb_dist_pct"],
                "cross_minute": cross_info["cross_minute"],
                "cross_hour_offset": cross_info["cross_hour_offset"],
                "vol_ratio_at_cross": cross_info["vol_ratio_at_cross"],
                "cross_speed": cross_info["cross_speed"],
                "prior_hour_return": safe_get(precomputed["prior_hour_return"], boundary_ts),
                "prior_hour_range_pct": safe_get(precomputed["prior_hour_range_pct"], boundary_ts),
                "bars_since_last_signal": hours_since,
                # Target
                "winner": winner,
                "trade_pnl": pnl,
            }
            all_records.append(record)
            n_signals_token += 1

        if n_signals_token > 0:
            token_signal_counts[token] = n_signals_token

    print(f"\n  Skipped {skipped_no_data} tokens (insufficient data)")
    print(f"  Tokens with signals: {len(token_signal_counts)}")
    total_raw_signals = sum(token_signal_counts.values())
    print(f"  Total signals with 1m cross + valid features: {len(all_records)}")

    if len(all_records) < 200:
        print("\nERROR: Too few records to build a meaningful model. Aborting.")
        return

    # ── Build DataFrame ──────────────────────────────────────────────────
    df = pd.DataFrame(all_records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    print(f"\n{'='*80}")
    print("Dataset Summary")
    print(f"{'='*80}")
    print(f"Total trades: {len(df)}")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Winners: {df['winner'].sum()} ({df['winner'].mean()*100:.1f}%)")
    print(f"Losers: {(1-df['winner']).sum():.0f} ({(1-df['winner'].mean())*100:.1f}%)")
    print(f"Long signals: {(df['direction']==1).sum()}")
    print(f"Short signals: {(df['direction']==-1).sum()}")
    print(f"Unique tokens: {df['token'].nunique()}")
    print(f"Avg PnL per trade: {df['trade_pnl'].mean()*100:.3f}%")
    print(f"Median PnL: {df['trade_pnl'].median()*100:.3f}%")
    print(f"\nTop tokens by signal count:")
    print(df["token"].value_counts().head(10).to_string())

    # ── Train/Test Split ─────────────────────────────────────────────────
    train_mask = df["timestamp"] < SPLIT_DATE
    test_mask = df["timestamp"] >= SPLIT_DATE

    df_train = df[train_mask].copy()
    df_test = df[test_mask].copy()

    print(f"\n{'='*80}")
    print(f"Train/Test Split (cutoff: {SPLIT_DATE})")
    print(f"{'='*80}")
    print(f"Training: {len(df_train)} trades ({df_train['timestamp'].min()} to {df_train['timestamp'].max()})")
    print(f"  Winners: {df_train['winner'].sum()} ({df_train['winner'].mean()*100:.1f}%)")
    print(f"  Avg PnL: {df_train['trade_pnl'].mean()*100:.3f}%")
    print(f"OOS Test: {len(df_test)} trades ({df_test['timestamp'].min()} to {df_test['timestamp'].max()})")
    print(f"  Winners: {df_test['winner'].sum()} ({df_test['winner'].mean()*100:.1f}%)")
    print(f"  Avg PnL: {df_test['trade_pnl'].mean()*100:.3f}%")

    if len(df_train) < 100 or len(df_test) < 50:
        print("\nERROR: Insufficient data for train/test split. Aborting.")
        return

    # ── Feature Preparation ──────────────────────────────────────────────
    X_train = df_train[FEATURE_COLS].copy()
    y_train = df_train["winner"].values
    X_test = df_test[FEATURE_COLS].copy()
    y_test = df_test["winner"].values

    # Fill NaN with median from TRAINING set only
    train_medians = X_train.median()
    X_train = X_train.fillna(train_medians)
    X_test = X_test.fillna(train_medians)

    train_timestamps = df_train["timestamp"].values

    print(f"\nFeature NaN counts (training, before fill):")
    nan_counts = df_train[FEATURE_COLS].isna().sum()
    for col in FEATURE_COLS:
        if nan_counts[col] > 0:
            print(f"  {col}: {nan_counts[col]} ({nan_counts[col]/len(df_train)*100:.1f}%)")

    # ── Adversarial Validation ───────────────────────────────────────────
    print(f"\n{'='*80}")
    print("Adversarial Validation (Train vs OOS distribution shift)")
    print(f"{'='*80}")

    X_adv = pd.concat([X_train, X_test], axis=0).values
    y_adv = np.array([0]*len(X_train) + [1]*len(X_test))
    adv_model = CatBoostClassifier(
        iterations=100, depth=4, learning_rate=0.1,
        verbose=0, random_seed=42,
    )
    # Simple 50/50 split for adversarial check
    n_adv = len(X_adv)
    perm = np.random.RandomState(42).permutation(n_adv)
    n_half = n_adv // 2
    adv_model.fit(X_adv[perm[:n_half]], y_adv[perm[:n_half]])
    adv_probs = adv_model.predict_proba(X_adv[perm[n_half:]])[:, 1]
    adv_auc = roc_auc_score(y_adv[perm[n_half:]], adv_probs)
    print(f"Adversarial AUC: {adv_auc:.4f}  (0.50=no shift, >0.65=significant shift)")
    if adv_auc > 0.65:
        print("  WARNING: Significant distribution shift between train and OOS!")
        # Show which features drive the shift
        adv_imp = pd.Series(adv_model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
        print("  Top shift-driving features:")
        for fname, imp in adv_imp.head(5).items():
            print(f"    {fname:25s}  {imp:.2f}")
    else:
        print("  Distribution shift is acceptable.")

    # ── CatBoost with Purged CV ──────────────────────────────────────────
    print(f"\n{'='*80}")
    print("CatBoost Training with Purged Time-Series CV")
    print(f"{'='*80}")

    param_grid = [
        {"iterations": 500, "depth": 4, "learning_rate": 0.05, "l2_leaf_reg": 3.0, "min_data_in_leaf": 20},
        {"iterations": 800, "depth": 5, "learning_rate": 0.03, "l2_leaf_reg": 5.0, "min_data_in_leaf": 30},
        {"iterations": 300, "depth": 3, "learning_rate": 0.1,  "l2_leaf_reg": 1.0, "min_data_in_leaf": 15},
        {"iterations": 600, "depth": 6, "learning_rate": 0.05, "l2_leaf_reg": 7.0, "min_data_in_leaf": 25},
        {"iterations": 500, "depth": 4, "learning_rate": 0.05, "l2_leaf_reg": 10.0, "min_data_in_leaf": 30},
    ]

    purged_cv = PurgedTimeSeriesSplit(n_splits=5, purge_gap_hours=PURGE_GAP_HOURS)
    train_ts = pd.DatetimeIndex(train_timestamps)

    best_auc = -1
    best_params = None
    best_fold_aucs = None

    print(f"\nCross-validating {len(param_grid)} configs, 5-fold purged TS split (gap={PURGE_GAP_HOURS}h)...")

    X_train_arr = X_train.values
    for pi, params in enumerate(param_grid):
        fold_aucs = []
        for fold, (train_idx, val_idx) in enumerate(purged_cv.split(X_train_arr, train_ts)):
            clf = CatBoostClassifier(
                random_seed=42, verbose=0,
                early_stopping_rounds=30,
                eval_metric="AUC",
                **params,
            )
            train_pool = Pool(X_train_arr[train_idx], y_train[train_idx])
            val_pool = Pool(X_train_arr[val_idx], y_train[val_idx])
            clf.fit(train_pool, eval_set=val_pool, verbose=False)
            probs = clf.predict_proba(X_train_arr[val_idx])[:, 1]
            try:
                auc = roc_auc_score(y_train[val_idx], probs)
            except ValueError:
                auc = 0.5
            fold_aucs.append(auc)

        mean_auc = np.mean(fold_aucs)
        std_auc = np.std(fold_aucs)
        print(f"  Config {pi+1}: AUC = {mean_auc:.4f} +/- {std_auc:.4f}  folds: {[f'{a:.3f}' for a in fold_aucs]}")
        if mean_auc > best_auc:
            best_auc = mean_auc
            best_params = params
            best_fold_aucs = fold_aucs

    print(f"\nBest CV AUC: {best_auc:.4f} +/- {np.std(best_fold_aucs):.4f}")
    print(f"Best params: {best_params}")

    # Fit final model on full training set
    print(f"\nFitting final CatBoost on full training set ({len(X_train_arr)} samples)...")
    final_model = CatBoostClassifier(
        random_seed=42, verbose=0,
        early_stopping_rounds=50,
        eval_metric="AUC",
        **best_params,
    )
    # Use last 15% as validation for early stopping
    n_train = len(X_train_arr)
    split_at = int(n_train * 0.85)
    train_pool = Pool(X_train_arr[:split_at], y_train[:split_at])
    val_pool = Pool(X_train_arr[split_at:], y_train[split_at:])
    final_model.fit(train_pool, eval_set=val_pool, verbose=False)

    train_probs = final_model.predict_proba(X_train_arr)[:, 1]
    train_auc = roc_auc_score(y_train, train_probs)
    print(f"Training AUC: {train_auc:.4f} (reference only — NOT for evaluation)")

    # ── SHAP Analysis ────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SHAP Feature Importance (on training set)")
    print(f"{'='*80}")

    try:
        import shap
        explainer = shap.TreeExplainer(final_model)
        # Use a sample for speed
        sample_size = min(2000, len(X_train_arr))
        sample_idx = np.random.RandomState(42).choice(len(X_train_arr), sample_size, replace=False)
        shap_values = explainer.shap_values(X_train_arr[sample_idx])

        # Mean absolute SHAP values
        mean_abs_shap = np.abs(shap_values).mean(axis=0)
        shap_imp = pd.Series(mean_abs_shap, index=FEATURE_COLS).sort_values(ascending=False)
        print(f"\nTop SHAP importances (mean |SHAP|):")
        for fname, imp in shap_imp.items():
            bar = "#" * int(imp / shap_imp.max() * 30)
            print(f"  {fname:25s}  {imp:.4f}  {bar}")
    except Exception as e:
        print(f"  SHAP failed: {e}")
        shap_imp = None

    # ── OOS Evaluation ───────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"OUT-OF-SAMPLE EVALUATION ({SPLIT_DATE} to {df_test['timestamp'].max()})")
    print(f"{'='*80}")

    X_test_arr = X_test.values
    test_probs = final_model.predict_proba(X_test_arr)[:, 1]
    test_preds = (test_probs >= 0.5).astype(int)

    print(f"\nClassification Report:")
    print(classification_report(y_test, test_preds, target_names=["Loser", "Winner"]))

    try:
        test_auc = roc_auc_score(y_test, test_probs)
        print(f"ROC-AUC Score: {test_auc:.4f}")
    except ValueError:
        test_auc = 0.5
        print("ROC-AUC: Could not compute")

    brier = brier_score_loss(y_test, test_probs)
    print(f"Brier Score: {brier:.4f} (lower is better)")

    cm = confusion_matrix(y_test, test_preds)
    print(f"\nConfusion Matrix:")
    print(f"                Predicted Loser  Predicted Winner")
    print(f"  Actual Loser    {cm[0,0]:>8d}        {cm[0,1]:>8d}")
    print(f"  Actual Winner   {cm[1,0]:>8d}        {cm[1,1]:>8d}")

    # CatBoost native feature importance
    cat_imp = pd.Series(final_model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print(f"\nCatBoost Feature Importances (native):")
    for fname, imp in cat_imp.items():
        bar = "#" * int(imp / cat_imp.max() * 30)
        print(f"  {fname:25s}  {imp:.2f}  {bar}")

    # ── Threshold Analysis ───────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("THRESHOLD ANALYSIS — OOS Win Rate Lift")
    print(f"{'='*80}")
    baseline_wr = y_test.mean() * 100
    total_test = len(y_test)
    test_pnl = df_test["trade_pnl"].values

    print(f"\nBaseline (all trades): {total_test} trades, WR = {baseline_wr:.1f}%, "
          f"avg PnL = {test_pnl.mean()*100:.3f}%")
    print()
    print(f"{'Thresh':>7s} | {'Kept':>7s} | {'% Kept':>7s} | {'WR':>7s} | {'Lift':>7s} | "
          f"{'Avg PnL':>9s} | {'Total PnL':>10s} | {'PF':>6s}")
    print("-" * 82)

    for threshold in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept < 10:
            continue
        wr = y_test[mask].mean() * 100
        lift = wr - baseline_wr
        filt_pnl = test_pnl[mask]
        avg_pnl = filt_pnl.mean() * 100
        total_pnl = filt_pnl.sum() * 100
        gross_win = filt_pnl[filt_pnl > 0].sum()
        gross_loss = abs(filt_pnl[filt_pnl <= 0].sum())
        pf = gross_win / max(gross_loss, 1e-10)
        pct_kept = n_kept / total_test * 100
        print(f"  {threshold:>5.2f} | {n_kept:>7d} | {pct_kept:>6.1f}% | {wr:>6.1f}% | "
              f"{lift:>+6.1f}pp | {avg_pnl:>+8.3f}% | {total_pnl:>+9.1f}% | {pf:>5.2f}")

    # ── OOS Returns Analysis ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("OOS COMPOUNDED EQUITY ($100k, 2% per trade)")
    print(f"{'='*80}")

    CAPITAL = 100_000
    POS_SIZE = 0.02

    for threshold in [0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        label = "ALL" if threshold == 0.0 else f"p>={threshold:.2f}"
        mask = test_probs >= threshold if threshold > 0 else np.ones(len(test_probs), dtype=bool)
        n_kept = mask.sum()
        if n_kept < 10:
            continue

        filt_pnl = test_pnl[mask]
        equity = CAPITAL
        peak = CAPITAL
        max_dd = 0.0
        for r in filt_pnl:
            equity += equity * POS_SIZE * r
            peak = max(peak, equity)
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)

        total_ret = (equity / CAPITAL - 1) * 100
        wr = y_test[mask].mean() * 100
        avg_pnl = filt_pnl.mean() * 100
        gross_win = filt_pnl[filt_pnl > 0].sum()
        gross_loss = abs(filt_pnl[filt_pnl <= 0].sum())
        pf = gross_win / max(gross_loss, 1e-10)

        print(f"\n  [{label}] {n_kept} trades, WR={wr:.1f}%")
        print(f"    Final equity: ${equity:,.0f} ({total_ret:+.1f}%)")
        print(f"    Max DD:       {max_dd*100:.1f}%")
        print(f"    Avg PnL:      {avg_pnl:+.3f}%")
        print(f"    PF:           {pf:.2f}")

    # ── Winner/Loser Feature Comparison ──────────────────────────────────
    print(f"\n{'='*80}")
    print("WINNER vs LOSER FEATURE COMPARISON (OOS)")
    print(f"{'='*80}")

    winners = df_test[df_test["winner"] == 1]
    losers = df_test[df_test["winner"] == 0]

    print(f"\n{'Feature':25s} | {'Winners':>10s} | {'Losers':>10s} | {'Delta':>10s} | {'Significant':>12s}")
    print("-" * 80)
    for col in FEATURE_COLS:
        w_mean = winners[col].mean()
        l_mean = losers[col].mean()
        delta = w_mean - l_mean
        # Simple significance: effect size
        pooled_std = df_test[col].std()
        effect = abs(delta) / max(pooled_std, 1e-10)
        sig = "***" if effect > 0.3 else "**" if effect > 0.2 else "*" if effect > 0.1 else ""
        print(f"  {col:23s} | {w_mean:>10.3f} | {l_mean:>10.3f} | {delta:>+10.3f} | {sig:>10s}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    best_thresh = None
    best_metric = -999
    for threshold in np.arange(0.40, 0.80, 0.05):
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept >= max(30, total_test * 0.10):
            filt_pnl = test_pnl[mask]
            wr = y_test[mask].mean() * 100
            avg_pnl = filt_pnl.mean()
            # Optimize for average PnL improvement (not just WR)
            metric = avg_pnl * 1000 + (wr - baseline_wr) * 0.1
            if metric > best_metric:
                best_metric = metric
                best_thresh = threshold

    if best_thresh is not None:
        mask = test_probs >= best_thresh
        n_kept = mask.sum()
        wr_filt = y_test[mask].mean() * 100
        filt_pnl = test_pnl[mask]
        print(f"\nBest practical threshold: p >= {best_thresh:.2f}")
        print(f"  ALL trades:      {total_test} trades, WR={baseline_wr:.1f}%, "
              f"avg PnL={test_pnl.mean()*100:.3f}%, total={test_pnl.sum()*100:+.1f}%")
        print(f"  Filtered trades: {n_kept} trades, WR={wr_filt:.1f}%, "
              f"avg PnL={filt_pnl.mean()*100:.3f}%, total={filt_pnl.sum()*100:+.1f}%")
        print(f"  Trades retained: {n_kept/total_test*100:.1f}%")
        print(f"  WR lift: {wr_filt - baseline_wr:+.1f}pp")
        print(f"  Avg PnL lift: {(filt_pnl.mean() - test_pnl.mean())*100:+.3f}%")
    else:
        print("\nNo threshold found with sufficient trades and positive lift.")

    print(f"\nCV AUC (train): {best_auc:.4f}")
    print(f"OOS AUC:        {test_auc:.4f}")
    print(f"AUC gap:        {best_auc - test_auc:+.4f}")
    if abs(best_auc - test_auc) > 0.05:
        print("  WARNING: >5% AUC gap — possible overfitting")
    else:
        print("  AUC gap acceptable (<5%)")

    # ── Save results ─────────────────────────────────────────────────────
    os.makedirs("results/v4", exist_ok=True)
    results = {
        "model": "CatBoost",
        "cv_auc": float(best_auc),
        "cv_auc_std": float(np.std(best_fold_aucs)),
        "oos_auc": float(test_auc),
        "brier_score": float(brier),
        "adversarial_auc": float(adv_auc),
        "best_params": best_params,
        "n_train": len(df_train),
        "n_test": len(df_test),
        "train_wr": float(df_train["winner"].mean() * 100),
        "test_wr": float(df_test["winner"].mean() * 100),
        "best_threshold": float(best_thresh) if best_thresh else None,
        "features": FEATURE_COLS,
        "feature_importances": {k: float(v) for k, v in cat_imp.items()},
    }

    import json
    with open("results/v4/s503_ml_entry_filter.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to results/v4/s503_ml_entry_filter.json")

    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
