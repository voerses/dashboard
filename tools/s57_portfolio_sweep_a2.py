#!/usr/bin/env python3
"""S57 Tuning — Test A2 (exempt combined) on multi-strategy portfolios.

A2: delta-neutral (combined) positions don't block other strategies.
This allows s57 to coexist with directional strategies while still preventing
two directional strategies from piling into the same token.
"""
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
    trades = state.position_manager.closed_trades
    per_strat = {}
    for t in trades:
        sid = t.strategy_id
        if sid not in per_strat: per_strat[sid] = {'trades': 0, 'pnl': 0.0}
        per_strat[sid]['trades'] += 1
        per_strat[sid]['pnl'] += t.pnl
    return {
        'pct_return': pct_return, 'sharpe': sharpe,
        'max_dd_pct': max_dd * 100, 'total_trades': len(trades),
        'per_strategy': per_strat,
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

    strat_specs = {
        's56': StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="perp"),
        's57': StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined"),
        's60': StrategySpec(strategy_id="s60", weight=1.0, max_positions=15, market="perp"),
        's63': StrategySpec(strategy_id="s63", weight=1.0, max_positions=15, market="perp"),
        's65': StrategySpec(strategy_id="s65", weight=1.0, max_positions=15, market="perp"),
        's72': StrategySpec(strategy_id="s72", weight=1.0, max_positions=15, market="perp"),
    }

    print(f"\n  Precomputing signals ({months}mo, ${capital:,})...")
    all_sigs = {}
    for sid, spec in strat_specs.items():
        toks = tokens_combined if spec.market == "combined" else tokens_perp
        all_sigs[sid] = precompute_strategy_signals(spec, toks, config, months)
        print(f"    {sid}: {len(all_sigs[sid])} tokens")

    portfolios = {
        's58 (s56+s57)': ['s56', 's57'],
        's58+s60':       ['s56', 's57', 's60'],
        's58+s65':       ['s56', 's57', 's65'],
        's58+s72':       ['s56', 's57', 's72'],
        '4-edge':        ['s56', 's57', 's63', 's65'],
    }

    original_tmft = PositionManager.total_margin_for_token

    def no_dedup(self, token):
        return 0.0

    def exempt_combined_dedup(self, token):
        """Don't count margin from combined (linked/delta-neutral) positions."""
        margin = 0.0
        for p in self.open_positions:
            if p.token == token:
                if p.linked_position_id:  # combined position (has linked leg)
                    continue
                margin += p.margin_usd
        return margin

    print(f"\n{'Portfolio':<20} {'Variant':<20} {'Return%':>10} {'Sharpe':>8} {'MaxDD%':>8} {'Trades':>7}")
    print("-" * 80)

    for pname, strat_ids in portfolios.items():
        sigs = {sid: all_sigs[sid] for sid in strat_ids}
        specs = {sid: strat_specs[sid] for sid in strat_ids}

        for vname, dedup_fn in [('A0_default', original_tmft), ('A1_no_dedup', no_dedup), ('A2_exempt_combined', exempt_combined_dedup)]:
            PositionManager.total_margin_for_token = dedup_fn
            r = run_and_measure(sigs, specs, config)
            if r:
                print(f"{pname:<20} {vname:<20} {r['pct_return']:>9.0f}% {r['sharpe']:>8.2f} {r['max_dd_pct']:>7.1f}% {r['total_trades']:>7}")
        print()

    PositionManager.total_margin_for_token = original_tmft


if __name__ == "__main__":
    main()
