"""
V3 Backtest Engine — Consolidated Core
========================================

Consolidated backtest engine with plugin strategy architecture.
Strategies import `from engine import StrategyContext, StrategyResult`.
JIT simulation returns out_entry_bar and out_exit_bar arrays for
equity curve reconstruction and per-window stats.
"""

import sys
import os

_v3_dir = os.path.dirname(os.path.abspath(__file__))

import numpy as np
import pandas as pd
import time
import importlib.util
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Tuple
from pathlib import Path

# Explicit import from v3/universe.py using importlib
def _load_v3_mod(name):
    spec = importlib.util.spec_from_file_location(f'v3_{name}', os.path.join(_v3_dir, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_universe = _load_v3_mod('universe')
get_all_tradeable = _universe.get_all_tradeable
compute_adv = _universe.compute_adv
adv_to_tier = _universe.adv_to_tier
adv_to_sizing = _universe.adv_to_sizing
adv_to_costs = _universe.adv_to_costs
FALLBACK_ADV = _universe.FALLBACK_ADV
# Legacy — kept for backward compat of external imports
LIQUID_TOKENS = _universe.LIQUID_TOKENS

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
# Numba-JIT Simulation Core (ENHANCED: returns entry/exit bars per trade)
# =============================================================================

_EXIT_REASONS = {0: 'stop', 1: 'target_5r', 2: 'target', 3: 'regime',
                 4: 'overbought', 5: 'mean_reached', 6: 'max_hold'}


@njit(cache=True)
def _simulate_core_jit(close, high, low, atr, entry_mask, direction,
                       stop_mult, trail_mult, target_mult,
                       regime, exit_regime_mask, min_hold, max_hold,
                       rsi, rsi_exit_level, use_rsi,
                       fee_rate, adv, base_spread_bps, impact_coeff,
                       initial_capital, tier,
                       convex_exit, mean_target_vals, use_mean_target,
                       no_stop_bars, edge_override,
                       kelly_mult, cap_pct):
    """
    Numba-JIT compiled trade simulation loop.
    Slippage is position-size-aware: slip_bps = base_spread + impact * sqrt(pos_usd / adv)
    Returns: pnl[], ret_pct[], hold_hours[], exit_code[], pos_usd[],
             entry_bar[], exit_bar[], final_equity
    """
    n = len(close)
    max_trades = n // 2
    out_pnl = np.empty(max_trades, dtype=np.float64)
    out_ret = np.empty(max_trades, dtype=np.float64)
    out_hold = np.empty(max_trades, dtype=np.int64)
    out_exit = np.empty(max_trades, dtype=np.int8)
    out_pos = np.empty(max_trades, dtype=np.float64)
    out_entry_bar = np.empty(max_trades, dtype=np.int64)
    out_exit_bar = np.empty(max_trades, dtype=np.int64)
    trade_count = 0

    equity = initial_capital
    position = 0.0
    entry_price = 0.0
    entry_bar = 0
    stop_price = 0.0
    highest = 0.0
    lowest = 999999.0
    initial_risk = 0.0

    for i in range(200, n):
        # EXIT
        if position != 0.0:
            bars_held = i - entry_bar
            d = direction[entry_bar] if entry_bar < n else 1

            if d == 1:
                if high[i] > highest:
                    highest = high[i]
            else:
                if low[i] < lowest:
                    lowest = low[i]

            cur_atr = atr[i]
            if np.isnan(cur_atr):
                cur_atr = abs(entry_price) * 0.02

            if convex_exit:
                if bars_held >= 48 and d == 1:
                    trail = highest - 2.0 * cur_atr
                    if trail > stop_price:
                        stop_price = trail
                elif bars_held >= 12 and d == 1:
                    if highest > entry_price + 1.5 * initial_risk:
                        be_trail = entry_price + 0.3 * initial_risk
                        if be_trail > stop_price:
                            stop_price = be_trail
            else:
                if bars_held >= no_stop_bars:
                    if d == 1:
                        trail = highest - trail_mult * cur_atr
                        if trail > stop_price:
                            stop_price = trail
                    else:
                        trail = lowest + trail_mult * cur_atr
                        if trail < stop_price:
                            stop_price = trail

            exit_signal = False
            exit_code = -1
            exit_price = close[i]

            stop_active = bars_held >= no_stop_bars or convex_exit

            if stop_active and d == 1 and low[i] <= stop_price:
                exit_signal = True
                exit_code = 0
                exit_price = stop_price
            elif stop_active and d == -1 and high[i] >= stop_price:
                exit_signal = True
                exit_code = 0
                exit_price = stop_price
            elif convex_exit and d == 1 and close[i] > entry_price + target_mult * initial_risk:
                exit_signal = True
                exit_code = 1
            elif not convex_exit and d == 1 and high[i] >= entry_price + target_mult * cur_atr:
                exit_signal = True
                exit_code = 2
                exit_price = entry_price + target_mult * cur_atr
            elif exit_regime_mask[i] and bars_held > 6:
                exit_signal = True
                exit_code = 3
            elif use_rsi and d == 1 and rsi[i] > rsi_exit_level and bars_held >= min_hold:
                exit_signal = True
                exit_code = 4
            elif convex_exit and use_mean_target:
                mt = mean_target_vals[i]
                if not np.isnan(mt) and d == 1 and close[i] >= mt and bars_held >= min_hold:
                    if close[i] < entry_price + 2.0 * initial_risk:
                        exit_signal = True
                        exit_code = 5
            if not exit_signal and bars_held >= max_hold:
                exit_signal = True
                exit_code = 6

            if exit_signal:
                exit_pos_usd = abs(position * exit_price)
                exit_participation = exit_pos_usd / max(adv, 1.0)
                exit_slip_bps = base_spread_bps + impact_coeff * np.sqrt(exit_participation) * 10000.0
                if exit_slip_bps > 100.0:
                    exit_slip_bps = 100.0
                slip = exit_price * exit_slip_bps / 10000.0
                if d == 1:
                    exit_price -= slip
                    pnl = position * (exit_price - entry_price)
                else:
                    exit_price += slip
                    pnl = position * (entry_price - exit_price)
                fee = abs(position * exit_price) * fee_rate
                net_pnl = pnl - fee
                equity += net_pnl

                pos_usd = abs(position * entry_price)
                if trade_count < max_trades:
                    out_pnl[trade_count] = net_pnl
                    out_ret[trade_count] = net_pnl / max(pos_usd, 1.0) * 100.0
                    out_hold[trade_count] = bars_held
                    out_exit[trade_count] = exit_code
                    out_pos[trade_count] = pos_usd
                    out_entry_bar[trade_count] = entry_bar
                    out_exit_bar[trade_count] = i
                    trade_count += 1
                position = 0.0

        # ENTRY
        if position == 0.0 and entry_mask[i]:
            d = direction[i] if i < n else 1
            if d == 0:
                d = 1

            cur_atr_e = atr[i]
            if np.isnan(cur_atr_e):
                cur_atr_e = close[i] * 0.02
            vol = cur_atr_e / max(close[i], 1e-10)

            if edge_override < 0.10:
                continue
            kelly_frac = kelly_mult * edge_override
            vol_adj = 0.02 / max(vol, 0.005) if vol > 0.0 else 1.0
            raw = equity * kelly_frac * vol_adj
            max_cap = equity * cap_pct
            max_trade = equity * 0.05
            pos_usd = min(raw, max_cap, max_trade)
            if pos_usd < 0.0:
                pos_usd = 0.0
            if pos_usd < 200.0:
                continue

            # Position-size-aware slippage: base spread + market impact
            participation = pos_usd / max(adv, 1.0)
            slip_bps = base_spread_bps + impact_coeff * np.sqrt(participation) * 10000.0
            if slip_bps > 100.0:
                slip_bps = 100.0

            entry_price = close[i] + (close[i] * slip_bps / 10000.0 * d)
            position = pos_usd / max(entry_price, 1e-10) * d
            entry_bar = i
            highest = high[i]
            lowest = low[i]
            fee = abs(position * entry_price) * fee_rate
            equity -= fee

            initial_risk = stop_mult * cur_atr_e
            if d == 1:
                stop_price = entry_price - initial_risk
            else:
                stop_price = entry_price + initial_risk

    # Close remaining position
    if position != 0.0:
        d_final = 1 if position > 0.0 else -1
        if d_final == 1:
            pnl = position * (close[n - 1] - entry_price)
        else:
            pnl = abs(position) * (entry_price - close[n - 1])
        fee = abs(position * close[n - 1]) * fee_rate
        equity += pnl - fee

    return (out_pnl[:trade_count], out_ret[:trade_count], out_hold[:trade_count],
            out_exit[:trade_count], out_pos[:trade_count],
            out_entry_bar[:trade_count], out_exit_bar[:trade_count],
            equity)


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
    """Everything a strategy needs to make decisions. Read-only."""
    ticker: str
    tier: int
    adv: float  # Average Daily Volume in USD (computed from volume data)

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

    def align_daily_to_1h(self, daily_values):
        return _align_higher_to_lower(self.idx_d, daily_values, self.idx_1h)

    def align_4h_to_1h(self, h4_values):
        return _align_higher_to_lower(self.idx_4h, h4_values, self.idx_1h)


@dataclass
class StrategyResult:
    """What a strategy returns: entry signals + trade parameters."""
    entry_mask: np.ndarray
    direction: np.ndarray

    stop_mult: float = 3.0
    trail_mult: float = 3.0
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


StrategyFn = Callable[[StrategyContext], StrategyResult]


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
    n = len(close)
    obv = np.zeros(n)
    for i in range(1, n):
        if close[i] > close[i - 1]:
            obv[i] = obv[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv[i] = obv[i - 1] - volume[i]
        else:
            obv[i] = obv[i - 1]
    ctx.custom['obv'] = obv
    obv_slope = np.zeros(n)
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
# Engine
# =============================================================================

class Engine:
    """Main backtesting engine."""

    def __init__(self, data_dir='data', market='spot', capital=200_000,
                 fee_rate=None, slippage_bps=None):
        self.data_dir = data_dir
        self.market = market  # 'perp' or 'spot'
        self.capital = capital
        self._fee_override = fee_rate
        self._slip_override = slippage_bps
        self._enriched = None
        self._enriched_loaded = False
        self._context_cache = {}

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
                       use_cache: bool = False) -> Optional[StrategyContext]:
        """Build a StrategyContext for one token.
        
        Args:
            use_cache: If True, cache and reuse contexts for the same (ticker, len) pair.
                       Useful when running multiple strategies on the same token data.
        """
        if df_1h is None or len(df_1h) < 500:
            return None
        
        cache_key = (ticker, len(df_1h))
        if use_cache and cache_key in self._context_cache:
            return self._context_cache[cache_key]

        df_4h = aggregate_to_timeframe(df_1h, hours=4)
        df_daily = aggregate_to_timeframe(df_1h, hours=24)

        if len(df_4h) < 100 or len(df_daily) < 30:
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

        # Compute ADV from actual volume data, derive tier dynamically
        adv = compute_adv(c1, v1, lookback_days=30, hours_per_bar=1)
        tier = adv_to_tier(adv)

        ctx = StrategyContext(
            ticker=ticker, tier=tier, adv=adv,
            ind_1h=ind_1h, ind_4h=ind_4h, ind_d=ind_d,
            idx_1h=idx_1h, idx_4h=idx_4h, idx_d=idx_d,
            regime_1h=regime_1h,
            df_1h=df_1h, df_4h=df_4h, df_daily=df_daily,
            enriched=token_enriched,
        )

        for plugin in _INDICATOR_PLUGINS:
            try:
                plugin(ctx)
            except Exception:
                pass

        if use_cache:
            self._context_cache[cache_key] = ctx
        return ctx

    def _simulate(self, ctx: StrategyContext, result: StrategyResult) -> Tuple[list, float]:
        """Run simulation. Returns (trades_list, final_equity).
        Trades include 'entry_bar' and 'exit_bar' keys for equity curve reconstruction.
        """
        n = len(ctx.ind_1h['close'])
        close = ctx.ind_1h['close']
        high = ctx.ind_1h['high']
        low = ctx.ind_1h['low']
        atr_arr = ctx.ind_1h['atr']

        regime_vals = np.array(list(result.exit_regimes), dtype=np.int8)
        exit_regime_mask = np.isin(ctx.regime_1h, regime_vals)

        use_rsi = result.rsi_exit_level < 999
        rsi = ctx.ind_1h['rsi']

        use_mean_target = result.mean_target_vals is not None
        mean_target = result.mean_target_vals if use_mean_target else np.full(n, np.nan)

        # ADV-based sizing: continuous functions of actual volume
        kelly_mult, cap_pct = adv_to_sizing(ctx.adv)

        # Fee: flat override or ADV-based
        if self._fee_override is not None:
            fee_rate = self._fee_override
        else:
            fee_rate = adv_to_costs(ctx.adv)

        # Slippage model: base_spread + impact * sqrt(pos/ADV)
        # base_spread: half the typical bid-ask spread (~3bps for liquid, doesn't vary much)
        # impact_coeff: market impact coefficient (~0.10 = 10% of sqrt participation)
        if self._slip_override is not None:
            # Legacy: flat slippage override → set base=override, impact=0
            base_spread_bps = self._slip_override
            impact_coeff = 0.0
        else:
            base_spread_bps = 3.0   # minimum spread cost
            impact_coeff = 0.03     # sqrt market impact coefficient

        entry_mask = np.asarray(result.entry_mask, dtype=np.bool_)
        direction = np.asarray(result.direction, dtype=np.int8)

        sim_result = _simulate_core_jit(
            close, high, low, atr_arr, entry_mask, direction,
            float(result.stop_mult), float(result.trail_mult), float(result.target_mult),
            ctx.regime_1h, exit_regime_mask, int(result.min_hold), int(result.max_hold),
            rsi, float(result.rsi_exit_level), use_rsi,
            float(fee_rate), float(ctx.adv), float(base_spread_bps), float(impact_coeff),
            float(self.capital), int(ctx.tier),
            result.convex_exit, mean_target, use_mean_target,
            int(result.no_stop_bars), float(result.edge),
            float(kelly_mult), float(cap_pct),
        )

        pnl_arr, ret_arr, hold_arr, exit_arr, pos_arr, entry_bars, exit_bars, final_equity = sim_result

        trades = []
        for j in range(len(pnl_arr)):
            trades.append({
                'pnl': float(pnl_arr[j]),
                'return_pct': float(ret_arr[j]),
                'hold_hours': int(hold_arr[j]),
                'exit_reason': _EXIT_REASONS.get(int(exit_arr[j]), 'unknown'),
                'position_usd': float(pos_arr[j]),
                'entry_bar': int(entry_bars[j]),
                'exit_bar': int(exit_bars[j]),
                'strategy': result.name,
            })

        return trades, final_equity

    def backtest_token(self, strategy_fn: StrategyFn, ticker: str,
                       df_1h: Optional[pd.DataFrame] = None) -> Optional[Dict]:
        """Backtest a single token with a strategy function."""
        if df_1h is None:
            h1_path = os.path.join(self._cache_dir('1h'), f'{ticker}_1h.parquet')
            if not os.path.exists(h1_path):
                return None
            df_1h = pd.read_parquet(h1_path)

        ctx = self._build_context(ticker, df_1h)
        if ctx is None:
            return None

        result = strategy_fn(ctx)
        trades, equity = self._simulate(ctx, result)

        total_return = (equity - self.capital) / self.capital * 100
        wins = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

        return {
            'ticker': ticker,
            'tier': ctx.tier,
            'adv': ctx.adv,
            'total_return': total_return,
            'n_trades': len(trades),
            'win_rate': len(wins) / max(len(trades), 1) * 100,
            'payoff_ratio': avg_win / max(avg_loss, 1),
            'equity': equity,
            'trades': trades,
            'strategy': result.name,
        }

    def run(self, strategy_fn: StrategyFn, tokens: Optional[List[str]] = None,
            verbose: bool = True) -> Dict[str, Dict]:
        """Run a strategy across all tokens with data."""
        if tokens is None:
            tokens = get_all_tradeable(self.market)

        results = {}
        t0 = time.time()

        for idx, ticker in enumerate(tokens, 1):
            if verbose:
                print(f"  [{idx}/{len(tokens)}] {ticker}...", end=' ', flush=True)

            r = self.backtest_token(strategy_fn, ticker)
            if r is not None:
                results[ticker] = r
                if verbose:
                    print(f"Ret={r['total_return']:+.1f}%  "
                          f"Trades={r['n_trades']}  WR={r['win_rate']:.0f}%  "
                          f"Payoff={r['payoff_ratio']:.1f}x")
            elif verbose:
                print("skip")

        elapsed = time.time() - t0
        if verbose:
            self._print_summary(results, elapsed)

        return results

    def _print_summary(self, results, elapsed):
        if not results:
            print("No results.")
            return
        total_pnl = sum(r['equity'] - self.capital for r in results.values())
        total_trades = sum(r['n_trades'] for r in results.values())
        profitable = sum(1 for r in results.values() if r['equity'] > self.capital)

        all_trades = []
        for r in results.values():
            all_trades.extend(r.get('trades', []))
        wins = [t for t in all_trades if t['pnl'] > 0]
        losers = [t for t in all_trades if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1
        wr = len(wins) / max(len(all_trades), 1) * 100
        pr = avg_win / max(avg_loss, 1)

        print(f"\n  Portfolio: PnL=${total_pnl:+,.0f}  Trades={total_trades}  "
              f"WR={wr:.0f}%  Payoff={pr:.2f}x  "
              f"Profitable={profitable}/{len(results)}  "
              f"Time={elapsed:.2f}s")
