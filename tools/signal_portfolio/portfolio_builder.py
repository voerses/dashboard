"""Multi-group portfolio construction and backtest."""

import os
import sys
import time
import json
import importlib.util
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple, Callable

from .config import SignalPortfolioConfig
from .catalog_loader import TokenSignalProfile
from .strategy_generator import GroupSignalConfig, create_group_strategy, build_group_config
from .optimizer import OptimizationResult


def _load_v3_module(name: str):
    """Load a v3 module by name."""
    full_name = f'v3_{name}'
    if full_name in sys.modules:
        return sys.modules[full_name]
    _v3_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'v3')
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(_v3_dir, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _get_token_trades_with_fn(ticker: str,
                                strategy_fn: Callable,
                                data_dir: str,
                                market: str,
                                capital: float,
                                exchange: str = 'binance') -> Optional[List[Dict]]:
    """Get timestamped trades for a single token using a strategy function.

    Same logic as v3/portfolio.py:_get_token_trades_single but accepts
    a callable instead of a file path.
    """
    engine_mod = _load_v3_module('engine')
    Engine = engine_mod.Engine

    h1_path = os.path.join(data_dir, market, f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(h1_path):
        return None

    df_1h = pd.read_parquet(h1_path)
    if len(df_1h) < 2000:
        return None

    engine = Engine(data_dir=data_dir, market=market, capital=capital, exchange=exchange)
    ctx = engine._build_context(ticker, df_1h)
    if ctx is None:
        return None

    result = strategy_fn(ctx)
    n = len(ctx.ind_1h['close'])

    # Apply walk-forward masking (same as portfolio.py)
    train_bars = 365 * 24
    recal_bars = 90 * 24
    purge_bars = 5 * 24

    if n <= train_bars + purge_bars:
        return None

    masked_entry = result.entry_mask.copy()
    masked_entry[:train_bars] = False

    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars

    result.entry_mask = masked_entry
    trades, final_equity = engine._simulate(ctx, result)

    if not trades:
        return None

    idx = ctx.idx_1h
    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]

    return trades


def extract_group_trades(group_config: GroupSignalConfig,
                          config: SignalPortfolioConfig) -> Dict[str, List[Dict]]:
    """Extract trades for all tokens in a group using the group's strategy."""
    strategy_fn = create_group_strategy(group_config)
    all_trades = {}

    for token in group_config.tokens:
        try:
            trades = _get_token_trades_with_fn(
                token, strategy_fn,
                config.data_dir, config.market, config.capital, config.exchange)
            if trades:
                all_trades[token] = trades
        except Exception as e:
            print(f"    Error processing {token}: {e}")

    return all_trades


def compute_group_allocation(group_configs: List[GroupSignalConfig],
                              group_trades: Dict[int, Dict[str, List[Dict]]],
                              method: str = 'inverse_vol') -> Dict[int, float]:
    """Compute capital allocation weights across groups.

    Methods:
        equal: 1/N equal weighting
        ic_weighted: proportional to average |IC| of group signals
        inverse_vol: proportional to 1/volatility of group returns
        max_return: proportional to historical return
    """
    n_groups = len(group_configs)
    if n_groups == 0:
        return {}

    if method == 'equal':
        weight = 1.0 / n_groups
        return {gc.group_id: weight for gc in group_configs}

    if method == 'ic_weighted':
        ic_sums = {}
        for gc in group_configs:
            avg_ic = np.mean([abs(s.mean_ic) for s in gc.signals]) if gc.signals else 0.0
            ic_sums[gc.group_id] = avg_ic
        total = sum(ic_sums.values())
        if total <= 0:
            return {gc.group_id: 1.0 / n_groups for gc in group_configs}
        return {gid: v / total for gid, v in ic_sums.items()}

    if method == 'inverse_vol':
        inv_vols = {}
        for gc in group_configs:
            trades = group_trades.get(gc.group_id, {})
            all_pnls = []
            for token_trades in trades.values():
                for t in token_trades:
                    all_pnls.append(t['pnl'])
            if all_pnls:
                vol = np.std(all_pnls)
                inv_vols[gc.group_id] = 1.0 / max(vol, 1.0)
            else:
                inv_vols[gc.group_id] = 1.0
        total = sum(inv_vols.values())
        return {gid: v / total for gid, v in inv_vols.items()}

    if method == 'max_return':
        returns = {}
        for gc in group_configs:
            trades = group_trades.get(gc.group_id, {})
            total_pnl = sum(
                t['pnl'] for token_trades in trades.values() for t in token_trades)
            returns[gc.group_id] = max(total_pnl, 0.0)
        total = sum(returns.values())
        if total <= 0:
            return {gc.group_id: 1.0 / n_groups for gc in group_configs}
        return {gid: v / total for gid, v in returns.items()}

    # Fallback to equal
    return {gc.group_id: 1.0 / n_groups for gc in group_configs}


def run_portfolio_backtest(group_configs: List[GroupSignalConfig],
                            config: SignalPortfolioConfig,
                            opt_results: Optional[Dict[int, OptimizationResult]] = None,
                            ) -> Dict:
    """Run full multi-group portfolio backtest.

    Steps:
        1. For each group -> create strategy function (with optimized params)
        2. For each token in group -> extract trades via v3 engine
        3. Scale trades by group capital allocation
        4. Pool all trades -> simulate_portfolio (shared cash pool)
        5. Compute portfolio metrics

    Returns:
        Dict with portfolio_equity, metrics, group_info, etc.
    """
    portfolio_mod = _load_v3_module('portfolio')
    simulate_portfolio = portfolio_mod.simulate_portfolio
    compute_portfolio_metrics = portfolio_mod.compute_portfolio_metrics
    universe_mod = _load_v3_module('universe')

    print(f"\nPortfolio Backtest: {len(group_configs)} groups")
    print(f"  Capital: ${config.capital:,.0f} | Market: {config.market}")

    # Apply optimized params if available
    if opt_results:
        for gc in group_configs:
            opt = opt_results.get(gc.group_id)
            if opt and opt.best_params:
                gc.entry_threshold = opt.best_params.get('entry_threshold', gc.entry_threshold)
                gc.min_vol_ratio = opt.best_params.get('min_vol_ratio', gc.min_vol_ratio)
                gc.stop_mult = opt.best_params.get('stop_mult', gc.stop_mult)
                gc.trail_mult = opt.best_params.get('trail_mult', gc.trail_mult)
                gc.min_hold = opt.best_params.get('min_hold', gc.min_hold)

    # Step 1-2: Extract trades per group
    t0 = time.time()
    group_trades = {}
    all_token_trades = {}

    for gc in group_configs:
        print(f"  Group {gc.group_id} ({len(gc.tokens)} tokens)...", end='', flush=True)
        trades = extract_group_trades(gc, config)
        group_trades[gc.group_id] = trades
        total = sum(len(t) for t in trades.values())
        print(f" {len(trades)} tokens, {total} trades")

        # Pool into all_token_trades
        for token, token_trades in trades.items():
            if token in all_token_trades:
                all_token_trades[token].extend(token_trades)
            else:
                all_token_trades[token] = token_trades

    elapsed = time.time() - t0
    total_trades = sum(len(t) for t in all_token_trades.values())
    print(f"  Total: {len(all_token_trades)} tokens, {total_trades} trades ({elapsed:.0f}s)")

    if not all_token_trades:
        print("  No trades produced. Check signal configuration.")
        return {}

    # Step 3: Compute group allocations (informational — actual sizing
    # is handled by the engine's position sizing + size_multiplier)
    allocations = compute_group_allocation(
        group_configs, group_trades, config.allocation_method)

    # Step 4: Simulate portfolio
    print(f"  Simulating portfolio...", end='', flush=True)
    fee_rate = universe_mod.get_fee_rate(config.exchange, config.market, 'taker')

    portfolio_eq, accepted, skipped, sim_info = simulate_portfolio(
        all_token_trades,
        capital=config.capital,
        max_token_pct=config.max_token_pct,
        min_position_usd=config.min_position_usd,
        fee_rate=fee_rate,
    )
    print(f" {len(accepted)} accepted, {len(skipped)} skipped")

    if len(portfolio_eq) < 2:
        print("  Insufficient data for portfolio equity.")
        return {}

    # Step 5: Compute metrics
    metrics = compute_portfolio_metrics(portfolio_eq, accepted, skipped, sim_info)

    # Summary
    print(f"\n{'='*60}")
    print(f"Portfolio Results")
    print(f"{'='*60}")
    print(f"  Annual Return: {metrics.annualized_return_pct:.1f}%")
    print(f"  Sharpe Ratio:  {metrics.sharpe_ratio:.2f}")
    print(f"  Sortino Ratio: {metrics.sortino_ratio:.2f}")
    print(f"  Max Drawdown:  {metrics.max_drawdown_pct:.1f}%")
    print(f"  Total Trades:  {metrics.total_trades}")
    print(f"  Win Rate:      {metrics.win_rate_pct:.1f}%")
    print(f"  Profit Factor: {metrics.profit_factor:.2f}")
    print(f"  Tokens Traded: {metrics.n_tokens_traded}")

    print(f"\n  Group Allocations ({config.allocation_method}):")
    for gid in sorted(allocations.keys()):
        pct = allocations[gid] * 100
        n_tokens = len(group_trades.get(gid, {}))
        print(f"    Group {gid}: {pct:.1f}% ({n_tokens} tokens with trades)")

    return {
        'portfolio_equity': portfolio_eq,
        'accepted_trades': accepted,
        'skipped_trades': skipped,
        'metrics': metrics,
        'info': sim_info,
        'group_allocations': allocations,
        'group_trades': group_trades,
    }
