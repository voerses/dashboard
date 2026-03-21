"""
s102 MACD Squeeze with Volatility-Scaled Sizing — Surgical s98 Improvement

s98 achieved: +340.6% return, Sharpe 1.55, MaxDD -23.5%, Calmar 4.68, 126 trades
Problem: MaxDD -23.5% exceeds target of <20% (only 3.5pp over)

Philosophy: s98 is nearly perfect. Don't break what works.
Only add TWO surgical overlays:

  1. BREAKEVEN RATCHET: Move stop to entry after +0.5 ATR profit
     Proven overlay: converts ~13.7% of losing trades to scratch
     Reduces tail losses without cutting winners.

  2. VOLATILITY-SCALED SIZING: When current ATR > 1.2x rolling ATR average,
     reduce position size proportionally. High-vol periods are when the worst
     DD occurs. Scaling down during these periods specifically targets DD
     while leaving normal-vol entries at full size.

All entry signals IDENTICAL to s98.
All exit parameters IDENTICAL to s98 (2.5 ATR trail, 72h grace, 720h max).
Leverage stays 7x.

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration (identical to s98 except noted) ────────────
LEVERAGE = 7.0
WARMUP = 200
MIN_ADV_USD = 1_000_000_000     # Ultra-liquid only ($1B+ ADV)
ADX_THRESH = 20
BB_LOOKBACK = 240               # 10-day BB width average
SQUEEZE_MULT = 0.8              # BB width < 80% of average

# Trade management — IDENTICAL to s98
STOP_MULT = 99.0                # No hard stop (trail handles)
TRAIL_MULT = 2.5                # 2.5 ATR flat trail (same as s98)
TARGET_MULT = 999.0
NO_STOP_BARS = 72               # 72h grace (same as s98)
MIN_HOLD = 24
MAX_HOLD = 720                  # 30 days (same as s98)
EDGE = 0.40

# NEW: Breakeven ratchet
BREAKEVEN_ATR = 0.5             # Move stop to entry after +0.5 ATR

# NEW: Volatility-scaled sizing
VOL_LOOKBACK = 168              # 7-day ATR average
VOL_SPIKE_THRESH = 1.2          # ATR > 1.2x average = reduce size
VOL_MIN_SCALE = 0.5             # Floor: never go below 50% size


def strategy(ctx: StrategyContext) -> StrategyResult:
    """MACD squeeze with vol-scaled sizing for DD control."""
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

    # ── Liquidity filter (identical to s98) ──────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── BB squeeze filter (identical to s98) ─────────────────────
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    # ── Regime filter (identical to s98) ─────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Regime change detection (identical to s98) ───────────────
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # ── EMA alignment (identical to s98) ─────────────────────────
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    # ── MACD signals (identical to s98) ──────────────────────────
    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── Entry signals (IDENTICAL to s98) ─────────────────────────
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

    # ── NEW: Volatility-scaled position sizing ───────────────────
    atr_avg = rolling_mean(atr, VOL_LOOKBACK)
    # When ATR is elevated, scale down; when normal/low, stay at 1.0
    atr_ratio = np.where(atr_avg > 0, atr / np.maximum(atr_avg, 1e-10), 1.0)
    # Scale down when ratio > threshold, floor at VOL_MIN_SCALE
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
        name='s102_macd_squeeze_vol_scaled',
        size_multiplier=size_mult,
    )
