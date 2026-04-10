"""
# BACKTEST CLI (reproduce this strategy's results):
#   /workspace/venv/bin/python v4/portfolio_backtest.py \
#       --strategy s523z_reversal --months 12 --capital 100000 \
#       --market perp --conviction-mode ranked \
#       --max-portfolio-positions 40 --concentration 0.30 --skip-wf \
#       --end-date 2026-04-05T16:00:00
#
S524a — Cycle Score + Reversal State Machine
==============================================

Combines TWO innovations:
1. Reversal state machine (from s523z) for BULL/BEAR regime detection
2. Cycle Position Score (from s523y) for conviction modulation

The reversal state machine provides bold, stable regime calls (1-2 transitions/yr).
The cycle score (12 features, absolute scaling, alt distress discount) provides
continuous conviction scaling within each regime:
- BULL + high score (>0.60) → 1.2x long boost
- BULL + low score (<0.40) → 0.8x long reduction (protects in choppy)
- BEAR + very low score (<0.30) → 1.2x short boost
- BEAR + moderate score → 1.0x (no boost)

Short gating: halving cycle + SMA50 override for intra-year dips.
Deep bear: DB-10%.

Status: RESEARCH
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
BREAKEVEN_ATR = 1.0           # breakeven ratchet (activated after 50% of max_hold)

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
    print(f"  [s523z] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s523z] WARNING: Config not found at {_CONFIG_PATH}")


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
        print(f"  [s523z] WARNING: Reversal regime failed ({exc}), falling back to all-BULL")
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
        print(f"  [s523z] WARNING: Failed to load {parquet_path}: {exc}")
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
            name='s524a_cycle_reversal',
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

        # G45 short gate: shorts only when last BTC 45-day return < 0
        # (smoother than 30d, proven +726% in re-optimization sweep)
        _m_ret_45d_h = (btc_aligned / btc_aligned.shift(45 * 24) - 1).values
        _m_ret_45d_h = np.nan_to_num(_m_ret_45d_h, nan=0)
        _short_ok = (_m_ret_45d_h < 0)

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
        if _rem>75:_doa[:]=False
        elif _rem>50:_dol[:]=False
    except:pass

    # ==== HALVING CYCLE + SMA50 OVERRIDE + REVERSAL + CYCLE SCORE ====
    # Short gating: halving cycle + SMA50 override for intra-year dips
    _bar_years = np.array([t.year for t in ctx.idx_1h])
    _post_halving = np.isin(_bar_years, [2021, 2022, 2025, 2026, 2029, 2030])
    _sma50_bear = ~_above_w50  # BTC below weekly SMA50
    _sof = np.where(_post_halving | _sma50_bear, True, _short_ok)

    # Reversal state machine for regime detection
    _bear_regime = _compute_reversal_regime(ctx.idx_1h, btc_aligned)

    # ---- CYCLE POSITION SCORE for conviction modulation ----
    # Compute daily score from BTC + alt breadth (cached)
    _score_h = np.full(n, 0.5)
    try:
        if not hasattr(_get_composite_aligned, '_cycle_score_cache_a'):
            _get_composite_aligned._cycle_score_cache_a = None
        if _get_composite_aligned._cycle_score_cache_a is None:
            # Resample BTC to daily
            _btc_path_a = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv",
            )
            _btc_1h_a = pd.read_csv(_btc_path_a, parse_dates=["datetime"]).set_index("datetime").sort_index()
            if _btc_1h_a.index.tz is not None:
                _btc_1h_a.index = _btc_1h_a.index.tz_convert(None)
            _bd = _btc_1h_a.resample('1D').agg({'close': 'last', 'volume': 'sum'}).dropna(subset=['close'])
            _bd['close'] = _bd['close'].astype(np.float64)
            _bd['volume'] = _bd['volume'].astype(np.float64)

            # Load alt breadth
            _regime_df_a = pd.read_parquet(_REGIME_SIGNALS_PATH)
            if _regime_df_a.index.tz is not None:
                _regime_df_a.index = _regime_df_a.index.tz_localize(None)
            _ab = _regime_df_a['alt_breadth_50d'].reindex(_bd.index, method='ffill').fillna(0.5)

            # Compute cycle score (absolute scaling + alt distress discount)
            _c = _bd['close']; _v = _bd['volume']; _rd = _c.pct_change()
            _sma350 = _c.rolling(350, min_periods=200).mean()
            _ath = _c.expanding().max(); _ath_dd = (_c - _ath) / _ath
            _low365 = _c.rolling(365, min_periods=180).min(); _rally = (_c - _low365) / _low365
            _delta = _c.diff(); _g = _delta.clip(lower=0); _l = (-_delta.clip(upper=0))
            _rsi30 = 100 - (100 / (1 + _g.ewm(alpha=1/30, min_periods=30).mean() / _l.ewm(alpha=1/30, min_periods=30).mean()))
            _ret30 = _c.pct_change(30)
            _rv30 = _rd.rolling(30, min_periods=15).std() * np.sqrt(365)

            _base = (
                0.25 * np.clip((_c - _sma350) / _sma350 / 1.50 + 1/3, 0, 1) +
                0.20 * np.clip((_ath_dd + 0.75) / 0.75, 0, 1) +
                0.15 * np.clip((_rsi30 - 20) / 60, 0, 1) +
                0.15 * _ab.clip(0, 1) +
                0.10 * np.clip(_rally / 4.0, 0, 1) +
                0.10 * np.clip((_ret30 + 0.30) / 0.60, 0, 1) +
                0.05 * (1 - np.clip(_rv30 / 1.50, 0, 1))
            ).clip(0, 1)

            # Alt distress discount: when alts bleeding AND BTC weak
            _btc_weak = _ret30 < 0
            _disc = pd.Series(np.where(_btc_weak, np.clip(_ab / 0.30, 0.4, 1.0), 1.0), index=_base.index)
            _score = (_base * _disc).clip(0, 1)
            _get_composite_aligned._cycle_score_cache_a = _score

        _cs_daily = _get_composite_aligned._cycle_score_cache_a
        _cs_1h = _cs_daily.reindex(ctx.idx_1h.normalize(), method='ffill')
        _cs_1h.index = ctx.idx_1h
        _score_h = _cs_1h.fillna(0.5).values
    except:
        pass

    # Cycle-score-based conviction modulation:
    # BULL regime: boost longs when score high, reduce when low
    _long_boost = np.where(_score_h > 0.60, 1.2,
                           np.where(_score_h < 0.40, 0.8, 1.0))
    _bear_regime_long_boost = np.where(~_bear_regime, _long_boost, 1.0)

    # BEAR regime: boost shorts when score very low (deep bear)
    _short_boost = np.where(_score_h < 0.30, 1.2, 1.0)
    _bear_regime_short_boost = np.where(_bear_regime, _short_boost, 1.0)

    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa
    short_signal=short_signal&day_change&rsi_short_window&_sof&_doa

    # Deep bear long suppression: no longs when BTC 30d return < -10%
    # Less aggressive than -5%: allows more contrarian longs, only blocks in deep crashes.
    # Frees portfolio slots for shorts in extended downtrends.
    # Re-optimization: DB-10% gives +86% in 2022 (vs +48% at -5%) and +359% in 2025.
    try:
        _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.10
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
    conviction = np.minimum(1.0, conviction)
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
        name='s524a_cycle_reversal',
        breakeven_atr=BREAKEVEN_ATR,
        conviction_score=conviction,
        exit_regimes={CRISIS},  # tested: removing HURTS (+528% → +306%, DD -46% → -54%)
    )
