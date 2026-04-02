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
import logging
import threading
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

def compute_core(close, high, low, volume, taker_buy=None):
    """Core indicators always computed: OHLCV, ATR, returns, volatility, taker."""
    n = len(close)

    tr = np.zeros(n)
    tr[1:] = np.maximum(high[1:] - low[1:],
                         np.maximum(np.abs(high[1:] - close[:-1]),
                                    np.abs(low[1:] - close[:-1])))
    atr = _ema(tr, 14)

    ret_1 = np.log(close / np.maximum(np.roll(close, 1), 1e-10))
    ret_1[0] = 0
    vol_20 = _rolling_std(ret_1, 20)

    if taker_buy is not None:
        taker = np.where(volume > 0, taker_buy / np.maximum(volume, 1e-10), 0.5)
    else:
        taker = np.full(n, 0.5)

    return {
        'close': close, 'high': high, 'low': low, 'volume': volume,
        'atr': atr, 'ret_1': ret_1, 'vol_20': vol_20, 'taker': taker,
        '_tr': tr,  # kept for ADX computation
    }


def compute_ema_indicators(close):
    """EMA indicators: EMA-10, EMA-20, EMA-50."""
    return {
        'ema_10': _ema(close, 10),
        'ema_20': _ema(close, 20),
        'ema_50': _ema(close, 50),
    }


def compute_macd(close):
    """MACD: line, signal, histogram."""
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    macd_signal = _ema(macd, 9)
    macd_hist = macd - macd_signal
    return {'macd': macd, 'macd_signal': macd_signal, 'macd_hist': macd_hist}


def compute_rsi(close):
    """RSI: 14-period."""
    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema(gains, 14)
    avg_loss = _ema(losses, 14)
    rs = avg_gain / np.maximum(avg_loss, 1e-10)
    rsi = 100 - 100 / (1 + rs)
    return {'rsi': rsi}


def compute_bb(close):
    """Bollinger Bands: upper, lower, width, pct."""
    sma20 = _rolling_mean(close, 20)
    bb_std = _rolling_std(close, 20)
    bb_upper = sma20 + 2 * bb_std
    bb_lower = sma20 - 2 * bb_std
    bb_width = 4 * bb_std / np.maximum(sma20, 1e-10)
    bb_pct = (close - np.nan_to_num(bb_lower, 0)) / np.maximum(4 * np.nan_to_num(bb_std, 1), 1e-10)
    return {'bb_upper': bb_upper, 'bb_lower': bb_lower, 'bb_width': bb_width, 'bb_pct': bb_pct}


def compute_adx_indicators(high, low, tr):
    """ADX: directional movement strength (requires pre-computed true range)."""
    n = len(high)
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
    return {'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di}


def compute_volume_indicators(volume):
    """Volume indicators: vol_ratio (vs SMA20)."""
    vol_sma = _rolling_mean(volume, 20)
    vol_ratio = volume / np.maximum(np.nan_to_num(vol_sma, 1), 1e-10)
    return {'vol_ratio': vol_ratio}


def compute_donchian(high, low):
    """Donchian channels: 20-bar high/low."""
    donch_high = pd.Series(high).rolling(20).max().values
    donch_low = pd.Series(low).rolling(20).min().values
    return {'donch_high': donch_high, 'donch_low': donch_low}


# Mapping of indicator group names to their compute functions.
# Used by compute_indicators_selective() for opt-in computation.
_INDICATOR_GROUPS = {
    'ema': lambda c, h, l, v, tr: compute_ema_indicators(c),
    'macd': lambda c, h, l, v, tr: compute_macd(c),
    'rsi': lambda c, h, l, v, tr: compute_rsi(c),
    'bb': lambda c, h, l, v, tr: compute_bb(c),
    'adx': lambda c, h, l, v, tr: compute_adx_indicators(h, l, tr),
    'volume': lambda c, h, l, v, tr: compute_volume_indicators(v),
    'donchian': lambda c, h, l, v, tr: compute_donchian(h, l),
}


def compute_indicators_selective(close, high, low, volume, taker_buy=None,
                                 groups=None):
    """Compute indicators selectively by group name.

    Args:
        groups: Set of group names to compute (e.g. {'ema', 'rsi', 'adx'}).
                If None, compute all groups (backward compatible).

    Returns:
        dict of indicator name -> numpy array. Core indicators always included.
    """
    result = compute_core(close, high, low, volume, taker_buy)
    tr = result.pop('_tr')  # internal, not exposed

    if groups is None:
        groups = set(_INDICATOR_GROUPS.keys())

    for name in groups:
        fn = _INDICATOR_GROUPS.get(name)
        if fn is not None:
            result.update(fn(close, high, low, volume, tr))

    return result


def compute_indicators_fast(close, high, low, volume, taker_buy=None, quote_vol=None):
    """Compute all indicators as numpy arrays. No pandas DataFrames.

    Backward-compatible wrapper: computes ALL indicator groups.
    New strategies can use compute_indicators_selective() with specific groups.
    """
    return compute_indicators_selective(close, high, low, volume, taker_buy, groups=None)


def _align_higher_to_lower(higher_idx, higher_vals, lower_idx):
    """Forward-fill higher timeframe values to lower timeframe index."""
    s = pd.Series(higher_vals, index=higher_idx)
    return s.reindex(lower_idx, method='ffill').values.copy()


# =============================================================================
# Daily Regime Detection
# =============================================================================

def detect_daily_regime(ind_d, adx_threshold=25, crisis_mult=2.0,
                        quiet_mult=0.7, ema_pair=(20, 50), min_periods=60):
    """Vectorized regime detection on daily indicators.

    Uses expanding (causal) percentiles for volatility thresholds to avoid
    look-ahead bias. Each bar's regime is determined using only data up to
    that point.

    Args:
        ind_d: dict with 'adx', 'ema_20', 'ema_50', 'vol_20', 'close' keys.
        adx_threshold: ADX value above which trend is considered strong.
        crisis_mult: vol_p75 multiplier for crisis detection.
        quiet_mult: vol_p25 multiplier for quiet detection.
        ema_pair: (fast, slow) EMA periods for trend direction.
        min_periods: minimum daily bars for stable quantile estimates.
    """
    n = len(ind_d['adx'])
    adx = ind_d['adx']
    vol_20 = ind_d['vol_20']

    # EMA pair: use precomputed if default, otherwise compute from close
    if ema_pair == (20, 50):
        ema_fast = ind_d['ema_20']
        ema_slow = ind_d['ema_50']
    else:
        ema_fast = _ema(ind_d['close'], ema_pair[0])
        ema_slow = _ema(ind_d['close'], ema_pair[1])

    # Expanding (causal) percentiles: at bar i, use only vol_20[:i+1]
    vol_series = pd.Series(vol_20)
    vol_p75 = vol_series.expanding(min_periods=min_periods).quantile(0.75).values
    vol_p25 = vol_series.expanding(min_periods=min_periods).quantile(0.25).values

    regimes = np.full(n, 3, dtype=np.int8)  # default: RANGE

    valid = ~np.isnan(adx) & ~np.isnan(vol_20) & ~np.isnan(vol_p75)
    crisis = valid & (vol_20 > vol_p75 * crisis_mult)
    quiet = valid & ~crisis & (vol_20 < vol_p25 * quiet_mult)
    strong = valid & ~crisis & ~quiet & (adx > adx_threshold)
    uptrend = strong & (ema_fast > ema_slow)
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
    cap_multiplier: object = 1.0  # float scalar or per-bar np.ndarray — scales ADV-based cap_pct

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

    # Breakeven ratchet (0 = disabled; strategies must explicitly opt in)
    breakeven_atr: float = 0.0

    # Chandelier stop: trail from highest-high (or lowest-low for shorts) over N-bar lookback
    # 0 = disabled (use standard trail from pos.highest). Typical values: 10-24 bars.
    chandelier_lookback: int = 0

    # SMA trailing stop: per-bar precomputed SMA values.
    # Exit when close crosses below SMA (longs) or above SMA (shorts).
    # Strategy computes SMA in its function and passes the array here.
    # None = disabled (use standard ATR-based trail).
    sma_trail_vals: Optional[np.ndarray] = None

    # Limit entry price: per-bar limit price for entries.
    # When set, simulator checks if the bar's low (long) or high (short)
    # would fill the limit. If fills: entry at limit_price. If not: entry at close.
    # None = disabled (always enter at close).
    entry_limit_price: Optional[np.ndarray] = None

    # Armed entry levels: per-bar watch price for real-time cross detection.
    # Set on PRE-cross bars (qualifying tokens that haven't crossed yet).
    # Paper engine monitors via 1m WebSocket and enters at market on cross.
    armed_levels: Optional[np.ndarray] = None     # float64, NaN = not armed
    armed_direction: Optional[np.ndarray] = None  # int8, 0 = not armed, 1 = long, -1 = short

    # Regime-conditional target: tighter TP in DOWNTREND regime.
    bear_target_mult: float = 0.0
    # Regime-conditional max hold: shorter hold in DOWNTREND (0 = use max_hold)
    bear_max_hold: int = 0

    # Configurable exit constants (extracted from hardcoded values)
    regime_exit_min_bars: int = 6                   # min bars before regime exit triggers
    convex_bar_thresholds: tuple = (48, 12)         # (mature_bars, early_bars)
    convex_multipliers: tuple = (2.0, 1.5, 0.3)    # (mature_trail_atr, early_profit_mult, early_be_offset)

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
_NAMED_PLUGINS: Dict[str, Callable] = {}
# Reverse map: fn.__name__ -> plugin name, for dependency-correct iteration order.
# Uses __name__ (not id(fn)) to survive importlib.reload scenarios.
_PLUGIN_NAMES: Dict[str, str] = {}


def register_indicator(fn=None, *, name=None):
    """Decorator: register a custom indicator computation function.

    Usage:
        @register_indicator           # unnamed (backward compat)
        def _compute_foo(ctx): ...

        @register_indicator(name='obv')  # named (opt-in via REQUIRED_PLUGINS)
        def _compute_obv(ctx): ...

    Named plugins can be selectively executed when a strategy declares
    REQUIRED_PLUGINS = ['obv', 'vwap']. Unnamed plugins ONLY execute
    when _required_plugins is None (all-plugins mode, the default).
    In opt-in mode (REQUIRED_PLUGINS is a list), unnamed plugins are skipped.

    Plugin dependencies (must include in REQUIRED_PLUGINS if using selective opt-in):
      - obv_divergence requires obv
      - momentum_accel requires momentum
    """
    def decorator(f):
        _INDICATOR_PLUGINS.append(f)
        if name is not None:
            _NAMED_PLUGINS[name] = f
            _PLUGIN_NAMES[f.__name__] = name
        return f

    if fn is not None:
        # Called without arguments: @register_indicator
        _INDICATOR_PLUGINS.append(fn)
        return fn
    # Called with arguments: @register_indicator(name='obv')
    return decorator


@register_indicator(name='obv')
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


@register_indicator(name='vwap')
def _compute_vwap_session(ctx: StrategyContext):
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    tp = close
    cum_tpv = _rolling_mean(tp * volume, 20) * 20
    cum_vol = _rolling_mean(volume, 20) * 20
    vwap = cum_tpv / np.maximum(cum_vol, 1e-10)
    ctx.custom['vwap_20'] = vwap
    ctx.custom['vwap_dev'] = (close - vwap) / np.maximum(vwap, 1e-10)


@register_indicator(name='momentum')
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


@register_indicator(name='enriched')
def _compute_enriched_signals(ctx: StrategyContext):
    if ctx.enriched is None:
        return
    for col in ['vpin', 'realized_vol', 'taker_buy_ratio', 'amihud_1m',
                'vwap_deviation', 'intraday_skew', 'parkinson_vol']:
        if col in ctx.enriched.columns:
            vals = ctx.enriched[col].values
            mapped = _align_higher_to_lower(ctx.enriched.index, vals, ctx.idx_1h)
            ctx.custom[f'enr_{col}'] = mapped


@register_indicator(name='obv_divergence')
def _compute_obv_divergence(ctx: StrategyContext):
    """OBV slope vs price slope divergence — detects accumulation/distribution."""
    close = ctx.ind_1h['close']
    obv = ctx.custom.get('obv')
    if obv is None:
        return
    n = len(close)
    lookback = 20
    # Price slope: normalized change over lookback
    price_slope = np.zeros(n)
    price_slope[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    # OBV slope: normalized change over lookback
    obv_slope_norm = np.zeros(n)
    obv_range = np.abs(obv[lookback:] - obv[:-lookback])
    obv_mean = rolling_mean(np.abs(obv), lookback * 2)
    obv_slope_norm[lookback:] = (obv[lookback:] - obv[:-lookback]) / np.maximum(obv_mean[lookback:], 1e-10)
    # Divergence: OBV rising while price flat/falling = accumulation (positive)
    # OBV falling while price flat/rising = distribution (negative)
    ctx.custom['obv_divergence'] = obv_slope_norm - price_slope
    ctx.custom['price_slope'] = price_slope


@register_indicator(name='momentum_accel')
def _compute_momentum_accel(ctx: StrategyContext):
    """Second derivative of price — momentum acceleration."""
    ret_6h = ctx.custom.get('ret_6h')
    if ret_6h is None:
        return
    n = len(ret_6h)
    accel = np.zeros(n)
    accel[6:] = ret_6h[6:] - ret_6h[:-6]
    ctx.custom['momentum_accel'] = accel


@register_indicator(name='funding_zscore')
def _compute_funding_zscore(ctx: StrategyContext):
    """Rolling z-score of funding rate — extreme positioning detection."""
    if ctx.funding_raw is None:
        ctx.custom['funding_zscore'] = np.zeros(len(ctx.ind_1h['close']))
        return
    ctx.custom['funding_zscore'] = rolling_zscore(ctx.funding_raw, 168)  # 7-day rolling window


@register_indicator(name='squeeze')
def _compute_squeeze_intensity(ctx: StrategyContext):
    """How deep into squeeze: 1 - (bb_width / bb_avg). 0=normal, 1=max compression."""
    bb_width = ctx.ind_1h['bb_width']
    bb_avg = rolling_mean(bb_width, 240)  # 10-day rolling average
    ratio = bb_width / np.maximum(bb_avg, 1e-10)
    intensity = np.clip(1.0 - ratio, 0.0, 1.0)
    ctx.custom['squeeze_intensity'] = intensity


@register_indicator(name='multi_tf')
def _compute_multi_tf_alignment(ctx: StrategyContext):
    """Score combining 1H/4H/daily trend agreement (0-3)."""
    n = len(ctx.ind_1h['close'])

    # 1H trend: MACD > 0
    score_1h = (ctx.ind_1h['macd'] > 0).astype(np.float64)

    # 4H trend: MACD > 0 (aligned to 1H)
    macd_4h = ctx.align_4h_to_1h(ctx.ind_4h['macd'])
    score_4h = (macd_4h > 0).astype(np.float64)

    # Daily trend: EMA20 > EMA50 (aligned to 1H)
    ema20_d = ctx.align_daily_to_1h(ctx.ind_d['ema_20'])
    ema50_d = ctx.align_daily_to_1h(ctx.ind_d['ema_50'])
    score_d = (ema20_d > ema50_d).astype(np.float64)

    ctx.custom['multi_tf_alignment'] = score_1h + score_4h + score_d


# =============================================================================
# V3 Overlay Plugins — Positioning & VRP (module-level caches)
# =============================================================================

_ENGINE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _ENGINE_DIR.parent
_POS_PATH = _PROJECT_ROOT / 'data' / 'alternative' / 'binance_metrics' / 'all_symbols_daily_ls.parquet'
_BTC_DVOL_PATH = _PROJECT_ROOT / 'data' / 'alternative' / 'deribit_options' / 'dvol' / 'btc_dvol_daily.json'
_ETH_DVOL_PATH = _PROJECT_ROOT / 'data' / 'alternative' / 'deribit_options' / 'dvol' / 'eth_dvol_daily.json'

_pos_cache: Dict[str, pd.DataFrame] = {}   # symbol -> filtered DataFrame
_dvol_cache: Dict[str, pd.Series] = {}     # 'BTC'/'ETH' -> dvol Series
_pos_raw_loaded: bool = False               # True after first parquet read
_pos_raw_df: Optional[pd.DataFrame] = None


def clear_module_caches() -> None:
    """Clear module-level caches to prevent unbounded memory growth in live runner.

    Called after each tick in the paper trading loop.
    """
    global _pos_raw_loaded, _pos_raw_df
    _pos_cache.clear()
    _dvol_cache.clear()
    _pos_raw_loaded = False
    _pos_raw_df = None


def _load_positioning_raw() -> pd.DataFrame:
    """Load raw positioning parquet once, cache at module level."""
    global _pos_raw_loaded, _pos_raw_df
    if _pos_raw_loaded:
        return _pos_raw_df
    _pos_raw_loaded = True
    if not _POS_PATH.exists():
        _pos_raw_df = pd.DataFrame()
        return _pos_raw_df
    _pos_raw_df = pd.read_parquet(_POS_PATH)
    return _pos_raw_df


def _get_positioning_for_symbol(symbol: str) -> pd.DataFrame:
    """Get positioning data for a single symbol (e.g. 'BTCUSDT'). Cached."""
    if symbol in _pos_cache:
        return _pos_cache[symbol]
    raw = _load_positioning_raw()
    if raw.empty or 'symbol' not in raw.columns:
        _pos_cache[symbol] = pd.DataFrame()
        return _pos_cache[symbol]
    filt = raw[raw['symbol'] == symbol].copy()
    if filt.empty:
        _pos_cache[symbol] = pd.DataFrame()
        return _pos_cache[symbol]
    filt['date'] = pd.to_datetime(filt['date'])
    filt = filt.set_index('date').sort_index()
    filt = filt[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    filt = filt[~filt.index.duplicated(keep='last')]
    _pos_cache[symbol] = filt
    return filt


def _load_dvol(ticker: str) -> pd.Series:
    """Load DVOL JSON for BTC or ETH. Cached at module level."""
    if ticker in _dvol_cache:
        return _dvol_cache[ticker]
    path = _BTC_DVOL_PATH if ticker == 'BTC' else _ETH_DVOL_PATH if ticker == 'ETH' else None
    if path is None or not path.exists():
        _dvol_cache[ticker] = pd.Series(dtype=float)
        return _dvol_cache[ticker]
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol_close': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    _dvol_cache[ticker] = dvol['dvol_close']
    return _dvol_cache[ticker]


@register_indicator(name='positioning')
def _compute_positioning_overlay(ctx: StrategyContext):
    """Positioning overlay: combined z-score of Top Trader L/S + divergence.

    Writes ctx.custom['pos_z'] and ctx.custom['pos_mult'].
    Falls back to 1.0 on any error or missing data.
    """
    n = len(ctx.ind_1h['close'])
    try:
        symbol = f'{ctx.ticker}USDT'
        pos = _get_positioning_for_symbol(symbol)
        if pos.empty:
            ctx.custom['pos_z'] = np.zeros(n, dtype=np.float64)
            ctx.custom['pos_mult'] = np.ones(n, dtype=np.float64)
            return

        # Align positioning to daily index
        pos_aligned = pos.reindex(ctx.idx_d).ffill()

        toptrader_ls = pos_aligned['sum_toptrader_ls_ratio'].values.astype(np.float64)
        count_toptrader = pos_aligned['count_toptrader_ls_ratio'].values.astype(np.float64)
        count_ls = pos_aligned['count_ls_ratio'].values.astype(np.float64)

        # Divergence: top trader vs retail
        divergence = count_toptrader - count_ls

        # Rolling z-scores (30d window)
        z_toptrader = rolling_zscore(toptrader_ls, 30)
        z_divergence = rolling_zscore(divergence, 30)

        # Combined z-score
        combined_z = (z_toptrader + z_divergence) / 2.0

        # Map z-score to multiplier (contrarian: crowded long -> reduce)
        multiplier = np.where(
            combined_z > 1.5, 0.3,
            np.where(combined_z > 0.5, 0.5,
                     np.where(combined_z > -0.5, 1.0,
                              np.where(combined_z > -1.5, 1.3,
                                       1.5))))
        multiplier = np.where(np.isnan(combined_z), 1.0, multiplier)

        # Align daily -> 1H
        ctx.custom['pos_z'] = ctx.align_daily_to_1h(combined_z).astype(np.float64)
        ctx.custom['pos_mult'] = ctx.align_daily_to_1h(multiplier).astype(np.float64)

    except Exception:
        ctx.custom['pos_z'] = np.zeros(n, dtype=np.float64)
        ctx.custom['pos_mult'] = np.ones(n, dtype=np.float64)


@register_indicator(name='vrp')
def _compute_vrp_overlay(ctx: StrategyContext):
    """VRP (Volatility Risk Premium) overlay: (IV - RV) z-score -> sizing mult.

    Writes ctx.custom['vrp_z'] and ctx.custom['vrp_mult'].
    Falls back to 1.0 on any error or missing data.
    """
    n = len(ctx.ind_1h['close'])
    try:
        daily_close = ctx.ind_d['close']
        n_daily = len(daily_close)

        # Realized vol: 20d rolling std of daily log returns, annualized
        log_ret = np.zeros(n_daily, dtype=np.float64)
        log_ret[1:] = np.log(daily_close[1:] / np.maximum(daily_close[:-1], 1e-10))
        rv_20d = rolling_std(log_ret, 20) * np.sqrt(365) * 100

        # Implied vol: DVOL if available (BTC/ETH), else RV proxy
        dvol = _load_dvol(ctx.ticker)
        if dvol.empty or len(dvol) < 30:
            # Proxy: 90d RV * 1.2 (typical IV/RV ratio)
            rv_90d = rolling_std(log_ret, 90) * np.sqrt(365) * 100
            iv = rv_90d * 1.2
        else:
            iv_series = dvol.reindex(ctx.idx_d).ffill()
            iv = iv_series.values.astype(np.float64)

        # VRP = IV - RV (positive = vol overpriced)
        vrp = iv - rv_20d

        # 60d rolling z-score
        vrp_z = rolling_zscore(vrp, 60)

        # Map z-score to multiplier
        multiplier = np.where(
            vrp_z > 1.0, 1.3,
            np.where(vrp_z > -0.5, 1.0,
                     np.where(vrp_z > -1.5, 0.5,
                              0.3)))
        multiplier = np.where(np.isnan(vrp_z), 1.0, multiplier)

        # Align daily -> 1H
        ctx.custom['vrp_z'] = ctx.align_daily_to_1h(vrp_z).astype(np.float64)
        ctx.custom['vrp_mult'] = ctx.align_daily_to_1h(multiplier).astype(np.float64)

    except Exception:
        ctx.custom['vrp_z'] = np.zeros(n, dtype=np.float64)
        ctx.custom['vrp_mult'] = np.ones(n, dtype=np.float64)


# Plugin dependency ordering assertion — catches silent misconfigurations
# if decorators are ever reordered or new plugins added in wrong position.
def _assert_plugin_ordering():
    """Verify that plugin registration order satisfies dependency DAG."""
    names_in_order = [_PLUGIN_NAMES.get(fn.__name__) for fn in _INDICATOR_PLUGINS]
    names_in_order = [n for n in names_in_order if n is not None]
    # obv must come before obv_divergence; momentum must come before momentum_accel
    for dep, dependent in [('obv', 'obv_divergence'), ('momentum', 'momentum_accel')]:
        if dep in names_in_order and dependent in names_in_order:
            assert names_in_order.index(dep) < names_in_order.index(dependent), \
                f"Plugin ordering violated: {dep} must be registered before {dependent}"

_assert_plugin_ordering()


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

def ema(arr, span):
    """Exponential moving average. Reacts faster than rolling_mean to recent data."""
    s = pd.Series(arr)
    return s.ewm(span=span, min_periods=1).mean().values


# =============================================================================
# Custom Indicator Utility
# =============================================================================

def compute_custom_indicators(ctx, periods: list[dict]) -> dict[str, np.ndarray]:
    """Compute custom indicators from a strategy context.

    Args:
        ctx: StrategyContext with ind_1h, ind_4h, ind_d data.
        periods: list of dicts, each with:
            - type: 'ema', 'sma', 'rsi', 'bb', 'atr', 'donch'
            - period: int (required)
            - timeframe: '1h', '4h', 'd' (default: '1h')
            - std: float (for BB, default 2.0)
            - source: str (column name, default 'close')

    Returns:
        dict[str, np.ndarray] with keys like 'ema_14_1h', 'rsi_21_4h', etc.
    """
    result = {}
    tf_map = {'1h': ctx.ind_1h, '4h': ctx.ind_4h, 'd': ctx.ind_d}

    for spec in periods:
        ind_type = spec['type']
        period = spec['period']
        tf = spec.get('timeframe', '1h')
        source = spec.get('source', 'close')
        ind = tf_map[tf]
        arr = ind[source]
        key = f"{ind_type}_{period}_{tf}"

        if ind_type == 'ema':
            result[key] = _ema(arr, period)
        elif ind_type == 'sma':
            result[key] = _rolling_mean(arr, period)
        elif ind_type == 'rsi':
            delta = np.diff(arr, prepend=arr[0])
            gain = np.where(delta > 0, delta, 0.0)
            loss = np.where(delta < 0, -delta, 0.0)
            avg_gain = _ema(gain, period)
            avg_loss = _ema(loss, period)
            rs = avg_gain / np.maximum(avg_loss, 1e-10)
            result[key] = 100.0 - 100.0 / (1.0 + rs)
        elif ind_type == 'bb':
            std_mult = spec.get('std', 2.0)
            mid = _rolling_mean(arr, period)
            std = _rolling_std(arr, period)
            result[f"bb_mid_{period}_{tf}"] = mid
            result[f"bb_upper_{period}_{tf}"] = mid + std_mult * std
            result[f"bb_lower_{period}_{tf}"] = mid - std_mult * std
        elif ind_type == 'atr':
            high = ind['high']
            low = ind['low']
            close = ind['close']
            tr = np.maximum(high - low,
                           np.maximum(np.abs(high - np.roll(close, 1)),
                                      np.abs(low - np.roll(close, 1))))
            tr[0] = high[0] - low[0]
            result[key] = _ema(tr, period)
        elif ind_type == 'donch':
            n = len(arr)
            high = ind['high']
            low = ind['low']
            donch_high = np.full(n, np.nan)
            donch_low = np.full(n, np.nan)
            for i in range(period - 1, n):
                donch_high[i] = np.max(high[i - period + 1:i + 1])
                donch_low[i] = np.min(low[i - period + 1:i + 1])
            result[f"donch_high_{period}_{tf}"] = donch_high
            result[f"donch_low_{period}_{tf}"] = donch_low

    return result


# =============================================================================
# Strategy Loader
# =============================================================================

_STRATEGY_MODULE_CACHE: Dict[str, object] = {}
_STRATEGY_MODULE_LOCK = threading.Lock()


def _load_strategy_fn(strategy_id: str):
    """Load a strategy function by ID from the strategies/ directory.

    Injects v4/ into sys.path so strategy `from engine import ...` resolves
    to this module (v4/engine.py).

    Caches loaded modules in _STRATEGY_MODULE_CACHE (thread-safe).
    On second call for the same strategy_id, returns from cache without
    re-executing the module.
    """
    with _STRATEGY_MODULE_LOCK:
        cached = _STRATEGY_MODULE_CACHE.get(strategy_id)
        if cached is not None:
            return cached.strategy

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

    with _STRATEGY_MODULE_LOCK:
        _STRATEGY_MODULE_CACHE[strategy_id] = mod

    return mod.strategy


def _load_strategy_required_plugins(strategy_id: str):
    """Return the REQUIRED_PLUGINS list from a cached strategy module.

    Returns None if the module has no REQUIRED_PLUGINS attribute (backward
    compatible — all plugins will run).

    Plugin dependencies (callers must include both if using selective opt-in):
      - obv_divergence requires obv
      - momentum_accel requires momentum (which produces ret_6h)

    Note: strategy module cache (_STRATEGY_MODULE_CACHE) means strategy file
    changes on disk require paper trader restart to take effect.

    Logs a warning for any plugin name not found in _NAMED_PLUGINS.
    """
    with _STRATEGY_MODULE_LOCK:
        mod = _STRATEGY_MODULE_CACHE.get(strategy_id)
    if mod is None:
        return None
    plugins = getattr(mod, 'REQUIRED_PLUGINS', None)
    if plugins is not None:
        for pname in plugins:
            if pname not in _NAMED_PLUGINS:
                logging.getLogger(__name__).warning(
                    "Strategy %s: REQUIRED_PLUGINS contains unknown plugin '%s' "
                    "(available: %s)", strategy_id, pname, sorted(_NAMED_PLUGINS.keys())
                )
    return plugins


def _load_strategy_required_indicator_groups(strategy_id: str):
    """Return the REQUIRED_INDICATOR_GROUPS set from a cached strategy module.

    Returns None if the module has no REQUIRED_INDICATOR_GROUPS attribute
    (backward compatible — all 7 indicator groups will be computed).

    Available groups: ema, macd, rsi, bb, adx, volume, donchian.
    Core indicators (close, high, low, volume, atr, vol_20) are always
    computed regardless of this setting.

    Note: daily indicators always compute all groups (cheap, ~208 bars)
    so detect_daily_regime() works.  This setting only affects 1H and 4H.
    """
    with _STRATEGY_MODULE_LOCK:
        mod = _STRATEGY_MODULE_CACHE.get(strategy_id)
    if mod is None:
        return None
    groups = getattr(mod, 'REQUIRED_INDICATOR_GROUPS', None)
    if groups is not None:
        groups = set(groups)
        unknown = groups - set(_INDICATOR_GROUPS.keys())
        if unknown:
            logging.getLogger(__name__).warning(
                "Strategy %s: REQUIRED_INDICATOR_GROUPS contains unknown groups %s "
                "(available: %s)", strategy_id, sorted(unknown),
                sorted(_INDICATOR_GROUPS.keys())
            )
    return groups


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

        # Use selective indicator groups when the strategy declares
        # REQUIRED_INDICATOR_GROUPS (saves compute + memory for 1H/4H).
        # Daily always computes all groups — cheap (~208 bars) and
        # detect_daily_regime() needs adx + ema.
        _ind_groups = getattr(self, '_required_indicator_groups', None)
        if _ind_groups is not None:
            ind_1h = compute_indicators_selective(c1, h1, l1, v1, t1, groups=_ind_groups)
            ind_4h = compute_indicators_selective(c4, h4, l4, v4, t4, groups=_ind_groups)
        else:
            ind_1h = compute_indicators_fast(c1, h1, l1, v1, t1)
            ind_4h = compute_indicators_fast(c4, h4, l4, v4, t4)
        ind_d = compute_indicators_fast(cd, hd, ld, vd)

        idx_1h = df_1h.index
        idx_4h = df_4h.index
        idx_d = df_daily.index

        regimes_d = detect_daily_regime(ind_d)
        # Shift regime by 1 day to avoid look-ahead bias: day T's regime
        # uses day T's close, so it's only actionable at day T+1.
        regimes_d_shifted = np.roll(regimes_d, 1)
        regimes_d_shifted[0] = RANGE  # default for first bar
        regime_1h = _align_higher_to_lower(idx_d, regimes_d_shifted.astype(float), idx_1h).astype(np.int8)
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

        # Run indicator plugins: all by default, or only requested ones.
        # When opt-in is active, iterate _INDICATOR_PLUGINS in registration
        # order (not the caller's list) to guarantee dependency-correct
        # execution: obv before obv_divergence, momentum before momentum_accel.
        required_plugins = getattr(self, '_required_plugins', None)
        if required_plugins is not None:
            required_set = set(required_plugins)
            for fn in _INDICATOR_PLUGINS:
                pname = _PLUGIN_NAMES.get(fn.__name__)
                if pname is not None and pname in required_set:
                    try:
                        fn(ctx)
                    except Exception:
                        pass
        else:
            # Default: run all plugins (backward compat for 211 strategies)
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
