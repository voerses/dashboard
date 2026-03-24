"""
s52 Funding Extremes Leveraged — 3x perp mean reversion on extreme funding

NEW strategy (not a wrapper). Builds on s27/s29 funding concepts but with
much tighter entry filters and 3x leverage for aggressive returns.

Key differences vs s27 (mean reversion, 2x) and s29 (carry, 1x):
- 3x leverage (vs 2x/1x)
- Z-score based extremes (z > 2.5) rather than absolute thresholds
- Requires funding PERSISTENCE (72h rolling mean extreme, not just spot)
- Token-specific regime filter (close > EMA50 for longs, < EMA50 for shorts)
- Volume confirmation (vol_ratio > 1.3)
- Progressive trail schedule for capturing full unwinds

Entry logic:
- Layer 1: Not crisis (regime != CRISIS)
- Layer 2: Funding rate z-score extreme (|z| > 2.5 on 72h window)
- Layer 3: Funding PERSISTENT (72h rolling mean |funding| > threshold)
- Layer 4: Token regime alignment — for shorts: close < EMA50 or RSI > 70
  (confirms overextension); for longs: close > EMA50 or RSI < 30
- Layer 5: Volume confirmation (vol_ratio > 1.3)
- Direction: OPPOSITE to funding (fade the crowd with leverage)

Exit: Progressive trail + regime exit on crisis
3x leverage on perp for aggressive returns
Edge set high (0.45) because multi-factor filtering catches high-conviction setups

Mechanism: Extreme funding = crowded positioning. When longs pay excessive funding,
they're the weak hand. Price unwinds are inevitable as funding erodes their P&L.
We lever into the unwind with tight risk management.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS,
                    rolling_mean, rolling_std)


TRAIL_SCHEDULE = np.array([
    [0.0, 3.5],
    [1.0, 3.0],
    [2.0, 2.5],
    [3.0, 2.0],
    [5.0, 1.5],
], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Leveraged funding extremes mean reversion on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema_50 = ctx.ind_1h['ema_50']
    rsi = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    # Get funding rate (perp-only signal)
    funding = ctx.funding_1h
    if funding is None:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            market_type=MarketType.PERP,
            leverage=3.0,
            name='s52_funding_extremes_leveraged',
            exchange='binance',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    regime_ok = regime != CRISIS

    # ── LAYER 2: FUNDING Z-SCORE EXTREMES ───────────────────────
    # Compute z-score of funding rate over 72h window
    fund_mean = rolling_mean(funding, 72)
    fund_std = rolling_std(funding, 72)
    fund_std_safe = np.maximum(fund_std, 1e-10)
    funding_z = (funding - fund_mean) / fund_std_safe

    # Extreme z-scores: |z| > 2.5 (stricter than s27's absolute thresholds)
    fund_extreme_pos = funding_z > 2.5    # longs overcrowded → SHORT
    fund_extreme_neg = funding_z < -2.5   # shorts overcrowded → LONG

    # ── LAYER 3: FUNDING PERSISTENCE ────────────────────────────
    # Funding must be persistently elevated, not just a spike
    abs_funding = np.abs(funding)
    funding_ma = rolling_mean(abs_funding, 72)
    funding_persistent = funding_ma > 0.00004  # ~35% annualized carry

    # ── LAYER 4: TOKEN REGIME ALIGNMENT ─────────────────────────
    # For shorts: token should be overextended (close < EMA50 or RSI > 70)
    # For longs: token should be oversold (close > EMA50 or RSI < 30)
    short_aligned = (close < ema_50) | (rsi > 70)
    long_aligned = (close > ema_50) | (rsi < 30)

    # ── LAYER 5: VOLUME ────────────────────────────────────────
    vol_ok = vol_ratio > 1.3

    # ── COMPOSE ─────────────────────────────────────────────────
    entry_short = regime_ok & fund_extreme_pos & funding_persistent & short_aligned & vol_ok
    entry_long = regime_ok & fund_extreme_neg & funding_persistent & long_aligned & vol_ok

    entry = entry_long | entry_short
    entry[:200] = False

    # Liquidity gate
    if ctx.liquidity_mask is not None:
        entry = entry & ctx.liquidity_mask

    # Direction: opposite to funding (fade the crowd)
    direction = np.where(entry_long, np.int8(1),
                np.where(entry_short, np.int8(-1), np.int8(0)))

    # Regime-adaptive sizing
    regime_size = np.where(regime == 2, 2.0,       # UPTREND — trend aids unwind
                 np.where(regime == 3, 1.5,         # RANGE — normal
                 np.where(regime == 4, 1.5,         # DOWNTREND — shorts benefit
                 np.where(regime == 1, 0.7, 0.3)))) # QUIET / fallback

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=3.0,
        stop_mult=4.0,
        trail_mult=3.5,
        target_mult=999,
        no_stop_bars=16,
        min_hold=12,
        max_hold=336,
        edge=0.45,
        exit_regimes={CRISIS},
        exchange='binance',
        name='s52_funding_extremes_leveraged',
        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=regime_size,
        breakeven_atr=0.5,
    )
