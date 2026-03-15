#!/usr/bin/env python3
"""S57 Tuning — Validate dedup_mode on key portfolios (proper implementation)."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio


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
        's63': ('perp', tokens_perp),
        's65': ('perp', tokens_perp),
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

    # Test portfolios
    portfolios = {
        's58': (['s56', 's57'], 40),
        's58+s63': (['s56', 's57', 's63'], 50),
        's58+s65': (['s56', 's57', 's65'], 50),
        '4-edge': (['s56', 's57', 's63', 's65'], 60),
    }

    print(f"\n{'Portfolio':<14} {'Mode':<20} {'Return%':>10} {'MaxDD%':>8} {'Trades':>7}")
    print("-" * 65)

    for pname, (strat_ids, max_pos) in portfolios.items():
        sigs = {sid: all_sigs[sid] for sid in strat_ids}
        specs = {sid: strat_specs[sid] for sid in strat_ids}

        for mode in ["default", "none", "exempt_combined"]:
            config = PortfolioConfig(
                capital=capital, max_portfolio_positions=max_pos,
                concentration_limit=0.10, adv_cap_pct=0.05,
                min_position_usd=200.0, exchange="binance",
                base_spread_bps=3.0, impact_coeff=0.03, seed=42,
                dedup_mode=mode,
            )
            r = run_and_measure(sigs, specs, config)
            if r:
                print(f"{pname:<14} {mode:<20} {r['pct_return']:>9.0f}% {r['max_dd_pct']:>7.1f}% {r['total_trades']:>7}")
        print()


if __name__ == "__main__":
    main()
