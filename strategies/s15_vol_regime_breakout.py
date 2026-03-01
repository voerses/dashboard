"""
Strategy S15: Volatility Regime Breakout
=========================================
Built from Signal Lab findings:
- yang_zhang IC=-0.065 (LOW vol tokens outperform)
- parkinson IC=-0.063 (same signal, different estimator)
- regime_vol_ratio IC=+0.018 (elevated vol relative to recent = setup)
- vol_term_structure IC=-0.006 (backwardation = spike, but unstable)

Logic: Enter when vol has compressed (quality filter) and shows early signs
       of expansion (breakout trigger), with trend confirmation.
       Opposite of traditional vol breakout (which failed) — this is
       "buy AFTER vol has been low, when it starts expanding."

Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_std, rolling_mean)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Vol Regime Breakout — low vol base -> expansion trigger -> trend entry."""
    n = len(ctx.ind_1h["close"])
    close = ctx.ind_1h["close"]
    high = ctx.ind_1h["high"]
    low = ctx.ind_1h["low"]
    atr = ctx.ind_1h["atr"]
    adx = ctx.ind_1h["adx"]
    ema20 = ctx.ind_1h["ema_20"]
    bb_width = ctx.ind_1h["bb_width"]
    ret_1 = ctx.ind_1h["ret_1"]

    # 1. Yang-Zhang volatility (rolling 20-bar)
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
    vol_20 = rolling_std(log_ret, 20)

    # 2. Vol has been low: current vol below 40th percentile of 240-bar history
    # Vectorized using rolling quantile
    vol_series = pd.Series(vol_20)
    pct40 = vol_series.rolling(240, min_periods=50).quantile(0.4).values
    vol_low = (~np.isnan(vol_20)) & (~np.isnan(pct40)) & (vol_20 < pct40)

    # 3. Expansion trigger: today's range > 1.5x average range
    day_range = high - low
    avg_range = rolling_mean(day_range, 20)
    expansion = day_range > 1.5 * np.nan_to_num(avg_range, nan=1e10)

    # 4. Directional: expansion must be upward
    bullish = ret_1 > 0.005  # at least 0.5% up

    # 5. Trend filter
    trend_ok = (close > ema20) & (adx > 15)

    # 6. Regime filter
    regime_ok = ctx.regime_1h != 0

    entry = vol_low & expansion & bullish & trend_ok & regime_ok
    entry[:300] = False

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
        name="vol_regime_breakout",
    )
