#!/usr/bin/env python3
"""
R172 Gate 3P — Portfolio Prototype Validation
==============================================

Runs s500_r172_portfolio through the raw backtest harness with:
1. Default parameters (baseline)
2. Parameter sensitivity variants (3 alternatives)

Kill criteria: Sharpe >2, Calmar >3, MaxDD >-25% in ALL windows (L12M/L6M/L3M)
"""

import sys
import time
sys.path.insert(0, '/workspace/crypto_backtest')

from tools.raw_backtest import Backtest
from strategies.s500_r172_portfolio import run

# ═══════════════════════════════════════════════════════════════════════
# VARIANT DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

VARIANTS = [
    {
        "name": "V1: Default (80/20 breakout/momentum, trail_sma=15)",
        "params": {},  # all defaults from module
    },
    {
        "name": "V2: More Momentum (70/30 breakout/momentum)",
        "params": {"w_breakout": 0.70, "w_momentum": 0.30},
    },
    {
        "name": "V3: Less Momentum (90/10 breakout/momentum)",
        "params": {"w_breakout": 0.90, "w_momentum": 0.10},
    },
    {
        "name": "V4: Wider Trailing Stop (trail_sma=20)",
        "params": {"trail_sma": 20},
    },
]


def run_variant(variant):
    """Run a single variant and return (result_dict, elapsed_seconds)."""
    name = variant["name"]
    params = variant["params"]

    print(f"\n{'#'*70}")
    print(f"# {name}")
    print(f"{'#'*70}\n")

    bt = Backtest(
        capital=100_000,
        fee_bps=7,
        market='perp',
        leverage_max=2.5,
        start='2024-01-01',
        end='2026-03-17',
    )

    t0 = time.time()
    bt = run(bt, **params)
    elapsed = time.time() - t0

    report_name = f"R172 Portfolio ({name.split(':')[0].strip()})"
    result = bt.report(report_name)

    # Per-component trade breakdown
    if bt.trades:
        reasons = {}
        for t in bt.trades:
            r = t.exit_reason
            reasons[r] = reasons.get(r, 0) + 1

        # Categorize by component
        brk_trades = [t for t in bt.trades if t.exit_reason in
                      ('trail_stop', 'breakeven_stop', 'max_hold',
                       'brk_partial', 'brk_partial_reentry', 'end_of_backtest',
                       'brk_entry')]
        mom_trades = [t for t in bt.trades if t.exit_reason in
                      ('mom_rebalance', 'end_of_backtest')]

        # Better: look at metadata/reason if available, or infer from exit
        brk_exits = [t for t in bt.trades if t.exit_reason in
                     ('trail_stop', 'breakeven_stop', 'max_hold', 'brk_partial')]
        mom_exits = [t for t in bt.trades if t.exit_reason == 'mom_rebalance']
        other_exits = [t for t in bt.trades if t.exit_reason not in
                       ('trail_stop', 'breakeven_stop', 'max_hold',
                        'brk_partial', 'mom_rebalance')]

        print(f"\n  --- Per-Component Breakdown ---")
        print(f"  Breakout exits (trail/BE/max_hold/partial): {len(brk_exits)}")
        print(f"    Net P&L: ${sum(t.net_pnl for t in brk_exits):+,.0f}")
        print(f"  Momentum exits (rebalance): {len(mom_exits)}")
        print(f"    Net P&L: ${sum(t.net_pnl for t in mom_exits):+,.0f}")
        print(f"  Other exits (end_of_backtest, etc): {len(other_exits)}")
        print(f"    Net P&L: ${sum(t.net_pnl for t in other_exits):+,.0f}")

        print(f"\n  Exit reason counts:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            pnl = sum(t.net_pnl for t in bt.trades if t.exit_reason == reason)
            print(f"    {reason:>25s}: {count:>5d} trades  ${pnl:>+10,.0f}")

    print(f"\n  Elapsed: {elapsed:.1f}s")

    return result, elapsed


# ═══════════════════════════════════════════════════════════════════════
# RUN ALL VARIANTS
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = []

    for variant in VARIANTS:
        result, elapsed = run_variant(variant)
        results.append({
            "name": variant["name"],
            "result": result,
            "elapsed": elapsed,
        })

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ═══════════════════════════════════════════════════════════════════

    print(f"\n\n{'='*80}")
    print(f" GATE 3P SUMMARY — R172 Portfolio Sensitivity Analysis")
    print(f"{'='*80}\n")

    hdr = f"  {'Variant':>45s}  {'Verdict':>8s}  {'Trades':>7s}  {'L12M Sharpe':>12s}  {'L12M MaxDD':>11s}  {'L12M Calmar':>12s}"
    print(hdr)
    print(f"  {'─'*45}  {'─'*8}  {'─'*7}  {'─'*12}  {'─'*11}  {'─'*12}")

    for r in results:
        res = r["result"]
        name = r["name"]
        verdict = res["verdict"]
        trades = res["trades"]
        w = res["windows"]
        l12m = w.get("L12M", {})
        sharpe = l12m.get("sharpe", 0)
        maxdd = l12m.get("maxdd", 0)
        calmar = l12m.get("calmar", 0)

        print(f"  {name:>45s}  {verdict:>8s}  {trades:>7d}  {sharpe:>12.2f}  {maxdd:>+10.1%}  {calmar:>12.2f}")

    print()

    # Kill analysis
    any_pass = any(r["result"]["verdict"] == "PASS" for r in results)
    all_pass = all(r["result"]["verdict"] == "PASS" for r in results)

    if all_pass:
        print("  GATE 3P RESULT: PASS -- All variants pass kill criteria.")
    elif any_pass:
        print("  GATE 3P RESULT: CONDITIONAL PASS -- Some variants pass.")
        print("  Failing variants:")
        for r in results:
            if r["result"]["verdict"] != "PASS":
                print(f"    {r['name']}: {r['result']['kill_reasons']}")
    else:
        print("  GATE 3P RESULT: KILL -- No variants pass.")
        print("  Kill reasons per variant:")
        for r in results:
            print(f"    {r['name']}:")
            for reason in r["result"]["kill_reasons"]:
                print(f"      - {reason}")

    # Detailed window breakdown for default
    print(f"\n{'='*80}")
    print(f" DETAILED WINDOW METRICS — V1 (Default)")
    print(f"{'='*80}")
    default_windows = results[0]["result"]["windows"]
    for wname in ["L12M", "L6M", "L3M"]:
        w = default_windows[wname]
        print(f"\n  {wname}:")
        print(f"    Return:       {w['total_ret']:>+8.1%}")
        print(f"    Ann. Return:  {w['ann_ret']:>+8.1%}")
        print(f"    Sharpe:       {w['sharpe']:>8.2f}   {'PASS' if w['sharpe'] >= 2.0 else 'FAIL'}")
        print(f"    Calmar:       {w['calmar']:>8.2f}   {'PASS' if w['calmar'] >= 3.0 else 'FAIL'}")
        print(f"    Max DD:       {w['maxdd']:>+8.1%}   {'PASS' if w['maxdd'] > -0.25 else 'FAIL'}")
        print(f"    Trades:       {w['trades']:>8d}")
        print(f"    Win Rate:     {w['win_rate']:>8.1%}")
        print(f"    Profit Factor:{w['profit_factor']:>8.2f}")

    print(f"\n{'='*80}")
    print(f" END OF GATE 3P VALIDATION")
    print(f"{'='*80}")
