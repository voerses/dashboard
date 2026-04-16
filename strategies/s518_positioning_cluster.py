"""
s518 — Positioning Cluster (L/S Divergence + OI Decline)
=========================================================

Signal: Binance top-trader vs retail L/S account ratio divergence,
confirmed by declining open interest. Trades only the 11 tokens
identified in R204 as positioning-responsive.

Hypothesis: When positioning is extreme (high L/S divergence z-score)
AND open interest is declining (OI z-score < 0), forced liquidations
are likely incoming, producing a stronger mean-reversion signal.

  divergence = count_toptrader_ls_ratio - count_ls_ratio
  oi_value   = sum_open_interest_value

Entry (contrarian, daily):
  SHORT when div_z30 > 2.5 AND oi_z30 < 0 (crowded bullish + OI declining)
  LONG  when div_z30 < -2.5 AND oi_z30 < 0 (crowded bearish + OI declining)
  Entry only on first bar of each new day.

Exit: Trail 2 ATR (no TP — trail captures profit from positioning unwinds),
  stop 2.5 ATR, max hold 14 days, breakeven ratchet at 0.5 ATR.

Leverage: 3x

Token universe: ADA, APT, ARB, BNB, DOT, INJ, JUP, LTC, OP, UNI, WIF
  (11 positioning-responsive tokens from R204 analysis)

Data: data/alternative/binance_metrics/all_symbols_daily_ls.parquet
  Columns: count_toptrader_ls_ratio, count_ls_ratio, sum_open_interest_value

Market: PERP | Leverage: 3x | Hold: 1-14 days
Based on: s514 (L/S divergence leveraged) with OI confirmation filter
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean, rolling_std, rolling_zscore)


# ======================================================================
#  TOKEN ALLOWLIST — R204 positioning-responsive tokens
# ======================================================================
ALLOWED_TOKENS = {
    'ADA', 'APT', 'ARB', 'BNB', 'DOT', 'INJ', 'JUP', 'LTC', 'OP', 'UNI', 'WIF',
}

# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal --
ZSCORE_WINDOW = 30 * 24     # 30 days in hours (720h)
OI_ZSCORE_WINDOW = 30 * 24  # 30 days in hours for OI z-score
THRESHOLD = 2.5             # z-score entry threshold
DIRECTION = "both"

# -- Trade management --
LEVERAGE = 3.0              # 3x
STOP_MULT = 2.5             # hard stop in ATR
TRAIL_MULT = 2.0            # trailing stop in ATR
TARGET_MULT = 999.0         # no TP — trail captures profit from positioning unwinds
MAX_HOLD = 336              # 14 days in hours
MIN_HOLD = 24               # 24h minimum hold
NO_STOP_BARS = 24           # 24h stop protection after entry
EDGE = 0.35
BREAKEVEN_ATR = 0.5

# -- Warmup --
WARMUP = 800

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  DATA CACHE
# ======================================================================

_LS_PARQUET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_metrics", "all_symbols_daily_ls.parquet",
)

_ls_cache: dict = {}
_oi_cache: dict = {}
_ls_loaded: bool = False
_aligned_cache: dict = {}


def _load_ls_data():
    """Load Binance L/S divergence and OI data from parquet. No-op after first call."""
    global _ls_cache, _oi_cache, _ls_loaded
    if _ls_loaded:
        return
    _ls_loaded = True

    if not os.path.exists(_LS_PARQUET_PATH):
        print(f"  [s518] WARNING: L/S parquet not found at {_LS_PARQUET_PATH}")
        return

    try:
        df = pd.read_parquet(
            _LS_PARQUET_PATH,
            columns=["date", "symbol", "count_toptrader_ls_ratio",
                      "count_ls_ratio", "sum_open_interest_value"],
        )
    except Exception as exc:
        print(f"  [s518] WARNING: Failed to load L/S parquet: {exc}")
        return

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

    for symbol, grp in df.groupby("symbol"):
        grp = grp.sort_values("date").drop_duplicates(subset="date", keep="last")
        # L/S divergence
        divergence = grp["count_toptrader_ls_ratio"].values - grp["count_ls_ratio"].values
        _ls_cache[symbol] = pd.Series(
            divergence, index=grp["date"].values, dtype=np.float64,
        )
        # Open interest value
        oi_vals = grp["sum_open_interest_value"].values
        _oi_cache[symbol] = pd.Series(
            oi_vals, index=grp["date"].values, dtype=np.float64,
        )

    if _ls_cache:
        print(f"  [s518] Loaded L/S divergence + OI data for {len(_ls_cache)} symbols")


def _get_aligned(cache: dict, symbol: str, idx_1h: pd.DatetimeIndex,
                 cache_prefix: str) -> np.ndarray:
    """Get daily metric forward-filled to 1H index with 1-day lag bias fix.

    BIAS FIX: Daily metric for date D covers 00:00-23:55 UTC and is NOT
    available until D+1 00:00. Shift daily index by +1 day so that day D's
    value is only used starting at D+1 bars.
    """
    cache_key = (cache_prefix, symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    if symbol not in cache:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
    else:
        series = cache[symbol]
        # Shift by 1 day: data for date D becomes available at D+1
        shifted = series.copy()
        shifted.index = shifted.index + pd.Timedelta(days=1)
        aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
        aligned.index = idx_1h
        result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Positioning Cluster — L/S divergence + OI decline confirmation."""
    _load_ls_data()

    close = ctx.ind_1h['close']
    n = len(close)
    symbol = ctx.ticker + "USDT"

    # Empty result template
    def _empty():
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
            name='s518_positioning_cluster',
            breakeven_atr=BREAKEVEN_ATR,
        )

    # Token allowlist filter
    if ctx.ticker not in ALLOWED_TOKENS:
        return _empty()

    # No data for this token
    if symbol not in _ls_cache:
        return _empty()

    # Daily divergence forward-filled to 1H (with 1-day lag)
    divergence = _get_aligned(_ls_cache, symbol, ctx.idx_1h, "div")

    # Daily OI value forward-filled to 1H (with 1-day lag)
    oi_value = _get_aligned(_oi_cache, symbol, ctx.idx_1h, "oi")

    # 30-day rolling z-scores
    div_zscore = rolling_zscore(divergence, ZSCORE_WINDOW)
    div_zscore = np.nan_to_num(div_zscore, nan=0.0)

    oi_zscore = rolling_zscore(oi_value, OI_ZSCORE_WINDOW)
    oi_zscore = np.nan_to_num(oi_zscore, nan=0.0)

    # OI declining filter: OI z-score < 0 means OI is below its 30-day mean
    oi_declining = oi_zscore < 0

    # Day boundaries — entry only on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Contrarian signals with edge detection
    short_level = div_zscore > THRESHOLD
    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    long_level = div_zscore < -THRESHOLD
    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)

    # Only on day boundaries AND OI declining
    long_signal = long_signal & day_change & oi_declining
    short_signal = short_signal & day_change & oi_declining

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
        name='s518_positioning_cluster',
        breakeven_atr=BREAKEVEN_ATR,
    )
