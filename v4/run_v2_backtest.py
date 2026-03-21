#!/usr/bin/env python3
"""Run V2 ML model portfolio backtests: s312+s313 (long+short) and compare with s305+s310."""
import sys, os, gc, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


def run_combo(specs, label, months=12, capital=200_000):
    """Run a strategy combination and return metrics."""
    config = PortfolioConfig(capital=capital, max_portfolio_positions=40, seed=42)
    data_end = infer_data_end_date("perp")

    all_signals = {}
    for sid, spec in specs.items():
        tokens = discover_tokens(spec.market)
        print(f'  Precomputing {sid} ({len(tokens)} tokens)...')
        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
        print(f'    Done: {len(signals)} tokens ({time.time()-t0:.1f}s)')
        all_signals[sid] = signals
        gc.collect()

    print(f'  Simulating...')
    t0 = time.time()
    state = simulate_portfolio(all_signals, specs, config)
    print(f'    Done: {len(state.position_manager.closed_trades)} trades ({time.time()-t0:.1f}s)')

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)

    total_ret = (extra_info['final_equity'] / capital - 1) * 100
    max_dd = metrics.max_drawdown_pct

    print(f'\n  === {label} ===')
    print(f'  Final Equity:  ${extra_info["final_equity"]:,.0f}')
    print(f'  Total Return:  {total_ret:+.1f}%')
    print(f'  Max Drawdown:  {max_dd:.1f}%')
    print(f'  Sharpe:        {metrics.sharpe_ratio:.2f}')
    print(f'  Sortino:       {metrics.sortino_ratio:.2f}')
    print(f'  Win Rate:      {metrics.win_rate_pct:.1f}%')
    print(f'  Total Trades:  {metrics.total_trades}')
    print(f'  Profit Factor: {metrics.profit_factor:.2f}')
    print(f'  Total Fees:    ${extra_info["total_fees"]:,.0f}')
    print(f'  Total Funding: ${extra_info["total_funding"]:,.0f}')

    sa = extra_info['strategy_attribution']
    for sid, s in sorted(sa.items()):
        print(f'    {sid}: {s["trades"]} trades, PnL=${s["pnl"]:+,.0f}')

    rej = extra_info['rejections']
    if rej['total'] > 0:
        print(f'  Rejections: {rej["total"]} total')
        for k in ['portfolio_limit', 'strategy_limit', 'capital']:
            if rej.get(k, 0) > 0:
                print(f'    {k}: {rej[k]}')

    # Sub-period analysis
    if len(eq_daily) > 1:
        eq_daily.index = pd.DatetimeIndex(eq_daily.index)
        n_days = len(eq_daily)

        # 3 periods: first third, middle third, last third
        third = n_days // 3
        periods = [
            ('First 4mo', eq_daily.iloc[:third]),
            ('Middle 4mo', eq_daily.iloc[third:2*third]),
            ('Last 4mo', eq_daily.iloc[2*third:]),
        ]

        print(f'\n  Sub-Period Analysis:')
        print(f'  {"Period":<12} {"Return":>8s} {"MaxDD":>7s} {"Start":>12s} {"End":>12s}')
        print(f'  {"-"*12} {"-"*8} {"-"*7} {"-"*12} {"-"*12}')

        for name, p_eq in periods:
            if len(p_eq) < 2:
                continue
            ret = (p_eq.iloc[-1] / p_eq.iloc[0] - 1) * 100
            cummax = p_eq.cummax()
            dd = ((p_eq - cummax) / cummax).min() * 100
            print(f'  {name:<12} {ret:>+7.1f}% {dd:>6.1f}% ${p_eq.iloc[0]:>11,.0f} ${p_eq.iloc[-1]:>11,.0f}')

    return total_ret, max_dd, metrics, eq_daily


def main():
    months = 12
    print('=' * 70)
    print('V2 ML MODEL PORTFOLIO BACKTEST')
    print('=' * 70)

    results = {}

    # V2 Long + V2 Short
    print(f'\n--- V2: s312 (V2 Long) + s313 (V2 Short) ---')
    specs_v2 = {
        's312': StrategySpec(strategy_id='s312', weight=0.5, max_positions=15, market='perp'),
        's313': StrategySpec(strategy_id='s313', weight=0.5, max_positions=15, market='perp'),
    }
    ret, dd, met, eq = run_combo(specs_v2, 'V2: s312+s313', months)
    results['v2_split'] = (ret, dd)
    gc.collect()

    # V1 baseline: s305 + s310
    print(f'\n\n--- V1 Baseline: s305 (V1 Long) + s310 (V1 Short) ---')
    specs_v1 = {
        's305': StrategySpec(strategy_id='s305', weight=0.5, max_positions=15, market='perp'),
        's310': StrategySpec(strategy_id='s310', weight=0.5, max_positions=15, market='perp'),
    }
    ret1, dd1, met1, eq1 = run_combo(specs_v1, 'V1: s305+s310', months)
    results['v1_best'] = (ret1, dd1)
    gc.collect()

    # Comparison
    print(f'\n\n{"="*70}')
    print(f'COMPARISON')
    print(f'{"="*70}')
    print(f'{"Combo":<25} {"Return":>10} {"MaxDD":>10} {"Calmar":>10}')
    print(f'{"-"*55}')
    for name, (r, d) in results.items():
        calmar = abs(r / d) if d != 0 else 0
        print(f'{name:<25} {r:>+9.1f}% {d:>9.1f}% {calmar:>10.2f}')
    print(f'{"="*70}')


if __name__ == "__main__":
    main()
