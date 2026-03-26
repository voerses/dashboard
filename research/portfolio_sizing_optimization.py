#!/workspace/venv/bin/python
"""
Portfolio Sizing Optimization — Per-Strategy Sizing Override Research
=====================================================================

Tests whether optimized per-strategy sizing can improve multi-strategy
portfolio performance, using the newly available sizing_overrides:
  - kelly_mult_floor, kelly_mult_range
  - cap_pct_floor, cap_pct_range
  - adv_scaling_divisor
  - kelly_mult_override, kelly_mult_scale
  - cap_pct_override, cap_pct_scale

Context: 7 paper pools running, ALL losing. Only s62 (+9.6%) and s65 (+4.9%)
are positive post-MTM 12mo. This research tests aggressive sizing on the
winners to see if capital efficiency can be improved.

SAFETY_RAILS bounds (from v4/config.py):
  kelly_mult_floor:    [0.05, 0.40]
  kelly_mult_range:    [0.10, 0.80]
  cap_pct_floor:       [0.005, 0.08]
  cap_pct_range:       [0.02, 0.30]
  adv_scaling_divisor: [1.0, 20.0]
  kelly_mult_override: [0.05, 0.50]
  kelly_mult_scale:    [0.5, 2.0]
  cap_pct_override:    [0.01, 0.15]
  cap_pct_scale:       [0.5, 2.0]

Experiments:
  1. Best-of-two (s62 + s65) with aggressive sizing
  2. s62 solo, fully optimized
  3. s56+s57+s65 (s58+s65 pool) with optimized sizing

Author: Quant Research Agent
Date: 2026-03-26
"""

import dataclasses
import json
import os
import sys
import time
import traceback
import warnings
from pathlib import Path
from dataclasses import asdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# Ensure project root is importable
PROJECT_ROOT = Path('/workspace/crypto_backtest')
sys.path.insert(0, str(PROJECT_ROOT))
# Strategies import `from engine import ...` and engine.py lives in v4/
sys.path.insert(0, str(PROJECT_ROOT / 'v4'))

from v4.config import PortfolioConfig, StrategySpec, SizingDefaults
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report, print_diagnostic_report


# ── Experiment Definitions ─────────────────────────────────────────────────

CAPITAL = 200_000
MONTHS_LIST = [12, 3]

def make_base_config(**kwargs):
    """Create a PortfolioConfig with sensible defaults for research."""
    defaults = dict(
        exchange="binance",
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        max_portfolio_positions=40,
        seed=42,
        base_spread_bps=3.0,
        impact_coeff=0.03,
    )
    defaults.update(kwargs)
    return PortfolioConfig(**defaults)


# ---------------------------------------------------------------------------
# Experiment 1: Best-of-two (s62 + s65) with aggressive sizing
# ---------------------------------------------------------------------------

EXP1_CONFIGS = {
    # Baseline: equal weight, default sizing
    "exp1_baseline_50_50": {
        "description": "s62+s65 equal weight (50/50), default sizing",
        "strategies": [
            {"strategy_id": "s62", "weight": 0.5, "market": "perp",
             "max_positions": 15, "sizing_overrides": {}},
            {"strategy_id": "s65", "weight": 0.5, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {}},
        ],
        "max_portfolio_positions": 30,
    },
    # 50/50 with aggressive sizing overrides
    "exp1_aggressive_50_50": {
        "description": "s62+s65 equal weight (50/50), aggressive cap_pct + kelly",
        "strategies": [
            {"strategy_id": "s62", "weight": 0.5, "market": "perp",
             "max_positions": 15, "sizing_overrides": {
                 "cap_pct_floor": 0.05, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.5,
             }},
            {"strategy_id": "s65", "weight": 0.5, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {
                 "cap_pct_floor": 0.05, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.5,
             }},
        ],
        "max_portfolio_positions": 30,
    },
    # 70/30 favoring s62 (best performer), aggressive sizing
    "exp1_aggressive_70_30": {
        "description": "s62+s65 weight 70/30, aggressive cap_pct + kelly",
        "strategies": [
            {"strategy_id": "s62", "weight": 0.7, "market": "perp",
             "max_positions": 15, "sizing_overrides": {
                 "cap_pct_floor": 0.05, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.5,
             }},
            {"strategy_id": "s65", "weight": 0.3, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {
                 "cap_pct_floor": 0.05, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.5,
             }},
        ],
        "max_portfolio_positions": 30,
    },
    # s62 solo in this experiment (weight=1.0, default sizing as reference)
    "exp1_s62_solo_default": {
        "description": "s62 solo, default sizing (baseline for exp2)",
        "strategies": [
            {"strategy_id": "s62", "weight": 1.0, "market": "perp",
             "max_positions": 15, "sizing_overrides": {}},
        ],
        "max_portfolio_positions": 15,
    },
    # s65 solo (weight=1.0, default sizing)
    "exp1_s65_solo_default": {
        "description": "s65 solo, default sizing",
        "strategies": [
            {"strategy_id": "s65", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {}},
        ],
        "max_portfolio_positions": 15,
    },
}


# ---------------------------------------------------------------------------
# Experiment 2: s62 solo, fully optimized
# ---------------------------------------------------------------------------

EXP2_CONFIGS = {
    # s62 with fixed kelly override and cap_pct override (bypass ADV curve)
    "exp2_s62_optimized_v1": {
        "description": "s62 solo: kelly_mult_override=0.40, cap_pct_override=0.10, conc=0.15",
        "strategies": [
            {"strategy_id": "s62", "weight": 1.0, "market": "perp",
             "max_positions": 15, "sizing_overrides": {
                 "kelly_mult_override": 0.40,
                 "cap_pct_override": 0.10,
             }},
        ],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.15,
    },
    # s62 with ADV curve shape tuning (not bypassing, just shifting the curve up)
    "exp2_s62_optimized_v2": {
        "description": "s62 solo: raised ADV curve (floor=0.25/0.05, range=0.50/0.15), conc=0.15",
        "strategies": [
            {"strategy_id": "s62", "weight": 1.0, "market": "perp",
             "max_positions": 15, "sizing_overrides": {
                 "kelly_mult_floor": 0.25,
                 "kelly_mult_range": 0.50,
                 "cap_pct_floor": 0.05,
                 "cap_pct_range": 0.15,
             }},
        ],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.15,
    },
    # s62 with max aggressive (push rails to near limits)
    "exp2_s62_max_aggressive": {
        "description": "s62 solo: max kelly_mult_scale=2.0, cap_pct_scale=2.0, conc=0.20",
        "strategies": [
            {"strategy_id": "s62", "weight": 1.0, "market": "perp",
             "max_positions": 15, "sizing_overrides": {
                 "kelly_mult_scale": 2.0,
                 "cap_pct_scale": 2.0,
             }},
        ],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.20,
    },
}


# ---------------------------------------------------------------------------
# Experiment 3: s56+s57+s65 pool with per-strategy sizing
# ---------------------------------------------------------------------------

EXP3_CONFIGS = {
    # Baseline: current production config (all default sizing)
    "exp3_pool_baseline": {
        "description": "s56+s57+s65 pool, production config (default sizing)",
        "strategies": [
            {"strategy_id": "s56", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "exit_resolution": 5, "sizing_overrides": {}},
            {"strategy_id": "s57", "weight": 1.0, "market": "combined",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {}},
            {"strategy_id": "s65", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {}},
        ],
        "max_portfolio_positions": 50,
    },
    # Per-strategy tuned sizing: s56 gets higher cap (perp, BTC+alts, high ADV)
    "exp3_pool_tuned_v1": {
        "description": "s56+s57+s65 pool, per-strategy sizing (s56/s65 higher cap)",
        "strategies": [
            {"strategy_id": "s56", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "exit_resolution": 5, "sizing_overrides": {
                 "cap_pct_floor": 0.04, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.3,
             }},
            {"strategy_id": "s57", "weight": 1.0, "market": "combined",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {
                 # s57 is combined (spot+perp), keep moderate sizing
                 "cap_pct_floor": 0.03, "cap_pct_range": 0.12,
             }},
            {"strategy_id": "s65", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {
                 # s65 carry needs position size for funding income
                 "cap_pct_floor": 0.05, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.5,
             }},
        ],
        "max_portfolio_positions": 50,
    },
    # Aggressive: all strategies pushed to high sizing
    "exp3_pool_aggressive": {
        "description": "s56+s57+s65 pool, aggressive sizing all strategies",
        "strategies": [
            {"strategy_id": "s56", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "exit_resolution": 5, "sizing_overrides": {
                 "cap_pct_floor": 0.06, "cap_pct_range": 0.20,
                 "kelly_mult_scale": 1.8,
             }},
            {"strategy_id": "s57", "weight": 1.0, "market": "combined",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {
                 "cap_pct_floor": 0.05, "cap_pct_range": 0.15,
                 "kelly_mult_scale": 1.5,
             }},
            {"strategy_id": "s65", "weight": 1.0, "market": "perp",
             "max_positions": 15, "pump_filter_funding_zscore": 3.0,
             "sizing_overrides": {
                 "cap_pct_floor": 0.06, "cap_pct_range": 0.20,
                 "kelly_mult_scale": 2.0,
             }},
        ],
        "max_portfolio_positions": 50,
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _detect_strategy_type(strategy_id: str) -> str:
    """Detect if a strategy is per_token (Class A) or portfolio (Class B)."""
    import importlib.util
    strategies_dir = os.path.join(str(PROJECT_ROOT), "strategies")
    for fname in os.listdir(strategies_dir):
        if fname.startswith(strategy_id + "_") and fname.endswith(".py"):
            fpath = os.path.join(strategies_dir, fname)
            spec = importlib.util.spec_from_file_location(f"_detect_{strategy_id}", fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, 'STRATEGY_TYPE', 'per_token')
    return 'per_token'


def build_strategy_spec(d: dict) -> StrategySpec:
    """Build a StrategySpec from experiment config dict."""
    stype = _detect_strategy_type(d["strategy_id"])
    return StrategySpec(
        strategy_id=d["strategy_id"],
        weight=d.get("weight", 1.0),
        max_positions=d.get("max_positions", 15),
        market=d.get("market", "combined"),
        strategy_type=stype,
        pump_filter_funding_zscore=d.get("pump_filter_funding_zscore", 0.0),
        exit_resolution=d.get("exit_resolution", 0),
        sizing_overrides=d.get("sizing_overrides", {}),
    )


def run_experiment(
    name: str,
    exp_config: dict,
    months: int,
    capital: float,
    signal_cache: dict,
) -> dict:
    """Run a single experiment configuration and return metrics dict.

    Uses signal_cache to avoid recomputing signals for strategies already computed
    at the same months level.
    """
    print(f"\n{'='*70}")
    print(f"  EXPERIMENT: {name} ({months}mo, ${capital:,.0f})")
    print(f"  {exp_config['description']}")
    print(f"{'='*70}")

    # Build strategy specs
    strategy_specs = {}
    for s_dict in exp_config["strategies"]:
        spec = build_strategy_spec(s_dict)
        strategy_specs[spec.strategy_id] = spec

    # Build portfolio config
    config = make_base_config(
        strategies=list(strategy_specs.values()),
        capital=capital,
        max_portfolio_positions=exp_config.get("max_portfolio_positions", 40),
        concentration_limit=exp_config.get("concentration_limit", 0.10),
    )

    # Precompute signals (or reuse from cache)
    data_end = infer_data_end_date("perp")
    all_precomputed = {}
    for sid, spec in strategy_specs.items():
        cache_key = (sid, spec.market, months)
        if cache_key in signal_cache:
            print(f"  Reusing cached signals for {sid} ({spec.market}, {months}mo)")
            all_precomputed[sid] = signal_cache[cache_key]
        else:
            tokens = discover_tokens(spec.market)
            print(f"  Precomputing signals for {sid} ({len(tokens)} tokens, {spec.market})...")
            t0 = time.time()
            signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
            print(f"  Done: {len(signals)} tokens with signals ({time.time()-t0:.1f}s)")
            all_precomputed[sid] = signals
            signal_cache[cache_key] = signals

    # Run simulation
    print(f"  Simulating portfolio...")
    t0 = time.time()
    state = simulate_portfolio(all_precomputed, strategy_specs, config)
    sim_time = time.time() - t0
    n_trades = len(state.position_manager.closed_trades)
    print(f"  Done: {n_trades} trades ({sim_time:.1f}s)")

    # Compute metrics
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)

    # Print report
    strategy_ids = list(strategy_specs.keys())
    print_report(metrics, extra_info, capital, strategy_ids)

    # Compute capital utilization from equity snapshots
    cap_util_pct = 0.0
    if state.equity_snapshots:
        # We can approximate utilization as avg(locked_margin / equity)
        # But we don't track margin snapshots. Use a simpler proxy:
        # final_equity / capital tells us how much capital was actually working.
        # Better proxy: count bars where positions were open vs total bars.
        pass

    result = {
        "name": name,
        "description": exp_config["description"],
        "months": months,
        "capital": capital,
        "annual_return_pct": metrics.annualized_return_pct,
        "total_return_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "sharpe": metrics.sharpe_ratio,
        "calmar": metrics.calmar_ratio,
        "sortino": metrics.sortino_ratio,
        "total_trades": metrics.total_trades,
        "win_rate_pct": metrics.win_rate_pct,
        "profit_factor": metrics.profit_factor,
        "avg_trade_pnl": metrics.avg_trade_pnl,
        "final_equity": extra_info["final_equity"],
        "total_fees": extra_info["total_fees"],
        "total_funding": extra_info["total_funding"],
        "rejections_total": extra_info["rejections"]["total"],
        "strategy_attribution": extra_info["strategy_attribution"],
    }

    return result


def format_results_table(results: list[dict], title: str) -> str:
    """Format results as a markdown table."""
    lines = []
    lines.append(f"\n### {title}\n")
    lines.append("| Config | Months | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Avg PnL | Final Eq | Fees | Funding |")
    lines.append("|--------|--------|----------|--------|--------|--------|---------|--------|------|----|---------|----------|------|---------|")

    for r in results:
        lines.append(
            f"| {r['name']} | {r['months']} | "
            f"{r['annual_return_pct']:+.1f}% | "
            f"{r['max_dd_pct']:.1f}% | "
            f"{r['sharpe']:.2f} | "
            f"{r['calmar']:.2f} | "
            f"{r['sortino']:.2f} | "
            f"{r['total_trades']} | "
            f"{r['win_rate_pct']:.1f}% | "
            f"{r['profit_factor']:.2f} | "
            f"${r['avg_trade_pnl']:.0f} | "
            f"${r['final_equity']:,.0f} | "
            f"${r['total_fees']:,.0f} | "
            f"${r['total_funding']:,.0f} |"
        )

    return "\n".join(lines)


def write_results_markdown(all_results: dict, output_path: str):
    """Write all experiment results to a markdown file."""
    lines = []
    lines.append("# Portfolio Sizing Optimization Results")
    lines.append(f"\nDate: 2026-03-26")
    lines.append(f"\nCapital: ${CAPITAL:,.0f}")
    lines.append(f"\nLookback periods tested: {MONTHS_LIST}")
    lines.append("")
    lines.append("## SAFETY_RAILS Reference")
    lines.append("```")
    lines.append("kelly_mult_floor:    [0.05, 0.40]   (default: 0.15)")
    lines.append("kelly_mult_range:    [0.10, 0.80]   (default: 0.35)")
    lines.append("cap_pct_floor:       [0.005, 0.08]  (default: 0.02)")
    lines.append("cap_pct_range:       [0.02, 0.30]   (default: 0.10)")
    lines.append("adv_scaling_divisor: [1.0, 20.0]    (default: 5.0)")
    lines.append("kelly_mult_override: [0.05, 0.50]   (default: 0.0 = use curve)")
    lines.append("kelly_mult_scale:    [0.5, 2.0]     (default: 1.0)")
    lines.append("cap_pct_override:    [0.01, 0.15]   (default: 0.0 = use curve)")
    lines.append("cap_pct_scale:       [0.5, 2.0]     (default: 1.0)")
    lines.append("```")

    # Experiment 1
    lines.append("\n## Experiment 1: Best-of-Two (s62 + s65)")
    lines.append("\nRationale: s62 (+9.6%) and s65 (+4.9%) are the only positive post-MTM strategies.")
    lines.append("Test whether pairing them with aggressive sizing yields better risk-adjusted returns.")
    if "exp1" in all_results:
        lines.append(format_results_table(all_results["exp1"], "Exp1 Results"))

    # Experiment 2
    lines.append("\n## Experiment 2: s62 Solo, Fully Optimized")
    lines.append("\nRationale: s62 (conservative funding carry) is the best performer.")
    lines.append("Test whether bypassing the ADV curve with fixed overrides or scaling up improves it.")
    if "exp2" in all_results:
        lines.append(format_results_table(all_results["exp2"], "Exp2 Results"))

    # Experiment 3
    lines.append("\n## Experiment 3: s56+s57+s65 Pool with Per-Strategy Sizing")
    lines.append("\nRationale: This is the existing s58+s65 production pool (s56+s57+s65).")
    lines.append("Test per-strategy sizing tuned to each strategy's ADV profile.")
    if "exp3" in all_results:
        lines.append(format_results_table(all_results["exp3"], "Exp3 Results"))

    # Summary
    lines.append("\n## Summary & Recommendations")
    lines.append("")

    # Find best config across all experiments
    all_flat = []
    for exp_name, results in all_results.items():
        all_flat.extend(results)

    if all_flat:
        # Best by Sharpe (12mo)
        results_12mo = [r for r in all_flat if r["months"] == 12]
        results_3mo = [r for r in all_flat if r["months"] == 3]

        if results_12mo:
            best_sharpe_12 = max(results_12mo, key=lambda r: r["sharpe"])
            best_return_12 = max(results_12mo, key=lambda r: r["annual_return_pct"])
            best_calmar_12 = max(results_12mo, key=lambda r: r["calmar"])

            lines.append("### 12-Month Lookback")
            lines.append(f"- **Best Sharpe:** {best_sharpe_12['name']} (Sharpe={best_sharpe_12['sharpe']:.2f}, Ann.Ret={best_sharpe_12['annual_return_pct']:+.1f}%)")
            lines.append(f"- **Best Return:** {best_return_12['name']} (Ann.Ret={best_return_12['annual_return_pct']:+.1f}%, Sharpe={best_return_12['sharpe']:.2f})")
            lines.append(f"- **Best Calmar:** {best_calmar_12['name']} (Calmar={best_calmar_12['calmar']:.2f}, MaxDD={best_calmar_12['max_dd_pct']:.1f}%)")

        if results_3mo:
            best_sharpe_3 = max(results_3mo, key=lambda r: r["sharpe"])
            best_return_3 = max(results_3mo, key=lambda r: r["annual_return_pct"])

            lines.append("\n### 3-Month Lookback")
            lines.append(f"- **Best Sharpe:** {best_sharpe_3['name']} (Sharpe={best_sharpe_3['sharpe']:.2f}, Ann.Ret={best_sharpe_3['annual_return_pct']:+.1f}%)")
            lines.append(f"- **Best Return:** {best_return_3['name']} (Ann.Ret={best_return_3['annual_return_pct']:+.1f}%, Sharpe={best_return_3['sharpe']:.2f})")

    lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  Results written to {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  PORTFOLIO SIZING OPTIMIZATION RESEARCH")
    print("  Testing per-strategy sizing overrides on positive strategies")
    print("=" * 70)

    all_results = {"exp1": [], "exp2": [], "exp3": []}
    signal_cache = {}  # (strategy_id, market, months) -> signals

    total_start = time.time()

    # Run experiments across both lookback periods
    for months in MONTHS_LIST:
        print(f"\n{'#'*70}")
        print(f"  LOOKBACK: {months} MONTHS")
        print(f"{'#'*70}")

        # Experiment 1
        for name, config in EXP1_CONFIGS.items():
            try:
                result = run_experiment(name, config, months, CAPITAL, signal_cache)
                all_results["exp1"].append(result)
            except Exception as e:
                print(f"  ERROR in {name}: {e}")
                traceback.print_exc()

        # Experiment 2
        for name, config in EXP2_CONFIGS.items():
            try:
                result = run_experiment(name, config, months, CAPITAL, signal_cache)
                all_results["exp2"].append(result)
            except Exception as e:
                print(f"  ERROR in {name}: {e}")
                traceback.print_exc()

        # Experiment 3
        for name, config in EXP3_CONFIGS.items():
            try:
                result = run_experiment(name, config, months, CAPITAL, signal_cache)
                all_results["exp3"].append(result)
            except Exception as e:
                print(f"  ERROR in {name}: {e}")
                traceback.print_exc()

    total_time = time.time() - total_start
    print(f"\n  Total runtime: {total_time:.0f}s ({total_time/60:.1f}min)")

    # Write results
    output_path = os.path.join(str(PROJECT_ROOT), "research", "portfolio_sizing_optimization_results.md")
    write_results_markdown(all_results, output_path)

    # Also dump raw JSON for further analysis
    json_path = os.path.join(str(PROJECT_ROOT), "research", "portfolio_sizing_optimization_results.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  Raw JSON saved to {json_path}")


if __name__ == "__main__":
    main()
