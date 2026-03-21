"""Strategy S313: ML V2 Short (Short, Perp, 4x, cap_mult=1.3)
====================================================================
Short-only strategy using V2 HistGBM model with market-context features.
No manual regime filter — the model handles regime awareness internally.
"""
import numpy as np
import pandas as pd
import joblib
import os
from pathlib import Path
from engine import StrategyContext, StrategyResult, MarketType

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_DIR = os.path.join(_BASE_DIR, 'results', 'v4')
_DATA_DIR = Path(os.path.join(_BASE_DIR, 'data', 'perp', '1h_cache'))

_model = joblib.load(os.path.join(_MODEL_DIR, 'ml_dir_v2_model.joblib'))
_feat_names = joblib.load(os.path.join(_MODEL_DIR, 'ml_dir_v2_features.joblib'))
_classes = list(_model.classes_)

_WHITELIST = {'ETH', 'SOL', 'DOGE', 'XRP', 'ADA', 'LINK', 'SUI', 'HBAR'}
_HIGH_VOL = {'DOGE', 'XRP', 'HBAR'}


def _ema(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values
def _rmean(arr, w):
    cs = np.cumsum(arr); out = np.full(len(arr), np.nan); out[w-1] = cs[w-1] / w
    if len(arr) > w: out[w:] = (cs[w:] - cs[:-w]) / w
    return out
def _rstd(arr, w): return pd.Series(arr).rolling(w, min_periods=w).std().values
def _rmax(arr, w): return pd.Series(arr).rolling(w, min_periods=w).max().values
def _rmin(arr, w): return pd.Series(arr).rolling(w, min_periods=w).min().values


def _build_market_context():
    btc = pd.read_parquet(_DATA_DIR / 'BTC_1h.parquet')
    btc_close = btc['close'].values.astype(np.float64)
    btc_idx = btc.index
    n_btc = len(btc_close)

    btc_ret_1h = np.zeros(n_btc)
    btc_ret_1h[1:] = np.log(btc_close[1:] / btc_close[:-1])
    btc_ret_24h = np.zeros(n_btc)
    btc_ret_24h[24:] = np.log(btc_close[24:] / btc_close[:-24])
    btc_vol_24h = np.nan_to_num(_rstd(btc_ret_1h, 24), nan=0.0)

    btc_daily = btc.resample('D').agg({
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
    d_tr = np.maximum(d_high - d_low, np.maximum(np.abs(d_high - d_prev_c), np.abs(d_low - d_prev_c)))
    d_atr = _ema(d_tr, 14)
    d_pdm = np.maximum(np.diff(d_high, prepend=d_high[0]), 0)
    d_mdm = np.maximum(-np.diff(d_low, prepend=d_low[0]), 0)
    d_pdm = np.where(d_pdm > d_mdm, d_pdm, 0); d_mdm = np.where(d_mdm > d_pdm, d_mdm, 0)
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
    regime_daily[crisis] = 0; regime_daily[quiet] = 1
    regime_daily[strong & (d_ema20 > d_ema50)] = 2
    regime_daily[strong & ~(d_ema20 > d_ema50)] = 4

    regime_series = pd.Series(regime_daily, index=btc_daily.index)
    market_regime = regime_series.reindex(btc_idx, method='ffill').fillna(3).values

    token_files = sorted(_DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:30]]
    ret_24h_matrix = np.full((n_btc, 30), np.nan)
    above_ema20_matrix = np.full((n_btc, 30), np.nan)

    for i, token in enumerate(tokens):
        try:
            df = pd.read_parquet(_DATA_DIR / f'{token}_1h.parquet')
            aligned = pd.Series(df['close'].values.astype(np.float64), index=df.index).reindex(btc_idx)
            ret_24h_matrix[:, i] = np.log(aligned / aligned.shift(24)).values
            e20 = aligned.ewm(span=20, adjust=False).mean()
            above_ema20_matrix[:, i] = (aligned > e20).astype(float).values
        except Exception:
            continue

    market_dispersion = np.nanstd(ret_24h_matrix, axis=1)
    disp_mean = np.nan_to_num(_rmean(market_dispersion, 720), nan=0.0)
    disp_std = np.nan_to_num(_rstd(market_dispersion, 720), nan=1.0)
    dispersion_zscore = np.nan_to_num(
        (market_dispersion - disp_mean) / np.maximum(disp_std, 1e-10), nan=0.0, posinf=0.0, neginf=0.0)
    market_breadth = np.nan_to_num(np.nanmean(above_ema20_matrix, axis=1), nan=0.5)

    return pd.DataFrame({
        'btc_ret_1h': btc_ret_1h, 'btc_ret_24h': btc_ret_24h, 'btc_vol_24h': btc_vol_24h,
        'market_regime': market_regime, 'market_breadth_ema20': market_breadth,
        'dispersion_zscore': dispersion_zscore, '_btc_ret_1h_series': btc_ret_1h,
    }, index=btc_idx)


_market_ctx = _build_market_context()


def _compute_features(close, high, low, volume, funding, idx_1h):
    n = len(close)
    def lr(s):
        r = np.zeros(n); r[s:] = np.log(close[s:] / close[:-s]); return r
    ret_1=lr(1); ret_4=lr(4); ret_12=lr(12); ret_24=lr(24); ret_48=lr(48); ret_168=lr(168)
    e10=_ema(close,10); e20=_ema(close,20); e50=_ema(close,50)
    ed10=(close-e10)/np.maximum(close,1e-10); ed20=(close-e20)/np.maximum(close,1e-10); ed50=(close-e50)/np.maximum(close,1e-10)
    ea=np.sign(close-e10)+np.sign(close-e20)+np.sign(close-e50)
    e12=_ema(close,12); e26=_ema(close,26); macd=e12-e26; msig=_ema(macd,9)
    mn=macd/np.maximum(close,1e-10); mhn=(macd-msig)/np.maximum(close,1e-10)
    d=np.diff(close,prepend=close[0]); g=np.where(d>0,d,0.0); l=np.where(d<0,-d,0.0)
    rs=_ema(g,14)/np.maximum(_ema(l,14),1e-10); rsi=100-100/(1+rs); rm=np.diff(rsi,prepend=rsi[0])
    bm=_rmean(close,20); bs=np.nan_to_num(_rstd(close,20),nan=1.0)
    bu=bm+2*bs; bl=bm-2*bs
    bw=np.where(np.nan_to_num(bm,nan=1)>0,(bu-bl)/np.maximum(np.nan_to_num(bm,nan=1),1e-10),0)
    bp=np.where(bu>bl,(close-bl)/np.maximum(bu-bl,1e-10),0.5)
    pc=np.roll(close,1); pc[0]=close[0]
    tr=np.maximum(high-low,np.maximum(np.abs(high-pc),np.abs(low-pc))); atr=_ema(tr,14); ap=atr/np.maximum(close,1e-10)
    v5=_rstd(ret_1,5); v20=_rstd(ret_1,20); v50=_rstd(ret_1,50)
    vr52=np.where(np.nan_to_num(v20,nan=1)>0,np.nan_to_num(v5,nan=0)/np.maximum(np.nan_to_num(v20,nan=1),1e-10),1.0)
    vm20=_rmean(volume,20); vr=np.where(np.nan_to_num(vm20,nan=1)>0,volume/np.maximum(np.nan_to_num(vm20,nan=1),1e-10),1.0)
    pdm=np.maximum(np.diff(high,prepend=high[0]),0); mdm=np.maximum(-np.diff(low,prepend=low[0]),0)
    pdm=np.where(pdm>mdm,pdm,0); mdm=np.where(mdm>pdm,mdm,0)
    sp=_ema(pdm,14); sm=_ema(mdm,14)
    pdi=100*sp/np.maximum(atr,1e-10); mdi=100*sm/np.maximum(atr,1e-10)
    dx=100*np.abs(pdi-mdi)/np.maximum(pdi+mdi,1e-10); adx=_ema(dx,14)
    dh=np.nan_to_num(_rmax(high,20),nan=high[0]); dl=np.nan_to_num(_rmin(low,20),nan=low[0])
    dp=np.where(dh>dl,(close-dl)/np.maximum(dh-dl,1e-10),0.5)
    bpct=np.diff(close,prepend=close[0])/np.maximum(close,1e-10)
    cr=high-low; uw=high-np.maximum(close,pc); lw=np.minimum(close,pc)-low
    wr=np.where(cr>0,(uw-lw)/np.maximum(cr,1e-10),0)
    co=np.zeros(n)
    for i in range(1,n):
        if close[i]>close[i-1]: co[i]=max(co[i-1],0)+1
        elif close[i]<close[i-1]: co[i]=min(co[i-1],0)-1
    h20=np.nan_to_num(_rmax(high,20),nan=high[0]); l20=np.nan_to_num(_rmin(low,20),nan=low[0])
    dhi=(close-h20)/np.maximum(close,1e-10); dlo=(close-l20)/np.maximum(close,1e-10)

    f = {
        'adx': adx, 'atr_pct': ap, 'bb_pct': np.nan_to_num(bp,nan=0.5), 'bb_width': np.nan_to_num(bw,nan=0),
        'body_pct': bpct, 'consec': co, 'dist_high': dhi, 'dist_low': dlo, 'donch_pos': np.nan_to_num(dp,nan=0.5),
        'ema_align': ea, 'ema_dist_10': ed10, 'ema_dist_20': ed20, 'ema_dist_50': ed50,
        'macd_hist_norm': mhn, 'macd_norm': mn, 'minus_di': mdi, 'plus_di': pdi,
        'ret_1': ret_1, 'ret_12': ret_12, 'ret_168': ret_168, 'ret_24': ret_24,
        'ret_4': ret_4, 'ret_48': ret_48, 'rsi': rsi, 'rsi_mom': rm,
        'vol_20': np.nan_to_num(v20,nan=0), 'vol_5': np.nan_to_num(v5,nan=0),
        'vol_50': np.nan_to_num(v50,nan=0), 'vol_ratio': np.nan_to_num(vr,nan=1),
        'vol_ratio_5_20': np.nan_to_num(vr52,nan=1), 'wick_ratio': wr,
    }
    if funding is not None:
        f['funding'] = funding
        f['funding_ma48'] = np.nan_to_num(_rmean(funding,48),nan=0)
        f['funding_ma8'] = np.nan_to_num(_rmean(funding,8),nan=0)

    ctx_aligned = _market_ctx.reindex(idx_1h, method='ffill')
    f['btc_ret_1h'] = np.nan_to_num(ctx_aligned['btc_ret_1h'].values, nan=0.0)
    f['btc_ret_24h'] = np.nan_to_num(ctx_aligned['btc_ret_24h'].values, nan=0.0)
    f['btc_vol_24h'] = np.nan_to_num(ctx_aligned['btc_vol_24h'].values, nan=0.0)
    f['market_regime'] = np.nan_to_num(ctx_aligned['market_regime'].values, nan=3.0)
    f['market_breadth_ema20'] = np.nan_to_num(ctx_aligned['market_breadth_ema20'].values, nan=0.5)
    f['dispersion_zscore'] = np.nan_to_num(ctx_aligned['dispersion_zscore'].values, nan=0.0)

    token_ret_1h = np.zeros(n)
    token_ret_1h[1:] = np.log(close[1:] / close[:-1])
    btc_ret_aligned = np.nan_to_num(ctx_aligned['_btc_ret_1h_series'].values, nan=0.0)
    corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(pd.Series(btc_ret_aligned)).values
    f['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)
    return f


def strategy(ctx: StrategyContext) -> StrategyResult:
    token = ctx.ticker if hasattr(ctx, 'ticker') else ''
    n = len(ctx.ind_1h['close'])

    if token and token not in _WHITELIST:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool), direction=-np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP, leverage=4.0,
            stop_mult=10.0, trail_mult=999.0, target_mult=999,
            no_stop_bars=18, min_hold=24, max_hold=24,
            edge=2.00, exit_regimes=set(), breakeven_atr=999.0,
            cap_multiplier=1.3, max_trade_pct=0.35,
            exchange='binance', name='s313_ml_v2_short')

    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    funding = ctx.funding_1h

    features = _compute_features(close, high, low, volume, funding, ctx.idx_1h)
    X = np.column_stack([features.get(k, np.zeros(n)) for k in _feat_names])
    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)
    proba = _model.predict_proba(X)

    short_prob = proba[:, _classes.index(-1)] if -1 in _classes else np.zeros(n)
    threshold = 0.75

    # No manual regime filter — model handles regime internally
    entry_mask = short_prob >= threshold
    direction = -np.ones(n, dtype=np.int8)

    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=4.0,
        stop_mult=10.0, trail_mult=999.0, target_mult=999,
        no_stop_bars=18, min_hold=24, max_hold=24,
        edge=2.00, exit_regimes=set(), breakeven_atr=999.0,
        cap_multiplier=1.3, max_trade_pct=0.35,
        conviction_score=short_prob,
        exchange='binance', name='s313_ml_v2_short')
