"""
s69 — s56 Signal-Enhanced Momentum + Time-Decayed Trail Overlay
================================================================
Class C Overlay: wraps s56 (Tier A) with time-based trail tightening.

Hypothesis: After no_stop_bars expires (bar 24), many s56 positions drift
sideways, accumulating funding costs until a belated trail stop. A
time-based trail that tightens independently of profit locks in gains
and cuts stale positions earlier.

Time trail schedule (bars_held → trail_mult):
  24+ bars: 2.5 ATR  (immediately after protection: begin tightening)
  30+ bars: 2.0 ATR  (1.25 days: moderate)
  36+ bars: 1.5 ATR  (1.5 days: aggressive)
  48+ bars: 1.0 ATR  (2 days: very tight — force resolution)

Combined with s56's profit-based trail via min(profit_trail, time_trail).

Base: s56_signal_enhanced_momentum (Tier A, perp, long-only momentum)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s56_signal_enhanced_momentum import strategy as base_strategy

TIME_TRAIL_SCHEDULE = None  # exit ablation: flat 1.5 ATR trail, schedule is dead no-op


def strategy(ctx):
    """s56 + time-decayed trail overlay."""
    result = base_strategy(ctx)

    # Return new StrategyResult with time_trail_schedule injected
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
        name='s69_s56_time_trail',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        size_multiplier=result.size_multiplier,
        max_trade_pct=result.max_trade_pct,
        cap_multiplier=result.cap_multiplier,
    )
