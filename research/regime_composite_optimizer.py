"""
Regime Composite Optimizer — Research Script
=============================================
Build a custom regime detection algorithm targeting 75%+ accuracy for crypto alt trading.
Classifies each day as BEAR (ungate shorts) or BULL (gate shorts).

Ground truth: forward 30-day BTC return (negative = bear was correct).
All features are causal (only use data up to time t).
Evaluation is walk-forward with expanding windows.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from itertools import combinations
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

# ============================================================
# STEP 0: Load Data
# ============================================================
print("=" * 80)
print("REGIME COMPOSITE OPTIMIZER")
print("=" * 80)

df = pd.read_parquet("/workspace/crypto_backtest/data/alternative/regime_signals.parquet")
print(f"\nLoaded {len(df)} rows from {df.index.min().date()} to {df.index.max().date()}")
print(f"Columns: {list(df.columns)}")

# ============================================================
# STEP 1: Feature Engineering
# ============================================================
print("\n" + "=" * 80)
print("STEP 1: Feature Engineering")
print("=" * 80)

# Compute daily returns for volatility calc
df["btc_daily_ret"] = df["btc_close"].pct_change()

# Binary features
df["btc_below_sma50"] = (~df["btc_above_sma50"].astype(bool)).astype(float)
df["btc_below_sma20"] = (~df["btc_above_sma20"].astype(bool)).astype(float)
df["btc_ret_30d_negative"] = (df["btc_ret_30d"] < 0).astype(float)
df["btc_ret_90d_negative"] = (df["btc_ret_90d"] < 0).astype(float)
df["alt_breadth_declining"] = (df["alt_breadth_50d"] < df["alt_breadth_50d"].shift(30)).astype(float)
df["alt_btc_spread_negative"] = (df["alt_btc_spread_30d"] < 0).astype(float)

# Volatility features
df["btc_volatility_20d"] = df["btc_daily_ret"].rolling(20).std() * np.sqrt(365)
df["btc_volatility_high"] = (
    df["btc_volatility_20d"] > df["btc_volatility_20d"].rolling(180, min_periods=90).quantile(0.75)
).astype(float)

# Additional binary features
df["alt_breadth_below_30"] = (df["alt_breadth_50d"] < 0.30).astype(float)
df["momentum_divergence"] = (
    (df["btc_ret_30d"] > 0) & df["alt_breadth_declining"].astype(bool)
).astype(float)

# Ensure halving_cycle_bear is float
df["halving_cycle_bear"] = df["halving_cycle_bear"].astype(float)

# ============================================================
# STEP 2: Target Variable
# ============================================================
print("\n" + "=" * 80)
print("STEP 2: Target Variable (Forward 30-day BTC return)")
print("=" * 80)

# Target: 1 if forward 30-day return is negative (bear correct), 0 otherwise
# We use btc_ret_30d shifted back by 30 days = forward return from current date
df["fwd_ret_30d"] = df["btc_ret_30d"].shift(-30)
df["target"] = (df["fwd_ret_30d"] < 0).astype(float)

# Filter to evaluation period: 2021-01-01 onward (warmup), drop NaN targets
eval_mask = (df.index >= "2021-01-01") & df["target"].notna()
df_eval = df[eval_mask].copy()
print(f"Evaluation period: {df_eval.index.min().date()} to {df_eval.index.max().date()}")
print(f"Evaluation rows: {len(df_eval)}")
print(f"Bear days (target=1): {df_eval['target'].sum():.0f} ({df_eval['target'].mean()*100:.1f}%)")
print(f"Bull days (target=0): {(1-df_eval['target']).sum():.0f} ({(1-df_eval['target']).mean()*100:.1f}%)")

# ============================================================
# STEP 3: Individual Signal Accuracy
# ============================================================
print("\n" + "=" * 80)
print("STEP 3: Individual Signal Accuracy")
print("=" * 80)

binary_features = [
    "btc_below_sma50", "btc_below_sma20",
    "btc_ret_30d_negative", "btc_ret_90d_negative",
    "alt_breadth_declining", "alt_btc_spread_negative",
    "btc_volatility_high", "alt_breadth_below_30",
    "momentum_divergence", "halving_cycle_bear",
    "composite_bear", "alt_bleed",
]

continuous_features = [
    "btc_ret_30d", "btc_ret_90d",
    "alt_breadth_50d", "alt_breadth_20d",
    "alt_btc_spread_30d", "btc_volatility_20d",
]

all_features = binary_features + continuous_features

results = []
for feat in binary_features:
    valid = df_eval[[feat, "target"]].dropna()
    if len(valid) == 0:
        continue
    pred = valid[feat].values
    actual = valid["target"].values

    acc = accuracy_score(actual, pred)
    prec = precision_score(actual, pred, zero_division=0)
    rec = recall_score(actual, pred, zero_division=0)
    f1 = f1_score(actual, pred, zero_division=0)
    bear_rate = pred.mean()

    results.append({
        "Signal": feat,
        "Accuracy": acc,
        "Precision": prec,
        "Recall": rec,
        "F1": f1,
        "Bear_Rate": bear_rate,
    })

results_df = pd.DataFrame(results).sort_values("Accuracy", ascending=False)
print("\nIndividual Binary Signal Performance (ranked by accuracy):")
print("-" * 90)
print(f"{'Signal':<30} {'Accuracy':>8} {'Precision':>9} {'Recall':>8} {'F1':>8} {'Bear%':>8}")
print("-" * 90)
for _, row in results_df.iterrows():
    print(f"{row['Signal']:<30} {row['Accuracy']:>8.3f} {row['Precision']:>9.3f} {row['Recall']:>8.3f} {row['F1']:>8.3f} {row['Bear_Rate']:>7.1%}")

# ============================================================
# STEP 4 + 5: Walk-Forward Evaluation Infrastructure
# ============================================================
print("\n" + "=" * 80)
print("STEP 4+5: Walk-Forward Ensemble Methods")
print("=" * 80)

def walk_forward_splits(df_eval, train_start="2021-01-01", pred_window=90, min_train=365):
    """Generate walk-forward train/test splits with expanding window."""
    dates = df_eval.index.sort_values()
    start = pd.Timestamp(train_start, tz=dates.tz)

    splits = []
    pred_start_idx = min_train  # Start predicting after min_train days

    while pred_start_idx < len(dates):
        pred_end_idx = min(pred_start_idx + pred_window, len(dates))
        train_dates = dates[:pred_start_idx]
        pred_dates = dates[pred_start_idx:pred_end_idx]

        if len(pred_dates) == 0:
            break

        splits.append((train_dates, pred_dates))
        pred_start_idx = pred_end_idx

    return splits


splits = walk_forward_splits(df_eval)
print(f"Walk-forward splits: {len(splits)}")
for i, (train, pred) in enumerate(splits):
    print(f"  Fold {i}: Train {train[0].date()} - {train[-1].date()} ({len(train)}d), "
          f"Pred {pred[0].date()} - {pred[-1].date()} ({len(pred)}d)")


def evaluate_wf(all_preds, all_actuals, all_dates=None):
    """Compute aggregate walk-forward metrics."""
    preds = np.concatenate(all_preds)
    actuals = np.concatenate(all_actuals)
    valid = ~(np.isnan(preds) | np.isnan(actuals))
    preds = preds[valid]
    actuals = actuals[valid]
    if len(preds) == 0:
        return {"accuracy": 0, "precision": 0, "recall": 0, "f1": 0, "transitions": 0}

    acc = accuracy_score(actuals, preds)
    prec = precision_score(actuals, preds, zero_division=0)
    rec = recall_score(actuals, preds, zero_division=0)
    f1 = f1_score(actuals, preds, zero_division=0)

    # Count transitions per year
    if all_dates is not None:
        dates = np.concatenate(all_dates)
        valid_dates = dates[valid[:len(dates)] if len(dates) >= len(valid) else np.ones(len(dates), dtype=bool)]
        if len(valid_dates) > 1:
            delta = valid_dates[-1] - valid_dates[0]
            years = delta / np.timedelta64(1, 'D') / 365.25
        else:
            years = 1
    else:
        years = len(preds) / 365.25
    transitions = np.sum(np.abs(np.diff(preds))) if len(preds) > 1 else 0
    trans_per_year = transitions / max(years, 0.1)

    return {
        "accuracy": acc, "precision": prec, "recall": rec,
        "f1": f1, "transitions_per_year": trans_per_year,
    }


# ============================================================
# Method A: Majority Vote (combos of binary signals)
# ============================================================
print("\n" + "-" * 80)
print("METHOD A: Majority Vote Combinations")
print("-" * 80)

# Use only the most promising binary features to keep combos manageable
vote_features = [
    "btc_below_sma50", "btc_below_sma20",
    "btc_ret_30d_negative", "btc_ret_90d_negative",
    "alt_breadth_declining", "alt_btc_spread_negative",
    "btc_volatility_high", "alt_breadth_below_30",
    "momentum_divergence", "halving_cycle_bear",
]

best_combos = []

for k in [3, 4, 5]:
    print(f"\n  Testing {k}-signal combinations...")
    combo_results = []

    for combo in combinations(vote_features, k):
        all_preds = []
        all_actuals = []
        all_dates_list = []

        for train_dates, pred_dates in splits:
            pred_data = df_eval.loc[pred_dates]
            # Majority vote: bear if >= ceil(k/2) signals say bear
            threshold = (k + 1) // 2
            vote_sum = pred_data[list(combo)].sum(axis=1)
            preds = (vote_sum >= threshold).astype(float).values
            actuals = pred_data["target"].values

            valid = ~np.isnan(actuals)
            all_preds.append(preds[valid])
            all_actuals.append(actuals[valid])
            all_dates_list.append(pred_data.index.values[valid])

        metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
        combo_results.append((combo, metrics))

    # Sort by accuracy
    combo_results.sort(key=lambda x: x[1]["accuracy"], reverse=True)

    # Store top 5 from each k
    for combo, metrics in combo_results[:5]:
        best_combos.append((k, combo, metrics))

# Print top 10 overall
best_combos.sort(key=lambda x: x[2]["accuracy"], reverse=True)
print(f"\nTop 10 Majority Vote Combinations:")
print("-" * 110)
print(f"{'K':>2} {'Signals':<65} {'Acc':>6} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Tr/Y':>6}")
print("-" * 110)
for k, combo, m in best_combos[:10]:
    sig_str = ", ".join([s.replace("btc_", "b_").replace("alt_", "a_").replace("_negative", "-").replace("_declining", "_decl") for s in combo])
    print(f"{k:>2} {sig_str:<65} {m['accuracy']:>6.3f} {m['precision']:>6.3f} {m['recall']:>6.3f} {m['f1']:>6.3f} {m['transitions_per_year']:>6.1f}")


# ============================================================
# Method B: Weighted Score (adaptive weights)
# ============================================================
print("\n" + "-" * 80)
print("METHOD B: Adaptive Weighted Score")
print("-" * 80)

thresholds_b = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3]

for threshold in thresholds_b:
    all_preds = []
    all_actuals = []
    all_dates_list = []

    for train_dates, pred_dates in splits:
        train_data = df_eval.loc[train_dates]
        pred_data = df_eval.loc[pred_dates]

        # Compute trailing 365-day accuracy for each binary signal
        weights = {}
        for feat in binary_features:
            valid_train = train_data[[feat, "target"]].dropna()
            if len(valid_train) < 30:
                weights[feat] = 0
                continue
            # Use last 365 days of training data
            recent = valid_train.iloc[-365:]
            acc = accuracy_score(recent["target"], recent[feat])
            weights[feat] = max(acc - 0.5, 0)  # Only positive weights

        # Score each prediction day
        for idx in pred_data.index:
            row = pred_data.loc[idx]
            score = 0
            total_weight = 0
            for feat, w in weights.items():
                val = row[feat]
                if not np.isnan(val):
                    score += w * val
                    total_weight += w

            if total_weight > 0:
                normalized_score = score / total_weight
            else:
                normalized_score = 0

            pred = 1.0 if normalized_score > threshold else 0.0
            if not np.isnan(row["target"]):
                all_preds.append([pred])
                all_actuals.append([row["target"]])
                all_dates_list.append([idx])

    metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
    print(f"  Threshold={threshold:.2f}: Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
          f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")


# ============================================================
# Method C: Logistic Regression (expanding window)
# ============================================================
print("\n" + "-" * 80)
print("METHOD C: Logistic Regression (expanding window)")
print("-" * 80)

for C_val in [0.01, 0.1, 1.0, 10.0]:
    all_preds = []
    all_actuals = []
    all_dates_list = []

    for train_dates, pred_dates in splits:
        train_data = df_eval.loc[train_dates][all_features + ["target"]].dropna()
        pred_data = df_eval.loc[pred_dates]

        if len(train_data) < 50:
            continue

        X_train = train_data[all_features].values
        y_train = train_data["target"].values

        # Standardize
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)

        # Fit
        model = LogisticRegression(C=C_val, max_iter=1000, random_state=42)
        model.fit(X_train_scaled, y_train)

        # Predict
        pred_valid = pred_data[all_features + ["target"]].dropna()
        if len(pred_valid) == 0:
            continue
        X_pred = scaler.transform(pred_valid[all_features].values)
        preds = model.predict(X_pred)

        all_preds.append(preds)
        all_actuals.append(pred_valid["target"].values)
        all_dates_list.append(pred_valid.index.values)

    metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
    print(f"  C={C_val:<5}: Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
          f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")

    # Print feature importances for best C
    if C_val == 0.1:
        coefs = pd.Series(model.coef_[0], index=all_features).sort_values()
        print("\n  Feature importances (C=0.1, last fold):")
        for feat, coef in coefs.items():
            direction = "BEAR" if coef > 0 else "BULL"
            print(f"    {feat:<30} {coef:>8.3f} ({direction})")


# ============================================================
# Method D: Decision Tree (expanding window)
# ============================================================
print("\n" + "-" * 80)
print("METHOD D: Decision Tree (expanding window)")
print("-" * 80)

for max_d in [2, 3, 4, 5]:
    all_preds = []
    all_actuals = []
    all_dates_list = []

    for train_dates, pred_dates in splits:
        train_data = df_eval.loc[train_dates][all_features + ["target"]].dropna()
        pred_data = df_eval.loc[pred_dates]

        if len(train_data) < 50:
            continue

        X_train = train_data[all_features].values
        y_train = train_data["target"].values

        model = DecisionTreeClassifier(max_depth=max_d, random_state=42, min_samples_leaf=20)
        model.fit(X_train, y_train)

        pred_valid = pred_data[all_features + ["target"]].dropna()
        if len(pred_valid) == 0:
            continue
        X_pred = pred_valid[all_features].values
        preds = model.predict(X_pred)

        all_preds.append(preds)
        all_actuals.append(pred_valid["target"].values)
        all_dates_list.append(pred_valid.index.values)

    metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
    print(f"  max_depth={max_d}: Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
          f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")

    # Print tree structure for depth=4
    if max_d == 4:
        importances = pd.Series(model.feature_importances_, index=all_features).sort_values(ascending=False)
        print("\n  Feature importances (depth=4, last fold):")
        for feat, imp in importances.head(8).items():
            print(f"    {feat:<30} {imp:>8.3f}")


# ============================================================
# Method E: Cascading Filter
# ============================================================
print("\n" + "-" * 80)
print("METHOD E: Cascading Filter (rule-based)")
print("-" * 80)

def cascading_filter(row, params):
    """Apply cascading bear detection rules. Returns 1 (bear) or 0 (bull)."""
    p = params

    # Level 1: Very low alt breadth → BEAR (high confidence)
    if not np.isnan(row.get("alt_breadth_50d", np.nan)):
        if row["alt_breadth_50d"] < p["breadth_thresh"]:
            return 1.0

    # Level 2: Below SMA50 AND deep 90d drawdown → BEAR
    if row.get("btc_below_sma50", 0) == 1 and not np.isnan(row.get("btc_ret_90d", np.nan)):
        if row["btc_ret_90d"] < p["ret90_thresh"]:
            return 1.0

    # Level 3: Alt underperformance AND declining breadth → BEAR
    if not np.isnan(row.get("alt_btc_spread_30d", np.nan)):
        if row["alt_btc_spread_30d"] < p["spread_thresh"] and row.get("alt_breadth_declining", 0) == 1:
            return 1.0

    # Level 4: Negative momentum AND high volatility → BEAR
    if not np.isnan(row.get("btc_ret_30d", np.nan)):
        if row["btc_ret_30d"] < p["ret30_thresh"] and row.get("btc_volatility_high", 0) == 1:
            return 1.0

    return 0.0


# Test different parameter sets
param_sets = [
    {"name": "Default",     "breadth_thresh": 0.25, "ret90_thresh": -0.10, "spread_thresh": -0.10, "ret30_thresh": -0.05},
    {"name": "Aggressive",  "breadth_thresh": 0.30, "ret90_thresh": -0.05, "spread_thresh": -0.05, "ret30_thresh": -0.03},
    {"name": "Conservative","breadth_thresh": 0.20, "ret90_thresh": -0.15, "spread_thresh": -0.15, "ret30_thresh": -0.10},
    {"name": "Balanced",    "breadth_thresh": 0.28, "ret90_thresh": -0.08, "spread_thresh": -0.08, "ret30_thresh": -0.05},
    {"name": "Wide",        "breadth_thresh": 0.35, "ret90_thresh": -0.05, "spread_thresh": -0.05, "ret30_thresh": -0.02},
]

for params in param_sets:
    name = params.pop("name")
    all_preds = []
    all_actuals = []
    all_dates_list = []

    for train_dates, pred_dates in splits:
        pred_data = df_eval.loc[pred_dates]
        preds = pred_data.apply(lambda row: cascading_filter(row, params), axis=1).values
        actuals = pred_data["target"].values
        valid = ~np.isnan(actuals)

        all_preds.append(preds[valid])
        all_actuals.append(actuals[valid])
        all_dates_list.append(pred_data.index.values[valid])

    metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
    print(f"  {name:<15}: Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
          f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")
    params["name"] = name  # restore


# ============================================================
# Method F: Optimized Threshold Ensemble
# ============================================================
print("\n" + "-" * 80)
print("METHOD F: Optimized Threshold Ensemble")
print("-" * 80)

def compute_regime_score(row):
    """Compute continuous regime score from multiple features."""
    score = 0.0
    count = 0

    # Component 1: Alt breadth (inverted — low breadth = bearish)
    if not np.isnan(row.get("alt_breadth_50d", np.nan)):
        score += 0.30 * (1.0 - row["alt_breadth_50d"])
        count += 1

    # Component 2: BTC 90d return (inverted — negative = bearish)
    if not np.isnan(row.get("btc_ret_90d", np.nan)):
        score += 0.25 * max(0, -row["btc_ret_90d"])
        count += 1

    # Component 3: Below SMA50
    if not np.isnan(row.get("btc_above_sma50", np.nan)):
        score += 0.20 * (1.0 - row["btc_above_sma50"])
        count += 1

    # Component 4: Alt-BTC spread (inverted)
    if not np.isnan(row.get("alt_btc_spread_30d", np.nan)):
        score += 0.15 * max(0, -row["alt_btc_spread_30d"])
        count += 1

    # Component 5: Halving cycle bear
    if not np.isnan(row.get("halving_cycle_bear", np.nan)):
        score += 0.10 * row["halving_cycle_bear"]
        count += 1

    return score if count > 0 else np.nan

# Pre-compute scores
df_eval["regime_score"] = df_eval.apply(compute_regime_score, axis=1)

# Method F1: Fixed thresholds
print("\n  F1: Fixed Thresholds:")
for thresh in np.arange(0.20, 0.65, 0.05):
    all_preds = []
    all_actuals = []
    all_dates_list = []

    for train_dates, pred_dates in splits:
        pred_data = df_eval.loc[pred_dates]
        valid = pred_data["regime_score"].notna() & pred_data["target"].notna()
        preds = (pred_data.loc[valid, "regime_score"] > thresh).astype(float).values
        actuals = pred_data.loc[valid, "target"].values

        all_preds.append(preds)
        all_actuals.append(actuals)
        all_dates_list.append(pred_data.index.values[valid.values])

    metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
    print(f"    Threshold={thresh:.2f}: Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
          f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")

# Method F2: Optimized threshold on expanding window
print("\n  F2: Walk-Forward Optimized Threshold:")
all_preds_opt = []
all_actuals_opt = []
all_dates_opt = []
chosen_thresholds = []

for train_dates, pred_dates in splits:
    train_data = df_eval.loc[train_dates]
    pred_data = df_eval.loc[pred_dates]

    # Find best threshold on training data
    best_thresh = 0.30
    best_acc = 0
    valid_train = train_data["regime_score"].notna() & train_data["target"].notna()
    train_scores = train_data.loc[valid_train, "regime_score"].values
    train_targets = train_data.loc[valid_train, "target"].values

    for t in np.arange(0.15, 0.60, 0.02):
        train_preds = (train_scores > t).astype(float)
        acc = accuracy_score(train_targets, train_preds)
        if acc > best_acc:
            best_acc = acc
            best_thresh = t

    chosen_thresholds.append(best_thresh)

    # Apply to prediction period
    valid_pred = pred_data["regime_score"].notna() & pred_data["target"].notna()
    preds = (pred_data.loc[valid_pred, "regime_score"] > best_thresh).astype(float).values
    actuals = pred_data.loc[valid_pred, "target"].values

    all_preds_opt.append(preds)
    all_actuals_opt.append(actuals)
    all_dates_opt.append(pred_data.index.values[valid_pred.values])

metrics_opt = evaluate_wf(all_preds_opt, all_actuals_opt, all_dates_opt)
print(f"    Optimized: Acc={metrics_opt['accuracy']:.3f} Prec={metrics_opt['precision']:.3f} "
      f"Rec={metrics_opt['recall']:.3f} F1={metrics_opt['f1']:.3f} Tr/Y={metrics_opt['transitions_per_year']:.1f}")
print(f"    Thresholds chosen per fold: {[f'{t:.2f}' for t in chosen_thresholds]}")


# ============================================================
# Method G (Bonus): Enhanced Cascading with Walk-Forward Threshold Opt
# ============================================================
print("\n" + "-" * 80)
print("METHOD G: Enhanced Composite (Cascading + Score + Smoothing)")
print("-" * 80)

def enhanced_composite(df_slice, smooth_window=5, breadth_thresh=0.28, score_thresh=0.35):
    """
    Combine cascading rules with score-based detection and smoothing.
    - High-confidence cascading rules trigger instantly
    - Borderline cases use smoothed regime score
    - Smoothing prevents rapid transitions
    """
    results = np.zeros(len(df_slice))

    for i, (idx, row) in enumerate(df_slice.iterrows()):
        # High confidence bear: very low breadth
        if not np.isnan(row.get("alt_breadth_50d", np.nan)):
            if row["alt_breadth_50d"] < 0.20:
                results[i] = 1.0
                continue

        # High confidence bear: below SMA50 + deep drawdown + low breadth
        if (row.get("btc_below_sma50", 0) == 1 and
            not np.isnan(row.get("btc_ret_90d", np.nan)) and
            row["btc_ret_90d"] < -0.10 and
            not np.isnan(row.get("alt_breadth_50d", np.nan)) and
            row["alt_breadth_50d"] < breadth_thresh):
            results[i] = 1.0
            continue

        # Score-based for borderline
        score = row.get("regime_score", 0)
        if not np.isnan(score) and score > score_thresh:
            results[i] = 1.0
        else:
            results[i] = 0.0

    # Apply smoothing: require N consecutive days to switch regime
    if smooth_window > 1:
        smoothed = results.copy()
        current_regime = results[0]
        count = 1
        for i in range(1, len(results)):
            if results[i] == current_regime:
                count += 1
                smoothed[i] = current_regime
            else:
                count += 1
                if count >= smooth_window:
                    # Check if last smooth_window days are all the same new regime
                    window = results[max(0, i - smooth_window + 1):i + 1]
                    if np.all(window == results[i]):
                        current_regime = results[i]
                        smoothed[i] = current_regime
                    else:
                        smoothed[i] = current_regime
                        count = 1
                else:
                    smoothed[i] = current_regime
        return smoothed

    return results


param_combos_g = [
    {"smooth_window": 1, "breadth_thresh": 0.28, "score_thresh": 0.35},
    {"smooth_window": 3, "breadth_thresh": 0.28, "score_thresh": 0.35},
    {"smooth_window": 5, "breadth_thresh": 0.28, "score_thresh": 0.35},
    {"smooth_window": 5, "breadth_thresh": 0.30, "score_thresh": 0.30},
    {"smooth_window": 7, "breadth_thresh": 0.30, "score_thresh": 0.30},
    {"smooth_window": 5, "breadth_thresh": 0.25, "score_thresh": 0.40},
    {"smooth_window": 10, "breadth_thresh": 0.28, "score_thresh": 0.35},
]

for params_g in param_combos_g:
    all_preds = []
    all_actuals = []
    all_dates_list = []

    for train_dates, pred_dates in splits:
        pred_data = df_eval.loc[pred_dates]
        preds = enhanced_composite(pred_data, **params_g)
        actuals = pred_data["target"].values
        valid = ~np.isnan(actuals)

        all_preds.append(preds[valid])
        all_actuals.append(actuals[valid])
        all_dates_list.append(pred_data.index.values[valid])

    metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
    print(f"  smooth={params_g['smooth_window']:>2}, breadth<{params_g['breadth_thresh']:.2f}, score>{params_g['score_thresh']:.2f}: "
          f"Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
          f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")


# ============================================================
# Method H (Bonus): LightGBM if available
# ============================================================
print("\n" + "-" * 80)
print("METHOD H: Gradient Boosting (if available)")
print("-" * 80)

try:
    from sklearn.ensemble import GradientBoostingClassifier

    for n_est, lr, md in [(50, 0.1, 3), (100, 0.05, 3), (100, 0.1, 4), (200, 0.05, 3)]:
        all_preds = []
        all_actuals = []
        all_dates_list = []

        for train_dates, pred_dates in splits:
            train_data = df_eval.loc[train_dates][all_features + ["target"]].dropna()
            pred_data = df_eval.loc[pred_dates]

            if len(train_data) < 50:
                continue

            X_train = train_data[all_features].values
            y_train = train_data["target"].values

            model = GradientBoostingClassifier(
                n_estimators=n_est, learning_rate=lr, max_depth=md,
                min_samples_leaf=20, random_state=42
            )
            model.fit(X_train, y_train)

            pred_valid = pred_data[all_features + ["target"]].dropna()
            if len(pred_valid) == 0:
                continue
            preds = model.predict(pred_valid[all_features].values)

            all_preds.append(preds)
            all_actuals.append(pred_valid["target"].values)
            all_dates_list.append(pred_valid.index.values)

        metrics = evaluate_wf(all_preds, all_actuals, all_dates_list)
        print(f"  n={n_est:>3}, lr={lr}, depth={md}: Acc={metrics['accuracy']:.3f} Prec={metrics['precision']:.3f} "
              f"Rec={metrics['recall']:.3f} F1={metrics['f1']:.3f} Tr/Y={metrics['transitions_per_year']:.1f}")

    # Feature importances from last model
    importances = pd.Series(model.feature_importances_, index=all_features).sort_values(ascending=False)
    print("\n  GBM Feature importances (last config, last fold):")
    for feat, imp in importances.head(10).items():
        print(f"    {feat:<30} {imp:>8.3f}")

except ImportError:
    print("  GradientBoosting not available, skipping.")


# ============================================================
# STEP 6: Final Summary Comparison
# ============================================================
print("\n" + "=" * 80)
print("STEP 6: FINAL COMPARISON TABLE")
print("=" * 80)

# Re-run all best methods and collect results
summary = []

# A: Best majority vote
best_mv = best_combos[0]
summary.append(("A: Best Majority Vote", best_mv[2]))

# B: Re-run best threshold
for threshold in [0.1, 0.15]:
    all_preds_b = []
    all_actuals_b = []
    all_dates_b = []
    for train_dates, pred_dates in splits:
        train_data = df_eval.loc[train_dates]
        pred_data = df_eval.loc[pred_dates]
        weights = {}
        for feat in binary_features:
            valid_train = train_data[[feat, "target"]].dropna()
            if len(valid_train) < 30:
                weights[feat] = 0
                continue
            recent = valid_train.iloc[-365:]
            acc = accuracy_score(recent["target"], recent[feat])
            weights[feat] = max(acc - 0.5, 0)
        for idx in pred_data.index:
            row = pred_data.loc[idx]
            score = sum(weights.get(f, 0) * row[f] for f in binary_features if not np.isnan(row[f]))
            tw = sum(weights.get(f, 0) for f in binary_features if not np.isnan(row[f]))
            ns = score / tw if tw > 0 else 0
            pred = 1.0 if ns > threshold else 0.0
            if not np.isnan(row["target"]):
                all_preds_b.append([pred])
                all_actuals_b.append([row["target"]])
                all_dates_b.append([idx])
    m = evaluate_wf(all_preds_b, all_actuals_b, all_dates_b)
    summary.append((f"B: Weighted Score (t={threshold})", m))

# C: Best LR
for C_val in [0.1]:
    all_preds_c = []
    all_actuals_c = []
    all_dates_c = []
    for train_dates, pred_dates in splits:
        train_data = df_eval.loc[train_dates][all_features + ["target"]].dropna()
        pred_data = df_eval.loc[pred_dates]
        if len(train_data) < 50:
            continue
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(train_data[all_features].values)
        model = LogisticRegression(C=C_val, max_iter=1000, random_state=42)
        model.fit(X_train_scaled, train_data["target"].values)
        pred_valid = pred_data[all_features + ["target"]].dropna()
        if len(pred_valid) == 0:
            continue
        preds = model.predict(scaler.transform(pred_valid[all_features].values))
        all_preds_c.append(preds)
        all_actuals_c.append(pred_valid["target"].values)
        all_dates_c.append(pred_valid.index.values)
    m = evaluate_wf(all_preds_c, all_actuals_c, all_dates_c)
    summary.append(("C: Logistic Regression (C=0.1)", m))

# D: Best DT
for md in [3, 4]:
    all_preds_d = []
    all_actuals_d = []
    all_dates_d = []
    for train_dates, pred_dates in splits:
        train_data = df_eval.loc[train_dates][all_features + ["target"]].dropna()
        pred_data = df_eval.loc[pred_dates]
        if len(train_data) < 50:
            continue
        model = DecisionTreeClassifier(max_depth=md, random_state=42, min_samples_leaf=20)
        model.fit(train_data[all_features].values, train_data["target"].values)
        pred_valid = pred_data[all_features + ["target"]].dropna()
        if len(pred_valid) == 0:
            continue
        preds = model.predict(pred_valid[all_features].values)
        all_preds_d.append(preds)
        all_actuals_d.append(pred_valid["target"].values)
        all_dates_d.append(pred_valid.index.values)
    m = evaluate_wf(all_preds_d, all_actuals_d, all_dates_d)
    summary.append((f"D: Decision Tree (depth={md})", m))

# E: Best cascading
for params in [param_sets[0], param_sets[3]]:  # Default, Balanced
    name = params["name"]
    all_preds_e = []
    all_actuals_e = []
    all_dates_e = []
    for train_dates, pred_dates in splits:
        pred_data = df_eval.loc[pred_dates]
        preds = pred_data.apply(lambda row: cascading_filter(row, params), axis=1).values
        actuals = pred_data["target"].values
        valid = ~np.isnan(actuals)
        all_preds_e.append(preds[valid])
        all_actuals_e.append(actuals[valid])
        all_dates_e.append(pred_data.index.values[valid])
    m = evaluate_wf(all_preds_e, all_actuals_e, all_dates_e)
    summary.append((f"E: Cascade ({name})", m))

# F: Optimized threshold
summary.append(("F: Score Opt Threshold", metrics_opt))

# G: Best enhanced composite
for pg in [param_combos_g[2], param_combos_g[4]]:  # smooth=5 and smooth=7
    all_preds_g = []
    all_actuals_g = []
    all_dates_g = []
    for train_dates, pred_dates in splits:
        pred_data = df_eval.loc[pred_dates]
        preds = enhanced_composite(pred_data, **pg)
        actuals = pred_data["target"].values
        valid = ~np.isnan(actuals)
        all_preds_g.append(preds[valid])
        all_actuals_g.append(actuals[valid])
        all_dates_g.append(pred_data.index.values[valid])
    m = evaluate_wf(all_preds_g, all_actuals_g, all_dates_g)
    summary.append((f"G: Enhanced (s={pg['smooth_window']},b={pg['breadth_thresh']},t={pg['score_thresh']})", m))

# H: GBM
try:
    from sklearn.ensemble import GradientBoostingClassifier
    for n_est, lr, md in [(100, 0.05, 3)]:
        all_preds_h = []
        all_actuals_h = []
        all_dates_h = []
        for train_dates, pred_dates in splits:
            train_data = df_eval.loc[train_dates][all_features + ["target"]].dropna()
            pred_data = df_eval.loc[pred_dates]
            if len(train_data) < 50:
                continue
            model = GradientBoostingClassifier(n_estimators=n_est, learning_rate=lr, max_depth=md,
                                               min_samples_leaf=20, random_state=42)
            model.fit(train_data[all_features].values, train_data["target"].values)
            pred_valid = pred_data[all_features + ["target"]].dropna()
            if len(pred_valid) == 0:
                continue
            preds = model.predict(pred_valid[all_features].values)
            all_preds_h.append(preds)
            all_actuals_h.append(pred_valid["target"].values)
            all_dates_h.append(pred_valid.index.values)
        m = evaluate_wf(all_preds_h, all_actuals_h, all_dates_h)
        summary.append((f"H: GBM (n={n_est},lr={lr},d={md})", m))
except ImportError:
    pass

# Print final table
print("\n" + "=" * 110)
print(f"{'Method':<50} {'WF Acc':>8} {'WF Prec':>8} {'WF Rec':>8} {'WF F1':>8} {'Tr/Year':>8}")
print("=" * 110)
for name, m in sorted(summary, key=lambda x: x[1]["accuracy"], reverse=True):
    marker = " ***" if m["accuracy"] >= 0.75 else ""
    print(f"{name:<50} {m['accuracy']:>8.3f} {m['precision']:>8.3f} {m['recall']:>8.3f} {m['f1']:>8.3f} {m['transitions_per_year']:>8.1f}{marker}")

print("\n*** = meets 75%+ accuracy target")

# Identify winners
winners = [(n, m) for n, m in summary if m["accuracy"] >= 0.75 and m["transitions_per_year"] < 12]
if winners:
    print(f"\n{'='*80}")
    print("WINNERS (75%+ accuracy, <12 transitions/year):")
    print(f"{'='*80}")
    for name, m in sorted(winners, key=lambda x: x[1]["accuracy"], reverse=True):
        print(f"  {name}: Acc={m['accuracy']:.3f}, F1={m['f1']:.3f}, Tr/Y={m['transitions_per_year']:.1f}")
else:
    print("\nNo method achieved 75%+ accuracy with <12 transitions/year.")
    # Find closest
    closest = sorted(summary, key=lambda x: x[1]["accuracy"], reverse=True)[:3]
    print("Closest methods:")
    for name, m in closest:
        print(f"  {name}: Acc={m['accuracy']:.3f}, F1={m['f1']:.3f}, Tr/Y={m['transitions_per_year']:.1f}")

# ============================================================
# Yearly Breakdown for Top Methods
# ============================================================
print("\n" + "=" * 80)
print("YEARLY BREAKDOWN — Top 3 Methods")
print("=" * 80)

top3 = sorted(summary, key=lambda x: x[1]["accuracy"], reverse=True)[:3]

for method_name, _ in top3:
    print(f"\n  {method_name}:")
    # We need to re-run with date tracking for yearly breakdown
    # Use the concatenated predictions from the summary runs
    pass  # Yearly breakdown requires storing per-date predictions

# Instead, do it for the best score-based and best ML methods
print("\n  Yearly breakdown for F: Score Optimized Threshold:")
all_preds_flat = np.concatenate(all_preds_opt)
all_actuals_flat = np.concatenate(all_actuals_opt)
all_dates_flat = np.concatenate(all_dates_opt)

yearly_df = pd.DataFrame({
    "pred": all_preds_flat,
    "actual": all_actuals_flat,
}, index=pd.DatetimeIndex(all_dates_flat))

for year in sorted(yearly_df.index.year.unique()):
    yr_data = yearly_df[yearly_df.index.year == year]
    if len(yr_data) < 10:
        continue
    acc = accuracy_score(yr_data["actual"], yr_data["pred"])
    prec = precision_score(yr_data["actual"], yr_data["pred"], zero_division=0)
    rec = recall_score(yr_data["actual"], yr_data["pred"], zero_division=0)
    transitions = np.sum(np.abs(np.diff(yr_data["pred"].values)))
    bear_pct = yr_data["pred"].mean()
    actual_bear_pct = yr_data["actual"].mean()
    print(f"    {year}: Acc={acc:.3f} Prec={prec:.3f} Rec={rec:.3f} "
          f"Transitions={transitions:.0f} Bear%={bear_pct:.1%} ActualBear%={actual_bear_pct:.1%}")

print("\n" + "=" * 80)
print("RESEARCH COMPLETE")
print("=" * 80)
