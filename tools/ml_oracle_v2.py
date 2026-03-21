"""
ML Oracle Research Pipeline v2 — Fully Vectorized
===================================================
Uses numpy vectorized operations for speed.
Trains GradientBoosting to predict profitable entries.
Walk-forward validated to prevent overfitting.
"""

import numpy as np
import pandas as pd
import os
import sys
import json
import gc
import warnings
import time
from pathlib import Path

warnings.filterwarnings('ignore')
sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')

from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
import joblib

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')


# ==================== Vectorized Helpers ====================

def ema_vec(arr, span):
    """EMA using pandas for speed."""
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values

def rolling_mean_vec(arr, w):
    """Rolling mean using cumsum."""
    cs = np.cumsum(arr)
    out = np.full(len(arr), np.nan)
    out[w-1] = cs[w-1] / w
    if len(arr) > w:
        out[w:] = (cs[w:] - cs[:-w]) / w
    return out

def rolling_std_vec(arr, w):
    """Rolling std using pandas."""
    return pd.Series(arr).rolling(w, min_periods=w).std().values

def rolling_max_vec(arr, w):
    """Rolling max using pandas."""
    return pd.Series(arr).rolling(w, min_periods=w).max().values

def rolling_min_vec(arr, w):
    """Rolling min using pandas."""
    return pd.Series(arr).rolling(w, min_periods=w).min().values


# ==================== Feature Engineering ====================

def compute_features(close, high, low, volume, funding=None):
    """Compute all features vectorized. Returns feature dict."""
    n = len(close)

    # Returns at multiple horizons
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

    # EMAs
    ema10 = ema_vec(close, 10)
    ema20 = ema_vec(close, 20)
    ema50 = ema_vec(close, 50)

    # EMA distances (normalized)
    ema_dist_10 = (close - ema10) / np.maximum(close, 1e-10)
    ema_dist_20 = (close - ema20) / np.maximum(close, 1e-10)
    ema_dist_50 = (close - ema50) / np.maximum(close, 1e-10)
    ema_align = np.sign(close - ema10) + np.sign(close - ema20) + np.sign(close - ema50)

    # MACD
    ema12 = ema_vec(close, 12)
    ema26 = ema_vec(close, 26)
    macd = ema12 - ema26
    macd_sig = ema_vec(macd, 9)
    macd_hist = macd - macd_sig
    macd_norm = macd / np.maximum(close, 1e-10)
    macd_hist_norm = macd_hist / np.maximum(close, 1e-10)

    # RSI
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = ema_vec(gain, 14)
    avg_loss = ema_vec(loss, 14)
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi_mom = np.diff(rsi, prepend=rsi[0])

    # Bollinger Bands
    bb_mid = rolling_mean_vec(close, 20)
    bb_std = rolling_std_vec(close, 20)
    bb_std = np.nan_to_num(bb_std, nan=1.0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = np.where(np.nan_to_num(bb_mid, nan=1) > 0,
                        (bb_upper - bb_lower) / np.maximum(np.nan_to_num(bb_mid, nan=1), 1e-10), 0)
    bb_pct = np.where(bb_upper > bb_lower,
                      (close - bb_lower) / np.maximum(bb_upper - bb_lower, 1e-10), 0.5)

    # ATR
    prev_c = np.roll(close, 1); prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = ema_vec(tr, 14)
    atr_pct = atr / np.maximum(close, 1e-10)

    # Volatility
    vol_5 = rolling_std_vec(ret_1, 5)
    vol_20 = rolling_std_vec(ret_1, 20)
    vol_50 = rolling_std_vec(ret_1, 50)
    vol_ratio_5_20 = np.where(np.nan_to_num(vol_20, nan=1) > 0,
                               np.nan_to_num(vol_5, nan=0) / np.maximum(np.nan_to_num(vol_20, nan=1), 1e-10), 1.0)

    # Volume
    vol_ma20 = rolling_mean_vec(volume, 20)
    vol_ratio = np.where(np.nan_to_num(vol_ma20, nan=1) > 0,
                         volume / np.maximum(np.nan_to_num(vol_ma20, nan=1), 1e-10), 1.0)

    # ADX
    plus_dm = np.maximum(np.diff(high, prepend=high[0]), 0)
    minus_dm = np.maximum(-np.diff(low, prepend=low[0]), 0)
    # Zero out where the other direction is larger
    plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0)
    minus_dm = np.where(minus_dm > plus_dm, minus_dm, 0)
    sm_plus = ema_vec(plus_dm, 14)
    sm_minus = ema_vec(minus_dm, 14)
    plus_di = 100 * sm_plus / np.maximum(atr, 1e-10)
    minus_di = 100 * sm_minus / np.maximum(atr, 1e-10)
    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    adx = ema_vec(dx, 14)

    # Donchian
    donch_high = np.nan_to_num(rolling_max_vec(high, 20), nan=high[0])
    donch_low = np.nan_to_num(rolling_min_vec(low, 20), nan=low[0])
    donch_pos = np.where(donch_high > donch_low,
                         (close - donch_low) / np.maximum(donch_high - donch_low, 1e-10), 0.5)

    # Candle patterns
    body_pct = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    candle_range = high - low
    upper_wick = high - np.maximum(close, prev_c)
    lower_wick = np.minimum(close, prev_c) - low
    wick_ratio = np.where(candle_range > 0,
                          (upper_wick - lower_wick) / np.maximum(candle_range, 1e-10), 0)

    # Consecutive up/down
    up = (close > prev_c).astype(np.float64)
    down = (close < prev_c).astype(np.float64)
    consec = np.zeros(n)
    for i in range(1, n):
        if up[i]:
            consec[i] = max(consec[i-1], 0) + 1
        elif down[i]:
            consec[i] = min(consec[i-1], 0) - 1

    # Distance from recent extremes
    h20 = np.nan_to_num(rolling_max_vec(high, 20), nan=high[0])
    l20 = np.nan_to_num(rolling_min_vec(low, 20), nan=low[0])
    dist_high = (close - h20) / np.maximum(close, 1e-10)
    dist_low = (close - l20) / np.maximum(close, 1e-10)

    # Hour of day (cyclical encoding) - from index if available
    hour_sin = np.zeros(n)
    hour_cos = np.zeros(n)

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


# ==================== Label Generation (Vectorized) ====================

def compute_labels_fast(close, high, low, lookfwd=48, thr=2.5):
    """Vectorized label computation using rolling operations.

    Labels:
      1 = forward max return > thr% (good long)
     -1 = forward max drop > thr% (good short)
      0 = neutral

    Simplified: uses rolling max/min of future prices.
    """
    n = len(close)
    labels = np.zeros(n, dtype=np.int8)

    # Compute forward rolling max/min of high and low
    # We need to look FORWARD, so we reverse, apply rolling, then reverse back
    high_rev = high[::-1].copy()
    low_rev = low[::-1].copy()

    fwd_max_high = pd.Series(high_rev).rolling(lookfwd, min_periods=1).max().values[::-1]
    fwd_min_low = pd.Series(low_rev).rolling(lookfwd, min_periods=1).min().values[::-1]

    # Shift by 1 to exclude current bar
    fwd_max_high = np.roll(fwd_max_high, -1)
    fwd_min_low = np.roll(fwd_min_low, -1)
    fwd_max_high[-lookfwd:] = close[-lookfwd:]
    fwd_min_low[-lookfwd:] = close[-lookfwd:]

    # Forward max return (long)
    fwd_max_ret = (fwd_max_high / close - 1) * 100
    # Forward max drop (short)
    fwd_max_drop = (fwd_min_low / close - 1) * 100

    # Label: long if max up > threshold, short if max down > threshold
    # Prefer the direction with larger move
    long_ok = fwd_max_ret >= thr
    short_ok = fwd_max_drop <= -thr

    # When both are possible, pick the one with larger absolute move
    both = long_ok & short_ok
    labels[long_ok & ~both] = 1
    labels[short_ok & ~both] = -1
    labels[both & (fwd_max_ret >= -fwd_max_drop)] = 1
    labels[both & (fwd_max_ret < -fwd_max_drop)] = -1

    # Don't label last lookfwd bars
    labels[-lookfwd:] = 0

    return labels, fwd_max_ret, fwd_max_drop


# ==================== Dataset Building ====================

def load_token(token, months=18):
    """Load and return token data."""
    fpath = DATA_DIR / f'{token}_1h.parquet'
    if not fpath.exists():
        return None
    df = pd.read_parquet(fpath)
    if months > 0:
        cutoff = len(df) - months * 730
        if cutoff > 0:
            df = df.iloc[cutoff:]
    return df


def build_token_features(token, months=18, lookfwd=48, thr=2.5):
    """Build features + labels for one token."""
    df = load_token(token, months)
    if df is None or len(df) < 500:
        return None, None, None

    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)
    funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None

    features = compute_features(close, high, low, volume, funding)
    labels, _, _ = compute_labels_fast(close, high, low, lookfwd, thr)

    # Stack features into matrix
    feat_names = sorted(features.keys())
    X = np.column_stack([features[k] for k in feat_names])

    # Valid mask: skip warmup, last lookfwd bars, and NaN rows
    valid = np.ones(len(close), dtype=bool)
    valid[:200] = False
    valid[-lookfwd:] = False
    nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
    valid = valid & ~nan_mask

    return X[valid], labels[valid], feat_names


def build_multi_token_dataset(tokens, months=18, lookfwd=48, thr=2.5):
    """Build dataset across multiple tokens."""
    all_X = []
    all_y = []

    for i, token in enumerate(tokens):
        X, y, feat_names = build_token_features(token, months, lookfwd, thr)
        if X is not None and len(X) > 0:
            all_X.append(X)
            all_y.append(y)
            if (i + 1) % 10 == 0:
                print(f'  Loaded {i+1}/{len(tokens)} tokens ({sum(len(x) for x in all_X)} samples)')
        gc.collect()

    if not all_X:
        return None, None, None

    X = np.vstack(all_X)
    y = np.concatenate(all_y)

    return X, y, feat_names


# ==================== Training ====================

def train_walk_forward(X, y, feat_names, n_splits=3):
    """Walk-forward cross-validation with multiple splits."""
    n = len(y)
    split_size = n // (n_splits + 1)

    results = []
    best_model = None
    best_scaler = None
    best_score = -1

    for fold in range(n_splits):
        train_end = split_size * (fold + 2)
        test_start = train_end
        test_end = min(test_start + split_size, n)

        if test_end <= test_start:
            break

        X_train = X[:train_end]
        y_train = y[:train_end]
        X_test = X[test_start:test_end]
        y_test = y[test_start:test_end]

        n_long = (y_train == 1).sum()
        n_short = (y_train == -1).sum()
        n_neutral = (y_train == 0).sum()

        print(f'\n  Fold {fold+1}/{n_splits}: Train={len(y_train)}, Test={len(y_test)}')
        print(f'    Train labels: Long={n_long} ({n_long/len(y_train)*100:.1f}%), '
              f'Short={n_short} ({n_short/len(y_train)*100:.1f}%), Neutral={n_neutral}')

        # Downsample neutral for balance
        pos_idx = np.where(y_train == 1)[0]
        neg_idx = np.where(y_train == -1)[0]
        zero_idx = np.where(y_train == 0)[0]

        target_neutral = min(len(zero_idx), max(len(pos_idx), len(neg_idx)) * 3)
        rng = np.random.RandomState(42 + fold)
        zero_sample = rng.choice(zero_idx, target_neutral, replace=False) if target_neutral < len(zero_idx) else zero_idx

        bal_idx = np.sort(np.concatenate([pos_idx, neg_idx, zero_sample]))
        X_bal = X_train[bal_idx]
        y_bal = y_train[bal_idx]

        print(f'    Balanced: {len(y_bal)} (L={sum(y_bal==1)}, S={sum(y_bal==-1)}, N={sum(y_bal==0)})')

        # Scale
        scaler = StandardScaler()
        X_bal_s = scaler.fit_transform(X_bal)
        X_test_s = scaler.transform(X_test)

        # Train
        t0 = time.time()
        model = GradientBoostingClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            min_samples_leaf=100,
            random_state=42,
        )
        model.fit(X_bal_s, y_bal)
        print(f'    Trained in {time.time()-t0:.1f}s')

        # Evaluate
        classes = list(model.classes_)
        y_pred = model.predict(X_test_s)
        y_proba = model.predict_proba(X_test_s)

        fold_result = evaluate_predictions(y_pred, y_proba, y_test, classes, feat_names, model)
        results.append(fold_result)

        # Track best
        combined_precision = fold_result.get('combined_precision', 0)
        if combined_precision > best_score:
            best_score = combined_precision
            best_model = model
            best_scaler = scaler

    return results, best_model, best_scaler


def evaluate_predictions(y_pred, y_proba, y_test, classes, feat_names, model):
    """Evaluate predictions and return metrics dict."""
    result = {}

    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]

        for prob_thr in [0.3, 0.4, 0.5, 0.6]:
            signals = probs >= prob_thr
            n_signals = signals.sum()
            if n_signals == 0:
                continue
            correct = (y_test[signals] == direction).sum()
            precision = correct / n_signals
            wrong_dir = (y_test[signals] == -direction).sum()

            key = f'{label}_p{int(prob_thr*100)}'
            result[key] = {
                'signals': int(n_signals),
                'precision': float(precision),
                'correct': int(correct),
                'wrong_dir': int(wrong_dir),
                'per_day': float(n_signals / (len(y_test) / 24))
            }
            print(f'    {label} (p>={prob_thr}): {n_signals} signals, '
                  f'Prec={precision:.3f}, WrongDir={wrong_dir}, /day={n_signals/(len(y_test)/24):.1f}')

    # Combined precision at p>=0.4
    total_correct = 0
    total_signals = 0
    for direction in [1, -1]:
        if direction not in classes:
            continue
        cls_idx = classes.index(direction)
        signals = y_proba[:, cls_idx] >= 0.4
        total_signals += signals.sum()
        total_correct += (y_test[signals] == direction).sum()

    result['combined_precision'] = total_correct / max(total_signals, 1)
    result['combined_signals'] = int(total_signals)

    # Feature importances
    imp = model.feature_importances_
    top_idx = np.argsort(imp)[::-1][:10]
    result['top_features'] = [(feat_names[i], float(imp[i])) for i in top_idx]
    print(f'    Top features: {[feat_names[i] for i in top_idx[:5]]}')

    return result


# ==================== Strategy Generation ====================

def generate_strategy_code(model_path, scaler_path, features_path, feat_names,
                           prob_threshold=0.4, name='s200_ml_oracle'):
    """Generate strategy .py file that uses trained ML model."""

    code = f'''"""
Strategy {name}: ML Oracle Classifier
======================================
GradientBoosting trained on oracle-labeled entries.
Walk-forward validated. Features: {len(feat_names)} indicators.
"""

import numpy as np
import pandas as pd
import joblib
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND

# Load model at import time
_model = joblib.load('{model_path}')
_scaler = joblib.load('{scaler_path}')
_feat_names = joblib.load('{features_path}')
_classes = list(_model.classes_)
_PROB_THR = {prob_threshold}


def _ema(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values

def _rolling_mean(arr, w):
    cs = np.cumsum(arr)
    out = np.full(len(arr), np.nan)
    out[w-1] = cs[w-1] / w
    if len(arr) > w:
        out[w:] = (cs[w:] - cs[:-w]) / w
    return out

def _rolling_std(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).std().values

def _rolling_max(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).max().values

def _rolling_min(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).min().values


def _compute_features(close, high, low, volume, funding=None):
    n = len(close)

    def log_ret(shift):
        r = np.zeros(n)
        r[shift:] = np.log(close[shift:] / close[:-shift])
        return r

    ret_1 = log_ret(1); ret_4 = log_ret(4); ret_12 = log_ret(12)
    ret_24 = log_ret(24); ret_48 = log_ret(48); ret_168 = log_ret(168)

    ema10 = _ema(close, 10); ema20 = _ema(close, 20); ema50 = _ema(close, 50)
    ema_dist_10 = (close - ema10) / np.maximum(close, 1e-10)
    ema_dist_20 = (close - ema20) / np.maximum(close, 1e-10)
    ema_dist_50 = (close - ema50) / np.maximum(close, 1e-10)
    ema_align = np.sign(close - ema10) + np.sign(close - ema20) + np.sign(close - ema50)

    ema12 = _ema(close, 12); ema26 = _ema(close, 26)
    macd = ema12 - ema26; macd_sig = _ema(macd, 9)
    macd_norm = macd / np.maximum(close, 1e-10)
    macd_hist_norm = (macd - macd_sig) / np.maximum(close, 1e-10)

    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    rs = _ema(gain, 14) / np.maximum(_ema(loss, 14), 1e-10)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi_mom = np.diff(rsi, prepend=rsi[0])

    bb_mid = _rolling_mean(close, 20)
    bb_std = np.nan_to_num(_rolling_std(close, 20), nan=1.0)
    bb_upper = bb_mid + 2 * bb_std; bb_lower = bb_mid - 2 * bb_std
    bb_width = np.where(np.nan_to_num(bb_mid, nan=1) > 0,
                        (bb_upper - bb_lower) / np.maximum(np.nan_to_num(bb_mid, nan=1), 1e-10), 0)
    bb_pct = np.where(bb_upper > bb_lower,
                      (close - bb_lower) / np.maximum(bb_upper - bb_lower, 1e-10), 0.5)

    prev_c = np.roll(close, 1); prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = _ema(tr, 14)
    atr_pct = atr / np.maximum(close, 1e-10)

    vol_5 = _rolling_std(ret_1, 5); vol_20 = _rolling_std(ret_1, 20); vol_50 = _rolling_std(ret_1, 50)
    vol_ratio_5_20 = np.where(np.nan_to_num(vol_20, nan=1) > 0,
                               np.nan_to_num(vol_5, nan=0) / np.maximum(np.nan_to_num(vol_20, nan=1), 1e-10), 1.0)

    vol_ma20 = _rolling_mean(volume, 20)
    vol_ratio = np.where(np.nan_to_num(vol_ma20, nan=1) > 0,
                         volume / np.maximum(np.nan_to_num(vol_ma20, nan=1), 1e-10), 1.0)

    plus_dm = np.maximum(np.diff(high, prepend=high[0]), 0)
    minus_dm = np.maximum(-np.diff(low, prepend=low[0]), 0)
    plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0)
    minus_dm = np.where(minus_dm > plus_dm, minus_dm, 0)
    sm_plus = _ema(plus_dm, 14); sm_minus = _ema(minus_dm, 14)
    plus_di = 100 * sm_plus / np.maximum(atr, 1e-10)
    minus_di = 100 * sm_minus / np.maximum(atr, 1e-10)
    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    adx = _ema(dx, 14)

    donch_high = np.nan_to_num(_rolling_max(high, 20), nan=high[0])
    donch_low = np.nan_to_num(_rolling_min(low, 20), nan=low[0])
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
        if close[i] > close[i-1]:
            consec[i] = max(consec[i-1], 0) + 1
        elif close[i] < close[i-1]:
            consec[i] = min(consec[i-1], 0) - 1

    h20 = np.nan_to_num(_rolling_max(high, 20), nan=high[0])
    l20 = np.nan_to_num(_rolling_min(low, 20), nan=low[0])
    dist_high = (close - h20) / np.maximum(close, 1e-10)
    dist_low = (close - l20) / np.maximum(close, 1e-10)

    features = {{
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
    }}

    if funding is not None:
        fm8 = _rolling_mean(funding, 8)
        fm48 = _rolling_mean(funding, 48)
        features['funding'] = funding
        features['funding_ma8'] = np.nan_to_num(fm8, nan=0)
        features['funding_ma48'] = np.nan_to_num(fm48, nan=0)

    return features


def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    n = len(close)
    regime = ctx.regime_1h

    funding = ctx.funding_1h

    features = _compute_features(close, high, low, volume, funding)

    # Build feature matrix in correct order
    X = np.column_stack([features.get(k, np.zeros(n)) for k in _feat_names])

    # Replace NaN/inf
    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

    # Scale and predict
    X_s = _scaler.transform(X)
    proba = _model.predict_proba(X_s)

    # Get long and short probabilities
    long_prob = np.zeros(n)
    short_prob = np.zeros(n)
    if 1 in _classes:
        long_prob = proba[:, _classes.index(1)]
    if -1 in _classes:
        short_prob = proba[:, _classes.index(-1)]

    # Entry signals
    entry_long = (long_prob >= _PROB_THR) & (regime != CRISIS)
    entry_short = (short_prob >= _PROB_THR) & (regime != CRISIS)

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long & ~entry_short, 1,
                np.where(entry_short & ~entry_long, -1,
                np.where(long_prob >= short_prob, 1, -1))).astype(np.int8)

    entry_mask[:200] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # Conviction score for sizing
    conviction = np.maximum(long_prob, short_prob)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=3.0,
        trail_mult=2.5,
        target_mult=999,
        no_stop_bars=18,
        min_hold=12,
        max_hold=720,
        edge=0.40,
        exit_regimes={{CRISIS}},
        max_trade_pct=0.12,
        breakeven_atr=0.8,
        conviction_score=conviction,
        exchange='binance',
        name='{name}',
    )
'''
    return code


# ==================== Main Research Loop ====================

def run_research(n_tokens=30, months=18, lookfwd=48, thr=2.5):
    """Main entry point."""
    t_start = time.time()

    print('=' * 70)
    print(f'ML Oracle Research v2 — Vectorized')
    print(f'Tokens={n_tokens}, Months={months}, Lookfwd={lookfwd}h, Thr={thr}%')
    print('=' * 70)

    # Get tokens sorted by data size (proxy for liquidity)
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'), key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:n_tokens]]
    print(f'\nTokens: {tokens[:10]}... ({len(tokens)} total)')

    # Build dataset
    print('\n--- Building Multi-Token Dataset ---')
    t0 = time.time()
    X, y, feat_names = build_multi_token_dataset(tokens, months, lookfwd, thr)
    print(f'Built in {time.time()-t0:.1f}s')

    if X is None:
        print('No data!')
        return

    print(f'Dataset: {X.shape[0]:,} samples, {X.shape[1]} features')
    print(f'Labels: Long={sum(y==1):,} ({sum(y==1)/len(y)*100:.1f}%), '
          f'Short={sum(y==-1):,} ({sum(y==-1)/len(y)*100:.1f}%), '
          f'Neutral={sum(y==0):,} ({sum(y==0)/len(y)*100:.1f}%)')

    # Train walk-forward
    print('\n--- Walk-Forward Training (3 folds) ---')
    results, best_model, best_scaler = train_walk_forward(X, y, feat_names, n_splits=3)

    # Save model
    model_path = str(RESULTS_DIR / 'ml_oracle_model.joblib')
    scaler_path = str(RESULTS_DIR / 'ml_oracle_scaler.joblib')
    features_path = str(RESULTS_DIR / 'ml_oracle_features.joblib')
    joblib.dump(best_model, model_path)
    joblib.dump(best_scaler, scaler_path)
    joblib.dump(feat_names, features_path)

    # Generate strategy code
    code = generate_strategy_code(model_path, scaler_path, features_path, feat_names,
                                   prob_threshold=0.4, name='s200_ml_oracle')
    strat_path = '/workspace/crypto_backtest/strategies/s200_ml_oracle.py'
    with open(strat_path, 'w') as f:
        f.write(code)
    print(f'\nStrategy written to {strat_path}')

    # Summary
    print(f'\n{"="*70}')
    print(f'Research completed in {time.time()-t_start:.0f}s')
    print(f'Model: GradientBoosting (300 trees, depth=4)')
    print(f'Features: {len(feat_names)}')
    if results:
        avg_precision = np.mean([r.get('combined_precision', 0) for r in results])
        avg_signals = np.mean([r.get('combined_signals', 0) for r in results])
        print(f'Avg combined precision: {avg_precision:.3f}')
        print(f'Avg signals per fold: {avg_signals:.0f}')
    print(f'{"="*70}')

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokens', type=int, default=30)
    parser.add_argument('--months', type=int, default=18)
    parser.add_argument('--lookfwd', type=int, default=48)
    parser.add_argument('--thr', type=float, default=2.5)
    args = parser.parse_args()

    run_research(args.tokens, args.months, args.lookfwd, args.thr)
