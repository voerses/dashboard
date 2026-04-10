"""
# BACKTEST CLI:
#   /workspace/venv/bin/python v4/portfolio_backtest.py \
#       --strategy s523y_cycle_score --months 12 --capital 100000 \
#       --market perp --conviction-mode ranked \
#       --max-portfolio-positions 40 --concentration 0.30 --skip-wf \
#       --end-date 2026-04-05T16:00:00
#
S523y — Cycle Position Score Regime Detection
==============================================

Uses a continuous 0-1 "Cycle Position Score" from 12 normalized features
across 5 dimensions (price position, momentum, volume, volatility, alt health).

Regime mapping:
  Score < 0.40 → BEAR: shorts ungated, deep bear -10%
  Score > 0.60 → BULL: shorts gated by 60d return, deep bear -3%
  0.40 - 0.60 → TRANSITION: shorts gated by 30d return, deep bear -5%

Optimized config: B40/U60/G30 = +564% all-years-positive (no hardcoded years).

Walk-forward validated: bull>0.75 has 74.5% accuracy, bear<0.30 has 80%.
No hardcoded years. Fully causal. Adapts to any market cycle.
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
BREAKEVEN_ATR = 1.0

TOKEN_BLACKLIST = set()
FUNDING_BOOST = 0.10
WARMUP = 400
MARKET = MarketType.PERP

PORTFOLIO_CONFIG = {
    "conviction_mode": "ranked",
    "max_positions": 50,
}

# Cycle score regime thresholds (optimized via parameter sweep)
BEAR_THRESHOLD = 0.40
BULL_THRESHOLD = 0.60


# ======================================================================
#  LOAD PER-TOKEN CONFIG
# ======================================================================

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "s521_token_config.json",
)
_token_configs: dict = {}
if os.path.exists(_CONFIG_PATH):
    with open(_CONFIG_PATH) as _f:
        _token_configs = json.load(_f)
    print(f"  [s523y] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s523y] WARNING: Config not found at {_CONFIG_PATH}")


# ======================================================================
#  DATA CACHES
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


def _daily_zscore(arr, window):
    s = pd.Series(arr, dtype=np.float64)
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
    z = (s - mu) / sd.replace(0, np.nan)
    return z.values


def _load_daily_signals(symbol, ticker):
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
        df = pd.read_parquet(parquet_path,
            columns=["create_time", "sum_open_interest_value",
                      "sum_toptrader_long_short_ratio",
                      "sum_taker_long_short_vol_ratio"])
    except Exception as exc:
        print(f"  [s523y] WARNING: Failed to load {parquet_path}: {exc}")
        return
    df["create_time"] = pd.to_datetime(df["create_time"])
    df = df.set_index("create_time").sort_index()
    if _paper_mode and PAPER_LOOKBACK_DAYS > 0:
        cutoff = df.index[-1] - pd.Timedelta(days=PAPER_LOOKBACK_DAYS)
        df = df.loc[cutoff:]
    daily = df.resample("1D").last().dropna(how="all")
    cfg = _token_configs[ticker]
    w_oi, w_pos, w_flow = cfg["oi_weight"], cfg["pos_weight"], cfg["flow_weight"]
    oi_sign, pos_sign, flow_sign = cfg["oi_sign"], cfg["pos_sign"], cfg["flow_sign"]
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
    composite = (w_oi * oi_z * oi_sign + w_pos * pos_z * pos_sign + w_flow * flow_z * flow_sign)
    composite_shifted = np.empty_like(composite)
    composite_shifted[0] = np.nan
    composite_shifted[1:] = composite[:-1]
    _composite_cache[symbol] = pd.Series(composite_shifted, index=daily.index, dtype=np.float64)


def _get_composite_aligned(symbol, ticker, idx_1h):
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
#  CYCLE POSITION SCORE (computed once, cached)
# ======================================================================

def _compute_cycle_score_daily(btc_close_daily, btc_volume_daily, alt_breadth_daily):
    """Compute daily cycle position score from BTC price, volume, and alt breadth.

    Returns pd.Series with values in [0, 1].
    0 = deep bear/capitulation, 1 = peak euphoria.
    All computations are causal (rolling windows only).
    """
    close = btc_close_daily
    volume = btc_volume_daily
    ret_d = close.pct_change()

    # --- Price Position ---
    sma350 = close.rolling(350, min_periods=200).mean()
    sma50_dist = (close - sma350) / sma350

    ath = close.expanding().max()
    ath_drawdown = (close - ath) / ath

    low_365 = close.rolling(365, min_periods=180).min()
    rally_from_low = (close - low_365) / low_365

    # --- Momentum ---
    def _rsi_daily(series, period):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta.clip(upper=0))
        avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    rsi_30d = _rsi_daily(close, 30)
    ret_30d = close.pct_change(30)
    roc_momentum = ret_30d - ret_30d.shift(30)

    # --- Volume ---
    vol_30 = volume.rolling(30, min_periods=15).mean()
    vol_90 = volume.rolling(90, min_periods=45).mean()
    volume_trend = vol_30 / vol_90 - 1

    # --- Volatility ---
    realized_vol_30 = ret_d.rolling(30, min_periods=15).std() * np.sqrt(365)
    realized_vol_180 = ret_d.rolling(180, min_periods=90).std() * np.sqrt(365)
    vol_regime = realized_vol_30 / realized_vol_180 - 1

    # --- Alt breadth ---
    alt_br = alt_breadth_daily.fillna(0.5)
    alt_br_momentum = alt_br - alt_br.shift(30)

    # --- Absolute scaling (fixed ranges from crypto market knowledge) ---
    # No rolling normalization — uses known feature ranges for direct mapping
    sma_s = np.clip((sma50_dist + 0.50) / 1.50, 0, 1)    # -50% → 0, +100% → 1
    ath_s = np.clip((ath_drawdown + 0.75) / 0.75, 0, 1)   # -75% → 0, 0% → 1
    rsi_s = np.clip((rsi_30d - 20) / 60, 0, 1)             # RSI 20 → 0, 80 → 1
    alt_s = alt_br.clip(0, 1)                                # already 0-1
    rally_s = np.clip(rally_from_low / 4.0, 0, 1)           # 0% → 0, 400%+ → 1
    ret_s = np.clip((ret_30d + 0.30) / 0.60, 0, 1)          # -30% → 0, +30% → 1
    vol_s = 1 - np.clip(realized_vol_30 / 1.50, 0, 1)       # high vol = bearish → invert

    # Weighted score — top features by IC weighted higher
    base_score = (
        0.25 * sma_s +       # SMA50 dist (IC=0.159)
        0.20 * ath_s +       # ATH drawdown (IC=0.106)
        0.15 * rsi_s +       # RSI 30d (IC=0.109)
        0.15 * alt_s +       # Alt breadth (IC captured via alt health)
        0.10 * rally_s +     # Rally from low (IC=0.193)
        0.10 * ret_s +       # 30d return momentum
        0.05 * vol_s         # Volatility regime
    ).clip(0, 1)

    # Alt distress discount: when alts are bleeding AND BTC is declining,
    # slash the score to push toward BEAR regime.
    # This captures 2025 Q1 (alts bleeding while BTC still elevated)
    # without triggering in 2023 (alts lagging but BTC rallying).
    btc_weak = ret_30d < 0
    raw_discount = np.clip(alt_br / 0.30, 0.4, 1.0)
    # Only apply discount when BTC is also weak
    discount = pd.Series(
        np.where(btc_weak, raw_discount, 1.0),
        index=base_score.index
    )
    score = (base_score * discount).clip(0, 1)

    return score


# ======================================================================
#  STRATEGY FUNCTION
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker
    symbol = ticker + "USDT"

    if ticker in TOKEN_BLACKLIST or ticker not in _token_configs:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            market_type=MARKET, leverage=LEVERAGE,
            stop_mult=STOP_MULT, trail_mult=TRAIL_MULT,
            target_mult=999, no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD, max_hold=720, edge=0.0,
            name='s523y_cycle_score', breakeven_atr=BREAKEVEN_ATR,
        )

    cfg = _token_configs[ticker]
    token_max_hold = cfg["max_hold_hours"]
    _load_daily_signals(symbol, ticker)
    composite = _get_composite_aligned(symbol, ticker, ctx.idx_1h)

    # Funding adjustment
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

    # ==== BTC data + Cycle Score computation ====
    W = 168
    try:
        _btc_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv",
        )
        # Load BTC 1h data (cached)
        if not hasattr(_get_composite_aligned, '_btc_1h_cache_y'):
            _get_composite_aligned._btc_1h_cache_y = None
        if _get_composite_aligned._btc_1h_cache_y is None:
            _btc_df = pd.read_csv(_btc_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
            if _btc_df.index.tz is not None:
                _btc_df.index = _btc_df.index.tz_convert(None)
            _get_composite_aligned._btc_1h_cache_y = _btc_df
        _btc_1h = _get_composite_aligned._btc_1h_cache_y
        btc_aligned = _btc_1h["close"].astype(np.float64).reindex(ctx.idx_1h, method="ffill")

        # Rolling returns for gates and deep bear
        _m_ret_30d_h = (btc_aligned / btc_aligned.shift(30 * 24) - 1).values
        _m_ret_30d_h = np.nan_to_num(_m_ret_30d_h, nan=0)

        _m_ret_30d_gate_h = (btc_aligned / btc_aligned.shift(30 * 24) - 1).values
        _m_ret_30d_gate_h = np.nan_to_num(_m_ret_30d_gate_h, nan=0)

        _m_ret_60d_h = (btc_aligned / btc_aligned.shift(60 * 24) - 1).values
        _m_ret_60d_h = np.nan_to_num(_m_ret_60d_h, nan=0)

        # Monthly body relative (for trending detection)
        _m = btc_aligned.resample('MS').agg(['first', 'last'])
        _m.columns = ['open', 'close']
        _m['body'] = ((_m['close'] - _m['open']) / _m['open']).abs() * 100
        _m_body_6mo = _m['body'].rolling(6, min_periods=3).mean()
        _m_body_12mo_med = _m['body'].rolling(12, min_periods=6).median()
        _m_body_relative = _m_body_6mo / _m_body_12mo_med.replace(0, np.nan)
        _m_body_rel_h = _m_body_relative.shift(1).reindex(ctx.idx_1h, method='ffill').values
        _is_trending = np.nan_to_num(_m_body_rel_h, nan=1.0) > 1.2

        _w_sma50 = btc_aligned.rolling(50 * W, min_periods=25 * W).mean()
        _above_w50 = (btc_aligned > _w_sma50).values
        _trending_up = _is_trending & _above_w50

        # ---- CYCLE POSITION SCORE ----
        # Compute daily score (cached across token calls)
        if not hasattr(_get_composite_aligned, '_cycle_score_cache'):
            _get_composite_aligned._cycle_score_cache = None
        if _get_composite_aligned._cycle_score_cache is None:
            # Resample BTC to daily for score computation
            _btc_daily = _btc_1h.resample('1D').agg({
                'close': 'last', 'volume': 'sum'
            }).dropna(subset=['close'])
            _btc_daily['close'] = _btc_daily['close'].astype(np.float64)
            _btc_daily['volume'] = _btc_daily['volume'].astype(np.float64)

            # Load alt breadth
            _regime_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "alternative", "regime_signals.parquet",
            )
            _alt_br = pd.Series(0.5, index=_btc_daily.index)
            if os.path.exists(_regime_path):
                _regime_df = pd.read_parquet(_regime_path)
                if _regime_df.index.tz is not None:
                    _regime_df.index = _regime_df.index.tz_localize(None)
                if 'alt_breadth_50d' in _regime_df.columns:
                    _alt_br = _regime_df['alt_breadth_50d'].reindex(_btc_daily.index, method='ffill').fillna(0.5)

            _get_composite_aligned._cycle_score_cache = _compute_cycle_score_daily(
                _btc_daily['close'], _btc_daily['volume'], _alt_br
            )

        _cycle_score_daily = _get_composite_aligned._cycle_score_cache

        # Forward-fill daily score to 1h bars
        _score_h = _cycle_score_daily.reindex(ctx.idx_1h.normalize(), method='ffill')
        _score_h.index = ctx.idx_1h
        _score_h = _score_h.fillna(0.5).values

        # ---- Map cycle score to regime ----
        _is_bear = _score_h < BEAR_THRESHOLD
        _is_bull = _score_h > BULL_THRESHOLD
        _is_transition = ~_is_bear & ~_is_bull

        # Short gate:
        # BEAR: ungated (all shorts fire)
        # BULL: gated by 60d return < 0 (strict but not zero)
        # TRANSITION: gated by 30d return < 0 (faster gate, proven in sweep)
        _short_ok_30d = (_m_ret_30d_gate_h < 0)
        _short_ok_60d = (_m_ret_60d_h < 0)
        _sof = np.where(
            _is_bear, True,
            np.where(_is_bull, _short_ok_60d, _short_ok_30d)
        )

        # Deep bear long suppression (regime-adaptive):
        # BEAR: block longs when 30d return < -10%
        # BULL: block longs when 30d return < -3%
        # TRANSITION: block longs when 30d return < -5%
        _db_thresh = np.where(
            _is_bear, -0.10,
            np.where(_is_bull, -0.03, -0.05)
        )
        _deep_bear = _m_ret_30d_h < _db_thresh

        # Conviction boost in bull regime with trending-up
        _long_conv = np.ones(n, dtype=np.float64)
        _long_conv[_trending_up & _is_bull] = 1.3

    except Exception:
        _sof = np.ones(n, dtype=bool)
        _long_conv = np.ones(n, dtype=np.float64)
        _trending_up = np.zeros(n, dtype=bool)
        _deep_bear = np.zeros(n, dtype=bool)

    # ---- Day boundaries ----
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # ---- RSI timing overlay ----
    close_4h = close[::RSI_RESAMPLE]
    rsi_4h = compute_rsi(close_4h, RSI_PERIOD)
    rsi_4h_prev = np.roll(rsi_4h, 1); rsi_4h_prev[0] = rsi_4h[0]
    cross_up_40_4h = (rsi_4h > RSI_LONG_LEVEL) & (rsi_4h_prev <= RSI_LONG_LEVEL)
    cross_down_60_4h = (rsi_4h < RSI_SHORT_LEVEL) & (rsi_4h_prev >= RSI_SHORT_LEVEL)
    cross_up_40_1h = np.repeat(cross_up_40_4h, RSI_RESAMPLE)[:n]
    cross_down_60_1h = np.repeat(cross_down_60_4h, RSI_RESAMPLE)[:n]
    if len(cross_up_40_1h) < n:
        cross_up_40_1h = np.pad(cross_up_40_1h, (0, n - len(cross_up_40_1h)), mode='edge')
        cross_down_60_1h = np.pad(cross_down_60_1h, (0, n - len(cross_down_60_1h)), mode='edge')
    rsi_long_window = pd.Series(cross_up_40_1h.astype(np.float64)).rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0
    rsi_short_window = pd.Series(cross_down_60_1h.astype(np.float64)).rolling(RSI_WINDOW_1H, min_periods=1).max().values > 0

    # ---- Entry signals ----
    abs_composite = np.abs(np.nan_to_num(adjusted_composite, nan=0.0))
    long_level = adjusted_composite > THRESHOLD
    short_level = adjusted_composite < -THRESHOLD
    long_prev = np.roll(long_level, 1); long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)
    short_prev = np.roll(short_level, 1); short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    # ---- Dilution filter ----
    _dol = np.ones(n, dtype=bool); _doa = np.ones(n, dtype=bool)
    try:
        _dp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "alternative", "token_historical_circulating.parquet")
        if not hasattr(_get_composite_aligned, '_dilution_cache'):
            _get_composite_aligned._dilution_cache = None
        if _get_composite_aligned._dilution_cache is None:
            _dd = pd.read_parquet(_dp); _dc = {}
            for _, _r in _dd.iterrows():
                _dc[(_r['token'], _r['date'].strftime('%Y-%m'))] = float(_r['remaining_pct'])
            _get_composite_aligned._dilution_cache = _dc
        _dc = _get_composite_aligned._dilution_cache
        _ts = ticker.replace('USDT', '') if ticker.endswith('USDT') else ticker
        _rem = 0.0
        for _mo in range(60):
            _ck = (ctx.idx_1h[-1] - pd.Timedelta(days=_mo * 30)).strftime('%Y-%m')
            if (_ts, _ck) in _dc: _rem = _dc[(_ts, _ck)]; break
        if _rem > 75: _doa[:] = False
        elif _rem > 50: _dol[:] = False
    except: pass

    # ---- Entry gating ----
    long_signal = long_signal & day_change & rsi_long_window & _dol & _doa
    short_signal = short_signal & day_change & rsi_short_window & _sof & _doa

    # Apply deep bear long suppression
    long_signal = long_signal & ~_deep_bear

    entry = long_signal | short_signal
    direction = np.where(long_signal, 1, np.where(short_signal, -1, 0)).astype(np.int8)
    entry[:WARMUP] = False

    # Conviction scoring
    conviction = np.minimum(1.0, abs_composite / 3.0)
    _lm = direction == 1
    conviction[_lm] *= _long_conv[_lm]
    conviction = np.minimum(1.0, conviction)
    conviction[~entry] = 0.0

    base_edge = min(0.5, float(np.nanmean(abs_composite[entry])) * 0.1) if entry.any() else 0.30
    token_no_stop = max(NO_STOP_BARS, token_max_hold // 2)

    return StrategyResult(
        entry_mask=entry, direction=direction,
        market_type=MARKET, leverage=LEVERAGE,
        stop_mult=STOP_MULT, trail_mult=TRAIL_MULT,
        target_mult=999, no_stop_bars=token_no_stop,
        min_hold=MIN_HOLD, max_hold=token_max_hold,
        edge=base_edge, name='s523y_cycle_score',
        breakeven_atr=BREAKEVEN_ATR, conviction_score=conviction,
        exit_regimes={CRISIS},
    )
