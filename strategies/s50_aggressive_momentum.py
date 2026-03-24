"""
s50 Aggressive Momentum — s37 momentum trail + adaptive regime-scaled sizing

Wraps s37 (momentum burst + trail progression) with aggressive size_multiplier:
- CRISIS: 0.0 (zero allocation — momentum fails in crisis)
- QUIET: 0.5 (low vol, momentum signals weak)
- UPTREND: 3.0 (max leverage — momentum's home regime)
- RANGE: 1.0 (normal)
- DOWNTREND: 0.0 (long-only momentum should NOT trade in downtrend)

Also scales by:
- ADX confidence: ADX > 30 → 1.5x, ADX 20-30 → 1.0x, ADX < 20 → 0.5x
- Momentum magnitude: |ret_24h| > 5% → 1.3x conviction boost

Target: 80%+ annual return from momentum in favorable regimes.

Does NOT modify s37. Calls s37.strategy(), then overrides size_multiplier.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, QUIET, UPTREND, DOWNTREND

from strategies.s37_momentum_trail_progression import strategy as s37_strategy

# Regime → sizing multiplier (directional long-only momentum)
REGIME_SIZE = np.array([0.0, 0.5, 3.0, 1.0, 0.0], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s37 momentum trail with aggressive regime-scaled sizing."""
    result = s37_strategy(ctx)

    n = len(ctx.ind_1h['close'])
    regime = ctx.regime_1h

    # Base regime multiplier
    size_mult = REGIME_SIZE[regime]

    # ADX confidence scaling
    adx = ctx.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.5, np.where(adx > 20, 1.0, 0.5))
    size_mult = size_mult * adx_scale

    # Momentum magnitude conviction boost
    ret_24h = ctx.custom.get('ret_24h')
    if ret_24h is not None:
        mom_boost = np.where(np.abs(ret_24h) > 0.05, 1.3, 1.0)
        size_mult = size_mult * mom_boost

    # Cap at 5.0
    size_mult = np.minimum(size_mult, 5.0)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s50_aggressive_momentum',
        trail_schedule=result.trail_schedule,
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
