"""
s99 EMA 5/20 Signal Exit — V4 Per-Token Strategy (Class A)

Daily-checked EMA 5/20 crossover on ETH. Hold until crossover flips.
This is the "signal-based exit" approach: positions are held indefinitely
until the EMA direction changes, rather than forced weekly rebalancing.

Key discovery: signal-based exit dramatically outperforms weekly rebalance
because it captures full trend moves and avoids fee drag from forced cycling.

1m backtest results (12mo ending Feb 2026, $200K, 5bps/side, 0.005%/8h funding):
  4x cap=2.0:  +530%, DD -16.5%, Cal 32.1, 15 trades, 0 liqs, WR 93.3%
  6x cap=1.5:  +664%, DD -18.5%, Cal 35.9, 15 trades, 0 liqs, WR 93.3%
  7x cap=1.0:  +416%, DD -14.5%, Cal 28.6, 15 trades, 0 liqs, WR 93.3%
  8x cap=1.0:  +486%, DD -17.8%, Cal 27.3, 16 trades, 1 liq,  WR 87.5%

Multi-year (4x cap=2.0):
  2yr: +1,993%, DD -20.7%, Cal 96.1, 34 trades, 0 liqs
  3yr: +6,219%, DD -20.7%, Cal 300.1, 50 trades, 0 liqs

Robustness (4x cap=2.0 across rolling 12mo windows, 2021-2026):
  ALL 12 windows profitable (min +134%, max +548%)
  DD ranges -16.5% to -27.1%, Calmar always > 5.0

V4 LIMITATION: V4 framework cannot do signal-based exit (it uses max_hold
for position duration). V4 shows -3.8% because positions stay open in the
wrong direction after signal flips. The definitive results above come from
the standalone minute-level backtester in tools/backtest_ema520.py.

Market: PERP (bidirectional — long or short)
Status: VALIDATED (standalone), NOT COMPATIBLE with V4
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# ── Configuration ────────────────────────────────────────────────
FAST_SPAN = 5           # Fast EMA period (daily bars)
SLOW_SPAN = 20          # Slow EMA period (daily bars)

# V4 approximation: check daily, hold up to 30 days max
# The real strategy exits on signal change (~15 trades/yr)
# V4 forces exit at max_hold regardless of signal
MAX_HOLD_DAYS = 30
MAX_HOLD_BARS = MAX_HOLD_DAYS * 24

LEVERAGE = 4.0          # 4x leverage (low enough for 0 liquidations)
MARGIN_CAP = 0.30       # Max position as % of equity
CAP_MULT = 2.0          # Cap multiplier → effective 24% of equity as margin
TARGET_TOKEN = "ETH"    # ETH-only

WARMUP_DAILY = max(SLOW_SPAN + 10, 60)
WARMUP_BARS = WARMUP_DAILY * 24


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Daily-checked EMA 5/20 trend following — hold until signal changes."""
    n = len(ctx.ind_1h['close'])

    # ETH-only: return empty result for all other tokens
    if ctx.ticker != TARGET_TOKEN:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            leverage=1.0,
            stop_mult=99.0, trail_mult=99.0, target_mult=999,
            no_stop_bars=MAX_HOLD_BARS,
            min_hold=24, max_hold=MAX_HOLD_BARS,
            edge=0.35, exit_regimes=set(),
            name='s99_ema520_signal_exit',
            breakeven_atr=0.0,
        )

    # ── Daily EMA computation ──────────────────────────────────────
    daily_close = ctx.ind_d['close']
    n_daily = len(daily_close)

    if n_daily < WARMUP_DAILY:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='s99_ema520_signal_exit',
        )

    fast_ema = pd.Series(daily_close).ewm(span=FAST_SPAN, adjust=False).mean().values
    slow_ema = pd.Series(daily_close).ewm(span=SLOW_SPAN, adjust=False).mean().values

    # Daily signal: +1 long when fast > slow, -1 short otherwise
    # No weekly gating — check every day for fastest response
    daily_signal = np.where(fast_ema > slow_ema, 1, -1).astype(np.int8)

    # ── Align daily signal to hourly bars ──────────────────────────
    hourly_direction = ctx.align_daily_to_1h(
        daily_signal.astype(np.float64)
    ).astype(np.int8)

    # Ensure no zeros
    hourly_direction[hourly_direction == 0] = 1

    # ── Entry mask: True for all bars after warmup ─────────────────
    entry_mask = np.ones(n, dtype=bool)
    entry_mask[:WARMUP_BARS] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=hourly_direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=99.0,              # No stop loss
        trail_mult=99.0,             # No trailing stop
        target_mult=999.0,           # No fixed target
        no_stop_bars=MAX_HOLD_BARS,  # No stops during hold
        min_hold=24,                 # Min 1 day hold
        max_hold=MAX_HOLD_BARS,      # 30 day max (V4 approximation)
        edge=0.40,
        exit_regimes=set(),
        exchange='binance',
        name='s99_ema520_signal_exit',
        breakeven_atr=0.0,
        max_trade_pct=MARGIN_CAP,
        cap_multiplier=CAP_MULT,
    )
