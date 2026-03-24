"""
Strategy S12: Quality Breakout
==============================
Built from Signal Lab findings (2026-03-01):
- bb_squeeze IC=+0.044 (compressed vol -> breakout)
- kyle_lambda IC=+0.042 (illiquid tokens have higher returns)
- volume_momentum IC=+0.039 (rising volume precedes moves)
- rolling_skew IC=+0.034 (positive skew = continuation)
- new_high_distance IC=+0.034 (near highs = momentum)

Logic: Enter when BB is squeezed (low vol) + volume starts rising +
       price near recent highs + trend filter. Captures breakouts
       from compression zones.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_mean, rolling_max)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Quality Breakout -- enter compressed vol setups with volume confirmation."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    bb_width = ctx.ind_1h['bb_width']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']

    # 1. BB Squeeze: current bandwidth < 60% of its 120-bar average
    bb_avg = rolling_mean(bb_width, 120)
    bb_avg = np.nan_to_num(bb_avg, nan=1.0)
    squeeze = bb_width < 0.6 * bb_avg

    # 2. Volume expansion: current volume > 1.5x 20-bar average
    vol_expanding = vol_ratio > 1.5

    # 3. Near recent highs: close within 5% of 60-bar high
    high_60 = rolling_max(high, 61)  # window=61 to match i-60:i+1 range
    high_60 = np.nan_to_num(high_60, nan=close[0] if len(close) > 0 else 1.0)
    near_highs = close > 0.95 * high_60

    # 4. Trend filter: above EMA20 and ADX > 15 (some trend present)
    trend_ok = (close > ema20) & (adx > 15)

    # 5. Not in crisis
    regime_ok = ctx.regime_1h != 0

    entry = squeeze & vol_expanding & near_highs & trend_ok & regime_ok
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        name='quality_breakout',
        breakeven_atr=0.5,
    )
