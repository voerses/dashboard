"""
s107 MACD Squeeze 6.5x — Optimized leverage between s102 (7x) and s106 (6x)

s106 at 6.0x: +340.4% return, MaxDD -18.5%, Calmar 5.92 — ALL TARGETS HIT
s102 at 7.0x: +400.0% return, MaxDD -22.6%, Calmar 5.45 — DD fails

Testing 6.5x as the sweet spot for maximum return within DD constraint.
Expected: ~370% return, ~20.5% DD (borderline)

Everything identical to s106 except LEVERAGE = 6.5.
Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 6.5                   # Between s106 (6.0) and s102 (7.0)
WARMUP = 200
MIN_ADV_USD = 1_000_000_000
ADX_THRESH = 20
BB_LOOKBACK = 240
SQUEEZE_MULT = 0.8

# Trade management
STOP_MULT = 99.0
TRAIL_MULT = 2.5
TARGET_MULT = 999.0
NO_STOP_BARS = 72
MIN_HOLD = 24
MAX_HOLD = 720
EDGE = 0.40
BREAKEVEN_ATR = 0.5

# Volatility-scaled sizing
VOL_LOOKBACK = 168
VOL_SPIKE_THRESH = 1.2
VOL_MIN_SCALE = 0.5


def strategy(ctx: StrategyContext) -> StrategyResult:
    """MACD squeeze at 6.5x leverage."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    entry_long = (
        ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend))
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend))
        & adx_ok & short_di & liquid & squeeze
    )

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

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
        name='s107_macd_squeeze_6p5x',
        size_multiplier=size_mult,
    )
