"""
Strategy S16: Composite Factor (IC-Weighted)
=============================================
Uses Signal Lab IC-weighted composite as the core signal.
Top 10 post-ETF signals combined into a single score.

Composite IC = 0.066 (5d), 0.110 (20d) — strongest single predictor.

Logic: Compute each signal in real-time on 1H bars, z-score cross-sectionally,
       weight by IC, enter when composite exceeds threshold + trend filter.

Since this is a single-token strategy (engine runs per-token), we use
time-series z-scoring instead of cross-sectional.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_zscore, rolling_std, rolling_mean, rolling_max,
                    rolling_skew)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Composite Factor — IC-weighted combination of top signals."""
    n = len(ctx.ind_1h["close"])
    close = ctx.ind_1h["close"]
    high = ctx.ind_1h["high"]
    low = ctx.ind_1h["low"]
    volume = ctx.ind_1h["volume"]
    bb_width = ctx.ind_1h["bb_width"]
    atr = ctx.ind_1h["atr"]
    ema20 = ctx.ind_1h["ema_20"]
    adx = ctx.ind_1h["adx"]

    window = 480  # 20 days for z-scoring

    # --- Signal 1: Vol-of-Vol (IC=-0.088, weight negative -> want LOW) ---
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
    vol_10 = rolling_std(log_ret, 10)
    vov = rolling_std(vol_10, 20)
    z_vov = rolling_zscore(vov, window) * -0.088

    # --- Signal 2: BB Squeeze (IC=+0.044, weight positive -> want HIGH squeeze) ---
    bb_avg = rolling_mean(bb_width, 120)
    bb_ratio = bb_width / np.maximum(np.nan_to_num(bb_avg, nan=1.0), 1e-10)
    z_bb = rolling_zscore(bb_ratio, window) * 0.044

    # --- Signal 3: Volume Momentum (IC=+0.039) ---
    vol_avg_20 = rolling_mean(volume, 20)
    vol_mom = volume / np.maximum(np.nan_to_num(vol_avg_20, nan=1.0), 1.0)
    z_vol = rolling_zscore(vol_mom, window) * 0.039

    # --- Signal 4: Rolling Skew (IC=+0.034) ---
    skew = rolling_skew(log_ret, 20)
    z_skew = rolling_zscore(skew, window) * 0.034

    # --- Signal 5: New High Distance (IC=+0.034) ---
    # rolling_max uses min_periods=1 so it includes current bar like original
    high_60 = rolling_max(high, 61)  # window=61 to match i-60:i+1 range
    nhd = (close - np.nan_to_num(high_60, nan=close[0])) / np.maximum(np.nan_to_num(high_60, nan=1.0), 1e-10)
    z_nhd = rolling_zscore(nhd, window) * 0.034

    # --- Composite ---
    composite = z_vov + z_bb + z_vol + z_skew + z_nhd

    # Entry: composite > 1 standard deviation above mean + trend filter
    entry_signal = composite > 0.5

    # Trend filter
    trend_ok = (close > ema20) & (adx > 15)
    regime_ok = ctx.regime_1h != 0

    entry = entry_signal & trend_ok & regime_ok
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
        name="composite_factor",
    )
