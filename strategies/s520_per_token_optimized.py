"""
s520 -- Per-Token Optimized Positioning (5-min data, per-token signals)
=======================================================================

Uses 5-min positioning data resampled to daily, with per-token optimal
signal field and 30-day rolling z-score. Each token uses the field
identified as most predictive in R205 analysis.

Key difference from s514: instead of using a single divergence metric
for all tokens, each token gets its optimal field from R205:
  - JUP uses OI value (strongest IC=-0.181)
  - ARB/ETH/LINK use top-trader L/S sum
  - INJ uses top-trader L/S count
  - ADA/DOGE use retail L/S count

Universe: 7 tokens (JUP, ARB, ETH, LINK, DOGE, ADA, INJ)
  All contrarian. SOL/XRP excluded (negative OOS performance).

Data: data/alternative/binance_metrics/5min/{SYMBOL}_5min.parquet
  Resampled to daily, then 30-day rolling z-score

BIAS DISCIPLINE:
- 5-min data resampled to daily mean
- Daily bar for date D uses data 00:00-23:55 UTC
- Not available until D+1 00:00 -> shift by +1 day
- 30-day rolling z-score on shifted daily data
- Entry on first bar of each new day

Market: PERP | Leverage: 3x | Hold: 1-14 days
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_zscore)


# ======================================================================
#  PER-TOKEN CONFIGURATION -- from R205 walk-forward validation
# ======================================================================
# Format: {token: (5min_column, direction)}
#   direction: -1 = contrarian (negative IC), +1 = momentum (positive IC)
# All tokens use 30-day rolling z-score on daily-resampled 5-min data

TOKEN_CONFIGS = {
    'JUP':  ('sum_open_interest_value',          -1),  # IC=-0.181, L12M PnL: +10.3k
    'ARB':  ('sum_toptrader_long_short_ratio',   -1),  # IC=-0.139, L12M PnL: +4.0k
    'ETH':  ('sum_toptrader_long_short_ratio',   -1),  # IC=-0.096, L12M PnL: -1.5k
    'LINK': ('sum_toptrader_long_short_ratio',   -1),  # IC=-0.087, L12M PnL: +2.3k
    'DOGE': ('count_long_short_ratio',           -1),  # IC=-0.079, L12M PnL: +7.7k
    'ADA':  ('count_long_short_ratio',           -1),  # IC=-0.072, L12M PnL: +4.1k
    'INJ':  ('count_toptrader_long_short_ratio', -1),  # IC=-0.062, L12M PnL: +0.9k
}


# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal --
ZSCORE_WINDOW = 30 * 24     # 30 days in hours (720h) — matches s514
THRESHOLD = 2.5             # z-score entry threshold

# -- Trade management (matched to s514 proven params) --
LEVERAGE = 3.0
STOP_MULT = 2.5             # hard stop in ATR
TRAIL_MULT = 2.0            # trailing stop in ATR
TARGET_MULT = 999.0         # no TP — trail captures profit
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

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_metrics", "5min",
)

_daily_cache: dict = {}     # {symbol: {field: pd.Series with daily index}}
_aligned_cache: dict = {}   # {cache_key: np.ndarray}
_load_attempted: set = set()


def _load_daily_from_5min(symbol: str):
    """Load 5-min data, resample to daily mean. Cached."""
    if symbol in _daily_cache:
        return _daily_cache[symbol]
    if symbol in _load_attempted:
        return None
    _load_attempted.add(symbol)

    path = os.path.join(_DATA_DIR, f"{symbol}_5min.parquet")
    if not os.path.exists(path):
        return None

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        print(f"  [s520] WARNING: Failed to load {path}: {exc}")
        return None

    df['create_time'] = pd.to_datetime(df['create_time'])
    df = df.set_index('create_time').sort_index()

    # Drop non-numeric columns before resampling
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    daily = df[numeric_cols].resample('1D').mean()

    _daily_cache[symbol] = daily
    return daily


def _get_signal_aligned(symbol: str, idx_1h: pd.DatetimeIndex,
                        field: str, direction: int) -> np.ndarray:
    """Compute daily z-score signal, forward-filled to 1H index.

    BIAS FIX: Daily data for date D covers 00:00-23:55 UTC and is NOT
    available until D+1 00:00. Shift daily index by +1 day.
    """
    cache_key = (symbol, field, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    daily = _load_daily_from_5min(symbol)
    if daily is None or field not in daily.columns:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
        _aligned_cache[cache_key] = result
        return result

    series = daily[field].astype(np.float64)

    # Shift by 1 day: data for date D becomes available at D+1
    shifted = series.copy()
    shifted.index = shifted.index + pd.Timedelta(days=1)

    # Forward-fill to hourly index
    aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h

    # 30-day rolling z-score (on the hourly-aligned daily values)
    zscore = rolling_zscore(aligned.values.astype(np.float64), ZSCORE_WINDOW)
    zscore = np.nan_to_num(zscore, nan=0.0)

    # Apply direction
    result = zscore * direction
    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Per-Token Optimized Positioning -- 5-min data, per-token signals."""
    n = len(ctx.ind_1h['close'])
    token = ctx.ticker
    symbol = token + 'USDT'

    # Empty result helper
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
            name='s520_per_token_optimized',
            breakeven_atr=BREAKEVEN_ATR,
        )

    # Only trade configured tokens
    if token not in TOKEN_CONFIGS:
        return _empty()

    field, direction = TOKEN_CONFIGS[token]
    signal = _get_signal_aligned(symbol, ctx.idx_1h, field, direction)

    # Day boundaries -- entry only on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Entry with edge detection (s514 pattern)
    # After direction adjustment: positive signal = favorable for long
    long_level = signal > THRESHOLD
    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)

    short_level = signal < -THRESHOLD
    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    # Only on day boundaries
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change

    entry = long_signal | short_signal
    dir_arr = np.where(long_signal, 1,
                       np.where(short_signal, -1, 0)).astype(np.int8)

    entry[:WARMUP] = False

    return StrategyResult(
        entry_mask=entry,
        direction=dir_arr,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        name='s520_per_token_optimized',
        breakeven_atr=BREAKEVEN_ATR,
    )
