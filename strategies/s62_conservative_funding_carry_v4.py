"""
s62 Conservative Funding Carry V4 — s61 with reduced sizing to control MaxDD

Same proven mechanism as s61 (funding carry), but with conservative position sizing
to reduce portfolio MaxDD contribution. s61 showed massive Calmar improvement (+23.56)
but killed on MaxDD (-4.71% > -3%) and Sharpe dilution (6.99 < 7.0) — both caused by
aggressive cap_multiplier=8.0. This version uses cap_multiplier=2.0 and halved regime
sizing to deliver ~25% of the risk with the same Sharpe ratio (scale-invariant).

Mechanism: Harvest structural funding rate premium. Retail is net long in crypto,
so funding is structurally positive. We go short to collect the payment. When
funding is negative (rare), we go long. Market-neutral — beta ≈ 0.

Key changes from s61:
  1. cap_multiplier: 8.0 → 2.0 (75% reduction in capital deployed)
  2. REGIME_SIZE: halved (1.0, 0.75, 1.25, 0.75 vs 2.0, 1.5, 2.5, 1.5)
  3. size_mult cap: 4.0 → 2.0

Status: V4 CANDIDATE
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
                    rolling_mean, rolling_std)


# Exit ablation (v2/v3): flat 1.5 ATR trail + breakeven beats progressive schedule.
TRAIL_SCHEDULE = None

# Regime sizing — HALVED from s61 to control MaxDD
REGIME_SIZE = np.array([0.0, 1.0, 0.75, 1.25, 0.75], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    n = len(ctx.ind_1h['close'])

    # ── FUNDING DATA ────────────────────────────────────────────
    funding = ctx.funding_1h  # per-hour funding rate
    if funding is None:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            name='s62_conservative_funding_carry_v4',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    regime = ctx.regime_1h
    regime_ok = regime != CRISIS

    # ── LAYER 2: FUNDING PERSISTENCE ────────────────────────────
    abs_funding = np.abs(funding)
    funding_ma = rolling_mean(abs_funding, 72)

    # Threshold: funding must be meaningfully elevated
    # 0.00005/hr ≈ 48% annualized — strong carry signal
    funding_threshold = 0.00005
    funding_elevated = funding_ma > funding_threshold

    # ── LAYER 3: DIRECTION (carry = opposite to funding) ────────
    funding_signed = rolling_mean(funding, 72)
    direction = np.where(funding_signed > 0, np.int8(-1), np.int8(1))

    # ── LAYER 4: LIQUIDITY ──────────────────────────────────────
    liquid = ctx.liquidity_mask

    # ── LAYER 5: ADX CONFIDENCE ─────────────────────────────────
    adx = ctx.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.3, np.where(adx > 20, 1.0, 0.7))

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = regime_ok & funding_elevated & liquid
    entry[:200] = False  # warmup guard

    # ── SIZING ──────────────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]
    size_mult = size_mult * adx_scale

    # Cap at 2.0 (conservative — s61 used 4.0)
    size_mult = np.minimum(size_mult, 2.0)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # ── TRADE MANAGEMENT (same as s61) ─────────────────────
        stop_mult=4.0,
        trail_mult=1.5,
        target_mult=999,
        no_stop_bars=48,
        min_hold=24,
        max_hold=504,
        edge=0.30,

        exit_regimes={CRISIS},
        market_type=MarketType.PERP,
        leverage=1.0,
        name='s62_conservative_funding_carry_v4',

        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=size_mult,
        cap_multiplier=2.0,   # Conservative — s61 used 8.0
        breakeven_atr=0.5,
    )
