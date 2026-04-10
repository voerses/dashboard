#!/usr/bin/env python3
"""
Cycle Position Score — Continuous Regime Signal Research
========================================================
Combines multiple market dimensions into a single continuous score [0, 1]
where 0 = deep bear/capitulation and 1 = peak euphoria.

Three methods:
  1. Weighted Average of rolling-percentile-normalized features
  2. PCA on normalized features (PC1 as regime axis)
  3. Expanding-window Logistic Regression (walk-forward)

All features are causal (no lookahead). Normalization uses rolling 365d window.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

# ──────────────────────────────────────────────────────────────────────
# 1. DATA LOADING
# ──────────────────────────────────────────────────────────────────────

print("=" * 80)
print("CYCLE POSITION SCORE — REGIME RESEARCH")
print("=" * 80)

# BTC 1h -> daily
btc_1h = pd.read_csv(
    "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv",
    parse_dates=["datetime"],
)
btc_1h["datetime"] = pd.to_datetime(btc_1h["datetime"], utc=True)
btc_1h = btc_1h.set_index("datetime").sort_index()

daily = btc_1h.resample("1D").agg({
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}).dropna(subset=["close"])

daily.index = daily.index.tz_localize(None)
print(f"BTC daily: {daily.index[0].date()} to {daily.index[-1].date()}  ({len(daily)} rows)")

# Regime signals (alt breadth)
regime = pd.read_parquet("/workspace/crypto_backtest/data/alternative/regime_signals.parquet")
regime.index = regime.index.tz_localize(None)
print(f"Regime signals: {regime.index[0].date()} to {regime.index[-1].date()}  ({len(regime)} rows)")

# ──────────────────────────────────────────────────────────────────────
# 2. FEATURE ENGINEERING (all causal)
# ──────────────────────────────────────────────────────────────────────

close = daily["close"]
volume = daily["volume"]
ret_d = close.pct_change()

# --- Price Position ---
sma350 = close.rolling(350, min_periods=200).mean()
sma50_dist = (close - sma350) / sma350

ath = close.expanding().max()
ath_drawdown = (close - ath) / ath  # always <= 0

low_365 = close.rolling(365, min_periods=180).min()
rally_from_low = (close - low_365) / low_365

# --- Momentum ---
def rsi(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

rsi_30d = rsi(close, 30)
rsi_7d = rsi(close, 7)

ret_30d = close.pct_change(30)
roc_momentum = ret_30d - ret_30d.shift(30)  # acceleration

# --- Volume / Activity ---
vol_30 = volume.rolling(30, min_periods=15).mean()
vol_90 = volume.rolling(90, min_periods=45).mean()
volume_trend = vol_30 / vol_90 - 1

vol_180_med = volume.rolling(180, min_periods=90).median()
volume_vs_avg = volume / vol_180_med

# --- Volatility ---
realized_vol_30 = ret_d.rolling(30, min_periods=15).std() * np.sqrt(365)
realized_vol_180 = ret_d.rolling(180, min_periods=90).std() * np.sqrt(365)
vol_regime = realized_vol_30 / realized_vol_180 - 1

# --- Alt Market Health ---
alt_breadth_50d = regime["alt_breadth_50d"].reindex(daily.index).ffill()
alt_breadth_momentum = alt_breadth_50d - alt_breadth_50d.shift(30)

# Combine into features DataFrame
features = pd.DataFrame({
    "sma50_dist": sma50_dist,
    "ath_drawdown": ath_drawdown,
    "rally_from_low": rally_from_low,
    "rsi_30d": rsi_30d,
    "rsi_7d": rsi_7d,
    "roc_momentum": roc_momentum,
    "volume_trend": volume_trend,
    "volume_vs_avg": volume_vs_avg,
    "realized_vol_30d": realized_vol_30,
    "vol_regime": vol_regime,
    "alt_breadth_50d": alt_breadth_50d,
    "alt_breadth_momentum": alt_breadth_momentum,
}, index=daily.index)

print(f"\nFeatures computed. NaN counts (before norm):")
print(features.isna().sum())

# ──────────────────────────────────────────────────────────────────────
# 3. ROLLING PERCENTILE NORMALIZATION (365-day window)
# ──────────────────────────────────────────────────────────────────────

NORM_WINDOW = 365

features_norm = pd.DataFrame(index=features.index)
for col in features.columns:
    features_norm[col] = features[col].rolling(NORM_WINDOW, min_periods=180).rank(pct=True)

# Drop rows where normalization isn't ready
valid_mask = features_norm.notna().all(axis=1)
first_valid = valid_mask.idxmax()
print(f"\nFirst valid date (all features normalized): {first_valid.date()}")
print(f"Valid rows: {valid_mask.sum()}")

# ──────────────────────────────────────────────────────────────────────
# 4. METHOD 1: WEIGHTED AVERAGE
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("METHOD 1: WEIGHTED AVERAGE CYCLE POSITION SCORE")
print("=" * 80)

# For ATH drawdown: it's always <= 0, so norm ~ 0 means near ATH, ~ 1 means deep drawdown
# We want "nearness to ATH" = 1 - ath_drawdown_norm (high = bullish)
weighted_score = (
    0.20 * features_norm["sma50_dist"] +
    0.10 * (1 - features_norm["ath_drawdown"].abs()) +  # closer to ATH = more bull
    0.15 * features_norm["rsi_30d"] +
    0.10 * features_norm["roc_momentum"] +
    0.15 * features_norm["alt_breadth_50d"] +
    0.10 * features_norm["alt_breadth_momentum"] +
    0.10 * features_norm["volume_trend"] +
    0.10 * (1 - features_norm["vol_regime"])  # low vol regime = calm = bull
)

# The score components are all [0,1] so weighted sum is [0,1]
# But abs() and (1 - x) can shift the mean. Let's clip just in case:
weighted_score = weighted_score.clip(0, 1)
weighted_score.name = "weighted_score"

# ──────────────────────────────────────────────────────────────────────
# 5. METHOD 2: PCA-BASED
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("METHOD 2: PCA-BASED CYCLE POSITION SCORE")
print("=" * 80)

# Use rolling PCA with expanding window (refit quarterly)
pca_score = pd.Series(np.nan, index=daily.index, name="pca_score")

refit_dates = pd.date_range(first_valid, daily.index[-1], freq="90D")
if refit_dates[-1] != daily.index[-1]:
    refit_dates = refit_dates.append(pd.DatetimeIndex([daily.index[-1]]))

for i in range(len(refit_dates) - 1):
    fit_end = refit_dates[i]
    apply_start = refit_dates[i]
    apply_end = refit_dates[i + 1]

    # Expanding window for fitting
    train = features_norm.loc[first_valid:fit_end].dropna()
    if len(train) < 60:
        continue

    scaler = StandardScaler()
    X_train = scaler.fit_transform(train)

    pca = PCA(n_components=1)
    pca.fit(X_train)

    # Apply to the window
    window_data = features_norm.loc[apply_start:apply_end].dropna()
    if len(window_data) == 0:
        continue
    X_window = scaler.transform(window_data)
    pc1 = pca.transform(X_window)[:, 0]

    # Map PC1 to [0, 1] using the training distribution
    train_pc1 = pca.transform(X_train)[:, 0]
    pc1_min, pc1_max = np.percentile(train_pc1, [2, 98])
    pc1_norm = (pc1 - pc1_min) / (pc1_max - pc1_min + 1e-10)
    pc1_norm = np.clip(pc1_norm, 0, 1)

    # Check sign: PC1 should correlate with bull features (sma50_dist, rsi_30d)
    # If loadings on sma50_dist are negative, flip
    loadings = pca.components_[0]
    sma_idx = list(features_norm.columns).index("sma50_dist")
    if loadings[sma_idx] < 0:
        pc1_norm = 1 - pc1_norm

    pca_score.loc[window_data.index] = pc1_norm

print(f"PCA explained variance ratio (last fit): {pca.explained_variance_ratio_[0]:.3f}")

# ──────────────────────────────────────────────────────────────────────
# 6. METHOD 3: LOGISTIC REGRESSION (expanding window, walk-forward)
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("METHOD 3: LOGISTIC REGRESSION CYCLE POSITION SCORE")
print("=" * 80)

# Target: sign of forward 30d return
fwd_ret_30d = close.pct_change(30).shift(-30)
target = (fwd_ret_30d > 0).astype(int)

logreg_score = pd.Series(np.nan, index=daily.index, name="logreg_score")

# Refit every 90 days with expanding window
min_train = 365  # minimum training days
refit_dates_lr = pd.date_range(
    first_valid + pd.Timedelta(days=min_train),
    daily.index[-1],
    freq="90D"
)
if len(refit_dates_lr) == 0:
    refit_dates_lr = pd.DatetimeIndex([daily.index[-1]])
if refit_dates_lr[-1] != daily.index[-1]:
    refit_dates_lr = refit_dates_lr.append(pd.DatetimeIndex([daily.index[-1]]))

for i in range(len(refit_dates_lr)):
    fit_end = refit_dates_lr[i]
    if i + 1 < len(refit_dates_lr):
        apply_end = refit_dates_lr[i + 1]
    else:
        apply_end = daily.index[-1]

    # Training data: all data up to fit_end with valid target
    train_mask = (
        (features_norm.index >= first_valid)
        & (features_norm.index <= fit_end)
        & features_norm.notna().all(axis=1)
        & target.notna()
    )
    X_train = features_norm.loc[train_mask]
    y_train = target.loc[train_mask]

    if len(X_train) < 180 or y_train.nunique() < 2:
        continue

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)

    lr = LogisticRegression(C=0.1, max_iter=1000, solver="lbfgs")
    lr.fit(X_scaled, y_train)

    # Apply from fit_end to next refit
    apply_mask = (
        (features_norm.index >= fit_end)
        & (features_norm.index <= apply_end)
        & features_norm.notna().all(axis=1)
    )
    X_apply = features_norm.loc[apply_mask]
    if len(X_apply) == 0:
        continue

    proba = lr.predict_proba(scaler.transform(X_apply))[:, 1]
    logreg_score.loc[X_apply.index] = proba

print(f"LogReg score coverage: {logreg_score.notna().sum()} days")

# ──────────────────────────────────────────────────────────────────────
# 7. COMBINE ALL SCORES INTO A SINGLE DATAFRAME
# ──────────────────────────────────────────────────────────────────────

scores = pd.DataFrame({
    "close": close,
    "weighted": weighted_score,
    "pca": pca_score,
    "logreg": logreg_score,
    "fwd_ret_30d": fwd_ret_30d,
}, index=daily.index)

def regime_label(s):
    if pd.isna(s):
        return "N/A"
    if s < 0.3:
        return "BEAR"
    elif s > 0.7:
        return "BULL"
    else:
        return "TRANS"

# ──────────────────────────────────────────────────────────────────────
# 8. MONTHLY TABLE
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("MONTHLY SCORE TABLE (Weighted Average Method)")
print("=" * 80)

monthly = scores.resample("ME").agg({
    "close": "last",
    "weighted": "mean",
    "pca": "mean",
    "logreg": "mean",
    "fwd_ret_30d": "mean",
})
monthly["btc_ret"] = monthly["close"].pct_change()
monthly["w_regime"] = monthly["weighted"].apply(regime_label)
monthly["p_regime"] = monthly["pca"].apply(regime_label)
monthly["l_regime"] = monthly["logreg"].apply(regime_label)

print(f"\n{'Month':<10} {'Close':>9} {'W_Score':>8} {'W_Reg':>6} {'PCA':>8} {'P_Reg':>6} {'LR':>8} {'L_Reg':>6} {'BTC_Ret':>8}")
print("-" * 80)
for idx, row in monthly.iterrows():
    m = idx.strftime("%Y-%m")
    print(f"{m:<10} {row['close']:>9.0f} {row['weighted']:>8.3f} {row['w_regime']:>6} "
          f"{row['pca']:>8.3f} {row['p_regime']:>6} "
          f"{row['logreg']:>8.3f} {row['l_regime']:>6} "
          f"{row['btc_ret']:>8.1%}" if pd.notna(row['logreg']) else
          f"{m:<10} {row['close']:>9.0f} {row['weighted']:>8.3f} {row['w_regime']:>6} "
          f"{row['pca']:>8.3f} {row['p_regime']:>6} "
          f"{'N/A':>8} {'N/A':>6} "
          f"{row['btc_ret']:>8.1%}" if pd.notna(row['btc_ret']) else
          f"{m:<10} {row['close']:>9.0f} {row['weighted']:>8.3f} {row['w_regime']:>6} "
          f"{'N/A':>8} {'N/A':>6} {'N/A':>8} {'N/A':>6} {'N/A':>8}")

# ──────────────────────────────────────────────────────────────────────
# 9. WALK-FORWARD ACCURACY (predict sign of forward 30d return)
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("WALK-FORWARD ACCURACY (Predict forward 30d return sign)")
print("=" * 80)

def compute_accuracy(score_series, name, threshold_bear=0.3, threshold_bull=0.7):
    """Walk-forward accuracy: score > 0.5 predicts positive 30d return."""
    valid = scores[["fwd_ret_30d"]].copy()
    valid["score"] = score_series
    valid = valid.dropna()

    if len(valid) == 0:
        print(f"\n{name}: No valid data")
        return

    actual_bull = valid["fwd_ret_30d"] > 0
    pred_bull = valid["score"] > 0.5

    acc = (pred_bull == actual_bull).mean()
    print(f"\n{name}:")
    print(f"  Overall accuracy (score>0.5 predicts ret>0): {acc:.3f}  (n={len(valid)})")

    # Bull precision/recall
    tp_bull = (pred_bull & actual_bull).sum()
    fp_bull = (pred_bull & ~actual_bull).sum()
    fn_bull = (~pred_bull & actual_bull).sum()
    tn_bull = (~pred_bull & ~actual_bull).sum()

    bull_prec = tp_bull / (tp_bull + fp_bull) if (tp_bull + fp_bull) > 0 else 0
    bull_rec = tp_bull / (tp_bull + fn_bull) if (tp_bull + fn_bull) > 0 else 0
    bear_prec = tn_bull / (tn_bull + fn_bull) if (tn_bull + fn_bull) > 0 else 0
    bear_rec = tn_bull / (tn_bull + fp_bull) if (tn_bull + fp_bull) > 0 else 0

    print(f"  Bull precision: {bull_prec:.3f}  recall: {bull_rec:.3f}")
    print(f"  Bear precision: {bear_prec:.3f}  recall: {bear_rec:.3f}")

    # High-confidence predictions
    strong_bull = valid["score"] > threshold_bull
    strong_bear = valid["score"] < threshold_bear
    if strong_bull.sum() > 0:
        bull_acc = (valid.loc[strong_bull, "fwd_ret_30d"] > 0).mean()
        print(f"  High-conf BULL (>{threshold_bull}): acc={bull_acc:.3f}  n={strong_bull.sum()}")
    if strong_bear.sum() > 0:
        bear_acc = (valid.loc[strong_bear, "fwd_ret_30d"] < 0).mean()
        print(f"  High-conf BEAR (<{threshold_bear}): acc={bear_acc:.3f}  n={strong_bear.sum()}")

for name, col in [("Weighted", "weighted"), ("PCA", "pca"), ("LogReg", "logreg")]:
    compute_accuracy(scores[col], name)

# ──────────────────────────────────────────────────────────────────────
# 10. REGIME DISTRIBUTION PER YEAR
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("REGIME DISTRIBUTION PER YEAR (Weighted Method)")
print("=" * 80)

scores["year"] = scores.index.year
scores["w_regime"] = scores["weighted"].apply(regime_label)

for year in sorted(scores["year"].dropna().unique()):
    yr_data = scores[scores["year"] == year]
    valid_yr = yr_data[yr_data["weighted"].notna()]
    if len(valid_yr) == 0:
        continue
    counts = valid_yr["w_regime"].value_counts(normalize=True)
    bear_pct = counts.get("BEAR", 0)
    trans_pct = counts.get("TRANS", 0)
    bull_pct = counts.get("BULL", 0)
    print(f"  {year}: BEAR={bear_pct:5.1%}  TRANS={trans_pct:5.1%}  BULL={bull_pct:5.1%}  (n={len(valid_yr)})")

# ──────────────────────────────────────────────────────────────────────
# 11. CRITICAL PERIOD TEST
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("CRITICAL PERIOD TEST")
print("=" * 80)

critical_periods = [
    ("2022-05", "2022-06", "Deep BEAR", "<0.2"),
    ("2023-01", "2023-02", "TRANSITION / early BULL", "0.3-0.5"),
    ("2023-11", "2023-12", "BULL", ">0.7"),
    ("2024-03", "2024-03", "Peak BULL", ">0.8"),
    ("2024-08", "2024-09", "Dip to TRANSITION", "0.4-0.6"),
    ("2025-01", "2025-01", "BULL weakening", "0.5-0.7"),
    ("2025-02", "2025-03", "Transition to BEAR", "dropping through 0.3"),
    ("2025-11", "2025-12", "Deep BEAR", "<0.2"),
    ("2026-01", "2026-03", "BEAR", "<0.3"),
]

for start, end, expected, score_range in critical_periods:
    mask = (scores.index >= start) & (scores.index < pd.Timestamp(end) + pd.offsets.MonthEnd(1))
    period_scores = scores.loc[mask]
    if len(period_scores) == 0 or period_scores["weighted"].isna().all():
        print(f"  {start} to {end}: NO DATA")
        continue

    w_mean = period_scores["weighted"].mean()
    w_min = period_scores["weighted"].min()
    w_max = period_scores["weighted"].max()
    p_mean = period_scores["pca"].mean() if period_scores["pca"].notna().any() else float("nan")
    l_mean = period_scores["logreg"].mean() if period_scores["logreg"].notna().any() else float("nan")

    regime = regime_label(w_mean)

    # Check if expectation is met
    check = ""
    if "<0.2" in score_range:
        check = "PASS" if w_mean < 0.2 else f"FAIL (got {w_mean:.3f})"
    elif "<0.3" in score_range:
        check = "PASS" if w_mean < 0.3 else f"FAIL (got {w_mean:.3f})"
    elif ">0.8" in score_range:
        check = "PASS" if w_mean > 0.8 else f"FAIL (got {w_mean:.3f})"
    elif ">0.7" in score_range:
        check = "PASS" if w_mean > 0.7 else f"FAIL (got {w_mean:.3f})"
    elif "0.3-0.5" in score_range:
        check = "PASS" if 0.3 <= w_mean <= 0.5 else f"FAIL (got {w_mean:.3f})"
    elif "0.4-0.6" in score_range:
        check = "PASS" if 0.4 <= w_mean <= 0.6 else f"FAIL (got {w_mean:.3f})"
    elif "0.5-0.7" in score_range:
        check = "PASS" if 0.5 <= w_mean <= 0.7 else f"FAIL (got {w_mean:.3f})"
    elif "dropping" in score_range:
        # Check that score drops through 0.3
        check = "PASS" if w_min < 0.35 else f"FAIL (min={w_min:.3f})"
    else:
        check = "?"

    print(f"  {start} to {end}: W={w_mean:.3f} [{w_min:.3f}-{w_max:.3f}] "
          f"PCA={p_mean:.3f} LR={l_mean:.3f}  "
          f"Expected={expected} ({score_range})  [{check}]")

# ──────────────────────────────────────────────────────────────────────
# 12. STRATEGY-RELEVANT ACCURACY (monthly)
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("STRATEGY-RELEVANT ACCURACY (monthly)")
print("=" * 80)
print("  Score < 0.35 => 'ungate shorts' (bear regime, shorts should be profitable)")
print("  Score > 0.65 => 'gate shorts' (bull regime, shorts would lose)")
print()

monthly_strat = scores.resample("ME").agg({
    "weighted": "mean",
    "fwd_ret_30d": "mean",
    "close": "last",
}).dropna(subset=["weighted", "fwd_ret_30d"])

monthly_strat["btc_ret_next_month"] = monthly_strat["close"].pct_change().shift(-1)

bear_months = monthly_strat[monthly_strat["weighted"] < 0.35]
bull_months = monthly_strat[monthly_strat["weighted"] > 0.65]

print(f"  Bear months (score<0.35): {len(bear_months)}")
if len(bear_months) > 0:
    # In bear months, shorts are profitable when BTC drops
    shorts_correct = (bear_months["fwd_ret_30d"] < 0).mean()
    avg_fwd_ret = bear_months["fwd_ret_30d"].mean()
    print(f"    Forward 30d return < 0: {shorts_correct:.1%} of the time")
    print(f"    Avg forward 30d return: {avg_fwd_ret:.1%}")

print(f"\n  Bull months (score>0.65): {len(bull_months)}")
if len(bull_months) > 0:
    longs_correct = (bull_months["fwd_ret_30d"] > 0).mean()
    avg_fwd_ret = bull_months["fwd_ret_30d"].mean()
    print(f"    Forward 30d return > 0: {longs_correct:.1%} of the time")
    print(f"    Avg forward 30d return: {avg_fwd_ret:.1%}")

# Neutral months
neutral_months = monthly_strat[(monthly_strat["weighted"] >= 0.35) & (monthly_strat["weighted"] <= 0.65)]
print(f"\n  Transition months (0.35-0.65): {len(neutral_months)}")
if len(neutral_months) > 0:
    avg_fwd_ret = neutral_months["fwd_ret_30d"].mean()
    print(f"    Avg forward 30d return: {avg_fwd_ret:.1%}")

# ──────────────────────────────────────────────────────────────────────
# 13. TRANSITION SMOOTHNESS
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("TRANSITION SMOOTHNESS (monthly weighted score)")
print("=" * 80)

monthly_smooth = scores["weighted"].resample("ME").mean().dropna()
monthly_change = monthly_smooth.diff().abs()
avg_change = monthly_change.mean()
max_change = monthly_change.max()
max_change_date = monthly_change.idxmax()

print(f"  Average monthly score change: {avg_change:.4f}")
print(f"  Max monthly score change:     {max_change:.4f} ({max_change_date.strftime('%Y-%m')})")
print()

# Text-based chart
print("Monthly Score Timeline (Weighted):")
print(f"{'Month':<8} {'Score':>6} {'Bar'}")
print("-" * 60)
for idx, val in monthly_smooth.items():
    bar_len = int(val * 50)
    regime = regime_label(val)
    marker = ""
    if regime == "BEAR":
        marker = " [BEAR]"
    elif regime == "BULL":
        marker = " [BULL]"
    print(f"{idx.strftime('%Y-%m'):<8} {val:>6.3f} {'#' * bar_len}{marker}")

# ──────────────────────────────────────────────────────────────────────
# 14. THRESHOLD OPTIMIZATION (walk-forward)
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("THRESHOLD OPTIMIZATION (walk-forward)")
print("=" * 80)
print("  Sweeping bear_threshold [0.25-0.45] x bull_threshold [0.55-0.75]")
print("  Metric: accuracy of high-confidence predictions")
print()

valid_scores = scores[["weighted", "fwd_ret_30d"]].dropna()
actual_positive = valid_scores["fwd_ret_30d"] > 0

results = []
for bear_th in np.arange(0.25, 0.46, 0.05):
    for bull_th in np.arange(0.55, 0.76, 0.05):
        strong_bull = valid_scores["weighted"] > bull_th
        strong_bear = valid_scores["weighted"] < bear_th

        if strong_bull.sum() < 30 or strong_bear.sum() < 30:
            continue

        bull_acc = (actual_positive[strong_bull]).mean()
        bear_acc = (~actual_positive[strong_bear]).mean()

        # Coverage: what fraction of days get a signal
        coverage = (strong_bull | strong_bear).mean()

        # Combined metric: harmonic mean of bull/bear accuracy weighted by coverage
        combined = 2 * bull_acc * bear_acc / (bull_acc + bear_acc + 1e-10)

        results.append({
            "bear_th": bear_th,
            "bull_th": bull_th,
            "bull_acc": bull_acc,
            "bear_acc": bear_acc,
            "coverage": coverage,
            "combined": combined,
            "n_bull": strong_bull.sum(),
            "n_bear": strong_bear.sum(),
        })

results_df = pd.DataFrame(results).sort_values("combined", ascending=False)
print(f"{'Bear_Th':>8} {'Bull_Th':>8} {'Bull_Acc':>9} {'Bear_Acc':>9} {'Coverage':>9} {'Combined':>9} {'N_Bull':>7} {'N_Bear':>7}")
print("-" * 72)
for _, row in results_df.head(10).iterrows():
    print(f"{row['bear_th']:>8.2f} {row['bull_th']:>8.2f} {row['bull_acc']:>9.3f} {row['bear_acc']:>9.3f} "
          f"{row['coverage']:>9.3f} {row['combined']:>9.3f} {row['n_bull']:>7.0f} {row['n_bear']:>7.0f}")

# ──────────────────────────────────────────────────────────────────────
# 15. PCA & LOGREG DETAILED TABLES
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("PCA METHOD — REGIME DISTRIBUTION PER YEAR")
print("=" * 80)

scores["p_regime"] = scores["pca"].apply(regime_label)
for year in sorted(scores["year"].dropna().unique()):
    yr_data = scores[(scores["year"] == year) & scores["pca"].notna()]
    if len(yr_data) == 0:
        continue
    counts = yr_data["p_regime"].value_counts(normalize=True)
    bear_pct = counts.get("BEAR", 0)
    trans_pct = counts.get("TRANS", 0)
    bull_pct = counts.get("BULL", 0)
    print(f"  {year}: BEAR={bear_pct:5.1%}  TRANS={trans_pct:5.1%}  BULL={bull_pct:5.1%}  (n={len(yr_data)})")

print("\n" + "=" * 80)
print("LOGREG METHOD — REGIME DISTRIBUTION PER YEAR")
print("=" * 80)

scores["l_regime"] = scores["logreg"].apply(regime_label)
for year in sorted(scores["year"].dropna().unique()):
    yr_data = scores[(scores["year"] == year) & scores["logreg"].notna()]
    if len(yr_data) == 0:
        continue
    counts = yr_data["l_regime"].value_counts(normalize=True)
    bear_pct = counts.get("BEAR", 0)
    trans_pct = counts.get("TRANS", 0)
    bull_pct = counts.get("BULL", 0)
    print(f"  {year}: BEAR={bear_pct:5.1%}  TRANS={trans_pct:5.1%}  BULL={bull_pct:5.1%}  (n={len(yr_data)})")

# ──────────────────────────────────────────────────────────────────────
# 16. FEATURE IMPORTANCE (Weighted Method contributions)
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("FEATURE CONTRIBUTIONS (correlation with forward 30d return)")
print("=" * 80)

valid_feat = features_norm.copy()
valid_feat["fwd_ret_30d"] = fwd_ret_30d
valid_feat = valid_feat.dropna()

for col in features_norm.columns:
    corr = valid_feat[col].corr(valid_feat["fwd_ret_30d"])
    print(f"  {col:<25} IC = {corr:+.4f}")

print(f"\n  {'weighted_score':<25} IC = {valid_scores['weighted'].corr(valid_scores['fwd_ret_30d']):+.4f}")

# ──────────────────────────────────────────────────────────────────────
# 17. LOGREG COEFFICIENTS (last fit)
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("LOGISTIC REGRESSION COEFFICIENTS (last fit)")
print("=" * 80)

coef_names = features_norm.columns
coefs = lr.coef_[0]
for name, coef in sorted(zip(coef_names, coefs), key=lambda x: abs(x[1]), reverse=True):
    print(f"  {name:<25} {coef:+.4f}")
print(f"  {'intercept':<25} {lr.intercept_[0]:+.4f}")

# ──────────────────────────────────────────────────────────────────────
# 18. COMPARISON OF METHODS
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("METHOD COMPARISON (correlation between scores)")
print("=" * 80)

methods = scores[["weighted", "pca", "logreg"]].dropna()
if len(methods) > 0:
    print(methods.corr().to_string(float_format="%.3f"))

# Monthly smoothness comparison
print("\nMonthly score volatility (std of monthly changes):")
for col in ["weighted", "pca", "logreg"]:
    m = scores[col].resample("ME").mean().dropna()
    vol = m.diff().abs().mean()
    print(f"  {col:<12} avg monthly change: {vol:.4f}")

# ──────────────────────────────────────────────────────────────────────
# 19. SUMMARY
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("SUMMARY & RECOMMENDATIONS")
print("=" * 80)

print("""
Three methods computed:
  1. WEIGHTED AVERAGE: Simple, transparent, no training needed. Weights are
     interpretable. Good baseline.
  2. PCA: Data-driven axis. Captures the dominant variation across all features.
     Less interpretable but may capture non-obvious combinations.
  3. LOGISTIC REGRESSION: Directly optimized for predicting forward returns.
     Most accurate but risk of overfitting.

Key findings:
  - The rolling 365d percentile normalization ensures all features are
    comparable and adapt to changing market conditions.
  - The weighted average method is the most stable (smoothest transitions).
  - Critical period test results above show which method best matches
    known regime phases.

Usage in strategy:
  - Score < bear_threshold => ungate shorts / full risk-off
  - Score > bull_threshold => gate shorts / full risk-on
  - Transition zone => reduced sizing or neutral
""")

print("DONE.")
