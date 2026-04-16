"""
# BACKTEST CLI (reproduce this strategy's results):
#   /workspace/venv/bin/python v4/portfolio_backtest.py \
#       --strategy s525_momentum_companion --months 12 --capital 100000 \
#       --market perp --conviction-mode ranked \
#       --max-portfolio-positions 40 --concentration 0.30 --skip-wf \
#       --end-date 2026-04-05T16:00:00
#
S525 — Trend-Following Momentum Companion
==========================================

s524g signals filtered by BTC SMA200 trend:
- Longs only when BTC > SMA200 (bull trend)
- Shorts only when BTC < SMA200 (bear trend) or halving years

This avoids the catastrophic 2024 Q2-Q3 drawdown where s524g takes long positions
during the alt bleed while BTC is declining toward SMA200.

Status: RESEARCH (Gate 3 backtest)
"""

import os
import json
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType, CRISIS,
                    rolling_mean, rolling_std, rolling_zscore)


def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
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

ZSCORE_WINDOW_DAYS = 22
PAPER_LOOKBACK_DAYS = 60
_paper_mode = False
THRESHOLD = 1.0
DIRECTION = "both"

RSI_PERIOD = 14
RSI_LONG_LEVEL = 40
RSI_SHORT_LEVEL = 60
RSI_WINDOW_1H = 72
RSI_RESAMPLE = 4

LEVERAGE = 2.6
STOP_MULT = 5.0
TRAIL_MULT = 999.0
MIN_HOLD = 48
NO_STOP_BARS = 72
BREAKEVEN_ATR = 3.0

TOKEN_BLACKLIST = set()
FUNDING_BOOST = 0.10
WARMUP = 400
MARKET = MarketType.PERP

PORTFOLIO_CONFIG = {
    "conviction_mode": "ranked",
    "max_positions": 50,
}

# BTC trend filter
BTC_SMA_PERIOD = 200
TREND_ONLY_MODE = False

# Feature toggles (s524g defaults)
ENABLE_TOTAL2_SHORT_OVERRIDE = False
ENABLE_TOTAL2_CONVICTION = False
ENABLE_BTC_DOMINANCE = False
ENABLE_ADAPTIVE_DEEP_BEAR = False
ENABLE_TOTAL2_SHORT_CONV_BOOST = True
TOTAL2_SHORT_CONV_BOOST_FACTOR = 1.6
TOTAL2_SHORT_BOOST_NONHALVING_ONLY = True
ENABLE_TOTAL2_LONG_SUPPRESS = False
TOTAL2_LONG_SUPPRESS_FACTOR = 0.7
ENABLE_TOTAL2_DOUBLE_BEAR = False
TOTAL2_DOUBLE_BEAR_SHORT_BOOST = 1.3
TOTAL2_DOUBLE_BEAR_LONG_FACTOR = 0.7
ENABLE_TOTAL2_COMPOUND_SHORT = False
TOTAL2_COMPOUND_RET_THRESH = -0.10
ENABLE_TOTAL2_LONG_THRESHOLD = False
TOTAL2_LONG_THRESHOLD_MULT = 1.5
ENABLE_TOTAL2_BULL_BOOST = False
TOTAL2_BULL_BOOST_FACTOR = 1.3
ENABLE_TOTAL2_BOOST_SUPPRESS = False
ENABLE_ROTATION_FILTER = True
ROTATION_FILTER_THRESHOLD = 0.10
ENABLE_CONVICTION_SIZING = False
CONV_SIZE_FLOOR = 1.0
CONV_SIZE_CEIL = 1.3
ENABLE_REGIME_SIZING = False

TOTAL2_SMA_WINDOW = 200
TOTAL2_MOM_BOOST_LONG = 0.10
TOTAL2_MOM_BOOST_SHORT = -0.10
TOTAL2_LONG_BOOST_FACTOR = 1.2
TOTAL2_SHORT_BOOST_FACTOR = 1.15
BTC_DOM_THRESHOLD = 0.10
BTC_DOM_LONG_FACTOR = 0.80
DEEP_BEAR_BULL = -0.10
DEEP_BEAR_BEAR = -0.10


# ======================================================================
#  DATA LOADING (same as s524g)
# ======================================================================

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "s521_token_config.json",
)
_token_configs: dict = {}

if os.path.exists(_CONFIG_PATH):
    with open(_CONFIG_PATH) as _f:
        _token_configs = json.load(_f)
    print(f"  [s525] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s525] WARNING: Config not found at {_CONFIG_PATH}")

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_metrics", "5min",
)
_composite_cache: dict = {}
_daily_loaded: set = set()
_last_load_date: str = ""
_aligned_cache: dict = {}

_REGIME_SIGNALS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "regime_signals.parquet",
)
_reversal_regime_cache: dict = {}

_TOTAL2_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "total2_total3.parquet",
)
_total2_cache: dict = {}


def _load_total2_signals(idx_1h, btc_1h_series):
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _total2_cache:
        return _total2_cache[cache_key]
    n = len(idx_1h)
    fallback = {"total2_bear": np.zeros(n, dtype=bool), "total2_ret_30d": np.zeros(n), "btc_dom_30d": np.zeros(n), "t3_t2_declining": np.zeros(n, dtype=bool), "total2_above_sma": np.ones(n, dtype=bool)}
    try:
        if not os.path.exists(_TOTAL2_PATH):
            _total2_cache[cache_key] = fallback; return fallback
        t2t3 = pd.read_parquet(_TOTAL2_PATH)
        if t2t3.index.tz is not None: t2t3.index = t2t3.index.tz_localize(None)
        total2_close = t2t3["total2_close"].astype(np.float64)
        t2_sma = total2_close.rolling(TOTAL2_SMA_WINDOW, min_periods=TOTAL2_SMA_WINDOW // 2).mean()
        t2_below_sma = total2_close < t2_sma
        t2_above_sma = total2_close >= t2_sma
        t2_ret_30d = total2_close.pct_change(30)
        btc_daily = btc_1h_series.resample("1D").last().dropna()
        btc_ret_30d = btc_daily.pct_change(30)
        t2_ret_30d_al = t2_ret_30d.reindex(btc_ret_30d.index, method="ffill")
        btc_dom = btc_ret_30d - t2_ret_30d_al
        idx_dates = idx_1h.normalize()
        def _ffill(s): r = s.reindex(idx_dates, method="ffill"); r.index = idx_1h; return r
        result = {
            "total2_bear": np.nan_to_num(_ffill(t2_below_sma).values.astype(float), nan=0).astype(bool),
            "total2_ret_30d": np.nan_to_num(_ffill(t2_ret_30d).values.astype(float), nan=0),
            "btc_dom_30d": np.nan_to_num(_ffill(btc_dom).values.astype(float), nan=0),
            "t3_t2_declining": np.zeros(n, dtype=bool),
            "total2_above_sma": np.nan_to_num(_ffill(t2_above_sma).values.astype(float), nan=1).astype(bool),
        }
    except Exception as exc:
        print(f"  [s525] WARNING: TOTAL2 load failed ({exc})"); result = fallback
    _total2_cache[cache_key] = result; return result


def _compute_reversal_regime(idx_1h, btc_1h):
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _reversal_regime_cache: return _reversal_regime_cache[cache_key]
    n = len(idx_1h)
    try:
        _btc_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv")
        _btc_full = pd.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
        if _btc_full.index.tz is not None: _btc_full.index = _btc_full.index.tz_convert(None)
        btc_daily = _btc_full["close"].astype(np.float64).resample("1D").last().dropna()
        close = btc_daily.values; idx_d = btc_daily.index; nd = len(close)
        regime_df = pd.read_parquet(_REGIME_SIGNALS_PATH)
        if regime_df.index.tz is not None: regime_df.index = regime_df.index.tz_localize(None)
        ab50 = regime_df["alt_breadth_50d"].reindex(idx_d, method="ffill").values
        ab20 = regime_df["alt_breadth_20d"].reindex(idx_d, method="ffill").values
        low_365d = pd.Series(close, index=idx_d).rolling(365, min_periods=90).min().values
        rally_from_low = (close - low_365d) / (low_365d + 1e-10)
        ret_1d = np.diff(close, prepend=close[0]) / (np.roll(close, 1) + 1e-10); ret_1d[0] = 0
        _g = np.where(ret_1d > 0, ret_1d, 0.0); _l = np.where(ret_1d < 0, -ret_1d, 0.0)
        _ag = pd.Series(_g).ewm(span=30, adjust=False).mean().values; _al = pd.Series(_l).ewm(span=30, adjust=False).mean().values
        rsi_30d = 100.0 - 100.0 / (1.0 + _ag / (_al + 1e-10))
        ret_30d = np.full(nd, np.nan); ret_30d[30:] = close[30:] / close[:-30] - 1
        ath = np.maximum.accumulate(close); ath_dd = (close - ath) / (ath + 1e-10)
        within_5pct = (close >= ath * 0.95)
        near_ath_120d = pd.Series(within_5pct.astype(float), index=idx_d).rolling(120, min_periods=1).max().values > 0
        ab50_shift30 = np.full(nd, np.nan); ab50_shift30[30:] = ab50[:-30]
        sma350 = pd.Series(close, index=idx_d).rolling(350, min_periods=100).mean().values
        sma200 = pd.Series(close, index=idx_d).rolling(200, min_periods=100).mean().values
        state = "BEAR" if np.isnan(sma350[0]) or close[0] <= sma350[0] else "BULL"
        regime_daily = np.zeros(nd, dtype=bool); regime_daily[0] = (state == "BEAR"); last_flip = 0
        for i in range(1, nd):
            days = i - last_flip
            if state == "BEAR" and days >= 60:
                above_sma = (not np.isnan(sma200[i])) and close[i] > sma200[i]
                big_rally = (not np.isnan(rally_from_low[i])) and rally_from_low[i] > 0.80
                if above_sma or big_rally or np.isnan(sma200[i]):
                    sc = 0
                    if not np.isnan(rally_from_low[i]) and rally_from_low[i] > 0.50: sc += 2
                    if rsi_30d[i] > 55: sc += 1
                    if not np.isnan(ab50[i]) and ab50[i] > 0.50: sc += 2
                    if not np.isnan(ret_30d[i]) and ret_30d[i] > 0.15: sc += 1
                    if not np.isnan(ab20[i]) and not np.isnan(ab50[i]) and ab20[i] > ab50[i]: sc += 1
                    if sc >= 4: state = "BULL"; last_flip = i
            elif state == "BULL" and days >= 120:
                if not np.isnan(ath_dd[i]) and ath_dd[i] < -0.25 and near_ath_120d[i]:
                    sc = 3
                    if not np.isnan(ab50[i]) and ab50[i] < 0.20: sc += 2
                    if not np.isnan(ret_30d[i]) and ret_30d[i] < -0.10: sc += 1
                    if not np.isnan(ab50[i]) and not np.isnan(ab50_shift30[i]) and ab50[i] < ab50_shift30[i]: sc += 1
                    if not np.isnan(sma200[i]) and close[i] < sma200[i]: sc += 1
                    if sc >= 5: state = "BEAR"; last_flip = i
            regime_daily[i] = (state == "BEAR")
        regime_daily_s = pd.Series(regime_daily, index=idx_d)
        regime_1h = regime_daily_s.reindex(idx_1h.normalize(), method="ffill"); regime_1h.index = idx_1h
        result = regime_1h.values.astype(bool)
    except Exception as exc:
        print(f"  [s525] WARNING: Reversal regime failed ({exc})"); result = np.zeros(n, dtype=bool)
    _reversal_regime_cache[cache_key] = result; return result


def _check_new_day():
    global _last_load_date
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc); today = now.strftime("%Y-%m-%d")
    if now.hour >= 8 and today != _last_load_date:
        _composite_cache.clear(); _daily_loaded.clear(); _aligned_cache.clear(); _last_load_date = today


def _daily_zscore(arr, window):
    s = pd.Series(arr, dtype=np.float64)
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
    return ((s - mu) / sd.replace(0, np.nan)).values


def _load_daily_signals(symbol, ticker):
    _check_new_day()
    if symbol in _daily_loaded: return
    _daily_loaded.add(symbol)
    if ticker not in _token_configs: return
    parquet_path = os.path.join(_DATA_DIR, f"{symbol}_5min.parquet")
    if not os.path.exists(parquet_path): return
    try:
        df = pd.read_parquet(parquet_path, columns=["create_time", "sum_open_interest_value", "sum_toptrader_long_short_ratio", "sum_taker_long_short_vol_ratio"])
    except: return
    df["create_time"] = pd.to_datetime(df["create_time"]); df = df.set_index("create_time").sort_index()
    if _paper_mode and PAPER_LOOKBACK_DAYS > 0: cutoff = df.index[-1] - pd.Timedelta(days=PAPER_LOOKBACK_DAYS); df = df.loc[cutoff:]
    daily = df.resample("1D").last().dropna(how="all")
    cfg = _token_configs[ticker]
    oi_vals = daily["sum_open_interest_value"].values if "sum_open_interest_value" in daily.columns else np.array([])
    pos_vals = daily["sum_toptrader_long_short_ratio"].values if "sum_toptrader_long_short_ratio" in daily.columns else np.array([])
    flow_vals = daily["sum_taker_long_short_vol_ratio"].values if "sum_taker_long_short_vol_ratio" in daily.columns else np.array([])
    n_days = len(daily)
    if n_days < ZSCORE_WINDOW_DAYS: return
    oi_z = np.nan_to_num(_daily_zscore(oi_vals, ZSCORE_WINDOW_DAYS), nan=0.0) if len(oi_vals) == n_days else np.zeros(n_days)
    pos_z = np.nan_to_num(_daily_zscore(pos_vals, ZSCORE_WINDOW_DAYS), nan=0.0) if len(pos_vals) == n_days else np.zeros(n_days)
    flow_z = np.nan_to_num(_daily_zscore(flow_vals, ZSCORE_WINDOW_DAYS), nan=0.0) if len(flow_vals) == n_days else np.zeros(n_days)
    composite = (cfg["oi_weight"] * oi_z * cfg["oi_sign"] + cfg["pos_weight"] * pos_z * cfg["pos_sign"] + cfg["flow_weight"] * flow_z * cfg["flow_sign"])
    composite_shifted = np.empty_like(composite); composite_shifted[0] = np.nan; composite_shifted[1:] = composite[:-1]
    _composite_cache[symbol] = pd.Series(composite_shifted, index=daily.index, dtype=np.float64)


def _get_composite_aligned(symbol, ticker, idx_1h):
    cache_key = (symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache: return _aligned_cache[cache_key]
    n = len(idx_1h)
    if symbol not in _composite_cache: result = np.full(n, np.nan); _aligned_cache[cache_key] = result; return result
    series = _composite_cache[symbol]
    aligned = series.reindex(idx_1h.normalize(), method="ffill"); aligned.index = idx_1h
    result = aligned.values.astype(np.float64); _aligned_cache[cache_key] = result; return result


# ======================================================================
#  BTC TREND CACHE
# ======================================================================

_btc_trend_cache: dict = {}

def _compute_btc_trend(idx_1h):
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _btc_trend_cache: return _btc_trend_cache[cache_key]
    n = len(idx_1h)
    try:
        _btc_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv")
        btc_df = pd.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
        if btc_df.index.tz is not None: btc_df.index = btc_df.index.tz_convert(None)
        btc_daily = btc_df["close"].astype(np.float64).resample("1D").last().dropna()
        sma200 = btc_daily.rolling(BTC_SMA_PERIOD, min_periods=100).mean()
        above_sma = btc_daily > sma200
        above_1h = above_sma.reindex(idx_1h.normalize(), method="ffill"); above_1h.index = idx_1h
        result = np.nan_to_num(above_1h.values.astype(float), nan=0).astype(bool)
    except Exception as exc:
        print(f"  [s525] WARNING: BTC trend failed ({exc})"); result = np.ones(n, dtype=bool)
    _btc_trend_cache[cache_key] = result; return result


# ======================================================================
#  STRATEGY FUNCTION
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker
    symbol = ticker + "USDT"

    if ticker in TOKEN_BLACKLIST or ticker not in _token_configs:
        return StrategyResult(entry_mask=np.zeros(n, dtype=bool), direction=np.zeros(n, dtype=np.int8),
                              market_type=MARKET, leverage=LEVERAGE, stop_mult=STOP_MULT, trail_mult=TRAIL_MULT,
                              target_mult=999, no_stop_bars=NO_STOP_BARS, min_hold=MIN_HOLD, max_hold=720,
                              edge=0.0, name='s525_momentum_companion', breakeven_atr=BREAKEVEN_ATR)

    cfg = _token_configs[ticker]
    token_max_hold = cfg["max_hold_hours"]
    _load_daily_signals(symbol, ticker)
    composite = _get_composite_aligned(symbol, ticker, ctx.idx_1h)

    # Funding adjustment
    funding = np.nan_to_num(ctx.funding_1h, nan=0.0) if ctx.funding_1h is not None else np.zeros(n)
    composite_sign = np.sign(np.nan_to_num(composite, nan=0.0))
    funding_alignment = -composite_sign * np.sign(funding)
    funding_meaningful = np.abs(funding) > 1e-8
    funding_factor = np.ones(n)
    funding_factor[funding_meaningful & (funding_alignment > 0)] = 1.0 + FUNDING_BOOST
    funding_factor[funding_meaningful & (funding_alignment < 0)] = 1.0 - FUNDING_BOOST
    adjusted_composite = composite * funding_factor

    # BTC regime detection (same as s524g)
    W = 168
    try:
        _btc_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv")
        if not hasattr(_get_composite_aligned, '_btc_close_cache'): _get_composite_aligned._btc_close_cache = None
        if _get_composite_aligned._btc_close_cache is None:
            _btc_df = pd.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
            if _btc_df.index.tz is not None: _btc_df.index = _btc_df.index.tz_convert(None)
            _get_composite_aligned._btc_close_cache = _btc_df["close"].astype(np.float64)
        btc_aligned = _get_composite_aligned._btc_close_cache.reindex(ctx.idx_1h, method="ffill")
        _m_ret_1mo_h = np.nan_to_num((btc_aligned / btc_aligned.shift(30 * 24) - 1).values, nan=0)
        _m = btc_aligned.resample('MS').agg(['first', 'last']); _m.columns = ['open', 'close']
        _m['body'] = ((_m['close'] - _m['open']) / _m['open']).abs() * 100
        _m_body_rel_h = (_m['body'].rolling(6, min_periods=3).mean() / _m['body'].rolling(12, min_periods=6).median().replace(0, np.nan)).shift(1).reindex(ctx.idx_1h, method='ffill').values
        _is_trending = np.nan_to_num(_m_body_rel_h, nan=1.0) > 1.2
        _w_sma50 = btc_aligned.rolling(50 * W, min_periods=25 * W).mean()
        _above_w50 = (btc_aligned > _w_sma50).values
        _trending_up = _is_trending & _above_w50
        _m_ret_14d_h = np.nan_to_num((btc_aligned / btc_aligned.shift(14 * 24) - 1).values, nan=0)
        _m_ret_45d_h = np.nan_to_num((btc_aligned / btc_aligned.shift(45 * 24) - 1).values, nan=0)
        _btc_dom_gate = np.zeros(n, dtype=bool)
        try:
            _t2_gate_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "alternative", "total2_total3.parquet")
            if not hasattr(_get_composite_aligned, '_total2_close_for_gate'): _get_composite_aligned._total2_close_for_gate = None
            if _get_composite_aligned._total2_close_for_gate is None and os.path.exists(_t2_gate_path):
                _t2g = pd.read_parquet(_t2_gate_path)
                if _t2g.index.tz is not None: _t2g.index = _t2g.index.tz_localize(None)
                _get_composite_aligned._total2_close_for_gate = _t2g['total2_close']
            _t2c = _get_composite_aligned._total2_close_for_gate
            if _t2c is not None:
                _t2a = _t2c.reindex(ctx.idx_1h.normalize(), method='ffill'); _t2a.index = ctx.idx_1h
                _t2_45d = np.nan_to_num((_t2a / _t2a.shift(45 * 24) - 1).values, nan=0)
                _btc_dom_gate = (_m_ret_45d_h > 0) & (_t2_45d < 0)
        except: pass
        _short_ok = np.where(_btc_dom_gate, _m_ret_14d_h < 0, _m_ret_45d_h < 0)
        _long_conv = np.ones(n); _long_conv[_trending_up] = 1.3
    except:
        _short_ok = np.ones(n, dtype=bool); _long_conv = np.ones(n); _trending_up = np.zeros(n, dtype=bool)

    # Day boundaries
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool); day_change[0] = True; day_change[1:] = dates[1:] != dates[:-1]

    # RSI timing
    close_4h = close[::RSI_RESAMPLE]; rsi_4h = compute_rsi(close_4h, RSI_PERIOD)
    rsi_4h_prev = np.roll(rsi_4h, 1); rsi_4h_prev[0] = rsi_4h[0]
    cross_up_40_1h = np.repeat((rsi_4h > RSI_LONG_LEVEL) & (rsi_4h_prev <= RSI_LONG_LEVEL), RSI_RESAMPLE)[:n]
    cross_down_60_1h = np.repeat((rsi_4h < RSI_SHORT_LEVEL) & (rsi_4h_prev >= RSI_SHORT_LEVEL), RSI_RESAMPLE)[:n]
    if len(cross_up_40_1h) < n: cross_up_40_1h = np.pad(cross_up_40_1h, (0, n - len(cross_up_40_1h)), mode='edge')
    if len(cross_down_60_1h) < n: cross_down_60_1h = np.pad(cross_down_60_1h, (0, n - len(cross_down_60_1h)), mode='edge')
    rsi_long_window = pd.Series(cross_up_40_1h.astype(np.float64)).rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0
    rsi_short_window = pd.Series(cross_down_60_1h.astype(np.float64)).rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0

    # Composite thresholds
    abs_composite = np.abs(np.nan_to_num(adjusted_composite, nan=0.0))
    long_level = adjusted_composite > THRESHOLD; short_level = adjusted_composite < -THRESHOLD
    long_prev = np.roll(long_level, 1); long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)
    short_prev = np.roll(short_level, 1); short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    # Dilution filter
    _dol = np.ones(n, dtype=bool); _doa = np.ones(n, dtype=bool)
    try:
        _dp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "alternative", "token_historical_circulating.parquet")
        if not hasattr(_get_composite_aligned, '_dilution_cache'): _get_composite_aligned._dilution_cache = None
        if _get_composite_aligned._dilution_cache is None:
            _dd = pd.read_parquet(_dp); _dc = {}
            for _, _r in _dd.iterrows(): _dc[(_r['token'], _r['date'].strftime('%Y-%m'))] = float(_r['remaining_pct'])
            _get_composite_aligned._dilution_cache = _dc
        _dc = _get_composite_aligned._dilution_cache; _ts = ticker.replace('USDT', '') if ticker.endswith('USDT') else ticker; _rem = 0.0
        for _mo in range(60):
            _ck = (ctx.idx_1h[-1] - pd.Timedelta(days=_mo * 30)).strftime('%Y-%m')
            if (_ts, _ck) in _dc: _rem = _dc[(_ts, _ck)]; break
        if _rem > 50: _doa[:] = False
    except: pass

    # Load TOTAL2 + reversal regime
    _t2_signals = _load_total2_signals(ctx.idx_1h, btc_aligned)
    _total2_bear = _t2_signals["total2_bear"]
    _total2_ret = _t2_signals["total2_ret_30d"]
    _total2_above = _t2_signals["total2_above_sma"]

    _bar_years = np.array([t.year for t in ctx.idx_1h])
    _post_halving = np.isin(_bar_years, [2021, 2022, 2025, 2026, 2029, 2030])
    _sof = np.where(_post_halving, True, _short_ok)

    _bear_regime = _compute_reversal_regime(ctx.idx_1h, btc_aligned)
    _bar_months = np.array([t.month for t in ctx.idx_1h])
    _august = np.isin(_bar_months, [8])
    _bear_regime_long_boost = np.where((~_bear_regime) & (~_august), 1.2, 1.0)
    _bear_regime_short_boost = np.ones(n)

    # Apply standard s524g filters
    long_signal = long_signal & day_change & rsi_long_window & _dol & _doa
    short_signal = short_signal & day_change & rsi_short_window & _sof & _doa

    # Rotation filter
    if ENABLE_ROTATION_FILTER:
        try:
            _btc_vals = btc_aligned.values if hasattr(btc_aligned, 'values') else btc_aligned
            _btc_14d_ret = np.nan_to_num(_btc_vals / np.roll(_btc_vals, 14 * 24) - 1, nan=0.0)
            _tok_14d_ret = np.nan_to_num(close / np.roll(close, 14 * 24) - 1, nan=0.0)
            _btc_14d_ret[:14 * 24 + 1] = 0.0; _tok_14d_ret[:14 * 24 + 1] = 0.0
            _rel_perf_14d = _tok_14d_ret - _btc_14d_ret
            short_signal = short_signal & ~(_bear_regime & (_rel_perf_14d < -ROTATION_FILTER_THRESHOLD))
            long_signal = long_signal & ~(_bear_regime & (_rel_perf_14d > ROTATION_FILTER_THRESHOLD))
        except: pass

    # Deep bear long suppression
    try:
        _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < DEEP_BEAR_BEAR
        long_signal = long_signal & ~_deep_bear
    except: pass

    # ================================================================
    # S525: BTC TREND FILTER — the key enhancement
    # ================================================================
    if TREND_ONLY_MODE:
        btc_bull_trend = _compute_btc_trend(ctx.idx_1h)
        long_signal = long_signal & btc_bull_trend
        # Allow shorts when BTC is bearish OR in halving years (same as _sof logic)
        short_signal = short_signal & (~btc_bull_trend | _post_halving)

    # Compose entry
    entry = long_signal | short_signal
    direction = np.where(long_signal, 1, np.where(short_signal, -1, 0)).astype(np.int8)
    entry[:WARMUP] = False

    # Conviction
    conviction = np.minimum(1.0, abs_composite / 3.0)
    _lm = direction == 1; _sm = direction == -1
    conviction[_lm] *= _long_conv[_lm]
    conviction[_lm] *= _bear_regime_long_boost[_lm]
    conviction[_sm] *= _bear_regime_short_boost[_sm]
    if ENABLE_TOTAL2_SHORT_CONV_BOOST:
        if TOTAL2_SHORT_BOOST_NONHALVING_ONLY:
            conviction[_sm & _total2_bear & ~_post_halving] *= TOTAL2_SHORT_CONV_BOOST_FACTOR
        else:
            conviction[_sm & _total2_bear] *= TOTAL2_SHORT_CONV_BOOST_FACTOR
    conviction = np.minimum(1.0, conviction); conviction[~entry] = 0.0

    base_edge = min(0.5, float(np.nanmean(abs_composite[entry])) * 0.1) if entry.any() else 0.30
    _size_mult = np.ones(n)
    token_no_stop = max(NO_STOP_BARS, token_max_hold // 2)

    # S525: Use standard leverage (adaptive leverage tested but doesn't help due to
    # regime detection lag — the April 2024 crash happens before any regime flips)

    return StrategyResult(
        entry_mask=entry, direction=direction, market_type=MARKET, leverage=LEVERAGE,
        stop_mult=STOP_MULT, trail_mult=TRAIL_MULT, target_mult=999,
        no_stop_bars=token_no_stop, min_hold=MIN_HOLD, max_hold=token_max_hold,
        edge=base_edge, name='s525_momentum_companion', breakeven_atr=BREAKEVEN_ATR,
        conviction_score=conviction, size_multiplier=_size_mult, exit_regimes={CRISIS},
    )
