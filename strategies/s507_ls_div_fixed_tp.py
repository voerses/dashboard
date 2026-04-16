"""
s507 — s506 L/S Divergence + Fixed Take-Profit Overlay
=======================================================
Class C Overlay: wraps s506 (contrarian mean-reversion) with fixed TP.

Hypothesis: MR trades have a natural profit cap — price reverts to mean but
rarely overshoots. s506 has target_mult=999 (no TP), so trail-only exits risk
giving back gains when trend resumes. Fixed TP at 3 ATR locks in reversions.

Proven: s75 (s63 + fixed TP) passed Gate 5O with +17.6% Calmar on MR base.
s506 is also MR → high-fit overlay.

Base: s506_ls_divergence (contrarian MR, perp, 1x)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

from strategies.s506_ls_divergence import strategy as base_strategy

# Fixed TP at 3 ATR — proven optimal for MR strategies in s75 Gate 5O sweep.
# s506 has stop_mult=2.5, so risk:reward = 1:1.2
TARGET_MULT = 3.0


def strategy(ctx):
    """s506 + fixed take-profit overlay."""
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
        name='s507_ls_div_fixed_tp',
        breakeven_atr=0.5,
    )
