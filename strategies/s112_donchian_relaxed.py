"""
s112 Donchian Breakout Relaxed — High-volume iteration of s110

s110 achieved: +5.4%, 66% WR, PF 3.21, Calmar 3.23 but only 59 trades.
Signal quality is excellent — need more entries by relaxing filters.

Changes from s110:
  1. NO TAKER FILTER: Removes the biggest restriction. Taker used for SIZING only.
  2. BROADER REGIME: Allow UPTREND + RANGE (not just UPTREND) for longs
  3. LOWER ADV: $200M (from $500M) — more tokens eligible
  4. BOTH FRESH + CONTINUATION: Enter on fresh breakout OR when at channel extreme
     with MACD confirmation (catches continuation moves)
  5. HIGHER LEVERAGE: 7x (justified by 66% WR signal quality)

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 7.0                   # Higher (justified by s110's 66% WR)
WARMUP = 200
MIN_ADV_USD = 200_000_000       # Broader universe ($200M)
ADX_THRESH = 20

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
    """Donchian breakout with relaxed filters for more trade volume."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema20 = ctx.ind_1h['ema_20']
    donch_high = ctx.ind_1h['donch_high']
    donch_low = ctx.ind_1h['donch_low']
    taker = ctx.ind_1h['taker']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Donchian breakout ─────────────────────────────────────────
    at_high = close >= donch_high
    at_low = close <= donch_low
    prev_high = np.roll(at_high, 1)
    prev_high[0] = False
    prev_low = np.roll(at_low, 1)
    prev_low[0] = False
    fresh_break_high = at_high & ~prev_high
    fresh_break_low = at_low & ~prev_low

    # MACD cross for continuation entries during channel ride
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── Trend filters ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    range_regime = regime == RANGE
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Entry signals ─────────────────────────────────────────────
    # Type 1: Fresh Donchian breakout in trending regime
    entry_long_break = (
        fresh_break_high & (uptrend | range_regime)
        & adx_ok & long_di & liquid
    )
    entry_short_break = (
        fresh_break_low & downtrend
        & adx_ok & short_di & liquid
    )

    # Type 2: MACD cross while riding Donchian channel (continuation)
    entry_long_cont = (
        at_high & macd_cross_bull & uptrend
        & adx_ok & long_di & liquid
    )
    entry_short_cont = (
        at_low & macd_cross_bear & downtrend
        & adx_ok & short_di & liquid
    )

    entry_long = entry_long_break | entry_long_cont
    entry_short = entry_short_break | entry_short_cont

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── Taker-informed sizing (taker as size lever, not entry filter) ──
    size_mult = np.ones(n, dtype=np.float64)
    # Boost when taker confirms direction
    taker_confirms_long = (taker > 0.52) & entry_long
    taker_confirms_short = (taker < 0.48) & entry_short
    size_mult[taker_confirms_long] = 1.3
    size_mult[taker_confirms_short] = 1.3

    # Vol-scaled sizing
    atr_avg = rolling_mean(atr, VOL_LOOKBACK)
    atr_ratio = np.where(atr_avg > 0, atr / np.maximum(atr_avg, 1e-10), 1.0)
    vol_scale = np.where(
        atr_ratio > VOL_SPIKE_THRESH,
        np.maximum(VOL_SPIKE_THRESH / atr_ratio, VOL_MIN_SCALE),
        1.0
    )
    size_mult *= vol_scale
    size_mult = np.clip(size_mult, 0.3, 1.5)

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
        name='s112_donchian_relaxed',
        size_multiplier=size_mult,
    )
