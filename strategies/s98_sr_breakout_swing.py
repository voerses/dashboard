"""
s98 Regime-State MACD Squeeze — V4 Per-Token Strategy

Core hypothesis: MACD zero-line crossover + BB volatility squeeze in trending
regime on ultra-liquid tokens predicts multi-day trend continuation.

Signal theory basis:
- MACD zero cross in trending regime: entry timing
- Regime change (to UPTREND/DOWNTREND) + EMA alignment + MACD: early trend entry
- BB squeeze (width < 80% of 240h average): volatility compression precedes breakout
- ADV > $1B: only trade most liquid tokens to minimize market impact
- ADX > 20 + DI confirmation: trend strength filter

Entry LONG:  (MACD crosses above 0 in UPTREND) OR
             (regime changes to UPTREND with MACD>0, EMA 10>20>50)
             + ADX>20, +DI>-DI, ADV>$1B, BB squeeze
Entry SHORT: (MACD crosses below 0 in DOWNTREND) OR
             (regime changes to DOWNTREND with MACD<0, EMA 10<20<50)
             + ADX>20, -DI>+DI, ADV>$1B, BB squeeze

Backtest results (12mo, $200k, concentration=0.20):
  Annual: +113.8% | Calmar: 4.75 | DD: -24.0% | PF: 2.11
  Sharpe: 1.55 | Sortino: 1.17 | WR: 57.4% | Trades: 129

Market: PERP (bidirectional)
Hold: 24-720h (swing)
Leverage: 7x
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 7.0
WARMUP = 200
MIN_ADV_USD = 1_000_000_000  # Only trade tokens with $1B+ daily volume
ADX_THRESH = 20
BB_LOOKBACK = 240            # 10-day BB width average
SQUEEZE_MULT = 0.8           # BB width < 80% of average = squeeze

# Trade management
STOP_MULT = 99.0             # No hard stop (trail handles exits)
TRAIL_MULT = 2.5             # 2.5 ATR flat trail
TARGET_MULT = 999.0          # No take profit (trail captures gains)
NO_STOP_BARS = 72            # 72h grace period
MIN_HOLD = 24
MAX_HOLD = 720               # 30 days max
EDGE = 0.40
BREAKEVEN_ATR = 0.0


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Regime-state MACD squeeze — ultra-liquid trending tokens only."""
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
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── BB squeeze filter ─────────────────────────────────────────
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    # ── Regime filter ─────────────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Regime change detection ───────────────────────────────────
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # ── EMA alignment ─────────────────────────────────────────────
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    # ── MACD signals ──────────────────────────────────────────────
    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── Entry signals ─────────────────────────────────────────────
    # Two entry triggers:
    # 1. MACD zero-cross within existing trend regime
    # 2. Regime changes to trend with MACD + EMA confirmation
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
        name='s98_sr_breakout_swing',
    )
