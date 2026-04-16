"""
s519 — Volatility Cluster (DVOL Rank)
=======================================

Signal: Deribit BTC DVOL 30-day percentile rank (implied volatility level
relative to recent history).

  LONG  when dvol_rank > 0.80 (vol elevated = fear = buy the dip)
  SHORT when dvol_rank < 0.20 (vol compressed = complacency = sell)
  Entry only on first bar of each new day.

Tokens: BTC (uses BTC DVOL), SOL (uses BTC DVOL as market-wide proxy).

Exit: Trail 2.5 ATR, stop 2.5 ATR, no TP, max hold 14 days, 2x leverage.

BIAS FIX: DVOL daily value for day D is lagged to D+1 (available next day).

Data: data/alternative/deribit_options/dvol/btc_dvol_daily.json
  OHLC candles: [timestamp_ms, open, high, low, close]
  1826 records, 2021-03-24 to 2026-03-23.

Results (24mo, skip-wf):
  Sharpe 0.84, Calmar 0.57, MaxDD -26.7%, 559 trades, +32.5% total.
  Win rate 48.3%, payoff 1.27, PF 1.19.

Results (12mo, skip-wf):
  Sharpe -0.57, -11.8% total (recent regime unfavorable).
  6/13 months positive, worst month -9.0%.

Market: PERP | Leverage: 2x | Hold: 1-14 days
"""

import json
import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_zscore)


# ======================================================================
#  TOKEN ALLOWLIST
# ======================================================================
ALLOWED_TOKENS = {'BTC', 'SOL'}

# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal --
ZSCORE_WINDOW = 30              # 30-day rolling z-score (daily data)
THRESHOLD_LONG = 1.0            # DVOL z > 1.0 → fear → LONG
THRESHOLD_SHORT = -1.0          # DVOL z < -1.0 → complacency → SHORT
RANK_LONG = 0.80                # DVOL rank > 80th percentile → fear → LONG
RANK_SHORT = 0.20               # DVOL rank < 20th percentile → complacency → SHORT
DIRECTION = "both"

# -- Trade management --
LEVERAGE = 2.0
STOP_MULT = 2.5                 # hard stop in ATR
TRAIL_MULT = 2.5                # trailing stop in ATR
TARGET_MULT = 999.0             # no TP — trail captures profit
MAX_HOLD = 336                  # 14 days in hours
MIN_HOLD = 24                   # 24h minimum hold
NO_STOP_BARS = 24               # 24h stop protection after entry
EDGE = 0.30

# -- Warmup --
WARMUP = 800                    # hours (~33 days)

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  DATA CACHE
# ======================================================================

_DVOL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "deribit_options", "dvol", "btc_dvol_daily.json",
)

_dvol_series: pd.Series | None = None
_dvol_loaded: bool = False
_aligned_cache: dict = {}


def _load_dvol_data():
    """Load BTC DVOL daily close from JSON. No-op after first call."""
    global _dvol_series, _dvol_loaded
    if _dvol_loaded:
        return
    _dvol_loaded = True

    if not os.path.exists(_DVOL_PATH):
        print(f"  [s519] WARNING: BTC DVOL not found at {_DVOL_PATH}")
        return

    try:
        with open(_DVOL_PATH, "r") as f:
            raw = json.load(f)
    except Exception as exc:
        print(f"  [s519] WARNING: Failed to load BTC DVOL: {exc}")
        return

    # Each record: [timestamp_ms, open, high, low, close]
    timestamps = [r[0] for r in raw]
    closes = [r[4] for r in raw]

    dates = pd.to_datetime(timestamps, unit="ms").normalize()
    _dvol_series = pd.Series(closes, index=dates, dtype=np.float64)
    _dvol_series = _dvol_series[~_dvol_series.index.duplicated(keep="last")]
    _dvol_series = _dvol_series.sort_index()

    print(f"  [s519] Loaded BTC DVOL: {len(_dvol_series)} daily records "
          f"({_dvol_series.index[0].date()} to {_dvol_series.index[-1].date()})")


def _get_dvol_zscore_aligned(idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get daily DVOL z-score forward-filled to 1H index.

    BIAS FIX: Daily DVOL for date D is shifted to D+1 (not available same day).
    Z-score computed on daily data first, then forward-filled to hourly.
    """
    cache_key = ("zscore", len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    if _dvol_series is None:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
        _aligned_cache[cache_key] = result
        return result

    # Compute z-score on daily data
    dvol_mean = _dvol_series.rolling(ZSCORE_WINDOW, min_periods=10).mean()
    dvol_std = _dvol_series.rolling(ZSCORE_WINDOW, min_periods=10).std()
    dvol_z = ((_dvol_series - dvol_mean) / dvol_std.replace(0, np.nan))

    # BIAS FIX: shift by +1 day — day D's DVOL only available at D+1
    dvol_z_shifted = dvol_z.copy()
    dvol_z_shifted.index = dvol_z_shifted.index + pd.Timedelta(days=1)

    # Forward-fill to hourly index
    daily_dates = idx_1h.normalize()
    aligned = dvol_z_shifted.reindex(daily_dates, method="ffill")
    aligned.index = idx_1h
    result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


def _get_dvol_rank_aligned(idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get daily DVOL 30d percentile rank forward-filled to 1H index.

    Percentile rank: where current DVOL sits in its 30-day rolling window (0-1).
    BIAS FIX: Daily DVOL for date D is shifted to D+1.
    """
    cache_key = ("rank", len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    if _dvol_series is None:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
        _aligned_cache[cache_key] = result
        return result

    # Rolling percentile rank: fraction of values in window <= current value
    dvol_rank = _dvol_series.rolling(ZSCORE_WINDOW, min_periods=10).apply(
        lambda x: (x[-1] >= x[:-1]).mean(), raw=True,
    )

    # BIAS FIX: shift by +1 day
    dvol_rank_shifted = dvol_rank.copy()
    dvol_rank_shifted.index = dvol_rank_shifted.index + pd.Timedelta(days=1)

    # Forward-fill to hourly index
    daily_dates = idx_1h.normalize()
    aligned = dvol_rank_shifted.reindex(daily_dates, method="ffill")
    aligned.index = idx_1h
    result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Volatility Cluster — DVOL z-score contrarian."""
    _load_dvol_data()

    close = ctx.ind_1h['close']
    n = len(close)

    # Token allowlist filter — BTC and SOL only (both use BTC DVOL)
    if ctx.ticker not in ALLOWED_TOKENS:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            market_type=MARKET,
            leverage=LEVERAGE,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=TARGET_MULT,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            name='s519_vol_cluster',
        )

    # Get DVOL z-score aligned to hourly bars (already lagged 1 day)
    dvol_z = _get_dvol_zscore_aligned(ctx.idx_1h)
    dvol_z = np.nan_to_num(dvol_z, nan=0.0)

    # Get DVOL rank aligned to hourly bars (already lagged 1 day)
    dvol_rank = _get_dvol_rank_aligned(ctx.idx_1h)
    dvol_rank = np.nan_to_num(dvol_rank, nan=0.5)

    # Day boundaries — entry only on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Combined signal: rank for entry, z-score for conviction
    # LONG when DVOL rank > 80th pctile (fear = buy the dip)
    # SHORT when DVOL rank < 20th pctile (complacency = sell)
    long_signal = (dvol_rank > RANK_LONG) & day_change
    short_signal = (dvol_rank < RANK_SHORT) & day_change

    # Direction
    if DIRECTION == "long":
        entry = long_signal
        direction = np.ones(n, dtype=np.int8)
    elif DIRECTION == "short":
        entry = short_signal
        direction = -np.ones(n, dtype=np.int8)
    else:
        entry = long_signal | short_signal
        direction = np.where(long_signal, 1,
                             np.where(short_signal, -1, 0)).astype(np.int8)

    entry[:WARMUP] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        name='s519_vol_cluster',
    )
