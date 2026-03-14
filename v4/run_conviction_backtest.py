#!/usr/bin/env python3
"""Conviction-based entry scoring backtest.

Compares shuffle (random) vs ranked (conviction) vs hybrid entry ordering
across key portfolios and multiple seeds. Measures Calmar, Sharpe, MaxDD,
return, seed sensitivity, and conviction-PnL correlation.
"""
import sys, os, gc, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import (
    build_unified_index, SimulationState, _process_exits, _process_entries,
    _close_all_remaining, simulate_portfolio,
)
from v4.report import compute_portfolio_metrics

# Key portfolios to test
TEST_PORTFOLIOS = {
    "s58": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
    },
    "s58+s60": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 0.5, "perp", 10, "per_token"),
        ],
        "max_portfolio_positions": 45,
        "concentration_limit": 0.10,
    },
    "super5": {
        "strategies": [
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 1.0, "perp", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
    },
    "4-edge": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 0.5, "perp", 10, "per_token"),
            ("s63", 0.5, "perp", 10, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
}

# Modes to compare
MODES = ["shuffle", "ranked", "hybrid"]

# Seeds for sensitivity testing
SEEDS = [42, 123, 456, 789, 1001, 2024, 3333, 5555, 7777, 9999]

# Conviction thresholds to sweep
THRESHOLDS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]


def run_sim(all_signals, specs, config):
    """Run the standard V4 simulator."""
    return simulate_portfolio(all_signals, specs, config)


def extract_metrics(state, capital=200_000):
    """Extract key metrics from simulation state."""
    metrics, extra, eq_daily = compute_portfolio_metrics(state, capital)
    total_ret = (extra['final_equity'] / capital - 1) * 100

    # Compute hourly DD (more accurate than daily-resampled)
    if state.equity_snapshots:
        eq_arr = np.array([s[1] for s in state.equity_snapshots])
        cummax = np.maximum.accumulate(eq_arr)
        dd_arr = (eq_arr - cummax) / np.maximum(cummax, 1e-10)
        hourly_max_dd = float(np.min(dd_arr)) * 100
    else:
        hourly_max_dd = 0.0

    return {
        'return_pct': total_ret,
        'sharpe': metrics.sharpe_ratio,
        'sortino': metrics.sortino_ratio,
        'calmar': metrics.calmar_ratio,
        'max_dd_daily': metrics.max_drawdown_pct,
        'max_dd_hourly': hourly_max_dd,
        'trades': metrics.total_trades,
        'rejections': state.rejections.to_dict(),
        'partial_fills': state.partial_fills,
    }


def run_conviction_comparison(portfolio_name, pdef, months, data_end, signal_cache):
    """Compare conviction modes for one portfolio."""
    strat_list = pdef["strategies"]

    # Precompute signals once (shared across all modes/seeds)
    specs_template = {}
    for sid, weight, market, max_pos, stype in strat_list:
        specs_template[sid] = StrategySpec(
            strategy_id=sid, weight=weight, max_positions=max_pos,
            market=market, strategy_type=stype,
        )

    base_config = PortfolioConfig(
        capital=200_000,
        max_portfolio_positions=pdef["max_portfolio_positions"],
        concentration_limit=pdef["concentration_limit"],
        adv_cap_pct=0.05,
    )

    all_signals = {}
    for sid, spec in specs_template.items():
        cache_key = (sid, spec.market, months)
        if cache_key not in signal_cache:
            tokens = discover_tokens(spec.market)
            print(f"  Precomputing {sid} ({len(tokens)} tokens, {spec.market})...")
            signal_cache[cache_key] = precompute_strategy_signals(
                spec, tokens, base_config, months, end_date=data_end
            )
            print(f"    Done: {len(signal_cache[cache_key])} tokens")
        all_signals[sid] = signal_cache[cache_key]

    results = {}

    # Phase 1: Compare modes with default seed and no threshold
    print(f"\n  --- Mode comparison (seed=42, threshold=0.0) ---")
    for mode in MODES:
        specs = {sid: StrategySpec(
            strategy_id=sid, weight=w, max_positions=mp,
            market=mkt, strategy_type=st,
        ) for sid, w, mkt, mp, st in strat_list}

        config = PortfolioConfig(
            capital=200_000,
            max_portfolio_positions=pdef["max_portfolio_positions"],
            concentration_limit=pdef["concentration_limit"],
            adv_cap_pct=0.05,
            seed=42,
            conviction_mode=mode,
            min_conviction_threshold=0.0,
        )

        state = run_sim(all_signals, specs, config)
        m = extract_metrics(state)
        results[f"mode_{mode}"] = m
        rej = m['rejections']
        print(f"    {mode:>8s}: Return={m['return_pct']:>+7.1f}%  Sharpe={m['sharpe']:>5.2f}"
              f"  MaxDD(h)={m['max_dd_hourly']:>6.1f}%  Calmar={m['calmar']:>6.1f}"
              f"  Trades={m['trades']:>5d}  Rej={rej['total']:>5d} (conv={rej.get('conviction',0)})")

    # Phase 2: Seed sensitivity for each mode
    print(f"\n  --- Seed sensitivity (10 seeds) ---")
    for mode in MODES:
        seed_returns = []
        for seed in SEEDS:
            specs = {sid: StrategySpec(
                strategy_id=sid, weight=w, max_positions=mp,
                market=mkt, strategy_type=st,
            ) for sid, w, mkt, mp, st in strat_list}

            config = PortfolioConfig(
                capital=200_000,
                max_portfolio_positions=pdef["max_portfolio_positions"],
                concentration_limit=pdef["concentration_limit"],
                adv_cap_pct=0.05,
                seed=seed,
                conviction_mode=mode,
                min_conviction_threshold=0.0,
            )

            state = run_sim(all_signals, specs, config)
            m = extract_metrics(state)
            seed_returns.append(m['return_pct'])

        arr = np.array(seed_returns)
        mean_ret = float(np.mean(arr))
        std_ret = float(np.std(arr))
        min_ret = float(np.min(arr))
        max_ret = float(np.max(arr))
        cv = std_ret / max(abs(mean_ret), 1e-10) * 100

        results[f"seed_{mode}"] = {
            'returns': seed_returns,
            'mean': mean_ret,
            'std': std_ret,
            'min': min_ret,
            'max': max_ret,
            'cv_pct': cv,
            'range_pct': max_ret - min_ret,
        }
        print(f"    {mode:>8s}: mean={mean_ret:>+7.1f}%  std={std_ret:>5.1f}pp"
              f"  range=[{min_ret:>+7.1f}%, {max_ret:>+7.1f}%]  CV={cv:>5.1f}%")

    # Phase 3: Threshold sweep for ranked mode
    print(f"\n  --- Threshold sweep (ranked mode, seed=42) ---")
    for thresh in THRESHOLDS:
        specs = {sid: StrategySpec(
            strategy_id=sid, weight=w, max_positions=mp,
            market=mkt, strategy_type=st,
        ) for sid, w, mkt, mp, st in strat_list}

        config = PortfolioConfig(
            capital=200_000,
            max_portfolio_positions=pdef["max_portfolio_positions"],
            concentration_limit=pdef["concentration_limit"],
            adv_cap_pct=0.05,
            seed=42,
            conviction_mode="ranked",
            min_conviction_threshold=thresh,
        )

        state = run_sim(all_signals, specs, config)
        m = extract_metrics(state)
        results[f"thresh_{thresh}"] = m
        rej = m['rejections']
        print(f"    thresh={thresh:.1f}: Return={m['return_pct']:>+7.1f}%  Sharpe={m['sharpe']:>5.2f}"
              f"  MaxDD(h)={m['max_dd_hourly']:>6.1f}%  Calmar={m['calmar']:>6.1f}"
              f"  Trades={m['trades']:>5d}  Rej.conv={rej.get('conviction',0)}")

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--portfolios", nargs="*", default=None,
                        help="Portfolio names to test (default: all)")
    args = parser.parse_args()

    data_end = infer_data_end_date("combined")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {args.months} months")

    signal_cache = {}
    all_results = {}

    portfolios = args.portfolios or list(TEST_PORTFOLIOS.keys())

    for name in portfolios:
        if name not in TEST_PORTFOLIOS:
            print(f"  Unknown portfolio: {name}")
            continue
        pdef = TEST_PORTFOLIOS[name]
        print(f"\n{'='*70}")
        print(f"  PORTFOLIO: {name}")
        print(f"{'='*70}")

        try:
            r = run_conviction_comparison(name, pdef, args.months, data_end, signal_cache)
            all_results[name] = r
        except Exception as e:
            import traceback
            print(f"  FAILED: {e}")
            traceback.print_exc()
        gc.collect()

    # Final summary
    print(f"\n{'='*90}")
    print(f"  CONVICTION SCORING — SUMMARY")
    print(f"{'='*90}")
    print(f"\n  Mode comparison (seed=42):")
    print(f"  {'Portfolio':>12s}  {'Mode':>8s}  {'Return':>9s}  {'Sharpe':>7s}  {'MaxDD(h)':>9s}  {'Calmar':>7s}  {'Trades':>6s}")
    print(f"  {'─'*12}  {'─'*8}  {'─'*9}  {'─'*7}  {'─'*9}  {'─'*7}  {'─'*6}")

    for name in portfolios:
        if name not in all_results:
            continue
        r = all_results[name]
        for mode in MODES:
            key = f"mode_{mode}"
            if key not in r:
                continue
            m = r[key]
            print(f"  {name:>12s}  {mode:>8s}  {m['return_pct']:>+8.1f}%  {m['sharpe']:>7.2f}"
                  f"  {m['max_dd_hourly']:>8.1f}%  {m['calmar']:>7.1f}  {m['trades']:>6d}")

    print(f"\n  Seed sensitivity (CV% = std/mean):")
    print(f"  {'Portfolio':>12s}  {'Mode':>8s}  {'Mean':>9s}  {'Std':>7s}  {'Range':>12s}  {'CV%':>6s}")
    print(f"  {'─'*12}  {'─'*8}  {'─'*9}  {'─'*7}  {'─'*12}  {'─'*6}")

    for name in portfolios:
        if name not in all_results:
            continue
        r = all_results[name]
        for mode in MODES:
            key = f"seed_{mode}"
            if key not in r:
                continue
            s = r[key]
            rng = f"[{s['min']:>+.0f}%, {s['max']:>+.0f}%]"
            print(f"  {name:>12s}  {mode:>8s}  {s['mean']:>+8.1f}%  {s['std']:>5.1f}pp"
                  f"  {rng:>12s}  {s['cv_pct']:>5.1f}%")

    print(f"\n  Best threshold (ranked mode):")
    print(f"  {'Portfolio':>12s}  {'Thresh':>7s}  {'Return':>9s}  {'Sharpe':>7s}  {'MaxDD(h)':>9s}  {'Calmar':>7s}  {'Trades':>6s}  {'Rej.conv':>9s}")
    print(f"  {'─'*12}  {'─'*7}  {'─'*9}  {'─'*7}  {'─'*9}  {'─'*7}  {'─'*6}  {'─'*9}")

    for name in portfolios:
        if name not in all_results:
            continue
        r = all_results[name]
        for thresh in THRESHOLDS:
            key = f"thresh_{thresh}"
            if key not in r:
                continue
            m = r[key]
            rej = m['rejections']
            print(f"  {name:>12s}  {thresh:>7.1f}  {m['return_pct']:>+8.1f}%  {m['sharpe']:>7.2f}"
                  f"  {m['max_dd_hourly']:>8.1f}%  {m['calmar']:>7.1f}  {m['trades']:>6d}  {rej.get('conviction',0):>9d}")

    # Save results
    out_path = Path("results/v4/conviction_backtest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    class NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    with open(out_path, 'w') as f:
        json.dump(all_results, f, indent=2, cls=NumpyEncoder)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
