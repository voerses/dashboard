#!/usr/bin/env python3
"""s523c Multi-Timeframe Feature Analysis.

Discovers WHICH price-structure features at WHICH timeframes predict s523c
trade success. Computes ~50 features across monthly/weekly/daily timeframes
at each trade's entry time, then analyzes predictive power.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.tree import DecisionTreeClassifier, export_text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 1. Load data
# ---------------------------------------------------------------------------
TRADE_LOG_PATH = PROJECT_ROOT / "results/v4/s523c_growth_51mo_50k_trades.json"
BTC_PATH = PROJECT_ROOT / "data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
OUTPUT_JSON = PROJECT_ROOT / "research/s523_multitimeframe_results.json"

print("Loading BTC 1h data...")
btc = pd.read_csv(BTC_PATH)
btc["datetime"] = pd.to_datetime(btc["datetime"], utc=True)
btc = btc.sort_values("datetime").reset_index(drop=True)
btc_close = btc["close"].values.astype(np.float64)
btc_high = btc["high"].values.astype(np.float64)
btc_ts = btc["datetime"].values  # numpy datetime64

print(f"BTC data: {len(btc)} bars, {btc['datetime'].iloc[0]} to {btc['datetime'].iloc[-1]}")

print("Loading trade log...")
with open(TRADE_LOG_PATH) as f:
    trades_raw = json.load(f)

trades = pd.DataFrame(trades_raw)
for col in ["pnl", "margin_usd", "entry_price", "exit_price"]:
    trades[col] = pd.to_numeric(trades[col], errors="coerce")
trades["entry_bar"] = trades["entry_bar"].astype(int)
trades["exit_bar"] = trades["exit_bar"].astype(int)

print(f"Trades: {len(trades)}, Longs: {(trades['direction']==1).sum()}, Shorts: {(trades['direction']==-1).sum()}")
print(f"Entry bar range: {trades['entry_bar'].min()} to {trades['entry_bar'].max()}")
print(f"PnL: mean={trades['pnl'].mean():.2f}, median={trades['pnl'].median():.2f}")

# ---------------------------------------------------------------------------
# 2. Precompute moving averages on BTC 1h close (vectorized)
# ---------------------------------------------------------------------------
print("\nPrecomputing moving averages...")

HOURS_PER_WEEK = 168
HOURS_PER_DAY = 24
HOURS_PER_MONTH = 720  # ~30 days


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average using cumsum trick."""
    out = np.full_like(arr, np.nan)
    cs = np.cumsum(arr)
    cs = np.insert(cs, 0, 0.0)
    out[period - 1:] = (cs[period:] - cs[:-period]) / period
    return out


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA via pandas (uses compiled C code)."""
    return pd.Series(arr).ewm(span=period, adjust=False).mean().values


# Weekly SMAs (period in hours)
print("  Weekly SMAs...")
w_sma20 = sma(btc_close, 20 * HOURS_PER_WEEK)
w_sma50 = sma(btc_close, 50 * HOURS_PER_WEEK)
w_sma100 = sma(btc_close, 100 * HOURS_PER_WEEK)
w_sma200 = sma(btc_close, 200 * HOURS_PER_WEEK)

# Weekly EMAs (period in hours)
print("  Weekly EMAs...")
w_ema8 = ema(btc_close, 8 * HOURS_PER_WEEK)
w_ema13 = ema(btc_close, 13 * HOURS_PER_WEEK)
w_ema21 = ema(btc_close, 21 * HOURS_PER_WEEK)
w_ema34 = ema(btc_close, 34 * HOURS_PER_WEEK)
w_ema55 = ema(btc_close, 55 * HOURS_PER_WEEK)

# Daily SMAs
print("  Daily SMAs...")
d_sma20 = sma(btc_close, 20 * HOURS_PER_DAY)
d_sma50 = sma(btc_close, 50 * HOURS_PER_DAY)
d_sma200 = sma(btc_close, 200 * HOURS_PER_DAY)

# ---------------------------------------------------------------------------
# 3. Compute features for each trade at entry bar
# ---------------------------------------------------------------------------
print("\nComputing features for each trade...")


def compute_daily_closes(close_1h: np.ndarray, up_to_bar: int) -> np.ndarray:
    """Extract daily closes from 1h data up to bar index (inclusive).
    Returns array of daily closes."""
    data = close_1h[:up_to_bar + 1]
    n_days = len(data) // HOURS_PER_DAY
    if n_days == 0:
        return np.array([data[-1]])
    # Take the last bar of each 24h block, aligned from the end
    offset = len(data) % HOURS_PER_DAY
    if offset > 0:
        data = data[offset:]
    return data[HOURS_PER_DAY - 1::HOURS_PER_DAY]


def compute_monthly_closes(close_1h: np.ndarray, up_to_bar: int) -> np.ndarray:
    """Extract monthly closes from 1h data."""
    data = close_1h[:up_to_bar + 1]
    n_months = len(data) // HOURS_PER_MONTH
    if n_months == 0:
        return np.array([data[-1]])
    offset = len(data) % HOURS_PER_MONTH
    if offset > 0:
        data = data[offset:]
    return data[HOURS_PER_MONTH - 1::HOURS_PER_MONTH]


def compute_weekly_closes(close_1h: np.ndarray, up_to_bar: int) -> np.ndarray:
    """Extract weekly closes from 1h data."""
    data = close_1h[:up_to_bar + 1]
    n_weeks = len(data) // HOURS_PER_WEEK
    if n_weeks == 0:
        return np.array([data[-1]])
    offset = len(data) % HOURS_PER_WEEK
    if offset > 0:
        data = data[offset:]
    return data[HOURS_PER_WEEK - 1::HOURS_PER_WEEK]


def rsi_14(daily_closes: np.ndarray) -> float:
    """Compute 14-day RSI from daily closes."""
    if len(daily_closes) < 15:
        return np.nan
    deltas = np.diff(daily_closes[-15:])
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def count_ema21_crosses_last_8w(close_1h: np.ndarray, ema21_arr: np.ndarray,
                                 bar: int) -> int:
    """Count number of times price crosses EMA21 in last 8 weekly bars."""
    lookback = 8 * HOURS_PER_WEEK
    start = max(0, bar - lookback)
    price = close_1h[start:bar + 1]
    ema_vals = ema21_arr[start:bar + 1]
    if len(price) < 2:
        return 0
    above = price > ema_vals
    crosses = np.sum(np.abs(np.diff(above.astype(int))))
    return int(crosses)


feature_names = [
    # Monthly
    "m_ret_1mo", "m_ret_2mo", "m_ret_3mo", "m_ret_6mo",
    "m_green_ratio_3mo", "m_green_ratio_6mo",
    "m_body_avg_3mo", "m_body_avg_6mo", "m_trend_accel",
    # Weekly
    "w_dist_sma20", "w_dist_sma50", "w_dist_sma100", "w_dist_sma200",
    "w_dist_ema8", "w_dist_ema21", "w_dist_ema55",
    "w_sma20_above_sma50", "w_sma50_above_sma200",
    "w_ema_alignment", "w_ribbon_width", "w_chop_8w", "w_ema8_slope",
    # Daily
    "d_dist_sma20", "d_dist_sma50", "d_dist_sma200",
    "d_ret_7d", "d_ret_14d", "d_ret_30d",
    "d_rsi_14d", "d_vol_30d", "d_vol_ratio",
    # Cross-timeframe
    "align_m_w", "align_w_d", "align_all",
    # Drawdown
    "dd_from_ath", "dd_from_6mo_high",
]

n_features = len(feature_names)
n_trades = len(trades)
features_matrix = np.full((n_trades, n_features), np.nan)

for i in range(n_trades):
    bar = trades.iloc[i]["entry_bar"]
    if bar < 0 or bar >= len(btc_close):
        continue

    price = btc_close[bar]

    # Monthly features
    mc = compute_monthly_closes(btc_close, bar)
    if len(mc) >= 2:
        features_matrix[i, 0] = (mc[-1] / mc[-2] - 1) * 100  # m_ret_1mo
    if len(mc) >= 3:
        features_matrix[i, 1] = ((mc[-1] / mc[-3]) ** (1/2) - 1) * 100  # m_ret_2mo avg
    if len(mc) >= 4:
        features_matrix[i, 2] = ((mc[-1] / mc[-4]) ** (1/3) - 1) * 100  # m_ret_3mo avg
    if len(mc) >= 7:
        features_matrix[i, 3] = ((mc[-1] / mc[-7]) ** (1/6) - 1) * 100  # m_ret_6mo avg

    # Green ratio
    if len(mc) >= 4:
        monthly_rets = np.diff(mc[-4:]) / mc[-4:-1]
        features_matrix[i, 4] = np.mean(monthly_rets > 0)  # m_green_ratio_3mo
    if len(mc) >= 7:
        monthly_rets6 = np.diff(mc[-7:]) / mc[-7:-1]
        features_matrix[i, 5] = np.mean(monthly_rets6 > 0)  # m_green_ratio_6mo

    # Body size
    if len(mc) >= 4:
        bodies3 = np.abs(np.diff(mc[-4:])) / mc[-4:-1] * 100
        features_matrix[i, 6] = np.mean(bodies3)  # m_body_avg_3mo
    if len(mc) >= 7:
        bodies6 = np.abs(np.diff(mc[-7:])) / mc[-7:-1] * 100
        features_matrix[i, 7] = np.mean(bodies6)  # m_body_avg_6mo

    # Trend acceleration
    features_matrix[i, 8] = (features_matrix[i, 0] or 0) - (features_matrix[i, 2] or 0)  # m_trend_accel

    # Weekly features - distances from MAs
    if not np.isnan(w_sma20[bar]):
        features_matrix[i, 9] = (price - w_sma20[bar]) / w_sma20[bar] * 100
    if not np.isnan(w_sma50[bar]):
        features_matrix[i, 10] = (price - w_sma50[bar]) / w_sma50[bar] * 100
    if not np.isnan(w_sma100[bar]):
        features_matrix[i, 11] = (price - w_sma100[bar]) / w_sma100[bar] * 100
    if not np.isnan(w_sma200[bar]):
        features_matrix[i, 12] = (price - w_sma200[bar]) / w_sma200[bar] * 100

    features_matrix[i, 13] = (price - w_ema8[bar]) / w_ema8[bar] * 100  # w_dist_ema8
    features_matrix[i, 14] = (price - w_ema21[bar]) / w_ema21[bar] * 100  # w_dist_ema21
    features_matrix[i, 15] = (price - w_ema55[bar]) / w_ema55[bar] * 100  # w_dist_ema55

    # MA cross states
    if not np.isnan(w_sma20[bar]) and not np.isnan(w_sma50[bar]):
        features_matrix[i, 16] = float(w_sma20[bar] > w_sma50[bar])  # w_sma20_above_sma50
    if not np.isnan(w_sma50[bar]) and not np.isnan(w_sma200[bar]):
        features_matrix[i, 17] = float(w_sma50[bar] > w_sma200[bar])  # w_sma50_above_sma200

    # EMA alignment: count consecutive bull-order pairs (8>13>21>34>55)
    ema_vals = [w_ema8[bar], w_ema13[bar], w_ema21[bar], w_ema34[bar], w_ema55[bar]]
    bull_count = sum(1 for j in range(4) if ema_vals[j] > ema_vals[j+1])
    # Normalize: 4 means full bull, 0 means full bear -> map to [-1, 1]
    features_matrix[i, 18] = (bull_count - 2) / 2.0  # w_ema_alignment

    # Ribbon width
    features_matrix[i, 19] = (w_ema8[bar] - w_ema55[bar]) / w_ema55[bar] * 100  # w_ribbon_width

    # Chop
    features_matrix[i, 20] = count_ema21_crosses_last_8w(btc_close, w_ema21, bar)  # w_chop_8w

    # EMA8 slope (weekly % change)
    if bar >= HOURS_PER_WEEK:
        features_matrix[i, 21] = (w_ema8[bar] / w_ema8[bar - HOURS_PER_WEEK] - 1) * 100  # w_ema8_slope

    # Daily features
    if not np.isnan(d_sma20[bar]):
        features_matrix[i, 22] = (price - d_sma20[bar]) / d_sma20[bar] * 100  # d_dist_sma20
    if not np.isnan(d_sma50[bar]):
        features_matrix[i, 23] = (price - d_sma50[bar]) / d_sma50[bar] * 100  # d_dist_sma50
    if not np.isnan(d_sma200[bar]):
        features_matrix[i, 24] = (price - d_sma200[bar]) / d_sma200[bar] * 100  # d_dist_sma200

    # Daily returns
    if bar >= 7 * HOURS_PER_DAY:
        features_matrix[i, 25] = (price / btc_close[bar - 7 * HOURS_PER_DAY] - 1) * 100  # d_ret_7d
    if bar >= 14 * HOURS_PER_DAY:
        features_matrix[i, 26] = (price / btc_close[bar - 14 * HOURS_PER_DAY] - 1) * 100  # d_ret_14d
    if bar >= 30 * HOURS_PER_DAY:
        features_matrix[i, 27] = (price / btc_close[bar - 30 * HOURS_PER_DAY] - 1) * 100  # d_ret_30d

    # RSI
    dc = compute_daily_closes(btc_close, bar)
    features_matrix[i, 28] = rsi_14(dc)  # d_rsi_14d

    # Volatility
    if len(dc) >= 31:
        log_rets_30 = np.diff(np.log(dc[-31:]))
        features_matrix[i, 29] = np.std(log_rets_30) * np.sqrt(365) * 100  # d_vol_30d annualized
    if len(dc) >= 31:
        log_rets_7 = np.diff(np.log(dc[-8:]))
        vol_7 = np.std(log_rets_7)
        vol_30 = np.std(np.diff(np.log(dc[-31:])))
        features_matrix[i, 30] = vol_7 / vol_30 if vol_30 > 0 else np.nan  # d_vol_ratio

    # Cross-timeframe alignment
    m_trend = features_matrix[i, 2] if not np.isnan(features_matrix[i, 2]) else 0  # m_ret_3mo
    w_trend = features_matrix[i, 10] if not np.isnan(features_matrix[i, 10]) else 0  # w_dist_sma50
    d_trend = features_matrix[i, 23] if not np.isnan(features_matrix[i, 23]) else 0  # d_dist_sma50

    features_matrix[i, 31] = float((m_trend > 0) == (w_trend > 0))  # align_m_w
    features_matrix[i, 32] = float((w_trend > 0) == (d_trend > 0))  # align_w_d
    features_matrix[i, 33] = float((m_trend > 0) and (w_trend > 0) and (d_trend > 0)) or \
                              float((m_trend <= 0) and (w_trend <= 0) and (d_trend <= 0))  # align_all

    # Drawdown features
    ath = np.max(btc_high[:bar + 1])
    features_matrix[i, 34] = (price - ath) / ath * 100  # dd_from_ath

    lookback_6mo = min(bar + 1, 6 * HOURS_PER_MONTH)
    high_6mo = np.max(btc_high[bar + 1 - lookback_6mo:bar + 1])
    features_matrix[i, 35] = (price - high_6mo) / high_6mo * 100  # dd_from_6mo_high

    if (i + 1) % 200 == 0:
        print(f"  Computed features for {i+1}/{n_trades} trades")

print(f"  Done. Feature matrix shape: {features_matrix.shape}")

# ---------------------------------------------------------------------------
# 4. Build feature DataFrame
# ---------------------------------------------------------------------------
feat_df = pd.DataFrame(features_matrix, columns=feature_names)
feat_df["pnl"] = trades["pnl"].values
feat_df["direction"] = trades["direction"].values
feat_df["margin_usd"] = trades["margin_usd"].values
feat_df["pnl_pct"] = feat_df["pnl"] / feat_df["margin_usd"] * 100  # return on margin

# Drop rows with too many NaN features (early trades)
valid_mask = feat_df[feature_names].notna().sum(axis=1) >= 20
feat_df_valid = feat_df[valid_mask].copy()
print(f"\nValid trades (>=20 features): {len(feat_df_valid)} / {len(feat_df)}")

longs = feat_df_valid[feat_df_valid["direction"] == 1]
shorts = feat_df_valid[feat_df_valid["direction"] == -1]
print(f"Longs: {len(longs)}, Shorts: {len(shorts)}")
print(f"Long mean PnL: ${longs['pnl'].mean():.2f}, Short mean PnL: ${shorts['pnl'].mean():.2f}")

# ---------------------------------------------------------------------------
# 5. Analysis: Feature correlations with PnL
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ANALYSIS 1: Feature Correlation with Trade PnL (Spearman)")
print("=" * 80)


def compute_correlations(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Compute Spearman correlation of each feature with PnL."""
    results = []
    for fname in feature_names:
        vals = df[fname].values
        pnl = df["pnl"].values
        mask = ~np.isnan(vals)
        if mask.sum() < 30:
            continue
        corr, pval = stats.spearmanr(vals[mask], pnl[mask])
        results.append({
            "feature": fname,
            "spearman_r": round(corr, 4),
            "p_value": round(pval, 4),
            "n": int(mask.sum()),
            "direction": label,
        })
    return pd.DataFrame(results).sort_values("spearman_r", key=abs, ascending=False)


all_corr = compute_correlations(feat_df_valid, "ALL")
long_corr = compute_correlations(longs, "LONG")
short_corr = compute_correlations(shorts, "SHORT")

print("\nTop 20 features (ALL trades):")
print(all_corr.head(20).to_string(index=False))

print("\nTop 10 features (LONG trades):")
print(long_corr.head(10).to_string(index=False))

print("\nTop 10 features (SHORT trades):")
print(short_corr.head(10).to_string(index=False))

# ---------------------------------------------------------------------------
# 6. Analysis: Per-direction median split
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ANALYSIS 2: Median Split — Mean PnL Above vs Below Median")
print("=" * 80)


def median_split_analysis(df: pd.DataFrame, label: str, top_features: list) -> list:
    """For each feature, compute mean PnL when feature > median vs <= median."""
    results = []
    for fname in top_features:
        vals = df[fname].values
        pnl = df["pnl"].values
        mask = ~np.isnan(vals)
        if mask.sum() < 30:
            continue
        med = np.nanmedian(vals[mask])
        above = pnl[mask & (vals > med)]
        below = pnl[mask & (vals <= med)]
        results.append({
            "feature": fname,
            "median": round(med, 4),
            "mean_pnl_above": round(above.mean(), 2) if len(above) > 0 else np.nan,
            "mean_pnl_below": round(below.mean(), 2) if len(below) > 0 else np.nan,
            "n_above": len(above),
            "n_below": len(below),
            "pnl_diff": round(above.mean() - below.mean(), 2) if len(above) > 0 and len(below) > 0 else np.nan,
        })
    return results


top_long_features = long_corr.head(10)["feature"].tolist()
top_short_features = short_corr.head(10)["feature"].tolist()

print(f"\nLONG trades (n={len(longs)}):")
long_split = median_split_analysis(longs, "LONG", top_long_features)
print(pd.DataFrame(long_split).to_string(index=False))

print(f"\nSHORT trades (n={len(shorts)}):")
short_split = median_split_analysis(shorts, "SHORT", top_short_features)
print(pd.DataFrame(short_split).to_string(index=False))

# ---------------------------------------------------------------------------
# 7. Analysis: Feature interactions (top 5 pairs)
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ANALYSIS 3: Feature Interactions (Top 5 x Top 5 Pairs)")
print("=" * 80)


def interaction_analysis(df: pd.DataFrame, top_feats: list, label: str):
    """For top 5 features, analyze pairwise conditional PnL."""
    top5 = top_feats[:5]
    print(f"\n{label} — Pair-wise conditional analysis:")
    print(f"{'Feature A':>25s} {'Feature B':>25s} {'Both>med PnL':>14s} {'Neither PnL':>14s} {'n_both':>7s} {'n_neither':>10s} {'lift':>8s}")
    print("-" * 110)

    results = []
    for i_a in range(len(top5)):
        for i_b in range(i_a + 1, len(top5)):
            fa, fb = top5[i_a], top5[i_b]
            va = df[fa].values
            vb = df[fb].values
            pnl = df["pnl"].values
            mask = ~np.isnan(va) & ~np.isnan(vb)
            if mask.sum() < 30:
                continue
            med_a = np.nanmedian(va[mask])
            med_b = np.nanmedian(vb[mask])

            both_above = mask & (va > med_a) & (vb > med_b)
            neither = mask & (va <= med_a) & (vb <= med_b)

            pnl_both = pnl[both_above].mean() if both_above.sum() > 5 else np.nan
            pnl_neither = pnl[neither].mean() if neither.sum() > 5 else np.nan
            lift = pnl_both - pnl_neither if not (np.isnan(pnl_both) or np.isnan(pnl_neither)) else np.nan

            print(f"{fa:>25s} {fb:>25s} {pnl_both:>14.2f} {pnl_neither:>14.2f} {both_above.sum():>7d} {neither.sum():>10d} {lift:>8.2f}")

            results.append({
                "feature_a": fa, "feature_b": fb,
                "pnl_both_above_median": round(pnl_both, 2),
                "pnl_neither_above_median": round(pnl_neither, 2),
                "n_both": int(both_above.sum()),
                "n_neither": int(neither.sum()),
                "lift": round(lift, 2) if not np.isnan(lift) else None,
            })
    return results


long_interactions = interaction_analysis(longs, top_long_features, "LONG")
short_interactions = interaction_analysis(shorts, top_short_features, "SHORT")

# ---------------------------------------------------------------------------
# 8. Optimal thresholds
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ANALYSIS 4: Optimal Thresholds (Feature Filtering)")
print("=" * 80)


def find_optimal_threshold(df: pd.DataFrame, fname: str, direction_label: str,
                           higher_is_better: bool = True) -> dict:
    """Sweep thresholds to find best PnL filter for a feature."""
    vals = df[fname].values
    pnl = df["pnl"].values
    mask = ~np.isnan(vals)
    if mask.sum() < 30:
        return {}

    percentiles = [10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90]
    thresholds = np.percentile(vals[mask], percentiles)

    base_pnl = pnl[mask].mean()
    base_wr = (pnl[mask] > 0).mean()
    best = {"improvement": -np.inf}

    for pct, thresh in zip(percentiles, thresholds):
        if higher_is_better:
            sel = mask & (vals >= thresh)
        else:
            sel = mask & (vals <= thresh)

        if sel.sum() < 20:
            continue

        mean_pnl = pnl[sel].mean()
        wr = (pnl[sel] > 0).mean()
        improvement = mean_pnl - base_pnl

        if improvement > best["improvement"]:
            best = {
                "feature": fname,
                "direction": direction_label,
                "threshold": round(float(thresh), 4),
                "percentile": pct,
                "filter_direction": ">=" if higher_is_better else "<=",
                "mean_pnl_filtered": round(float(mean_pnl), 2),
                "mean_pnl_baseline": round(float(base_pnl), 2),
                "improvement": round(float(improvement), 2),
                "improvement_pct": round(float(improvement / abs(base_pnl) * 100), 1) if base_pnl != 0 else 0,
                "win_rate_filtered": round(float(wr) * 100, 1),
                "win_rate_baseline": round(float(base_wr) * 100, 1),
                "n_trades_kept": int(sel.sum()),
                "n_trades_total": int(mask.sum()),
                "kept_pct": round(float(sel.sum() / mask.sum() * 100), 1),
            }

    return best


print("\nLONG optimal thresholds:")
long_thresholds = []
for fname in top_long_features:
    # Determine if positive correlation (higher = better) or negative
    corr_row = long_corr[long_corr["feature"] == fname]
    if len(corr_row) == 0:
        continue
    higher = corr_row.iloc[0]["spearman_r"] > 0
    result = find_optimal_threshold(longs, fname, "LONG", higher_is_better=higher)
    if result:
        long_thresholds.append(result)
        r = result
        print(f"  {r['feature']:>25s}: filter {r['filter_direction']} {r['threshold']:>10.4f} "
              f"(p{r['percentile']:>2d}) -> PnL ${r['mean_pnl_filtered']:>8.2f} "
              f"(baseline ${r['mean_pnl_baseline']:>8.2f}, +${r['improvement']:>7.2f}, "
              f"WR {r['win_rate_filtered']:>5.1f}%, keep {r['kept_pct']:>4.1f}%)")

print("\nSHORT optimal thresholds:")
short_thresholds = []
for fname in top_short_features:
    corr_row = short_corr[short_corr["feature"] == fname]
    if len(corr_row) == 0:
        continue
    higher = corr_row.iloc[0]["spearman_r"] > 0
    result = find_optimal_threshold(shorts, fname, "SHORT", higher_is_better=higher)
    if result:
        short_thresholds.append(result)
        r = result
        print(f"  {r['feature']:>25s}: filter {r['filter_direction']} {r['threshold']:>10.4f} "
              f"(p{r['percentile']:>2d}) -> PnL ${r['mean_pnl_filtered']:>8.2f} "
              f"(baseline ${r['mean_pnl_baseline']:>8.2f}, +${r['improvement']:>7.2f}, "
              f"WR {r['win_rate_filtered']:>5.1f}%, keep {r['kept_pct']:>4.1f}%)")

# ---------------------------------------------------------------------------
# 9. Decision tree (depth-3) for longs and shorts
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ANALYSIS 5: Decision Tree Rules (depth=3)")
print("=" * 80)


def fit_decision_tree(df: pd.DataFrame, label: str) -> str:
    """Fit depth-3 decision tree on features -> binary PnL."""
    X = df[feature_names].values.copy()
    y = (df["pnl"].values > 0).astype(int)

    # Impute NaN with column medians
    col_medians = np.nanmedian(X, axis=0)
    for j in range(X.shape[1]):
        mask = np.isnan(X[:, j])
        X[mask, j] = col_medians[j]

    # Handle any remaining NaN
    X = np.nan_to_num(X, nan=0.0)

    clf = DecisionTreeClassifier(max_depth=3, min_samples_leaf=30, random_state=42)
    clf.fit(X, y)

    tree_text = export_text(clf, feature_names=feature_names, decimals=4)
    accuracy = clf.score(X, y)

    print(f"\n{label} Decision Tree (accuracy={accuracy:.3f}, n={len(y)}):")
    print(tree_text)

    # Feature importances
    importances = clf.feature_importances_
    top_idx = np.argsort(importances)[::-1][:10]
    print(f"\n{label} tree feature importances:")
    for idx in top_idx:
        if importances[idx] > 0:
            print(f"  {feature_names[idx]:>25s}: {importances[idx]:.4f}")

    return tree_text


long_tree = fit_decision_tree(longs, "LONG")
short_tree = fit_decision_tree(shorts, "SHORT")

# ---------------------------------------------------------------------------
# 10. Cross-timeframe layer analysis
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("ANALYSIS 6: Cross-Timeframe Layer Importance")
print("=" * 80)

monthly_features = [f for f in feature_names if f.startswith("m_")]
weekly_features = [f for f in feature_names if f.startswith("w_")]
daily_features = [f for f in feature_names if f.startswith("d_")]
cross_features = [f for f in feature_names if f.startswith("align_") or f.startswith("dd_")]


def layer_importance(df: pd.DataFrame, label: str):
    """Compute average absolute correlation by timeframe layer."""
    layers = {
        "Monthly": monthly_features,
        "Weekly": weekly_features,
        "Daily": daily_features,
        "Cross/DD": cross_features,
    }
    print(f"\n{label} — Average |Spearman r| by timeframe layer:")
    layer_results = {}
    for layer_name, feats in layers.items():
        corrs = []
        for fname in feats:
            vals = df[fname].values
            pnl = df["pnl"].values
            mask = ~np.isnan(vals)
            if mask.sum() < 30:
                continue
            r, _ = stats.spearmanr(vals[mask], pnl[mask])
            corrs.append(abs(r))
        if corrs:
            avg = np.mean(corrs)
            mx = np.max(corrs)
            layer_results[layer_name] = {"avg_abs_corr": round(avg, 4), "max_abs_corr": round(mx, 4), "n_features": len(corrs)}
            print(f"  {layer_name:>10s}: avg|r|={avg:.4f}, max|r|={mx:.4f}, n_features={len(corrs)}")
    return layer_results


long_layers = layer_importance(longs, "LONG")
short_layers = layer_importance(shorts, "SHORT")

# ---------------------------------------------------------------------------
# 11. Save results JSON
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("Saving results to JSON...")

output = {
    "metadata": {
        "n_trades_total": int(len(trades)),
        "n_trades_valid": int(len(feat_df_valid)),
        "n_longs": int(len(longs)),
        "n_shorts": int(len(shorts)),
        "features_computed": feature_names,
        "long_mean_pnl": round(float(longs["pnl"].mean()), 2),
        "short_mean_pnl": round(float(shorts["pnl"].mean()), 2),
        "long_win_rate": round(float((longs["pnl"] > 0).mean() * 100), 1),
        "short_win_rate": round(float((shorts["pnl"] > 0).mean() * 100), 1),
    },
    "correlations": {
        "all_top20": all_corr.head(20).to_dict(orient="records"),
        "long_top10": long_corr.head(10).to_dict(orient="records"),
        "short_top10": short_corr.head(10).to_dict(orient="records"),
    },
    "median_splits": {
        "long": long_split,
        "short": short_split,
    },
    "interactions": {
        "long": long_interactions,
        "short": short_interactions,
    },
    "optimal_thresholds": {
        "long": long_thresholds,
        "short": short_thresholds,
    },
    "decision_trees": {
        "long": long_tree,
        "short": short_tree,
    },
    "layer_importance": {
        "long": long_layers,
        "short": short_layers,
    },
}

with open(OUTPUT_JSON, "w") as f:
    json.dump(output, f, indent=2, default=str)

print(f"Results saved to {OUTPUT_JSON}")

# ---------------------------------------------------------------------------
# 12. Plain English Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("PLAIN ENGLISH SUMMARY")
print("=" * 80)

# Determine most important layer per direction
for label, layers in [("LONG", long_layers), ("SHORT", short_layers)]:
    best_layer = max(layers, key=lambda k: layers[k]["avg_abs_corr"])
    print(f"\n{label} trades: most predictive layer is {best_layer} "
          f"(avg|r|={layers[best_layer]['avg_abs_corr']:.4f})")

print("\n--- Top actionable rules ---")

# Print top 3 long thresholds
print("\nFor LONG trades:")
for r in sorted(long_thresholds, key=lambda x: x.get("improvement", 0), reverse=True)[:3]:
    print(f"  Filter: keep trades where {r['feature']} {r['filter_direction']} {r['threshold']:.4f}")
    print(f"    -> Mean PnL: ${r['mean_pnl_filtered']:.2f} vs baseline ${r['mean_pnl_baseline']:.2f} "
          f"(+${r['improvement']:.2f}, WR {r['win_rate_filtered']:.1f}%, "
          f"keeps {r['kept_pct']:.1f}% of trades)")

print("\nFor SHORT trades:")
for r in sorted(short_thresholds, key=lambda x: x.get("improvement", 0), reverse=True)[:3]:
    print(f"  Filter: keep trades where {r['feature']} {r['filter_direction']} {r['threshold']:.4f}")
    print(f"    -> Mean PnL: ${r['mean_pnl_filtered']:.2f} vs baseline ${r['mean_pnl_baseline']:.2f} "
          f"(+${r['improvement']:.2f}, WR {r['win_rate_filtered']:.1f}%, "
          f"keeps {r['kept_pct']:.1f}% of trades)")

print("\nDone.")
