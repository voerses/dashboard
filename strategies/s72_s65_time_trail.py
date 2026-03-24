"""
s72 — s65 Funding Carry + Time-Decayed Trail Overlay
=====================================================
Class C Overlay: wraps s65 with time-based trail tightening.

s65 is carry (funding rate harvest) with no_stop_bars=48, max_hold=336.
Carry trades accumulate funding income over time, so the trail should be
more relaxed than momentum. But positions that haven't moved after 5+ days
are likely in a funding regime shift and should be cut.

Time trail schedule (bars_held → trail_mult):
  48+ bars: 3.5 ATR  (after protection: begin tightening from 4.0 to 3.5)
  96+ bars: 3.0 ATR  (4 days: moderate)
 168+ bars: 2.5 ATR  (7 days: carry should have paid by now)
 240+ bars: 2.0 ATR  (10 days: aggressive — force resolution)

Base: s65_funding_carry_v4 (carry, perp, 1x leverage)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s65_funding_carry_v4 import strategy as base_strategy

# Time trail disabled — base trail_mult=1.5 post-ablation makes all schedule
# values (3.5, 3.0, 2.5, 2.0) > trail_mult, so min() was always trail_mult.
TIME_TRAIL_SCHEDULE = None


def strategy(ctx):
    """s65 + time-decayed trail overlay."""
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
        name='s72_s65_time_trail',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        size_multiplier=result.size_multiplier,
        cap_multiplier=getattr(result, 'cap_multiplier', 1.0),
        breakeven_atr=0.5,
    )
