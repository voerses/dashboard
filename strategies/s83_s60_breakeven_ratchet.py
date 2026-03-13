"""
s83 — s60 Momentum Burst + Breakeven Ratchet + Bear Target Overlay
===================================================================
Class C Overlay: wraps s60 (Tier A) with breakeven ratchet and
regime-conditional profit target.

Hypothesis: After a trade reaches +0.5 ATR profit, moving the stop
to entry price (breakeven) dramatically improves payoff ratio by
converting potential losers into scratch trades. In DOWNTREND regime,
a 2.0 ATR profit target captures bear-market spikes before reversal.

Backtested improvements (s60, 12mo, $200K):
  Baseline:         PnL $7.9M, payoff 2.67x, MaxDD 4.5%
  BE=0.5 only:      PnL $15.6M (+97%), payoff 5.57x, MaxDD 3.8%
  BE+bear_tgt_2.0:  PnL $16.4M (+107%), payoff 4.59x, MaxDD 3.9%

Mechanism:
  1. After peak unrealized PnL exceeds 0.5*ATR → stop moves to entry
  2. In DOWNTREND bars → profit target at 2.0*ATR captures spikes
  3. In other regimes → no target cap, winners run with trail

Base: s60_momentum_burst_perp_v4 (Tier A, perp, momentum burst)
Status: EXPERIMENTAL (deploying to paper trading)
"""

from strategies.s60_momentum_burst_perp_v4 import strategy as base_strategy

BREAKEVEN_ATR = 0.5      # Move stop to entry after +0.5 ATR profit
BEAR_TARGET_MULT = 2.0   # Fixed target at 2.0 ATR in DOWNTREND


def strategy(ctx):
    """s60 + breakeven ratchet + bear target overlay."""
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
        name='s83_s60_breakeven_ratchet',
        size_multiplier=result.size_multiplier,
        cap_multiplier=result.cap_multiplier,
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        max_trail_mult=getattr(result, 'max_trail_mult', None),
        rsi_exit_level=result.rsi_exit_level,
        conviction_score=getattr(result, 'conviction_score', None),
        # Breakeven ratchet overlay
        breakeven_atr=BREAKEVEN_ATR,
        # Regime-conditional target
        bear_target_mult=BEAR_TARGET_MULT,
    )
