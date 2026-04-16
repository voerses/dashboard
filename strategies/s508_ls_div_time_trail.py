"""
s508 — s506 L/S Divergence + Time-Decayed Trail Overlay
========================================================
Class C Overlay: wraps s506 with time-based trail tightening.

s506 is contrarian MR with max_hold=336 (14 days), no_stop_bars=24.
Positions that haven't resolved after 7+ days are likely in wrong regime.
Time trail forces resolution by tightening trail as hold lengthens.

Proven: s72 (s65 + time trail) passed Gate 5 with +148% Calmar. s506 has
even longer holds (336h vs s65's 336h) — excellent fit.

Time trail schedule (bars_held → trail_mult):
  72+ bars (3d): 1.8 ATR  (begin tightening from 2.0 base)
 168+ bars (7d): 1.5 ATR  (MR should have played out by now)
 264+ bars (11d): 1.2 ATR (aggressive — force resolution before max_hold)

Base: s506_ls_divergence (contrarian MR, perp, 1x)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from strategies.s506_ls_divergence import strategy as base_strategy

TIME_TRAIL_SCHEDULE = np.array([
    [72,  1.8],   # 3 days: begin tightening from base 2.0
    [168, 1.5],   # 7 days: moderate
    [264, 1.2],   # 11 days: aggressive — force resolution
], dtype=np.float64)


def strategy(ctx):
    """s506 + time-decayed trail overlay."""
    result = base_strategy(ctx)

    from engine import StrategyResult
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
        name='s508_ls_div_time_trail',
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
