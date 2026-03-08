"""
s51 Concentrated Leveraged Momentum — Token-specific regime + multi-horizon momentum + 3x perp

NEW strategy (not a wrapper). Uses perp with 3x leverage.

Entry logic:
- Layer 1: Token-specific regime — token must be in its OWN uptrend
  (close > EMA50, ADX > 20, EMA20 > EMA50) — not just global BTC regime
- Layer 2: Multi-horizon momentum confirmation — ret_24h > 3% AND ret_48h > 5%
  (momentum must be building, not a single spike)
- Layer 3: Volume surge — vol_ratio > 1.5 (above-average volume)
- Layer 4: NOT overbought — RSI < 80 (avoid chasing into reversal)

Exit: Progressive trail + regime exit on token downtrend/crisis
3x leverage on perp for aggressive returns
Edge set high (0.6) because this is a high-conviction filtered entry

Mechanism: Crypto momentum cascades from BTC to alts. By requiring
token-specific uptrend + multi-horizon momentum + volume surge, we
filter for HIGH CONVICTION setups where the cascade is confirmed,
then leverage them for aggressive returns.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


TRAIL_SCHEDULE = np.array([
    [0.0, 3.0],
    [1.0, 2.5],
    [2.0, 2.0],
    [3.0, 1.5],
], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Concentrated leveraged momentum on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    rsi = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']

    # ── LAYER 1: TOKEN-SPECIFIC UPTREND ──────────────────────
    # Token must be in its OWN uptrend (not just global BTC regime)
    token_uptrend = (close > ema_50) & (ema_20 > ema_50) & (adx > 20)

    # Also exclude global crisis
    not_crisis = ctx.regime_1h != 0  # CRISIS = 0

    # ── LAYER 2: MULTI-HORIZON MOMENTUM ──────────────────────
    ret_24h = ctx.custom.get('ret_24h')
    ret_48h = ctx.custom.get('ret_48h')
    if ret_24h is None:
        # Fallback: compute from close
        ret_24h = np.zeros(n)
        ret_24h[24:] = (close[24:] - close[:-24]) / np.maximum(close[:-24], 1e-10)
    if ret_48h is None:
        ret_48h = np.zeros(n)
        ret_48h[48:] = (close[48:] - close[:-48]) / np.maximum(close[:-48], 1e-10)

    momentum_building = (ret_24h > 0.03) & (ret_48h > 0.05)

    # ── LAYER 3: VOLUME SURGE ────────────────────────────────
    vol_ok = vol_ratio > 1.5

    # ── LAYER 4: NOT OVERBOUGHT ──────────────────────────────
    not_overbought = rsi < 80

    # ── COMPOSE ──────────────────────────────────────────────
    entry = token_uptrend & not_crisis & momentum_building & vol_ok & not_overbought
    entry[:200] = False

    # Liquidity gate
    if ctx.liquidity_mask is not None:
        entry = entry & ctx.liquidity_mask

    # Direction: always long (momentum strategy)
    direction = np.ones(n, dtype=np.int8)

    # Regime-adaptive sizing: UPTREND=3x, RANGE=1.5x, QUIET=0.5x
    regime = ctx.regime_1h
    regime_size = np.where(regime == 2, 2.0,       # UPTREND
                 np.where(regime == 3, 1.0,         # RANGE
                 np.where(regime == 1, 0.5, 0.3)))  # QUIET / DOWNTREND

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=3.0,
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.60,
        exit_regimes={CRISIS, DOWNTREND},
        exchange='binance',
        name='s51_concentrated_momentum_leveraged',
        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=regime_size,
    )
