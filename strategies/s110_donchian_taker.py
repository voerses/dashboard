"""
s110 Donchian Breakout with Taker Confirmation

Completely different signal architecture from s98 (MACD squeeze):
  - s98: MACD zero-cross during volatility compression
  - s110: Price channel breakout with institutional flow confirmation

Entry LONG:  Price breaks above 20-bar Donchian high (new high)
             + taker buy ratio > 0.52 (institutional buying pressure)
             + regime is UPTREND + ADX > 20 + +DI > -DI
             + ADV > $500M (liquid)

Entry SHORT: Price breaks below 20-bar Donchian low
             + taker buy ratio < 0.48 (selling pressure)
             + regime is DOWNTREND + ADX > 20 + -DI > +DI

This is classic turtle-trading adapted for crypto with modern microstructure
data (taker ratio) as confirmation.

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 6.5
WARMUP = 200
MIN_ADV_USD = 500_000_000
ADX_THRESH = 20
TAKER_LONG_THRESH = 0.52        # Buying pressure
TAKER_SHORT_THRESH = 0.48       # Selling pressure

# Trade management
STOP_MULT = 99.0
TRAIL_MULT = 2.5
TARGET_MULT = 999.0
NO_STOP_BARS = 72
MIN_HOLD = 24
MAX_HOLD = 720
EDGE = 0.40
BREAKEVEN_ATR = 0.5

# Vol-scaled sizing
VOL_LOOKBACK = 168
VOL_SPIKE_THRESH = 1.2
VOL_MIN_SCALE = 0.5


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Donchian breakout with taker ratio confirmation."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    donch_high = ctx.ind_1h['donch_high']
    donch_low = ctx.ind_1h['donch_low']
    taker = ctx.ind_1h['taker']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Donchian breakout detection ───────────────────────────────
    # Fresh breakout: price at new 20-bar high/low
    breakout_high = close >= donch_high
    breakout_low = close <= donch_low
    # Previous bar wasn't at channel extreme (fresh break)
    prev_high = np.roll(breakout_high, 1)
    prev_high[0] = False
    prev_low = np.roll(breakout_low, 1)
    prev_low[0] = False
    fresh_break_high = breakout_high & ~prev_high
    fresh_break_low = breakout_low & ~prev_low

    # ── Taker flow confirmation ───────────────────────────────────
    taker_buy = taker > TAKER_LONG_THRESH
    taker_sell = taker < TAKER_SHORT_THRESH

    # ── Trend filters ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Entry signals ─────────────────────────────────────────────
    entry_long = (
        fresh_break_high & taker_buy
        & uptrend & adx_ok & long_di & liquid
    )
    entry_short = (
        fresh_break_low & taker_sell
        & downtrend & adx_ok & short_di & liquid
    )

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── Vol-scaled sizing ─────────────────────────────────────────
    atr_avg = rolling_mean(atr, VOL_LOOKBACK)
    atr_ratio = np.where(atr_avg > 0, atr / np.maximum(atr_avg, 1e-10), 1.0)
    size_mult = np.where(
        atr_ratio > VOL_SPIKE_THRESH,
        np.maximum(VOL_SPIKE_THRESH / atr_ratio, VOL_MIN_SCALE),
        1.0
    )

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        exit_regimes={CRISIS},
        breakeven_atr=BREAKEVEN_ATR,
        exchange='binance',
        name='s110_donchian_taker',
        size_multiplier=size_mult,
    )
