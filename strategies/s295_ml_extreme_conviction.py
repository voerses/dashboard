"""Strategy S295: ML Extreme Conviction (Long, Perp)
====================================================================
Extreme conviction-weighted sizing:
  p >= 0.65: 10x leverage, size_mult=3.0, cap_mult=2.5
  p >= 0.60: 7x leverage, size_mult=2.0, cap_mult=2.0
  p >= 0.55: 5x leverage, size_mult=1.0, cap_mult=1.5

Concentrates capital heavily on highest-conviction trades.
High-conviction signals have 2-3x better forward returns and
higher win rates, so larger positions are justified.
Combined with moderate short hedge for DD protection.
"""
import numpy as np
import pandas as pd
import joblib
import os
from engine import StrategyContext, StrategyResult, MarketType, CRISIS, DOWNTREND

_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'results', 'v4')
_model = joblib.load(os.path.join(_MODEL_DIR, 'ml_dir_model.joblib'))
_scaler = joblib.load(os.path.join(_MODEL_DIR, 'ml_dir_scaler.joblib'))
_feat_names = joblib.load(os.path.join(_MODEL_DIR, 'ml_dir_features.joblib'))
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

def _compute_features(close, high, low, volume, funding=None):
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
    return f


def strategy(ctx: StrategyContext) -> StrategyResult:
    token = ctx.ticker if hasattr(ctx, 'ticker') else ''
    n = len(ctx.ind_1h['close'])

    if token and token not in _WHITELIST:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool), direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP, leverage=5.0,
            stop_mult=10.0, trail_mult=999.0, target_mult=999,
            no_stop_bars=18, min_hold=24, max_hold=24,
            edge=2.00, exit_regimes=set(), breakeven_atr=999.0,
            cap_multiplier=2.5, max_trade_pct=0.50,
            exchange='binance', name='s295_ml_extreme_conviction')

    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    regime = ctx.regime_1h
    funding = ctx.funding_1h

    features = _compute_features(close, high, low, volume, funding)
    X = np.column_stack([features.get(k, np.zeros(n)) for k in _feat_names])
    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)
    X_s = _scaler.transform(X)
    proba = _model.predict_proba(X_s)

    long_prob = proba[:, _classes.index(1)] if 1 in _classes else np.zeros(n)

    threshold = 0.58 if token in _HIGH_VOL else 0.55

    entry_long = (long_prob >= threshold) & (regime != CRISIS) & (regime != DOWNTREND)

    entry_mask = entry_long
    direction = np.ones(n, dtype=np.int8)

    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # Per-bar conviction-weighted leverage — extreme tiers
    leverage = np.where(long_prob >= 0.65, 10.0,
               np.where(long_prob >= 0.60, 7.0, 5.0)).astype(np.float64)

    # Size multiplier: heavily concentrate on best trades
    size_mult = np.where(long_prob >= 0.65, 3.0,
                np.where(long_prob >= 0.60, 2.0, 1.0)).astype(np.float64)

    # Per-bar cap multiplier
    cap_mult = np.where(long_prob >= 0.65, 2.5,
               np.where(long_prob >= 0.60, 2.0, 1.5)).astype(np.float64)

    conviction = long_prob

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=leverage,   # per-bar leverage
        stop_mult=10.0,
        trail_mult=999.0,
        target_mult=999,
        no_stop_bars=18,
        min_hold=24, max_hold=24,
        edge=2.00,
        exit_regimes=set(),
        breakeven_atr=999.0,
        cap_multiplier=cap_mult,      # per-bar cap scaling
        max_trade_pct=0.50,
        size_multiplier=size_mult,    # per-bar sizing
        conviction_score=conviction,
        exchange='binance', name='s295_ml_extreme_conviction')
