#!/usr/bin/env python3
"""Test breakeven ratchet (BE=0.5) across ALL portfolios.

Precomputes signals once (shared cache), then runs each portfolio twice:
  1. Baseline (no breakeven)
  2. BE=0.5 (breakeven_atr=0.5 injected into all TokenBarArrays)

Prints a comparison table and saves results to JSON.
"""
import sys, os, gc, time, json, copy
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


# ── Portfolios to test ─────────────────────────────────────────────
# Individual strategies
PORTFOLIOS = {
    # Solo strategies
    "s60": {
        "strategies": [("s60", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
    },
    "s69": {
        "strategies": [("s69", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
    },
    "s72": {
        "strategies": [("s72", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
    },
    "s76": {
        "strategies": [("s76", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
    },
    # Combined strategies
    "s58": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
    },
    "s58+s60": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 0.5, "perp", 10, "per_token"),
        ],
        "max_portfolio_positions": 45,
    },
    "s58+s63": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
    },
    "s58+s65": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
    },
    "4-edge": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60,
    },
    "s58+s69": {
        "strategies": [
            ("s69", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
    },
    "s58+s72": {
        "strategies": [
            ("s69", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s72", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
    },
    "s58+s75": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s75", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
    },
    "s58+s76": {
        "strategies": [
            ("s76", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40,
    },
    "4-edge+ptp": {
        "strategies": [
            ("s76", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60,
    },
    "s80+s81": {
        "strategies": [
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 30,
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
        "dynamic": True,
    },
}


def simulate_portfolio_static(all_signals, strategy_specs, config):
    """Run simulation without dynamic weights (matches simulate_portfolio)."""
    from v5.simulator import simulate_portfolio
    return simulate_portfolio(all_signals, strategy_specs, config)


def simulate_portfolio_dynamic(all_signals, strategy_specs, config, allocator):
    """Run simulation with dynamic weights."""
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


def set_breakeven(all_signals, breakeven_atr):
    """Set breakeven_atr on all TokenBarArrays in all strategies."""
    for sid, token_sigs in all_signals.items():
        for token, sig in token_sigs.items():
            sig.breakeven_atr = breakeven_atr


def precompute_portfolio_signals(pdef, months, data_end):
    """Precompute signals for one portfolio. Returns (specs, config, all_signals)."""
    strat_list = pdef["strategies"]
    specs = {}
    for sid, weight, market, max_pos, stype in strat_list:
        specs[sid] = StrategySpec(
            strategy_id=sid, weight=weight, max_positions=max_pos,
            market=market,
        )

    config = PortfolioConfig(
        capital=200_000,
        max_portfolio_positions=pdef["max_portfolio_positions"],
        concentration_limit=pdef.get("concentration_limit", 0.10),
        adv_cap_pct=0.05,
        seed=42,
    )

    all_signals = {}
    for sid, spec in specs.items():
        tokens = discover_tokens(spec.market)
        all_signals[sid] = precompute_strategy_signals(
            spec, tokens, config, months, end_date=data_end
        )

    return specs, config, all_signals


def run_simulation(pdef, specs, config, all_signals, heatmap, breakeven_atr=0.0):
    """Run simulation with given signals and breakeven override."""
    is_dynamic = pdef.get("dynamic", False)
    strat_list = pdef["strategies"]

    set_breakeven(all_signals, breakeven_atr)

    allocator = None
    if is_dynamic and heatmap is not None:
        strategies = list(specs.keys())
        base_weights = {sid: specs[sid].weight for sid in strategies}
        allocator = DynamicWeightAllocator.from_heatmap(
            heatmap=heatmap, strategies=strategies,
            base_weights=base_weights, min_trades=15, smoothing_alpha=0.0,
        )

    # Reset weights for dynamic
    for sid, weight, market, max_pos, stype in strat_list:
        specs[sid].weight = weight

    t0 = time.time()
    if is_dynamic:
        state = simulate_portfolio_dynamic(all_signals, specs, config, allocator)
    else:
        state = simulate_portfolio_static(all_signals, specs, config)
    elapsed = time.time() - t0

    metrics, extra, eq_daily = compute_portfolio_metrics(state, 200_000)
    total_pnl = extra['final_equity'] - 200_000
    total_ret = (extra['final_equity'] / 200_000 - 1) * 100

    set_breakeven(all_signals, 0.0)

    return {
        'trades': int(metrics.total_trades),
        'win_rate': float(metrics.win_rate_pct),
        'payoff': float(metrics.payoff_ratio),
        'profit_factor': float(metrics.profit_factor),
        'total_pnl': float(total_pnl),
        'total_return_pct': float(total_ret),
        'max_dd_pct': float(metrics.max_drawdown_pct),
        'sharpe': float(metrics.sharpe_ratio),
        'sortino': float(metrics.sortino_ratio),
        'calmar': float(metrics.calmar_ratio),
        'elapsed': float(elapsed),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Test breakeven ratchet across all portfolios")
    parser.add_argument("--months", type=int, default=12, help="Lookback months (default 12)")
    parser.add_argument("--portfolios", nargs="*", help="Specific portfolios to test (default: all)")
    args = parser.parse_args()

    heatmap = None
    heatmap_path = "results/v4/regime_heatmap.json"
    if os.path.exists(heatmap_path):
        with open(heatmap_path) as f:
            heatmap = json.load(f)['heatmap']

    data_end = infer_data_end_date("combined")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {args.months} months")
    print(f"Capital:  $200,000 per portfolio\n")

    # Select portfolios
    if args.portfolios:
        port_names = [p for p in args.portfolios if p in PORTFOLIOS]
    else:
        port_names = list(PORTFOLIOS.keys())

    print(f"Testing {len(port_names)} portfolios: {', '.join(port_names)}\n")

    results = {}

    for i, name in enumerate(port_names):
        pdef = PORTFOLIOS[name]
        print(f"[{i+1}/{len(port_names)}] {name}")

        try:
            # Precompute signals ONCE for this portfolio
            specs, config, all_signals = precompute_portfolio_signals(pdef, args.months, data_end)

            # Baseline (no breakeven)
            baseline = run_simulation(pdef, specs, config, all_signals, heatmap, breakeven_atr=0.0)
            print(f"  Baseline:  PnL=${baseline['total_pnl']:>+12,.0f}  payoff={baseline['payoff']:.2f}x  "
                  f"MaxDD={baseline['max_dd_pct']:.1f}%  Calmar={baseline['calmar']:.1f}  "
                  f"trades={baseline['trades']}  ({baseline['elapsed']:.1f}s)")

            # BE=0.5
            be05 = run_simulation(pdef, specs, config, all_signals, heatmap, breakeven_atr=0.5)
            pnl_delta = (be05['total_pnl'] / max(baseline['total_pnl'], 1) - 1) * 100 if baseline['total_pnl'] > 0 else 0
            dd_delta = be05['max_dd_pct'] - baseline['max_dd_pct']
            print(f"  BE=0.5:    PnL=${be05['total_pnl']:>+12,.0f}  payoff={be05['payoff']:.2f}x  "
                  f"MaxDD={be05['max_dd_pct']:.1f}%  Calmar={be05['calmar']:.1f}  "
                  f"trades={be05['trades']}  ({be05['elapsed']:.1f}s)")
            print(f"  Delta:     PnL {pnl_delta:>+.0f}%  DD {dd_delta:>+.1f}pp  "
                  f"payoff {be05['payoff']-baseline['payoff']:>+.2f}x\n")

            results[name] = {'baseline': baseline, 'BE=0.5': be05}

            # Free signals to avoid OOM
            del all_signals, specs, config

        except Exception as e:
            print(f"  FAILED: {e}\n")
            import traceback
            traceback.print_exc()

        gc.collect()

    # ── Summary Table ──────────────────────────────────────────────
    print(f"\n{'='*130}")
    print(f"  BREAKEVEN RATCHET (BE=0.5) — COMPREHENSIVE TEST RESULTS")
    print(f"  {args.months}mo backtest, $200K capital, {len(results)} portfolios")
    print(f"{'='*130}")
    print(f"  {'Portfolio':>14s}  │  {'Baseline PnL':>13s}  {'BE PnL':>13s}  {'Δ PnL':>7s}  │"
          f"  {'Base DD':>7s}  {'BE DD':>6s}  {'Δ DD':>6s}  │"
          f"  {'Base Payoff':>11s}  {'BE Payoff':>9s}  │"
          f"  {'Base Calmar':>11s}  {'BE Calmar':>9s}")
    print(f"  {'─'*14}──┼──{'─'*13}──{'─'*13}──{'─'*7}──┼──{'─'*7}──{'─'*6}──{'─'*6}──┼──{'─'*11}──{'─'*9}──┼──{'─'*11}──{'─'*9}")

    # Sort by PnL delta
    sorted_names = sorted(results.keys(),
        key=lambda n: results[n]['BE=0.5']['total_pnl'] / max(results[n]['baseline']['total_pnl'], 1),
        reverse=True)

    for name in sorted_names:
        b = results[name]['baseline']
        be = results[name]['BE=0.5']
        pnl_d = (be['total_pnl'] / max(b['total_pnl'], 1) - 1) * 100 if b['total_pnl'] > 0 else 0
        dd_d = be['max_dd_pct'] - b['max_dd_pct']
        print(f"  {name:>14s}  │  ${b['total_pnl']:>11,.0f}  ${be['total_pnl']:>11,.0f}  {pnl_d:>+6.0f}%  │"
              f"  {b['max_dd_pct']:>6.1f}%  {be['max_dd_pct']:>5.1f}%  {dd_d:>+5.1f}  │"
              f"  {b['payoff']:>10.2f}x  {be['payoff']:>8.2f}x  │"
              f"  {b['calmar']:>10.1f}  {be['calmar']:>8.1f}")

    # ── Winners / Losers ──────────────────────────────────────────
    winners = [n for n in sorted_names if results[n]['BE=0.5']['total_pnl'] > results[n]['baseline']['total_pnl']]
    losers = [n for n in sorted_names if results[n]['BE=0.5']['total_pnl'] < results[n]['baseline']['total_pnl']]
    neutral = [n for n in sorted_names if results[n]['BE=0.5']['total_pnl'] == results[n]['baseline']['total_pnl']]

    print(f"\n  Winners ({len(winners)}): {', '.join(winners)}")
    if losers:
        print(f"  Losers  ({len(losers)}): {', '.join(losers)}")
    if neutral:
        print(f"  Neutral ({len(neutral)}): {', '.join(neutral)}")

    # ── DD Improvement ────────────────────────────────────────────
    # DD is negative; less negative = better
    dd_improved = [n for n in sorted_names if results[n]['BE=0.5']['max_dd_pct'] > results[n]['baseline']['max_dd_pct']]
    dd_worse = [n for n in sorted_names if results[n]['BE=0.5']['max_dd_pct'] < results[n]['baseline']['max_dd_pct']]
    print(f"\n  DD improved ({len(dd_improved)}): {', '.join(dd_improved)}")
    if dd_worse:
        print(f"  DD worse    ({len(dd_worse)}): {', '.join(dd_worse)}")

    # ── Save to JSON ──────────────────────────────────────────────
    out_path = f"results/v4/breakeven_all_portfolios_{args.months}mo.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
