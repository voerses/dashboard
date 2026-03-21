"""
s101 MACD Squeeze with Drawdown Control — Improved s98

s98 achieved: +340.6% return, Sharpe 1.55, MaxDD -23.5%, Calmar 4.68, 126 trades
Problem: MaxDD -23.5% exceeds target of <20%

Changes from s98:
  1. REGIME-SCALED LEVERAGE: 7x in strong UPTREND, 5x in DOWNTREND, 4x other
     s98 used flat 7x everywhere — scaling down in weaker regimes reduces DD
  2. ADX-SCALED LEVERAGE: Higher ADX = stronger trend = more leverage
     ADX > 30: full leverage, ADX 20-30: 70% leverage
  3. TIGHTER TRAIL: 2.0 ATR (from 2.5) — cuts losers faster
  4. BREAKEVEN RATCHET: Move stop to entry after +0.5 ATR profit
     Proven overlay: converts ~13.7% of losing trades to scratch
  5. SHORTER MAX HOLD: 504h (21d) from 720h (30d) — force stale exits sooner
  6. CRISIS + QUIET regime exit: Exit on QUIET too (low vol = squeeze exhausted)

All entry signals IDENTICAL to s98 (proven working):
  - MACD zero-cross + BB squeeze + ADX >20 + DI confirmation + ADV >$1B
  - Regime change + EMA alignment trigger

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)


# ── Configuration ────────────────────────────────────────────────
WARMUP = 200
MIN_ADV_USD = 1_000_000_000     # Ultra-liquid only ($1B+ ADV)
ADX_THRESH = 20
BB_LOOKBACK = 240               # 10-day BB width average
SQUEEZE_MULT = 0.8              # BB width < 80% of average

# Regime-dependent leverage (key DD control lever)
LEVERAGE_UPTREND = 7.0          # Full leverage in strong uptrend
LEVERAGE_DOWNTREND = 5.0        # Moderate for shorts in downtrend
LEVERAGE_OTHER = 4.0            # Conservative in other regimes

# ADX-dependent leverage scaling
ADX_FULL_THRESH = 30            # Full leverage above this ADX
ADX_SCALE_BELOW = 0.7           # 70% leverage when ADX 20-30

# Trade management — tighter than s98
STOP_MULT = 99.0                # No hard stop (trail handles)
TRAIL_MULT = 2.0                # 2.0 ATR trail (tighter than s98's 2.5)
TARGET_MULT = 999.0
NO_STOP_BARS = 48               # 48h grace (shorter than s98's 72h)
MIN_HOLD = 24
MAX_HOLD = 504                  # 21 days (shorter than s98's 30d)
EDGE = 0.40
BREAKEVEN_ATR = 0.5             # Breakeven ratchet at +0.5 ATR


def strategy(ctx: StrategyContext) -> StrategyResult:
    """MACD squeeze with regime-adaptive leverage for DD control."""
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

    # ── Entry signals (identical to s98) ─────────────────────────
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

    # ── Regime-dependent leverage (per-bar sizing) ────────────────
    base_lev = np.full(n, LEVERAGE_OTHER, dtype=np.float64)
    base_lev[uptrend] = LEVERAGE_UPTREND
    base_lev[downtrend] = LEVERAGE_DOWNTREND

    # ADX scaling: reduce leverage when ADX is weak (20-30)
    adx_scale = np.where(adx > ADX_FULL_THRESH, 1.0, ADX_SCALE_BELOW)
    size_mult = (base_lev * adx_scale) / LEVERAGE_UPTREND  # Normalize to [0, 1]

    # Use max leverage as the StrategyResult.leverage, then scale via size_multiplier
    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE_UPTREND,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        exit_regimes={CRISIS, QUIET},
        breakeven_atr=BREAKEVEN_ATR,
        exchange='binance',
        name='s101_macd_squeeze_dd_control',
        size_multiplier=size_mult,
    )
