"""
s96 Weekly EMA Trend + Signal-Strength Leverage — V4 Per-Token Strategy (Class A)

Same weekly-checked EMA 8/30 signal as s95, but with leverage that scales
with signal conviction (EMA gap). When the trend is strong (large gap between
fast and slow EMA), leverage is high. Near crossovers (small gap), leverage
is minimal.

Additionally uses cap_multiplier > 1.0 to allow larger position sizes
beyond the default 12% cap.

Leverage mapping (EMA gap as % of price):
  |gap| > 8%:  MAX_LEV (strong trend, high confidence)
  |gap| 4-8%:  MID_LEV
  |gap| < 2%:  MIN_LEV (near crossover, defensive)

This concentrates risk in high-confidence trending periods while being
defensive near unreliable crossover zones.

Market: PERP (bidirectional — long or short)
Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# ── Signal Configuration ─────────────────────────────────────────
FAST_SPAN = 8           # Fast EMA period (daily bars)
SLOW_SPAN = 30          # Slow EMA period (daily bars)
REBAL_DAYS = 7          # Signal check frequency (days)
REBAL_BARS = REBAL_DAYS * 24  # In hourly bars = 168

# ── Leverage Configuration ────────────────────────────────────────
MAX_LEV = 12.0          # Strong trend
MID_LEV = 8.0           # Moderate trend
MIN_LEV = 4.0           # Near crossover / weak signal

# EMA gap thresholds (as fraction of slow EMA)
GAP_HIGH = 0.06         # |gap| > 6% → MAX_LEV
GAP_LOW = 0.02          # |gap| < 2% → MIN_LEV
                         # Linear interpolation between

CAP_MULT = 2.0          # Position size multiplier (allows up to 24% equity)
MARGIN_CAP = 0.30       # Max position as % of equity
TARGET_TOKEN = "ETH"    # Optimized for ETH

WARMUP_DAILY = max(SLOW_SPAN + REBAL_DAYS, 60)
WARMUP_BARS = WARMUP_DAILY * 24


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Weekly EMA trend with signal-strength based leverage."""
    n = len(ctx.ind_1h['close'])

    # ETH-only: return empty result for all other tokens
    if ctx.ticker != TARGET_TOKEN:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            leverage=1.0,
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999,
            no_stop_bars=REBAL_BARS,
            min_hold=24,
            max_hold=REBAL_BARS,
            edge=0.35,
            exit_regimes=set(),
            name='s96_weekly_ema_dynlev',
            breakeven_atr=0.0,
        )

    # ── Daily EMA computation ──────────────────────────────────────
    daily_close = ctx.ind_d['close']
    n_daily = len(daily_close)

    if n_daily < WARMUP_DAILY:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='s96_weekly_ema_dynlev',
        )

    fast_ema = pd.Series(daily_close).ewm(span=FAST_SPAN, adjust=False).mean().values
    slow_ema = pd.Series(daily_close).ewm(span=SLOW_SPAN, adjust=False).mean().values

    # Raw signal: +1 long when fast > slow, -1 short otherwise
    raw_signal = np.where(fast_ema > slow_ema, 1, -1).astype(np.int8)

    # Weekly-checked signal
    weekly_signal = np.zeros(n_daily, dtype=np.int8)
    weekly_signal[0] = raw_signal[0]
    for i in range(1, n_daily):
        weekly_signal[i] = weekly_signal[i - 1]
        if i % REBAL_DAYS == 0:
            weekly_signal[i] = raw_signal[i]

    # ── Signal-strength leverage ─────────────────────────────────
    ema_gap = np.abs(fast_ema - slow_ema) / np.maximum(slow_ema, 1.0)

    # Linear interpolation between MIN_LEV and MAX_LEV based on gap
    gap_frac = np.clip((ema_gap - GAP_LOW) / (GAP_HIGH - GAP_LOW), 0.0, 1.0)
    daily_leverage = MIN_LEV + (MAX_LEV - MIN_LEV) * gap_frac
    # Quantize to 0.5 increments
    daily_leverage = np.round(daily_leverage * 2) / 2

    # ── Align to hourly bars ──────────────────────────────────────
    hourly_direction = ctx.align_daily_to_1h(
        weekly_signal.astype(np.float64)
    ).astype(np.int8)
    hourly_direction[hourly_direction == 0] = 1

    hourly_leverage = ctx.align_daily_to_1h(daily_leverage)

    # ── Entry mask ────────────────────────────────────────────────
    entry_mask = np.ones(n, dtype=bool)
    entry_mask[:WARMUP_BARS] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=hourly_direction,
        market_type=MarketType.PERP,
        leverage=hourly_leverage,
        stop_mult=99.0,
        trail_mult=99.0,
        target_mult=999.0,
        no_stop_bars=REBAL_BARS,
        min_hold=24,
        max_hold=REBAL_BARS,
        edge=0.40,
        exit_regimes=set(),
        exchange='binance',
        name='s96_weekly_ema_dynlev',
        breakeven_atr=0.0,
        max_trade_pct=MARGIN_CAP,
        cap_multiplier=CAP_MULT,
    )
