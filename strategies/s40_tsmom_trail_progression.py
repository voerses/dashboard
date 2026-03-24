"""
s40 TSMOM Trail Progression — s13 + O5 Progressive Trailing Stop

Wraps s13_vol_weighted_tsmom with progressive trailing stop tightening.

Does NOT modify s13. Calls s13.strategy(), then adds trail_schedule.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult
from strategies.s13_vol_weighted_tsmom import strategy as s13_strategy

TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry: match s13 base trail_mult
    [1.0, 2.5],
    [2.0, 2.0],
    [3.0, 1.5],
], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s13 vol-weighted TSMOM with progressive trailing stop."""
    result = s13_strategy(ctx)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        name='s40_tsmom_trail_progression',
        max_trade_pct=getattr(result, 'max_trade_pct', 0.12),
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
