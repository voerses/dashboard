"""
s54 Turbo Carry — s44 basis carry with maximum aggressive sizing

Wraps s44 (basis carry + trail) with EXTREME size_multiplier to exploit
the strategy's Sharpe 5.27 and MaxDD -4.3%. With such low risk, we can
safely 4-5x the sizing to push toward 100%+ annual returns while keeping
drawdown under 20%.

Math: s49 (3x regime sizing) → 36% AnnRet, -4.3% MaxDD
      s54 (6x regime sizing) → target ~80-120% AnnRet, ~10-15% MaxDD

Sizing:
- CRISIS: 0.0 (zero allocation)
- QUIET: 3.0 (basis often wide in quiet markets — double s49)
- UPTREND: 6.0 (maximum carry opportunity — double s49)
- RANGE: 3.0 (moderate carry)
- DOWNTREND: 2.0 (basis can invert, still trade cautiously)

ADX scaling: same as s49 (1.5x for ADX>30, 1.0 for 20-30, 0.7 for <20)
Funding boost: When funding rate z-score > 1.5, add 1.5x boost
  (high funding = carry income supplements basis trade)
Cap: 10.0 (allow extreme sizing)

Does NOT modify s44. Calls s44.strategy(), then overrides size_multiplier.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS,
                    rolling_mean, rolling_std)

from strategies.s44_basis_carry_trail_progression import strategy as s44_strategy

# Regime → sizing multiplier (TURBO: 2x s49's values)
REGIME_SIZE = np.array([0.0, 3.0, 6.0, 3.0, 2.0], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """s44 basis carry trail with TURBO aggressive sizing."""
    result = s44_strategy(ctx_spot, ctx_perp)

    n = len(ctx_spot.ind_1h['close'])
    regime = ctx_spot.regime_1h

    # Base regime multiplier (2x s49's values)
    size_mult = REGIME_SIZE[regime]

    # ADX confidence scaling (using spot context)
    adx = ctx_spot.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.5, np.where(adx > 20, 1.0, 0.7))
    size_mult = size_mult * adx_scale

    # Funding rate boost: high funding = extra carry income
    funding = ctx_perp.funding_1h
    if funding is not None:
        fund_abs = np.abs(funding[:n])
        fund_ma = rolling_mean(fund_abs, 72)
        fund_std = rolling_std(fund_abs, 72)
        fund_std_safe = np.maximum(fund_std, 1e-10)
        fund_z = (fund_abs - fund_ma) / fund_std_safe
        funding_boost = np.where(fund_z > 1.5, 1.5, 1.0)
        size_mult = size_mult * funding_boost

    # Cap at 10.0 (allow extreme sizing)
    size_mult = np.minimum(size_mult, 10.0)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        secondary_entry_mask=result.secondary_entry_mask,
        secondary_direction=result.secondary_direction,
        secondary_market_type=result.secondary_market_type,
        secondary_leverage=result.secondary_leverage,
        capital_split=result.capital_split,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        secondary_stop_mult=result.secondary_stop_mult,
        secondary_trail_mult=result.secondary_trail_mult,
        secondary_target_mult=result.secondary_target_mult,
        secondary_no_stop_bars=result.secondary_no_stop_bars,
        secondary_min_hold=result.secondary_min_hold,
        secondary_max_hold=result.secondary_max_hold,
        secondary_edge=result.secondary_edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s54_turbo_carry',
        trail_schedule=result.trail_schedule,
        size_multiplier=size_mult,
        cap_multiplier=15.0,  # 15x the ADV-based cap — carry MaxDD is tiny, push harder
    )
