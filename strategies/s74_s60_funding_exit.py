"""
s74 — s60 Momentum Burst Perp + Funding-Aware Exit Overlay
============================================================
Class C Overlay: wraps s60 (momentum burst perp) with cumulative funding
cost ceiling.

Same mechanism as s73 but on s60 base, which has second-highest observed
funding drag in paper trading (-$951 funding on $2,089 realized = 46%).

Base: s60_momentum_burst_perp_v4 (perp, bidirectional momentum)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s60_momentum_burst_perp_v4 import strategy as base_strategy

FUNDING_EXIT_THRESHOLD = 0.005


def strategy(ctx):
    """s60 + funding-aware exit overlay."""
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
        name='s74_s60_funding_exit',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        size_multiplier=result.size_multiplier,
        max_trade_pct=result.max_trade_pct,
        cap_multiplier=getattr(result, 'cap_multiplier', 1.0),
        funding_exit_threshold=FUNDING_EXIT_THRESHOLD,
        breakeven_atr=0.5,
    )
