#!/usr/bin/env python3
"""Per-portfolio concentration_limit sweep.

Tests concentration_limit values [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.50, 1.0]
on all multi-strategy portfolios across 3 periods (12mo, 3mo, 1mo).
Reports optimal value per portfolio per period.

Usage:
    python tools/concentration_sweep.py
    python tools/concentration_sweep.py --periods 12 3
    python tools/concentration_sweep.py --portfolios s58 4-edge super5-dyn
"""
import sys, time, gc, json, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics
from v4.dynamic_weights import DynamicWeightAllocator

CAPITAL = 200_000
CONC_VALUES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.50, 1.0]

# All portfolios from multi_v4_paper.json (multi-strategy only — solo strategies
# don't benefit from concentration tuning since there's only one strategy)
PORTFOLIOS = {
    "s58":          {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15)], "max_pos": 40},
    "s58+s60":      {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s60", 0.5, "perp", 10)], "max_pos": 45},
    "s58+s62":      {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s62", 1.0, "perp", 15)], "max_pos": 50},
    "s58+s63":      {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s63", 1.0, "perp", 15)], "max_pos": 50},
    "s58+s65":      {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s65", 1.0, "perp", 15)], "max_pos": 50},
    "s58+s59":      {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s59", 1.0, "perp", 15)], "max_pos": 50},
    "4-edge":       {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s63", 1.0, "perp", 15), ("s65", 1.0, "perp", 15)], "max_pos": 60},
    "s58+s69":      {"strats": [("s69", 1.0, "perp", 15), ("s57", 1.0, "combined", 15)], "max_pos": 40},
    "s58+s72":      {"strats": [("s69", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s72", 1.0, "perp", 15)], "max_pos": 50},
    "s58+s75":      {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s75", 1.0, "perp", 15)], "max_pos": 50},
    "s58+s76":      {"strats": [("s76", 1.0, "perp", 15), ("s57", 1.0, "combined", 15)], "max_pos": 40},
    "4-edge+ptp":   {"strats": [("s76", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s63", 1.0, "perp", 15), ("s65", 1.0, "perp", 15)], "max_pos": 60},
    "4-edge-conv":  {"strats": [("s56", 1.0, "perp", 15), ("s57", 1.0, "combined", 15), ("s63", 1.0, "perp", 15), ("s65", 1.0, "perp", 15)], "max_pos": 60, "conviction_mode": "ranked"},
    "super5-conv":  {"strats": [("s57", 1.0, "combined", 15), ("s60", 1.0, "perp", 15), ("s63", 1.0, "perp", 15), ("s80", 1.0, "perp", 15, "portfolio"), ("s81", 1.0, "perp", 15, "portfolio")], "max_pos": 40, "conviction_mode": "hybrid", "conc_default": 0.10},
    "super5-dyn":   {"strats": [("s57", 1.0, "combined", 15), ("s60", 1.0, "perp", 15), ("s63", 1.0, "perp", 15), ("s80", 1.0, "perp", 15, "portfolio"), ("s81", 1.0, "perp", 15, "portfolio")], "max_pos": 60, "dynamic": True, "conc_default": 0.10},
    "s80+s81":      {"strats": [("s80", 1.0, "perp", 15, "portfolio"), ("s81", 1.0, "perp", 15, "portfolio")], "max_pos": 30, "conc_default": 0.10},
    "s80+s81-dyn":  {"strats": [("s80", 1.0, "perp", 15, "portfolio"), ("s81", 1.0, "perp", 15, "portfolio")], "max_pos": 30, "dynamic": True, "conc_default": 0.10},
}


def run_backtest(all_signals, pdef, months, data_end, conc_limit, heatmap=None):
    """Run one backtest with a specific concentration_limit. Returns metrics dict."""
    specs = {}
    for strat_tuple in pdef["strats"]:
        sid, weight, market, max_pos = strat_tuple[:4]
        stype = strat_tuple[4] if len(strat_tuple) > 4 else "per_token"
        specs[sid] = StrategySpec(
            strategy_id=sid, weight=weight, max_positions=max_pos,
            market=market, strategy_type=stype,
        )

    config = PortfolioConfig(
        capital=CAPITAL, max_portfolio_positions=pdef["max_pos"],
        concentration_limit=conc_limit, adv_cap_pct=0.05,
        min_position_usd=200.0, exchange="binance",
        base_spread_bps=3.0, impact_coeff=0.03, seed=42,
    )
    if pdef.get("conviction_mode"):
        config.conviction_mode = pdef["conviction_mode"]

    # Filter signals to this portfolio's strategies
    sigs = {sid: all_signals[sid] for sid in specs if sid in all_signals}
    if not sigs:
        return None

    # Dynamic weights
    is_dynamic = pdef.get("dynamic", False)
    if is_dynamic and heatmap is not None:
        from v4.rank_all_portfolios import simulate_dynamic, build_unified_index
        strategies = list(specs.keys())
        base_weights = {sid: specs[sid].weight for sid in strategies}
        allocator = DynamicWeightAllocator.from_heatmap(
            heatmap=heatmap, strategies=strategies,
            base_weights=base_weights, min_trades=15, smoothing_alpha=0.0,
        )
        state = simulate_dynamic(sigs, specs, config, allocator)
    else:
        state = simulate_portfolio(sigs, specs, config)

    m, extra, _ = compute_portfolio_metrics(state, CAPITAL)
    final_eq = extra["final_equity"]
    return {
        "pct_return": (final_eq / CAPITAL - 1) * 100,
        "max_dd_pct": m.max_drawdown_pct,
        "trades": m.total_trades,
        "calmar": m.calmar_ratio if hasattr(m, 'calmar_ratio') else 0.0,
        "conc_rejections": state.rejections.concentration,
        "partial_fills": state.partial_fills,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="*", type=int, default=[12, 3, 1],
                        help="Months to test (default: 12 3 1)")
    parser.add_argument("--portfolios", nargs="*",
                        help="Specific portfolios (default: all)")
    parser.add_argument("--values", nargs="*", type=float,
                        help="Concentration values to sweep (default: 0.05 0.10 0.15 0.20 0.25 0.30 0.50 1.0)")
    args = parser.parse_args()

    conc_values = args.values if args.values else CONC_VALUES
    port_names = args.portfolios if args.portfolios else list(PORTFOLIOS.keys())
    port_names = [p for p in port_names if p in PORTFOLIOS]
    periods = args.periods

    # Load heatmap for dynamic weights
    heatmap = None
    import os
    heatmap_path = "results/v4/regime_heatmap.json"
    if os.path.exists(heatmap_path):
        with open(heatmap_path) as f:
            heatmap = json.load(f)["heatmap"]

    data_end = infer_data_end_date("combined")
    print(f"  Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Capital: ${CAPITAL:,}")
    print(f"  Periods: {periods} months")
    print(f"  Concentration values: {conc_values}")
    print(f"  Portfolios: {len(port_names)}")

    # results[period][portfolio] = {conc_val: metrics_dict}
    all_results = {}

    for months in periods:
        print(f"\n{'='*100}")
        print(f"  PERIOD: {months} months")
        print(f"{'='*100}")

        period_results = {}

        for pname in port_names:
            pdef = PORTFOLIOS[pname]
            current_conc = pdef.get("conc_default", 1.0)
            print(f"\n  {pname} (current: {current_conc})")

            # Precompute signals for THIS portfolio only (memory-efficient)
            needed_sids = {}
            for st in pdef["strats"]:
                sid, _, market = st[0], st[1], st[2]
                stype = st[4] if len(st) > 4 else "per_token"
                needed_sids[sid] = (market, stype)

            all_signals = {}
            skip = False
            for sid, (market, stype) in needed_sids.items():
                spec = StrategySpec(strategy_id=sid, weight=1.0, max_positions=15,
                                    market=market, strategy_type=stype)
                tokens = discover_tokens(market)
                t0 = time.time()
                try:
                    sigs = precompute_strategy_signals(spec, tokens, PortfolioConfig(capital=CAPITAL), months, end_date=data_end)
                    all_signals[sid] = sigs
                    print(f"    {sid} ({market}): {len(sigs)} tokens ({time.time()-t0:.1f}s)")
                except Exception as e:
                    print(f"    {sid} ({market}): FAILED — {e}")
                    skip = True

            if skip or not all_signals:
                print(f"    SKIPPED (signal error)")
                del all_signals
                gc.collect()
                continue

            port_results = {}
            for conc_val in conc_values:
                try:
                    r = run_backtest(all_signals, pdef, months, data_end, conc_val, heatmap)
                    if r:
                        port_results[conc_val] = r
                        marker = " ←current" if conc_val == current_conc else ""
                        print(f"    conc={conc_val:.2f}  ret={r['pct_return']:>+8.0f}%  "
                              f"DD={r['max_dd_pct']:>6.1f}%  trades={r['trades']:>5}  "
                              f"conc_rej={r['conc_rejections']:>4}  partial={r['partial_fills']:>4}{marker}")
                except Exception as e:
                    print(f"    conc={conc_val:.2f}  FAILED: {e}")

            period_results[pname] = port_results

            # Free memory before next portfolio
            del all_signals
            gc.collect()

        all_results[months] = period_results

        # Print best per portfolio for this period
        print(f"\n  {'─'*80}")
        print(f"  BEST per portfolio ({months}mo):")
        print(f"  {'Portfolio':<16} {'Current':>8} {'Best':>8} {'Ret Δ':>10} {'DD Δ':>8} {'Trades Δ':>9}")
        print(f"  {'─'*16} {'─'*8} {'─'*8} {'─'*10} {'─'*8} {'─'*9}")

        for pname in port_names:
            pr = period_results.get(pname, {})
            if not pr:
                continue
            current_conc = PORTFOLIOS[pname].get("conc_default", 1.0)
            current_r = pr.get(current_conc)
            if not current_r:
                continue

            # Best = highest Calmar ratio (return / |max_dd|)
            best_val = current_conc
            best_calmar = abs(current_r["pct_return"]) / max(abs(current_r["max_dd_pct"]), 0.01)
            for val, r in pr.items():
                calmar = abs(r["pct_return"]) / max(abs(r["max_dd_pct"]), 0.01)
                if calmar > best_calmar:
                    best_val = val
                    best_calmar = calmar

            best_r = pr[best_val]
            ret_delta = best_r["pct_return"] - current_r["pct_return"]
            dd_delta = best_r["max_dd_pct"] - current_r["max_dd_pct"]
            trades_delta = best_r["trades"] - current_r["trades"]
            tag = "  ✓" if best_val != current_conc else ""
            print(f"  {pname:<16} {current_conc:>7.2f}  {best_val:>7.2f}  "
                  f"{ret_delta:>+9.0f}%  {dd_delta:>+7.1f}%  {trades_delta:>+8d}{tag}")

    # Final cross-period summary
    print(f"\n{'='*100}")
    print(f"  CROSS-PERIOD SUMMARY — Optimal concentration_limit per portfolio")
    print(f"{'='*100}")
    print(f"  {'Portfolio':<16} {'Current':>8}", end="")
    for months in periods:
        print(f"  {'Best '+str(months)+'mo':>10} {'Δret':>8}", end="")
    print(f"  {'Recommendation':>16}")
    print(f"  {'─'*16} {'─'*8}", end="")
    for _ in periods:
        print(f"  {'─'*10} {'─'*8}", end="")
    print(f"  {'─'*16}")

    recommendations = {}
    for pname in port_names:
        current_conc = PORTFOLIOS[pname].get("conc_default", 1.0)
        print(f"  {pname:<16} {current_conc:>7.2f}", end="")

        best_vals = []
        for months in periods:
            pr = all_results.get(months, {}).get(pname, {})
            current_r = pr.get(current_conc)
            if not current_r or not pr:
                print(f"  {'N/A':>10} {'':>8}", end="")
                continue

            best_val = current_conc
            best_ret = current_r["pct_return"]
            for val, r in pr.items():
                dd_worse = r["max_dd_pct"] - current_r["max_dd_pct"]
                if r["pct_return"] > best_ret and dd_worse > -3.0:
                    best_val = val
                    best_ret = r["pct_return"]

            ret_delta = pr[best_val]["pct_return"] - current_r["pct_return"]
            print(f"  {best_val:>10.2f} {ret_delta:>+7.0f}%", end="")
            best_vals.append(best_val)

        # Recommendation: most common best value, or current if no improvement
        if best_vals:
            from collections import Counter
            most_common = Counter(best_vals).most_common(1)[0][0]
            if most_common != current_conc:
                recommendations[pname] = most_common
                print(f"  → {most_common:.2f}")
            else:
                print(f"  (keep {current_conc:.2f})")
        else:
            print(f"  (no data)")

    # Save results
    import os
    out_path = "results/v4/concentration_sweep.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    save_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capital": CAPITAL,
        "conc_values": conc_values,
        "periods": periods,
        "recommendations": recommendations,
        "results": {},
    }
    for months in periods:
        save_data["results"][str(months)] = {}
        for pname in port_names:
            pr = all_results.get(months, {}).get(pname, {})
            save_data["results"][str(months)][pname] = {
                str(k): v for k, v in pr.items()
            }

    with open(out_path, "w") as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"\n  Saved to {out_path}")

    if recommendations:
        print(f"\n  {len(recommendations)} portfolios have better concentration_limit values:")
        for pname, val in recommendations.items():
            current = PORTFOLIOS[pname].get("conc_default", 1.0)
            print(f"    {pname}: {current} → {val}")


if __name__ == "__main__":
    main()
