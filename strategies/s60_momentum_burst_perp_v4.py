"""
Strategy S60: Momentum Burst Perp — V4 Portfolio Sideways Complement
====================================================================
Class D (V4 Portfolio Strategy): gate path 0->2->V4-3->V4-4->V4-5->6->7

Hypothesis: Bidirectional perp momentum burst captures both long and short
micro-trends across 165 perp tokens, generating alpha in sideways/choppy
markets where s58's carry component (s57) goes flat and s58's momentum
component (s56, long-only spot) has no sustained trend to ride.

V4 rebuild of s28 (killed at V3 Gate 5 with 6.7% rate). V4 sweep showed
s28 was the BEST overall V4 candidate: +3157% (12mo), Sharpe 5.2,
MaxDD -4.1%, 2400+ trades. March 2026: +$15.2K (beats s58's $14.9K).
V3 kill reason (per-token selectivity) irrelevant in V4 portfolio context.

Enhancements over original s28:
  1. Progressive trailing stops (proven: s37/s44, Sharpe +0.4-1.4)
  2. Regime-aware sizing (RANGE/QUIET boosted for sideways mission)
  3. Multi-TF alignment sizing (4h trend confirmation, cheap O(n))
  4. ADX confidence scaling (ADX is #1 predictor, IC=0.067)
  5. 1x leverage + aggressive sizing (proven > raw leverage, finding #23)

Market: PERP (bidirectional — essential for sideways markets)
Target regimes: RANGE, QUIET, DOWNTREND (sideways complement to s58)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS)

# Progressive trail schedule (proven in s37/s44/s56)
TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry: wide trail
    [1.0, 2.5],   # 1 ATR profit: tighten
    [2.0, 2.0],   # 2 ATR profit: moderate
    [3.0, 1.5],   # 3+ ATR: lock in winners
], dtype=np.float64)

# Regime sizing — boosted for sideways/choppy mission
# Index:         CRISIS  QUIET  UPTREND  RANGE  DOWNTREND
REGIME_SIZE = np.array([0.0, 1.5, 2.5, 2.0, 2.0], dtype=np.float64)
# CRISIS: 0 (no entries)
# QUIET: 1.5 (both directions work, moderate)
# UPTREND: 2.5 (long momentum strongest here)
# RANGE: 2.0 (key for mission — both directions profit from chop)
# DOWNTREND: 2.0 (short momentum works here — s60's unique edge)


def _compute_tf_alignment(ctx, direction):
    """Multi-timeframe trend alignment signal (cheap, no rolling z-score).

    When 1h entry direction agrees with 4h trend → boost sizing (1.3x).
    When they disagree → reduce sizing (0.7x).
    Uses pre-computed 4h EMA from engine — O(n) array ops only.
    """
    n = len(ctx.ind_1h['close'])
    ema20_4h = ctx.ind_4h.get('ema_20')
    close_4h = ctx.ind_4h.get('close')
    if ema20_4h is None or close_4h is None:
        return np.ones(n)

    # 4h trend: above EMA20 = uptrend, below = downtrend
    # Shift by 1 to prevent look-ahead (4h bar not complete until hour 4)
    trend_up_4h = np.empty(len(close_4h), dtype=bool)
    trend_up_4h[0] = False
    trend_up_4h[1:] = close_4h[:-1] > ema20_4h[:-1]

    # Align to 1h grid (each 4h bar → 4 consecutive 1h bars)
    aligned = np.repeat(trend_up_4h, 4)[:n]
    if len(aligned) < n:
        aligned = np.concatenate([aligned, np.full(n - len(aligned), aligned[-1])])

    # Longs boosted when 4h uptrend; shorts boosted when 4h downtrend
    return np.where(
        (direction == 1) & aligned, 1.3,
        np.where(
            (direction == -1) & ~aligned, 1.3,
            0.8
        )
    )


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Bidirectional perp momentum burst — long upswings, short downswings."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    ret_1 = ctx.ind_1h['ret_1']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    # Allow all except CRISIS — bidirectional profits in all regimes
    regime_ok = regime != 0

    # ── LAYER 2: TREND CONFIRMATION (ADX) ───────────────────────
    adx_ok = adx > 20  # Trend must be present (either direction)

    # ── LAYER 3: ENTRY SIGNAL (bidirectional momentum burst) ────
    # LONG: strong upward burst + above EMA20 (uptrend alignment)
    long_entry = regime_ok & adx_ok & (ret_1 > 0.03) & (close > ema20)
    # SHORT: strong downward burst + below EMA20 (downtrend alignment)
    short_entry = regime_ok & adx_ok & (ret_1 < -0.03) & (close < ema20)

    # ── LAYER 4: VOLUME CONFIRMATION ────────────────────────────
    vol_ok = vol_ratio > 0.8  # Slightly relaxed for perps
    long_entry = long_entry & vol_ok
    short_entry = short_entry & vol_ok

    # ── COMPOSE ─────────────────────────────────────────────────
    entry = long_entry | short_entry
    entry[:200] = False  # Warmup guard

    direction = np.where(long_entry, 1,
                np.where(short_entry, -1, 0)).astype(np.int8)

    # ── REGIME SIZING ───────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ── ADX CONFIDENCE SCALING ──────────────────────────────────
    # ADX is #1 predictor (IC=0.067). Strong trend = bigger position.
    adx_scale = np.where(adx > 30, 1.5, np.where(adx > 20, 1.0, 0.5))
    size_mult = size_mult * adx_scale

    # ── MULTI-TF ALIGNMENT SIZING ──────────────────────────────
    # Boost when 1h entry direction agrees with 4h trend (1.3x)
    # Reduce when they disagree (0.7x) — fighting the higher TF
    tf_scale = _compute_tf_alignment(ctx, direction)
    size_mult = size_mult * tf_scale

    # ── MOMENTUM MAGNITUDE BOOST ────────────────────────────────
    # Stronger bursts (>5%) deserve more capital
    abs_ret = np.abs(ret_1)
    mom_boost = np.where(abs_ret > 0.05, 1.3, 1.0)
    size_mult = size_mult * mom_boost

    # Cap at 8.0 (higher than s56's 6.0 because perp fees eat more)
    size_mult = np.minimum(size_mult, 8.0)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Trade management
        stop_mult=3.0,        # 3x ATR initial stop (Tier A proven)
        trail_mult=3.0,       # 3x ATR trailing
        target_mult=999,      # Trail only, no fixed target
        no_stop_bars=24,      # 24h protection (biggest single lever)
        min_hold=18,          # Min 18 hours
        max_hold=720,         # Max 30 days
        edge=0.40,            # Kelly edge estimate

        # CRITICAL: Do NOT exit on DOWNTREND — shorts need downtrends!
        exit_regimes={CRISIS},

        name='s60_momentum_burst_perp_v4',

        # V4 perp settings
        market_type=MarketType.PERP,
        leverage=1.0,            # 1x leverage (proven > raw leverage)
        exchange='binance',
        size_multiplier=size_mult,
        cap_multiplier=15.0,     # Aggressive ADV cap for capital deployment
        trail_schedule=TRAIL_SCHEDULE,
    )
