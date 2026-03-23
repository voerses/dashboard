#!/usr/bin/env python3
"""Experiment B: Walk-Forward ML Retraining
============================================
Hypothesis: Walk-forward retraining (rolling window, retrain periodically)
adapts to changing market structure that made the static V2 model overfit OOS.

Design:
  - Training window: 12 months rolling
  - Retraining frequency: every 3 months
  - Test period per window: next 3 months
  - First test window starts July 2025
  - ~3 test windows: Jul-Sep 2025, Oct-Dec 2025, Jan-Mar 2026

Two configurations tested:
  1. 24h horizon / 3% threshold  (V2 standard)
  2. 8h horizon / 1.5% threshold (shorter-term variant)
"""

import numpy as np
import pandas as pd
import joblib
import os
import sys
import time
import json
import warnings
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
SAVE_DIR = Path('/workspace/crypto_backtest/results/exp_b')
SAVE_DIR.mkdir(parents=True, exist_ok=True)

TOP_N = 30
MIN_BARS = 500

# Walk-forward schedule
WALK_FORWARD_WINDOWS = [
    # (test_start, test_end)  -- train is 12 months before test_start
    ('2025-07-01', '2025-10-01'),
    ('2025-10-01', '2026-01-01'),
    ('2026-01-01', '2026-04-01'),
]

CONFIGS = [
    {'name': '24h/3%',  'horizon': 24, 'threshold_pct': 3.0},
    {'name': '8h/1.5%', 'horizon': 8,  'threshold_pct': 1.5},
]

V2_FEATURES = [
    'adx', 'atr_pct', 'bb_pct', 'bb_width', 'body_pct', 'btc_ret_1h', 'btc_ret_24h',
    'btc_vol_24h', 'consec', 'dispersion_zscore', 'dist_high', 'dist_low', 'donch_pos',
    'ema_align', 'ema_dist_10', 'ema_dist_20', 'ema_dist_50', 'funding', 'funding_ma48',
    'funding_ma8', 'macd_hist_norm', 'macd_norm', 'market_breadth_ema20', 'market_regime',
    'minus_di', 'plus_di', 'ret_1', 'ret_12', 'ret_168', 'ret_24', 'ret_4', 'ret_48',
    'rsi', 'rsi_mom', 'token_btc_corr_168h', 'vol_20', 'vol_5', 'vol_50', 'vol_ratio',
    'vol_ratio_5_20', 'wick_ratio',
]


# ── Helpers ──────────────────────────────────────────────────────────────
def _ema(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values

def _rmean(arr, w):
    cs = np.cumsum(arr)
    out = np.full(len(arr), np.nan)
    if len(arr) >= w:
        out[w-1] = cs[w-1] / w
    if len(arr) > w:
        out[w:] = (cs[w:] - cs[:-w]) / w
    return out

def _rstd(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).std().values

def _rmax(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).max().values

def _rmin(arr, w):
    return pd.Series(arr).rolling(w, min_periods=w).min().values


# ── Feature computation (identical to V2/v5) ─────────────────────────────
def compute_base_features(close, high, low, volume, funding=None):
    """30-33 base TA features, exactly matching V2 training."""
    n = len(close)
    def lr(s):
        r = np.zeros(n); r[s:] = np.log(close[s:] / close[:-s]); return r

    ret_1=lr(1); ret_4=lr(4); ret_12=lr(12); ret_24=lr(24); ret_48=lr(48); ret_168=lr(168)

    e10=_ema(close,10); e20=_ema(close,20); e50=_ema(close,50)
    ed10=(close-e10)/np.maximum(close,1e-10)
    ed20=(close-e20)/np.maximum(close,1e-10)
    ed50=(close-e50)/np.maximum(close,1e-10)
    ea=np.sign(close-e10)+np.sign(close-e20)+np.sign(close-e50)

    e12=_ema(close,12); e26=_ema(close,26); macd=e12-e26; msig=_ema(macd,9)
    mn=macd/np.maximum(close,1e-10); mhn=(macd-msig)/np.maximum(close,1e-10)

    d=np.diff(close,prepend=close[0]); g=np.where(d>0,d,0.0); l_=np.where(d<0,-d,0.0)
    rs=_ema(g,14)/np.maximum(_ema(l_,14),1e-10); rsi=100-100/(1+rs); rm=np.diff(rsi,prepend=rsi[0])

    bm=_rmean(close,20); bs=np.nan_to_num(_rstd(close,20),nan=1.0)
    bu=bm+2*bs; bl=bm-2*bs
    bw=np.where(np.nan_to_num(bm,nan=1)>0,(bu-bl)/np.maximum(np.nan_to_num(bm,nan=1),1e-10),0)
    bp=np.where(bu>bl,(close-bl)/np.maximum(bu-bl,1e-10),0.5)

    pc=np.roll(close,1); pc[0]=close[0]
    tr=np.maximum(high-low,np.maximum(np.abs(high-pc),np.abs(low-pc)))
    atr_arr=_ema(tr,14); ap=atr_arr/np.maximum(close,1e-10)

    v5=_rstd(ret_1,5); v20=_rstd(ret_1,20); v50=_rstd(ret_1,50)
    vr52=np.where(np.nan_to_num(v20,nan=1)>0,
                  np.nan_to_num(v5,nan=0)/np.maximum(np.nan_to_num(v20,nan=1),1e-10), 1.0)
    vm20=_rmean(volume,20)
    vr=np.where(np.nan_to_num(vm20,nan=1)>0,
                volume/np.maximum(np.nan_to_num(vm20,nan=1),1e-10), 1.0)

    pdm=np.maximum(np.diff(high,prepend=high[0]),0)
    mdm=np.maximum(-np.diff(low,prepend=low[0]),0)
    pdm_c=np.where(pdm>mdm,pdm,0); mdm_c=np.where(mdm>pdm,mdm,0)
    sp=_ema(pdm_c,14); sm=_ema(mdm_c,14)
    pdi=100*sp/np.maximum(atr_arr,1e-10); mdi=100*sm/np.maximum(atr_arr,1e-10)
    dx=100*np.abs(pdi-mdi)/np.maximum(pdi+mdi,1e-10); adx=_ema(dx,14)

    dh=np.nan_to_num(_rmax(high,20),nan=high[0])
    dl=np.nan_to_num(_rmin(low,20),nan=low[0])
    dp=np.where(dh>dl,(close-dl)/np.maximum(dh-dl,1e-10),0.5)

    bpct=np.diff(close,prepend=close[0])/np.maximum(close,1e-10)
    cr=high-low; uw=high-np.maximum(close,pc); lw=np.minimum(close,pc)-low
    wr=np.where(cr>0,(uw-lw)/np.maximum(cr,1e-10),0)

    co=np.zeros(n)
    for i in range(1, n):
        if close[i]>close[i-1]: co[i]=max(co[i-1],0)+1
        elif close[i]<close[i-1]: co[i]=min(co[i-1],0)-1

    h20=np.nan_to_num(_rmax(high,20),nan=high[0])
    l20=np.nan_to_num(_rmin(low,20),nan=low[0])
    dhi=(close-h20)/np.maximum(close,1e-10)
    dlo=(close-l20)/np.maximum(close,1e-10)

    f = {
        'adx': adx, 'atr_pct': ap, 'bb_pct': np.nan_to_num(bp,nan=0.5),
        'bb_width': np.nan_to_num(bw,nan=0), 'body_pct': bpct, 'consec': co,
        'dist_high': dhi, 'dist_low': dlo, 'donch_pos': np.nan_to_num(dp,nan=0.5),
        'ema_align': ea, 'ema_dist_10': ed10, 'ema_dist_20': ed20, 'ema_dist_50': ed50,
        'macd_hist_norm': mhn, 'macd_norm': mn, 'minus_di': mdi, 'plus_di': pdi,
        'ret_1': ret_1, 'ret_12': ret_12, 'ret_168': ret_168, 'ret_24': ret_24,
        'ret_4': ret_4, 'ret_48': ret_48, 'rsi': rsi, 'rsi_mom': rm,
        'vol_20': np.nan_to_num(v20,nan=0), 'vol_5': np.nan_to_num(v5,nan=0),
        'vol_50': np.nan_to_num(v50,nan=0), 'vol_ratio': np.nan_to_num(vr,nan=1),
        'vol_ratio_5_20': np.nan_to_num(vr52,nan=1), 'wick_ratio': wr,
    }
    if funding is not None and len(funding) == n:
        f['funding'] = np.nan_to_num(funding, nan=0)
        f['funding_ma48'] = np.nan_to_num(_rmean(funding, 48), nan=0)
        f['funding_ma8'] = np.nan_to_num(_rmean(funding, 8), nan=0)
    else:
        f['funding'] = np.zeros(n)
        f['funding_ma48'] = np.zeros(n)
        f['funding_ma8'] = np.zeros(n)
    return f


def compute_labels(close, horizon=24, threshold_pct=3.0):
    """Same as V2: +1/-1/0 labels based on forward return."""
    n = len(close)
    labels = np.zeros(n, dtype=np.int8)
    thr = threshold_pct / 100.0
    for i in range(n - horizon):
        fwd = close[i + horizon] / close[i] - 1.0
        if fwd > thr:
            labels[i] = 1
        elif fwd < -thr:
            labels[i] = -1
    return labels


# ── Market context builder (V2 baseline features) ────────────────────────
def build_market_context(top_tokens, token_dfs, btc_idx):
    """Build market context aligned to btc_idx.
    Returns V2 baseline features: btc_ret_1h/24h, btc_vol_24h, market_regime,
    breadth, disp_zscore, and per-token btc correlation series.
    """
    btc_df = token_dfs['BTC']
    btc_close = btc_df['close'].values.astype(np.float64)
    n_btc = len(btc_close)

    # BTC returns / vol
    btc_ret_1h = np.zeros(n_btc)
    btc_ret_1h[1:] = np.log(btc_close[1:] / btc_close[:-1])
    btc_ret_24h = np.zeros(n_btc)
    btc_ret_24h[24:] = np.log(btc_close[24:] / btc_close[:-24])
    btc_vol_24h = np.nan_to_num(_rstd(btc_ret_1h, 24), nan=0.0)

    # BTC daily regime (same as V2)
    btc_daily = btc_df.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close'])
    dc = btc_daily['close'].values.astype(np.float64)
    n_d = len(dc)
    d_ema20 = _ema(dc, 20); d_ema50 = _ema(dc, 50)
    d_ret = np.zeros(n_d); d_ret[1:] = np.log(dc[1:] / dc[:-1])
    d_vol = np.nan_to_num(_rstd(d_ret, 20), nan=0.0)

    d_high = btc_daily['high'].values.astype(np.float64)
    d_low = btc_daily['low'].values.astype(np.float64)
    d_prev_c = np.roll(dc, 1); d_prev_c[0] = dc[0]
    d_tr = np.maximum(d_high - d_low,
                      np.maximum(np.abs(d_high - d_prev_c), np.abs(d_low - d_prev_c)))
    d_atr = _ema(d_tr, 14)
    d_pdm = np.maximum(np.diff(d_high, prepend=d_high[0]), 0)
    d_mdm = np.maximum(-np.diff(d_low, prepend=d_low[0]), 0)
    d_pdm = np.where(d_pdm > d_mdm, d_pdm, 0)
    d_mdm = np.where(d_mdm > d_pdm, d_mdm, 0)
    d_sp = _ema(d_pdm, 14); d_sm = _ema(d_mdm, 14)
    d_pdi = 100 * d_sp / np.maximum(d_atr, 1e-10)
    d_mdi = 100 * d_sm / np.maximum(d_atr, 1e-10)
    d_dx = 100 * np.abs(d_pdi - d_mdi) / np.maximum(d_pdi + d_mdi, 1e-10)
    d_adx = _ema(d_dx, 14)
    d_vol_p75 = pd.Series(d_vol).expanding(min_periods=30).quantile(0.75).values

    regime_daily = np.full(n_d, 3, dtype=np.int8)
    crisis = d_vol > np.nan_to_num(d_vol_p75, nan=1.0) * 2
    quiet = ~crisis & (d_vol < np.nan_to_num(d_vol_p75, nan=1.0) * 0.3)
    strong = ~crisis & ~quiet & (d_adx > 25)
    regime_daily[crisis] = 0
    regime_daily[quiet] = 1
    regime_daily[strong & (d_ema20 > d_ema50)] = 2
    regime_daily[strong & ~(d_ema20 > d_ema50)] = 4

    regime_series = pd.Series(regime_daily, index=btc_daily.index)
    market_regime = regime_series.reindex(btc_idx, method='ffill').fillna(3).values

    # Cross-sectional matrices
    n_tokens = min(len(top_tokens), 30)
    ret_24h_matrix = np.full((n_btc, n_tokens), np.nan)
    above_ema20_matrix = np.full((n_btc, n_tokens), np.nan)

    for i, token in enumerate(top_tokens[:n_tokens]):
        if token not in token_dfs:
            continue
        df = token_dfs[token]
        aligned = pd.Series(df['close'].values.astype(np.float64), index=df.index).reindex(btc_idx)
        r24h = np.log(aligned / aligned.shift(24)).values
        ret_24h_matrix[:, i] = r24h
        e20 = aligned.ewm(span=20, adjust=False).mean()
        above_ema20_matrix[:, i] = (aligned > e20).astype(float).values

    # V2 dispersion
    market_dispersion = np.nanstd(ret_24h_matrix, axis=1)
    disp_mean = np.nan_to_num(_rmean(market_dispersion, 720), nan=0.0)
    disp_std = np.nan_to_num(_rstd(market_dispersion, 720), nan=1.0)
    dispersion_zscore = np.nan_to_num(
        (market_dispersion - disp_mean) / np.maximum(disp_std, 1e-10),
        nan=0.0, posinf=0.0, neginf=0.0)

    # V2 breadth
    market_breadth = np.nan_to_num(np.nanmean(above_ema20_matrix, axis=1), nan=0.5)

    return pd.DataFrame({
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        '_btc_ret_1h_series': btc_ret_1h,
    }, index=btc_idx)


def build_dataset(token_dfs, top_tokens, market_ctx, btc_idx, horizon=24,
                   threshold_pct=3.0):
    """Build feature matrix + labels for all tokens, with timestamps preserved.
    Uses V2 41-feature set."""
    all_X = []
    all_y = []
    all_ts = []
    all_tokens = []

    for token in top_tokens:
        if token not in token_dfs:
            continue
        df = token_dfs[token]
        if len(df) < MIN_BARS:
            continue

        close = df['close'].values.astype(np.float64)
        high = df['high'].values.astype(np.float64)
        low = df['low'].values.astype(np.float64)
        volume = df['volume'].values.astype(np.float64)
        funding = df['funding_rate'].values if 'funding_rate' in df.columns else None

        # Base features
        feats = compute_base_features(close, high, low, volume, funding)

        # Market context (aligned to token's index)
        ctx_aligned = market_ctx.reindex(df.index, method='ffill')
        feats['btc_ret_1h'] = np.nan_to_num(ctx_aligned['btc_ret_1h'].values, nan=0.0)
        feats['btc_ret_24h'] = np.nan_to_num(ctx_aligned['btc_ret_24h'].values, nan=0.0)
        feats['btc_vol_24h'] = np.nan_to_num(ctx_aligned['btc_vol_24h'].values, nan=0.0)
        feats['market_regime'] = np.nan_to_num(ctx_aligned['market_regime'].values, nan=3.0)
        feats['market_breadth_ema20'] = np.nan_to_num(ctx_aligned['market_breadth_ema20'].values, nan=0.5)
        feats['dispersion_zscore'] = np.nan_to_num(ctx_aligned['dispersion_zscore'].values, nan=0.0)

        # Token-BTC correlation
        n = len(close)
        token_ret = np.zeros(n)
        token_ret[1:] = np.log(close[1:] / close[:-1])
        btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret).rolling(168, min_periods=48).corr(
            pd.Series(btc_ret_aligned)).values
        feats['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)

        # Labels with specified horizon/threshold
        labels = compute_labels(close, horizon=horizon, threshold_pct=threshold_pct)

        # Build X matrix (V2 features)
        X = np.column_stack([feats.get(k, np.zeros(n)) for k in V2_FEATURES])
        X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

        # Valid mask: skip first 200 bars (warmup), skip neutral labels
        valid = np.ones(n, dtype=bool)
        valid[:200] = False
        valid[labels == 0] = False

        idx_valid = np.where(valid)[0]
        if len(idx_valid) < 30:
            continue

        all_X.append(X[idx_valid])
        all_y.append(labels[idx_valid])
        all_ts.append(df.index[idx_valid])
        all_tokens.extend([token] * len(idx_valid))

    if not all_X:
        return np.array([]), np.array([]), np.array([]), np.array([])

    X_all = np.vstack(all_X)
    y_all = np.concatenate(all_y)
    ts_all = np.concatenate(all_ts)

    return X_all, y_all, ts_all, np.array(all_tokens)


def undersample(X, y, seed=42):
    """Balance classes by undersampling majority."""
    classes, counts = np.unique(y, return_counts=True)
    min_count = counts.min()
    idx = []
    rng = np.random.RandomState(seed)
    for c in classes:
        c_idx = np.where(y == c)[0]
        chosen = rng.choice(c_idx, size=min_count, replace=False)
        idx.extend(chosen)
    idx = sorted(idx)
    return X[idx], y[idx]


def train_model(X_train, y_train):
    """Train HistGBM with V2 hyperparameters."""
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
    )
    model.fit(X_train, y_train)
    return model


def evaluate_window(model, X_test, y_test):
    """Evaluate model on test data. Returns dict of threshold -> metrics."""
    if len(y_test) == 0:
        return {}

    proba = model.predict_proba(X_test)
    classes = list(model.classes_)

    long_prob = proba[:, classes.index(1)] if 1 in classes else np.zeros(len(y_test))
    short_prob = proba[:, classes.index(-1)] if -1 in classes else np.zeros(len(y_test))

    results = {}
    for thr in [0.55, 0.60, 0.65, 0.70]:
        long_mask = long_prob >= thr
        short_mask = short_prob >= thr
        n_long = int(long_mask.sum())
        n_short = int(short_mask.sum())
        long_prec = float(np.mean(y_test[long_mask] == 1) * 100) if n_long > 0 else 0.0
        short_prec = float(np.mean(y_test[short_mask] == -1) * 100) if n_short > 0 else 0.0
        results[thr] = {
            'long_prec': long_prec, 'long_n': n_long,
            'short_prec': short_prec, 'short_n': n_short,
        }
    return results


def print_window_results(results):
    """Print threshold table for a single window."""
    print(f'  Thresh | Long Prec | Long N | Short Prec | Short N')
    print(f'  -------+-----------+--------+------------+---------')
    for thr in [0.55, 0.60, 0.65, 0.70]:
        r = results.get(thr, {})
        lp = r.get('long_prec', 0)
        ln = r.get('long_n', 0)
        sp = r.get('short_prec', 0)
        sn = r.get('short_n', 0)
        print(f'  {thr:.2f}   | {lp:>8.1f}% | {ln:>6} | {sp:>9.1f}% | {sn:>7}')


def main():
    t0 = time.time()
    print('=' * 70)
    print('EXPERIMENT B: WALK-FORWARD ML RETRAINING')
    print('=' * 70)
    print(f'Training window: 12 months rolling')
    print(f'Retraining frequency: every 3 months')
    print(f'Test period per window: 3 months')
    print(f'Features: V2 41-feature set')
    print(f'Model: HistGradientBoostingClassifier (V2 hyperparams)')
    print()

    # ── Load data ──────────────────────────────────────────────────────
    print('Loading data...')
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    top_tokens = [f.stem.replace('_1h', '') for f in token_files[:TOP_N]]
    print(f'  Top {TOP_N} tokens: {", ".join(top_tokens[:10])}...')

    token_dfs = {}
    for f in token_files[:TOP_N]:
        token = f.stem.replace('_1h', '')
        df = pd.read_parquet(f)
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'timestamp' in df.columns:
                df.index = pd.to_datetime(df['timestamp'])
            elif 'date' in df.columns:
                df.index = pd.to_datetime(df['date'])
        # Remove timezone info for consistent comparison
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        token_dfs[token] = df

    btc_idx = token_dfs['BTC'].index
    print(f'  BTC range: {btc_idx.min()} -> {btc_idx.max()}')
    print(f'  Data loaded: {len(token_dfs)} tokens')

    # ── Build market context (once, covers all data) ──────────────────
    print('\nBuilding market context...')
    market_ctx = build_market_context(top_tokens, token_dfs, btc_idx)
    print(f'  Market context shape: {market_ctx.shape}')

    # ── Run walk-forward for each config ──────────────────────────────
    all_results = {}  # config_name -> list of window results

    for cfg in CONFIGS:
        cfg_name = cfg['name']
        horizon = cfg['horizon']
        threshold_pct = cfg['threshold_pct']

        print(f'\n{"=" * 70}')
        print(f'  CONFIG: {cfg_name} (horizon={horizon}h, threshold={threshold_pct}%)')
        print(f'{"=" * 70}')

        # Build full dataset with this config's horizon/threshold
        print(f'\n  Building dataset (horizon={horizon}h, threshold={threshold_pct}%)...')
        X_all, y_all, ts_all, tokens_all = build_dataset(
            token_dfs, top_tokens, market_ctx, btc_idx,
            horizon=horizon, threshold_pct=threshold_pct)

        if len(y_all) == 0:
            print(f'  ERROR: No samples generated for {cfg_name}')
            continue

        # Make timestamps timezone-naive for comparison
        ts_naive = pd.DatetimeIndex(ts_all)
        if ts_naive.tz is not None:
            ts_naive = ts_naive.tz_localize(None)

        print(f'  Total samples: {len(y_all)} (+1={np.sum(y_all==1)}, -1={np.sum(y_all==-1)})')
        print(f'  Date range: {ts_naive.min()} -> {ts_naive.max()}')

        window_results = []

        for w_idx, (test_start_str, test_end_str) in enumerate(WALK_FORWARD_WINDOWS):
            test_start = pd.Timestamp(test_start_str)
            test_end = pd.Timestamp(test_end_str)
            train_start = test_start - pd.DateOffset(months=12)

            print(f'\n  Window {w_idx + 1}: Train [{train_start.date()} - {test_start.date()}], '
                  f'Test [{test_start.date()} - {test_end.date()}]')

            # Split data
            train_mask = (ts_naive >= train_start) & (ts_naive < test_start)
            test_mask = (ts_naive >= test_start) & (ts_naive < test_end)

            X_train = X_all[train_mask]
            y_train = y_all[train_mask]
            X_test = X_all[test_mask]
            y_test = y_all[test_mask]

            print(f'    Train samples: {len(y_train)} '
                  f'(+1={np.sum(y_train==1)}, -1={np.sum(y_train==-1)})')
            print(f'    Test samples:  {len(y_test)} '
                  f'(+1={np.sum(y_test==1)}, -1={np.sum(y_test==-1)})')

            if len(y_train) < 100:
                print(f'    SKIP: Not enough training samples')
                window_results.append({
                    'window': w_idx + 1,
                    'train_start': str(train_start.date()),
                    'train_end': str(test_start.date()),
                    'test_start': str(test_start.date()),
                    'test_end': str(test_end.date()),
                    'train_n': len(y_train),
                    'test_n': len(y_test),
                    'results': {},
                    'skipped': True,
                })
                continue

            if len(y_test) < 10:
                print(f'    SKIP: Not enough test samples')
                window_results.append({
                    'window': w_idx + 1,
                    'train_start': str(train_start.date()),
                    'train_end': str(test_start.date()),
                    'test_start': str(test_start.date()),
                    'test_end': str(test_end.date()),
                    'train_n': len(y_train),
                    'test_n': len(y_test),
                    'results': {},
                    'skipped': True,
                })
                continue

            # Balance training data
            X_train_bal, y_train_bal = undersample(X_train, y_train, seed=42 + w_idx)
            print(f'    Balanced train: {len(y_train_bal)} samples')

            # Train
            t_train = time.time()
            model = train_model(X_train_bal, y_train_bal)
            train_time = time.time() - t_train
            print(f'    Model trained in {train_time:.1f}s')

            # Evaluate
            results = evaluate_window(model, X_test, y_test)
            print()
            print_window_results(results)

            # Check if model is worth saving (>55% precision at p>=0.65)
            r65 = results.get(0.65, {})
            save_model = False
            if r65.get('long_prec', 0) > 55 and r65.get('long_n', 0) > 10:
                save_model = True
            if r65.get('short_prec', 0) > 55 and r65.get('short_n', 0) > 10:
                save_model = True

            if save_model:
                model_path = SAVE_DIR / f'wf_model_{cfg_name.replace("/", "_")}_{test_start_str}.joblib'
                joblib.dump(model, model_path)
                print(f'    ** MODEL SAVED: {model_path.name} (>55% precision at p>=0.65)')

            # Feature importance for this window
            if hasattr(model, 'feature_importances_'):
                fi = model.feature_importances_
                top_idx = np.argsort(fi)[::-1][:10]
                print(f'\n    Top 10 features:')
                for rank, i in enumerate(top_idx):
                    print(f'      {rank+1:>2}. {V2_FEATURES[i]:<25} {fi[i]:.4f}')

            window_results.append({
                'window': w_idx + 1,
                'train_start': str(train_start.date()),
                'train_end': str(test_start.date()),
                'test_start': str(test_start.date()),
                'test_end': str(test_end.date()),
                'train_n': len(y_train),
                'test_n': len(y_test),
                'results': {str(k): v for k, v in results.items()},
                'skipped': False,
            })

        all_results[cfg_name] = window_results

    # ── Aggregate OOS Performance ─────────────────────────────────────
    print(f'\n\n{"=" * 70}')
    print(f'AGGREGATE OOS PERFORMANCE:')
    print(f'{"=" * 70}')

    # Header
    print(f'  {"Config":<8} | {"Avg Long Prec":>13} | {"Avg Short Prec":>14} | '
          f'{"Total Long N":>12} | {"Total Short N":>13} | {"Verdict":>10}')
    print(f'  {"-"*8}-+-{"-"*13}-+-{"-"*14}-+-{"-"*12}-+-{"-"*13}-+-{"-"*10}')

    for cfg_name, windows in all_results.items():
        # Compute aggregate at threshold 0.65
        total_long_n = 0
        total_short_n = 0
        weighted_long_prec = 0.0
        weighted_short_prec = 0.0
        n_valid_windows = 0

        for w in windows:
            if w.get('skipped', True):
                continue
            r = w['results'].get('0.65', w['results'].get(0.65, {}))
            ln = r.get('long_n', 0)
            sn = r.get('short_n', 0)
            lp = r.get('long_prec', 0)
            sp = r.get('short_prec', 0)
            total_long_n += ln
            total_short_n += sn
            weighted_long_prec += lp * ln
            weighted_short_prec += sp * sn
            n_valid_windows += 1

        avg_long_prec = weighted_long_prec / max(total_long_n, 1)
        avg_short_prec = weighted_short_prec / max(total_short_n, 1)

        if avg_long_prec > 60 and avg_short_prec > 60:
            verdict = 'REAL EDGE'
        elif avg_long_prec > 55 or avg_short_prec > 55:
            verdict = 'MARGINAL'
        elif avg_long_prec > 52 or avg_short_prec > 52:
            verdict = 'WEAK'
        else:
            verdict = 'NO EDGE'

        print(f'  {cfg_name:<8} | {avg_long_prec:>12.1f}% | {avg_short_prec:>13.1f}% | '
              f'{total_long_n:>12} | {total_short_n:>13} | {verdict:>10}')

    # ── Detailed per-threshold aggregate ──────────────────────────────
    print(f'\n  DETAILED PER-THRESHOLD AGGREGATE (weighted by sample count):')
    for cfg_name, windows in all_results.items():
        print(f'\n  --- {cfg_name} ---')
        print(f'  {"Thresh":>6} | {"Avg Long Prec":>13} | {"Total Long N":>12} | '
              f'{"Avg Short Prec":>14} | {"Total Short N":>13}')
        print(f'  {"-"*6}-+-{"-"*13}-+-{"-"*12}-+-{"-"*14}-+-{"-"*13}')

        for thr in [0.55, 0.60, 0.65, 0.70]:
            total_ln = 0
            total_sn = 0
            w_lp = 0.0
            w_sp = 0.0
            for w in windows:
                if w.get('skipped', True):
                    continue
                r = w['results'].get(str(thr), w['results'].get(thr, {}))
                ln = r.get('long_n', 0)
                sn = r.get('short_n', 0)
                w_lp += r.get('long_prec', 0) * ln
                w_sp += r.get('short_prec', 0) * sn
                total_ln += ln
                total_sn += sn
            alp = w_lp / max(total_ln, 1)
            asp = w_sp / max(total_sn, 1)
            print(f'  {thr:>6.2f} | {alp:>12.1f}% | {total_ln:>12} | '
                  f'{asp:>13.1f}% | {total_sn:>13}')

    # ── Per-window summary table ──────────────────────────────────────
    print(f'\n  PER-WINDOW SUMMARY (p>=0.65):')
    print(f'  {"Config":<8} | {"Window":>6} | {"Test Period":>22} | '
          f'{"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'  {"-"*8}-+-{"-"*6}-+-{"-"*22}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')

    for cfg_name, windows in all_results.items():
        for w in windows:
            if w.get('skipped', True):
                test_str = f'{w["test_start"]} - {w["test_end"]}'
                print(f'  {cfg_name:<8} | {w["window"]:>6} | {test_str:>22} | '
                      f'{"SKIP":>9} | {"":>6} | {"SKIP":>10} | {"":>7}')
                continue
            r = w['results'].get('0.65', w['results'].get(0.65, {}))
            test_str = f'{w["test_start"]} - {w["test_end"]}'
            print(f'  {cfg_name:<8} | {w["window"]:>6} | {test_str:>22} | '
                  f'{r.get("long_prec", 0):>8.1f}% | {r.get("long_n", 0):>6} | '
                  f'{r.get("short_prec", 0):>9.1f}% | {r.get("short_n", 0):>7}')

    # ── Comparison vs static V2 ───────────────────────────────────────
    print(f'\n  COMPARISON VS STATIC V2 (full-period train, test Jul2025+):')
    print(f'  Static V2 OOS performance was ~50% precision (random) at all thresholds.')
    print(f'  Walk-forward improvement = Aggregate precision - 50% (random baseline)')
    for cfg_name, windows in all_results.items():
        total_ln = 0
        total_sn = 0
        w_lp = 0.0
        w_sp = 0.0
        for w in windows:
            if w.get('skipped', True):
                continue
            r = w['results'].get('0.65', w['results'].get(0.65, {}))
            ln = r.get('long_n', 0)
            sn = r.get('short_n', 0)
            w_lp += r.get('long_prec', 0) * ln
            w_sp += r.get('short_prec', 0) * sn
            total_ln += ln
            total_sn += sn
        alp = w_lp / max(total_ln, 1)
        asp = w_sp / max(total_sn, 1)
        l_delta = alp - 50.0
        s_delta = asp - 50.0
        print(f'  {cfg_name}: Long {alp:.1f}% ({l_delta:+.1f}pp vs random) | '
              f'Short {asp:.1f}% ({s_delta:+.1f}pp vs random)')

    # ── Save metadata ─────────────────────────────────────────────────
    meta = {
        'experiment': 'B - Walk-Forward Retraining',
        'configs': [c['name'] for c in CONFIGS],
        'walk_forward_windows': WALK_FORWARD_WINDOWS,
        'training_window_months': 12,
        'retrain_frequency_months': 3,
        'results': {},
    }
    for cfg_name, windows in all_results.items():
        meta['results'][cfg_name] = windows

    with open(SAVE_DIR / 'exp_b_walkforward_meta.json', 'w') as f:
        json.dump(meta, f, indent=2, default=str)

    elapsed = time.time() - t0
    print(f'\n  Results saved to {SAVE_DIR}')
    print(f'  Total time: {elapsed:.1f}s')

    # ── Final verdict ─────────────────────────────────────────────────
    print(f'\n{"=" * 70}')
    print(f'  EXPERIMENT B VERDICT')
    print(f'{"=" * 70}')
    any_edge = False
    for cfg_name, windows in all_results.items():
        total_ln = 0
        total_sn = 0
        w_lp = 0.0
        w_sp = 0.0
        for w in windows:
            if w.get('skipped', True):
                continue
            r = w['results'].get('0.65', w['results'].get(0.65, {}))
            ln = r.get('long_n', 0)
            sn = r.get('short_n', 0)
            w_lp += r.get('long_prec', 0) * ln
            w_sp += r.get('short_prec', 0) * sn
            total_ln += ln
            total_sn += sn
        alp = w_lp / max(total_ln, 1)
        asp = w_sp / max(total_sn, 1)
        if alp > 55 or asp > 55:
            any_edge = True
            print(f'  {cfg_name}: Walk-forward shows IMPROVEMENT over static model')
            print(f'    Long: {alp:.1f}%, Short: {asp:.1f}% at p>=0.65')
        else:
            print(f'  {cfg_name}: Walk-forward does NOT fix overfitting')
            print(f'    Long: {alp:.1f}%, Short: {asp:.1f}% at p>=0.65')

    if any_edge:
        print(f'\n  CONCLUSION: Walk-forward retraining shows promise.')
        print(f'  Next steps: tune retrain window size, explore feature selection per window.')
    else:
        print(f'\n  CONCLUSION: Walk-forward retraining alone is NOT sufficient.')
        print(f'  The V2 feature set may be fundamentally unable to capture OOS signal.')
        print(f'  Consider: different features, shorter horizons, regime-conditional models,')
        print(f'  or fundamentally different modeling approach (e.g., online learning).')


if __name__ == '__main__':
    main()
