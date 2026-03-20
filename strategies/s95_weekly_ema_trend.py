"""
s95 Weekly EMA Trend — V4 Per-Token Strategy (Class A)

Weekly-checked EMA 8/30 crossover trend following on daily bars.
Always-in-market: long when fast EMA > slow EMA, short otherwise.
Signal checked every 7 daily bars to reduce whipsaw.

Optimized for ETH perps at 8x leverage (V4 realistic simulation).

V4 backtest results (12mo, $200K):
  8x cap=1.0:  +122%, DD -31.5%, Cal 1.56, Sh 1.06, 54 trades, 7 liqs
  8x cap=1.5:  +170%, DD -45.1%, Cal 1.42, 7 liqs (aggressive)
  8x cap=2.0:  +196%, DD -55.0%, Cal 1.31, 7 liqs (very aggressive)

Standalone backtest (daily bars, no hourly liq check):
  e8/30 18x m7%:  +329%, DD -16.7%, Cal 19.6 (unrealistically optimistic)

Market: PERP (bidirectional — long or short)
Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# ── Configuration ────────────────────────────────────────────────
FAST_SPAN = 8           # Fast EMA period (daily bars)
SLOW_SPAN = 30          # Slow EMA period (daily bars)
REBAL_DAYS = 7          # Signal check frequency (days)
REBAL_BARS = REBAL_DAYS * 24  # In hourly bars = 168

LEVERAGE = 8.0          # Leverage multiplier (optimal for V4)
MARGIN_CAP = 0.30       # Max position as % of equity
CAP_MULT = 1.0          # Position cap multiplier (1.0=conservative, 2.0=aggressive)
TARGET_TOKEN = "ETH"    # Optimized for ETH

WARMUP_DAILY = max(SLOW_SPAN + REBAL_DAYS, 60)
WARMUP_BARS = WARMUP_DAILY * 24


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Weekly-checked EMA trend following — always in market, no stops."""
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
            name='s95_weekly_ema_trend',
            breakeven_atr=0.0,
        )

    # ── Daily EMA computation ──────────────────────────────────────
    daily_close = ctx.ind_d['close']
    n_daily = len(daily_close)

    if n_daily < WARMUP_DAILY:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='s95_weekly_ema_trend',
        )

    fast_ema = pd.Series(daily_close).ewm(span=FAST_SPAN, adjust=False).mean().values
    slow_ema = pd.Series(daily_close).ewm(span=SLOW_SPAN, adjust=False).mean().values

    # Raw signal: +1 long when fast > slow, -1 short otherwise
    raw_signal = np.where(fast_ema > slow_ema, 1, -1).astype(np.int8)

    # Weekly-checked signal: only update every REBAL_DAYS daily bars.
    # Between checkpoints, carry forward the previous signal.
    # This reduces false crossovers from ~36/year to ~5-12/year.
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

    # Ensure no zeros — always in market (long or short)
    hourly_direction[hourly_direction == 0] = 1

    # ── Entry mask: True for all bars after warmup ─────────────────
    # Simulator enters when no position is open (after max_hold exit).
    # With max_hold=168, this creates weekly position rotation.
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
        stop_mult=99.0,          # No stop loss
        trail_mult=99.0,         # No trailing stop
        target_mult=999.0,       # No fixed target
        no_stop_bars=REBAL_BARS, # Protect full holding period from stops
        min_hold=24,             # Minimum 1 day hold
        max_hold=REBAL_BARS,     # 7 days = rebalance period
        edge=0.40,
        exit_regimes=set(),      # No regime exits — hold to rebalance
        exchange='binance',
        name='s95_weekly_ema_trend',
        breakeven_atr=0.0,       # Disable breakeven ratchet
        max_trade_pct=MARGIN_CAP,
        cap_multiplier=CAP_MULT,
    )
