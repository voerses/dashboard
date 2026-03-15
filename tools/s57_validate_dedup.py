#!/usr/bin/env python3
"""S57 Tuning — Validate that configurable dedup_mode matches monkey-patched results."""
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

    # Precompute s56 + s57 signals once
    spec_s56 = StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="perp")
    spec_s57 = StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined")

    base_config = PortfolioConfig(
        capital=capital, max_portfolio_positions=40,
        concentration_limit=0.10, adv_cap_pct=0.05,
        min_position_usd=200.0, exchange="binance",
        base_spread_bps=3.0, impact_coeff=0.03, seed=42,
    )

    print("  Precomputing signals...")
    t0 = time.time()
    sigs_s56 = precompute_strategy_signals(spec_s56, tokens_perp, base_config, months)
    sigs_s57 = precompute_strategy_signals(spec_s57, tokens_combined, base_config, months)
    print(f"  Done ({time.time()-t0:.1f}s)")

    all_signals = {"s56": sigs_s56, "s57": sigs_s57}
    specs = {"s56": spec_s56, "s57": spec_s57}

    # Test each dedup_mode
    for mode in ["default", "none", "exempt_combined"]:
        config = PortfolioConfig(
            capital=capital, max_portfolio_positions=40,
            concentration_limit=0.10, adv_cap_pct=0.05,
            min_position_usd=200.0, exchange="binance",
            base_spread_bps=3.0, impact_coeff=0.03, seed=42,
            dedup_mode=mode,
        )
        t0 = time.time()
        r = run_and_measure(all_signals, specs, config)
        elapsed = time.time() - t0
        if r:
            print(f"  {mode:>20}: {r['pct_return']:>10.0f}% return, {r['max_dd_pct']:>7.1f}% DD, {r['total_trades']:>5} trades ({elapsed:.1f}s)")

    print("\n  Expected from monkey-patched sweep:")
    print(f"  {'default (A0)':>20}: {'113561':>10}% return, {'  -3.4':>7}% DD")
    print(f"  {'none (A1)':>20}: {'138903':>10}% return, {'  -2.8':>7}% DD")
    print(f"  {'exempt_combined (A2)':>20}: {'136707':>10}% return, {'  -3.2':>7}% DD")


if __name__ == "__main__":
    main()
