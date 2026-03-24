"""
Strategy S21: Skew Momentum
=============================
Signal Lab: rolling_skew IC=+0.034 post-ETF (stable, didn't degrade).
Positive skew in returns = fat right tail = potential for explosive up moves.

Combined with momentum confirmation -- enter when recent return distribution
is positively skewed AND price is trending up.

Related: Fat-tail capture (Borgards 2021), crypto has longer momentum periods.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_skew)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Skew Momentum -- enter when return distribution is positively skewed."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']

    # 1. Rolling skewness of hourly returns (20-day = 480 bars)
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
    skew_20d = rolling_skew(log_ret, 480)

    # Positive skew > 0.5 (meaningful positive tail)
    pos_skew = np.nan_to_num(skew_20d, nan=0) > 0.5

    # 2. Price trending up: above EMA20
    trend_up = close > ema20

    # 3. ADX: some trend present
    adx_ok = adx > 15

    # 4. Momentum trigger: positive recent return (use pre-computed if available)
    ret_24h = ctx.custom.get('ret_24h', None)
    if ret_24h is None:
        ret_24h = np.zeros(n)
        ret_24h[24:] = close[24:] / close[:-24] - 1
    positive_mom = ret_24h > 0.005

    # 5. Volume: at least average
    vol_ok = vol_ratio > 0.8

    # 6. Regime filter
    regime_ok = ctx.regime_1h != 0

    entry = pos_skew & trend_up & adx_ok & positive_mom & vol_ok & regime_ok
    entry[:500] = False

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
        name='skew_momentum',
        breakeven_atr=0.5,
    )
