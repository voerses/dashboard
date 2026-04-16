"""
s510 — s506 L/S Divergence + Regime Sizing Overlay
====================================================
Class C Overlay: wraps s506 (contrarian MR) with regime-dependent sizing.

Key insight: s506 works in RANGE regime (IC=-0.35) but weakens in sustained
UPTREND (IC=-0.139). The 2024 flat period was a sustained ETF-driven uptrend.
Regime sizing directly addresses this by scaling down in trending markets.

Regime weights (MR-optimized, inverted from momentum):
  CRISIS=0:    0.0  (no exposure — correlations spike, MR fails)
  QUIET=1:     0.5  (reduced — not enough vol for MR profits)
  UPTREND=2:   0.3  (aggressive reduction — MR weakest here)
  RANGE=3:     1.0  (full — MR strongest regime)
  DOWNTREND=4: 0.5  (moderate — MR works but volatile)

Proven pattern: s34 (momentum + regime sizing) passed Gate 5O with +0.26 Sharpe.
s506's regime dependency is even stronger → expected higher impact.

Base: s506_ls_divergence (contrarian MR, perp, 1x)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from strategies.s506_ls_divergence import strategy as base_strategy

# MR-optimized regime weights (0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND)
REGIME_WEIGHTS = np.array([0.0, 0.5, 0.3, 1.0, 0.5], dtype=np.float64)


def strategy(ctx):
    """s506 + regime sizing overlay."""
    result = base_strategy(ctx)

    # Vectorized regime -> size multiplier
    size_mult = REGIME_WEIGHTS[np.clip(ctx.regime_1h, 0, 4)]

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
        name='s510_ls_div_regime',
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
