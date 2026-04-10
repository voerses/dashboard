#!/usr/bin/env python3
"""
s523y Cycle Score Parameter Sweep
=================================
Tests different configurations of the s523y strategy by patching the strategy
file and running backtests for each config.

Configs sweep: BEAR_THRESHOLD, BULL_THRESHOLD, GATE_WINDOW_HOURS, bull_gate_days
"""

import os
import re
import shutil
import subprocess
import sys
import time

WORKSPACE = "/workspace/crypto_backtest"
STRATEGY_SRC = os.path.join(WORKSPACE, "strategies", "s523y_cycle_score.py")
STRATEGY_TMP = os.path.join(WORKSPACE, "strategies", "s523y_sweep_tmp.py")
PYTHON = "/workspace/venv/bin/python"
BACKTEST_CMD = os.path.join(WORKSPACE, "v4", "portfolio_backtest.py")

# Configs: (name, bear_thresh, bull_thresh, gate_hours, bull_gate_days)
# gate_hours is not directly used as a single constant — it's the 45*24 in the
# _m_ret_45d_h shift. We handle it via bull_gate_days for the bull gate and
# the transition gate window.
# bull_gate_days controls the rolling return window for the BULL short gate
# (currently 60d = 60*24 shift)
CONFIGS = [
    ("B40_U60_G30",     0.40, 0.60, 30, 45),   # wider bear, wider bull, faster gate
    ("B40_U60_G45",     0.40, 0.60, 45, 45),   # wider bear, wider bull, standard gate
    ("B45_U55_G30",     0.45, 0.55, 30, 45),   # very wide bear+bull, fast gate
    ("B40_U65_G30",     0.40, 0.65, 30, 45),   # wider bear only, fast gate
    ("B35_U60_G30",     0.35, 0.60, 30, 45),   # current bear, wider bull, fast gate
    ("B40_U60_G30_B45g", 0.40, 0.60, 30, 30),  # everything faster
]

# Backtest periods: (label, months, end_date)
PERIODS = [
    ("2022",   12, "2023-01-01T00:00:00"),
    ("2023",   12, "2024-01-01T00:00:00"),
    ("2024",   12, "2025-01-01T00:00:00"),
    ("2025",   12, "2026-01-01T00:00:00"),
    ("2026Q1",  3, "2026-04-05T16:00:00"),
]

# Baselines
BASELINES = {
    "s523v": {"2022": 76, "2023": 194, "2024": 48, "2025": 292, "2026Q1": 42},
    "s523y_current": {"2022": 39, "2023": 59, "2024": 104, "2025": 128, "2026Q1": -6},
}


def patch_strategy(src_text, bear_thresh, bull_thresh, gate_days, bull_gate_days):
    """Patch strategy source with new parameters."""
    patched = src_text

    # 1. Replace BEAR_THRESHOLD
    patched = re.sub(
        r'BEAR_THRESHOLD\s*=\s*[\d.]+',
        f'BEAR_THRESHOLD = {bear_thresh}',
        patched
    )

    # 2. Replace BULL_THRESHOLD
    patched = re.sub(
        r'BULL_THRESHOLD\s*=\s*[\d.]+',
        f'BULL_THRESHOLD = {bull_thresh}',
        patched
    )

    # 3. Replace the transition gate window (45d -> gate_days)
    # The transition gate uses _m_ret_45d_h which is shift(45 * 24)
    # We need to replace the 45*24 shift AND the variable names
    if gate_days != 45:
        # Replace the shift for the transition gate
        patched = patched.replace(
            '_m_ret_45d_h = (btc_aligned / btc_aligned.shift(45 * 24) - 1).values',
            f'_m_ret_45d_h = (btc_aligned / btc_aligned.shift({gate_days} * 24) - 1).values'
        )

    # 4. Replace the bull gate window (60d -> bull_gate_days)
    if bull_gate_days != 60:
        patched = patched.replace(
            '_m_ret_60d_h = (btc_aligned / btc_aligned.shift(60 * 24) - 1).values',
            f'_m_ret_60d_h = (btc_aligned / btc_aligned.shift({bull_gate_days} * 24) - 1).values'
        )

    # 5. Change the strategy name to avoid cache conflicts
    patched = patched.replace(
        "name='s523y_cycle_score'",
        "name='s523y_sweep_tmp'"
    )

    # 6. Clear caches — change cache attribute names to force recomputation
    patched = patched.replace('_btc_1h_cache_y', '_btc_1h_cache_sweep')
    patched = patched.replace('_cycle_score_cache', '_cycle_score_cache_sweep')
    patched = patched.replace('_dilution_cache', '_dilution_cache_sweep')

    return patched


def extract_return(output):
    """Extract Total Return percentage from backtest output."""
    for line in output.split('\n'):
        if 'Total Return:' in line:
            match = re.search(r'([+-]?[\d.]+)%', line)
            if match:
                return float(match.group(1))
    return None


def run_backtest(months, end_date):
    """Run a single backtest, return (return_pct, raw_output)."""
    cmd = [
        PYTHON, BACKTEST_CMD,
        "--strategy", "s523y_sweep_tmp",
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
            cmd, capture_output=True, text=True, timeout=120,
            cwd=WORKSPACE
        )
        output = result.stdout + result.stderr
        ret = extract_return(output)
        return ret, output
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    except Exception as e:
        return None, str(e)


def main():
    # Read source strategy
    with open(STRATEGY_SRC, 'r') as f:
        src_text = f.read()

    print("=" * 90)
    print("s523y Cycle Score Parameter Sweep")
    print("=" * 90)
    print()

    all_results = {}

    for cfg_idx, (name, bear_t, bull_t, gate_d, bull_gate_d) in enumerate(CONFIGS):
        print(f"\n{'='*90}")
        print(f"Config {cfg_idx+1}/{len(CONFIGS)}: {name}")
        print(f"  BEAR_THRESHOLD={bear_t}, BULL_THRESHOLD={bull_t}, "
              f"transition_gate={gate_d}d, bull_gate={bull_gate_d}d")
        print(f"{'='*90}")

        # Patch and write temp strategy
        patched = patch_strategy(src_text, bear_t, bull_t, gate_d, bull_gate_d)
        with open(STRATEGY_TMP, 'w') as f:
            f.write(patched)

        results = {}
        for period_label, months, end_date in PERIODS:
            t0 = time.time()
            ret, output = run_backtest(months, end_date)
            elapsed = time.time() - t0

            if ret is not None:
                results[period_label] = ret
                print(f"  {period_label}: {ret:+.1f}%  ({elapsed:.0f}s)")
            else:
                results[period_label] = "ERR"
                print(f"  {period_label}: ERR  ({elapsed:.0f}s)")
                # Print last few lines for debugging
                err_lines = [l for l in output.strip().split('\n') if l.strip()][-5:]
                for el in err_lines:
                    print(f"    > {el}")

        all_results[name] = results

    # Clean up temp file
    if os.path.exists(STRATEGY_TMP):
        os.remove(STRATEGY_TMP)
    print("\nCleaned up temp strategy file.")

    # === Summary Table ===
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print()

    header = f"{'Config':<20} | {'2022':>8} | {'2023':>8} | {'2024':>8} | {'2025':>8} | {'2026Q1':>8} | {'SUM':>8} | {'All+':>5}"
    print(header)
    print("-" * len(header))

    # Baselines first
    for bname, bvals in BASELINES.items():
        total = sum(bvals.values())
        all_pos = all(v > 0 for v in bvals.values())
        row = f"{bname:<20}"
        for p in ["2022", "2023", "2024", "2025", "2026Q1"]:
            v = bvals[p]
            row += f" | {v:+7.1f}%"
        row += f" | {total:+7.0f}%"
        row += f" | {'YES' if all_pos else 'NO':>5}"
        print(row)

    print("-" * len(header))

    # Sweep results
    for name, results in all_results.items():
        row = f"{name:<20}"
        numeric_vals = []
        for p in ["2022", "2023", "2024", "2025", "2026Q1"]:
            v = results.get(p, "ERR")
            if isinstance(v, (int, float)):
                row += f" | {v:+7.1f}%"
                numeric_vals.append(v)
            else:
                row += f" | {'ERR':>8}"
        if len(numeric_vals) == 5:
            total = sum(numeric_vals)
            all_pos = all(v > 0 for v in numeric_vals)
            row += f" | {total:+7.0f}%"
            row += f" | {'YES' if all_pos else 'NO':>5}"
        else:
            row += f" | {'N/A':>8} | {'N/A':>5}"
        print(row)

    print()
    print("Target: beat s523v +652% (all years positive)")
    print()

    # Save results to JSON for later reference
    import json
    results_path = os.path.join(WORKSPACE, "research", "s523y_param_sweep_results.json")
    with open(results_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
