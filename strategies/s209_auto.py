"""
Strategy s209_auto: Vol-adaptive: low vol = bigger bets
"""
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = 200

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)
    regime = ctx.regime_1h
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    atr = ctx.ind_1h['atr']
    adx = ctx.ind_1h['adx']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    bb_pct = ctx.ind_1h['bb_pct']
    vol_ratio = ctx.ind_1h['vol_ratio']
    vol_20 = ctx.ind_1h['vol_20']

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0

    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(n)


    # Low volatility → bigger moves coming, take directional bets
    vol_percentile = np.zeros(n)
    for i in range(200, n):
        window = vol_20[100:i+1]
        vol_percentile[i] = (window < vol_20[i]).sum() / len(window)

    low_vol = vol_percentile < 0.25  # Bottom quartile

    # In low vol, use momentum to pick direction
    ret_24h = np.zeros(n)
    ret_24h[24:] = close[24:] / close[:-24] - 1

    entry_long = (low_vol & (ret_24h > 0) & (rsi > 50) & (rsi < 70)
                  & (macd > macd_sig) & (regime != CRISIS))
    entry_short = (low_vol & (ret_24h < 0) & (rsi < 50) & (rsi > 30)
                   & (macd < macd_sig) & (regime != CRISIS))


    entry_mask = entry_long | entry_short
    direction = np.where(entry_long & ~entry_short, 1,
                np.where(entry_short & ~entry_long, -1, 1)).astype(np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=2.0,
        stop_mult=2.0,
        trail_mult=1.5,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=168,
        edge=0.4,
        exit_regimes={CRISIS},
        breakeven_atr=0.8,
        max_trade_pct=0.12,
        exchange='binance',
        name='s209_auto',
    )
