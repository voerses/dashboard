"""
s111 Funding Divergence Momentum

Completely different alpha source: funding rate disagreement as "dispersion."
When funding diverges from price action, it signals a crowded trade that
will unwind — creating momentum alpha.

Funding IS a dispersion measure — it captures the disagreement between
spot (price) and futures (sentiment) markets.

Entry LONG:  Funding rate is NEGATIVE (shorts paying longs = crowd is short)
             BUT price is trending UP (EMA alignment + MACD > 0)
             This = shorts are wrong, will be forced to cover = momentum fuel

Entry SHORT: Funding rate is POSITIVE (longs paying shorts = crowd is long)
             BUT price is trending DOWN (EMA alignment + MACD < 0)
             This = longs are wrong, will be forced to exit = momentum fuel

Key filters:
  - Regime must confirm (UPTREND for longs, DOWNTREND for shorts)
  - ADX > 20 + DI confirmation (trend quality)
  - ADV > $500M (liquid)
  - BB squeeze (volatility compression = buildup before move)

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
FUNDING_WINDOW = 24             # 1-day average funding
FUNDING_LONG_THRESH = -0.00005  # Funding below this = shorts paying (negative)
FUNDING_SHORT_THRESH = 0.00005  # Funding above this = longs paying (positive)
BB_LOOKBACK = 240
SQUEEZE_MULT = 0.8              # BB width < 80% avg = squeeze

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
    """Funding divergence momentum — trade against crowded positioning."""
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

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── Funding rate signal ───────────────────────────────────────
    funding = ctx.funding_1h
    if funding is None:
        # No funding data — can't run this strategy
        entry_mask = np.zeros(n, dtype=bool)
        direction = np.zeros(n, dtype=np.int8)
        return StrategyResult(
            entry_mask=entry_mask, direction=direction,
            market_type=MarketType.PERP, leverage=LEVERAGE,
            stop_mult=STOP_MULT, trail_mult=TRAIL_MULT,
            target_mult=TARGET_MULT, no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD, max_hold=MAX_HOLD, edge=EDGE,
            exit_regimes={CRISIS}, exchange='binance',
            name='s111_funding_divergence',
        )

    funding_avg = rolling_mean(funding, FUNDING_WINDOW)
    funding_negative = funding_avg < FUNDING_LONG_THRESH   # Shorts paying
    funding_positive = funding_avg > FUNDING_SHORT_THRESH  # Longs paying

    # ── BB squeeze (compression before breakout) ──────────────────
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
    macd_bull = macd > 0
    macd_bear = macd < 0

    # ── MACD cross for timing ─────────────────────────────────────
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── Regime change detection ───────────────────────────────────
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # ── Entry signals ─────────────────────────────────────────────
    # Core: funding diverges from trend direction
    # Entry timing: MACD cross or regime change (proven from s98)
    entry_long = (
        funding_negative  # Crowd is short (shorts paying)
        & ((macd_cross_bull & uptrend) | (regime_change_up & macd_bull & ema_bull))
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        funding_positive  # Crowd is long (longs paying)
        & ((macd_cross_bear & downtrend) | (regime_change_down & macd_bear & ema_bear))
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

    # Boost size when funding divergence is extreme
    funding_extreme = np.abs(funding_avg) > 0.0002  # Very extreme funding
    size_mult = np.where(funding_extreme, size_mult * 1.3, size_mult)
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
        name='s111_funding_divergence',
        size_multiplier=size_mult,
    )
