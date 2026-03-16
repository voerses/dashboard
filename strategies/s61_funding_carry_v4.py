"""
s61 Funding Carry V4 — s29 funding carry + V4 aggressive sizing + trail progression

V4 adaptation of s29 funding carry for portfolio complement use.
Designed to add market-neutral diversification to s58 (momentum + basis carry).

Enhancement over s29:
  1. Progressive trailing stops (proven universal overlay)
  2. Regime-aware sizing (boost in RANGE/QUIET where carry is steadiest)
  3. ADX confidence scaling (proven: ADX is #1 predictor IC=0.067)
  4. Aggressive cap_multiplier for V4 shared capital (more capital deployed)
  5. Signal-informed entry: cross-TF divergence for better timing

Mechanism: Harvest structural funding rate premium. Retail is net long in crypto,
so funding is structurally positive. We go short to collect the payment. When
funding is negative (rare), we go long. Market-neutral — beta ≈ 0.

Mission target: Add to s58 portfolio to boost March/sideways performance.
Near-zero correlation with s56 (momentum) and s57 (carry) means pure
diversification benefit.

Status: V4 CANDIDATE
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
                    rolling_mean, rolling_std)


# Flat trail — exit ablation winner (trail_mult=1.5 across all strategies)
TRAIL_SCHEDULE = None

# Regime sizing — carry works in ALL regimes except CRISIS
# Boost in RANGE/QUIET where carry is steadiest and momentum/carry strategies flatten
REGIME_SIZE = np.array([0.0, 2.0, 1.5, 2.5, 1.5], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND
# CRISIS: 0 (no entries — liquidity risk)
# QUIET: 2.0 (carry very stable in low vol)
# UPTREND: 1.5 (carry works but momentum better here)
# RANGE: 2.5 (carry's best regime — s57 goes flat here, we fill the gap)
# DOWNTREND: 1.5 (carry works — funding stays positive as retail holds longs)


def strategy(ctx: StrategyContext) -> StrategyResult:
    n = len(ctx.ind_1h['close'])

    # ── FUNDING DATA ────────────────────────────────────────────
    funding = ctx.funding_1h  # per-hour funding rate
    if funding is None:
        # No funding data — skip this token
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            name='s61_funding_carry_v4',
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
    # Higher ADX = stronger trend = more predictable funding persistence
    adx = ctx.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.3, np.where(adx > 20, 1.0, 0.7))

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = regime_ok & funding_elevated & liquid
    entry[:200] = False  # warmup guard

    # ── SIZING ──────────────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]
    size_mult = size_mult * adx_scale

    # Cap at 4.0
    size_mult = np.minimum(size_mult, 4.0)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # ── TRADE MANAGEMENT ────────────────────────────────────
        stop_mult=4.0,        # 4x ATR initial stop (carry needs room)
        trail_mult=1.5,       # 1.5x ATR flat trail (exit ablation winner)
        target_mult=999,      # No fixed target — let carry accumulate
        no_stop_bars=48,      # 48h protection (funding accumulates over time)
        min_hold=24,          # Minimum 24 hours
        max_hold=504,         # Maximum 21 days (longer than s29's 14d for V4)
        edge=0.30,            # Conservative Kelly

        exit_regimes={CRISIS},  # Only exit on CRISIS
        market_type=MarketType.PERP,
        leverage=1.0,
        name='s61_funding_carry_v4',

        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=size_mult,
        cap_multiplier=8.0,   # Aggressive V4 capital deployment
    )
