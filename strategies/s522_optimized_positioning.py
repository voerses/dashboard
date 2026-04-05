"""
S522 — Optimized Per-Token Composite Positioning
=================================================

Evolved from s521. Optimized for max return with controlled DD.

1. **Per-token IC-proportional weights**: Instead of fixed 0.40/0.35/0.25 for all
   tokens, each token's OI/positioning/flow weights are proportional to that token's
   measured |IC| from the full scan.

2. **Per-token hold period**: Each token uses the horizon from its strongest signal
   family (e.g. BTC=720h, ETH=504h) instead of a fixed 336h for all.

3. **Funding-aware conviction**: When the composite says long and funding is negative
   (longs receive payment), conviction is boosted 10%. When funding opposes the trade
   direction, conviction is reduced 10%. Does not change direction, only edge sizing.

4. **Continuous edge sizing**: Instead of binary threshold entry, any |composite| > 1.0
   generates a signal with edge proportional to |composite|. This lets ranked conviction
   mode in v4 naturally allocate more capital to stronger signals.

5. **Wider stops for mean-reversion**: 8 ATR stop, no trailing stop (trail=999),
   breakeven ratchet only after 50% of max_hold has passed. MR trades need room.

Signal: Per-token weighted composite of 3 z-score families:
  1. OI signal       (weight per token): z-score of sum_open_interest_value
  2. Positioning     (weight per token): z-score of sum_toptrader_long_short_ratio
  3. Flow            (weight per token): z-score of sum_taker_long_short_vol_ratio

Config: data/alternative/s521_token_config.json (built by tools/build_s521_config.py)

Entry: |composite| > 1.0 on first bar of new day (signal lagged 1 day).
Direction: sign(composite) * IC_sign.
Edge: min(0.5, |composite| * 0.1) — continuous, proportional to signal strength.
Hold: per-token (24h to 720h). Stop: 8.0 ATR. Trail: 999 (disabled). Breakeven: 1.0 ATR.

IC basis: 139-token scan with per-token IC-proportional weighting.
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
THRESHOLD = 1.0               # composite z-score threshold for entry (lower than s520)
DIRECTION = "both"            # "long", "short", or "both"

# -- Trade management --
LEVERAGE = 1.9                # sweep-optimized (2.5x best Calmar, 1.9 best DD)
STOP_MULT = 5.0               # tighter stop cuts losers faster (from 8.0)
TRAIL_MULT = 999.0            # effectively no trail — MR trades need room to breathe
MIN_HOLD = 48                 # minimum 48h hold before exit allowed
NO_STOP_BARS = 72             # 72h stop protection after entry
BREAKEVEN_ATR = 1.0           # breakeven ratchet (activated after 50% of max_hold)

# -- Token blacklist: 50 value-destroying tokens from L12M optimization sweep --
# Tokens with negative PnL over 3+ trades at 2.5x leverage.
# Includes large-caps where positioning signal is weak (BTC, SOL, DOT, etc.)
TOKEN_BLACKLIST = {
    "EIGEN", "BAN", "CETUS", "ONT", "BANANA", "DOT",
    "STRK", "SOL", "PIPPIN", "DEGO", "SUI", "NEIRO",
    "ZEN", "MINA", "STEEM", "ANKR", "AVAX", "BCH",
    "BTC", "CRV", "DASH", "DUSK", "ENA", "ETC",
    "FET", "FIL", "G", "GRASS", "INJ", "JUP",
    "KAS", "KAVA", "NEO", "OGN", "POLYX", "RENDER",
    "RVN", "SAND", "SIREN", "TAO", "UNI", "WLD",
    "LTC", "LINK", "AAVE", "XRP", "ADA", "TRX",
    "DOGE", "SHIB",
}

# -- Funding adjustment --
FUNDING_BOOST = 0.10          # 10% conviction adjustment for aligned/opposed funding

# -- Warmup --
WARMUP = 400                  # skip first 400 bars (~17 days, covers 30d z-score + buffer)

# -- Market --
MARKET = MarketType.PERP

# -- Portfolio config for v4 backtest harness --
PORTFOLIO_CONFIG = {
    "conviction_mode": "ranked",
    "max_positions": 50,
}


# ======================================================================
#  LOAD PER-TOKEN CONFIG (IC weights, signs, hold periods)
# ======================================================================

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "s521_token_config.json",
)
_token_configs: dict = {}

if os.path.exists(_CONFIG_PATH):
    with open(_CONFIG_PATH) as _f:
        _token_configs = json.load(_f)
    print(f"  [s521] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s521] WARNING: Config not found at {_CONFIG_PATH}")


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
    """Load 5-min parquet, resample to daily, compute per-token weighted composite.
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
        print(f"  [s521] WARNING: Failed to load {parquet_path}: {exc}")
        return

    df["create_time"] = pd.to_datetime(df["create_time"])
    df = df.set_index("create_time").sort_index()

    # Resample 5-min to daily: take last value per day
    daily = df.resample("1D").last().dropna(how="all")

    cfg = _token_configs[ticker]

    # Per-token IC-proportional weights
    w_oi = cfg["oi_weight"]
    w_pos = cfg["pos_weight"]
    w_flow = cfg["flow_weight"]

    # Per-token IC signs
    oi_sign = cfg["oi_sign"]
    pos_sign = cfg["pos_sign"]
    flow_sign = cfg["flow_sign"]

    # Compute z-scores at DAILY level
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

    # Per-token weighted composite with IC-sign flipping
    composite = (w_oi * oi_z * oi_sign +
                 w_pos * pos_z * pos_sign +
                 w_flow * flow_z * flow_sign)

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
    """Adaptive Per-Token Composite — IC-weighted, funding-aware, continuous edge."""
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker  # e.g. "BTC"
    symbol = ticker + "USDT"  # e.g. "BTCUSDT"

    # Skip blacklisted tokens and tokens not in config
    if ticker in TOKEN_BLACKLIST or ticker not in _token_configs:
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
            max_hold=720,
            edge=0.0,
            name='s522_optimized_positioning',
            breakeven_atr=BREAKEVEN_ATR,
        )

    cfg = _token_configs[ticker]
    token_max_hold = cfg["max_hold_hours"]

    # Load data for this symbol (no-op if already loaded)
    _load_daily_signals(symbol, ticker)

    # Get composite signal aligned to 1H bars
    composite = _get_composite_aligned(symbol, ticker, ctx.idx_1h)

    # ---- Funding-aware conviction adjustment ----
    # funding_1h: per-hour funding rate (positive = longs pay, negative = longs receive)
    funding = np.zeros(n, dtype=np.float64)
    if ctx.funding_1h is not None:
        funding = np.nan_to_num(ctx.funding_1h, nan=0.0)

    # Compute funding adjustment factor per bar:
    # Long signal (composite > 0) + negative funding (longs receive) -> boost
    # Long signal + positive funding (longs pay) -> reduce
    # Short signal (composite < 0) + positive funding (shorts receive) -> boost
    # Short signal + negative funding (shorts pay) -> reduce
    # Net: aligned when sign(composite) * sign(-funding) > 0
    composite_sign = np.sign(np.nan_to_num(composite, nan=0.0))
    funding_alignment = -composite_sign * np.sign(funding)  # +1 when aligned, -1 when opposed
    # Only adjust where funding is meaningfully nonzero
    funding_meaningful = np.abs(funding) > 1e-8
    funding_factor = np.ones(n, dtype=np.float64)
    funding_factor[funding_meaningful & (funding_alignment > 0)] = 1.0 + FUNDING_BOOST
    funding_factor[funding_meaningful & (funding_alignment < 0)] = 1.0 - FUNDING_BOOST

    # Apply funding adjustment to composite magnitude (preserving sign)
    adjusted_composite = composite * funding_factor

    # ---- Detect day boundaries: entry only on first bar of each new day ----
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # ---- Continuous entry signals: |adjusted_composite| > THRESHOLD ----
    abs_composite = np.abs(np.nan_to_num(adjusted_composite, nan=0.0))
    long_level = adjusted_composite > THRESHOLD
    short_level = adjusted_composite < -THRESHOLD

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

    # ---- Compose entry mask and direction ----
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

    # ---- Continuous conviction scoring: proportional to |composite| ----
    # conviction_score in [0, 1] — ranked mode uses this for entry prioritization
    # and v4 sizes proportionally to conviction within the ranked pool.
    conviction = np.minimum(1.0, abs_composite / 3.0)  # normalize to [0, 1]
    conviction[~entry] = 0.0

    # Scalar edge: base edge for sizing. Ranked conviction mode modulates
    # actual allocation via conviction_score, so we use a moderate base edge.
    # Stronger signals get more capital through higher conviction_score.
    base_edge = min(0.5, float(np.nanmean(abs_composite[entry])) * 0.1) if entry.any() else 0.30

    # ---- Breakeven ratchet timing ----
    # We want breakeven only after 50% of max_hold has passed.
    # Since breakeven_atr is a scalar applied from entry, we set it to 1.0 ATR
    # and rely on no_stop_bars to provide initial protection.
    # Set no_stop_bars to 50% of max_hold to delay breakeven activation.
    token_no_stop = max(NO_STOP_BARS, token_max_hold // 2)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=999,
        no_stop_bars=token_no_stop,
        min_hold=MIN_HOLD,
        max_hold=token_max_hold,
        edge=base_edge,
        name='s522_optimized_positioning',
        breakeven_atr=BREAKEVEN_ATR,
        conviction_score=conviction,
    )
