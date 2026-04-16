#!/usr/bin/env python3
"""s507 OOS Monthly Analysis — run via portfolio_backtest.py --oos-monthly.

Usage:
    # OOS monthly (compounding, hard data cap each month):
    python v4/portfolio_backtest.py --strategy s507 --market perp --oos-monthly --months 12 --capital 100000 --output results/v4

    # Full window (non-OOS, for comparison):
    python v4/portfolio_backtest.py --strategy s507 --market perp --months 12 --capital 100000

Results saved to: results/v4/s507_12mo_oos_monthly.json

s507 config: perp market, 1x leverage (not 3x like s513).
"""

import subprocess
import sys

PYTHON = "/workspace/venv/bin/python"
BACKTEST = "v4/portfolio_backtest.py"

def main():
    print("=" * 70)
    print("  s507_ls_div_fixed_tp — OOS Monthly Analysis (12 months)")
    print("=" * 70)

    # 1. OOS monthly
    print("\n>>> Running OOS monthly (compounding, hard data cap)...\n")
    subprocess.run([
        PYTHON, BACKTEST,
        "--strategy", "s507",
        "--market", "perp",
        "--oos-monthly",
        "--months", "12",
        "--capital", "100000",
        "--output", "results/v4",
    ], check=True)

    # 2. Full window for comparison
    print("\n>>> Running full 12-month (non-OOS, for comparison)...\n")
    subprocess.run([
        PYTHON, BACKTEST,
        "--strategy", "s507",
        "--market", "perp",
        "--months", "12",
        "--capital", "100000",
    ], check=True)


if __name__ == "__main__":
    main()
