"""
Strategy s207_auto: Multi-signal union 3x leverage
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


    sig1_long = ((rsi < 30) & (rsi > rsi_prev)
                 & (macd > macd_sig) & (macd_prev <= macd_sig_prev))
    sig1_short = ((rsi > 70) & (rsi < rsi_prev)
                  & (macd < macd_sig) & (macd_prev >= macd_sig_prev))
    sig2_long = (bb_pct < 0.05) & (rsi < 25) & (rsi > rsi_prev)
    sig2_short = (bb_pct > 0.95) & (rsi > 75) & (rsi < rsi_prev)
    sig3_long = ((close < ema_20) & (close > ema_50) & (rsi < 40) & (rsi > rsi_prev)
                 & (adx > 25) & (ema_20 > ema_50))
    sig3_short = ((close > ema_20) & (close < ema_50) & (rsi > 60) & (rsi < rsi_prev)
                  & (adx > 25) & (ema_20 < ema_50))
    entry_long = ((sig1_long | sig2_long | sig3_long) & (regime != CRISIS))
    entry_short = ((sig1_short | sig2_short | sig3_short) & (regime != CRISIS))


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
        leverage=3.0,
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.4,
        exit_regimes={CRISIS},
        breakeven_atr=0.8,
        max_trade_pct=0.12,
        exchange='binance',
        name='s207_auto',
    )
