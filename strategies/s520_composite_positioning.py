"""
S520 — Per-Token Adaptive Composite Positioning
=================================================

Signal: weighted composite of 3 independent signal families, each computed as
a 14-day rolling z-score from 5-min Binance metrics resampled to daily:

  1. OI signal       (weight=0.40): z-score of sum_open_interest_value
  2. Positioning      (weight=0.35): z-score of sum_toptrader_long_short_ratio
  3. Flow             (weight=0.25): z-score of sum_taker_long_short_vol_ratio

Per-token IC signs from /tmp/token_signal_configs.json determine whether each
signal is used in momentum or contrarian mode (multiplied by sign(IC)).

Composite = 0.40 * OI_z * sign(OI_ic) + 0.35 * Pos_z * sign(Pos_ic) + 0.25 * Flow_z * sign(Flow_ic)

Entry: |composite| > 1.5 on first bar of new day (signal lagged by 1 day).
Direction: sign(composite).
Hold: 14 days (336h). Stop: 3.0 ATR. Trail: 2.5 ATR.

Data: per-token 5-min parquet -> daily resample -> 1-day lag -> ffill to 1H.
Tokens without config: no trades.

IC basis: 227-token scan, OI mean|IC|=0.27, Positioning=0.25, Flow=0.19.
Status: RESEARCH (Gate 3 backtest)
"""

import os
import json
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean, rolling_std, rolling_zscore)


# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal parameters --
ZSCORE_WINDOW_DAYS = 30       # 30 days for rolling z-score (computed on daily data)
THRESHOLD = 2.0               # composite z-score threshold for entry
DIRECTION = "both"            # "long", "short", or "both"

# -- Composite weights (proportional to mean |IC|) --
W_OI = 0.40
W_POS = 0.35
W_FLOW = 0.25

# -- Trade management --
LEVERAGE = 1.0                # conservative start
MAX_HOLD = 336                # 14 days in hours
STOP_MULT = 6.0               # stop loss in ATR multiples
TRAIL_MULT = 4.0              # trailing stop in ATR multiples
EDGE = 0.30                   # Kelly edge estimate
MIN_HOLD = 48                 # minimum 48h hold before exit allowed
NO_STOP_BARS = 72             # 72h stop protection after entry

# -- Warmup --
WARMUP = 400                  # skip first 400 bars (~17 days, covers 14d z-score + buffer)

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  LOAD PER-TOKEN SIGNAL CONFIGS
# ======================================================================

_CONFIG_PATH = "/tmp/token_signal_configs.json"
_token_configs: dict = {}

if os.path.exists(_CONFIG_PATH):
    with open(_CONFIG_PATH) as _f:
        _token_configs = json.load(_f)
    print(f"  [s520] Loaded signal configs for {len(_token_configs)} tokens")
else:
    print(f"  [s520] WARNING: Config not found at {_CONFIG_PATH}")


# ======================================================================
#  MODULE-LEVEL DATA CACHE (loaded once per symbol, reused across calls)
# ======================================================================

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_metrics", "5min",
)

# {symbol: pd.Series with DatetimeIndex -> daily composite z-score}
_composite_cache: dict = {}
_daily_loaded: set = set()

# Per-call alignment cache: {(symbol, n_bars, first_ts, last_ts): np.ndarray}
_aligned_cache: dict = {}


def _daily_zscore(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling z-score on daily array using pandas for vectorized computation."""
    s = pd.Series(arr, dtype=np.float64)
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
    z = (s - mu) / sd.replace(0, np.nan)
    return z.values


def _load_daily_signals(symbol: str, ticker: str):
    """Load 5-min parquet, resample to daily, compute z-scores and composite at daily level.
    No-op if already loaded."""
    if symbol in _daily_loaded:
        return
    _daily_loaded.add(symbol)

    if ticker not in _token_configs:
        return

    parquet_path = os.path.join(_DATA_DIR, f"{symbol}_5min.parquet")
    if not os.path.exists(parquet_path):
        return

    try:
        df = pd.read_parquet(
            parquet_path,
            columns=["create_time", "sum_open_interest_value",
                      "sum_toptrader_long_short_ratio",
                      "sum_taker_long_short_vol_ratio"],
        )
    except Exception as exc:
        print(f"  [s520] WARNING: Failed to load {parquet_path}: {exc}")
        return

    df["create_time"] = pd.to_datetime(df["create_time"])
    df = df.set_index("create_time").sort_index()

    # Resample 5-min to daily: take last value per day
    daily = df.resample("1D").last().dropna(how="all")

    cfg = _token_configs[ticker]

    # Get IC signs for each family
    oi_sign = np.sign(cfg["families"]["OI"]["ic"]) if "OI" in cfg["families"] else 0.0
    pos_sign = np.sign(cfg["families"]["positioning"]["ic"]) if "positioning" in cfg["families"] else 0.0
    flow_sign = np.sign(cfg["families"]["flow"]["ic"]) if "flow" in cfg["families"] else 0.0

    # Compute z-scores at DAILY level (proper rolling stats on daily data)
    oi_vals = daily["sum_open_interest_value"].values if "sum_open_interest_value" in daily.columns else np.array([])
    pos_vals = daily["sum_toptrader_long_short_ratio"].values if "sum_toptrader_long_short_ratio" in daily.columns else np.array([])
    flow_vals = daily["sum_taker_long_short_vol_ratio"].values if "sum_taker_long_short_vol_ratio" in daily.columns else np.array([])

    n_days = len(daily)
    if n_days < ZSCORE_WINDOW_DAYS:
        return

    oi_z = _daily_zscore(oi_vals, ZSCORE_WINDOW_DAYS) if len(oi_vals) == n_days else np.zeros(n_days)
    pos_z = _daily_zscore(pos_vals, ZSCORE_WINDOW_DAYS) if len(pos_vals) == n_days else np.zeros(n_days)
    flow_z = _daily_zscore(flow_vals, ZSCORE_WINDOW_DAYS) if len(flow_vals) == n_days else np.zeros(n_days)

    # Replace NaN with 0
    oi_z = np.nan_to_num(oi_z, nan=0.0)
    pos_z = np.nan_to_num(pos_z, nan=0.0)
    flow_z = np.nan_to_num(flow_z, nan=0.0)

    # Composite at daily level with IC-sign flipping
    composite = (W_OI * oi_z * oi_sign +
                 W_POS * pos_z * pos_sign +
                 W_FLOW * flow_z * flow_sign)

    # Shift by 1 day: signal from day D used on day D+1 (avoid lookahead)
    composite_shifted = np.empty_like(composite)
    composite_shifted[0] = np.nan
    composite_shifted[1:] = composite[:-1]

    _composite_cache[symbol] = pd.Series(
        composite_shifted, index=daily.index, dtype=np.float64,
    )


def _get_composite_aligned(symbol: str, ticker: str,
                           idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get daily composite signal forward-filled to 1H index.

    Returns array of shape (n,) with the composite z-score, NaN where unavailable.
    """
    cache_key = (symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)

    if symbol not in _composite_cache:
        result = np.full(n, np.nan, dtype=np.float64)
        _aligned_cache[cache_key] = result
        return result

    series = _composite_cache[symbol]
    # Forward-fill daily composite to 1H via date normalization
    aligned = series.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h
    result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY FUNCTION
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Composite Positioning — per-token adaptive multi-signal strategy."""
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker  # e.g. "BTC"
    symbol = ticker + "USDT"  # e.g. "BTCUSDT"

    # Empty result for tokens not in config
    if ticker not in _token_configs:
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
            name='s520_composite_positioning',
            breakeven_atr=0.5,
        )

    # Load data for this symbol (no-op if already loaded)
    _load_daily_signals(symbol, ticker)

    # Get composite signal aligned to 1H bars
    composite = _get_composite_aligned(symbol, ticker, ctx.idx_1h)

    # Detect day boundaries: entry only on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Entry signals: |composite| > threshold
    long_level = composite > THRESHOLD
    short_level = composite < -THRESHOLD

    # Edge detection: fire on threshold crossing or new day still above
    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)

    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    # Only fire on first bar of new day
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

    # Warmup guard
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
        name='s520_composite_positioning',
        breakeven_atr=0.5,
    )
