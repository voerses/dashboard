"""
s521 — Fear & Greed + SP500 Trend/Momentum
============================================

Signal: Crypto Fear & Greed Index combined with SP500 5-day rate of change.
This is a TREND/MOMENTUM signal — distinct from MR/positioning strategies.

Hypothesis: When greed is extreme (FG > 72) AND equities are rising (SP500
5d ROC > 0), crypto is in a broad risk-on regime favoring long exposure.
When fear is extreme (FG < 25) AND equities are falling (SP500 5d ROC < 0),
crypto faces maximum headwinds favoring short exposure.

Based on R202 walk-forward validated rule:
  fg_bullish (FG > 72) AND sp500_roc5 > 0
  Sharpe 1.99, 4/4 quarters, 10/10 tokens positive

Entry (daily):
  LONG  when FG > 72 AND SP500 5d ROC > 0  (greed + equity momentum)
  SHORT when FG < 25 AND SP500 5d ROC < 0  (fear + equity weakness)
  Entry only on first bar of each new day.

Exit: Trail 3 ATR, stop 3 ATR, no TP, max hold 14 days.

Leverage: 3x

Token universe: Top 20 by OI
  BTC ETH SOL XRP DOGE ADA AVAX LINK BNB DOT
  LTC UNI NEAR ARB SUI APT INJ AAVE FIL ATOM

Data:
  Fear & Greed: data/alternative/fear_greed/fear_greed_index.parquet
  SP500:        data/alternative/macro/sp500.parquet
  Both lagged 1 day (no look-ahead)

Market: PERP | Leverage: 3x | Hold: 1-14 days
"""

import os
import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult, MarketType


# ======================================================================
#  TOKEN ALLOWLIST — Top 20 by OI
# ======================================================================
ALLOWED_TOKENS = {
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK', 'BNB', 'DOT',
    'LTC', 'UNI', 'NEAR', 'ARB', 'SUI', 'APT', 'INJ', 'AAVE', 'FIL', 'ATOM',
}

# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal thresholds (from R202) --
FG_BULL_THRESHOLD = 72      # Fear & Greed above this = greed regime
FG_BEAR_THRESHOLD = 25      # Fear & Greed below this = fear regime
SP500_ROC_WINDOW = 5        # 5-day rate of change for SP500

# -- Trade management --
LEVERAGE = 3.0              # 3x as specified
STOP_MULT = 3.0             # 3 ATR hard stop
TRAIL_MULT = 3.0            # 3 ATR trailing stop
TARGET_MULT = 999.0         # no TP — trail captures trend
MAX_HOLD = 336              # 14 days in hours
MIN_HOLD = 24               # 24h minimum hold
NO_STOP_BARS = 48           # 48h stop protection — daily macro signal
EDGE = 0.35
BREAKEVEN_ATR = 0.5
DIRECTION = "both"          # both directions

# -- Warmup --
WARMUP = 200

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  DATA CACHE
# ======================================================================

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FG_PATH = os.path.join(_BASE_DIR, "data", "alternative", "fear_greed",
                         "fear_greed_index.parquet")
_SP500_PATH = os.path.join(_BASE_DIR, "data", "alternative", "macro",
                            "sp500.parquet")

_fg_series: pd.Series = None       # date -> FG value (lagged 1 day)
_sp500_roc: pd.Series = None       # date -> SP500 5d ROC (lagged 1 day)
_data_loaded: bool = False
_aligned_cache: dict = {}


def _load_macro_data():
    """Load Fear & Greed and SP500 data. No-op after first call."""
    global _fg_series, _sp500_roc, _data_loaded
    if _data_loaded:
        return
    _data_loaded = True

    # --- Fear & Greed ---
    if not os.path.exists(_FG_PATH):
        print(f"  [s521] WARNING: Fear & Greed parquet not found at {_FG_PATH}")
        return
    try:
        fg_df = pd.read_parquet(_FG_PATH, columns=["timestamp", "value"])
        fg_df["date"] = pd.to_datetime(fg_df["timestamp"]).dt.tz_localize(None)
        fg_df = fg_df.sort_values("date").drop_duplicates(subset="date", keep="last")
        # Lag by 1 day: data for date D available at D+1
        _fg_series = pd.Series(
            fg_df["value"].values.astype(np.float64),
            index=fg_df["date"].values + np.timedelta64(1, 'D'),
        )
        print(f"  [s521] Loaded Fear & Greed data: {len(_fg_series)} days")
    except Exception as exc:
        print(f"  [s521] WARNING: Failed to load Fear & Greed: {exc}")
        return

    # --- SP500 ---
    if not os.path.exists(_SP500_PATH):
        print(f"  [s521] WARNING: SP500 parquet not found at {_SP500_PATH}")
        return
    try:
        sp_df = pd.read_parquet(_SP500_PATH, columns=["Date", "Close"])
        sp_df["date"] = pd.to_datetime(sp_df["Date"]).dt.tz_localize(None)
        sp_df = sp_df.sort_values("date").drop_duplicates(subset="date", keep="last")
        close = sp_df["Close"].values.astype(np.float64)
        # 5-day rate of change
        roc5 = np.full(len(close), np.nan)
        roc5[SP500_ROC_WINDOW:] = (
            close[SP500_ROC_WINDOW:] / close[:-SP500_ROC_WINDOW] - 1.0
        )
        # Lag by 1 day
        _sp500_roc = pd.Series(
            roc5,
            index=sp_df["date"].values + np.timedelta64(1, 'D'),
        )
        print(f"  [s521] Loaded SP500 ROC data: {len(_sp500_roc)} days")
    except Exception as exc:
        print(f"  [s521] WARNING: Failed to load SP500: {exc}")
        return


def _align_daily_to_1h(series: pd.Series, idx_1h: pd.DatetimeIndex,
                        cache_key: str) -> np.ndarray:
    """Forward-fill daily series to 1H index (already lagged at source)."""
    key = (cache_key, len(idx_1h), idx_1h[0], idx_1h[-1])
    if key in _aligned_cache:
        return _aligned_cache[key]

    if series is None:
        result = np.full(len(idx_1h), np.nan, dtype=np.float64)
    else:
        aligned = series.reindex(idx_1h.normalize(), method="ffill")
        aligned.index = idx_1h
        result = aligned.values.astype(np.float64)

    _aligned_cache[key] = result
    return result


# ======================================================================
#  STRATEGY
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Fear & Greed + SP500 Trend — macro trend/momentum signal."""
    _load_macro_data()

    close = ctx.ind_1h['close']
    n = len(close)

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
            name='s521_fg_sp500_trend',
            breakeven_atr=BREAKEVEN_ATR,
        )

    # Token allowlist filter
    if ctx.ticker not in ALLOWED_TOKENS:
        return _empty()

    # No data loaded
    if _fg_series is None or _sp500_roc is None:
        return _empty()

    # Align daily macro data to 1H bars
    fg_values = _align_daily_to_1h(_fg_series, ctx.idx_1h, "fg")
    sp_roc = _align_daily_to_1h(_sp500_roc, ctx.idx_1h, "sp_roc")

    # Day boundaries — entry only on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Signal conditions
    fg_bull = fg_values > FG_BULL_THRESHOLD      # greed regime
    fg_bear = fg_values < FG_BEAR_THRESHOLD      # fear regime
    sp_up = sp_roc > 0                            # equity momentum positive
    sp_down = sp_roc < 0                          # equity momentum negative

    # Entry signals (daily rebalance)
    long_signal = fg_bull & sp_up & day_change
    short_signal = fg_bear & sp_down & day_change

    # Warmup guard
    long_signal[:WARMUP] = False
    short_signal[:WARMUP] = False

    # Direction filter
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
        name='s521_fg_sp500_trend',
        breakeven_atr=BREAKEVEN_ATR,
    )
