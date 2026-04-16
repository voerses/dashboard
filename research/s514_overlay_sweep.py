#!/usr/bin/env python3
"""s514 Overlay Sweep — VRP sizing, trend filter, ADX sizing, combined.

Tests overlays on s514 (L/S divergence contrarian MR) via size_multiplier
monkey-patching. Each overlay modifies the strategy function to inject
per-bar size_multiplier (and optionally mask entries) into the StrategyResult.

Overlays tested:
  1. VRP sizing: IV - RV z-score scales position size
  2. Trend filter: BTC 20/50 EMA cross skips counter-trend trades
  3. ADX sizing: reduce size in strong trends, increase in ranges
  4. Combined VRP + trend filter

Baseline: s514 with conc=15%, no TP -> +465.8% (from prior research).

Usage:
    /workspace/venv/bin/python research/s514_overlay_sweep.py
"""

import sys
import os
import time
import copy
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report
from v4.engine import (
    _load_strategy_fn, _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK,
    StrategyResult, compute_adx_indicators, _ema,
)

# ======================================================================
#  Constants
# ======================================================================

STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000
MONTHS = 12
END_DATE = '2026-04-01'

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DVOL_DIR = os.path.join(DATA_DIR, 'alternative', 'deribit_options', 'dvol')
BTC_PARQUET = os.path.join(DATA_DIR, 'perp', '1h_cache', 'BTC_1h.parquet')


# ======================================================================
#  VRP (Vol Risk Premium) Data
# ======================================================================

_vrp_cache = {}


def _load_dvol(symbol='btc'):
    """Load Deribit DVOL daily data -> pd.Series indexed by date."""
    path = os.path.join(DVOL_DIR, f'{symbol}_dvol_daily.json')
    if not os.path.exists(path):
        print(f"  [overlay] WARNING: DVOL not found: {path}")
        return None
    data = json.load(open(path))
    # Format: [[timestamp_ms, open, high, low, close], ...]
    df = pd.DataFrame(data, columns=['ts', 'open', 'high', 'low', 'close'])
    df['date'] = pd.to_datetime(df['ts'], unit='ms')
    df = df.drop_duplicates(subset='date', keep='last')
    df = df.set_index('date').sort_index()
    return df['close']  # DVOL close = implied vol


def _compute_vrp_zscore(idx_1h, rv_window=30, zscore_window=90):
    """Compute VRP z-score aligned to 1H index.

    VRP = IV (DVOL) - RV (realized vol from BTC close).
    Z-score over 90-day rolling window.
    """
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _vrp_cache:
        return _vrp_cache[cache_key]

    n = len(idx_1h)
    result = np.zeros(n, dtype=np.float64)

    # Load DVOL (implied vol)
    dvol = _load_dvol('btc')
    if dvol is None:
        _vrp_cache[cache_key] = result
        return result

    # Load BTC 1H close for realized vol
    if not os.path.exists(BTC_PARQUET):
        print("  [overlay] WARNING: BTC parquet not found")
        _vrp_cache[cache_key] = result
        return result

    btc_df = pd.read_parquet(BTC_PARQUET, columns=['close'])
    btc_df.index = pd.to_datetime(btc_df.index).tz_localize(None)

    # Align BTC close to idx_1h via merge_asof (handles mismatched timestamps)
    target = pd.DataFrame({'ts': idx_1h}, index=idx_1h)
    btc_aligned = pd.merge_asof(
        target, btc_df[['close']].rename_axis('ts').reset_index(),
        on='ts', direction='backward',
    )
    btc_close_vals = btc_aligned['close'].values

    # Compute daily realized vol (annualized) from 1H returns
    btc_1h = pd.Series(btc_close_vals, index=idx_1h)
    log_ret = np.log(btc_1h / btc_1h.shift(1))
    # 30-day rolling RV: std of hourly returns * sqrt(24*365) to annualize, * 100 for %
    rv_daily = log_ret.rolling(rv_window * 24).std() * np.sqrt(24 * 365) * 100

    # Align DVOL (daily) to 1H via forward-fill using merge_asof
    dvol_frame = dvol.reset_index()
    dvol_frame.columns = ['ts', 'dvol']
    dvol_aligned = pd.merge_asof(
        target.reset_index(drop=True), dvol_frame,
        on='ts', direction='backward',
    )
    dvol_1h_vals = dvol_aligned['dvol'].values.astype(np.float64)

    # VRP = IV - RV
    vrp = dvol_1h_vals - rv_daily.values

    # Z-score VRP over rolling window
    vrp_series = pd.Series(vrp, index=idx_1h)
    vrp_mean = vrp_series.rolling(zscore_window * 24).mean()
    vrp_std = vrp_series.rolling(zscore_window * 24).std()
    vrp_z = ((vrp_series - vrp_mean) / vrp_std.clip(lower=1e-10)).values
    vrp_z = np.nan_to_num(vrp_z, nan=0.0)

    _vrp_cache[cache_key] = vrp_z
    return vrp_z


# ======================================================================
#  BTC Trend Data (EMA cross)
# ======================================================================

_btc_trend_cache = {}


def _compute_btc_trend(idx_1h):
    """Compute BTC EMA20 > EMA50 trend signal aligned to 1H index.

    Returns: +1 (uptrend), -1 (downtrend) array.
    """
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _btc_trend_cache:
        return _btc_trend_cache[cache_key]

    n = len(idx_1h)
    result = np.zeros(n, dtype=np.int8)

    if not os.path.exists(BTC_PARQUET):
        _btc_trend_cache[cache_key] = result
        return result

    btc_df = pd.read_parquet(BTC_PARQUET, columns=['close'])
    btc_df.index = pd.to_datetime(btc_df.index).tz_localize(None)
    target = pd.DataFrame({'ts': idx_1h}, index=idx_1h)
    btc_aligned = pd.merge_asof(
        target, btc_df[['close']].rename_axis('ts').reset_index(),
        on='ts', direction='backward',
    )
    btc_close = btc_aligned['close'].values

    ema20 = _ema(btc_close, 20 * 24)  # 20-day EMA in hourly bars
    ema50 = _ema(btc_close, 50 * 24)  # 50-day EMA in hourly bars

    trend = np.where(ema20 > ema50, 1, -1).astype(np.int8)
    _btc_trend_cache[cache_key] = trend
    return trend


# ======================================================================
#  BTC ADX Data
# ======================================================================

_btc_adx_cache = {}


def _compute_btc_adx(idx_1h):
    """Compute BTC ADX aligned to 1H index.

    Uses daily-scale ADX (14-day period on 1H bars = 14*24 span).
    """
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _btc_adx_cache:
        return _btc_adx_cache[cache_key]

    n = len(idx_1h)
    result = np.full(n, 20.0, dtype=np.float64)  # neutral default

    if not os.path.exists(BTC_PARQUET):
        _btc_adx_cache[cache_key] = result
        return result

    btc_df = pd.read_parquet(BTC_PARQUET, columns=['open', 'high', 'low', 'close'])
    btc_df.index = pd.to_datetime(btc_df.index).tz_localize(None)
    target = pd.DataFrame({'ts': idx_1h}, index=idx_1h)
    btc_aligned = pd.merge_asof(
        target, btc_df[['high', 'low', 'close']].rename_axis('ts').reset_index(),
        on='ts', direction='backward',
    )

    high = btc_aligned['high'].values
    low = btc_aligned['low'].values
    close = btc_aligned['close'].values

    # True range
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    adx_data = compute_adx_indicators(high, low, tr)
    adx = adx_data['adx']

    _btc_adx_cache[cache_key] = adx
    return adx


# ======================================================================
#  Overlay Wrappers
# ======================================================================

def _make_vrp_overlay(original_fn):
    """VRP sizing overlay: scale size based on vol risk premium z-score."""
    def wrapped(ctx):
        result = original_fn(ctx)
        n = len(result.entry_mask)
        vrp_z = _compute_vrp_zscore(ctx.idx_1h)

        sm = np.ones(n, dtype=np.float64)
        sm[vrp_z > 1.0] = 1.3    # complacent: bigger positions
        sm[vrp_z < -0.5] = 0.5   # turbulent: smaller positions

        # Combine with any existing size_multiplier
        if isinstance(result.size_multiplier, np.ndarray):
            sm = sm * result.size_multiplier
        elif result.size_multiplier != 1.0:
            sm = sm * float(result.size_multiplier)

        result.size_multiplier = sm
        return result
    return wrapped


def _make_trend_filter(original_fn):
    """BTC EMA trend filter: skip counter-trend trades."""
    def wrapped(ctx):
        result = original_fn(ctx)
        trend = _compute_btc_trend(ctx.idx_1h)

        # Skip shorts when BTC in uptrend (EMA20 > EMA50)
        uptrend = trend > 0
        short_mask = result.direction < 0
        result.entry_mask = result.entry_mask & ~(uptrend & short_mask)

        # Skip longs when BTC in downtrend (EMA20 < EMA50)
        downtrend = trend < 0
        long_mask = result.direction > 0
        result.entry_mask = result.entry_mask & ~(downtrend & long_mask)

        return result
    return wrapped


def _make_adx_overlay(original_fn):
    """ADX sizing overlay: reduce size in strong trends, increase in ranges."""
    def wrapped(ctx):
        result = original_fn(ctx)
        n = len(result.entry_mask)
        adx = _compute_btc_adx(ctx.idx_1h)

        sm = np.ones(n, dtype=np.float64)
        sm[adx > 25] = 0.5    # strong trend: MR works worse
        sm[adx < 15] = 1.3    # range-bound: MR works best

        if isinstance(result.size_multiplier, np.ndarray):
            sm = sm * result.size_multiplier
        elif result.size_multiplier != 1.0:
            sm = sm * float(result.size_multiplier)

        result.size_multiplier = sm
        return result
    return wrapped


def _make_vrp_trend_combined(original_fn):
    """Combined VRP sizing + trend filter."""
    # Chain: first apply VRP sizing, then trend filter
    vrp_fn = _make_vrp_overlay(original_fn)
    combined_fn = _make_trend_filter(vrp_fn)
    return combined_fn


# ======================================================================
#  Backtest Runner
# ======================================================================

def make_config():
    """Create PortfolioConfig for s514 with conc=15%."""
    spec = StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=28,
        market=MARKET,
        strategy_type='per_token',
        max_concurrent_per_token=1,
        dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
        max_portfolio_positions=28,
        concentration_limit=0.15,
        strategies=[spec],
    )
    return config, spec


def run_backtest(label):
    """Run a single s514 backtest and return metrics."""
    end_date = pd.Timestamp(END_DATE)
    config, spec = make_config()

    tokens = discover_tokens(MARKET)
    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
    t1 = time.time()

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Tokens with signals: {len(signals)}")
    print(f"  Signal compute: {t1 - t0:.1f}s")
    print(f"{'='*70}")

    if not signals:
        print("  No signals!")
        return None, None, None

    strategy_specs = {STRATEGY_ID: spec}
    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config)
    t2 = time.time()
    print(f"  Simulation: {len(state.position_manager.closed_trades)} trades ({t2 - t1:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    print_report(metrics, extra_info, CAPITAL, [STRATEGY_ID])

    return metrics, extra_info, eq_daily


def run_with_overlay(overlay_name, overlay_factory):
    """Run backtest with a monkey-patched overlay on s514."""
    # Ensure strategy is loaded
    _load_strategy_fn(STRATEGY_ID)

    with _STRATEGY_MODULE_LOCK:
        mod = _STRATEGY_MODULE_CACHE.get(STRATEGY_ID)

    if mod is None:
        print(f"  ERROR: Could not load {STRATEGY_ID}")
        return None, None, None

    original_fn = mod.strategy

    # Clear signal caches so overlay is picked up
    # (precompute_strategy_signals caches per strategy_id)
    mod.strategy = overlay_factory(original_fn)
    # Also clear the module's alignment cache to avoid stale data
    if hasattr(mod, '_aligned_cache'):
        mod._aligned_cache.clear()

    try:
        metrics, extra_info, eq_daily = run_backtest(f"OVERLAY: {overlay_name}")
    finally:
        mod.strategy = original_fn

    return metrics, extra_info, eq_daily


# ======================================================================
#  Main
# ======================================================================

def main():
    print("=" * 70)
    print("  s514 Overlay Sweep — VRP, Trend, ADX, Combined")
    print("  Baseline: conc=15%, no TP")
    print("=" * 70)

    results = {}

    # 0. Baseline (no overlay)
    print("\n>>> Running BASELINE...")
    _load_strategy_fn(STRATEGY_ID)
    m, ei, eq = run_backtest("BASELINE: s514 (no overlay)")
    if m:
        results['Baseline'] = (m, ei)

    # We need to invalidate signal cache between runs.
    # precompute_strategy_signals uses the strategy function from the cached module,
    # so monkey-patching the module's .strategy attribute is enough -- signals
    # are recomputed each call since we pass fresh params.

    # 1. VRP sizing
    print("\n>>> Running VRP SIZING overlay...")
    m, ei, eq = run_with_overlay("VRP Sizing (1.3x complacent / 0.5x turbulent)",
                                  _make_vrp_overlay)
    if m:
        results['VRP Sizing'] = (m, ei)

    # 2. Trend filter
    print("\n>>> Running TREND FILTER overlay...")
    m, ei, eq = run_with_overlay("Trend Filter (BTC EMA20/50 cross)",
                                  _make_trend_filter)
    if m:
        results['Trend Filter'] = (m, ei)

    # 3. ADX sizing
    print("\n>>> Running ADX SIZING overlay...")
    m, ei, eq = run_with_overlay("ADX Sizing (0.5x trend / 1.3x range)",
                                  _make_adx_overlay)
    if m:
        results['ADX Sizing'] = (m, ei)

    # 4. Combined VRP + Trend
    print("\n>>> Running COMBINED VRP + TREND overlay...")
    m, ei, eq = run_with_overlay("VRP + Trend Combined",
                                  _make_vrp_trend_combined)
    if m:
        results['VRP + Trend'] = (m, ei)

    # ================================================================
    #  Summary Table
    # ================================================================
    print(f"\n{'='*100}")
    print("  OVERLAY COMPARISON SUMMARY")
    print(f"{'='*100}")
    print(f"  {'Overlay':<25s} {'Return':>10s} {'Delta':>10s} {'Sharpe':>7s} "
          f"{'Calmar':>7s} {'MaxDD':>7s} {'Trades':>7s} {'WinR':>6s}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*6}")

    baseline_return = None
    for label, (m, ei) in results.items():
        ret = m.total_return_pct
        if label == 'Baseline':
            baseline_return = ret
        delta_str = f"{ret - baseline_return:+.1f}%" if baseline_return is not None and label != 'Baseline' else "--"
        print(f"  {label:<25s} {ret:>+9.1f}% {delta_str:>10s} {m.sharpe_ratio:>7.2f} "
              f"{m.calmar_ratio:>7.2f} {m.max_drawdown_pct:>6.1f}% {m.total_trades:>7d} {m.win_rate_pct:>5.1f}%")

    # Analysis
    print(f"\n{'='*100}")
    print("  ANALYSIS")
    print(f"{'='*100}")

    if baseline_return is not None and len(results) > 1:
        best_label = max(
            [k for k in results if k != 'Baseline'],
            key=lambda k: results[k][0].sharpe_ratio,
        )
        best_m = results[best_label][0]
        base_m = results['Baseline'][0]

        print(f"\n  Best overlay by Sharpe: {best_label}")
        print(f"    Sharpe: {base_m.sharpe_ratio:.2f} -> {best_m.sharpe_ratio:.2f} "
              f"({best_m.sharpe_ratio - base_m.sharpe_ratio:+.2f})")
        print(f"    Return: {base_m.total_return_pct:+.1f}% -> {best_m.total_return_pct:+.1f}% "
              f"({best_m.total_return_pct - base_m.total_return_pct:+.1f}%)")
        print(f"    MaxDD:  {base_m.max_drawdown_pct:.1f}% -> {best_m.max_drawdown_pct:.1f}%")

        # VRP + MR thesis check
        if 'VRP Sizing' in results:
            vrp_m = results['VRP Sizing'][0]
            print(f"\n  VRP + MR thesis (from finding #52: super-additive with positioning):")
            print(f"    VRP Sharpe delta: {vrp_m.sharpe_ratio - base_m.sharpe_ratio:+.3f}")
            print(f"    VRP Return delta: {vrp_m.total_return_pct - base_m.total_return_pct:+.1f}%")
            if vrp_m.sharpe_ratio > base_m.sharpe_ratio:
                print(f"    CONFIRMED: VRP sizing improves risk-adjusted returns on MR strategy")
            else:
                print(f"    NOT CONFIRMED: VRP sizing does not improve Sharpe on s514")

        if 'VRP + Trend' in results:
            comb_m = results['VRP + Trend'][0]
            print(f"\n  Combined VRP + Trend vs individual overlays:")
            for comp in ['VRP Sizing', 'Trend Filter']:
                if comp in results:
                    comp_m = results[comp][0]
                    print(f"    vs {comp}: Sharpe {comb_m.sharpe_ratio - comp_m.sharpe_ratio:+.3f}, "
                          f"Return {comb_m.total_return_pct - comp_m.total_return_pct:+.1f}%")

    print("\nDone.")


if __name__ == '__main__':
    main()
