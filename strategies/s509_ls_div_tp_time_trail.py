"""
s509 — s506 L/S Divergence + Fixed TP + Time Trail (Combined)
==============================================================
Class C Overlay: wraps s506 with BOTH fixed take-profit and time trail.

Combines the two highest-fit overlays for contrarian mean-reversion:
1. Fixed TP at 3 ATR — locks in MR profits before trend resumes
2. Time trail — forces stale losers to resolve before max_hold

Both are proven independently (s75 Gate 5O, s72 Gate 5). Testing additivity.

Base: s506_ls_divergence (contrarian MR, perp, 1x)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from strategies.s506_ls_divergence import strategy as base_strategy

TARGET_MULT = 3.0

TIME_TRAIL_SCHEDULE = np.array([
    [72,  1.8],   # 3 days: begin tightening from base 2.0
    [168, 1.5],   # 7 days: moderate
    [264, 1.2],   # 11 days: aggressive — force resolution
], dtype=np.float64)


def strategy(ctx):
    """s506 + fixed TP + time trail combined overlay."""
    result = base_strategy(ctx)

    from engine import StrategyResult
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        leverage=result.leverage,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=TARGET_MULT,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        name='s509_ls_div_tp_time_trail',
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
