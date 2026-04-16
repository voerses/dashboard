"""
# BACKTEST CLI (reproduce this strategy's results):
#   /workspace/venv/bin/python v4/portfolio_backtest.py \
#       --strategy s523u_regime_adaptive --months 12 --capital 100000 \
#       --market perp --conviction-mode ranked \
#       --max-portfolio-positions 40 --concentration 0.30 --skip-wf \
#       --end-date 2026-04-05T16:00:00
#
S523u — Smoothed 3-State Regime-Adaptive Positioning
=====================================================

Evolved from s523t. Uses a SMOOTHED 3-STATE regime detector with
parameters optimized per state:

1. RISK_OFF (bear): shorts ungated, deep bear at -10%, no long boost
2. RISK_ON (bull): shorts fully gated, deep bear at -3%, 1.3x long boost
3. NEUTRAL (default): shorts gated by 45d return, deep bear at -5%

Regime classification uses alt_breadth_50d, btc_ret_30d, btc_ret_90d,
btc_above_sma50 from pre-computed regime_signals.parquet.

14-day smoothing via rolling majority vote prevents rapid regime flipping.
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
ZSCORE_WINDOW_DAYS = 22
PAPER_LOOKBACK_DAYS = 60
_paper_mode = False
THRESHOLD = 1.0
DIRECTION = "both"

# -- RSI timing parameters --
RSI_PERIOD = 14
RSI_LONG_LEVEL = 40
RSI_SHORT_LEVEL = 60
RSI_WINDOW_1H = 72
RSI_RESAMPLE = 4

# -- Trade management --
LEVERAGE = 2.6
STOP_MULT = 5.0
TRAIL_MULT = 999.0
MIN_HOLD = 48
NO_STOP_BARS = 72
BREAKEVEN_ATR = 1.0

# -- Token blacklist --
TOKEN_BLACKLIST = set()

# -- Funding adjustment --
FUNDING_BOOST = 0.10

# -- Warmup --
WARMUP = 400

# -- Market --
MARKET = MarketType.PERP

# -- Portfolio config --
PORTFOLIO_CONFIG = {
    "conviction_mode": "ranked",
    "max_positions": 50,
}

# -- Regime-adaptive parameters (3-state) --
# RISK_OFF (bear): shorts ungated, aggressive deep bear filter
RISK_OFF_DEEP_BEAR_THRESH = -0.10

# RISK_ON (bull): shorts fully gated, light deep bear filter, long boost
RISK_ON_DEEP_BEAR_THRESH = -0.03
RISK_ON_LONG_BOOST = 1.3

# NEUTRAL: shorts gated by 45d return, moderate deep bear filter
NEUTRAL_DEEP_BEAR_THRESH = -0.05

# Short gate rolling return window (used in NEUTRAL regime only)
GATE_WINDOW_HOURS = 45 * 24     # 45-day rolling return for short gate

# Smoothing: 14-day rolling window, 40% threshold for majority
REGIME_SMOOTH_WINDOW = 14 * 24   # 336 hours
REGIME_SMOOTH_THRESH = 0.4       # 40% of window must agree


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
    print(f"  [s523u] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s523u] WARNING: Config not found at {_CONFIG_PATH}")


# ======================================================================
#  MODULE-LEVEL DATA CACHE
# ======================================================================

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_metrics", "5min",
)

_composite_cache: dict = {}
_daily_loaded: set = set()
_last_load_date: str = ""
_aligned_cache: dict = {}


def _check_new_day():
    global _last_load_date
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if now.hour >= 8 and today != _last_load_date:
        _composite_cache.clear()
        _daily_loaded.clear()
        _aligned_cache.clear()
        _last_load_date = today


def _daily_zscore(arr: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(arr, dtype=np.float64)
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
    z = (s - mu) / sd.replace(0, np.nan)
    return z.values


def _load_daily_signals(symbol: str, ticker: str):
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
        print(f"  [s523u] WARNING: Failed to load {parquet_path}: {exc}")
        return

    df["create_time"] = pd.to_datetime(df["create_time"])
    df = df.set_index("create_time").sort_index()

    if _paper_mode and PAPER_LOOKBACK_DAYS > 0:
        cutoff = df.index[-1] - pd.Timedelta(days=PAPER_LOOKBACK_DAYS)
        df = df.loc[cutoff:]

    daily = df.resample("1D").last().dropna(how="all")

    cfg = _token_configs[ticker]
    w_oi = cfg["oi_weight"]
    w_pos = cfg["pos_weight"]
    w_flow = cfg["flow_weight"]
    oi_sign = cfg["oi_sign"]
    pos_sign = cfg["pos_sign"]
    flow_sign = cfg["flow_sign"]

    oi_vals = daily["sum_open_interest_value"].values if "sum_open_interest_value" in daily.columns else np.array([])
    pos_vals = daily["sum_toptrader_long_short_ratio"].values if "sum_toptrader_long_short_ratio" in daily.columns else np.array([])
    flow_vals = daily["sum_taker_long_short_vol_ratio"].values if "sum_taker_long_short_vol_ratio" in daily.columns else np.array([])

    n_days = len(daily)
    if n_days < ZSCORE_WINDOW_DAYS:
        return

    oi_z = _daily_zscore(oi_vals, ZSCORE_WINDOW_DAYS) if len(oi_vals) == n_days else np.zeros(n_days)
    pos_z = _daily_zscore(pos_vals, ZSCORE_WINDOW_DAYS) if len(pos_vals) == n_days else np.zeros(n_days)
    flow_z = _daily_zscore(flow_vals, ZSCORE_WINDOW_DAYS) if len(flow_vals) == n_days else np.zeros(n_days)

    oi_z = np.nan_to_num(oi_z, nan=0.0)
    pos_z = np.nan_to_num(pos_z, nan=0.0)
    flow_z = np.nan_to_num(flow_z, nan=0.0)

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
    cache_key = (symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)

    if symbol not in _composite_cache:
        result = np.full(n, np.nan, dtype=np.float64)
        _aligned_cache[cache_key] = result
        return result

    series = _composite_cache[symbol]
    aligned = series.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h
    result = aligned.values.astype(np.float64)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY FUNCTION
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Regime-Adaptive Composite Positioning — halving-cycle regime detection."""
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker
    symbol = ticker + "USDT"

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
            name='s523u_regime_adaptive',
            breakeven_atr=BREAKEVEN_ATR,
        )

    cfg = _token_configs[ticker]
    token_max_hold = cfg["max_hold_hours"]

    _load_daily_signals(symbol, ticker)
    composite = _get_composite_aligned(symbol, ticker, ctx.idx_1h)

    # ---- Funding-aware conviction adjustment ----
    funding = np.zeros(n, dtype=np.float64)
    if ctx.funding_1h is not None:
        funding = np.nan_to_num(ctx.funding_1h, nan=0.0)

    composite_sign = np.sign(np.nan_to_num(composite, nan=0.0))
    funding_alignment = -composite_sign * np.sign(funding)
    funding_meaningful = np.abs(funding) > 1e-8
    funding_factor = np.ones(n, dtype=np.float64)
    funding_factor[funding_meaningful & (funding_alignment > 0)] = 1.0 + FUNDING_BOOST
    funding_factor[funding_meaningful & (funding_alignment < 0)] = 1.0 - FUNDING_BOOST
    adjusted_composite = composite * funding_factor

    # ==== BTC regime detection ====
    W = 168  # hours per week
    try:
        _btc_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv",
        )
        if not hasattr(_get_composite_aligned, '_btc_close_cache_u'):
            _get_composite_aligned._btc_close_cache_u = None
        if _get_composite_aligned._btc_close_cache_u is None:
            _btc_df = pd.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
            if _btc_df.index.tz is not None:
                _btc_df.index = _btc_df.index.tz_convert(None)
            _get_composite_aligned._btc_close_cache_u = _btc_df["close"].astype(np.float64)
        btc_aligned = _get_composite_aligned._btc_close_cache_u.reindex(ctx.idx_1h, method="ffill")

        # CAUSAL: rolling returns (no look-ahead)
        _m_ret_30d_h = (btc_aligned / btc_aligned.shift(30 * 24) - 1).values
        _m_ret_30d_h = np.nan_to_num(_m_ret_30d_h, nan=0)

        _m_ret_45d_h = (btc_aligned / btc_aligned.shift(GATE_WINDOW_HOURS) - 1).values
        _m_ret_45d_h = np.nan_to_num(_m_ret_45d_h, nan=0)

        # Monthly body relative to trailing 12mo median (for conviction boost)
        _m = btc_aligned.resample('MS').agg(['first', 'last'])
        _m.columns = ['open', 'close']
        _m['body'] = ((_m['close'] - _m['open']) / _m['open']).abs() * 100
        _m_body_6mo = _m['body'].rolling(6, min_periods=3).mean()
        _m_body_12mo_med = _m['body'].rolling(12, min_periods=6).median()
        _m_body_relative = _m_body_6mo / _m_body_12mo_med.replace(0, np.nan)
        _m_body_rel_h = _m_body_relative.shift(1).reindex(ctx.idx_1h, method='ffill').values

        _is_trending = np.nan_to_num(_m_body_rel_h, nan=1.0) > 1.2

        # Weekly SMA50 for trend direction (also used for conviction boost)
        _w_sma50 = btc_aligned.rolling(50 * W, min_periods=25 * W).mean()
        _above_w50 = (btc_aligned > _w_sma50).values
        _trending_up = _is_trending & _above_w50

        # ---- 3-STATE REGIME DETECTION WITH SMOOTHING ----
        _regime_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "alternative", "regime_signals.parquet",
        )
        if not hasattr(_get_composite_aligned, '_regime_cache'):
            _get_composite_aligned._regime_cache = None
        if _get_composite_aligned._regime_cache is None and os.path.exists(_regime_path):
            _get_composite_aligned._regime_cache = pd.read_parquet(_regime_path)

        _regime_df = _get_composite_aligned._regime_cache
        if _regime_df is not None:
            # Forward-fill daily signals to 1h bars
            _alt_br_50d = _regime_df['alt_breadth_50d'].astype(float)
            _btc_ret_30d_sig = _regime_df['btc_ret_30d'].astype(float)
            _btc_ret_90d_sig = _regime_df['btc_ret_90d'].astype(float)
            _btc_above_sma50_sig = _regime_df['btc_above_sma50'].astype(float)

            _alt_br_h = _alt_br_50d.reindex(ctx.idx_1h.normalize(), method='ffill')
            _alt_br_h.index = ctx.idx_1h
            _alt_br_h = _alt_br_h.fillna(0.5).values

            _btc_r30_h = _btc_ret_30d_sig.reindex(ctx.idx_1h.normalize(), method='ffill')
            _btc_r30_h.index = ctx.idx_1h
            _btc_r30_h = _btc_r30_h.fillna(0.0).values

            _btc_r90_h = _btc_ret_90d_sig.reindex(ctx.idx_1h.normalize(), method='ffill')
            _btc_r90_h.index = ctx.idx_1h
            _btc_r90_h = _btc_r90_h.fillna(0.0).values

            _btc_a50_h = _btc_above_sma50_sig.reindex(ctx.idx_1h.normalize(), method='ffill')
            _btc_a50_h.index = ctx.idx_1h
            _btc_a50_h = _btc_a50_h.fillna(1.0).values.astype(bool)

            # --- Classify raw 3-state regime per bar ---
            # RISK_OFF = -1, NEUTRAL = 0, RISK_ON = 1
            _raw_regime = np.zeros(n, dtype=np.float64)

            # RISK_OFF conditions (priority: checked first)
            _risk_off = (
                ((_alt_br_h < 0.25) & (_btc_r30_h < 0))       # deep alt bleed + BTC declining
                | ((~_btc_a50_h) & (_btc_r90_h < -0.15))       # confirmed bear trend
                | (_alt_br_h < 0.20)                             # extreme alt destruction
            )
            _raw_regime[_risk_off] = -1.0

            # RISK_ON conditions (only where not already RISK_OFF)
            _risk_on = (
                ((_alt_br_h > 0.60) & _btc_a50_h)              # healthy alts + BTC uptrend
                | ((_alt_br_h > 0.50) & (_btc_r30_h > 0.10))   # strong momentum
            )
            _risk_on = _risk_on & ~_risk_off  # RISK_OFF takes priority
            _raw_regime[_risk_on] = 1.0

            # --- 14-day smoothing: rolling sum majority vote ---
            _smooth_win = REGIME_SMOOTH_WINDOW  # 336 bars
            _thresh = _smooth_win * REGIME_SMOOTH_THRESH  # ~134

            _rolling_sum = pd.Series(_raw_regime).rolling(
                _smooth_win, min_periods=1
            ).sum().values

            _regime_state = np.zeros(n, dtype=np.int8)  # 0 = NEUTRAL
            _regime_state[_rolling_sum < -_thresh] = -1  # RISK_OFF
            _regime_state[_rolling_sum > _thresh] = 1    # RISK_ON

        else:
            # Fallback: SMA50-only (2-state) mapped to 3-state
            _regime_state = np.where(_above_w50, np.int8(1), np.int8(-1))
            _alt_br_h = np.full(n, 0.5)

        # ---- Map regime states to strategy parameters ----
        _is_risk_off = _regime_state == -1
        _is_risk_on = _regime_state == 1
        _is_neutral = _regime_state == 0

        # Short gate: RISK_OFF=ungated, RISK_ON=fully gated, NEUTRAL=45d return gate
        _short_ok_neutral = (_m_ret_45d_h < 0)
        _sof = np.where(
            _is_risk_off, True,
            np.where(_is_risk_on, False, _short_ok_neutral)
        )

        # Deep bear long suppression: regime-dependent threshold
        _deep_bear_thresh = np.where(
            _is_risk_off, RISK_OFF_DEEP_BEAR_THRESH,
            np.where(_is_risk_on, RISK_ON_DEEP_BEAR_THRESH, NEUTRAL_DEEP_BEAR_THRESH)
        )
        _deep_bear = _m_ret_30d_h < _deep_bear_thresh

        # Conviction boost: RISK_ON with trending up gets 1.3x
        _long_conv = np.ones(n, dtype=np.float64)
        _long_conv[_trending_up & _is_risk_on] = RISK_ON_LONG_BOOST

    except Exception:
        _sof = np.ones(n, dtype=bool)
        _long_conv = np.ones(n, dtype=np.float64)
        _trending_up = np.zeros(n, dtype=bool)
        _deep_bear = np.zeros(n, dtype=bool)
    # ==== end regime detection ====

    # ---- Detect day boundaries ----
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # ---- RSI timing overlay ----
    close_4h = close[::RSI_RESAMPLE]
    rsi_4h = compute_rsi(close_4h, RSI_PERIOD)

    rsi_4h_prev = np.roll(rsi_4h, 1)
    rsi_4h_prev[0] = rsi_4h[0]
    cross_up_40_4h = (rsi_4h > RSI_LONG_LEVEL) & (rsi_4h_prev <= RSI_LONG_LEVEL)
    cross_down_60_4h = (rsi_4h < RSI_SHORT_LEVEL) & (rsi_4h_prev >= RSI_SHORT_LEVEL)

    cross_up_40_1h = np.repeat(cross_up_40_4h, RSI_RESAMPLE)[:n]
    cross_down_60_1h = np.repeat(cross_down_60_4h, RSI_RESAMPLE)[:n]
    if len(cross_up_40_1h) < n:
        cross_up_40_1h = np.pad(cross_up_40_1h, (0, n - len(cross_up_40_1h)), mode='edge')
        cross_down_60_1h = np.pad(cross_down_60_1h, (0, n - len(cross_down_60_1h)), mode='edge')

    rsi_long_window = (
        pd.Series(cross_up_40_1h.astype(np.float64))
        .rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0
    )
    rsi_short_window = (
        pd.Series(cross_down_60_1h.astype(np.float64))
        .rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0
    )

    # ---- Entry signals ----
    abs_composite = np.abs(np.nan_to_num(adjusted_composite, nan=0.0))
    long_level = adjusted_composite > THRESHOLD
    short_level = adjusted_composite < -THRESHOLD

    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)

    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    # ---- Dilution filter ----
    _dol = np.ones(n, dtype=bool)
    _doa = np.ones(n, dtype=bool)
    try:
        _dp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "alternative", "token_historical_circulating.parquet")
        if not hasattr(_get_composite_aligned, '_dilution_cache'):
            _get_composite_aligned._dilution_cache = None
        if _get_composite_aligned._dilution_cache is None:
            _dd = pd.read_parquet(_dp)
            _dc = {}
            for _, _r in _dd.iterrows():
                _dc[(_r['token'], _r['date'].strftime('%Y-%m'))] = float(_r['remaining_pct'])
            _get_composite_aligned._dilution_cache = _dc
        _dc = _get_composite_aligned._dilution_cache
        _ts = ticker.replace('USDT', '') if ticker.endswith('USDT') else ticker
        _rem = 0.0
        for _mo in range(60):
            _ck = (ctx.idx_1h[-1] - pd.Timedelta(days=_mo * 30)).strftime('%Y-%m')
            if (_ts, _ck) in _dc:
                _rem = _dc[(_ts, _ck)]
                break
        if _rem > 75:
            _doa[:] = False
        elif _rem > 50:
            _dol[:] = False
    except:
        pass

    # ---- Compose entry gating ----
    long_signal = long_signal & day_change & rsi_long_window & _dol & _doa
    short_signal = short_signal & day_change & rsi_short_window & _sof & _doa

    # Apply deep bear long suppression (regime-adaptive threshold)
    long_signal = long_signal & ~_deep_bear

    # Compose entry mask
    entry = long_signal | short_signal
    direction = np.where(long_signal, 1,
                         np.where(short_signal, -1, 0)).astype(np.int8)

    # Warmup guard
    entry[:WARMUP] = False

    # ---- Conviction scoring ----
    conviction = np.minimum(1.0, abs_composite / 3.0)
    _lm = direction == 1
    conviction[_lm] *= _long_conv[_lm]
    conviction = np.minimum(1.0, conviction)
    conviction[~entry] = 0.0

    base_edge = min(0.5, float(np.nanmean(abs_composite[entry])) * 0.1) if entry.any() else 0.30

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
        name='s523u_regime_adaptive',
        breakeven_atr=BREAKEVEN_ATR,
        conviction_score=conviction,
        exit_regimes={CRISIS},
    )
