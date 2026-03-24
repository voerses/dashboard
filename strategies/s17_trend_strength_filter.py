"""
Strategy S17: Trend Strength Filter
=====================================
Refined version of S09/S11 using Signal Lab insights:
- ADX alone (IC=+0.017 post-ETF) has weakened but still works
- EMA stack (IC=+0.008) barely works post-ETF
- di_crossover (IC=-0.028) FLIPPED — strong -DI now predicts gains
  (contrarian: when bears dominate on DI, bounce follows)

Logic: Instead of requiring strong uptrend, look for ADX > 25
       (any strong directional move) + positive momentum burst.
       This catches both trend continuations and reversal setups.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Trend Strength Filter — strong ADX + momentum burst, any direction."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ret_1 = ctx.ind_1h['ret_1']
    vol_ratio = ctx.ind_1h['vol_ratio']
    rsi = ctx.ind_1h['rsi']

    # 1. Strong directional move (ADX > 25 — any direction)
    strong_move = adx > 25

    # 2. Momentum burst (>2% move, slightly lower threshold than S11)
    burst = ret_1 > 0.02

    # 3. Bullish DI: +DI > -DI (we're going long)
    bullish_di = plus_di > minus_di

    # 4. Price above EMA10 (short-term trend aligned)
    above_ema = close > ema10

    # 5. Volume confirmation (at least average)
    vol_ok = vol_ratio > 1.0

    # 6. RSI not overbought (don't chase exhausted moves)
    rsi_ok = rsi < 75

    entry = strong_move & burst & bullish_di & above_ema & vol_ok & rsi_ok
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        name='trend_strength_filter',
        breakeven_atr=0.5,
    )
