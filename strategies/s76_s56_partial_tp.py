"""
s76 — s56 Signal-Enhanced Momentum + Partial Profit-Taking Overlay
===================================================================
Class C Overlay: wraps s56 (Tier A) with partial profit-taking.

Hypothesis: Taking 50% profit at 2x ATR while letting the remainder
ride with a tighter trail (1.5x ATR) improves Sortino by reducing
variance of winner outcomes, because locking in partial gains on
strong moves stabilizes the equity curve.

Mechanism: When unrealized profit reaches partial_tp_atr * ATR:
  1. Close partial_tp_pct (50%) of the position, booking guaranteed profit
  2. Tighten trail to partial_tp_trail (1.5x ATR) on the remainder
  3. Remainder continues to ride with tighter stop

This converts uncertain large winners into certain moderate profit +
potential large profit, reducing equity curve variance.

Base: s56_signal_enhanced_momentum (Tier A, perp, long-only momentum)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s56_signal_enhanced_momentum import strategy as base_strategy

# Partial profit-taking parameters
PARTIAL_TP_ATR = 2.0     # Take partial profit at 2x ATR
PARTIAL_TP_PCT = 0.5     # Close 50% of position
PARTIAL_TP_TRAIL = 1.5   # Tighten trail to 1.5x ATR on remainder


def strategy(ctx):
    """s56 + partial profit-taking overlay."""
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
        name='s76_s56_partial_tp',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        size_multiplier=result.size_multiplier,
        max_trade_pct=result.max_trade_pct,
        cap_multiplier=getattr(result, 'cap_multiplier', 1.0),
        funding_exit_threshold=getattr(result, 'funding_exit_threshold', 0.0),
        # Partial profit-taking overlay
        partial_tp_atr=PARTIAL_TP_ATR,
        partial_tp_pct=PARTIAL_TP_PCT,
        partial_tp_trail=PARTIAL_TP_TRAIL,
    )
