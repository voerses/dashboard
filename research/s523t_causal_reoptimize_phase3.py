"""
S523t causal re-optimization Phase 3.
Focus: maximize 2024 return. Key insight from phases 1-2:
- The short gate HURTS in 2024 because it blocks profitable shorts during BTC rallies
- The deep bear filter at -5% kills too many longs in volatile months
- Best 2024 so far: +89% (G30_DB30_t10) — need +141% target

New ideas:
1. No deep bear at all + wide gate (G45/G60)
2. Very tight deep bear (-20%) = almost no effect
3. Deep bear at -10% with gate at 45d/60d
4. Different deep bear windows (60d, 90d) for more stability
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
    # Gate 45d + no deep bear
    ("G45_noDB",        45, None, None, "normal"),
    # Gate 60d + no deep bear
    ("G60_noDB",        60, None, None, "normal"),
    # Gate 45d + very loose deep bear
    ("G45_DB30_t15",    45, 30, -0.15, "normal"),
    ("G45_DB30_t12",    45, 30, -0.12, "normal"),
    # Gate 45d + 60d deep bear (very stable)
    ("G45_DB60_t8",     45, 60, -0.08, "normal"),
    ("G45_DB60_t10",    45, 60, -0.10, "normal"),
    # Gate 60d + deep bear combos
    ("G60_DB30_t8",     60, 30, -0.08, "normal"),
    ("G60_DB45_t10",    60, 45, -0.10, "normal"),
    # SPECIAL: no gate in bull years, gate in bear years, with different DBs
    # halving_bull_only: bull years (2023,2024) get no short gate at all
    ("noGateB_DB30_t10", None, 30, -0.10, "halving_bull_nogateB"),
    ("noGateB_DB30_t8",  None, 30, -0.08, "halving_bull_nogateB"),
    # Original 30d with -10% deep bear for comparison baseline
    ("G30_DB30_t10_v",  30, 30, -0.10, "normal"),
]


def patch_strategy(cfg_name, gate_w, db_w, db_t, halving_mode):
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
    elif halving_mode == "halving_bull_nogateB":
        # Bull years (2023, 2024, 2027, 2028): no short gate at all
        # Bear years (2021, 2022, 2025, 2026): no short gate either (True)
        # Basically: shorts always ungated, rely only on deep_bear for long suppression
        code = code.replace(old_halving, "    _sof = np.ones(n, dtype=bool)  # all years ungated")

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
        print(f"\nConfig {ci+1}/{len(CONFIGS)}: {cfg_name} (gate={gate_w}d, db={db_w}d/{db_t}, halving={halving_mode})")
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

    # Merge with prior results
    try:
        with open("/workspace/crypto_backtest/research/s523t_causal_reoptimize.json") as f:
            prior = json.load(f)
        prior.update(results)
        results = prior
    except:
        pass

    with open("/workspace/crypto_backtest/research/s523t_causal_reoptimize.json", "w") as f:
        json.dump(results, f, indent=2)

    # Print full sorted matrix
    print(f"\n{'='*80}")
    print("FULL RESULTS MATRIX (all phases, sorted by 4yr total, all-positive first)")
    print(f"{'='*80}")
    print(f"{'Config':<22} {'2022':>7} {'2023':>7} {'2024':>7} {'2025':>7} {'4YR':>7} {'AP':>4}")
    print("-" * 65)

    sorted_cfgs = sorted(
        results.items(),
        key=lambda x: (
            not all((x[1]["returns"].get(y) or 0) > 0 for y in ["2022","2023","2024","2025"]),
            -sum(x[1]["returns"].get(y, 0) or 0 for y in ["2022","2023","2024","2025"])
        )
    )
    for name, data in sorted_cfgs:
        r = data["returns"]
        def fmt(v):
            return f"{v:+.0f}%" if v is not None else "N/A"
        yr4 = sum(r.get(y, 0) or 0 for y in ["2022","2023","2024","2025"])
        ap4 = all((r.get(y) or 0) > 0 for y in ["2022","2023","2024","2025"])
        print(f"{name:<22} {fmt(r.get('2022')):>7} {fmt(r.get('2023')):>7} {fmt(r.get('2024')):>7} {fmt(r.get('2025')):>7} {fmt(yr4):>7} {'Y' if ap4 else 'n':>4}")

    print("-" * 65)
    print(f"{'s523t_30d(current)':<22} {'+50%':>7} {'+166%':>7} {'+77%':>7} {'+270%':>7} {'+563%':>7} {'Y':>4}")
    print(f"{'s523c(no monthly)':<22} {'+3%':>7} {'-47%':>7} {'+141%':>7} {'+352%':>7} {'+449%':>7} {'n':>4}")


if __name__ == "__main__":
    main()
