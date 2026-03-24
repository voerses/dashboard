"""
Strategy S59: Funding Rate Mean Reversion — V4 Portfolio Component
=================================================================
Class D (V4 Portfolio Strategy): gate path 0->2->V4-3->V4-4->V4-5->6->7

Hypothesis: Extreme funding rates on perps revert to their mean as arbitrageurs
normalize the imbalance. Short when funding z-score > +2 (longs overcrowded),
long when z-score < -2 (shorts overcrowded).

V4 rebuild of s27 (killed at V3 Gate 5 with 11.6% rate). V4 sweep showed s27
was the BEST March 2026 performer (+$16.2K) — V4 portfolio context transforms
individually weak per-token strategies into useful portfolio components.

Key changes from s27:
  - Z-score (adaptive) instead of absolute thresholds (fixed)
  - 1x leverage + size_multiplier=3.0 (proven > raw leverage, finding #23)
  - Wider regime filter: allow RANGE/QUIET (where funding MR works best)
  - cap_multiplier=15.0 for more capital deployment

Market: PERP (bidirectional — essential for sideways markets)
Target regime: RANGE, QUIET (sideways complement to s58)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS)


def _fast_rolling_zscore(arr, window):
    """Vectorized z-score using numpy cumsum — no for-loops."""
    n = len(arr)
    cs = np.concatenate(([0.0], np.cumsum(arr)))
    cs2 = np.concatenate(([0.0], np.cumsum(arr * arr)))
    # roll_sum[i] = sum of arr[i-window+1 : i+1] for i >= window-1
    roll_sum = cs[window:] - cs[:-window]    # length n - window + 1
    roll_sum2 = cs2[window:] - cs2[:-window]
    roll_mean = roll_sum / window
    roll_var = roll_sum2 / window - roll_mean * roll_mean
    roll_std = np.sqrt(np.maximum(roll_var, 0))
    safe_std = np.where(roll_std > 1e-10, roll_std, 1e-10)
    # arr[window-1:] has same length as roll_mean
    zscore_tail = (arr[window - 1:] - roll_mean) / safe_std
    out = np.zeros(n)
    out[window - 1:] = zscore_tail
    return out


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Funding rate mean reversion — fade crowded positioning via z-score."""
    n = len(ctx.ind_1h['close'])
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    # Get funding rate (perp-only signal)
    funding = ctx.funding_1h
    if funding is None:
        entry = np.zeros(n, dtype=bool)
        return StrategyResult(
            entry_mask=entry,
            direction=np.zeros(n, dtype=np.int8),
            stop_mult=4.0, trail_mult=1.5, target_mult=999,
            no_stop_bars=24, min_hold=12, max_hold=168, edge=0.30,
            exit_regimes={CRISIS}, name='funding_mean_rev_v4',
            market_type=MarketType.PERP, leverage=1.0, exchange='binance',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    # Allow all regimes except CRISIS — funding MR works in sideways AND trends
    regime_ok = regime != 0

    # ── LAYER 2: FUNDING Z-SCORE (adaptive, not absolute) ──────
    # Rolling z-score of funding rate over 168h (7 days)
    # Adapts to each token's funding distribution (unlike s27's fixed thresholds)
    fund_zscore = _fast_rolling_zscore(funding, 168)

    # Entry at z-score extremes: +/-2.0
    fund_high = fund_zscore > 2.0   # longs overcrowded -> SHORT
    fund_low = fund_zscore < -2.0   # shorts overcrowded -> LONG

    # ── LAYER 3: TREND CONFIRMATION ────────────────────────────
    # Relaxed ADX: >15 (not 20 like s27) — we want entries in quiet markets too
    trend_present = adx > 15

    # ── LAYER 4: VOLUME CONFIRMATION ───────────────────────────
    vol_ok = vol_ratio > 0.8  # Relaxed — funding MR can work in lower volume

    # ── COMPOSE ────────────────────────────────────────────────
    entry_short = regime_ok & fund_high & trend_present & vol_ok
    entry_long = regime_ok & fund_low & trend_present & vol_ok

    direction = np.where(entry_long, 1,
                np.where(entry_short, -1, 0)).astype(np.int8)

    entry = entry_long | entry_short
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Trade management — wider stops for mean reversion
        stop_mult=4.0,        # 4x ATR (funding MR needs room)
        trail_mult=1.5,       # 1.5x ATR flat trail (exit ablation winner)
        target_mult=999,      # Trail only, no fixed target
        no_stop_bars=24,      # 24h protection (biggest lever, finding from Tier A)
        min_hold=12,          # Min 12h (funding takes time to normalize)
        max_hold=168,         # Max 7 days
        edge=0.30,            # Moderate edge estimate

        exit_regimes={CRISIS},  # Only exit on CRISIS — need both trends for shorts
        name='funding_mean_rev_v4',

        # V4 perp settings — 1x leverage + aggressive sizing (finding #23)
        market_type=MarketType.PERP,
        leverage=1.0,            # 1x leverage (not 2x like s27)
        exchange='binance',
        size_multiplier=3.0,     # Aggressive sizing at 1x leverage
        cap_multiplier=15.0,     # Relax ADV cap for more capital deployment,
        breakeven_atr=0.5,
    )
