"""
s88 — s86 Funding Carry Spot/Perp + Time-Decayed Trail Overlay
==============================================================
Class C Overlay: wraps s86 (spot-long/perp-short carry) with time-based trail tightening.

Same relationship as s72→s65, but on top of the spot-long/perp-short variant:
  s65 (perp carry) → s72 (+ time trail)
  s86 (spot/perp carry) → s88 (+ time trail)

Time trail schedule (bars_held → trail_mult):
  48+ bars: 3.5 ATR  (after protection: begin tightening from 4.0 to 3.5)
  96+ bars: 3.0 ATR  (4 days: moderate)
 168+ bars: 2.5 ATR  (7 days: carry should have paid by now)
 240+ bars: 2.0 ATR  (10 days: aggressive — force resolution)

Base: s86_s65_spot_long_perp_short (spot longs / perp shorts carry)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s86_s65_spot_long_perp_short import strategy as base_strategy

# Time trail disabled — base trail_mult=1.5 post-ablation makes all schedule
# values (3.5, 3.0, 2.5, 2.0) > trail_mult, so min() was always trail_mult.
TIME_TRAIL_SCHEDULE = None


def strategy(ctx_spot, ctx_perp):
    """s86 + time-decayed trail overlay."""
    result = base_strategy(ctx_spot, ctx_perp)

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
        exchange=getattr(result, 'exchange', 'binance'),
        name='s88_s72_spot_long_perp_short',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        size_multiplier=result.size_multiplier,
        cap_multiplier=getattr(result, 'cap_multiplier', 4.0),
    )
