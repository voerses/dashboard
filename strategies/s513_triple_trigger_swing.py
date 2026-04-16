"""
s513 Triple-Trigger Regime Swing — Enhanced s98
================================================

Builds on s98's regime-state MACD squeeze framework with two additional
entry triggers that fire at different times, increasing trade frequency
from ~149 to ~196 trades/year while maintaining quality.

Entry triggers (ANY of these, within regime filters):
  1. MACD zero-cross in existing trend regime (original s98)
  2. RSI pullback recovery: RSI crosses back above 40 from below in
     UPTREND (oversold bounce in bull) / below 60 from above in
     DOWNTREND (overbought fade in bear)
  3. Donchian 20-day breakout: close exceeds prior bar's 480h high in
     UPTREND / breaks below 480h low in DOWNTREND

All triggers require the same quality filters:
  - Regime: UPTREND or DOWNTREND (with DI confirmation)
  - ADX > 20 (trend strength)
  - ADV > $1B (liquidity — ultra-liquid tokens only)
  - BB squeeze (bb_width < 80% of 240h average — volatility compression)

Leverage reduced from 7x to 3x (Calmar-optimal):
  7x: Sharpe 1.99, Calmar 10.27, MaxDD -36.7%
  3x: Sharpe 2.56, Calmar 11.87, MaxDD -16.6%

Backtest (L12M, 3x, $100k):
  Return: +194.8% | Sharpe: 2.56 | Calmar: 11.87 | MaxDD: -16.6%
  Trades: 194 | WR: 54.6% | Sortino: 2.92

Parameter sensitivity (at 3x):
  TRAIL_MULT: robust (±17%). NO_STOP_BARS: fragile (±60-74%).
  Walk-forward validation required before Gate 5.

Market: PERP (bidirectional)
Hold: 24-720h (swing)
Leverage: 3x (Calmar-optimal)
Status: EXPERIMENTAL (Gate 3 prototype)
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)

REQUIRED_PLUGINS = []  # uses only built-in indicators (macd, rsi, adx, ema, bb)

# Strategy-level CRISIS exit (replaces engine's exit_regimes={CRISIS})
from v4.exit_handlers import ExitCheck as _ExitCheck

def _crisis_exit(pos, bar):
    if bar.bars_held > 6 and bar.regime == 0:
        return _ExitCheck(should_exit=True, reason="crisis")
    return None

PORTFOLIO_CONFIG = {
    "exit_check_fn": _crisis_exit,
}

# ── Configuration ────────────────────────────────────────────────
LEVERAGE = 3.0
WARMUP = 200
MIN_ADV_USD = 1_000_000_000   # Only trade tokens with $1B+ daily volume
ADX_THRESH = 20
BB_LOOKBACK = 240             # 10-day BB width average
SQUEEZE_MULT = 0.8            # BB width < 80% of average = squeeze

# RSI pullback thresholds
RSI_LONG_THRESH = 40          # RSI crosses above this from below = long pullback
RSI_SHORT_THRESH = 60         # RSI crosses below this from above = short pullback

# Donchian channel
DONCHIAN_PERIOD = 480         # 20 days in hours
DONCHIAN_MIN_PERIODS = 240    # Minimum 10 days for rolling calc

# Trade management
STOP_MULT = 99.0              # No hard stop (trail handles exits)
TRAIL_MULT = 2.5
TARGET_MULT = 999.0           # No take profit (trail captures gains)
NO_STOP_BARS = 72             # 72h grace period
MIN_HOLD = 24
MAX_HOLD = 720                # 30 days max
EDGE = 0.40
BREAKEVEN_ATR = 0.0


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Triple-trigger regime swing — MACD + RSI pullback + Donchian breakout."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
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
    regime = ctx.regime_1h

    # ── Liquidity filter ──────────────────────────────────────────
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── BB squeeze filter ─────────────────────────────────────────
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    # ── Regime + trend filters ────────────────────────────────────
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # Common filter stack (all triggers must pass these)
    common_long = uptrend & adx_ok & long_di & liquid & squeeze
    common_short = downtrend & adx_ok & short_di & liquid & squeeze

    # ── Trigger 1: MACD zero-cross (original s98) ────────────────
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

    macd_long = (regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)
    macd_short = (regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)

    # ── Trigger 2: RSI pullback recovery ─────────────────────────
    rsi_prev = np.roll(rsi, 1)
    rsi_prev[0] = 50.0
    rsi_pullback_long = (rsi > RSI_LONG_THRESH) & (rsi_prev <= RSI_LONG_THRESH)
    rsi_pullback_short = (rsi < RSI_SHORT_THRESH) & (rsi_prev >= RSI_SHORT_THRESH)

    # ── Trigger 3: Donchian breakout ─────────────────────────────
    donchian_high = pd.Series(high).rolling(
        DONCHIAN_PERIOD, min_periods=DONCHIAN_MIN_PERIODS
    ).max().values
    donchian_low = pd.Series(low).rolling(
        DONCHIAN_PERIOD, min_periods=DONCHIAN_MIN_PERIODS
    ).min().values
    donchian_high_prev = np.roll(donchian_high, 1)
    donchian_low_prev = np.roll(donchian_low, 1)
    donchian_break_long = close > donchian_high_prev
    donchian_break_short = close < donchian_low_prev

    # ── Combined entry: ANY trigger + ALL filters ─────────────────
    entry_long = (macd_long | rsi_pullback_long | donchian_break_long) & common_long
    entry_short = (macd_short | rsi_pullback_short | donchian_break_short) & common_short

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
        # CRISIS exit moved to PORTFOLIO_CONFIG exit_check_fn
        breakeven_atr=BREAKEVEN_ATR,
        exchange='binance',
        name='s513_triple_trigger_swing',
    )
