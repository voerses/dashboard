"""
Strategy s212_auto: HF mean revert: 6h hold, BB extreme
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


    # Very short-term mean reversion
    entry_long = ((bb_pct < 0.02) & (rsi < 20) & (rsi > rsi_prev)
                  & (regime != CRISIS))
    entry_short = ((bb_pct > 0.98) & (rsi > 80) & (rsi < rsi_prev)
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
        stop_mult=2.0,
        trail_mult=1.5,
        target_mult=3.0,
        no_stop_bars=6,
        min_hold=3,
        max_hold=48,
        edge=0.4,
        exit_regimes={CRISIS},
        breakeven_atr=0.5,
        max_trade_pct=0.12,
        exchange='binance',
        name='s212_auto',
    )
