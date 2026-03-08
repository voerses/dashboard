"""CLI wiring for signal portfolio system."""

import argparse
import json
import os
import time
from dataclasses import asdict

from .config import SignalPortfolioConfig
from .catalog_loader import load_all_profiles, discover_common_features
from .token_clustering import (
    cluster_tokens, characterize_group, print_cluster_summary,
)
from .strategy_generator import (
    GroupSignalConfig, build_group_config, export_strategy_file,
)
from .optimizer import optimize_group, OptimizationResult
from .portfolio_builder import run_portfolio_backtest


def _build_group_configs(profiles, groups, common_features, config):
    """Build GroupSignalConfig for each cluster."""
    group_configs = []
    for gid in sorted(groups.keys()):
        tokens = groups[gid]
        # Collect all filtered signals from the group's tokens
        group_signals = []
        seen = set()
        for token in tokens:
            p = profiles.get(token)
            if not p:
                continue
            for s in p.filtered_signals:
                key = (s.feature, s.horizon)
                if key not in seen:
                    group_signals.append(s)
                    seen.add(key)

        gc = build_group_config(gid, tokens, group_signals)
        group_configs.append(gc)

    return group_configs


def run_cluster(config: SignalPortfolioConfig):
    """Run clustering step only."""
    profiles = load_all_profiles(config)
    if not profiles:
        print("No profiles loaded.")
        return

    common_features = discover_common_features(profiles)
    groups = cluster_tokens(profiles, config)
    print_cluster_summary(groups, profiles, common_features)

    # Save clustering results
    os.makedirs(config.output_dir, exist_ok=True)
    cluster_output = {}
    for gid, tokens in groups.items():
        info = characterize_group(tokens, profiles, common_features)
        cluster_output[str(gid)] = {
            'tokens': tokens,
            'n_tokens': len(tokens),
            'dominant_signals': info['dominant_signals'],
        }

    output_path = os.path.join(config.output_dir, 'clusters.json')
    with open(output_path, 'w') as f:
        json.dump(cluster_output, f, indent=2)
    print(f"\nCluster results saved to {output_path}")

    return profiles, groups, common_features


def run_optimize(config: SignalPortfolioConfig):
    """Run steps 1-6: load, cluster, optimize."""
    profiles = load_all_profiles(config)
    if not profiles:
        return

    common_features = discover_common_features(profiles)
    groups = cluster_tokens(profiles, config)
    print_cluster_summary(groups, profiles, common_features)

    group_configs = _build_group_configs(profiles, groups, common_features, config)

    print(f"\n{'='*60}")
    print(f"Optimizing {len(group_configs)} groups...")
    print(f"{'='*60}")

    opt_results = {}
    for gc in group_configs:
        result = optimize_group(gc, profiles, config)
        opt_results[gc.group_id] = result

    # Save optimization results
    os.makedirs(config.output_dir, exist_ok=True)
    opt_output = {}
    for gid, result in opt_results.items():
        opt_output[str(gid)] = {
            'best_params': result.best_params,
            'train_annual_return': result.train_annual_return,
            'val_annual_return': result.val_annual_return,
            'test_annual_return': result.test_annual_return,
            'train_max_dd': result.train_max_dd,
            'val_max_dd': result.val_max_dd,
            'test_max_dd': result.test_max_dd,
            'n_param_combos_tested': result.n_param_combos_tested,
            'representative_tokens': result.representative_tokens,
        }

    output_path = os.path.join(config.output_dir, 'optimization.json')
    with open(output_path, 'w') as f:
        json.dump(opt_output, f, indent=2)
    print(f"\nOptimization results saved to {output_path}")

    return profiles, groups, common_features, group_configs, opt_results


def run_backtest(config: SignalPortfolioConfig):
    """Run full pipeline: load, cluster, optimize, backtest."""
    result = run_optimize(config)
    if result is None:
        return

    profiles, groups, common_features, group_configs, opt_results = result

    # Run portfolio backtest
    portfolio_result = run_portfolio_backtest(
        group_configs, config, opt_results)

    if not portfolio_result:
        return

    # Save results
    os.makedirs(config.output_dir, exist_ok=True)
    metrics = portfolio_result.get('metrics')
    if metrics:
        metrics_dict = asdict(metrics)
        output_path = os.path.join(config.output_dir, 'portfolio_metrics.json')
        with open(output_path, 'w') as f:
            json.dump(metrics_dict, f, indent=2)
        print(f"\nPortfolio metrics saved to {output_path}")

    # Save equity curve
    equity = portfolio_result.get('portfolio_equity')
    if equity is not None and len(equity) > 0:
        eq_path = os.path.join(config.output_dir, 'portfolio_equity.csv')
        equity.to_csv(eq_path)
        print(f"Equity curve saved to {eq_path}")


def run_export_strategies(config: SignalPortfolioConfig):
    """Export group strategies as standalone .py files."""
    profiles = load_all_profiles(config)
    if not profiles:
        return

    common_features = discover_common_features(profiles)
    groups = cluster_tokens(profiles, config)
    group_configs = _build_group_configs(profiles, groups, common_features, config)

    # Load optimized params if available
    opt_path = os.path.join(config.output_dir, 'optimization.json')
    if os.path.exists(opt_path):
        with open(opt_path) as f:
            opt_data = json.load(f)
        for gc in group_configs:
            opt = opt_data.get(str(gc.group_id))
            if opt and opt.get('best_params'):
                params = opt['best_params']
                gc.entry_threshold = params.get('entry_threshold', gc.entry_threshold)
                gc.min_vol_ratio = params.get('min_vol_ratio', gc.min_vol_ratio)
                gc.stop_mult = params.get('stop_mult', gc.stop_mult)
                gc.trail_mult = params.get('trail_mult', gc.trail_mult)
                gc.min_hold = params.get('min_hold', gc.min_hold)
        print(f"Loaded optimized params from {opt_path}")

    # Export strategies
    strategies_dir = os.path.join(
        os.path.dirname(__file__), '..', '..', 'strategies')
    os.makedirs(strategies_dir, exist_ok=True)

    for gc in group_configs:
        filename = f's_signal_group_{gc.group_id}.py'
        output_path = os.path.join(strategies_dir, filename)
        export_strategy_file(gc, output_path)
        print(f"Exported: {filename} ({len(gc.tokens)} tokens)")

    print(f"\n{len(group_configs)} strategy files exported to strategies/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Signal-Driven Portfolio Strategy System')

    # Mode selection
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--cluster', action='store_true',
                      help='Run clustering only (step 2)')
    mode.add_argument('--optimize', action='store_true',
                      help='Run clustering + optimization (steps 1-6)')
    mode.add_argument('--backtest', action='store_true',
                      help='Run full pipeline (steps 1-7)')
    mode.add_argument('--export-strategies', action='store_true',
                      help='Export group strategies as .py files')

    # Configuration
    parser.add_argument('--signal-dir', default='outputs/signal_discovery',
                        help='Directory with signal discovery outputs')
    parser.add_argument('--data-dir', default='data',
                        help='Directory with market data')
    parser.add_argument('--output-dir', default='outputs/signal_portfolio',
                        help='Output directory')
    parser.add_argument('--market', default='perp',
                        help='Market type (spot or perp)')
    parser.add_argument('--exchange', default='binance',
                        help='Exchange for fee structure')
    parser.add_argument('--capital', type=float, default=200_000,
                        help='Starting capital')
    parser.add_argument('--workers', type=int, default=4,
                        help='Parallel workers')
    parser.add_argument('--allocation', default='inverse_vol',
                        choices=['equal', 'ic_weighted', 'inverse_vol', 'max_return'],
                        help='Capital allocation method')
    parser.add_argument('--min-clusters', type=int, default=3,
                        help='Minimum number of token clusters')
    parser.add_argument('--max-clusters', type=int, default=6,
                        help='Maximum number of token clusters')

    return parser


def main(args=None):
    parser = build_parser()
    parsed = parser.parse_args(args)

    config = SignalPortfolioConfig(
        signal_dir=parsed.signal_dir,
        data_dir=parsed.data_dir,
        output_dir=parsed.output_dir,
        market=parsed.market,
        exchange=parsed.exchange,
        capital=parsed.capital,
        workers=parsed.workers,
        allocation_method=parsed.allocation,
        min_clusters=parsed.min_clusters,
        max_clusters=parsed.max_clusters,
    )

    t0 = time.time()

    if parsed.cluster:
        run_cluster(config)
    elif parsed.optimize:
        run_optimize(config)
    elif parsed.backtest:
        run_backtest(config)
    elif parsed.export_strategies:
        run_export_strategies(config)

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.0f}s")
