#!/usr/bin/env python3
"""Re-validate ALL strategies at FIXED $200K capital (no compounding).

Uses max_sizing_equity=200_000 to prevent equity growth from inflating
position sizes. This gives a realistic view of strategy performance
at a fixed capital base.

Saves comprehensive results to results/v4/fixed_capital_revalidation.json
"""
import sys
import os
import gc
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import (
    simulate_portfolio, build_unified_index, SimulationState,
    _process_exits, _process_entries, _process_margin_calls, _record_equity_snapshot,
    _close_all_remaining,
)
from v4.report import compute_portfolio_metrics
from v4.dynamic_weights import DynamicWeightAllocator

CAPITAL = 200_000
MAX_SIZING_EQUITY = 200_000  # FIXED capital — no compounding

# ── Solo strategies ────────────────────────────────────────────────
SOLO = {
    "s56": ("s56", "perp", "per_token"),
    "s57": ("s57", "combined", "per_token"),
    "s59": ("s59", "perp", "per_token"),
    "s60": ("s60", "perp", "per_token"),
    "s62": ("s62", "perp", "per_token"),
    "s63": ("s63", "perp", "per_token"),
    "s65": ("s65", "perp", "per_token"),
    "s69": ("s69", "perp", "per_token"),
    "s72": ("s72", "perp", "per_token"),
    "s75": ("s75", "perp", "per_token"),
    "s76": ("s76", "perp", "per_token"),
}

# ── Combo portfolios ──────────────────────────────────────────────
COMBOS = {
    "s58": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
        ],
        "max_portfolio_positions": 30,
    },
    "s58+s62": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s62", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 45,
    },
    "s58+s65": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 50,
    },
    "4-edge": {
        "strategies": [
            ("s56", 1.0, "perp", 15, "per_token"),
            ("s57", 1.0, "combined", 15, "per_token"),
            ("s63", 1.0, "perp", 15, "per_token"),
            ("s65", 1.0, "perp", 15, "per_token"),
        ],
        "max_portfolio_positions": 60,
    },
}

PERIODS = [
    ("all_time", 60),
    ("last_12mo", 12),
    ("last_3mo", 3),
]


def build_all_portfolios():
    """Build complete dict of all solos + combos."""
    all_p = {}
    for name, (sid, market, stype) in SOLO.items():
        all_p[name] = {
            "strategies": [(sid, 1.0, market, 15, stype)],
            "max_portfolio_positions": 15,
        }
    all_p.update(COMBOS)
    return all_p


def run_backtest(pdef, months, data_end):
    """Run one backtest at FIXED $200K capital. Returns comprehensive metrics."""
    strat_list = pdef["strategies"]

    specs = {}
    for sid, weight, market, max_pos, stype in strat_list:
        specs[sid] = StrategySpec(
            strategy_id=sid, weight=weight, max_positions=max_pos,
            market=market, strategy_type=stype,
            adv_sizing_enabled=True, adv_sizing_base=75_000_000,
        )
    config = PortfolioConfig(
        capital=CAPITAL,
        max_portfolio_positions=pdef["max_portfolio_positions"],
        concentration_limit=pdef.get("concentration_limit", 1.0),
        adv_cap_pct=0.05, seed=42,
        max_sizing_equity=MAX_SIZING_EQUITY,  # FIXED $200K — no compounding
        stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )

    all_signals = {}
    for sid, spec in specs.items():
        tokens = discover_tokens(spec.market)
        all_signals[sid] = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)

    state = simulate_portfolio(all_signals, specs, config)

    m, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    final_eq = extra["final_equity"]
    pnl = final_eq - CAPITAL
    pct_ret = (final_eq / CAPITAL - 1) * 100

    # Trading period
    eq_ts = [s[0] for s in state.equity_snapshots]
    t_start = str(pd.Timestamp(eq_ts[0]).date()) if eq_ts else ""
    t_end = str(pd.Timestamp(eq_ts[-1]).date()) if eq_ts else ""

    return {
        "pct_return": round(float(pct_ret), 2),
        "dollar_pnl": round(float(pnl), 2),
        "max_dd_pct": round(float(m.max_drawdown_pct), 2),
        "sharpe": round(float(m.sharpe_ratio), 2),
        "calmar": round(float(m.calmar_ratio), 2),
        "trades": int(m.total_trades),
        "win_rate": round(float(m.win_rate_pct), 1),
        "avg_trade_pnl": round(float(m.avg_trade_pnl), 2),
        "profit_factor": round(float(m.profit_factor), 2),
        "sortino": round(float(m.sortino_ratio), 2),
        "trading_period": f"{t_start} -> {t_end}",
    }


def main():
    all_portfolios = build_all_portfolios()
    port_names = list(all_portfolios.keys())

    data_end = infer_data_end_date("combined")
    print(f"Data end:            {data_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Capital:             ${CAPITAL:,} per backtest (FIXED, no compounding)")
    print(f"Max sizing equity:   ${MAX_SIZING_EQUITY:,}")
    print(f"Stress ADV mult:     0.5")
    print(f"Impact coeff:        0.01")
    print(f"Periods:             {', '.join(p[0] for p in PERIODS)}")
    print(f"Strategies/combos:   {len(port_names)}")
    print()

    results = {}
    total_runs = len(PERIODS) * len(port_names)
    run_count = 0
    t0 = time.time()

    for period_name, months in PERIODS:
        print(f"\n{'='*90}")
        print(f"  PERIOD: {period_name} (months={months})")
        print(f"{'='*90}")

        for i, name in enumerate(port_names):
            pdef = all_portfolios[name]
            run_count += 1
            print(f"  [{run_count}/{total_runs}] {name:16s} ({period_name})...", end="", flush=True)

            if name not in results:
                results[name] = {
                    "strategies": [s[0] for s in pdef["strategies"]],
                    "type": "combo" if len(pdef["strategies"]) > 1 else "solo",
                }

            try:
                r = run_backtest(pdef, months, data_end)
                results[name][period_name] = r
                print(f"  ret={r['pct_return']:>+8.1f}%  DD={r['max_dd_pct']:>6.1f}%  "
                      f"sharpe={r['sharpe']:>5.2f}  trades={r['trades']:>5d}  "
                      f"WR={r['win_rate']:>4.1f}%")
            except Exception as e:
                results[name][period_name] = None
                print(f"  FAILED: {e}")
                import traceback
                traceback.print_exc()

            gc.collect()

    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed:.0f}s ({elapsed/60:.1f}m)")

    # ── Print Rankings ──────────────────────────────────────────
    period_labels = {
        "all_time": "ALL TIME (~5yr, $200K fixed capital)",
        "last_12mo": "LAST 12 MONTHS ($200K fixed capital)",
        "last_3mo": "LAST 3 MONTHS ($200K fixed capital)",
    }

    for period_name, _ in PERIODS:
        label = period_labels.get(period_name, period_name)
        valid = [(n, results[n][period_name]) for n in port_names
                 if results.get(n, {}).get(period_name)]
        valid.sort(key=lambda x: x[1]["pct_return"], reverse=True)

        if not valid:
            continue

        print(f"\n  {'='*120}")
        print(f"  {label}")
        print(f"  {'='*120}")
        print(f"  {'#':>2s}  {'Name':>16s}  {'Type':>5s}  {'Return %':>10s}  {'PnL ($)':>14s}  "
              f"{'MaxDD':>7s}  {'Sharpe':>7s}  {'Calmar':>7s}  {'Trades':>6s}  {'WinRate':>7s}")
        print(f"  {'--':>2s}  {'--':>16s}  {'--':>5s}  {'--':>10s}  {'--':>14s}  "
              f"{'--':>7s}  {'--':>7s}  {'--':>7s}  {'--':>6s}  {'--':>7s}")

        for rank, (name, r) in enumerate(valid):
            rtype = results[name]["type"]
            print(f"  {rank+1:>2d}  {name:>16s}  {rtype:>5s}  {r['pct_return']:>+9.1f}%  "
                  f"${r['dollar_pnl']:>12,.0f}  {r['max_dd_pct']:>6.1f}%  "
                  f"{r['sharpe']:>7.2f}  {r['calmar']:>7.2f}  {r['trades']:>6d}  "
                  f"{r['win_rate']:>5.1f}%")

    print(f"\n  Fixed capital: ${CAPITAL:,} | max_sizing_equity=${MAX_SIZING_EQUITY:,}")
    print(f"  stress_adv_multiplier=0.5 | impact_coeff=0.01\n")

    # ── Save results ──────────────────────────────────────────
    out_path = "results/v4/fixed_capital_revalidation.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    output = {
        "metadata": {
            "capital": CAPITAL,
            "max_sizing_equity": MAX_SIZING_EQUITY,
            "stress_adv_multiplier": 0.5,
            "impact_coeff": 0.01,
            "data_end": data_end.strftime("%Y-%m-%d %H:%M"),
            "periods": {p[0]: p[1] for p in PERIODS},
            "generated_at": pd.Timestamp.now().isoformat(),
        },
        "results": results,
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  Saved to {out_path}")


if __name__ == "__main__":
    main()
