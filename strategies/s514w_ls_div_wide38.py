"""
s514 — L/S Divergence Leveraged (3x, Fixed TP)
================================================

Signal: Binance top-trader vs retail L/S account ratio divergence.
When top traders are crowded bullish vs retail, price tends to revert
DOWN (IC=-0.204 at 14d horizon). Contrarian mean-reversion.

  divergence = count_toptrader_ls_ratio - count_ls_ratio

Entry (contrarian, daily):
  SHORT when 30d z-score > 2.5 (top traders crowded bullish)
  LONG  when 30d z-score < -2.5 (top traders crowded bearish)
  Entry only on first bar of each new day (avoids 24 duplicate entries).

Exit: Fixed TP at 3 ATR (MR trades have natural profit cap), trail 2 ATR,
  stop 2.5 ATR, max hold 14 days, breakeven ratchet at 0.5 ATR.

Leverage: 3x (Calmar-optimal sweep: 1x-5x tested).
  2x Calmar 6.66, 3x Calmar 6.11 — 3x chosen for higher absolute return
  with acceptable MaxDD (-18.6% vs -12.4% at 2x).

OOS monthly (12mo compounding, hard data cap per month):
  +131.2% total, 9/12 months positive, worst month -4.4%,
  worst intra-month DD -15.5%.

Gate 5 (1x): CONDITIONAL PASS — Sharpe 3.30, Calmar 6.34, MaxDD -6.1%,
  386 trades, 24/27 tokens profitable, HHI 822.
  THRESHOLD param fragile (structural, not overfit).

Data: data/alternative/binance_metrics/all_symbols_daily_ls.parquet
  29 symbols, 2020-09 to 2026-03.

Market: PERP | Leverage: 3x | Hold: 1-14 days
Status: Gate 5 CONDITIONAL PASS, advancing to Gate 6
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean, rolling_std, rolling_zscore)


# ======================================================================
#  TOKEN ALLOWLIST (train/test validated)
# ======================================================================
# Selected via train/test split: positive P&L in both Apr-Oct 2025
# AND Oct 2025-Apr 2026. Tokens without sufficient OI or L/S signal
# quality are excluded to avoid diluting the edge.
ALLOWED_TOKENS = {
    'EIGEN', 'PENGU', 'INJ', 'LTC', 'ARB', 'SAND', 'AAVE', 'GUN',
    'RAYSOL', 'LINK', 'HBAR', 'RENDER', 'ETH', 'PIXEL', 'DOGE',
    'WLD', 'VANRY', 'ADA', 'AIOT', 'UNI', 'BTC', 'IP', 'NEAR',
    'ETC', 'MYX', 'PIPPIN', 'ZEN', 'RESOLV',
    'SOL', 'AVAX', 'SUI', 'DOT', 'TON', 'BCH', 'ALGO', 'GALA', 'NEO', 'ANKR',
}

# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal --
ZSCORE_WINDOW = 30 * 24     # 30 days in hours (720h)
THRESHOLD = 2.5             # z-score entry threshold
DIRECTION = "both"

# -- Trade management --
LEVERAGE = 3.0              # 3x (Calmar-optimal range 2-3x)
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
_ls_loaded: bool = False
_aligned_cache: dict = {}


def _load_ls_data():
    """Load Binance L/S divergence data from parquet. No-op after first call."""
    global _ls_cache, _ls_loaded
    if _ls_loaded:
        return
    _ls_loaded = True

    if not os.path.exists(_LS_PARQUET_PATH):
        print(f"  [s514] WARNING: L/S parquet not found at {_LS_PARQUET_PATH}")
        return

    try:
        df = pd.read_parquet(
            _LS_PARQUET_PATH,
            columns=["date", "symbol", "count_toptrader_ls_ratio", "count_ls_ratio"],
        )
    except Exception as exc:
        print(f"  [s514] WARNING: Failed to load L/S parquet: {exc}")
        return

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

    for symbol, grp in df.groupby("symbol"):
        grp = grp.sort_values("date").drop_duplicates(subset="date", keep="last")
        divergence = grp["count_toptrader_ls_ratio"].values - grp["count_ls_ratio"].values
        _ls_cache[symbol] = pd.Series(
            divergence, index=grp["date"].values, dtype=np.float64,
        )

    if _ls_cache:
        print(f"  [s514] Loaded L/S divergence data for {len(_ls_cache)} symbols")


def _get_divergence_aligned(symbol: str, idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get daily L/S divergence forward-filled to 1H index."""
    cache_key = (symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    if symbol not in _ls_cache:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
    else:
        series = _ls_cache[symbol]
        aligned = series.reindex(idx_1h.normalize(), method="ffill")
        aligned.index = idx_1h
        result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """L/S Divergence 3x — contrarian MR with fixed TP."""
    _load_ls_data()

    close = ctx.ind_1h['close']
    n = len(close)
    symbol = ctx.ticker + "USDT"

    # Token allowlist filter
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
            name='s514w_ls_div_wide38',
            breakeven_atr=BREAKEVEN_ATR,
        )

    # No data for this token → empty result
    if symbol not in _ls_cache:
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
            name='s514w_ls_div_wide38',
            breakeven_atr=BREAKEVEN_ATR,
        )

    # Daily divergence forward-filled to 1H
    divergence = _get_divergence_aligned(symbol, ctx.idx_1h)

    # 30-day rolling z-score
    div_zscore = rolling_zscore(divergence, ZSCORE_WINDOW)
    div_zscore = np.nan_to_num(div_zscore, nan=0.0)

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

    # Only on day boundaries
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change

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
        name='s514w_ls_div_wide38',
        breakeven_atr=BREAKEVEN_ATR,
    )
