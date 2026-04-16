#!/usr/bin/env python3
"""
Gate 5 — s507 Parameter Sensitivity Test
=========================================
Vary each key parameter +/-20% and run L12M backtests.
Flag as FRAGILE if Calmar degrades >30% for any single +-20% change.

Parameters tested:
  - THRESHOLD (z-score entry): base=2.5
  - STOP_MULT (stop loss ATR): base=2.5
  - TRAIL_MULT (trail stop ATR): base=2.0
  - MAX_HOLD (hours): base=336
  - TARGET_MULT (take profit ATR, s507): base=3.0
"""

import sys
import os
import time
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

import numpy as np
import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable
from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK

import strategies.s506_ls_divergence as s506_mod
import strategies.s507_ls_div_fixed_tp as s507_mod


# ======================================================================
#  Metrics computation
# ======================================================================

def compute_metrics(equity_snapshots, capital=100_000):
    eq = np.array([s[1] for s in equity_snapshots], dtype=np.float64)
    ts = np.array([s[0] for s in equity_snapshots])
    n = len(eq)
    if n < 100:
        return None

    total_ret = (eq[-1] / eq[0] - 1)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = dd.min()

    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-10)
    mean_ret = np.mean(rets)
    std_ret = np.std(rets)
    sharpe = mean_ret / max(std_ret, 1e-10) * np.sqrt(8760)

    neg_rets = rets[rets < 0]
    downside = np.sqrt(np.mean(neg_rets**2)) if len(neg_rets) > 0 else 1e-10
    sortino = mean_ret / downside * np.sqrt(8760)

    hours = n
    years = hours / 8760.0
    ann_ret = (1 + total_ret) ** (1.0 / max(years, 0.01)) - 1 if total_ret > -1 else total_ret
    calmar = ann_ret / max(abs(max_dd), 1e-10)

    return {
        'total_return': total_ret * 100,
        'ann_return': ann_ret * 100,
        'max_dd': max_dd * 100,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'n_bars': n,
        'final_equity': float(eq[-1]),
    }


# ======================================================================
#  Cached module references (set after first load)
# ======================================================================
_cached_s506 = None
_cached_s507 = None


def _ensure_cached_modules():
    """After first backtest, grab references to the cached modules used
    by _load_strategy_fn so we can monkey-patch them directly."""
    global _cached_s506, _cached_s507
    with _STRATEGY_MODULE_LOCK:
        if 's507' in _STRATEGY_MODULE_CACHE:
            _cached_s507 = _STRATEGY_MODULE_CACHE['s507']
        if 's506' in _STRATEGY_MODULE_CACHE:
            _cached_s506 = _STRATEGY_MODULE_CACHE['s506']

    # s507's strategy function imports s506 via:
    #   from strategies.s506_ls_divergence import strategy as base_strategy
    # This resolves to sys.modules['strategies.s506_ls_divergence'] which is our s506_mod.
    # So s506_mod IS the right target for THRESHOLD etc.
    # But the cached s507 module has its own TARGET_MULT that we need to patch.
    # Similarly, the cached s506 might be a different object from s506_mod if loaded
    # via importlib (different module name).


def _patch_s506(param_name, value):
    """Patch a parameter on ALL copies of s506 that might be referenced."""
    setattr(s506_mod, param_name, value)
    if _cached_s506 is not None and _cached_s506 is not s506_mod:
        setattr(_cached_s506, param_name, value)
    # Also patch sys.modules copy if different
    sm = sys.modules.get('strategies.s506_ls_divergence')
    if sm is not None and sm is not s506_mod:
        setattr(sm, param_name, value)


def _patch_s507(param_name, value):
    """Patch a parameter on ALL copies of s507 that might be referenced."""
    setattr(s507_mod, param_name, value)
    if _cached_s507 is not None and _cached_s507 is not s507_mod:
        setattr(_cached_s507, param_name, value)
    sm = sys.modules.get('strategies.s507_ls_div_fixed_tp')
    if sm is not None and sm is not s507_mod:
        setattr(sm, param_name, value)


# ======================================================================
#  Run one backtest (DOES NOT clear module cache after first run)
# ======================================================================

def run_one(label="baseline", first_run=False):
    """Run s507 L12M backtest, return metrics dict."""
    config = PortfolioConfig(
        capital=100_000,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
    )

    spec = StrategySpec(
        strategy_id='s507',
        market='perp',
        strategy_type='per_token',
        max_positions=50,
    )

    tokens = get_all_tradeable()
    end_date = pd.Timestamp('2026-04-01')

    if first_run:
        # Clear cache only on first run to force fresh load
        with _STRATEGY_MODULE_LOCK:
            _STRATEGY_MODULE_CACHE.pop('s507', None)
            _STRATEGY_MODULE_CACHE.pop('s506', None)
    # For subsequent runs, keep cached modules (we monkey-patch them directly)

    # Always clear s506 alignment cache (data alignment depends on signal params)
    s506_mod._aligned_cache.clear()
    if _cached_s506 is not None and hasattr(_cached_s506, '_aligned_cache'):
        _cached_s506._aligned_cache.clear()

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, months=12, end_date=end_date)
    t1 = time.perf_counter()

    if not signals:
        print(f"  [{label}] No signals produced!")
        return None

    state = simulate_portfolio(
        {'s507': signals},
        {'s507': spec},
        config,
    )
    t2 = time.perf_counter()

    metrics = compute_metrics(state.equity_snapshots, 100_000)
    if metrics is None:
        print(f"  [{label}] Insufficient equity data")
        return None

    metrics['label'] = label
    metrics['n_trades'] = len(state.position_manager.closed_trades)
    metrics['signal_time'] = t1 - t0
    metrics['sim_time'] = t2 - t1
    return metrics


def print_metrics(m, header=""):
    if m is None:
        print(f"  {header}: FAILED")
        return
    print(f"\n  {header}")
    print(f"  {'─' * 50}")
    print(f"  Return:      {m['total_return']:+.1f}%")
    print(f"  Ann. Return: {m['ann_return']:+.1f}%")
    print(f"  Max DD:      {m['max_dd']:.1f}%")
    print(f"  Sharpe:      {m['sharpe']:.2f}")
    print(f"  Sortino:     {m['sortino']:.2f}")
    print(f"  Calmar:      {m['calmar']:.2f}")
    print(f"  Trades:      {m['n_trades']}")
    print(f"  Time:        {m['signal_time']:.1f}s signals + {m['sim_time']:.1f}s sim")


# ======================================================================
#  Main — parameter sweep
# ======================================================================

def main():
    print("=" * 70)
    print("  GATE 5 — s507 PARAMETER SENSITIVITY TEST")
    print("  Vary each param +/-20%, check Calmar degradation")
    print("=" * 70)

    # Save original values
    orig_s506 = {
        'THRESHOLD': s506_mod.THRESHOLD,
        'STOP_MULT': s506_mod.STOP_MULT,
        'TRAIL_MULT': s506_mod.TRAIL_MULT,
        'MAX_HOLD': s506_mod.MAX_HOLD,
    }
    orig_target = s507_mod.TARGET_MULT

    # ── Baseline ──
    print(f"\n{'=' * 70}")
    print(f"  BASELINE")
    print(f"  THRESHOLD={orig_s506['THRESHOLD']} STOP_MULT={orig_s506['STOP_MULT']} "
          f"TRAIL_MULT={orig_s506['TRAIL_MULT']} MAX_HOLD={orig_s506['MAX_HOLD']} "
          f"TARGET_MULT={orig_target}")
    print(f"{'=' * 70}")

    baseline = run_one("BASELINE", first_run=True)
    print_metrics(baseline, "BASELINE")

    # Grab cached module references after first load
    _ensure_cached_modules()

    if baseline is None:
        print("\nBASELINE FAILED — cannot proceed with sensitivity test.")
        return

    baseline_calmar = baseline['calmar']
    print(f"\n  Baseline Calmar: {baseline_calmar:.2f}")

    # Verify patching works: check that cached modules exist
    print(f"\n  Module resolution:")
    print(f"    s506_mod id: {id(s506_mod)}")
    print(f"    _cached_s506 id: {id(_cached_s506) if _cached_s506 else 'None'}")
    print(f"    sys.modules s506 id: {id(sys.modules.get('strategies.s506_ls_divergence', 'N/A'))}")
    print(f"    _cached_s507 id: {id(_cached_s507) if _cached_s507 else 'None'}")
    print(f"    s507_mod id: {id(s507_mod)}")

    # ── Parameter variations ──
    results = [baseline]
    fragile_params = []

    s506_params = {
        'THRESHOLD': 2.5,
        'STOP_MULT': 2.5,
        'TRAIL_MULT': 2.0,
        'MAX_HOLD': 336,
    }

    for param_name, base_val in s506_params.items():
        for direction, factor in [('-20%', 0.8), ('+20%', 1.2)]:
            new_val = base_val * factor
            if param_name == 'MAX_HOLD':
                new_val = int(new_val)

            label = f"{param_name} {direction} ({new_val})"
            print(f"\n{'=' * 70}")
            print(f"  {label}")
            print(f"{'=' * 70}")

            # Monkey-patch ALL copies
            _patch_s506(param_name, new_val)

            m = run_one(label)
            print_metrics(m, label)

            if m is not None:
                calmar_change = (m['calmar'] - baseline_calmar) / max(abs(baseline_calmar), 1e-10) * 100
                print(f"  Calmar change: {calmar_change:+.1f}% vs baseline")
                m['calmar_change_pct'] = calmar_change
                if calmar_change < -30:
                    fragile_params.append((param_name, direction, calmar_change))
                    print(f"  *** FRAGILE: Calmar degraded >30% ***")

            results.append(m)

            # Restore
            _patch_s506(param_name, base_val)

    # s507 TARGET_MULT
    for direction, factor in [('-20%', 0.8), ('+20%', 1.2)]:
        new_val = orig_target * factor
        label = f"TARGET_MULT {direction} ({new_val:.1f})"
        print(f"\n{'=' * 70}")
        print(f"  {label}")
        print(f"{'=' * 70}")

        _patch_s507('TARGET_MULT', new_val)

        m = run_one(label)
        print_metrics(m, label)

        if m is not None:
            calmar_change = (m['calmar'] - baseline_calmar) / max(abs(baseline_calmar), 1e-10) * 100
            print(f"  Calmar change: {calmar_change:+.1f}% vs baseline")
            m['calmar_change_pct'] = calmar_change
            if calmar_change < -30:
                fragile_params.append(('TARGET_MULT', direction, calmar_change))
                print(f"  *** FRAGILE: Calmar degraded >30% ***")

        results.append(m)
        _patch_s507('TARGET_MULT', orig_target)

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"  SENSITIVITY SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n  {'Parameter':<30} {'Calmar':>8} {'Change':>10} {'Verdict':>10}")
    print(f"  {'─' * 60}")

    for m in results:
        if m is None:
            continue
        change_str = f"{m.get('calmar_change_pct', 0):+.1f}%" if 'calmar_change_pct' in m else "  base"
        verdict = "FRAGILE" if m.get('calmar_change_pct', 0) < -30 else "OK"
        if m['label'] == 'BASELINE':
            verdict = "BASE"
        print(f"  {m['label']:<30} {m['calmar']:>8.2f} {change_str:>10} {verdict:>10}")

    print(f"\n  Baseline Calmar: {baseline_calmar:.2f}")

    if fragile_params:
        print(f"\n  *** FRAGILE PARAMETERS ({len(fragile_params)}) ***")
        for pname, direction, change in fragile_params:
            print(f"    - {pname} {direction}: Calmar change {change:+.1f}%")
        print(f"\n  VERDICT: FRAGILE — strategy has parameter sensitivity concerns")
    else:
        print(f"\n  VERDICT: ROBUST — no parameter degrades Calmar >30% at +/-20%")


if __name__ == "__main__":
    main()
