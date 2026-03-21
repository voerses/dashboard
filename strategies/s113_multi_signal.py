"""
s113 Multi-Signal Momentum — Three Independent Entry Pathways

Different architecture: instead of ONE signal type with heavy filtering,
use MULTIPLE independent signals that each capture different alpha:

  Signal A — MACD SQUEEZE (proven from s98):
    MACD zero-cross + BB squeeze + regime + ADX + DI

  Signal B — RSI PULLBACK (mean reversion in trend):
    RSI < 35 (oversold) + UPTREND regime + price > EMA50 + ADX > 20
    = Buy-the-dip in macro uptrend
    RSI > 65 (overbought) + DOWNTREND + price < EMA50 + ADX > 20
    = Sell-the-rip in macro downtrend

  Signal C — DONCHIAN + TAKER (institutional breakout):
    Fresh Donchian high + taker > 0.53 + UPTREND + ADX > 25
    This is selective (s110 showed 66% WR) but additive

Each signal fires independently → more entries without lowering quality.
All share the same exit management (proven 2.5 ATR trail + breakeven).

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
MIN_ADV_USD = 500_000_000       # $500M ADV
ADX_THRESH = 20
ADX_STRONG = 25                  # Stronger threshold for Donchian signal
BB_LOOKBACK = 240
SQUEEZE_MULT = 0.8

# RSI thresholds for pullback entries
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65

# Taker threshold for Donchian confirmation
TAKER_LONG_THRESH = 0.53

# Trade management (proven)
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
    """Multi-signal: MACD squeeze + RSI pullback + Donchian breakout."""
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
    bb_width = ctx.ind_1h['bb_width']
    donch_high = ctx.ind_1h['donch_high']
    donch_low = ctx.ind_1h['donch_low']
    taker = ctx.ind_1h['taker']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Common filters ────────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    adx_strong = adx > ADX_STRONG
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Signal A: MACD SQUEEZE (identical to s98) ────────────────
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

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

    sig_a_long = (
        ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend))
        & adx_ok & long_di & liquid & squeeze
    )
    sig_a_short = (
        ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend))
        & adx_ok & short_di & liquid & squeeze
    )

    # ── Signal B: RSI PULLBACK (buy dip in uptrend) ──────────────
    rsi_oversold = rsi < RSI_OVERSOLD
    rsi_overbought = rsi > RSI_OVERBOUGHT
    price_above_ema50 = close > ema50
    price_below_ema50 = close < ema50

    sig_b_long = (
        rsi_oversold & uptrend & price_above_ema50
        & adx_ok & long_di & liquid
    )
    sig_b_short = (
        rsi_overbought & downtrend & price_below_ema50
        & adx_ok & short_di & liquid
    )

    # ── Signal C: DONCHIAN + TAKER (institutional breakout) ──────
    at_high = close >= donch_high
    at_low = close <= donch_low
    prev_high = np.roll(at_high, 1)
    prev_high[0] = False
    prev_low = np.roll(at_low, 1)
    prev_low[0] = False
    fresh_break_high = at_high & ~prev_high
    fresh_break_low = at_low & ~prev_low

    taker_buy = taker > TAKER_LONG_THRESH
    taker_sell = taker < (1.0 - TAKER_LONG_THRESH)

    sig_c_long = (
        fresh_break_high & taker_buy & uptrend
        & adx_strong & long_di & liquid
    )
    sig_c_short = (
        fresh_break_low & taker_sell & downtrend
        & adx_strong & short_di & liquid
    )

    # ── Combined entry (OR of all signals) ────────────────────────
    entry_long = sig_a_long | sig_b_long | sig_c_long
    entry_short = sig_a_short | sig_b_short | sig_c_short

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
        name='s113_multi_signal',
        size_multiplier=size_mult,
    )
