"""
s115 Trend Pullback — Buy the Dip in Strong Trends

Completely different entry paradigm from s98:
  - s98: Enters at MACD zero-cross during BB squeeze (breakout timing)
  - s115: Enters on PULLBACK to EMA20 during strong uptrend (dip buying)

Entry LONG:
  1. Strong uptrend: EMA10 > EMA20 > EMA50 (aligned)
  2. Price TOUCHES EMA20 from above (pullback)
  3. ADX > 25 (strong trend, pullback is healthy not reversal)
  4. RSI 35-55 (oversold enough to bounce, not collapsing)
  5. MACD still positive (trend intact despite pullback)
  6. Regime is UPTREND

Entry SHORT:
  Mirror for downtrends: price bounces up to EMA20 from below.

This captures a different set of trades than s98 — continuation moves
after healthy pullbacks, not breakout moves from compression.

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
ADX_THRESH = 25                  # Strong trend only

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
    """Trend pullback — enter on dip to EMA20 in strong trend."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    rsi = ctx.ind_1h['rsi']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Trend filters ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── EMA alignment (strong trend) ──────────────────────────────
    ema_bull_aligned = (ema10 > ema20) & (ema20 > ema50)
    ema_bear_aligned = (ema10 < ema20) & (ema20 < ema50)

    # ── Pullback detection ────────────────────────────────────────
    # Long pullback: price touches or crosses below EMA20 from above
    # We detect when close is within 0.3% of EMA20 or just crossed below
    close_near_ema20 = np.abs(close - ema20) / np.maximum(ema20, 1e-10) < 0.003
    close_below_ema20 = close < ema20
    close_above_ema20 = close > ema20
    prev_above = np.roll(close_above_ema20, 1)
    prev_above[0] = False
    prev_below = np.roll(close_below_ema20, 1)
    prev_below[0] = False

    # Touch from above: was above, now near or crossing below
    touch_from_above = prev_above & (close_near_ema20 | close_below_ema20)
    # Touch from below: was below, now near or crossing above
    touch_from_below = prev_below & (close_near_ema20 | close_above_ema20)

    # ── Momentum confirmation ─────────────────────────────────────
    macd_bull = macd > 0          # Trend intact despite pullback
    macd_bear = macd < 0
    rsi_long_ok = (rsi > 35) & (rsi < 55)  # Pulled back but not collapsing
    rsi_short_ok = (rsi > 45) & (rsi < 65)

    # ── Entry signals ─────────────────────────────────────────────
    entry_long = (
        touch_from_above & ema_bull_aligned & macd_bull
        & uptrend & adx_ok & long_di & liquid & rsi_long_ok
    )
    entry_short = (
        touch_from_below & ema_bear_aligned & macd_bear
        & downtrend & adx_ok & short_di & liquid & rsi_short_ok
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
        name='s115_trend_pullback',
        size_multiplier=size_mult,
    )
