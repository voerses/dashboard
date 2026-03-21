"""
ML Oracle Research Pipeline
============================
1. Find retrospectively perfect entries (oracle analysis)
2. Extract rich feature sets at every bar
3. Train sklearn GradientBoosting to predict entry quality
4. Walk-forward validated
5. Deploy as strategy

Target: 300%+ returns, DD <20%, Calmar >3
"""

import numpy as np
import pandas as pd
import os
import sys
import json
import gc
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')
sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')

from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, precision_score, recall_score
from scipy import stats
import joblib

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')


def compute_indicators(close, high, low, volume, funding=None):
    """Compute technical indicators from raw OHLCV. Returns dict of arrays."""
    n = len(close)

    def ema(arr, span):
        alpha = 2.0 / (span + 1)
        out = np.empty_like(arr, dtype=np.float64)
        out[0] = arr[0]
        for i in range(1, len(arr)):
            out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
        return out

    def rolling_std(arr, w):
        out = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(w - 1, len(arr)):
            out[i] = np.std(arr[i - w + 1:i + 1], ddof=1)
        return out

    def rolling_mean(arr, w):
        cs = np.cumsum(arr)
        out = np.full_like(arr, np.nan, dtype=np.float64)
        out[w - 1] = cs[w - 1] / w
        out[w:] = (cs[w:] - cs[:-w]) / w
        return out

    def rolling_max(arr, w):
        out = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(w - 1, len(arr)):
            out[i] = np.max(arr[i - w + 1:i + 1])
        return out

    def rolling_min(arr, w):
        out = np.full_like(arr, np.nan, dtype=np.float64)
        for i in range(w - 1, len(arr)):
            out[i] = np.min(arr[i - w + 1:i + 1])
        return out

    # Returns at multiple horizons
    ret_1 = np.zeros(n)
    ret_1[1:] = np.log(close[1:] / close[:-1])

    ret_4 = np.zeros(n)
    ret_4[4:] = np.log(close[4:] / close[:-4])

    ret_12 = np.zeros(n)
    ret_12[12:] = np.log(close[12:] / close[:-12])

    ret_24 = np.zeros(n)
    ret_24[24:] = np.log(close[24:] / close[:-24])

    ret_48 = np.zeros(n)
    ret_48[48:] = np.log(close[48:] / close[:-48])

    ret_168 = np.zeros(n)
    ret_168[168:] = np.log(close[168:] / close[:-168])

    # EMAs
    ema_10 = ema(close, 10)
    ema_20 = ema(close, 20)
    ema_50 = ema(close, 50)

    # EMA distances (normalized)
    ema_dist_10 = (close - ema_10) / close
    ema_dist_20 = (close - ema_20) / close
    ema_dist_50 = (close - ema_50) / close

    # EMA alignment score (-3 to +3)
    ema_align = np.sign(close - ema_10) + np.sign(close - ema_20) + np.sign(close - ema_50)

    # MACD
    ema_12 = ema(close, 12)
    ema_26 = ema(close, 26)
    macd = ema_12 - ema_26
    macd_signal = ema(macd, 9)
    macd_hist = macd - macd_signal
    # Normalize MACD
    macd_norm = macd / close
    macd_hist_norm = macd_hist / close

    # RSI
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = ema(gain, 14)
    avg_loss = ema(loss, 14)
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100.0 - 100.0 / (1.0 + rs)

    # RSI momentum (change in RSI)
    rsi_mom = np.zeros(n)
    rsi_mom[1:] = rsi[1:] - rsi[:-1]

    # Bollinger Bands
    bb_mid = rolling_mean(close, 20)
    bb_std_raw = rolling_std(close, 20)
    bb_std_raw = np.nan_to_num(bb_std_raw, nan=1.0)
    bb_upper = bb_mid + 2 * bb_std_raw
    bb_lower = bb_mid - 2 * bb_std_raw
    bb_width = np.where(bb_mid > 0, (bb_upper - bb_lower) / bb_mid, 0)
    bb_pct = np.where(bb_upper > bb_lower,
                      (close - bb_lower) / np.maximum(bb_upper - bb_lower, 1e-10), 0.5)

    # ATR
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    tr[0] = high[0] - low[0]
    atr = ema(tr, 14)
    atr_pct = atr / np.maximum(close, 1e-10)

    # Volatility at multiple windows
    vol_5 = rolling_std(ret_1, 5)
    vol_20 = rolling_std(ret_1, 20)
    vol_50 = rolling_std(ret_1, 50)
    vol_ratio_5_20 = np.where(vol_20 > 0, vol_5 / np.maximum(vol_20, 1e-10), 1.0)

    # Volume features
    vol_ma_20 = rolling_mean(volume, 20)
    vol_ratio = np.where(vol_ma_20 > 0, volume / np.maximum(vol_ma_20, 1e-10), 1.0)

    # ADX (simplified)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
    smooth_plus = ema(plus_dm, 14)
    smooth_minus = ema(minus_dm, 14)
    plus_di = 100 * smooth_plus / np.maximum(atr, 1e-10)
    minus_di = 100 * smooth_minus / np.maximum(atr, 1e-10)
    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    adx = ema(dx, 14)

    # Donchian channels (20 period)
    donch_high = rolling_max(high, 20)
    donch_low = rolling_min(low, 20)
    donch_mid = (donch_high + donch_low) / 2
    donch_pos = np.where(donch_high > donch_low,
                         (close - donch_low) / np.maximum(donch_high - donch_low, 1e-10), 0.5)

    # Bar patterns
    body = close - np.roll(close, 1)
    body[0] = 0
    body_pct = body / np.maximum(close, 1e-10)
    upper_wick = high - np.maximum(close, np.roll(close, 1))
    upper_wick[0] = 0
    lower_wick = np.minimum(close, np.roll(close, 1)) - low
    lower_wick[0] = 0
    candle_range = high - low
    wick_ratio = np.where(candle_range > 0,
                          (upper_wick - lower_wick) / np.maximum(candle_range, 1e-10), 0)

    # Consecutive up/down bars
    consec = np.zeros(n)
    for i in range(1, n):
        if close[i] > close[i - 1]:
            consec[i] = max(consec[i - 1], 0) + 1
        elif close[i] < close[i - 1]:
            consec[i] = min(consec[i - 1], 0) - 1

    # Distance from recent high/low
    high_20 = rolling_max(high, 20)
    low_20 = rolling_min(low, 20)
    dist_from_high = (close - high_20) / np.maximum(close, 1e-10)
    dist_from_low = (close - low_20) / np.maximum(close, 1e-10)

    features = {
        'ret_1': ret_1, 'ret_4': ret_4, 'ret_12': ret_12,
        'ret_24': ret_24, 'ret_48': ret_48, 'ret_168': ret_168,
        'ema_dist_10': ema_dist_10, 'ema_dist_20': ema_dist_20, 'ema_dist_50': ema_dist_50,
        'ema_align': ema_align,
        'macd_norm': macd_norm, 'macd_hist_norm': macd_hist_norm,
        'rsi': rsi, 'rsi_mom': rsi_mom,
        'bb_pct': bb_pct, 'bb_width': bb_width,
        'atr_pct': atr_pct,
        'vol_5': np.nan_to_num(vol_5, nan=0),
        'vol_20': np.nan_to_num(vol_20, nan=0),
        'vol_50': np.nan_to_num(vol_50, nan=0),
        'vol_ratio_5_20': np.nan_to_num(vol_ratio_5_20, nan=1),
        'vol_ratio': np.nan_to_num(vol_ratio, nan=1),
        'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'donch_pos': np.nan_to_num(donch_pos, nan=0.5),
        'body_pct': body_pct, 'wick_ratio': wick_ratio,
        'consec': consec,
        'dist_from_high': np.nan_to_num(dist_from_high, nan=0),
        'dist_from_low': np.nan_to_num(dist_from_low, nan=0),
    }

    if funding is not None:
        fund_ma_8 = rolling_mean(funding, 8)
        fund_ma_48 = rolling_mean(funding, 48)
        features['funding'] = funding
        features['funding_ma_8'] = np.nan_to_num(fund_ma_8, nan=0)
        features['funding_ma_48'] = np.nan_to_num(fund_ma_48, nan=0)

    return features


def compute_labels(close, high, low, lookfwd=48, long_thr=2.0, short_thr=2.0, mae_limit=1.5):
    """Label bars: 1=good long, -1=good short, 0=neutral.

    A 'good' entry has:
    - Max favorable excursion > threshold within lookfwd bars
    - Max adverse excursion < mae_limit before the favorable target is hit
    """
    n = len(close)
    labels = np.zeros(n, dtype=np.int8)
    fwd_ret = np.zeros(n)

    for i in range(n - lookfwd):
        future_c = close[i + 1:i + 1 + lookfwd]
        future_h = high[i + 1:i + 1 + lookfwd]
        future_l = low[i + 1:i + 1 + lookfwd]

        # Long opportunity
        max_up = (future_h.max() / close[i] - 1) * 100
        max_down = (future_l.min() / close[i] - 1) * 100

        # Forward close-to-close return for regression target
        fwd_ret[i] = (future_c[-1] / close[i] - 1) * 100

        # Check if favorable excursion reached BEFORE adverse limit
        if max_up >= long_thr:
            # When did we first hit the long target?
            first_target = None
            worst_before = 0
            for j in range(lookfwd):
                bar_low_pct = (low[i + 1 + j] / close[i] - 1) * 100
                bar_high_pct = (high[i + 1 + j] / close[i] - 1) * 100
                if worst_before > -mae_limit and bar_high_pct >= long_thr:
                    first_target = j
                    break
                worst_before = min(worst_before, bar_low_pct)
            if first_target is not None:
                labels[i] = 1

        if max_down <= -short_thr and labels[i] == 0:
            # When did we first hit the short target?
            first_target = None
            worst_before = 0
            for j in range(lookfwd):
                bar_high_pct = (high[i + 1 + j] / close[i] - 1) * 100
                bar_low_pct = (low[i + 1 + j] / close[i] - 1) * 100
                if worst_before < mae_limit and bar_low_pct <= -short_thr:
                    first_target = j
                    break
                worst_before = max(worst_before, bar_high_pct)
            if first_target is not None:
                labels[i] = -1

    return labels, fwd_ret


def load_token_data(token, months=18):
    """Load token data, return close/high/low/volume/funding arrays."""
    fpath = DATA_DIR / f'{token}_1h.parquet'
    if not fpath.exists():
        return None
    df = pd.read_parquet(fpath)
    if months > 0:
        cutoff = len(df) - months * 730  # ~730 bars per month
        if cutoff > 0:
            df = df.iloc[cutoff:]
    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)
    funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None
    index = df.index
    return close, high, low, volume, funding, index


def build_dataset(tokens, months=18, lookfwd=48, long_thr=2.5, short_thr=2.5, mae_limit=1.5):
    """Build feature matrix and labels across multiple tokens."""
    all_X = []
    all_y = []
    all_meta = []  # (token, bar_index) for tracking

    for token in tokens:
        data = load_token_data(token, months=months)
        if data is None:
            continue
        close, high, low, volume, funding, index = data
        if len(close) < 500:
            continue

        features = compute_indicators(close, high, low, volume, funding)
        labels, fwd_ret = compute_labels(close, high, low, lookfwd, long_thr, short_thr, mae_limit)

        # Convert features dict to matrix
        feat_names = sorted(features.keys())
        X = np.column_stack([features[k] for k in feat_names])

        # Skip warmup (first 200 bars) and last lookfwd bars
        valid = np.ones(len(close), dtype=bool)
        valid[:200] = False
        valid[-lookfwd:] = False

        # Remove NaN rows
        nan_mask = np.any(np.isnan(X), axis=1)
        valid = valid & ~nan_mask

        all_X.append(X[valid])
        all_y.append(labels[valid])
        all_meta.extend([(token, i) for i in range(len(close)) if valid[i]])

        del X, labels, features, close, high, low, volume, funding
        gc.collect()

    if not all_X:
        return None, None, None, None

    X = np.vstack(all_X)
    y = np.concatenate(all_y)
    feat_names = sorted(compute_indicators(
        np.ones(300), np.ones(300), np.ones(300), np.ones(300), np.ones(300)
    ).keys())

    return X, y, feat_names, all_meta


def walk_forward_train_eval(X, y, meta, feat_names, train_frac=0.7):
    """Walk-forward: train on first 70%, evaluate on last 30%."""
    n = len(y)
    split = int(n * train_frac)

    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # Class balance
    n_pos = (y_train == 1).sum()
    n_neg = (y_train == -1).sum()
    n_zero = (y_train == 0).sum()
    print(f'  Train: {len(y_train)} bars | Long={n_pos} ({n_pos / len(y_train) * 100:.1f}%) | '
          f'Short={n_neg} ({n_neg / len(y_train) * 100:.1f}%) | Neutral={n_zero}')

    n_pos_t = (y_test == 1).sum()
    n_neg_t = (y_test == -1).sum()
    n_zero_t = (y_test == 0).sum()
    print(f'  Test:  {len(y_test)} bars | Long={n_pos_t} ({n_pos_t / len(y_test) * 100:.1f}%) | '
          f'Short={n_neg_t} ({n_neg_t / len(y_test) * 100:.1f}%) | Neutral={n_zero_t}')

    # Subsample neutral class for balance (keep all longs/shorts)
    pos_idx = np.where(y_train == 1)[0]
    neg_idx = np.where(y_train == -1)[0]
    zero_idx = np.where(y_train == 0)[0]

    # Downsample neutral to 2x the max of long/short
    target_neutral = min(len(zero_idx), max(len(pos_idx), len(neg_idx)) * 2)
    if target_neutral < len(zero_idx):
        rng = np.random.RandomState(42)
        zero_sample = rng.choice(zero_idx, target_neutral, replace=False)
    else:
        zero_sample = zero_idx

    balanced_idx = np.sort(np.concatenate([pos_idx, neg_idx, zero_sample]))
    X_bal = X_train[balanced_idx]
    y_bal = y_train[balanced_idx]
    print(f'  Balanced train: {len(y_bal)} (Long={sum(y_bal == 1)}, Short={sum(y_bal == -1)}, Neutral={sum(y_bal == 0)})')

    # Scale features
    scaler = StandardScaler()
    X_bal_s = scaler.fit_transform(X_bal)
    X_test_s = scaler.transform(X_test)

    # Train GradientBoosting
    print('  Training GradientBoosting...')
    model = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        min_samples_leaf=50,
        random_state=42,
    )
    model.fit(X_bal_s, y_bal)

    # Predictions
    y_pred = model.predict(X_test_s)
    y_proba = model.predict_proba(X_test_s)

    # Get class order
    classes = list(model.classes_)
    print(f'  Classes: {classes}')

    # Evaluate
    print('\n  === Test Set Performance ===')
    for cls, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if cls in classes:
            idx = classes.index(cls)
            pred_cls = y_pred == cls
            actual_cls = y_test == cls
            if pred_cls.sum() > 0:
                precision = (y_test[pred_cls] == cls).sum() / pred_cls.sum()
                recall = (y_test[pred_cls] == cls).sum() / max(actual_cls.sum(), 1)
                print(f'  {label}: Predicted={pred_cls.sum()}, Precision={precision:.3f}, Recall={recall:.3f}')
            else:
                print(f'  {label}: No predictions')

    # Feature importances
    imp = model.feature_importances_
    top_idx = np.argsort(imp)[::-1][:15]
    print('\n  Top 15 Features:')
    for i, idx in enumerate(top_idx):
        print(f'    {i + 1}. {feat_names[idx]}: {imp[idx]:.4f}')

    return model, scaler, classes, y_pred, y_proba, X_test_s, y_test


def evaluate_as_strategy(y_pred, y_proba, y_test, classes, min_prob=0.5):
    """Evaluate ML predictions as if they were trading signals."""
    print(f'\n  === Strategy Evaluation (min_prob={min_prob}) ===')

    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]

        # Filter by probability threshold
        signals = probs >= min_prob
        n_signals = signals.sum()
        if n_signals == 0:
            print(f'  {label}: No signals at prob >= {min_prob}')
            continue

        # How many of these were actually good entries?
        correct = (y_test[signals] == direction).sum()
        precision = correct / n_signals

        # Also check: of the signals, how many would have been profitable?
        # (labels == direction means the oracle says this was a good entry)
        neutral_as_signal = (y_test[signals] == 0).sum()

        print(f'  {label}: {n_signals} signals, Precision={precision:.3f} '
              f'(correct={correct}, neutral={neutral_as_signal}, wrong={n_signals - correct - neutral_as_signal})')

    # Combined: what if we took all signals?
    for prob_thr in [0.3, 0.4, 0.5, 0.6, 0.7]:
        total_signals = 0
        correct_signals = 0
        for direction in [1, -1]:
            if direction not in classes:
                continue
            cls_idx = classes.index(direction)
            probs = y_proba[:, cls_idx]
            signals = probs >= prob_thr
            total_signals += signals.sum()
            correct_signals += (y_test[signals] == direction).sum()
        if total_signals > 0:
            print(f'  Prob>={prob_thr}: {total_signals} signals, '
                  f'Precision={correct_signals / total_signals:.3f}, '
                  f'Signals/day={(total_signals / (len(y_test) / 24)):.1f}')


def run_research(n_tokens=30, months=18, lookfwd=48, long_thr=2.5, short_thr=2.5):
    """Main research loop."""
    print('=' * 70)
    print(f'ML Oracle Research Pipeline')
    print(f'Tokens={n_tokens}, Months={months}, Lookfwd={lookfwd}h, '
          f'Long_thr={long_thr}%, Short_thr={short_thr}%')
    print('=' * 70)

    # Get top tokens by file size (proxy for data availability/liquidity)
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'), key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:n_tokens]]
    print(f'\nTokens: {tokens[:10]}... ({len(tokens)} total)')

    # Build dataset
    print('\n--- Building Dataset ---')
    X, y, feat_names, meta = build_dataset(
        tokens, months=months, lookfwd=lookfwd,
        long_thr=long_thr, short_thr=short_thr, mae_limit=1.5
    )
    if X is None:
        print('No data!')
        return

    print(f'\nDataset: {X.shape[0]} samples, {X.shape[1]} features')
    print(f'Labels: Long={sum(y == 1)}, Short={sum(y == -1)}, Neutral={sum(y == 0)}')
    print(f'Label rates: Long={sum(y == 1) / len(y) * 100:.1f}%, Short={sum(y == -1) / len(y) * 100:.1f}%')

    # Walk-forward train/eval
    print('\n--- Walk-Forward Training ---')
    model, scaler, classes, y_pred, y_proba, X_test, y_test = walk_forward_train_eval(
        X, y, meta, feat_names
    )

    # Evaluate as strategy
    evaluate_as_strategy(y_pred, y_proba, y_test, classes)

    # Save model
    model_path = RESULTS_DIR / 'ml_oracle_model.joblib'
    scaler_path = RESULTS_DIR / 'ml_oracle_scaler.joblib'
    joblib.dump(model, model_path)
    joblib.dump(scaler, scaler_path)
    joblib.dump(feat_names, RESULTS_DIR / 'ml_oracle_features.joblib')
    print(f'\nModel saved to {model_path}')

    return model, scaler, feat_names, classes


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokens', type=int, default=30)
    parser.add_argument('--months', type=int, default=18)
    parser.add_argument('--lookfwd', type=int, default=48)
    parser.add_argument('--long-thr', type=float, default=2.5)
    parser.add_argument('--short-thr', type=float, default=2.5)
    args = parser.parse_args()

    run_research(
        n_tokens=args.tokens,
        months=args.months,
        lookfwd=args.lookfwd,
        long_thr=args.long_thr,
        short_thr=args.short_thr,
    )
