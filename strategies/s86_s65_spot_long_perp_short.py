"""
s86 — s65 Funding Carry with Spot Longs / Perp Shorts
======================================================
Class C Overlay: wraps s65 signal logic with per-bar venue routing.

Same funding carry signal as s65, but routes trades to the optimal venue:
  - LONG entries → SPOT (zero funding cost, own the asset)
  - SHORT entries → PERP (collect funding when rates are positive)

Economic mechanism: s65's longs pay ~0.4-0.7% of notional in funding drag.
By switching longs to spot, we eliminate this cost entirely. Shorts stay on
perp where they RECEIVE funding when rates are positive.

Signal source: ctx_perp (funding rates only available on perpetual markets)
Execution: spot for longs (spot prices), perp for shorts (perp prices)

Base: s65_funding_carry_v4 (funding carry, perp, 1x leverage)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS, rolling_mean


# Flat trail — exit ablation winner (trail_mult=1.5 across all strategies)
TRAIL_SCHEDULE = None

# Regime sizing — same as s65
REGIME_SIZE = np.array([0.0, 1.5, 1.0, 2.0, 1.5], dtype=np.float64)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """Funding carry with spot longs / perp shorts — eliminates funding drag on longs."""
    n = len(ctx_perp.ind_1h['close'])
    adx = ctx_perp.ind_1h['adx']
    regime = ctx_perp.regime_1h
    funding = ctx_perp.funding_1h

    # No funding data = no trades
    if funding is None:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.SPOT,
            name='s86_s65_spot_long_perp_short',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    regime_ok = regime != 0

    # ── LAYER 2: FUNDING PERSISTENCE & DIRECTION ─────────────────
    funding_signed = rolling_mean(funding, 72)
    funding_mag = np.abs(funding_signed)
    funding_elevated = funding_mag > 0.00005

    # ── LAYER 3: DIRECTION (carry = opposite to funding) ────────
    direction = np.where(funding_signed > 0, np.int8(-1), np.int8(1))

    # ── LAYER 4: LIQUIDITY ──────────────────────────────────────
    liquid = ctx_perp.liquidity_mask

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = regime_ok & funding_elevated & liquid
    entry[:200] = False

    # ── REGIME SIZING ───────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ── ADX CONFIDENCE SCALING ──────────────────────────────────
    adx_scale = np.where(adx > 35, 1.5, np.where(adx > 25, 1.2, 1.0))
    size_mult = size_mult * adx_scale

    # ── FUNDING MAGNITUDE SCALING ───────────────────────────────
    fund_scale = np.where(funding_mag > 0.0001, 1.5,
                 np.where(funding_mag > 0.00007, 1.2, 1.0))
    size_mult = size_mult * fund_scale
    size_mult = np.minimum(size_mult, 4.5)

    # ── PER-BAR VENUE ROUTING ───────────────────────────────────
    # Longs → SPOT (no funding), Shorts → PERP (collect funding)
    market_type = np.where(direction == 1, MarketType.SPOT, MarketType.PERP)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        stop_mult=4.5,
        trail_mult=1.5,       # 1.5x ATR flat trail (exit ablation winner)
        target_mult=999,
        no_stop_bars=48,
        min_hold=24,
        max_hold=336,
        edge=0.30,

        exit_regimes={CRISIS},

        name='s86_s65_spot_long_perp_short',

        market_type=market_type,
        leverage=1.0,
        exchange='binance',
        size_multiplier=size_mult,
        cap_multiplier=4.0,
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
