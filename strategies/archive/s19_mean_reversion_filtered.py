"""
Strategy S19: Mean Reversion with Quality Filter
==================================================
All previous mean reversion strategies LOST money (-$25K to -$185K/yr).
Signal Lab shows: mean_reversion_5d IC=+0.022 post-ETF (improved from -0.008 pre-ETF).
This is actually positive now! But raw mean reversion still loses.

Key insight: Mean reversion ONLY works with heavy filtering:
- Only after sharp drops (>10% in 5 days)
- Only on liquid tokens (high volume)
- Only when vol-of-vol is low (stable environment)
- Only when NOT in bear market regime
- Wide stops (4x ATR) -- tight stops kill MR (research confirmed)

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Filtered Mean Reversion -- only take the highest-quality bounce setups."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    rsi = ctx.ind_1h['rsi']
    bb_pct = ctx.ind_1h['bb_pct']
    ema50 = ctx.ind_1h['ema_50']
    vol_ratio = ctx.ind_1h['vol_ratio']

    # 1. Sharp drop: price fell >8% in last 120 bars (5 days) -- vectorized
    ret_5d = ctx.custom.get('ret_5d', None)
    if ret_5d is None:
        ret_5d = np.zeros(n)
        ret_5d[120:] = close[120:] / close[:-120] - 1
    sharp_drop = ret_5d < -0.08

    # 2. RSI oversold on 1H
    rsi_oversold = rsi < 30

    # 3. Below lower BB (BB% < 0.15)
    bb_low = bb_pct < 0.15

    # 4. Volume spike: current volume > 2x average (capitulation)
    vol_spike = vol_ratio > 2.0

    # 5. Still above longer-term support: above EMA50
    above_support = close > ema50

    # 6. Not in crisis or downtrend
    regime_ok = (ctx.regime_1h != 0) & (ctx.regime_1h != 4)

    entry = sharp_drop & rsi_oversold & bb_low & vol_spike & above_support & regime_ok
    entry[:300] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=4.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=6,
        max_hold=336,
        edge=0.30,
        exit_regimes={CRISIS, DOWNTREND},
        rsi_exit_level=70,
        name='mean_reversion_filtered',
    )
