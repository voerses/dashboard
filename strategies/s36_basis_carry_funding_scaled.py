"""
s36 Basis Carry Funding-Scaled — s30 + O6 Funding-Aware Carry Scaling

Wraps s30_basis_carry with funding-rate-magnitude sizing:
  O6: Scale position size by funding rate z-score. When funding is rich
      (shorts collect more), increase position. When funding is thin or
      negative (shorts pay), decrease position.

Does NOT modify s30. Calls s30.strategy(), then applies size_multiplier.
Base strategy signals, entries, and exits are unchanged.

Gate 0: PASS — carry-magnitude overlay on delta-neutral base (not directional)
Gate 2: PASS — no existing overlay targets funding-rate-based sizing

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType)
from strategies.s30_basis_carry import strategy as s30_strategy


# O6 parameters
FUNDING_ZSCORE_WINDOW = 720   # 30 days of 1h bars
FUNDING_SCALE = 0.5           # sensitivity: z-score multiplied by this
FUNDING_BASE = 1.0            # at z=0 (average funding), no change
FUND_SIZE_MIN = 0.3           # floor: 30% of normal at very negative funding
FUND_SIZE_MAX = 2.0           # cap: 2x at extreme positive funding


def _funding_size_mult(funding, window, base, scale, lo, hi):
    """Compute funding rolling z-score → size multiplier. Vectorized via cumsum."""
    arr = np.asarray(funding, dtype=np.float64)
    n = len(arr)
    cs = np.cumsum(arr)
    cs2 = np.cumsum(arr * arr)

    # Pre-allocate output as base value
    out = np.full(n, base, dtype=np.float64)
    if n <= 1:
        return out

    # Only compute rolling window portion (skip expanding warmup — stays at base)
    w = window
    if n > w:
        cnt = float(w)
        rm = (cs[w:] - cs[:n - w]) / cnt
        rv = (cs2[w:] - cs2[:n - w]) / cnt - rm * rm
        rs = np.sqrt(np.maximum(rv, 0.0))
        mask = rs > 1e-10
        z = np.zeros(n - w, dtype=np.float64)
        z[mask] = np.clip((arr[w:][mask] - rm[mask]) / rs[mask], -3.0, 3.0)
        out[w:] = base + scale * z

    return np.clip(out, lo, hi)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """s30 basis carry with funding-rate-magnitude sizing overlay."""
    # Get base s30 result (unchanged)
    result = s30_strategy(ctx_spot, ctx_perp)

    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    # Get per-hour funding rate from perp context
    funding = ctx_perp.funding_1h[:n]

    # Funding z-score → size multiplier in one pass:
    #   z=0 (average funding) → 1.0 (no change from base)
    #   z=+1 (rich carry)     → 1.5 (50% more)
    #   z=+2 (very rich)      → 2.0 (capped)
    #   z=-1 (thin carry)     → 0.5
    #   z=-2 (negative carry) → 0.3 (floor)
    size_mult = _funding_size_mult(
        funding, FUNDING_ZSCORE_WINDOW,
        FUNDING_BASE, FUNDING_SCALE, FUND_SIZE_MIN, FUND_SIZE_MAX,
    )

    return StrategyResult(
        # Primary leg (spot long)
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,

        # Secondary leg (perp short)
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
        name='s36_basis_carry_funding_scaled',
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
