"""
s502_ml_entry_filter.py – ML model to filter BB breakout entries at 1m level

Strategy s501 detects Bollinger Band breakouts on 4H timeframe, confirmed on 1H bars.
This script builds a classifier predicting whether a breakout trade will win or lose,
using features extracted from 1-minute price action around the entry.

CRITICAL: Train/test split by date (2025-04-03). All feature engineering, normalization,
and model training use ONLY the training set. OOS test set is touched exactly once.
"""

import os
import sys
import warnings
import time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    confusion_matrix,
)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = "/workspace/crypto_backtest/data/perp"
DIR_1M = os.path.join(DATA_DIR, "1m_cache")
DIR_1H = os.path.join(DATA_DIR, "1h_cache")

SPLIT_DATE = pd.Timestamp("2025-04-03", tz=None)

BB_PERIOD = 20
BB_STD = 2.0
VOL_RATIO_THRESH = 1.3
MOM_BARS_4H = 42  # ~7 days of 4H bars

MAX_HOLD_HOURS = 4
ATR_PERIOD = 14
TP_ATR_MULT = 1.5
TRAIL_ATR_MULT = 2.0

FEATURE_COLS = [
    # Pre-entry only — known at the moment of BB cross (no look-ahead)
    "bb_dist_pct",          # distance from BB at cross moment
    "cross_minute",         # which minute the cross happened
    "vol_ratio_at_cross",   # 1m volume at cross vs prior hour avg
    "prior_hour_return",    # return of previous 1h bar
    # New pre-entry context features (all known before cross)
    "bb_bandwidth_pct",     # BB width as % of mid (volatility measure)
    "bbw_percentile",       # where current BBW sits vs last 120 4H bars
    "squeeze_bars",         # how many consecutive 4H bars BB was contracting
    "atr_percentile",       # ATR percentile over last 100 1H bars
    "rsi_1h",               # RSI(14) on 1H at signal bar
    "rsi_4h",               # RSI(14) on enclosing 4H bar
    "ema_trend_1h",         # 1 if EMA50 > EMA200 on 1H, else 0
    "mom_strength",         # absolute momentum magnitude (|mom_7d|)
    "hour_of_day",          # UTC hour (0-23)
    "day_of_week",          # day of week (0=Mon, 6=Sun)
    "prior_hour_vol_ratio", # prior hour volume / 20-period avg volume
    "prior_hour_range_pct", # prior hour (high-low)/close as %
    "bars_since_last_signal", # bars since last BB signal on this token
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
    )


def align_4h_to_1h(df_4h: pd.DataFrame, df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    Forward-fill 4H BB values onto 1H bars.
    Each 1H bar gets the BB from its enclosing (most recent past) 4H bar.
    """
    cols_to_align = ["prior_bb_upper", "prior_bb_lower", "mom_4h"]
    aligned = df_4h[cols_to_align].reindex(df_1h.index, method="ffill")
    return aligned


def compute_atr_1h(df_1h: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Compute ATR on 1H bars."""
    h = df_1h["high"]
    l = df_1h["low"]
    c = df_1h["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def detect_signals(df_1h: pd.DataFrame, aligned: pd.DataFrame) -> pd.DataFrame:
    """
    Detect BB cross signals on 1H bars.
    Long: close > prior_bb_upper AND vol_ratio > 1.3 AND mom > 0
    Short: close < prior_bb_lower AND vol_ratio > 1.3 AND mom < 0
    Deduplicate: first signal per 4H window only.
    """
    close = df_1h["close"]
    vol = df_1h["volume"]
    vol_sma = vol.rolling(20, min_periods=10).mean()
    vol_ratio = vol / vol_sma

    bb_upper = aligned["prior_bb_upper"]
    bb_lower = aligned["prior_bb_lower"]
    mom = aligned["mom_4h"]

    long_signal = (close > bb_upper) & (vol_ratio > VOL_RATIO_THRESH) & (mom > 0)
    short_signal = (close < bb_lower) & (vol_ratio > VOL_RATIO_THRESH) & (mom < 0)

    signals = pd.DataFrame(index=df_1h.index)
    signals["direction"] = 0
    signals.loc[long_signal, "direction"] = 1
    signals.loc[short_signal, "direction"] = -1
    signals["bb_level"] = np.nan
    signals.loc[long_signal, "bb_level"] = bb_upper[long_signal]
    signals.loc[short_signal, "bb_level"] = bb_lower[short_signal]
    signals["vol_ratio_1h"] = vol_ratio

    # Keep only active signals
    active = signals[signals["direction"] != 0].copy()
    if active.empty:
        return active

    # Deduplicate: first signal per 4H window
    active["window_4h"] = active.index.floor("4h")
    active["_orig_ts"] = active.index
    # groupby sets window_4h as index; first() picks the earliest 1h bar per window
    dedup = active.groupby("window_4h").first()
    # Restore original 1h timestamps as index
    dedup = dedup.set_index("_orig_ts")
    dedup.index.name = None
    dedup = dedup.drop(columns=["vol_ratio_1h"], errors="ignore")
    return dedup[["direction", "bb_level"]]


def extract_features_for_signal(
    sig_ts: pd.Timestamp,
    direction: int,
    bb_level: float,
    df_1m: pd.DataFrame,
    df_1h: pd.DataFrame,
    precomputed: dict,
) -> dict | None:
    """
    Extract pre-entry features for a single signal bar.
    All features are known at or before the moment of BB cross — no look-ahead.

    precomputed: dict with pre-computed series for this token:
        bb_bandwidth_pct, bbw_percentile, squeeze_bars, atr_percentile,
        rsi_1h, rsi_4h_aligned, ema_trend_1h, mom_abs_aligned,
        vol_ratio_1h, hour_range_1h
    """
    # 1m bars within the signal hour
    hour_start = sig_ts
    hour_end = sig_ts + pd.Timedelta(hours=1)
    m1_hour = df_1m.loc[hour_start:hour_end - pd.Timedelta(minutes=1)]

    if len(m1_hour) < 10:
        return None

    closes_1m = m1_hour["close"].values
    volumes_1m = m1_hour["volume"].values

    # Find first 1m close crossing the BB level
    if direction == 1:
        cross_mask = closes_1m > bb_level
    else:
        cross_mask = closes_1m < bb_level

    cross_indices = np.where(cross_mask)[0]
    if len(cross_indices) == 0:
        return None

    cross_idx = cross_indices[0]
    cross_close = closes_1m[cross_idx]
    cross_minute = cross_idx

    # ── Pre-entry features (known at cross moment) ──────────────────────
    bb_dist_pct = abs(cross_close - bb_level) / max(bb_level, 1e-10) * 100

    # Volume at cross vs prior hour average (no look-ahead)
    prior_ts = sig_ts - pd.Timedelta(hours=1)
    if prior_ts in df_1m.index:
        prior_hour = df_1m.loc[prior_ts:sig_ts - pd.Timedelta(minutes=1)]
        prior_vol_mean = prior_hour["volume"].mean() if len(prior_hour) > 0 else 1e-9
    else:
        prior_vol_mean = volumes_1m[:max(cross_idx, 1)].mean() if cross_idx > 0 else 1e-9
    vol_ratio_at_cross = volumes_1m[cross_idx] / max(prior_vol_mean, 1e-9)

    # prior_hour_return
    if prior_ts in df_1h.index and sig_ts in df_1h.index:
        prior_close = df_1h.loc[prior_ts, "close"]
        cur_open = df_1h.loc[sig_ts, "open"]
        prior_hour_return = (cur_open - prior_close) / max(prior_close, 1e-10) * 100
    else:
        prior_hour_return = np.nan

    # ── New pre-entry context features (from precomputed) ───────────────
    def safe_get(series, ts):
        if series is None:
            return np.nan
        if ts in series.index:
            return float(series.loc[ts])
        # Find nearest prior value
        mask = series.index <= ts
        if mask.any():
            return float(series.loc[series.index[mask][-1]])
        return np.nan

    bb_bandwidth_pct = safe_get(precomputed.get("bb_bandwidth_pct"), sig_ts)
    bbw_percentile = safe_get(precomputed.get("bbw_percentile"), sig_ts)
    squeeze_bars = safe_get(precomputed.get("squeeze_bars"), sig_ts)
    atr_percentile = safe_get(precomputed.get("atr_percentile"), sig_ts)
    rsi_1h = safe_get(precomputed.get("rsi_1h"), sig_ts)
    rsi_4h = safe_get(precomputed.get("rsi_4h_aligned"), sig_ts)
    ema_trend_1h = safe_get(precomputed.get("ema_trend_1h"), sig_ts)
    mom_strength = safe_get(precomputed.get("mom_abs_aligned"), sig_ts)

    # Time features
    hour_of_day = sig_ts.hour
    day_of_week = sig_ts.dayofweek

    # Prior hour volume ratio and range (fully known)
    prior_hour_vol_ratio = safe_get(precomputed.get("vol_ratio_1h_shifted"), sig_ts)
    if prior_ts in df_1h.index:
        ph = df_1h.loc[prior_ts]
        prior_hour_range_pct = (ph["high"] - ph["low"]) / max(ph["close"], 1e-10) * 100
    else:
        prior_hour_range_pct = np.nan

    # Bars since last signal (from precomputed)
    bars_since_last_signal = safe_get(precomputed.get("bars_since_signal"), sig_ts)

    # ── Trade Outcome ────────────────────────────────────────────────────
    # Entry price: 1m close at cross_idx + 1 (1-minute delay)
    entry_idx_in_hour = cross_idx + 1
    if entry_idx_in_hour < len(closes_1m):
        entry_price = closes_1m[entry_idx_in_hour]
    else:
        # Cross on last minute of the hour — load first minute of next hour
        next_hour_start = hour_end
        m1_next_bar = df_1m.loc[next_hour_start:next_hour_start]
        if len(m1_next_bar) > 0:
            entry_price = m1_next_bar["close"].values[0]
        else:
            return None

    entry_ts_1m = m1_hour.index[min(entry_idx_in_hour, len(m1_hour) - 1)]

    # ATR at entry bar
    if sig_ts in df_1h.index:
        idx_pos = df_1h.index.get_loc(sig_ts)
        if idx_pos >= ATR_PERIOD:
            h_slice = df_1h.iloc[idx_pos - ATR_PERIOD : idx_pos + 1]
            tr_vals = pd.concat(
                [
                    h_slice["high"] - h_slice["low"],
                    (h_slice["high"] - h_slice["close"].shift(1)).abs(),
                    (h_slice["low"] - h_slice["close"].shift(1)).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr = tr_vals.iloc[1:].mean()  # skip first NaN from shift
        else:
            atr = None
    else:
        atr = None

    # Track price over next 4 hours of 1m data
    hold_end = sig_ts + pd.Timedelta(hours=1 + MAX_HOLD_HOURS)
    m1_hold = df_1m.loc[entry_ts_1m:hold_end]

    if len(m1_hold) < 10:
        return None

    hold_closes = m1_hold["close"].values
    hold_highs = m1_hold["high"].values
    hold_lows = m1_hold["low"].values

    # Determine winner/loser using ATR-based exits if possible, else simple rule
    winner = None
    if atr is not None and atr > 0:
        tp_level = entry_price + direction * TP_ATR_MULT * atr
        initial_sl = entry_price - direction * TRAIL_ATR_MULT * atr
        trail_dist = TRAIL_ATR_MULT * atr

        # Walk through bars
        best_price = entry_price
        partial_taken = False
        pnl = 0.0

        for i in range(1, len(hold_closes)):
            bar_high = hold_highs[i]
            bar_low = hold_lows[i]
            bar_close = hold_closes[i]

            if direction == 1:
                best_price = max(best_price, bar_high)
                trail_sl = best_price - trail_dist
                stop = max(initial_sl, trail_sl) if partial_taken else initial_sl

                # Check TP hit
                if not partial_taken and bar_high >= tp_level:
                    pnl += 0.5 * (tp_level - entry_price) / entry_price
                    partial_taken = True

                # Check stop hit
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
            # Max hold expired, exit at last close
            remaining = 0.5 if partial_taken else 1.0
            if direction == 1:
                pnl += remaining * (hold_closes[-1] - entry_price) / entry_price
            else:
                pnl += remaining * (entry_price - hold_closes[-1]) / entry_price

        winner = 1 if pnl > 0 else 0
    else:
        # Simple fallback: did price move >0.5% in signal direction within 4h?
        if direction == 1:
            max_move = (hold_highs.max() - entry_price) / entry_price
            pnl = max_move  # approximate
        else:
            max_move = (entry_price - hold_lows.min()) / entry_price
            pnl = max_move
        winner = 1 if max_move > 0.005 else 0

    return {
        "timestamp": sig_ts,
        "direction": direction,
        "bb_level": bb_level,
        "entry_price": entry_price,
        # Pre-entry features
        "bb_dist_pct": bb_dist_pct,
        "cross_minute": cross_minute,
        "vol_ratio_at_cross": vol_ratio_at_cross,
        "prior_hour_return": prior_hour_return,
        # New context features
        "bb_bandwidth_pct": bb_bandwidth_pct,
        "bbw_percentile": bbw_percentile,
        "squeeze_bars": squeeze_bars,
        "atr_percentile": atr_percentile,
        "rsi_1h": rsi_1h,
        "rsi_4h": rsi_4h,
        "ema_trend_1h": ema_trend_1h,
        "mom_strength": mom_strength,
        "hour_of_day": hour_of_day,
        "day_of_week": day_of_week,
        "prior_hour_vol_ratio": prior_hour_vol_ratio,
        "prior_hour_range_pct": prior_hour_range_pct,
        "bars_since_last_signal": bars_since_last_signal,
        # Target + PnL
        "winner": winner,
        "trade_pnl": pnl,  # signed return per trade (fraction, not %)
    }


# ── Main Pipeline ──────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    print("=" * 80)
    print("s502_ml_entry_filter — ML BB Breakout Entry Filter")
    print("=" * 80)

    # ── Step 1: Discover tokens ──────────────────────────────────────────
    tokens_1m = {f.replace("_1m.parquet", "") for f in os.listdir(DIR_1M) if f.endswith(".parquet")}
    tokens_1h = {f.replace("_1h.parquet", "") for f in os.listdir(DIR_1H) if f.endswith(".parquet")}
    common_tokens = sorted(tokens_1m & tokens_1h)
    print(f"\nTokens with both 1m and 1h data: {len(common_tokens)}")
    print(f"Sample: {common_tokens[:10]}")

    # ── Step 2-3: Signal Detection + Feature Extraction ──────────────────
    all_records = []
    token_signal_counts = {}
    skipped_no_data = 0

    for i, token in enumerate(common_tokens):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"\n  Processing token {i+1}/{len(common_tokens)}: {token} ...")

        try:
            df_1h = load_parquet(os.path.join(DIR_1H, f"{token}_1h.parquet"))
            df_1m = load_parquet(os.path.join(DIR_1M, f"{token}_1m.parquet"))
        except Exception as e:
            skipped_no_data += 1
            continue

        # Need at least BB_PERIOD * 4 + MOM_BARS_4H hours of 1h data
        if len(df_1h) < (BB_PERIOD * 4 + MOM_BARS_4H):
            skipped_no_data += 1
            continue

        # Resample 1H to 4H
        df_4h = resample_1h_to_4h(df_1h)
        if len(df_4h) < BB_PERIOD + MOM_BARS_4H + 5:
            skipped_no_data += 1
            continue

        # Compute BB and momentum on 4H
        df_4h = compute_bb(df_4h)
        df_4h["mom_4h"] = compute_mom_4h(df_4h)

        # Align to 1H
        aligned = align_4h_to_1h(df_4h, df_1h)

        # Detect signals
        signals = detect_signals(df_1h, aligned)
        if signals.empty:
            continue

        token_signal_counts[token] = len(signals)

        # ── Precompute context features for this token ────────────────
        precomputed = {}

        # BB bandwidth and squeeze on 4H (shifted by 1 = known at bar open)
        bbw = (df_4h["bb_upper"] - df_4h["bb_lower"]) / df_4h["bb_mid"].clip(lower=1e-10) * 100
        bbw_shifted = bbw.shift(1)  # prior bar's BBW = known
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
                squeeze_count.iloc[j] = squeeze_count.iloc[j-1] + 1
            else:
                squeeze_count.iloc[j] = 0
        precomputed["squeeze_bars"] = squeeze_count.reindex(df_1h.index, method="ffill")

        # ATR percentile on 1H (using prior bar = known)
        atr_1h = compute_atr_1h(df_1h)
        atr_shifted = atr_1h.shift(1)
        atr_pctile = atr_shifted.rolling(100, min_periods=20).apply(
            lambda x: (x.iloc[-1] <= x).mean() * 100 if len(x) > 0 else np.nan, raw=False
        )
        precomputed["atr_percentile"] = atr_pctile

        # RSI(14) on 1H (using prior bar close = known at bar open)
        close_1h = df_1h["close"]
        delta = close_1h.diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
        rs = gain / loss.clip(lower=1e-10)
        rsi_1h_series = 100 - (100 / (1 + rs))
        precomputed["rsi_1h"] = rsi_1h_series.shift(1)  # prior bar = known

        # RSI(14) on 4H aligned to 1H
        close_4h = df_4h["close"]
        delta_4h = close_4h.diff()
        gain_4h = delta_4h.clip(lower=0).rolling(14, min_periods=14).mean()
        loss_4h = (-delta_4h.clip(upper=0)).rolling(14, min_periods=14).mean()
        rs_4h = gain_4h / loss_4h.clip(lower=1e-10)
        rsi_4h_series = (100 - (100 / (1 + rs_4h))).shift(1)
        precomputed["rsi_4h_aligned"] = rsi_4h_series.reindex(df_1h.index, method="ffill")

        # EMA trend on 1H (EMA50 > EMA200 = uptrend)
        ema50 = close_1h.ewm(span=50, min_periods=50).mean()
        ema200 = close_1h.ewm(span=200, min_periods=200).mean()
        ema_trend = (ema50 > ema200).astype(float).shift(1)  # prior bar = known
        precomputed["ema_trend_1h"] = ema_trend

        # Momentum strength (absolute value of 7d momentum, aligned)
        mom_abs = df_4h["mom_4h"].abs().shift(1)
        precomputed["mom_abs_aligned"] = mom_abs.reindex(df_1h.index, method="ffill")

        # Prior hour volume ratio (shifted so it's the completed prior bar)
        vol_1h = df_1h["volume"]
        vol_sma_1h = vol_1h.rolling(20, min_periods=10).mean()
        vol_ratio_1h = vol_1h / vol_sma_1h.clip(lower=1e-9)
        precomputed["vol_ratio_1h_shifted"] = vol_ratio_1h.shift(1)

        # Bars since last signal
        signal_mask = pd.Series(False, index=df_1h.index)
        for sig_ts_tmp in signals.index:
            if sig_ts_tmp in signal_mask.index:
                signal_mask.loc[sig_ts_tmp] = True
        bars_since = pd.Series(np.nan, index=df_1h.index)
        last_sig_idx = -9999
        for j, ts in enumerate(df_1h.index):
            if signal_mask.iloc[j]:
                bars_since.iloc[j] = j - last_sig_idx if last_sig_idx >= 0 else 9999
                last_sig_idx = j
        precomputed["bars_since_signal"] = bars_since

        # ── Extract features for each signal ──────────────────────────
        for sig_ts, row in signals.iterrows():
            feat = extract_features_for_signal(
                sig_ts=sig_ts,
                direction=int(row["direction"]),
                bb_level=row["bb_level"],
                df_1m=df_1m,
                df_1h=df_1h,
                precomputed=precomputed,
            )
            if feat is not None:
                feat["token"] = token
                all_records.append(feat)

    print(f"\n  Skipped {skipped_no_data} tokens (insufficient data)")
    print(f"  Tokens with signals: {len(token_signal_counts)}")
    total_raw_signals = sum(token_signal_counts.values())
    print(f"  Total raw signals: {total_raw_signals}")
    print(f"  Signals with valid features: {len(all_records)}")

    if len(all_records) < 100:
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
    print(f"\nTokens in dataset: {df['token'].nunique()}")
    print(f"Top tokens by signal count:")
    print(df["token"].value_counts().head(10).to_string())

    # ── Step 5: Train/Test Split ─────────────────────────────────────────
    train_mask = df["timestamp"] < SPLIT_DATE
    test_mask = df["timestamp"] >= SPLIT_DATE

    df_train = df[train_mask].copy()
    df_test = df[test_mask].copy()

    print(f"\n{'='*80}")
    print(f"Train/Test Split (cutoff: {SPLIT_DATE})")
    print(f"{'='*80}")
    print(f"Training set: {len(df_train)} trades ({df_train['timestamp'].min()} to {df_train['timestamp'].max()})")
    print(f"  Winners: {df_train['winner'].sum()} ({df_train['winner'].mean()*100:.1f}%)")
    print(f"Test set:     {len(df_test)} trades ({df_test['timestamp'].min()} to {df_test['timestamp'].max()})")
    print(f"  Winners: {df_test['winner'].sum()} ({df_test['winner'].mean()*100:.1f}%)")

    if len(df_train) < 50 or len(df_test) < 20:
        print("\nERROR: Insufficient data for train/test split. Aborting.")
        return

    # ── Step 6: Feature Preparation + Model Training (ONLY training data) ──
    X_train = df_train[FEATURE_COLS].copy()
    y_train = df_train["winner"].values
    X_test = df_test[FEATURE_COLS].copy()
    y_test = df_test["winner"].values

    # Fill NaN with median from TRAINING set only
    train_medians = X_train.median()
    X_train = X_train.fillna(train_medians)
    X_test = X_test.fillna(train_medians)

    # Normalize: fit on training, transform both
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    print(f"\nFeature statistics (training set):")
    print(X_train.describe().round(3).to_string())

    # ── Cross-validation within training set ─────────────────────────────
    print(f"\n{'='*80}")
    print("Model Training (HistGradientBoostingClassifier)")
    print(f"{'='*80}")

    # Hyperparameter search via TimeSeriesSplit
    # Using HistGradientBoostingClassifier — histogram-based, ~100x faster than GBM
    param_grid = [
        {"max_iter": 300, "max_depth": 4, "learning_rate": 0.05, "min_samples_leaf": 20, "max_leaf_nodes": 31, "l2_regularization": 0.1},
        {"max_iter": 500, "max_depth": 5, "learning_rate": 0.03, "min_samples_leaf": 30, "max_leaf_nodes": 31, "l2_regularization": 1.0},
        {"max_iter": 200, "max_depth": 3, "learning_rate": 0.1,  "min_samples_leaf": 15, "max_leaf_nodes": 15, "l2_regularization": 0.01},
        {"max_iter": 400, "max_depth": 6, "learning_rate": 0.05, "min_samples_leaf": 25, "max_leaf_nodes": 63, "l2_regularization": 0.5},
        {"max_iter": 300, "max_depth": 4, "learning_rate": 0.05, "min_samples_leaf": 20, "max_leaf_nodes": 31, "l2_regularization": 5.0},
    ]

    tscv = TimeSeriesSplit(n_splits=5)
    best_auc = -1
    best_params = None

    print(f"\nCross-validating {len(param_grid)} parameter sets with 5-fold TimeSeries split...")

    for pi, params in enumerate(param_grid):
        fold_aucs = []
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_train_scaled)):
            clf = HistGradientBoostingClassifier(random_state=42, early_stopping=True, validation_fraction=0.1, n_iter_no_change=20, **params)
            clf.fit(X_train_scaled[train_idx], y_train[train_idx])
            probs = clf.predict_proba(X_train_scaled[val_idx])[:, 1]
            try:
                auc = roc_auc_score(y_train[val_idx], probs)
            except ValueError:
                auc = 0.5
            fold_aucs.append(auc)
        mean_auc = np.mean(fold_aucs)
        print(f"  Params {pi+1}: AUC = {mean_auc:.4f} (folds: {[f'{a:.3f}' for a in fold_aucs]})")
        if mean_auc > best_auc:
            best_auc = mean_auc
            best_params = params

    print(f"\nBest CV AUC: {best_auc:.4f}")
    print(f"Best params: {best_params}")

    # Fit final model on full training set
    print(f"\nFitting final model on full training set ({len(X_train_scaled)} samples)...")
    final_model = HistGradientBoostingClassifier(random_state=42, early_stopping=True, validation_fraction=0.1, n_iter_no_change=20, **best_params)
    final_model.fit(X_train_scaled, y_train)

    # Training set performance (for reference, NOT for evaluation)
    train_probs = final_model.predict_proba(X_train_scaled)[:, 1]
    train_auc = roc_auc_score(y_train, train_probs)
    print(f"Training AUC: {train_auc:.4f} (for reference only)")

    # ── Step 7: OOS Evaluation (EXACTLY ONCE) ────────────────────────────
    print(f"\n{'='*80}")
    print("OUT-OF-SAMPLE EVALUATION (2025-04-03 to 2026-04-03)")
    print(f"{'='*80}")

    test_probs = final_model.predict_proba(X_test_scaled)[:, 1]
    test_preds = (test_probs >= 0.5).astype(int)

    # Classification report
    print(f"\nClassification Report:")
    print(classification_report(y_test, test_preds, target_names=["Loser", "Winner"]))

    # ROC-AUC
    try:
        test_auc = roc_auc_score(y_test, test_probs)
        print(f"ROC-AUC Score: {test_auc:.4f}")
    except ValueError:
        test_auc = None
        print("ROC-AUC: Could not compute (single class in test set?)")

    # Confusion Matrix
    cm = confusion_matrix(y_test, test_preds)
    print(f"\nConfusion Matrix:")
    print(f"                Predicted Loser  Predicted Winner")
    print(f"  Actual Loser    {cm[0,0]:>8d}        {cm[0,1]:>8d}")
    print(f"  Actual Winner   {cm[1,0]:>8d}        {cm[1,1]:>8d}")

    # Feature Importances (use permutation importance for HistGBM)
    from sklearn.inspection import permutation_importance
    perm_result = permutation_importance(final_model, X_test_scaled, y_test, n_repeats=10, random_state=42, scoring='roc_auc')
    feat_imp = pd.Series(perm_result.importances_mean, index=FEATURE_COLS).sort_values(ascending=False)
    print(f"\nTop Feature Importances (permutation, OOS):")
    for fname, imp in feat_imp.items():
        print(f"  {fname:25s}  {imp:.4f}")

    # ── Practical Impact Table ───────────────────────────────────────────
    print(f"\n{'='*80}")
    print("PRACTICAL IMPACT — Threshold Analysis")
    print(f"{'='*80}")
    baseline_wr = y_test.mean() * 100
    total_test = len(y_test)

    print(f"\nBaseline (all trades): {total_test} trades, win rate = {baseline_wr:.1f}%")
    print()
    print(f"{'Threshold':>10s} | {'Trades Kept':>12s} | {'Win Rate':>9s} | {'Baseline':>9s} | {'Lift':>8s} | {'% Trades Kept':>14s}")
    print("-" * 75)

    for threshold in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept == 0:
            continue
        wr = y_test[mask].mean() * 100
        lift = wr - baseline_wr
        pct_kept = n_kept / total_test * 100
        print(
            f"  {threshold:>7.2f}   | {n_kept:>8d} ({pct_kept:>4.1f}%) | {wr:>7.1f}%  | {baseline_wr:>7.1f}%  | {lift:>+6.1f}pp | {pct_kept:>12.1f}%"
        )

    # ── OOS Returns Analysis ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("OOS RETURNS ANALYSIS (simulated, equal-weight per trade)")
    print(f"{'='*80}")

    test_pnl = df_test["trade_pnl"].values
    baseline_total_pnl = test_pnl.sum()
    baseline_avg_pnl = test_pnl.mean()
    baseline_sharpe_approx = test_pnl.mean() / max(test_pnl.std(), 1e-10) * np.sqrt(len(test_pnl))

    print(f"\nBaseline (all {total_test} trades):")
    print(f"  Total PnL (sum of returns): {baseline_total_pnl*100:.1f}%")
    print(f"  Avg PnL per trade:          {baseline_avg_pnl*100:.3f}%")
    print(f"  Median PnL per trade:       {np.median(test_pnl)*100:.3f}%")
    print(f"  Std PnL per trade:          {test_pnl.std()*100:.3f}%")
    print(f"  Approx Sharpe (per-trade):  {baseline_sharpe_approx:.2f}")
    print(f"  Worst trade:                {test_pnl.min()*100:.2f}%")
    print(f"  Best trade:                 {test_pnl.max()*100:.2f}%")

    print(f"\n{'Threshold':>10s} | {'Trades':>7s} | {'Total PnL':>10s} | {'Avg PnL':>9s} | {'Sharpe':>7s} | {'Win Rate':>9s} | {'vs Baseline':>12s}")
    print("-" * 85)

    for threshold in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept < 10:
            continue
        filt_pnl = test_pnl[mask]
        total_ret = filt_pnl.sum()
        avg_ret = filt_pnl.mean()
        wr = y_test[mask].mean() * 100
        sharpe = filt_pnl.mean() / max(filt_pnl.std(), 1e-10) * np.sqrt(len(filt_pnl))
        pnl_vs_base = total_ret - baseline_total_pnl
        print(
            f"  {threshold:>7.2f}   | {n_kept:>7d} | {total_ret*100:>+9.1f}% | {avg_ret*100:>+7.3f}% | {sharpe:>7.2f} | {wr:>7.1f}%  | {pnl_vs_base*100:>+10.1f}%"
        )

    # ── Compounded equity simulation at $100k ─────────────────────────
    print(f"\n{'='*80}")
    print("COMPOUNDED EQUITY SIMULATION ($100k start, equal-size trades)")
    print(f"{'='*80}")

    CAPITAL = 100_000
    POS_SIZE_FRAC = 0.02  # 2% of equity per trade (conservative)

    for threshold in [0.0, 0.50, 0.60, 0.70]:
        label = "ALL" if threshold == 0.0 else f"p>={threshold:.2f}"
        mask = test_probs >= threshold if threshold > 0 else np.ones(len(test_probs), dtype=bool)
        n_kept = mask.sum()
        if n_kept < 10:
            continue
        filt_pnl = test_pnl[mask]
        filt_wr = y_test[mask].mean() * 100

        equity = CAPITAL
        peak = CAPITAL
        max_dd = 0.0
        equities = [equity]
        for r in filt_pnl:
            trade_pnl_dollar = equity * POS_SIZE_FRAC * r
            equity += trade_pnl_dollar
            equities.append(equity)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        total_ret_pct = (equity / CAPITAL - 1) * 100
        # Annualize: OOS period is 12 months
        ann_ret = total_ret_pct  # already 12 months ≈ 1 year
        ann_sharpe = (np.mean(filt_pnl) / max(np.std(filt_pnl), 1e-10)) * np.sqrt(365 * 6)  # ~6 trades/day avg

        print(f"\n  [{label}] {n_kept} trades, WR={filt_wr:.1f}%")
        print(f"    Final equity:    ${equity:,.0f} ({total_ret_pct:+.1f}%)")
        print(f"    Max drawdown:    {max_dd*100:.1f}%")
        print(f"    Avg PnL/trade:   {np.mean(filt_pnl)*100:.3f}%")
        print(f"    Profit factor:   {abs(filt_pnl[filt_pnl>0].sum()) / max(abs(filt_pnl[filt_pnl<=0].sum()), 1e-10):.2f}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    best_thresh = None
    best_lift = -999
    for threshold in np.arange(0.40, 0.75, 0.05):
        mask = test_probs >= threshold
        n_kept = mask.sum()
        if n_kept >= max(10, total_test * 0.1):
            wr = y_test[mask].mean() * 100
            lift = wr - baseline_wr
            if lift > best_lift:
                best_lift = lift
                best_thresh = threshold

    if best_thresh is not None:
        mask = test_probs >= best_thresh
        n_kept = mask.sum()
        wr_filt = y_test[mask].mean() * 100
        filt_pnl = test_pnl[mask]
        print(f"\nBest practical threshold: p >= {best_thresh:.2f}")
        print(f"  ALL trades:      {total_test} trades, WR = {baseline_wr:.1f}%, total PnL = {baseline_total_pnl*100:+.1f}%")
        print(f"  Filtered trades: {n_kept} trades, WR = {wr_filt:.1f}%, total PnL = {filt_pnl.sum()*100:+.1f}%, lift = {wr_filt - baseline_wr:+.1f}pp")
        print(f"  Trades retained: {n_kept/total_test*100:.1f}%")
        print(f"  Avg PnL/trade:   baseline {baseline_avg_pnl*100:.3f}% → filtered {filt_pnl.mean()*100:.3f}%")
    else:
        print("\nNo threshold found with sufficient trades and positive lift.")

    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
