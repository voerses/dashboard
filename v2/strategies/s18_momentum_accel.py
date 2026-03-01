"""
Strategy S18: Momentum Acceleration
====================================
From Signal Lab: short-term momentum signals weakened post-ETF,
but ACCELERATION (change in momentum) may still work.

Logic: Enter when momentum is ACCELERATING -- not just positive momentum,
       but momentum that's INCREASING. Combined with volume confirmation.
       This catches the "second wave" of momentum -- when a move that was
       already happening starts going faster.

Related research: Frog-in-the-Pan (Da, Gurun & Warachka 2014),
                  Momentum lifecycle (Vayanos & Woolley 2013).

Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Momentum Acceleration -- enter when momentum is speeding up."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']

    # 1. Compute multi-horizon returns (vectorized)
    ret_24h = np.zeros(n)
    ret_72h = np.zeros(n)
    ret_168h = np.zeros(n)  # 7 days
    ret_24h[24:] = close[24:] / close[:-24] - 1
    ret_72h[72:] = close[72:] / close[:-72] - 1
    ret_168h[168:] = close[168:] / close[:-168] - 1

    # 2. Acceleration: short-term momentum > medium-term momentum
    accel_1 = ret_24h > ret_72h / 3
    accel_2 = ret_72h > ret_168h * 72 / 168

    # 3. Positive momentum
    positive = ret_24h > 0.005

    # 4. Consistency: fraction of positive hourly returns in last 48h
    ret_1h = np.zeros(n)
    ret_1h[1:] = close[1:] / close[:-1] - 1
    pos_bars = (ret_1h > 0).astype(np.float64)
    pos_frac = pd.Series(pos_bars).rolling(48, min_periods=48).mean().values
    pos_frac = np.nan_to_num(pos_frac, nan=0.5)
    consistent = pos_frac > 0.55

    # 5. Trend + volume filter
    trend_ok = (close > ema20) & (adx > 15)
    vol_ok = vol_ratio > 0.8

    entry = accel_1 & accel_2 & positive & consistent & trend_ok & vol_ok
    entry[:300] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.35,
        exit_regimes={CRISIS, DOWNTREND},
        name='momentum_accel',
    )
