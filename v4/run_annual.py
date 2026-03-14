#!/usr/bin/env python3
"""Run s56+s57 multi-strategy backtest and report annual returns."""
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


def run(months=36, verbose_mem=False):
    specs = {
        's56': StrategySpec(strategy_id='s56', weight=0.5, max_positions=15, market='perp'),
        's57': StrategySpec(strategy_id='s57', weight=0.5, max_positions=15, market='combined'),
    }

    config = PortfolioConfig(capital=200_000, max_portfolio_positions=40, seed=42)

    # Infer data end date for deterministic backtesting
    data_end = infer_data_end_date("combined")
    print(f'Data end: {data_end.strftime("%Y-%m-%d %H:%M")}')

    # Precompute signals one strategy at a time to limit peak memory
    all_signals = {}
    for sid, spec in specs.items():
        tokens = discover_tokens(spec.market)
        print(f'Precomputing {sid} ({len(tokens)} tokens, {spec.market})...')
        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
        print(f'  Done: {len(signals)} tokens ({time.time()-t0:.1f}s)')
        all_signals[sid] = signals
        gc.collect()

    # Run simulation
    print(f'\nSimulating portfolio...')
    t0 = time.time()
    state = simulate_portfolio(all_signals, specs, config)
    print(f'Done: {len(state.position_manager.closed_trades)} trades ({time.time()-t0:.1f}s)')

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, 200_000)

    print(f'\n{"="*70}')
    print(f'  V4 PORTFOLIO: s56 (momentum) + s57 (carry) — {months}mo lookback')
    print(f'{"="*70}')
    print(f'  Capital:       $200,000')
    print(f'  Final Equity:  ${extra_info["final_equity"]:,.0f}')
    total_ret = (extra_info['final_equity'] / 200_000 - 1) * 100
    print(f'  Total Return:  {total_ret:+.1f}%')
    print(f'  Sharpe:        {metrics.sharpe_ratio:.2f}')
    print(f'  Sortino:       {metrics.sortino_ratio:.2f}')
    print(f'  Max Drawdown:  {metrics.max_drawdown_pct:.1f}%')
    print(f'  Total Trades:  {metrics.total_trades}')
    print(f'  Win Rate:      {metrics.win_rate_pct:.1f}%')
    print(f'  Profit Factor: {metrics.profit_factor:.2f}')
    print(f'  Total Fees:    ${extra_info["total_fees"]:,.0f}')
    print(f'  Total Funding: ${extra_info["total_funding"]:,.0f}')

    sa = extra_info['strategy_attribution']
    print(f'\n  Per-Strategy:')
    for sid, s in sorted(sa.items()):
        print(f'    {sid}: {s["trades"]} trades, PnL=${s["pnl"]:+,.0f}, Funding=${s["funding"]:+,.0f}')

    rej = extra_info['rejections']
    if rej['total'] > 0:
        print(f'\n  Rejections: {rej["total"]} total')
        for k in ['portfolio_limit','strategy_limit','min_size','adv_cap','concentration','capital']:
            if rej[k] > 0:
                print(f'    {k}: {rej[k]}')

    # Annual returns
    print(f'\n{"="*70}')
    print(f'  ANNUAL RETURNS BY YEAR')
    print(f'{"="*70}')

    if len(eq_daily) > 1:
        eq_daily.index = pd.DatetimeIndex(eq_daily.index)
        years = sorted(set(eq_daily.index.year))

        print(f'  {"Year":>6s}  {"Return":>8s}  {"Start Eq":>12s}  {"End Eq":>12s}  {"MaxDD":>7s}  {"Sharpe":>7s}')
        print(f'  {"-"*6}  {"-"*8}  {"-"*12}  {"-"*12}  {"-"*7}  {"-"*7}')

        for yr in years:
            yr_eq = eq_daily[eq_daily.index.year == yr]
            if len(yr_eq) < 2:
                continue
            start_eq = yr_eq.iloc[0]
            end_eq = yr_eq.iloc[-1]
            yr_ret = (end_eq / start_eq - 1) * 100

            cummax = yr_eq.cummax()
            dd = (yr_eq - cummax) / cummax
            max_dd = dd.min() * 100

            daily_ret = yr_eq.pct_change().dropna().replace([np.inf, -np.inf], 0).fillna(0)
            if daily_ret.std() > 1e-10:
                sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(365)
            else:
                sharpe = 0.0

            print(f'  {yr:>6d}  {yr_ret:>+7.1f}%  ${start_eq:>11,.0f}  ${end_eq:>11,.0f}  {max_dd:>6.1f}%  {sharpe:>7.2f}')

    print(f'{"="*70}')


if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    run(months)
