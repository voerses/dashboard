"""
s32 Regime-Adaptive Spot-Perp — Use optimal instrument per regime.

Hypothesis: "Using spot for long trades in uptrends and perp for short trades
in downtrends improves Calmar because spot avoids funding drag on longs,
perp enables shorts + earns funding in downtrends."

Gate 0: PASS — bidirectional with instrument efficiency, score 7/10
Gate 2: PASS — no existing combined strategy; different from s28 (perp-only bidirectional)

Signal stack:
  Primary (spot long — uptrend):
    Layer 1: Regime — UPTREND or QUIET only
    Layer 2: Trend — close > EMA20, ADX > 25
    Layer 3: Entry — ret_24h > 0.05 (sustained momentum, not just 1h burst)
    Layer 4: Volume — vol_ratio > 0.8

  Secondary (perp short — downtrend):
    Layer 1: Regime — DOWNTREND only
    Layer 2: Trend — close < EMA20, ADX > 20 (trend present, bearish)
    Layer 3: Entry — ret_24h < -0.03 (downward momentum confirmed)
    Layer 4: Volume — vol_ratio > 0.8

Combined engine test coverage:
  - Alternating legs (primary in uptrends, secondary in downtrends)
  - Regime-gated entries per leg
  - Different directions per leg (long vs short)
  - capital_split 0.6/0.4
  - Distinct secondary_* trade params

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
                    rolling_mean)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """Regime-adaptive: long spot in uptrends, short perp in downtrends."""
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    # ════════════════════════════════════════════════════════════
    # PRIMARY LEG: Spot Long in Uptrends
    # ════════════════════════════════════════════════════════════

    spot_close = ctx_spot.ind_1h['close'][:n]
    spot_regime = ctx_spot.regime_1h[:n]

    # Layer 1: Regime — uptrend or quiet (favorable for longs)
    regime_long_ok = (spot_regime == UPTREND) | (spot_regime == QUIET)

    # Layer 2: Trend alignment
    spot_ema20 = ctx_spot.ind_1h['ema_20'][:n]
    spot_adx = ctx_spot.ind_1h['adx'][:n]
    trend_long = (spot_close > spot_ema20) & (spot_adx > 25)

    # Layer 3: Sustained upward momentum (not just 1h spike)
    ret_24h = ctx_spot.custom.get('ret_24h')
    if ret_24h is not None:
        ret_24h = ret_24h[:n]
    else:
        # Fallback: compute from close
        ret_24h = np.zeros(n, dtype=np.float64)
        ret_24h[24:] = (spot_close[24:] - spot_close[:-24]) / np.where(spot_close[:-24] > 0, spot_close[:-24], 1.0)
    momentum_long = ret_24h > 0.05

    # Layer 4: Volume
    spot_vol_ratio = ctx_spot.ind_1h['vol_ratio'][:n]
    vol_long_ok = spot_vol_ratio > 0.8

    spot_entry = regime_long_ok & trend_long & momentum_long & vol_long_ok
    spot_entry[:200] = False

    # ════════════════════════════════════════════════════════════
    # SECONDARY LEG: Perp Short in Downtrends
    # ════════════════════════════════════════════════════════════

    perp_close = ctx_perp.ind_1h['close'][:n]
    perp_regime = ctx_perp.regime_1h[:n]

    # Layer 1: Regime — downtrend only (this is the short opportunity)
    regime_short_ok = perp_regime == DOWNTREND

    # Layer 2: Trend alignment (bearish)
    perp_ema20 = ctx_perp.ind_1h['ema_20'][:n]
    perp_adx = ctx_perp.ind_1h['adx'][:n]
    trend_short = (perp_close < perp_ema20) & (perp_adx > 20)

    # Layer 3: Downward momentum confirmed
    perp_ret_24h = ctx_perp.custom.get('ret_24h')
    if perp_ret_24h is not None:
        perp_ret_24h = perp_ret_24h[:n]
    else:
        perp_ret_24h = np.zeros(n, dtype=np.float64)
        perp_ret_24h[24:] = (perp_close[24:] - perp_close[:-24]) / np.where(perp_close[:-24] > 0, perp_close[:-24], 1.0)
    momentum_short = perp_ret_24h < -0.03

    # Layer 4: Volume
    perp_vol_ratio = ctx_perp.ind_1h['vol_ratio'][:n]
    vol_short_ok = perp_vol_ratio > 0.8

    # Layer 5: Liquidity
    perp_liquid = ctx_perp.liquidity_mask[:n] if ctx_perp.liquidity_mask is not None else np.ones(n, dtype=bool)

    perp_entry = regime_short_ok & trend_short & momentum_short & vol_short_ok & perp_liquid
    perp_entry[:200] = False

    return StrategyResult(
        # Primary: spot long in uptrends
        entry_mask=spot_entry,
        direction=np.ones(n, dtype=np.int8),
        market_type=MarketType.COMBINED,

        # Secondary: perp short in downtrends
        secondary_entry_mask=perp_entry,
        secondary_direction=-np.ones(n, dtype=np.int8),
        secondary_market_type=MarketType.PERP,
        secondary_leverage=1.0,  # no leverage — let the direction do the work

        capital_split=0.6,  # 60% longs (more frequent), 40% shorts

        # Primary trade management (uptrend longs)
        stop_mult=3.0,
        trail_mult=2.5,
        target_mult=5.0,     # take profit at 5x ATR in uptrends
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.35,

        # Secondary trade management (downtrend shorts — tighter, faster)
        secondary_stop_mult=2.5,     # tighter stop — downtrends are faster/sharper
        secondary_trail_mult=2.0,
        secondary_target_mult=4.0,   # shorter profit target
        secondary_no_stop_bars=12,   # less protection — moves are sharper
        secondary_min_hold=12,
        secondary_max_hold=336,      # max 14 days (downtrends resolve faster)
        secondary_edge=0.30,

        exit_regimes={CRISIS},  # only exit on CRISIS (regime-gated entries handle the rest)
        exchange='binance',
        name='s32_regime_spot_perp',
        breakeven_atr=0.5,
    )
