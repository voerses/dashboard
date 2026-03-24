"""
s49 Aggressive Carry — s44 basis carry trail + adaptive regime-scaled sizing

Wraps s44 (basis carry + trail progression) with aggressive size_multiplier:
- CRISIS: 0.0 (zero allocation)
- QUIET: 1.5 (basis often wide in quiet markets)
- UPTREND: 3.0 (basis widens as sentiment drives perp premium)
- RANGE: 1.5 (moderate carry opportunities)
- DOWNTREND: 1.0 (basis can invert, be cautious)

Also scales by ADX (trend strength) as a confidence proxy:
- ADX > 30: multiply by 1.5 (strong trend = wider basis)
- ADX 20-30: multiply by 1.0 (normal)
- ADX < 20: multiply by 0.7 (weak trend = narrow basis)

s44 is delta-neutral (long spot + short perp), so regime scaling
affects SIZE not DIRECTION. Bigger size in favorable carry regimes.

Target: 100%+ annual return by aggressively sizing a Sharpe 5.80 strategy.

Does NOT modify s44. Calls s44.strategy(), then overrides size_multiplier.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, QUIET, UPTREND, DOWNTREND

from strategies.s44_basis_carry_trail_progression import strategy as s44_strategy

# Regime → sizing multiplier (for delta-neutral carry)
REGIME_SIZE = np.array([0.0, 1.5, 3.0, 1.5, 1.0], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """s44 basis carry trail with aggressive regime-scaled sizing."""
    result = s44_strategy(ctx_spot, ctx_perp)

    n = len(ctx_spot.ind_1h['close'])
    regime = ctx_spot.regime_1h

    # Base regime multiplier
    size_mult = REGIME_SIZE[regime]

    # ADX confidence scaling (using spot context)
    adx = ctx_spot.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.5, np.where(adx > 20, 1.0, 0.7))
    size_mult = size_mult * adx_scale

    # Cap at 5.0 to avoid extreme sizing
    size_mult = np.minimum(size_mult, 5.0)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        secondary_entry_mask=result.secondary_entry_mask,
        secondary_direction=result.secondary_direction,
        secondary_market_type=result.secondary_market_type,
        secondary_leverage=result.secondary_leverage,
        capital_split=result.capital_split,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        secondary_stop_mult=result.secondary_stop_mult,
        secondary_trail_mult=result.secondary_trail_mult,
        secondary_target_mult=result.secondary_target_mult,
        secondary_no_stop_bars=result.secondary_no_stop_bars,
        secondary_min_hold=result.secondary_min_hold,
        secondary_max_hold=result.secondary_max_hold,
        secondary_edge=result.secondary_edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s49_aggressive_carry',
        trail_schedule=result.trail_schedule,
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
