"""
S506 — L/S Divergence (Contrarian Mean-Reversion)
===================================================

Signal: difference between Binance top-trader L/S account ratio and global
L/S account ratio. When top traders are crowded bullish vs retail, price
tends to go DOWN (IC=-0.204 at 14d horizon). Contrarian signal.

  divergence = count_toptrader_ls_ratio - count_ls_ratio

Entry logic (contrarian):
  - SHORT when 30d z-score > 2.5 (top traders crowded bullish -> mean reversion down)
  - LONG  when 30d z-score < -2.5 (top traders crowded bearish -> mean reversion up)

Data: daily parquet, forward-filled to 1H bars. Entry only on FIRST bar of
each new day when z-score crosses threshold (avoids 24 duplicate entries).

Hold: 14 days (336 hours), trail stop at 2 ATR, stop at 2.5 ATR.

IC test: OOS IC=-0.204 at 14d horizon. 29 symbols, 2020-09 to 2026-03.
Status: RESEARCH (Gate 3 backtest)
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean, rolling_std, rolling_zscore)


# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal parameters --
ZSCORE_WINDOW = 30 * 24     # 30 days in hours (720h) for rolling z-score
THRESHOLD = 2.5             # z-score threshold for entry
DIRECTION = "both"          # "long", "short", or "both"

# -- Trade management --
LEVERAGE = 1.0              # conservative for first test
MAX_HOLD = 336              # 14 days in hours
STOP_MULT = 2.5             # stop loss in ATR multiples
TRAIL_MULT = 2.0            # trailing stop in ATR multiples
EDGE = 0.35                 # Kelly edge estimate
MIN_HOLD = 24               # minimum 24h hold before exit allowed
NO_STOP_BARS = 24           # 24h stop protection after entry

# -- Warmup --
WARMUP = 800                # skip first 800 bars (>30 days z-score + buffer)

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  MODULE-LEVEL DATA CACHE (loaded once, reused across strategy calls)
# ======================================================================

_LS_PARQUET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_metrics", "all_symbols_daily_ls.parquet",
)

# {symbol: pd.Series with DatetimeIndex -> divergence}
_ls_cache: dict = {}
_ls_loaded: bool = False

# Per-call alignment cache: {(symbol, n_bars): np.ndarray}
_aligned_cache: dict = {}


def _load_ls_data():
    """Load Binance L/S divergence data from parquet into cache. No-op after first call."""
    global _ls_cache, _ls_loaded
    if _ls_loaded:
        return
    _ls_loaded = True

    if not os.path.exists(_LS_PARQUET_PATH):
        print(f"  [s506] WARNING: L/S parquet not found at {_LS_PARQUET_PATH}")
        return

    try:
        df = pd.read_parquet(
            _LS_PARQUET_PATH,
            columns=["date", "symbol", "count_toptrader_ls_ratio", "count_ls_ratio"],
        )
    except Exception as exc:
        print(f"  [s506] WARNING: Failed to load L/S parquet: {exc}")
        return

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

    # Compute divergence per symbol and cache as Series
    for symbol, grp in df.groupby("symbol"):
        grp = grp.sort_values("date").drop_duplicates(subset="date", keep="last")
        divergence = grp["count_toptrader_ls_ratio"].values - grp["count_ls_ratio"].values
        _ls_cache[symbol] = pd.Series(
            divergence, index=grp["date"].values, dtype=np.float64,
        )

    if _ls_cache:
        print(f"  [s506] Loaded L/S divergence data for {len(_ls_cache)} symbols")


def _get_divergence_aligned(symbol: str, idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get daily L/S divergence forward-filled to 1H index.

    Returns array of shape (n,) with NaN where data is unavailable.
    Uses alignment cache for fast repeat calls.
    """
    # Use first+last timestamp as cache key (not just length) to avoid
    # stale alignments when OOS monthly mode reuses same-length windows.
    cache_key = (symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    if symbol not in _ls_cache:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
    else:
        series = _ls_cache[symbol]
        # Normalize idx_1h to date-level for matching, then ffill
        aligned = series.reindex(idx_1h.normalize(), method="ffill")
        aligned.index = idx_1h  # restore original hourly index
        result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY FUNCTION
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """L/S Divergence — contrarian mean-reversion signal."""
    _load_ls_data()

    close = ctx.ind_1h['close']
    n = len(close)

    # Map ctx.ticker (e.g., 'BTC') to parquet symbol (e.g., 'BTCUSDT')
    symbol = ctx.ticker + "USDT"

    # Graceful degradation: if no data for this token, return empty result
    if symbol not in _ls_cache:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            market_type=MARKET,
            leverage=LEVERAGE,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=999,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            name='s506_ls_divergence',
            breakeven_atr=0.5,
        )

    # Get daily divergence forward-filled to 1H bars
    divergence = _get_divergence_aligned(symbol, ctx.idx_1h)

    # Compute 30-day rolling z-score of the divergence
    # (ZSCORE_WINDOW is in hours = 720h for 30 days)
    div_zscore = rolling_zscore(divergence, ZSCORE_WINDOW)

    # Replace any NaN z-scores with 0 (no signal)
    div_zscore = np.nan_to_num(div_zscore, nan=0.0)

    # Detect day boundaries: entry only on first bar of each new day
    # when z-score crosses threshold. This prevents 24 duplicate entries.
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    # Compare consecutive dates — new day when date changes
    day_change[1:] = dates[1:] != dates[:-1]

    # CONTRARIAN signals with edge detection (fire on threshold crossing only):
    # Top traders crowded bullish (z > threshold) -> expect mean reversion DOWN -> SHORT
    short_level = div_zscore > THRESHOLD
    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)  # crossed OR new day still above

    # Top traders crowded bearish (z < -threshold) -> expect mean reversion UP -> LONG
    long_level = div_zscore < -THRESHOLD
    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)  # crossed OR new day still below

    # Only fire entries on the first bar of each new day
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change

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

    # Warmup guard — skip first WARMUP bars
    entry[:WARMUP] = False

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
        name='s506_ls_divergence',
        breakeven_atr=0.5,
    )
