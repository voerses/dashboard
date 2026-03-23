"""
EXPERIMENT D: Microstructure ML — Order Flow Features from 1-Minute Data
========================================================================
Hypothesis: Microstructure features computed from 1-minute data capture
ORDER FLOW dynamics invisible to hourly TA. These features may predict
direction over 4-8 hour horizons.

Context: Experiments A/B/C proved standard TA features have ZERO predictive
power for crypto direction out-of-sample (~50% precision = random).

Features (computed from 60x 1m bars per hour):
  1. VPIN_approx    - Volume-weighted price toxicity indicator
  2. Realized_vol_ratio - 1m realized vol / hourly close-to-close vol
  3. Intraday_momentum  - Trend consistency within hour
  4. Volume_profile     - Volume concentration across thirds
  5. Kyle_lambda        - Price impact coefficient
  6. Amihud             - Illiquidity ratio
  7. Parkinson_vol      - Range-based volatility estimator
  8. Autocorrelation    - Serial correlation of 1m returns
  9. CLV                - Close location value (buying/selling pressure)
  10. Volume_momentum   - Recent vs trailing volume ratio
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

from sklearn.ensemble import HistGradientBoostingClassifier
import joblib

# ==================== Configuration ====================

DATA_1M_DIR = Path('/workspace/crypto_backtest/data/perp/1m_cache')
DATA_1H_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v5')
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_CUTOFF = pd.Timestamp('2025-07-01')
FWD_HOURS = 8
LABEL_THRESH = 0.015  # 1.5%
TOP_N_TOKENS = 15
EVAL_THRESHOLDS = [0.55, 0.60, 0.65, 0.70]

MICRO_FEATURES = [
    'vpin_approx', 'vpin_4h',
    'realized_vol_ratio',
    'intraday_momentum',
    'volume_profile',
    'kyle_lambda',
    'amihud',
    'parkinson_vol',
    'autocorrelation',
    'clv',
    'volume_momentum',
]

TA_FEATURES = [
    'ret_24h',
    'vol_20',
    'rsi_14',
    'funding_1h',
    'btc_ret_24h',
]

HISTGBM_PARAMS = dict(
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


# ==================== Microstructure Feature Engine ====================

def compute_microstructure_hourly(df_1m):
    """Compute microstructure features from 1-minute data, aggregated to hourly.

    Args:
        df_1m: DataFrame with DatetimeIndex (minute), columns: open, high, low, close, volume

    Returns:
        DataFrame indexed by hour with microstructure features (float32)
    """
    # Convert to float32 for memory efficiency
    o = df_1m['open'].values.astype(np.float32)
    h = df_1m['high'].values.astype(np.float32)
    lo = df_1m['low'].values.astype(np.float32)
    c = df_1m['close'].values.astype(np.float32)
    v = df_1m['volume'].values.astype(np.float32)

    n = len(df_1m)

    # Pre-compute 1m log returns
    log_ret = np.zeros(n, dtype=np.float32)
    log_ret[1:] = np.log(np.maximum(c[1:], 1e-10) / np.maximum(c[:-1], 1e-10))

    # Pre-compute per-bar quantities
    hl_range = np.maximum(h - lo, 1e-10)
    buy_frac = (c - lo) / hl_range  # CLV per bar
    abs_log_oc = np.abs(np.log(np.maximum(c, 1e-10) / np.maximum(o, 1e-10)))
    log_hl = np.log(np.maximum(h, 1e-10) / np.maximum(lo, 1e-10))

    # Floor index to hour
    hour_idx = df_1m.index.floor('h')
    unique_hours = hour_idx.unique().sort_values()

    # Pre-allocate output arrays
    n_hours = len(unique_hours)
    vpin_hourly = np.full(n_hours, np.nan, dtype=np.float32)
    rv_ratio = np.full(n_hours, np.nan, dtype=np.float32)
    intra_mom = np.full(n_hours, np.nan, dtype=np.float32)
    vol_profile = np.full(n_hours, np.nan, dtype=np.float32)
    kyle_lam = np.full(n_hours, np.nan, dtype=np.float32)
    amihud_arr = np.full(n_hours, np.nan, dtype=np.float32)
    park_vol = np.full(n_hours, np.nan, dtype=np.float32)
    autocorr_arr = np.full(n_hours, np.nan, dtype=np.float32)
    clv_arr = np.full(n_hours, np.nan, dtype=np.float32)

    # Group by hour using integer positions for speed
    # Build hour boundaries
    hour_labels = hour_idx.values
    # Find boundaries where hour changes
    changes = np.where(hour_labels[1:] != hour_labels[:-1])[0] + 1
    starts = np.concatenate([[0], changes])
    ends = np.concatenate([changes, [n]])

    for i in range(n_hours):
        s, e = starts[i], ends[i]
        count = e - s
        if count < 10:  # need minimum bars for meaningful stats
            continue

        v_slice = v[s:e]
        bf_slice = buy_frac[s:e]
        lr_slice = log_ret[s:e]
        abs_oc_slice = abs_log_oc[s:e]
        lh_slice = log_hl[s:e]
        c_slice = c[s:e]
        o_slice = o[s:e]

        total_vol = v_slice.sum()
        if total_vol < 1e-10:
            continue

        # 1. VPIN_approx
        buy_vol = (bf_slice * v_slice).sum()
        sell_vol = ((1.0 - bf_slice) * v_slice).sum()
        vpin_hourly[i] = abs(buy_vol - sell_vol) / total_vol

        # 2. Realized vol ratio
        rv_1m = np.std(lr_slice) * np.sqrt(60.0) if count >= 2 else 0.0
        rv_1h = abs(np.log(max(c_slice[-1], 1e-10) / max(o_slice[0], 1e-10)))
        rv_ratio[i] = rv_1m / max(rv_1h, 1e-10)

        # 3. Intraday momentum
        mid = count // 2
        if mid >= 2 and (count - mid) >= 2:
            ret_first = c_slice[mid - 1] / max(o_slice[0], 1e-10) - 1.0
            ret_second = c_slice[-1] / max(c_slice[mid - 1], 1e-10) - 1.0
            intra_mom[i] = np.sign(ret_first) * np.sign(ret_second)
        else:
            intra_mom[i] = 0.0

        # 4. Volume profile (split into thirds)
        third = count // 3
        if third >= 1:
            v1 = v_slice[:third].sum()
            v2 = v_slice[third:2*third].sum()
            v3 = v_slice[2*third:].sum()
            mean_third = (v1 + v2 + v3) / 3.0
            vol_profile[i] = max(v1, v2, v3) / max(mean_third, 1e-10)
        else:
            vol_profile[i] = 1.0

        # 5. Kyle lambda
        sqrt_v = np.sqrt(np.maximum(v_slice, 1e-10))
        lambda_bars = abs_oc_slice / sqrt_v
        kyle_lam[i] = np.median(lambda_bars)

        # 6. Amihud illiquidity
        abs_ret = np.abs(lr_slice)
        amihud_arr[i] = np.mean(abs_ret / np.maximum(v_slice, 1e-10))

        # 7. Parkinson vol
        park_vol[i] = np.sum(lh_slice ** 2) / (4.0 * count * np.log(2.0))

        # 8. Autocorrelation of 1m returns
        if count >= 3:
            r1 = lr_slice[:-1]
            r2 = lr_slice[1:]
            if np.std(r1) > 1e-10 and np.std(r2) > 1e-10:
                autocorr_arr[i] = np.corrcoef(r1, r2)[0, 1]
            else:
                autocorr_arr[i] = 0.0
        else:
            autocorr_arr[i] = 0.0

        # 9. CLV
        clv_arr[i] = np.mean(bf_slice)

    # Build DataFrame
    result = pd.DataFrame({
        'vpin_approx': vpin_hourly,
        'realized_vol_ratio': rv_ratio,
        'intraday_momentum': intra_mom,
        'volume_profile': vol_profile,
        'kyle_lambda': kyle_lam,
        'amihud': amihud_arr,
        'parkinson_vol': park_vol,
        'autocorrelation': autocorr_arr,
        'clv': clv_arr,
    }, index=unique_hours)

    # Rolling VPIN (4h)
    result['vpin_4h'] = result['vpin_approx'].rolling(4, min_periods=2).mean()

    # Volume momentum (4h vs 24h) — needs hourly volume
    hourly_vol = pd.Series(np.nan, index=unique_hours, dtype=np.float32)
    for i in range(n_hours):
        s, e = starts[i], ends[i]
        hourly_vol.iloc[i] = v[s:e].sum()

    vol_4h = hourly_vol.rolling(4, min_periods=2).sum()
    vol_24h = hourly_vol.rolling(24, min_periods=12).sum()
    result['volume_momentum'] = (vol_4h / np.maximum(vol_24h, 1e-10)) * 6.0

    return result.astype(np.float32)


def compute_ta_features_hourly(df_1h):
    """Compute a small subset of hourly TA features for the combo model.

    Args:
        df_1h: hourly DataFrame with OHLCV + funding columns

    Returns:
        DataFrame with TA features (float32)
    """
    c = df_1h['close'].values.astype(np.float64)
    n = len(c)

    # ret_24h
    ret_24h = np.full(n, np.nan)
    ret_24h[24:] = np.log(c[24:] / c[:-24])

    # vol_20 (realized vol, 20h rolling std of log returns)
    lr = np.zeros(n)
    lr[1:] = np.log(c[1:] / c[:-1])
    vol_20 = pd.Series(lr).rolling(20, min_periods=10).std().values

    # RSI 14
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(span=14, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(span=14, adjust=False).mean().values
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100.0 - 100.0 / (1.0 + rs)

    # Funding (already in data)
    funding = df_1h['funding_1h'].values.astype(np.float64) if 'funding_1h' in df_1h.columns else np.zeros(n)

    result = pd.DataFrame({
        'ret_24h': ret_24h,
        'vol_20': vol_20,
        'rsi_14': rsi,
        'funding_1h': funding,
    }, index=df_1h.index).astype(np.float32)

    return result


def build_btc_context():
    """Build BTC return features for cross-token context."""
    btc_path = DATA_1H_DIR / 'BTC_1h.parquet'
    if not btc_path.exists():
        return None
    btc = pd.read_parquet(btc_path)
    c = btc['close'].values.astype(np.float64)
    n = len(c)
    btc_ret_24h = np.full(n, np.nan)
    btc_ret_24h[24:] = np.log(c[24:] / c[:-24])
    return pd.Series(btc_ret_24h, index=btc.index, name='btc_ret_24h', dtype=np.float32)


# ==================== Labeling ====================

def make_labels(df_1h, fwd_hours=8, thresh=0.015):
    """Create direction labels from hourly close.

    +1 if fwd return > thresh
    -1 if fwd return < -thresh
     0 otherwise (dropped)
    """
    c = df_1h['close'].values.astype(np.float64)
    n = len(c)
    fwd_ret = np.full(n, np.nan)
    if n > fwd_hours:
        fwd_ret[:-fwd_hours] = c[fwd_hours:] / c[:-fwd_hours] - 1.0

    labels = np.zeros(n, dtype=np.int8)
    labels[fwd_ret > thresh] = 1
    labels[fwd_ret < -thresh] = -1
    # NaN forward returns -> label stays 0

    return pd.Series(labels, index=df_1h.index, name='label'), \
           pd.Series(fwd_ret, index=df_1h.index, name='fwd_ret')


# ==================== Data Pipeline ====================

def get_top_tokens(n=15):
    """Get top N tokens by 1m file size (proxy for data length)."""
    files = list(DATA_1M_DIR.glob('*_1m.parquet'))
    sized = [(f.stat().st_size, f.stem.replace('_1m', '')) for f in files]
    sized.sort(reverse=True)
    return [tok for _, tok in sized[:n]]


def process_token(token, btc_ret_24h_series=None):
    """Process a single token: load 1m data, compute features, align with hourly labels.

    Returns (micro_df, combo_df, labels, timestamps) or None if insufficient data.
    """
    path_1m = DATA_1M_DIR / f'{token}_1m.parquet'
    path_1h = DATA_1H_DIR / f'{token}_1h.parquet'

    if not path_1m.exists() or not path_1h.exists():
        return None

    # Load 1m data
    df_1m = pd.read_parquet(path_1m)
    if len(df_1m) < 60 * 24 * 30:  # need at least ~30 days of 1m data
        print(f'    {token}: insufficient 1m data ({len(df_1m)} bars), skipping')
        return None

    # Compute microstructure features
    micro = compute_microstructure_hourly(df_1m)
    del df_1m
    gc.collect()

    # Load hourly data for labels and TA features
    df_1h = pd.read_parquet(path_1h)

    # Labels
    labels, fwd_ret = make_labels(df_1h, FWD_HOURS, LABEL_THRESH)

    # TA features
    ta = compute_ta_features_hourly(df_1h)

    # Add BTC ret 24h
    if btc_ret_24h_series is not None:
        ta = ta.join(btc_ret_24h_series, how='left')
        ta['btc_ret_24h'] = ta['btc_ret_24h'].fillna(0.0).astype(np.float32)
    else:
        ta['btc_ret_24h'] = np.float32(0.0)

    # Align micro features with hourly index
    # micro is indexed by hour timestamps from 1m data; align with 1h data index
    common_idx = micro.index.intersection(df_1h.index)
    if len(common_idx) < 500:
        print(f'    {token}: insufficient aligned data ({len(common_idx)} hours), skipping')
        return None

    micro_aligned = micro.loc[common_idx]
    ta_aligned = ta.loc[common_idx]
    labels_aligned = labels.loc[common_idx]

    # Drop neutral labels
    mask = labels_aligned != 0
    micro_final = micro_aligned.loc[mask]
    ta_final = ta_aligned.loc[mask]
    labels_final = labels_aligned.loc[mask]

    if len(labels_final) < 100:
        print(f'    {token}: too few labeled samples ({len(labels_final)}), skipping')
        return None

    # Combo features
    combo_final = pd.concat([micro_final, ta_final], axis=1)

    # Drop rows with any NaN
    valid_micro = micro_final.dropna()
    valid_combo = combo_final.dropna()
    labels_micro = labels_final.loc[valid_micro.index]
    labels_combo = labels_final.loc[valid_combo.index]

    return {
        'micro_X': valid_micro,
        'combo_X': valid_combo,
        'micro_y': labels_micro,
        'combo_y': labels_combo,
        'token': token,
    }


# ==================== Training & Evaluation ====================

def balance_classes(X, y):
    """Undersample majority class to match minority."""
    classes, counts = np.unique(y, return_counts=True)
    min_count = counts.min()
    indices = []
    rng = np.random.RandomState(42)
    for cls in classes:
        cls_idx = np.where(y == cls)[0]
        chosen = rng.choice(cls_idx, size=min_count, replace=False)
        indices.extend(chosen)
    indices = sorted(indices)
    return X[indices], y[indices]


def evaluate_predictions(y_true, y_proba, classes, thresholds):
    """Evaluate predictions at multiple confidence thresholds.

    Returns list of dicts with threshold, long_prec, long_n, short_prec, short_n.
    """
    results = []
    # Identify class indices
    long_cls_idx = np.where(classes == 1)[0][0] if 1 in classes else None
    short_cls_idx = np.where(classes == -1)[0][0] if -1 in classes else None

    if long_cls_idx is None or short_cls_idx is None:
        return results

    for thresh in thresholds:
        # Long predictions
        long_mask = y_proba[:, long_cls_idx] >= thresh
        long_n = long_mask.sum()
        long_prec = (y_true[long_mask] == 1).mean() if long_n > 0 else 0.0

        # Short predictions
        short_mask = y_proba[:, short_cls_idx] >= thresh
        short_n = short_mask.sum()
        short_prec = (y_true[short_mask] == -1).mean() if short_n > 0 else 0.0

        results.append({
            'threshold': thresh,
            'long_prec': long_prec,
            'long_n': int(long_n),
            'short_prec': short_prec,
            'short_n': int(short_n),
        })

    return results


def evaluate_per_month(y_true, y_proba, classes, timestamps, thresh=0.60):
    """Evaluate precision broken out by month."""
    long_cls_idx = np.where(classes == 1)[0][0] if 1 in classes else None
    short_cls_idx = np.where(classes == -1)[0][0] if -1 in classes else None
    if long_cls_idx is None or short_cls_idx is None:
        return {}

    months = pd.Series(timestamps).dt.to_period('M')
    results = {}
    for month in months.unique():
        mask = months == month
        m_true = y_true[mask.values]
        m_proba = y_proba[mask.values]

        long_mask = m_proba[:, long_cls_idx] >= thresh
        short_mask = m_proba[:, short_cls_idx] >= thresh

        long_n = long_mask.sum()
        short_n = short_mask.sum()
        long_prec = (m_true[long_mask] == 1).mean() if long_n > 0 else 0.0
        short_prec = (m_true[short_mask] == -1).mean() if short_n > 0 else 0.0

        results[str(month)] = {
            'long_prec': long_prec, 'long_n': int(long_n),
            'short_prec': short_prec, 'short_n': int(short_n),
        }

    return results


def evaluate_per_token(y_true, y_proba, classes, tokens_arr, thresh=0.60):
    """Evaluate precision broken out by token."""
    long_cls_idx = np.where(classes == 1)[0][0] if 1 in classes else None
    short_cls_idx = np.where(classes == -1)[0][0] if -1 in classes else None
    if long_cls_idx is None or short_cls_idx is None:
        return {}

    results = {}
    for tok in np.unique(tokens_arr):
        mask = tokens_arr == tok
        t_true = y_true[mask]
        t_proba = y_proba[mask]

        long_mask = t_proba[:, long_cls_idx] >= thresh
        short_mask = t_proba[:, short_cls_idx] >= thresh

        long_n = long_mask.sum()
        short_n = short_mask.sum()
        long_prec = (t_true[long_mask] == 1).mean() if long_n > 0 else 0.0
        short_prec = (t_true[short_mask] == -1).mean() if short_n > 0 else 0.0

        results[tok] = {
            'long_prec': long_prec, 'long_n': int(long_n),
            'short_prec': short_prec, 'short_n': int(short_n),
        }

    return results


# ==================== Main ====================

def main():
    t_start = time.time()

    print('=' * 70)
    print('EXPERIMENT D: MICROSTRUCTURE ML')
    print('=' * 70)
    print(f'Forward horizon: {FWD_HOURS}h | Label threshold: {LABEL_THRESH*100:.1f}%')
    print(f'Train cutoff: {TRAIN_CUTOFF} | Eval thresholds: {EVAL_THRESHOLDS}')
    print()

    # Get top tokens by file size
    tokens = get_top_tokens(TOP_N_TOKENS)
    print(f'Top {TOP_N_TOKENS} tokens by 1m data size: {tokens}')
    print()

    # Build BTC context
    btc_ret = build_btc_context()
    print(f'BTC context built: {len(btc_ret)} hourly observations' if btc_ret is not None else 'BTC context: N/A')

    # Process tokens one at a time (memory efficiency)
    all_micro_train_X = []
    all_micro_train_y = []
    all_micro_test_X = []
    all_micro_test_y = []
    all_micro_test_ts = []
    all_micro_test_tok = []

    all_combo_train_X = []
    all_combo_train_y = []
    all_combo_test_X = []
    all_combo_test_y = []
    all_combo_test_ts = []
    all_combo_test_tok = []

    for i, token in enumerate(tokens):
        print(f'  [{i+1}/{len(tokens)}] Processing {token}...', end='')
        t0 = time.time()

        result = process_token(token, btc_ret)
        if result is None:
            print(' SKIPPED')
            continue

        micro_X = result['micro_X']
        combo_X = result['combo_X']
        micro_y = result['micro_y']
        combo_y = result['combo_y']

        # Train/test split by time
        # Micro
        train_mask_m = micro_X.index < TRAIN_CUTOFF
        test_mask_m = micro_X.index >= TRAIN_CUTOFF

        if train_mask_m.sum() >= 50 and test_mask_m.sum() >= 20:
            all_micro_train_X.append(micro_X.loc[train_mask_m].values)
            all_micro_train_y.append(micro_y.loc[train_mask_m].values)
            all_micro_test_X.append(micro_X.loc[test_mask_m].values)
            all_micro_test_y.append(micro_y.loc[test_mask_m].values)
            all_micro_test_ts.append(micro_X.loc[test_mask_m].index)
            all_micro_test_tok.append(np.array([token] * test_mask_m.sum()))

        # Combo
        train_mask_c = combo_X.index < TRAIN_CUTOFF
        test_mask_c = combo_X.index >= TRAIN_CUTOFF

        if train_mask_c.sum() >= 50 and test_mask_c.sum() >= 20:
            all_combo_train_X.append(combo_X.loc[train_mask_c].values)
            all_combo_train_y.append(combo_y.loc[train_mask_c].values)
            all_combo_test_X.append(combo_X.loc[test_mask_c].values)
            all_combo_test_y.append(combo_y.loc[test_mask_c].values)
            all_combo_test_ts.append(combo_X.loc[test_mask_c].index)
            all_combo_test_tok.append(np.array([token] * test_mask_c.sum()))

        elapsed = time.time() - t0
        print(f' done ({elapsed:.1f}s) | micro train={train_mask_m.sum()} test={test_mask_m.sum()} | combo train={train_mask_c.sum()} test={test_mask_c.sum()}')

        del result, micro_X, combo_X, micro_y, combo_y
        gc.collect()

    # Store feature names before stacking
    micro_feature_names = MICRO_FEATURES
    combo_feature_names = MICRO_FEATURES + TA_FEATURES

    print()

    # ==================== Stack all token data ====================
    if not all_micro_train_X:
        print('ERROR: No tokens produced usable microstructure data.')
        return

    X_train_micro = np.vstack(all_micro_train_X).astype(np.float32)
    y_train_micro = np.concatenate(all_micro_train_y)
    X_test_micro = np.vstack(all_micro_test_X).astype(np.float32)
    y_test_micro = np.concatenate(all_micro_test_y)
    ts_test_micro = np.concatenate([idx.values for idx in all_micro_test_ts])
    tok_test_micro = np.concatenate(all_micro_test_tok)

    X_train_combo = np.vstack(all_combo_train_X).astype(np.float32)
    y_train_combo = np.concatenate(all_combo_train_y)
    X_test_combo = np.vstack(all_combo_test_X).astype(np.float32)
    y_test_combo = np.concatenate(all_combo_test_y)
    ts_test_combo = np.concatenate([idx.values for idx in all_combo_test_ts])
    tok_test_combo = np.concatenate(all_combo_test_tok)

    # Free intermediate lists
    del all_micro_train_X, all_micro_train_y, all_micro_test_X, all_micro_test_y
    del all_combo_train_X, all_combo_train_y, all_combo_test_X, all_combo_test_y
    gc.collect()

    # Replace any remaining NaN/inf
    X_train_micro = np.nan_to_num(X_train_micro, nan=0.0, posinf=0.0, neginf=0.0)
    X_test_micro = np.nan_to_num(X_test_micro, nan=0.0, posinf=0.0, neginf=0.0)
    X_train_combo = np.nan_to_num(X_train_combo, nan=0.0, posinf=0.0, neginf=0.0)
    X_test_combo = np.nan_to_num(X_test_combo, nan=0.0, posinf=0.0, neginf=0.0)

    print(f'MICRO: Train {X_train_micro.shape} | Test {X_test_micro.shape}')
    print(f'COMBO: Train {X_train_combo.shape} | Test {X_test_combo.shape}')

    # Base rates
    base_rate_long_micro = (y_test_micro == 1).mean()
    base_rate_short_micro = (y_test_micro == -1).mean()
    base_rate_long_combo = (y_test_combo == 1).mean()
    base_rate_short_combo = (y_test_combo == -1).mean()

    print(f'MICRO test base rates: Long={base_rate_long_micro:.3f} Short={base_rate_short_micro:.3f}')
    print(f'COMBO test base rates: Long={base_rate_long_combo:.3f} Short={base_rate_short_combo:.3f}')
    print()

    # ==================== PART 1: Micro Features Only ====================
    print('=' * 70)
    print('PART 1: MICRO FEATURES ONLY (10 features)')
    print('=' * 70)

    # Balance training
    X_train_m_bal, y_train_m_bal = balance_classes(X_train_micro, y_train_micro)
    print(f'Balanced training: {len(y_train_m_bal)} samples (L={sum(y_train_m_bal==1)}, S={sum(y_train_m_bal==-1)})')

    model_micro = HistGradientBoostingClassifier(**HISTGBM_PARAMS)
    t0 = time.time()
    model_micro.fit(X_train_m_bal, y_train_m_bal)
    print(f'Training time: {time.time()-t0:.1f}s')

    y_proba_micro = model_micro.predict_proba(X_test_micro)
    classes_micro = model_micro.classes_

    # Threshold evaluation
    micro_results = evaluate_predictions(y_test_micro, y_proba_micro, classes_micro, EVAL_THRESHOLDS)

    print(f'\n  {"Thresh":>8} | {"Long Prec":>10} | {"Long N":>8} | {"Short Prec":>11} | {"Short N":>8}')
    print(f'  {"-"*8} | {"-"*10} | {"-"*8} | {"-"*11} | {"-"*8}')
    for r in micro_results:
        print(f'  {r["threshold"]:>8.2f} | {r["long_prec"]:>10.3f} | {r["long_n"]:>8,} | {r["short_prec"]:>11.3f} | {r["short_n"]:>8,}')

    print(f'\n  Base rate (random): Long={base_rate_long_micro:.3f} Short={base_rate_short_micro:.3f}')

    # Verdict
    best_micro_prec = max(
        max((r['long_prec'] for r in micro_results if r['long_n'] >= 30), default=0),
        max((r['short_prec'] for r in micro_results if r['short_n'] >= 30), default=0),
    )
    base_rate_micro = max(base_rate_long_micro, base_rate_short_micro)
    if best_micro_prec > base_rate_micro + 0.10:
        micro_verdict = 'EDGE'
    elif best_micro_prec > base_rate_micro + 0.05:
        micro_verdict = 'MARGINAL'
    else:
        micro_verdict = 'NO EDGE'
    print(f'  VERDICT: {micro_verdict}')

    # Per-month breakdown
    print('\n  Per-month breakdown (thresh=0.60):')
    monthly_micro = evaluate_per_month(y_test_micro, y_proba_micro, classes_micro,
                                        pd.DatetimeIndex(ts_test_micro), thresh=0.60)
    for month, m in sorted(monthly_micro.items()):
        print(f'    {month}: Long prec={m["long_prec"]:.3f} (n={m["long_n"]}) | Short prec={m["short_prec"]:.3f} (n={m["short_n"]})')

    # Per-token breakdown
    print('\n  Per-token breakdown (thresh=0.60):')
    per_token_micro = evaluate_per_token(y_test_micro, y_proba_micro, classes_micro,
                                          tok_test_micro, thresh=0.60)
    for tok, m in sorted(per_token_micro.items()):
        print(f'    {tok:>6}: Long prec={m["long_prec"]:.3f} (n={m["long_n"]}) | Short prec={m["short_prec"]:.3f} (n={m["short_n"]})')

    # Feature importances (via permutation-like inspection — use tree importances if available)
    # HistGBM doesn't expose feature_importances_ directly in older sklearn versions,
    # but recent versions do. We'll try both.
    print('\n  Feature importances (micro model):')
    try:
        importances = model_micro.feature_importances_
        fi = sorted(zip(micro_feature_names, importances), key=lambda x: -x[1])
        for name, imp in fi:
            print(f'    {name:>25}: {imp:.4f}')
    except AttributeError:
        # Fallback: permutation importance
        from sklearn.inspection import permutation_importance
        perm_imp = permutation_importance(model_micro, X_test_micro, y_test_micro,
                                          n_repeats=5, random_state=42, n_jobs=-1)
        fi = sorted(zip(micro_feature_names, perm_imp.importances_mean), key=lambda x: -x[1])
        for name, imp in fi:
            print(f'    {name:>25}: {imp:.4f}')

    print()

    # ==================== PART 2: Micro + TA Combo ====================
    print('=' * 70)
    print('PART 2: MICRO + TA COMBO (15 features)')
    print('=' * 70)

    X_train_c_bal, y_train_c_bal = balance_classes(X_train_combo, y_train_combo)
    print(f'Balanced training: {len(y_train_c_bal)} samples (L={sum(y_train_c_bal==1)}, S={sum(y_train_c_bal==-1)})')

    model_combo = HistGradientBoostingClassifier(**HISTGBM_PARAMS)
    t0 = time.time()
    model_combo.fit(X_train_c_bal, y_train_c_bal)
    print(f'Training time: {time.time()-t0:.1f}s')

    y_proba_combo = model_combo.predict_proba(X_test_combo)
    classes_combo = model_combo.classes_

    combo_results = evaluate_predictions(y_test_combo, y_proba_combo, classes_combo, EVAL_THRESHOLDS)

    print(f'\n  {"Thresh":>8} | {"Long Prec":>10} | {"Long N":>8} | {"Short Prec":>11} | {"Short N":>8}')
    print(f'  {"-"*8} | {"-"*10} | {"-"*8} | {"-"*11} | {"-"*8}')
    for r in combo_results:
        print(f'  {r["threshold"]:>8.2f} | {r["long_prec"]:>10.3f} | {r["long_n"]:>8,} | {r["short_prec"]:>11.3f} | {r["short_n"]:>8,}')

    print(f'\n  Base rate (random): Long={base_rate_long_combo:.3f} Short={base_rate_short_combo:.3f}')

    # Verdict
    best_combo_prec = max(
        max((r['long_prec'] for r in combo_results if r['long_n'] >= 30), default=0),
        max((r['short_prec'] for r in combo_results if r['short_n'] >= 30), default=0),
    )
    base_rate_combo = max(base_rate_long_combo, base_rate_short_combo)
    if best_combo_prec > base_rate_combo + 0.10:
        combo_verdict = 'EDGE'
    elif best_combo_prec > base_rate_combo + 0.05:
        combo_verdict = 'MARGINAL'
    else:
        combo_verdict = 'NO EDGE'
    print(f'  VERDICT: {combo_verdict}')

    # Per-month breakdown
    print('\n  Per-month breakdown (thresh=0.60):')
    monthly_combo = evaluate_per_month(y_test_combo, y_proba_combo, classes_combo,
                                        pd.DatetimeIndex(ts_test_combo), thresh=0.60)
    for month, m in sorted(monthly_combo.items()):
        print(f'    {month}: Long prec={m["long_prec"]:.3f} (n={m["long_n"]}) | Short prec={m["short_prec"]:.3f} (n={m["short_n"]})')

    # Per-token breakdown
    print('\n  Per-token breakdown (thresh=0.60):')
    per_token_combo = evaluate_per_token(y_test_combo, y_proba_combo, classes_combo,
                                          tok_test_combo, thresh=0.60)
    for tok, m in sorted(per_token_combo.items()):
        print(f'    {tok:>6}: Long prec={m["long_prec"]:.3f} (n={m["long_n"]}) | Short prec={m["short_prec"]:.3f} (n={m["short_n"]})')

    # Feature importances
    print('\n  Feature importances (combo model, top 15):')
    try:
        importances_c = model_combo.feature_importances_
        fi_c = sorted(zip(combo_feature_names, importances_c), key=lambda x: -x[1])
        for name, imp in fi_c[:15]:
            print(f'    {name:>25}: {imp:.4f}')
    except AttributeError:
        from sklearn.inspection import permutation_importance
        perm_imp_c = permutation_importance(model_combo, X_test_combo, y_test_combo,
                                             n_repeats=5, random_state=42, n_jobs=-1)
        fi_c = sorted(zip(combo_feature_names, perm_imp_c.importances_mean), key=lambda x: -x[1])
        for name, imp in fi_c[:15]:
            print(f'    {name:>25}: {imp:.4f}')

    print()

    # ==================== BEST RESULT SUMMARY ====================
    print('=' * 70)
    print('BEST RESULT SUMMARY')
    print('=' * 70)

    # Collect all results
    all_results = []
    for r in micro_results:
        for direction in ['long', 'short']:
            prec = r[f'{direction}_prec']
            n = r[f'{direction}_n']
            base = base_rate_long_micro if direction == 'long' else base_rate_short_micro
            all_results.append({
                'config': 'Micro only',
                'direction': direction,
                'threshold': r['threshold'],
                'precision': prec,
                'n_trades': n,
                'vs_base': prec - base,
                'base_rate': base,
            })
    for r in combo_results:
        for direction in ['long', 'short']:
            prec = r[f'{direction}_prec']
            n = r[f'{direction}_n']
            base = base_rate_long_combo if direction == 'long' else base_rate_short_combo
            all_results.append({
                'config': 'Micro+TA',
                'direction': direction,
                'threshold': r['threshold'],
                'precision': prec,
                'n_trades': n,
                'vs_base': prec - base,
                'base_rate': base,
            })

    # Filter for meaningful sample sizes and sort by precision lift
    meaningful = [r for r in all_results if r['n_trades'] >= 20]
    meaningful.sort(key=lambda x: -x['vs_base'])

    print(f'\n  {"Config":>12} | {"Best Prec":>10} | {"Direction":>10} | {"Threshold":>10} | {"N trades":>9} | {"vs Base Rate":>13}')
    print(f'  {"-"*12} | {"-"*10} | {"-"*10} | {"-"*10} | {"-"*9} | {"-"*13}')
    for r in meaningful[:10]:
        print(f'  {r["config"]:>12} | {r["precision"]:>10.3f} | {r["direction"]:>10} | {r["threshold"]:>10.2f} | {r["n_trades"]:>9,} | {r["vs_base"]:>+13.3f}')

    # ==================== Save best model ====================
    # Determine which model is best
    best_micro_lift = max((r['vs_base'] for r in meaningful if r['config'] == 'Micro only'), default=-1)
    best_combo_lift = max((r['vs_base'] for r in meaningful if r['config'] == 'Micro+TA'), default=-1)

    if best_combo_lift >= best_micro_lift:
        best_model = model_combo
        best_features = combo_feature_names
        best_config = 'Micro+TA'
    else:
        best_model = model_micro
        best_features = micro_feature_names
        best_config = 'Micro only'

    model_path = RESULTS_DIR / 'ml_exp_d_best_model.joblib'
    joblib.dump({
        'model': best_model,
        'features': best_features,
        'config': best_config,
        'train_cutoff': str(TRAIN_CUTOFF),
        'fwd_hours': FWD_HOURS,
        'label_thresh': LABEL_THRESH,
        'tokens': tokens,
        'histgbm_params': HISTGBM_PARAMS,
    }, model_path)
    print(f'\nBest model saved to: {model_path}')
    print(f'Best config: {best_config}')

    elapsed = time.time() - t_start
    print(f'\nTotal elapsed: {elapsed:.0f}s ({elapsed/60:.1f} min)')
    print('=' * 70)


if __name__ == '__main__':
    main()
