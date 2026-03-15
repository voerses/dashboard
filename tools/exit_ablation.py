#!/usr/bin/env python3
"""Exit mechanism ablation study across top portfolios.

Tests individual exits and combinations as REPLACEMENTS (not additions).
Runs on: s58 (momentum+carry), 4-edge (4 families), s80+s81 (xsec+sector).
Periods: 12mo and 3mo.
"""
import gc
import sys
import time
from pathlib import Path
from dataclasses import dataclass

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


@dataclass
class ExitConfig:
    label: str
    breakeven_atr: float = 0.0
    trail_schedule: bool = True       # True=keep progressive, False=fixed
    chandelier_lookback: int = 0
    bear_target_mult: float = 0.0
    bear_max_hold: int = 0
    trail_mult_override: float = 0.0  # 0=keep default


PORTFOLIOS = {
    "s58": [
        StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="combined"),
        StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined"),
    ],
    "4-edge": [
        StrategySpec(strategy_id="s56", weight=1.0, max_positions=15, market="combined"),
        StrategySpec(strategy_id="s57", weight=1.0, max_positions=15, market="combined"),
        StrategySpec(strategy_id="s63", weight=1.0, max_positions=15, market="combined", strategy_type="per_token"),
        StrategySpec(strategy_id="s65", weight=1.0, max_positions=15, market="combined", strategy_type="per_token"),
    ],
    "s80+s81": [
        StrategySpec(strategy_id="s80", weight=1.0, max_positions=15, market="perp", strategy_type="portfolio"),
        StrategySpec(strategy_id="s81", weight=1.0, max_positions=15, market="perp", strategy_type="portfolio"),
    ],
}

# Focused set of configs: individual mechanisms + key combos
CONFIGS = [
    # Baseline
    ExitConfig("CURRENT (BE+prog)", breakeven_atr=0.5, trail_schedule=True),

    # Individual mechanisms only (disable everything else)
    ExitConfig("BARE (no mods)", breakeven_atr=0.0, trail_schedule=False),
    ExitConfig("ONLY BE(0.5)", breakeven_atr=0.5, trail_schedule=False),
    ExitConfig("ONLY prog_trail", breakeven_atr=0.0, trail_schedule=True),
    ExitConfig("ONLY chand(10)", breakeven_atr=0.0, trail_schedule=False, chandelier_lookback=10),
    ExitConfig("ONLY chand(16)", breakeven_atr=0.0, trail_schedule=False, chandelier_lookback=16),
    ExitConfig("ONLY chand(24)", breakeven_atr=0.0, trail_schedule=False, chandelier_lookback=24),
    ExitConfig("ONLY bear_t(5)", breakeven_atr=0.0, trail_schedule=False, bear_target_mult=5.0),
    ExitConfig("ONLY bear_t(8)", breakeven_atr=0.0, trail_schedule=False, bear_target_mult=8.0),
    ExitConfig("ONLY trail(2.0)", breakeven_atr=0.0, trail_schedule=False, trail_mult_override=2.0),
    ExitConfig("ONLY trail(1.5)", breakeven_atr=0.0, trail_schedule=False, trail_mult_override=1.5),

    # 2-way combos: chandelier + something
    ExitConfig("chand(16)+BE", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=16),
    ExitConfig("chand(24)+BE", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=24),
    ExitConfig("chand(16)+trail(2.0)", breakeven_atr=0.0, trail_schedule=False, chandelier_lookback=16, trail_mult_override=2.0),

    # 2-way combos: bear target + something
    ExitConfig("bear_t(8)+BE", breakeven_atr=0.5, trail_schedule=False, bear_target_mult=8.0),
    ExitConfig("bear_t(5)+prog", breakeven_atr=0.0, trail_schedule=True, bear_target_mult=5.0),

    # 3-way combos
    ExitConfig("chand(16)+BE+bear_t(8)", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=16, bear_target_mult=8.0),
    ExitConfig("chand(24)+BE+bear_t(8)", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=24, bear_target_mult=8.0),
    ExitConfig("trail(2.0)+BE", breakeven_atr=0.5, trail_schedule=False, trail_mult_override=2.0),
    ExitConfig("trail(1.5)+BE", breakeven_atr=0.5, trail_schedule=False, trail_mult_override=1.5),

    # Alternative to current: replace prog_trail with chandelier
    ExitConfig("chand(16)+BE (repl prog)", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=16),
    ExitConfig("chand(24)+BE (repl prog)", breakeven_atr=0.5, trail_schedule=False, chandelier_lookback=24),
]


def run_test(portfolio_name: str, ec: ExitConfig, months: int = 12):
    strategies = PORTFOLIOS[portfolio_name]
    max_pos = 40 if len(strategies) > 2 else (30 if len(strategies) == 2 else 15)
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=max_pos,
        concentration_limit=1.0,
    )

    data_end = infer_data_end_date("combined")

    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
        for token, sig in signals.items():
            sig.breakeven_atr = ec.breakeven_atr
            sig.chandelier_lookback = ec.chandelier_lookback
            sig.bear_target_mult = ec.bear_target_mult
            sig.bear_max_hold = ec.bear_max_hold
            if not ec.trail_schedule:
                sig.trail_schedule = None
            if ec.trail_mult_override > 0:
                sig.trail_mult = np.full_like(sig.trail_mult, ec.trail_mult_override)
        all_precomputed[spec.strategy_id] = signals

    strategy_specs = {s.strategy_id: s for s in strategies}
    state = simulate_portfolio(all_precomputed, strategy_specs, config)
    metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

    return {
        "portfolio": portfolio_name,
        "label": ec.label,
        "months": months,
        "return_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "calmar": metrics.calmar_ratio,
        "sharpe": metrics.sharpe_ratio,
        "sortino": metrics.sortino_ratio,
        "trades": metrics.total_trades,
        "win_rate": metrics.win_rate_pct,
    }


if __name__ == "__main__":
    periods = [12, 3]

    all_results = []
    for period in periods:
        for pf_name in PORTFOLIOS:
            print(f"\n{'='*105}")
            print(f"  {period}-MONTH — {pf_name}")
            print(f"{'='*105}")
            print(f"  {'Config':<30s}  {'ret':>9s}  {'DD':>8s}  {'calmar':>8s}  "
                  f"{'sharpe':>7s}  {'sortino':>7s}  {'trades':>6s}  {'wr':>6s}  {'time':>5s}")
            print(f"  {'-'*30}  {'-'*9}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*5}")

            for ec in CONFIGS:
                t0 = time.time()
                try:
                    r = run_test(pf_name, ec, period)
                    elapsed = time.time() - t0
                    all_results.append(r)
                    tag = " <<" if ec.label.startswith("CURRENT") else ""
                    print(f"  {ec.label:<30s}  {r['return_pct']:+8.1f}%  "
                          f"{r['max_dd_pct']:+7.2f}%  {r['calmar']:8.2f}  "
                          f"{r['sharpe']:7.2f}  {r['sortino']:7.2f}  "
                          f"{r['trades']:6d}  {r['win_rate']:5.1f}%  "
                          f"{elapsed:4.1f}s{tag}")
                except Exception as e:
                    print(f"  {ec.label:<30s}  ERROR: {e}")
                gc.collect()

    # Per-portfolio ranking
    for period in periods:
        for pf_name in PORTFOLIOS:
            print(f"\n{'='*105}")
            print(f"  RANKING: {pf_name} ({period}mo) — BY CALMAR")
            print(f"{'='*105}")
            pf_results = sorted(
                [r for r in all_results if r["months"] == period and r["portfolio"] == pf_name],
                key=lambda x: x["calmar"], reverse=True
            )
            bl = [r for r in pf_results if r["label"].startswith("CURRENT")]
            bl_c = bl[0]["calmar"] if bl else 0
            bl_r = bl[0]["return_pct"] if bl else 0

            for i, r in enumerate(pf_results[:10]):
                dc = r["calmar"] - bl_c
                dr = r["return_pct"] - bl_r
                marker = " ** CURRENT **" if r["label"].startswith("CURRENT") else ""
                better = " BETTER" if dc > 0.5 and not r["label"].startswith("CURRENT") else ""
                print(f"  {i+1:2d}. {r['label']:<30s}  calmar={r['calmar']:8.2f} (Δ{dc:+8.2f})  "
                      f"ret={r['return_pct']:+9.1f}% (Δ{dr:+8.1f}%)  "
                      f"DD={r['max_dd_pct']:+7.2f}%{marker}{better}")
