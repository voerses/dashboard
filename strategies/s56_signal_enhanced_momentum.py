"""
s56 Signal-Enhanced Momentum — s11 momentum burst + signal discovery sizing overlays

Uses signal discovery IC data as SIZING OVERLAYS on proven momentum entries.
Key insight: signals predict return direction, but are best used to SIZE
positions in favorable conditions rather than to generate entries.

Enhancement stack on top of s11 momentum burst:
  1. Progressive trailing stops (from s37)
  2. Regime-scaled sizing using per-regime IC from signal discovery
  3. Cross-TF divergence sizing boost (ret_1_1h_vs_4h, IC=-0.34, STABLE LEADING)
  4. ADX confidence scaling (proven: ADX is #1 predictor)
  5. 24h no-stop protection (proven: +43% improvement)

Signal-informed sizing logic:
  - ret_1_1h_vs_4h z-score indicates mean-reversion pressure
  - When z-score is LOW (negative), momentum entry is WITH the reversal → boost size
  - When z-score is HIGH (positive), momentum may be about to reverse → reduce size
  - This uses the signal's predictive power without fighting it

Expected improvement over s11: higher Sharpe via regime concentration + signal sizing.

Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult,
                    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND)

from strategies.s11_momentum_burst import strategy as s11_strategy

# Progressive trail schedule (proven in s37)
TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],   # Entry: wide trail
    [1.0, 2.5],   # 1 ATR profit: start tightening
    [2.0, 2.0],   # 2 ATR profit: moderate
    [3.0, 1.5],   # 3+ ATR: lock in winners
], dtype=np.float64)

# Regime sizing from signal discovery per-regime IC data
# s11 is long-only momentum — size up in regimes where momentum signals are strongest
REGIME_SIZE = np.array([0.0, 0.8, 3.0, 1.0, 0.0], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND
# CRISIS: 0 (no entries)
# QUIET: 0.8 (weak momentum in low vol)
# UPTREND: 3.0 (momentum's best regime — per-regime IC confirms)
# RANGE: 1.0 (neutral)
# DOWNTREND: 0.0 (long-only, don't trade)


def _zscore_rolling(arr, window=72):
    """Fast rolling z-score for signal computation."""
    s = pd.Series(arr)
    mu = s.rolling(window, min_periods=window).mean()
    sigma = s.rolling(window, min_periods=window).std()
    z = ((s - mu) / sigma.clip(lower=1e-10)).values
    return np.clip(np.nan_to_num(z, nan=0.0), -3.0, 3.0)


def _compute_cross_tf_divergence(ctx):
    """Compute ret_1 cross-TF divergence (1h vs 4h).

    This is the #1 universal signal from signal discovery:
    IC=-0.34 at 4h, STABLE, LEADING with HIGH confidence.

    Returns z-scored divergence array aligned to 1h.
    """
    ret_1h = ctx.ind_1h.get('ret_1')
    ret_4h = ctx.ind_4h.get('ret_1')
    if ret_1h is None or ret_4h is None:
        return np.zeros(len(ctx.ind_1h['close']))

    # Align 4h to 1h grid
    aligned_4h = pd.Series(ret_4h, index=ctx.idx_4h).reindex(
        ctx.idx_1h, method='ffill').values.copy()

    z1 = _zscore_rolling(ret_1h)
    z4 = _zscore_rolling(aligned_4h)
    return z1 - z4


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Signal-enhanced momentum: s11 entries + signal-informed sizing."""
    result = s11_strategy(ctx)

    n = len(ctx.ind_1h['close'])
    regime = ctx.regime_1h

    # ── BASE REGIME SIZING ──────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ── ADX CONFIDENCE SCALING ──────────────────────────────────
    adx = ctx.ind_1h['adx']
    adx_scale = np.where(adx > 30, 1.5, np.where(adx > 20, 1.0, 0.5))
    size_mult = size_mult * adx_scale

    # ── CROSS-TF SIGNAL SIZING ──────────────────────────────────
    # ret_1_1h_vs_4h has negative IC: high z → low future returns
    # For long-only momentum: we WANT to enter when signal is LOW (reversal UP coming)
    # Low signal z-score → boost size (momentum aligns with mean-reversion)
    # High signal z-score → reduce size (momentum may reverse)
    cross_tf = _compute_cross_tf_divergence(ctx)

    # Map z-score to sizing: z < -1 → 1.5x boost, z > 1 → 0.5x reduction
    signal_scale = np.clip(1.0 - 0.25 * cross_tf, 0.5, 1.5)
    size_mult = size_mult * signal_scale

    # ── MOMENTUM MAGNITUDE BOOST ────────────────────────────────
    ret_1 = ctx.ind_1h.get('ret_1')
    if ret_1 is not None:
        # Strong burst (>5%) deserves more capital
        mom_boost = np.where(ret_1 > 0.05, 1.3, 1.0)
        size_mult = size_mult * mom_boost

    # Cap at 6.0
    size_mult = np.minimum(size_mult, 6.0)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,          # CRITICAL: 24h protection
        min_hold=18,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        name='s56_signal_enhanced_momentum',
        max_trade_pct=0.12,
        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=size_mult,
        cap_multiplier=4.0,       # 4x ADV cap (momentum can handle more)
    )
