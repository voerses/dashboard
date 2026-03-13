"""
s84 — s56 Signal-Enhanced Momentum + Breakeven Ratchet Overlay
===============================================================
Class C Overlay: wraps s56 (Tier A) with breakeven ratchet.

Hypothesis: After a trade reaches +0.5 ATR profit, moving the stop
to entry price (breakeven) improves payoff ratio by converting
potential losers into scratch trades.

Backtested improvements (s56, 12mo, $200K):
  Baseline:    PnL $755K, payoff 3.22x, MaxDD 3.7%
  BE=0.5:      PnL $1.15M (+52%), payoff 7.25x, MaxDD 2.5%

Note: bear_target_mult has <1% effect on s56 because it doesn't
trade in DOWNTREND (REGIME_SIZE[DOWNTREND]=0). Only breakeven used.

Base: s56_signal_enhanced_momentum (Tier A, perp, momentum)
Status: EXPERIMENTAL (deploying to paper trading)
"""

from strategies.s56_signal_enhanced_momentum import strategy as base_strategy

BREAKEVEN_ATR = 0.5


def strategy(ctx):
    """s56 + breakeven ratchet overlay."""
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
        name='s84_s56_breakeven_ratchet',
        size_multiplier=result.size_multiplier,
        cap_multiplier=result.cap_multiplier,
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        max_trail_mult=getattr(result, 'max_trail_mult', None),
        rsi_exit_level=result.rsi_exit_level,
        conviction_score=getattr(result, 'conviction_score', None),
        # Breakeven ratchet overlay
        breakeven_atr=BREAKEVEN_ATR,
    )
