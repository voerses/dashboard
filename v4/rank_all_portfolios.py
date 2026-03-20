#!/usr/bin/env python3
"""Rank ALL strategies and portfolios — each period starts fresh at $200K.

Runs 4 separate backtests per strategy (months=60, 12, 3, 1),
each starting with $200K capital. Ranked by % return.

Saves to results/v4/portfolio_rankings.json
"""
import sys, os, gc, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import (
    simulate_portfolio, build_unified_index, SimulationState,
    _process_exits, _process_entries, _process_margin_calls, _record_equity_snapshot,
    _close_all_remaining,
)
from v4.report import compute_portfolio_metrics
from v4.dynamic_weights import DynamicWeightAllocator

CAPITAL = 200_000

# ── Solo strategies ────────────────────────────────────────────────
SOLO = {
    "s56": ("s56", "perp", "per_token"),
    "s57": ("s57", "combined", "per_token"),
    "s59": ("s59", "perp", "per_token"),
    "s60": ("s60", "perp", "per_token"),
    "s62": ("s62", "perp", "per_token"),
    "s63": ("s63", "perp", "per_token"),
    "s65": ("s65", "perp", "per_token"),
    "s69": ("s69", "perp", "per_token"),
    "s72": ("s72", "perp", "per_token"),
    "s75": ("s75", "perp", "per_token"),
    "s76": ("s76", "perp", "per_token"),
}

PERIODS = [
    ("all_time", 60),
    ("last_12mo", 12),
    ("last_3mo", 3),
    ("last_1mo", 1),
]


def load_paper_portfolios():
    """Load combined portfolio definitions from paper config."""
    with open("configs/multi_v4_paper.json") as f:
        cfg = json.load(f)
    portfolios = {}
    for p in cfg["portfolios"]:
        name = p["pool_name"]
        strats = []
        for s in p["strategies"]:
            stype = s.get("strategy_type", "per_token")
            strats.append((s["strategy_id"], s["weight"], s["market"], s["max_positions"], stype))
        pdef = {
            "strategies": strats,
            "max_portfolio_positions": p["max_portfolio_positions"],
            "concentration_limit": p.get("concentration_limit", cfg["shared"].get("concentration_limit", 1.0)),
        }
        if p.get("dynamic_weights"):
            pdef["dynamic"] = True
        if p.get("conviction_mode"):
            pdef["conviction_mode"] = p["conviction_mode"]
        portfolios[name] = pdef
    return portfolios


def build_all_portfolios():
    """Build complete dict of all solos + combos."""
    all_p = {}
    for name, (sid, market, stype) in SOLO.items():
        all_p[name] = {
            "strategies": [(sid, 1.0, market, 15, stype)],
            "max_portfolio_positions": 15,
        }
    all_p.update(load_paper_portfolios())
    return all_p


def simulate_dynamic(all_signals, strategy_specs, config, allocator):
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)
    state = SimulationState(initial_capital=config.capital)
    rng = np.random.RandomState(config.seed)
    btc_regime, btc_bar_map = None, None
    for sid, token_sigs in all_signals.items():
        if "BTC" in token_sigs:
            btc_regime = token_sigs["BTC"].regime
            btc_bar_map = bar_maps.get("BTC")
            break
    for global_bar in range(n_bars):
        if allocator and btc_regime is not None and btc_bar_map is not None:
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


def run_backtest(pdef, months, data_end, heatmap):
    """Run one backtest starting at $200K. Returns (pct_return, dollar_pnl, max_dd, trades, trading_period)."""
    strat_list = pdef["strategies"]
    is_dynamic = pdef.get("dynamic", False)
    conviction_mode = pdef.get("conviction_mode", None)

    specs = {}
    for sid, weight, market, max_pos, stype in strat_list:
        specs[sid] = StrategySpec(
            strategy_id=sid, weight=weight, max_positions=max_pos,
            market=market, strategy_type=stype,
            adv_sizing_enabled=True, adv_sizing_base=75_000_000,
        )
    config = PortfolioConfig(
        capital=CAPITAL,
        max_portfolio_positions=pdef["max_portfolio_positions"],
        concentration_limit=pdef.get("concentration_limit", 1.0),
        adv_cap_pct=0.05, seed=42,
        max_sizing_equity=2_000_000, stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )
    if conviction_mode:
        config.conviction_mode = conviction_mode

    all_signals = {}
    for sid, spec in specs.items():
        tokens = discover_tokens(spec.market)
        all_signals[sid] = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)

    allocator = None
    if is_dynamic and heatmap is not None:
        strategies = list(specs.keys())
        base_weights = {sid: specs[sid].weight for sid in strategies}
        allocator = DynamicWeightAllocator.from_heatmap(
            heatmap=heatmap, strategies=strategies,
            base_weights=base_weights, min_trades=15, smoothing_alpha=0.0,
        )
        for sid, weight, market, max_pos, stype in strat_list:
            specs[sid].weight = weight

    if is_dynamic:
        state = simulate_dynamic(all_signals, specs, config, allocator)
    else:
        state = simulate_portfolio(all_signals, specs, config)

    m, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    final_eq = extra["final_equity"]
    pnl = final_eq - CAPITAL
    pct_ret = (final_eq / CAPITAL - 1) * 100

    # Trading period
    eq_ts = [s[0] for s in state.equity_snapshots]
    t_start = str(pd.Timestamp(eq_ts[0]).date()) if eq_ts else ""
    t_end = str(pd.Timestamp(eq_ts[-1]).date()) if eq_ts else ""

    return {
        "pct_return": float(pct_ret),
        "dollar_pnl": float(pnl),
        "max_dd_pct": float(m.max_drawdown_pct),
        "trades": int(m.total_trades),
        "trading_period": f"{t_start}→{t_end}",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolios", nargs="*", help="Specific names")
    parser.add_argument("--periods", nargs="*", help="Specific periods: all_time last_12mo last_3mo last_1mo")
    args = parser.parse_args()

    all_portfolios = build_all_portfolios()
    port_names = args.portfolios if args.portfolios else list(all_portfolios.keys())
    port_names = [p for p in port_names if p in all_portfolios]

    sel_periods = args.periods if args.periods else [p[0] for p in PERIODS]
    periods = [(name, mo) for name, mo in PERIODS if name in sel_periods]

    heatmap = None
    heatmap_path = "results/v4/regime_heatmap.json"
    if os.path.exists(heatmap_path):
        with open(heatmap_path) as f:
            heatmap = json.load(f)["heatmap"]

    data_end = infer_data_end_date("combined")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Capital:  ${CAPITAL:,} per backtest (fresh for each period)")
    print(f"Periods:  {', '.join(p[0] for p in periods)}")
    print(f"Entries:  {len(port_names)} strategies/portfolios\n")

    # results[name][period_name] = {...}
    results = {}

    for period_name, months in periods:
        print(f"\n{'─'*80}")
        print(f"  PERIOD: {period_name} (months={months})")
        print(f"{'─'*80}")

        for i, name in enumerate(port_names):
            pdef = all_portfolios[name]
            print(f"  [{i+1}/{len(port_names)}] {name}...", end="", flush=True)

            if name not in results:
                results[name] = {
                    "strategies": [s[0] for s in pdef["strategies"]],
                    "is_dynamic": pdef.get("dynamic", False),
                    "conviction_mode": pdef.get("conviction_mode"),
                }

            try:
                r = run_backtest(pdef, months, data_end, heatmap)
                results[name][period_name] = r
                print(f"  ret={r['pct_return']:>+,.0f}%  PnL=${r['dollar_pnl']:>+,.0f}  "
                      f"DD={r['max_dd_pct']:.1f}%  trades={r['trades']}")
            except Exception as e:
                results[name][period_name] = None
                print(f"  FAILED: {e}")

            gc.collect()

    # ── Print Rankings ──
    period_labels = {
        "all_time": "ALL TIME (~5yr trading, $200K start)",
        "last_12mo": "LAST 12 MONTHS ($200K start)",
        "last_3mo": "LAST 3 MONTHS ($200K start)",
        "last_1mo": "LAST MONTH / MARCH 2026 ($200K start)",
    }

    for period_name, _ in periods:
        label = period_labels.get(period_name, period_name)
        valid = [(n, results[n][period_name]) for n in port_names
                 if results.get(n, {}).get(period_name)]
        valid.sort(key=lambda x: x[1]["pct_return"], reverse=True)

        if not valid:
            continue

        print(f"\n  {'='*100}")
        print(f"  {label}")
        print(f"  {'='*100}")
        print(f"  {'#':>2s}  {'Name':>16s}  {'Return %':>12s}  {'PnL ($)':>15s}  "
              f"{'MaxDD':>7s}  {'Trades':>6s}")
        print(f"  {'─'*2}  {'─'*16}  {'─'*12}  {'─'*15}  {'─'*7}  {'─'*6}")

        for rank, (name, r) in enumerate(valid):
            tag = ""
            if results[name].get("is_dynamic"): tag = " *"
            elif results[name].get("conviction_mode"): tag = " ^"
            print(f"  {rank+1:>2d}  {name+tag:>16s}  {r['pct_return']:>+11,.0f}%  "
                  f"${r['dollar_pnl']:>13,.0f}  {r['max_dd_pct']:>6.1f}%  {r['trades']:>6d}")

    print(f"\n  * = dynamic weights    ^ = conviction mode")
    print(f"  All periods start fresh at ${CAPITAL:,}\n")

    # Save
    out_path = "results/v4/portfolio_rankings.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Saved to {out_path}")


if __name__ == "__main__":
    main()
