"""
Strategy S65: Funding Rate Carry — V4 Portfolio Carry Component
================================================================
Class D (V4 Portfolio Strategy): gate path 0->2->V4-3->V4-4->V4-5->6->7

Hypothesis: Structural funding rate imbalance (retail long bias creates persistently
positive funding) can be harvested. Short when funding extreme positive, long when
extreme negative. Edge comes from carry income, not price prediction.

V4 rebuild of s29 (Tier B at 20.1% V3 rate). V4 portfolio context transforms weak
per-token strategies into useful portfolio components (proven: s28->s60 +3157%, s25->s63).

Key differentiator: CARRY — genuinely uncorrelated with s56 (momentum), s63 (counter-
trend). s29 had corr -0.16 vs s11, near-zero beta. Carry profits in trending AND ranging
markets because funding rate is driven by positioning imbalance, not price direction.

Enhancements over original s29:
  1. Progressive trailing stops (proven overlay from s37/s44)
  2. Regime-aware sizing (RANGE/QUIET boosted — funding more predictable in calm markets)
  3. ADX confidence scaling (directional strength = stronger positioning imbalance)
  4. Funding magnitude scaling (bigger imbalance = more carry income)
  5. Tighter entry: z-score based funding extremes (not just above threshold)
  6. 1x leverage + moderate sizing (conservative carry)

Market: PERP (bidirectional — short when longs pay, long when shorts pay)
Target regimes: ALL except CRISIS (carry works everywhere funding exists)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, rolling_mean)

# Exit ablation (v2/v3): flat 1.5 ATR trail + breakeven beats progressive schedule.
TRAIL_SCHEDULE = None

# Regime sizing — carry works in all non-crisis regimes
# Index:         CRISIS  QUIET  UPTREND  RANGE  DOWNTREND
REGIME_SIZE = np.array([0.0, 1.5, 1.0, 2.0, 1.5], dtype=np.float64)
# CRISIS: 0 (funding market breaks down)
# QUIET: 1.5 (most predictable funding — stable positioning)
# UPTREND: 1.0 (funding elevated but volatile)
# RANGE: 2.0 (best for carry — stable funding, mean-reverting price)
# DOWNTREND: 1.5 (funding can flip negative — profitable but riskier)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Funding rate carry — harvest structural funding payments on perp."""
    n = len(ctx.ind_1h['close'])
    adx = ctx.ind_1h['adx']
    regime = ctx.regime_1h
    funding = ctx.funding_1h

    # No funding data = no trades (spot context)
    if funding is None:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            name='s65_funding_carry_v4',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    # Carry works in all regimes except CRISIS
    regime_ok = regime != 0

    # ── LAYER 2: FUNDING PERSISTENCE & DIRECTION ─────────────────
    # Single rolling mean (signed) — captures both direction and magnitude
    # When funding is persistently positive, signed mean is positive (and vice versa)
    funding_signed = rolling_mean(funding, 72)
    funding_mag = np.abs(funding_signed)

    # Entry when funding is meaningfully in one direction
    # 0.00005/hr ≈ 0.0004/8hr ≈ 48% annualized — real carry opportunity
    funding_elevated = funding_mag > 0.00005

    # ── LAYER 3: DIRECTION (carry = opposite to funding) ────────
    # Short when funding positive (collect from longs), long when negative
    direction = np.where(funding_signed > 0, np.int8(-1), np.int8(1))

    # ── LAYER 4: LIQUIDITY ──────────────────────────────────────
    liquid = ctx.liquidity_mask

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = regime_ok & funding_elevated & liquid
    entry[:200] = False  # Warmup guard

    # ── REGIME SIZING ───────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ── ADX CONFIDENCE SCALING ──────────────────────────────────
    # Higher ADX = stronger directional positioning = more funding imbalance
    adx_scale = np.where(adx > 35, 1.5, np.where(adx > 25, 1.2, 1.0))
    size_mult = size_mult * adx_scale

    # ── FUNDING MAGNITUDE SCALING ───────────────────────────────
    # Bigger funding = more carry income = bigger position
    fund_scale = np.where(funding_mag > 0.0001, 1.5,
                 np.where(funding_mag > 0.00007, 1.2, 1.0))
    size_mult = size_mult * fund_scale

    # Cap at 4.5 (moderate for carry)
    size_mult = np.minimum(size_mult, 4.5)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Trade management — wider stops, longer holds for carry
        stop_mult=4.5,        # 4.5x ATR (wider — carry compensates for noise)
        trail_mult=1.5,       # 1.5x ATR flat trail (exit ablation winner)
        target_mult=999,      # Trail only (carry accumulates, no fixed target)
        no_stop_bars=48,      # 48h protection (need time for funding to accumulate)
        min_hold=24,          # Min 24h (at least 3 funding periods)
        max_hold=336,         # Max 14 days (avoid overstaying)
        edge=0.30,            # Conservative edge (carry is steady but lower per-trade)

        exit_regimes={CRISIS},

        name='s65_funding_carry_v4',

        # V4 perp settings
        market_type=MarketType.PERP,
        leverage=1.0,            # 1x leverage (carry is the edge, not leverage)
        exchange='binance',
        size_multiplier=size_mult,
        cap_multiplier=4.0,      # Moderate ADV cap
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
