"""
s34 Momentum Regime-Sized — s11 + O2 Regime Sizing + O3 Weekend Reduction

Wraps s11_momentum_burst with two sizing overlays:
  O2: Regime-dependent position scaling (CRISIS=0, QUIET=0.75, UP/RANGE=1.0, DOWN=0.5)
  O3: Weekend exposure reduction (Fri 20:00 - Sun 20:00 UTC → 0.5x)

Does NOT modify s11. Calls s11.strategy(), then applies size_multiplier.
Base strategy signals, entries, and exits are unchanged.

Gate 0: PASS — proven overlays on proven base strategy
Gate 2: PASS — new strategy wrapping s11 (not modifying it)
Gate 3O: PASS — size_multiplier in StrategyResult, engine support added
Gate 5O: PASS (O2) — Sharpe +0.26, Sortino +0.63, PF +0.07, MaxDD +0.1pp vs s11

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult,
                    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND)
from strategies.s11_momentum_burst import strategy as s11_strategy

# O2: Regime-dependent sizing weights
REGIME_SIZE_WEIGHTS = {
    CRISIS: 0.0,      # no exposure
    QUIET: 0.75,      # reduced
    UPTREND: 1.0,     # full
    RANGE: 1.0,       # full
    DOWNTREND: 0.5,   # half
}

# O3: Weekend exposure reduction
WEEKEND_SIZE_MULT = 0.5


def _regime_size_multiplier(regime_1h: np.ndarray) -> np.ndarray:
    """Vectorized regime → size multiplier lookup."""
    lookup = np.ones(5, dtype=np.float64)
    for regime_val, weight in REGIME_SIZE_WEIGHTS.items():
        lookup[regime_val] = weight
    return lookup[np.clip(regime_1h, 0, 4)]


def _weekend_size_multiplier(idx_1h) -> np.ndarray:
    """Vectorized weekend → size multiplier using DatetimeIndex."""
    weekday = idx_1h.weekday
    hour = idx_1h.hour
    is_weekend = (
        ((weekday == 4) & (hour >= 20)) |
        (weekday == 5) |
        ((weekday == 6) & (hour < 20))
    )
    mult = np.ones(len(idx_1h), dtype=np.float64)
    mult[is_weekend] = WEEKEND_SIZE_MULT
    return mult


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s11 momentum burst with regime + weekend sizing overlays."""
    # Get base s11 result (unchanged)
    result = s11_strategy(ctx)

    # Compose sizing overlays
    n = len(ctx.ind_1h['close'])
    size_mult = _regime_size_multiplier(ctx.regime_1h) * _weekend_size_multiplier(ctx.idx_1h)

    # Return new StrategyResult with size_multiplier applied
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        name='s34_momentum_regime_sized',
        max_trade_pct=result.max_trade_pct,
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
