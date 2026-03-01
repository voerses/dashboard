"""
Strategy S08: OBV Divergence
=============================
Buy when price makes new low but OBV doesn't (bullish divergence).
Uses custom indicators (OBV) from the plugin system.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (
    StrategyContext, StrategyResult, _rolling_mean,
    CRISIS, DOWNTREND, RANGE, QUIET,
)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """OBV divergence — bullish when price drops but OBV holds."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']

    # OBV is auto-computed by the indicator plugin system
    obv = ctx.custom.get('obv')
    if obv is None:
        # Fallback: compute inline
        volume = ctx.ind_1h['volume']
        obv = np.zeros(n)
        for i in range(1, n):
            if close[i] > close[i-1]:
                obv[i] = obv[i-1] + volume[i]
            elif close[i] < close[i-1]:
                obv[i] = obv[i-1] - volume[i]
            else:
                obv[i] = obv[i-1]

    # Price makes 20-bar low
    import pandas as pd
    price_low_20 = pd.Series(close).rolling(20).min().values
    at_low = close <= price_low_20 * 1.01  # within 1% of 20-bar low

    # OBV NOT at low (divergence)
    obv_low_20 = pd.Series(obv).rolling(20).min().values
    obv_divergence = obv > obv_low_20 * 1.05  # OBV 5% above its 20-bar low

    # RSI oversold confirmation
    rsi = ctx.ind_1h['rsi']
    rsi_ok = rsi < 40

    # Not in crisis
    regime_ok = ctx.regime_1h != CRISIS

    entry = at_low & obv_divergence & rsi_ok & regime_ok
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0, trail_mult=3.0, target_mult=999,
        no_stop_bars=12, min_hold=12, max_hold=480,
        edge=0.35,
        exit_regimes={CRISIS, DOWNTREND},
        name='obv_divergence',
    )
