"""
s71 — s63 Vol Spike Reversal + Time-Decayed Trail Overlay
==========================================================
Class C Overlay: wraps s63 with time-based trail tightening.

s63 is counter-trend (mean reversion) with no_stop_bars=12, max_hold=168.
Reversion trades should resolve quickly — if a fade hasn't worked within
2-3 days, the original move was likely structural, not a spike.

Time trail schedule (bars_held → trail_mult):
  12+ bars: 3.0 ATR  (immediately after protection: begin tightening)
  24+ bars: 2.5 ATR  (1 day: moderate)
  36+ bars: 2.0 ATR  (1.5 days: aggressive — reversion should be done)
  72+ bars: 1.5 ATR  (3 days: very tight — force resolution)

Base: s63_vol_spike_reversal_v4 (counter-trend, perp, 1x leverage)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from strategies.s63_vol_spike_reversal_v4 import strategy as base_strategy

TIME_TRAIL_SCHEDULE = np.array([
    [12,  3.0],   # After no_stop_bars: start tightening (trail_mult=3.5, so 3.0 is tighter)
    [24,  2.5],   # 1 day: moderate
    [36,  2.0],   # 1.5 days: aggressive
    [72,  1.5],   # 3 days: very tight
], dtype=np.float64)


def strategy(ctx):
    """s63 + time-decayed trail overlay."""
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
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s71_s63_time_trail',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        size_multiplier=result.size_multiplier,
        cap_multiplier=getattr(result, 'cap_multiplier', 1.0),
    )
