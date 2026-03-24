"""
s42 Momentum Defensive Trail — s11 + O5 Progressive Trail + O4 Defensive Stop

Wraps s11_momentum_burst with both progressive trailing stop (O5) and
volatility-adaptive trail ceiling (O4).

O5: Trail tightens as profit grows (trail_schedule)
O4: Trail ceiling tightens when previous bar's range is extreme (max_trail_mult)

Uses previous bar's range (lagged by 1) to avoid look-ahead bias.
When prev bar range > RANGE_THRESHOLD * ATR, cap trail at DEFENSIVE_MULT.

Does NOT modify s11. Calls s11.strategy(), then adds both overlays.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult
from strategies.s11_momentum_burst import strategy as s11_strategy

# O5: Progressive trailing stop schedule
TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry: match s11 base trail_mult
    [1.0, 2.5],   # 1 ATR profit: start tightening
    [2.0, 2.0],   # 2 ATR profit: moderate
    [3.0, 1.5],   # 3+ ATR profit: lock in
], dtype=np.float64)

# O4: Defensive stop parameters
RANGE_THRESHOLD = 2.5   # Previous bar range > 2.5x ATR triggers defensive mode
DEFENSIVE_MULT = 1.5    # Trail ceiling during defensive mode
NORMAL_CEIL = 999.0     # No ceiling on normal bars


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s11 momentum burst with progressive trail (O5) + defensive stop (O4)."""
    result = s11_strategy(ctx)

    # Compute per-bar max trail multiplier from previous bar's range
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    atr = ctx.ind_1h['atr']
    n = len(high)

    # Previous bar's range normalized by ATR (lagged by 1 — no look-ahead)
    bar_range = high - low
    prev_atr = np.maximum(atr, 1e-10)
    prev_range_ratio = np.empty(n, dtype=np.float64)
    prev_range_ratio[0] = 0.0
    prev_range_ratio[1:] = bar_range[:-1] / prev_atr[:-1]

    # Cap trail on bars following extreme-range bars
    max_trail = np.where(prev_range_ratio > RANGE_THRESHOLD, DEFENSIVE_MULT, NORMAL_CEIL)

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
        name='s42_momentum_defensive_trail',
        max_trade_pct=getattr(result, 'max_trade_pct', 0.12),
        trail_schedule=TRAIL_SCHEDULE,
        max_trail_mult=max_trail,
        breakeven_atr=0.5,
    )
