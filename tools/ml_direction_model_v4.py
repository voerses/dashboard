"""
ML Direction Model V4 — Cross-Token Generalization
====================================================
Addresses the key adversarial finding from V3: half the "edge" was token-specific
memorization that doesn't generalize to unseen tokens.

Key changes from V3:
  1. Leave-K-tokens-out (LOTO) validation within temporal folds
  2. Cross-sectional (rank-based) features that naturally generalize
  3. Report in-sample-token vs OOS-token precision separately
  4. The headline metric is OOS-token temporal-OOS precision
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


# ==================== Helpers ====================

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

def undersample_balance(X, y, rng):
    pos_idx = np.where(y == 1)[0]; neg_idx = np.where(y == -1)[0]
    n_min = min(len(pos_idx), len(neg_idx))
    if n_min == 0:
        return X, y
    pos_sample = rng.choice(pos_idx, n_min, replace=False)
    neg_sample = rng.choice(neg_idx, n_min, replace=False)
    idx = np.sort(np.concatenate([pos_sample, neg_sample]))
    return X[idx], y[idx]


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
    d_pdm = np.where(d_pdm > d_mdm, d_pdm, 0)
    d_mdm = np.where(d_mdm > d_pdm, d_mdm, 0)
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

    # Cross-asset breadth + dispersion
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
    del above_ema20_matrix; gc.collect()

    # Funding mean (market-level)
    funding_matrix = np.full((n_btc, top_n), np.nan, dtype=np.float32)
    for i, token in enumerate(tokens):
        try:
            df = pd.read_parquet(data_dir / f'{token}_1h.parquet')
            if 'funding_rate' in df.columns:
                aligned = pd.Series(df['funding_rate'].values.astype(np.float32),
                                    index=df.index).reindex(btc_idx)
                funding_matrix[:, i] = aligned.values
            del df
        except Exception:
            continue
    gc.collect()

    market_funding_mean = np.nan_to_num(np.nanmean(funding_matrix, axis=1), nan=0.0)
    del funding_matrix; gc.collect()

    context = pd.DataFrame({
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        'market_funding_mean': market_funding_mean,
        '_btc_ret_1h_series': btc_ret_1h,
        '_ret_24h_matrix': 0,  # placeholder; actual matrix passed separately
    }, index=btc_idx)

    print(f'  Market context: {len(context):,} bars ({time.time()-t0:.1f}s)')
    return context, ret_24h_matrix, btc_idx


# ==================== Cross-Sectional Features ====================

def compute_cross_sectional_features(token_data_dict, btc_idx):
    """Compute rank-based features across all tokens at each timestamp.

    token_data_dict: {token_name: {'close': pd.Series aligned to btc_idx, ...}}

    Returns dict of {token_name: {feature_name: np.array}} with cross-sectional features.
    """
    t0 = time.time()
    print('  Computing cross-sectional features...')

    tokens = list(token_data_dict.keys())
    n = len(btc_idx)
    n_tokens = len(tokens)

    # Build matrices aligned to btc_idx
    ret_24h_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    ret_1h_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    vol_20_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    rsi_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)

    for i, token in enumerate(tokens):
        d = token_data_dict[token]
        close = d['close_aligned']  # aligned to btc_idx

        # 24h return
        r24 = np.full(n, np.nan)
        r24[24:] = np.log(close[24:] / np.maximum(close[:-24], 1e-10))
        ret_24h_mat[:, i] = r24

        # 1h return
        r1 = np.full(n, np.nan)
        r1[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
        ret_1h_mat[:, i] = r1

        # 20h volatility
        v20 = pd.Series(r1).rolling(20, min_periods=10).std().values
        vol_20_mat[:, i] = v20

        # RSI
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        rs = ema_vec(gain, 14) / np.maximum(ema_vec(loss, 14), 1e-10)
        rsi_mat[:, i] = 100 - 100 / (1 + rs)

    # Compute percentile ranks (0-1) at each timestamp
    # Using scipy-free approach: rank / count
    cross_features = {}
    for i, token in enumerate(tokens):
        cf = {}

        # Ret 24h rank
        rank = np.full(n, np.nan, dtype=np.float32)
        for t in range(24, n):
            vals = ret_24h_mat[t, :]
            valid = ~np.isnan(vals)
            if valid.sum() >= 3:
                v = vals[valid]
                # percentile rank of this token among peers
                if not np.isnan(vals[i]):
                    rank[t] = np.mean(v <= vals[i])
        cf['xsect_ret24h_rank'] = np.nan_to_num(rank, nan=0.5)

        # Ret 1h rank
        rank = np.full(n, np.nan, dtype=np.float32)
        for t in range(1, n):
            vals = ret_1h_mat[t, :]
            valid = ~np.isnan(vals)
            if valid.sum() >= 3:
                v = vals[valid]
                if not np.isnan(vals[i]):
                    rank[t] = np.mean(v <= vals[i])
        cf['xsect_ret1h_rank'] = np.nan_to_num(rank, nan=0.5)

        # Vol rank
        rank = np.full(n, np.nan, dtype=np.float32)
        for t in range(20, n):
            vals = vol_20_mat[t, :]
            valid = ~np.isnan(vals)
            if valid.sum() >= 3:
                v = vals[valid]
                if not np.isnan(vals[i]):
                    rank[t] = np.mean(v <= vals[i])
        cf['xsect_vol_rank'] = np.nan_to_num(rank, nan=0.5)

        # RSI rank
        rank = np.full(n, np.nan, dtype=np.float32)
        for t in range(14, n):
            vals = rsi_mat[t, :]
            valid = ~np.isnan(vals)
            if valid.sum() >= 3:
                v = vals[valid]
                if not np.isnan(vals[i]):
                    rank[t] = np.mean(v <= vals[i])
        cf['xsect_rsi_rank'] = np.nan_to_num(rank, nan=0.5)

        # Relative strength vs market median
        median_ret = np.nanmedian(ret_24h_mat, axis=1)
        cf['relative_strength_24h'] = np.nan_to_num(
            ret_24h_mat[:, i] - median_ret, nan=0.0).astype(np.float32)

        cross_features[token] = cf

    del ret_24h_mat, ret_1h_mat, vol_20_mat, rsi_mat
    gc.collect()

    print(f'    Cross-sectional features computed ({time.time()-t0:.1f}s)')
    return cross_features


# ==================== Feature Engineering ====================

def compute_features(close, high, low, volume, funding=None):
    """34 base TA features (same as V3)."""
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


# ==================== Per-Token Dataset ====================

def build_per_token_datasets(tokens, market_ctx, btc_idx, cross_features,
                              horizon=24, target_type='adaptive', threshold_mult=1.5):
    """Build per-token (X, y, timestamps) arrays with cross-sectional features.
    Returns dict: {token_name: (X, y, timestamps, feat_names)}
    """
    datasets = {}
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
                thr = 0.03
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
        features['market_funding_mean'] = np.nan_to_num(ctx['market_funding_mean'].values, nan=0.0)

        token_ret_1h = np.zeros(n)
        token_ret_1h[1:] = np.log(close[1:] / close[:-1])
        btc_ret = np.nan_to_num(ctx['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(pd.Series(btc_ret)).values
        features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)
        del ctx

        # Add cross-sectional features (aligned to btc_idx, need reindexing)
        if token in cross_features:
            cf = cross_features[token]
            # Cross-sectional features are aligned to btc_idx; map to token's own index
            cf_df = pd.DataFrame(cf, index=btc_idx)
            cf_aligned = cf_df.reindex(df.index, method='ffill')
            for col in cf_aligned.columns:
                features[col] = np.nan_to_num(cf_aligned[col].values.astype(np.float32), nan=0.5)
            del cf_df, cf_aligned

        fn = sorted([k for k in features.keys() if not k.startswith('_')])
        X = np.column_stack([features[k] for k in fn]).astype(np.float32)

        valid = np.ones(n, dtype=bool)
        valid[:200] = False; valid[-horizon:] = False
        nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
        valid = valid & ~nan_mask

        if valid.sum() > 100:
            datasets[token] = {
                'X': X[valid],
                'y': labels[valid],
                'ts': df.index[valid].values,
            }
            if feat_names is None:
                feat_names = fn

        del df, close, high, low, volume, funding, features, X, labels
        gc.collect()

        if (i + 1) % 5 == 0:
            print(f'  Loaded {i+1}/{len(tokens)} tokens')

    return datasets, feat_names


# ==================== LOTO Temporal Walk-Forward ====================

def loto_temporal_eval(datasets, feat_names, experiment_name,
                       train_tokens, test_tokens, hparams=None):
    """Leave-K-tokens-out temporal validation.

    Train on train_tokens (temporal train period), test on test_tokens (temporal test period).
    Returns metrics dict.
    """
    print(f'\n  LOTO: train={len(train_tokens)} tokens, test={len(test_tokens)} tokens')

    # Collect all timestamps to determine temporal split
    all_ts = []
    for token in train_tokens + test_tokens:
        if token in datasets:
            all_ts.append(datasets[token]['ts'])
    if not all_ts:
        print('    No data!')
        return {}
    ts_sorted = np.sort(np.concatenate(all_ts))
    n_ts = len(ts_sorted)

    # Default hparams
    hp = {
        'max_iter': 400, 'max_depth': 5, 'learning_rate': 0.05,
        'min_samples_leaf': 80, 'max_leaf_nodes': 31,
        'l2_regularization': 2.0, 'max_bins': 128,
    }
    if hparams:
        hp.update(hparams)

    # Temporal folds: expanding window
    folds = [
        ('Fold1: 0-50%/50-67%', 0.50, 0.67),
        ('Fold2: 0-67%/67-83%', 0.67, 0.83),
        ('Fold3: 0-83%/83-100%', 0.83, 1.00),
    ]

    all_fold_metrics = []
    best_model = None
    best_fold_score = 0
    embargo_hours = 48

    for fold_name, train_pct, test_pct in folds:
        train_end_idx = int(n_ts * train_pct)
        train_cutoff = pd.Timestamp(ts_sorted[train_end_idx])
        embargo_end = train_cutoff + pd.Timedelta(hours=embargo_hours)
        test_end_idx = int(n_ts * test_pct)
        test_end_date = pd.Timestamp(ts_sorted[min(test_end_idx, n_ts - 1)])

        # Build train set from TRAIN tokens only (temporal train period)
        train_X_list, train_y_list = [], []
        for token in train_tokens:
            if token not in datasets:
                continue
            d = datasets[token]
            mask = d['ts'] <= np.datetime64(train_cutoff)
            nz = (d['y'] == 1) | (d['y'] == -1)
            mask = mask & nz
            if mask.sum() > 0:
                train_X_list.append(d['X'][mask])
                train_y_list.append(d['y'][mask])

        if not train_X_list:
            continue

        X_train = np.vstack(train_X_list)
        y_train = np.concatenate(train_y_list)
        del train_X_list, train_y_list

        # Build test set from TEST tokens only (temporal test period)
        test_X_list, test_y_list = [], []
        for token in test_tokens:
            if token not in datasets:
                continue
            d = datasets[token]
            mask = (d['ts'] >= np.datetime64(embargo_end)) & (d['ts'] <= np.datetime64(test_end_date))
            nz = (d['y'] == 1) | (d['y'] == -1)
            mask = mask & nz
            if mask.sum() > 0:
                test_X_list.append(d['X'][mask])
                test_y_list.append(d['y'][mask])

        if not test_X_list:
            del X_train, y_train
            continue

        X_test = np.vstack(test_X_list)
        y_test = np.concatenate(test_y_list)
        del test_X_list, test_y_list

        if len(X_test) < 50 or len(X_train) < 100:
            del X_train, y_train, X_test, y_test
            continue

        # Train
        rng = np.random.RandomState(42)
        X_bal, y_bal = undersample_balance(X_train, y_train, rng)
        del X_train, y_train

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
        del X_bal, y_bal

        # Evaluate
        y_proba = model.predict_proba(X_test)
        classes = list(model.classes_)

        train_end_str = train_cutoff.date()
        test_start_str = embargo_end.date()
        test_end_str = test_end_date.date()
        print(f'    {fold_name} (train→{train_end_str}, test {test_start_str}→{test_end_str})')
        print(f'      Test: {len(y_test):,} samples (L={(y_test==1).sum():,}, S={(y_test==-1).sum():,})')

        fold_metrics = {}
        for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
            if direction not in classes:
                continue
            cls_idx = classes.index(direction)
            probs = y_proba[:, cls_idx]

            parts = []
            for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                signals = probs >= thr
                n_sig = signals.sum()
                if n_sig == 0:
                    continue
                correct = (y_test[signals] == direction).sum()
                prec = correct / n_sig
                key = f'{label}_p{int(thr*100)}'
                fold_metrics[key] = {'precision': float(prec), 'n': int(n_sig)}
                parts.append(f'p{int(thr*100)}={prec:.3f}({n_sig})')
            if parts:
                print(f'      {label}: {" | ".join(parts)}')

        all_fold_metrics.append(fold_metrics)

        # Track best model
        fold_score = np.mean([v['precision'] for v in fold_metrics.values()] or [0])
        if fold_name.startswith('Fold3') and fold_score > best_fold_score:
            best_fold_score = fold_score
            best_model = model

        del X_test, y_test, y_proba
        gc.collect()

    # Average metrics
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

    print(f'\n  === {experiment_name}: Averaged across {len(all_fold_metrics)} folds ===')
    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        parts = []
        for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
            key = f'{label}_p{int(thr*100)}'
            if key in avg_metrics:
                m = avg_metrics[key]
                parts.append(f'p{int(thr*100)}={m["mean_precision"]:.3f}±{m["std_precision"]:.3f}')
        if parts:
            print(f'    {label}: {" | ".join(parts)}')

    # Feature importance
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
            fname = feat_names[idx] if idx < len(feat_names) else f'feature_{idx}'
            print(f'    {rank+1:2d}. {fname:<30s} {imp[idx]:.4f}')

    return avg_metrics, best_model


# ==================== Main ====================

def main():
    t_start = time.time()
    print('=' * 70)
    print('ML DIRECTION MODEL V4 — CROSS-TOKEN GENERALIZATION')
    print('=' * 70)

    market_ctx, ret_24h_matrix, btc_idx = build_market_context(DATA_DIR, top_n=15)

    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:15]]
    print(f'\nTokens ({len(tokens)}): {tokens}')

    # ======================================================
    # Build cross-sectional features
    # ======================================================
    print('\n--- Building cross-sectional feature matrices ---')
    token_data_dict = {}
    for token in tokens:
        fpath = DATA_DIR / f'{token}_1h.parquet'
        if not fpath.exists():
            continue
        df = pd.read_parquet(fpath)
        if len(df) < 500:
            del df; continue
        close_aligned = pd.Series(
            df['close'].values.astype(np.float32), index=df.index
        ).reindex(btc_idx).values
        # Fill forward NaNs
        close_aligned = pd.Series(close_aligned).ffill().values
        token_data_dict[token] = {'close_aligned': close_aligned}
        del df
    gc.collect()

    # Use vectorized cross-sectional features (fast)
    cross_features = compute_cross_sectional_fast(token_data_dict, btc_idx)
    del token_data_dict; gc.collect()
    del ret_24h_matrix; gc.collect()

    # ======================================================
    # Build per-token datasets
    # ======================================================
    print('\n--- Building per-token datasets ---')
    datasets, feat_names = build_per_token_datasets(
        tokens, market_ctx, btc_idx, cross_features,
        horizon=24, target_type='adaptive', threshold_mult=1.5
    )
    del market_ctx, cross_features, btc_idx; gc.collect()

    active_tokens = [t for t in tokens if t in datasets]
    print(f'\n  Active tokens: {len(active_tokens)}: {active_tokens}')
    total_samples = sum(len(d['y']) for d in datasets.values())
    print(f'  Total samples: {total_samples:,}')
    print(f'  Features: {len(feat_names)}: {feat_names}')

    # ======================================================
    # Experiment K: LOTO (train 10, test 5) — baseline
    # ======================================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT K: LOTO baseline (no cross-sectional, for comparison)')
    print(f'{"#"*70}')

    # Build a version without cross-sectional features for comparison
    # We'll use feature_mask to exclude xsect features
    xsect_cols = [i for i, f in enumerate(feat_names) if f.startswith('xsect_') or f == 'relative_strength_24h']
    base_cols = [i for i in range(len(feat_names)) if i not in xsect_cols]

    # LOTO group A: train on first 10, test on last 5
    train_a = active_tokens[:10]
    test_a = active_tokens[10:]
    # LOTO group B: train on last 10, test on first 5
    train_b = active_tokens[5:]
    test_b = active_tokens[:5]

    # Mask datasets to exclude xsect features
    datasets_base = {}
    for token, d in datasets.items():
        datasets_base[token] = {
            'X': d['X'][:, base_cols],
            'y': d['y'],
            'ts': d['ts'],
        }

    base_feat_names = [feat_names[i] for i in base_cols]

    print(f'\n  --- LOTO Group A: train on {train_a[:3]}.../{len(train_a)}, test on {test_a} ---')
    metrics_ka, model_ka = loto_temporal_eval(
        datasets_base, base_feat_names, 'K-A (base, group A)',
        train_a, test_a)

    print(f'\n  --- LOTO Group B: train on ...{train_b[-3:]}/{len(train_b)}, test on {test_b} ---')
    metrics_kb, model_kb = loto_temporal_eval(
        datasets_base, base_feat_names, 'K-B (base, group B)',
        train_b, test_b)

    del datasets_base; gc.collect()

    # ======================================================
    # Experiment L: LOTO + cross-sectional features
    # ======================================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT L: LOTO + cross-sectional features')
    print(f'{"#"*70}')

    print(f'\n  --- LOTO Group A ---')
    metrics_la, model_la = loto_temporal_eval(
        datasets, feat_names, 'L-A (xsect, group A)',
        train_a, test_a)

    print(f'\n  --- LOTO Group B ---')
    metrics_lb, model_lb = loto_temporal_eval(
        datasets, feat_names, 'L-B (xsect, group B)',
        train_b, test_b)

    # ======================================================
    # Experiment M: LOTO + more regularization
    # ======================================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT M: LOTO + cross-sectional + heavy regularization')
    print(f'{"#"*70}')

    heavy_hp = {
        'max_depth': 4, 'l2_regularization': 5.0,
        'min_samples_leaf': 120, 'max_leaf_nodes': 15,
        'learning_rate': 0.03,
    }

    print(f'\n  --- LOTO Group A ---')
    metrics_ma, model_ma = loto_temporal_eval(
        datasets, feat_names, 'M-A (heavy reg, group A)',
        train_a, test_a, hparams=heavy_hp)

    print(f'\n  --- LOTO Group B ---')
    metrics_mb, model_mb = loto_temporal_eval(
        datasets, feat_names, 'M-B (heavy reg, group B)',
        train_b, test_b, hparams=heavy_hp)

    # ======================================================
    # Experiment N: All tokens train (V3-style) for in-sample comparison
    # ======================================================
    print(f'\n{"#"*70}')
    print('EXPERIMENT N: All-token temporal (V3-style, for comparison)')
    print(f'{"#"*70}')

    metrics_n, model_n = loto_temporal_eval(
        datasets, feat_names, 'N (all tokens, temporal only)',
        active_tokens, active_tokens)

    del datasets; gc.collect()

    # ======================================================
    # Final Comparison
    # ======================================================
    print(f'\n{"="*70}')
    print('FINAL COMPARISON: LOTO vs All-Token')
    print(f'{"="*70}')

    def avg_group(ma, mb):
        """Average metrics from two LOTO groups."""
        keys = set(list(ma.keys()) + list(mb.keys()))
        out = {}
        for k in keys:
            precs = []
            if k in ma: precs.append(ma[k]['mean_precision'])
            if k in mb: precs.append(mb[k]['mean_precision'])
            if precs:
                out[k] = {'mean_precision': np.mean(precs)}
        return out

    all_results = [
        ('K: LOTO base (no xsect)', avg_group(metrics_ka, metrics_kb)),
        ('L: LOTO + cross-sectional', avg_group(metrics_la, metrics_lb)),
        ('M: LOTO + xsect + heavy reg', avg_group(metrics_ma, metrics_mb)),
        ('N: All-token temporal', metrics_n),
    ]

    print(f'{"Experiment":<35} {"L p55":>8} {"L p60":>8} {"L p65":>8} {"S p55":>8} {"S p60":>8} {"S p65":>8}')
    print(f'{"-"*83}')

    best_name = None
    best_score = 0

    for name, m in all_results:
        vals = []
        parts = []
        for key in ['LONG_p55', 'LONG_p60', 'LONG_p65', 'SHORT_p55', 'SHORT_p60', 'SHORT_p65']:
            v = m.get(key, {}).get('mean_precision', 0)
            parts.append(f'{v:>8.4f}')
            vals.append(v)
        print(f'{name:<35} {" ".join(parts)}')
        score = np.mean(vals)
        if score > best_score:
            best_score = score
            best_name = name

    print(f'\nBest experiment: {best_name} (avg precision={best_score:.4f})')

    # Compare LOTO vs all-token to measure token-specific overfitting
    n_lp60 = metrics_n.get('LONG_p60', {}).get('mean_precision', 0)
    loto_lp60 = avg_group(metrics_la, metrics_lb).get('LONG_p60', {}).get('mean_precision', 0)
    gap = n_lp60 - loto_lp60

    print(f'\n  Token-specific overfitting gap:')
    print(f'    All-token LONG p60:  {n_lp60:.4f}')
    print(f'    LOTO LONG p60:       {loto_lp60:.4f}')
    print(f'    Gap (overfitting):   {gap:+.4f}')
    if gap > 0.05:
        print(f'    CAUTION: >5pp gap indicates significant token-specific overfitting')
    elif gap > 0.02:
        print(f'    MILD: 2-5pp gap, some token specificity but manageable')
    else:
        print(f'    GOOD: <2pp gap, model generalizes well across tokens')

    # Save best model
    best_models = {
        'K': model_ka, 'L': model_la, 'M': model_ma, 'N': model_n
    }
    best_key = best_name[0]  # 'K', 'L', etc.
    if best_models.get(best_key) is not None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(best_models[best_key], str(RESULTS_DIR / 'ml_dir_v4_model.joblib'))
        joblib.dump(feat_names, str(RESULTS_DIR / 'ml_dir_v4_features.joblib'))
        config = {'experiment': best_name, 'avg_precision': best_score}
        joblib.dump(config, str(RESULTS_DIR / 'ml_dir_v4_config.joblib'))
        print(f'  Saved V4 model ({best_name})')

    # Verdict
    print(f'\n{"="*70}')
    if best_score > 0.57:
        print('VERDICT: Model generalizes across tokens (>57% avg precision on LOTO)')
    elif best_score > 0.53:
        print('VERDICT: Marginal cross-token edge (53-57%)')
    else:
        print('VERDICT: No cross-token edge (<53%)')
    print(f'{"="*70}')

    elapsed = time.time() - t_start
    print(f'\nTotal time: {elapsed:.0f}s')


def compute_cross_sectional_fast(token_data_dict, btc_idx):
    """Vectorized cross-sectional features (much faster than per-bar loop)."""
    t0 = time.time()
    print('  Computing cross-sectional features (vectorized)...')

    tokens = list(token_data_dict.keys())
    n = len(btc_idx)
    n_tokens = len(tokens)

    # Build matrices
    close_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    for i, token in enumerate(tokens):
        close_mat[:, i] = token_data_dict[token]['close_aligned']

    # 24h returns
    ret_24h_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    ret_24h_mat[24:] = np.log(np.maximum(close_mat[24:], 1e-10) /
                               np.maximum(close_mat[:-24], 1e-10))

    # 1h returns
    ret_1h_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    ret_1h_mat[1:] = np.log(np.maximum(close_mat[1:], 1e-10) /
                              np.maximum(close_mat[:-1], 1e-10))

    # 20h volatility per token
    vol_20_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    for i in range(n_tokens):
        vol_20_mat[:, i] = pd.Series(ret_1h_mat[:, i]).rolling(20, min_periods=10).std().values

    # RSI per token
    rsi_mat = np.full((n, n_tokens), np.nan, dtype=np.float32)
    for i in range(n_tokens):
        delta = np.diff(close_mat[:, i], prepend=close_mat[0, i])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        rs = ema_vec(gain, 14) / np.maximum(ema_vec(loss, 14), 1e-10)
        rsi_mat[:, i] = 100 - 100 / (1 + rs)

    del close_mat; gc.collect()

    # Compute ranks using argsort (vectorized, no per-bar loop)
    cross_features = {}

    for i, token in enumerate(tokens):
        cf = {}

        # Percentile rank via counting: for each bar, what fraction of tokens have lower value
        # ret_24h rank
        valid_count = np.sum(~np.isnan(ret_24h_mat), axis=1)
        this_val = ret_24h_mat[:, i]
        # Count how many tokens <= this token's value at each bar
        rank_count = np.nansum(ret_24h_mat <= this_val[:, None], axis=1).astype(np.float32)
        rank = np.where(valid_count >= 3, rank_count / np.maximum(valid_count, 1), 0.5)
        cf['xsect_ret24h_rank'] = np.nan_to_num(rank, nan=0.5).astype(np.float32)

        # ret_1h rank
        valid_count = np.sum(~np.isnan(ret_1h_mat), axis=1)
        this_val = ret_1h_mat[:, i]
        rank_count = np.nansum(ret_1h_mat <= this_val[:, None], axis=1).astype(np.float32)
        rank = np.where(valid_count >= 3, rank_count / np.maximum(valid_count, 1), 0.5)
        cf['xsect_ret1h_rank'] = np.nan_to_num(rank, nan=0.5).astype(np.float32)

        # vol rank
        valid_count = np.sum(~np.isnan(vol_20_mat), axis=1)
        this_val = vol_20_mat[:, i]
        rank_count = np.nansum(vol_20_mat <= this_val[:, None], axis=1).astype(np.float32)
        rank = np.where(valid_count >= 3, rank_count / np.maximum(valid_count, 1), 0.5)
        cf['xsect_vol_rank'] = np.nan_to_num(rank, nan=0.5).astype(np.float32)

        # RSI rank
        valid_count = np.sum(~np.isnan(rsi_mat), axis=1)
        this_val = rsi_mat[:, i]
        rank_count = np.nansum(rsi_mat <= this_val[:, None], axis=1).astype(np.float32)
        rank = np.where(valid_count >= 3, rank_count / np.maximum(valid_count, 1), 0.5)
        cf['xsect_rsi_rank'] = np.nan_to_num(rank, nan=0.5).astype(np.float32)

        # Relative strength vs market median
        median_ret = np.nanmedian(ret_24h_mat, axis=1)
        cf['relative_strength_24h'] = np.nan_to_num(
            ret_24h_mat[:, i] - median_ret, nan=0.0).astype(np.float32)

        cross_features[token] = cf

    del ret_24h_mat, ret_1h_mat, vol_20_mat, rsi_mat
    gc.collect()

    print(f'    Done ({time.time()-t0:.1f}s)')
    return cross_features


if __name__ == '__main__':
    main()
