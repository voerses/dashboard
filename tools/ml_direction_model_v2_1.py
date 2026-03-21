"""
ML Direction Model V2.1 — Phase 2 Improvements
================================================
Changes over V2:
  1. +1 feature: market_funding_mean (aggregate funding positioning)
  2. Adaptive threshold target (vol-normalized instead of fixed 3%)
  3. Per-regime precision reporting
  4. Sample weighting by recency (exponential decay)
  5. 42 total features (34 base + 8 market context)

This is an incremental test — compare against V2 baseline to isolate impact.
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
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')

from sklearn.ensemble import HistGradientBoostingClassifier
import joblib

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')


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


# ==================== Market Context Builder ====================

def build_market_context(data_dir, top_n=30):
    """Build shared market-level features. Now includes market_funding_mean."""
    t0 = time.time()
    print('Building market context (V2.1)...')

    btc = pd.read_parquet(data_dir / 'BTC_1h.parquet')
    btc_close = btc['close'].values.astype(np.float64)
    btc_idx = btc.index
    n_btc = len(btc_close)

    btc_ret_1h = np.zeros(n_btc)
    btc_ret_1h[1:] = np.log(btc_close[1:] / btc_close[:-1])
    btc_ret_24h = np.zeros(n_btc)
    btc_ret_24h[24:] = np.log(btc_close[24:] / btc_close[:-24])
    btc_vol_24h = np.nan_to_num(rolling_std_vec(btc_ret_1h, 24), nan=0.0)

    # Market regime from BTC daily
    btc_daily = btc.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close'])
    dc = btc_daily['close'].values.astype(np.float64)
    n_d = len(dc)
    d_ema20 = ema_vec(dc, 20); d_ema50 = ema_vec(dc, 50)
    d_ret = np.zeros(n_d); d_ret[1:] = np.log(dc[1:] / dc[:-1])
    d_vol = np.nan_to_num(rolling_std_vec(d_ret, 20), nan=0.0)
    d_high = btc_daily['high'].values.astype(np.float64)
    d_low = btc_daily['low'].values.astype(np.float64)
    d_prev_c = np.roll(dc, 1); d_prev_c[0] = dc[0]
    d_tr = np.maximum(d_high - d_low, np.maximum(np.abs(d_high - d_prev_c), np.abs(d_low - d_prev_c)))
    d_atr = ema_vec(d_tr, 14)
    d_pdm = np.maximum(np.diff(d_high, prepend=d_high[0]), 0)
    d_mdm = np.maximum(-np.diff(d_low, prepend=d_low[0]), 0)
    d_pdm = np.where(d_pdm > d_mdm, d_pdm, 0); d_mdm = np.where(d_mdm > d_pdm, d_mdm, 0)
    d_sp = ema_vec(d_pdm, 14); d_sm = ema_vec(d_mdm, 14)
    d_pdi = 100 * d_sp / np.maximum(d_atr, 1e-10)
    d_mdi = 100 * d_sm / np.maximum(d_atr, 1e-10)
    d_dx = 100 * np.abs(d_pdi - d_mdi) / np.maximum(d_pdi + d_mdi, 1e-10)
    d_adx = ema_vec(d_dx, 14)
    d_vol_p75 = pd.Series(d_vol).expanding(min_periods=30).quantile(0.75).values

    regime_daily = np.full(n_d, 3, dtype=np.int8)
    crisis = d_vol > np.nan_to_num(d_vol_p75, nan=1.0) * 2
    quiet = ~crisis & (d_vol < np.nan_to_num(d_vol_p75, nan=1.0) * 0.3)
    strong = ~crisis & ~quiet & (d_adx > 25)
    regime_daily[crisis] = 0; regime_daily[quiet] = 1
    regime_daily[strong & (d_ema20 > d_ema50)] = 2
    regime_daily[strong & ~(d_ema20 > d_ema50)] = 4

    regime_series = pd.Series(regime_daily, index=btc_daily.index)
    market_regime = regime_series.reindex(btc_idx, method='ffill').fillna(3).values.astype(np.float64)

    # Cross-asset features
    token_files = sorted(data_dir.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:top_n]]

    # Use float32 to save memory
    ret_24h_matrix = np.full((n_btc, top_n), np.nan, dtype=np.float32)
    above_ema20_matrix = np.full((n_btc, top_n), np.nan, dtype=np.float32)
    funding_matrix = np.full((n_btc, top_n), np.nan, dtype=np.float32)

    for i, token in enumerate(tokens):
        try:
            df = pd.read_parquet(data_dir / f'{token}_1h.parquet')
            aligned = pd.Series(df['close'].values.astype(np.float32),
                                index=df.index).reindex(btc_idx)
            ret_24h_matrix[:, i] = np.log(aligned / aligned.shift(24)).values
            e20 = aligned.ewm(span=20, adjust=False).mean()
            above_ema20_matrix[:, i] = (aligned > e20).astype(np.float32).values

            if 'funding_rate' in df.columns:
                fund = pd.Series(df['funding_rate'].values.astype(np.float32),
                                 index=df.index).reindex(btc_idx)
                funding_matrix[:, i] = fund.values
            del df, aligned
        except Exception:
            continue
    gc.collect()

    market_dispersion = np.nanstd(ret_24h_matrix, axis=1)
    disp_mean = np.nan_to_num(rolling_mean_vec(market_dispersion, 720), nan=0.0)
    disp_std = np.nan_to_num(rolling_std_vec(market_dispersion, 720), nan=1.0)
    dispersion_zscore = np.nan_to_num(
        (market_dispersion - disp_mean) / np.maximum(disp_std, 1e-10),
        nan=0.0, posinf=0.0, neginf=0.0)
    market_breadth = np.nan_to_num(np.nanmean(above_ema20_matrix, axis=1), nan=0.5)
    market_funding_mean = np.nan_to_num(np.nanmean(funding_matrix, axis=1), nan=0.0)

    # Free large intermediate matrices
    del ret_24h_matrix, above_ema20_matrix, funding_matrix; gc.collect()

    context = pd.DataFrame({
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        'market_funding_mean': market_funding_mean,  # NEW
        '_btc_ret_1h_series': btc_ret_1h,
    }, index=btc_idx)

    elapsed = time.time() - t0
    print(f'  Market context built: {len(context):,} bars, {elapsed:.1f}s')
    return context


# ==================== Feature Engineering ====================

def compute_features(close, high, low, volume, funding=None):
    """Compute 31-34 base TA features (unchanged from V1)."""
    n = len(close)
    def log_ret(shift):
        r = np.zeros(n); r[shift:] = np.log(close[shift:] / close[:-shift]); return r

    ret_1 = log_ret(1); ret_4 = log_ret(4); ret_12 = log_ret(12)
    ret_24 = log_ret(24); ret_48 = log_ret(48); ret_168 = log_ret(168)

    ema10 = ema_vec(close, 10); ema20 = ema_vec(close, 20); ema50 = ema_vec(close, 50)
    ema_dist_10 = (close - ema10) / np.maximum(close, 1e-10)
    ema_dist_20 = (close - ema20) / np.maximum(close, 1e-10)
    ema_dist_50 = (close - ema50) / np.maximum(close, 1e-10)
    ema_align = np.sign(close - ema10) + np.sign(close - ema20) + np.sign(close - ema50)

    ema12 = ema_vec(close, 12); ema26 = ema_vec(close, 26)
    macd = ema12 - ema26; macd_sig = ema_vec(macd, 9)
    macd_norm = macd / np.maximum(close, 1e-10)
    macd_hist_norm = (macd - macd_sig) / np.maximum(close, 1e-10)

    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0); loss = np.where(delta < 0, -delta, 0.0)
    rs = ema_vec(gain, 14) / np.maximum(ema_vec(loss, 14), 1e-10)
    rsi = 100 - 100 / (1 + rs); rsi_mom = np.diff(rsi, prepend=rsi[0])

    bb_mid = rolling_mean_vec(close, 20)
    bb_std = np.nan_to_num(rolling_std_vec(close, 20), nan=1.0)
    bb_upper = bb_mid + 2 * bb_std; bb_lower = bb_mid - 2 * bb_std
    bb_width = np.where(np.nan_to_num(bb_mid, nan=1) > 0,
                        (bb_upper - bb_lower) / np.maximum(np.nan_to_num(bb_mid, nan=1), 1e-10), 0)
    bb_pct = np.where(bb_upper > bb_lower,
                      (close - bb_lower) / np.maximum(bb_upper - bb_lower, 1e-10), 0.5)

    prev_c = np.roll(close, 1); prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = ema_vec(tr, 14); atr_pct = atr / np.maximum(close, 1e-10)

    vol_5 = rolling_std_vec(ret_1, 5); vol_20 = rolling_std_vec(ret_1, 20)
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
    sm_plus = ema_vec(plus_dm, 14); sm_minus = ema_vec(minus_dm, 14)
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

    consec = np.zeros(n)
    for i in range(1, n):
        if close[i] > prev_c[i]:
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
        'vol_50': np.nan_to_num(vol_50, nan=0), 'vol_ratio_5_20': np.nan_to_num(vol_ratio_5_20, nan=1),
        'vol_ratio': np.nan_to_num(vol_ratio, nan=1),
        'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'donch_pos': np.nan_to_num(donch_pos, nan=0.5),
        'body_pct': body_pct, 'wick_ratio': wick_ratio,
        'consec': consec, 'dist_high': dist_high, 'dist_low': dist_low,
    }
    if funding is not None:
        fm8 = rolling_mean_vec(funding, 8); fm48 = rolling_mean_vec(funding, 48)
        features['funding'] = funding
        features['funding_ma8'] = np.nan_to_num(fm8, nan=0)
        features['funding_ma48'] = np.nan_to_num(fm48, nan=0)
    return features


# ==================== Direction Labels ====================

def compute_direction_labels_fixed(close, horizon=24, threshold_pct=3.0):
    """Original fixed-threshold labels (for comparison)."""
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


def compute_direction_labels_adaptive(close, horizon=24, threshold_mult=1.5):
    """NEW: Adaptive vol-normalized threshold labels.

    threshold = threshold_mult * rolling_std(ret_1h) * sqrt(horizon)
    A +1 label means "moved more than 1.5 expected standard deviations."
    More balanced classes across volatility regimes.
    """
    n = len(close)
    labels = np.zeros(n, dtype=np.int8)
    if n <= horizon:
        return labels, np.zeros(n)

    ret_1h = np.zeros(n)
    ret_1h[1:] = np.log(close[1:] / close[:-1])

    # 7-day realized vol (causal, no lookahead)
    vol = np.nan_to_num(rolling_std_vec(ret_1h, 168), nan=0.02)
    # Ensure minimum threshold of 1% to avoid labeling noise as signal
    expected_move = np.maximum(vol * np.sqrt(horizon) * threshold_mult, 0.01)

    # Forward return
    fwd_ret = np.zeros(n)
    fwd_ret[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0

    labels[fwd_ret > expected_move] = 1
    labels[fwd_ret < -expected_move] = -1
    labels[-horizon:] = 0

    return labels, fwd_ret


# ==================== Dataset Building ====================

def load_token(token):
    fpath = DATA_DIR / f'{token}_1h.parquet'
    if not fpath.exists():
        return None
    return pd.read_parquet(fpath)

def get_tokens_by_data_size(n_tokens=50):
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    return [f.stem.replace('_1h', '') for f in token_files[:n_tokens]]


def build_token_features(token, horizon=24, target='adaptive', market_ctx=None):
    """Build features + labels for one token with market context.

    target: 'fixed' (3% threshold) or 'adaptive' (vol-normalized)
    """
    df = load_token(token)
    if df is None or len(df) < 500:
        return None, None, None

    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)
    funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None

    features = compute_features(close, high, low, volume, funding)

    if target == 'adaptive':
        labels, fwd_ret = compute_direction_labels_adaptive(close, horizon, threshold_mult=1.5)
    else:
        labels, fwd_ret = compute_direction_labels_fixed(close, horizon, threshold_pct=3.0)

    # Market context features
    if market_ctx is not None:
        n = len(close)
        ctx_aligned = market_ctx.reindex(df.index, method='ffill')

        features['btc_ret_1h'] = np.nan_to_num(ctx_aligned['btc_ret_1h'].values, nan=0.0)
        features['btc_ret_24h'] = np.nan_to_num(ctx_aligned['btc_ret_24h'].values, nan=0.0)
        features['btc_vol_24h'] = np.nan_to_num(ctx_aligned['btc_vol_24h'].values, nan=0.0)
        features['market_regime'] = np.nan_to_num(ctx_aligned['market_regime'].values, nan=3.0)
        features['market_breadth_ema20'] = np.nan_to_num(ctx_aligned['market_breadth_ema20'].values, nan=0.5)
        features['dispersion_zscore'] = np.nan_to_num(ctx_aligned['dispersion_zscore'].values, nan=0.0)
        features['market_funding_mean'] = np.nan_to_num(ctx_aligned['market_funding_mean'].values, nan=0.0)

        token_ret_1h = np.zeros(n)
        token_ret_1h[1:] = np.log(close[1:] / close[:-1])
        btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(
            pd.Series(btc_ret_aligned)).values
        features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)

    feat_names = sorted([k for k in features.keys() if not k.startswith('_')])
    X = np.column_stack([features[k] for k in feat_names])

    valid = np.ones(len(close), dtype=bool)
    valid[:200] = False; valid[-horizon:] = False
    nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
    valid = valid & ~nan_mask

    del df, close, high, low, volume, funding, features; gc.collect()
    return X[valid], labels[valid], feat_names


# ==================== Training ====================

def undersample_balance(X, y, rng):
    pos_idx = np.where(y == 1)[0]; neg_idx = np.where(y == -1)[0]
    n_min = min(len(pos_idx), len(neg_idx))
    if n_min == 0:
        return X, y
    pos_sample = rng.choice(pos_idx, n_min, replace=False)
    neg_sample = rng.choice(neg_idx, n_min, replace=False)
    return X[np.sort(np.concatenate([pos_sample, neg_sample]))], \
           y[np.sort(np.concatenate([pos_sample, neg_sample]))]


def compute_sample_weights(n_samples, decay_halflife=0.3):
    """Exponential recency weighting. Recent samples get higher weight.

    decay_halflife: fraction of data at which weight = 0.5 * max_weight.
    So decay_halflife=0.3 means the first 30% of data has weight ~0.5 of the last sample.
    """
    positions = np.linspace(0, 1, n_samples)  # 0 = oldest, 1 = newest
    # Exponential: w = exp(lambda * position), where lambda = ln(2) / (1 - halflife)
    lam = np.log(2) / (1 - decay_halflife)
    weights = np.exp(lam * (positions - 1))  # normalize so newest = 1
    return weights / weights.mean()  # mean-normalize so total weight ~ n_samples


def evaluate_direction(y_proba, y_test, classes, feat_names, model, model_type='',
                       market_regime_test=None):
    """Evaluate direction predictions with optional per-regime breakdown."""
    metrics = {}
    thresholds = [0.55, 0.60, 0.65, 0.70, 0.75]
    best_precision = 0.0

    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]

        print(f'\n  {label} predictions ({model_type}):')
        print(f'  {"Threshold":<12} {"N_preds":<10} {"Correct":<10} {"Precision":<12} {"Recall":<10}')
        print(f'  {"-"*54}')

        for thr in thresholds:
            signals = probs >= thr
            n_signals = signals.sum()
            if n_signals == 0:
                print(f'  p>={thr:.2f}       0')
                continue
            correct = (y_test[signals] == direction).sum()
            precision = correct / n_signals
            total_actual = (y_test == direction).sum()
            recall = correct / max(total_actual, 1)
            key = f'{label}_p{int(thr*100)}'
            metrics[key] = {'n_predictions': int(n_signals), 'precision': float(precision),
                            'recall': float(recall)}
            print(f'  p>={thr:.2f}       {n_signals:<10} {correct:<10} {precision:<12.4f} {recall:<10.4f}')
            if precision > best_precision:
                best_precision = precision

    metrics['best_precision'] = best_precision

    # Per-regime precision (if regime data available)
    if market_regime_test is not None:
        print(f'\n  Per-Regime Precision (p>=0.60):')
        regime_names = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}
        for r_val, r_name in regime_names.items():
            r_mask = market_regime_test == r_val
            if r_mask.sum() == 0:
                continue

            for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
                if direction not in classes:
                    continue
                cls_idx = classes.index(direction)
                probs = y_proba[:, cls_idx]
                signals = (probs >= 0.60) & r_mask
                n_sig = signals.sum()
                if n_sig == 0:
                    continue
                correct = (y_test[signals] == direction).sum()
                prec = correct / n_sig
                print(f'    {r_name:<12} {label:<6} n={n_sig:<6} prec={prec:.4f}')

    return metrics


def train_fold(X_train, y_train, X_test, y_test, feat_names, fold_name,
               use_recency_weights=False, market_regime_test=None):
    """Train and evaluate a single fold."""
    rng = np.random.RandomState(42)
    X_train_bal, y_train_bal = undersample_balance(X_train, y_train, rng)

    print(f'\n  {fold_name}: Train {len(y_train_bal):,} | Test {len(y_test):,}')

    t0 = time.time()
    model = HistGradientBoostingClassifier(
        max_iter=400, max_depth=6, learning_rate=0.05,
        min_samples_leaf=50, max_leaf_nodes=31,
        l2_regularization=1.0, max_bins=128,
        early_stopping=True, validation_fraction=0.15,
        n_iter_no_change=20, random_state=42, verbose=0,
    )

    if use_recency_weights:
        weights = compute_sample_weights(len(y_train_bal), decay_halflife=0.3)
        model.fit(X_train_bal, y_train_bal, sample_weight=weights)
    else:
        model.fit(X_train_bal, y_train_bal)

    train_time = time.time() - t0
    print(f'  Trained in {train_time:.1f}s (iter={model.n_iter_})')

    y_proba = model.predict_proba(X_test)
    classes = list(model.classes_)
    metrics = evaluate_direction(y_proba, y_test, classes, feat_names, model,
                                 fold_name, market_regime_test)
    metrics['train_time'] = train_time

    # Feature importances from tree splits
    n_features = model.n_features_in_
    imp = np.zeros(n_features)
    for tree_list in model._predictors:
        for tree in tree_list:
            for node in tree.nodes:
                if not node['is_leaf']:
                    feat_idx = node['feature_idx']
                    if 0 <= feat_idx < n_features:
                        imp[feat_idx] += 1
    if imp.sum() > 0:
        imp = imp / imp.sum()
    top_idx = np.argsort(imp)[::-1][:15]
    print(f'\n  Top 15 features:')
    for rank, i in enumerate(top_idx):
        print(f'    {rank+1:2d}. {feat_names[i]:<28s} {imp[i]:.4f}')

    return model, metrics


# ==================== Main ====================

def build_dataset_both_targets(train_tokens, horizon, market_ctx):
    """Build features once, compute both fixed and adaptive labels.
    Returns X (float32), y_fixed, y_adaptive, feat_names.
    Memory-efficient: uses float32 and processes tokens incrementally."""
    X_list, y_fixed_list, y_adapt_list = [], [], []
    feat_names = None

    for i, token in enumerate(train_tokens):
        df = load_token(token)
        if df is None or len(df) < 500:
            continue

        close = df['close'].values.astype(np.float64)
        high = df['high'].values.astype(np.float64)
        low = df['low'].values.astype(np.float64)
        volume = df['volume'].values.astype(np.float64)
        funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None

        features = compute_features(close, high, low, volume, funding)

        # Both label sets from same data
        labels_fixed, _ = compute_direction_labels_fixed(close, horizon, threshold_pct=3.0)
        labels_adapt, _ = compute_direction_labels_adaptive(close, horizon, threshold_mult=1.5)

        # Market context features
        if market_ctx is not None:
            n = len(close)
            ctx_aligned = market_ctx.reindex(df.index, method='ffill')
            features['btc_ret_1h'] = np.nan_to_num(ctx_aligned['btc_ret_1h'].values, nan=0.0)
            features['btc_ret_24h'] = np.nan_to_num(ctx_aligned['btc_ret_24h'].values, nan=0.0)
            features['btc_vol_24h'] = np.nan_to_num(ctx_aligned['btc_vol_24h'].values, nan=0.0)
            features['market_regime'] = np.nan_to_num(ctx_aligned['market_regime'].values, nan=3.0)
            features['market_breadth_ema20'] = np.nan_to_num(ctx_aligned['market_breadth_ema20'].values, nan=0.5)
            features['dispersion_zscore'] = np.nan_to_num(ctx_aligned['dispersion_zscore'].values, nan=0.0)
            features['market_funding_mean'] = np.nan_to_num(ctx_aligned['market_funding_mean'].values, nan=0.0)

            token_ret_1h = np.zeros(n)
            token_ret_1h[1:] = np.log(close[1:] / close[:-1])
            btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
            corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(
                pd.Series(btc_ret_aligned)).values
            features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)
            del ctx_aligned

        fn = sorted([k for k in features.keys() if not k.startswith('_')])
        X = np.column_stack([features[k] for k in fn]).astype(np.float32)

        valid = np.ones(len(close), dtype=bool)
        valid[:200] = False; valid[-horizon:] = False
        nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
        valid = valid & ~nan_mask

        if valid.sum() > 100:
            X_list.append(X[valid])
            y_fixed_list.append(labels_fixed[valid])
            y_adapt_list.append(labels_adapt[valid])
            if feat_names is None:
                feat_names = fn

        del df, close, high, low, volume, funding, features, X
        gc.collect()

        if (i + 1) % 5 == 0:
            print(f'  Loaded {i+1}/{len(train_tokens)} tokens')

    X_all = np.vstack(X_list).astype(np.float32)
    y_fixed_all = np.concatenate(y_fixed_list)
    y_adapt_all = np.concatenate(y_adapt_list)
    del X_list, y_fixed_list, y_adapt_list; gc.collect()

    print(f'  Total: {len(y_fixed_all):,} samples, {len(feat_names)} features, {X_all.nbytes/1e6:.0f}MB')
    return X_all, y_fixed_all, y_adapt_all, feat_names


def run_v2_1_research():
    t_start = time.time()
    print('=' * 70)
    print('ML DIRECTION MODEL V2.1 RESEARCH')
    print('Phase 2: Adaptive Target + Funding Feature + Recency Weighting')
    print('=' * 70)

    # Use top_n=15 for market context to save memory (fewer intermediate matrices)
    market_ctx = build_market_context(DATA_DIR, top_n=15)

    all_tokens = get_tokens_by_data_size(50)
    train_tokens = all_tokens[:15]  # 15 tokens fits in memory (was 30)

    horizon = 24

    # Build features once, get both label sets
    print(f'\nBuilding dataset ({len(train_tokens)} tokens, both label sets)...')
    X_all, y_fixed, y_adapt, feat_names = build_dataset_both_targets(
        train_tokens, horizon, market_ctx)
    del market_ctx; gc.collect()

    regime_col_idx = feat_names.index('market_regime') if 'market_regime' in feat_names else None

    # ==========================================
    # Experiment A: V2 baseline (fixed target)
    # ==========================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT A: V2 Baseline (fixed 3% target)')
    print(f'{"#"*70}')

    mask_nz = (y_fixed == 1) | (y_fixed == -1)
    X_nz = X_all[mask_nz]; y_nz = y_fixed[mask_nz]
    n = len(y_nz)
    print(f'Fixed target: {n:,} non-neutral, {len(feat_names)} features')
    print(f'  LONG: {(y_nz==1).sum():,} ({(y_nz==1).sum()/n*100:.1f}%)')
    print(f'  SHORT: {(y_nz==-1).sum():,} ({(y_nz==-1).sum()/n*100:.1f}%)')

    train_sl = slice(0, int(n * 0.67)); test_sl = slice(int(n * 0.67), n)
    regime_test = X_nz[test_sl, regime_col_idx] if regime_col_idx is not None else None

    _, metrics_a = train_fold(X_nz[train_sl], y_nz[train_sl],
                              X_nz[test_sl], y_nz[test_sl],
                              feat_names, 'A: V2 baseline',
                              market_regime_test=regime_test)
    del X_nz, y_nz, regime_test; gc.collect()

    # ==========================================
    # Experiment B: Adaptive target
    # ==========================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT B: Adaptive target (vol-normalized, 1.5 sigma)')
    print(f'{"#"*70}')

    mask_nz_b = (y_adapt == 1) | (y_adapt == -1)
    X_nz_b = X_all[mask_nz_b]; y_nz_b = y_adapt[mask_nz_b]
    n_b = len(y_nz_b)
    print(f'Adaptive target: {n_b:,} non-neutral, {len(feat_names)} features')
    print(f'  LONG: {(y_nz_b==1).sum():,} ({(y_nz_b==1).sum()/n_b*100:.1f}%)')
    print(f'  SHORT: {(y_nz_b==-1).sum():,} ({(y_nz_b==-1).sum()/n_b*100:.1f}%)')

    train_sl_b = slice(0, int(n_b * 0.67)); test_sl_b = slice(int(n_b * 0.67), n_b)
    regime_test_b = X_nz_b[test_sl_b, regime_col_idx] if regime_col_idx is not None else None

    _, metrics_b = train_fold(X_nz_b[train_sl_b], y_nz_b[train_sl_b],
                              X_nz_b[test_sl_b], y_nz_b[test_sl_b],
                              feat_names, 'B: Adaptive target',
                              market_regime_test=regime_test_b)

    # ==========================================
    # Experiment C: Adaptive target + recency weighting
    # ==========================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT C: Adaptive target + recency weighting')
    print(f'{"#"*70}')

    _, metrics_c = train_fold(X_nz_b[train_sl_b], y_nz_b[train_sl_b],
                              X_nz_b[test_sl_b], y_nz_b[test_sl_b],
                              feat_names, 'C: Adaptive + recency',
                              use_recency_weights=True,
                              market_regime_test=regime_test_b)

    # Free data before comparison
    del X_all, y_fixed, y_adapt, X_nz_b, y_nz_b; gc.collect()

    # ==========================================
    # Comparison Table
    # ==========================================
    print(f'\n{"="*70}')
    print('COMPARISON: V2 vs V2.1 Experiments')
    print(f'{"="*70}')
    print(f'{"Experiment":<35} {"LONG p60":>10} {"LONG p70":>10} {"SHORT p60":>10} {"SHORT p70":>10}')
    print(f'{"-"*75}')

    for name, m in [('A: V2 baseline (fixed 3%)', metrics_a),
                    ('B: Adaptive target', metrics_b),
                    ('C: Adaptive + recency', metrics_c)]:
        lp60 = m.get('LONG_p60', {}).get('precision', 0)
        lp70 = m.get('LONG_p70', {}).get('precision', 0)
        sp60 = m.get('SHORT_p60', {}).get('precision', 0)
        sp70 = m.get('SHORT_p70', {}).get('precision', 0)
        print(f'{name:<35} {lp60:>10.4f} {lp70:>10.4f} {sp60:>10.4f} {sp70:>10.4f}')

    # Determine best
    best_exp = 'A'
    best_prec = metrics_a.get('best_precision', 0)
    for exp, m in [('B', metrics_b), ('C', metrics_c)]:
        if m.get('best_precision', 0) > best_prec:
            best_prec = m.get('best_precision', 0)
            best_exp = exp

    print(f'\nBest experiment: {best_exp} (precision={best_prec:.4f})')

    if best_exp in ('B', 'C'):
        print('V2.1 improvements show lift over V2 baseline')
        print('NOTE: Would save V2.1 model but skipping retrain to save memory')
        print('If lift is significant, retrain separately with full 30-token dataset')
    else:
        print('V2.1 did not improve on V2 baseline — keeping V2 model')

    elapsed = time.time() - t_start
    print(f'\nTotal research time: {elapsed:.0f}s')
    print(f'{"="*70}')


if __name__ == '__main__':
    run_v2_1_research()
