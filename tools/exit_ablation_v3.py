#!/usr/bin/env python3
"""Exit mechanism ablation v3 — regime-adaptive exits.

Addresses peer review criticism of v2:
1. Fixed trail(1.5) may overfit to bull market → test regime-conditional trails
2. 1.5x ATR below research consensus (2.5-3.5x) → test higher multipliers
3. No strategy-type differentiation → test on solo portfolios

New configs:
- Regime-adaptive: different trail_mult per regime (bear tight, bull wide)
- Research consensus: trail(2.5)+BE, trail(3.0)+BE
- Hybrid: regime-adaptive + progressive schedule
- Triple barrier: tight target + time stop in bear

Regime values: 0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND
"""
import gc
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

# Regime constants
CRISIS = 0
QUIET = 1
UPTREND = 2
RANGE = 3
DOWNTREND = 4


@dataclass
class ExitConfig:
    label: str
    breakeven_atr: float = 0.5
    trail_schedule: bool = False
    chandelier_lookback: int = 0
    bear_target_mult: float = 0.0
    bear_max_hold: int = 0
    trail_mult_override: float = 0.0
    # Regime-adaptive trail: {regime_int: trail_mult}
    regime_trail: Optional[Dict[int, float]] = None


# =========================================================================
# CONFIGS
# =========================================================================
CONFIGS = [
    # --- Baselines (from v2 for comparison) ---
    ExitConfig("CURRENT (BE+prog)", breakeven_atr=0.5, trail_schedule=True),
    ExitConfig("trail(1.5)+BE", trail_mult_override=1.5),
    ExitConfig("trail(2.0)+BE", trail_mult_override=2.0),

    # --- Research consensus range (2.5-3.5x ATR) ---
    ExitConfig("trail(2.5)+BE", trail_mult_override=2.5),
    ExitConfig("trail(3.0)+BE", trail_mult_override=3.0),

    # --- REGIME-ADAPTIVE: the core hypothesis ---
    # RA1: tight bear, wide bull (aggressive capture)
    ExitConfig("RA: 1.5/2.5/2.0+BE",
               regime_trail={CRISIS: 1.5, DOWNTREND: 1.5, UPTREND: 2.5, RANGE: 2.0, QUIET: 2.0}),
    # RA2: tight bear, wider bull (let winners run more)
    ExitConfig("RA: 1.5/3.0/2.0+BE",
               regime_trail={CRISIS: 1.5, DOWNTREND: 1.5, UPTREND: 3.0, RANGE: 2.0, QUIET: 2.0}),
    # RA3: research-aligned bear, wide bull
    ExitConfig("RA: 2.0/3.0/2.5+BE",
               regime_trail={CRISIS: 2.0, DOWNTREND: 2.0, UPTREND: 3.0, RANGE: 2.5, QUIET: 2.5}),
    # RA4: very tight bear, very wide bull (maximum differentiation)
    ExitConfig("RA: 1.0/3.0/2.0+BE",
               regime_trail={CRISIS: 1.0, DOWNTREND: 1.0, UPTREND: 3.0, RANGE: 2.0, QUIET: 2.0}),
    # RA5: moderate spread (conservative)
    ExitConfig("RA: 1.5/2.0/1.8+BE",
               regime_trail={CRISIS: 1.5, DOWNTREND: 1.5, UPTREND: 2.0, RANGE: 1.8, QUIET: 1.8}),

    # --- Bear-specific mechanisms ---
    # Triple barrier style: tight target + time stop in bear
    ExitConfig("trail(1.5)+bear_t3+BE",
               trail_mult_override=1.5, bear_target_mult=3.0),
    ExitConfig("trail(1.5)+bear_t2+BE",
               trail_mult_override=1.5, bear_target_mult=2.0),
    # Bear max hold (force exit after N bars in downtrend)
    ExitConfig("trail(1.5)+bear_h12+BE",
               trail_mult_override=1.5, bear_max_hold=12),
    ExitConfig("trail(1.5)+bear_h24+BE",
               trail_mult_override=1.5, bear_max_hold=24),

    # --- Regime-adaptive + bear mechanics combined ---
    ExitConfig("RA:1.5/3.0+bear_t3+BE",
               regime_trail={CRISIS: 1.5, DOWNTREND: 1.5, UPTREND: 3.0, RANGE: 2.0, QUIET: 2.0},
               bear_target_mult=3.0),
    ExitConfig("RA:1.5/3.0+bear_h12+BE",
               regime_trail={CRISIS: 1.5, DOWNTREND: 1.5, UPTREND: 3.0, RANGE: 2.0, QUIET: 2.0},
               bear_max_hold=12),
]


def load_portfolios_from_config(config_path: str):
    with open(config_path) as f:
        cfg = json.load(f)

    portfolios = {}
    for pf in cfg["portfolios"]:
        name = pf["pool_name"]
        strategies = []
        for s in pf["strategies"]:
            spec = StrategySpec(
                strategy_id=s["strategy_id"],
                weight=s.get("weight", 1.0),
                max_positions=s.get("max_positions", 15),
                market=s.get("market", "perp"),
                strategy_type=s.get("strategy_type", "per_token"),
            )
            strategies.append(spec)
        portfolios[name] = {
            "strategies": strategies,
            "max_portfolio_positions": pf.get("max_portfolio_positions", 40),
            "concentration_limit": pf.get("concentration_limit", 1.0),
        }
    return portfolios


def apply_exit_config(signals_dict, ec: ExitConfig):
    """Apply exit overrides and return a list of (token, originals) for restoration."""
    saved = []
    for token, sig in signals_dict.items():
        orig = {
            "breakeven_atr": sig.breakeven_atr,
            "chandelier_lookback": sig.chandelier_lookback,
            "bear_target_mult": sig.bear_target_mult,
            "bear_max_hold": sig.bear_max_hold,
            "trail_schedule": sig.trail_schedule,
            "trail_mult": sig.trail_mult,
        }
        saved.append((token, orig))

        sig.breakeven_atr = ec.breakeven_atr
        sig.chandelier_lookback = ec.chandelier_lookback
        sig.bear_target_mult = ec.bear_target_mult
        sig.bear_max_hold = ec.bear_max_hold
        if not ec.trail_schedule:
            sig.trail_schedule = None

        if ec.regime_trail is not None:
            # Regime-adaptive: set trail_mult per-bar based on regime
            new_trail = np.empty_like(sig.trail_mult)
            for regime_val, mult in ec.regime_trail.items():
                mask = sig.regime == regime_val
                new_trail[mask] = mult
            sig.trail_mult = new_trail
        elif ec.trail_mult_override > 0:
            sig.trail_mult = np.full_like(sig.trail_mult, ec.trail_mult_override)
    return saved


def restore_signals(signals_dict, saved):
    """Restore original signal values after override."""
    for token, orig in saved:
        sig = signals_dict[token]
        sig.breakeven_atr = orig["breakeven_atr"]
        sig.chandelier_lookback = orig["chandelier_lookback"]
        sig.bear_target_mult = orig["bear_target_mult"]
        sig.bear_max_hold = orig["bear_max_hold"]
        sig.trail_schedule = orig["trail_schedule"]
        sig.trail_mult = orig["trail_mult"]


def run_portfolio_period(pf_name, pf_def, period, data_end, all_results):
    """Run all configs for one portfolio and one period."""
    strategies = pf_def["strategies"]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=pf_def["max_portfolio_positions"],
        concentration_limit=pf_def["concentration_limit"],
    )

    # Precompute signals once for this portfolio+period
    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, period, end_date=data_end)
        all_precomputed[spec.strategy_id] = signals

    strats = ", ".join(s.strategy_id for s in strategies)
    print(f"\n{'='*120}")
    print(f"  {period}-MONTH — {pf_name}")
    print(f"  Strategies: {strats}  max_pos={pf_def['max_portfolio_positions']}  conc={pf_def['concentration_limit']}")
    print(f"{'='*120}")
    print(f"  {'Config':<26s}  {'ret':>10s}  {'DD':>8s}  {'calmar':>9s}  "
          f"{'sharpe':>7s}  {'sortino':>7s}  {'trades':>6s}  {'wr':>6s}  {'time':>5s}")
    print(f"  {'-'*26}  {'-'*10}  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*5}")
    sys.stdout.flush()

    strategy_specs = {s.strategy_id: s for s in strategies}

    for ec in CONFIGS:
        t0 = time.time()
        saved_all = {}
        try:
            # Apply overrides
            for sid, signals in all_precomputed.items():
                saved_all[sid] = apply_exit_config(signals, ec)

            # Simulate
            state = simulate_portfolio(all_precomputed, strategy_specs, config)
            metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

            # Restore
            for sid, saved in saved_all.items():
                restore_signals(all_precomputed[sid], saved)

            elapsed = time.time() - t0
            r = {
                "portfolio": pf_name,
                "label": ec.label,
                "months": period,
                "return_pct": metrics.total_return_pct,
                "max_dd_pct": metrics.max_drawdown_pct,
                "calmar": metrics.calmar_ratio,
                "sharpe": metrics.sharpe_ratio,
                "sortino": metrics.sortino_ratio,
                "trades": metrics.total_trades,
                "win_rate": metrics.win_rate_pct,
            }
            all_results.append(r)
            tag = " <<" if ec.label.startswith("CURRENT") else ""
            tag += " **v2winner**" if ec.label == "trail(1.5)+BE" else ""
            print(f"  {ec.label:<26s}  {r['return_pct']:+9.1f}%  "
                  f"{r['max_dd_pct']:+7.2f}%  {r['calmar']:9.2f}  "
                  f"{r['sharpe']:7.2f}  {r['sortino']:7.2f}  "
                  f"{r['trades']:6d}  {r['win_rate']:5.1f}%  "
                  f"{elapsed:4.1f}s{tag}")
        except Exception as e:
            for sid, saved in saved_all.items():
                restore_signals(all_precomputed[sid], saved)
            print(f"  {ec.label:<26s}  ERROR: {e}")
        sys.stdout.flush()

    # Free memory
    del all_precomputed
    gc.collect()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+", type=int, default=[12, 6, 3, 1],
                        help="Months to test")
    parser.add_argument("--portfolios", nargs="+", default=None,
                        help="Portfolio names to test (default: solo strategies)")
    args = parser.parse_args()

    periods = args.periods
    config_path = PROJECT_ROOT / "configs" / "multi_v4_paper.json"
    all_portfolios = load_portfolios_from_config(str(config_path))

    # Default: solo portfolios for clean per-strategy signal
    SOLO_PORTFOLIOS = ["s58", "s60", "s69", "s72", "s76", "s80+s81",
                       "4-edge", "4-edge+ptp"]

    if args.portfolios:
        portfolios = {k: v for k, v in all_portfolios.items() if k in args.portfolios}
    else:
        portfolios = {k: v for k, v in all_portfolios.items() if k in SOLO_PORTFOLIOS}

    total_runs = len(portfolios) * len(CONFIGS) * len(periods)
    print(f"EXIT ABLATION v3 — Regime-Adaptive Exits")
    print(f"=" * 60)
    print(f"Testing {len(portfolios)} portfolios x {len(CONFIGS)} configs x {len(periods)} periods = {total_runs} runs")
    print(f"Portfolios: {', '.join(portfolios.keys())}")
    print(f"Periods: {periods}")
    print(f"\nConfigs:")
    for i, ec in enumerate(CONFIGS):
        rt = f" regime_trail={ec.regime_trail}" if ec.regime_trail else ""
        tm = f" trail={ec.trail_mult_override}" if ec.trail_mult_override > 0 else ""
        bt = f" bear_target={ec.bear_target_mult}" if ec.bear_target_mult > 0 else ""
        bh = f" bear_hold={ec.bear_max_hold}" if ec.bear_max_hold > 0 else ""
        ts = " prog_trail" if ec.trail_schedule else ""
        print(f"  {i+1:2d}. {ec.label:<26s}  BE={ec.breakeven_atr}{tm}{rt}{bt}{bh}{ts}")
    sys.stdout.flush()

    data_end = infer_data_end_date("combined")
    all_results = []

    for period in periods:
        for pf_name, pf_def in portfolios.items():
            try:
                run_portfolio_period(pf_name, pf_def, period, data_end, all_results)
            except Exception as e:
                print(f"\n  PORTFOLIO ERROR {pf_name} {period}mo: {e}")
                import traceback; traceback.print_exc()
            gc.collect()

    # =========================================================================
    # RANKINGS
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  RANKINGS BY CALMAR (top 5 per portfolio per period)")
    print(f"{'#'*120}")

    for period in periods:
        for pf_name in portfolios:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            if not pf_results:
                continue
            bl_c = next((r["calmar"] for r in pf_results if r["label"].startswith("CURRENT")), 0)
            v2_c = next((r["calmar"] for r in pf_results if r["label"] == "trail(1.5)+BE"), 0)

            print(f"\n  --- {pf_name} ({period}mo) ---")
            for i, r in enumerate(pf_results[:7]):
                dc = r["calmar"] - bl_c
                dv2 = r["calmar"] - v2_c
                marker = ""
                if r["label"].startswith("CURRENT"):
                    marker = " **CUR**"
                elif r["label"] == "trail(1.5)+BE":
                    marker = " **v2**"
                elif r["label"].startswith("RA:"):
                    marker = " [REGIME]"
                beat_v2 = " +v2" if dv2 > 0.5 and r["label"] != "trail(1.5)+BE" else ""
                print(f"    {i+1}. {r['label']:<26s}  cal={r['calmar']:9.2f} "
                      f"(ΔvsCUR={dc:+8.1f}  ΔvsV2={dv2:+8.1f})  "
                      f"ret={r['return_pct']:+10.1f}%  DD={r['max_dd_pct']:+7.2f}%  "
                      f"wr={r['win_rate']:5.1f}%{marker}{beat_v2}")

    # =========================================================================
    # REGIME-ADAPTIVE vs FIXED COMPARISON
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  REGIME-ADAPTIVE vs FIXED TRAIL — Head-to-head")
    print(f"{'#'*120}")

    regime_labels = [ec.label for ec in CONFIGS if ec.regime_trail is not None]
    fixed_labels = ["trail(1.5)+BE", "trail(2.0)+BE"]
    baseline_label = "CURRENT (BE+prog)"

    for pf_name in portfolios:
        print(f"\n  --- {pf_name} ---")
        print(f"  {'Config':<26s}", end="")
        for p in periods:
            print(f"  {p:>3d}mo cal", end="")
        print(f"  avg_rank  top3")

        all_labels = [baseline_label] + fixed_labels + regime_labels
        label_data = {}
        for label in all_labels:
            ranks = []
            cals = []
            for period in periods:
                pf_results = sorted(
                    [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                    key=lambda x: x["calmar"], reverse=True
                )
                for rank, r in enumerate(pf_results):
                    if r["label"] == label:
                        ranks.append(rank + 1)
                        cals.append(r["calmar"])
                        break
            if ranks:
                avg_rank = sum(ranks) / len(ranks)
                top3 = sum(1 for r in ranks if r <= 3)
                label_data[label] = (avg_rank, top3, ranks, cals)

        sorted_labels = sorted(label_data.keys(), key=lambda l: label_data[l][0])
        for label in sorted_labels:
            avg_rank, top3, ranks, cals = label_data[label]
            tag = " <<" if label == baseline_label else ""
            tag += " **v2**" if label in fixed_labels else ""
            tag += " [RA]" if label in regime_labels else ""
            print(f"  {label:<26s}", end="")
            for c in cals:
                print(f"  {c:>9.1f}", end="")
            print(f"  {avg_rank:>8.1f}  {top3}/{len(periods)}{tag}")

    # =========================================================================
    # GRAND RECOMMENDATION
    # =========================================================================
    print(f"\n\n{'#'*120}")
    print(f"  RECOMMENDATION: Best exit config per portfolio (harmonic rank score)")
    print(f"{'#'*120}")

    for pf_name in portfolios:
        config_scores = {}
        for period in periods:
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            for rank, r in enumerate(pf_results):
                label = r["label"]
                if label not in config_scores:
                    config_scores[label] = 0.0
                config_scores[label] += 1.0 / (rank + 1)

        best = sorted(config_scores.items(), key=lambda x: -x[1])
        bl_score = config_scores.get("CURRENT (BE+prog)", 0)
        v2_score = config_scores.get("trail(1.5)+BE", 0)
        winner = best[0]
        is_regime = any(winner[0] == ec.label and ec.regime_trail is not None for ec in CONFIGS)
        regime_tag = " [REGIME-ADAPTIVE]" if is_regime else ""
        print(f"  {pf_name:<16s}  BEST: {winner[0]:<26s} (score={winner[1]:.2f}){regime_tag}")
        print(f"  {'':16s}  v2:   trail(1.5)+BE          (score={v2_score:.2f})")
        print(f"  {'':16s}  CUR:  CURRENT (BE+prog)      (score={bl_score:.2f})")
