"""
s37 Momentum Trail Progression — s11 + O5 Progressive Trailing Stop

Wraps s11_momentum_burst with progressive trailing stop tightening:
  O5: Tighten trail_mult as unrealized profit grows (in ATR units).
      Entry: trail=3.0 ATR (room to develop), 1 ATR profit: 2.5,
      2 ATR profit: 2.0, 3+ ATR: 1.5 (lock in big winners).

Does NOT modify s11. Calls s11.strategy(), then adds trail_schedule.
Base strategy signals, entries, and exits are unchanged (except trail behavior).

Gate 0: PASS — standard trend-following practice, solid mechanism
Gate 2: PASS — no existing overlay targets trailing stop progression
Gate 3O: PASS — uses trail_schedule field in StrategyResult

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult
from strategies.s11_momentum_burst import strategy as s11_strategy

# O5: Progressive trail schedule — (profit_in_ATR, trail_mult)
# Sorted ascending by threshold. Linear scan in JIT.
TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry stage: wide trail (match s11 base)
    [1.0, 2.5],   # 1 ATR profit: start tightening
    [2.0, 2.0],   # 2 ATR profit: moderate trail
    [3.0, 1.5],   # 3+ ATR profit: lock in winners
], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s11 momentum burst with progressive trailing stop tightening."""
    # Get base s11 result (unchanged)
    result = s11_strategy(ctx)

    # Return new StrategyResult with trail_schedule applied
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
        name='s37_momentum_trail_progression',
        max_trade_pct=result.max_trade_pct,
        trail_schedule=TRAIL_SCHEDULE,
    )
