"""
s514 Asymmetric L/S Parameter Sweep
====================================
Optimize short and long sides independently, then combine.

Approach:
  1. Sweep short-only params (threshold, trail, no_stop_bars)
  2. Sweep long-only params (same grid)
  3. Find best config for each side
  4. Combine by summing equity curves (approximate -- shared capital in reality)
  5. Compare combined vs best symmetric baseline
"""

import sys
import os
import itertools

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

import pandas as pd
import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics
from v4.engine import _STRATEGY_MODULE_CACHE


# ======================================================================
#  CONSTANTS
# ======================================================================
CAPITAL = 100_000
END_DATE = pd.Timestamp('2026-04-01')

DEFAULTS = {
    'THRESHOLD': 2.5,
    'EDGE': 0.35,
    'TRAIL_MULT': 2.0,
    'STOP_MULT': 2.5,
    'TARGET_MULT': 999.0,
    'MAX_HOLD': 336,
    'NO_STOP_BARS': 24,
    'MIN_HOLD': 24,
    'LEVERAGE': 3.0,
    'BREAKEVEN_ATR': 0.5,
    'DIRECTION': 'both',
}

# Sweep grids
THRESHOLDS = [2.0, 2.2, 2.5, 2.8, 3.0]
TRAILS = [1.5, 2.0, 2.5, 3.0, 4.0]
NO_STOP_BARS_VALS = [12, 24, 36, 48]


# ======================================================================
#  BOOTSTRAP: force initial signal cache load
# ======================================================================
print("=" * 80)
print("s514 Asymmetric L/S Parameter Sweep")
print("=" * 80)
print("\nBootstrapping signal cache...")

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
precompute_strategy_signals(spec0, tokens, config0, 12, end_date=END_DATE)
print("Bootstrap complete.\n")


# ======================================================================
#  RUN HELPER
# ======================================================================
def run(label, overrides):
    """Run s514 with given param overrides, return (metrics, trades, eq_daily)."""
    mod = _STRATEGY_MODULE_CACHE.get('s514')
    # Reset to defaults
    for k, v in DEFAULTS.items():
        setattr(mod, k, v)
    # Apply overrides
    for k, v in overrides.items():
        setattr(mod, k, v)
    # Clear caches so new params take effect
    mod._aligned_cache.clear()
    mod._ls_loaded = False
    mod._ls_cache.clear()

    spec = StrategySpec(
        strategy_id='s514', weight=1.0, max_positions=28, market='perp',
        strategy_type='per_token', max_concurrent_per_token=1, dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL, exchange='binance', skip_walk_forward=True,
        conviction_mode='ranked', max_portfolio_positions=28,
        strategies=[spec], concentration_limit=0.15,
    )
    signals = precompute_strategy_signals(spec, tokens, config, 12, end_date=END_DATE)
    state = simulate_portfolio({'s514': signals}, {'s514': spec}, config)
    metrics, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    trades = state.position_manager.closed_trades
    print(f"  {label:<55} Ret={metrics.total_return_pct:>+8.1f}%  "
          f"Sharpe={metrics.sharpe_ratio:.2f}  MaxDD={metrics.max_drawdown_pct:.1f}%  "
          f"Trades={len(trades)}")
    return metrics, trades, eq_daily


# ======================================================================
#  BASELINE
# ======================================================================
print("=" * 80)
print("BASELINE (both sides, best symmetric config: conc=15%, no TP)")
print("=" * 80)
baseline_m, baseline_t, baseline_eq = run("baseline_both", {})
print()


# ======================================================================
#  SWEEP FUNCTION
# ======================================================================
def sweep_direction(direction):
    """Sweep threshold x trail x no_stop_bars for one direction."""
    results = []
    combos = list(itertools.product(THRESHOLDS, TRAILS, NO_STOP_BARS_VALS))
    total = len(combos)
    print(f"Sweeping {direction.upper()} ({total} combinations)...")

    for i, (thresh, trail, nsb) in enumerate(combos, 1):
        label = f"{direction}_T{thresh}_TR{trail}_NSB{nsb}"
        overrides = {
            'DIRECTION': direction,
            'THRESHOLD': thresh,
            'TRAIL_MULT': trail,
            'NO_STOP_BARS': nsb,
        }
        try:
            m, trades, eq = run(f"[{i}/{total}] {label}", overrides)
            results.append({
                'direction': direction,
                'threshold': thresh,
                'trail_mult': trail,
                'no_stop_bars': nsb,
                'total_return_pct': m.total_return_pct,
                'sharpe': m.sharpe_ratio,
                'max_dd_pct': m.max_drawdown_pct,
                'calmar': m.total_return_pct / abs(m.max_drawdown_pct) if m.max_drawdown_pct != 0 else 0,
                'trades': len(trades),
                'eq_daily': eq,
            })
        except Exception as e:
            print(f"  ERROR {label}: {e}")
    return results


# ======================================================================
#  RUN SWEEPS
# ======================================================================
print("=" * 80)
print("SHORT-ONLY SWEEP")
print("=" * 80)
short_results = sweep_direction('short')

print()
print("=" * 80)
print("LONG-ONLY SWEEP")
print("=" * 80)
long_results = sweep_direction('long')


# ======================================================================
#  FIND BEST CONFIGS
# ======================================================================
def find_best(results, sort_key='sharpe'):
    """Find best config by sort_key, filtering for min trades and reasonable DD."""
    valid = [r for r in results if r['trades'] >= 20 and r['max_dd_pct'] > -25]
    if not valid:
        valid = results
    valid.sort(key=lambda x: x[sort_key], reverse=True)
    return valid[0] if valid else None


print()
print("=" * 80)
print("TOP 10 SHORT CONFIGS (by Sharpe)")
print("=" * 80)
short_sorted = sorted(
    [r for r in short_results if r['trades'] >= 20],
    key=lambda x: x['sharpe'], reverse=True,
)
for i, r in enumerate(short_sorted[:10], 1):
    print(f"  #{i}  T={r['threshold']}  TR={r['trail_mult']}  NSB={r['no_stop_bars']:<3}  "
          f"Ret={r['total_return_pct']:>+7.1f}%  Sharpe={r['sharpe']:.2f}  "
          f"MaxDD={r['max_dd_pct']:.1f}%  Calmar={r['calmar']:.1f}  Trades={r['trades']}")

print()
print("=" * 80)
print("TOP 10 LONG CONFIGS (by Sharpe)")
print("=" * 80)
long_sorted = sorted(
    [r for r in long_results if r['trades'] >= 20],
    key=lambda x: x['sharpe'], reverse=True,
)
for i, r in enumerate(long_sorted[:10], 1):
    print(f"  #{i}  T={r['threshold']}  TR={r['trail_mult']}  NSB={r['no_stop_bars']:<3}  "
          f"Ret={r['total_return_pct']:>+7.1f}%  Sharpe={r['sharpe']:.2f}  "
          f"MaxDD={r['max_dd_pct']:.1f}%  Calmar={r['calmar']:.1f}  Trades={r['trades']}")

best_short = find_best(short_results)
best_long = find_best(long_results)


# ======================================================================
#  COMBINED ESTIMATE
# ======================================================================
print()
print("=" * 80)
print("COMBINED ASYMMETRIC ESTIMATE")
print("=" * 80)

if best_short and best_long:
    print(f"\nBest SHORT: T={best_short['threshold']}  TR={best_short['trail_mult']}  "
          f"NSB={best_short['no_stop_bars']}  → Ret={best_short['total_return_pct']:+.1f}%  "
          f"Sharpe={best_short['sharpe']:.2f}  MaxDD={best_short['max_dd_pct']:.1f}%")
    print(f"Best LONG:  T={best_long['threshold']}  TR={best_long['trail_mult']}  "
          f"NSB={best_long['no_stop_bars']}  → Ret={best_long['total_return_pct']:+.1f}%  "
          f"Sharpe={best_long['sharpe']:.2f}  MaxDD={best_long['max_dd_pct']:.1f}%")

    # Combine equity curves: each side runs on full capital, so combined P&L
    # is sum of individual P&Ls (approximate -- ignores capital contention)
    eq_short = best_short['eq_daily']
    eq_long = best_long['eq_daily']

    # Align on common dates
    common_idx = eq_short.index.intersection(eq_long.index)
    pnl_short = eq_short.loc[common_idx] - CAPITAL
    pnl_long = eq_long.loc[common_idx] - CAPITAL
    combined_eq = CAPITAL + pnl_short + pnl_long

    # Compute combined metrics
    combined_ret = (combined_eq.iloc[-1] / CAPITAL - 1) * 100
    daily_ret = combined_eq.pct_change().dropna()
    combined_sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else 0
    running_max = combined_eq.cummax()
    drawdown = (combined_eq - running_max) / running_max * 100
    combined_max_dd = drawdown.min()
    combined_calmar = combined_ret / abs(combined_max_dd) if combined_max_dd != 0 else 0

    print(f"\nCombined (approximate, summed P&L):")
    print(f"  Return:   {combined_ret:+.1f}%")
    print(f"  Sharpe:   {combined_sharpe:.2f}")
    print(f"  MaxDD:    {combined_max_dd:.1f}%")
    print(f"  Calmar:   {combined_calmar:.1f}")
    print(f"  Trades:   {best_short['trades']} short + {best_long['trades']} long "
          f"= {best_short['trades'] + best_long['trades']} total")

    print(f"\nBaseline (symmetric both):")
    print(f"  Return:   {baseline_m.total_return_pct:+.1f}%")
    print(f"  Sharpe:   {baseline_m.sharpe_ratio:.2f}")
    print(f"  MaxDD:    {baseline_m.max_drawdown_pct:.1f}%")
    print(f"  Trades:   {len(baseline_t)}")

    delta_ret = combined_ret - baseline_m.total_return_pct
    delta_sharpe = combined_sharpe - baseline_m.sharpe_ratio
    print(f"\nDelta (combined - baseline):")
    print(f"  Return:   {delta_ret:+.1f}%")
    print(f"  Sharpe:   {delta_sharpe:+.2f}")
    print(f"  MaxDD:    {combined_max_dd - baseline_m.max_drawdown_pct:+.1f}%")
else:
    print("ERROR: Could not find valid configs for both sides.")

# Also try: best by return instead of Sharpe
best_short_ret = find_best(short_results, sort_key='total_return_pct')
best_long_ret = find_best(long_results, sort_key='total_return_pct')

if best_short_ret and best_long_ret:
    print()
    print("=" * 80)
    print("ALTERNATIVE: BEST BY RETURN (instead of Sharpe)")
    print("=" * 80)
    print(f"Best SHORT (ret): T={best_short_ret['threshold']}  TR={best_short_ret['trail_mult']}  "
          f"NSB={best_short_ret['no_stop_bars']}  → Ret={best_short_ret['total_return_pct']:+.1f}%  "
          f"Sharpe={best_short_ret['sharpe']:.2f}")
    print(f"Best LONG (ret):  T={best_long_ret['threshold']}  TR={best_long_ret['trail_mult']}  "
          f"NSB={best_long_ret['no_stop_bars']}  → Ret={best_long_ret['total_return_pct']:+.1f}%  "
          f"Sharpe={best_long_ret['sharpe']:.2f}")

    eq_s = best_short_ret['eq_daily']
    eq_l = best_long_ret['eq_daily']
    common = eq_s.index.intersection(eq_l.index)
    combined2 = CAPITAL + (eq_s.loc[common] - CAPITAL) + (eq_l.loc[common] - CAPITAL)
    ret2 = (combined2.iloc[-1] / CAPITAL - 1) * 100
    dr2 = combined2.pct_change().dropna()
    sh2 = dr2.mean() / dr2.std() * np.sqrt(365) if dr2.std() > 0 else 0
    dd2 = ((combined2 - combined2.cummax()) / combined2.cummax() * 100).min()
    print(f"\n  Combined: Ret={ret2:+.1f}%  Sharpe={sh2:.2f}  MaxDD={dd2:.1f}%")

print("\n" + "=" * 80)
print("SWEEP COMPLETE")
print("=" * 80)
