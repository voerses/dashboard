"""
s73 — s56 Signal-Enhanced Momentum + Funding-Aware Exit Overlay
================================================================
Class C Overlay: wraps s56 (Tier A) with cumulative funding cost ceiling.

Hypothesis: Perp positions where cumulative funding exceeds expected edge
are negative-EV to hold. By forcing exit when funding drag exceeds a
threshold (% of margin), we cut losers that bleed via funding before the
price-based trailing stop fires.

Paper trading evidence (F31): funding costs consume 67% of gross PnL.
PIPPIN paid $1,071 funding across 3 trades for only $389 net.

Mechanism: If cumulative_funding / margin_usd > threshold, force exit.
threshold=0.005 means exit when funding drag exceeds 0.5% of margin.

Base: s56_signal_enhanced_momentum (Tier A, perp, long+short momentum)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s56_signal_enhanced_momentum import strategy as base_strategy

# Funding exit threshold: exit if cumulative funding cost exceeds this
# fraction of margin_usd. 0.005 = 0.5% of margin.
# At typical 0.01%/8h funding rate on 1x leverage, this triggers after
# ~400 hours (~17 days) of continuous adverse funding — catches stuck
# positions bleeding funding well before max_hold (720h).
FUNDING_EXIT_THRESHOLD = 0.005


def strategy(ctx):
    """s56 + funding-aware exit overlay."""
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
        name='s73_s56_funding_exit',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        size_multiplier=result.size_multiplier,
        max_trade_pct=result.max_trade_pct,
        cap_multiplier=result.cap_multiplier,
        funding_exit_threshold=FUNDING_EXIT_THRESHOLD,
    )
