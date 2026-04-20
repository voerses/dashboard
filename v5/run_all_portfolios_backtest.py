#!/usr/bin/env python3
"""Run all paper trading portfolios through V4 backtest for comparison."""
import sys, os, gc, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))
import numpy as np
import pandas as pd
from v5.config import PortfolioConfig, StrategySpec
from v5.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v5.simulator import build_unified_index, SimulationState, _process_exits, _process_entries, _process_margin_calls, _record_equity_snapshot, _close_all_remaining
from v5.report import compute_portfolio_metrics
from v5.dynamic_weights import DynamicWeightAllocator
# All portfolios from multi_v4_paper.json
PORTFOLIOS = {
    "s58": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
    },
    "s60": {
        "strategies": [("s60", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.10,
    },
    "s58+s60": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 0.5, "perp", 10, "per_token"),
        ],
        "max_portfolio_positions": 45,
        "concentration_limit": 0.10,
    },
    "s58+s62": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s62", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
    "s58+s63": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
    "s58+s65": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
    "s58+s59": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s59", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
    "4-edge": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60,
        "concentration_limit": 0.10,
    },
    "s69": {
        "strategies": [("s69", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.10,
    },
    "s58+s69": {
        "strategies": [
            ("s69", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
    },
    "s72": {
        "strategies": [("s72", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.10,
    },
    "s58+s72": {
        "strategies": [
            ("s69", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s72", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
    "s58+s75": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s75", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
        "concentration_limit": 0.10,
    },
    "s76": {
        "strategies": [("s76", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
        "concentration_limit": 0.10,
    },
    "s58+s76": {
        "strategies": [
            ("s76", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
    },
    "4-edge+ptp": {
        "strategies": [
            ("s76", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60,
        "concentration_limit": 0.10,
    },
    "s80+s81": {
        "strategies": [
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 30,
        "concentration_limit": 0.10,
    },
    # Dynamic portfolios
    "s58-dyn": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
        "dynamic": True,
    },
    "super5-dyn": {
        "strategies": [
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 1.0, "perp", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 60,
        "concentration_limit": 0.10,
        "dynamic": True,
    },
}
def simulate_with_dynamic_weights(all_signals, strategy_specs, config, allocator=None):
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)
    state = SimulationState(initial_capital=config.capital)
    rng = np.random.RandomState(config.seed)
    btc_regime = None
    btc_bar_map = None
    for sid, token_sigs in all_signals.items():
        if 'BTC' in token_sigs:
            sig = token_sigs['BTC']
            btc_regime = sig.regime
            btc_bar_map = bar_maps.get('BTC')
            break
    for global_bar in range(n_bars):
        if allocator is not None and btc_regime is not None and btc_bar_map is not None:
            local_bar = int(btc_bar_map[global_bar])
            if 0 <= local_bar < len(btc_regime):
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
def compute_monthly(state):
    if not state.equity_snapshots:
        return {}
    ts = [s[0] for s in state.equity_snapshots]
    eq = [s[1] for s in state.equity_snapshots]
    eq_series = pd.Series(eq, index=pd.DatetimeIndex(ts))
    eq_daily = eq_series.resample('D').last().ffill()
    months = {}
    for period, grp in eq_daily.groupby(eq_daily.index.to_period('M')):
        if len(grp) < 2:
            continue
        start_eq = grp.iloc[0]
        end_eq = grp.iloc[-1]
        pct_ret = (end_eq / start_eq - 1) * 100
        abs_ret = end_eq - start_eq
        cummax = grp.cummax()
        dd = ((grp - cummax) / cummax).min() * 100
        months[str(period)] = {
            'abs_return': float(abs_ret),
            'pct_return': float(pct_ret),
            'max_dd_pct': float(dd),
        }
    return months
def run_one(name, pdef, months, data_end, heatmap, signal_cache):
    """Run one portfolio. Returns dict with metrics + monthly."""
    is_dynamic = pdef.get("dynamic", False)
    strat_list = pdef["strategies"]
    specs = {}
    for sid, weight, market, max_pos, stype in strat_list:
        specs[sid] = StrategySpec(
            strategy_id=sid, weight=weight, max_positions=max_pos,
            market=market,
            # M8 — adv_sizing_enabled deleted per AC-Sz6 (v4 opaque pipeline).
            # Equivalent M8 clamp: adv_cap_pct on PortfolioConfig below.
        )
    config = PortfolioConfig(
        capital=200_000,
        max_portfolio_positions=pdef["max_portfolio_positions"],
        concentration_limit=pdef["concentration_limit"],
        adv_cap_pct=0.05,
        seed=42,
        max_sizing_equity=2_000_000, stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )
    # Precompute signals (use cache to avoid redundant work)
    all_signals = {}
    for sid, spec in specs.items():
        cache_key = (sid, spec.market, months)
        if cache_key not in signal_cache:
            tokens = discover_tokens(spec.market)
            signal_cache[cache_key] = precompute_strategy_signals(
                spec, tokens, config, months, end_date=data_end
            )
        all_signals[sid] = signal_cache[cache_key]
    # Build allocator if dynamic
    allocator = None
    if is_dynamic and heatmap is not None:
        strategies = list(specs.keys())
        base_weights = {sid: specs[sid].weight for sid in strategies}
        allocator = DynamicWeightAllocator.from_heatmap(
            heatmap=heatmap, strategies=strategies,
            base_weights=base_weights, min_trades=15, smoothing_alpha=0.0,
        )
    # Reset weights
    for sid, weight, market, max_pos, stype in strat_list:
        specs[sid].weight = weight
    t0 = time.time()
    state = simulate_with_dynamic_weights(all_signals, specs, config, allocator)
    elapsed = time.time() - t0
    metrics, extra, eq_daily = compute_portfolio_metrics(state, 200_000)
    monthly = compute_monthly(state)
    total_ret = (extra['final_equity'] / 200_000 - 1) * 100
    print(f"  {name:>16s}: Return={total_ret:>+7.1f}%  Sharpe={metrics.sharpe_ratio:>5.2f}"
          f"  MaxDD={metrics.max_drawdown_pct:>6.1f}%  Trades={metrics.total_trades:>5d}  ({elapsed:.1f}s)")
    return {
        'name': name,
        'return_pct': total_ret,
        'return_abs': extra['final_equity'] - 200_000,
        'sharpe': metrics.sharpe_ratio,
        'sortino': metrics.sortino_ratio,
        'max_dd': metrics.max_drawdown_pct,
        'trades': metrics.total_trades,
        'calmar': metrics.calmar_ratio,
        'monthly': monthly,
        'dynamic': is_dynamic,
    }
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=4)
    args = parser.parse_args()
    heatmap = None
    heatmap_path = "results/v4/regime_heatmap.json"
    if os.path.exists(heatmap_path):
        with open(heatmap_path) as f:
            heatmap = json.load(f)['heatmap']
    data_end = infer_data_end_date("combined")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {args.months} months\n")
    signal_cache = {}
    results = []
    for name, pdef in PORTFOLIOS.items():
        try:
            r = run_one(name, pdef, args.months, data_end, heatmap, signal_cache)
            results.append(r)
        except Exception as e:
            print(f"  {name:>16s}: FAILED — {e}")
        gc.collect()
    # Sort by return
    results.sort(key=lambda x: x['return_pct'], reverse=True)
    # Print ranked table
    print(f"\n{'='*90}")
    print(f"  RANKED BY RETURN — {args.months}mo backtest, $200k capital")
    print(f"{'='*90}")
    print(f"  {'#':>2s}  {'Portfolio':>16s}  {'Return':>9s}  {'Return $':>11s}  {'Sharpe':>7s}  {'Sortino':>8s}  {'MaxDD':>7s}  {'Calmar':>7s}  {'Trades':>6s}")
    print(f"  {'─'*2}  {'─'*16}  {'─'*9}  {'─'*11}  {'─'*7}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*6}")
    for i, r in enumerate(results):
        tag = " *" if r['dynamic'] else ""
        print(f"  {i+1:>2d}  {r['name']+tag:>18s}  {r['return_pct']:>+8.1f}%  ${r['return_abs']:>+10,.0f}"
              f"  {r['sharpe']:>7.2f}  {r['sortino']:>8.2f}  {r['max_dd']:>6.1f}%  {r['calmar']:>7.1f}  {r['trades']:>6d}")
    print(f"\n  * = dynamic regime weights\n")
    # Monthly comparison for top 5
    top5 = results[:5]
    all_months = sorted(set(m for r in top5 for m in r['monthly'].keys()))
    # Filter to months with actual trading
    active_months = [m for m in all_months if any(r['monthly'].get(m, {}).get('abs_return', 0) != 0 for r in top5)]
    print(f"{'='*90}")
    print(f"  MONTHLY RETURNS (%) — TOP 5")
    print(f"{'='*90}")
    header = f"  {'Month':>8s}"
    for r in top5:
        header += f"  {r['name']:>14s}"
    print(header)
    print(f"  {'─'*8}" + f"  {'─'*14}" * len(top5))
    for m in active_months:
        row = f"  {m:>8s}"
        for r in top5:
            mr = r['monthly'].get(m, {})
            pct = mr.get('pct_return', 0)
            row += f"  {pct:>+13.1f}%"
        print(row)
    # Totals
    print(f"  {'─'*8}" + f"  {'─'*14}" * len(top5))
    row = f"  {'TOTAL':>8s}"
    for r in top5:
        row += f"  {r['return_pct']:>+13.1f}%"
    print(row)
    # Monthly absolute $
    print(f"\n{'='*90}")
    print(f"  MONTHLY RETURNS ($) — TOP 5")
    print(f"{'='*90}")
    header = f"  {'Month':>8s}"
    for r in top5:
        header += f"  {r['name']:>14s}"
    print(header)
    print(f"  {'─'*8}" + f"  {'─'*14}" * len(top5))
    for m in active_months:
        row = f"  {m:>8s}"
        for r in top5:
            mr = r['monthly'].get(m, {})
            amt = mr.get('abs_return', 0)
            row += f"  ${amt:>+12,.0f}"
        print(row)
    print(f"  {'─'*8}" + f"  {'─'*14}" * len(top5))
    row = f"  {'TOTAL':>8s}"
    for r in top5:
        row += f"  ${r['return_abs']:>+12,.0f}"
    print(row)
    # Save results to JSON
    class NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, (np.floating,)): return float(obj)
            return super().default(obj)
    out = []
    for r in results:
        out.append({k: v for k, v in r.items() if k != 'monthly'})
        out[-1]['monthly'] = r['monthly']
    with open("results/v4/all_portfolios_4mo.json", "w") as f:
        json.dump(out, f, indent=2, cls=NumpyEncoder)
    print(f"\nResults saved to results/v4/all_portfolios_4mo.json")
if __name__ == "__main__":
    main()
