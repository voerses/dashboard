#!/usr/bin/env python3
"""S57 Tuning — Compare portfolios with and without cross-strategy token dedup.

Tests: s58, 4-edge, s58+s65, s58+s72, s58+s60 with A0 (default) vs A1 (no dedup).
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
    """Run backtest, return metrics dict."""
    state = simulate_portfolio(all_signals, specs, config)
    eq = [e for _, e in state.equity_snapshots]
    if not eq:
        return None
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
    years = len(eq) / (365 * 24)
    ann_return = (final / initial) ** (1 / max(years, 0.01)) - 1
    calmar = ann_return / abs(max_dd) if max_dd < 0 else 999.0
    trades = state.position_manager.closed_trades
    per_strat = {}
    for t in trades:
        sid = t.strategy_id
        if sid not in per_strat:
            per_strat[sid] = {'trades': 0, 'pnl': 0.0}
        per_strat[sid]['trades'] += 1
        per_strat[sid]['pnl'] += t.pnl
    return {
        'pct_return': pct_return, 'sharpe': sharpe, 'calmar': calmar,
        'max_dd_pct': max_dd * 100, 'total_trades': len(trades),
        'per_strategy': per_strat, 'final_equity': final,
        'total_fees': state.total_fees, 'total_funding': state.total_funding,
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

    # Precompute all needed strategies
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
        t0 = time.time()
        toks = tokens_combined if spec.market == "combined" else tokens_perp
        all_sigs[sid] = precompute_strategy_signals(spec, toks, config, months)
        print(f"    {sid}: {len(all_sigs[sid])} tokens ({time.time()-t0:.1f}s)")

    # Define portfolios to test
    portfolios = {
        's58 (s56+s57)':     ['s56', 's57'],
        's58+s60':           ['s56', 's57', 's60'],
        's58+s65':           ['s56', 's57', 's65'],
        's58+s72':           ['s56', 's57', 's72'],
        '4-edge':            ['s56', 's57', 's63', 's65'],
    }

    original_tmft = PositionManager.total_margin_for_token

    def no_dedup(self, token):
        return 0.0

    results = []

    for pname, strat_ids in portfolios.items():
        sigs = {sid: all_sigs[sid] for sid in strat_ids}
        specs = {sid: strat_specs[sid] for sid in strat_ids}

        # A0: default
        PositionManager.total_margin_for_token = original_tmft
        t0 = time.time()
        r0 = run_and_measure(sigs, specs, config)
        if r0:
            r0['portfolio'] = pname
            r0['variant'] = 'A0_default'
            r0['elapsed_s'] = round(time.time() - t0, 1)
            results.append(r0)

        # A1: no dedup
        PositionManager.total_margin_for_token = no_dedup
        t0 = time.time()
        r1 = run_and_measure(sigs, specs, config)
        if r1:
            r1['portfolio'] = pname
            r1['variant'] = 'A1_no_dedup'
            r1['elapsed_s'] = round(time.time() - t0, 1)
            results.append(r1)

        # Print comparison
        if r0 and r1:
            delta_ret = r1['pct_return'] - r0['pct_return']
            delta_sh = r1['sharpe'] - r0['sharpe']
            delta_dd = r1['max_dd_pct'] - r0['max_dd_pct']
            print(f"\n  {pname}:")
            print(f"    A0: {r0['pct_return']:>10.0f}%, Sharpe {r0['sharpe']:.2f}, MaxDD {r0['max_dd_pct']:.1f}%, Trades {r0['total_trades']}")
            print(f"    A1: {r1['pct_return']:>10.0f}%, Sharpe {r1['sharpe']:.2f}, MaxDD {r1['max_dd_pct']:.1f}%, Trades {r1['total_trades']}")
            better = "BETTER" if delta_ret > 0 and delta_dd >= -1.0 else "MIXED" if delta_ret > 0 else "WORSE"
            print(f"    Delta: {delta_ret:+.0f}% return, {delta_sh:+.2f} Sharpe, {delta_dd:+.1f}pp DD → {better}")

    # Restore
    PositionManager.total_margin_for_token = original_tmft

    # Save
    out_path = "results/v4/s57_portfolio_dedup_sweep.json"
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
