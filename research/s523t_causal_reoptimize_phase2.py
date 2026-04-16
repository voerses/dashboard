"""
S523t causal re-optimization Phase 2.
Focus: push 2024 higher while keeping other years positive.

Phase 1 findings:
- G30_DB30_t10: best 2024 (+89%) but 2023 drops to +108%
- G30_DB30_t8: good balance (+90/+159/+81/+297)
- Key insight: tighter deep bear threshold = more longs allowed = better 2024
- 2026Q1 is -31% for ALL configs (structural, ignore it)

Phase 2: explore (1) even tighter deep bear, (2) no deep bear with wider gate,
(3) mixed windows to optimize responsiveness.
"""
import subprocess
import json
import os
import re
import shutil
import time

STRATEGY_SRC = "/workspace/crypto_backtest/strategies/s523t_deep_bear_filter.py"
STRATEGY_BAK = "/workspace/crypto_backtest/strategies/s523t_deep_bear_filter.py.bak"
PYTHON = "/workspace/venv/bin/python"
ENGINE = "v4/portfolio_backtest.py"
WORKDIR = "/workspace/crypto_backtest"

PERIODS = [
    ("2022", 12, "2023-01-01T00:00:00"),
    ("2023", 12, "2024-01-01T00:00:00"),
    ("2024", 12, "2025-01-01T00:00:00"),
    ("2025", 12, "2026-01-01T00:00:00"),
]

CONFIGS = [
    # Deep bear at -15% (very tight = almost no long suppression)
    ("G30_DB30_t15",    30, 30, -0.15, "normal"),
    ("G14_DB30_t15",    14, 30, -0.15, "normal"),
    # Deep bear at -12%
    ("G30_DB30_t12",    30, 30, -0.12, "normal"),
    ("G14_DB30_t12",    14, 30, -0.12, "normal"),
    # No deep bear at all, just gate
    ("G30_noDB",        30, None, None, "normal"),
    ("G21_noDB",        21, None, None, "normal"),
    ("G7_noDB",          7, None, None, "normal"),
    # Gate=30d with 45d deep bear at various thresholds
    ("G30_DB45_t8",     30, 45, -0.08, "normal"),
    ("G30_DB45_t10",    30, 45, -0.10, "normal"),
    # Gate=14d with 30d deep bear at -8% (responsive gate + moderate filter)
    ("G14_DB30_t8",     14, 30, -0.08, "normal"),
    ("G14_DB30_t10",    14, 30, -0.10, "normal"),
    # Gate=14d with 45d deep bear at -8%
    ("G14_DB45_t8",     14, 45, -0.08, "normal"),
    ("G14_DB45_t10",    14, 45, -0.10, "normal"),
    # Try gate=45d (very slow gate = almost never blocks shorts in bull markets)
    ("G45_DB30_t8",     45, 30, -0.08, "normal"),
    ("G45_DB30_t10",    45, 30, -0.10, "normal"),
    # Try gate=60d
    ("G60_DB30_t10",    60, 30, -0.10, "normal"),
]


def patch_strategy(cfg_name, gate_w, db_w, db_t, halving_mode):
    """Patch the strategy file with the given config."""
    with open(STRATEGY_SRC, "r") as f:
        code = f.read()

    gate_hours = gate_w * 24 if gate_w else None
    db_hours = db_w * 24 if db_w else None

    old_ret = """        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift(30 * 24) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)"""

    if gate_w and db_w and gate_w != db_w:
        new_ret = f"""        _m_ret_gate_h = (btc_aligned / btc_aligned.shift({gate_hours}) - 1).values
        _m_ret_gate_h = np.nan_to_num(_m_ret_gate_h, nan=0)
        _m_ret_db_h = (btc_aligned / btc_aligned.shift({db_hours}) - 1).values
        _m_ret_db_h = np.nan_to_num(_m_ret_db_h, nan=0)
        _m_ret_1mo_h = _m_ret_gate_h"""
    elif gate_w:
        new_ret = f"""        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift({gate_hours}) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)"""
    elif db_w:
        new_ret = f"""        _m_ret_1mo_h = (btc_aligned / btc_aligned.shift({db_hours}) - 1).values
        _m_ret_1mo_h = np.nan_to_num(_m_ret_1mo_h, nan=0)"""
    else:
        new_ret = old_ret

    code = code.replace(old_ret, new_ret)

    old_gate = "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)"
    if gate_w and db_w and gate_w != db_w:
        new_gate = "        _short_ok = (np.nan_to_num(_m_ret_gate_h, nan=0) < 0)"
    elif gate_w is None:
        new_gate = "        _short_ok = np.ones(n, dtype=bool)"
    else:
        new_gate = old_gate
    code = code.replace(old_gate, new_gate)

    old_db = "        _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.05"
    if db_w is None:
        new_db = "        _deep_bear = np.zeros(n, dtype=bool)"
    elif gate_w and db_w and gate_w != db_w:
        new_db = f"        _deep_bear = np.nan_to_num(_m_ret_db_h, nan=0) < {db_t}"
    else:
        new_db = f"        _deep_bear = np.nan_to_num(_m_ret_1mo_h, nan=0) < {db_t}"
    code = code.replace(old_db, new_db)

    old_halving = """    _bear_years = np.isin(_bar_years, [2021, 2022, 2025, 2026, 2029, 2030])
    _sof = np.where(_bear_years, True, _short_ok)"""

    if halving_mode == "no_halving":
        code = code.replace(old_halving, "    _sof = _short_ok")

    with open(STRATEGY_SRC, "w") as f:
        f.write(code)


def restore_strategy():
    shutil.copy2(STRATEGY_BAK, STRATEGY_SRC)


def run_backtest(months, end_date):
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=WORKDIR)
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "Total Return" in line:
                m = re.search(r'([-+]?\d+\.?\d*)%', line)
                if m:
                    return float(m.group(1))
        return None
    except:
        return None


def main():
    shutil.copy2(STRATEGY_SRC, STRATEGY_BAK)
    results = {}

    for ci, (cfg_name, gate_w, db_w, db_t, halving_mode) in enumerate(CONFIGS):
        print(f"\nConfig {ci+1}/{len(CONFIGS)}: {cfg_name} (gate={gate_w}d, db={db_w}d/{db_t})")
        restore_strategy()
        patch_strategy(cfg_name, gate_w, db_w, db_t, halving_mode)

        cfg_results = {}
        for period_name, months, end_date in PERIODS:
            ret = run_backtest(months, end_date)
            cfg_results[period_name] = ret
            sym = "+" if ret and ret > 0 else ""
            print(f"  {period_name}: {sym}{ret}%")

        vals = [v for v in cfg_results.values() if v is not None]
        total = sum(vals) if vals else None
        all_pos = all(v is not None and v > 0 for v in cfg_results.values())
        results[cfg_name] = {"config": {"gate_window": gate_w, "deep_bear_window": db_w,
                              "deep_bear_thresh": db_t, "halving_mode": halving_mode},
                              "returns": cfg_results, "total": total, "all_positive": all_pos}
        print(f"  => TOTAL: {total:.0f}%  all_pos={all_pos}")

    restore_strategy()

    # Merge with phase 1 results
    try:
        with open("/workspace/crypto_backtest/research/s523t_causal_reoptimize.json") as f:
            phase1 = json.load(f)
        results.update(phase1)
    except:
        pass

    with open("/workspace/crypto_backtest/research/s523t_causal_reoptimize.json", "w") as f:
        json.dump(results, f, indent=2)

    # Print sorted matrix (4 full years only)
    print(f"\n{'='*80}")
    print("FULL RESULTS MATRIX (sorted by 4yr total, all-positive first)")
    print(f"{'='*80}")
    print(f"{'Config':<20} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'4YR':>8} {'AllPos':>6}")
    print("-" * 78)

    sorted_cfgs = sorted(
        results.items(),
        key=lambda x: (
            not x[1]["all_positive"],
            -sum(x[1]["returns"].get(y, 0) or 0 for y in ["2022","2023","2024","2025"])
        )
    )
    for name, data in sorted_cfgs:
        r = data["returns"]
        def fmt(v):
            return f"{v:+.0f}%" if v is not None else "N/A"
        yr4 = sum(r.get(y, 0) or 0 for y in ["2022","2023","2024","2025"])
        ap4 = all((r.get(y) or 0) > 0 for y in ["2022","2023","2024","2025"])
        ap_str = "YES" if ap4 else "no"
        print(f"{name:<20} {fmt(r.get('2022')):>8} {fmt(r.get('2023')):>8} {fmt(r.get('2024')):>8} {fmt(r.get('2025')):>8} {fmt(yr4):>8} {ap_str:>6}")

    print("-" * 78)
    print(f"{'s523t_30d(current)':<20} {'+50%':>8} {'+166%':>8} {'+77%':>8} {'+270%':>8} {'+563%':>8} {'YES':>6}")
    print(f"{'s523c_nomonthly':<20} {'+3%':>8} {'-47%':>8} {'+141%':>8} {'+352%':>8} {'+449%':>8} {'no':>6}")


if __name__ == "__main__":
    main()
