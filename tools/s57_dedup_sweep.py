#!/usr/bin/env python3
"""S57 Tuning Mission — Sweep cross-strategy token dedup options.

Tests dedup variants for the s56+s57 (s58) portfolio without modifying
production code. Uses monkey-patching on simulate_portfolio internals.

Usage:
    python tools/s57_dedup_sweep.py [--months 60] [--capital 200000]
"""
import sys, os, time, json, copy, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio, _process_entries
from v4 import simulator as sim_module


def run_backtest(all_signals, specs, config):
    """Run simulate_portfolio and extract metrics."""
    state = simulate_portfolio(all_signals, specs, config)
    eq = [e for _, e in state.equity_snapshots]
    if not eq:
        return None
    initial = config.capital
    final = eq[-1]
    pct_return = (final / initial - 1) * 100

    # Sharpe
    daily_eq = eq[::24] if len(eq) > 48 else eq
    if len(daily_eq) > 2:
        rets = np.diff(daily_eq) / np.array(daily_eq[:-1])
        sharpe = np.mean(rets) / (np.std(rets) + 1e-12) * np.sqrt(365)
    else:
        sharpe = 0.0

    # MaxDD
    peak = initial
    max_dd = 0.0
    for e in eq:
        if e > peak:
            peak = e
        dd = (e - peak) / peak
        if dd < max_dd:
            max_dd = dd

    # Calmar
    years = len(eq) / (365 * 24)
    ann_return = (final / initial) ** (1 / max(years, 0.01)) - 1
    calmar = ann_return / abs(max_dd) if max_dd < 0 else 999.0

    trades = state.position_manager.closed_trades
    n_trades = len(trades)

    # Per-strategy trade counts
    s56_trades = sum(1 for t in trades if t.strategy_id == 's56')
    s57_trades = sum(1 for t in trades if t.strategy_id == 's57')
    s56_pnl = sum(t.pnl for t in trades if t.strategy_id == 's56')
    s57_pnl = sum(t.pnl for t in trades if t.strategy_id == 's57')

    return {
        'pct_return': pct_return,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd_pct': max_dd * 100,
        'total_trades': n_trades,
        's56_trades': s56_trades,
        's57_trades': s57_trades,
        's56_pnl': s56_pnl,
        's57_pnl': s57_pnl,
        'final_equity': final,
        'total_fees': state.total_fees,
        'total_funding': state.total_funding,
    }


def make_config(capital=200_000, seed=42):
    return PortfolioConfig(
        capital=capital,
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        min_position_usd=200.0,
        exchange="binance",
        base_spread_bps=3.0,
        impact_coeff=0.03,
        seed=seed,
    )


# ---- Monkey-patch helpers ----

# Save original _process_entries
_original_process_entries = sim_module._process_entries


def make_patched_entries(dedup_mode="none", exempt_combined=False):
    """Return a patched _process_entries that modifies dedup behavior.

    dedup_mode:
      "default" - original behavior (block if any strategy holds token)
      "none" - no cross-strategy dedup at all
      "exempt_combined" - skip dedup if existing position is from a combined strategy
      "exempt_market_mismatch" - skip dedup if existing and new use different markets
    """
    # We patch at the simulate_portfolio level by modifying PositionManager behavior
    # Actually easier: patch PositionManager.total_margin_for_token
    pass


def run_variant(variant_name, all_signals, specs, config, dedup_fn=None):
    """Run one variant with optional dedup override."""
    from v4.position import PositionManager

    original_tmft = PositionManager.total_margin_for_token

    if dedup_fn is not None:
        PositionManager.total_margin_for_token = dedup_fn

    try:
        t0 = time.time()
        result = run_backtest(all_signals, specs, config)
        elapsed = time.time() - t0
        if result:
            result['variant'] = variant_name
            result['elapsed_s'] = round(elapsed, 1)
        return result
    finally:
        PositionManager.total_margin_for_token = original_tmft


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--months', type=int, default=60)
    parser.add_argument('--capital', type=int, default=200_000)
    args = parser.parse_args()

    print(f"\n  S57 Dedup Sweep — {args.months}mo, ${args.capital:,}")
    print("=" * 70)

    # Precompute signals once (shared across all variants)
    config = make_config(capital=args.capital)

    spec_s56 = StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="perp")
    spec_s57 = StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined")

    tokens_perp = discover_tokens("perp")
    tokens_spot = discover_tokens("spot")
    tokens_combined = sorted(set(tokens_perp) & set(tokens_spot))

    print(f"\n  Tokens: {len(tokens_perp)} perp, {len(tokens_spot)} spot, {len(tokens_combined)} combined")

    print("  Precomputing s56 signals...")
    t0 = time.time()
    sigs_s56 = precompute_strategy_signals(spec_s56, tokens_perp, config, args.months)
    print(f"  Done: {len(sigs_s56)} tokens ({time.time()-t0:.1f}s)")

    print("  Precomputing s57 signals...")
    t0 = time.time()
    sigs_s57 = precompute_strategy_signals(spec_s57, tokens_combined, config, args.months)
    print(f"  Done: {len(sigs_s57)} tokens ({time.time()-t0:.1f}s)")

    specs = {"s56": spec_s56, "s57": spec_s57}
    all_signals = {"s56": sigs_s56, "s57": sigs_s57}

    # Also precompute s56-only for baseline
    all_signals_s56_only = {"s56": sigs_s56}
    specs_s56_only = {"s56": spec_s56}

    results = []

    # ---- BASELINE: s56 solo ----
    print("\n  [0] s56 solo (baseline)...")
    r = run_variant("s56_solo", all_signals_s56_only, specs_s56_only, config)
    if r: results.append(r)

    # ---- BASELINE: s57 solo ----
    all_signals_s57_only = {"s57": sigs_s57}
    specs_s57_only = {"s57": spec_s57}
    print("  [1] s57 solo (baseline)...")
    r = run_variant("s57_solo", all_signals_s57_only, specs_s57_only, config)
    if r: results.append(r)

    # ---- A0: DEFAULT dedup (current behavior) ----
    print("  [2] s56+s57 DEFAULT dedup...")
    r = run_variant("A0_default_dedup", all_signals, specs, config)
    if r: results.append(r)

    # ---- A1: NO cross-strategy dedup ----
    def no_dedup(self, token):
        """Always return 0 — no cross-strategy blocking."""
        return 0.0

    print("  [3] A1: NO dedup...")
    r = run_variant("A1_no_dedup", all_signals, specs, config, dedup_fn=no_dedup)
    if r: results.append(r)

    # ---- A2: EXEMPT combined (delta-neutral) strategies ----
    def exempt_combined_dedup(self, token):
        """Don't count margin from combined (delta-neutral) strategies."""
        margin = 0.0
        for p in self.open_positions:
            if p.token == token:
                # Skip combined positions (they have linked legs)
                if p.leg == "primary" and p.linked_position_id:
                    continue
                if p.leg == "secondary":
                    continue
                margin += p.margin_usd
        return margin

    print("  [4] A2: EXEMPT combined strategies from dedup...")
    r = run_variant("A2_exempt_combined", all_signals, specs, config, dedup_fn=exempt_combined_dedup)
    if r: results.append(r)

    # ---- A4: KEEP dedup, reduce s57 max_positions ----
    for max_pos in [3, 5, 8]:
        variant = f"A4_s57_maxpos_{max_pos}"
        specs_mp = {"s56": spec_s56, "s57": StrategySpec(strategy_id="s57", weight=1.0, max_positions=max_pos, market="combined")}
        print(f"  [A4-{max_pos}] KEEP dedup, s57 max_positions={max_pos}...")
        r = run_variant(variant, all_signals, specs_mp, config)
        if r: results.append(r)

    # ---- A1 + B2: no dedup + s56-heavy weight ----
    specs_b2 = {"s56": spec_s56, "s57": StrategySpec(strategy_id="s57", weight=0.5, max_positions=15, market="combined")}
    print("  [B2] A1+s57 weight=0.5...")
    r = run_variant("A1_B2_s57_w0.5", all_signals, specs_b2, config, dedup_fn=no_dedup)
    if r: results.append(r)

    # ---- A2 + reduced max_pos ----
    for max_pos in [5, 8]:
        variant = f"A2_s57_maxpos_{max_pos}"
        specs_a2mp = {"s56": spec_s56, "s57": StrategySpec(strategy_id="s57", weight=1.0, max_positions=max_pos, market="combined")}
        print(f"  [A2-{max_pos}] EXEMPT combined + s57 max_positions={max_pos}...")
        r = run_variant(variant, all_signals, specs_a2mp, config, dedup_fn=exempt_combined_dedup)
        if r: results.append(r)

    # ---- Print results table ----
    print("\n" + "=" * 130)
    print(f"{'Variant':<28} {'Return%':>10} {'Sharpe':>8} {'Calmar':>8} {'MaxDD%':>8} {'Trades':>7} {'s56 Tr':>7} {'s57 Tr':>7} {'s56 PnL':>12} {'s57 PnL':>12}")
    print("-" * 130)
    for r in results:
        print(f"{r['variant']:<28} {r['pct_return']:>9.0f}% {r['sharpe']:>8.2f} {r['calmar']:>8.1f} {r['max_dd_pct']:>7.1f}% {r['total_trades']:>7} {r['s56_trades']:>7} {r['s57_trades']:>7} {r['s56_pnl']:>11,.0f} {r['s57_pnl']:>11,.0f}")
    print("=" * 130)

    # Save results
    out_path = "results/v4/s57_dedup_sweep.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to {out_path}")


if __name__ == "__main__":
    main()
