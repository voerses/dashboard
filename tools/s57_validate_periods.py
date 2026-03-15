#!/usr/bin/env python3
"""S57 Tuning — Validate dedup/concentration improvements across time periods.

Tests 60mo, 12mo, 3mo, 1mo to ensure improvements aren't just historical artifacts.
"""
import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio


def run_and_measure(all_signals, specs, config):
    state = simulate_portfolio(all_signals, specs, config)
    eq = [e for _, e in state.equity_snapshots]
    if not eq:
        return None
    initial = config.capital
    final = eq[-1]
    pct_return = (final / initial - 1) * 100
    peak = initial
    max_dd = 0.0
    for e in eq:
        if e > peak:
            peak = e
        dd = (e - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return {
        'pct_return': round(pct_return, 1),
        'max_dd_pct': round(max_dd * 100, 1),
        'total_trades': len(state.position_manager.closed_trades),
    }


def main():
    capital = 200_000
    periods = [60, 12, 3, 1]

    tokens_perp = discover_tokens("perp")
    tokens_spot = discover_tokens("spot")
    tokens_combined = sorted(set(tokens_perp) & set(tokens_spot))
    data_end = infer_data_end_date()

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

    # Portfolio definitions with their optimal config
    portfolios = {
        's58':         (['s56', 's57'], 40, 'default', True),    # B2
        's58+s60':     (['s56', 's57', 's60'], 45, 'none', False),  # B1
        's58+s62':     (['s56', 's57', 's62'], 50, 'none', False),  # B1
        's58+s63':     (['s56', 's57', 's63'], 50, 'none', True),   # B3
        's58+s65':     (['s56', 's57', 's65'], 50, 'default', False),  # A0
        's58+s59':     (['s56', 's57', 's59'], 50, 'none', False),  # B1
        '4-edge':      (['s56', 's57', 's63', 's65'], 60, 'none', False),  # B1
        's58+s69':     (['s69', 's57'], 40, 'default', True),     # B2
        's58+s72':     (['s69', 's57', 's72'], 50, 'default', False),  # A0
        's58+s75':     (['s56', 's57', 's75'], 50, 'none', True),  # B3
        's58+s76':     (['s76', 's57'], 40, 'default', True),     # B2
        '4-edge+ptp':  (['s76', 's57', 's63', 's65'], 60, 'none', True),  # B3
        '4-edge-conv': (['s56', 's57', 's63', 's65'], 60, 'none', False),  # B1
    }

    for months in periods:
        print(f"\n{'='*80}")
        print(f"  PERIOD: {months} months")
        print(f"{'='*80}")

        # Precompute signals for this period
        base_config = PortfolioConfig(
            capital=capital, max_portfolio_positions=60,
            concentration_limit=0.10, adv_cap_pct=0.05,
            min_position_usd=200.0, exchange="binance",
            base_spread_bps=3.0, impact_coeff=0.03, seed=42,
        )

        print(f"  Precomputing signals ({months}mo)...")
        all_sigs = {}
        strat_specs = {}
        for sid, (market, toks) in needed.items():
            spec = StrategySpec(strategy_id=sid, weight=1.0, max_positions=15, market=market)
            strat_specs[sid] = spec
            all_sigs[sid] = precompute_strategy_signals(spec, toks, base_config, months, end_date=data_end)

        print(f"\n  {'Portfolio':<14} {'A0 Ret%':>10} {'A0 DD%':>8} {'Best Ret%':>10} {'Best DD%':>8} {'Delta':>8} {'Verdict':>8}")
        print(f"  {'-'*68}")

        wins = 0
        losses = 0
        ties = 0

        for pname, (strat_ids, max_pos, opt_dedup, opt_conc) in portfolios.items():
            missing = [s for s in strat_ids if s not in all_sigs or len(all_sigs[s]) == 0]
            if missing:
                print(f"  {pname:<14} SKIP (missing: {missing})")
                continue

            sigs = {sid: all_sigs[sid] for sid in strat_ids}
            specs = {sid: strat_specs[sid] for sid in strat_ids}

            # A0 baseline
            config_a0 = PortfolioConfig(
                capital=capital, max_portfolio_positions=max_pos,
                concentration_limit=0.10, adv_cap_pct=0.05,
                min_position_usd=200.0, exchange="binance",
                base_spread_bps=3.0, impact_coeff=0.03, seed=42,
            )
            r_a0 = run_and_measure(sigs, specs, config_a0)

            # Optimized config
            config_opt = PortfolioConfig(
                capital=capital, max_portfolio_positions=max_pos,
                concentration_limit=0.10, adv_cap_pct=0.05,
                min_position_usd=200.0, exchange="binance",
                base_spread_bps=3.0, impact_coeff=0.03, seed=42,
                dedup_mode=opt_dedup,
                concentration_exempt_combined=opt_conc,
            )
            r_opt = run_and_measure(sigs, specs, config_opt)

            if r_a0 and r_opt:
                # For A0-assigned portfolios, both should be identical
                if opt_dedup == 'default' and not opt_conc:
                    print(f"  {pname:<14} {r_a0['pct_return']:>9.0f}% {r_a0['max_dd_pct']:>7.1f}% {'(A0=best)':>10} {'':>8} {'':>8} {'--':>8}")
                    continue

                delta_ret = r_opt['pct_return'] - r_a0['pct_return']
                delta_dd = r_opt['max_dd_pct'] - r_a0['max_dd_pct']

                if delta_ret > 0 and delta_dd > -2.0:
                    verdict = "WIN"
                    wins += 1
                elif delta_ret > 0:
                    verdict = "MIXED"
                    ties += 1
                elif abs(delta_ret) < 1.0:
                    verdict = "FLAT"
                    ties += 1
                else:
                    verdict = "LOSS"
                    losses += 1

                print(f"  {pname:<14} {r_a0['pct_return']:>9.0f}% {r_a0['max_dd_pct']:>7.1f}% {r_opt['pct_return']:>9.0f}% {r_opt['max_dd_pct']:>7.1f}% {delta_ret:>+7.0f}% {verdict:>8}")

        print(f"\n  Score: {wins} WIN / {ties} FLAT-MIXED / {losses} LOSS (of {wins+ties+losses} tuned)")


if __name__ == "__main__":
    main()
