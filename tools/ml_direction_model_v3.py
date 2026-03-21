"""
ML Direction Model V3 — Proper Temporal Validation
====================================================
Fixes the critical temporal leakage bug from V2:
  - All splits are DATE-BASED (not row-index-based)
  - Purged walk-forward: embargo of `horizon` hours between train/test
  - Multiple temporal folds averaged for robust precision estimates

Architecture (kept from V2):
  - HistGradientBoostingClassifier
  - 41 features: 34 base TA + 7 market context
  - Balanced training via undersampling

Experiments (iterated until adversarial OOS passes):
  A: Fixed 3% target, 24h horizon (V2 config, proper splits)
  B: Adaptive vol-normalized target, 24h horizon
  C: Fixed 3% target, 12h horizon (faster signal)
  D: Feature selection (top 20 only)
"""

import numpy as np
import pandas as pd
import sys, gc, time, warnings
from pathlib import Path

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, '/workspace/crypto_backtest')

from sklearn.ensemble import HistGradientBoostingClassifier
import joblib

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')


# ==================== Helpers (from V2.1, no changes) ====================

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


# ==================== Market Context ====================

def build_market_context(data_dir, top_n=15):
    """Build market-level features. Memory-optimized (float32 matrices)."""
    t0 = time.time()
    print('Building market context...')

    btc = pd.read_parquet(data_dir / 'BTC_1h.parquet')
    btc_close = btc['close'].values.astype(np.float64)
    btc_idx = btc.index
    n_btc = len(btc_close)

    btc_ret_1h = np.zeros(n_btc)
    btc_ret_1h[1:] = np.log(btc_close[1:] / btc_close[:-1])
    btc_ret_24h = np.zeros(n_btc)
    btc_ret_24h[24:] = np.log(btc_close[24:] / btc_close[:-24])
    btc_vol_24h = np.nan_to_num(rolling_std_vec(btc_ret_1h, 24), nan=0.0)

    # BTC regime from daily
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

    # Cross-asset
    token_files = sorted(data_dir.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:top_n]]

    ret_24h_matrix = np.full((n_btc, top_n), np.nan, dtype=np.float32)
    above_ema20_matrix = np.full((n_btc, top_n), np.nan, dtype=np.float32)

    for i, token in enumerate(tokens):
        try:
            df = pd.read_parquet(data_dir / f'{token}_1h.parquet')
            aligned = pd.Series(df['close'].values.astype(np.float32),
                                index=df.index).reindex(btc_idx)
            ret_24h_matrix[:, i] = np.log(aligned / aligned.shift(24)).values
            e20 = aligned.ewm(span=20, adjust=False).mean()
            above_ema20_matrix[:, i] = (aligned > e20).astype(np.float32).values
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
    del ret_24h_matrix, above_ema20_matrix; gc.collect()

    context = pd.DataFrame({
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        '_btc_ret_1h_series': btc_ret_1h,
    }, index=btc_idx)

    print(f'  Market context: {len(context):,} bars ({time.time()-t0:.1f}s)')
    return context


# ==================== Feature Engineering ====================

def compute_features(close, high, low, volume, funding=None):
    """34 base TA features (unchanged from V1/V2)."""
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


# ==================== Dataset Building (with timestamps) ====================

def build_dataset(tokens, market_ctx, horizon=24, target_type='fixed',
                  threshold_pct=3.0, threshold_mult=1.5):
    """Build dataset with timestamps for proper temporal splitting.
    Returns X (float32), y (int8), timestamps (datetime64), feat_names."""
    X_list, y_list, ts_list = [], [], []
    feat_names = None

    for i, token in enumerate(tokens):
        fpath = DATA_DIR / f'{token}_1h.parquet'
        if not fpath.exists():
            continue
        df = pd.read_parquet(fpath)
        if len(df) < 500:
            del df; continue

        close = df['close'].values.astype(np.float64)
        high = df['high'].values.astype(np.float64)
        low = df['low'].values.astype(np.float64)
        volume = df['volume'].values.astype(np.float64)
        funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None
        n = len(close)

        features = compute_features(close, high, low, volume, funding)

        # Labels
        labels = np.zeros(n, dtype=np.int8)
        if n > horizon:
            fwd_ret = np.zeros(n)
            fwd_ret[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0

            if target_type == 'fixed':
                thr = threshold_pct / 100.0
                labels[fwd_ret > thr] = 1
                labels[fwd_ret < -thr] = -1
            elif target_type == 'adaptive':
                ret_1h = np.zeros(n)
                ret_1h[1:] = np.log(close[1:] / close[:-1])
                vol_168 = np.nan_to_num(rolling_std_vec(ret_1h, 168), nan=0.02)
                expected_move = np.maximum(vol_168 * np.sqrt(horizon) * threshold_mult, 0.01)
                labels[fwd_ret > expected_move] = 1
                labels[fwd_ret < -expected_move] = -1

            labels[-horizon:] = 0

        # Market context
        ctx = market_ctx.reindex(df.index, method='ffill')
        features['btc_ret_1h'] = np.nan_to_num(ctx['btc_ret_1h'].values, nan=0.0)
        features['btc_ret_24h'] = np.nan_to_num(ctx['btc_ret_24h'].values, nan=0.0)
        features['btc_vol_24h'] = np.nan_to_num(ctx['btc_vol_24h'].values, nan=0.0)
        features['market_regime'] = np.nan_to_num(ctx['market_regime'].values, nan=3.0)
        features['market_breadth_ema20'] = np.nan_to_num(ctx['market_breadth_ema20'].values, nan=0.5)
        features['dispersion_zscore'] = np.nan_to_num(ctx['dispersion_zscore'].values, nan=0.0)

        token_ret_1h = np.zeros(n)
        token_ret_1h[1:] = np.log(close[1:] / close[:-1])
        btc_ret = np.nan_to_num(ctx['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(pd.Series(btc_ret)).values
        features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)
        del ctx

        fn = sorted([k for k in features.keys() if not k.startswith('_')])
        X = np.column_stack([features[k] for k in fn]).astype(np.float32)

        valid = np.ones(n, dtype=bool)
        valid[:200] = False; valid[-horizon:] = False
        nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
        valid = valid & ~nan_mask

        if valid.sum() > 100:
            X_list.append(X[valid])
            y_list.append(labels[valid])
            ts_list.append(df.index[valid].values)
            if feat_names is None:
                feat_names = fn

        del df, close, high, low, volume, funding, features, X, labels
        gc.collect()

        if (i + 1) % 5 == 0:
            print(f'  Loaded {i+1}/{len(tokens)} tokens')

    X_all = np.vstack(X_list).astype(np.float32)
    y_all = np.concatenate(y_list)
    ts_all = np.concatenate(ts_list)
    del X_list, y_list, ts_list; gc.collect()

    # Sort by timestamp (critical for temporal splits)
    sort_idx = np.argsort(ts_all)
    X_all = X_all[sort_idx]
    y_all = y_all[sort_idx]
    ts_all = ts_all[sort_idx]
    del sort_idx; gc.collect()

    print(f'  Dataset: {len(y_all):,} samples, {len(feat_names)} features, {X_all.nbytes/1e6:.0f}MB')
    return X_all, y_all, ts_all, feat_names


# ==================== Purged Temporal Walk-Forward ====================

def purged_temporal_split(ts, train_end_pct, test_end_pct, embargo_hours=24):
    """Split by date percentile with embargo gap.
    Returns train_mask, test_mask (boolean arrays)."""
    n = len(ts)
    train_end_idx = int(n * train_end_pct)
    test_start_idx = int(n * train_end_pct)  # same as train end before embargo
    test_end_idx = int(n * test_end_pct)

    train_end_date = pd.Timestamp(ts[train_end_idx])
    embargo_date = train_end_date + pd.Timedelta(hours=embargo_hours)

    train_mask = np.zeros(n, dtype=bool)
    test_mask = np.zeros(n, dtype=bool)

    # Train: everything before train_end_date
    train_mask[:train_end_idx] = True

    # Test: everything after embargo, up to test_end
    for j in range(test_start_idx, min(test_end_idx, n)):
        if pd.Timestamp(ts[j]) >= embargo_date:
            test_mask[j] = True

    return train_mask, test_mask


def undersample_balance(X, y, rng):
    pos_idx = np.where(y == 1)[0]; neg_idx = np.where(y == -1)[0]
    n_min = min(len(pos_idx), len(neg_idx))
    if n_min == 0:
        return X, y
    pos_sample = rng.choice(pos_idx, n_min, replace=False)
    neg_sample = rng.choice(neg_idx, n_min, replace=False)
    return X[np.sort(np.concatenate([pos_sample, neg_sample]))], \
           y[np.sort(np.concatenate([pos_sample, neg_sample]))]


def train_and_eval(X_all, y_all, ts_all, feat_names, experiment_name,
                   feature_mask=None, hparams=None):
    """Train with purged temporal walk-forward, return averaged metrics."""
    print(f'\n{"="*70}')
    print(f'EXPERIMENT: {experiment_name}')
    print(f'{"="*70}')

    if feature_mask is not None:
        X_data = X_all[:, feature_mask]
        fn = [feat_names[i] for i in range(len(feat_names)) if feature_mask[i]]
    else:
        X_data = X_all
        fn = feat_names

    # Filter to non-neutral only
    nz_mask = (y_all == 1) | (y_all == -1)
    X_nz = X_data[nz_mask]; y_nz = y_all[nz_mask]; ts_nz = ts_all[nz_mask]
    n = len(y_nz)
    print(f'  Non-neutral: {n:,} (L={( y_nz==1).sum():,}, S={(y_nz==-1).sum():,})')

    # Default hparams
    hp = {
        'max_iter': 400, 'max_depth': 6, 'learning_rate': 0.05,
        'min_samples_leaf': 50, 'max_leaf_nodes': 31,
        'l2_regularization': 1.0, 'max_bins': 128,
    }
    if hparams:
        hp.update(hparams)

    # 3-fold purged temporal walk-forward
    folds = [
        ('Fold1: 0-50%/50-67%', 0.50, 0.67),
        ('Fold2: 0-67%/67-83%', 0.67, 0.83),
        ('Fold3: 0-83%/83-100%', 0.83, 1.00),
    ]

    all_fold_metrics = []
    best_model = None
    best_fold_prec = 0

    for fold_name, train_pct, test_pct in folds:
        embargo_hours = 48  # 2x horizon for safety
        train_mask, test_mask = purged_temporal_split(ts_nz, train_pct, test_pct, embargo_hours)

        X_train = X_nz[train_mask]; y_train = y_nz[train_mask]
        X_test = X_nz[test_mask]; y_test = y_nz[test_mask]

        if len(X_test) < 100 or len(X_train) < 100:
            print(f'  {fold_name}: Skipped (too few samples)')
            continue

        train_start = pd.Timestamp(ts_nz[train_mask][0]).date()
        train_end = pd.Timestamp(ts_nz[train_mask][-1]).date()
        test_start = pd.Timestamp(ts_nz[test_mask][0]).date()
        test_end = pd.Timestamp(ts_nz[test_mask][-1]).date()

        rng = np.random.RandomState(42)
        X_bal, y_bal = undersample_balance(X_train, y_train, rng)

        model = HistGradientBoostingClassifier(
            max_iter=hp['max_iter'], max_depth=hp['max_depth'],
            learning_rate=hp['learning_rate'],
            min_samples_leaf=hp['min_samples_leaf'],
            max_leaf_nodes=hp['max_leaf_nodes'],
            l2_regularization=hp['l2_regularization'],
            max_bins=hp['max_bins'],
            early_stopping=True, validation_fraction=0.15,
            n_iter_no_change=20, random_state=42, verbose=0,
        )
        model.fit(X_bal, y_bal)

        y_proba = model.predict_proba(X_test)
        classes = list(model.classes_)

        print(f'\n  {fold_name} (train {train_start}→{train_end}, test {test_start}→{test_end})')
        print(f'    Train: {len(y_bal):,} balanced | Test: {len(y_test):,}')

        fold_metrics = {}
        for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
            if direction not in classes:
                continue
            cls_idx = classes.index(direction)
            probs = y_proba[:, cls_idx]

            for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                signals = probs >= thr
                n_sig = signals.sum()
                if n_sig == 0:
                    continue
                correct = (y_test[signals] == direction).sum()
                prec = correct / n_sig
                key = f'{label}_p{int(thr*100)}'
                fold_metrics[key] = {'precision': float(prec), 'n': int(n_sig)}

        all_fold_metrics.append(fold_metrics)

        # Print fold summary
        for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
            parts = []
            for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                key = f'{label}_p{int(thr*100)}'
                if key in fold_metrics:
                    m = fold_metrics[key]
                    parts.append(f'p{int(thr*100)}={m["precision"]:.3f}({m["n"]})')
            if parts:
                print(f'    {label}: {" | ".join(parts)}')

        # Track best model (by Fold3 for production)
        fold_best = max([m.get('precision', 0) for m in fold_metrics.values()] or [0])
        if fold_name.startswith('Fold3') and fold_best > best_fold_prec:
            best_fold_prec = fold_best
            best_model = model

        del X_train, y_train, X_test, y_test, X_bal, y_bal, y_proba
        gc.collect()

    del X_nz, y_nz, ts_nz; gc.collect()

    # Average metrics across folds
    avg_metrics = {}
    all_keys = set()
    for fm in all_fold_metrics:
        all_keys.update(fm.keys())

    for key in sorted(all_keys):
        precs = [fm[key]['precision'] for fm in all_fold_metrics if key in fm]
        ns = [fm[key]['n'] for fm in all_fold_metrics if key in fm]
        if precs:
            avg_metrics[key] = {
                'mean_precision': float(np.mean(precs)),
                'std_precision': float(np.std(precs)),
                'min_precision': float(np.min(precs)),
                'total_n': int(sum(ns)),
                'n_folds': len(precs),
            }

    print(f'\n  === Averaged across {len(all_fold_metrics)} folds ===')
    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        parts = []
        for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
            key = f'{label}_p{int(thr*100)}'
            if key in avg_metrics:
                m = avg_metrics[key]
                parts.append(f'p{int(thr*100)}={m["mean_precision"]:.3f}±{m["std_precision"]:.3f}')
        if parts:
            print(f'    {label}: {" | ".join(parts)}')

    # Feature importance from best model
    if best_model is not None:
        n_features = best_model.n_features_in_
        imp = np.zeros(n_features)
        for tree_list in best_model._predictors:
            for tree in tree_list:
                for node in tree.nodes:
                    if not node['is_leaf']:
                        fi = node['feature_idx']
                        if 0 <= fi < n_features:
                            imp[fi] += 1
        if imp.sum() > 0:
            imp = imp / imp.sum()
        top_idx = np.argsort(imp)[::-1][:10]
        print(f'\n  Top 10 features (Fold3):')
        for rank, idx in enumerate(top_idx):
            print(f'    {rank+1:2d}. {fn[idx]:<25s} {imp[idx]:.4f}')

    return avg_metrics, best_model, fn


# ==================== Main ====================

def main():
    t_start = time.time()
    print('=' * 70)
    print('ML DIRECTION MODEL V3 — PROPER TEMPORAL VALIDATION')
    print('=' * 70)

    market_ctx = build_market_context(DATA_DIR, top_n=15)

    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:15]]

    # ==========================================
    # Experiment A: Fixed 3%, 24h horizon (V2 config)
    # ==========================================
    print(f'\nBuilding dataset: fixed 3% target, 24h horizon...')
    X_a, y_a, ts_a, fn_a = build_dataset(tokens, market_ctx, horizon=24,
                                          target_type='fixed', threshold_pct=3.0)
    metrics_a, model_a, feats_a = train_and_eval(X_a, y_a, ts_a, fn_a,
                                                  'A: Fixed 3%, 24h horizon')

    # ==========================================
    # Experiment B: Adaptive target, 24h horizon
    # ==========================================
    print(f'\nBuilding dataset: adaptive target, 24h horizon...')
    X_b, y_b, ts_b, fn_b = build_dataset(tokens, market_ctx, horizon=24,
                                          target_type='adaptive', threshold_mult=1.5)
    del X_a, y_a, ts_a; gc.collect()
    metrics_b, model_b, feats_b = train_and_eval(X_b, y_b, ts_b, fn_b,
                                                  'B: Adaptive 1.5σ, 24h horizon')
    del X_b, y_b, ts_b; gc.collect()

    # ==========================================
    # Experiment C: Fixed 2%, 12h horizon (faster signals)
    # ==========================================
    print(f'\nBuilding dataset: fixed 2% target, 12h horizon...')
    X_c, y_c, ts_c, fn_c = build_dataset(tokens, market_ctx, horizon=12,
                                          target_type='fixed', threshold_pct=2.0)
    metrics_c, model_c, feats_c = train_and_eval(X_c, y_c, ts_c, fn_c,
                                                  'C: Fixed 2%, 12h horizon')
    del X_c, y_c, ts_c; gc.collect()

    # ==========================================
    # Experiment D: Fixed 5%, 48h horizon (larger moves)
    # ==========================================
    print(f'\nBuilding dataset: fixed 5% target, 48h horizon...')
    X_d, y_d, ts_d, fn_d = build_dataset(tokens, market_ctx, horizon=48,
                                          target_type='fixed', threshold_pct=5.0)
    metrics_d, model_d, feats_d = train_and_eval(X_d, y_d, ts_d, fn_d,
                                                  'D: Fixed 5%, 48h horizon')
    del X_d, y_d, ts_d; gc.collect()

    # ==========================================
    # Experiment E: More regularization (depth=4, l2=5.0)
    # ==========================================
    print(f'\nBuilding dataset: fixed 3%, 24h, heavy regularization...')
    X_e, y_e, ts_e, fn_e = build_dataset(tokens, market_ctx, horizon=24,
                                          target_type='fixed', threshold_pct=3.0)
    metrics_e, model_e, feats_e = train_and_eval(X_e, y_e, ts_e, fn_e,
        'E: Fixed 3%, heavy reg (d=4, l2=5)',
        hparams={'max_depth': 4, 'l2_regularization': 5.0, 'min_samples_leaf': 100,
                 'max_leaf_nodes': 15, 'learning_rate': 0.03})
    del X_e, y_e, ts_e; gc.collect()

    # ==========================================
    # Experiment F: Adaptive target, 48h horizon
    # ==========================================
    print(f'\nBuilding dataset: adaptive target, 48h horizon...')
    X_f, y_f, ts_f, fn_f = build_dataset(tokens, market_ctx, horizon=48,
                                          target_type='adaptive', threshold_mult=1.5)
    metrics_f, model_f, feats_f = train_and_eval(X_f, y_f, ts_f, fn_f,
                                                  'F: Adaptive 1.5σ, 48h horizon')
    del X_f, y_f, ts_f; gc.collect()

    del market_ctx; gc.collect()

    # ==========================================
    # Final Comparison
    # ==========================================
    print(f'\n{"="*70}')
    print('FINAL COMPARISON: All Experiments')
    print(f'{"="*70}')
    print(f'{"Experiment":<40} {"L p60":>8} {"L p70":>8} {"S p60":>8} {"S p70":>8}')
    print(f'{"-"*72}')

    all_results = [
        ('A: Fixed 3%, 24h', metrics_a),
        ('B: Adaptive 1.5σ, 24h', metrics_b),
        ('C: Fixed 2%, 12h', metrics_c),
        ('D: Fixed 5%, 48h', metrics_d),
        ('E: Fixed 3%, heavy reg', metrics_e),
        ('F: Adaptive 1.5σ, 48h', metrics_f),
    ]

    best_name = None
    best_score = 0

    for name, m in all_results:
        lp60 = m.get('LONG_p60', {}).get('mean_precision', 0)
        lp70 = m.get('LONG_p70', {}).get('mean_precision', 0)
        sp60 = m.get('SHORT_p60', {}).get('mean_precision', 0)
        sp70 = m.get('SHORT_p70', {}).get('mean_precision', 0)
        print(f'{name:<40} {lp60:>8.4f} {lp70:>8.4f} {sp60:>8.4f} {sp70:>8.4f}')

        # Score: average of p60 and p70 for both directions
        score = np.mean([lp60, lp70, sp60, sp70])
        if score > best_score:
            best_score = score
            best_name = name

    print(f'\nBest experiment: {best_name} (avg precision={best_score:.4f})')

    # Save best model
    best_models = {'A': model_a, 'B': model_b, 'C': model_c,
                   'D': model_d, 'E': model_e, 'F': model_f}
    best_feats = {'A': feats_a, 'B': feats_b, 'C': feats_c,
                  'D': feats_d, 'E': feats_e, 'F': feats_f}
    best_key = best_name[0]  # 'A', 'B', etc.

    if best_models[best_key] is not None:
        joblib.dump(best_models[best_key], str(RESULTS_DIR / 'ml_dir_v3_model.joblib'))
        joblib.dump(best_feats[best_key], str(RESULTS_DIR / 'ml_dir_v3_features.joblib'))
        config = {'experiment': best_name, 'avg_precision': best_score}
        joblib.dump(config, str(RESULTS_DIR / 'ml_dir_v3_config.joblib'))
        print(f'Saved V3 model ({best_name})')

    # Verdict
    print(f'\n{"="*70}')
    if best_score > 0.60:
        print('VERDICT: Model has meaningful edge (>60% avg precision)')
        print('Proceed to adversarial validation and strategy update')
    elif best_score > 0.55:
        print('VERDICT: Model has marginal edge (55-60% avg precision)')
        print('May be profitable with tight risk management')
    else:
        print('VERDICT: Model has minimal/no edge (<55% avg precision)')
        print('Need fundamentally different features or approach')
    print(f'{"="*70}')

    elapsed = time.time() - t_start
    print(f'\nTotal time: {elapsed:.0f}s')


if __name__ == '__main__':
    main()
