"""
s117 BB Squeeze + EMA Cross — Testing if Alpha is in the Squeeze Filter

Hypothesis: s98's alpha comes from the BB SQUEEZE filter, not the MACD
timing. If true, swapping MACD for EMA cross should still work.

Entry LONG:
  - BB squeeze (same as s98: width < 80% of 240h average)
  - EMA10 crosses above EMA20 (golden cross — different timing from MACD zero-cross)
  - UPTREND regime + ADX > 20 + +DI > -DI + ADV > $1B

Entry SHORT: Mirror for downtrends.

If this works: the squeeze filter is the alpha source (validates concept)
If this fails: MACD zero-cross timing is the unique edge (s98 is irreplaceable)

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
MIN_ADV_USD = 1_000_000_000     # Same as s98
ADX_THRESH = 20
BB_LOOKBACK = 240
SQUEEZE_MULT = 0.8

# Trade management (identical to s98)
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
    """BB squeeze + EMA cross — testing squeeze filter hypothesis."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── BB squeeze filter (identical to s98) ──────────────────────
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    # ── Trend filters ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── EMA alignment ─────────────────────────────────────────────
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    # ── EMA CROSS (replacement for MACD zero-cross) ──────────────
    ema10_prev = np.roll(ema10, 1)
    ema20_prev = np.roll(ema20, 1)
    ema10_prev[0] = np.nan
    ema20_prev[0] = np.nan

    ema_cross_bull = (ema10 > ema20) & (ema10_prev <= ema20_prev)
    ema_cross_bear = (ema10 < ema20) & (ema10_prev >= ema20_prev)

    # ── Regime change detection (same as s98) ─────────────────────
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # ── Entry signals ─────────────────────────────────────────────
    # Structure mirrors s98 but uses EMA cross instead of MACD cross
    entry_long = (
        ((regime_change_up & (ema10 > ema20) & ema_bull) | (ema_cross_bull & uptrend))
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        ((regime_change_down & (ema10 < ema20) & ema_bear) | (ema_cross_bear & downtrend))
        & adx_ok & short_di & liquid & squeeze
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
        name='s117_squeeze_ema_cross',
        size_multiplier=size_mult,
    )
