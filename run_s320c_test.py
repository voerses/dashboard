#!/usr/bin/env python3
"""Run s320 Option C backtest: lower min_position_usd to allow overlay-scaled entries.

Usage:
    python run_s320c_test.py [--min-pos 10] [--months 12]
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec, SizingDefaults, resolve_sizing
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report, save_results


def main():
    parser = argparse.ArgumentParser(description="S320 Option C: low min_position_usd backtest")
    parser.add_argument("--config", type=str, default="configs/s320c_test.json",
                        help="Path to JSON config")
    parser.add_argument("--months", type=int, default=12,
                        help="Lookback months (default: 12)")
    parser.add_argument("--min-pos", type=float, default=None,
                        help="Override min_position_usd from config")
    args = parser.parse_args()

    # Load config JSON
    config_path = PROJECT_ROOT / args.config
    with open(config_path) as f:
        cfg_data = json.load(f)

    capital = cfg_data.get("capital", 200_000)
    min_pos = args.min_pos if args.min_pos is not None else cfg_data.get("min_position_usd", 200.0)

    # Build strategy specs
    strategy_specs = {}
    for s in cfg_data["strategies"]:
        spec = StrategySpec.from_dict(s)
        strategy_specs[spec.strategy_id] = spec

    # Resolve sizing defaults with overrides
    base_sizing = SizingDefaults()
    for sid, spec in strategy_specs.items():
        if spec.sizing_overrides:
            resolved = resolve_sizing(base_sizing, spec.sizing_overrides)
            print(f"  {sid} sizing overrides applied: kelly_mult_override={resolved.kelly_mult_override}, "
                  f"spot_max_equity_pct={resolved.spot_max_equity_pct}")

    # Build portfolio config
    config = PortfolioConfig(
        strategies=list(strategy_specs.values()),
        capital=capital,
        min_position_usd=min_pos,
        max_portfolio_positions=cfg_data.get("max_portfolio_positions", 40),
        concentration_limit=cfg_data.get("concentration_limit", 0.10),
        adv_cap_pct=cfg_data.get("adv_cap_pct", 0.05),
        exchange=cfg_data.get("exchange", "binance"),
        seed=cfg_data.get("seed", 42),
    )

    print("=" * 70)
    print("  S320 OPTION C BACKTEST — Low min_position_usd")
    print("=" * 70)
    print(f"  Capital:          ${capital:,.0f}")
    print(f"  min_position_usd: ${min_pos:.0f}  (default=200)")
    print(f"  Months:           {args.months}")
    print(f"  Strategies:       {list(strategy_specs.keys())}")
    for sid, spec in strategy_specs.items():
        print(f"    {sid}: market={spec.market}, max_pos={spec.max_positions}, "
              f"overrides={spec.sizing_overrides}")
    print("=" * 70)

    # Discover tokens and precompute signals
    data_end = infer_data_end_date("spot")
    print(f"  Data End: {data_end.strftime('%Y-%m-%d %H:%M')}")

    all_precomputed = {}
    for sid, spec in strategy_specs.items():
        # For s320, only BTC — but discover_tokens will find what's available
        tokens = discover_tokens(spec.market)
        # Filter to BTC only for s320
        if "BTC" in tokens:
            tokens = ["BTC"]
        print(f"\n  Precomputing signals for {sid} ({len(tokens)} tokens, {spec.market})...")
        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, args.months, end_date=data_end)
        print(f"  Done: {len(signals)} tokens with signals ({time.time()-t0:.1f}s)")
        all_precomputed[sid] = signals

    # Run simulation
    print(f"\n  Simulating portfolio (capital=${capital:,.0f}, min_pos=${min_pos:.0f})...")
    t0 = time.time()
    state = simulate_portfolio(all_precomputed, strategy_specs, config)
    sim_time = time.time() - t0
    n_trades = len(state.position_manager.closed_trades)
    print(f"  Done: {n_trades} trades ({sim_time:.1f}s)")

    # Compute metrics
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)

    # Print report
    print_report(metrics, extra_info, capital, list(strategy_specs.keys()))

    # Save results
    label = f"s320c_minpos{int(min_pos)}_{args.months}mo"
    save_results(metrics, extra_info, state.position_manager.closed_trades,
                 "results/s320c", label, equity_curve=eq_daily)

    # Summary
    rej = extra_info["rejections"]
    total_candidates = metrics.total_trades + rej["total"]
    rej_pct = rej["total"] / max(total_candidates, 1) * 100
    total_return = (extra_info["final_equity"] / capital - 1) * 100

    print("\n" + "=" * 70)
    print("  OPTION C SUMMARY")
    print("=" * 70)
    print(f"  min_position_usd: ${min_pos:.0f}")
    print(f"  Total Return:     {total_return:+.1f}%")
    print(f"  Sharpe Ratio:     {metrics.sharpe_ratio:.2f}")
    print(f"  Max Drawdown:     {metrics.max_drawdown_pct:.1f}%")
    print(f"  Trade Count:      {metrics.total_trades}")
    print(f"  Rejections:       {rej['total']} / {total_candidates} ({rej_pct:.1f}%)")
    print(f"  Rejection Detail: {json.dumps(rej, indent=2)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
