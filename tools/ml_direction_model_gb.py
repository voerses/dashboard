"""
ML Direction Model — GradientBoosting Variant
==============================================
Same pipeline as ml_direction_model.py (RandomForest) but uses
sklearn.ensemble.GradientBoostingClassifier.

Labeling:
  +1 if close[t+H] > close[t] * 1.03  (up 3%+)
  -1 if close[t+H] < close[t] * 0.97  (down 3%+)
   0 otherwise (neutral, skipped in training)

Horizon: 24h (best from prior RF research)
Model: GradientBoosting (200 trees, max_depth=5, lr=0.05, subsample=0.8, min_samples_leaf=50)
Walk-forward: train first 60%, test last 40%
Training tokens: 30, validation tokens: 20 (separate)

OOM protection: balanced training capped at MAX_TRAIN_SAMPLES (80k).
Each phase frees memory before starting the next.

Output: results/v4/ml_dir_gb_model.joblib, ml_dir_gb_scaler.joblib, ml_dir_gb_features.joblib
"""

import numpy as np
import pandas as pd
import os
import sys
import gc
import warnings
import time
from pathlib import Path

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(line_buffering=True)  # unbuffered output
sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')

from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
import joblib

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')

# OOM guard: cap balanced training samples for GradientBoosting
MAX_TRAIN_SAMPLES = 80_000
HORIZON = 24
THRESHOLD_PCT = 3.0


# ==================== Vectorized Helpers ====================

def ema_vec(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values

def rolling_mean_vec(arr, w):
    cs = np.cumsum(arr)
    out = np.full(len(arr), np.nan)
    out[w-1] = cs[w-1] / w
    if len(arr) > w:
        out[w:] = (cs[w:] - cs[:-w]) / w
    return out

def rolling_std_vec(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).std().values

def rolling_max_vec(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).max().values

def rolling_min_vec(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).min().values


# ==================== Feature Engineering ====================

def compute_features(close, high, low, volume, funding=None):
    """Compute all 34 features vectorized. Same as ml_direction_model.py."""
    n = len(close)

    def log_ret(shift):
        r = np.zeros(n)
        r[shift:] = np.log(close[shift:] / close[:-shift])
        return r

    ret_1 = log_ret(1)
    ret_4 = log_ret(4)
    ret_12 = log_ret(12)
    ret_24 = log_ret(24)
    ret_48 = log_ret(48)
    ret_168 = log_ret(168)

    ema10 = ema_vec(close, 10)
    ema20 = ema_vec(close, 20)
    ema50 = ema_vec(close, 50)

    ema_dist_10 = (close - ema10) / np.maximum(close, 1e-10)
    ema_dist_20 = (close - ema20) / np.maximum(close, 1e-10)
    ema_dist_50 = (close - ema50) / np.maximum(close, 1e-10)
    ema_align = np.sign(close - ema10) + np.sign(close - ema20) + np.sign(close - ema50)

    ema12 = ema_vec(close, 12)
    ema26 = ema_vec(close, 26)
    macd = ema12 - ema26
    macd_sig = ema_vec(macd, 9)
    macd_hist = macd - macd_sig
    macd_norm = macd / np.maximum(close, 1e-10)
    macd_hist_norm = macd_hist / np.maximum(close, 1e-10)

    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = ema_vec(gain, 14)
    avg_loss = ema_vec(loss, 14)
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi_mom = np.diff(rsi, prepend=rsi[0])

    bb_mid = rolling_mean_vec(close, 20)
    bb_std = rolling_std_vec(close, 20)
    bb_std = np.nan_to_num(bb_std, nan=1.0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = np.where(np.nan_to_num(bb_mid, nan=1) > 0,
                        (bb_upper - bb_lower) / np.maximum(np.nan_to_num(bb_mid, nan=1), 1e-10), 0)
    bb_pct = np.where(bb_upper > bb_lower,
                      (close - bb_lower) / np.maximum(bb_upper - bb_lower, 1e-10), 0.5)

    prev_c = np.roll(close, 1); prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = ema_vec(tr, 14)
    atr_pct = atr / np.maximum(close, 1e-10)

    vol_5 = rolling_std_vec(ret_1, 5)
    vol_20 = rolling_std_vec(ret_1, 20)
    vol_50 = rolling_std_vec(ret_1, 50)
    vol_ratio_5_20 = np.where(np.nan_to_num(vol_20, nan=1) > 0,
                               np.nan_to_num(vol_5, nan=0) / np.maximum(np.nan_to_num(vol_20, nan=1), 1e-10), 1.0)

    vol_ma20 = rolling_mean_vec(volume, 20)
    vol_ratio = np.where(np.nan_to_num(vol_ma20, nan=1) > 0,
                         volume / np.maximum(np.nan_to_num(vol_ma20, nan=1), 1e-10), 1.0)

    plus_dm = np.maximum(np.diff(high, prepend=high[0]), 0)
    minus_dm = np.maximum(-np.diff(low, prepend=low[0]), 0)
    plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0)
    minus_dm = np.where(minus_dm > plus_dm, minus_dm, 0)
    sm_plus = ema_vec(plus_dm, 14)
    sm_minus = ema_vec(minus_dm, 14)
    plus_di = 100 * sm_plus / np.maximum(atr, 1e-10)
    minus_di = 100 * sm_minus / np.maximum(atr, 1e-10)
    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    adx = ema_vec(dx, 14)

    donch_high = np.nan_to_num(rolling_max_vec(high, 20), nan=high[0])
    donch_low = np.nan_to_num(rolling_min_vec(low, 20), nan=low[0])
    donch_pos = np.where(donch_high > donch_low,
                         (close - donch_low) / np.maximum(donch_high - donch_low, 1e-10), 0.5)

    body_pct = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    candle_range = high - low
    upper_wick = high - np.maximum(close, prev_c)
    lower_wick = np.minimum(close, prev_c) - low
    wick_ratio = np.where(candle_range > 0,
                          (upper_wick - lower_wick) / np.maximum(candle_range, 1e-10), 0)

    up = (close > prev_c).astype(np.float64)
    consec = np.zeros(n)
    for i in range(1, n):
        if up[i]:
            consec[i] = max(consec[i-1], 0) + 1
        elif close[i] < prev_c[i]:
            consec[i] = min(consec[i-1], 0) - 1

    h20 = np.nan_to_num(rolling_max_vec(high, 20), nan=high[0])
    l20 = np.nan_to_num(rolling_min_vec(low, 20), nan=low[0])
    dist_high = (close - h20) / np.maximum(close, 1e-10)
    dist_low = (close - l20) / np.maximum(close, 1e-10)

    features = {
        'ret_1': ret_1, 'ret_4': ret_4, 'ret_12': ret_12,
        'ret_24': ret_24, 'ret_48': ret_48, 'ret_168': ret_168,
        'ema_dist_10': ema_dist_10, 'ema_dist_20': ema_dist_20, 'ema_dist_50': ema_dist_50,
        'ema_align': ema_align,
        'macd_norm': macd_norm, 'macd_hist_norm': macd_hist_norm,
        'rsi': rsi, 'rsi_mom': rsi_mom,
        'bb_pct': np.nan_to_num(bb_pct, nan=0.5), 'bb_width': np.nan_to_num(bb_width, nan=0),
        'atr_pct': atr_pct,
        'vol_5': np.nan_to_num(vol_5, nan=0), 'vol_20': np.nan_to_num(vol_20, nan=0),
        'vol_50': np.nan_to_num(vol_50, nan=0),
        'vol_ratio_5_20': np.nan_to_num(vol_ratio_5_20, nan=1),
        'vol_ratio': np.nan_to_num(vol_ratio, nan=1),
        'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'donch_pos': np.nan_to_num(donch_pos, nan=0.5),
        'body_pct': body_pct, 'wick_ratio': wick_ratio,
        'consec': consec,
        'dist_high': dist_high, 'dist_low': dist_low,
    }

    if funding is not None:
        fm8 = rolling_mean_vec(funding, 8)
        fm48 = rolling_mean_vec(funding, 48)
        features['funding'] = funding
        features['funding_ma8'] = np.nan_to_num(fm8, nan=0)
        features['funding_ma48'] = np.nan_to_num(fm48, nan=0)

    return features


# ==================== Direction Labels ====================

def compute_direction_labels(close, horizon=24, threshold_pct=3.0):
    n = len(close)
    labels = np.zeros(n, dtype=np.int8)
    if n <= horizon:
        return labels, np.zeros(n)
    fwd_ret = np.zeros(n)
    fwd_ret[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0
    thr = threshold_pct / 100.0
    labels[fwd_ret > thr] = 1
    labels[fwd_ret < -thr] = -1
    labels[-horizon:] = 0
    return labels, fwd_ret


# ==================== Dataset Building ====================

def load_token(token):
    fpath = DATA_DIR / f'{token}_1h.parquet'
    if not fpath.exists():
        return None
    return pd.read_parquet(fpath)


def build_token_features_direction(token, horizon=24, threshold_pct=3.0):
    df = load_token(token)
    if df is None or len(df) < 500:
        return None, None, None

    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)
    funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None

    features = compute_features(close, high, low, volume, funding)
    labels, fwd_ret = compute_direction_labels(close, horizon, threshold_pct)

    feat_names = sorted(features.keys())
    X = np.column_stack([features[k] for k in feat_names])

    valid = np.ones(len(close), dtype=bool)
    valid[:200] = False
    valid[-horizon:] = False
    nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
    valid = valid & ~nan_mask

    del df, close, high, low, volume, funding, features
    gc.collect()

    return X[valid], labels[valid], feat_names


def get_tokens_by_data_size(n_tokens=50):
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    return [f.stem.replace('_1h', '') for f in token_files[:n_tokens]]


# ==================== Training ====================

def undersample_balance(X, y, rng, max_total=None):
    pos_idx = np.where(y == 1)[0]
    neg_idx = np.where(y == -1)[0]
    n_min = min(len(pos_idx), len(neg_idx))
    if n_min == 0:
        return X, y
    if max_total is not None:
        n_min = min(n_min, max_total // 2)
    pos_sample = rng.choice(pos_idx, n_min, replace=False)
    neg_sample = rng.choice(neg_idx, n_min, replace=False)
    bal_idx = np.sort(np.concatenate([pos_sample, neg_sample]))
    return X[bal_idx], y[bal_idx]


def evaluate_direction(y_proba, y_test, classes, feat_names, model):
    """Evaluate direction predictions at multiple probability thresholds."""
    metrics = {}
    thresholds = [0.55, 0.60, 0.65, 0.70]
    best_precision = 0.0

    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            print(f'  WARNING: class {direction} ({label}) not in model classes')
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]

        print(f'\n  {label} predictions (class={direction}):')
        print(f'  {"Threshold":<12} {"N_preds":<10} {"Correct":<10} {"Precision":<12} {"Recall":<10} {"Wrong_dir":<12}')
        print(f'  {"-"*66}')

        for thr in thresholds:
            signals = probs >= thr
            n_signals = signals.sum()
            if n_signals == 0:
                print(f'  p>={thr:.2f}       0           --          --           --          --')
                continue
            correct = (y_test[signals] == direction).sum()
            wrong_dir = (y_test[signals] == -direction).sum()
            precision = correct / n_signals
            total_actual = (y_test == direction).sum()
            recall = correct / max(total_actual, 1)

            key = f'{label}_p{int(thr*100)}'
            metrics[key] = {
                'n_predictions': int(n_signals),
                'correct': int(correct),
                'wrong_direction': int(wrong_dir),
                'precision': float(precision),
                'recall': float(recall),
            }
            print(f'  p>={thr:.2f}       {n_signals:<10} {correct:<10} {precision:<12.4f} {recall:<10.4f} {wrong_dir}')
            if precision > best_precision:
                best_precision = precision

    metrics['best_precision'] = best_precision

    # Feature importances
    imp = model.feature_importances_
    top_idx = np.argsort(imp)[::-1][:15]
    print(f'\n  Top 15 Feature Importances:')
    fi_list = []
    for rank, i in enumerate(top_idx):
        fi_list.append((feat_names[i], float(imp[i])))
        print(f'    {rank+1:2d}. {feat_names[i]:<20s} {imp[i]:.4f}')
    metrics['feature_importances'] = fi_list

    # Overall accuracy
    y_pred = np.zeros(len(y_test), dtype=np.int8)
    if 1 in classes and -1 in classes:
        long_idx = classes.index(1)
        short_idx = classes.index(-1)
        long_p = y_proba[:, long_idx]
        short_p = y_proba[:, short_idx]
        y_pred[long_p > short_p] = 1
        y_pred[short_p > long_p] = -1
        y_pred[(long_p == short_p)] = 1

    total = len(y_test)
    correct_total = (y_pred == y_test).sum()
    print(f'\n  Overall accuracy (argmax): {correct_total}/{total} = {correct_total/total:.4f}')
    metrics['overall_accuracy'] = float(correct_total / total)

    return metrics


# ==================== Main ====================

def run_direction_gb():
    """Train GB direction model: 30 train tokens, validate on 20 held-out tokens."""
    t_start = time.time()

    print('=' * 70)
    print('ML DIRECTION MODEL -- GRADIENT BOOSTING')
    print(f'Horizon: {HORIZON}h | Threshold: {THRESHOLD_PCT}% | OOM cap: {MAX_TRAIN_SAMPLES:,}')
    print('=' * 70)

    all_tokens = get_tokens_by_data_size(50)
    train_tokens = all_tokens[:30]
    val_tokens = all_tokens[30:50]

    print(f'\nTrain tokens ({len(train_tokens)}): {train_tokens[:10]}...')
    print(f'Validation tokens ({len(val_tokens)}): {val_tokens[:10]}...')

    # ==========================================
    # PHASE 1: Build train dataset, train, evaluate on walk-forward test
    # ==========================================
    print(f'\n--- Building TRAIN dataset ({len(train_tokens)} tokens) ---')
    train_X_list = []
    train_y_list = []
    feat_names = None

    for i, token in enumerate(train_tokens):
        X, y, fn = build_token_features_direction(token, horizon=HORIZON, threshold_pct=THRESHOLD_PCT)
        if X is not None and len(X) > 100:
            train_X_list.append(X)
            train_y_list.append(y)
            if feat_names is None:
                feat_names = fn
            if (i + 1) % 10 == 0:
                n_samples = sum(len(x) for x in train_X_list)
                print(f'  Loaded {i+1}/{len(train_tokens)} tokens ({n_samples:,} samples)')
        gc.collect()

    if not train_X_list:
        print('ERROR: No training data loaded.')
        return

    train_X = np.vstack(train_X_list)
    train_y = np.concatenate(train_y_list)
    del train_X_list, train_y_list
    gc.collect()

    n_long = (train_y == 1).sum()
    n_short = (train_y == -1).sum()
    n_neut = (train_y == 0).sum()
    print(f'  Train dataset: {len(train_y):,} samples, {train_X.shape[1]} features')
    print(f'    LONG: {n_long:,} ({n_long/len(train_y)*100:.1f}%)')
    print(f'    SHORT: {n_short:,} ({n_short/len(train_y)*100:.1f}%)')
    print(f'    Neutral: {n_neut:,} ({n_neut/len(train_y)*100:.1f}%)')

    # Only keep +1 and -1
    mask_nz = (train_y == 1) | (train_y == -1)
    X_nz = train_X[mask_nz]
    y_nz = train_y[mask_nz]
    del train_X, train_y
    gc.collect()

    n_total = len(y_nz)
    print(f'\n  Non-neutral: {n_total:,} (L={(y_nz==1).sum():,}, S={(y_nz==-1).sum():,})')

    # Walk-forward: 60% train, 40% test
    split_idx = int(n_total * 0.60)
    X_train_raw = X_nz[:split_idx]
    y_train_raw = y_nz[:split_idx]
    X_test_raw = X_nz[split_idx:]
    y_test_raw = y_nz[split_idx:]
    del X_nz, y_nz
    gc.collect()

    print(f'  Walk-forward split (60/40):')
    print(f'    Train: {len(y_train_raw):,} (L={sum(y_train_raw==1):,}, S={sum(y_train_raw==-1):,})')
    print(f'    Test:  {len(y_test_raw):,} (L={sum(y_test_raw==1):,}, S={sum(y_test_raw==-1):,})')

    # Balance + cap
    rng = np.random.RandomState(42)
    X_train_bal, y_train_bal = undersample_balance(X_train_raw, y_train_raw, rng, max_total=MAX_TRAIN_SAMPLES)
    del X_train_raw, y_train_raw
    gc.collect()

    print(f'    Balanced (capped {MAX_TRAIN_SAMPLES:,}): {len(y_train_bal):,} '
          f'(L={sum(y_train_bal==1):,}, S={sum(y_train_bal==-1):,})')

    # Scale
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train_bal)
    X_test_s = scaler.transform(X_test_raw)
    del X_train_bal
    gc.collect()

    # Train
    t0 = time.time()
    model = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        min_samples_leaf=50,
        random_state=42,
        verbose=1,
    )
    print(f'\n  Fitting GradientBoosting on {len(y_train_bal):,} samples, {X_train_s.shape[1]} features...')
    model.fit(X_train_s, y_train_bal)
    train_time = time.time() - t0
    print(f'\n  GradientBoosting trained in {train_time:.1f}s')
    del X_train_s, y_train_bal
    gc.collect()

    # Evaluate on walk-forward test
    classes = list(model.classes_)
    y_proba = model.predict_proba(X_test_s)
    del X_test_s
    gc.collect()

    print(f'\n--- Walk-Forward OOS Results ({len(y_test_raw):,} samples) ---')
    wf_metrics = evaluate_direction(y_proba, y_test_raw, classes, feat_names, model)
    del y_proba, y_test_raw
    gc.collect()

    # ==========================================
    # PHASE 2: Save model IMMEDIATELY (before loading validation data)
    # ==========================================
    print(f'\n{"="*70}')
    print('SAVING MODEL')
    print(f'{"="*70}')

    model_path = RESULTS_DIR / 'ml_dir_gb_model.joblib'
    scaler_path = RESULTS_DIR / 'ml_dir_gb_scaler.joblib'
    features_path = RESULTS_DIR / 'ml_dir_gb_features.joblib'

    joblib.dump(model, str(model_path))
    joblib.dump(scaler, str(scaler_path))
    joblib.dump(feat_names, str(features_path))

    print(f'  Saved: {model_path}')
    print(f'  Saved: {scaler_path}')
    print(f'  Saved: {features_path}')

    # ==========================================
    # PHASE 3: Validation on 20 held-out tokens
    # ==========================================
    print(f'\n--- Building VALIDATION dataset ({len(val_tokens)} tokens) ---')
    val_X_list = []
    val_y_list = []

    for i, token in enumerate(val_tokens):
        X, y, fn = build_token_features_direction(token, horizon=HORIZON, threshold_pct=THRESHOLD_PCT)
        if X is not None and len(X) > 100:
            val_X_list.append(X)
            val_y_list.append(y)
            if (i + 1) % 10 == 0:
                n_samples = sum(len(x) for x in val_X_list)
                print(f'  Loaded {i+1}/{len(val_tokens)} tokens ({n_samples:,} samples)')
        gc.collect()

    if val_X_list:
        val_X = np.vstack(val_X_list)
        val_y = np.concatenate(val_y_list)
        del val_X_list, val_y_list
        gc.collect()

        val_mask = (val_y == 1) | (val_y == -1)
        val_X_nz = val_X[val_mask]
        val_y_nz = val_y[val_mask]
        del val_X, val_y
        gc.collect()

        print(f'  Validation: {len(val_y_nz):,} non-neutral (L={(val_y_nz==1).sum():,}, S={(val_y_nz==-1).sum():,})')

        if len(val_y_nz) > 500:
            val_X_s = scaler.transform(val_X_nz)
            del val_X_nz
            gc.collect()

            val_proba = model.predict_proba(val_X_s)
            del val_X_s
            gc.collect()

            print(f'\n--- VALIDATION Results (20 held-out tokens) ---')
            val_metrics = evaluate_direction(val_proba, val_y_nz, classes, feat_names, model)
            del val_proba
        else:
            print('  SKIP validation -- not enough samples')
            val_metrics = {}

        del val_y_nz
        gc.collect()
    else:
        print('  No validation data available')
        val_metrics = {}

    # ==========================================
    # Final Summary
    # ==========================================
    elapsed = time.time() - t_start
    print(f'\n\n{"="*70}')
    print(f'FINAL SUMMARY -- GRADIENT BOOSTING')
    print(f'{"="*70}')
    print(f'Horizon: {HORIZON}h, Threshold: {THRESHOLD_PCT}%')
    print(f'Train time: {train_time:.1f}s, Total time: {elapsed:.0f}s')

    print(f'\nWalk-forward (30 tokens, 60/40 split):')
    for key in ['LONG_p55', 'LONG_p60', 'LONG_p65', 'LONG_p70']:
        if key in wf_metrics:
            m = wf_metrics[key]
            print(f'  {key}: precision={m["precision"]:.4f}  n={m["n_predictions"]:,}  recall={m["recall"]:.4f}')

    print(f'\nValidation (20 held-out tokens):')
    for key in ['LONG_p55', 'LONG_p60', 'LONG_p65', 'LONG_p70']:
        if key in val_metrics:
            m = val_metrics[key]
            print(f'  {key}: precision={m["precision"]:.4f}  n={m["n_predictions"]:,}  recall={m["recall"]:.4f}')

    print(f'\nModel saved to: {model_path}')
    print(f'{"="*70}')


if __name__ == '__main__':
    run_direction_gb()
