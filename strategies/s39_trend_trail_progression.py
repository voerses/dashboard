"""
s39 Trend Trail Progression — s09 + O5 Progressive Trailing Stop

Wraps s09_optimized_trend with progressive trailing stop tightening.
Same schedule as s37 (applied to s11): tighten trail as profit grows.

Does NOT modify s09. Calls s09.strategy(), then adds trail_schedule.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult
from strategies.s09_optimized_trend import strategy as s09_strategy

TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry: match s09 base trail_mult
    [1.0, 2.5],   # 1 ATR profit: start tightening
    [2.0, 2.0],   # 2 ATR profit: moderate
    [3.0, 1.5],   # 3+ ATR profit: lock in
], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s09 optimized trend with progressive trailing stop."""
    result = s09_strategy(ctx)

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
        name='s39_trend_trail_progression',
        max_trade_pct=getattr(result, 'max_trade_pct', 0.12),
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
