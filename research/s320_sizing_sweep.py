#!/workspace/venv/bin/python
"""
s320 Sizing Override Sweep — Capital Utilization Research
=========================================================

Problem: s320 (BTC-only V3 spot strategy) had 88% idle capital because the V4
engine's default ADV-curve capped BTC position at ~12% of equity (cap_pct=0.12,
concentration_limit=0.10).

Today: sizing_overrides are per-strategy overridable. This script sweeps four
sizing configurations to measure the impact on returns, risk, and capital
utilization.

Configs (original request — cap_pct overrides):
  A — Conservative unlock (cap_pct_floor=0.08, cap_pct_range=0.22, kelly=0.50)
  B — Aggressive unlock (same + adv_scaling_divisor=2.0, cap_pct_range=0.30)
  C — Max BTC allocation (cap_pct_override=0.15, kelly=0.50, cap_mult=8.0)
  D — Baseline (current production config from multi_v4_paper.json)

Additional configs (after binding-constraint analysis):
  E — target_vol=0.03 (1.5x vol_adj increase)
  F — target_vol=0.05 (2.5x vol_adj, max rail)
  G — No kelly override (pure ADV curve)
  H — Full unlock (target_vol=0.05 + kelly=0.50 + cap_pct max)

IMPORTANT FINDING: For BTC (ADV ~$1.5B), the ADV-to-sizing curve saturates at
cap_pct=0.12. With cap_multiplier=8.0 from the strategy, cap = 96% of equity.
The binding constraint is `raw` (Kelly formula), NOT `cap`. Configs A-C produce
identical results to D because they only relax the non-binding cap constraint.
Configs E-H vary target_vol which actually affects the binding `raw` size.

Each config is tested at months=60, 12, 3 with capital=$200,000.

Author: Quant Research Agent
Date: 2026-03-26
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec, SizingDefaults, resolve_sizing
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

# ---------------------------------------------------------------------------
# Configuration variants
# ---------------------------------------------------------------------------

CAPITAL = 200_000
MONTHS_LIST = [60, 12, 24]

# Shared portfolio-level settings (s320 is BTC-only, so concentration_limit=1.0 is safe)
BASE_PORTFOLIO_KWARGS = dict(
    exchange="binance",
    adv_cap_pct=0.05,
    min_position_usd=200.0,
    seed=42,
    base_spread_bps=3.0,
    impact_coeff=0.03,
    max_portfolio_positions=1,
    conviction_mode="shuffle",
)


def _make_strategy_spec(sizing_overrides: dict) -> StrategySpec:
    """Create an s320 StrategySpec with given sizing overrides."""
    return StrategySpec(
        strategy_id="s320",
        weight=1.0,
        max_positions=1,
        market="spot",
        strategy_type="per_token",
        sizing_overrides=sizing_overrides,
    )


CONFIGS: dict[str, dict] = {
    # --- Original requested configs (cap_pct overrides) ---
    "A_conservative": {
        "label": "A: Cap Pct Conservative",
        "description": (
            "cap_pct_floor=0.08, cap_pct_range=0.22, kelly_mult=0.50, "
            "spot_max_equity=0.95, concentration=1.0"
        ),
        "spec": _make_strategy_spec({
            "cap_pct_floor": 0.08,
            "cap_pct_range": 0.22,
            "kelly_mult_override": 0.50,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
    "B_aggressive": {
        "label": "B: Cap Pct Aggressive",
        "description": (
            "cap_pct_floor=0.08, cap_pct_range=0.30, kelly_mult=0.50, "
            "adv_scaling_div=2.0, spot_max_equity=0.95, concentration=1.0"
        ),
        "spec": _make_strategy_spec({
            "cap_pct_floor": 0.08,
            "cap_pct_range": 0.30,
            "kelly_mult_override": 0.50,
            "adv_scaling_divisor": 2.0,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
    "C_max_btc": {
        "label": "C: Cap Pct Override Max",
        "description": (
            "cap_pct_override=0.15, kelly_mult=0.50, spot_max_equity=0.95, "
            "concentration=1.0, cap_multiplier=8.0 (from strategy)"
        ),
        "spec": _make_strategy_spec({
            "cap_pct_override": 0.15,
            "kelly_mult_override": 0.50,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
    "D_baseline": {
        "label": "D: Baseline (Current Prod)",
        "description": (
            "kelly_mult_override=0.50, spot_max_equity=0.95 "
            "(current multi_v4_paper.json config)"
        ),
        "spec": _make_strategy_spec({
            "kelly_mult_override": 0.50,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,  # Production uses shared concentration_limit=1.0
    },
    # --- Configs that vary the BINDING constraint (Kelly raw) ---
    # The raw Kelly size = equity * kelly_mult * edge * size_mult * (target_vol / vol).
    # For BTC (ADV ~$1.5B), the ADV-curve saturates cap_pct, so cap is never binding.
    # To change actual position size, must vary target_vol (vol_adj multiplier).
    "E_target_vol_high": {
        "label": "E: TargetVol 3% (1.5x vol_adj)",
        "description": (
            "target_vol=0.03, kelly_mult=0.50, spot_max_equity=0.95, "
            "concentration=1.0 — increases vol_adj from 1.0 to 1.5x"
        ),
        "spec": _make_strategy_spec({
            "kelly_mult_override": 0.50,
            "target_vol": 0.03,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
    "F_target_vol_max": {
        "label": "F: TargetVol 5% (2.5x vol_adj)",
        "description": (
            "target_vol=0.05, kelly_mult=0.50, spot_max_equity=0.95, "
            "concentration=1.0 — increases vol_adj up to 2.5x"
        ),
        "spec": _make_strategy_spec({
            "kelly_mult_override": 0.50,
            "target_vol": 0.05,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
    "G_no_kelly_override": {
        "label": "G: No Kelly Override (ADV curve)",
        "description": (
            "No kelly_mult_override (use ADV curve: ~0.50 for BTC), "
            "spot_max_equity=0.95 — tests if ADV curve alone is sufficient"
        ),
        "spec": _make_strategy_spec({
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
    "H_full_unlock": {
        "label": "H: Full Unlock (max everything)",
        "description": (
            "target_vol=0.05, kelly_mult=0.50, cap_pct_floor=0.08, "
            "cap_pct_range=0.30, spot_max_equity=0.95 — maximum allocation"
        ),
        "spec": _make_strategy_spec({
            "kelly_mult_override": 0.50,
            "target_vol": 0.05,
            "cap_pct_floor": 0.08,
            "cap_pct_range": 0.30,
            "spot_max_equity_pct": 0.95,
        }),
        "concentration_limit": 1.0,
    },
}


# ---------------------------------------------------------------------------
# Run a single backtest
# ---------------------------------------------------------------------------

def run_single(
    config_name: str,
    config_entry: dict,
    months: int,
    capital: float,
    precomputed_signals: dict[str, dict] | None = None,
    end_date: pd.Timestamp | None = None,
) -> dict:
    """Run a single backtest and return result dict.

    If precomputed_signals is None, signals are computed fresh.
    Returns dict with all metrics + extra info.
    """
    spec = config_entry["spec"]
    conc_limit = config_entry["concentration_limit"]

    portfolio_config = PortfolioConfig(
        strategies=[spec],
        capital=capital,
        concentration_limit=conc_limit,
        **BASE_PORTFOLIO_KWARGS,
    )

    # Precompute signals if not cached
    if precomputed_signals is None:
        tokens = discover_tokens("spot")
        data_end = end_date or infer_data_end_date("spot")
        signals = precompute_strategy_signals(spec, tokens, portfolio_config, months, end_date=data_end)
        precomputed_signals = {"s320": signals}
        used_end_date = data_end
    else:
        used_end_date = end_date

    strategy_specs = {"s320": spec}

    # Run simulation
    state = simulate_portfolio(precomputed_signals, strategy_specs, portfolio_config)

    # Compute metrics
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)

    # Compute capital utilization: average (locked_margin / equity) across all equity snapshots
    # We'll approximate from trades: average position size as % of equity
    trades = state.position_manager.closed_trades
    total_margin_used = sum(t.margin_usd for t in trades)
    avg_pos_size = total_margin_used / max(len(trades), 1)
    avg_pos_pct = (avg_pos_size / capital) * 100 if capital > 0 else 0.0

    # Capital utilization: fraction of time capital was deployed
    # Approximate from equity snapshots: count bars where locked_margin > 0
    # Since we track closed trades, we can estimate from trade hold times
    total_bars_deployed = sum(t.hold_bars for t in trades)
    total_bars = len(eq_daily) if len(eq_daily) > 0 else 1
    # s320 can only have 1 position at a time, so this is a reasonable estimate
    # Convert eq_daily days to approx hours (24h per day)
    total_hours_available = total_bars * 24
    utilization_pct = min((total_bars_deployed / max(total_hours_available, 1)) * 100, 100.0)

    return {
        "config_name": config_name,
        "config_label": config_entry["label"],
        "months": months,
        "capital": capital,
        # Core metrics
        "annual_return_pct": metrics.annualized_return_pct,
        "total_return_pct": metrics.total_return_pct,
        "sharpe": metrics.sharpe_ratio,
        "sortino": metrics.sortino_ratio,
        "calmar": metrics.calmar_ratio,
        "max_dd_pct": metrics.max_drawdown_pct,
        "max_dd_duration_days": metrics.max_drawdown_duration_days,
        # Trade stats
        "trade_count": metrics.total_trades,
        "win_rate_pct": metrics.win_rate_pct,
        "profit_factor": metrics.profit_factor,
        "avg_trade_pnl": metrics.avg_trade_pnl,
        "avg_hold_hours": metrics.avg_hold_hours,
        # Capital utilization
        "avg_pos_size_usd": avg_pos_size,
        "avg_pos_pct_equity": avg_pos_pct,
        "capital_utilization_pct": utilization_pct,
        # Extras
        "final_equity": extra_info["final_equity"],
        "total_fees": extra_info["total_fees"],
        "rejections_total": extra_info["rejections"]["total"],
    }


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def main():
    print("=" * 80)
    print("  s320 SIZING OVERRIDE SWEEP — Capital Utilization Research")
    print("=" * 80)
    print(f"  Capital:  ${CAPITAL:,.0f}")
    print(f"  Periods:  {MONTHS_LIST} months")
    print(f"  Configs:  {len(CONFIGS)}")
    print()

    # Validate all configs against SAFETY_RAILS before running
    print("  Validating sizing overrides against SAFETY_RAILS...")
    defaults = SizingDefaults()
    for name, cfg in CONFIGS.items():
        try:
            resolved = resolve_sizing(defaults, cfg["spec"].sizing_overrides)
            print(f"    {name}: OK")
        except ValueError as e:
            print(f"    {name}: FAILED — {e}")
            sys.exit(1)
    print()

    # Discover data end date once
    data_end = infer_data_end_date("spot")
    print(f"  Data end date: {data_end}")
    print()

    all_results = []

    for months in MONTHS_LIST:
        print(f"\n{'='*80}")
        print(f"  PERIOD: {months} months")
        print(f"{'='*80}")

        # Precompute signals once per period (signals don't depend on sizing config)
        # We use the baseline spec for signal precomputation since the strategy
        # function itself is the same — only the sizing pipeline differs
        base_spec = CONFIGS["D_baseline"]["spec"]
        base_config = PortfolioConfig(
            strategies=[base_spec],
            capital=CAPITAL,
            concentration_limit=1.0,
            **BASE_PORTFOLIO_KWARGS,
        )
        tokens = discover_tokens("spot")
        print(f"\n  Precomputing signals for s320 ({len(tokens)} tokens, spot, {months}mo)...")
        t0 = time.time()
        signals = precompute_strategy_signals(base_spec, tokens, base_config, months, end_date=data_end)
        print(f"  Done: {len(signals)} tokens with signals ({time.time()-t0:.1f}s)")

        # Count BTC signal entries
        if "BTC" in signals:
            btc_sig = signals["BTC"]
            entry_count = int(np.sum(btc_sig.entry_mask))
            print(f"  BTC entry signals (post-WF): {entry_count}")

        # Run each config variant
        for config_name, config_entry in CONFIGS.items():
            print(f"\n  --- {config_entry['label']} ---")
            print(f"      {config_entry['description']}")
            t0 = time.time()

            result = run_single(
                config_name=config_name,
                config_entry=config_entry,
                months=months,
                capital=CAPITAL,
                precomputed_signals={"s320": signals},
                end_date=data_end,
            )
            elapsed = time.time() - t0

            print(f"      Return: {result['annual_return_pct']:+.1f}% ann | "
                  f"Sharpe: {result['sharpe']:.2f} | "
                  f"MaxDD: {result['max_dd_pct']:.1f}% | "
                  f"Trades: {result['trade_count']} | "
                  f"AvgPos: {result['avg_pos_pct_equity']:.1f}% eq | "
                  f"Util: {result['capital_utilization_pct']:.1f}% | "
                  f"({elapsed:.1f}s)")

            all_results.append(result)

    # -----------------------------------------------------------------------
    # Generate results markdown
    # -----------------------------------------------------------------------
    print(f"\n\n{'='*80}")
    print("  GENERATING RESULTS REPORT")
    print(f"{'='*80}")

    md_lines = [
        "# s320 Sizing Override Sweep Results",
        "",
        f"**Date:** 2026-03-26",
        f"**Capital:** ${CAPITAL:,.0f}",
        f"**Periods tested:** {', '.join(str(m) + 'mo' for m in MONTHS_LIST)}",
        "",
        "## Problem Statement",
        "",
        "s320 is a BTC-only V3 spot strategy (EMA 20/50 + Positioning + VRP overlay, binary gates).",
        "The V4 engine's default ADV-to-sizing curve capped BTC position at ~12% of equity",
        "(cap_pct=0.12, concentration_limit=0.10), leaving 88% of capital idle.",
        "",
        "Today, `cap_pct_floor`, `cap_pct_range`, `kelly_mult_floor`, `kelly_mult_range`,",
        "and `adv_scaling_divisor` became per-strategy overridable via `sizing_overrides`.",
        "",
        "## Binding Constraint Analysis",
        "",
        "Position size = min(raw, cap, adv_cap, spot_cap, max_trade_pct)",
        "",
        "For BTC with ADV ~ $1.5-2.0B:",
        "- `raw = equity * kelly_mult * edge * size_mult * (target_vol / volatility)`",
        "- `cap = equity * cap_pct * cap_multiplier` where cap_pct saturates at curve max (~0.12) for BTC's enormous ADV",
        "- With cap_multiplier=8.0 from strategy: cap = equity * 0.12 * 8.0 = 96% of equity",
        "- `raw` with kelly=0.50, edge=0.40, target_vol=0.02, vol~0.005: raw = equity * 0.20 * 4.0 = 80% of equity",
        "",
        "**The binding constraint is `raw` (Kelly), NOT `cap`.** Configs A-C only relax the non-binding",
        "`cap_pct` parameters and produce identical results to the baseline. To increase actual position",
        "size, `target_vol` (which drives `vol_adj`) must be increased. Configs E-H test this.",
        "",
        "## Configurations",
        "",
    ]

    for name, cfg in CONFIGS.items():
        md_lines.append(f"### {cfg['label']}")
        md_lines.append(f"- {cfg['description']}")
        md_lines.append(f"- Overrides: `{cfg['spec'].sizing_overrides}`")
        md_lines.append("")

    # Results tables per period
    for months in MONTHS_LIST:
        period_results = [r for r in all_results if r["months"] == months]
        if not period_results:
            continue

        md_lines.append(f"## Results — {months} Month{'s' if months != 1 else ''} Lookback")
        md_lines.append("")
        md_lines.append(
            "| Config | Ann Return % | Total Return % | MaxDD % | Sharpe | Calmar | Sortino | "
            "Trades | Avg Pos % Eq | Cap Util % | Final Equity |"
        )
        md_lines.append(
            "|--------|-------------|---------------|---------|--------|--------|---------|"
            "--------|-------------|-----------|-------------|"
        )

        for r in period_results:
            md_lines.append(
                f"| {r['config_label']} "
                f"| {r['annual_return_pct']:+.1f} "
                f"| {r['total_return_pct']:+.1f} "
                f"| {r['max_dd_pct']:.1f} "
                f"| {r['sharpe']:.2f} "
                f"| {r['calmar']:.2f} "
                f"| {r['sortino']:.2f} "
                f"| {r['trade_count']} "
                f"| {r['avg_pos_pct_equity']:.1f} "
                f"| {r['capital_utilization_pct']:.1f} "
                f"| ${r['final_equity']:,.0f} |"
            )
        md_lines.append("")

    # Detailed trade stats
    md_lines.append("## Detailed Trade Statistics")
    md_lines.append("")
    md_lines.append(
        "| Config | Period | Win Rate % | Profit Factor | Avg PnL | Avg Hold (h) | "
        "Avg Pos USD | Rejections | Fees |"
    )
    md_lines.append(
        "|--------|--------|-----------|--------------|---------|-------------|"
        "------------|-----------|------|"
    )
    for r in all_results:
        md_lines.append(
            f"| {r['config_label']} "
            f"| {r['months']}mo "
            f"| {r['win_rate_pct']:.1f} "
            f"| {r['profit_factor']:.2f} "
            f"| ${r['avg_trade_pnl']:,.0f} "
            f"| {r['avg_hold_hours']:.0f} "
            f"| ${r['avg_pos_size_usd']:,.0f} "
            f"| {r['rejections_total']} "
            f"| ${r['total_fees']:,.0f} |"
        )
    md_lines.append("")

    # Comparison: improvement over baseline
    md_lines.append("## Improvement Over Baseline (D)")
    md_lines.append("")
    md_lines.append(
        "| Config | Period | dReturn (pp) | dSharpe | dMaxDD (pp) | dCalmar | "
        "Pos Size Multiplier |"
    )
    md_lines.append(
        "|--------|--------|-------------|---------|------------|---------|"
        "--------------------|"
    )
    for months in MONTHS_LIST:
        baseline = next((r for r in all_results if r["config_name"] == "D_baseline" and r["months"] == months), None)
        if baseline is None:
            continue
        for r in all_results:
            if r["months"] != months or r["config_name"] == "D_baseline":
                continue
            d_ret = r["annual_return_pct"] - baseline["annual_return_pct"]
            d_sharpe = r["sharpe"] - baseline["sharpe"]
            d_dd = r["max_dd_pct"] - baseline["max_dd_pct"]
            d_calmar = r["calmar"] - baseline["calmar"]
            pos_mult = r["avg_pos_pct_equity"] / max(baseline["avg_pos_pct_equity"], 0.01)
            md_lines.append(
                f"| {r['config_label']} "
                f"| {months}mo "
                f"| {d_ret:+.1f} "
                f"| {d_sharpe:+.2f} "
                f"| {d_dd:+.1f} "
                f"| {d_calmar:+.2f} "
                f"| {pos_mult:.1f}x |"
            )
    md_lines.append("")

    # Key findings
    md_lines.append("## Key Findings")
    md_lines.append("")

    # Find best config per period
    for months in MONTHS_LIST:
        period_results = [r for r in all_results if r["months"] == months]
        if not period_results:
            continue
        best_sharpe = max(period_results, key=lambda r: r["sharpe"])
        best_return = max(period_results, key=lambda r: r["annual_return_pct"])
        best_calmar = max(period_results, key=lambda r: r["calmar"])

        md_lines.append(f"### {months}-Month Period")
        md_lines.append(f"- **Best Sharpe:** {best_sharpe['config_label']} ({best_sharpe['sharpe']:.2f})")
        md_lines.append(f"- **Best Return:** {best_return['config_label']} ({best_return['annual_return_pct']:+.1f}%)")
        md_lines.append(f"- **Best Calmar:** {best_calmar['config_label']} ({best_calmar['calmar']:.2f})")

        baseline = next((r for r in period_results if r["config_name"] == "D_baseline"), None)
        if baseline:
            md_lines.append(f"- **Baseline:** Sharpe={baseline['sharpe']:.2f}, "
                          f"Return={baseline['annual_return_pct']:+.1f}%, "
                          f"MaxDD={baseline['max_dd_pct']:.1f}%, "
                          f"AvgPos={baseline['avg_pos_pct_equity']:.1f}% equity")
        md_lines.append("")

    md_lines.append("## Recommendations")
    md_lines.append("")
    md_lines.append("_To be filled based on results above._")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("*Generated by s320_sizing_sweep.py*")

    # Write report
    output_path = PROJECT_ROOT / "research" / "s320_sizing_sweep_results.md"
    with open(output_path, "w") as f:
        f.write("\n".join(md_lines))
    print(f"\n  Results saved to {output_path}")

    # Also print a summary table to stdout
    print(f"\n\n{'='*80}")
    print("  SUMMARY TABLE")
    print(f"{'='*80}")
    print(f"  {'Config':<30s}  {'Period':>6s}  {'AnnRet':>8s}  {'Sharpe':>7s}  {'MaxDD':>7s}  "
          f"{'Calmar':>7s}  {'Trades':>7s}  {'AvgPos%':>8s}  {'Util%':>6s}")
    print(f"  {'-'*30}  {'-'*6}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*6}")
    for r in all_results:
        print(f"  {r['config_label']:<30s}  {r['months']:>4d}mo  {r['annual_return_pct']:>+7.1f}%  "
              f"{r['sharpe']:>7.2f}  {r['max_dd_pct']:>6.1f}%  {r['calmar']:>7.2f}  "
              f"{r['trade_count']:>7d}  {r['avg_pos_pct_equity']:>7.1f}%  "
              f"{r['capital_utilization_pct']:>5.1f}%")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
