#!/usr/bin/env python3
"""Quick validation: Chandelier stop on s58 portfolio (s56+s57).

Tests chandelier_lookback values [0, 10, 16, 24] across 12mo and 3mo.
Compares Calmar ratio vs baseline (lookback=0 = standard trail).
"""
import gc
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


def run_test(chandelier_lookback: int, months: int = 12):
    """Run s58 portfolio backtest with given chandelier_lookback."""
    strategies = [
        StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="combined"),
        StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined"),
    ]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=40,
        concentration_limit=1.0,
    )

    data_end = infer_data_end_date("combined")

    # Precompute signals
    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
        # Override chandelier_lookback on all token signals
        for token, sig in signals.items():
            sig.chandelier_lookback = chandelier_lookback
        all_precomputed[spec.strategy_id] = signals

    strategy_specs = {s.strategy_id: s for s in strategies}
    state = simulate_portfolio(all_precomputed, strategy_specs, config)

    metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)
    trades = state.position_manager.closed_trades

    return {
        "chandelier_lookback": chandelier_lookback,
        "months": months,
        "return_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "calmar": metrics.calmar_ratio,
        "sharpe": metrics.sharpe_ratio,
        "sortino": metrics.sortino_ratio,
        "trades": metrics.total_trades,
        "win_rate": metrics.win_rate_pct,
    }


if __name__ == "__main__":
    lookbacks = [0, 10, 16, 24]
    periods = [12, 3]

    all_results = []
    for period in periods:
        print(f"\n{'='*70}")
        print(f"  {period}-MONTH PERIOD")
        print(f"{'='*70}")
        for lb in lookbacks:
            t0 = time.time()
            try:
                r = run_test(lb, period)
                elapsed = time.time() - t0
                all_results.append(r)
                tag = " << BASELINE" if lb == 0 else ""
                print(f"  lb={lb:3d}  ret={r['return_pct']:+8.1f}%  "
                      f"DD={r['max_dd_pct']:+7.2f}%  "
                      f"calmar={r['calmar']:6.2f}  "
                      f"sharpe={r['sharpe']:5.2f}  "
                      f"sortino={r['sortino']:5.2f}  "
                      f"trades={r['trades']:4d}  "
                      f"wr={r['win_rate']:5.1f}%  "
                      f"{elapsed:.1f}s{tag}")
            except Exception as e:
                import traceback
                print(f"  lb={lb:3d}  ERROR: {e}")
                traceback.print_exc()
            gc.collect()

    # Deltas vs baseline
    print(f"\n{'='*70}")
    print("  DELTA vs BASELINE (lookback=0)")
    print(f"{'='*70}")
    for r in all_results:
        bl = [x for x in all_results if x["months"] == r["months"] and x["chandelier_lookback"] == 0]
        if bl and r["chandelier_lookback"] != 0:
            b = bl[0]
            print(f"  {r['months']:2d}mo  lb={r['chandelier_lookback']:3d}  "
                  f"Δcalmar={r['calmar'] - b['calmar']:+.3f}  "
                  f"ΔDD={r['max_dd_pct'] - b['max_dd_pct']:+.2f}pp  "
                  f"Δret={r['return_pct'] - b['return_pct']:+.1f}%  "
                  f"Δsharpe={r['sharpe'] - b['sharpe']:+.3f}")
