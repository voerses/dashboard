"""
s524a_cycle_reversal parameter sweep.

Sweeps conviction modulation thresholds/multipliers and deep bear threshold.
For each config, creates a temp strategy, runs 5 backtests, records results.
"""

import subprocess
import sys
import os
import re
import tempfile
import shutil

STRATEGY_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "strategies", "s524a_cycle_reversal.py",
)

PYTHON = "/workspace/venv/bin/python"
BACKTEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "v4", "portfolio_backtest.py",
)

STRATEGIES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "strategies",
)

# (end_date, months, label)
PERIODS = [
    ("2023-01-01T00:00:00", 12, "2022"),
    ("2024-01-01T00:00:00", 12, "2023"),
    ("2025-01-01T00:00:00", 12, "2024"),
    ("2026-01-01T00:00:00", 12, "2025"),
    ("2026-04-05T16:00:00", 3, "2026Q1"),
]

# (name, long_boost_thresh, long_reduce_thresh, long_boost_mult, long_reduce_mult,
#  short_boost_thresh, short_boost_mult, db_thresh)
CONFIGS = [
    ("baseline",     0.60, 0.40, 1.2, 0.8,  0.30, 1.2,  -0.10),
    ("LB55",         0.55, 0.40, 1.2, 0.8,  0.30, 1.2,  -0.10),
    ("LB65",         0.65, 0.40, 1.2, 0.8,  0.30, 1.2,  -0.10),
    ("LR35",         0.60, 0.35, 1.2, 0.8,  0.30, 1.2,  -0.10),
    ("LR45",         0.60, 0.45, 1.2, 0.8,  0.30, 1.2,  -0.10),
    ("LM13",         0.60, 0.40, 1.3, 0.8,  0.30, 1.2,  -0.10),
    ("LM11",         0.60, 0.40, 1.1, 0.8,  0.30, 1.2,  -0.10),
    ("LRM07",        0.60, 0.40, 1.2, 0.7,  0.30, 1.2,  -0.10),
    ("LRM09",        0.60, 0.40, 1.2, 0.9,  0.30, 1.2,  -0.10),
    ("SB25",         0.60, 0.40, 1.2, 0.8,  0.25, 1.2,  -0.10),
    ("SB35",         0.60, 0.40, 1.2, 0.8,  0.35, 1.2,  -0.10),
    ("SM115",        0.60, 0.40, 1.2, 0.8,  0.30, 1.15, -0.10),
    ("DB08",         0.60, 0.40, 1.2, 0.8,  0.30, 1.2,  -0.08),
    ("DB12",         0.60, 0.40, 1.2, 0.8,  0.30, 1.2,  -0.12),
    ("NOBOOST",      0.60, 0.40, 1.0, 1.0,  0.30, 1.0,  -0.10),
    ("MAXAGG",       0.55, 0.35, 1.3, 0.7,  0.25, 1.2,  -0.08),
    ("CONSERVATIVE", 0.65, 0.45, 1.1, 0.9,  0.35, 1.1,  -0.12),
    ("BESTGUESS",    0.55, 0.40, 1.2, 0.85, 0.30, 1.15, -0.10),
]


def make_temp_strategy(name, lb_thresh, lr_thresh, lb_mult, lr_mult,
                       sb_thresh, sb_mult, db_thresh):
    """Create a temporary strategy file with modified parameters."""
    with open(STRATEGY_SRC) as f:
        src = f.read()

    # Replace long boost line:
    # _long_boost = np.where(_score_h > 0.60, 1.2,
    #                        np.where(_score_h < 0.40, 0.8, 1.0))
    src = src.replace(
        "_long_boost = np.where(_score_h > 0.60, 1.2,\n"
        "                           np.where(_score_h < 0.40, 0.8, 1.0))",
        f"_long_boost = np.where(_score_h > {lb_thresh}, {lb_mult},\n"
        f"                           np.where(_score_h < {lr_thresh}, {lr_mult}, 1.0))",
    )

    # Replace short boost line:
    # _short_boost = np.where(_score_h < 0.30, 1.2, 1.0)
    src = src.replace(
        "_short_boost = np.where(_score_h < 0.30, 1.2, 1.0)",
        f"_short_boost = np.where(_score_h < {sb_thresh}, {sb_mult}, 1.0)",
    )

    # Replace deep bear threshold:
    # _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.10
    src = src.replace(
        "_deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.10",
        f"_deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < {db_thresh}",
    )

    # Write temp strategy
    temp_name = f"_sweep_s524a_{name}"
    temp_path = os.path.join(STRATEGIES_DIR, f"{temp_name}.py")
    with open(temp_path, "w") as f:
        f.write(src)

    return temp_name, temp_path


def run_backtest(strategy_name, end_date, months):
    """Run a single backtest, return total return % or None on error."""
    cmd = [
        PYTHON, BACKTEST,
        "--strategy", strategy_name,
        "--months", str(months),
        "--capital", "100000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "40",
        "--concentration", "0.30",
        "--skip-wf",
        "--end-date", end_date,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "Total Return:" in line:
                # Parse "+70.3%" -> 70.3
                m = re.search(r'([+-]?\d+\.?\d*)%', line)
                if m:
                    return float(m.group(1))
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def main():
    results = []
    total = len(CONFIGS)

    for ci, (name, lb_thresh, lr_thresh, lb_mult, lr_mult, sb_thresh, sb_mult, db_thresh) in enumerate(CONFIGS):
        print(f"\n[{ci+1}/{total}] Running config: {name}")
        print(f"  LB={lb_thresh} LR={lr_thresh} LBM={lb_mult} LRM={lr_mult} SB={sb_thresh} SBM={sb_mult} DB={db_thresh}")

        temp_name, temp_path = make_temp_strategy(
            name, lb_thresh, lr_thresh, lb_mult, lr_mult, sb_thresh, sb_mult, db_thresh
        )

        year_results = {}
        for end_date, months, label in PERIODS:
            ret = run_backtest(temp_name, end_date, months)
            year_results[label] = ret
            symbol = "+" if ret is not None and ret > 0 else ""
            print(f"  {label}: {symbol}{ret}%" if ret is not None else f"  {label}: ERROR")

        # Clean up temp file
        try:
            os.remove(temp_path)
        except:
            pass

        total_sum = sum(v for v in year_results.values() if v is not None)
        all_positive = all(v is not None and v > 0 for v in year_results.values())
        results.append((name, year_results, total_sum, all_positive))

    # Sort by total sum descending
    results.sort(key=lambda x: x[2], reverse=True)

    # Print table
    print("\n" + "=" * 100)
    print(f"{'Config':<16} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'2026Q1':>8} {'SUM':>8} {'All+':>5}")
    print("-" * 100)
    for name, yr, total_sum, all_pos in results:
        def fmt(v):
            if v is None:
                return "ERR"
            return f"{v:+.1f}%"
        print(f"{name:<16} {fmt(yr.get('2022')):>8} {fmt(yr.get('2023')):>8} {fmt(yr.get('2024')):>8} {fmt(yr.get('2025')):>8} {fmt(yr.get('2026Q1')):>8} {total_sum:>+8.1f}% {'YES' if all_pos else 'NO':>5}")
    print("=" * 100)

    # Target comparison
    print(f"\nTarget: beat s523z +756% while keeping all years positive")
    best = results[0]
    print(f"Best config: {best[0]} with SUM={best[2]:+.1f}%, All+={'YES' if best[3] else 'NO'}")


if __name__ == "__main__":
    main()
