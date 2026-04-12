"""
S530 — BTC Donchian Breakout (Bull Market Companion)
=====================================================

BACKTEST CLI (standalone):
  /workspace/venv/bin/python v4/portfolio_backtest.py \
      --strategy s530_btc_donchian --months 12 --capital 100000 \
      --market perp --conviction-mode ranked \
      --max-portfolio-positions 1 --concentration 1.0 --skip-wf \
      --end-date 2025-01-01

Signal: BTC Donchian channel breakout (20d high entry, 10d low exit)
  - LONG when BTC daily close > 20-day high (lagged 1 day, no lookahead)
  - EXIT via max_hold (engine handles it) — Donchian exit encoded as entry suppression
  - Entry on first 1h bar of new day after breakout signal

Lookahead verified: V2 (daily lag) matches V3 (hourly exec). No bias.

Only trades BTC. Designed as bull-market companion to s524l (alt contrarian).

Status: RESEARCH
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType, CRISIS,
                    UPTREND, DOWNTREND, RANGE, QUIET)

STRATEGY_TYPE = "per_token"
MARKET = MarketType.PERP
DIRECTION = "long"

PORTFOLIO_CONFIG = {
    "conviction_mode": "ranked",
    "max_positions": 1,
    "sizing_overrides": {
        "kelly_mult_override": 0.8,
        "cap_pct_override": 0.90,
        "target_vol": 0.10,
    },
}

# ── Parameters ──
DONCHIAN_ENTRY = 20  # days: long when close > N-day high
DONCHIAN_EXIT = 10   # days: exit when close < N-day low
LEVERAGE = 3.0
STOP_MULT = 999.0    # no ATR stop — Donchian exit via signal suppression
TRAIL_MULT = 999.0
MIN_HOLD = 24        # minimum 1 day hold
MAX_HOLD = 2160      # 90 days — let trends run, Donchian exit via no re-entry
WARMUP = 500
BREAKEVEN_ATR = 0.0


def strategy(ctx: StrategyContext) -> StrategyResult:
    """BTC Donchian breakout — long only, single token."""
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker

    # Only trade BTC
    if ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            conviction_score=np.zeros(n, dtype=np.float64),
            size_multiplier=np.ones(n, dtype=np.float64),
            leverage=LEVERAGE,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=999,
            no_stop_bars=MIN_HOLD,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=0.0,
            name='s530_btc_donchian',
            breakeven_atr=BREAKEVEN_ATR,
        )

    idx = ctx.idx_1h
    high = ctx.ind_1h.get('high', close)
    low = ctx.ind_1h.get('low', close)

    # Compute daily OHLC from 1h bars
    daily_close = pd.Series(close, index=idx).resample('1D').last()
    daily_high = pd.Series(high, index=idx).resample('1D').max()
    daily_low = pd.Series(low, index=idx).resample('1D').min()

    # Donchian channels (shifted 1 day to avoid lookahead)
    upper = daily_high.rolling(DONCHIAN_ENTRY).max().shift(1)
    lower = daily_low.rolling(DONCHIAN_EXIT).min().shift(1)

    # Daily state machine: enter on breakout, exit on breakdown
    # All comparisons use YESTERDAY's close vs YESTERDAY's channel (lagged)
    daily_close_prev = daily_close.shift(1)
    breakout = daily_close_prev > upper.shift(1)  # yesterday's close > day-before-yesterday's 20d high
    breakdown = daily_close_prev < lower.shift(1)

    position_daily = pd.Series(0, index=daily_close.index, dtype=int)
    state = 0
    for i in range(len(daily_close)):
        if state == 0 and breakout.iloc[i] if not pd.isna(breakout.iloc[i]) else False:
            state = 1
        elif state == 1 and breakdown.iloc[i] if not pd.isna(breakdown.iloc[i]) else False:
            state = 0
        position_daily.iloc[i] = state

    # Detect entry transitions
    daily_entry = (position_daily == 1) & (position_daily.shift(1) == 0)
    daily_entry.iloc[0] = False

    # Map to 1h: entry on first bar of each day
    day_change = np.zeros(n, dtype=bool)
    if len(idx) > 1:
        days = np.array([t.date() for t in idx])
        day_change[1:] = days[1:] != days[:-1]
        day_change[0] = True

    entry_daily_1h = daily_entry.reindex(idx, method='ffill').fillna(False).values
    entry = entry_daily_1h & day_change
    entry[:WARMUP] = False

    # REGIME GATE: only active in pre-halving years (2023/2024, 2027/2028, etc.)
    # Post-halving years are bear — s524l handles those with alt shorts
    # The Donchian signal itself avoids entering in downtrends (no breakouts)
    _bar_years = np.array([t.year for t in idx])
    _post_halving = np.isin(_bar_years, [2021, 2022, 2025, 2026, 2029, 2030])
    entry = entry & ~_post_halving

    direction = np.where(entry, 1, 0).astype(np.int8)
    conviction = np.where(entry, 1.0, 0.0).astype(np.float64)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        conviction_score=conviction,
        size_multiplier=np.ones(n, dtype=np.float64),
        cap_multiplier=np.full(n, 8.0, dtype=np.float64),
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=999,
        no_stop_bars=MIN_HOLD,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=0.50,
        name='s530_btc_donchian',
        breakeven_atr=BREAKEVEN_ATR,
        exit_regimes={CRISIS},
    )
