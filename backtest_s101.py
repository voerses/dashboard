#!/usr/bin/env python3
"""Quick backtest for s101/s102 MACD squeeze strategies."""
import sys, os, time, json, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import build_unified_index, SimulationState, _process_exits, _process_entries, _process_margin_calls, _record_equity_snapshot, _close_all_remaining
from v4.report import compute_portfolio_metrics
from v4.minute_exits import MinuteExitCache, process_minute_exits


def run_backtest(months=12, capital=200_000, strategy_id='s101', exit_resolution=0):
    data_end = infer_data_end_date("perp")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    res_label = f"{exit_resolution}m exits" if exit_resolution else "hourly exits"
    print(f"Lookback: {months} months, Capital: ${capital:,.0f}, Exit resolution: {res_label}\n")

    spec = StrategySpec(
        strategy_id=strategy_id,
        weight=1.0,
        max_positions=8,
        market='perp',
        strategy_type='per_token',
        adv_sizing_enabled=True,
        adv_sizing_base=75_000_000,
    )

    config = PortfolioConfig(
        capital=capital,
        max_portfolio_positions=8,
        concentration_limit=0.15,
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
    minute_cache = MinuteExitCache(resolution=exit_resolution) if exit_resolution else None

    for global_bar in range(n_bars):
        if minute_cache is not None:
            process_minute_exits(state, all_signals, bar_maps, global_bar,
                                 unified_ts, config, strategy_specs, minute_cache)
        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)
    elapsed = time.time() - t0
    print(f"  Simulation done in {elapsed:.1f}s ({n_bars} bars)")

    # Compute metrics
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

    # Target check
    print(f"\n  TARGET CHECK:")
    print(f"  Annual Return >= 300%:  {'PASS' if annual_ret >= 300 else 'FAIL'} ({annual_ret:+.1f}%)")
    print(f"  Max DD <= 20%:          {'PASS' if abs(metrics.max_drawdown_pct) <= 20 else 'FAIL'} ({metrics.max_drawdown_pct:.1f}%)")
    print(f"  Calmar >= 3:            {'PASS' if metrics.calmar_ratio >= 3 else 'FAIL'} ({metrics.calmar_ratio:.2f})")
    print()

    # Monthly breakdown
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
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--capital", type=int, default=200_000)
    parser.add_argument("--strategy", type=str, default='s101')
    parser.add_argument("--exit-resolution", type=int, default=0, choices=[0, 1, 5, 15, 30],
                        help="Sub-hourly exit resolution in minutes (0=hourly only, 1/5/15/30)")
    args = parser.parse_args()
    run_backtest(args.months, args.capital, args.strategy, exit_resolution=args.exit_resolution)
