"""
s33 Leveraged Conviction Perp — Dynamic Leverage Scaled by Conviction Score.

Hypothesis: "A multi-factor conviction score, mapped to variable leverage (1x-10x)
with inverse-vol scaling (Moreira & Muir 2017), amplifies edge on high-confidence
momentum setups while naturally limiting risk in uncertain or volatile conditions."

Class: A (Per-Token Signal), Market: PERP, Direction: Bidirectional

Key difference from s28 (killed at 6.7%): continuous conviction scoring instead of
hard binary thresholds. ADX is a continuous 0-1 sub-score, not a binary gate.

Conviction sub-scores (3 factors, Gate 1 pruned):
  ADX strength     (0.35) — clip((adx - 15) / 35, 0, 1)
  Multi-TF trend   (0.40) — (1h + 4h + daily alignment) / 3
  Momentum mag     (0.25) — clip(abs(ret_24h) / 0.15, 0, 1)

Gate 1 dropped: RSI positioning (IC=-0.020, harmful), Volume (IC=0.000, dead weight)

Leverage: 1x-10x, inverse-vol scaled, discretized to 0.5x steps.
Stops: fixed-dollar-risk — stop_mult = max(3.0 / sqrt(leverage), 0.5) ATR.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Leveraged conviction perp — bidirectional with dynamic leverage."""
    n = len(ctx.ind_1h['close'])

    close = ctx.ind_1h['close']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    vol_20 = ctx.ind_1h['vol_20']
    regime = ctx.regime_1h

    # ── 24h return (from custom indicators or fallback) ──────────
    ret_24h = ctx.custom.get('ret_24h')
    if ret_24h is None:
        ret_24h = np.zeros(n, dtype=np.float64)
        ret_24h[24:] = (close[24:] - close[:-24]) / np.where(
            close[:-24] > 0, close[:-24], 1.0)

    # ── Multi-timeframe trend alignment ──────────────────────────
    # 1h: close vs ema20
    align_1h_long = (close > ema20).astype(np.float64)
    align_1h_short = (close < ema20).astype(np.float64)

    # 4h: close vs ema20
    close_4h = ctx.ind_4h['close']
    ema20_4h = ctx.ind_4h['ema_20']
    align_4h_long_raw = (close_4h > ema20_4h).astype(np.float64)
    align_4h_short_raw = (close_4h < ema20_4h).astype(np.float64)
    align_4h_long = ctx.align_4h_to_1h(align_4h_long_raw)
    align_4h_short = ctx.align_4h_to_1h(align_4h_short_raw)

    # Daily: close vs ema20
    close_d = ctx.ind_d['close']
    ema20_d = ctx.ind_d['ema_20']
    align_d_long_raw = (close_d > ema20_d).astype(np.float64)
    align_d_short_raw = (close_d < ema20_d).astype(np.float64)
    align_d_long = ctx.align_daily_to_1h(align_d_long_raw)
    align_d_short = ctx.align_daily_to_1h(align_d_short_raw)

    # ════════════════════════════════════════════════════════════
    # CONVICTION SCORING — 3 sub-scores (Gate 1 pruned RSI + Volume)
    # ════════════════════════════════════════════════════════════

    # Sub-score 1: ADX strength (0.35 weight)
    # 0 at ADX=15, 1 at ADX=50
    adx_score = np.clip((adx - 15.0) / 35.0, 0.0, 1.0)

    # Sub-score 2: Multi-TF trend alignment (0.40 weight)
    mtf_long = (align_1h_long + align_4h_long + align_d_long) / 3.0
    mtf_short = (align_1h_short + align_4h_short + align_d_short) / 3.0

    # Sub-score 3: Momentum magnitude (0.25 weight)
    # 0 at 0%, 1 at 15%
    mom_score = np.clip(np.abs(ret_24h) / 0.15, 0.0, 1.0)

    # Composite conviction (3 factors only — RSI and volume dropped per Gate 1)
    conviction_long = 0.35 * adx_score + 0.40 * mtf_long + 0.25 * mom_score
    conviction_short = 0.35 * adx_score + 0.40 * mtf_short + 0.25 * mom_score

    # ════════════════════════════════════════════════════════════
    # ENTRY CONDITIONS
    # ════════════════════════════════════════════════════════════

    # Long: not CRISIS/DOWNTREND, close > EMA20, ret_24h > 2%, vol > 0.8, conviction > 0.20
    regime_long_ok = (regime != CRISIS) & (regime != DOWNTREND)
    long_entry = (regime_long_ok &
                  (close > ema20) &
                  (ret_24h > 0.02) &
                  (vol_ratio > 0.8) &
                  (conviction_long > 0.20))

    # Short: DOWNTREND only, close < EMA20, ret_24h < -2%, vol > 0.8, conviction > 0.25
    short_entry = ((regime == DOWNTREND) &
                   (close < ema20) &
                   (ret_24h < -0.02) &
                   (vol_ratio > 0.8) &
                   (conviction_short > 0.25))

    # Combined entry mask and direction
    entry_mask = long_entry | short_entry
    entry_mask[:200] = False  # warmup guard

    direction = np.ones(n, dtype=np.int8)
    direction[short_entry] = -1

    # ════════════════════════════════════════════════════════════
    # LEVERAGE MAPPING — Moreira-Muir inverse vol scaling
    # ════════════════════════════════════════════════════════════

    # Use per-direction conviction for leverage
    conviction = np.where(long_entry, conviction_long, conviction_short)
    # For bars with no entry, use long conviction (doesn't matter, won't be used)
    no_entry = ~entry_mask
    conviction[no_entry] = conviction_long[no_entry]

    # Inverse-vol scaling (Moreira & Muir 2017)
    # Upper clamp 1.5 (was 3.0) prevents low-vol regimes from pushing everything to 10x
    vol_scale = np.clip(0.015 / np.maximum(vol_20, 0.003), 0.3, 1.5)

    # Base leverage from conviction² (concave: low conviction stays low, only high conviction amplifies)
    # conviction=0.3→1.8x, 0.5→3.3x, 0.7→5.4x, 0.9→8.3x, 1.0→10x
    base_leverage = 1.0 + (conviction ** 2) * 9.0

    # Discretize to 0.5x steps, clip to [1, 10]
    leverage_arr = np.clip(
        np.round(base_leverage * vol_scale * 2.0) / 2.0,
        1.0, 10.0)

    # ════════════════════════════════════════════════════════════
    # STOP/TRAIL ARRAYS — Fixed dollar risk via sqrt scaling
    # ════════════════════════════════════════════════════════════

    # stop_mult = max(3.0 / sqrt(leverage), 0.5) ATR
    sqrt_lev = np.sqrt(leverage_arr)
    stop_mult_arr = np.maximum(3.0 / sqrt_lev, 0.5)
    trail_mult_arr = np.maximum(3.0 / sqrt_lev, 0.5)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,

        stop_mult=stop_mult_arr,
        trail_mult=trail_mult_arr,
        target_mult=999,        # trail only, no fixed target
        no_stop_bars=12,        # shorter than s11's 24 — higher leverage needs faster protection
        min_hold=12,
        max_hold=336,           # 14 days — limits funding accumulation
        edge=0.30,              # conservative Kelly — leverage amplifies

        exit_regimes={CRISIS},  # DOWNTREND is a short opportunity, not an exit
        market_type=MarketType.PERP,
        leverage=leverage_arr,
        exchange='binance',
        name='s33_leveraged_conviction_perp',
        breakeven_atr=0.5,
    )
