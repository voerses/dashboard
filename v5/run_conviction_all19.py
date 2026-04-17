#!/usr/bin/env python3
"""Conviction scoring test across ALL 19 paper trading portfolios.
Compares shuffle vs ranked vs hybrid for every portfolio.
Single-strategy portfolios included as sanity check (ordering shouldn't matter).
"""
import sys, os, gc, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))
import numpy as np
import pandas as pd
from v5.config import PortfolioConfig, StrategySpec
from v5.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v5.simulator import simulate_portfolio
from v5.report import compute_portfolio_metrics
# All 19 portfolios from multi_v4_paper.json
PORTFOLIOS = {
    # Single-strategy (conviction ordering = no effect expected)
    "s60": {
        "strategies": [("s60", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15, "concentration_limit": 0.10,
    },
    "s69": {
        "strategies": [("s69", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15, "concentration_limit": 0.10,
    },
    "s72": {
        "strategies": [("s72", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15, "concentration_limit": 0.10,
    },
    "s76": {
        "strategies": [("s76", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15, "concentration_limit": 0.10,
    },
    # 2-strategy combos
    "s58": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40, "concentration_limit": 0.10,
    },
    "s58+s69": {
        "strategies": [
            ("s69", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40, "concentration_limit": 0.10,
    },
    "s58+s76": {
        "strategies": [
            ("s76", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 40, "concentration_limit": 0.10,
    },
    "s80+s81": {
        "strategies": [
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 30, "concentration_limit": 0.10,
    },
    # 3-strategy combos
    "s58+s60": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 0.5, "perp", 10, "per_token"),
        ],
        "max_portfolio_positions": 45, "concentration_limit": 0.10,
    },
    "s58+s62": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s62", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50, "concentration_limit": 0.10,
    },
    "s58+s63": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50, "concentration_limit": 0.10,
    },
    "s58+s65": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50, "concentration_limit": 0.10,
    },
    "s58+s59": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s59", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50, "concentration_limit": 0.10,
    },
    "s58+s75": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s75", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50, "concentration_limit": 0.10,
    },
    "s58+s72": {
        "strategies": [
            ("s69", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s72", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50, "concentration_limit": 0.10,
    },
    # 4-strategy combos
    "4-edge": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60, "concentration_limit": 0.10,
    },
    "4-edge+ptp": {
        "strategies": [
            ("s76", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60, "concentration_limit": 0.10,
    },
    # 5-strategy combos
    "super5": {
        "strategies": [
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 1.0, "perp", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 40, "concentration_limit": 0.10,
    },
    # Dynamic (test static conviction only — dynamic weights are separate concern)
    "super5-dyn": {
        "strategies": [
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s60", 1.0, "perp", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s80", 1.0, "perp", 15, "portfolio"),
            ("s81", 1.0, "perp", 15, "portfolio"),
        ],
        "max_portfolio_positions": 60, "concentration_limit": 0.10,
    },
}
MODES = ["shuffle", "ranked", "hybrid"]
def extract_metrics(state, capital=200_000):
    metrics, extra, _ = compute_portfolio_metrics(state, capital)
    total_ret = (extra['final_equity'] / capital - 1) * 100
    # Hourly DD
    if state.equity_snapshots:
        eq_arr = np.array([s[1] for s in state.equity_snapshots])
        cummax = np.maximum.accumulate(eq_arr)
        dd_arr = (eq_arr - cummax) / np.maximum(cummax, 1e-10)
        hourly_max_dd = float(np.min(dd_arr)) * 100
    else:
        hourly_max_dd = 0.0
    return {
        'return_pct': total_ret,
        'sharpe': metrics.sharpe_ratio,
        'calmar': metrics.calmar_ratio,
        'max_dd_hourly': hourly_max_dd,
        'trades': metrics.total_trades,
        'rej_total': state.rejections.total(),
        'rej_conviction': state.rejections.conviction,
    }
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=4)
    args = parser.parse_args()
    data_end = infer_data_end_date("combined")
    print(f"Data end: {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {args.months} months\n")
    signal_cache = {}
    all_results = {}
    for name, pdef in PORTFOLIOS.items():
        strat_list = pdef["strategies"]
        n_strats = len(strat_list)
        print(f"  {name} ({n_strats} strategies)...", end=" ", flush=True)
        # Precompute signals (cached)
        specs_base = {}
        for sid, weight, market, max_pos, stype in strat_list:
            specs_base[sid] = StrategySpec(
                strategy_id=sid, weight=weight, max_positions=max_pos,
                market=market,
            )
        base_config = PortfolioConfig(
            capital=200_000,
            max_portfolio_positions=pdef["max_portfolio_positions"],
            concentration_limit=pdef["concentration_limit"],
            adv_cap_pct=0.05,
        )
        all_signals = {}
        for sid, spec in specs_base.items():
            cache_key = (sid, spec.market, args.months)
            if cache_key not in signal_cache:
                tokens = discover_tokens(spec.market)
                signal_cache[cache_key] = precompute_strategy_signals(
                    spec, tokens, base_config, args.months, end_date=data_end
                )
            all_signals[sid] = signal_cache[cache_key]
        mode_results = {}
        for mode in MODES:
            specs = {sid: StrategySpec(
                strategy_id=sid, weight=w, max_positions=mp,
                market=mkt,
            ) for sid, w, mkt, mp, st in strat_list}
            config = PortfolioConfig(
                capital=200_000,
                max_portfolio_positions=pdef["max_portfolio_positions"],
                concentration_limit=pdef["concentration_limit"],
                adv_cap_pct=0.05,
                seed=42,
            )
            try:
                state = simulate_portfolio(all_signals, specs, config)
                m = extract_metrics(state)
                mode_results[mode] = m
            except Exception as e:
                mode_results[mode] = {'error': str(e)}
        all_results[name] = mode_results
        gc.collect()
        # Print inline summary
        s = mode_results.get('shuffle', {})
        r = mode_results.get('ranked', {})
        h = mode_results.get('hybrid', {})
        if 'error' not in s:
            print(f"S={s['return_pct']:>+.0f}%  R={r['return_pct']:>+.0f}%  H={h['return_pct']:>+.0f}%")
        else:
            print("ERROR")
    # Print full comparison table
    print(f"\n{'='*110}")
    print(f"  CONVICTION SCORING — ALL 19 PORTFOLIOS ({args.months}mo backtest)")
    print(f"{'='*110}")
    print(f"  {'Portfolio':>14s}  #S  │{'SHUFFLE':^28s}│{'RANKED':^28s}│{'HYBRID':^28s}│ Best")
    print(f"  {'':>14s}      │{'Ret%':>8s} {'DD%':>7s} {'Calmar':>7s} {'Trd':>5s}│{'Ret%':>8s} {'DD%':>7s} {'Calmar':>7s} {'Trd':>5s}│{'Ret%':>8s} {'DD%':>7s} {'Calmar':>7s} {'Trd':>5s}│")
    print(f"  {'─'*14}  ──  │{'─'*28}│{'─'*28}│{'─'*28}│{'─'*7}")
    for name, pdef in PORTFOLIOS.items():
        n_strats = len(pdef["strategies"])
        mr = all_results[name]
        def fmt_mode(m):
            if 'error' in m:
                return f"{'ERR':>8s} {'':>7s} {'':>7s} {'':>5s}"
            return f"{m['return_pct']:>+7.0f}% {m['max_dd_hourly']:>6.1f}% {m['calmar']:>7.1f} {m['trades']:>5d}"
        s = mr.get('shuffle', {'error': True})
        r = mr.get('ranked', {'error': True})
        h = mr.get('hybrid', {'error': True})
        # Determine best mode by Calmar
        best = "—"
        if 'error' not in s and 'error' not in r and 'error' not in h:
            calmars = {'S': s['calmar'], 'R': r['calmar'], 'H': h['calmar']}
            best_key = max(calmars, key=calmars.get)
            best = {'S': 'shuffle', 'R': 'RANKED', 'H': 'HYBRID'}[best_key]
            # Mark if ranked/hybrid beats shuffle
            if best_key != 'S':
                s_cal = s['calmar'] if s['calmar'] != 0 else 1e-10
                imp = (calmars[best_key] / abs(s_cal) - 1) * 100
                best = f"{best} +{imp:.0f}%"
        print(f"  {name:>14s}  {n_strats:>2d}  │{fmt_mode(s)}│{fmt_mode(r)}│{fmt_mode(h)}│ {best}")
    # Summary stats
    print(f"\n  {'─'*80}")
    wins = {'shuffle': 0, 'ranked': 0, 'hybrid': 0}
    calmar_improvements = []
    dd_improvements = []
    for name, mr in all_results.items():
        s = mr.get('shuffle', {})
        r = mr.get('ranked', {})
        h = mr.get('hybrid', {})
        if 'error' in s or 'error' in r or 'error' in h:
            continue
        calmars = {'shuffle': s['calmar'], 'ranked': r['calmar'], 'hybrid': h['calmar']}
        winner = max(calmars, key=calmars.get)
        wins[winner] += 1
        # Track improvements vs shuffle
        best_non_shuffle = max(r['calmar'], h['calmar'])
        if s['calmar'] != 0:
            calmar_improvements.append((best_non_shuffle / abs(s['calmar']) - 1) * 100)
        best_dd = min(abs(r['max_dd_hourly']), abs(h['max_dd_hourly']))
        if abs(s['max_dd_hourly']) > 0:
            dd_improvements.append((best_dd / abs(s['max_dd_hourly']) - 1) * 100)
    print(f"  Calmar wins: shuffle={wins['shuffle']}, ranked={wins['ranked']}, hybrid={wins['hybrid']}")
    if calmar_improvements:
        arr = np.array(calmar_improvements)
        print(f"  Calmar improvement (best of ranked/hybrid vs shuffle): "
              f"mean={np.mean(arr):>+.1f}%, median={np.median(arr):>+.1f}%, "
              f"range=[{np.min(arr):>+.0f}%, {np.max(arr):>+.0f}%]")
    if dd_improvements:
        arr = np.array(dd_improvements)
        print(f"  DD improvement (best of ranked/hybrid vs shuffle): "
              f"mean={np.mean(arr):>+.1f}%, median={np.median(arr):>+.1f}%")
    # Save
    out = Path("results/v4/conviction_all19.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    class NE(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, (np.integer,)): return int(o)
            if isinstance(o, (np.floating,)): return float(o)
            if isinstance(o, np.ndarray): return o.tolist()
            return super().default(o)
    with open(out, 'w') as f:
        json.dump(all_results, f, indent=2, cls=NE)
    print(f"\n  Results saved to {out}")
if __name__ == "__main__":
    main()
