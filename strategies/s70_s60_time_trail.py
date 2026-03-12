"""
s70 — s60 Momentum Burst Perp + Time-Decayed Trail Overlay
============================================================
Class C Overlay: wraps s60 (Tier A/B) with time-based trail tightening.

Hypothesis: s60's no_stop_bars=24 means 80% of exits cluster at exactly
bar 24 when trails activate. Funding costs accumulate during the 24h
protection window. A time-based trail that starts tightening after 36h
(post-protection) forces earlier exit of stale positions and reduces
funding drag.

Time trail schedule (bars_held → trail_mult):
  24+ bars: 2.5 ATR  (immediately after protection: begin tightening)
  36+ bars: 2.0 ATR  (1.5 days: moderate)
  48+ bars: 1.5 ATR  (2 days: aggressive)
  72+ bars: 1.0 ATR  (3 days: very tight — force resolution)

s60 has max_hold=720 (30 days), so time trail gives progressively
tighter stops well before max_hold forces exit.

Combined with s60's profit-based trail via min(profit_trail, time_trail).

Base: s60_momentum_burst_perp_v4 (bidirectional perp, 1x leverage)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from strategies.s60_momentum_burst_perp_v4 import strategy as base_strategy

# Time-based trail schedule: [bars_held_threshold, trail_mult]
# Aggressive: s60 exits cluster near bar 24 (no_stop_bars), so
# tightening must begin immediately after protection expires.
TIME_TRAIL_SCHEDULE = np.array([
    [24,  2.5],   # Immediately after protection: begin tightening (was 3.0)
    [36,  2.0],   # 1.5 days: moderate
    [48,  1.5],   # 2 days: aggressive
    [72,  1.0],   # 3 days: very tight — force resolution
], dtype=np.float64)


def strategy(ctx):
    """s60 + time-decayed trail overlay."""
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
        name='s70_s60_time_trail',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        size_multiplier=result.size_multiplier,
        cap_multiplier=getattr(result, 'cap_multiplier', 1.0),
    )
