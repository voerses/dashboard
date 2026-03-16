"""
s58 Multi-Strategy Portfolio — combines momentum (s56) + carry (s57) trade streams

NOT a single strategy function — this is a portfolio runner that:
1. Extracts trades from s56 (signal-enhanced momentum, perp-only)
2. Extracts trades from s57 (signal-timed turbo carry, combined spot+perp)
3. Pools all trades into a shared cash pool via v3/portfolio.simulate_portfolio()

This achieves diversification: momentum captures directional moves in trends,
carry captures funding income and basis convergence in all regimes.

Usage:
    python strategies/s58_multi_strategy_portfolio.py
    python strategies/s58_multi_strategy_portfolio.py --capital 200000 --workers 4

Status: EXPERIMENTAL
"""

import sys
import os
import time
import json
import argparse
import importlib.util
import pandas as pd
from dataclasses import asdict
from typing import Dict, List, Optional

# Ensure paths are set up
_this_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_this_dir)
_v3_dir = os.path.join(_project_root, 'v3')
for p in [_v3_dir, _project_root]:
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_v3(name):
    full_name = f'v3_{name}'
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(_v3_dir, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _get_momentum_trades(tokens, data_dir, capital, exchange='binance'):
    """Extract trades from s56 signal-enhanced momentum (perp-only)."""
    engine_mod = _load_v3('engine')
    Engine = engine_mod.Engine

    from strategies.s56_signal_enhanced_momentum import strategy as s56_fn

    all_trades = {}
    for ticker in tokens:
        try:
            h1_path = os.path.join(data_dir, 'perp', f'1h_cache/{ticker}_1h.parquet')
            if not os.path.exists(h1_path):
                continue
            df_1h = pd.read_parquet(h1_path)
            if len(df_1h) < 2000:
                continue

            engine = Engine(data_dir=data_dir, market='perp',
                          capital=capital, exchange=exchange)
            ctx = engine._build_context(ticker, df_1h)
            if ctx is None:
                continue

            result = s56_fn(ctx)
            n = len(ctx.ind_1h['close'])

            # Walk-forward masking
            train_bars = 365 * 24
            recal_bars = 90 * 24
            purge_bars = 5 * 24
            if n <= train_bars + purge_bars:
                continue

            masked_entry = result.entry_mask.copy()
            masked_entry[:train_bars] = False
            recal_point = train_bars
            while recal_point < n:
                purge_end = min(recal_point + purge_bars, n)
                masked_entry[recal_point:purge_end] = False
                recal_point += recal_bars
            result.entry_mask = masked_entry

            trades, _ = engine._simulate(ctx, result)
            if not trades:
                continue

            idx = ctx.idx_1h
            for t in trades:
                t['token'] = ticker
                t['entry_time'] = idx[min(t['entry_bar'], len(idx) - 1)]
                t['exit_time'] = idx[min(t['exit_bar'], len(idx) - 1)]
                t['strategy'] = 'momentum'

            all_trades[f'{ticker}_mom'] = trades
        except Exception as e:
            print(f"  Momentum error {ticker}: {e}")

    return all_trades


def _get_carry_trades(tokens, data_dir, capital, exchange='binance'):
    """Extract trades from s57 signal-timed turbo carry (combined spot+perp)."""
    engine_mod = _load_v3('engine')
    Engine = engine_mod.Engine

    from strategies.s57_signal_timed_turbo_carry import strategy as s57_fn

    all_trades = {}
    for ticker in tokens:
        try:
            spot_path = os.path.join(data_dir, 'spot', f'1h_cache/{ticker}_1h.parquet')
            perp_path = os.path.join(data_dir, 'perp', f'1h_cache/{ticker}_1h.parquet')
            if not os.path.exists(spot_path) or not os.path.exists(perp_path):
                continue

            df_spot = pd.read_parquet(spot_path)
            df_perp = pd.read_parquet(perp_path)
            if len(df_spot) < 2000 or len(df_perp) < 2000:
                continue

            # Align to common time range
            common_start = max(df_spot.index.min(), df_perp.index.min())
            common_end = min(df_spot.index.max(), df_perp.index.max())
            df_spot = df_spot.loc[common_start:common_end]
            df_perp = df_perp.loc[common_start:common_end]
            if len(df_spot) < 2000 or len(df_perp) < 2000:
                continue

            engine_spot = Engine(data_dir=data_dir, market='spot',
                               capital=capital, exchange=exchange)
            engine_perp = Engine(data_dir=data_dir, market='perp',
                               capital=capital, exchange=exchange)

            ctx_spot = engine_spot._build_context(ticker, df_spot)
            ctx_perp = engine_perp._build_context(ticker, df_perp)
            if ctx_spot is None or ctx_perp is None:
                continue

            result = s57_fn(ctx_spot, ctx_perp)
            n = min(len(ctx_spot.ind_1h['close']),
                    len(ctx_perp.ind_1h['close']))

            # Walk-forward masking
            train_bars = 365 * 24
            recal_bars = 90 * 24
            purge_bars = 5 * 24
            if n <= train_bars + purge_bars:
                continue

            masked_entry = result.entry_mask.copy()
            masked_entry[:train_bars] = False
            recal_point = train_bars
            while recal_point < n:
                purge_end = min(recal_point + purge_bars, n)
                masked_entry[recal_point:purge_end] = False
                recal_point += recal_bars
            result.entry_mask = masked_entry

            if result.secondary_entry_mask is not None:
                masked_secondary = result.secondary_entry_mask.copy()
                masked_secondary[:train_bars] = False
                recal_point = train_bars
                while recal_point < n:
                    purge_end = min(recal_point + purge_bars, n)
                    masked_secondary[recal_point:purge_end] = False
                    recal_point += recal_bars
                result.secondary_entry_mask = masked_secondary

            trades, _ = engine_perp._simulate_combined(
                ctx_spot, ctx_perp, result)
            if not trades:
                continue

            idx = ctx_spot.idx_1h
            for t in trades:
                t['token'] = ticker
                t['entry_time'] = idx[min(t['entry_bar'], len(idx) - 1)]
                t['exit_time'] = idx[min(t['exit_bar'], len(idx) - 1)]
                t['strategy'] = 'carry'

            all_trades[f'{ticker}_carry'] = trades
        except Exception as e:
            print(f"  Carry error {ticker}: {e}")

    return all_trades


def run_multi_strategy(capital=200_000, data_dir='data', exchange='binance',
                        workers=1):
    """Run the multi-strategy portfolio backtest."""
    portfolio_mod = _load_v3('portfolio')
    universe_mod = _load_v3('universe')
    simulate_portfolio = portfolio_mod.simulate_portfolio
    compute_portfolio_metrics = portfolio_mod.compute_portfolio_metrics

    # Get all tradeable tokens
    perp_tokens = universe_mod.get_all_tradeable('perp')
    print(f"\nMulti-Strategy Portfolio: s56 momentum + s57 carry")
    print(f"  Capital: ${capital:,.0f} | Tokens available: {len(perp_tokens)}")

    # Extract momentum trades (perp-only, all tokens)
    t0 = time.time()
    print(f"\n  Extracting momentum trades (s56)...", end='', flush=True)
    mom_trades = _get_momentum_trades(perp_tokens, data_dir, capital, exchange)
    mom_total = sum(len(t) for t in mom_trades.values())
    print(f" {len(mom_trades)} tokens, {mom_total} trades ({time.time()-t0:.0f}s)")

    # Extract carry trades (combined, needs both spot+perp)
    t1 = time.time()
    print(f"  Extracting carry trades (s57)...", end='', flush=True)
    carry_trades = _get_carry_trades(perp_tokens, data_dir, capital, exchange)
    carry_total = sum(len(t) for t in carry_trades.values())
    print(f" {len(carry_trades)} tokens, {carry_total} trades ({time.time()-t1:.0f}s)")

    # Pool all trades into unified token dict
    # For portfolio simulation, key by token name (merge momentum + carry)
    all_token_trades = {}
    for key, trades in mom_trades.items():
        token = key.replace('_mom', '')
        if token not in all_token_trades:
            all_token_trades[token] = []
        all_token_trades[token].extend(trades)

    for key, trades in carry_trades.items():
        token = key.replace('_carry', '')
        if token not in all_token_trades:
            all_token_trades[token] = []
        all_token_trades[token].extend(trades)

    total_trades = sum(len(t) for t in all_token_trades.values())
    print(f"  Combined: {len(all_token_trades)} tokens, {total_trades} trades")

    if not all_token_trades:
        print("  No trades. Exiting.")
        return {}

    # Simulate portfolio
    print(f"  Simulating portfolio...", end='', flush=True)
    fee_rate = universe_mod.get_fee_rate(exchange, 'perp', 'taker')
    portfolio_eq, accepted, skipped, sim_info = simulate_portfolio(
        all_token_trades, capital, max_token_pct=0.15,
        min_position_usd=1000, fee_rate=fee_rate)
    print(f" {len(accepted)} accepted, {len(skipped)} skipped")

    if len(portfolio_eq) < 2:
        print("  Insufficient equity data.")
        return {}

    metrics = compute_portfolio_metrics(portfolio_eq, accepted, skipped, sim_info)

    # Strategy breakdown
    mom_accepted = [t for t in accepted if t.get('strategy') == 'momentum']
    carry_accepted = [t for t in accepted if t.get('strategy') == 'carry']
    mom_pnl = sum(t['pnl'] for t in mom_accepted)
    carry_pnl = sum(t['pnl'] for t in carry_accepted)

    print(f"\n{'='*60}")
    print(f"Multi-Strategy Portfolio Results")
    print(f"{'='*60}")
    print(f"  Annual Return:  {metrics.annualized_return_pct:.1f}%")
    print(f"  Total Return:   {metrics.total_return_pct:.1f}%")
    print(f"  Sharpe Ratio:   {metrics.sharpe_ratio:.2f}")
    print(f"  Sortino Ratio:  {metrics.sortino_ratio:.2f}")
    print(f"  Max Drawdown:   {metrics.max_drawdown_pct:.1f}%")
    print(f"  Total Trades:   {metrics.total_trades}")
    print(f"  Win Rate:       {metrics.win_rate_pct:.1f}%")
    print(f"  Profit Factor:  {metrics.profit_factor:.2f}")
    print(f"  Tokens Traded:  {metrics.n_tokens_traded}")
    print(f"\n  Strategy Breakdown:")
    print(f"    Momentum: {len(mom_accepted)} trades, "
          f"PnL ${mom_pnl:,.0f}")
    print(f"    Carry:    {len(carry_accepted)} trades, "
          f"PnL ${carry_pnl:,.0f}")
    print(f"\n  Top: {metrics.top_contributors}")
    print(f"  Worst: {metrics.worst_contributors}")

    # Save results
    results_dir = os.path.join(_project_root, 'results')
    os.makedirs(results_dir, exist_ok=True)

    from datetime import datetime
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    result_data = {
        'strategy': 's58_multi_strategy_portfolio',
        'timestamp': datetime.now().isoformat(),
        'config': {
            'capital': capital,
            'exchange': exchange,
        },
        'metrics': asdict(metrics),
        'breakdown': {
            'momentum_trades': len(mom_accepted),
            'momentum_pnl': mom_pnl,
            'carry_trades': len(carry_accepted),
            'carry_pnl': carry_pnl,
        },
    }

    output_path = os.path.join(results_dir, f'portfolio_s58_{ts}.json')
    with open(output_path, 'w') as f:
        json.dump(result_data, f, indent=2, default=str)
    print(f"\n  Results saved to {output_path}")

    return {
        'portfolio_equity': portfolio_eq,
        'accepted_trades': accepted,
        'skipped_trades': skipped,
        'metrics': metrics,
        'info': sim_info,
    }


# Also provide a single-market strategy function for backward compatibility
# with the portfolio runner (perp-only, momentum only)
def strategy(ctx):
    """Fallback: runs s56 momentum when called as a single strategy."""
    from strategies.s56_signal_enhanced_momentum import strategy as s56_fn
    return s56_fn(ctx)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='s58 Multi-Strategy Portfolio')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()

    run_multi_strategy(
        capital=args.capital,
        data_dir=args.data_dir,
        exchange=args.exchange,
        workers=args.workers,
    )
