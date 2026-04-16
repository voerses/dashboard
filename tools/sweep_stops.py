#!/usr/bin/env python3
"""Sweep stop_mult across 4 regime+direction combos for s524r_time_stops."""

import subprocess
import sys
import os
import re

STRATEGY_FILE = "/workspace/crypto_backtest/strategies/s524r_time_stops.py"

ORIGINAL_BLOCK = """    _stop_mult = np.full(n, STOP_MULT)
    _bear_long = _bear_regime & (direction == 1) & entry
    _stop_mult[_bear_long] = STOP_MULT_BEAR_LONG"""

BACKTESTS = [
    ("2023-01-01T00:00:00", "2022", 12),
    ("2024-01-01T00:00:00", "2023", 12),
    ("2025-01-01T00:00:00", "2024", 12),
    ("2026-01-01T00:00:00", "2025", 12),
    ("2026-04-05T16:00:00", "Q1-26", 3),
]

COMBOS = [
    ("BEAR_LONG",  "_bear_regime",    "1"),
    ("BEAR_SHORT", "_bear_regime",    "-1"),
    ("BULL_LONG",  "(~_bear_regime)", "1"),
    ("BULL_SHORT", "(~_bear_regime)", "-1"),
]

STOPS = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]


def read_file():
    with open(STRATEGY_FILE, 'r') as f:
        return f.read()


def write_file(content):
    with open(STRATEGY_FILE, 'w') as f:
        f.write(content)


def set_stop(original_content, regime_expr, direction, stop_val):
    """Replace the stop block with a sweep-specific version."""
    if stop_val == 5.0:
        # Default for everything = 5.0, no override needed
        new_block = "    _stop_mult = np.full(n, 5.0)"
    else:
        new_block = (
            f"    _stop_mult = np.full(n, 5.0)\n"
            f"    _sweep_mask = {regime_expr} & (direction == {direction}) & entry\n"
            f"    _stop_mult[_sweep_mask] = {stop_val}"
        )

    result = original_content.replace(ORIGINAL_BLOCK, new_block)
    if result == original_content:
        print("ERROR: Could not find the stop block to replace!", file=sys.stderr)
        sys.exit(1)
    return result


def run_backtest(end_date, label, months):
    """Run one backtest, return the Total Return string."""
    cmd = [
        "/workspace/venv/bin/python", "v4/portfolio_backtest.py",
        "--strategy", "s524r_time_stops",
        "--months", str(months),
        "--capital", "100000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "50",
        "--concentration", "0.30",
        "--skip-wf",
        "--adv-cap", "0.005",
        "--end-date", end_date,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            cwd="/workspace/crypto_backtest"
        )
        output = result.stdout + result.stderr
        for line in output.split('\n'):
            if 'Total Return' in line:
                val = line.split(':')[-1].strip()
                return val
        return "N/A"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"ERR:{e}"


def parse_pct(s):
    """Parse '+176.32%' to 176.32"""
    try:
        cleaned = s.replace('%', '').replace('+', '').replace(',', '').strip()
        return float(cleaned)
    except:
        return 0.0


def main():
    original_content = read_file()

    # Verify the original block exists
    if ORIGINAL_BLOCK not in original_content:
        print("ERROR: Cannot find the stop_mult block in strategy file!")
        print("Expected block:")
        print(ORIGINAL_BLOCK)
        sys.exit(1)

    all_results = {}  # {combo_name: {stop: {label: value}}}
    best_stops = {}   # {combo_name: best_stop_val}

    for combo_name, regime_expr, direction in COMBOS:
        print(f"\n=== {combo_name} sweep ===")
        print(f"{'stop':<5} | {'2022':>7} | {'2023':>7} | {'2024':>7} | {'2025':>7} | {'Q1-26':>7} | {'SUM':>7}")
        print("-" * 65)

        combo_results = {}
        best_sum = -99999
        best_stop = 5.0

        for stop_val in STOPS:
            # Modify strategy file
            modified = set_stop(original_content, regime_expr, direction, stop_val)
            write_file(modified)

            year_results = {}
            for end_date, label, months in BACKTESTS:
                val = run_backtest(end_date, label, months)
                year_results[label] = val

            # Compute sum
            total = sum(parse_pct(year_results[label]) for _, label, _ in BACKTESTS)

            print(f"{stop_val:<5} | {year_results['2022']:>7} | {year_results['2023']:>7} | {year_results['2024']:>7} | {year_results['2025']:>7} | {year_results['Q1-26']:>7} | {total:>7.0f}%")
            sys.stdout.flush()

            combo_results[stop_val] = year_results
            if total > best_sum:
                best_sum = total
                best_stop = stop_val

        all_results[combo_name] = combo_results
        best_stops[combo_name] = best_stop
        print(f"\nBEST {combo_name}: stop={best_stop} (SUM={best_sum:.0f}%)")

    # Restore original
    write_file(original_content)
    print("\n" + "=" * 65)
    print("Strategy file restored to original.")

    # Summary
    print("\n=== OPTIMAL STOPS PER COMBO ===")
    for combo_name, stop_val in best_stops.items():
        print(f"  {combo_name}: {stop_val}")

    # Final combined backtest
    print("\n=== FINAL COMBINED BACKTEST ===")
    print(f"Applying: bear_long={best_stops['BEAR_LONG']}, bear_short={best_stops['BEAR_SHORT']}, "
          f"bull_long={best_stops['BULL_LONG']}, bull_short={best_stops['BULL_SHORT']}")

    # Build combined replacement
    lines = ["    _stop_mult = np.full(n, 5.0)"]
    bl = best_stops['BEAR_LONG']
    bs = best_stops['BEAR_SHORT']
    bul = best_stops['BULL_LONG']
    bus = best_stops['BULL_SHORT']

    if bl != 5.0:
        lines.append(f"    _stop_mult[_bear_regime & (direction == 1) & entry] = {bl}")
    if bs != 5.0:
        lines.append(f"    _stop_mult[_bear_regime & (direction == -1) & entry] = {bs}")
    if bul != 5.0:
        lines.append(f"    _stop_mult[(~_bear_regime) & (direction == 1) & entry] = {bul}")
    if bus != 5.0:
        lines.append(f"    _stop_mult[(~_bear_regime) & (direction == -1) & entry] = {bus}")

    combined_block = "\n".join(lines)
    modified = original_content.replace(ORIGINAL_BLOCK, combined_block)
    write_file(modified)

    print(f"{'label':>7} | {'return':>10}")
    total = 0
    for end_date, label, months in BACKTESTS:
        val = run_backtest(end_date, label, months)
        pct = parse_pct(val)
        total += pct
        print(f"{label:>7} | {val:>10}")
    print(f"{'SUM':>7} | {total:>9.0f}%")

    # Restore original
    write_file(original_content)
    print("\nStrategy file restored to original.")


if __name__ == "__main__":
    main()
