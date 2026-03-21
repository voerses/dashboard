"""
s116 Volume Spike Breakout — Institutional Flow Detection

Different alpha source: abnormal volume as a signal for institutional entry.
When volume spikes to 2x+ normal, it often signals smart money positioning.

Entry LONG:
  1. vol_ratio > 2.0 (volume spike — 2x recent average)
  2. Close > EMA20 (price confirms upward direction)
  3. MACD > 0 (momentum positive)
  4. Regime is UPTREND
  5. ADX > 20 + DI confirmation
  6. ADV > $500M (liquid)

Entry SHORT:
  Mirror: volume spike + price below EMA20 + MACD < 0 + DOWNTREND

This is fundamentally different from s98 because the PRIMARY signal
is volume (microstructure), not MACD (momentum oscillator).

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


LEVERAGE = 6.5
WARMUP = 200
MIN_ADV_USD = 500_000_000
ADX_THRESH = 20
VOL_RATIO_THRESH = 2.0          # Volume 2x above recent average

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
    """Volume spike breakout — enter on institutional volume."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema20 = ctx.ind_1h['ema_20']
    vol_ratio = ctx.ind_1h['vol_ratio']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Volume spike detection ────────────────────────────────────
    vol_spike = vol_ratio > VOL_RATIO_THRESH

    # ── Trend filters ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Price direction ───────────────────────────────────────────
    price_above_ema = close > ema20
    price_below_ema = close < ema20
    macd_bull = macd > 0
    macd_bear = macd < 0

    # ── Entry signals ─────────────────────────────────────────────
    entry_long = (
        vol_spike & price_above_ema & macd_bull
        & uptrend & adx_ok & long_di & liquid
    )
    entry_short = (
        vol_spike & price_below_ema & macd_bear
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
        name='s116_volume_spike',
        size_multiplier=size_mult,
    )
