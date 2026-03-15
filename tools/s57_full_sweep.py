#!/usr/bin/env python3
"""S57 Tuning — Full sweep: A0/A1/A2 across ALL 15 portfolios using s57."""
import sys, os, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.position import PositionManager


def run_and_measure(all_signals, specs, config):
    state = simulate_portfolio(all_signals, specs, config)
    eq = [e for _, e in state.equity_snapshots]
    if not eq: return None
    initial = config.capital
    final = eq[-1]
    pct_return = (final / initial - 1) * 100
    daily_eq = eq[::24] if len(eq) > 48 else eq
    if len(daily_eq) > 2:
        rets = np.diff(daily_eq) / np.array(daily_eq[:-1])
        sharpe = np.mean(rets) / (np.std(rets) + 1e-12) * np.sqrt(365)
    else:
        sharpe = 0.0
    peak = initial
    max_dd = 0.0
    for e in eq:
        if e > peak: peak = e
        dd = (e - peak) / peak
        if dd < max_dd: max_dd = dd
    return {
        'pct_return': pct_return, 'sharpe': sharpe,
        'max_dd_pct': max_dd * 100, 'total_trades': len(state.position_manager.closed_trades),
    }


def main():
    months = 60
    capital = 200_000
    config = PortfolioConfig(
        capital=capital, max_portfolio_positions=40,
        concentration_limit=0.10, adv_cap_pct=0.05,
        min_position_usd=200.0, exchange="binance",
        base_spread_bps=3.0, impact_coeff=0.03, seed=42,
    )

    tokens_perp = discover_tokens("perp")
    tokens_spot = discover_tokens("spot")
    tokens_combined = sorted(set(tokens_perp) & set(tokens_spot))

    # All strategies used across the 15 portfolios
    needed = {
        's56': 'perp', 's57': 'combined', 's59': 'perp', 's60': 'perp',
        's62': 'perp', 's63': 'perp', 's65': 'perp', 's69': 'perp',
        's72': 'perp', 's75': 'perp', 's76': 'perp', 's80': 'perp', 's81': 'perp',
    }

    print(f"\n  Precomputing {len(needed)} strategies ({months}mo, ${capital:,})...")
    all_sigs = {}
    strat_specs = {}
    for sid, market in needed.items():
        spec = StrategySpec(strategy_id=sid, weight=1.0, max_positions=15, market=market)
        strat_specs[sid] = spec
        toks = tokens_combined if market == "combined" else tokens_perp
        t0 = time.time()
        all_sigs[sid] = precompute_strategy_signals(spec, toks, config, months)
        print(f"    {sid}: {len(all_sigs[sid])} tokens ({time.time()-t0:.1f}s)")

    # All 15 portfolios from multi_v4_paper.json
    portfolios = {
        's58':          ['s56', 's57'],
        's58+s60':      ['s56', 's57', 's60'],
        's58+s62':      ['s56', 's57', 's62'],
        's58+s63':      ['s56', 's57', 's63'],
        's58+s65':      ['s56', 's57', 's65'],
        's58+s59':      ['s56', 's57', 's59'],
        '4-edge':       ['s56', 's57', 's63', 's65'],
        's58+s69':      ['s69', 's57'],
        's58+s72':      ['s69', 's57', 's72'],
        's58+s75':      ['s56', 's57', 's75'],
        's58+s76':      ['s76', 's57'],
        '4-edge+ptp':   ['s76', 's57', 's63', 's65'],
        'super5-dyn':   ['s57', 's60', 's63', 's80', 's81'],
        '4-edge-conv':  ['s56', 's57', 's63', 's65'],
        'super5-conv':  ['s57', 's60', 's63', 's80', 's81'],
    }

    original_tmft = PositionManager.total_margin_for_token

    def no_dedup(self, token):
        return 0.0

    def exempt_combined_dedup(self, token):
        margin = 0.0
        for p in self.open_positions:
            if p.token == token:
                if p.linked_position_id:
                    continue
                margin += p.margin_usd
        return margin

    variants = [
        ('A0', original_tmft),
        ('A1', no_dedup),
        ('A2', exempt_combined_dedup),
    ]

    all_results = {}

    print(f"\n{'Portfolio':<18} {'A0 Return%':>11} {'A0 DD%':>8} {'A1 Return%':>11} {'A1 DD%':>8} {'A2 Return%':>11} {'A2 DD%':>8} {'Best':>6}")
    print("-" * 100)

    for pname, strat_ids in portfolios.items():
        # Check all strategies have signals
        missing = [s for s in strat_ids if s not in all_sigs or len(all_sigs[s]) == 0]
        if missing:
            print(f"{pname:<18} SKIP (missing signals: {missing})")
            continue

        sigs = {sid: all_sigs[sid] for sid in strat_ids}
        specs = {sid: strat_specs[sid] for sid in strat_ids}

        row = {}
        for vname, dedup_fn in variants:
            PositionManager.total_margin_for_token = dedup_fn
            r = run_and_measure(sigs, specs, config)
            if r:
                row[vname] = r

        if len(row) == 3:
            # Determine best: highest return where DD doesn't worsen more than 2pp
            a0 = row['A0']
            best = 'A0'
            for v in ['A1', 'A2']:
                rv = row[v]
                dd_delta = rv['max_dd_pct'] - a0['max_dd_pct']
                ret_delta = rv['pct_return'] - a0['pct_return']
                # Better if: return improved AND dd didn't worsen by >2pp
                if ret_delta > 0 and dd_delta > -2.0:
                    if best == 'A0' or rv['pct_return'] > row[best]['pct_return']:
                        best = v

            print(f"{pname:<18} {row['A0']['pct_return']:>10.0f}% {row['A0']['max_dd_pct']:>7.1f}% {row['A1']['pct_return']:>10.0f}% {row['A1']['max_dd_pct']:>7.1f}% {row['A2']['pct_return']:>10.0f}% {row['A2']['max_dd_pct']:>7.1f}% {best:>6}")
            all_results[pname] = {'variants': row, 'best': best}

    PositionManager.total_margin_for_token = original_tmft

    # Summary
    print("\n  SUMMARY:")
    best_counts = {'A0': 0, 'A1': 0, 'A2': 0}
    for pname, data in all_results.items():
        best_counts[data['best']] += 1
    for v, cnt in best_counts.items():
        print(f"    {v}: best for {cnt} portfolios")

    # Save
    out_path = "results/v4/s57_full_dedup_sweep.json"
    with open(out_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
