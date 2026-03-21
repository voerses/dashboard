#!/usr/bin/env python3
"""Generic backtest runner for portfolio strategies."""
import sys, time, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import build_unified_index, SimulationState, _process_exits, _process_entries, _process_margin_calls, _record_equity_snapshot, _close_all_remaining
from v4.report import compute_portfolio_metrics


def run_backtest(strategy_id, months=12, capital=200_000, strategy_type='portfolio',
                 max_positions=15, concentration=0.20):
    data_end = infer_data_end_date("perp")
    print(f"Strategy: {strategy_id} ({strategy_type})")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {months} months, Capital: ${capital:,.0f}\n")

    spec = StrategySpec(
        strategy_id=strategy_id,
        weight=1.0,
        max_positions=max_positions,
        market='perp',
        strategy_type=strategy_type,
        adv_sizing_enabled=True,
        adv_sizing_base=75_000_000,
    )

    config = PortfolioConfig(
        capital=capital,
        max_portfolio_positions=max_positions,
        concentration_limit=concentration,
        adv_cap_pct=0.05,
        seed=42,
        max_sizing_equity=2_000_000,
        stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )

    print("Precomputing signals...")
    t0 = time.time()
    tokens = discover_tokens(spec.market)
    print(f"  Discovered {len(tokens)} tokens")
    all_signals = {
        strategy_id: precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
    }
    print(f"  Signals computed in {time.time()-t0:.1f}s")
    print(f"  Tokens with signals: {len(all_signals[strategy_id])}")

    strategy_specs = {strategy_id: spec}

    print("\nRunning simulation...")
    t0 = time.time()
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)
    state = SimulationState(initial_capital=capital)
    rng = np.random.RandomState(config.seed)

    for global_bar in range(n_bars):
        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)
    elapsed = time.time() - t0
    print(f"  Simulation done in {elapsed:.1f}s ({n_bars} bars)")

    metrics, extra, eq_daily = compute_portfolio_metrics(state, capital)

    total_ret = (extra['final_equity'] / capital - 1) * 100
    annual_ret = metrics.annualized_return_pct if hasattr(metrics, 'annualized_return_pct') else total_ret

    print(f"\n{'='*60}")
    print(f"  {strategy_id.upper()} BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Total Return:      {total_ret:>+8.1f}%")
    print(f"  Final Equity:      ${extra['final_equity']:>12,.0f}")
    print(f"  Sharpe Ratio:      {metrics.sharpe_ratio:>8.2f}")
    print(f"  Sortino Ratio:     {metrics.sortino_ratio:>8.2f}")
    print(f"  Calmar Ratio:      {metrics.calmar_ratio:>8.2f}")
    print(f"  Max Drawdown:      {metrics.max_drawdown_pct:>8.1f}%")
    print(f"  Total Trades:      {metrics.total_trades:>8d}")
    print(f"  Win Rate:          {metrics.win_rate_pct:>8.1f}%")
    print(f"  Profit Factor:     {metrics.profit_factor:>8.2f}")
    print(f"  Avg Hold (hours):  {metrics.avg_hold_hours:>8.1f}")
    print(f"  Payoff Ratio:      {metrics.payoff_ratio:>8.2f}")
    print(f"  Annual Return:     {annual_ret:>+8.1f}%")
    print(f"{'='*60}")

    print(f"\n  TARGET CHECK:")
    print(f"  Total Return >= 300%:   {'PASS' if total_ret >= 300 else 'FAIL'} ({total_ret:+.1f}%)")
    print(f"  Max DD <= 20%:          {'PASS' if abs(metrics.max_drawdown_pct) <= 20 else 'FAIL'} ({metrics.max_drawdown_pct:.1f}%)")
    print(f"  Calmar >= 3:            {'PASS' if metrics.calmar_ratio >= 3 else 'FAIL'} ({metrics.calmar_ratio:.2f})")
    print()

    if state.equity_snapshots:
        ts = [s[0] for s in state.equity_snapshots]
        eq = [s[1] for s in state.equity_snapshots]
        eq_series = pd.Series(eq, index=pd.DatetimeIndex(ts))
        eq_daily = eq_series.resample('D').last().ffill()

        print("  Monthly breakdown:")
        for period, grp in eq_daily.groupby(eq_daily.index.to_period('M')):
            if len(grp) < 2:
                continue
            pct = (grp.iloc[-1] / grp.iloc[0] - 1) * 100
            dd = ((grp - grp.cummax()) / grp.cummax()).min() * 100
            print(f"    {period}: {pct:>+7.1f}%  DD={dd:>6.1f}%")

    return metrics, extra


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy", type=str)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--capital", type=int, default=200_000)
    parser.add_argument("--type", type=str, default='portfolio', choices=['portfolio', 'per_token'])
    parser.add_argument("--max-positions", type=int, default=15)
    parser.add_argument("--concentration", type=float, default=0.20)
    args = parser.parse_args()
    run_backtest(args.strategy, args.months, args.capital, args.type,
                 args.max_positions, args.concentration)
