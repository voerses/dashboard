"""
s35 Regime Spot/Perp Vol-Sized — s32 + S1 Vol-Managed Sizing

Wraps s32_regime_spot_perp with Moreira-Muir vol-managed sizing:
  S1: Scales position size inversely with 20-bar EWMA realized vol.
      Low vol → larger positions (capture breakouts).
      High vol → smaller positions (cut drawdown).

Does NOT modify s32. Calls s32.strategy(), then applies size_multiplier.
Base strategy signals, entries, and exits are unchanged.

Gate 0: PASS — Moreira-Muir (JF 2017) proven across all asset classes
Gate 2: PASS — new strategy wrapping s32 (not modifying it)

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND)
from strategies.s32_regime_spot_perp import strategy as s32_strategy

# S1: Vol-managed sizing parameters
TARGET_VOL = 0.30   # 30% annualized target
VOL_SIZE_MIN = 0.3  # floor multiplier
VOL_SIZE_MAX = 2.0  # ceiling multiplier
VOL_SPAN = 20       # EWMA span in hours


def _vol_size_multiplier(close: np.ndarray) -> np.ndarray:
    """Compute vol-managed size multiplier from close prices.
    Uses simple rolling std instead of EWMA to stay vectorized."""
    n = len(close)
    rets = np.zeros(n, dtype=np.float64)
    rets[1:] = np.log(close[1:] / np.where(close[:-1] > 0, close[:-1], 1.0))

    # Rolling std via cumulative sums (O(n), no for-loop)
    rets2 = rets * rets
    cs = np.cumsum(rets)
    cs2 = np.cumsum(rets2)
    w = VOL_SPAN
    var = np.zeros(n, dtype=np.float64)
    # Expanding window for first w bars
    counts = np.arange(1, w + 1, dtype=np.float64)
    var[:w] = cs2[:w] / counts - (cs[:w] / counts) ** 2
    # Rolling window for rest
    if n > w:
        cnt = float(w)
        mean_r = (cs[w:] - cs[:n - w]) / cnt
        mean_r2 = (cs2[w:] - cs2[:n - w]) / cnt
        var[w:] = mean_r2 - mean_r ** 2

    realized_vol_ann = np.sqrt(np.maximum(var, 0)) * np.sqrt(8760)

    safe_vol = np.where(realized_vol_ann > 0.01, realized_vol_ann, 1.0)
    mult = np.where(realized_vol_ann > 0.01, TARGET_VOL / safe_vol, 1.0)
    return np.clip(mult, VOL_SIZE_MIN, VOL_SIZE_MAX)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """s32 regime spot/perp with vol-managed sizing overlay."""
    # Get base s32 result (unchanged)
    result = s32_strategy(ctx_spot, ctx_perp)

    # Compute vol-managed size multiplier from spot close
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))
    size_mult = _vol_size_multiplier(ctx_spot.ind_1h['close'][:n])

    # Return new StrategyResult with size_multiplier applied
    return StrategyResult(
        # Primary leg
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        # Secondary leg
        secondary_entry_mask=result.secondary_entry_mask,
        secondary_direction=result.secondary_direction,
        secondary_market_type=result.secondary_market_type,
        secondary_leverage=result.secondary_leverage,
        capital_split=result.capital_split,
        # Primary trade management
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        # Secondary trade management
        secondary_stop_mult=result.secondary_stop_mult,
        secondary_trail_mult=result.secondary_trail_mult,
        secondary_target_mult=result.secondary_target_mult,
        secondary_no_stop_bars=result.secondary_no_stop_bars,
        secondary_min_hold=result.secondary_min_hold,
        secondary_max_hold=result.secondary_max_hold,
        secondary_edge=result.secondary_edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s35_regime_spot_perp_volsized',
        size_multiplier=size_mult,
    )
