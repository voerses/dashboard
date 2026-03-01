"""
Strategy S22: Supertrend + ADX
================================
Signal Lab: supertrend_dir IC=+0.023 post-ETF (FLIPPED from -0.042 pre-ETF).
This is one of the few signals that IMPROVED post-ETF.
ADX remains the strongest trend indicator (IC=+0.017 post-ETF).

Logic: Enter when Supertrend flips to bullish + ADX confirms trend strength.
       This is a trend-following entry that adapts to volatility via ATR.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Supertrend + ADX -- enter on Supertrend flip with ADX confirmation."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    atr = ctx.ind_1h['atr']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ema20 = ctx.ind_1h['ema_20']

    # 1. Compute Supertrend (3x ATR, 10-period)
    # This loop is inherently sequential (state-dependent) but runs in O(n) which is fast enough
    mid = (high + low) / 2
    upper_band = mid + 3.0 * atr
    lower_band = mid - 3.0 * atr

    direction = np.ones(n, dtype=np.int8)  # 1 = up, -1 = down
    for i in range(1, n):
        if close[i] > upper_band[i-1]:
            direction[i] = 1
        elif close[i] < lower_band[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]

    # 2. Supertrend just flipped to bullish (within last 6 bars) - vectorized
    # Find where direction changed from -1 to 1
    dir_change = np.diff(direction, prepend=direction[0])
    flip_points = dir_change == 2  # -1 to 1 = change of +2
    # Check if any flip happened in last 6 bars using rolling max
    import pandas as pd
    flip_to_bull = pd.Series(flip_points.astype(np.float64)).rolling(6, min_periods=1).max().values > 0
    flip_to_bull &= (direction == 1)

    # 3. ADX confirms: strong trend + bullish direction
    adx_confirm = (adx > 20) & (plus_di > minus_di)

    # 4. Volume confirmation
    vol_ok = vol_ratio > 0.8

    # 5. Price above EMA20 (basic trend alignment)
    trend_ok = close > ema20

    # 6. Regime filter
    regime_ok = ctx.regime_1h != 0

    entry = flip_to_bull & adx_confirm & vol_ok & trend_ok & regime_ok
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
        name='supertrend_adx',
    )
