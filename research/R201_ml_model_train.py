"""
R201 — ML Model Training & SHAP Analysis for Alpha Discovery
=============================================================
Trains LightGBM on the feature matrix (44k+ rows, 122 features, 26 tokens)
with purged walk-forward cross-validation, then runs SHAP analysis to
identify actionable features and thresholds.

Input:  data/ml_features/feature_matrix.parquet
Output: outputs/ml_discovery/R201_results.json
"""

import sys
import os
import json
import time

sys.path.insert(0, "/workspace/crypto_backtest")
os.chdir("/workspace/crypto_backtest")

import pandas as pd
import numpy as np
from scipy import stats
import warnings

warnings.filterwarnings("ignore")

t0 = time.time()

# =============================================================================
# Step 1: Load and prepare data
# =============================================================================
print("=" * 70)
print("STEP 1: Loading data")
print("=" * 70)

df = pd.read_parquet("data/ml_features/feature_matrix.parquet")
print(f"Loaded: {df.shape[0]} rows, {df.shape[1]} columns")

label_col = "label_7d_fwd"
exclude_cols = {label_col, "token"}
feature_cols = [c for c in df.columns if c not in exclude_cols]

has_token = "token" in df.columns
if has_token:
    tokens = df["token"].unique()
    print(f"Tokens: {len(tokens)} — {sorted(tokens)[:5]}...")

X = df[feature_cols].copy()
y = df[label_col].copy()

# Drop rows with NaN label
valid = ~y.isna()
X = X[valid]
y = y[valid]
dates = df.index[valid]

# Fill remaining NaN features with 0
nan_counts = X.isna().sum()
nan_features = nan_counts[nan_counts > 0]
if len(nan_features) > 0:
    print(f"Features with NaNs: {len(nan_features)} (filling with 0)")
X = X.fillna(0)

# Replace inf with large finite values
X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

print(f"After cleanup: {X.shape[0]} rows, {X.shape[1]} features")
print(f"Label stats: mean={y.mean():.4f}, std={y.std():.4f}, "
      f"median={y.median():.4f}")
print(f"Date range: {dates.min()} to {dates.max()}")

# Binary label for classification
y_binary = (y > 0).astype(int)
print(f"Positive return fraction: {y_binary.mean():.3f}")

# =============================================================================
# Step 2: Time-series cross-validation (purged walk-forward)
# =============================================================================
print("\n" + "=" * 70)
print("STEP 2: Setting up purged walk-forward CV")
print("=" * 70)

# Sort by date (should already be sorted)
sort_idx = dates.argsort()
X = X.iloc[sort_idx].reset_index(drop=True)
y = y.iloc[sort_idx].reset_index(drop=True)
y_binary = y_binary.iloc[sort_idx].reset_index(drop=True)
dates_sorted = pd.DatetimeIndex(dates[sort_idx])

n = len(X)
n_folds = 5
fold_size = n // n_folds
purge_rows = 7 * 26  # 7 days * ~26 tokens per day

folds = []
for i in range(n_folds):
    start = i * fold_size
    end = (i + 1) * fold_size if i < n_folds - 1 else n
    folds.append((start, end))

print(f"Total samples: {n}")
print(f"Fold size: ~{fold_size}")
print(f"Purge gap: {purge_rows} rows (~7 days)")

for i, (s, e) in enumerate(folds):
    d0 = dates_sorted[s] if s < len(dates_sorted) else "?"
    d1 = dates_sorted[min(e - 1, len(dates_sorted) - 1)]
    print(f"  Fold {i}: rows {s}-{e} ({d0.date()} to {d1.date()})")

# =============================================================================
# Step 3: Train LightGBM (regression + classification)
# =============================================================================
print("\n" + "=" * 70)
print("STEP 3: Training LightGBM models")
print("=" * 70)

import lightgbm as lgb

params_reg = {
    "objective": "regression",
    "metric": "mae",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "max_depth": 6,
    "learning_rate": 0.05,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "min_child_samples": 50,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "n_jobs": -1,
    "seed": 42,
}

params_cls = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "max_depth": 6,
    "learning_rate": 0.05,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "min_child_samples": 50,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "n_jobs": -1,
    "seed": 42,
}

fold_results = []
models_reg = []
models_cls = []
all_oos_preds = np.full(n, np.nan)
all_oos_preds_cls = np.full(n, np.nan)
all_importances = []

for k in range(1, n_folds):
    print(f"\n--- Fold {k} (train on 0..{k-1}, test on {k}) ---")

    # Train indices: all folds before k
    train_end = folds[k - 1][1]
    # Purge: skip rows between train_end and train_end + purge_rows
    test_start = folds[k][0]
    test_end = folds[k][1]

    # Apply purge gap
    effective_train_end = max(0, test_start - purge_rows)
    if effective_train_end <= 0:
        print(f"  Skipping fold {k}: not enough training data after purge")
        continue

    train_idx = list(range(0, effective_train_end))
    test_idx = list(range(test_start, test_end))

    X_train = X.iloc[train_idx]
    y_train = y.iloc[train_idx]
    y_train_bin = y_binary.iloc[train_idx]
    X_test = X.iloc[test_idx]
    y_test = y.iloc[test_idx]
    y_test_bin = y_binary.iloc[test_idx]

    print(f"  Train: {len(train_idx)} rows, Test: {len(test_idx)} rows")
    print(f"  Purge gap: {test_start - effective_train_end} rows")

    # --- Regression model ---
    dtrain = lgb.Dataset(X_train, label=y_train)
    dtest = lgb.Dataset(X_test, label=y_test, reference=dtrain)

    model_reg = lgb.train(
        params_reg,
        dtrain,
        num_boost_round=500,
        valid_sets=[dtest],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
    )
    models_reg.append(model_reg)

    preds_reg = model_reg.predict(X_test)
    all_oos_preds[test_idx] = preds_reg

    # IC = Spearman correlation
    ic, ic_p = stats.spearmanr(preds_reg, y_test)
    mae = np.mean(np.abs(preds_reg - y_test))

    # --- Classification model ---
    dtrain_cls = lgb.Dataset(X_train, label=y_train_bin)
    dtest_cls = lgb.Dataset(X_test, label=y_test_bin, reference=dtrain_cls)

    model_cls = lgb.train(
        params_cls,
        dtrain_cls,
        num_boost_round=500,
        valid_sets=[dtest_cls],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
    )
    models_cls.append(model_cls)

    preds_cls = model_cls.predict(X_test)
    all_oos_preds_cls[test_idx] = preds_cls

    # AUC
    from sklearn.metrics import roc_auc_score

    auc = roc_auc_score(y_test_bin, preds_cls)

    # Feature importance (gain)
    imp = model_reg.feature_importance(importance_type="gain")
    all_importances.append(imp)

    print(f"  Regression: IC={ic:.4f} (p={ic_p:.4f}), MAE={mae:.4f}, "
          f"best_iter={model_reg.best_iteration}")
    print(f"  Classification: AUC={auc:.4f}, best_iter={model_cls.best_iteration}")

    fold_results.append({
        "fold": k,
        "train_size": len(train_idx),
        "test_size": len(test_idx),
        "ic": float(ic),
        "ic_pvalue": float(ic_p),
        "mae": float(mae),
        "auc": float(auc),
        "reg_best_iter": model_reg.best_iteration,
        "cls_best_iter": model_cls.best_iteration,
    })

# Summary
print("\n" + "-" * 50)
ics = [f["ic"] for f in fold_results]
aucs = [f["auc"] for f in fold_results]
print(f"Mean OOS IC:  {np.mean(ics):.4f} +/- {np.std(ics):.4f}")
print(f"Mean OOS AUC: {np.mean(aucs):.4f} +/- {np.std(aucs):.4f}")
print(f"Per-fold IC:  {[f'{x:.4f}' for x in ics]}")
print(f"Per-fold AUC: {[f'{x:.4f}' for x in aucs]}")

# =============================================================================
# Step 4: Feature importance & SHAP analysis
# =============================================================================
print("\n" + "=" * 70)
print("STEP 4: Feature importance & SHAP analysis")
print("=" * 70)

# Average feature importance across folds
avg_imp = np.mean(all_importances, axis=0)
imp_df = pd.DataFrame({
    "feature": feature_cols,
    "importance": avg_imp,
}).sort_values("importance", ascending=False).reset_index(drop=True)

print("\nTop 20 features by LightGBM gain:")
print("-" * 60)
for i, row in imp_df.head(20).iterrows():
    print(f"  {i+1:2d}. {row['feature']:30s}  gain={row['importance']:.1f}")

top_10_features = imp_df.head(10)["feature"].tolist()
top_20_features = imp_df.head(20)["feature"].tolist()

# Try SHAP
try:
    import shap

    HAS_SHAP = True
    print("\nSHAP is available — computing SHAP values on last fold...")
except ImportError:
    HAS_SHAP = False
    print("\nSHAP not installed — using feature importance only")

shap_importance = {}
shap_thresholds = {}

if HAS_SHAP and len(models_reg) > 0:
    # Use last fold's model and test data
    last_model = models_reg[-1]
    last_test_start = folds[-1][0]
    last_test_end = folds[-1][1]
    X_shap = X.iloc[last_test_start:last_test_end]
    y_shap = y.iloc[last_test_start:last_test_end]

    # Subsample if too large
    max_shap_samples = 2000
    if len(X_shap) > max_shap_samples:
        shap_idx = np.random.RandomState(42).choice(
            len(X_shap), max_shap_samples, replace=False
        )
        X_shap = X_shap.iloc[shap_idx]
        y_shap = y_shap.iloc[shap_idx]

    explainer = shap.TreeExplainer(last_model)
    shap_values = explainer.shap_values(X_shap)

    # Mean absolute SHAP per feature
    mean_abs_shap = np.mean(np.abs(shap_values), axis=0)
    shap_df = pd.DataFrame({
        "feature": feature_cols,
        "mean_abs_shap": mean_abs_shap,
    }).sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)

    print("\nTop 20 features by mean |SHAP|:")
    print("-" * 60)
    for i, row in shap_df.head(20).iterrows():
        print(f"  {i+1:2d}. {row['feature']:30s}  mean|SHAP|={row['mean_abs_shap']:.6f}")

    # Update top features list based on SHAP
    top_10_features = shap_df.head(10)["feature"].tolist()
    top_20_features = shap_df.head(20)["feature"].tolist()

    for feat in shap_df["feature"].tolist():
        shap_importance[feat] = float(
            shap_df.loc[shap_df["feature"] == feat, "mean_abs_shap"].values[0]
        )

    # For top 10 features: find threshold where SHAP changes sign
    print("\nSHAP sign-change thresholds for top 10 features:")
    print("-" * 60)
    for feat in top_10_features:
        feat_idx = feature_cols.index(feat)
        feat_vals = X_shap[feat].values
        feat_shap = shap_values[:, feat_idx]

        # Bin into 10 bins and find where mean SHAP crosses zero
        try:
            bins = pd.qcut(feat_vals, 10, labels=False, duplicates="drop")
            bin_means = pd.DataFrame({"bin": bins, "shap": feat_shap}).groupby("bin")["shap"].mean()

            # Find sign change
            signs = np.sign(bin_means.values)
            sign_changes = np.where(np.diff(signs) != 0)[0]
            if len(sign_changes) > 0:
                # Use the first sign change
                change_bin = sign_changes[0]
                # Get the feature value at boundary
                bin_edges = pd.qcut(feat_vals, 10, retbins=True, duplicates="drop")[1]
                threshold = bin_edges[change_bin + 1]
                direction = "positive above" if signs[change_bin + 1] > signs[change_bin] else "negative above"
                print(f"  {feat:30s}  threshold~{threshold:+.4f} ({direction})")
                shap_thresholds[feat] = {
                    "threshold": float(threshold),
                    "direction": direction,
                }
            else:
                direction = "monotonic positive" if bin_means.mean() > 0 else "monotonic negative"
                print(f"  {feat:30s}  {direction} (no sign change)")
                shap_thresholds[feat] = {"threshold": None, "direction": direction}
        except Exception as e:
            print(f"  {feat:30s}  error: {e}")

# =============================================================================
# Step 5: Quintile analysis (extract rules)
# =============================================================================
print("\n" + "=" * 70)
print("STEP 5: Quintile analysis for top 10 features")
print("=" * 70)

quintile_results = {}
extracted_rules = []

for feat in top_10_features:
    print(f"\n  Feature: {feat}")
    print(f"  {'Quintile':>10s}  {'Range':>25s}  {'Mean Ret':>10s}  {'Hit Rate':>10s}  {'Count':>8s}")
    print(f"  {'-'*70}")

    try:
        quintiles, bin_edges = pd.qcut(X[feat], 5, labels=False, duplicates="drop", retbins=True)
    except Exception:
        print(f"  Could not compute quintiles (insufficient unique values)")
        continue

    q_data = []
    for q in sorted(quintiles.dropna().unique()):
        mask = quintiles == q
        mean_ret = y[mask].mean()
        hit_rate = (y[mask] > 0).mean()
        count = mask.sum()
        lo = bin_edges[int(q)]
        hi = bin_edges[int(q) + 1]
        print(f"  Q{int(q):8d}  [{lo:+10.4f}, {hi:+10.4f}]  {mean_ret:+10.4f}  {hit_rate:10.3f}  {count:8d}")
        q_data.append({
            "quintile": int(q),
            "lo": float(lo),
            "hi": float(hi),
            "mean_ret": float(mean_ret),
            "hit_rate": float(hit_rate),
            "count": int(count),
        })

    quintile_results[feat] = q_data

    # Extract rule: compare Q0 vs Q4
    if len(q_data) >= 2:
        q0_ret = q_data[0]["mean_ret"]
        q4_ret = q_data[-1]["mean_ret"]
        spread = q4_ret - q0_ret

        # Check monotonicity
        rets = [qd["mean_ret"] for qd in q_data]
        is_monotonic_inc = all(rets[i] <= rets[i + 1] for i in range(len(rets) - 1))
        is_monotonic_dec = all(rets[i] >= rets[i + 1] for i in range(len(rets) - 1))

        if abs(spread) > 0.005:
            if spread > 0:
                rule = f"LONG when {feat} > {q_data[-1]['lo']:+.4f} (Q4 mean ret: {q4_ret:+.4f})"
                rule_short = f"SHORT when {feat} < {q_data[0]['hi']:+.4f} (Q0 mean ret: {q0_ret:+.4f})"
            else:
                rule = f"LONG when {feat} < {q_data[0]['hi']:+.4f} (Q0 mean ret: {q0_ret:+.4f})"
                rule_short = f"SHORT when {feat} > {q_data[-1]['lo']:+.4f} (Q4 mean ret: {q4_ret:+.4f})"

            monotone_tag = ""
            if is_monotonic_inc:
                monotone_tag = " [MONOTONIC INCREASING]"
            elif is_monotonic_dec:
                monotone_tag = " [MONOTONIC DECREASING]"

            extracted_rules.append({
                "feature": feat,
                "spread_q4_q0": float(spread),
                "rule_long": rule,
                "rule_short": rule_short,
                "monotonic": "increasing" if is_monotonic_inc else ("decreasing" if is_monotonic_dec else "non-monotonic"),
            })
            print(f"  => Spread (Q4-Q0): {spread:+.4f}{monotone_tag}")
            print(f"     {rule}")

# =============================================================================
# Step 6: Report and save
# =============================================================================
print("\n" + "=" * 70)
print("STEP 6: Summary Report")
print("=" * 70)

print(f"\n1. Per-fold OOS results:")
print(f"   {'Fold':>5s}  {'IC':>8s}  {'AUC':>8s}  {'MAE':>8s}  {'Train':>7s}  {'Test':>7s}")
for fr in fold_results:
    print(f"   {fr['fold']:5d}  {fr['ic']:8.4f}  {fr['auc']:8.4f}  {fr['mae']:8.4f}  "
          f"{fr['train_size']:7d}  {fr['test_size']:7d}")

print(f"\n2. Mean OOS IC:  {np.mean(ics):.4f} +/- {np.std(ics):.4f}")
print(f"   Mean OOS AUC: {np.mean(aucs):.4f} +/- {np.std(aucs):.4f}")

print(f"\n3. Top 20 features:")
for i, feat in enumerate(top_20_features):
    imp_val = imp_df.loc[imp_df["feature"] == feat, "importance"].values[0]
    shap_val = shap_importance.get(feat, 0)
    print(f"   {i+1:2d}. {feat:30s}  gain={imp_val:8.1f}  shap={shap_val:.6f}")

print(f"\n4. Extracted rules ({len(extracted_rules)} rules):")
for rule in extracted_rules:
    print(f"   {rule['feature']:30s}  spread={rule['spread_q4_q0']:+.4f}  "
          f"[{rule['monotonic']}]")
    print(f"      {rule['rule_long']}")

# Compute overall OOS IC (all folds combined)
oos_mask = ~np.isnan(all_oos_preds)
overall_ic, overall_ic_p = stats.spearmanr(all_oos_preds[oos_mask], y[oos_mask])
print(f"\n5. Overall OOS IC (all folds): {overall_ic:.4f} (p={overall_ic_p:.6f})")
print(f"   OOS coverage: {oos_mask.sum()}/{n} rows")

elapsed = time.time() - t0
print(f"\nTotal runtime: {elapsed:.1f}s")

# Save results
results = {
    "meta": {
        "script": "R201_ml_model_train.py",
        "n_rows": int(n),
        "n_features": int(len(feature_cols)),
        "date_range": [str(dates_sorted[0].date()), str(dates_sorted[-1].date())],
        "runtime_seconds": round(elapsed, 1),
    },
    "cv_results": {
        "n_folds": len(fold_results),
        "purge_rows": purge_rows,
        "per_fold": fold_results,
        "mean_ic": float(np.mean(ics)),
        "std_ic": float(np.std(ics)),
        "mean_auc": float(np.mean(aucs)),
        "std_auc": float(np.std(aucs)),
        "overall_oos_ic": float(overall_ic),
    },
    "feature_importance": {
        "top_20_gain": [
            {"feature": row["feature"], "gain": float(row["importance"])}
            for _, row in imp_df.head(20).iterrows()
        ],
        "top_20_shap": [
            {"feature": feat, "mean_abs_shap": shap_importance.get(feat, 0)}
            for feat in top_20_features
        ] if shap_importance else [],
    },
    "shap_thresholds": shap_thresholds,
    "quintile_analysis": quintile_results,
    "extracted_rules": extracted_rules,
}

output_path = "outputs/ml_discovery/R201_results.json"
os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, "w") as f:
    json.dump(results, f, indent=2)

print(f"\nResults saved to {output_path}")
print("Done.")
