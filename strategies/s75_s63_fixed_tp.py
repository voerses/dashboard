"""
s75 — s63 Vol Spike Reversal + Fixed Take-Profit Overlay
==========================================================
Class C Overlay: wraps s63 (counter-trend) with a fixed take-profit target.

Hypothesis: Counter-trend (mean reversion) trades have a natural profit cap —
price reverts to the mean but rarely overshoots the other way. Trail-only exits
(target_mult=999) risk giving back all gains when the original trend resumes.
A fixed TP at N*ATR locks in strong reversions before they reverse back.

Paper trading evidence (F34): s63 at 0% win rate (2 stopped losses at 12-18 bars).
The trail never locks gains because reversions are too fast and shallow for
progressive trail tightening to catch.

Base: s63_vol_spike_reversal_v4 (perp, counter-trend, bidirectional)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s63_vol_spike_reversal_v4 import strategy as base_strategy

# Fixed take-profit target in ATR units.
# s63 has stop_mult=4.0, so risk:reward = 1:0.75 at target=3
# Gate 5O sweep: 3.0 beat 2.0/2.5/3.5/4.0/5.0/7.0/10.0/999 on Calmar and return.
# 264 trades (11.8%) exit at TP, locking in mean-reversion profit.
TARGET_MULT = 3.0


def strategy(ctx):
    """s63 + fixed take-profit overlay."""
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
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s75_s63_fixed_tp',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        size_multiplier=result.size_multiplier,
        cap_multiplier=getattr(result, 'cap_multiplier', 1.0),
    )
