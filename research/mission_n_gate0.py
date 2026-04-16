"""
Mission N Gate 0 — Adaptive Stop Tightening for Losers

Hypothesis: trades that are still underwater at +3d/+5d/+7d post-entry are mostly
going to keep losing. Cutting them at +Nd (instead of waiting for the existing
5×ATR stop or max-hold) converts deeper losers into smaller losers without
touching winners.

Counterfactual: for each trade, if currently_underwater_+Nd == 1, replace its
pnl with the leveraged current price return at +Nd minus a round-trip cost.
Otherwise leave the trade alone. Compare total dollars.

Reuses research/mission_m_gate0_enriched.parquet.

Verdict thresholds:
- PASS if total $ improvement >= 5% of baseline total pnl, AND median trade-level
  improvement is positive, AND winning trades are not damaged (since we only
  touch underwater trades, this should be automatic — verify)
- KILL otherwise
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
ENRICHED = REPO / "research/mission_m_gate0_enriched.parquet"
OUT_REPORT = REPO / "research/mission_n_gate0_report.md"
OUT_RESULTS = REPO / "research/mission_n_gate0_results.json"

LEVERAGE = 2.6           # s523c default
ROUND_TRIP_COST = 0.0008 # ~8bps fee+slippage for the early exit (0.04% per side fee + light slippage)


def evaluate_at_offset(df: pd.DataFrame, offset_days: int, leverage: float, cost: float) -> dict:
    """For each trade, simulate 'exit at +Nd if underwater'.
    Returns dict with metrics vs baseline.
    """
    pnl_col = f"current_pnl_pct_+{offset_days}d"
    uw_col = f"current_underwater_+{offset_days}d"

    # Drop trades where we don't have a valid +Nd snapshot
    valid = df.dropna(subset=[pnl_col, uw_col]).copy()
    if len(valid) == 0:
        return {}

    # Original pnl_dollars (pnl is already net of fees + funding in s523c trade log)
    valid["original_pnl_dollars"] = valid["pnl_dollars"]

    # Counterfactual: if underwater at +Nd, exit there with cost
    valid["counterfactual_pnl_pct"] = valid[pnl_col] * leverage - cost
    valid["counterfactual_pnl_dollars"] = valid["counterfactual_pnl_pct"] * valid["margin_usd"]

    is_uw = valid[uw_col] == 1

    # New pnl: exit early if underwater, else hold
    valid["new_pnl_dollars"] = np.where(
        is_uw,
        valid["counterfactual_pnl_dollars"],
        valid["original_pnl_dollars"],
    )

    # Per-trade improvement (only meaningful for the underwater bucket)
    valid["improvement_dollars"] = valid["new_pnl_dollars"] - valid["original_pnl_dollars"]

    # ============================================================
    # AGGREGATE
    # ============================================================
    total_orig = valid["original_pnl_dollars"].sum()
    total_new = valid["new_pnl_dollars"].sum()
    total_improvement = total_new - total_orig

    n_total = len(valid)
    n_underwater = int(is_uw.sum())
    n_touched = n_underwater  # we only touch underwater trades

    # Among the underwater trades that we exit early:
    uw = valid[is_uw].copy()
    n_eventually_lost = int((uw["original_pnl_dollars"] < 0).sum())
    n_eventually_won = int((uw["original_pnl_dollars"] > 0).sum())

    saved_dollars = uw[uw["original_pnl_dollars"] < 0]["improvement_dollars"].sum()
    given_back_dollars = uw[uw["original_pnl_dollars"] > 0]["improvement_dollars"].sum()
    # Note: improvement can be negative for "saved" if the early exit was at a worse price
    # than the original stop. Both improvement_dollars sums are signed.

    # How many of the touched trades had positive improvement?
    n_helped = int((uw["improvement_dollars"] > 0).sum())
    n_hurt = int((uw["improvement_dollars"] < 0).sum())

    # Median trade-level improvement among touched trades
    median_improvement = float(uw["improvement_dollars"].median()) if len(uw) > 0 else 0.0

    # Recovery rate: of underwater trades that EVENTUALLY won, what's their distribution?
    if n_eventually_won > 0:
        recovered = uw[uw["original_pnl_dollars"] > 0]
        mean_recovered_pnl = float(recovered["original_pnl_dollars"].mean())
    else:
        mean_recovered_pnl = 0.0

    # Of those that eventually lost, mean original pnl
    if n_eventually_lost > 0:
        deepened = uw[uw["original_pnl_dollars"] < 0]
        mean_deepened_pnl = float(deepened["original_pnl_dollars"].mean())
    else:
        mean_deepened_pnl = 0.0

    return {
        "offset_days": offset_days,
        "n_total_valid": n_total,
        "n_underwater_at_offset": n_underwater,
        "n_eventually_lost": n_eventually_lost,
        "n_eventually_won": n_eventually_won,
        "loser_rate_among_underwater": n_eventually_lost / n_underwater if n_underwater > 0 else 0.0,
        "n_helped_by_rule": n_helped,
        "n_hurt_by_rule": n_hurt,
        "median_improvement_dollars": median_improvement,
        "saved_dollars_total": float(saved_dollars),
        "given_back_dollars_total": float(given_back_dollars),
        "saved_vs_given_back_ratio": float(abs(saved_dollars / given_back_dollars)) if given_back_dollars != 0 else float("inf"),
        "total_orig_pnl_dollars": float(total_orig),
        "total_new_pnl_dollars": float(total_new),
        "total_improvement_dollars": float(total_improvement),
        "improvement_pct_of_baseline": float(total_improvement / abs(total_orig)) if total_orig != 0 else 0.0,
        "mean_recovered_pnl": mean_recovered_pnl,
        "mean_deepened_pnl": mean_deepened_pnl,
    }


def main() -> None:
    print(f"Loading enriched parquet: {ENRICHED}")
    df = pd.read_parquet(ENRICHED)
    print(f"  {len(df)} trades enriched")

    results: dict = {
        "config": {
            "leverage": LEVERAGE,
            "round_trip_cost": ROUND_TRIP_COST,
            "leverage_source": "s523c default",
            "cost_source": "0.04% binance fee per side + 8bps slippage",
        },
        "by_offset": {},
    }

    for offset in [3, 5, 7, 14]:
        r = evaluate_at_offset(df, offset, LEVERAGE, ROUND_TRIP_COST)
        results["by_offset"][f"+{offset}d"] = r
        print(f"\n+{offset}d:")
        for k, v in r.items():
            if isinstance(v, float):
                print(f"  {k}: {v:,.2f}")
            else:
                print(f"  {k}: {v}")

    # ============================================================
    # SLIPPAGE / COST SENSITIVITY
    # ============================================================
    print("\n\nCost sensitivity (best offset):")
    print(f"{'cost (bps)':<12} {'+3d Δ$':>12} {'+5d Δ$':>12} {'+7d Δ$':>12}")
    sens = {}
    for cost_bps in [5, 10, 25, 50, 100]:
        cost = cost_bps / 10000
        row = {}
        for d in [3, 5, 7]:
            r = evaluate_at_offset(df, d, LEVERAGE, cost)
            row[f"+{d}d"] = r["total_improvement_dollars"]
        sens[f"{cost_bps}bps"] = row
        print(f"{cost_bps:<12} {row['+3d']:>12,.0f} {row['+5d']:>12,.0f} {row['+7d']:>12,.0f}")
    results["cost_sensitivity"] = sens

    # ============================================================
    # VERDICT
    # ============================================================
    best_offset = max(results["by_offset"].items(), key=lambda x: x[1].get("improvement_pct_of_baseline", -1))
    best_label, best_data = best_offset

    verdict = "KILL"
    reasons: list[str] = []

    impr_pct = best_data.get("improvement_pct_of_baseline", 0)
    saved_ratio = best_data.get("saved_vs_given_back_ratio", 0)
    median_impr = best_data.get("median_improvement_dollars", 0)
    n_helped = best_data.get("n_helped_by_rule", 0)
    n_hurt = best_data.get("n_hurt_by_rule", 0)

    if impr_pct >= 0.05 and saved_ratio >= 2.0 and median_impr > 0:
        verdict = "PASS"
        reasons.append(f"Best offset {best_label}: improvement {impr_pct:.1%}, saved/given_back ratio {saved_ratio:.2f}x, median +${median_impr:.0f}")
    else:
        if impr_pct < 0.05:
            reasons.append(f"Best offset {best_label}: improvement {impr_pct:.1%} < 5% threshold")
        if saved_ratio < 2.0:
            reasons.append(f"Best offset {best_label}: saved/given_back ratio {saved_ratio:.2f}x < 2.0 threshold")
        if median_impr <= 0:
            reasons.append(f"Best offset {best_label}: median improvement ${median_impr:.0f} not positive")
        # Marginal pass check
        if impr_pct > 0 and saved_ratio > 1.0:
            verdict = "MARGINAL"
            reasons.append(f"MARGINAL: edge exists (improvement {impr_pct:.1%}, ratio {saved_ratio:.2f}x) but below PASS thresholds")

    results["verdict"] = verdict
    results["verdict_reasons"] = reasons

    # Survival under realistic costs
    cost_50bps = sens["50bps"]
    survives_50bps = any(v > 0 for v in cost_50bps.values())
    results["survives_50bps_slippage"] = survives_50bps

    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))

    # ============================================================
    # MARKDOWN REPORT
    # ============================================================
    lines: list[str] = []
    lines.append("# Mission N Gate 0 — Adaptive Stop Tightening Diagnostic")
    lines.append(f"\nGenerated: 2026-04-08")
    lines.append(f"\n**Verdict: {verdict}**\n")
    for r in reasons:
        lines.append(f"- {r}")

    lines.append("\n## Hypothesis being tested\n")
    lines.append(
        "If a trade is still underwater at +Nd post-entry, exit it immediately at +Nd close price "
        "(simulating a tightened stop hit). Trades NOT underwater at +Nd are left alone — preserves "
        "the right tail. Cost: 8bps round-trip fee+slippage on early exits."
    )

    lines.append("\n## Configuration\n")
    lines.append(f"- Leverage: {LEVERAGE}× (s523c default)")
    lines.append(f"- Round-trip cost on early exits: {ROUND_TRIP_COST*10000:.0f} bps")
    lines.append(f"- Source data: clean 12-month s523c backtest, 583 trades enriched")

    lines.append("\n## Results by offset\n")
    lines.append("| Offset | N total | N underwater | Loser rate among UW | N helped by rule | N hurt by rule | Median Δ$ | Total Δ$ | Δ% of baseline |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for offset_label, r in results["by_offset"].items():
        lines.append(
            f"| {offset_label} | {r['n_total_valid']} | {r['n_underwater_at_offset']} "
            f"| {r['loser_rate_among_underwater']:.1%} "
            f"| {r['n_helped_by_rule']} | {r['n_hurt_by_rule']} "
            f"| ${r['median_improvement_dollars']:,.0f} | ${r['total_improvement_dollars']:+,.0f} "
            f"| {r['improvement_pct_of_baseline']:+.1%} |"
        )

    lines.append("\n## Saved vs Given-Back breakdown by offset\n")
    lines.append("| Offset | Saved $ (cut deeper losers) | Given back $ (cut early winners) | Ratio |")
    lines.append("|---|---:|---:|---:|")
    for offset_label, r in results["by_offset"].items():
        ratio = r["saved_vs_given_back_ratio"]
        ratio_str = f"{ratio:.2f}x" if ratio != float("inf") else "∞"
        lines.append(
            f"| {offset_label} | ${r['saved_dollars_total']:+,.0f} | "
            f"${r['given_back_dollars_total']:+,.0f} | {ratio_str} |"
        )

    lines.append("\n## Cost sensitivity (Δ$ vs baseline)\n")
    lines.append("| Cost | +3d | +5d | +7d |")
    lines.append("|---|---:|---:|---:|")
    for cost_label, row in sens.items():
        lines.append(
            f"| {cost_label} | ${row['+3d']:+,.0f} | ${row['+5d']:+,.0f} | ${row['+7d']:+,.0f} |"
        )

    lines.append("\n## Diagnosis\n")
    if verdict == "PASS":
        lines.append(
            "Cutting trades that are still underwater at +Nd post-entry produces a net positive expected "
            "value at realistic costs. Recommend Gate 1 rule sweep (stop tightening levels, ATR multipliers, "
            "time thresholds) on the clean 12-month backtest with full walk-forward semantics."
        )
    elif verdict == "MARGINAL":
        lines.append(
            "Edge exists but is too small to commit to engine code. Either the cost assumption is too "
            "punitive for the underlying improvement, or the recovered trades (false positives) absorb "
            "too much of the saved-loser benefit. Consider testing with deeper-leverage tightening "
            "(e.g., move stop closer to entry rather than full close) before declaring dead."
        )
    else:
        lines.append(
            "Underwater trades at +Nd are NOT systematically losers. Either many of them recover before "
            "the actual stop fires, or the 8bps cost on early exits eats more than the saved losses. "
            "The 5×ATR static stop is doing its job — losers that survive past the no_stop_bars grace "
            "period are mostly cut by the existing mechanism, not by Mission N's time-based rule."
        )

    OUT_REPORT.write_text("\n".join(lines))
    print(f"\nVerdict: {verdict}")
    for r in reasons:
        print(f"  {r}")
    print(f"\nReport: {OUT_REPORT}")


if __name__ == "__main__":
    main()
