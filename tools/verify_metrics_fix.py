"""Verification script: compare old vs new metrics after Phase 1-4 fixes.

Runs s56 and s60 with:
  - Baseline (no filters, old-style metrics for comparison)
  - With ADV sizing + graduated pump filter (new native filters)
Reports both daily and hourly MaxDD, corrected annualization.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.simulator import simulate_portfolio
from v4.signals import precompute_strategy_signals, TokenSignals, discover_tokens
from v4.data_loader import infer_data_end_date
from v4.report import compute_portfolio_metrics
from v4.metrics import compute_metrics


def run_one(strategy_id, months, spec_overrides=None, config_overrides=None,
            end_date=None, market="perp"):
    """Run backtest and return (metrics, extra_info, state)."""
    stype = "per_token"

    spec_kwargs = dict(
        strategy_id=strategy_id,
        weight=1.0,
        max_positions=15,
        market=market,
        strategy_type=stype,
    )
    if spec_overrides:
        spec_kwargs.update(spec_overrides)
    spec = StrategySpec(**spec_kwargs)

    config_kwargs = dict(
        strategies=[spec],
        capital=200_000,
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        exchange="binance",
        seed=42,
        train_bars=8760,
    )
    if config_overrides:
        config_kwargs.update(config_overrides)
    config = PortfolioConfig(**config_kwargs)

    tokens = discover_tokens(spec.market)
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    all_signals = {strategy_id: signals}
    strategy_specs = {strategy_id: spec}

    state = simulate_portfolio(all_signals, strategy_specs, config)
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, config.capital)
    return metrics, extra_info, state


def main():
    MONTHS = int(os.environ.get("BACKTEST_MONTHS", "24"))
    end_date = infer_data_end_date("perp")
    print(f"  Data end date: {end_date}")
    print(f"  Backtest months: {MONTHS}")

    # Full filter specs for s56 and s60
    s56_filters = {
        "adv_sizing_enabled": True, "adv_sizing_base": 75_000_000, "adv_sizing_floor": 0.20,
        "pump_grad_enabled": True, "pump_grad_window": 168,
        "pump_grad_t1": 0.15, "pump_grad_t2": 0.25, "pump_grad_t3": 0.40,
    }
    s60_filters = {
        "adv_sizing_enabled": True, "adv_sizing_base": 100_000_000, "adv_sizing_floor": 0.20,
        "pump_grad_enabled": True, "pump_grad_window": 168,
        "pump_grad_t1": 0.25, "pump_grad_t2": 0.40, "pump_grad_t3": 0.60,
    }

    configs = {
        # Baseline: no filters, no equity cap
        "s56_baseline": ("s56", {}, {}),
        "s60_baseline": ("s60", {}, {}),
        # Full filters + equity cap $2M (realistic production config)
        "s56_filters_cap2m": ("s56", s56_filters, {"max_sizing_equity": 2_000_000}),
        "s60_filters_cap2m": ("s60", s60_filters, {"max_sizing_equity": 2_000_000}),
    }

    results = {}
    for label, (sid, spec_ov, config_ov) in configs.items():
        print(f"\n{'='*60}")
        print(f"  Running: {label}")
        print(f"{'='*60}")
        t0 = time.time()
        metrics, extra_info, state = run_one(sid, MONTHS, spec_ov, config_ov,
                                             end_date=end_date)
        elapsed = time.time() - t0

        r = {
            "label": label,
            "strategy": sid,
            "sharpe": round(metrics.sharpe_ratio, 3),
            "sortino": round(metrics.sortino_ratio, 3),
            "calmar": round(metrics.calmar_ratio, 3),
            "max_dd_daily_pct": round(metrics.max_drawdown_pct, 2),
            "max_dd_hourly_pct": round(metrics.max_dd_hourly_pct, 2),
            "total_return_pct": round(metrics.total_return_pct, 1),
            "annualized_return_pct": round(metrics.annualized_return_pct, 1),
            "n_trades": metrics.total_trades,
            "win_rate_pct": round(metrics.win_rate_pct, 1),
            "profit_factor": round(metrics.profit_factor, 2),
            "final_equity": round(extra_info["final_equity"], 0),
            "total_fees": round(extra_info["total_fees"], 0),
            "total_funding": round(extra_info["total_funding"], 0),
            "elapsed_s": round(elapsed, 1),
        }
        results[label] = r

        print(f"  Trades:          {r['n_trades']}")
        print(f"  Win Rate:        {r['win_rate_pct']:.1f}%")
        print(f"  Sharpe:          {r['sharpe']}")
        print(f"  Sortino:         {r['sortino']}")
        print(f"  Calmar:          {r['calmar']}")
        print(f"  Max DD (daily):  {r['max_dd_daily_pct']:.1f}%")
        print(f"  Max DD (hourly): {r['max_dd_hourly_pct']:.1f}%")
        print(f"  Return:          {r['total_return_pct']:.1f}%")
        print(f"  Ann. Return:     {r['annualized_return_pct']:.1f}%")
        print(f"  Profit Factor:   {r['profit_factor']}")
        print(f"  Final Equity:    ${r['final_equity']:,.0f}")
        print(f"  Total Fees:      ${r['total_fees']:,.0f}")
        print(f"  Total Funding:   ${r['total_funding']:,.0f}")
        print(f"  Elapsed:         {elapsed:.1f}s")

    # Summary comparison table
    print(f"\n{'='*110}")
    print("  COMPARISON TABLE")
    print(f"{'='*110}")
    print(f"  {'Config':<25s} {'Trades':>6s} {'WR%':>5s} {'Sharpe':>7s} {'Sortino':>8s} {'Calmar':>8s} {'DD(h)':>7s} {'Return':>8s} {'Ann.Ret':>8s} {'PF':>5s} {'FinalEq':>12s}")
    print(f"  {'-'*25} {'-'*6} {'-'*5} {'-'*7} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*5} {'-'*12}")
    for label, r in results.items():
        print(f"  {label:<25s} {r['n_trades']:>6d} {r['win_rate_pct']:>4.0f}% {r['sharpe']:>7.2f} {r['sortino']:>8.2f} {r['calmar']:>8.2f} "
              f"{r['max_dd_hourly_pct']:>6.1f}% {r['total_return_pct']:>7.1f}% {r['annualized_return_pct']:>7.1f}% "
              f"{r['profit_factor']:>5.2f} ${r['final_equity']:>11,.0f}")

    # Save results
    out_path = "results/v4/verify_metrics_fix.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
