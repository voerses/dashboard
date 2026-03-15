#!/usr/bin/env python3
"""Quick test: Triple barrier on s63 (counter-trend) and s65 (funding carry).

Counter-trend strategies may benefit from different exit dynamics than momentum.
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


def run_test(strategy_id: str, bear_target: float, bear_hold: int, chandelier_lb: int, months: int = 12):
    """Run single-strategy backtest with overlay params."""
    spec = StrategySpec(strategy_id=strategy_id, weight=1.0, max_positions=15, market="combined")
    config = PortfolioConfig(
        strategies=[spec],
        capital=200_000.0,
        max_portfolio_positions=15,
        concentration_limit=1.0,
    )

    data_end = infer_data_end_date("combined")
    tokens = discover_tokens(spec.market)
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
    for token, sig in signals.items():
        sig.bear_target_mult = bear_target
        sig.bear_max_hold = bear_hold
        sig.chandelier_lookback = chandelier_lb
    all_precomputed = {strategy_id: signals}

    strategy_specs = {strategy_id: spec}
    state = simulate_portfolio(all_precomputed, strategy_specs, config)
    metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

    return {
        "strategy": strategy_id,
        "bear_target": bear_target,
        "bear_hold": bear_hold,
        "chandelier_lb": chandelier_lb,
        "months": months,
        "return_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "calmar": metrics.calmar_ratio,
        "sharpe": metrics.sharpe_ratio,
        "trades": metrics.total_trades,
        "win_rate": metrics.win_rate_pct,
    }


if __name__ == "__main__":
    test_configs = [
        # (strategy, bear_target, bear_hold, chandelier_lb, label)
        ("s63", 0, 0, 0, "baseline"),
        ("s63", 3.0, 0, 0, "bt=3"),
        ("s63", 5.0, 0, 0, "bt=5"),
        ("s63", 3.0, 48, 0, "bt=3,bh=48"),
        ("s63", 0, 0, 10, "chand=10"),
        ("s63", 0, 0, 16, "chand=16"),
        ("s65", 0, 0, 0, "baseline"),
        ("s65", 3.0, 0, 0, "bt=3"),
        ("s65", 5.0, 0, 0, "bt=5"),
        ("s65", 3.0, 48, 0, "bt=3,bh=48"),
        ("s65", 0, 0, 10, "chand=10"),
        ("s65", 0, 0, 16, "chand=16"),
    ]

    for period in [12, 3]:
        print(f"\n{'='*90}")
        print(f"  {period}-MONTH PERIOD")
        print(f"{'='*90}")
        current_strat = None
        for strat, bt, bh, clb, label in test_configs:
            if strat != current_strat:
                current_strat = strat
                print(f"\n  --- {strat} ---")
            t0 = time.time()
            try:
                r = run_test(strat, bt, bh, clb, period)
                elapsed = time.time() - t0
                tag = " << BASE" if label == "baseline" else ""
                print(f"    {label:15s}  ret={r['return_pct']:+8.1f}%  "
                      f"DD={r['max_dd_pct']:+7.2f}%  "
                      f"calmar={r['calmar']:7.2f}  "
                      f"sharpe={r['sharpe']:5.2f}  "
                      f"trades={r['trades']:4d}  "
                      f"wr={r['win_rate']:5.1f}%  "
                      f"{elapsed:.1f}s{tag}")
            except Exception as e:
                print(f"    {label:15s}  ERROR: {e}")
            gc.collect()
