"""
Strategy s214_auto: Donchian breakout in trend
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


    donch_h = ctx.ind_1h['donch_high']
    donch_l = ctx.ind_1h['donch_low']
    prev_close = np.roll(close, 1); prev_close[0] = close[0]

    # Breakout: close > 20-bar high, in uptrend
    entry_long = ((close >= donch_h) & (prev_close < donch_h)
                  & (adx > 25) & (ema_20 > ema_50)
                  & (regime != CRISIS))
    # Breakdown: close < 20-bar low, in downtrend
    entry_short = ((close <= donch_l) & (prev_close > donch_l)
                   & (adx > 25) & (ema_20 < ema_50)
                   & (regime != CRISIS))


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
        stop_mult=2.5,
        trail_mult=2.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.4,
        exit_regimes={CRISIS},
        breakeven_atr=0.8,
        max_trade_pct=0.12,
        exchange='binance',
        name='s214_auto',
    )
