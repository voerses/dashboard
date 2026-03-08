"""
s55 Turbo Momentum Leveraged — s51 concentrated momentum with boosted sizing

Wraps s51 (3x leveraged perp momentum) with EXTREME regime sizing.
s51 gets 31.6% annual return with -13.8% MaxDD. By adding turbo sizing
in favorable regimes, we push returns toward 80-100%+ while accepting
~20-25% MaxDD (still within tolerance).

Sizing:
- CRISIS: 0.0 (zero — momentum fails in crisis)
- QUIET: 0.5 (low vol, momentum weak)
- UPTREND: 4.0 (maximum — momentum's home regime, 4x in s51's regime=2)
- RANGE: 2.0 (double normal)
- DOWNTREND: 0.0 (long-only momentum should NOT trade in downtrend)

ADX scaling: ADX>30 → 2.0x, ADX 20-30 → 1.0x, ADX<20 → 0.5x
Momentum magnitude: |ret_24h| > 5% → 1.5x boost
Cap: 8.0

Does NOT modify s51. Calls s51.strategy(), then overrides size_multiplier.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND

from strategies.s51_concentrated_momentum_leveraged import strategy as s51_strategy

# Regime → sizing multiplier (TURBO momentum)
REGIME_SIZE = np.array([0.0, 0.5, 4.0, 2.0, 0.0], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s51 concentrated momentum with TURBO regime sizing."""
    result = s51_strategy(ctx)

    n = len(ctx.ind_1h['close'])
    regime = ctx.regime_1h

    # Base regime multiplier
    size_mult = REGIME_SIZE[regime]

    # ADX confidence scaling (stronger scaling for turbo)
    adx = ctx.ind_1h['adx']
    adx_scale = np.where(adx > 30, 2.0, np.where(adx > 20, 1.0, 0.5))
    size_mult = size_mult * adx_scale

    # Momentum magnitude conviction boost
    ret_24h = ctx.custom.get('ret_24h')
    if ret_24h is not None:
        mom_boost = np.where(np.abs(ret_24h) > 0.05, 1.5, 1.0)
        size_mult = size_mult * mom_boost

    # Cap at 8.0
    size_mult = np.minimum(size_mult, 8.0)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        leverage=result.leverage,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s55_turbo_momentum_leveraged',
        trail_schedule=result.trail_schedule,
        size_multiplier=size_mult,
        cap_multiplier=4.0,  # 4x the ADV-based cap — balanced aggression
    )
