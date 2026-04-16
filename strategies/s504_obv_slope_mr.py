"""
S504 — OBV Slope Divergence (Mean-Reversion)
=============================================

OBV (On-Balance Volume) slope over 24h captures accumulation/distribution pressure.
When OBV slope is POSITIVE (accumulation happened), prices tend to REVERSE -> SHORT.
When OBV slope is NEGATIVE (distribution happened), prices tend to REVERSE -> LONG.

IC test: OOS IC=-0.046 (t=-9.53) at 4h horizon. Works on all 10 tokens tested.
Best horizons: 4h, 8h, 24h. Dies at 48h.

Status: RESEARCH (Gate 1 backtest)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_max, rolling_min,
                    rolling_median, rolling_zscore, rolling_skew, rolling_corr)


# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal parameters --
LOOKBACK = 24           # OBV slope window in hours
THRESHOLD = 1.5         # z-score threshold for entry
DIRECTION = "both"      # "long", "short", or "both"

# -- Trade management --
LEVERAGE = 1.0
MAX_HOLD = 8            # max hours to hold a position
STOP_MULT = 3.0
TRAIL_MULT = 3.0
EDGE = 0.35
MIN_HOLD = 1
NO_STOP_BARS = 0

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  STRATEGY FUNCTION
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """OBV Slope Divergence — mean-reversion signal."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)

    # Compute OBV: cumulative sum of signed volume
    price_change = np.diff(close, prepend=close[0])
    signed_vol = np.where(price_change > 0, volume,
                          np.where(price_change < 0, -volume, 0.0))
    obv = np.cumsum(signed_vol)

    # OBV slope: deviation from rolling mean over LOOKBACK window
    obv_slope = obv - rolling_mean(obv, LOOKBACK)

    # Z-score over 4x window for normalization
    obv_slope_zscore = rolling_zscore(obv_slope, LOOKBACK * 4)

    # MEAN-REVERSION signals (edge detection — fire on threshold crossing only):
    # Distribution happened (negative OBV slope) -> expect bounce -> LONG
    long_level = obv_slope_zscore < -THRESHOLD
    long_prev = np.roll(long_level, 1); long_prev[0] = False
    long_signal = long_level & ~long_prev  # just crossed below -THRESHOLD

    # Accumulation happened (positive OBV slope) -> expect reversal -> SHORT
    short_level = obv_slope_zscore > THRESHOLD
    short_prev = np.roll(short_level, 1); short_prev[0] = False
    short_signal = short_level & ~short_prev  # just crossed above THRESHOLD

    # Compose entry mask and direction
    if DIRECTION == "long":
        entry = long_signal
        direction = np.ones(n, dtype=np.int8)
    elif DIRECTION == "short":
        entry = short_signal
        direction = -np.ones(n, dtype=np.int8)
    else:  # "both"
        entry = long_signal | short_signal
        direction = np.where(long_signal, 1,
                             np.where(short_signal, -1, 0)).astype(np.int8)

    # Warmup guard
    entry[:max(LOOKBACK * 4, 200)] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=999,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        name='s504_obv_slope_mr',
        breakeven_atr=0.5,
    )
