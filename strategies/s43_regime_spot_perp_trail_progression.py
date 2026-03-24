"""
s43 Regime Spot-Perp Trail Progression — s32 + O5 Progressive Trailing Stop

Wraps s32_regime_spot_perp with progressive trailing stop tightening.

s32 is directional (long spot in uptrend, short perp in downtrend),
so trail progression should help lock in profits on both legs.

Trail schedule shared across both legs (combined JIT supports this).
Schedule starts at 3.0 matching s32's primary stop_mult.

Does NOT modify s32. Calls s32.strategy(), then adds trail_schedule.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult
from strategies.s32_regime_spot_perp import strategy as s32_strategy

TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry: match s32 primary trail_mult (2.5 for secondary)
    [1.0, 2.5],
    [2.0, 2.0],
    [3.0, 1.5],
], dtype=np.float64)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """s32 regime spot/perp with progressive trailing stop."""
    result = s32_strategy(ctx_spot, ctx_perp)

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
        name='s43_regime_spot_perp_trail_progression',
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
