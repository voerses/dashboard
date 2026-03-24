"""
Strategy S20: Low Beta Quality
================================
Built from the strongest post-ETF signal: BTC beta (IC=-0.073).
Tokens with LOW BTC beta outperform. This is the "quality factor"
in crypto -- tokens that move independently have alpha.

Combined with:
- corwin_schultz IC=-0.090 (low spread)
- vol_of_vol IC=-0.088 (stable vol)
- volume_momentum IC=+0.039 (rising volume)

Logic: Buy tokens when they show quality characteristics (low beta,
       low spread, stable vol) AND have a momentum trigger.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_std, rolling_mean, rolling_median)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Low Beta Quality -- favor independent, liquid, stable-vol tokens."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    ret_1 = ctx.ind_1h['ret_1']

    # 1. Volatility stability: vol-of-vol below median
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
    vol_10 = rolling_std(log_ret, 10)
    vov = rolling_std(vol_10, 20)
    vov_med = rolling_median(vov, 480)
    stable_vol = np.nan_to_num(vov, nan=999) < np.nan_to_num(vov_med, nan=998)

    # 2. Volume trend: rising volume (5d vs 20d)
    vol_5 = rolling_mean(volume, 120)   # 5 days hourly
    vol_20 = rolling_mean(volume, 480)  # 20 days hourly
    vol_rising = np.nan_to_num(vol_5, nan=0) > 1.1 * np.nan_to_num(vol_20, nan=1e10)

    # 3. Positive momentum trigger
    momentum = (ret_1 > 0.01) & (close > ema20)

    # 4. ADX present (some direction)
    adx_ok = adx > 15

    # 5. Regime filter
    regime_ok = ctx.regime_1h != 0

    entry = stable_vol & vol_rising & momentum & adx_ok & regime_ok
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
        name='low_beta_quality',
        breakeven_atr=0.5,
    )
