"""
R206 — Portfolio Combination Analysis for Cluster Strategies.

Tests combined portfolios of s514, s517, s518 using the V4 multi-strategy engine.
Evaluates diversification benefit: do strategies provide uncorrelated returns?

Combinations tested:
  1. s514 + s518  (L/S divergence + positioning/OI)
  2. s514 + s517  (L/S divergence + macro cluster)
  3. s517 + s518  (macro cluster + positioning/OI)
  4. s514 + s517 + s518  (all three)

For each: L12M single-window + OOS monthly (12 months compounding).
"""
import subprocess
import sys
import json
import os
import re
from pathlib import Path

PYTHON = "/workspace/venv/bin/python"
BACKTEST = "v4/portfolio_backtest.py"
CWD = "/workspace/crypto_backtest"

COMBINATIONS = {
    "s514+s518": "s514,s518",
    "s514+s517": "s514,s517",
    "s517+s518": "s517,s518",
    "s514+s517+s518": "s514,s517,s518",
}

# Also run individual strategies for comparison
INDIVIDUALS = {
    "s514": "s514",
    "s517": "s517",
    "s518": "s518",
}

BASE_ARGS = [
    "--market", "perp",
    "--months", "12",
    "--capital", "100000",
    "--conviction-mode", "ranked",
    "--max-positions", "40",
    "--max-portfolio-positions", "40",
    "--skip-wf",
    "--concentration", "0.10",
]


def run_backtest(label: str, strategy: str, oos: bool = False) -> dict:
    """Run a single backtest and parse output."""
    args = [PYTHON, BACKTEST, "--strategy", strategy] + BASE_ARGS
    if oos:
        args.append("--oos-monthly")

    mode = "OOS" if oos else "L12M"
    print(f"\n{'='*70}")
    print(f"  Running: {label} ({mode})")
    print(f"  Command: {' '.join(args)}")
    print(f"{'='*70}")

    result = subprocess.run(
        args, cwd=CWD, capture_output=True, text=True, timeout=600
    )

    output = result.stdout + result.stderr
    print(output[-3000:] if len(output) > 3000 else output)

    return {"label": label, "strategy": strategy, "mode": mode, "output": output,
            "returncode": result.returncode}


def parse_metrics(output: str) -> dict:
    """Extract key metrics from backtest output."""
    metrics = {}

    # Total return
    m = re.search(r'Total Return[:\s]+([\-+]?\d+\.?\d*)%', output)
    if m:
        metrics["return_pct"] = float(m.group(1))

    # Sharpe
    m = re.search(r'Sharpe[:\s]+([\-+]?\d+\.?\d*)', output)
    if m:
        metrics["sharpe"] = float(m.group(1))

    # Max drawdown
    m = re.search(r'Max Drawdown[:\s]+([\-+]?\d+\.?\d*)%', output)
    if m:
        metrics["max_dd_pct"] = float(m.group(1))

    # Final equity
    m = re.search(r'Final Equity[:\s]+\$?([\d,]+\.?\d*)', output)
    if m:
        metrics["final_equity"] = float(m.group(1).replace(",", ""))

    # Trade count
    m = re.search(r'(\d+)\s+trades', output)
    if m:
        metrics["trades"] = int(m.group(1))

    # Win rate
    m = re.search(r'Win Rate[:\s]+([\d.]+)%', output)
    if m:
        metrics["win_rate"] = float(m.group(1))

    return metrics


def parse_oos_monthly(output: str) -> list:
    """Extract per-month returns from OOS output."""
    months = []
    # Pattern: month  $capital  +X.X%  X.X%  $equity
    for m in re.finditer(r'(\d{4}-\d{2})\s+\$[\d,]+\s+([\-+]?\d+\.?\d*)%\s+([\d.]+)%\s+\$[\d,]+', output):
        months.append({
            "month": m.group(1),
            "return_pct": float(m.group(2)),
            "max_dd_pct": float(m.group(3)),
        })
    return months


def print_summary_table(results: dict):
    """Print formatted comparison table."""
    print(f"\n{'='*80}")
    print(f"  R206 — PORTFOLIO COMBINATION SUMMARY")
    print(f"{'='*80}")
    print(f"  {'Label':>22s}  {'Return':>8s}  {'Sharpe':>7s}  {'MaxDD':>7s}  {'Trades':>7s}  {'WinRate':>7s}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}")

    for label, info in results.items():
        m = info.get("metrics", {})
        ret = m.get("return_pct", float("nan"))
        sharpe = m.get("sharpe", float("nan"))
        dd = m.get("max_dd_pct", float("nan"))
        trades = m.get("trades", 0)
        wr = m.get("win_rate", float("nan"))
        print(f"  {label:>22s}  {ret:>+7.1f}%  {sharpe:>7.2f}  {dd:>6.1f}%  {trades:>7d}  {wr:>6.1f}%")

    print(f"{'='*80}")


def compute_correlation(oos_results: dict):
    """Compute correlation between individual strategy monthly returns."""
    import numpy as np

    strats = ["s514", "s517", "s518"]
    # Collect monthly returns for each individual strategy
    monthly = {}
    for s in strats:
        if s in oos_results and oos_results[s].get("oos_months"):
            monthly[s] = [m["return_pct"] for m in oos_results[s]["oos_months"]]

    if len(monthly) < 2:
        print("\n  Not enough OOS data for correlation analysis.")
        return

    print(f"\n{'='*60}")
    print(f"  MONTHLY RETURN CORRELATION")
    print(f"{'='*60}")

    keys = sorted(monthly.keys())
    # Header
    print(f"  {'':>6s}", end="")
    for k in keys:
        print(f"  {k:>7s}", end="")
    print()

    for i, k1 in enumerate(keys):
        print(f"  {k1:>6s}", end="")
        for j, k2 in enumerate(keys):
            r1 = np.array(monthly[k1])
            r2 = np.array(monthly[k2])
            min_len = min(len(r1), len(r2))
            if min_len < 3:
                print(f"  {'N/A':>7s}", end="")
            else:
                corr = np.corrcoef(r1[:min_len], r2[:min_len])[0, 1]
                print(f"  {corr:>7.2f}", end="")
        print()

    # Also show per-month returns side by side
    print(f"\n  Per-Month Returns:")
    print(f"  {'Month':>8s}", end="")
    for k in keys:
        print(f"  {k:>7s}", end="")
    print()

    max_months = max(len(v) for v in monthly.values())
    for i in range(max_months):
        if keys and i < len(oos_results[keys[0]].get("oos_months", [])):
            month_label = oos_results[keys[0]]["oos_months"][i]["month"]
        else:
            month_label = f"M{i+1}"
        print(f"  {month_label:>8s}", end="")
        for k in keys:
            if i < len(monthly[k]):
                print(f"  {monthly[k][i]:>+6.1f}%", end="")
            else:
                print(f"  {'':>7s}", end="")
        print()

    print(f"{'='*60}")


def main():
    all_results = {}

    # Step 1: Run individual strategies (L12M + OOS)
    print("\n" + "#" * 70)
    print("  PHASE 1: Individual Strategy Baselines")
    print("#" * 70)

    for label, strat in INDIVIDUALS.items():
        # L12M
        r = run_backtest(label, strat, oos=False)
        m = parse_metrics(r["output"])
        all_results[label] = {"l12m": r, "metrics": m}

        # OOS monthly
        r_oos = run_backtest(f"{label}_oos", strat, oos=True)
        all_results[label]["oos"] = r_oos
        all_results[label]["oos_months"] = parse_oos_monthly(r_oos["output"])
        all_results[label]["oos_metrics"] = parse_metrics(r_oos["output"])

    # Step 2: Run combinations (L12M + OOS)
    print("\n" + "#" * 70)
    print("  PHASE 2: Portfolio Combinations")
    print("#" * 70)

    for label, strat in COMBINATIONS.items():
        # L12M
        r = run_backtest(label, strat, oos=False)
        m = parse_metrics(r["output"])
        all_results[label] = {"l12m": r, "metrics": m}

        # OOS monthly
        r_oos = run_backtest(f"{label}_oos", strat, oos=True)
        all_results[label]["oos"] = r_oos
        all_results[label]["oos_months"] = parse_oos_monthly(r_oos["output"])
        all_results[label]["oos_metrics"] = parse_metrics(r_oos["output"])

    # Step 3: Summary
    print("\n" + "#" * 70)
    print("  RESULTS SUMMARY")
    print("#" * 70)

    # L12M table
    print("\n  --- L12M Single-Window Results ---")
    l12m_results = {k: v for k, v in all_results.items()}
    print_summary_table(l12m_results)

    # OOS table
    print("\n  --- OOS Monthly Compounding Results ---")
    oos_view = {}
    for k, v in all_results.items():
        if v.get("oos_metrics"):
            oos_view[k + "_oos"] = {"metrics": v["oos_metrics"]}
    if oos_view:
        print_summary_table(oos_view)

    # Step 4: Correlation analysis
    compute_correlation(all_results)

    # Step 5: Diversification assessment
    print(f"\n{'='*60}")
    print(f"  DIVERSIFICATION ASSESSMENT")
    print(f"{'='*60}")

    # Find best individual Sharpe
    ind_sharpes = {}
    for s in INDIVIDUALS:
        sh = all_results.get(s, {}).get("metrics", {}).get("sharpe", float("-inf"))
        ind_sharpes[s] = sh
    best_ind = max(ind_sharpes, key=ind_sharpes.get)
    best_ind_sharpe = ind_sharpes[best_ind]

    # Find best combo Sharpe
    combo_sharpes = {}
    for c in COMBINATIONS:
        sh = all_results.get(c, {}).get("metrics", {}).get("sharpe", float("-inf"))
        combo_sharpes[c] = sh
    best_combo = max(combo_sharpes, key=combo_sharpes.get)
    best_combo_sharpe = combo_sharpes[best_combo]

    print(f"  Best individual: {best_ind} (Sharpe: {best_ind_sharpe:.2f})")
    print(f"  Best combo:      {best_combo} (Sharpe: {best_combo_sharpe:.2f})")
    if best_combo_sharpe > best_ind_sharpe:
        print(f"  --> Combination IMPROVES Sharpe by {best_combo_sharpe - best_ind_sharpe:.2f}")
    else:
        print(f"  --> Combination does NOT improve Sharpe (diff: {best_combo_sharpe - best_ind_sharpe:.2f})")

    print(f"\n  Individual Sharpes: {ind_sharpes}")
    print(f"  Combo Sharpes:     {combo_sharpes}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
