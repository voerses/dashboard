#!/usr/bin/env python3
"""Experiment A: Recent-Only Training (Jan 2024 - June 2025)
=============================================================
Hypothesis: Old data (2020-2023) pollutes the model. Training on ONLY
recent post-ETF data (~18 months) will capture current market structure better.

  Train: Jan 2024 - June 2025 (18 months, post-ETF era)
  Test:  July 2025 - March 2026 (same OOS period as V5)

Tests FOUR label horizons:
  - 4h  / 1.5% threshold
  - 8h  / 1.5% threshold
  - 12h / 2.0% threshold
  - 24h / 3.0% threshold

Uses V2 features only (41 features) for simplicity.
"""
import numpy as np
import pandas as pd
import joblib
import time
import warnings
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
SAVE_DIR = Path('/workspace/crypto_backtest/results/v5')
SAVE_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_START = pd.Timestamp('2024-01-01')
TRAIN_END   = pd.Timestamp('2025-07-01')
TEST_START  = pd.Timestamp('2025-07-01')

TOP_N = 30
MIN_BARS = 500

# Four experiment configs: (horizon_hours, threshold_pct)
CONFIGS = [
    (4,  1.5),
    (8,  1.5),
    (12, 2.0),
    (24, 3.0),
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


# ── Feature computation (identical to V2) ────────────────────────────────
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


# ── V2 feature list (41 features) ────────────────────────────────────────
V2_FEATURES = [
    'adx', 'atr_pct', 'bb_pct', 'bb_width', 'body_pct', 'btc_ret_1h', 'btc_ret_24h',
    'btc_vol_24h', 'consec', 'dispersion_zscore', 'dist_high', 'dist_low', 'donch_pos',
    'ema_align', 'ema_dist_10', 'ema_dist_20', 'ema_dist_50', 'funding', 'funding_ma48',
    'funding_ma8', 'macd_hist_norm', 'macd_norm', 'market_breadth_ema20', 'market_regime',
    'minus_di', 'plus_di', 'ret_1', 'ret_12', 'ret_168', 'ret_24', 'ret_4', 'ret_48',
    'rsi', 'rsi_mom', 'token_btc_corr_168h', 'vol_20', 'vol_5', 'vol_50', 'vol_ratio',
    'vol_ratio_5_20', 'wick_ratio',
]


# ── Market context (V2 baseline only) ────────────────────────────────────
def build_market_context(top_tokens, token_dfs, btc_idx):
    """Build market context aligned to btc_idx (V2 baseline features only)."""
    btc_df = token_dfs['BTC']
    btc_close = btc_df['close'].values.astype(np.float64)
    n_btc = len(btc_close)

    # BTC returns / vol
    btc_ret_1h = np.zeros(n_btc)
    btc_ret_1h[1:] = np.log(btc_close[1:] / btc_close[:-1])
    btc_ret_24h = np.zeros(n_btc)
    btc_ret_24h[24:] = np.log(btc_close[24:] / btc_close[:-24])
    btc_vol_24h = np.nan_to_num(_rstd(btc_ret_1h, 24), nan=0.0)

    # BTC daily regime
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
        '_btc_ret_1h_series': btc_ret_1h,  # needed for token-BTC correlation
    }, index=btc_idx)


def build_dataset(token_dfs, top_tokens, market_ctx, btc_idx, horizon, threshold_pct):
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

        # Token-BTC correlation
        token_ret = np.zeros(len(close))
        token_ret[1:] = np.log(close[1:] / close[:-1])
        btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret).rolling(168, min_periods=48).corr(
            pd.Series(btc_ret_aligned)).values
        feats['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)

        # Labels (using config-specific horizon and threshold)
        labels = compute_labels(close, horizon=horizon, threshold_pct=threshold_pct)

        # Build X matrix (V2 features only = 41)
        n = len(close)
        X = np.column_stack([feats.get(k, np.zeros(n)) for k in V2_FEATURES])
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


def evaluate_config(model, X_test, y_test, horizon, threshold_pct):
    """Evaluate a single config and return results dict."""
    proba = model.predict_proba(X_test)
    classes = list(model.classes_)

    long_prob = proba[:, classes.index(1)] if 1 in classes else np.zeros(len(y_test))
    short_prob = proba[:, classes.index(-1)] if -1 in classes else np.zeros(len(y_test))

    base_rate_long = np.mean(y_test == 1) * 100
    base_rate_short = np.mean(y_test == -1) * 100

    print(f'\n  Config: {horizon}h / {threshold_pct}% threshold')
    print(f'    Test samples: {len(y_test)}')
    print(f'    Class distribution: +1={np.sum(y_test==1)} / -1={np.sum(y_test==-1)}')
    print(f'')
    print(f'    {"Thresh":>6} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'    {"-"*6}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')

    results = {}
    for thr in [0.55, 0.60, 0.65, 0.70]:
        long_mask = long_prob >= thr
        short_mask = short_prob >= thr

        n_long = long_mask.sum()
        n_short = short_mask.sum()

        long_prec = np.mean(y_test[long_mask] == 1) * 100 if n_long > 0 else 0
        short_prec = np.mean(y_test[short_mask] == -1) * 100 if n_short > 0 else 0

        print(f'    {thr:>6.2f} | {long_prec:>8.1f}% | {n_long:>6} | {short_prec:>9.1f}% | {n_short:>7}')

        results[thr] = {
            'long_prec': long_prec, 'long_n': n_long,
            'short_prec': short_prec, 'short_n': n_short,
        }

    print(f'')
    print(f'    Base rate (long): {base_rate_long:.1f}%')
    print(f'    Base rate (short): {base_rate_short:.1f}%')

    # Determine verdict
    # Check at threshold 0.60 (good balance of precision vs sample count)
    best_long = 0
    best_short = 0
    best_thr = 0.55
    for thr in [0.55, 0.60, 0.65, 0.70]:
        r = results[thr]
        # Need at least 20 samples to be meaningful
        lp = r['long_prec'] if r['long_n'] >= 20 else 0
        sp = r['short_prec'] if r['short_n'] >= 20 else 0
        if lp + sp > best_long + best_short:
            best_long = lp
            best_short = sp
            best_thr = thr

    if best_long > 60 and best_long > base_rate_long + 10:
        verdict = 'EDGE'
    elif best_long > 55 and best_long > base_rate_long + 5:
        verdict = 'MARGINAL'
    else:
        verdict = 'NO EDGE'

    print(f'    VERDICT: {verdict}')

    return results, verdict, best_long, best_short, best_thr


def main():
    t0 = time.time()
    print('=' * 70)
    print('  EXPERIMENT A: RECENT-ONLY TRAINING (Jan 2024 - June 2025)')
    print('=' * 70)
    print(f'  Train window: {TRAIN_START.date()} to {TRAIN_END.date()}')
    print(f'  Test window:  {TEST_START.date()} onward')
    print(f'  Configs: {len(CONFIGS)} horizon/threshold combos')
    print(f'  Features: V2 (41 features)')
    print()

    # ── Load data ─────────────────────────────────────────────────────
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

    # ── Build market context ──────────────────────────────────────────
    print('\nBuilding market context...')
    market_ctx = build_market_context(top_tokens, token_dfs, btc_idx)
    print(f'  Market context: {market_ctx.shape}')

    # ── Run each config ───────────────────────────────────────────────
    summary = []
    best_model_overall = None
    best_score_overall = 0
    best_config_label = ''
    best_features_to_save = None

    for horizon, threshold_pct in CONFIGS:
        config_label = f'{horizon}h/{threshold_pct}%'
        print(f'\n{"="*70}')
        print(f'  CONFIG: {config_label}')
        print(f'{"="*70}')

        # Build dataset with this horizon/threshold
        print(f'  Building dataset (horizon={horizon}h, threshold={threshold_pct}%)...')
        X_all, y_all, ts_all, tokens_all = build_dataset(
            token_dfs, top_tokens, market_ctx, btc_idx,
            horizon=horizon, threshold_pct=threshold_pct)
        print(f'  Total samples: {len(y_all)}')
        print(f'  Class dist: +1={np.sum(y_all==1)}, -1={np.sum(y_all==-1)}')

        # Temporal split: train = Jan 2024 to June 2025, test = July 2025+
        ts_naive = pd.DatetimeIndex(ts_all)
        if ts_naive.tz is not None:
            ts_naive = ts_naive.tz_localize(None)

        train_mask = (ts_naive >= TRAIN_START) & (ts_naive < TRAIN_END)
        test_mask = ts_naive >= TEST_START

        X_train_full, y_train_full = X_all[train_mask], y_all[train_mask]
        X_test, y_test = X_all[test_mask], y_all[test_mask]

        print(f'  Train: {train_mask.sum()} samples ({TRAIN_START.date()} to {TRAIN_END.date()})')
        print(f'  Test:  {test_mask.sum()} samples (from {TEST_START.date()})')

        if train_mask.sum() < 100:
            print('  SKIPPED: Not enough training samples')
            summary.append((config_label, 0, 0, 0, 'SKIP'))
            continue
        if test_mask.sum() < 100:
            print('  SKIPPED: Not enough test samples')
            summary.append((config_label, 0, 0, 0, 'SKIP'))
            continue

        # Undersample and train
        X_train_bal, y_train_bal = undersample(X_train_full, y_train_full)
        print(f'  Training model ({len(X_train_bal)} balanced samples)...')

        model = train_model(X_train_bal, y_train_bal)

        # Evaluate
        results, verdict, best_long, best_short, best_thr = evaluate_config(
            model, X_test, y_test, horizon, threshold_pct)

        summary.append((config_label, best_long, best_short, best_thr, verdict))

        # Track best model overall
        combined_score = best_long + best_short
        if combined_score > best_score_overall:
            best_score_overall = combined_score
            best_model_overall = model
            best_config_label = config_label
            best_features_to_save = V2_FEATURES

    # ── Summary table ─────────────────────────────────────────────────
    print(f'\n\n{"="*70}')
    print(f'  SUMMARY')
    print(f'{"="*70}')
    print(f'  {"Horizon":>10} | {"Best Long Prec":>14} | {"Best Short Prec":>15} | {"Best Threshold":>14} | {"Verdict":>8}')
    print(f'  {"-"*10}-+-{"-"*14}-+-{"-"*15}-+-{"-"*14}-+-{"-"*8}')
    for config_label, best_long, best_short, best_thr, verdict in summary:
        print(f'  {config_label:>10} | {best_long:>13.1f}% | {best_short:>14.1f}% | {best_thr:>14.2f} | {verdict:>8}')

    # ── Save best model ──────────────────────────────────────────────
    if best_model_overall is not None:
        model_path = SAVE_DIR / 'ml_exp_a_best_model.joblib'
        feat_path = SAVE_DIR / 'ml_exp_a_best_features.joblib'
        joblib.dump(best_model_overall, model_path)
        joblib.dump(best_features_to_save, feat_path)
        print(f'\n  Best model ({best_config_label}) saved to:')
        print(f'    {model_path}')
        print(f'    {feat_path}')
    else:
        print('\n  No model worth saving.')

    elapsed = time.time() - t0
    print(f'\n  Total time: {elapsed:.1f}s')


if __name__ == '__main__':
    main()
