"""
s514 Expanded Universe Validation — 229 tokens
================================================

Runs s514_ls_div_leveraged across L12M/L6M/L3M windows on the expanded
229-token universe (previously 29 tokens).

Usage:
    /workspace/venv/bin/python research/s514_expanded_universe.py
"""

import sys
import os
import time
import dataclasses

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report

STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000

windows = {
    'L12M': {'months': 12, 'end': '2026-04-01'},
    'L6M':  {'months': 6,  'end': '2026-04-01'},
    'L3M':  {'months': 3,  'end': '2026-04-01'},
}

# Previous 29-token baseline for comparison
BASELINE = {
    'sharpe': 2.65,
    'calmar': 5.90,
    'max_dd': -18.7,
    'total_return': 109.7,
    'trades': 382,
    'tokens': 29,
}


def run_window(name, months, end_date_str):
    """Run s514 backtest for a single time window."""
    end_date = pd.Timestamp(end_date_str)

    spec = StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=100,
        market=MARKET,
        strategy_type='per_token',
        max_concurrent_per_token=1,
        dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
        max_portfolio_positions=100,
        strategies=[spec],
    )

    tokens = discover_tokens(MARKET)
    print(f"\n{'='*70}")
    print(f"  {name}: {months}M lookback ending {end_date_str}")
    print(f"  Universe: {len(tokens)} tokens")
    print(f"{'='*70}")

    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    t1 = time.time()

    # Count tokens that actually produced entry signals
    tokens_with_signals = sum(1 for t, sigs in signals.items() if sigs.entry_mask.any())
    print(f"  Signals: {len(signals)} tokens total, {tokens_with_signals} with entries ({t1 - t0:.1f}s)")

    if not signals:
        print(f"  [{name}] No signals produced!")
        return None

    strategy_specs = {STRATEGY_ID: spec}
    config_run = dataclasses.replace(config, strategies=[spec])

    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)
    t2 = time.time()
    n_trades = len(state.position_manager.closed_trades)
    print(f"  Simulation: {n_trades} trades ({t2 - t1:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    print_report(metrics, extra_info, CAPITAL, [STRATEGY_ID])

    return metrics, extra_info, tokens_with_signals, n_trades


def main():
    print("=" * 70)
    print("  s514_ls_div_leveraged — Expanded Universe (229 tokens)")
    print("  Previous baseline (29 tokens): Sharpe 2.65, Calmar 5.90,")
    print("    MaxDD -18.7%, +109.7%, 382 trades")
    print("=" * 70)

    results = {}
    for name, w in windows.items():
        result = run_window(name, w['months'], w['end'])
        if result is not None:
            results[name] = result

    # Summary comparison table
    print(f"\n{'='*70}")
    print("  SUMMARY — s514 Expanded Universe")
    print(f"{'='*70}")
    print(f"  {'Window':<8} {'Sharpe':>8} {'Calmar':>8} {'MaxDD%':>8} {'Return%':>10} {'Trades':>8} {'Tokens':>8}")
    print(f"  {'-'*60}")
    for name, (metrics, extra_info, tokens_with_signals, n_trades) in results.items():
        print(f"  {name:<8} {metrics.sharpe_ratio:>8.2f} {metrics.calmar_ratio:>8.2f} "
              f"{metrics.max_drawdown_pct:>8.1f} {metrics.total_return_pct:>10.1f} "
              f"{n_trades:>8} {tokens_with_signals:>8}")

    # Baseline comparison
    if 'L12M' in results:
        m = results['L12M'][0]
        print(f"\n  vs 29-token baseline (L12M):")
        print(f"    Sharpe:  {m.sharpe_ratio:.2f} vs {BASELINE['sharpe']:.2f} ({m.sharpe_ratio - BASELINE['sharpe']:+.2f})")
        print(f"    Calmar:  {m.calmar_ratio:.2f} vs {BASELINE['calmar']:.2f} ({m.calmar_ratio - BASELINE['calmar']:+.2f})")
        print(f"    MaxDD:   {m.max_drawdown_pct:.1f}% vs {BASELINE['max_dd']:.1f}%")
        print(f"    Return:  {m.total_return_pct:.1f}% vs {BASELINE['total_return']:.1f}%")
        print(f"    Trades:  {m.total_trades} vs {BASELINE['trades']}")
        print(f"    Tokens:  {results['L12M'][2]} vs {BASELINE['tokens']}")


if __name__ == '__main__':
    main()
