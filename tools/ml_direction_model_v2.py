"""
ML Direction Model V2 — Market-Context-Aware Direction Prediction
==================================================================
Improvements over V1:
  1. 7 new market-context features (BTC returns, market regime, breadth,
     dispersion, token-BTC correlation)
  2. HistGradientBoostingClassifier instead of RandomForest
  3. 3-fold expanding walk-forward validation
  4. Baseline RF comparison on Fold 3

Labeling unchanged: +1 if >3% in 24h, -1 if <-3%, 0 neutral.
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

from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
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


# ==================== Market Context Builder (NEW) ====================

def build_market_context(data_dir, top_n=30):
    """Build shared market-level features once before the token loop.

    Returns DataFrame indexed by DatetimeIndex with columns:
    - btc_ret_1h, btc_ret_24h, btc_vol_24h
    - market_regime (int 0-4)
    - market_breadth_ema20 (float 0-1)
    - dispersion_zscore (float)
    - _btc_ret_1h_series (private, for per-token corr)
    """
    t0 = time.time()
    print('Building market context...')

    # --- Load BTC ---
    btc = pd.read_parquet(data_dir / 'BTC_1h.parquet')
    btc_close = btc['close'].values.astype(np.float64)
    btc_idx = btc.index
    n_btc = len(btc_close)

    # BTC returns
    btc_ret_1h = np.zeros(n_btc)
    btc_ret_1h[1:] = np.log(btc_close[1:] / btc_close[:-1])

    btc_ret_24h = np.zeros(n_btc)
    btc_ret_24h[24:] = np.log(btc_close[24:] / btc_close[:-24])

    # BTC realized vol (24h window)
    btc_vol_24h = np.nan_to_num(rolling_std_vec(btc_ret_1h, 24), nan=0.0)

    # --- Market regime from BTC (inline, no engine import) ---
    # Resample to daily
    btc_daily = btc.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close'])

    dc = btc_daily['close'].values.astype(np.float64)
    n_d = len(dc)

    # Compute daily ADX + EMAs for regime
    d_ema20 = ema_vec(dc, 20)
    d_ema50 = ema_vec(dc, 50)

    d_ret = np.zeros(n_d)
    d_ret[1:] = np.log(dc[1:] / dc[:-1])
    d_vol = np.nan_to_num(rolling_std_vec(d_ret, 20), nan=0.0)

    # ADX on daily
    d_high = btc_daily['high'].values.astype(np.float64)
    d_low = btc_daily['low'].values.astype(np.float64)
    d_prev_c = np.roll(dc, 1); d_prev_c[0] = dc[0]
    d_tr = np.maximum(d_high - d_low, np.maximum(np.abs(d_high - d_prev_c), np.abs(d_low - d_prev_c)))
    d_atr = ema_vec(d_tr, 14)
    d_pdm = np.maximum(np.diff(d_high, prepend=d_high[0]), 0)
    d_mdm = np.maximum(-np.diff(d_low, prepend=d_low[0]), 0)
    d_pdm = np.where(d_pdm > d_mdm, d_pdm, 0)
    d_mdm = np.where(d_mdm > d_pdm, d_mdm, 0)
    d_sp = ema_vec(d_pdm, 14)
    d_sm = ema_vec(d_mdm, 14)
    d_pdi = 100 * d_sp / np.maximum(d_atr, 1e-10)
    d_mdi = 100 * d_sm / np.maximum(d_atr, 1e-10)
    d_dx = 100 * np.abs(d_pdi - d_mdi) / np.maximum(d_pdi + d_mdi, 1e-10)
    d_adx = ema_vec(d_dx, 14)

    # Regime classification
    d_vol_p75 = pd.Series(d_vol).expanding(min_periods=30).quantile(0.75).values
    regime_daily = np.full(n_d, 3, dtype=np.int8)  # default RANGE=3
    crisis = d_vol > np.nan_to_num(d_vol_p75, nan=1.0) * 2
    quiet = ~crisis & (d_vol < np.nan_to_num(d_vol_p75, nan=1.0) * 0.3)
    strong = ~crisis & ~quiet & (d_adx > 25)
    uptrend = strong & (d_ema20 > d_ema50)
    downtrend = strong & ~uptrend
    regime_daily[crisis] = 0   # CRISIS
    regime_daily[quiet] = 1    # QUIET
    regime_daily[uptrend] = 2  # UPTREND
    regime_daily[downtrend] = 4  # DOWNTREND

    # Forward-fill daily regime to hourly
    regime_series = pd.Series(regime_daily, index=btc_daily.index)
    market_regime = regime_series.reindex(btc_idx, method='ffill').fillna(3).values.astype(np.float64)

    # --- Load top-N tokens for breadth + dispersion ---
    token_files = sorted(data_dir.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:top_n]]

    ret_24h_matrix = np.full((n_btc, top_n), np.nan)
    above_ema20_matrix = np.full((n_btc, top_n), np.nan)

    for i, token in enumerate(tokens):
        try:
            df = pd.read_parquet(data_dir / f'{token}_1h.parquet')
            aligned = pd.Series(df['close'].values.astype(np.float64),
                                index=df.index).reindex(btc_idx)

            # 24h log return
            ret_24h = np.log(aligned / aligned.shift(24)).values
            ret_24h_matrix[:, i] = ret_24h

            # Close vs EMA20
            e20 = aligned.ewm(span=20, adjust=False).mean()
            above_ema20_matrix[:, i] = (aligned > e20).astype(float).values
        except Exception:
            continue

    # Market dispersion = cross-sectional std of 24h returns
    market_dispersion = np.nanstd(ret_24h_matrix, axis=1)
    # Z-score against 720-bar (30-day) rolling mean/std
    disp_mean = np.nan_to_num(rolling_mean_vec(market_dispersion, 720), nan=0.0)
    disp_std = np.nan_to_num(rolling_std_vec(market_dispersion, 720), nan=1.0)
    dispersion_zscore = (market_dispersion - disp_mean) / np.maximum(disp_std, 1e-10)
    dispersion_zscore = np.nan_to_num(dispersion_zscore, nan=0.0, posinf=0.0, neginf=0.0)

    # Market breadth = fraction of tokens above their 20 EMA
    market_breadth = np.nanmean(above_ema20_matrix, axis=1)
    market_breadth = np.nan_to_num(market_breadth, nan=0.5)

    # Package
    context = pd.DataFrame({
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        '_btc_ret_1h_series': btc_ret_1h,
    }, index=btc_idx)

    elapsed = time.time() - t0
    print(f'  Market context built: {len(context):,} bars, {elapsed:.1f}s')
    print(f'  Tokens used for breadth/dispersion: {len(tokens)}')

    # Regime distribution
    for r, name in [(0, 'CRISIS'), (1, 'QUIET'), (2, 'UPTREND'), (3, 'RANGE'), (4, 'DOWNTREND')]:
        pct = (regime_daily == r).sum() / n_d * 100
        print(f'    BTC regime {name}: {pct:.1f}%')

    return context


# ==================== Feature Engineering ====================

def compute_features(close, high, low, volume, funding=None):
    """Compute 31-34 base TA features (unchanged from V1)."""
    n = len(close)

    def log_ret(shift):
        r = np.zeros(n)
        r[shift:] = np.log(close[shift:] / close[:-shift])
        return r

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


def get_tokens_by_data_size(n_tokens=50):
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    return [f.stem.replace('_1h', '') for f in token_files[:n_tokens]]


def build_token_features_direction(token, horizon=24, threshold_pct=3.0, market_ctx=None):
    """Build features + direction labels for one token.

    If market_ctx is provided, adds 7 market-context features (41 total).
    Otherwise, uses 34 base features only (V1 compatible).
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
    labels, fwd_ret = compute_direction_labels(close, horizon, threshold_pct)

    # --- Add market-context features ---
    if market_ctx is not None:
        ctx_aligned = market_ctx.reindex(df.index, method='ffill')

        # 6 shared features
        features['btc_ret_1h'] = np.nan_to_num(ctx_aligned['btc_ret_1h'].values, nan=0.0)
        features['btc_ret_24h'] = np.nan_to_num(ctx_aligned['btc_ret_24h'].values, nan=0.0)
        features['btc_vol_24h'] = np.nan_to_num(ctx_aligned['btc_vol_24h'].values, nan=0.0)
        features['market_regime'] = np.nan_to_num(ctx_aligned['market_regime'].values, nan=3.0)
        features['market_breadth_ema20'] = np.nan_to_num(ctx_aligned['market_breadth_ema20'].values, nan=0.5)
        features['dispersion_zscore'] = np.nan_to_num(ctx_aligned['dispersion_zscore'].values, nan=0.0)

        # 1 per-token feature: rolling 168h correlation with BTC
        n = len(close)
        token_ret_1h = np.zeros(n)
        token_ret_1h[1:] = np.log(close[1:] / close[:-1])
        btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(
            pd.Series(btc_ret_aligned)).values
        features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)

    # Stack into matrix
    feat_names = sorted(features.keys())
    # Remove private columns
    feat_names = [k for k in feat_names if not k.startswith('_')]
    X = np.column_stack([features[k] for k in feat_names])

    # Valid mask
    valid = np.ones(len(close), dtype=bool)
    valid[:200] = False
    valid[-horizon:] = False
    nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
    valid = valid & ~nan_mask

    del df, close, high, low, volume, funding, features
    gc.collect()

    return X[valid], labels[valid], feat_names


# ==================== Training ====================

def undersample_balance(X, y, rng):
    pos_idx = np.where(y == 1)[0]
    neg_idx = np.where(y == -1)[0]
    n_min = min(len(pos_idx), len(neg_idx))
    if n_min == 0:
        return X, y
    pos_sample = rng.choice(pos_idx, n_min, replace=False)
    neg_sample = rng.choice(neg_idx, n_min, replace=False)
    bal_idx = np.sort(np.concatenate([pos_sample, neg_sample]))
    return X[bal_idx], y[bal_idx]


def evaluate_direction(y_proba, y_test, classes, feat_names, model, model_type=''):
    """Evaluate direction predictions at multiple probability thresholds."""
    metrics = {}
    thresholds = [0.55, 0.60, 0.65, 0.70]
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
            metrics[key] = {
                'n_predictions': int(n_signals),
                'correct': int(correct),
                'precision': float(precision),
                'recall': float(recall),
            }
            print(f'  p>={thr:.2f}       {n_signals:<10} {correct:<10} {precision:<12.4f} {recall:<10.4f}')
            if precision > best_precision:
                best_precision = precision

    metrics['best_precision'] = best_precision

    # Feature importances
    if hasattr(model, 'feature_importances_'):
        imp = model.feature_importances_
        top_idx = np.argsort(imp)[::-1][:15]
        print(f'\n  Top 15 Feature Importances ({model_type}):')
        fi_list = []
        for rank, i in enumerate(top_idx):
            fi_list.append((feat_names[i], float(imp[i])))
            print(f'    {rank+1:2d}. {feat_names[i]:<25s} {imp[i]:.4f}')
        metrics['feature_importances'] = fi_list

    return metrics


def train_fold(X_train, y_train, X_test, y_test, feat_names, fold_name, model_type='histgbm'):
    """Train and evaluate a single fold."""
    rng = np.random.RandomState(42)
    X_train_bal, y_train_bal = undersample_balance(X_train, y_train, rng)

    n_long_train = (y_train_bal == 1).sum()
    n_short_train = (y_train_bal == -1).sum()
    print(f'\n  {fold_name}: Train {len(y_train_bal):,} (L={n_long_train:,}, S={n_short_train:,}) | Test {len(y_test):,}')

    t0 = time.time()

    if model_type == 'histgbm':
        model = HistGradientBoostingClassifier(
            max_iter=400,
            max_depth=6,
            learning_rate=0.05,
            min_samples_leaf=50,
            max_leaf_nodes=31,
            l2_regularization=1.0,
            max_bins=128,
            early_stopping=True,
            validation_fraction=0.15,
            n_iter_no_change=20,
            random_state=42,
            verbose=0,
        )
        # HistGBM does not need scaling
        model.fit(X_train_bal, y_train_bal)
        y_proba = model.predict_proba(X_test)
        scaler = None
    else:
        # Baseline RF
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train_bal)
        X_test_s = scaler.transform(X_test)
        model = RandomForestClassifier(
            n_estimators=100, max_depth=10, min_samples_leaf=30,
            n_jobs=2, random_state=42,
        )
        model.fit(X_train_s, y_train_bal)
        y_proba = model.predict_proba(X_test_s)

    train_time = time.time() - t0
    print(f'  Trained {model_type} in {train_time:.1f}s')

    if hasattr(model, 'n_iter_'):
        print(f'  HistGBM iterations: {model.n_iter_}')

    classes = list(model.classes_)
    metrics = evaluate_direction(y_proba, y_test, classes, feat_names, model, model_type)
    metrics['train_time'] = train_time

    return model, scaler, metrics


# ==================== Main Research Pipeline ====================

def run_direction_research_v2():
    """Main entry point: V2 model with market context + HistGBM."""
    t_start = time.time()

    print('=' * 70)
    print('ML DIRECTION MODEL V2 RESEARCH')
    print('Market-Context-Aware + HistGBM')
    print('=' * 70)

    # Phase 1: Build market context
    market_ctx = build_market_context(DATA_DIR, top_n=30)

    # Get tokens
    all_tokens = get_tokens_by_data_size(50)
    train_tokens = all_tokens[:30]
    val_tokens = all_tokens[30:50]
    print(f'\nTrain tokens ({len(train_tokens)}): {train_tokens[:10]}...')
    print(f'Validation tokens ({len(val_tokens)}): {val_tokens[:10]}...')

    # Only 24h horizon (primary)
    horizon = 24
    horizon_name = '24h'

    print(f'\n{"#"*70}')
    print(f'# HORIZON: {horizon_name} (threshold: 3%)')
    print(f'{"#"*70}')

    # ---- Build dataset WITH market context ----
    print(f'\n--- Building dataset with market context ({len(train_tokens)} tokens) ---')
    X_list, y_list = [], []
    feat_names = None

    for i, token in enumerate(train_tokens):
        X, y, fn = build_token_features_direction(
            token, horizon=horizon, threshold_pct=3.0, market_ctx=market_ctx)
        if X is not None and len(X) > 100:
            X_list.append(X)
            y_list.append(y)
            if feat_names is None:
                feat_names = fn
            if (i + 1) % 10 == 0:
                n_samples = sum(len(x) for x in X_list)
                print(f'  Loaded {i+1}/{len(train_tokens)} tokens ({n_samples:,} samples)')
        gc.collect()

    if not X_list:
        print('ERROR: No training data')
        return

    all_X = np.vstack(X_list)
    all_y = np.concatenate(y_list)
    del X_list, y_list; gc.collect()

    # Filter to non-neutral only
    mask_nz = (all_y == 1) | (all_y == -1)
    X_nz = all_X[mask_nz]
    y_nz = all_y[mask_nz]
    del all_X, all_y; gc.collect()

    n_total = len(y_nz)
    n_long = (y_nz == 1).sum()
    n_short = (y_nz == -1).sum()
    print(f'\nTotal non-neutral samples: {n_total:,}')
    print(f'  LONG (+1): {n_long:,} ({n_long/n_total*100:.1f}%)')
    print(f'  SHORT (-1): {n_short:,} ({n_short/n_total*100:.1f}%)')
    print(f'  Features: {len(feat_names)} ({feat_names})')

    # ---- Also build baseline dataset WITHOUT market context ----
    print(f'\n--- Building BASELINE dataset (no market context) ---')
    base_X_list, base_y_list = [], []
    base_feat_names = None

    for i, token in enumerate(train_tokens):
        X, y, fn = build_token_features_direction(
            token, horizon=horizon, threshold_pct=3.0, market_ctx=None)
        if X is not None and len(X) > 100:
            base_X_list.append(X)
            base_y_list.append(y)
            if base_feat_names is None:
                base_feat_names = fn
        gc.collect()

    base_X = np.vstack(base_X_list)
    base_y = np.concatenate(base_y_list)
    del base_X_list, base_y_list; gc.collect()

    base_mask_nz = (base_y == 1) | (base_y == -1)
    base_X_nz = base_X[base_mask_nz]
    base_y_nz = base_y[base_mask_nz]
    del base_X, base_y; gc.collect()

    print(f'  Baseline: {len(base_y_nz):,} non-neutral samples, {len(base_feat_names)} features')

    # ==========================================
    # 3-Fold Expanding Walk-Forward
    # ==========================================
    print(f'\n{"="*70}')
    print(f'EXPANDING WALK-FORWARD VALIDATION (3 folds)')
    print(f'{"="*70}')

    n = len(y_nz)
    folds = [
        ('Fold1 (train 0-33%, test 33-50%)', slice(0, int(n * 0.33)), slice(int(n * 0.33), int(n * 0.50))),
        ('Fold2 (train 0-50%, test 50-67%)', slice(0, int(n * 0.50)), slice(int(n * 0.50), int(n * 0.67))),
        ('Fold3 (train 0-67%, test 67-100%)', slice(0, int(n * 0.67)), slice(int(n * 0.67), n)),
    ]

    fold_results = {}
    best_model = None
    best_scaler = None
    best_fold3_precision = 0.0

    for fold_name, train_sl, test_sl in folds:
        print(f'\n--- {fold_name} ---')

        # V2: HistGBM with market context features
        model, scaler, metrics = train_fold(
            X_nz[train_sl], y_nz[train_sl],
            X_nz[test_sl], y_nz[test_sl],
            feat_names, fold_name, model_type='histgbm'
        )
        fold_results[fold_name] = metrics

        if 'Fold3' in fold_name:
            best_model = model
            best_scaler = scaler
            best_fold3_precision = metrics.get('best_precision', 0.0)

    # ==========================================
    # Baseline RF on Fold 3 for comparison
    # ==========================================
    print(f'\n{"="*70}')
    print(f'BASELINE: RandomForest (no market features) on Fold 3')
    print(f'{"="*70}')

    n_base = len(base_y_nz)
    base_train_sl = slice(0, int(n_base * 0.67))
    base_test_sl = slice(int(n_base * 0.67), n_base)

    _, _, baseline_metrics = train_fold(
        base_X_nz[base_train_sl], base_y_nz[base_train_sl],
        base_X_nz[base_test_sl], base_y_nz[base_test_sl],
        base_feat_names, 'Baseline Fold3', model_type='rf'
    )

    del base_X_nz, base_y_nz; gc.collect()

    # ==========================================
    # Comparison Table
    # ==========================================
    print(f'\n{"="*70}')
    print(f'COMPARISON: V1 RF (34 features) vs V2 HistGBM (41 features)')
    print(f'{"="*70}')
    print(f'{"Metric":<30} {"V1 RF Baseline":<20} {"V2 HistGBM":<20} {"Delta":<10}')
    print(f'{"-"*80}')

    for key in ['LONG_p55', 'LONG_p60', 'LONG_p65', 'SHORT_p55', 'SHORT_p60', 'SHORT_p65']:
        v1 = baseline_metrics.get(key, {})
        v2 = fold_results.get([k for k in fold_results if 'Fold3' in k][0], {}).get(key, {})
        v1_prec = v1.get('precision', 0)
        v2_prec = v2.get('precision', 0)
        v1_n = v1.get('n_predictions', 0)
        v2_n = v2.get('n_predictions', 0)
        delta = v2_prec - v1_prec
        print(f'{key:<30} {v1_prec:.4f} (n={v1_n:<5}) {v2_prec:.4f} (n={v2_n:<5}) {delta:+.4f}')

    print(f'\nV1 RF train time: {baseline_metrics.get("train_time", 0):.1f}s')
    print(f'V2 HistGBM train time: {fold_results.get([k for k in fold_results if "Fold3" in k][0], {}).get("train_time", 0):.1f}s')

    # ==========================================
    # Validation on held-out tokens
    # ==========================================
    print(f'\n{"="*70}')
    print(f'HELD-OUT TOKEN VALIDATION ({len(val_tokens)} tokens)')
    print(f'{"="*70}')

    val_X_list, val_y_list = [], []
    for token in val_tokens:
        X, y, fn = build_token_features_direction(
            token, horizon=horizon, threshold_pct=3.0, market_ctx=market_ctx)
        if X is not None and len(X) > 100:
            val_X_list.append(X)
            val_y_list.append(y)
        gc.collect()

    if val_X_list:
        val_X = np.vstack(val_X_list)
        val_y = np.concatenate(val_y_list)
        val_mask = (val_y == 1) | (val_y == -1)
        val_X_nz = val_X[val_mask]
        val_y_nz = val_y[val_mask]

        print(f'Validation samples: {len(val_y_nz):,} (L={sum(val_y_nz==1):,}, S={sum(val_y_nz==-1):,})')

        if len(val_y_nz) > 500:
            val_proba = best_model.predict_proba(val_X_nz)
            val_metrics = evaluate_direction(
                val_proba, val_y_nz, list(best_model.classes_), feat_names, best_model, 'V2 HistGBM'
            )
        del val_X, val_y, val_X_nz, val_y_nz
        gc.collect()

    # ==========================================
    # Save model if Fold 3 precision >= 0.55
    # ==========================================
    print(f'\n{"="*70}')
    print(f'SAVE DECISION')
    print(f'{"="*70}')
    print(f'Fold 3 best precision: {best_fold3_precision:.4f}')

    if best_fold3_precision > 0.55:
        print(f'Precision > 0.55 — SAVING V2 MODEL')

        # Save to v2-specific paths
        model_path = RESULTS_DIR / 'ml_dir_v2_model.joblib'
        features_path = RESULTS_DIR / 'ml_dir_v2_features.joblib'

        joblib.dump(best_model, str(model_path))
        joblib.dump(feat_names, str(features_path))

        # Also save market context builder config
        ctx_config = {
            'top_n': 30,
            'data_dir': str(DATA_DIR),
            'features': [f for f in feat_names if not f.startswith('_')],
        }
        joblib.dump(ctx_config, str(RESULTS_DIR / 'ml_dir_v2_config.joblib'))

        print(f'  Saved: {model_path}')
        print(f'  Saved: {features_path}')
        print(f'  Note: HistGBM does not need a scaler (no StandardScaler saved)')
    else:
        print(f'Precision <= 0.55 — NOT saving model')

    elapsed = time.time() - t_start
    print(f'\nTotal research time: {elapsed:.0f}s')
    print(f'{"="*70}')


if __name__ == '__main__':
    run_direction_research_v2()
