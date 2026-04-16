"""
S523t causal rolling-window re-optimization.
Tests 13 configs of gate window, deep bear window/threshold, and halving gate.
"""
import subprocess
import json
import os
import re
import shutil
import sys
import time

STRATEGY_SRC = "/workspace/crypto_backtest/strategies/s523t_deep_bear_filter.py"
STRATEGY_BAK = "/workspace/crypto_backtest/strategies/s523t_deep_bear_filter.py.bak"
PYTHON = "/workspace/venv/bin/python"
ENGINE = "v4/portfolio_backtest.py"
WORKDIR = "/workspace/crypto_backtest"

# Test periods: (label, months, end_date)
PERIODS = [
    ("2022", 12, "2023-01-01T00:00:00"),
    ("2023", 12, "2024-01-01T00:00:00"),
    ("2024", 12, "2025-01-01T00:00:00"),
    ("2025", 12, "2026-01-01T00:00:00"),
    ("2026Q1", 3, "2026-04-01T00:00:00"),
]

# Config: (name, gate_window, deep_bear_window, deep_bear_thresh, halving_mode)
# halving_mode: "normal" = current code, "no_halving" = no halving gate at all,
#               "halving_nogateB" = bear years fully ungated (no gate needed), bull years use gate_window
CONFIGS = [
    # A: faster gate, stable filter
    ("G14_DB30_t5",     14, 30, -0.05, "normal"),
    ("G14_DB14_t5",     14, 14, -0.05, "normal"),
    ("G14_DB45_t5",     14, 45, -0.05, "normal"),
    ("G21_DB30_t5",     21, 30, -0.05, "normal"),
    ("G7_DB30_t5",       7, 30, -0.05, "normal"),
    # B: threshold sweep
    ("G30_DB30_t3",     30, 30, -0.03, "normal"),
    ("G30_DB30_t8",     30, 30, -0.08, "normal"),
    ("G30_DB30_t10",    30, 30, -0.10, "normal"),
    # C: no deep bear
    ("G14_noDB",        14, None, None, "normal"),
    # D: loose combo
    ("G14_DB14_t3",     14, 14, -0.03, "normal"),
    # E: halving combos
    ("noGate_DBonly",   None, 30, -0.05, "no_halving"),
    ("G14_halving",     14, 30, -0.05, "halving_nogateB"),
    ("G21_halving",     21, 30, -0.05, "halving_nogateB"),
]


def patch_strategy(cfg_name, gate_w, db_w, db_t, halving_mode):
    """Patch the strategy file with the given config."""
    with open(STRATEGY_SRC, "r") as f:
        code = f.read()

    # 1. Replace rolling window for the main return calculation
    # The gate uses _m_ret_1mo_h, deep bear uses it too (or its own)
    # We need to handle the case where gate and deep bear use DIFFERENT windows

    gate_hours = gate_w * 24 if gate_w else None
    db_hours = db_w * 24 if db_w else None

    # Replace the rolling return computation
    old_ret = """        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift(30 * 24) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)"""

    if gate_w and db_w and gate_w != db_w:
        # Two separate rolling returns
        new_ret = f"""        _m_ret_gate_h = (btc_aligned / btc_aligned.shift({gate_hours}) - 1).values
        _m_ret_gate_h = np.nan_to_num(_m_ret_gate_h, nan=0)
        _m_ret_db_h = (btc_aligned / btc_aligned.shift({db_hours}) - 1).values
        _m_ret_db_h = np.nan_to_num(_m_ret_db_h, nan=0)
        _m_ret_1mo_h = _m_ret_gate_h  # for compatibility"""
    elif gate_w:
        new_ret = f"""        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift({gate_hours}) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)"""
    elif db_w:
        new_ret = f"""        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift({db_hours}) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)"""
    else:
        # No gate, no deep bear — keep original but won't be used
        new_ret = old_ret

    code = code.replace(old_ret, new_ret)

    # 2. Replace the short gate
    old_gate = "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)"
    if gate_w and db_w and gate_w != db_w:
        new_gate = "        _short_ok = (np.nan_to_num(_m_ret_gate_h, nan=0) < 0)"
    elif gate_w is None:
        new_gate = "        _short_ok = np.ones(n, dtype=bool)  # no gate"
    else:
        new_gate = old_gate  # uses _m_ret_1mo_h which was already set to right window
    code = code.replace(old_gate, new_gate)

    # 3. Replace the deep bear filter
    old_db = "        _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.05"
    if db_w is None:
        new_db = "        _deep_bear = np.zeros(n, dtype=bool)  # no deep bear"
    elif gate_w and db_w and gate_w != db_w:
        new_db = f"        _deep_bear = np.nan_to_num(_m_ret_db_h, nan=0) < {db_t}"
    else:
        new_db = f"        _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < {db_t}"
    code = code.replace(old_db, new_db)

    # 4. Handle halving mode
    old_halving = """    _bear_years = np.isin(_bar_years, [2021, 2022, 2025, 2026, 2029, 2030])
    _sof = np.where(_bear_years, True, _short_ok)"""

    if halving_mode == "normal":
        pass  # keep as-is
    elif halving_mode == "no_halving":
        # No halving gate — just use _short_ok everywhere (or True if no gate)
        new_halving = "    _sof = _short_ok"
        code = code.replace(old_halving, new_halving)
    elif halving_mode == "halving_nogateB":
        # Bear years: fully ungated (True), Bull years: use gate
        pass  # same as "normal" — bear years already ungated

    with open(STRATEGY_SRC, "w") as f:
        f.write(code)


def restore_strategy():
    shutil.copy2(STRATEGY_BAK, STRATEGY_SRC)


def run_backtest(months, end_date):
    """Run backtest and return total_return_pct."""
    cmd = [
        PYTHON, ENGINE,
        "--strategy", "s523t_deep_bear_filter",
        "--months", str(months),
        "--capital", "50000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "40",
        "--concentration", "0.30",
        "--skip-wf",
        "--end-date", end_date,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=WORKDIR
        )
        output = result.stdout + result.stderr
        # Parse total return from output
        # Look for "Total Return:" or similar
        for line in output.split("\n"):
            if "Total Return" in line or "total_return" in line:
                # Extract percentage
                m = re.search(r'([-+]?\d+\.?\d*)%', line)
                if m:
                    return float(m.group(1))
            if "TOTAL_RETURN_PCT" in line:
                m = re.search(r'([-+]?\d+\.?\d*)', line)
                if m:
                    return float(m.group(1))
        # Try to find it in JSON output
        m = re.search(r'"total_return_pct":\s*([-+]?\d+\.?\d*)', output)
        if m:
            return float(m.group(1))
        # Try "Return:" pattern
        m = re.search(r'Return:\s*([-+]?\d+\.?\d*)%', output)
        if m:
            return float(m.group(1))
        # Save output for debugging
        print(f"  [DEBUG] Could not parse return. Last 30 lines:")
        lines = output.strip().split("\n")
        for l in lines[-30:]:
            print(f"    {l}")
        return None
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT]")
        return None
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None


def main():
    # Backup original
    shutil.copy2(STRATEGY_SRC, STRATEGY_BAK)

    # Clear module cache between runs
    results = {}
    total_configs = len(CONFIGS)

    for ci, (cfg_name, gate_w, db_w, db_t, halving_mode) in enumerate(CONFIGS):
        print(f"\n{'='*60}")
        print(f"Config {ci+1}/{total_configs}: {cfg_name}")
        print(f"  gate={gate_w}d, deep_bear={db_w}d/{db_t}, halving={halving_mode}")
        print(f"{'='*60}")

        # Restore clean copy then patch
        restore_strategy()
        # Clear any cached BTC data (force reload)
        patch_strategy(cfg_name, gate_w, db_w, db_t, halving_mode)

        cfg_results = {}
        for period_name, months, end_date in PERIODS:
            print(f"  Running {period_name} (months={months}, end={end_date})...")
            t0 = time.time()
            ret = run_backtest(months, end_date)
            elapsed = time.time() - t0
            cfg_results[period_name] = ret
            print(f"    -> {ret}% ({elapsed:.0f}s)")

        results[cfg_name] = {
            "config": {
                "gate_window": gate_w,
                "deep_bear_window": db_w,
                "deep_bear_thresh": db_t,
                "halving_mode": halving_mode,
            },
            "returns": cfg_results,
        }

        # Compute sum
        vals = [v for v in cfg_results.values() if v is not None]
        total = sum(vals) if vals else None
        results[cfg_name]["total"] = total
        all_pos = all(v is not None and v > 0 for v in cfg_results.values())
        results[cfg_name]["all_positive"] = all_pos
        print(f"  TOTAL: {total}%  all_positive={all_pos}")

    # Restore original
    restore_strategy()

    # Save results
    out_path = "/workspace/crypto_backtest/research/s523t_causal_reoptimize.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")

    # Print sorted results matrix
    print(f"\n{'='*80}")
    print("RESULTS MATRIX (sorted by total return, all-positive first)")
    print(f"{'='*80}")
    print(f"{'Config':<20} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'2026Q1':>8} {'TOTAL':>8} {'AllPos':>6}")
    print("-" * 80)

    sorted_cfgs = sorted(
        results.items(),
        key=lambda x: (not x[1]["all_positive"], -(x[1]["total"] or -9999))
    )
    for name, data in sorted_cfgs:
        r = data["returns"]
        def fmt(v):
            return f"{v:+.0f}%" if v is not None else "N/A"
        t = data["total"]
        ap = "YES" if data["all_positive"] else "no"
        print(f"{name:<20} {fmt(r.get('2022')):>8} {fmt(r.get('2023')):>8} {fmt(r.get('2024')):>8} {fmt(r.get('2025')):>8} {fmt(r.get('2026Q1')):>8} {fmt(t):>8} {ap:>6}")

    # Baselines
    print("-" * 80)
    print(f"{'s523t_current':<20} {'+50%':>8} {'+166%':>8} {'+77%':>8} {'+270%':>8} {'+35%':>8} {'+598%':>8} {'YES':>6}")
    print(f"{'s523c_nomonthly':<20} {'+3%':>8} {'-47%':>8} {'+141%':>8} {'+352%':>8} {'N/A':>8} {'+449%':>8} {'no':>6}")


if __name__ == "__main__":
    main()
