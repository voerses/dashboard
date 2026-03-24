"""
s135 — s56 Signal-Enhanced Momentum + Realized Volatility Skew Quality Overlay
===============================================================================
Class C Overlay: wraps s56 (Tier A) with skew_30d trend quality filter.

Research finding:
  skew_30d has IC = +0.224 OOS (t = +5.15) for BTC/ETH at 7d horizon.
  Positive skew = right-skewed returns = healthy uptrend momentum.

Signal definition:
  skew_30d = (mean - median) / std of daily returns over a 30-day rolling window
  Computed from ctx.ind_d['close'] (daily close prices already available).
  Aligned back to 1h via ctx.align_daily_to_1h().

Sizing overlay (s56 is long-only momentum):
  - Strong positive skew (> 0.3): 2.0x boost (very healthy uptrend)
  - Positive skew (> 0):          1.5x boost (healthy uptrend)
  - Negative skew (> -0.3):       0.5x reduction (unhealthy distribution)
  - Strong negative skew (<= -0.3): 0.0x (skip trade entirely)

No external data needed — skew computed from price data itself.

Base: s56_signal_enhanced_momentum (Tier A, perp, long-only momentum)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import numpy as np
import pandas as pd
from strategies.s56_signal_enhanced_momentum import strategy as base_strategy

# Skew parameters
SKEW_WINDOW = 30  # 30-day rolling window


def _compute_skew_30d(daily_close: np.ndarray, window: int = SKEW_WINDOW) -> np.ndarray:
    """Realized volatility skew over a rolling window.

    skew_30d = (mean - median) / std of daily returns.
    Positive = right-skewed = bullish trend quality.

    Uses cumsum for mean/std (O(n)) and pandas rolling only for median.
    """
    n = len(daily_close)
    result = np.zeros(n, dtype=np.float64)
    if n < window + 1:
        return result

    # Daily returns
    daily_ret = np.empty(n, dtype=np.float64)
    daily_ret[0] = np.nan
    daily_ret[1:] = (daily_close[1:] - daily_close[:-1]) / np.maximum(daily_close[:-1], 1e-10)

    # Rolling mean and sum-of-squares via cumsum (O(n), no pandas overhead)
    ret_clean = daily_ret.copy()
    ret_clean[0] = 0.0  # placeholder for cumsum
    cs = np.cumsum(ret_clean)
    cs2 = np.cumsum(ret_clean ** 2)

    # rolling_mean[i] = (cs[i] - cs[i-window]) / window  for i >= window
    rm = np.zeros(n, dtype=np.float64)
    rs = np.zeros(n, dtype=np.float64)
    rm[window:] = (cs[window:] - cs[:-window]) / window
    var = (cs2[window:] - cs2[:-window]) / window - rm[window:] ** 2
    # Bessel correction: std = sqrt(var * window / (window - 1))
    rs[window:] = np.sqrt(np.maximum(var * window / (window - 1), 0.0))

    # Rolling median — only pandas needed here (C skip-list, fast)
    rmed = pd.Series(daily_ret).rolling(window, min_periods=window).median().values

    # Skew = (mean - median) / std
    valid = rs > 1e-10
    result[valid] = (rm[valid] - rmed[valid]) / rs[valid]
    # Warmup zone stays 0.0
    result[:window] = 0.0
    return result


def strategy(ctx):
    """s56 + realized volatility skew quality overlay."""
    result = base_strategy(ctx)

    # Compute skew_30d from daily closes (already in ctx)
    daily_close = ctx.ind_d['close']
    skew_daily = _compute_skew_30d(daily_close, SKEW_WINDOW)

    # Align daily skew to 1h grid (forward-fill)
    skew_1h = ctx.align_daily_to_1h(skew_daily)

    # Apply sizing tiers based on skew value
    # Strong positive (>0.3) → 2.0x, positive (>0) → 1.5x,
    # negative (>-0.3) → 0.5x, strong negative (≤-0.3) → 0.0x
    skew_scale = np.where(
        skew_1h > 0.3, 2.0,
        np.where(
            skew_1h > 0.0, 1.5,
            np.where(
                skew_1h > -0.3, 0.5,
                0.0
            )
        )
    )

    # Combine with s56's existing size_multiplier
    base_size = result.size_multiplier
    if isinstance(base_size, np.ndarray):
        combined_size = base_size * skew_scale
    else:
        combined_size = float(base_size) * skew_scale

    from engine import StrategyResult
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        leverage=result.leverage,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s135_skew_quality_overlay',
        size_multiplier=combined_size,
        cap_multiplier=result.cap_multiplier,
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        max_trail_mult=getattr(result, 'max_trail_mult', None),
        max_trade_pct=result.max_trade_pct,
        rsi_exit_level=result.rsi_exit_level,
        conviction_score=getattr(result, 'conviction_score', None),
        breakeven_atr=result.breakeven_atr,
    )
