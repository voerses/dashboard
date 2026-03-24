"""
s53 Alt Momentum Breakout — Explosive breakout detection with leveraged perp

NEW strategy (not a wrapper). Captures alt-season-like explosive moves where
a token breaks out of consolidation with massive momentum and volume.

This is the "5-10x in months" strategy — targets the explosive phase of
a token's cycle when everything aligns: trend, momentum, volume, breakout.

Entry logic:
- Layer 1: Token-specific STRONG uptrend — EMA20 > EMA50, ADX > 25,
  close > EMA20 (not just above EMA50 like s51, but above EMA20 = strong trend)
- Layer 2: Multi-horizon momentum CASCADE — ret_12h > 2% AND ret_24h > 4%
  AND ret_48h > 6% (each horizon must show ACCELERATING returns)
- Layer 3: Breakout confirmation — close > Bollinger upper band OR
  close > Donchian high (price pushing into new territory)
- Layer 4: Volume EXPLOSION — vol_ratio > 2.0 (double normal volume)
- Layer 5: RSI momentum zone — RSI 55-85 (strong but not extreme reversal risk)
- Layer 6: Not in crisis

Exit: Progressive trail (tight at entry, loosens as profit builds) + crisis exit
2x leverage on perp (lower than s51's 3x due to higher volatility of breakouts)
Edge set high (0.55) because 5-layer filter produces very high conviction entries

Mechanism: Alt tokens have reflexive momentum — breakouts attract capital,
which drives further breakouts. By requiring trend + momentum + breakout +
volume simultaneously, we enter only when the reflexive loop is confirmed,
then ride the cascade with trailing stops.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


TRAIL_SCHEDULE = np.array([
    [0.0, 4.0],   # Wide at entry (let breakout breathe)
    [2.0, 3.0],   # Tighten as profit builds
    [4.0, 2.5],
    [6.0, 2.0],
    [10.0, 1.5],  # Very tight at 10% profit (protect gains)
], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Alt momentum breakout — explosive moves with leveraged perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    rsi = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']
    bb_upper = ctx.ind_1h['bb_upper']
    donch_high = ctx.ind_1h['donch_high']
    regime = ctx.regime_1h

    # ── LAYER 1: STRONG TOKEN UPTREND ───────────────────────────
    # Stricter than s51: close must be above EMA20 (not just EMA50)
    strong_uptrend = (close > ema_20) & (ema_20 > ema_50) & (adx > 25)

    # ── LAYER 2: MOMENTUM CASCADE ──────────────────────────────
    # Each horizon must show positive and ACCELERATING returns
    ret_12h = ctx.custom.get('ret_12h')
    ret_24h = ctx.custom.get('ret_24h')
    ret_48h = ctx.custom.get('ret_48h')

    # Fallback computation if not in custom
    if ret_12h is None:
        ret_12h = np.zeros(n)
        ret_12h[12:] = (close[12:] - close[:-12]) / np.maximum(close[:-12], 1e-10)
    if ret_24h is None:
        ret_24h = np.zeros(n)
        ret_24h[24:] = (close[24:] - close[:-24]) / np.maximum(close[:-24], 1e-10)
    if ret_48h is None:
        ret_48h = np.zeros(n)
        ret_48h[48:] = (close[48:] - close[:-48]) / np.maximum(close[:-48], 1e-10)

    # Momentum cascade: each horizon shows strong, building returns
    momentum_cascade = (ret_12h > 0.02) & (ret_24h > 0.04) & (ret_48h > 0.06)

    # ── LAYER 3: BREAKOUT CONFIRMATION ──────────────────────────
    # Price pushing into new territory
    breakout = (close > bb_upper) | (close > donch_high)

    # ── LAYER 4: VOLUME EXPLOSION ──────────────────────────────
    vol_explosion = vol_ratio > 2.0

    # ── LAYER 5: RSI MOMENTUM ZONE ─────────────────────────────
    # Strong momentum (>55) but not extremely overbought (<85)
    rsi_zone = (rsi > 55) & (rsi < 85)

    # ── LAYER 6: NOT CRISIS ────────────────────────────────────
    not_crisis = regime != CRISIS

    # ── COMPOSE ─────────────────────────────────────────────────
    entry = (strong_uptrend & momentum_cascade & breakout &
             vol_explosion & rsi_zone & not_crisis)
    entry[:200] = False

    # Liquidity gate
    if ctx.liquidity_mask is not None:
        entry = entry & ctx.liquidity_mask

    # Direction: always long (breakout strategy)
    direction = np.ones(n, dtype=np.int8)

    # Regime-adaptive sizing: maximize in uptrend, reduce elsewhere
    regime_size = np.where(regime == 2, 2.5,       # UPTREND — breakout's home
                 np.where(regime == 3, 1.5,         # RANGE — breakout from range
                 np.where(regime == 1, 0.5, 0.3)))  # QUIET / other

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=2.0,
        stop_mult=4.0,
        trail_mult=4.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=12,
        max_hold=480,
        edge=0.55,
        exit_regimes={CRISIS, DOWNTREND},
        exchange='binance',
        name='s53_alt_momentum_breakout',
        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=regime_size,
        breakeven_atr=0.5,
    )
