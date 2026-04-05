"""
S524 — Volume Zone Dual-Regime Strategy
========================================

Two sub-strategies sharing one capital pool, exploiting Bollinger Band
squeeze regimes in opposite ways:

**Sub A: Mean-Reversion inside equilibrium zone (chop)**
- Detect equilibrium: BB bandwidth below 30th percentile of 120-bar lookback
- Price touches VWAP -2sigma (long) or +2sigma (short) while squeeze is active
- RSI(14) < 30 (long) or > 70 (short) as confirmation
- Target: VWAP. Stop: 3 ATR. Hold: 24-96h.

**Sub B: Breakout from equilibrium zone**
- BB squeeze detected (BBW < 30th pct), then price closes outside BB bands
- Volume on breakout bar > 1.5x 20-bar average volume
- Direction aligned with 50-bar EMA slope AND engine regime
- Target: trailing stop at 2.5 ATR. Hold: 48-336h.

Market: PERP (bidirectional)
Leverage: 2x
Hold: 24-336h
Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, UPTREND, DOWNTREND,
                    rolling_mean, rolling_std)


# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 2.0
WARMUP = 200
MIN_ADV_USD = 500_000_000   # $500M+ daily volume

# Bollinger Bands
BB_PERIOD = 20
BB_STD_MULT = 2.0

# BB width percentile for squeeze detection
BBW_LOOKBACK = 120
SQUEEZE_PCTILE = 0.30       # Bottom 30% = squeeze

# VWAP
VWAP_PERIOD = 168           # 7-day rolling VWAP
VWAP_STD_MULT = 2.0

# RSI
RSI_PERIOD = 14
RSI_LONG_THRESH = 30
RSI_SHORT_THRESH = 70

# Volume spike
VOL_AVG_PERIOD = 20
VOL_SPIKE_MULT = 1.5

# EMA slope
EMA_PERIOD = 50
EMA_SLOPE_BARS = 5

# Breakout: squeeze lookback (was squeezing within N bars)
SQUEEZE_MEMORY = 24

# Trade management
STOP_MULT = 99.0            # No hard stop (trail handles exits)
TRAIL_MULT = 2.5            # 2.5 ATR trailing stop
TARGET_MULT = 999.0         # No fixed TP
NO_STOP_BARS = 72           # 3-day grace period
MIN_HOLD = 24
MAX_HOLD = 336
EDGE = 0.40
BREAKEVEN_ATR = 0.0


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Volume Zone dual-regime — mean-reversion in squeeze + breakout from squeeze."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    regime = ctx.regime_1h
    n = len(close)

    # Use engine-provided indicators where available
    rsi = ctx.ind_1h.get('rsi')
    bb_width_engine = ctx.ind_1h.get('bb_width')
    adx = ctx.ind_1h.get('adx')
    ema50 = ctx.ind_1h.get('ema_50')

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Regime filter (engine) ────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND

    # ── Bollinger Bands (20 period, 2 std) ────────────────────────
    bb_mid = rolling_mean(close, BB_PERIOD)
    bb_std = rolling_std(close, BB_PERIOD)
    bb_upper = bb_mid + BB_STD_MULT * bb_std
    bb_lower = bb_mid - BB_STD_MULT * bb_std

    if bb_width_engine is not None:
        bb_width = bb_width_engine
    else:
        bb_width = (bb_upper - bb_lower) / (bb_mid + 1e-10)

    # ── BB width percentile (120-bar lookback) — squeeze detection ─
    bbw_series = pd.Series(bb_width)
    bbw_pctile = bbw_series.rolling(BBW_LOOKBACK, min_periods=60).apply(
        lambda x: (x.iloc[-1] >= x).mean(), raw=False
    ).values
    squeeze = bbw_pctile < SQUEEZE_PCTILE

    # ── Rolling VWAP (168-bar) ────────────────────────────────────
    typical_price = (high + low + close) / 3.0
    tp_vol = typical_price * volume
    vwap = (pd.Series(tp_vol).rolling(VWAP_PERIOD, min_periods=84).sum().values /
            (pd.Series(volume).rolling(VWAP_PERIOD, min_periods=84).sum().values + 1e-10))
    vwap_dev = typical_price - vwap
    vwap_std = pd.Series(vwap_dev).rolling(VWAP_PERIOD, min_periods=84).std().values
    vwap_upper2 = vwap + VWAP_STD_MULT * vwap_std
    vwap_lower2 = vwap - VWAP_STD_MULT * vwap_std

    # ── RSI(14) ───────────────────────────────────────────────────
    if rsi is None:
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pd.Series(gain).ewm(span=RSI_PERIOD, adjust=False).mean().values
        avg_loss = pd.Series(loss).ewm(span=RSI_PERIOD, adjust=False).mean().values
        rsi = 100.0 - (100.0 / (1.0 + avg_gain / (avg_loss + 1e-10)))

    # ── Volume spike ──────────────────────────────────────────────
    vol_avg = pd.Series(volume).rolling(VOL_AVG_PERIOD, min_periods=10).mean().values
    vol_spike = volume > VOL_SPIKE_MULT * vol_avg

    # ── EMA 50 slope ──────────────────────────────────────────────
    if ema50 is None:
        ema50 = pd.Series(close).ewm(span=EMA_PERIOD, adjust=False).mean().values
    ema50_lagged = np.roll(ema50, EMA_SLOPE_BARS)
    ema50_lagged[:EMA_SLOPE_BARS] = ema50[:EMA_SLOPE_BARS]
    ema_slope = np.sign(ema50 - ema50_lagged)

    # ── ADX trend strength (if available) ─────────────────────────
    adx_ok = adx > 20 if adx is not None else np.ones(n, dtype=bool)

    # ── Sub A: Mean-Reversion in squeeze ──────────────────────────
    # Only MR in non-trending regimes (not up/downtrend)
    non_trending = ~uptrend & ~downtrend
    mr_long = squeeze & non_trending & (close < vwap_lower2) & (rsi < RSI_LONG_THRESH)
    mr_short = squeeze & non_trending & (close > vwap_upper2) & (rsi > RSI_SHORT_THRESH)

    # ── Sub B: Breakout from squeeze ──────────────────────────────
    was_squeezing = (pd.Series(squeeze.astype(np.float64))
                     .rolling(SQUEEZE_MEMORY, min_periods=1).max().values > 0)

    # Require regime alignment + ADX for breakout quality
    bo_long = (was_squeezing & (close > bb_upper) & vol_spike
               & (ema_slope > 0) & (uptrend | ~downtrend) & adx_ok)
    bo_short = (was_squeezing & (close < bb_lower) & vol_spike
                & (ema_slope < 0) & (downtrend | ~uptrend) & adx_ok)

    # ── Combined entry ────────────────────────────────────────────
    entry_long = (mr_long | bo_long) & liquid
    entry_short = (mr_short | bo_short) & liquid

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

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
        name='s524_volume_zone',
    )
