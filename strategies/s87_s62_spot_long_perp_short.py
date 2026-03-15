"""
s87 — s62 Conservative Funding Carry with Spot Longs / Perp Shorts
==================================================================
Class C Overlay: wraps s62 signal logic with per-bar venue routing.

Same conservative funding carry signal as s62, but routes trades to optimal venue:
  - LONG entries → SPOT (zero funding cost)
  - SHORT entries → PERP (collect funding when rates positive)

Base: s62_conservative_funding_carry_v4 (conservative carry, perp, 1x leverage)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, rolling_mean, rolling_std)


# Progressive trail schedule — same as s62
TRAIL_SCHEDULE = np.array([
    [0.0, 3.5],
    [1.0, 3.0],
    [2.0, 2.5],
    [3.0, 2.0],
], dtype=np.float64)

# Regime sizing — same as s62 (conservative)
REGIME_SIZE = np.array([0.0, 1.0, 0.75, 1.25, 0.75], dtype=np.float64)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """Conservative funding carry with spot longs / perp shorts."""
    n = len(ctx_perp.ind_1h['close'])

    funding = ctx_perp.funding_1h
    if funding is None:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.SPOT,
            name='s87_s62_spot_long_perp_short',
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    regime = ctx_perp.regime_1h
    regime_ok = regime != CRISIS

    # ── LAYER 2: FUNDING PERSISTENCE ────────────────────────────
    abs_funding = np.abs(funding)
    funding_ma = rolling_mean(abs_funding, 72)
    funding_threshold = 0.00005
    funding_elevated = funding_ma > funding_threshold

    # ── LAYER 3: DIRECTION (carry = opposite to funding) ────────
    funding_signed = rolling_mean(funding, 72)
    direction = np.where(funding_signed > 0, np.int8(-1), np.int8(1))

    # ── LAYER 4: LIQUIDITY ──────────────────────────────────────
    liquid = ctx_perp.liquidity_mask

    # ── LAYER 5: ADX CONFIDENCE ─────────────────────────────────
    adx = ctx_perp.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.3, np.where(adx > 20, 1.0, 0.7))

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = regime_ok & funding_elevated & liquid
    entry[:200] = False

    # ── SIZING ──────────────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]
    size_mult = size_mult * adx_scale
    size_mult = np.minimum(size_mult, 2.0)

    # ── PER-BAR VENUE ROUTING ───────────────────────────────────
    market_type = np.where(direction == 1, MarketType.SPOT, MarketType.PERP)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        stop_mult=4.0,
        trail_mult=3.5,
        target_mult=999,
        no_stop_bars=48,
        min_hold=24,
        max_hold=504,
        edge=0.30,

        exit_regimes={CRISIS},
        market_type=market_type,
        leverage=1.0,
        name='s87_s62_spot_long_perp_short',

        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=size_mult,
        cap_multiplier=2.0,
    )
