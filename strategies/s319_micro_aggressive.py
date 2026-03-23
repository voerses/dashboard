"""Strategy S316: Microstructure Short (8h hold, 4x leverage)
===============================================================
Uses Experiment D's combo model (10 microstructure + 5 TA features)
trained with strict OOS temporal holdout.

Microstructure features computed from 1-minute data:
  - VPIN approx (order flow toxicity)
  - Realized vol ratio (1m vs hourly)
  - Kyle lambda (price impact)
  - Amihud illiquidity
  - Intraday momentum/autocorrelation
  - Volume profile / CLV

Short-biased: short edge +9.6pp at p>=0.65, long edge +13.7pp at p>=0.60
"""
import numpy as np
import pandas as pd
import joblib
import os
import gc
from pathlib import Path
from engine import StrategyContext, StrategyResult, MarketType

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_PATH = os.path.join(_BASE_DIR, 'results', 'v5', 'ml_exp_d_best_model.joblib')
_DATA_1M_DIR = Path(os.path.join(_BASE_DIR, 'data', 'perp', '1m_cache'))
_DATA_1H_DIR = Path(os.path.join(_BASE_DIR, 'data', 'perp', '1h_cache'))

# Load model
_artifact = joblib.load(_MODEL_PATH)
_model = _artifact['model']
_feat_names = _artifact['features']
_classes = list(_model.classes_)

# Whitelist: tokens with sufficient 1m data (from experiment D)
_files_1m = sorted(_DATA_1M_DIR.glob('*_1m.parquet'),
                   key=lambda f: f.stat().st_size, reverse=True)
_WHITELIST = set(f.stem.replace('_1m', '') for f in _files_1m[:15])


# ── Microstructure feature computation from 1m data ──────────────────
def _compute_micro_hourly(df_1m):
    """Compute microstructure features from 1m data, aggregated to hourly.
    Exact copy of Experiment D's computation."""
    o = df_1m['open'].values.astype(np.float32)
    h = df_1m['high'].values.astype(np.float32)
    lo = df_1m['low'].values.astype(np.float32)
    c = df_1m['close'].values.astype(np.float32)
    v = df_1m['volume'].values.astype(np.float32)
    n = len(df_1m)

    log_ret = np.zeros(n, dtype=np.float32)
    log_ret[1:] = np.log(np.maximum(c[1:], 1e-10) / np.maximum(c[:-1], 1e-10))

    hl_range = np.maximum(h - lo, 1e-10)
    buy_frac = (c - lo) / hl_range
    abs_log_oc = np.abs(np.log(np.maximum(c, 1e-10) / np.maximum(o, 1e-10)))
    log_hl = np.log(np.maximum(h, 1e-10) / np.maximum(lo, 1e-10))

    hour_idx = df_1m.index.floor('h')
    unique_hours = hour_idx.unique().sort_values()
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

    hour_labels = hour_idx.values
    changes = np.where(hour_labels[1:] != hour_labels[:-1])[0] + 1
    starts = np.concatenate([[0], changes])
    ends = np.concatenate([changes, [n]])

    for i in range(n_hours):
        s, e = starts[i], ends[i]
        count = e - s
        if count < 10:
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

        buy_vol = (bf_slice * v_slice).sum()
        sell_vol = ((1.0 - bf_slice) * v_slice).sum()
        vpin_hourly[i] = abs(buy_vol - sell_vol) / total_vol

        rv_1m = np.std(lr_slice) * np.sqrt(60.0) if count >= 2 else 0.0
        rv_1h = abs(np.log(max(c_slice[-1], 1e-10) / max(o_slice[0], 1e-10)))
        rv_ratio[i] = rv_1m / max(rv_1h, 1e-10)

        mid = count // 2
        if mid >= 2 and (count - mid) >= 2:
            ret_first = c_slice[mid - 1] / max(o_slice[0], 1e-10) - 1.0
            ret_second = c_slice[-1] / max(c_slice[mid - 1], 1e-10) - 1.0
            intra_mom[i] = np.sign(ret_first) * np.sign(ret_second)
        else:
            intra_mom[i] = 0.0

        third = count // 3
        if third >= 1:
            v1 = v_slice[:third].sum()
            v2 = v_slice[third:2*third].sum()
            v3 = v_slice[2*third:].sum()
            mean_third = (v1 + v2 + v3) / 3.0
            vol_profile[i] = max(v1, v2, v3) / max(mean_third, 1e-10)
        else:
            vol_profile[i] = 1.0

        sqrt_v = np.sqrt(np.maximum(v_slice, 1e-10))
        kyle_lam[i] = np.median(abs_oc_slice / sqrt_v)

        amihud_arr[i] = np.mean(np.abs(lr_slice) / np.maximum(v_slice, 1e-10))
        park_vol[i] = np.sum(lh_slice ** 2) / (4.0 * count * np.log(2.0))

        if count >= 3:
            r1, r2 = lr_slice[:-1], lr_slice[1:]
            if np.std(r1) > 1e-10 and np.std(r2) > 1e-10:
                autocorr_arr[i] = np.corrcoef(r1, r2)[0, 1]
            else:
                autocorr_arr[i] = 0.0
        else:
            autocorr_arr[i] = 0.0

        clv_arr[i] = np.mean(bf_slice)

    result = pd.DataFrame({
        'vpin_approx': vpin_hourly, 'realized_vol_ratio': rv_ratio,
        'intraday_momentum': intra_mom, 'volume_profile': vol_profile,
        'kyle_lambda': kyle_lam, 'amihud': amihud_arr,
        'parkinson_vol': park_vol, 'autocorrelation': autocorr_arr,
        'clv': clv_arr,
    }, index=unique_hours)

    result['vpin_4h'] = result['vpin_approx'].rolling(4, min_periods=2).mean()

    hourly_vol = pd.Series(np.nan, index=unique_hours, dtype=np.float32)
    for i in range(n_hours):
        s, e = starts[i], ends[i]
        hourly_vol.iloc[i] = v[s:e].sum()
    vol_4h = hourly_vol.rolling(4, min_periods=2).sum()
    vol_24h = hourly_vol.rolling(24, min_periods=12).sum()
    result['volume_momentum'] = (vol_4h / np.maximum(vol_24h, 1e-10)) * 6.0

    return result.astype(np.float32)


# ── BTC context ──────────────────────────────────────────────────────
def _build_btc_ret():
    btc = pd.read_parquet(_DATA_1H_DIR / 'BTC_1h.parquet')
    c = btc['close'].values.astype(np.float64)
    n = len(c)
    ret = np.full(n, np.nan)
    ret[24:] = np.log(c[24:] / c[:-24])
    return pd.Series(ret, index=btc.index, name='btc_ret_24h', dtype=np.float32)

_btc_ret_24h = _build_btc_ret()


# ── Precompute micro features for all whitelist tokens ────────────────
print(f"[s316] Precomputing microstructure features for {len(_WHITELIST)} tokens...")
_micro_cache = {}

for _tok in sorted(_WHITELIST):
    _path_1m = _DATA_1M_DIR / f'{_tok}_1m.parquet'
    if not _path_1m.exists():
        continue
    try:
        _df_1m = pd.read_parquet(_path_1m)
        if len(_df_1m) < 60 * 24 * 7:  # need at least 7 days
            continue
        _micro_cache[_tok] = _compute_micro_hourly(_df_1m)
        del _df_1m
        gc.collect()
    except Exception as e:
        print(f"  [s316] {_tok}: error computing micro features: {e}")

print(f"[s316] Micro features cached for {len(_micro_cache)} tokens")


# ── Strategy function ─────────────────────────────────────────────────
def strategy(ctx: StrategyContext) -> StrategyResult:
    token = ctx.ticker if hasattr(ctx, 'ticker') else ''
    n = len(ctx.ind_1h['close'])

    # Default empty result
    empty = StrategyResult(
        entry_mask=np.zeros(n, dtype=bool),
        direction=-np.ones(n, dtype=np.int8),
        market_type=MarketType.PERP, leverage=2.0,
        stop_mult=2.5, trail_mult=4.0, target_mult=3.0,
        no_stop_bars=4, min_hold=4, max_hold=8,
        edge=1.50, exit_regimes=set(), breakeven_atr=999.0,
        cap_multiplier=1.0, max_trade_pct=0.25,
        exchange='binance', name='s319_micro_aggressive')

    if token not in _micro_cache:
        return empty

    # Get precomputed micro features
    micro = _micro_cache[token]

    # Compute TA features inline
    close = ctx.ind_1h['close']
    idx_1h = ctx.idx_1h

    # ret_24h
    ret_24h = np.full(n, 0.0, dtype=np.float32)
    ret_24h[24:] = np.log(close[24:] / close[:-24]).astype(np.float32)

    # vol_20
    lr = np.zeros(n, dtype=np.float32)
    lr[1:] = np.log(close[1:] / close[:-1]).astype(np.float32)
    vol_20 = pd.Series(lr).rolling(20, min_periods=10).std().values.astype(np.float32)

    # rsi_14
    d = np.diff(close, prepend=close[0])
    g = np.where(d > 0, d, 0.0)
    l_ = np.where(d < 0, -d, 0.0)
    ag = pd.Series(g).ewm(span=14, adjust=False).mean().values
    al = pd.Series(l_).ewm(span=14, adjust=False).mean().values
    rs = ag / np.maximum(al, 1e-10)
    rsi = (100.0 - 100.0 / (1.0 + rs)).astype(np.float32)

    # funding
    funding = np.zeros(n, dtype=np.float32)
    if ctx.funding_1h is not None and len(ctx.funding_1h) == n:
        funding = np.nan_to_num(ctx.funding_1h, nan=0).astype(np.float32)

    # btc_ret_24h
    btc_aligned = _btc_ret_24h.reindex(idx_1h, method='ffill').fillna(0).values.astype(np.float32)

    # Align micro features to this token's hourly index
    micro_aligned = micro.reindex(idx_1h, method='ffill')

    # Build feature matrix in correct order
    # MICRO_FEATURES + TA_FEATURES
    feat_dict = {
        'vpin_approx': np.nan_to_num(micro_aligned['vpin_approx'].values, nan=0),
        'vpin_4h': np.nan_to_num(micro_aligned['vpin_4h'].values, nan=0),
        'realized_vol_ratio': np.nan_to_num(micro_aligned['realized_vol_ratio'].values, nan=1),
        'intraday_momentum': np.nan_to_num(micro_aligned['intraday_momentum'].values, nan=0),
        'volume_profile': np.nan_to_num(micro_aligned['volume_profile'].values, nan=1),
        'kyle_lambda': np.nan_to_num(micro_aligned['kyle_lambda'].values, nan=0),
        'amihud': np.nan_to_num(micro_aligned['amihud'].values, nan=0),
        'parkinson_vol': np.nan_to_num(micro_aligned['parkinson_vol'].values, nan=0),
        'autocorrelation': np.nan_to_num(micro_aligned['autocorrelation'].values, nan=0),
        'clv': np.nan_to_num(micro_aligned['clv'].values, nan=0.5),
        'volume_momentum': np.nan_to_num(micro_aligned['volume_momentum'].values, nan=1),
        'ret_24h': np.nan_to_num(ret_24h, nan=0),
        'vol_20': np.nan_to_num(vol_20, nan=0),
        'rsi_14': np.nan_to_num(rsi, nan=50),
        'funding_1h': np.nan_to_num(funding, nan=0),
        'btc_ret_24h': np.nan_to_num(btc_aligned, nan=0),
    }

    X = np.column_stack([feat_dict.get(k, np.zeros(n)) for k in _feat_names])
    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0).astype(np.float32)

    # Predict
    proba = _model.predict_proba(X)
    short_prob = proba[:, _classes.index(-1)] if -1 in _classes else np.zeros(n)
    long_prob = proba[:, _classes.index(1)] if 1 in _classes else np.zeros(n)

    # V4: Short-only p>=0.65, 3x leverage, no stops — maximum aggression on confirmed edge
    short_entry = short_prob >= 0.65

    direction = -np.ones(n, dtype=np.int8)
    entry_mask = short_entry

    # Safety: skip first 200 bars (warmup)
    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=3.0,
        stop_mult=999.0, trail_mult=999.0, target_mult=999.0,
        no_stop_bars=8, min_hold=8, max_hold=8,
        edge=1.50, exit_regimes=set(), breakeven_atr=999.0,
        cap_multiplier=1.5, max_trade_pct=0.35,
        conviction_score=short_prob,
        exchange='binance', name='s319_micro_aggressive')
