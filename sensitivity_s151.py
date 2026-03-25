#!/usr/bin/env python3
"""Parameter Sensitivity Analysis for s151_volatile_funding_ls.

Creates temporary variant strategy files (one param changed at a time),
runs each through the V4 portfolio backtest, and collects metrics.
"""
import os
import sys
import shutil
import json
import time
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
STRATEGIES_DIR = PROJECT_ROOT / "strategies"
BASE_STRATEGY = STRATEGIES_DIR / "s151_volatile_funding_ls.py"
VENV_PYTHON = "/workspace/venv/bin/python"
BACKTEST_SCRIPT = str(PROJECT_ROOT / "v4" / "portfolio_backtest.py")

# Parameter variants: (param_name, base_value, test_values_with_labels)
VARIANTS = [
    ("FUNDING_WINDOW", 24, [
        (18, "fw18"),
        (30, "fw30"),
    ]),
    ("REBALANCE_BARS", "7 * 24", [
        ("5 * 24", "rb120"),   # 120 bars = 5 days
        ("10 * 24", "rb240"),  # 240 bars = 10 days
    ]),
    ("N_LONG", 5, [
        (3, "nl3"),
        (7, "nl7"),
    ]),
    ("N_SHORT", 5, [
        (3, "ns3"),
        (7, "ns7"),
    ]),
    ("MIN_FUNDING_DISP", 0.0002, [
        (0.00015, "mfd15"),
        (0.0003, "mfd30"),
    ]),
    ("MIN_ADV_USD", "5_000_000", [
        ("3_000_000", "adv3m"),
        ("10_000_000", "adv10m"),
    ]),
]


def create_variant(param_name, new_value, variant_id):
    """Create a variant strategy file with one parameter changed."""
    with open(BASE_STRATEGY, "r") as f:
        content = f.read()

    # Build replacement pattern
    if param_name == "REBALANCE_BARS":
        # Special: expression like "7 * 24"
        pattern = r"^REBALANCE_BARS\s*=\s*.+$"
        replacement = f"REBALANCE_BARS = {new_value}"
    elif param_name == "MIN_ADV_USD":
        pattern = r"^MIN_ADV_USD\s*=\s*.+$"
        replacement = f"MIN_ADV_USD = {new_value}"
    elif param_name == "MIN_FUNDING_DISP":
        pattern = r"^MIN_FUNDING_DISP\s*=\s*.+$"
        replacement = f"MIN_FUNDING_DISP = {new_value}     # Only trade high-dispersion tokens"
    elif param_name in ("N_LONG", "N_SHORT", "FUNDING_WINDOW"):
        pattern = rf"^{param_name}\s*=\s*.+$"
        replacement = f"{param_name} = {new_value}"
    else:
        raise ValueError(f"Unknown param: {param_name}")

    new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    # Also rename the strategy name in StrategyResult
    new_content = new_content.replace(
        "name='s151_volatile_funding_ls'",
        f"name='s151_{variant_id}'"
    )

    # Write variant file - use a unique strategy ID
    variant_filename = f"s151{variant_id}_volatile_funding_ls.py"
    variant_path = STRATEGIES_DIR / variant_filename
    with open(variant_path, "w") as f:
        f.write(new_content)

    return variant_path, f"s151{variant_id}"


def run_backtest(strategy_id):
    """Run backtest and parse metrics from output."""
    cmd = [
        VENV_PYTHON, BACKTEST_SCRIPT,
        "--strategy", strategy_id,
        "--months", "12",
        "--capital", "200000",
        "--market", "perp",
    ]

    print(f"\n{'='*70}")
    print(f"  Running backtest for: {strategy_id}")
    print(f"{'='*70}")
    sys.stdout.flush()

    t0 = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=600,  # 10 min max
    )
    elapsed = time.time() - t0

    output = result.stdout + result.stderr
    print(output[-2000:] if len(output) > 2000 else output)  # Last 2000 chars

    if result.returncode != 0:
        print(f"  FAILED (exit code {result.returncode}) in {elapsed:.0f}s")
        return None

    # Parse metrics from output
    metrics = {}
    for line in output.split("\n"):
        line = line.strip()
        if "Total Return:" in line:
            m = re.search(r"([+-]?\d+\.?\d*)%", line)
            if m:
                metrics["total_return_pct"] = float(m.group(1))
        elif "Sharpe:" in line and "Sharpe" in line:
            m = re.search(r"(-?\d+\.?\d*)", line.split("Sharpe:")[-1])
            if m:
                metrics["sharpe_ratio"] = float(m.group(1))
        elif "Calmar:" in line:
            m = re.search(r"(-?\d+\.?\d*)", line.split("Calmar:")[-1])
            if m:
                metrics["calmar_ratio"] = float(m.group(1))
        elif "Max Drawdown:" in line and "Duration" not in line:
            m = re.search(r"(-?\d+\.?\d*)%", line)
            if m:
                metrics["max_drawdown_pct"] = float(m.group(1))
        elif "Total Trades:" in line:
            m = re.search(r"(\d+)", line.split("Total Trades:")[-1])
            if m:
                metrics["total_trades"] = int(m.group(1))

    # Also try to read from saved JSON
    results_dir = PROJECT_ROOT / "results" / "v4"
    json_files = sorted(results_dir.glob(f"{strategy_id}*_metrics.json"), key=os.path.getmtime, reverse=True)
    if json_files:
        with open(json_files[0]) as f:
            jdata = json.load(f)
        final_eq = float(jdata.get("final_equity", 200000))
        metrics.setdefault("total_return_pct", (final_eq / 200000 - 1) * 100)
        metrics.setdefault("sharpe_ratio", float(jdata.get("sharpe_ratio", 0)))
        metrics.setdefault("calmar_ratio", float(jdata.get("calmar_ratio", 0)))
        metrics.setdefault("max_drawdown_pct", float(jdata.get("max_drawdown_pct", 0)))
        metrics.setdefault("total_trades", int(jdata.get("total_trades", 0)))

    metrics["elapsed_s"] = elapsed
    return metrics


def main():
    print("=" * 70)
    print("  S151 PARAMETER SENSITIVITY ANALYSIS")
    print("=" * 70)
    print(f"  Base strategy: s151_volatile_funding_ls")
    print(f"  Backtest: 12 months, $200K capital, perp market")
    print()

    # Baseline values for comparison
    baseline = {
        "total_return_pct": 26.7,
        "sharpe_ratio": 1.09,
        "calmar_ratio": 0.84,
        "max_drawdown_pct": -14.9,
        "total_trades": 176,
    }

    results = []
    results.append(("BASELINE", "base", "-", baseline))

    variant_files = []  # Track files to clean up

    for param_name, base_val, test_values in VARIANTS:
        for new_val, label in test_values:
            variant_path, strategy_id = create_variant(param_name, new_val, label)
            variant_files.append(variant_path)
            print(f"\n  Created variant: {variant_path.name}")
            print(f"  Parameter: {param_name} = {new_val} (base: {base_val})")

            metrics = run_backtest(strategy_id)
            if metrics:
                results.append((param_name, label, new_val, metrics))
            else:
                results.append((param_name, label, new_val, {
                    "total_return_pct": float("nan"),
                    "sharpe_ratio": float("nan"),
                    "calmar_ratio": float("nan"),
                    "max_drawdown_pct": float("nan"),
                    "total_trades": 0,
                }))

    # Clean up variant files
    print("\n  Cleaning up variant strategy files...")
    for vf in variant_files:
        if vf.exists():
            os.remove(vf)
            print(f"    Removed: {vf.name}")

    # Also clean up __pycache__ entries
    cache_dir = STRATEGIES_DIR / "__pycache__"
    if cache_dir.exists():
        for f in cache_dir.glob("s151*variant*"):
            os.remove(f)

    # Print summary table
    print("\n")
    print("=" * 120)
    print("  S151 PARAMETER SENSITIVITY ANALYSIS — RESULTS")
    print("=" * 120)
    print(f"  {'Parameter':<20s} {'Variant':<10s} {'Value':<16s}  {'Return%':>9s}  {'Sharpe':>7s}  {'Calmar':>7s}  {'MaxDD%':>8s}  {'Trades':>7s}  {'Calmar_chg':>11s}")
    print(f"  {'-'*20} {'-'*10} {'-'*16}  {'-'*9}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*11}")

    base_calmar = baseline["calmar_ratio"]

    for param_name, label, value, m in results:
        ret = m.get("total_return_pct", float("nan"))
        sharpe = m.get("sharpe_ratio", float("nan"))
        calmar = m.get("calmar_ratio", float("nan"))
        maxdd = m.get("max_drawdown_pct", float("nan"))
        trades = m.get("total_trades", 0)

        if label == "base":
            calmar_chg = "  (baseline)"
        elif base_calmar != 0:
            chg = ((calmar - base_calmar) / abs(base_calmar)) * 100
            calmar_chg = f"  {chg:+.1f}%"
        else:
            calmar_chg = "  N/A"

        print(f"  {param_name:<20s} {label:<10s} {str(value):<16s}  {ret:>+8.1f}%  {sharpe:>7.2f}  {calmar:>7.2f}  {maxdd:>7.1f}%  {trades:>7d} {calmar_chg}")

    print("=" * 120)

    # Overfit check
    print("\n  OVERFIT CHECK: Calmar degradation > 30% for any +/-25% parameter change?")
    overfit_flags = []
    for param_name, label, value, m in results:
        if label == "base":
            continue
        calmar = m.get("calmar_ratio", float("nan"))
        if base_calmar != 0:
            chg = ((calmar - base_calmar) / abs(base_calmar)) * 100
            if chg < -30:
                overfit_flags.append((param_name, label, value, calmar, chg))

    if overfit_flags:
        print("  ** WARNING: The following variants show >30% Calmar degradation:")
        for param_name, label, value, calmar, chg in overfit_flags:
            print(f"     {param_name} = {value} ({label}): Calmar {calmar:.2f} ({chg:+.1f}% vs baseline {base_calmar:.2f})")
        print("  ** This suggests potential overfitting to the base parameter values.")
    else:
        print("  PASS: No variant shows >30% Calmar degradation. Parameters appear robust.")

    print()


if __name__ == "__main__":
    main()
