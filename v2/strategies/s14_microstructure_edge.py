"""
Strategy S14: Microstructure Edge
=================================
Built from Signal Lab findings:
- corwin_schultz IC=-0.090 (LOW spread tokens outperform)
- kyle_lambda IC=+0.042 (illiquid tokens have higher returns — but spread matters)
- vol_of_vol IC=-0.088 (LOW vol-of-vol tokens outperform)
- btc_beta_20 IC=-0.073 (LOW BTC beta tokens outperform)

Logic: Enter tokens showing quality microstructure characteristics
       (low spread, stable volatility, low BTC dependence) with
       momentum confirmation. This is a "quality factor" strategy.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_median)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Microstructure Edge — favor low-spread, stable-vol, low-beta setups."""
    n = len(ctx.ind_1h["close"])
    close = ctx.ind_1h["close"]
    high = ctx.ind_1h["high"]
    low = ctx.ind_1h["low"]
    ema20 = ctx.ind_1h["ema_20"]
    adx = ctx.ind_1h["adx"]
    ret_1 = ctx.ind_1h["ret_1"]
    vol_ratio = ctx.ind_1h["vol_ratio"]

    # 1. Corwin-Schultz spread estimate (rolling 20-bar average)
    log_hl = np.log(np.maximum(high, 1e-10) / np.maximum(low, 1e-10))
    log_hl_sq = log_hl ** 2
    beta_cs = log_hl_sq + np.roll(log_hl_sq, 1)
    beta_cs[:1] = np.nan

    # Vectorized 2-bar high/low (replaces for-loop)
    h2 = np.maximum(high, np.roll(high, 1))
    l2 = np.minimum(low, np.roll(low, 1))
    h2[0] = high[0]
    l2[0] = low[0]

    gamma_cs = np.log(np.maximum(h2, 1e-10) / np.maximum(l2, 1e-10)) ** 2
    sqrt2 = np.sqrt(2)
    alpha_cs = (np.sqrt(2 * beta_cs) - np.sqrt(beta_cs)) / (3 - 2 * sqrt2) - np.sqrt(gamma_cs / (3 - 2 * sqrt2))
    alpha_cs = np.maximum(alpha_cs, 0)
    spread = 2 * (np.exp(alpha_cs) - 1) / (1 + np.exp(alpha_cs))
    spread_avg = rolling_mean(spread, 20)

    # Low spread: below median of its own history
    spread_med = rolling_median(spread_avg, 240)
    low_spread = np.nan_to_num(spread_avg, nan=999) < np.nan_to_num(spread_med, nan=998)

    # 2. Vol-of-vol: rolling std of rolling vol
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
    vol_10 = rolling_std(log_ret, 10)
    vov = rolling_std(vol_10, 20)

    # Low vol-of-vol: below median
    vov_med = rolling_median(vov, 240)
    low_vov = np.nan_to_num(vov, nan=999) < np.nan_to_num(vov_med, nan=998)

    # 3. Momentum confirmation: positive 1H return + above EMA20
    momentum_ok = (ret_1 > 0) & (close > ema20)

    # 4. ADX filter
    adx_ok = adx > 15

    # 5. Volume confirmation
    vol_ok = vol_ratio > 0.8

    entry = low_spread & low_vov & momentum_ok & adx_ok & vol_ok
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
        edge=0.35,
        exit_regimes={CRISIS, DOWNTREND},
        name="microstructure_edge",
    )
