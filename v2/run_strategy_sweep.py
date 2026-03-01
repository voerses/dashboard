"""
Strategy Sweep — Test all strategies through the engine and CPCV validation.
============================================================================

Runs every strategy in strategies/ against all 11 CPCV tokens on 5yr data.
Produces a ranked comparison table and per-strategy validation results.

Usage: python run_strategy_sweep.py
"""

import sys
import os
import time
import json
import importlib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import Engine, CPCV_ROBUST_TOKENS

# ── Strategy Definitions ──────────────────────────────────────────────────
# Each entry: (module_path, label)
STRATEGIES = [
    # Existing proven strategies
    ('strategies.s11_momentum_burst', 'S11 Momentum Burst'),
    ('strategies.s09_optimized_trend', 'S09 Optimized Trend'),

    # NEW: Signal Lab-derived strategies
    ('strategies.s12_quality_breakout', 'S12 Quality Breakout'),
    ('strategies.s13_vol_weighted_tsmom', 'S13 Vol-Weighted TSMOM'),
    ('strategies.s14_microstructure_edge', 'S14 Microstructure Edge'),
    ('strategies.s15_vol_regime_breakout', 'S15 Vol Regime Breakout'),
    ('strategies.s16_composite_factor', 'S16 Composite Factor'),
    ('strategies.s17_trend_strength_filter', 'S17 Trend Strength Filter'),
    ('strategies.s18_momentum_accel', 'S18 Momentum Accel'),
    ('strategies.s19_mean_reversion_filtered', 'S19 Mean Reversion Filtered'),
    ('strategies.s20_low_beta_quality', 'S20 Low Beta Quality'),
    ('strategies.s21_skew_momentum', 'S21 Skew Momentum'),
    ('strategies.s22_supertrend_adx', 'S22 Supertrend ADX'),
]


def load_strategy(module_path):
    """Import strategy function from module path."""
    mod = importlib.import_module(module_path)
    return mod.strategy


def run_sweep():
    """Run all strategies and produce comparison."""
    print("=" * 100)
    print("STRATEGY SWEEP — 5yr Data, 11 CPCV Tokens")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Tokens: {', '.join(CPCV_ROBUST_TOKENS)}")
    print("=" * 100)

    engine = Engine()
    results = []
    strategy_fns = []
    labels = []

    # ── Phase 1: Quick comparison (all strategies, backtest only) ──
    print("\n" + "─" * 80)
    print("PHASE 1: Quick Backtest Comparison")
    print("─" * 80)

    for module_path, label in STRATEGIES:
        try:
            fn = load_strategy(module_path)
            strategy_fns.append(fn)
            labels.append(label)
        except Exception as e:
            print(f"  SKIP {label}: {e}")

    t0 = time.time()
    engine.compare(
        strategies=strategy_fns,
        labels=labels,
        tokens=CPCV_ROBUST_TOKENS,
    )
    elapsed = time.time() - t0
    print(f"\n  Comparison done in {elapsed:.1f}s")

    # ── Phase 2: Full validation on top performers ──
    print("\n" + "─" * 80)
    print("PHASE 2: Full CPCV + Walk-Forward Validation")
    print("─" * 80)

    validation_results = []
    for fn, label in zip(strategy_fns, labels):
        print(f"\n{'='*60}")
        print(f"VALIDATING: {label}")
        print(f"{'='*60}")
        try:
            t0 = time.time()
            result = engine.validate(fn, tokens=CPCV_ROBUST_TOKENS, verbose=True)
            elapsed = time.time() - t0

            validated = result.get('validated_tokens', [])
            total_pnl = result.get('total_pnl', 0)
            annual_pnl = result.get('annual_pnl', 0)

            validation_results.append({
                'label': label,
                'total_pnl': total_pnl,
                'annual_pnl': annual_pnl,
                'validated_count': len(validated),
                'validated_tokens': validated,
                'elapsed': elapsed,
            })

            print(f"  {label}: PnL=${total_pnl:+,.0f} Annual=${annual_pnl:+,.0f}/yr "
                  f"Validated={len(validated)}/11 [{', '.join(validated)}] ({elapsed:.1f}s)")

        except Exception as e:
            print(f"  {label}: FAILED — {e}")
            validation_results.append({
                'label': label,
                'total_pnl': 0,
                'annual_pnl': 0,
                'validated_count': 0,
                'validated_tokens': [],
                'elapsed': 0,
                'error': str(e),
            })

    # ── Phase 3: Summary Table ──
    print("\n" + "=" * 100)
    print("FINAL RANKING — All Strategies (by Annual PnL)")
    print("=" * 100)

    validation_results.sort(key=lambda x: x['annual_pnl'], reverse=True)

    print(f"\n{'Rank':<5} {'Strategy':<30} {'Annual PnL':>12} {'Total PnL':>12} "
          f"{'Validated':>10} {'Tokens':>40}")
    print("─" * 110)
    for i, r in enumerate(validation_results, 1):
        tokens_str = ', '.join(r['validated_tokens'][:6])
        if len(r['validated_tokens']) > 6:
            tokens_str += f" +{len(r['validated_tokens'])-6} more"
        print(f"{i:<5} {r['label']:<30} ${r['annual_pnl']:>+10,.0f} ${r['total_pnl']:>+10,.0f} "
              f"{r['validated_count']:>5}/11    {tokens_str}")

    # ── Save results ──
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = f"results/sweep_{timestamp}.json"
    os.makedirs("results", exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(validation_results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    return validation_results


if __name__ == "__main__":
    run_sweep()
