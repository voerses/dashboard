"""
s97 Multi-Token Weekly EMA Trend — V4 Per-Token Strategy (Class A)

Same weekly-checked EMA 8/30 signal as s95 but applied to ALL tokens,
not just ETH. Each token independently goes long when fast EMA > slow EMA,
short otherwise. Weekly rebalance via max_hold=168.

With max_positions=5-10, this diversifies across multiple tokens, reducing
per-trade liquidation impact and multiplying total notional exposure.

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

LEVERAGE = 8.0          # Leverage multiplier (best from V4 sweep)
MARGIN_CAP = 0.15       # Max position as % of equity per token

WARMUP_DAILY = max(SLOW_SPAN + REBAL_DAYS, 60)
WARMUP_BARS = WARMUP_DAILY * 24

# Minimum daily volume to trade (filters out illiquid tokens)
MIN_ADV_USD = 50_000_000  # $50M daily volume


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Weekly-checked EMA trend following — multi-token, always in market."""
    n = len(ctx.ind_1h['close'])

    # ── Daily EMA computation ──────────────────────────────────────
    daily_close = ctx.ind_d['close']
    n_daily = len(daily_close)

    if n_daily < WARMUP_DAILY:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='s97_multi_ema_trend',
        )

    fast_ema = pd.Series(daily_close).ewm(span=FAST_SPAN, adjust=False).mean().values
    slow_ema = pd.Series(daily_close).ewm(span=SLOW_SPAN, adjust=False).mean().values

    # Raw signal: +1 long when fast > slow, -1 short otherwise
    raw_signal = np.where(fast_ema > slow_ema, 1, -1).astype(np.int8)

    # Weekly-checked signal: only update every REBAL_DAYS daily bars
    weekly_signal = np.zeros(n_daily, dtype=np.int8)
    weekly_signal[0] = raw_signal[0]
    for i in range(1, n_daily):
        weekly_signal[i] = weekly_signal[i - 1]
        if i % REBAL_DAYS == 0:
            weekly_signal[i] = raw_signal[i]

    # ── Align daily signal to hourly bars ──────────────────────────
    hourly_direction = ctx.align_daily_to_1h(
        weekly_signal.astype(np.float64)
    ).astype(np.int8)
    hourly_direction[hourly_direction == 0] = 1

    # ── Entry mask ────────────────────────────────────────────────
    entry_mask = np.ones(n, dtype=bool)
    entry_mask[:WARMUP_BARS] = False

    # Liquidity filter
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=hourly_direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=99.0,
        trail_mult=99.0,
        target_mult=999.0,
        no_stop_bars=REBAL_BARS,
        min_hold=24,
        max_hold=REBAL_BARS,
        edge=0.40,
        exit_regimes=set(),
        exchange='binance',
        name='s97_multi_ema_trend',
        breakeven_atr=0.0,
        max_trade_pct=MARGIN_CAP,
    )
