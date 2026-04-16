"""
s511 — s506 L/S Divergence + Full Overlay Stack
=================================================
Class C Overlay: wraps s506 with ALL proven overlays combined:
  1. Regime sizing (MR-optimized: RANGE=1.0, UPTREND=0.3)
  2. Fixed take-profit at 3 ATR (locks MR profits)
  3. Time-decayed trail (forces stale position resolution)

Combines the three highest-impact overlays for contrarian mean-reversion:
- Regime sizing addresses 2024 UPTREND weakness (s34 proven, Gate 5O)
- Fixed TP locks MR profits before trend resumes (s75 proven, Gate 5O)
- Time trail cuts stale losers before max_hold (s72 proven, Gate 5)

Base: s506_ls_divergence (contrarian MR, perp, 1x)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from strategies.s506_ls_divergence import strategy as base_strategy

# Overlay 1: Regime sizing (MR-optimized)
REGIME_WEIGHTS = np.array([0.0, 0.5, 0.3, 1.0, 0.5], dtype=np.float64)

# Overlay 2: Fixed take-profit
TARGET_MULT = 3.0

# Overlay 3: Time-decayed trail
TIME_TRAIL_SCHEDULE = np.array([
    [72,  1.8],   # 3 days: begin tightening from base 2.0
    [168, 1.5],   # 7 days: moderate
    [264, 1.2],   # 11 days: aggressive
], dtype=np.float64)


def strategy(ctx):
    """s506 + full overlay stack (regime + TP + time trail)."""
    result = base_strategy(ctx)

    # Regime sizing
    size_mult = REGIME_WEIGHTS[np.clip(ctx.regime_1h, 0, 4)]

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
        name='s511_ls_div_full_stack',
        size_multiplier=size_mult,
        time_trail_schedule=TIME_TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
