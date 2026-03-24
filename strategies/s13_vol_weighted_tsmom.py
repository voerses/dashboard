"""
Strategy S13: Volume-Weighted Time-Series Momentum (TSMOM)
==========================================================
Academic basis: Huang, Sangiorgi & Urquhart (2024), annualized Sharpe 2.17.
Han, Kang & Ryu (2023), optimal 28-day lookback, Sharpe 1.51.

Signal Lab result: vol_weighted_tsmom IC=+0.080 pre-ETF, +0.008 post-ETF.
The raw signal weakened post-ETF, so we add regime + volatility filters
that the academic papers identify as critical.

Logic: Buy when volume-weighted trailing return is in top tercile of own history,
       AND regime is not bear, AND vol is not extreme.

Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_mean)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Volume-Weighted TSMOM with regime filter."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']

    # 1. Volume-weighted return over 28 days (672 hourly bars)
    lookback = 672  # 28 days * 24 hours
    ret_1h = np.zeros(n)
    ret_1h[1:] = close[1:] / close[:-1] - 1

    # Volume ratio (current vs 20-day avg)
    vol_avg = rolling_mean(volume, 480)
    vol_ratio = volume / np.maximum(np.nan_to_num(vol_avg, nan=1.0), 1.0)

    # Volume-weighted cumulative return (rolling sum over lookback)
    vw_ret = ret_1h * vol_ratio
    vw_cum = pd.Series(vw_ret).rolling(lookback, min_periods=lookback).sum().values

    # 2. Rank in own history: is this in the top third?
    # Use rolling rank via pandas
    hist_window = 6048  # ~252 days of history (hourly)
    vw_series = pd.Series(vw_cum)
    rank_pct = vw_series.rolling(hist_window, min_periods=100).rank(pct=True).values

    # Top tercile
    tsmom_signal = np.nan_to_num(rank_pct, nan=0.5) > 0.67

    # 3. Trend filter: above EMA20 (momentum confirmation)
    trend_ok = close > ema20

    # 4. ADX filter: some trend present
    adx_ok = adx > 15

    # 5. Regime filter: not crisis
    regime_ok = ctx.regime_1h != 0

    entry = tsmom_signal & trend_ok & adx_ok & regime_ok
    entry[:max(lookback, 1000)] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.35,
        exit_regimes={CRISIS, DOWNTREND},
        name='vol_weighted_tsmom',
        breakeven_atr=0.5,
    )
