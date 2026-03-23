#!/usr/bin/env python3
"""
EXPERIMENT E: Cross-Sectional Relative Strength ML Model

Hypothesis: Relative features (cross-sectional ranks) are naturally stationary
and more predictable than absolute TA features. Instead of predicting absolute
direction, we predict which tokens will OUTPERFORM or UNDERPERFORM the median.

All features are cross-sectional RANKS (0-1 percentile) computed across all
tokens at each timestamp, making them bounded, stationary, and regime-invariant.
"""

import os
import sys
import warnings
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_score, classification_report
import joblib

warnings.filterwarnings("ignore")

# ── Configuration ──────────────────────────────────────────────────────────
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
RESULTS_DIR = Path("/workspace/crypto_backtest/results/v5")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

N_TOKENS = 30          # top 30 by file size
MIN_TOKENS_PER_TS = 10  # minimum tokens with data for ranks to be meaningful
TRAIN_CUTOFF = pd.Timestamp("2025-07-01")
QUINTILE_TOP = 0.80     # top 20%
QUINTILE_BOT = 0.20     # bottom 20%
FWD_HOURS = 24          # forward return horizon

FEATURE_NAMES = [
    "ret_1h_rank", "ret_4h_rank", "ret_24h_rank", "ret_168h_rank",
    "vol_24h_rank", "volume_rank", "rsi_rank", "funding_rank",
    "drawdown_rank", "strength_vs_btc_rank",
    "btc_ret_24h", "market_breadth", "dispersion",
]


def get_top_tokens(data_dir: Path, n: int) -> list[str]:
    """Get top N tokens by file size (proxy for data length/liquidity)."""
    files = sorted(data_dir.glob("*_1h.parquet"), key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace("_1h", "") for f in files[:n]]
    return tokens


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI for a single token's close series."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def load_and_prepare_token_data(tokens: list[str], data_dir: Path) -> dict[str, pd.DataFrame]:
    """Load all token data and compute per-token raw features."""
    token_data = {}
    for tok in tokens:
        fpath = data_dir / f"{tok}_1h.parquet"
        if not fpath.exists():
            continue
        df = pd.read_parquet(fpath)
        df = df.sort_index()

        # Raw returns
        df["ret_1h"] = df["close"].pct_change(1)
        df["ret_4h"] = df["close"].pct_change(4)
        df["ret_24h"] = df["close"].pct_change(24)
        df["ret_168h"] = df["close"].pct_change(168)

        # Volatility: std of 1h returns over 24h window
        df["vol_24h"] = df["ret_1h"].rolling(24, min_periods=12).std()

        # Volume ratio: current volume / 20h avg
        vol_avg = df["volume"].rolling(20, min_periods=10).mean()
        df["volume_ratio"] = df["volume"] / vol_avg.replace(0, np.nan)

        # RSI(14)
        df["rsi"] = compute_rsi(df["close"], 14)

        # Funding rate (use funding_1h if available)
        if "funding_1h" in df.columns:
            df["funding"] = df["funding_1h"]
        elif "funding_rate" in df.columns:
            df["funding"] = df["funding_rate"]
        else:
            df["funding"] = 0.0

        # Drawdown from 20h high
        rolling_high = df["high"].rolling(20, min_periods=10).max()
        df["drawdown_20h"] = (df["close"] - rolling_high) / rolling_high.replace(0, np.nan)

        # Forward 24h return (target)
        df["fwd_ret_24h"] = df["close"].shift(-FWD_HOURS) / df["close"] - 1

        token_data[tok] = df

    return token_data


def build_cross_sectional_features(token_data: dict[str, pd.DataFrame], btc_key: str = "BTC") -> pd.DataFrame:
    """
    Build the full feature matrix with cross-sectional ranks.
    Each row = (token, timestamp, 13 features, label).

    Memory-optimized: builds wide panels one at a time and converts to float32.
    """
    import gc

    # Get the union of all timestamps
    all_timestamps = set()
    for tok, df in token_data.items():
        all_timestamps.update(df.index)
    all_timestamps = sorted(all_timestamps)

    print(f"  Total unique timestamps: {len(all_timestamps):,}")

    tokens = list(token_data.keys())

    # Pre-extract BTC 24h returns for relative strength
    btc_ret_24h_series = None
    if btc_key in token_data:
        btc_ret_24h_series = token_data[btc_key]["ret_24h"]

    # Build ONE wide panel at a time to find valid timestamps
    # Use ret_1h to determine valid timestamps, then discard
    wide_ret1h = pd.DataFrame(index=all_timestamps)
    for tok in tokens:
        if "ret_1h" in token_data[tok].columns:
            wide_ret1h[tok] = token_data[tok]["ret_1h"]

    valid_count = wide_ret1h.notna().sum(axis=1)
    valid_mask = valid_count >= MIN_TOKENS_PER_TS
    valid_timestamps = valid_mask[valid_mask].index
    print(f"  Timestamps with >= {MIN_TOKENS_PER_TS} tokens: {len(valid_timestamps):,}")
    del wide_ret1h, valid_count, valid_mask
    gc.collect()

    # Mapping from rank feature name to raw feature name
    rank_features_map = {
        "ret_1h_rank": "ret_1h",
        "ret_4h_rank": "ret_4h",
        "ret_24h_rank": "ret_24h",
        "ret_168h_rank": "ret_168h",
        "vol_24h_rank": "vol_24h",
        "volume_rank": "volume_ratio",
        "rsi_rank": "rsi",
        "funding_rank": "funding",
        "drawdown_rank": "drawdown_20h",
    }

    print("  Computing cross-sectional ranks (one feature at a time)...")
    t0 = time.time()

    # Store per-token rank series (dict of {rank_name: {token: Series}})
    # This is more memory-efficient than storing full wide DataFrames
    token_rank_data = {rn: {} for rn in rank_features_map}
    token_rank_data["strength_vs_btc_rank"] = {}

    # Process each rank feature: build wide, rank, extract columns, discard wide
    for rank_name, raw_name in rank_features_map.items():
        wide = pd.DataFrame(index=valid_timestamps, dtype="float32")
        for tok in tokens:
            if raw_name in token_data[tok].columns:
                wide[tok] = token_data[tok][raw_name].reindex(valid_timestamps)
        ranked = wide.rank(axis=1, pct=True, na_option="keep")

        # If this is ret_24h, also compute strength_vs_btc and context features
        if raw_name == "ret_24h":
            # market_breadth
            market_breadth = (wide > 0).sum(axis=1) / wide.notna().sum(axis=1)
            market_breadth = market_breadth.astype("float32")
            # dispersion
            dispersion = wide.std(axis=1).astype("float32")
            # strength vs BTC
            if btc_ret_24h_series is not None:
                btc_aligned = btc_ret_24h_series.reindex(valid_timestamps).astype("float32")
                svb = wide.sub(btc_aligned, axis=0)
                svb_ranked = svb.rank(axis=1, pct=True, na_option="keep")
                for tok in tokens:
                    if tok in svb_ranked.columns:
                        token_rank_data["strength_vs_btc_rank"][tok] = svb_ranked[tok].astype("float32")
                del svb, svb_ranked
            else:
                for tok in tokens:
                    if tok in ranked.columns:
                        token_rank_data["strength_vs_btc_rank"][tok] = ranked[tok].astype("float32")

        # Extract per-token columns
        for tok in tokens:
            if tok in ranked.columns:
                token_rank_data[rank_name][tok] = ranked[tok].astype("float32")

        del wide, ranked
        gc.collect()

    # Context features
    btc_ret_24h_ctx = pd.Series(np.float32(0.0), index=valid_timestamps, dtype="float32")
    if btc_ret_24h_series is not None:
        btc_ret_24h_ctx = btc_ret_24h_series.reindex(valid_timestamps).astype("float32")

    # Forward returns: build wide, rank, extract
    fwd_wide = pd.DataFrame(index=valid_timestamps, dtype="float32")
    for tok in tokens:
        if "fwd_ret_24h" in token_data[tok].columns:
            fwd_wide[tok] = token_data[tok]["fwd_ret_24h"].reindex(valid_timestamps)
    fwd_rank_wide = fwd_wide.rank(axis=1, pct=True, na_option="keep")

    print(f"  Ranks computed in {time.time() - t0:.1f}s")

    # Now build per-token DataFrames and concatenate
    print("  Building feature matrix (melting to long format)...")
    t0 = time.time()

    records = []
    for tok in tokens:
        # Check token has data
        if tok not in token_rank_data["ret_1h_rank"]:
            continue

        feat_dict = {}
        for rank_name in rank_features_map:
            if tok in token_rank_data[rank_name]:
                feat_dict[rank_name] = token_rank_data[rank_name][tok]
            else:
                feat_dict[rank_name] = np.float32(np.nan)

        if tok in token_rank_data["strength_vs_btc_rank"]:
            feat_dict["strength_vs_btc_rank"] = token_rank_data["strength_vs_btc_rank"][tok]
        else:
            feat_dict["strength_vs_btc_rank"] = np.float32(np.nan)

        feat_dict["btc_ret_24h"] = btc_ret_24h_ctx
        feat_dict["market_breadth"] = market_breadth
        feat_dict["dispersion"] = dispersion

        if tok in fwd_rank_wide.columns:
            feat_dict["fwd_rank"] = fwd_rank_wide[tok].astype("float32")
            feat_dict["fwd_ret"] = fwd_wide[tok].astype("float32")
        else:
            feat_dict["fwd_rank"] = np.float32(np.nan)
            feat_dict["fwd_ret"] = np.float32(np.nan)

        feat_df = pd.DataFrame(feat_dict, index=valid_timestamps)
        feat_df["token"] = tok
        records.append(feat_df)

    # Free rank data before concat
    del token_rank_data, fwd_rank_wide, fwd_wide
    gc.collect()

    result = pd.concat(records, ignore_index=False)
    del records
    gc.collect()

    result = result.reset_index().rename(columns={"index": "timestamp"})

    # Assign labels: +1 top quintile, -1 bottom quintile, 0 middle
    result["label"] = np.int8(0)
    result.loc[result["fwd_rank"] >= QUINTILE_TOP, "label"] = np.int8(1)
    result.loc[result["fwd_rank"] <= QUINTILE_BOT, "label"] = np.int8(-1)

    # Drop rows with NaN in any feature or label
    before_drop = len(result)
    result = result.dropna(subset=FEATURE_NAMES + ["fwd_rank"])
    after_drop = len(result)
    print(f"  Dropped {before_drop - after_drop:,} rows with NaN ({after_drop:,} remaining)")
    print(f"  Feature matrix built in {time.time() - t0:.1f}s")

    return result


def balance_classes(X: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Undersample to balance +1 and -1 classes (drop 0 class)."""
    mask_pos = y == 1
    mask_neg = y == -1

    n_pos = mask_pos.sum()
    n_neg = mask_neg.sum()
    n_min = min(n_pos, n_neg)

    idx_pos = rng.choice(np.where(mask_pos)[0], size=n_min, replace=False)
    idx_neg = rng.choice(np.where(mask_neg)[0], size=n_min, replace=False)

    idx = np.concatenate([idx_pos, idx_neg])
    rng.shuffle(idx)

    return X[idx], y[idx]


def evaluate_at_thresholds(model, X_test, y_test, thresholds):
    """Evaluate precision at various probability thresholds."""
    proba = model.predict_proba(X_test)
    classes = model.classes_

    # Find indices for class +1 and -1
    idx_pos = np.where(classes == 1)[0][0]
    idx_neg = np.where(classes == -1)[0][0]

    p_pos = proba[:, idx_pos]
    p_neg = proba[:, idx_neg]

    results = []
    for thresh in thresholds:
        # Top quintile predictions
        top_mask = p_pos >= thresh
        n_top = top_mask.sum()
        if n_top > 0:
            top_prec = (y_test[top_mask] == 1).mean()
        else:
            top_prec = np.nan

        # Bottom quintile predictions
        bot_mask = p_neg >= thresh
        n_bot = bot_mask.sum()
        if n_bot > 0:
            bot_prec = (y_test[bot_mask] == -1).mean()
        else:
            bot_prec = np.nan

        results.append({
            "threshold": thresh,
            "top_q_prec": top_prec,
            "n_top": n_top,
            "bot_q_prec": bot_prec,
            "n_bot": n_bot,
        })

    return results, p_pos, p_neg


def per_month_breakdown(df_test, model, feature_names, threshold=0.60):
    """Break down performance by month."""
    X_test = df_test[feature_names].values
    y_test = df_test["label"].values

    proba = model.predict_proba(X_test)
    classes = model.classes_
    idx_pos = np.where(classes == 1)[0][0]
    p_pos = proba[:, idx_pos]

    df_test = df_test.copy()
    df_test["p_pos"] = p_pos
    df_test["month"] = pd.to_datetime(df_test["timestamp"]).dt.to_period("M")

    results = []
    for month, grp in df_test.groupby("month"):
        mask = grp["p_pos"] >= threshold
        n_pred = mask.sum()
        if n_pred > 0:
            prec = (grp.loc[mask, "label"] == 1).mean()
            avg_ret = grp.loc[mask, "fwd_ret"].mean()
        else:
            prec = np.nan
            avg_ret = np.nan

        base_rate = (grp["label"] == 1).mean()
        results.append({
            "month": str(month),
            "n_predictions": n_pred,
            "precision": prec,
            "avg_fwd_ret": avg_ret,
            "base_rate": base_rate,
            "total_samples": len(grp),
        })

    return results


def per_month_breakdown_short(df_test, model, feature_names, threshold=0.60):
    """Break down SHORT-SIDE performance by month."""
    X_test = df_test[feature_names].values

    proba = model.predict_proba(X_test)
    classes = model.classes_
    idx_neg = np.where(classes == -1)[0][0]
    p_neg = proba[:, idx_neg]

    df_test = df_test.copy()
    df_test["p_neg"] = p_neg
    df_test["month"] = pd.to_datetime(df_test["timestamp"]).dt.to_period("M")

    results = []
    for month, grp in df_test.groupby("month"):
        mask = grp["p_neg"] >= threshold
        n_pred = mask.sum()
        if n_pred > 0:
            prec = (grp.loc[mask, "label"] == -1).mean()
            avg_ret = grp.loc[mask, "fwd_ret"].mean()
        else:
            prec = np.nan
            avg_ret = np.nan

        base_rate = (grp["label"] == -1).mean()
        results.append({
            "month": str(month),
            "n_predictions": n_pred,
            "precision": prec,
            "avg_fwd_ret": avg_ret,
            "base_rate": base_rate,
            "total_samples": len(grp),
        })

    return results


def per_token_breakdown(df_test, model, feature_names, threshold=0.60):
    """Break down performance by token."""
    X_test = df_test[feature_names].values

    proba = model.predict_proba(X_test)
    classes = model.classes_
    idx_pos = np.where(classes == 1)[0][0]
    p_pos = proba[:, idx_pos]

    df_test = df_test.copy()
    df_test["p_pos"] = p_pos

    results = []
    for tok, grp in df_test.groupby("token"):
        mask = grp["p_pos"] >= threshold
        n_pred = mask.sum()
        if n_pred > 0:
            prec = (grp.loc[mask, "label"] == 1).mean()
            avg_ret = grp.loc[mask, "fwd_ret"].mean()
        else:
            prec = np.nan
            avg_ret = np.nan

        base_rate = (grp["label"] == 1).mean()
        results.append({
            "token": tok,
            "n_predictions": n_pred,
            "precision": prec,
            "avg_fwd_ret": avg_ret,
            "base_rate": base_rate,
            "total_samples": len(grp),
        })

    return sorted(results, key=lambda x: x.get("precision", 0) or 0, reverse=True)


def main():
    print("=" * 72)
    print("EXPERIMENT E: CROSS-SECTIONAL RELATIVE STRENGTH ML")
    print("=" * 72)
    print()

    # ── Step 1: Select tokens ──────────────────────────────────────────────
    tokens = get_top_tokens(DATA_DIR, N_TOKENS)
    print(f"Top {N_TOKENS} tokens by data size: {', '.join(tokens)}")
    print()

    # ── Step 2: Load and prepare per-token data ────────────────────────────
    print("Loading and preparing token data...")
    t0 = time.time()
    token_data = load_and_prepare_token_data(tokens, DATA_DIR)
    print(f"  Loaded {len(token_data)} tokens in {time.time() - t0:.1f}s")
    print()

    # ── Step 3: Build cross-sectional feature matrix ───────────────────────
    print("Building cross-sectional feature matrix...")
    df_all = build_cross_sectional_features(token_data)
    del token_data  # free memory
    import gc; gc.collect()
    print()

    # ── Step 4: Train/test split ───────────────────────────────────────────
    df_all["timestamp"] = pd.to_datetime(df_all["timestamp"])
    df_train = df_all[df_all["timestamp"] < TRAIN_CUTOFF].copy()
    df_test = df_all[df_all["timestamp"] >= TRAIN_CUTOFF].copy()

    # For training: only use +1 and -1 labels (drop neutral)
    df_train_labeled = df_train[df_train["label"] != 0].copy()
    df_test_labeled = df_test[df_test["label"] != 0].copy()

    # Also keep full test set for evaluation on all samples
    n_train = len(df_train_labeled)
    n_test_labeled = len(df_test_labeled)
    n_test_all = len(df_test)

    n_unique_tokens = df_all["token"].nunique()
    n_unique_ts = df_all["timestamp"].nunique()

    label_dist_train = df_train_labeled["label"].value_counts().sort_index()
    label_dist_test = df_test_labeled["label"].value_counts().sort_index()

    print("Dataset:")
    print(f"  Tokens: {n_unique_tokens}, Timestamps: {n_unique_ts:,}")
    print(f"  Train samples (labeled): {n_train:,} (before July 2025)")
    print(f"  Test samples (labeled): {n_test_labeled:,} (from July 2025)")
    print(f"  Test samples (all, incl neutral): {n_test_all:,}")
    print(f"  Train label dist: +1={label_dist_train.get(1, 0):,}, -1={label_dist_train.get(-1, 0):,}")
    print(f"  Test label dist:  +1={label_dist_test.get(1, 0):,}, -1={label_dist_test.get(-1, 0):,}")
    print()

    # ── Step 5: Balance classes ────────────────────────────────────────────
    X_train = df_train_labeled[FEATURE_NAMES].values
    y_train = df_train_labeled["label"].values

    rng = np.random.default_rng(42)
    X_train_bal, y_train_bal = balance_classes(X_train, y_train, rng)

    print(f"  Balanced training set: {len(X_train_bal):,} samples "
          f"(+1: {(y_train_bal == 1).sum():,}, -1: {(y_train_bal == -1).sum():,})")
    print()

    # ── Step 6: Train HistGBM ──────────────────────────────────────────────
    print("Training HistGradientBoostingClassifier...")
    t0 = time.time()

    model = HistGradientBoostingClassifier(
        max_iter=500,
        max_depth=5,
        learning_rate=0.05,
        min_samples_leaf=200,
        l2_regularization=1.0,
        max_bins=128,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=30,
        random_state=42,
        class_weight="balanced",
    )
    model.fit(X_train_bal, y_train_bal)
    print(f"  Training complete in {time.time() - t0:.1f}s")
    print(f"  Best iteration: {model.n_iter_}")
    print()

    # ── Step 7: Evaluate OOS ───────────────────────────────────────────────
    X_test = df_test_labeled[FEATURE_NAMES].values
    y_test = df_test_labeled["label"].values

    # Base rates
    base_top = (y_test == 1).mean()
    base_bot = (y_test == -1).mean()

    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
    thresh_results, p_pos, p_neg = evaluate_at_thresholds(model, X_test, y_test, thresholds)

    print("OOS EVALUATION (labeled test set: top & bottom quintile only):")
    print(f"  {'Thresh':>6s} | {'Top-Q Prec':>10s} | {'Top N':>6s} | {'Bot-Q Prec':>10s} | {'Bot N':>6s}")
    print(f"  {'-'*6}-+-{'-'*10}-+-{'-'*6}-+-{'-'*10}-+-{'-'*6}")
    for r in thresh_results:
        tp = f"{r['top_q_prec']:.3f}" if not np.isnan(r['top_q_prec']) else "  N/A"
        bp = f"{r['bot_q_prec']:.3f}" if not np.isnan(r['bot_q_prec']) else "  N/A"
        print(f"  {r['threshold']:6.2f} | {tp:>10s} | {r['n_top']:>6d} | {bp:>10s} | {r['n_bot']:>6d}")

    print()
    print(f"  Base rate (top quintile in labeled set): {base_top:.3f} (={base_top*100:.1f}%)")
    print(f"  Base rate (bottom quintile in labeled set): {base_bot:.3f} (={base_bot*100:.1f}%)")
    print()

    # Also evaluate on FULL test set (including neutral)
    print("OOS EVALUATION (full test set including neutral class):")
    X_test_full = df_test[FEATURE_NAMES].values
    y_test_full = df_test["label"].values
    proba_full = model.predict_proba(X_test_full)
    classes = model.classes_
    idx_pos_full = np.where(classes == 1)[0][0]
    idx_neg_full = np.where(classes == -1)[0][0]
    p_pos_full = proba_full[:, idx_pos_full]
    p_neg_full = proba_full[:, idx_neg_full]

    full_base_top = (y_test_full == 1).mean()
    full_base_bot = (y_test_full == -1).mean()

    print(f"  {'Thresh':>6s} | {'Top-Q Prec':>10s} | {'Top N':>6s} | {'Bot-Q Prec':>10s} | {'Bot N':>6s}")
    print(f"  {'-'*6}-+-{'-'*10}-+-{'-'*6}-+-{'-'*10}-+-{'-'*6}")
    for thresh in thresholds:
        top_mask = p_pos_full >= thresh
        n_top = top_mask.sum()
        top_prec = (y_test_full[top_mask] == 1).mean() if n_top > 0 else np.nan

        bot_mask = p_neg_full >= thresh
        n_bot = bot_mask.sum()
        bot_prec = (y_test_full[bot_mask] == -1).mean() if n_bot > 0 else np.nan

        tp = f"{top_prec:.3f}" if not np.isnan(top_prec) else "  N/A"
        bp = f"{bot_prec:.3f}" if not np.isnan(bot_prec) else "  N/A"
        print(f"  {thresh:6.2f} | {tp:>10s} | {n_top:>6d} | {bp:>10s} | {n_bot:>6d}")

    print(f"\n  Full-set base rate: top={full_base_top:.3f}, bottom={full_base_bot:.3f}")
    print()

    # ── Verdict ────────────────────────────────────────────────────────────
    # IMPORTANT: Use the FULL test set (including neutral class) for verdict.
    # The labeled-only evaluation is misleading because it excludes the middle
    # 60% of tokens, artificially inflating precision.
    #
    # The real question is: when the model says "this will be top quintile",
    # how often IS it actually top quintile out of ALL tokens?

    print("VERDICT ANALYSIS (using full test set — the real-world evaluation):")
    print()

    # Find precision at p>=0.60 on full set for top and bottom quintile
    full_top_mask_060 = p_pos_full >= 0.60
    full_bot_mask_060 = p_neg_full >= 0.60
    n_top_060 = full_top_mask_060.sum()
    n_bot_060 = full_bot_mask_060.sum()
    full_top_prec_060 = (y_test_full[full_top_mask_060] == 1).mean() if n_top_060 > 0 else 0
    full_bot_prec_060 = (y_test_full[full_bot_mask_060] == -1).mean() if n_bot_060 > 0 else 0
    top_lift = full_top_prec_060 / full_base_top if full_base_top > 0 else 0
    bot_lift = full_bot_prec_060 / full_base_bot if full_base_bot > 0 else 0

    print(f"  Top quintile (p>=0.60): prec={full_top_prec_060:.3f} vs base={full_base_top:.3f} "
          f"(lift={top_lift:.2f}x, N={n_top_060})")
    print(f"  Bot quintile (p>=0.60): prec={full_bot_prec_060:.3f} vs base={full_base_bot:.3f} "
          f"(lift={bot_lift:.2f}x, N={n_bot_060})")
    print()

    # Determine verdict based on full-set metrics
    top_has_edge = full_top_prec_060 > full_base_top * 1.15 and n_top_060 > 100
    bot_has_edge = full_bot_prec_060 > full_base_bot * 1.15 and n_bot_060 > 100

    if top_has_edge and bot_has_edge:
        verdict = "EDGE FOUND (both sides)"
    elif top_has_edge:
        verdict = "EDGE FOUND (top quintile only)"
    elif bot_has_edge:
        verdict = "ASYMMETRIC EDGE (bottom quintile only — can identify losers, not winners)"
    elif full_top_prec_060 > full_base_top * 1.05 or full_bot_prec_060 > full_base_bot * 1.05:
        verdict = "MARGINAL (slight lift but not actionable)"
    else:
        verdict = "NO EDGE"

    print(f"  VERDICT: {verdict}")
    print()

    # Explain why labeled-set and full-set results differ
    print("  NOTE: The labeled-set evaluation (top/bottom only) shows ~62% precision")
    print("  because it only asks 'is this top or bottom?' (binary). The full-set")
    print("  evaluation asks 'is this top quintile out of ALL tokens?' which is the")
    print("  real trading question. The 20% base rate is the correct benchmark.")
    print()

    # ── Feature importances ────────────────────────────────────────────────
    # HistGBM doesn't have feature_importances_ by default with early stopping
    # Use permutation importance or just the built-in if available
    # Actually HistGBM does NOT have feature_importances_ — we'll compute from
    # the internal tree structure (n_features_ splits count)
    # Workaround: use sklearn's permutation_importance (but it's slow)
    # Better: train without early stopping OR use a simple feature importance proxy

    # Let's use a fast approach: train accuracy drop when shuffling each feature
    print("Feature importances (permutation-based, 3 shuffles):")
    from sklearn.metrics import accuracy_score

    baseline_acc = accuracy_score(y_test, model.predict(X_test))
    importances = []
    for i, fname in enumerate(FEATURE_NAMES):
        drops = []
        for _ in range(3):
            X_shuf = X_test.copy()
            rng.shuffle(X_shuf[:, i])
            shuf_acc = accuracy_score(y_test, model.predict(X_shuf))
            drops.append(baseline_acc - shuf_acc)
        importances.append((fname, np.mean(drops)))

    importances.sort(key=lambda x: x[1], reverse=True)
    for fname, imp in importances:
        bar = "+" * max(0, int(imp * 500))
        print(f"  {fname:<25s} {imp:+.4f}  {bar}")
    print()

    # ── Per-month breakdown ────────────────────────────────────────────────
    print("PER-MONTH BREAKDOWN (top quintile precision at p>=0.60):")
    month_results = per_month_breakdown(df_test, model, FEATURE_NAMES, threshold=0.60)
    print(f"  {'Month':<10s} | {'N Pred':>7s} | {'Prec':>6s} | {'Avg Ret':>8s} | {'Base':>6s} | {'Total':>7s}")
    print(f"  {'-'*10}-+-{'-'*7}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*7}")
    for m in month_results:
        prec_str = f"{m['precision']:.3f}" if not np.isnan(m['precision']) else " N/A"
        ret_str = f"{m['avg_fwd_ret']:+.4f}" if not np.isnan(m['avg_fwd_ret']) else "  N/A"
        print(f"  {m['month']:<10s} | {m['n_predictions']:>7d} | {prec_str:>6s} | "
              f"{ret_str:>8s} | {m['base_rate']:>6.3f} | {m['total_samples']:>7d}")
    print()

    # ── Per-month breakdown (SHORT side) ──────────────────────────────────
    print("PER-MONTH BREAKDOWN (bottom quintile / SHORT side precision at p>=0.60):")
    month_results_short = per_month_breakdown_short(df_test, model, FEATURE_NAMES, threshold=0.60)
    print(f"  {'Month':<10s} | {'N Pred':>7s} | {'Prec':>6s} | {'Avg Ret':>8s} | {'Base':>6s} | {'Total':>7s}")
    print(f"  {'-'*10}-+-{'-'*7}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*7}")
    for m in month_results_short:
        prec_str = f"{m['precision']:.3f}" if not np.isnan(m['precision']) else " N/A"
        ret_str = f"{m['avg_fwd_ret']:+.4f}" if not np.isnan(m['avg_fwd_ret']) else "  N/A"
        print(f"  {m['month']:<10s} | {m['n_predictions']:>7d} | {prec_str:>6s} | "
              f"{ret_str:>8s} | {m['base_rate']:>6.3f} | {m['total_samples']:>7d}")
    print()

    # ── Per-token breakdown ────────────────────────────────────────────────
    print("PER-TOKEN BREAKDOWN (top 10 best-predicted, p>=0.60):")
    token_results = per_token_breakdown(df_test, model, FEATURE_NAMES, threshold=0.60)
    print(f"  {'Token':<8s} | {'N Pred':>7s} | {'Prec':>6s} | {'Avg Ret':>8s} | {'Base':>6s}")
    print(f"  {'-'*8}-+-{'-'*7}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}")
    for r in token_results[:10]:
        if np.isnan(r.get("precision", np.nan)):
            continue
        ret_str = f"{r['avg_fwd_ret']:+.4f}" if not np.isnan(r['avg_fwd_ret']) else "  N/A"
        print(f"  {r['token']:<8s} | {r['n_predictions']:>7d} | {r['precision']:>6.3f} | "
              f"{ret_str:>8s} | {r['base_rate']:>6.3f}")
    print()

    # ── Strategy return estimate ───────────────────────────────────────────
    print("STRATEGY RETURN ESTIMATE:")
    # Use p>=0.60 threshold
    top_mask_full = p_pos_full >= 0.60
    bot_mask_full = p_neg_full >= 0.60

    df_test_copy = df_test.copy()
    df_test_copy["p_pos"] = p_pos_full
    df_test_copy["p_neg"] = p_neg_full

    # Long-short: long top predictions, short bottom predictions
    n_days_test = (df_test_copy["timestamp"].max() - df_test_copy["timestamp"].min()).days
    if n_days_test == 0:
        n_days_test = 1

    top_preds = df_test_copy[top_mask_full]
    bot_preds = df_test_copy[bot_mask_full]

    avg_top_ret = top_preds["fwd_ret"].mean() if len(top_preds) > 0 else 0
    avg_bot_ret = bot_preds["fwd_ret"].mean() if len(bot_preds) > 0 else 0

    trades_per_day_long = len(top_preds) / max(n_days_test, 1)
    trades_per_day_short = len(bot_preds) / max(n_days_test, 1)

    # Long-only return
    long_monthly = avg_top_ret * trades_per_day_long * 30 / FWD_HOURS if trades_per_day_long > 0 else 0

    # Long-short return
    ls_return_per_trade = avg_top_ret - avg_bot_ret
    ls_monthly = ls_return_per_trade * min(trades_per_day_long, trades_per_day_short) * 30 / FWD_HOURS if min(trades_per_day_long, trades_per_day_short) > 0 else 0

    print(f"  Test period: {n_days_test} days")
    print(f"  Top predictions (p>=0.60): {len(top_preds):,} total, {trades_per_day_long:.1f}/day")
    print(f"  Bot predictions (p>=0.60): {len(bot_preds):,} total, {trades_per_day_short:.1f}/day")
    print(f"  Avg forward 24h return on top preds: {avg_top_ret:+.4f} ({avg_top_ret*100:+.2f}%)")
    print(f"  Avg forward 24h return on bot preds: {avg_bot_ret:+.4f} ({avg_bot_ret*100:+.2f}%)")
    print(f"  Long-short spread per trade: {ls_return_per_trade:+.4f} ({ls_return_per_trade*100:+.2f}%)")
    print()
    print(f"  Estimated monthly return (long-only, naive): {long_monthly*100:+.2f}%")
    print(f"  Estimated monthly return (long-short, naive): {ls_monthly*100:+.2f}%")
    print(f"  NOTE: These are naive estimates ignoring fees, slippage, and correlation.")
    print()

    # ── Save model ─────────────────────────────────────────────────────────
    model_path = RESULTS_DIR / "ml_exp_e_xsect_model.joblib"
    joblib.dump({
        "model": model,
        "feature_names": FEATURE_NAMES,
        "tokens": tokens,
        "train_cutoff": str(TRAIN_CUTOFF),
        "verdict": verdict,
    }, model_path)
    print(f"Model saved to {model_path}")
    print()
    print("=" * 72)
    print("EXPERIMENT E COMPLETE")
    print("=" * 72)


if __name__ == "__main__":
    main()
