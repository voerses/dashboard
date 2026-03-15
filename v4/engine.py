"""
V4 Backtest Engine — Types, Context Builder, and Helpers
=========================================================

Consolidation of v3/engine.py types and context building into v4.
This module provides StrategyContext, StrategyResult, MarketType, regime
constants, indicator computation, and the Engine class for context building.

Simulation is handled by v4/simulator.py (portfolio-level simulation).
Strategy files import `from engine import StrategyContext, StrategyResult`.
"""

import sys
import os
import warnings

import numpy as np
import pandas as pd
import importlib.util
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Tuple, Union
from pathlib import Path

# Import from v4/universe.py
from v4.universe import (
    get_all_tradeable,
    compute_adv,
    adv_to_tier,
    adv_to_sizing,
    adv_to_costs,
    get_fee_rate,
    get_maint_margin_rate,
    FALLBACK_ADV,
    LIQUID_TOKENS,
    compute_liquidity_mask,
    compute_rolling_adv,
)

# Numba JIT — optional
try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    def njit(*args, **kwargs):
        def decorator(func):
            return func
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator


# =============================================================================
# Market Type Constants (Numba-compatible integers)
# =============================================================================

class MarketType:
    SPOT = 0
    PERP = 1
    COMBINED = 2

_MARKET_INT_TO_STR = {MarketType.SPOT: 'spot', MarketType.PERP: 'perp', MarketType.COMBINED: 'combined'}


# =============================================================================
# Regime Constants
# =============================================================================

CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4


# =============================================================================
# Fast Rolling Helpers
# =============================================================================

def _rolling_mean(arr, w):
    """O(n) rolling mean via cumsum."""
    cs = np.cumsum(np.nan_to_num(arr, 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(arr), np.nan)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def _rolling_std(arr, w):
    """O(n) rolling std via cumsum."""
    a = np.nan_to_num(arr, 0.0)
    cs = np.insert(np.cumsum(a), 0, 0.0)
    cs2 = np.insert(np.cumsum(a ** 2), 0, 0.0)
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = (s2 - s ** 2 / w) / max(w - 1, 1)
    var = np.maximum(var, 0)
    out = np.full(len(arr), np.nan)
    out[w - 1:] = np.sqrt(var)
    return out


def _ema(arr, span):
    """Vectorized EMA."""
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values


# =============================================================================
# Timeframe Aggregation
# =============================================================================

def aggregate_to_timeframe(df_1h, hours=4):
    """Aggregate 1H bars to any higher timeframe."""
    ohlcv = df_1h.resample(f'{hours}h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum',
    }).dropna(subset=['open'])

    if 'taker_buy_base' in df_1h.columns:
        tb = df_1h.resample(f'{hours}h').agg({'taker_buy_base': 'sum'})
        tb = tb.reindex(ohlcv.index)
        ohlcv['taker_buy_ratio'] = np.where(
            ohlcv['volume'] > 0,
            tb['taker_buy_base'].fillna(0) / ohlcv['volume'].clip(lower=1e-10),
            0.5)
    if 'quote_volume' in df_1h.columns:
        ohlcv['quote_volume'] = df_1h.resample(f'{hours}h')['quote_volume'].sum()
    if 'trades' in df_1h.columns:
        ohlcv['trades'] = df_1h.resample(f'{hours}h')['trades'].sum()
    return ohlcv


# =============================================================================
# Indicator Computation
# =============================================================================

def compute_indicators_fast(close, high, low, volume, taker_buy=None, quote_vol=None):
    """Compute all indicators as numpy arrays. No pandas DataFrames."""
    n = len(close)

    ema_10 = _ema(close, 10)
    ema_20 = _ema(close, 20)
    ema_50 = _ema(close, 50)

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    macd_signal = _ema(macd, 9)
    macd_hist = macd - macd_signal

    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema(gains, 14)
    avg_loss = _ema(losses, 14)
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100 - 100 / (1 + rs)

    sma20 = _rolling_mean(close, 20)
    bb_std = _rolling_std(close, 20)
    bb_upper = sma20 + 2 * bb_std
    bb_lower = sma20 - 2 * bb_std
    bb_width = 4 * bb_std / np.maximum(sma20, 1e-10)
    bb_pct = (close - np.nan_to_num(bb_lower, 0)) / np.maximum(4 * np.nan_to_num(bb_std, 1), 1e-10)

    tr = np.zeros(n)
    tr[1:] = np.maximum(high[1:] - low[1:],
                         np.maximum(np.abs(high[1:] - close[:-1]),
                                    np.abs(low[1:] - close[:-1])))
    atr = _ema(tr, 14)

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    plus_dm[1:] = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                            np.maximum(high[1:] - high[:-1], 0), 0)
    minus_dm[1:] = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                             np.maximum(low[:-1] - low[1:], 0), 0)
    smooth_atr = _ema(tr, 14)
    plus_di = 100 * _ema(plus_dm, 14) / np.maximum(smooth_atr, 1e-10)
    minus_di = 100 * _ema(minus_dm, 14) / np.maximum(smooth_atr, 1e-10)
    dx = np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10) * 100
    adx = _ema(dx, 14)

    vol_sma = _rolling_mean(volume, 20)
    vol_ratio = volume / np.maximum(np.nan_to_num(vol_sma, 1), 1e-10)

    ret_1 = np.log(close / np.maximum(np.roll(close, 1), 1e-10))
    ret_1[0] = 0
    vol_20 = _rolling_std(ret_1, 20)

    donch_high = pd.Series(high).rolling(20).max().values
    donch_low = pd.Series(low).rolling(20).min().values

    if taker_buy is not None:
        taker = np.where(volume > 0, taker_buy / np.maximum(volume, 1e-10), 0.5)
    else:
        taker = np.full(n, 0.5)

    return {
        'close': close, 'high': high, 'low': low, 'volume': volume,
        'ema_10': ema_10, 'ema_20': ema_20, 'ema_50': ema_50,
        'macd': macd, 'macd_signal': macd_signal, 'macd_hist': macd_hist,
        'rsi': rsi, 'bb_upper': bb_upper, 'bb_lower': bb_lower,
        'bb_width': bb_width, 'bb_pct': bb_pct,
        'atr': atr, 'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'vol_ratio': vol_ratio, 'ret_1': ret_1, 'vol_20': vol_20,
        'donch_high': donch_high, 'donch_low': donch_low,
        'taker': taker,
    }


def _align_higher_to_lower(higher_idx, higher_vals, lower_idx):
    """Forward-fill higher timeframe values to lower timeframe index."""
    s = pd.Series(higher_vals, index=higher_idx)
    return s.reindex(lower_idx, method='ffill').values.copy()


# =============================================================================
# Daily Regime Detection
# =============================================================================

def detect_daily_regime(ind_d):
    """Vectorized regime detection on daily indicators.

    Uses expanding (causal) percentiles for volatility thresholds to avoid
    look-ahead bias. Each bar's regime is determined using only data up to
    that point. A minimum of 60 daily bars is required for stable estimates.
    """
    n = len(ind_d['adx'])
    adx = ind_d['adx']
    ema_20 = ind_d['ema_20']
    ema_50 = ind_d['ema_50']
    vol_20 = ind_d['vol_20']

    # Expanding (causal) percentiles: at bar i, use only vol_20[:i+1]
    vol_series = pd.Series(vol_20)
    min_periods = 60  # need ~2 months of daily data for stable quantiles
    vol_p75 = vol_series.expanding(min_periods=min_periods).quantile(0.75).values
    vol_p25 = vol_series.expanding(min_periods=min_periods).quantile(0.25).values

    regimes = np.full(n, 3, dtype=np.int8)  # default: RANGE

    valid = ~np.isnan(adx) & ~np.isnan(vol_20) & ~np.isnan(vol_p75)
    crisis = valid & (vol_20 > vol_p75 * 2)
    quiet = valid & ~crisis & (vol_20 < vol_p25 * 0.7)
    strong = valid & ~crisis & ~quiet & (adx > 25)
    uptrend = strong & (ema_20 > ema_50)
    downtrend = strong & ~uptrend

    regimes[crisis] = 0
    regimes[quiet] = 1
    regimes[uptrend] = 2
    regimes[downtrend] = 4

    regimes[:20] = 3
    return regimes


# =============================================================================
# Numba-JIT Squeeze Bar Counter
# =============================================================================

@njit(cache=False)
def _count_squeeze_bars_jit(in_squeeze):
    """Numba-JIT squeeze bar counter."""
    n = len(in_squeeze)
    out = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if in_squeeze[i]:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 0
    return out


# =============================================================================
# Strategy Protocol
# =============================================================================

@dataclass
class StrategyContext:
    """Everything a strategy needs to make decisions. Read-only.

    Note: `adv` and `tier` were removed to prevent look-ahead bias.
    Use `rolling_adv` (per-bar array, point-in-time) for any ADV-dependent logic.
    """
    ticker: str

    ind_1h: Dict[str, np.ndarray]
    ind_4h: Dict[str, np.ndarray]
    ind_d: Dict[str, np.ndarray]

    idx_1h: pd.DatetimeIndex
    idx_4h: pd.DatetimeIndex
    idx_d: pd.DatetimeIndex

    regime_1h: np.ndarray

    df_1h: pd.DataFrame
    df_4h: pd.DataFrame
    df_daily: pd.DataFrame

    enriched: Optional[pd.DataFrame] = None
    custom: Dict[str, np.ndarray] = field(default_factory=dict)

    # Point-in-time liquidity data
    liquidity_mask: Optional[np.ndarray] = None  # bool mask (True = liquid enough to trade)
    rolling_adv: Optional[np.ndarray] = None     # per-bar ADV for sizing/slippage

    # Futures support
    funding_1h: Optional[np.ndarray] = None   # per-hour funding rate aligned to 1h bars
    funding_raw: Optional[np.ndarray] = None   # raw settlement-interval rate (for signal use)
    market_type: str = 'spot'                  # 'spot' or 'perp' — for strategy introspection

    def align_daily_to_1h(self, daily_values):
        return _align_higher_to_lower(self.idx_d, daily_values, self.idx_1h)

    def align_4h_to_1h(self, h4_values):
        return _align_higher_to_lower(self.idx_4h, h4_values, self.idx_1h)


@dataclass
class StrategyResult:
    """What a strategy returns: entry signals + trade parameters."""
    entry_mask: np.ndarray
    direction: np.ndarray

    stop_mult: object = 3.0      # float scalar or per-bar np.ndarray
    trail_mult: object = 1.5     # float scalar or per-bar np.ndarray (exit ablation: 1.5 ATR flat)
    target_mult: float = 999.0
    no_stop_bars: int = 0
    min_hold: int = 6
    max_hold: int = 720
    edge: float = 0.35

    exit_regimes: set = field(default_factory=lambda: {CRISIS})
    rsi_exit_level: float = 999.0
    convex_exit: bool = False
    mean_target_vals: Optional[np.ndarray] = None

    name: str = 'unnamed'
    max_trade_pct: float = 0.0  # max position as % of equity (0 = use ADV-based cap only)
    size_multiplier: object = 1.0  # float scalar or per-bar np.ndarray — strategy-configured sizing overlay
    cap_multiplier: float = 1.0  # scales ADV-based cap_pct (default 2-12% of equity) — use >1.0 for aggressive strategies

    # Progressive trailing stop schedule (None = use fixed trail_mult)
    # Shape (N, 2): [[profit_atr_threshold, trail_mult], ...] sorted by threshold ascending
    trail_schedule: Optional[np.ndarray] = None

    # Time-based trail tightening — same shape as trail_schedule but keyed on bars_held
    time_trail_schedule: Optional[np.ndarray] = None

    # Per-bar ceiling on trail multiplier (None = no ceiling)
    max_trail_mult: Optional[np.ndarray] = None

    # Funding-aware exit: force close if cumulative funding / margin_usd exceeds threshold
    funding_exit_threshold: float = 0.0

    # Partial profit-taking
    partial_tp_atr: float = 0.0
    partial_tp_pct: float = 0.5
    partial_tp_trail: float = 1.5

    # Breakeven ratchet
    breakeven_atr: float = 0.5

    # Chandelier stop: trail from highest-high (or lowest-low for shorts) over N-bar lookback
    # 0 = disabled (use standard trail from pos.highest). Typical values: 10-24 bars.
    chandelier_lookback: int = 0

    # Regime-conditional target: tighter TP in DOWNTREND regime.
    bear_target_mult: float = 0.0
    # Regime-conditional max hold: shorter hold in DOWNTREND (0 = use max_hold)
    bear_max_hold: int = 0

    # Conviction score: per-bar signal strength in [0, 1] for entry prioritization.
    conviction_score: Optional[np.ndarray] = None

    # Futures support (defaults preserve backward compatibility)
    market_type: int = 0        # MarketType.SPOT
    leverage: object = 1.0      # float scalar or per-bar np.ndarray
    exchange: str = 'binance'   # for fee/funding lookup

    # Combined strategy fields (secondary leg)
    secondary_entry_mask: Optional[np.ndarray] = None
    secondary_direction: Optional[np.ndarray] = None
    secondary_market_type: int = 1     # defaults to PERP
    secondary_leverage: float = 1.0
    capital_split: float = 0.5         # fraction of capital to primary leg
    # Secondary leg trade management (defaults to primary leg values via None sentinel)
    secondary_stop_mult: Optional[float] = None
    secondary_trail_mult: Optional[float] = None
    secondary_target_mult: Optional[float] = None
    secondary_no_stop_bars: Optional[int] = None
    secondary_min_hold: Optional[int] = None
    secondary_max_hold: Optional[int] = None
    secondary_edge: Optional[float] = None
    secondary_rsi_exit_level: Optional[float] = None
    secondary_convex_exit: Optional[bool] = None


StrategyFn = Callable[[StrategyContext], StrategyResult]
CombinedStrategyFn = Callable[[StrategyContext, StrategyContext], StrategyResult]


# =============================================================================
# Custom Indicator Plugin System
# =============================================================================

_INDICATOR_PLUGINS: List[Callable] = []


def register_indicator(fn):
    """Decorator: register a custom indicator computation function."""
    _INDICATOR_PLUGINS.append(fn)
    return fn


@register_indicator
def _compute_obv(ctx: StrategyContext):
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    # Vectorized OBV: sign of price change * volume, then cumsum
    sign = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(sign * volume)
    ctx.custom['obv'] = obv
    obv_slope = np.zeros(len(close))
    obv_slope[10:] = obv[10:] - obv[:-10]
    ctx.custom['obv_slope'] = obv_slope


@register_indicator
def _compute_vwap_session(ctx: StrategyContext):
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    tp = close
    cum_tpv = _rolling_mean(tp * volume, 20) * 20
    cum_vol = _rolling_mean(volume, 20) * 20
    vwap = cum_tpv / np.maximum(cum_vol, 1e-10)
    ctx.custom['vwap_20'] = vwap
    ctx.custom['vwap_dev'] = (close - vwap) / np.maximum(vwap, 1e-10)


@register_indicator
def _compute_momentum_signals(ctx: StrategyContext):
    close = ctx.ind_1h['close']
    for period in [6, 12, 24, 48, 120]:
        ret = np.zeros(len(close))
        ret[period:] = (close[period:] - close[:-period]) / np.maximum(close[:-period], 1e-10)
        ctx.custom[f'ret_{period}h'] = ret
    close_d = ctx.ind_d['close']
    for period in [5, 10, 20, 60]:
        ret_d = np.zeros(len(close_d))
        ret_d[period:] = (close_d[period:] - close_d[:-period]) / np.maximum(close_d[:-period], 1e-10)
        ctx.custom[f'ret_{period}d'] = ctx.align_daily_to_1h(ret_d)


@register_indicator
def _compute_enriched_signals(ctx: StrategyContext):
    if ctx.enriched is None:
        return
    for col in ['vpin', 'realized_vol', 'taker_buy_ratio', 'amihud_1m',
                'vwap_deviation', 'intraday_skew', 'parkinson_vol']:
        if col in ctx.enriched.columns:
            vals = ctx.enriched[col].values
            mapped = _align_higher_to_lower(ctx.enriched.index, vals, ctx.idx_1h)
            ctx.custom[f'enr_{col}'] = mapped


# =============================================================================
# Vectorized Rolling Helpers (for strategy authors — avoid Python loops)
# =============================================================================

def rolling_mean(arr, window):
    """Vectorized rolling mean. Use instead of for-loop + np.mean."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=1).mean().values

def rolling_std(arr, window):
    """Vectorized rolling std. Use instead of for-loop + np.std."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=1).std().values

def rolling_median(arr, window):
    """Vectorized rolling median. Use instead of for-loop + np.median."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=1).median().values

def rolling_max(arr, window):
    """Vectorized rolling max. Use instead of for-loop + np.max."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=1).max().values

def rolling_min(arr, window):
    """Vectorized rolling min. Use instead of for-loop + np.min."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=1).min().values

def rolling_zscore(arr, window):
    """Vectorized rolling z-score. Returns (arr - rolling_mean) / rolling_std."""
    s = pd.Series(arr)
    mu = s.rolling(window, min_periods=max(10, window // 4)).mean()
    sigma = s.rolling(window, min_periods=max(10, window // 4)).std()
    z = ((s - mu) / sigma.clip(lower=1e-10)).clip(-3, 3).fillna(0)
    return z.values

def rolling_skew(arr, window):
    """Vectorized rolling skewness."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=max(5, window // 4)).skew().fillna(0).values

def rolling_corr(arr1, arr2, window):
    """Vectorized rolling correlation."""
    s1, s2 = pd.Series(arr1), pd.Series(arr2)
    return s1.rolling(window, min_periods=max(10, window // 4)).corr(s2).fillna(0).values


# =============================================================================
# Strategy Loader
# =============================================================================

def _load_strategy_fn(strategy_id: str):
    """Load a strategy function by ID from the strategies/ directory.

    Injects v4/ into sys.path so strategy `from engine import ...` resolves
    to this module (v4/engine.py).
    """
    strategies_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "strategies",
    )
    for fname in os.listdir(strategies_dir):
        if fname.startswith(strategy_id + "_") and fname.endswith(".py"):
            fpath = os.path.join(strategies_dir, fname)
            break
    else:
        raise FileNotFoundError(
            f"No strategy file for '{strategy_id}' in {strategies_dir}"
        )

    # Ensure v4/ is on sys.path so `from engine import ...` resolves to v4/engine.py
    v4_dir = os.path.dirname(os.path.abspath(__file__))
    if v4_dir not in sys.path:
        sys.path.insert(0, v4_dir)

    spec = importlib.util.spec_from_file_location(
        f"strategy_{strategy_id}", fpath,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.strategy


# =============================================================================
# Engine
# =============================================================================

class Engine:
    """Main backtesting engine — context building and data loading."""

    def __init__(self, data_dir='data', market='spot', capital=200_000,
                 fee_rate=None, slippage_bps=None, exchange='binance'):
        self.data_dir = data_dir
        self.market = market  # 'perp' or 'spot'
        self.capital = capital
        self.exchange = exchange
        self._fee_override = fee_rate
        self._slip_override = slippage_bps
        self._enriched = None
        self._enriched_loaded = False
        self._context_cache = {}
        self._warned_mismatches = set()

    def _cache_dir(self, timeframe='1h'):
        """Resolve cache directory: data/{market}/{timeframe}_cache/"""
        return os.path.join(self.data_dir, self.market, f'{timeframe}_cache')

    def _load_enriched(self):
        if self._enriched_loaded:
            return self._enriched
        path = os.path.join(self.data_dir, 'all_tokens_enriched.parquet')
        if os.path.exists(path):
            df = pd.read_parquet(path)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            self._enriched = df
        self._enriched_loaded = True
        return self._enriched

    def _build_context(self, ticker: str, df_1h: pd.DataFrame,
                       use_cache: bool = False,
                       market_override: Optional[str] = None,
                       min_bars: int = 500) -> Optional[StrategyContext]:
        """Build a StrategyContext for one token.

        Args:
            use_cache: If True, cache and reuse contexts for the same (ticker, len) pair.
                       Useful when running multiple strategies on the same token data.
            market_override: If set, use this market type instead of self.market.
                             Avoids mutating self.market (thread-safe).
            min_bars: Minimum 1h bars required (default 500 for production,
                      lower for signal-identity testing with shorter series).
        """
        if df_1h is None or len(df_1h) < min_bars:
            return None

        cache_key = (ticker, len(df_1h))
        if use_cache and cache_key in self._context_cache:
            return self._context_cache[cache_key]

        df_4h = aggregate_to_timeframe(df_1h, hours=4)
        df_daily = aggregate_to_timeframe(df_1h, hours=24)

        min_4h = max(min_bars // 5, 10)
        min_d = max(min_bars // 17, 5)
        if len(df_4h) < min_4h or len(df_daily) < min_d:
            return None

        def _arrays(df):
            return (df['close'].values.astype(np.float64),
                    df['high'].values.astype(np.float64),
                    df['low'].values.astype(np.float64),
                    df['volume'].values.astype(np.float64),
                    df['taker_buy_base'].values.astype(np.float64)
                    if 'taker_buy_base' in df.columns else None)

        c1, h1, l1, v1, t1 = _arrays(df_1h)
        c4, h4, l4, v4, t4 = _arrays(df_4h)
        cd, hd, ld, vd, _ = _arrays(df_daily)

        ind_1h = compute_indicators_fast(c1, h1, l1, v1, t1)
        ind_4h = compute_indicators_fast(c4, h4, l4, v4, t4)
        ind_d = compute_indicators_fast(cd, hd, ld, vd)

        idx_1h = df_1h.index
        idx_4h = df_4h.index
        idx_d = df_daily.index

        regimes_d = detect_daily_regime(ind_d)
        regime_1h = _align_higher_to_lower(idx_d, regimes_d.astype(float), idx_1h).astype(np.int8)
        regime_1h = np.nan_to_num(regime_1h, nan=RANGE).astype(np.int8)

        enriched = self._load_enriched()
        token_enriched = None
        if enriched is not None:
            mask = enriched['ticker'] == ticker
            if mask.sum() > 0:
                token_enriched = enriched.loc[mask].copy()
                token_enriched = token_enriched[token_enriched.index >= '2024-01-01']
                if len(token_enriched) < 10:
                    token_enriched = None

        # Compute static ADV for reporting only (NOT exposed to strategies)
        _adv_static = compute_adv(c1, v1, lookback_days=30, hours_per_bar=1)
        _tier_static = adv_to_tier(_adv_static)

        # Compute point-in-time liquidity mask and rolling ADV
        liq_mask = compute_liquidity_mask(c1, v1, hours_per_bar=1)
        rolling_adv_arr = compute_rolling_adv(c1, v1, lookback_days=30, hours_per_bar=1)
        # Fill NaN bars (before lookback period) with conservative fallback (no look-ahead)
        rolling_adv_arr = np.where(np.isnan(rolling_adv_arr), FALLBACK_ADV, rolling_adv_arr)

        # Funding data (perp parquets with funding_1h column)
        effective_market = market_override if market_override is not None else self.market
        funding_1h_arr = None
        funding_raw_arr = None
        if effective_market == 'perp' and 'funding_1h' in df_1h.columns:
            funding_1h_arr = df_1h['funding_1h'].values.astype(np.float64)
            funding_1h_arr = np.nan_to_num(funding_1h_arr, nan=0.0)
            if 'funding_rate' in df_1h.columns:
                funding_raw_arr = df_1h['funding_rate'].values.astype(np.float64)
                funding_raw_arr = np.nan_to_num(funding_raw_arr, nan=0.0)

        ctx = StrategyContext(
            ticker=ticker,
            ind_1h=ind_1h, ind_4h=ind_4h, ind_d=ind_d,
            idx_1h=idx_1h, idx_4h=idx_4h, idx_d=idx_d,
            regime_1h=regime_1h,
            df_1h=df_1h, df_4h=df_4h, df_daily=df_daily,
            enriched=token_enriched,
            liquidity_mask=liq_mask,
            rolling_adv=rolling_adv_arr,
            funding_1h=funding_1h_arr,
            funding_raw=funding_raw_arr,
            market_type=effective_market,
        )

        # Store static ADV/tier for engine reporting (NOT for strategy use)
        ctx._adv_static = _adv_static
        ctx._tier_static = _tier_static

        for plugin in _INDICATOR_PLUGINS:
            try:
                plugin(ctx)
            except Exception:
                pass

        if use_cache:
            self._context_cache[cache_key] = ctx
        return ctx


# Alias for backward compatibility
BacktestEngine = Engine
