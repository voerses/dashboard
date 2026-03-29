#!/usr/bin/env python3
"""
Sweep s501 exit parameters to find optimal DD/return tradeoff.
==============================================================

s501 currently uses NO risk management (stop=999, trail=999, max_hold=4).
This sweep tests adding stops, trails, targets, and partial TP to reduce
drawdown while preserving return.

Strategy: Precompute signals ONCE, then re-simulate with overridden exit
params. This is 100x faster than re-running the full pipeline per config.

Usage:
    python tools/sweep_s501_exits.py [--workers 4] [--months 12]
"""
from __future__ import annotations

import copy
import dataclasses
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import infer_data_end_date, TokenSignals
from v4.portfolio_signals import precompute_portfolio_signals
from v4.universe import get_all_tradeable
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


# ── Sweep parameter grid ──────────────────────────────────────────────
# Phase 1: trail + stop + max_hold (biggest levers)
TRAIL_MULTS = [1.5, 2.0, 2.5, 3.0, 4.0, 999.0]      # 999 = no trail
STOP_MULTS  = [1.5, 2.0, 3.0, 4.0, 999.0]             # 999 = no stop
MAX_HOLDS   = [3, 4, 6, 8, 12]                          # hours
# Phase 2: partial TP + exit resolution (on top 10 from phase 1)
PARTIAL_TP_ATRS = [0.0, 1.5, 2.0, 3.0]                 # ATR units
PARTIAL_TP_PCT  = 0.5                                    # 50% close
EXIT_RESOLUTIONS = [0, 1, 5]                             # minutes (0=hourly)

BASELINE_LABEL = "BASELINE_s999_t999_h4"


@dataclass
class SweepResult:
    label: str = ""
    stop_mult: float = 999.0
    trail_mult: float = 999.0
    max_hold: int = 4
    partial_tp_atr: float = 0.0
    exit_resolution: int = 0
    # Metrics
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    calmar: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_dd_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    avg_hold_hours: float = 0.0
    final_equity: float = 0.0
    runtime_s: float = 0.0
    error: str = ""


def _override_exit_params(
    signals: dict[str, TokenSignals],
    stop_mult: float,
    trail_mult: float,
    max_hold: int,
    partial_tp_atr: float = 0.0,
    partial_tp_pct: float = 0.5,
) -> dict[str, TokenSignals]:
    """Deep-copy signals and override exit parameters."""
    new_signals = {}
    for token, sig in signals.items():
        new_sig = copy.copy(sig)  # shallow copy (arrays shared)
        # stop_mult and trail_mult are numpy arrays — create new ones
        new_sig.stop_mult = np.full_like(sig.stop_mult, stop_mult)
        new_sig.trail_mult = np.full_like(sig.trail_mult, trail_mult)
        new_sig.max_hold = max_hold
        new_sig.partial_tp_atr = partial_tp_atr
        new_sig.partial_tp_pct = partial_tp_pct
        if partial_tp_atr > 0:
            new_sig.partial_tp_trail = min(trail_mult, 2.0)  # tighter trail after partial
        new_signals[token] = new_sig
    return new_signals


def _run_simulation(
    all_signals: dict[str, dict[str, TokenSignals]],
    strategy_specs: dict[str, StrategySpec],
    config: PortfolioConfig,
    capital: float,
    stop_mult: float,
    trail_mult: float,
    max_hold: int,
    partial_tp_atr: float = 0.0,
    exit_resolution: int = 0,
) -> SweepResult:
    """Run one simulation with overridden exit params."""
    label = f"s{stop_mult:.0f}_t{trail_mult:.0f}_h{max_hold}"
    if partial_tp_atr > 0:
        label += f"_tp{partial_tp_atr:.1f}"
    if exit_resolution > 0:
        label += f"_er{exit_resolution}"

    result = SweepResult(
        label=label,
        stop_mult=stop_mult,
        trail_mult=trail_mult,
        max_hold=max_hold,
        partial_tp_atr=partial_tp_atr,
        exit_resolution=exit_resolution,
    )

    try:
        t0 = time.perf_counter()

        # Override exit params in signals
        overridden_signals = {}
        for sid, token_signals in all_signals.items():
            overridden_signals[sid] = _override_exit_params(
                token_signals, stop_mult, trail_mult, max_hold,
                partial_tp_atr=partial_tp_atr,
            )

        # Update exit_resolution on strategy specs
        specs = {}
        for sid, spec in strategy_specs.items():
            specs[sid] = dataclasses.replace(spec, exit_resolution=exit_resolution)

        config_run = dataclasses.replace(
            config,
            strategies=list(specs.values()),
            capital=capital,
        )

        state = simulate_portfolio(overridden_signals, specs, config_run)
        result.runtime_s = time.perf_counter() - t0

        metrics, extra_info, _ = compute_portfolio_metrics(state, capital)
        result.total_return_pct = (extra_info["final_equity"] / capital - 1) * 100
        result.annualized_return_pct = metrics.annualized_return_pct
        result.calmar = metrics.calmar_ratio
        result.sharpe = metrics.sharpe_ratio
        result.sortino = metrics.sortino_ratio
        result.max_dd_pct = metrics.max_drawdown_pct
        result.profit_factor = metrics.profit_factor
        result.total_trades = metrics.total_trades
        result.win_rate = metrics.win_rate_pct
        result.avg_hold_hours = metrics.avg_hold_hours
        result.final_equity = extra_info["final_equity"]

    except Exception as e:
        result.error = str(e)
        import traceback
        traceback.print_exc()

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sweep s501 exit parameters")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--phase", type=int, default=1, choices=[1, 2],
                        help="Phase 1: trail+stop+hold. Phase 2: add partial TP + exit_res to top configs")
    parser.add_argument("--top-n", type=int, default=10,
                        help="Phase 2: number of top Phase 1 configs to extend")
    args = parser.parse_args()

    print("=" * 100)
    print("  S501 EXIT PARAMETER SWEEP")
    print("=" * 100)

    # ── Setup ──────────────────────────────────────────────────────────
    strategy_id = "s501"
    market = "perp"
    capital = args.capital

    config = PortfolioConfig(
        exchange="binance",
        concentration_limit=1.0,
        adv_cap_pct=0.05,
        max_portfolio_positions=50,
        seed=42,
        skip_walk_forward=True,
        conviction_mode='ranked',
    )

    # Ensure v4/ is importable (strategies use `from engine import ...`)
    v4_dir = str(PROJECT_ROOT / "v4")
    if v4_dir not in sys.path:
        sys.path.insert(0, v4_dir)

    # Detect strategy type
    from v4.portfolio_backtest import _detect_strategy_type, _load_strategy_module_attrs
    stype = _detect_strategy_type(strategy_id)
    mod_attrs = _load_strategy_module_attrs(strategy_id)

    spec = StrategySpec(
        strategy_id=strategy_id,
        weight=1.0,
        max_positions=50,
        market=market,
        strategy_type=stype,
        sizing_overrides=mod_attrs.get('sizing_overrides', {}),
        regime_params=mod_attrs.get('regime_params', None),
        max_concurrent_per_token=mod_attrs.get('max_concurrent_per_token', 1),
        dd_scaling=mod_attrs.get('dd_scaling', []),
        entry_resolution=1,
    )
    strategy_specs = {strategy_id: spec}

    # ── Precompute signals ONCE ────────────────────────────────────────
    print(f"\n  Strategy:  {strategy_id} ({stype})")
    print(f"  Market:    {market}")
    print(f"  Capital:   ${capital:,.0f}")
    print(f"  Months:    {args.months}")

    data_end = infer_data_end_date(market)
    print(f"  Data End:  {data_end.strftime('%Y-%m-%d %H:%M')}")

    tokens = get_all_tradeable()
    print(f"\n  Precomputing signals ({len(tokens)} tokens)...")
    t0 = time.time()
    all_signals = {strategy_id: precompute_portfolio_signals(spec, tokens, config, args.months, end_date=data_end)}
    print(f"  Done: {len(all_signals[strategy_id])} tokens ({time.time()-t0:.1f}s)")

    # ── Build sweep grid ───────────────────────────────────────────────
    if args.phase == 1:
        combos = list(itertools.product(STOP_MULTS, TRAIL_MULTS, MAX_HOLDS))
        # Filter nonsensical: stop tighter than trail
        combos = [(s, t, h) for s, t, h in combos if s <= t or t == 999.0 or s == 999.0]
        print(f"\n  Phase 1: {len(combos)} combinations (stop × trail × max_hold)")
        print(f"  Stop mults:  {STOP_MULTS}")
        print(f"  Trail mults: {TRAIL_MULTS}")
        print(f"  Max holds:   {MAX_HOLDS}")
    else:
        # Load phase 1 results
        p1_path = PROJECT_ROOT / "results" / "v4" / "sweep_s501_exits_phase1.json"
        if not p1_path.exists():
            print(f"\n  ERROR: Phase 1 results not found at {p1_path}")
            print(f"  Run with --phase 1 first.")
            sys.exit(1)
        with open(p1_path) as f:
            p1_data = json.load(f)
        top_configs = p1_data["results"][:args.top_n]
        print(f"\n  Phase 2: Extending top {len(top_configs)} Phase 1 configs")
        print(f"  + Partial TP: {PARTIAL_TP_ATRS}")
        print(f"  + Exit Res:   {EXIT_RESOLUTIONS}")

    # ── Run sweep ──────────────────────────────────────────────────────
    results = []
    t_sweep_start = time.time()

    if args.phase == 1:
        total = len(combos)
        for i, (stop, trail, hold) in enumerate(combos):
            r = _run_simulation(
                all_signals, strategy_specs, config, capital,
                stop_mult=stop, trail_mult=trail, max_hold=hold,
            )
            results.append(r)
            err = f" ERR: {r.error[:50]}" if r.error else ""
            print(
                f"  [{i+1:3d}/{total}] {r.label:25s} | "
                f"Ann={r.annualized_return_pct:>8.1f}% "
                f"Cal={r.calmar:>7.2f} "
                f"DD={r.max_dd_pct:>6.1f}% "
                f"PF={r.profit_factor:>5.2f} "
                f"Trd={r.total_trades:>4d} "
                f"WR={r.win_rate:>5.1f}% "
                f"({r.runtime_s:.1f}s){err}"
            )
    else:
        # Phase 2: extend top Phase 1 configs with partial TP + exit resolution
        combos_p2 = []
        for cfg in top_configs:
            for ptp in PARTIAL_TP_ATRS:
                for er in EXIT_RESOLUTIONS:
                    combos_p2.append((cfg["stop_mult"], cfg["trail_mult"],
                                     cfg["max_hold"], ptp, er))
        total = len(combos_p2)
        print(f"  Total Phase 2 combos: {total}")
        for i, (stop, trail, hold, ptp, er) in enumerate(combos_p2):
            r = _run_simulation(
                all_signals, strategy_specs, config, capital,
                stop_mult=stop, trail_mult=trail, max_hold=hold,
                partial_tp_atr=ptp, exit_resolution=er,
            )
            results.append(r)
            err = f" ERR: {r.error[:50]}" if r.error else ""
            print(
                f"  [{i+1:3d}/{total}] {r.label:35s} | "
                f"Ann={r.annualized_return_pct:>8.1f}% "
                f"Cal={r.calmar:>7.2f} "
                f"DD={r.max_dd_pct:>6.1f}% "
                f"PF={r.profit_factor:>5.2f} "
                f"Trd={r.total_trades:>4d} "
                f"({r.runtime_s:.1f}s){err}"
            )

    sweep_time = time.time() - t_sweep_start

    # ── Rank and display ───────────────────────────────────────────────
    valid = [r for r in results if not r.error and r.total_trades >= 30]
    valid.sort(key=lambda r: r.calmar, reverse=True)

    print(f"\n{'=' * 120}")
    print(f"  TOP 20 BY CALMAR ({len(valid)} valid / {len(results)} total, sweep took {sweep_time:.0f}s)")
    print(f"{'=' * 120}")
    print(f"  {'#':>3} {'Label':30s} {'Annual%':>8} {'Calmar':>7} {'MaxDD%':>7} "
          f"{'Sharpe':>7} {'Sortino':>8} {'PF':>5} {'Trades':>6} {'WR%':>5} {'AvgH':>5}")
    print(f"  {'-'*3} {'-'*30} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*5} {'-'*6} {'-'*5} {'-'*5}")

    for i, r in enumerate(valid[:20]):
        marker = " ***" if r.label == BASELINE_LABEL else ""
        print(
            f"  {i+1:>3} {r.label:30s} {r.annualized_return_pct:>8.1f} "
            f"{r.calmar:>7.2f} {r.max_dd_pct:>7.1f} {r.sharpe:>7.2f} "
            f"{r.sortino:>8.2f} {r.profit_factor:>5.2f} {r.total_trades:>6d} "
            f"{r.win_rate:>5.1f} {r.avg_hold_hours:>5.1f}{marker}"
        )

    # Find baseline for comparison
    baseline = next((r for r in results if r.stop_mult == 999 and r.trail_mult == 999
                     and r.max_hold == 4 and r.partial_tp_atr == 0 and r.exit_resolution == 0), None)
    if baseline:
        print(f"\n  BASELINE (current s501): Ann={baseline.annualized_return_pct:.1f}% "
              f"DD={baseline.max_dd_pct:.1f}% Calmar={baseline.calmar:.2f} "
              f"Sharpe={baseline.sharpe:.2f} PF={baseline.profit_factor:.2f} "
              f"Trades={baseline.total_trades}")

    # Show best by different criteria
    print(f"\n  BEST BY METRIC:")
    for metric_name in ['calmar', 'annualized_return_pct', 'sortino', 'profit_factor']:
        best = max(valid, key=lambda r: getattr(r, metric_name))
        print(f"    {metric_name:25s}: {best.label} = {getattr(best, metric_name):.2f} "
              f"(Ann={best.annualized_return_pct:.1f}%, DD={best.max_dd_pct:.1f}%)")
    # Best DD (lowest)
    best_dd = min(valid, key=lambda r: r.max_dd_pct)
    print(f"    {'lowest_max_dd':25s}: {best_dd.label} = {best_dd.max_dd_pct:.1f}% "
          f"(Ann={best_dd.annualized_return_pct:.1f}%, Calmar={best_dd.calmar:.2f})")

    # ── Save results ───────────────────────────────────────────────────
    os.makedirs(str(PROJECT_ROOT / "results" / "v4"), exist_ok=True)
    phase_tag = f"phase{args.phase}"
    outpath = PROJECT_ROOT / "results" / "v4" / f"sweep_s501_exits_{phase_tag}.json"
    data = {
        "sweep_name": f"s501_exit_sweep_{phase_tag}",
        "strategy": "s501",
        "months": args.months,
        "capital": capital,
        "total_configs": len(results),
        "valid_configs": len(valid),
        "sweep_time_s": sweep_time,
        "baseline": asdict(baseline) if baseline else None,
        "results": [asdict(r) for r in valid],  # sorted by calmar
        "all_results": [asdict(r) for r in results],
    }
    with open(outpath, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n  Results saved to {outpath}")


if __name__ == "__main__":
    main()
