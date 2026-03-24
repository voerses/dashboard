"""
s120 — Triple Macro Regime Overlay (DXY + US10Y + Oil) on s56
==============================================================
Class C Overlay: wraps s56 (Tier A) with macro regime-based position sizing.

Research basis (IC=-0.413 BTC 14D OOS, triple regime):
  - DXY 20d momentum + US10Y 20d change define TIGHTENING/EASING/MIXED
  - Oil 20d momentum adds third dimension (stagflation risk)
  - TIGHTENING (10Y rising + DXY rising): worst crypto regime, skip trades
  - EASING (both falling): best regime, 2x sizing
  - MIXED: normal sizing
  - Oil rising fast (>2 std of 20d change): additional size reduction

Regime -> size_multiplier:
  EASING:     2.0 (both DXY and 10Y falling)
  MIXED:      1.0 (one up, one down)
  TIGHTENING: 0.0 (both rising — skip trades entirely)
  Oil shock:  0.5x additional reduction when oil 20d change > 2 std

Macro data loaded ONCE at module level (cached). All computation vectorized.
Aligns daily macro regime to hourly bars via ctx.align_daily_to_1h().

Base: s56_signal_enhanced_momentum (Tier A, perp, long-only momentum)
Status: EXPERIMENTAL
"""

import os
import numpy as np
import pandas as pd

from strategies.s56_signal_enhanced_momentum import strategy as base_strategy

# =============================================================================
# Module-level macro data loading (cached, runs once on import)
# =============================================================================

_MACRO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'alternative', 'macro')
_LOOKBACK = 20  # 20-day lookback for regime signals


def _load_macro_series(filename):
    """Load a macro parquet file, return daily Close series indexed by date."""
    path = os.path.join(_MACRO_DIR, filename)
    df = pd.read_parquet(path)
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    return df['Close'].dropna()


def _compute_regime_data():
    """
    Compute daily regime arrays from macro data. Returns tuple of:
      - daily_timestamps: int64 array of nanosecond timestamps
      - combined_mult: float64 array of regime * oil_factor multiplier
    All computations are causal (no lookahead).
    """
    us10y = _load_macro_series('us10y_yield.parquet')
    dxy = _load_macro_series('usd_index.parquet')
    oil = _load_macro_series('oil_wti.parquet')

    # Build on common daily index (US10Y as anchor, ffill others)
    idx = us10y.index
    df = pd.DataFrame(index=idx)
    df['us10y'] = us10y
    df['dxy'] = dxy.reindex(idx, method='ffill')
    df['oil'] = oil.reindex(idx, method='ffill')

    # 20-day changes (causal)
    df['us10y_20d_chg'] = df['us10y'].diff(_LOOKBACK)
    df['dxy_20d_mom'] = df['dxy'].pct_change(_LOOKBACK)
    df['oil_20d_chg'] = df['oil'].pct_change(_LOOKBACK)

    # Drop warmup rows where we don't have enough data
    df = df.dropna(subset=['us10y_20d_chg', 'dxy_20d_mom', 'oil_20d_chg'])

    # Regime classification: vectorized
    y10_up = df['us10y_20d_chg'].values > 0
    dxy_up = df['dxy_20d_mom'].values > 0

    # Default MIXED = 1.0
    regime_mult = np.ones(len(df))
    # TIGHTENING: both rising -> 0.0
    regime_mult[y10_up & dxy_up] = 0.0
    # EASING: both falling -> 2.0
    regime_mult[~y10_up & ~dxy_up] = 2.0

    # Oil shock: 20d change > 2 std (expanding std to avoid lookahead)
    oil_chg = df['oil_20d_chg'].values
    oil_chg_series = pd.Series(oil_chg)
    oil_chg_std = oil_chg_series.expanding(min_periods=_LOOKBACK).std().values
    oil_chg_mean = oil_chg_series.expanding(min_periods=_LOOKBACK).mean().values
    oil_shock = oil_chg > (oil_chg_mean + 2.0 * oil_chg_std)

    # Pre-combine regime and oil factor into single multiplier
    oil_factor = np.where(oil_shock, 0.5, 1.0)
    combined = regime_mult * oil_factor

    # Return as numpy arrays for fast searchsorted alignment
    timestamps = df.index.values.astype('int64')
    return timestamps, combined.astype(np.float64)


# Load at module level -- this runs once on first import
_REGIME_TIMESTAMPS, _REGIME_COMBINED = _compute_regime_data()

# Per-ticker cache: {id(idx_1h): size_mult_array}
_ALIGN_CACHE = {}


def _get_aligned_mult(hourly_idx):
    """
    Align daily combined multiplier to hourly index via searchsorted.
    Cached per hourly index identity (same ctx.idx_1h object = cache hit).
    """
    cache_key = id(hourly_idx)
    cached = _ALIGN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # searchsorted: for each hourly timestamp, find the last daily timestamp <= it
    hourly_ns = hourly_idx.values.astype('int64')
    indices = np.searchsorted(_REGIME_TIMESTAMPS, hourly_ns, side='right') - 1

    # Bars before first macro data point get MIXED (1.0)
    n = len(hourly_ns)
    result = np.ones(n, dtype=np.float64)
    valid = indices >= 0
    result[valid] = _REGIME_COMBINED[indices[valid]]

    _ALIGN_CACHE[cache_key] = result
    return result


# =============================================================================
# Strategy function
# =============================================================================

def strategy(ctx):
    """s56 + triple macro regime overlay (DXY + US10Y + Oil)."""
    result = base_strategy(ctx)

    # Get cached hourly-aligned size multiplier
    size_mult = _get_aligned_mult(ctx.idx_1h)

    # Combine with base strategy's size_multiplier
    base_size = result.size_multiplier
    if isinstance(base_size, np.ndarray):
        final_size = base_size * size_mult
    else:
        final_size = float(base_size) * size_mult

    from engine import StrategyResult
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        leverage=result.leverage,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        exchange=result.exchange,
        name='s120_triple_regime_overlay',
        trail_schedule=result.trail_schedule,
        time_trail_schedule=getattr(result, 'time_trail_schedule', None),
        size_multiplier=final_size,
        max_trade_pct=result.max_trade_pct,
        cap_multiplier=result.cap_multiplier,
        breakeven_atr=0.5,
    )
