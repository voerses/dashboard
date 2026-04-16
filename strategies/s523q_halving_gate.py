"""
# BACKTEST CLI (reproduce this strategy's results):
#   /workspace/venv/bin/python v4/portfolio_backtest.py \
#       --strategy s523q_halving_gate --months 12 --capital 100000 \
#       --market perp --conviction-mode ranked \
#       --max-portfolio-positions 30 --skip-wf \
#       --end-date 2026-04-05T16:00:00
#
S523 — RSI-Timed Composite Positioning
=======================================

Evolved from s522. Adds RSI timing overlay to improve entry timing.

Research finding #102: "Defer entry within 168-bar window to first 4h RSI
cross-up through 40 (for longs), fallback to normal entry."

All s522 logic is preserved. The only change is an additional RSI filter
on the entry mask:

**Long entries:** composite > threshold on day boundary AND 4H RSI(14) crossed
  UP through 40 from below within the last 72 1H-bars (3 days).

**Short entries:** composite < -threshold on day boundary AND 4H RSI(14) crossed
  DOWN through 60 from above within the last 72 1H-bars (3 days).

The RSI filter requires a recent momentum inflection (oversold bounce for longs,
overbought fade for shorts) to coincide with the positioning signal. This filters
out entries where price momentum hasn't yet confirmed the positioning reversal,
reducing drawdown while maintaining return.

Config: data/alternative/s521_token_config.json (same as s522)

Entry: |composite| > 1.0 on first bar of new day AND RSI timing condition met.
Direction: sign(composite) * IC_sign.
Edge: min(0.5, |composite| * 0.1) — continuous, proportional to signal strength.
Hold: per-token (24h to 720h). Stop: 5.0 ATR. Trail: 999 (disabled). Breakeven: 1.0 ATR.

IC basis: 139-token scan with per-token IC-proportional weighting.
Status: RESEARCH (Gate 3 backtest)
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
    print(f"  [s523] Loaded per-token configs for {len(_token_configs)} tokens")
else:
    print(f"  [s523] WARNING: Config not found at {_CONFIG_PATH}")


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
        print(f"  [s523] WARNING: Failed to load {parquet_path}: {exc}")
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
            name='s523q_halving_gate',
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
        _m = btc_aligned.resample('MS').agg(['first', 'last'])
        _m.columns = ['open', 'close']
        _m['body'] = ((_m['close'] - _m['open']) / _m['open']).abs() * 100
        _m['ret'] = _m['close'].pct_change()
        _m_body_6mo = _m['body'].rolling(6, min_periods=3).mean()
        _m_body_12mo_med = _m['body'].rolling(12, min_periods=6).median()
        _m_body_relative = _m_body_6mo / _m_body_12mo_med.replace(0, np.nan)
        _m_ret_1mo = _m['ret']
        _m_body_rel_h = _m_body_relative.reindex(ctx.idx_1h, method='ffill').values
        _m_ret_1mo_h = _m_ret_1mo.reindex(ctx.idx_1h, method='ffill').values

        # Relative regime
        _is_trending = np.nan_to_num(_m_body_rel_h, nan=1.0) > 1.2
        _is_choppy = np.nan_to_num(_m_body_rel_h, nan=1.0) < 0.8

        # Layer 2: Weekly SMA50 for trend direction
        _w_sma50 = btc_aligned.rolling(50 * W, min_periods=25 * W).mean()
        _above_w50 = (btc_aligned > _w_sma50).values
        _trending_up = _is_trending & _above_w50

        # 1mo_red short gate: shorts only when last BTC monthly return < 0
        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)

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


    _dilution_ok_long = np.ones(n, dtype=bool)
    _dilution_ok_any = np.ones(n, dtype=bool)
    try:
        _dp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "alternative", "token_historical_circulating.parquet")
        if not hasattr(_get_composite_aligned, '_dilution_cache'):
            _get_composite_aligned._dilution_cache = None
        if _get_composite_aligned._dilution_cache is None:
            import pandas as _pd_d
            _dd = _pd_d.read_parquet(_dp)
            _dc = {}
            for _, _r in _dd.iterrows():
                _dc[(_r['token'], _r['date'].strftime('%Y-%m'))] = float(_r['remaining_pct'])
            _get_composite_aligned._dilution_cache = _dc
        _dc = _get_composite_aligned._dilution_cache
        _ts = ticker.replace('USDT', '') if ticker.endswith('USDT') else ticker
        _rem = 0.0
        for _mo in range(60):
            _ck = (ctx.idx_1h[-1] - pd.Timedelta(days=_mo*30)).strftime('%Y-%m')
            if (_ts, _ck) in _dc:
                _rem = _dc[(_ts, _ck)]
                break
        if _rem > 75: _dilution_ok_any[:] = False
        elif _rem > 50: _dilution_ok_long[:] = False
    except: pass

    # ==== s523q: HALVING CYCLE short gate ====
    # Bull window: halving-12mo TO halving+12mo → 1mo_red gate (protect bull)
    # Bear window: everything else → NO gate (let shorts rip)
    _halvings = [pd.Timestamp('2012-11-28'), pd.Timestamp('2016-07-09'),
                 pd.Timestamp('2020-05-11'), pd.Timestamp('2024-04-20')]
    try:
        _in_bull_window = np.zeros(n, dtype=bool)
        for _h in _halvings:
            _bull_start = _h - pd.DateOffset(months=12)
            _bull_end = _h + pd.DateOffset(months=12)
            _in_bull_window |= ((ctx.idx_1h >= _bull_start) & (ctx.idx_1h <= _bull_end)).values
        # In bull window: use 1mo_red. In bear window: all shorts allowed.
        _short_ok_halving = np.where(_in_bull_window, _short_ok, True)
    except Exception:
        _short_ok_halving = _short_ok
    # ==== end halving gate ====
    long_signal = long_signal & day_change & rsi_long_window & _dilution_ok_long & _dilution_ok_any
    short_signal = short_signal & day_change & rsi_short_window & _short_ok_halving & _dilution_ok_any

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
        name='s523q_halving_gate',
        breakeven_atr=BREAKEVEN_ATR,
        conviction_score=conviction,
        exit_regimes={CRISIS},  # tested: removing HURTS (+528% → +306%, DD -46% → -54%)
    )
