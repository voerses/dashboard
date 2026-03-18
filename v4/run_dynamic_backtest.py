#!/usr/bin/env python3
"""Run actual V4 portfolio backtests comparing static vs dynamic regime weights.

This uses the real V4 simulator with capital constraints, concentration limits,
and ADV caps — NOT the PnL-scaling approximation.

Usage:
    python v4/run_dynamic_backtest.py --months 4
    python v4/run_dynamic_backtest.py --months 12
"""
import sys, os, gc, time, json, copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import build_unified_index, SimulationState, _process_exits, _process_entries, _process_margin_calls, _record_equity_snapshot, _close_all_remaining
from v4.report import compute_portfolio_metrics
from v4.dynamic_weights import DynamicWeightAllocator, REGIME_NAMES


# Portfolio definitions: {name: {strategy_id: (weight, market, max_pos, strategy_type)}}
PORTFOLIOS = {
    "s80+s81": {
        "s80": (1.0, "perp", 15, "portfolio"),
        "s81": (1.0, "perp", 15, "portfolio"),
    },
    "super5": {
        "s57": (1.0, "combined", 15, "per_token"),
        "s60": (1.0, "perp", 15, "per_token"),
        "s63": (1.0, "perp", 15, "per_token"),
        "s80": (1.0, "perp", 15, "portfolio"),
        "s81": (1.0, "perp", 15, "portfolio"),
    },
    "s58": {
        "s56": (1.0, "perp", 15, "per_token"),
        "s57": (1.0, "combined", 15, "per_token"),
    },
}


def make_specs(portfolio_def):
    """Create StrategySpec dict from portfolio definition."""
    specs = {}
    for sid, (weight, market, max_pos, stype) in portfolio_def.items():
        specs[sid] = StrategySpec(
            strategy_id=sid,
            weight=weight,
            max_positions=max_pos,
            market=market,
            strategy_type=stype,
        )
    return specs


def simulate_with_dynamic_weights(
    all_signals, strategy_specs, config, allocator=None
):
    """Run V4 simulation, optionally adjusting weights per-bar via DynamicWeightAllocator.

    If allocator is None, runs with static weights (baseline).
    """
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)

    state = SimulationState(initial_capital=config.capital)
    rng = np.random.RandomState(config.seed)

    # Get BTC regime array from any strategy that has BTC signals
    btc_regime = None
    btc_bar_map = None
    for sid, token_sigs in all_signals.items():
        if 'BTC' in token_sigs:
            sig = token_sigs['BTC']
            btc_regime = sig.regime
            btc_bar_map = bar_maps.get('BTC')
            break

    for global_bar in range(n_bars):
        # Apply dynamic weights before entries
        if allocator is not None and btc_regime is not None and btc_bar_map is not None:
            local_bar = int(btc_bar_map[global_bar])
            if local_bar >= 0 and local_bar < len(btc_regime):
                regime = int(btc_regime[local_bar])
                new_weights = allocator.get_weights_instant(regime)
                for sid, spec in strategy_specs.items():
                    if sid in new_weights:
                        spec.weight = new_weights[sid]

        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)
    return state


def compute_monthly_metrics(state, capital):
    """Compute per-month returns from equity snapshots."""
    if not state.equity_snapshots:
        return {}

    ts = [s[0] for s in state.equity_snapshots]
    eq = [s[1] for s in state.equity_snapshots]
    eq_series = pd.Series(eq, index=pd.DatetimeIndex(ts))

    # Resample to daily for cleaner monthly aggregation
    eq_daily = eq_series.resample('D').last().ffill()

    months = {}
    for period, grp in eq_daily.groupby(eq_daily.index.to_period('M')):
        if len(grp) < 2:
            continue
        start_eq = grp.iloc[0]
        end_eq = grp.iloc[-1]
        pct_ret = (end_eq / start_eq - 1) * 100
        abs_ret = end_eq - start_eq

        # Max drawdown in this month
        cummax = grp.cummax()
        dd = (grp - cummax) / cummax
        max_dd = dd.min() * 100

        months[str(period)] = {
            'start_eq': float(start_eq),
            'end_eq': float(end_eq),
            'abs_return': float(abs_ret),
            'pct_return': float(pct_ret),
            'max_dd_pct': float(max_dd),
        }

    return months


def run_portfolio(name, portfolio_def, months, allocator_factory=None):
    """Run one portfolio through V4 simulator, static and optionally dynamic."""
    print(f"\n{'='*70}")
    print(f"  PORTFOLIO: {name} — {months}mo lookback")
    print(f"{'='*70}")

    specs = make_specs(portfolio_def)

    # Determine max portfolio positions based on number of strategies
    n_strats = len(specs)
    max_port_pos = min(n_strats * 15, 60)

    config = PortfolioConfig(
        capital=200_000,
        max_portfolio_positions=max_port_pos,
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        seed=42,
    )

    data_end = infer_data_end_date("combined")
    print(f"  Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")

    # Precompute signals
    all_signals = {}
    for sid, spec in specs.items():
        tokens = discover_tokens(spec.market)
        print(f"  Precomputing {sid} ({len(tokens)} tokens, {spec.market})...")
        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
        print(f"    Done: {len(signals)} tokens ({time.time()-t0:.1f}s)")
        all_signals[sid] = signals
        gc.collect()

    results = {}

    # --- STATIC baseline ---
    print(f"\n  Running STATIC simulation...")
    # Reset weights to 1.0
    for sid, spec in specs.items():
        spec.weight = portfolio_def[sid][0]

    t0 = time.time()
    state_static = simulate_with_dynamic_weights(all_signals, specs, config, allocator=None)
    elapsed = time.time() - t0
    print(f"    Done: {len(state_static.position_manager.closed_trades)} trades ({elapsed:.1f}s)")

    metrics_s, extra_s, eq_daily_s = compute_portfolio_metrics(state_static, 200_000)
    monthly_s = compute_monthly_metrics(state_static, 200_000)
    results['static'] = {
        'metrics': metrics_s,
        'extra': extra_s,
        'monthly': monthly_s,
    }

    print(f"    Return: {(extra_s['final_equity']/200_000-1)*100:+.1f}%")
    print(f"    Sharpe: {metrics_s.sharpe_ratio:.2f}")
    print(f"    MaxDD:  {metrics_s.max_drawdown_pct:.1f}%")
    print(f"    Trades: {metrics_s.total_trades}")

    # --- DYNAMIC ---
    if allocator_factory:
        print(f"\n  Running DYNAMIC simulation...")
        # Reset weights to 1.0
        for sid, spec in specs.items():
            spec.weight = portfolio_def[sid][0]

        allocator = allocator_factory(list(specs.keys()), {sid: portfolio_def[sid][0] for sid in specs})

        t0 = time.time()
        state_dyn = simulate_with_dynamic_weights(all_signals, specs, config, allocator=allocator)
        elapsed = time.time() - t0
        print(f"    Done: {len(state_dyn.position_manager.closed_trades)} trades ({elapsed:.1f}s)")

        metrics_d, extra_d, eq_daily_d = compute_portfolio_metrics(state_dyn, 200_000)
        monthly_d = compute_monthly_metrics(state_dyn, 200_000)
        results['dynamic'] = {
            'metrics': metrics_d,
            'extra': extra_d,
            'monthly': monthly_d,
        }

        print(f"    Return: {(extra_d['final_equity']/200_000-1)*100:+.1f}%")
        print(f"    Sharpe: {metrics_d.sharpe_ratio:.2f}")
        print(f"    MaxDD:  {metrics_d.max_drawdown_pct:.1f}%")
        print(f"    Trades: {metrics_d.total_trades}")

    # Print monthly breakdown
    print(f"\n  {'─'*68}")
    print(f"  MONTHLY BREAKDOWN")
    print(f"  {'─'*68}")

    all_months = sorted(set(list(monthly_s.keys()) + (list(monthly_d.keys()) if 'dynamic' in results else [])))

    if 'dynamic' in results:
        print(f"  {'Month':>8s}  {'Static $':>10s}  {'Static %':>8s}  {'Dyn $':>10s}  {'Dyn %':>8s}  {'DD(S)':>6s}  {'DD(D)':>6s}")
        print(f"  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*6}  {'─'*6}")
        for m in all_months:
            ms = monthly_s.get(m, {})
            md = monthly_d.get(m, {})
            print(f"  {m:>8s}  ${ms.get('abs_return',0):>+9,.0f}  {ms.get('pct_return',0):>+7.1f}%"
                  f"  ${md.get('abs_return',0):>+9,.0f}  {md.get('pct_return',0):>+7.1f}%"
                  f"  {ms.get('max_dd_pct',0):>5.1f}%  {md.get('max_dd_pct',0):>5.1f}%")
    else:
        print(f"  {'Month':>8s}  {'Return $':>10s}  {'Return %':>8s}  {'MaxDD':>6s}")
        print(f"  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*6}")
        for m in all_months:
            ms = monthly_s.get(m, {})
            print(f"  {m:>8s}  ${ms.get('abs_return',0):>+9,.0f}  {ms.get('pct_return',0):>+7.1f}%  {ms.get('max_dd_pct',0):>5.1f}%")

    # Totals
    total_s = sum(ms.get('abs_return', 0) for ms in monthly_s.values())
    total_s_pct = (extra_s['final_equity'] / 200_000 - 1) * 100
    if 'dynamic' in results:
        total_d = sum(md.get('abs_return', 0) for md in monthly_d.values())
        total_d_pct = (extra_d['final_equity'] / 200_000 - 1) * 100
        print(f"  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*6}  {'─'*6}")
        print(f"  {'TOTAL':>8s}  ${total_s:>+9,.0f}  {total_s_pct:>+7.1f}%  ${total_d:>+9,.0f}  {total_d_pct:>+7.1f}%"
              f"  {metrics_s.max_drawdown_pct:>5.1f}%  {metrics_d.max_drawdown_pct:>5.1f}%")
    else:
        print(f"  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*6}")
        print(f"  {'TOTAL':>8s}  ${total_s:>+9,.0f}  {total_s_pct:>+7.1f}%  {metrics_s.max_drawdown_pct:>5.1f}%")

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--portfolios", type=str, default="s80+s81,super5,s58",
                        help="Comma-separated portfolio names")
    args = parser.parse_args()

    # Load heatmap for dynamic weights
    heatmap_path = "results/v4/regime_heatmap.json"
    heatmap = None
    if os.path.exists(heatmap_path):
        with open(heatmap_path) as f:
            heatmap = json.load(f)['heatmap']
        print(f"Loaded regime heatmap from {heatmap_path}")

    def make_allocator(strategies, base_weights):
        if heatmap is None:
            return None
        return DynamicWeightAllocator.from_heatmap(
            heatmap=heatmap,
            strategies=strategies,
            base_weights=base_weights,
            min_trades=15,
            smoothing_alpha=0.0,  # No smoothing for backtest — instant weight changes
        )

    portfolios_to_run = [p.strip() for p in args.portfolios.split(",")]

    all_results = {}
    for name in portfolios_to_run:
        if name not in PORTFOLIOS:
            print(f"Unknown portfolio: {name}, skipping")
            continue
        result = run_portfolio(name, PORTFOLIOS[name], args.months, allocator_factory=make_allocator)
        all_results[name] = result
        gc.collect()

    # Summary comparison
    print(f"\n\n{'='*70}")
    print(f"  SUMMARY: STATIC vs DYNAMIC ({args.months}mo)")
    print(f"{'='*70}")
    print(f"  {'Portfolio':>12s}  {'S.Return':>8s}  {'D.Return':>8s}  {'S.Sharpe':>8s}  {'D.Sharpe':>8s}  {'S.MaxDD':>7s}  {'D.MaxDD':>7s}  {'S.Trades':>8s}  {'D.Trades':>8s}")
    print(f"  {'─'*12}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*8}  {'─'*8}")

    for name, res in all_results.items():
        ms = res['static']['metrics']
        es = res['static']['extra']
        s_ret = (es['final_equity'] / 200_000 - 1) * 100

        if 'dynamic' in res:
            md = res['dynamic']['metrics']
            ed = res['dynamic']['extra']
            d_ret = (ed['final_equity'] / 200_000 - 1) * 100
            print(f"  {name:>12s}  {s_ret:>+7.1f}%  {d_ret:>+7.1f}%  {ms.sharpe_ratio:>8.2f}  {md.sharpe_ratio:>8.2f}"
                  f"  {ms.max_drawdown_pct:>6.1f}%  {md.max_drawdown_pct:>6.1f}%  {ms.total_trades:>8d}  {md.total_trades:>8d}")
        else:
            print(f"  {name:>12s}  {s_ret:>+7.1f}%  {'N/A':>8s}  {ms.sharpe_ratio:>8.2f}  {'N/A':>8s}"
                  f"  {ms.max_drawdown_pct:>6.1f}%  {'N/A':>7s}  {ms.total_trades:>8d}  {'N/A':>8s}")


if __name__ == "__main__":
    main()
