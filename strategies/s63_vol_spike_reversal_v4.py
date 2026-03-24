"""
Strategy S63: Vol Spike Reversal — V4 Portfolio Counter-Trend Complement
========================================================================
Class D (V4 Portfolio Strategy): gate path 0->2->V4-3->V4-4->V4-5->6->7

Hypothesis: Extreme volatility spikes (vol_ratio > 3x) cause temporary price
overshoot due to liquidation cascades and retail panic. Price reverts toward
the pre-spike mean within 12-168h. We FADE the spike direction.

V4 rebuild of s25 (killed at V3 Gate 5 — BTC failed 3x). V4 portfolio
context transforms weak per-token strategies into useful portfolio components
(proven: s28->s60 went from 6.7% V3 rate to +3157% V4 return).

Key differentiator: This is COUNTER-TREND — genuinely uncorrelated with
s56 (momentum), s57 (carry), s60 (momentum perp), s62 (funding carry).
All others follow trends or harvest carry; s63 fades extreme moves.

Enhancements over original s25:
  1. Progressive trailing stops (proven overlay from s37/s44)
  2. Regime-aware sizing (RANGE/QUIET boosted — more spikes in choppy markets)
  3. ADX confidence scaling (ADX = top predictor IC=0.067)
  4. Displacement magnitude scaling (bigger spike = bigger fade)
  5. 1x leverage + moderate sizing (conservative — MR is inherently riskier)

Market: PERP (bidirectional — shorts overshoot, longs undershoot)
Target regimes: RANGE, QUIET, DOWNTREND (spikes happen in all, worst in CRISIS)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS)

# Exit ablation (v2/v3): flat 1.5 ATR trail + breakeven beats progressive schedule.
TRAIL_SCHEDULE = None

# Regime sizing — moderate because mean reversion is inherently riskier
# Index:         CRISIS  QUIET  UPTREND  RANGE  DOWNTREND
REGIME_SIZE = np.array([0.0, 1.5, 1.0, 2.0, 1.5], dtype=np.float64)
# CRISIS: 0 (no entries — spikes in crisis don't revert)
# QUIET: 1.5 (vol spikes in quiet = strong signal, quick reversion)
# UPTREND: 1.0 (reduced — fading uptrend momentum is risky)
# RANGE: 2.0 (best regime — spikes in both directions revert well)
# DOWNTREND: 1.5 (spikes revert but trend can resume)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Vol spike mean reversion — fade extreme moves on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ret_1 = ctx.ind_1h['ret_1']
    regime = ctx.regime_1h

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    # Exclude CRISIS — cascading liquidations don't revert
    regime_ok = regime != 0

    # ── LAYER 2: VOL SPIKE DETECTION ────────────────────────────
    # 3x average volume = significant forced trading activity
    vol_spike = vol_ratio > 3.0

    # ── LAYER 3: TREND PRESENCE (ADX) ───────────────────────────
    # Need directional movement to fade (no spike without direction)
    adx_ok = adx > 20

    # ── LAYER 4: PRICE DISPLACEMENT — fade the overextension ────
    displacement = (close - ema20) / np.maximum(ema20, 1e-10)

    # Overextended UP → SHORT (fade the spike)
    overextended_up = displacement > 0.025
    # Overextended DOWN → LONG (fade the spike)
    overextended_down = displacement < -0.025

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry_short = regime_ok & vol_spike & adx_ok & overextended_up
    entry_long = regime_ok & vol_spike & adx_ok & overextended_down

    entry = entry_short | entry_long
    entry[:200] = False  # Warmup guard

    direction = np.where(entry_short, -1,
                np.where(entry_long, 1, 0)).astype(np.int8)

    # ── REGIME SIZING ───────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ── ADX CONFIDENCE SCALING ──────────────────────────────────
    # Higher ADX = stronger directional move = more confident reversion
    adx_scale = np.where(adx > 35, 1.5, np.where(adx > 25, 1.2, 1.0))
    size_mult = size_mult * adx_scale

    # ── DISPLACEMENT MAGNITUDE SCALING ──────────────────────────
    # Bigger displacement = stronger reversion expected = bigger position
    abs_disp = np.abs(displacement)
    disp_scale = np.where(abs_disp > 0.05, 1.5,
                 np.where(abs_disp > 0.035, 1.2, 1.0))
    size_mult = size_mult * disp_scale

    # Cap at 4.0 (conservative for mean reversion)
    size_mult = np.minimum(size_mult, 4.0)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Trade management — wider stops for MR (needs room to breathe)
        stop_mult=4.0,        # 4x ATR (wider than momentum — MR needs slack)
        trail_mult=1.5,       # 1.5x ATR flat trail (exit ablation winner)
        target_mult=999,      # Trail only
        no_stop_bars=12,      # 12h protection (reversion starts quickly)
        min_hold=12,          # Min 12h
        max_hold=168,         # Max 7 days (MR is short-hold)
        edge=0.30,            # Conservative edge — MR less reliable than momentum

        exit_regimes={CRISIS},

        name='s63_vol_spike_reversal_v4',

        # V4 perp settings
        market_type=MarketType.PERP,
        leverage=1.0,            # 1x leverage (conservative for MR)
        exchange='binance',
        size_multiplier=size_mult,
        cap_multiplier=4.0,      # Moderate ADV cap (less aggressive than momentum)
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
