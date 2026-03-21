"""
s109 Volatility Breakout Momentum — Per-Token Dispersion Proxy

Fundamentally different from s98 (MACD squeeze) and s100 (cross-sectional):
Instead of cross-sectional dispersion (needs all tokens in memory), this uses
PER-TOKEN volatility metrics as a proxy for the same concept.

Core hypothesis: When a token's volatility EXPANDS from compression, it signals
breakout and trend initiation. Combined with momentum confirmation, this captures
the same edge as dispersion (high differentiation = momentum pays) without
needing cross-sectional data.

Signal architecture (3-layer confirmation):
  1. VOL BREAKOUT: BB width crosses above 120% of its 10-day average
     This is the INVERSE of BB squeeze — we want the expansion moment.
     Squeeze precedes breakout, but the ENTRY is on the expansion.

  2. MOMENTUM CONFIRMATION: Price above EMA20 (long) or below (short)
     + RSI 40-70 (not overbought, room to run)
     + MACD positive (long) or negative (short)

  3. TREND QUALITY: ADX > 20, DI confirmation, regime is UPTREND/DOWNTREND

Exit management:
  - 2.5 ATR trail (proven from s98)
  - Breakeven ratchet at 0.5 ATR
  - Crisis exit
  - 720h max hold

This is structurally different from s98:
  - s98 enters on MACD zero-cross DURING squeeze
  - s109 enters on volatility EXPANSION with momentum confirmation
  - s98 needs BB squeeze (compression) — s109 needs BB expansion (breakout)
  - Different entry timing = different trade universe = true diversification

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 6.5                   # Calibrated from s107
WARMUP = 200
MIN_ADV_USD = 500_000_000       # $500M ADV (broader than s98's $1B)
ADX_THRESH = 20
BB_LOOKBACK = 240               # 10-day BB width average
BB_EXPANSION_MULT = 1.2         # BB width > 120% of average = expansion

# RSI filter — avoid chasing overbought/oversold
RSI_LONG_MIN = 40
RSI_LONG_MAX = 70
RSI_SHORT_MIN = 30
RSI_SHORT_MAX = 60

# Trade management (proven from s98/s106/s107)
STOP_MULT = 99.0
TRAIL_MULT = 2.5
TARGET_MULT = 999.0
NO_STOP_BARS = 72
MIN_HOLD = 24
MAX_HOLD = 720
EDGE = 0.40
BREAKEVEN_ATR = 0.5

# Volatility-scaled sizing (from s102)
VOL_LOOKBACK = 168
VOL_SPIKE_THRESH = 1.3          # Scale down at higher vol (since we enter on expansion)
VOL_MIN_SCALE = 0.5


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Volatility breakout momentum — enter on vol expansion, not compression."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    rsi = ctx.ind_1h['rsi']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Volatility EXPANSION filter (opposite of s98's squeeze) ──
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    # Vol expansion: BB width just crossed above threshold
    expansion = bb_width > bb_avg * BB_EXPANSION_MULT
    expansion_prev = np.roll(expansion, 1)
    expansion_prev[0] = False
    # Fresh expansion (just crossed up)
    vol_breakout = expansion & ~expansion_prev

    # Also allow entry if already in expansion AND momentum just confirmed
    in_expansion = expansion

    # ── Trend filters ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Momentum confirmation ─────────────────────────────────────
    price_above_ema = close > ema20
    price_below_ema = close < ema20
    ema_trend_up = ema20 > ema50
    ema_trend_down = ema20 < ema50
    macd_bull = macd > 0
    macd_bear = macd < 0
    rsi_long_ok = (rsi > RSI_LONG_MIN) & (rsi < RSI_LONG_MAX)
    rsi_short_ok = (rsi > RSI_SHORT_MIN) & (rsi < RSI_SHORT_MAX)

    # MACD zero-cross for momentum timing
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── Entry signals ─────────────────────────────────────────────
    # Type 1: Vol breakout + momentum confirmation in trending regime
    entry_long_breakout = (
        vol_breakout & price_above_ema & ema_trend_up & macd_bull
        & adx_ok & long_di & liquid & uptrend & rsi_long_ok
    )
    entry_short_breakout = (
        vol_breakout & price_below_ema & ema_trend_down & macd_bear
        & adx_ok & short_di & liquid & downtrend & rsi_short_ok
    )

    # Type 2: MACD cross during ongoing vol expansion
    entry_long_macd = (
        in_expansion & macd_cross_bull & price_above_ema & ema_trend_up
        & adx_ok & long_di & liquid & uptrend & rsi_long_ok
    )
    entry_short_macd = (
        in_expansion & macd_cross_bear & price_below_ema & ema_trend_down
        & adx_ok & short_di & liquid & downtrend & rsi_short_ok
    )

    entry_long = entry_long_breakout | entry_long_macd
    entry_short = entry_short_breakout | entry_short_macd

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── Volatility-scaled position sizing ─────────────────────────
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
        name='s109_vol_breakout_momentum',
        size_multiplier=size_mult,
    )
