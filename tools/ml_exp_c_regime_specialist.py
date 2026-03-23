#!/usr/bin/env python3
"""Experiment C: Regime-Specialist Models
==========================================
Hypothesis: Training separate models per market regime captures regime-specific
patterns that a single model averages away.

Regime definition: dispersion-breadth 2x2 matrix:
  - DIVERGENT_BULL  (high disp + rising breadth): tokens differentiating upward
  - DIVERGENT_BEAR  (high disp + falling breadth): panic/rotation
  - CORRELATED_BULL (low disp + rising breadth): everything up together
  - CORRELATED_BEAR (low disp + falling breadth): everything down together

Uses V2 41 features MINUS market_regime (since we split by regime = 40 features).
Temporal split: train Jan 2024 - June 2025, test July 2025+.
24h horizon, 3% threshold, 30 tokens, HistGBM.
"""
import numpy as np
import pandas as pd
import time
import warnings
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
TRAIN_START = pd.Timestamp('2024-01-01')
TRAIN_CUTOFF = pd.Timestamp('2025-07-01')
LABEL_HORIZON = 24
LABEL_THRESHOLD = 0.03
TOP_N = 30
MIN_BARS = 500

REGIME_NAMES = {
    0: 'DIVERGENT_BULL',
    1: 'DIVERGENT_BEAR',
    2: 'CORRELATED_BULL',
    3: 'CORRELATED_BEAR',
}

# V2 features MINUS market_regime (40 features instead of 41)
SPECIALIST_FEATURES = [
    'adx', 'atr_pct', 'bb_pct', 'bb_width', 'body_pct', 'btc_ret_1h', 'btc_ret_24h',
    'btc_vol_24h', 'consec', 'dispersion_zscore', 'dist_high', 'dist_low', 'donch_pos',
    'ema_align', 'ema_dist_10', 'ema_dist_20', 'ema_dist_50', 'funding', 'funding_ma48',
    'funding_ma8', 'macd_hist_norm', 'macd_norm', 'market_breadth_ema20',
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


def compute_labels(close, horizon=LABEL_HORIZON, threshold_pct=3.0):
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


# ── Market context + dispersion regime ────────────────────────────────
def build_market_context_and_regimes(top_tokens, token_dfs, btc_idx):
    """Build market context aligned to btc_idx.

    Returns:
      market_ctx: DataFrame with V2 features + dispersion regime column
      dispersion_regime: integer array (0-3) aligned to btc_idx
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

    # V2 dispersion (cross-sectional std of 24h returns)
    market_dispersion = np.nanstd(ret_24h_matrix, axis=1)
    disp_mean = np.nan_to_num(_rmean(market_dispersion, 720), nan=0.0)
    disp_std = np.nan_to_num(_rstd(market_dispersion, 720), nan=1.0)
    dispersion_zscore = np.nan_to_num(
        (market_dispersion - disp_mean) / np.maximum(disp_std, 1e-10),
        nan=0.0, posinf=0.0, neginf=0.0)

    # V2 breadth
    market_breadth = np.nan_to_num(np.nanmean(above_ema20_matrix, axis=1), nan=0.5)

    # ── Dispersion-breadth 2x2 regime matrix ──────────────────────
    # Dispersion median (rolling 720h)
    disp_median = pd.Series(market_dispersion).rolling(720, min_periods=48).median().values
    disp_median = np.nan_to_num(disp_median, nan=np.nanmedian(market_dispersion))

    # High dispersion = above rolling median
    high_disp = market_dispersion > disp_median

    # Breadth momentum (24h change in market breadth)
    breadth_mom = np.zeros(n_btc)
    breadth_mom[24:] = market_breadth[24:] - market_breadth[:-24]

    # Rising breadth = positive momentum
    rising_breadth = breadth_mom > 0

    # 2x2 regime assignment:
    #   high_disp + rising   -> DIVERGENT_BULL  (0)
    #   high_disp + falling  -> DIVERGENT_BEAR  (1)
    #   low_disp  + rising   -> CORRELATED_BULL (2)
    #   low_disp  + falling  -> CORRELATED_BEAR (3)
    dispersion_regime = np.full(n_btc, 3, dtype=np.int8)  # default CORRELATED_BEAR
    dispersion_regime[high_disp & rising_breadth] = 0   # DIVERGENT_BULL
    dispersion_regime[high_disp & ~rising_breadth] = 1  # DIVERGENT_BEAR
    dispersion_regime[~high_disp & rising_breadth] = 2  # CORRELATED_BULL
    dispersion_regime[~high_disp & ~rising_breadth] = 3 # CORRELATED_BEAR

    return pd.DataFrame({
        'btc_ret_1h': btc_ret_1h,
        'btc_ret_24h': btc_ret_24h,
        'btc_vol_24h': btc_vol_24h,
        'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore,
        '_btc_ret_1h_series': btc_ret_1h,
        'dispersion_regime': dispersion_regime,
        '_market_dispersion': market_dispersion,
        '_breadth_momentum': breadth_mom,
    }, index=btc_idx), dispersion_regime


def build_dataset(token_dfs, top_tokens, market_ctx, btc_idx):
    """Build feature matrix + labels for all tokens, with timestamps and regime preserved."""
    all_X = []
    all_y = []
    all_ts = []
    all_tokens = []
    all_regimes = []

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
        feats['market_breadth_ema20'] = np.nan_to_num(ctx_aligned['market_breadth_ema20'].values, nan=0.5)
        feats['dispersion_zscore'] = np.nan_to_num(ctx_aligned['dispersion_zscore'].values, nan=0.0)

        # Token-BTC correlation
        token_ret = np.zeros(len(close))
        token_ret[1:] = np.log(close[1:] / close[:-1])
        btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret).rolling(168, min_periods=48).corr(
            pd.Series(btc_ret_aligned)).values
        feats['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)

        # Dispersion regime for each row
        regime_aligned = np.nan_to_num(ctx_aligned['dispersion_regime'].values, nan=3).astype(np.int8)

        # Labels
        labels = compute_labels(close)

        # Build X matrix (specialist features -- no market_regime)
        n = len(close)
        X = np.column_stack([feats.get(k, np.zeros(n)) for k in SPECIALIST_FEATURES])
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
        all_regimes.append(regime_aligned[idx_valid])

    X_all = np.vstack(all_X)
    y_all = np.concatenate(all_y)
    ts_all = np.concatenate(all_ts)
    regime_all = np.concatenate(all_regimes)

    return X_all, y_all, ts_all, np.array(all_tokens), regime_all


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


def evaluate_regime_model(model, X_test, y_test, regime_name):
    """Evaluate a single regime-specialist model. Returns results dict."""
    if len(X_test) < 10:
        return None

    proba = model.predict_proba(X_test)
    classes = list(model.classes_)

    long_prob = proba[:, classes.index(1)] if 1 in classes else np.zeros(len(y_test))
    short_prob = proba[:, classes.index(-1)] if -1 in classes else np.zeros(len(y_test))

    base_rate_long = np.mean(y_test == 1) * 100
    base_rate_short = np.mean(y_test == -1) * 100

    print(f'\n  Regime: {regime_name}')
    print(f'    Test samples: {len(y_test)}  |  Base rates: Long {base_rate_long:.1f}%, Short {base_rate_short:.1f}%')
    print(f'    {"Thresh":>6} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'    {"-"*6}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')

    results = {}
    for thr in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
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

    # Feature importance - top 10
    if hasattr(model, 'feature_importances_'):
        fi = model.feature_importances_
        top_idx = np.argsort(fi)[::-1][:10]
        print(f'    Top 10 Features:')
        for rank, i in enumerate(top_idx):
            print(f'      {rank+1:>2}. {SPECIALIST_FEATURES[i]:<25} {fi[i]:.4f}')

    return {
        'results': results,
        'base_rate_long': base_rate_long,
        'base_rate_short': base_rate_short,
        'n_test': len(y_test),
    }


def main():
    t0 = time.time()
    print('=' * 70)
    print('  EXPERIMENT C: REGIME-SPECIALIST MODELS')
    print('=' * 70)
    print(f'  Train window: {TRAIN_START.date()} to {TRAIN_CUTOFF.date()}')
    print(f'  Test: {TRAIN_CUTOFF.date()} onward')
    print(f'  Features: {len(SPECIALIST_FEATURES)} (V2 41 minus market_regime)')
    print(f'  Regime split: dispersion-breadth 2x2 matrix')
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
    print(f'  BTC range: {btc_idx.min()} -> {btc_idx.max()}')
    print(f'  Data loaded: {len(token_dfs)} tokens')

    # ── Build market context + dispersion regimes ─────────────────
    print('\nBuilding market context + dispersion regimes...')
    market_ctx, disp_regime_btc = build_market_context_and_regimes(
        top_tokens, token_dfs, btc_idx)
    print(f'  Market context: {market_ctx.shape}')

    # ── Regime time analysis (on full BTC timeline) ───────────────
    print('\n  DISPERSION REGIME TIME DISTRIBUTION (full BTC timeline):')
    for rv, rn in sorted(REGIME_NAMES.items()):
        count = np.sum(disp_regime_btc == rv)
        pct = count / len(disp_regime_btc) * 100
        print(f'    {rn:<20}: {count:>7} hours ({pct:.1f}%)')

    # ── Build dataset ──────────────────────────────────────────────
    print('\nBuilding feature matrix...')
    X_all, y_all, ts_all, tokens_all, regime_all = build_dataset(
        token_dfs, top_tokens, market_ctx, btc_idx)
    print(f'  Total samples: {len(y_all)}')
    print(f'  Class dist: +1={np.sum(y_all==1)}, -1={np.sum(y_all==-1)}')
    print(f'  Date range: {pd.Timestamp(ts_all.min())} -> {pd.Timestamp(ts_all.max())}')

    # ── Temporal split ─────────────────────────────────────────────
    ts_naive = pd.DatetimeIndex(ts_all)
    if ts_naive.tz is not None:
        ts_naive = ts_naive.tz_localize(None)

    # Train: only Jan 2024 to June 2025 (recent data)
    train_mask = (ts_naive >= TRAIN_START) & (ts_naive < TRAIN_CUTOFF)
    test_mask = ts_naive >= TRAIN_CUTOFF

    X_train_full = X_all[train_mask]
    y_train_full = y_all[train_mask]
    regime_train = regime_all[train_mask]

    X_test_full = X_all[test_mask]
    y_test_full = y_all[test_mask]
    regime_test = regime_all[test_mask]
    ts_test = ts_all[test_mask]

    print(f'\n  TEMPORAL SPLIT:')
    print(f'    Train: {train_mask.sum()} samples ({TRAIN_START.date()} to {TRAIN_CUTOFF.date()})')
    print(f'    Test:  {test_mask.sum()} samples ({TRAIN_CUTOFF.date()} onward)')
    if train_mask.sum() > 0:
        print(f'    Train date range: {pd.Timestamp(ts_all[train_mask].min())} -> {pd.Timestamp(ts_all[train_mask].max())}')
    if test_mask.sum() > 0:
        print(f'    Test date range:  {pd.Timestamp(ts_test.min())} -> {pd.Timestamp(ts_test.max())}')

    if test_mask.sum() < 100:
        print('  ERROR: Not enough test samples. Check cutoff date.')
        return

    # ── Regime distribution in train/test ──────────────────────────
    print(f'\n{"="*70}')
    print(f'  DISPERSION REGIME DISTRIBUTION:')
    print(f'{"="*70}')
    print(f'  {"Regime":<20} | {"Train N":>8} | {"Test N":>7} | {"% of data":>9}')
    print(f'  {"-"*20}-+-{"-"*8}-+-{"-"*7}-+-{"-"*9}')
    total = len(y_train_full) + len(y_test_full)
    for rv, rn in sorted(REGIME_NAMES.items()):
        n_train = np.sum(regime_train == rv)
        n_test = np.sum(regime_test == rv)
        pct = (n_train + n_test) / total * 100
        print(f'  {rn:<20} | {n_train:>8} | {n_test:>7} | {pct:>8.1f}%')
    print(f'  {"TOTAL":<20} | {len(y_train_full):>8} | {len(y_test_full):>7} | {100.0:>8.1f}%')

    # ── Train per-regime specialist models ─────────────────────────
    print(f'\n{"="*70}')
    print(f'  PER-REGIME MODEL EVALUATION (OOS):')
    print(f'{"="*70}')

    regime_models = {}
    regime_results = {}

    for rv, rn in sorted(REGIME_NAMES.items()):
        # Split by regime
        r_train_mask = regime_train == rv
        r_test_mask = regime_test == rv

        X_r_train = X_train_full[r_train_mask]
        y_r_train = y_train_full[r_train_mask]
        X_r_test = X_test_full[r_test_mask]
        y_r_test = y_test_full[r_test_mask]

        n_train_r = len(y_r_train)
        n_test_r = len(y_r_test)

        if n_train_r < 100 or n_test_r < 30:
            print(f'\n  Regime: {rn}')
            print(f'    SKIPPED: insufficient data (train={n_train_r}, test={n_test_r})')
            continue

        # Check class distribution
        n_pos = np.sum(y_r_train == 1)
        n_neg = np.sum(y_r_train == -1)
        if n_pos < 30 or n_neg < 30:
            print(f'\n  Regime: {rn}')
            print(f'    SKIPPED: insufficient class balance (pos={n_pos}, neg={n_neg})')
            continue

        # Undersample and train
        X_r_bal, y_r_bal = undersample(X_r_train, y_r_train)
        model = train_model(X_r_bal, y_r_bal)
        regime_models[rv] = model

        # Evaluate
        result = evaluate_regime_model(model, X_r_test, y_r_test, rn)
        if result is not None:
            regime_results[rv] = result

    # ── Also train and evaluate a single unified model for comparison ──
    print(f'\n{"="*70}')
    print(f'  BASELINE: SINGLE UNIFIED MODEL (same features, same data window)')
    print(f'{"="*70}')

    X_unified_bal, y_unified_bal = undersample(X_train_full, y_train_full)
    print(f'  Training unified model ({len(X_unified_bal)} balanced samples)...')
    model_unified = train_model(X_unified_bal, y_unified_bal)

    # Evaluate unified model on full test set
    proba_unified = model_unified.predict_proba(X_test_full)
    classes_unified = list(model_unified.classes_)
    lp_u = proba_unified[:, classes_unified.index(1)] if 1 in classes_unified else np.zeros(len(y_test_full))
    sp_u = proba_unified[:, classes_unified.index(-1)] if -1 in classes_unified else np.zeros(len(y_test_full))

    base_rate_long_u = np.mean(y_test_full == 1) * 100
    base_rate_short_u = np.mean(y_test_full == -1) * 100
    print(f'  Test samples: {len(y_test_full)}  |  Base rates: Long {base_rate_long_u:.1f}%, Short {base_rate_short_u:.1f}%')
    print(f'  {"Thresh":>6} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'  {"-"*6}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')
    unified_results = {}
    for thr in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        lm = lp_u >= thr
        sm = sp_u >= thr
        lprec = np.mean(y_test_full[lm] == 1) * 100 if lm.sum() > 0 else 0
        sprec = np.mean(y_test_full[sm] == -1) * 100 if sm.sum() > 0 else 0
        print(f'  {thr:>6.2f} | {lprec:>8.1f}% | {lm.sum():>6} | {sprec:>9.1f}% | {sm.sum():>7}')
        unified_results[thr] = {'long_prec': lprec, 'long_n': int(lm.sum()),
                                'short_prec': sprec, 'short_n': int(sm.sum())}

    # ── Also evaluate unified model PER REGIME (for fair comparison) ──
    print(f'\n  UNIFIED MODEL — PER-REGIME BREAKDOWN (p>=0.65):')
    print(f'  {"Regime":<20} | {"Long Prec":>9} | {"Long N":>6} | {"Short Prec":>10} | {"Short N":>7}')
    print(f'  {"-"*20}-+-{"-"*9}-+-{"-"*6}-+-{"-"*10}-+-{"-"*7}')
    unified_per_regime = {}
    for rv, rn in sorted(REGIME_NAMES.items()):
        rmask = regime_test == rv
        if rmask.sum() < 10:
            continue
        lm_r = rmask & (lp_u >= 0.65)
        sm_r = rmask & (sp_u >= 0.65)
        lprec_r = np.mean(y_test_full[lm_r] == 1) * 100 if lm_r.sum() > 0 else 0
        sprec_r = np.mean(y_test_full[sm_r] == -1) * 100 if sm_r.sum() > 0 else 0
        print(f'  {rn:<20} | {lprec_r:>8.1f}% | {lm_r.sum():>6} | {sprec_r:>9.1f}% | {sm_r.sum():>7}')
        unified_per_regime[rv] = {
            'long_prec': lprec_r, 'long_n': int(lm_r.sum()),
            'short_prec': sprec_r, 'short_n': int(sm_r.sum()),
        }

    # ── Aggregate: best direction per regime ───────────────────────
    print(f'\n{"="*70}')
    print(f'  AGGREGATE (selecting best direction per regime at p>=0.65):')
    print(f'{"="*70}')
    print(f'  {"Regime":<20} | {"Best Dir":>8} | {"Spec Prec":>9} | {"Uni Prec":>8} | {"Spec N":>6} | {"Uni N":>5} | {"vs Base":>7}')
    print(f'  {"-"*20}-+-{"-"*8}-+-{"-"*9}-+-{"-"*8}-+-{"-"*6}-+-{"-"*5}-+-{"-"*7}')

    total_spec_trades = 0
    total_spec_correct = 0
    total_uni_trades = 0
    total_uni_correct = 0
    tradeable_regimes = []

    for rv, rn in sorted(REGIME_NAMES.items()):
        if rv not in regime_results:
            print(f'  {rn:<20} | {"N/A":>8} | {"N/A":>9} | {"N/A":>8} | {"N/A":>6} | {"N/A":>5} | {"N/A":>7}')
            continue

        res = regime_results[rv]['results']
        base_long = regime_results[rv]['base_rate_long']
        base_short = regime_results[rv]['base_rate_short']

        # Pick best direction at p>=0.65 for specialist
        thr = 0.65
        spec_long_prec = res[thr]['long_prec']
        spec_short_prec = res[thr]['short_prec']
        spec_long_n = res[thr]['long_n']
        spec_short_n = res[thr]['short_n']

        if spec_long_prec > spec_short_prec and spec_long_n > 5:
            best_dir = 'Long'
            spec_prec = spec_long_prec
            spec_n = spec_long_n
            base = base_long
        elif spec_short_n > 5:
            best_dir = 'Short'
            spec_prec = spec_short_prec
            spec_n = spec_short_n
            base = base_short
        else:
            best_dir = 'Long'
            spec_prec = spec_long_prec
            spec_n = spec_long_n
            base = base_long

        # Unified comparison
        uni = unified_per_regime.get(rv, {})
        if best_dir == 'Long':
            uni_prec = uni.get('long_prec', 0)
            uni_n = uni.get('long_n', 0)
        else:
            uni_prec = uni.get('short_prec', 0)
            uni_n = uni.get('short_n', 0)

        vs_base = spec_prec - base

        print(f'  {rn:<20} | {best_dir:>8} | {spec_prec:>8.1f}% | {uni_prec:>7.1f}% | {spec_n:>6} | {uni_n:>5} | {vs_base:>+6.1f}pp')

        if spec_prec > 55 and spec_n > 10:
            tradeable_regimes.append(rn)
            # Compute correct trades for specialist
            r_test_mask = regime_test == rv
            X_r_test = X_test_full[r_test_mask]
            y_r_test = y_test_full[r_test_mask]
            model = regime_models[rv]
            p_r = model.predict_proba(X_r_test)
            cls = list(model.classes_)
            if best_dir == 'Long':
                prob_col = p_r[:, cls.index(1)] if 1 in cls else np.zeros(len(y_r_test))
                mask = prob_col >= 0.65
                correct = np.sum(y_r_test[mask] == 1)
            else:
                prob_col = p_r[:, cls.index(-1)] if -1 in cls else np.zeros(len(y_r_test))
                mask = prob_col >= 0.65
                correct = np.sum(y_r_test[mask] == -1)
            total_spec_trades += int(mask.sum())
            total_spec_correct += int(correct)

    # ── Combined strategy stats ────────────────────────────────────
    print(f'\n{"="*70}')
    print(f'  COMBINED STRATEGY ANALYSIS')
    print(f'{"="*70}')
    print(f'  Tradeable regimes (>55% prec, >10 trades): {", ".join(tradeable_regimes) if tradeable_regimes else "NONE"}')
    if total_spec_trades > 0:
        combined_prec = total_spec_correct / total_spec_trades * 100
        print(f'  Total specialist trades (combined): {total_spec_trades}')
        print(f'  Combined precision: {combined_prec:.1f}%')
        print(f'  Meets minimum trade count (>100)? {"YES" if total_spec_trades > 100 else "NO"}')
    else:
        print(f'  Total specialist trades: 0 -- no tradeable regimes found')

    # ── Time spent in each regime ──────────────────────────────────
    print(f'\n  TIME IN EACH REGIME (test period):')
    ts_test_pd = pd.DatetimeIndex(ts_test)
    # Approximate unique hours
    unique_hours = len(np.unique(ts_test_pd.floor('h')))
    for rv, rn in sorted(REGIME_NAMES.items()):
        rmask = regime_test == rv
        regime_hours = len(np.unique(ts_test_pd[rmask].floor('h')))
        pct = regime_hours / max(unique_hours, 1) * 100
        print(f'    {rn:<20}: ~{regime_hours:>5} hours ({pct:.1f}%)')

    # ── Monthly breakdown for specialist models ───────────────────
    print(f'\n  MONTHLY SPECIALIST OOS PRECISION (best direction per regime, p>=0.65):')
    ts_test_pd_full = pd.DatetimeIndex(ts_test)
    months = ts_test_pd_full.to_period('M').unique()
    print(f'  {"Month":>8} |', end='')
    for rv, rn in sorted(REGIME_NAMES.items()):
        short_name = rn.replace('DIVERGENT_', 'DV_').replace('CORRELATED_', 'CR_')
        print(f' {short_name:>10} |', end='')
    print()
    print(f'  {"-"*8}-+', end='')
    for _ in REGIME_NAMES:
        print(f'-{"-"*10}-+', end='')
    print()

    for m in months:
        mmask_full = ts_test_pd_full.to_period('M') == m
        print(f'  {str(m):>8} |', end='')
        for rv, rn in sorted(REGIME_NAMES.items()):
            if rv not in regime_models:
                print(f' {"N/A":>10} |', end='')
                continue
            rmask = regime_test == rv
            combined = mmask_full & rmask
            if combined.sum() < 5:
                print(f' {"<5 samp":>10} |', end='')
                continue
            X_rm = X_test_full[combined]
            y_rm = y_test_full[combined]
            model = regime_models[rv]
            p_rm = model.predict_proba(X_rm)
            cls = list(model.classes_)
            lp_rm = p_rm[:, cls.index(1)] if 1 in cls else np.zeros(len(y_rm))
            sp_rm = p_rm[:, cls.index(-1)] if -1 in cls else np.zeros(len(y_rm))
            lm_rm = lp_rm >= 0.65
            sm_rm = sp_rm >= 0.65
            lprec_rm = np.mean(y_rm[lm_rm] == 1) * 100 if lm_rm.sum() > 0 else 0
            sprec_rm = np.mean(y_rm[sm_rm] == -1) * 100 if sm_rm.sum() > 0 else 0
            best = max(lprec_rm, sprec_rm)
            n_best = lm_rm.sum() if lprec_rm >= sprec_rm else sm_rm.sum()
            dir_label = 'L' if lprec_rm >= sprec_rm else 'S'
            if n_best > 0:
                print(f' {best:>5.1f}%{dir_label}{n_best:>2} |', end='')
            else:
                print(f' {"0 sig":>10} |', end='')
        print()

    # ── VERDICT ────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print(f'\n{"="*70}')
    print(f'  VERDICT: Does regime specialization add alpha?')
    print(f'{"="*70}')

    # Check if any specialist meaningfully beats unified
    any_edge = False
    for rv, rn in sorted(REGIME_NAMES.items()):
        if rv not in regime_results:
            continue
        res = regime_results[rv]['results']
        thr = 0.65
        spec_best = max(res[thr]['long_prec'], res[thr]['short_prec'])
        uni = unified_per_regime.get(rv, {})
        uni_best = max(uni.get('long_prec', 0), uni.get('short_prec', 0))
        if spec_best > 55 and spec_best > uni_best + 3:
            any_edge = True
            print(f'  {rn}: specialist {spec_best:.1f}% > unified {uni_best:.1f}% (+{spec_best - uni_best:.1f}pp)')

    if any_edge:
        if total_spec_trades > 100:
            print(f'\n  CONCLUSION: Regime specialization shows PROMISE')
            print(f'  Combined trades: {total_spec_trades} (sufficient)')
            if total_spec_trades > 0:
                print(f'  Combined precision: {total_spec_correct/total_spec_trades*100:.1f}%')
            print(f'  NEXT: Test with tighter thresholds, walk-forward validation')
        else:
            print(f'\n  CONCLUSION: Some regime edge exists but INSUFFICIENT TRADES ({total_spec_trades})')
            print(f'  Need >100 trades for statistical significance')
    else:
        print(f'\n  CONCLUSION: NO meaningful advantage from regime specialization')
        print(f'  Specialist models do not consistently beat unified model')
        print(f'  The regime-specific patterns seen in V2 were likely overfit artifacts')

    print(f'\n  Total runtime: {elapsed:.1f}s')


if __name__ == '__main__':
    main()
