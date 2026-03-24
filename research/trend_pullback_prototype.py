"""
Trend+Pullback Strategy Prototype — BTC Long-Only (Spot)
=========================================================

Walk-forward validated signal (R99): BTC long passes both WF agents.
Signal: Daily EMA(20) > EMA(50) [uptrend] + 4h RSI(14) crosses UP through 40 [pullback entry].
Exit: 2x ATR(14) trailing stop, 168h max hold.

Architecture from R104: uses pre-computed 4h RSI aligned to 1h, per-bar direction.

OOS metrics (R99 reconciled):
  - BTC long: 6-9/10 WF windows positive
  - Mean OOS Sharpe: 1.41-8.64 (depending on window config)
  - ~3-4 trades/month

This is a RESEARCH PROTOTYPE. Production version will be strategies/sNNN_*.py after gate validation.
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


# =============================================================================
# Constants (from R99 walk-forward validated parameters)
# =============================================================================

RSI_LONG_THRESH = 40.0      # 4h RSI cross-up threshold for long entry
WARMUP_BARS = 1200           # 50 days × 24h (for daily EMA50 warmup)
TRAIL_MULT = 2.0             # 2x ATR trailing stop (R99 validated)
STOP_MULT = 3.0              # Initial stop: 3x ATR (wide, pullback needs room)
MAX_HOLD = 168               # 7 days in hours (R99 validated)
MIN_HOLD = 4                 # 4h minimum (one 4h bar cycle)
NO_STOP_BARS = 4             # 4h grace before stop activates
EDGE = 0.35                  # Conservative Kelly edge


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Trend+Pullback: Daily uptrend + 4h RSI pullback entry. BTC long-only."""
    n = len(ctx.ind_1h['close'])

    # ── BTC-ONLY FILTER ──────────────────────────────────────────────
    if ctx.ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='trend_pullback',
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=999.0,
            no_stop_bars=NO_STOP_BARS,
            max_hold=MAX_HOLD,
            min_hold=MIN_HOLD,
            edge=EDGE,
            exit_regimes={CRISIS},
            market_type=MarketType.SPOT,
            leverage=1.0,
            size_multiplier=np.zeros(n, dtype=np.float64),
            conviction_score=np.zeros(n, dtype=np.float64),
            breakeven_atr=0.0,
        )

    # ── LAYER 1: REGIME FILTER ────────────────────────────────────────
    regime_ok = ctx.regime_1h != CRISIS

    # ── LAYER 2: DAILY TREND ALIGNMENT ────────────────────────────────
    # Long when daily EMA(20) > EMA(50) — same base as V3 but used differently
    ema_20_d = ctx.ind_d['ema_20']
    ema_50_d = ctx.ind_d['ema_50']
    uptrend_daily = ema_20_d > ema_50_d
    uptrend_1h = ctx.align_daily_to_1h(uptrend_daily.astype(np.float64)) > 0.5

    # ── LAYER 3: 4H RSI PULLBACK CROSS ───────────────────────────────
    # Core signal: RSI(14) on 4h bars crosses UP through RSI_LONG_THRESH
    # Compute cross and conviction on 4h bars BEFORE alignment (single align call)
    rsi_4h = ctx.ind_4h['rsi']
    rsi_4h_prev = np.roll(rsi_4h, 1)
    rsi_4h_prev[0] = 50.0  # neutral default for first bar

    # Cross detection on 4h timeframe
    cross_up_4h = (rsi_4h_prev < RSI_LONG_THRESH) & (rsi_4h >= RSI_LONG_THRESH)

    # Conviction on 4h: deeper pullback = higher conviction
    # RSI at 30 → 1.0, RSI at 40 → 0.5, RSI at 50 → 0.0
    conviction_4h = np.clip((RSI_LONG_THRESH + 10 - rsi_4h) / 20.0, 0.0, 1.0)
    # Only set conviction on cross bars, zero elsewhere
    conviction_4h = np.where(cross_up_4h, conviction_4h, 0.0)

    # Single alignment call for cross+conviction combined
    # Pack into one array: cross as 1.0/0.0 + conviction as decimal
    cross_up_1h = ctx.align_4h_to_1h(cross_up_4h.astype(np.float64)) > 0.5
    conviction_1h = ctx.align_4h_to_1h(conviction_4h)

    # Only fire on the FIRST 1h bar of the 4h cross (avoid 4 duplicate entries)
    cross_up_1h_prev = np.roll(cross_up_1h, 1)
    cross_up_1h_prev[0] = False
    cross_up_pulse = cross_up_1h & ~cross_up_1h_prev

    # ── LAYER 4: VOLUME CONFIRMATION (light) ──────────────────────────
    # Require volume above 50% of 20-bar average (not aggressive)
    vol_ratio = ctx.ind_1h['vol_ratio']
    vol_ok = vol_ratio > 0.5

    # ── COMPOSE ENTRY ─────────────────────────────────────────────────
    entry_mask = regime_ok & uptrend_1h & cross_up_pulse & vol_ok

    # Warmup guard
    entry_mask[:WARMUP_BARS] = False

    # Liquidity filter
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── DIRECTION (long-only for now) ─────────────────────────────────
    direction = np.ones(n, dtype=np.int8)  # All long

    # ── SIZING: use positioning + VRP overlays if available ───────────
    pos_mult = ctx.custom.get('pos_mult', np.ones(n, dtype=np.float64))
    vrp_mult = ctx.custom.get('vrp_mult', np.ones(n, dtype=np.float64))
    size_mult = np.clip(pos_mult * vrp_mult, 0.3, 1.5)

    # ── CONVICTION ────────────────────────────────────────────────────
    conviction = conviction_1h

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=999.0,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        exit_regimes={CRISIS},
        name='trend_pullback',
        size_multiplier=size_mult,
        conviction_score=conviction,
        market_type=MarketType.SPOT,
        leverage=1.0,
        breakeven_atr=0.0,
    )
