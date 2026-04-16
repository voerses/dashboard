"""
s514 Conviction-Based Dynamic Sizing
=====================================

Tests dynamic position sizing based on z-score magnitude and S/R proximity.
The V4 engine supports `size_multiplier` as a per-bar array in StrategyResult.

Baseline: conc=15%, no TP -> +465.8%, Sharpe 4.53, MaxDD -8.9%.

Conviction schemes:
  1. Linear:     size_mult = clip((|z| - 2.0) / 2.0, 0.25, 2.0)
  2. Step:       z buckets -> 0.5x / 1.0x / 1.5x / 2.0x
  3. Sqrt:       size_mult = sqrt(|z| / 2.5), capped at 2.0
  4. Aggressive: size_mult = (|z| / 2.5)^2, capped at 3.0

Plus S/R proximity overlay and combined schemes.

Usage:
    /workspace/venv/bin/python research/s514_conviction_sizing.py
"""

import sys
import os

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

import pandas as pd
import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics
from v4.engine import _STRATEGY_MODULE_CACHE, StrategyResult, rolling_zscore, MarketType

CAPITAL = 100_000
end_date = pd.Timestamp('2026-04-01')
DEFAULTS = {
    'THRESHOLD': 2.5, 'EDGE': 0.35, 'TRAIL_MULT': 2.0, 'STOP_MULT': 2.5,
    'TARGET_MULT': 999.0, 'MAX_HOLD': 336, 'NO_STOP_BARS': 24, 'MIN_HOLD': 24,
    'LEVERAGE': 3.0, 'BREAKEVEN_ATR': 0.5, 'DIRECTION': 'both',
    'ZSCORE_WINDOW': 720, 'WARMUP': 800,
}

# Force initial load
spec0 = StrategySpec(
    strategy_id='s514', weight=1.0, max_positions=28, market='perp',
    strategy_type='per_token', max_concurrent_per_token=1, dd_scaling=[],
)
config0 = PortfolioConfig(
    capital=CAPITAL, exchange='binance', skip_walk_forward=True,
    conviction_mode='ranked', max_portfolio_positions=28,
    strategies=[spec0], concentration_limit=0.15,
)
tokens = discover_tokens('perp')
precompute_strategy_signals(spec0, tokens, config0, 12, end_date=end_date)

mod = _STRATEGY_MODULE_CACHE.get('s514')
original_fn = mod.strategy


# ======================================================================
#  SIZING SCHEME HELPERS
# ======================================================================

def _compute_abs_zscore(ctx):
    """Recompute |z-score| from the strategy module's L/S data."""
    symbol = ctx.ticker + "USDT"
    if symbol not in mod._ls_cache:
        return np.ones(len(ctx.ind_1h['close']), dtype=np.float64)
    divergence = mod._get_divergence_aligned(symbol, ctx.idx_1h)
    div_zscore = rolling_zscore(divergence, mod.ZSCORE_WINDOW)
    div_zscore = np.nan_to_num(div_zscore, nan=0.0)
    return np.abs(div_zscore)


def _compute_sr_proximity(ctx, direction_arr):
    """Compute S/R proximity multiplier using 20-day Donchian channels.

    For shorts: closer to high (resistance) = more conviction.
    For longs: closer to low (support) = more conviction.
    Returns multiplier in [0.5, 1.5] range.
    """
    n = len(ctx.ind_1h['close'])
    high_20d = pd.Series(ctx.ind_1h['high']).rolling(480, min_periods=48).max().values
    low_20d = pd.Series(ctx.ind_1h['low']).rolling(480, min_periods=48).min().values
    close = ctx.ind_1h['close']

    range_width = high_20d - low_20d
    safe_range = np.maximum(range_width, 1e-10)

    # Distance from boundary as fraction [0, 1]
    # dist_from_high = 0 means at resistance, 1 means at support
    dist_from_high = (high_20d - close) / safe_range
    dist_from_low = (close - low_20d) / safe_range

    # For shorts (direction=-1): closer to high = more conviction -> use (1 - dist_from_high)
    # For longs (direction=+1): closer to low = more conviction -> use (1 - dist_from_low)
    # Neutral (direction=0): 1.0x
    sr_mult = np.ones(n, dtype=np.float64)

    short_mask = direction_arr == -1
    long_mask = direction_arr == 1

    # conviction = 0.5 + proximity * 1.0 -> range [0.5, 1.5]
    sr_mult[short_mask] = 0.5 + np.clip(1.0 - dist_from_high[short_mask], 0, 1) * 1.0
    sr_mult[long_mask] = 0.5 + np.clip(1.0 - dist_from_low[long_mask], 0, 1) * 1.0

    return sr_mult


def make_conviction_wrapper(scheme_fn, use_sr=False, label=""):
    """Create a wrapped strategy function that applies conviction sizing."""
    def conviction_strategy(ctx):
        result = original_fn(ctx)
        abs_z = _compute_abs_zscore(ctx)
        size_mult = scheme_fn(abs_z)

        if use_sr:
            sr_mult = _compute_sr_proximity(ctx, result.direction)
            size_mult = size_mult * sr_mult

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
            name=result.name,
            breakeven_atr=mod.BREAKEVEN_ATR,
            size_multiplier=size_mult,
        )
    return conviction_strategy


# -- Scheme definitions --

def linear_scheme(abs_z):
    """Linear: size_mult = clip((|z| - 2.0) / 2.0, 0.25, 2.0)"""
    return np.clip((abs_z - 2.0) / 2.0, 0.25, 2.0)


def step_scheme(abs_z):
    """Step: z=2.5-3.0 -> 0.5x, 3.0-3.5 -> 1.0x, 3.5-4.0 -> 1.5x, >4.0 -> 2.0x"""
    mult = np.full_like(abs_z, 0.5)
    mult[abs_z >= 3.0] = 1.0
    mult[abs_z >= 3.5] = 1.5
    mult[abs_z >= 4.0] = 2.0
    return mult


def sqrt_scheme(abs_z):
    """Sqrt: size_mult = sqrt(|z| / 2.5), capped at 2.0"""
    return np.clip(np.sqrt(abs_z / 2.5), 0.5, 2.0)


def aggressive_scheme(abs_z):
    """Aggressive: size_mult = (|z| / 2.5)^2, capped at 3.0"""
    return np.clip((abs_z / 2.5) ** 2, 0.5, 3.0)


def sr_only_scheme(abs_z):
    """No z-score sizing, just return 1.0 (S/R applied externally)."""
    return np.ones_like(abs_z)


# ======================================================================
#  RUN INFRASTRUCTURE
# ======================================================================

def reset_module():
    """Reset module state to defaults."""
    for k, v in DEFAULTS.items():
        setattr(mod, k, v)
    mod._aligned_cache.clear()
    mod._ls_loaded = False
    mod._ls_cache.clear()


def run_backtest(label, strategy_fn=None):
    """Run a single backtest, optionally with a custom strategy function."""
    reset_module()

    if strategy_fn is not None:
        mod.strategy = strategy_fn
    else:
        mod.strategy = original_fn

    spec = StrategySpec(
        strategy_id='s514', weight=1.0, max_positions=28, market='perp',
        strategy_type='per_token', max_concurrent_per_token=1, dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL, exchange='binance', skip_walk_forward=True,
        conviction_mode='ranked', max_portfolio_positions=28,
        strategies=[spec], concentration_limit=0.15,
    )
    signals = precompute_strategy_signals(spec, tokens, config, 12, end_date=end_date)
    state = simulate_portfolio({'s514': signals}, {'s514': spec}, config)
    metrics, _, _ = compute_portfolio_metrics(state, CAPITAL)

    # Restore original
    mod.strategy = original_fn

    print(f"  {label:<55} Ret={metrics.total_return_pct:>+8.1f}%  "
          f"Sharpe={metrics.sharpe_ratio:.2f}  Calmar={metrics.calmar_ratio:.2f}  "
          f"MaxDD={metrics.max_drawdown_pct:.1f}%  Trades={metrics.total_trades}")
    return metrics


# ======================================================================
#  MAIN
# ======================================================================

if __name__ == '__main__':
    results = {}

    # ------------------------------------------------------------------
    print("=" * 110)
    print("PART 0: BASELINE (uniform sizing)")
    print("=" * 110)
    results['baseline'] = run_backtest("Baseline (uniform 1.0x)")

    # ------------------------------------------------------------------
    print()
    print("=" * 110)
    print("PART 1: Z-SCORE CONVICTION SCHEMES (no S/R)")
    print("=" * 110)

    schemes = [
        ("Linear  (|z|-2)/2, [0.25, 2.0]", linear_scheme),
        ("Step    0.5/1.0/1.5/2.0", step_scheme),
        ("Sqrt    sqrt(|z|/2.5), [0.5, 2.0]", sqrt_scheme),
        ("Aggressive (|z|/2.5)^2, [0.5, 3.0]", aggressive_scheme),
    ]

    for label, scheme_fn in schemes:
        wrapper = make_conviction_wrapper(scheme_fn, use_sr=False)
        results[label] = run_backtest(label, wrapper)

    # ------------------------------------------------------------------
    print()
    print("=" * 110)
    print("PART 2: S/R PROXIMITY ONLY (no z-score scaling)")
    print("=" * 110)

    wrapper = make_conviction_wrapper(sr_only_scheme, use_sr=True)
    results['SR only'] = run_backtest("S/R proximity only [0.5, 1.5]", wrapper)

    # ------------------------------------------------------------------
    print()
    print("=" * 110)
    print("PART 3: Z-SCORE + S/R PROXIMITY COMBINED")
    print("=" * 110)

    for label, scheme_fn in schemes:
        combo_label = f"{label} + S/R"
        wrapper = make_conviction_wrapper(scheme_fn, use_sr=True)
        results[combo_label] = run_backtest(combo_label, wrapper)

    # ------------------------------------------------------------------
    print()
    print("=" * 110)
    print("SUMMARY")
    print("=" * 110)

    baseline = results['baseline']
    print(f"\n  {'Scheme':<55} {'Return':>9} {'Sharpe':>7} {'Calmar':>7} {'MaxDD':>7} {'Trades':>7}  vs Base")
    print("  " + "-" * 105)

    for name, m in results.items():
        delta_ret = m.total_return_pct - baseline.total_return_pct
        delta_sharpe = m.sharpe_ratio - baseline.sharpe_ratio
        print(f"  {name:<55} {m.total_return_pct:>+8.1f}% {m.sharpe_ratio:>7.2f} "
              f"{m.calmar_ratio:>7.2f} {m.max_drawdown_pct:>6.1f}% {m.total_trades:>7}  "
              f"dRet={delta_ret:>+7.1f}% dSh={delta_sharpe:>+.2f}")

    print("\nDone.")
