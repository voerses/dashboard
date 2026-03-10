#!/usr/bin/env python3
"""V4 Portfolio Backtest — CLI entry point and orchestrator.

Usage:
    python v4/portfolio_backtest.py --strategy s30 --months 12
    python v4/portfolio_backtest.py --strategy s30,s32 --months 6 --capital 200000
    python v4/portfolio_backtest.py --strategy s30 --capital 100000,500000,1000000,5000000  # sweep
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v3"))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report, save_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V4 Portfolio Backtest Engine")
    parser.add_argument("--strategy", type=str, required=True,
                        help="Comma-separated strategy IDs (e.g. s30,s32)")
    parser.add_argument("--months", type=int, default=12,
                        help="Lookback period in months (default: 12)")
    parser.add_argument("--capital", type=str, default="200000",
                        help="Initial capital (single or comma-separated for sweep)")
    parser.add_argument("--exchange", type=str, default="binance",
                        help="Exchange (default: binance)")
    parser.add_argument("--concentration", type=float, default=0.10,
                        help="Per-token concentration limit (default: 0.10)")
    parser.add_argument("--adv-cap", type=float, default=0.05,
                        help="ADV hard cap (default: 0.05)")
    parser.add_argument("--max-positions", type=int, default=15,
                        help="Per-strategy max positions (default: 15)")
    parser.add_argument("--max-portfolio-positions", type=int, default=40,
                        help="Portfolio-wide max positions (default: 40)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--output", type=str, default="results/v4",
                        help="Output directory for JSON results")
    parser.add_argument("--market", type=str, default=None,
                        help="Override market type for all strategies (spot/perp/combined)")
    return parser.parse_args()


def run_backtest(
    strategy_ids: list[str],
    months: int,
    capital: float,
    config: PortfolioConfig,
    precomputed_signals: dict | None = None,
    end_date: pd.Timestamp | None = None,
) -> tuple:
    """Run a single backtest with given parameters.

    Returns (metrics, extra_info, trades, state)
    """
    strategy_specs = {}
    for sid in strategy_ids:
        spec = StrategySpec(
            strategy_id=sid,
            weight=1.0 / len(strategy_ids),
            max_positions=config.max_portfolio_positions // len(strategy_ids) if len(strategy_ids) > 1 else config.max_portfolio_positions,
            market=config._market_overrides.get(sid, "combined") if hasattr(config, '_market_overrides') else "combined",
        )
        # Use the per-strategy max_positions from config if only 1 strategy
        if len(strategy_ids) == 1:
            spec.max_positions = min(spec.max_positions, config.max_portfolio_positions)
        strategy_specs[sid] = spec

    # Precompute signals (or reuse)
    if precomputed_signals is None:
        precomputed_signals = {}
        for sid, spec in strategy_specs.items():
            tokens = discover_tokens(spec.market)
            print(f"\n  Precomputing signals for {sid} ({len(tokens)} tokens, {spec.market})...")
            t0 = time.time()
            signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
            print(f"  Done: {len(signals)} tokens with signals ({time.time()-t0:.1f}s)")
            precomputed_signals[sid] = signals

    # Run simulation
    config_run = PortfolioConfig(
        strategies=list(strategy_specs.values()),
        capital=capital,
        max_portfolio_positions=config.max_portfolio_positions,
        concentration_limit=config.concentration_limit,
        adv_cap_pct=config.adv_cap_pct,
        min_position_usd=config.min_position_usd,
        exchange=config.exchange,
        base_spread_bps=config.base_spread_bps,
        impact_coeff=config.impact_coeff,
        seed=config.seed,
        train_bars=config.train_bars,
        recal_bars=config.recal_bars,
        purge_bars=config.purge_bars,
    )

    print(f"\n  Simulating portfolio (capital=${capital:,.0f})...")
    t0 = time.time()
    state = simulate_portfolio(precomputed_signals, strategy_specs, config_run)
    print(f"  Done: {len(state.position_manager.closed_trades)} trades ({time.time()-t0:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)
    return metrics, extra_info, state.position_manager.closed_trades, precomputed_signals


def main():
    args = parse_args()

    strategy_ids = [s.strip() for s in args.strategy.split(",")]
    capital_levels = [float(c.strip()) for c in args.capital.split(",")]
    is_sweep = len(capital_levels) > 1

    # Build config
    market = args.market or "combined"
    config = PortfolioConfig(
        exchange=args.exchange,
        concentration_limit=args.concentration,
        adv_cap_pct=args.adv_cap,
        max_portfolio_positions=args.max_portfolio_positions,
        seed=args.seed,
    )

    print("=" * 70)
    print("  V4 PORTFOLIO BACKTEST ENGINE")
    print("=" * 70)
    print(f"  Strategies: {', '.join(strategy_ids)}")
    print(f"  Months:     {args.months}")
    print(f"  Capital:    {', '.join(f'${c:,.0f}' for c in capital_levels)}")
    print(f"  Exchange:   {args.exchange}")
    print(f"  Market:     {market}")
    print(f"  Seed:       {args.seed}")

    # Infer data end date for deterministic backtesting
    data_end = infer_data_end_date(market)
    print(f"  Data End:   {data_end.strftime('%Y-%m-%d %H:%M')}")

    # Precompute signals once (they don't depend on capital)
    strategy_specs_for_precompute = {}
    for sid in strategy_ids:
        spec = StrategySpec(
            strategy_id=sid,
            weight=1.0 / len(strategy_ids),
            max_positions=args.max_positions,
            market=market,
        )
        strategy_specs_for_precompute[sid] = spec

    all_precomputed = {}
    for sid, spec in strategy_specs_for_precompute.items():
        tokens = discover_tokens(spec.market)
        print(f"\n  Precomputing signals for {sid} ({len(tokens)} tokens, {spec.market})...")
        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, args.months, end_date=data_end)
        print(f"  Done: {len(signals)} tokens with signals ({time.time()-t0:.1f}s)")
        all_precomputed[sid] = signals

    # Run simulation(s)
    all_results = []
    for capital in capital_levels:
        config_run = PortfolioConfig(
            strategies=[strategy_specs_for_precompute[sid] for sid in strategy_ids],
            capital=capital,
            max_portfolio_positions=args.max_portfolio_positions,
            concentration_limit=args.concentration,
            adv_cap_pct=args.adv_cap,
            min_position_usd=config.min_position_usd,
            exchange=args.exchange,
            base_spread_bps=config.base_spread_bps,
            impact_coeff=config.impact_coeff,
            seed=args.seed,
            train_bars=config.train_bars,
            recal_bars=config.recal_bars,
            purge_bars=config.purge_bars,
        )

        print(f"\n  Simulating portfolio (capital=${capital:,.0f})...")
        t0 = time.time()
        state = simulate_portfolio(all_precomputed, strategy_specs_for_precompute, config_run)
        sim_time = time.time() - t0
        print(f"  Done: {len(state.position_manager.closed_trades)} trades ({sim_time:.1f}s)")

        metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)
        all_results.append((capital, metrics, extra_info, state))

        print_report(metrics, extra_info, capital, strategy_ids)

        # Save results
        label = f"{'_'.join(strategy_ids)}_{args.months}mo_{int(capital/1000)}k"
        save_results(metrics, extra_info, state.position_manager.closed_trades,
                     args.output, label, equity_curve=eq_daily)

    # Capital sweep comparison table
    if is_sweep:
        print("\n" + "=" * 70)
        print("  CAPITAL SWEEP COMPARISON")
        print("=" * 70)
        print(f"  {'Capital':>12s}  {'Return':>8s}  {'Sharpe':>7s}  {'MaxDD':>7s}  {'Trades':>7s}  {'Rej%':>6s}")
        print(f"  {'-'*12}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}")
        for capital, metrics, extra_info, state in all_results:
            total_return = (extra_info["final_equity"] / capital - 1) * 100
            rej = extra_info["rejections"]
            total_candidates = metrics.total_trades + rej["total"]
            rej_pct = rej["total"] / max(total_candidates, 1) * 100
            print(f"  ${capital:>11,.0f}  {total_return:>+7.1f}%  {metrics.sharpe_ratio:>7.2f}  "
                  f"{metrics.max_drawdown_pct:>6.1f}%  {metrics.total_trades:>7d}  {rej_pct:>5.1f}%")
        print("=" * 70)


if __name__ == "__main__":
    main()
