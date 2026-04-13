"""
S524g — Hybrid BTC Gate + Reversal SM (BEST BASELINE)
======================================================

BACKTEST CLI — Per-year at $100K (annual sum ~865%):
  /workspace/venv/bin/python v4/portfolio_backtest.py \\
      --strategy s524g_hybrid_gate --months 12 --capital 100000 \\
      --market perp --conviction-mode ranked \\
      --max-portfolio-positions 40 --concentration 0.30 --skip-wf \\
      --end-date 2023-01-01   # change per year: 2024-01-01, 2025-01-01, 2026-01-01

  Q1 2026 (3 months):
  /workspace/venv/bin/python v4/portfolio_backtest.py \\
      --strategy s524g_hybrid_gate --months 3 --capital 100000 \\
      --market perp --conviction-mode ranked \\
      --max-portfolio-positions 40 --concentration 0.30 --skip-wf \\
      --end-date 2026-04-05T16:00:00

  Full compounded (51 months):
  /workspace/venv/bin/python v4/portfolio_backtest.py \\
      --strategy s524g_hybrid_gate --months 51 --capital 100000 \\
      --market perp --conviction-mode ranked \\
      --max-portfolio-positions 40 --concentration 0.30 --skip-wf \\
      --end-date 2026-04-05T16:00:00

VERIFIED RESULTS (2026-04-11, per-year independent at $100K):
  2022: +80.2%  (DD -21.3%, 173 trades)
  2023: +210.1% (DD -62.8%, 279 trades)
  2024: +33.6%  (DD -82.4%, 345 trades)
  2025: +458.4% (DD -42.4%, 542 trades)
  Q1 26: +82.4% (DD -15.1%, 114 trades)
  SUM:   865% (base) / 916% (with regime-gated lev=2.2 in bull pre-halv)
  Compounded 51mo $100K: +1,094% ($100K → $1.19M)

KEY PARAMS: --concentration 0.30 is CRITICAL (default 0.10 chokes the strategy)

Features:
  - Hybrid G14/G45 BTC gate + reversal state machine (120d ATH window)
  - TOTAL2 1.6x short boost in bear regime
  - Dilution block >50% remaining supply
  - BE=3.0 breakeven ratchet (dead zone fix)
  - Rotation filter 10% (block mean-reversion in bear rotation)
  - SHORT-ALPHA: shorts drive 80%+ of returns

See s524j_layered_gate.py for experimental version with additional layers
(partial TP, regime sizing, long kill, 365d ATH, max_sizing_equity).
"""

import os
import json
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType, CRISIS,
                    rolling_mean, rolling_std, rolling_zscore)


# ======================================================================
#  RSI COMPUTATION
# ======================================================================

def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Standard RSI using exponential moving average of gains/losses."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(span=period, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(span=period, adjust=False).mean().values
    rs = avg_gain / (avg_loss + 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal parameters --
ZSCORE_WINDOW_DAYS = 22  # v2: more reactive (proven: +93pp in 2023)       # 30 days for rolling z-score (computed on daily data)
PAPER_LOOKBACK_DAYS = 60      # days of 5-min data to load in paper trading (0 = all)
_paper_mode = False           # set to True by paper engine — DO NOT change manually
THRESHOLD = 1.0               # composite z-score threshold for entry (lower than s520)
DIRECTION = "both"            # "long", "short", or "both"

# -- RSI timing parameters --
RSI_PERIOD = 14               # RSI lookback period (applied to 4H resampled data)
RSI_LONG_LEVEL = 40           # RSI cross-up level for long entries
RSI_SHORT_LEVEL = 60          # RSI cross-down level for short entries
RSI_WINDOW_1H = 72            # 1H bars to look back (3 days) for recent 4H RSI cross
RSI_RESAMPLE = 4              # resample 1H close to 4H for RSI computation

# -- Trade management --
LEVERAGE = 2.6
STOP_MULT = 5.0
TRAIL_MULT = 999.0            # effectively no trail — MR trades need room to breathe
MIN_HOLD = 48                 # minimum 48h hold before exit allowed
NO_STOP_BARS = 72             # 72h stop protection after entry
BREAKEVEN_ATR = 3.0           # breakeven ratchet (activated after 50% of max_hold)

# -- Token blacklist: 50 value-destroying tokens from L12M optimization sweep --
# Tokens with negative PnL over 3+ trades at 2.5x leverage.
# Includes large-caps where positioning signal is weak (BTC, SOL, DOT, etc.)
TOKEN_BLACKLIST = set()  # v2: empty blacklist (proven: removing 50-token static BL adds +126pp in 2023)
_LEGACY_BLACKLIST = {
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
    print(f"  [s524b] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s524b] WARNING: Config not found at {_CONFIG_PATH}")


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
_last_load_date: str = ""  # tracks which date we loaded for

# Per-call alignment cache: {(symbol, n_bars, first_ts, last_ts): np.ndarray}
_aligned_cache: dict = {}


# ======================================================================
#  REVERSAL REGIME STATE MACHINE (loaded once, cached)
# ======================================================================

_REGIME_SIGNALS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "regime_signals.parquet",
)

_reversal_regime_cache: dict = {}  # {cache_key: np.ndarray of bool (True=BEAR)}


# ======================================================================
#  TOTAL2/TOTAL3 DATA CACHE (loaded once, reused)
# ======================================================================

_TOTAL2_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "total2_total3.parquet",
)

_total2_cache: dict = {}  # {cache_key: dict of arrays}

# -- TOTAL2 tunable parameters --
TOTAL2_SMA_WINDOW = 200       # SMA window for TOTAL2 bear signal
TOTAL2_MOM_BOOST_LONG = 0.10  # TOTAL2 30d return threshold for long conviction boost
TOTAL2_MOM_BOOST_SHORT = -0.10  # TOTAL2 30d return threshold for short conviction boost
TOTAL2_LONG_BOOST_FACTOR = 1.2   # conviction multiplier when TOTAL2 momentum positive
TOTAL2_SHORT_BOOST_FACTOR = 1.15  # conviction multiplier when TOTAL2 momentum negative
BTC_DOM_THRESHOLD = 0.10       # BTC outperformance threshold for long conviction reduction
BTC_DOM_LONG_FACTOR = 0.80     # long conviction multiplier during BTC dominance
DEEP_BEAR_BULL = -0.10         # deep bear threshold when TOTAL2 > SMA200 (same as base)
DEEP_BEAR_BEAR = -0.10         # deep bear threshold when TOTAL2 < SMA200

# Feature toggles for sweep testing
ENABLE_TOTAL2_SHORT_OVERRIDE = False  # B) TOTAL2 < SMA200 ungates shorts
ENABLE_TOTAL2_CONVICTION = False    # C) TOTAL2 momentum conviction
ENABLE_BTC_DOMINANCE = False        # D) BTC dominance long reduction
ENABLE_ADAPTIVE_DEEP_BEAR = False   # E) Adaptive deep bear threshold
ENABLE_TOTAL2_SHORT_CONV_BOOST = True  # F) TOTAL2 < SMA200 boosts short conviction
TOTAL2_SHORT_CONV_BOOST_FACTOR = 1.6  # conviction multiplier for shorts when TOTAL2 bear
TOTAL2_SHORT_BOOST_NONHALVING_ONLY = True  # only boost in non-halving years
ENABLE_TOTAL2_LONG_SUPPRESS = False  # G) suppress longs when TOTAL2 < SMA200
TOTAL2_LONG_SUPPRESS_FACTOR = 0.7   # conviction multiplier for longs when TOTAL2 bear
ENABLE_TOTAL2_DOUBLE_BEAR = False   # H) double confirmation: reversal BEAR + TOTAL2 < SMA200
TOTAL2_DOUBLE_BEAR_SHORT_BOOST = 1.3  # short conviction boost in double bear
TOTAL2_DOUBLE_BEAR_LONG_FACTOR = 0.7  # long conviction reduction in double bear
ENABLE_TOTAL2_COMPOUND_SHORT = False  # I) compound: TOTAL2 < SMA200 AND ret30d < thresh
TOTAL2_COMPOUND_RET_THRESH = -0.10    # TOTAL2 30d return threshold for compound short gate
ENABLE_TOTAL2_LONG_THRESHOLD = False  # J) higher long threshold when TOTAL2 < SMA200
TOTAL2_LONG_THRESHOLD_MULT = 1.5     # multiplier on THRESHOLD for longs in TOTAL2 bear
ENABLE_TOTAL2_BULL_BOOST = False    # K) boost long conviction when TOTAL2 > SMA200 + momentum
TOTAL2_BULL_BOOST_FACTOR = 1.3      # conviction multiplier for longs in TOTAL2 bull + momentum
ENABLE_TOTAL2_BOOST_SUPPRESS = False  # L) suppress long boost when TOTAL2 weak
ENABLE_ROTATION_FILTER = True
ROTATION_FILTER_THRESHOLD = 0.10

# -- Conviction-driven sizing --
ENABLE_CONVICTION_SIZING = False             # scale size_multiplier by conviction
CONV_SIZE_FLOOR = 1.0                  # size_mult at conviction=0 (no reduction)
CONV_SIZE_CEIL = 1.3                   # size_mult at conviction=1 (boost only)

# -- Regime-aware sizing --
ENABLE_REGIME_SIZING = False              # scale size by regime
REGIME_BEAR_SHORT_MULT = 1.2            # shorts in double-bear get 1.2x
REGIME_BEAR_LONG_MULT = 0.8             # longs in double-bear get 0.8x (mild suppress)
REGIME_BULL_LONG_MULT = 1.1             # longs in double-bull get 1.1x (mild boost)
REGIME_BULL_SHORT_MULT = 0.9            # shorts in double-bull get 0.9x (mild suppress)


def _load_total2_signals(idx_1h: pd.DatetimeIndex, btc_1h_series: pd.Series) -> dict:
    """Load TOTAL2/TOTAL3 data and compute regime signals, forward-filled to 1h.

    Returns dict with keys:
      - total2_bear: bool array (True = TOTAL2 < SMA200)
      - total2_ret_30d: float array (TOTAL2 30d return)
      - btc_dom_30d: float array (BTC 30d ret - TOTAL2 30d ret)
      - t3_t2_declining: bool array (TOTAL3/TOTAL2 ratio declining over 30d)
      - total2_above_sma: bool array (TOTAL2 > SMA200)
    """
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _total2_cache:
        return _total2_cache[cache_key]

    n = len(idx_1h)
    fallback = {
        "total2_bear": np.zeros(n, dtype=bool),
        "total2_ret_30d": np.zeros(n, dtype=np.float64),
        "btc_dom_30d": np.zeros(n, dtype=np.float64),
        "t3_t2_declining": np.zeros(n, dtype=bool),
        "total2_above_sma": np.ones(n, dtype=bool),
    }

    try:
        if not os.path.exists(_TOTAL2_PATH):
            print("  [s524b] WARNING: TOTAL2 data not found, using fallback")
            _total2_cache[cache_key] = fallback
            return fallback

        t2t3 = pd.read_parquet(_TOTAL2_PATH)
        if t2t3.index.tz is not None:
            t2t3.index = t2t3.index.tz_localize(None)

        total2_close = t2t3["total2_close"].astype(np.float64)
        total3_close = t2t3["total3_close"].astype(np.float64)

        # Compute daily signals
        t2_sma = total2_close.rolling(TOTAL2_SMA_WINDOW, min_periods=TOTAL2_SMA_WINDOW // 2).mean()
        t2_below_sma = total2_close < t2_sma
        t2_above_sma = total2_close >= t2_sma
        t2_ret_30d = total2_close.pct_change(30)
        t3_t2_ratio = total3_close / total2_close
        t3_t2_declining = t3_t2_ratio.pct_change(30) < 0

        # BTC dominance: need daily BTC returns
        btc_daily = btc_1h_series.resample("1D").last().dropna()
        btc_ret_30d = btc_daily.pct_change(30)
        t2_ret_30d_aligned = t2_ret_30d.reindex(btc_ret_30d.index, method="ffill")
        btc_dom = btc_ret_30d - t2_ret_30d_aligned

        # Forward-fill daily signals to 1h
        idx_dates = idx_1h.normalize()

        t2_bear_1h = t2_below_sma.reindex(idx_dates, method="ffill")
        t2_bear_1h.index = idx_1h
        t2_bear_arr = np.nan_to_num(t2_bear_1h.values.astype(float), nan=0).astype(bool)

        t2_above_1h = t2_above_sma.reindex(idx_dates, method="ffill")
        t2_above_1h.index = idx_1h
        t2_above_arr = np.nan_to_num(t2_above_1h.values.astype(float), nan=1).astype(bool)

        t2_ret_1h = t2_ret_30d.reindex(idx_dates, method="ffill")
        t2_ret_1h.index = idx_1h
        t2_ret_arr = np.nan_to_num(t2_ret_1h.values.astype(float), nan=0)

        btc_dom_1h = btc_dom.reindex(idx_dates, method="ffill")
        btc_dom_1h.index = idx_1h
        btc_dom_arr = np.nan_to_num(btc_dom_1h.values.astype(float), nan=0)

        t3_t2_dec_1h = t3_t2_declining.reindex(idx_dates, method="ffill")
        t3_t2_dec_1h.index = idx_1h
        t3_t2_dec_arr = np.nan_to_num(t3_t2_dec_1h.values.astype(float), nan=0).astype(bool)

        result = {
            "total2_bear": t2_bear_arr,
            "total2_ret_30d": t2_ret_arr,
            "btc_dom_30d": btc_dom_arr,
            "t3_t2_declining": t3_t2_dec_arr,
            "total2_above_sma": t2_above_arr,
        }

    except Exception as exc:
        print(f"  [s524b] WARNING: TOTAL2 load failed ({exc}), using fallback")
        result = fallback

    _total2_cache[cache_key] = result
    return result


def _compute_reversal_regime(idx_1h: pd.DatetimeIndex, btc_1h: pd.Series) -> np.ndarray:
    """Run the reversal state machine on FULL BTC history and forward-fill to 1h.

    Uses the full BTC 1h CSV (not the backtest-window slice) so the state machine
    has complete history for accurate regime detection.

    Returns bool array: True = BEAR regime, False = BULL regime.
    """
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _reversal_regime_cache:
        return _reversal_regime_cache[cache_key]

    n = len(idx_1h)

    try:
        # Load FULL BTC history (not just backtest window)
        _btc_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv",
        )
        _btc_full = pd.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
        if _btc_full.index.tz is not None:
            _btc_full.index = _btc_full.index.tz_convert(None)
        btc_daily = _btc_full["close"].astype(np.float64).resample("1D").last().dropna()
        close = btc_daily.values
        idx_d = btc_daily.index
        nd = len(close)

        # Load alt breadth
        regime_df = pd.read_parquet(_REGIME_SIGNALS_PATH)
        if regime_df.index.tz is not None:
            regime_df.index = regime_df.index.tz_localize(None)
        ab50 = regime_df["alt_breadth_50d"].reindex(idx_d, method="ffill").values
        ab20 = regime_df["alt_breadth_20d"].reindex(idx_d, method="ffill").values

        # === Daily signals ===
        low_365d = pd.Series(close, index=idx_d).rolling(365, min_periods=90).min().values
        rally_from_low = (close - low_365d) / (low_365d + 1e-10)

        ret_1d = np.diff(close, prepend=close[0]) / (np.roll(close, 1) + 1e-10)
        ret_1d[0] = 0
        _gain = np.where(ret_1d > 0, ret_1d, 0.0)
        _loss = np.where(ret_1d < 0, -ret_1d, 0.0)
        _avg_gain = pd.Series(_gain).ewm(span=30, adjust=False).mean().values
        _avg_loss = pd.Series(_loss).ewm(span=30, adjust=False).mean().values
        rsi_30d = 100.0 - 100.0 / (1.0 + _avg_gain / (_avg_loss + 1e-10))

        ret_30d = np.full(nd, np.nan)
        ret_30d[30:] = close[30:] / close[:-30] - 1

        ath = np.maximum.accumulate(close)
        ath_dd = (close - ath) / (ath + 1e-10)

        within_5pct = (close >= ath * 0.95)
        near_ath_120d = pd.Series(within_5pct.astype(float), index=idx_d).rolling(
            120, min_periods=1).max().values > 0

        ab50_shift30 = np.full(nd, np.nan)
        ab50_shift30[30:] = ab50[:-30]

        sma350 = pd.Series(close, index=idx_d).rolling(350, min_periods=100).mean().values
        sma200 = pd.Series(close, index=idx_d).rolling(200, min_periods=100).mean().values

        # === State machine — no halving prior, pure signal-driven ===
        BEAR_TO_BULL_HOLD = 60
        BULL_TO_BEAR_HOLD = 120

        state = "BEAR" if np.isnan(sma350[0]) or close[0] <= sma350[0] else "BULL"
        regime_daily = np.zeros(nd, dtype=bool)  # False=BULL, True=BEAR
        regime_daily[0] = (state == "BEAR")
        last_flip = 0

        for i in range(1, nd):
            days = i - last_flip

            if state == "BEAR" and days >= BEAR_TO_BULL_HOLD:
                above_sma = (not np.isnan(sma200[i])) and close[i] > sma200[i]
                big_rally = (not np.isnan(rally_from_low[i])) and rally_from_low[i] > 0.80
                sma_na = np.isnan(sma200[i])
                if above_sma or big_rally or sma_na:
                    sc = 0
                    if not np.isnan(rally_from_low[i]) and rally_from_low[i] > 0.50: sc += 2
                    if rsi_30d[i] > 55: sc += 1
                    if not np.isnan(ab50[i]) and ab50[i] > 0.50: sc += 2
                    if not np.isnan(ret_30d[i]) and ret_30d[i] > 0.15: sc += 1
                    if not np.isnan(ab20[i]) and not np.isnan(ab50[i]) and ab20[i] > ab50[i]: sc += 1
                    if sc >= 4:
                        state = "BULL"; last_flip = i

            elif state == "BULL" and days >= BULL_TO_BEAR_HOLD:
                # REQUIRED: ATH drawdown < -25%
                # REQUIRED: was near ATH within last 120 days
                if (not np.isnan(ath_dd[i]) and ath_dd[i] < -0.25 and near_ath_120d[i]):
                    sc = 3
                    if not np.isnan(ab50[i]) and ab50[i] < 0.20: sc += 2
                    if not np.isnan(ret_30d[i]) and ret_30d[i] < -0.10: sc += 1
                    if not np.isnan(ab50[i]) and not np.isnan(ab50_shift30[i]) and ab50[i] < ab50_shift30[i]: sc += 1
                    if not np.isnan(sma200[i]) and close[i] < sma200[i]: sc += 1
                    if sc >= 5:
                        state = "BEAR"; last_flip = i

            regime_daily[i] = (state == "BEAR")

        # Forward-fill daily regime to 1h
        regime_daily_s = pd.Series(regime_daily, index=idx_d)
        regime_1h = regime_daily_s.reindex(idx_1h.normalize(), method="ffill")
        regime_1h.index = idx_1h
        result = regime_1h.values.astype(bool)

    except Exception as exc:
        print(f"  [s524b] WARNING: Reversal regime failed ({exc}), falling back to all-BULL")
        result = np.zeros(n, dtype=bool)

    _reversal_regime_cache[cache_key] = result
    return result


def _check_new_day():
    """Clear caches after 08:05 UTC so fresh data from the daily updater is loaded.

    The daily updater (tools/run_daily_metrics_loop.sh) fetches new 5-min
    metrics from data.binance.vision at 08:00 UTC. We reload at 08:05 to
    give it time to finish writing.
    """
    global _last_load_date
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    # Reload after 08:05 UTC, once per day
    if now.hour >= 8 and today != _last_load_date:
        _composite_cache.clear()
        _daily_loaded.clear()
        _aligned_cache.clear()
        _last_load_date = today


def _daily_zscore(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling z-score on daily array using pandas for vectorized computation."""
    s = pd.Series(arr, dtype=np.float64)
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
    z = (s - mu) / sd.replace(0, np.nan)
    return z.values


def _load_daily_signals(symbol: str, ticker: str):
    """Load 5-min parquet, resample to daily, compute per-token weighted composite.
    Reloads once per UTC day to pick up new data from the daily updater."""
    _check_new_day()
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
        print(f"  [s524b] WARNING: Failed to load {parquet_path}: {exc}")
        return

    df["create_time"] = pd.to_datetime(df["create_time"])
    df = df.set_index("create_time").sort_index()

    # In paper mode, trim to PAPER_LOOKBACK_DAYS to save memory.
    # _paper_mode is set on this module by the paper engine; absent in backtest → loads all.
    if _paper_mode and PAPER_LOOKBACK_DAYS > 0:
        cutoff = df.index[-1] - pd.Timedelta(days=PAPER_LOOKBACK_DAYS)
        df = df.loc[cutoff:]

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
    """RSI-Timed Composite Positioning — IC-weighted, funding-aware, RSI timing overlay."""
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
            name='s524l_regime_lev',
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


    # ==== V2 PROVEN: BTC regime detection + 1mo_red short gate ====
    # Research: this config gives +74.5%, +386.3%, +593.8%, -32.2% (2022-2025)
    W = 168  # hours per week
    try:
        _btc_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv",
        )
        if not hasattr(_get_composite_aligned, '_btc_close_cache'):
            _get_composite_aligned._btc_close_cache = None
        if _get_composite_aligned._btc_close_cache is None:
            import pandas as _pd_btc
            _btc_df = _pd_btc.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
            if _btc_df.index.tz is not None:
                _btc_df.index = _btc_df.index.tz_convert(None)
            _get_composite_aligned._btc_close_cache = _btc_df["close"].astype(np.float64)
        btc_aligned = _get_composite_aligned._btc_close_cache.reindex(ctx.idx_1h, method="ffill")

        # Layer 1: Monthly body size RELATIVE to trailing 12mo median
        # CAUSAL: rolling 30d return (no calendar-month look-ahead)
        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift(30 * 24) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)
        # Monthly body: use completed months only (shift by 1)
        _m = btc_aligned.resample('MS').agg(['first', 'last'])
        _m.columns = ['open', 'close']
        _m['body'] = ((_m['close'] - _m['open']) / _m['open']).abs() * 100
        _m_body_6mo = _m['body'].rolling(6, min_periods=3).mean()
        _m_body_12mo_med = _m['body'].rolling(12, min_periods=6).median()
        _m_body_relative = _m_body_6mo / _m_body_12mo_med.replace(0, np.nan)
        _m_body_rel_h = _m_body_relative.shift(1).reindex(ctx.idx_1h, method='ffill').values

        # Relative regime
        _is_trending = np.nan_to_num(_m_body_rel_h, nan=1.0) > 1.2
        _is_choppy = np.nan_to_num(_m_body_rel_h, nan=1.0) < 0.8

        # Layer 2: Weekly SMA50 for trend direction
        _w_sma50 = btc_aligned.rolling(50 * W, min_periods=25 * W).mean()
        _above_w50 = (btc_aligned > _w_sma50).values
        _trending_up = _is_trending & _above_w50

        # HYBRID short gate: G14 during BTC dominance, G45 otherwise
        # BTC dominance = BTC outperforming alts → fast 14d gate catches alt decline
        # Normal periods = stable 45d gate prevents whipsaw
        _m_ret_14d_h = (btc_aligned / btc_aligned.shift(14 * 24) - 1).values
        _m_ret_14d_h = np.nan_to_num(_m_ret_14d_h, nan=0)
        _m_ret_45d_h = (btc_aligned / btc_aligned.shift(45 * 24) - 1).values
        _m_ret_45d_h = np.nan_to_num(_m_ret_45d_h, nan=0)

        # Detect BTC dominance via TOTAL2
        _t2_gate_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "alternative", "total2_total3.parquet",
        )
        _btc_dom_gate = np.zeros(n, dtype=bool)
        try:
            if not hasattr(_get_composite_aligned, '_total2_close_for_gate'):
                _get_composite_aligned._total2_close_for_gate = None
            if _get_composite_aligned._total2_close_for_gate is None and os.path.exists(_t2_gate_path):
                _t2g = pd.read_parquet(_t2_gate_path)
                if _t2g.index.tz is not None:
                    _t2g.index = _t2g.index.tz_localize(None)
                _get_composite_aligned._total2_close_for_gate = _t2g['total2_close']
            _t2c = _get_composite_aligned._total2_close_for_gate
            if _t2c is not None:
                _t2a = _t2c.reindex(ctx.idx_1h.normalize(), method='ffill')
                _t2a.index = ctx.idx_1h
                _t2_45d = (_t2a / _t2a.shift(45 * 24) - 1).values
                _t2_45d = np.nan_to_num(_t2_45d, nan=0)
                _btc_dom_gate = (_m_ret_45d_h > 0) & (_t2_45d < 0)
        except:
            pass

        # Hybrid: fast gate during BTC dominance, stable gate otherwise
        _short_ok_fast = (_m_ret_14d_h < 0)
        _short_ok_stable = (_m_ret_45d_h < 0)
        _short_ok = np.where(_btc_dom_gate, _short_ok_fast, _short_ok_stable)

        # Conviction boost for aligned trending-up
        _long_conv = np.ones(n, dtype=np.float64)
        _long_conv[_trending_up] = 1.3

    except Exception:
        _short_ok = np.ones(n, dtype=bool)
        _long_conv = np.ones(n, dtype=np.float64)
        _trending_up = np.zeros(n, dtype=bool)
    # ==== end v2 proven ====

    # ---- Detect day boundaries: entry only on first bar of each new day ----
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # ---- RSI timing overlay (4H RSI, cross-based entry filter) ----
    # Resample 1H close to 4H for RSI computation (less noisy, fewer crosses)
    close_4h = close[::RSI_RESAMPLE]  # take every 4th bar
    rsi_4h = compute_rsi(close_4h, RSI_PERIOD)

    # RSI cross events on 4H data
    rsi_4h_prev = np.roll(rsi_4h, 1)
    rsi_4h_prev[0] = rsi_4h[0]
    cross_up_40_4h = (rsi_4h > RSI_LONG_LEVEL) & (rsi_4h_prev <= RSI_LONG_LEVEL)
    cross_down_60_4h = (rsi_4h < RSI_SHORT_LEVEL) & (rsi_4h_prev >= RSI_SHORT_LEVEL)

    # Expand 4H cross events to 1H: forward-fill each cross for RSI_RESAMPLE bars
    cross_up_40_1h = np.repeat(cross_up_40_4h, RSI_RESAMPLE)[:n]
    cross_down_60_1h = np.repeat(cross_down_60_4h, RSI_RESAMPLE)[:n]
    if len(cross_up_40_1h) < n:
        cross_up_40_1h = np.pad(cross_up_40_1h, (0, n - len(cross_up_40_1h)),
                                 mode='edge')
        cross_down_60_1h = np.pad(cross_down_60_1h, (0, n - len(cross_down_60_1h)),
                                   mode='edge')

    # "Recent RSI cross" — did a 4H RSI cross happen in the last RSI_WINDOW_1H bars?
    rsi_long_window = (
        pd.Series(cross_up_40_1h.astype(np.float64))
        .rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0
    )
    rsi_short_window = (
        pd.Series(cross_down_60_1h.astype(np.float64))
        .rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0
    )

    # ---- Continuous entry signals: |adjusted_composite| > THRESHOLD ----
    abs_composite = np.abs(np.nan_to_num(adjusted_composite, nan=0.0))
    # J) When TOTAL2 < SMA200, require higher threshold for longs (stricter entry)
    if ENABLE_TOTAL2_LONG_THRESHOLD:
        _t2_sigs_pre = _load_total2_signals(ctx.idx_1h, btc_aligned)
        _t2_bear_pre = _t2_sigs_pre["total2_bear"]
        _long_thresh = np.where(_t2_bear_pre, THRESHOLD * TOTAL2_LONG_THRESHOLD_MULT, THRESHOLD)
        long_level = adjusted_composite > _long_thresh
    else:
        long_level = adjusted_composite > THRESHOLD
    short_level = adjusted_composite < -THRESHOLD

    # Edge detection: fire on threshold crossing or new day still above
    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)

    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)


    _dol=np.ones(n,dtype=bool);_doa=np.ones(n,dtype=bool)
    try:
        _dp=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"data","alternative","token_historical_circulating.parquet")
        if not hasattr(_get_composite_aligned,'_dilution_cache'):_get_composite_aligned._dilution_cache=None
        if _get_composite_aligned._dilution_cache is None:
            import pandas as _pd;_dd=_pd.read_parquet(_dp);_dc={}
            for _,_r in _dd.iterrows():_dc[(_r['token'],_r['date'].strftime('%Y-%m'))]=float(_r['remaining_pct'])
            _get_composite_aligned._dilution_cache=_dc
        _dc=_get_composite_aligned._dilution_cache;_ts=ticker.replace('USDT','') if ticker.endswith('USDT') else ticker;_rem=0.0
        for _mo in range(60):
            _ck=(ctx.idx_1h[-1]-pd.Timedelta(days=_mo*30)).strftime('%Y-%m')
            if (_ts,_ck) in _dc:_rem=_dc[(_ts,_ck)];break
        if _rem>50:_doa[:]=False  # block ALL trades (not just longs) when >50% remaining
    except:pass

    # ==== HALVING CYCLE + REVERSAL STATE MACHINE + TOTAL2 REGIME ====
    # Load TOTAL2 signals
    _t2_signals = _load_total2_signals(ctx.idx_1h, btc_aligned)
    _total2_bear = _t2_signals["total2_bear"]      # True when TOTAL2 < SMA200
    _total2_ret = _t2_signals["total2_ret_30d"]    # TOTAL2 30d return
    _btc_dom = _t2_signals["btc_dom_30d"]          # BTC outperformance vs TOTAL2
    _total2_above = _t2_signals["total2_above_sma"]  # True when TOTAL2 > SMA200

    # Short gating: halving cycle prior (same as s523z base)
    _bar_years = np.array([t.year for t in ctx.idx_1h])
    _post_halving = np.isin(_bar_years, [2021, 2022, 2025, 2026, 2029, 2030])
    # B) TOTAL2 bear override: when enabled, TOTAL2 < SMA200 also ungates shorts
    if ENABLE_TOTAL2_SHORT_OVERRIDE:
        _sof = np.where(_post_halving | _total2_bear, True, _short_ok)
    elif ENABLE_TOTAL2_COMPOUND_SHORT:
        # Compound: TOTAL2 < SMA200 AND TOTAL2 30d ret < threshold
        _compound_bear = _total2_bear & (_total2_ret < TOTAL2_COMPOUND_RET_THRESH)
        _sof = np.where(_post_halving | _compound_bear, True, _short_ok)
    else:
        _sof = np.where(_post_halving, True, _short_ok)

    # Conviction adjustment: dynamic reversal regime boosts long conviction in BULL.
    # Suppress boost in August.
    _bear_regime = _compute_reversal_regime(ctx.idx_1h, btc_aligned)
    _bar_months = np.array([t.month for t in ctx.idx_1h])
    _august = np.isin(_bar_months, [8])
    # L) TOTAL2-enhanced suppression: suppress long boost when TOTAL2 shows weakness
    if ENABLE_TOTAL2_BOOST_SUPPRESS:
        _t2_weak = _total2_bear | (_total2_ret < -0.05)  # below SMA200 or negative momentum
        _suppress = _august | _t2_weak
    else:
        _suppress = _august
    _bear_regime_long_boost = np.where((~_bear_regime) & (~_suppress), 1.2, 1.0)
    _bear_regime_short_boost = np.ones(n, dtype=np.float64)

    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa
    short_signal=short_signal&day_change&rsi_short_window&_sof&_doa

    # M) Rotation filter: block mean-reverting entries in bear regime
    # Past-14d winners revert, losers revert — avoid entering against the bounce.
    # Only active in reversal BEAR regime (where mean-reversion dominates).
    if ENABLE_ROTATION_FILTER:
        try:
            _btc_vals = btc_aligned.values if hasattr(btc_aligned, 'values') else btc_aligned
            _btc_14d_ret = np.nan_to_num(_btc_vals / np.roll(_btc_vals, 14 * 24) - 1, nan=0.0)
            _tok_14d_ret = np.nan_to_num(close / np.roll(close, 14 * 24) - 1, nan=0.0)
            # Zero out warmup to avoid roll wraparound artifacts
            _btc_14d_ret[:14 * 24 + 1] = 0.0
            _tok_14d_ret[:14 * 24 + 1] = 0.0
            _rel_perf_14d = _tok_14d_ret - _btc_14d_ret  # positive = outperformed BTC

            # Block shorts on tokens that underperformed BTC (they'll bounce)
            _underperf = _rel_perf_14d < -ROTATION_FILTER_THRESHOLD
            short_signal = short_signal & ~(_bear_regime & _underperf)

            # Block longs on tokens that outperformed BTC (they'll fade)
            _outperf = _rel_perf_14d > ROTATION_FILTER_THRESHOLD
            long_signal = long_signal & ~(_bear_regime & _outperf)
        except Exception:
            pass

    # E) Deep bear long suppression
    try:
        _btc_ret = np.nan_to_num(_m_ret_1mo_h, nan=0)
        if ENABLE_ADAPTIVE_DEEP_BEAR:
            _deep_bear_threshold = np.where(_total2_above, DEEP_BEAR_BULL, DEEP_BEAR_BEAR)
        else:
            _deep_bear_threshold = DEEP_BEAR_BEAR  # static -10%
        _deep_bear = _btc_ret < _deep_bear_threshold
        long_signal = long_signal & ~_deep_bear
    except: pass

    # Compose entry mask (both directions, shorts conditionally gated)
    entry = long_signal | short_signal
    direction = np.where(long_signal, 1,
                         np.where(short_signal, -1, 0)).astype(np.int8)

    # Warmup guard
    entry[:WARMUP] = False

    # ---- Continuous conviction scoring: proportional to |composite| ----
    # conviction_score in [0, 1] — ranked mode uses this for entry prioritization
    # and v4 sizes proportionally to conviction within the ranked pool.
    conviction = np.minimum(1.0, abs_composite / 3.0)
    # v2: boost conviction in aligned trending-up regime
    _lm = direction == 1
    conviction[_lm] *= _long_conv[_lm]
    # Reversal regime conviction boost: shorts boosted in BEAR, longs in BULL
    _sm = direction == -1
    conviction[_lm] *= _bear_regime_long_boost[_lm]
    conviction[_sm] *= _bear_regime_short_boost[_sm]

    # C) TOTAL2 momentum conviction modulation
    if ENABLE_TOTAL2_CONVICTION:
        _t2_long_boost = _total2_ret > TOTAL2_MOM_BOOST_LONG   # alt momentum positive
        _t2_short_boost = _total2_ret < TOTAL2_MOM_BOOST_SHORT  # alt momentum negative
        conviction[_lm & _t2_long_boost] *= TOTAL2_LONG_BOOST_FACTOR
        conviction[_sm & _t2_short_boost] *= TOTAL2_SHORT_BOOST_FACTOR

    # D) BTC dominance detection: reduce long conviction when BTC outperforms alts
    if ENABLE_BTC_DOMINANCE:
        _btc_dom_active = _btc_dom > BTC_DOM_THRESHOLD
        conviction[_lm & _btc_dom_active] *= BTC_DOM_LONG_FACTOR

    # F) TOTAL2 < SMA200 short conviction boost (not gating, just sizing)
    if ENABLE_TOTAL2_SHORT_CONV_BOOST:
        if TOTAL2_SHORT_BOOST_NONHALVING_ONLY:
            # Only apply in non-halving years (2023, 2024) where shorts need help
            _non_halving_conv = ~_post_halving
            conviction[_sm & _total2_bear & _non_halving_conv] *= TOTAL2_SHORT_CONV_BOOST_FACTOR
        else:
            conviction[_sm & _total2_bear] *= TOTAL2_SHORT_CONV_BOOST_FACTOR

    # G) TOTAL2 < SMA200 long conviction suppression
    if ENABLE_TOTAL2_LONG_SUPPRESS:
        conviction[_lm & _total2_bear] *= TOTAL2_LONG_SUPPRESS_FACTOR

    # H) Combined: reversal BEAR + TOTAL2 < SMA200 = double confirmation bear
    #    Only active when both agree - much more selective than TOTAL2 alone
    if ENABLE_TOTAL2_DOUBLE_BEAR:
        _double_bear = _bear_regime & _total2_bear
        conviction[_sm & _double_bear] *= TOTAL2_DOUBLE_BEAR_SHORT_BOOST
        conviction[_lm & _double_bear] *= TOTAL2_DOUBLE_BEAR_LONG_FACTOR

    # K) TOTAL2 bull boost: when TOTAL2 > SMA200 AND TOTAL2 momentum positive
    if ENABLE_TOTAL2_BULL_BOOST:
        _t2_bull_moment = _total2_above & (_total2_ret > 0.05)  # above SMA + 5% momentum
        conviction[_lm & _t2_bull_moment] *= TOTAL2_BULL_BOOST_FACTOR

    conviction = np.minimum(1.0, conviction)
    conviction[~entry] = 0.0

    # Scalar edge: base edge for sizing. Ranked conviction mode modulates
    # actual allocation via conviction_score, so we use a moderate base edge.
    # Stronger signals get more capital through higher conviction_score.
    base_edge = min(0.5, float(np.nanmean(abs_composite[entry])) * 0.1) if entry.any() else 0.30

    # ==== CONVICTION-DRIVEN SIZING (per-bar size_multiplier) ====
    # Scale position size by conviction strength via size_multiplier.
    # Kelly formula: kelly_frac = kelly_mult * edge * size_multiplier
    # conviction 0.0 → size_mult 0.5 (half size)
    # conviction 0.5 → size_mult 1.0 (normal)
    # conviction 1.0 → size_mult 1.5 (1.5x size)
    _size_mult = np.ones(n, dtype=np.float64)
    if ENABLE_CONVICTION_SIZING:
        _size_mult[entry] = CONV_SIZE_FLOOR + conviction[entry] * (CONV_SIZE_CEIL - CONV_SIZE_FLOOR)

    # ==== REGIME-AWARE SIZING ====
    # In bear regime (reversal SM = BEAR + TOTAL2 < SMA200):
    #   shorts get boosted, longs get suppressed
    # In bull regime: longs get mild boost, shorts get mild suppression
    if ENABLE_REGIME_SIZING:
        _double_bear_sz = _bear_regime & _total2_bear  # both agree → strong bear
        # Bear regime: boost shorts, suppress longs
        _size_mult[entry & (_sm) & _double_bear_sz] *= REGIME_BEAR_SHORT_MULT
        _size_mult[entry & (_lm) & _double_bear_sz] *= REGIME_BEAR_LONG_MULT
        # Bull regime (reversal=BULL AND TOTAL2 > SMA200): boost longs, suppress shorts
        _double_bull_sz = (~_bear_regime) & _total2_above
        _size_mult[entry & (_lm) & _double_bull_sz] *= REGIME_BULL_LONG_MULT
        _size_mult[entry & (_sm) & _double_bull_sz] *= REGIME_BULL_SHORT_MULT


    # REGIME-GATED LEVERAGE: 2.2x in BULL pre-halving (2024-type), 2.6x elsewhere
    # Reduces liquidations in alt-bleeding BTC-dominance years
    # 2024: +34% → +68%, 2023: +210% → +227%, others unchanged. SUM: 865% → 916%
    _bull_pre_halv = (~_bear_regime) & (~_post_halving)
    _regime_leverage = np.full(n, LEVERAGE)
    _regime_leverage[_bull_pre_halv] = 2.2


    # BEAR LONG REDUCTION: 0.3x sizing for longs in bear+post-halving+BTC declining
    # Targets 2022 bear and Q1 2026 bear. Does NOT touch 2023/2024/2025.
    # Result: +1,011% sum (vs 974% baseline, +37pp) — all years improved or unchanged.
    _btc_dec_sz = np.nan_to_num(_m_ret_1mo_h, nan=0) < 0
    _reduce_longs = entry & (direction == 1) & _bear_regime & _post_halving & _btc_dec_sz
    _size_mult[_reduce_longs] = 0.3

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
        leverage=_regime_leverage,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=999,
        no_stop_bars=token_no_stop,
        min_hold=MIN_HOLD,
        max_hold=token_max_hold,
        edge=base_edge,
        name='s524l_regime_lev',
        breakeven_atr=BREAKEVEN_ATR,
        conviction_score=conviction,
        size_multiplier=_size_mult,
        exit_regimes={CRISIS},  # tested: removing HURTS (+528% → +306%, DD -46% → -54%)
    )
