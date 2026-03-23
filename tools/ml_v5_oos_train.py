#!/usr/bin/env python3
"""ML Direction Model V5 — Strict Out-of-Sample Training
=========================================================
Fixes V2's temporal leakage: train by DATE, not row index.

  Train: all data before TRAIN_CUTOFF (2025-07-01)
  OOS:   2025-07-01 to end of data (~8.5 months)

Features:
  - V2 base: 30 TA + 3 funding = 33
  - V2 market context: 7 (btc_ret_1h/24h, btc_vol, regime, breadth, disp, corr)
  - NEW enhanced dispersion: 5 (disp_pctile, disp_delta, corr_regime, breadth_mom, vol_pctile)
  - Total: 45 features

Runs two evaluations:
  1. V2-baseline (41 features) — establishes OOS baseline
  2. V5-enhanced (45 features) — tests if new dispersion features help
"""
import numpy as np
import pandas as pd
import joblib
import os
import sys
import time
import warnings
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_score, accuracy_score, classification_report

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
SAVE_DIR = Path('/workspace/crypto_backtest/results/v5')
SAVE_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_CUTOFF = pd.Timestamp('2025-07-01', tz=None)
LABEL_HORIZON = 24
LABEL_THRESHOLD = 0.03
TOP_N = 30
MIN_BARS = 500

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


# ── Feature computation (identical to V2/s312) ──────────────────────────
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
    """Same as V2: +1/−1/0 labels based on forward return."""
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


# ── Market context (V2 baseline + enhanced dispersion) ───────────────────
def build_market_context(top_tokens, token_dfs, btc_idx):
    """Build market context aligned to btc_idx.

    Returns dict with:
      V2 baseline: btc_ret_1h/24h, btc_vol_24h, market_regime, breadth, disp_zscore, per-token btc_corr
      V5 enhanced: disp_pctile, disp_delta_24h, corr_regime, breadth_mom, vol_pctile
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
    ret_1h_matrix = np.full((n_btc, n_tokens), np.nan)
    above_ema20_matrix = np.full((n_btc, n_tokens), np.nan)

    for i, token in enumerate(top_tokens[:n_tokens]):
        if token not in token_dfs:
            continue
        df = token_dfs[token]
        aligned = pd.Series(df['close'].values.astype(np.float64), index=df.index).reindex(btc_idx)
        r1h = np.log(aligned / aligned.shift(1)).values
        r24h = np.log(aligned / aligned.shift(24)).values
        ret_1h_matrix[:, i] = r1h
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

    # ── V5 Enhanced dispersion features ──────────────────────────
    # 1. Dispersion percentile (vs trailing 720h = 30 days)
    disp_pctile = pd.Series(market_dispersion).rolling(720, min_periods=48).rank(pct=True).values
    disp_pctile = np.nan_to_num(disp_pctile, nan=0.5)

    # 2. Dispersion delta (24h change — rising = regime shift)
    disp_delta = np.zeros(n_btc)
    disp_delta[24:] = market_dispersion[24:] - market_dispersion[:-24]
    disp_delta = np.nan_to_num(disp_delta, nan=0.0)

    # 3. Correlation regime (average rolling correlation across top 10 pairs)
    # Use ret_1h_matrix for top 10 most-data tokens
    valid_cols = np.sum(~np.isnan(ret_1h_matrix), axis=0)
    top10_idx = np.argsort(valid_cols)[-10:]
    top10_rets = ret_1h_matrix[:, top10_idx]
    # Rolling 168h average pairwise correlation
    corr_regime = np.full(n_btc, np.nan)
    window = 168
    for t in range(window, n_btc):
        block = top10_rets[t-window:t]
        valid_mask = ~np.isnan(block).any(axis=0)
        if valid_mask.sum() >= 3:
            sub = block[:, valid_mask]
            cc = np.corrcoef(sub.T)
            n_c = cc.shape[0]
            # Mean of upper triangle
            mask_upper = np.triu(np.ones((n_c, n_c), dtype=bool), k=1)
            corr_regime[t] = np.nanmean(cc[mask_upper])
    corr_regime = pd.Series(corr_regime).ffill().fillna(0.5).values

    # 4. Breadth momentum (24h change)
    breadth_mom = np.zeros(n_btc)
    breadth_mom[24:] = market_breadth[24:] - market_breadth[:-24]

    # 5. Vol percentile (BTC vol vs trailing 720h)
    vol_pctile = pd.Series(btc_vol_24h).rolling(720, min_periods=48).rank(pct=True).values
    vol_pctile = np.nan_to_num(vol_pctile, nan=0.5)

    return pd.DataFrame({
        # V2 baseline
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        '_btc_ret_1h_series': btc_ret_1h,
        # V5 enhanced
        'dispersion_pctile': disp_pctile,
        'dispersion_delta_24h': disp_delta,
        'corr_regime': corr_regime,
        'breadth_momentum': breadth_mom,
        'vol_regime_pctile': vol_pctile,
    }, index=btc_idx)


V2_FEATURES = [
    'adx', 'atr_pct', 'bb_pct', 'bb_width', 'body_pct', 'btc_ret_1h', 'btc_ret_24h',
    'btc_vol_24h', 'consec', 'dispersion_zscore', 'dist_high', 'dist_low', 'donch_pos',
    'ema_align', 'ema_dist_10', 'ema_dist_20', 'ema_dist_50', 'funding', 'funding_ma48',
    'funding_ma8', 'macd_hist_norm', 'macd_norm', 'market_breadth_ema20', 'market_regime',
    'minus_di', 'plus_di', 'ret_1', 'ret_12', 'ret_168', 'ret_24', 'ret_4', 'ret_48',
    'rsi', 'rsi_mom', 'token_btc_corr_168h', 'vol_20', 'vol_5', 'vol_50', 'vol_ratio',
    'vol_ratio_5_20', 'wick_ratio',
]

V5_EXTRA_FEATURES = [
    'dispersion_pctile', 'dispersion_delta_24h', 'corr_regime',
    'breadth_momentum', 'vol_regime_pctile',
]

V5_FEATURES = V2_FEATURES + V5_EXTRA_FEATURES


def build_dataset(token_dfs, top_tokens, market_ctx, btc_idx):
    """Build feature matrix + labels for all tokens, with timestamps preserved."""
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

        # V5 enhanced
        feats['dispersion_pctile'] = np.nan_to_num(ctx_aligned['dispersion_pctile'].values, nan=0.5)
        feats['dispersion_delta_24h'] = np.nan_to_num(ctx_aligned['dispersion_delta_24h'].values, nan=0.0)
        feats['corr_regime'] = np.nan_to_num(ctx_aligned['corr_regime'].values, nan=0.5)
        feats['breadth_momentum'] = np.nan_to_num(ctx_aligned['breadth_momentum'].values, nan=0.0)
        feats['vol_regime_pctile'] = np.nan_to_num(ctx_aligned['vol_regime_pctile'].values, nan=0.5)

        # Token-BTC correlation
        token_ret = np.zeros(len(close))
        token_ret[1:] = np.log(close[1:] / close[:-1])
        btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret).rolling(168, min_periods=48).corr(
            pd.Series(btc_ret_aligned)).values
        feats['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)

        # Labels
        labels = compute_labels(close)

        # Build X matrix (V5 features)
        n = len(close)
        X = np.column_stack([feats.get(k, np.zeros(n)) for k in V5_FEATURES])
        X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

        # Valid mask: skip first 200 bars, skip neutral labels
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

    X_all = np.vstack(all_X)
    y_all = np.concatenate(all_y)
    ts_all = np.concatenate(all_ts)

    return X_all, y_all, ts_all, np.array(all_tokens)


def undersample(X, y):
    """Balance classes by undersampling majority."""
    classes, counts = np.unique(y, return_counts=True)
    min_count = counts.min()
    idx = []
    rng = np.random.RandomState(42)
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


def evaluate_model(model, X_test, y_test, feature_names, label=''):
    """Comprehensive evaluation at multiple probability thresholds."""
    proba = model.predict_proba(X_test)
    classes = list(model.classes_)

    long_prob = proba[:, classes.index(1)] if 1 in classes else np.zeros(len(y_test))
    short_prob = proba[:, classes.index(-1)] if -1 in classes else np.zeros(len(y_test))

    print(f'\n{"="*70}')
    print(f'  {label} EVALUATION')
    print(f'{"="*70}')
    print(f'  Test samples: {len(y_test)}')
    print(f'  Class distribution: +1={np.sum(y_test==1)} / -1={np.sum(y_test==-1)}')
    print(f'  Base rate (long): {np.mean(y_test==1)*100:.1f}%')

    # Overall accuracy
    preds = model.predict(X_test)
    acc = accuracy_score(y_test, preds)
    print(f'  Overall accuracy: {acc*100:.1f}%')

    # Per-threshold evaluation
    print(f'\n  {"Thresh":>6} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7} | {"Combined":>8}')
    print(f'  {"-"*6}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}-+-{"-"*8}')

    results = {}
    for thr in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        long_mask = long_prob >= thr
        short_mask = short_prob >= thr

        n_long = long_mask.sum()
        n_short = short_mask.sum()

        long_prec = np.mean(y_test[long_mask] == 1) * 100 if n_long > 0 else 0
        short_prec = np.mean(y_test[short_mask] == -1) * 100 if n_short > 0 else 0
        combined = (long_prec + short_prec) / 2 if (n_long > 0 and n_short > 0) else 0

        print(f'  {thr:>6.2f} | {long_prec:>8.1f}% | {n_long:>6} | {short_prec:>9.1f}% | {n_short:>7} | {combined:>7.1f}%')

        results[thr] = {
            'long_prec': long_prec, 'long_n': n_long,
            'short_prec': short_prec, 'short_n': n_short,
        }

    # Feature importance
    if hasattr(model, 'feature_importances_'):
        fi = model.feature_importances_
        top_idx = np.argsort(fi)[::-1][:15]
        print(f'\n  Top 15 Feature Importances:')
        for rank, i in enumerate(top_idx):
            print(f'    {rank+1:>2}. {feature_names[i]:<25} {fi[i]:.4f}')

    # Overfitting diagnostic
    print(f'\n  OVERFITTING CHECKS:')
    # Compare to V2 in-sample numbers (known: ~80% at p>=0.70)
    v2_insample_long_70 = 80.0
    v2_insample_short_70 = 80.0
    if 0.70 in results and results[0.70]['long_n'] > 10:
        drop = v2_insample_long_70 - results[0.70]['long_prec']
        status = 'OVERFIT' if drop > 15 else ('CAUTION' if drop > 5 else 'OK')
        print(f'    Long p>=0.70: V2 in-sample ~{v2_insample_long_70:.0f}% → OOS {results[0.70]["long_prec"]:.1f}% (drop {drop:.1f}pp) → {status}')
    if 0.70 in results and results[0.70]['short_n'] > 10:
        drop = v2_insample_short_70 - results[0.70]['short_prec']
        status = 'OVERFIT' if drop > 15 else ('CAUTION' if drop > 5 else 'OK')
        print(f'    Short p>=0.70: V2 in-sample ~{v2_insample_short_70:.0f}% → OOS {results[0.70]["short_prec"]:.1f}% (drop {drop:.1f}pp) → {status}')

    return results


def main():
    t0 = time.time()
    print('ML Direction V5 — Strict OOS Training')
    print(f'Train cutoff: {TRAIN_CUTOFF}')
    print(f'Top N tokens: {TOP_N}')
    print()

    # ── Load data ──────────────────────────────────────────────────
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
    print(f'  BTC range: {btc_idx.min()} → {btc_idx.max()}')
    print(f'  Data loaded: {len(token_dfs)} tokens')

    # ── Build market context ───────────────────────────────────────
    print('\nBuilding market context (with enhanced dispersion)...')
    market_ctx = build_market_context(top_tokens, token_dfs, btc_idx)
    print(f'  Market context: {market_ctx.shape}')

    # ── Build dataset ──────────────────────────────────────────────
    print('\nBuilding feature matrix...')
    X_all, y_all, ts_all, tokens_all = build_dataset(
        token_dfs, top_tokens, market_ctx, btc_idx)
    print(f'  Total samples: {len(y_all)}')
    print(f'  Class dist: +1={np.sum(y_all==1)}, -1={np.sum(y_all==-1)}')
    print(f'  Date range: {pd.Timestamp(ts_all.min())} → {pd.Timestamp(ts_all.max())}')

    # ── Temporal split ─────────────────────────────────────────────
    cutoff = TRAIN_CUTOFF
    # Handle timezone
    ts_naive = pd.DatetimeIndex(ts_all)
    if ts_naive.tz is not None:
        ts_naive = ts_naive.tz_localize(None)

    train_mask = ts_naive < cutoff
    test_mask = ts_naive >= cutoff

    X_train_full, y_train_full = X_all[train_mask], y_all[train_mask]
    X_test, y_test = X_all[test_mask], y_all[test_mask]
    ts_test = ts_all[test_mask]
    tokens_test = tokens_all[test_mask]

    print(f'\n  TEMPORAL SPLIT:')
    print(f'    Train: {train_mask.sum()} samples (before {cutoff.date()})')
    print(f'    Test:  {test_mask.sum()} samples (from {cutoff.date()})')
    print(f'    Train date range: {pd.Timestamp(ts_all[train_mask].min())} → {pd.Timestamp(ts_all[train_mask].max())}')
    print(f'    Test date range:  {pd.Timestamp(ts_test.min())} → {pd.Timestamp(ts_test.max())}')

    if test_mask.sum() < 100:
        print('  ERROR: Not enough test samples. Check cutoff date.')
        return

    # ── Train V2 baseline (41 features) ────────────────────────────
    print('\n' + '='*70)
    print('  PART 1: V2 BASELINE (41 features, strict OOS)')
    print('='*70)

    v2_feat_idx = [V5_FEATURES.index(f) for f in V2_FEATURES]
    X_train_v2 = X_train_full[:, v2_feat_idx]
    X_test_v2 = X_test[:, v2_feat_idx]

    X_train_v2_bal, y_train_v2_bal = undersample(X_train_v2, y_train_full)
    print(f'  Training V2 model ({len(X_train_v2_bal)} balanced samples)...')

    model_v2 = train_model(X_train_v2_bal, y_train_v2_bal)
    v2_results = evaluate_model(model_v2, X_test_v2, y_test, V2_FEATURES, 'V2 BASELINE OOS')

    # ── Train V5 enhanced (45 features) ────────────────────────────
    print('\n' + '='*70)
    print('  PART 2: V5 ENHANCED (45 features, strict OOS)')
    print('='*70)

    X_train_v5_bal, y_train_v5_bal = undersample(X_train_full, y_train_full)
    print(f'  Training V5 model ({len(X_train_v5_bal)} balanced samples)...')

    model_v5 = train_model(X_train_v5_bal, y_train_v5_bal)
    v5_results = evaluate_model(model_v5, X_test, y_test, V5_FEATURES, 'V5 ENHANCED OOS')

    # ── Per-regime breakdown (V5) ──────────────────────────────────
    print('\n  PER-REGIME BREAKDOWN (V5, p>=0.65):')
    regime_idx = V5_FEATURES.index('market_regime')
    regime_vals = X_test[:, regime_idx]
    regime_names = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}

    proba_v5 = model_v5.predict_proba(X_test)
    classes_v5 = list(model_v5.classes_)
    lp = proba_v5[:, classes_v5.index(1)] if 1 in classes_v5 else np.zeros(len(y_test))
    sp = proba_v5[:, classes_v5.index(-1)] if -1 in classes_v5 else np.zeros(len(y_test))

    print(f'  {"Regime":<12} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'  {"-"*12}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')
    for rv, rn in sorted(regime_names.items()):
        rmask = np.abs(regime_vals - rv) < 0.5
        if rmask.sum() < 10:
            continue
        lm = rmask & (lp >= 0.65)
        sm = rmask & (sp >= 0.65)
        lprec = np.mean(y_test[lm] == 1) * 100 if lm.sum() > 0 else 0
        sprec = np.mean(y_test[sm] == -1) * 100 if sm.sum() > 0 else 0
        print(f'  {rn:<12} | {lprec:>8.1f}% | {lm.sum():>6} | {sprec:>9.1f}% | {sm.sum():>7}')

    # ── Per-token OOS breakdown ────────────────────────────────────
    print('\n  PER-TOKEN OOS PRECISION (V5, p>=0.65, top 15):')
    print(f'  {"Token":<8} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'  {"-"*8}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')
    unique_tokens = np.unique(tokens_test)
    token_results = []
    for t in unique_tokens:
        tmask = tokens_test == t
        if tmask.sum() < 20:
            continue
        X_t = X_test[tmask]
        y_t = y_test[tmask]
        p_t = model_v5.predict_proba(X_t)
        lp_t = p_t[:, classes_v5.index(1)] if 1 in classes_v5 else np.zeros(len(y_t))
        sp_t = p_t[:, classes_v5.index(-1)] if -1 in classes_v5 else np.zeros(len(y_t))
        lm_t = lp_t >= 0.65
        sm_t = sp_t >= 0.65
        lprec_t = np.mean(y_t[lm_t] == 1) * 100 if lm_t.sum() > 5 else 0
        sprec_t = np.mean(y_t[sm_t] == -1) * 100 if sm_t.sum() > 5 else 0
        token_results.append((t, lprec_t, lm_t.sum(), sprec_t, sm_t.sum()))

    token_results.sort(key=lambda x: -(x[1] + x[3]))
    for t, lp, ln, sp, sn in token_results[:15]:
        print(f'  {t:<8} | {lp:>8.1f}% | {ln:>6} | {sp:>9.1f}% | {sn:>7}')

    # ── Monthly OOS breakdown ─────────────────────────────────────
    print('\n  MONTHLY OOS PRECISION (V5, p>=0.65):')
    ts_test_pd = pd.DatetimeIndex(ts_test)
    months = ts_test_pd.to_period('M').unique()
    print(f'  {"Month":>8} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'  {"-"*8}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')
    for m in months:
        mmask = ts_test_pd.to_period('M') == m
        if mmask.sum() < 10:
            continue
        X_m = X_test[mmask]
        y_m = y_test[mmask]
        p_m = model_v5.predict_proba(X_m)
        lp_m = p_m[:, classes_v5.index(1)] if 1 in classes_v5 else np.zeros(len(y_m))
        sp_m = p_m[:, classes_v5.index(-1)] if -1 in classes_v5 else np.zeros(len(y_m))
        lm_m = lp_m >= 0.65
        sm_m = sp_m >= 0.65
        lprec_m = np.mean(y_m[lm_m] == 1) * 100 if lm_m.sum() > 0 else 0
        sprec_m = np.mean(y_m[sm_m] == -1) * 100 if sm_m.sum() > 0 else 0
        print(f'  {str(m):>8} | {lprec_m:>8.1f}% | {lm_m.sum():>6} | {sprec_m:>9.1f}% | {sm_m.sum():>7}')

    # ── Save models ───────────────────────────────────────────────
    joblib.dump(model_v2, SAVE_DIR / 'ml_dir_v2_oos_model.joblib')
    joblib.dump(V2_FEATURES, SAVE_DIR / 'ml_dir_v2_oos_features.joblib')
    joblib.dump(model_v5, SAVE_DIR / 'ml_dir_v5_oos_model.joblib')
    joblib.dump(V5_FEATURES, SAVE_DIR / 'ml_dir_v5_oos_features.joblib')

    # Save training metadata
    meta = {
        'train_cutoff': str(TRAIN_CUTOFF),
        'train_samples': int(train_mask.sum()),
        'test_samples': int(test_mask.sum()),
        'train_date_range': [str(pd.Timestamp(ts_all[train_mask].min())),
                              str(pd.Timestamp(ts_all[train_mask].max()))],
        'test_date_range': [str(pd.Timestamp(ts_test.min())),
                             str(pd.Timestamp(ts_test.max()))],
        'v2_features': V2_FEATURES,
        'v5_features': V5_FEATURES,
        'v2_results': {str(k): v for k, v in v2_results.items()},
        'v5_results': {str(k): v for k, v in v5_results.items()},
    }
    import json
    with open(SAVE_DIR / 'v5_training_meta.json', 'w') as f:
        json.dump(meta, f, indent=2, default=str)

    elapsed = time.time() - t0
    print(f'\n  Models saved to {SAVE_DIR}')
    print(f'  Total time: {elapsed:.1f}s')

    # ── VERDICT ────────────────────────────────────────────────────
    print(f'\n{"="*70}')
    print(f'  VERDICT')
    print(f'{"="*70}')
    best_v5_long = v5_results.get(0.65, {}).get('long_prec', 0)
    best_v5_short = v5_results.get(0.65, {}).get('short_prec', 0)
    if best_v5_long > 60 and best_v5_short > 60:
        print(f'  REAL EDGE DETECTED — proceed to strategy build')
        print(f'  Long precision @0.65: {best_v5_long:.1f}%')
        print(f'  Short precision @0.65: {best_v5_short:.1f}%')
    elif best_v5_long > 55 or best_v5_short > 55:
        print(f'  MARGINAL EDGE — needs feature engineering or different approach')
        print(f'  Long precision @0.65: {best_v5_long:.1f}%')
        print(f'  Short precision @0.65: {best_v5_short:.1f}%')
    else:
        print(f'  NO EDGE — V2 model was overfit')
        print(f'  Long precision @0.65: {best_v5_long:.1f}%')
        print(f'  Short precision @0.65: {best_v5_short:.1f}%')
        print(f'  Need fundamentally different approach')


if __name__ == '__main__':
    main()
