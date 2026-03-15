#!/usr/bin/env python3
"""S57 Tuning — Compare dedup-only vs concentration-exempt vs both.

Tests 4 variants on key portfolios:
  A0: default (dedup ON, concentration counts all)
  B1: dedup-only relaxation (skip dedup, concentration counts all)
  B2: concentration-only relaxation (dedup ON, concentration skips combined)
  B3: both relaxed (monkey-patch behavior: dedup OFF + concentration skips combined)
"""
import sys, time
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
    peak = initial
    max_dd = 0.0
    for e in eq:
        if e > peak: peak = e
        dd = (e - peak) / peak
        if dd < max_dd: max_dd = dd
    return {
        'pct_return': pct_return,
        'max_dd_pct': max_dd * 100,
        'total_trades': len(state.position_manager.closed_trades),
    }


def main():
    months = 60
    capital = 200_000

    tokens_perp = discover_tokens("perp")
    tokens_spot = discover_tokens("spot")
    tokens_combined = sorted(set(tokens_perp) & set(tokens_spot))

    needed = {
        's56': ('perp', tokens_perp),
        's57': ('combined', tokens_combined),
        's59': ('perp', tokens_perp),
        's60': ('perp', tokens_perp),
        's62': ('perp', tokens_perp),
        's63': ('perp', tokens_perp),
        's65': ('perp', tokens_perp),
        's69': ('perp', tokens_perp),
        's72': ('perp', tokens_perp),
        's75': ('perp', tokens_perp),
        's76': ('perp', tokens_perp),
    }

    base_config = PortfolioConfig(
        capital=capital, max_portfolio_positions=60,
        concentration_limit=0.10, adv_cap_pct=0.05,
        min_position_usd=200.0, exchange="binance",
        base_spread_bps=3.0, impact_coeff=0.03, seed=42,
    )

    print("  Precomputing signals...")
    all_sigs = {}
    strat_specs = {}
    for sid, (market, toks) in needed.items():
        spec = StrategySpec(strategy_id=sid, weight=1.0, max_positions=15, market=market)
        strat_specs[sid] = spec
        t0 = time.time()
        all_sigs[sid] = precompute_strategy_signals(spec, toks, base_config, months)
        print(f"    {sid}: {len(all_sigs[sid])} tokens ({time.time()-t0:.1f}s)")

    # Save original method
    original_tmft = PositionManager.total_margin_for_token

    def exempt_combined_tmft(self, token):
        """Skip combined (linked) positions in margin calculation."""
        margin = 0.0
        for p in self.open_positions:
            if p.token == token:
                if p.linked_position_id:
                    continue
                margin += p.margin_usd
        return margin

    def no_dedup_tmft(self, token):
        return 0.0

    # All 13 s57-containing portfolios
    portfolios = {
        's58':          (['s56', 's57'], 40),
        's58+s60':      (['s56', 's57', 's60'], 45),
        's58+s62':      (['s56', 's57', 's62'], 50),
        's58+s63':      (['s56', 's57', 's63'], 50),
        's58+s65':      (['s56', 's57', 's65'], 50),
        's58+s59':      (['s56', 's57', 's59'], 50),
        '4-edge':       (['s56', 's57', 's63', 's65'], 60),
        's58+s69':      (['s69', 's57'], 40),
        's58+s72':      (['s69', 's57', 's72'], 50),
        's58+s75':      (['s56', 's57', 's75'], 50),
        's58+s76':      (['s76', 's57'], 40),
        '4-edge+ptp':   (['s76', 's57', 's63', 's65'], 60),
        '4-edge-conv':  (['s56', 's57', 's63', 's65'], 60),
    }

    # Variants to test
    # We test with the production code (config.dedup_mode) for dedup-only,
    # and monkey-patch for concentration-only and both.
    variants = [
        ('A0_default',    'default',          original_tmft),         # both ON
        ('B1_dedup_only', 'none',             original_tmft),         # dedup OFF, conc ON
        ('B2_conc_only',  'default',          exempt_combined_tmft),  # dedup ON, conc exempt_combined
        ('B3_both',       'none',             exempt_combined_tmft),  # both relaxed (monkey-patch A1+conc)
        ('B4_monkey_A1',  'default',          no_dedup_tmft),         # original monkey-patch A1 (no dedup via tmft=0)
        ('B5_monkey_A2',  'default',          exempt_combined_tmft),  # original monkey-patch A2 (same as B2)
    ]

    print(f"\n{'Portfolio':<14} {'Variant':<16} {'Return%':>10} {'MaxDD%':>8} {'Trades':>7}")
    print("-" * 60)

    best_per_portfolio = {}

    for pname, (strat_ids, max_pos) in portfolios.items():
        missing = [s for s in strat_ids if s not in all_sigs or len(all_sigs[s]) == 0]
        if missing:
            print(f"{pname:<14} SKIP (missing: {missing})")
            continue

        sigs = {sid: all_sigs[sid] for sid in strat_ids}
        specs = {sid: strat_specs[sid] for sid in strat_ids}
        results = {}

        for vname, dedup_mode, tmft_fn in variants:
            PositionManager.total_margin_for_token = tmft_fn
            config = PortfolioConfig(
                capital=capital, max_portfolio_positions=max_pos,
                concentration_limit=0.10, adv_cap_pct=0.05,
                min_position_usd=200.0, exchange="binance",
                base_spread_bps=3.0, impact_coeff=0.03, seed=42,
                dedup_mode=dedup_mode,
            )
            r = run_and_measure(sigs, specs, config)
            if r:
                results[vname] = r
                print(f"{pname:<14} {vname:<16} {r['pct_return']:>9.0f}% {r['max_dd_pct']:>7.1f}% {r['total_trades']:>7}")

        # Determine best: highest return where DD doesn't worsen more than 2pp vs A0
        if 'A0_default' in results:
            a0 = results['A0_default']
            best = 'A0_default'
            for vname, r in results.items():
                if vname == 'A0_default':
                    continue
                dd_delta = r['max_dd_pct'] - a0['max_dd_pct']
                ret_delta = r['pct_return'] - a0['pct_return']
                if ret_delta > 0 and dd_delta > -2.0:
                    if best == 'A0_default' or r['pct_return'] > results[best]['pct_return']:
                        best = vname
            best_per_portfolio[pname] = best
            print(f"{'':14} → BEST: {best}")

        print()

    PositionManager.total_margin_for_token = original_tmft

    # Summary
    print("\n  SUMMARY — Best variant per portfolio:")
    from collections import Counter
    counts = Counter(best_per_portfolio.values())
    for v, cnt in counts.most_common():
        print(f"    {v}: {cnt} portfolios")
    print()
    for pname, best in best_per_portfolio.items():
        print(f"    {pname:<14} → {best}")


if __name__ == "__main__":
    main()
