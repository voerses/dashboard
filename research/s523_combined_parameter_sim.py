#!/usr/bin/env python3
"""
s523 Combined Parameter Simulation (v2)
=========================================
Full 51-month runs with per-year equity curve extraction.

Phases:
  1. Per-year optimal z-score window (V2_BASE and SHORT_HEAVY, full-period runs)
  2. Combined regime switch (dynamic V2_BASE <-> SHORT_HEAVY)
  3. Blacklist impact on best combined config

Results saved to: research/s523_combined_parameter_results.json
"""

import sys
import os
import json
import time
import shutil
import subprocess
import traceback
from datetime import datetime

sys.path.insert(0, "/workspace/crypto_backtest")

# ─── Paths ──────────────────────────────────────────────────────────────────

STRATEGY_PATH = "/workspace/crypto_backtest/strategies/s523h_regime_adaptive.py"
BACKUP_PATH = "/workspace/crypto_backtest/strategies/s523h_regime_adaptive.py.bak"
RESULTS_PATH = "/workspace/crypto_backtest/research/s523_combined_parameter_results.json"
OUTPUT_DIR = "/workspace/crypto_backtest/results/v4/s523_param_sim"
PYTHON = "/workspace/venv/bin/python"
BACKTEST_SCRIPT = "/workspace/crypto_backtest/v4/portfolio_backtest.py"

CAPITAL = 50000
MONTHS = 51
END_DATE = "2026-04-05"

ZSCORE_VALUES = [15, 18, 20, 22, 25, 30, 35]
REGIME_THRESHOLDS = [-0.05, -0.10, -0.15]

# ─── Utility ────────────────────────────────────────────────────────────────

def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def read_strategy():
    with open(STRATEGY_PATH) as f:
        return f.read()

def write_strategy(code):
    with open(STRATEGY_PATH, "w") as f:
        f.write(code)

def backup_strategy():
    shutil.copy2(STRATEGY_PATH, BACKUP_PATH)

def restore_strategy():
    if os.path.exists(BACKUP_PATH):
        shutil.copy2(BACKUP_PATH, STRATEGY_PATH)


# ─── Strategy Patching ──────────────────────────────────────────────────────

def patch_v2_base(code, zw, blacklist="none"):
    """V2_BASE: proven config with 1mo_red short gate, no regime flip, RSI=always."""
    if blacklist == "none":
        code = code.replace(
            "TOKEN_BLACKLIST = {",
            "TOKEN_BLACKLIST = set()\n_OLD_BLACKLIST = {"
        )
    code = code.replace("ZSCORE_WINDOW_DAYS = 30", f"ZSCORE_WINDOW_DAYS = {zw}")
    code = code.replace("RSI_WINDOW_1H = 72", "RSI_WINDOW_1H = 99999")
    code = code.replace(
        "bear_regime = (btc_aligned < btc_sma_200d).values",
        "bear_regime = np.zeros(n, dtype=bool)"
    )

    old_gate = """\
    # Only fire on first bar of new day AND RSI timing condition
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window"""

    new_gate = """\
    # V2_BASE: 1mo_red short gate
    _mr = btc_aligned.resample('MS').last().pct_change().reindex(ctx.idx_1h, method='ffill')
    _short_ok = (_mr < 0).values
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _short_ok"""

    code = code.replace(old_gate, new_gate)
    return code


def patch_short_heavy(code, zw, blacklist="none"):
    """SHORT_HEAVY: no short gate, no RSI, long conviction 0.5x."""
    if blacklist == "none":
        code = code.replace(
            "TOKEN_BLACKLIST = {",
            "TOKEN_BLACKLIST = set()\n_OLD_BLACKLIST = {"
        )
    code = code.replace("ZSCORE_WINDOW_DAYS = 30", f"ZSCORE_WINDOW_DAYS = {zw}")
    code = code.replace("RSI_WINDOW_1H = 72", "RSI_WINDOW_1H = 99999")
    code = code.replace(
        "bear_regime = (btc_aligned < btc_sma_200d).values",
        "bear_regime = np.zeros(n, dtype=bool)"
    )

    old_gate = """\
    # Only fire on first bar of new day AND RSI timing condition
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window"""

    new_gate = """\
    # SHORT_HEAVY: no RSI, no short gate
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change"""

    code = code.replace(old_gate, new_gate)

    code = code.replace(
        "conviction = np.minimum(1.0, abs_composite / 3.0)  # normalize to [0, 1]",
        "conviction = np.minimum(1.0, abs_composite / 3.0)  # normalize to [0, 1]\n"
        "    # SHORT_HEAVY: reduce long conviction by 0.5x\n"
        "    _is_long = direction == 1\n"
        "    conviction[_is_long & entry] *= 0.5"
    )
    return code


def patch_combined_regime(code, zw_bull, zw_bear, threshold, blacklist="none"):
    """COMBINED: dynamic switch V2_BASE <-> SHORT_HEAVY based on BTC 3mo return + SMA200."""
    if blacklist == "none":
        code = code.replace(
            "TOKEN_BLACKLIST = {",
            "TOKEN_BLACKLIST = set()\n_OLD_BLACKLIST = {"
        )
    code = code.replace("ZSCORE_WINDOW_DAYS = 30", f"ZSCORE_WINDOW_DAYS = {zw_bull}")
    code = code.replace("RSI_WINDOW_1H = 72", "RSI_WINDOW_1H = 99999")

    # Replace bear_regime with 3mo return + SMA200d detection
    old_bear = "bear_regime = (btc_aligned < btc_sma_200d).values"
    new_bear = f"""\
# Combined regime: 3mo return < {threshold} AND below SMA200d
        _btc_3mo_ret = btc_aligned.pct_change(periods=90*24).values
        _btc_below_sma = (btc_aligned < btc_sma_200d).values
        _btc_3mo_bear = np.nan_to_num(_btc_3mo_ret, nan=0.0) < {threshold}
        bear_regime = _btc_3mo_bear & _btc_below_sma
        _is_bear_regime = bear_regime.copy()"""

    code = code.replace(old_bear, new_bear)

    # No composite flip (we handle direction via entry gating)
    code = code.replace(
        "regime_flip = np.where(bear_regime, -1.0, 1.0)",
        "regime_flip = np.ones(n, dtype=np.float64)  # Combined: no composite flip"
    )

    old_gate = """\
    # Only fire on first bar of new day AND RSI timing condition
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window"""

    new_gate = f"""\
    # COMBINED REGIME ENTRY GATE
    _mr = btc_aligned.resample('MS').last().pct_change().reindex(ctx.idx_1h, method='ffill')
    _short_ok_bull = (_mr < 0).values
    _bull_mask = ~_is_bear_regime
    _bear_mask = _is_bear_regime
    # Bull (V2_BASE): RSI always on + 1mo_red short gate
    long_signal_bull = long_signal & day_change & rsi_long_window & _bull_mask
    short_signal_bull = short_signal & day_change & rsi_short_window & _short_ok_bull & _bull_mask
    # Bear (SHORT_HEAVY): no gates
    long_signal_bear = long_signal & day_change & _bear_mask
    short_signal_bear = short_signal & day_change & _bear_mask
    long_signal = long_signal_bull | long_signal_bear
    short_signal = short_signal_bull | short_signal_bear"""

    code = code.replace(old_gate, new_gate)

    # Reduce long conviction in bear regime
    code = code.replace(
        "conviction = np.minimum(1.0, abs_composite / 3.0)  # normalize to [0, 1]",
        "conviction = np.minimum(1.0, abs_composite / 3.0)  # normalize to [0, 1]\n"
        "    # COMBINED: reduce long conviction in bear regime\n"
        "    _is_long = direction == 1\n"
        "    conviction[_is_long & entry & _is_bear_regime] *= 0.5"
    )
    return code


# ─── Engine Run ─────────────────────────────────────────────────────────────

def run_full_backtest(label_suffix):
    """Run 51-month backtest, return (metrics_dict, per_year_returns) or (None, None)."""
    out_dir = os.path.join(OUTPUT_DIR, f"s523h_{label_suffix}")
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        PYTHON, BACKTEST_SCRIPT,
        "--strategy", "s523h_regime_adaptive",
        "--months", str(MONTHS),
        "--capital", str(CAPITAL),
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30",
        "--concentration", "0.30",
        "--skip-wf",
        "--end-date", END_DATE,
        "--output", out_dir,
    ]

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            cwd="/workspace/crypto_backtest"
        )
        elapsed = time.time() - t0

        # Find metrics and equity curve files
        metrics_dict = None
        eq_data = None
        for fn in os.listdir(out_dir):
            fpath = os.path.join(out_dir, fn)
            if fn.endswith("_metrics.json"):
                with open(fpath) as f:
                    metrics_dict = json.load(f)
            elif fn.endswith("_equity_curve.json"):
                with open(fpath) as f:
                    eq_data = json.load(f)

        if metrics_dict is None:
            print(f"  [WARN] No metrics for {label_suffix}")
            if result.stdout:
                print(f"  stdout tail: ...{result.stdout[-300:]}")
            if result.stderr:
                print(f"  stderr tail: ...{result.stderr[-300:]}")
            return None, None

        metrics_dict["_elapsed_sec"] = round(elapsed, 1)

        # Extract per-year returns from equity curve
        per_year = {}
        if eq_data:
            dates = sorted(eq_data.keys())
            if dates:
                prev_eq = CAPITAL
                # Group by year
                year_groups = {}
                for d in dates:
                    y = d[:4]
                    if y not in year_groups:
                        year_groups[y] = []
                    year_groups[y].append((d, eq_data[d]))

                for y in sorted(year_groups.keys()):
                    entries = year_groups[y]
                    end_eq = entries[-1][1]
                    ret = (end_eq / prev_eq - 1) * 100
                    per_year[y] = round(ret, 2)
                    prev_eq = end_eq

        return metrics_dict, per_year

    except subprocess.TimeoutExpired:
        print(f"  [WARN] Timeout for {label_suffix}")
        return None, None
    except Exception as e:
        print(f"  [ERROR] {label_suffix}: {e}")
        traceback.print_exc()
        return None, None


# ─── Phase 1 ────────────────────────────────────────────────────────────────

def run_phase1():
    """Z-score window sweep for V2_BASE and SHORT_HEAVY (full-period runs)."""
    print("\n" + "=" * 70)
    print("  PHASE 1: Z-SCORE WINDOW SWEEP (51-month full runs)")
    print("=" * 70)

    results = {"V2_BASE": {}, "SHORT_HEAVY": {}}

    for zw in ZSCORE_VALUES:
        # V2_BASE
        print(f"\n  V2_BASE zw={zw}...")
        backup_strategy()
        try:
            code = read_strategy()
            code = patch_v2_base(code, zw)
            write_strategy(code)
            m, py = run_full_backtest(f"v2base_zw{zw}")
            results["V2_BASE"][zw] = {
                "total_return_pct": _to_float(m.get("total_return_pct")) if m else None,
                "sharpe_ratio": _to_float(m.get("sharpe_ratio")) if m else None,
                "max_drawdown_pct": _to_float(m.get("max_drawdown_pct")) if m else None,
                "total_trades": m.get("total_trades") if m else None,
                "per_year": py or {},
                "elapsed_sec": _to_float(m.get("_elapsed_sec")) if m else None,
            }
            print(f"    Total: {m.get('total_return_pct', 'N/A')}%  Per-year: {py}")
        finally:
            restore_strategy()

        # SHORT_HEAVY
        print(f"\n  SHORT_HEAVY zw={zw}...")
        backup_strategy()
        try:
            code = read_strategy()
            code = patch_short_heavy(code, zw)
            write_strategy(code)
            m, py = run_full_backtest(f"shortheavy_zw{zw}")
            results["SHORT_HEAVY"][zw] = {
                "total_return_pct": _to_float(m.get("total_return_pct")) if m else None,
                "sharpe_ratio": _to_float(m.get("sharpe_ratio")) if m else None,
                "max_drawdown_pct": _to_float(m.get("max_drawdown_pct")) if m else None,
                "total_trades": m.get("total_trades") if m else None,
                "per_year": py or {},
                "elapsed_sec": _to_float(m.get("_elapsed_sec")) if m else None,
            }
            print(f"    Total: {m.get('total_return_pct', 'N/A')}%  Per-year: {py}")
        finally:
            restore_strategy()

    return results


# ─── Phase 2 ────────────────────────────────────────────────────────────────

def find_best_zw_for_years(phase1, mode, target_years):
    """Find zw that maximizes average return across target years."""
    best_zw = 22
    best_avg = -9999
    for zw_key, data in phase1.get(mode, {}).items():
        zw = int(zw_key)
        py = data.get("per_year", {})
        total = 0
        count = 0
        for y in target_years:
            v = py.get(y)
            if v is not None:
                total += v
                count += 1
        if count > 0:
            avg = total / count
            if avg > best_avg:
                best_avg = avg
                best_zw = zw
    return best_zw, best_avg


def run_phase2(phase1):
    """Combined regime switch: full 51-month runs."""
    print("\n" + "=" * 70)
    print("  PHASE 2: COMBINED REGIME SWITCH")
    print("=" * 70)

    # Find optimal bull zw (best for 2022-2024)
    best_bull_zw, bull_avg = find_best_zw_for_years(phase1, "V2_BASE", ["2022", "2023", "2024"])
    print(f"  Best bull (V2_BASE) zw: {best_bull_zw} (avg 2022-2024 return: {bull_avg:.1f}%)")

    # Find optimal bear zw (best for 2025)
    best_bear_zw, bear_ret = find_best_zw_for_years(phase1, "SHORT_HEAVY", ["2025"])
    print(f"  Best bear (SHORT_HEAVY) zw: {best_bear_zw} (2025 return: {bear_ret:.1f}%)")

    results = {"_optimal_zw": {"bull": best_bull_zw, "bear": best_bear_zw}}

    for threshold in REGIME_THRESHOLDS:
        tkey = f"thresh_{int(abs(threshold)*100)}pct"
        print(f"\n  Combined {tkey} (bull_zw={best_bull_zw}, bear_zw={best_bear_zw})...")
        backup_strategy()
        try:
            code = read_strategy()
            code = patch_combined_regime(code, best_bull_zw, best_bear_zw, threshold)
            write_strategy(code)
            m, py = run_full_backtest(f"combined_{tkey}")
            results[tkey] = {
                "total_return_pct": _to_float(m.get("total_return_pct")) if m else None,
                "sharpe_ratio": _to_float(m.get("sharpe_ratio")) if m else None,
                "max_drawdown_pct": _to_float(m.get("max_drawdown_pct")) if m else None,
                "total_trades": m.get("total_trades") if m else None,
                "per_year": py or {},
                "elapsed_sec": _to_float(m.get("_elapsed_sec")) if m else None,
            }
            print(f"    Total: {m.get('total_return_pct', 'N/A')}%  Per-year: {py}")
        finally:
            restore_strategy()

    # Also test with the V2_BASE best zw used for BOTH bull and bear
    for alt_bear_zw in [best_bull_zw, 22]:
        if alt_bear_zw == best_bear_zw:
            continue
        for threshold in [-0.10, -0.15]:
            tkey = f"thresh_{int(abs(threshold)*100)}pct_bearzw{alt_bear_zw}"
            print(f"\n  Combined {tkey} (bull_zw={best_bull_zw}, bear_zw={alt_bear_zw})...")
            backup_strategy()
            try:
                code = read_strategy()
                code = patch_combined_regime(code, best_bull_zw, alt_bear_zw, threshold)
                write_strategy(code)
                m, py = run_full_backtest(f"combined_{tkey}")
                results[tkey] = {
                    "total_return_pct": _to_float(m.get("total_return_pct")) if m else None,
                    "sharpe_ratio": _to_float(m.get("sharpe_ratio")) if m else None,
                    "max_drawdown_pct": _to_float(m.get("max_drawdown_pct")) if m else None,
                    "total_trades": m.get("total_trades") if m else None,
                    "per_year": py or {},
                    "elapsed_sec": _to_float(m.get("_elapsed_sec")) if m else None,
                }
                print(f"    Total: {m.get('total_return_pct', 'N/A')}%  Per-year: {py}")
            finally:
                restore_strategy()

    return results


# ─── Phase 3 ────────────────────────────────────────────────────────────────

def run_phase3(phase2, phase1):
    """Blacklist impact on best combined config."""
    print("\n" + "=" * 70)
    print("  PHASE 3: BLACKLIST IMPACT")
    print("=" * 70)

    # Find best combined config (highest total return with most years positive)
    best_key = None
    best_total = -9999
    for tkey, data in phase2.items():
        if tkey.startswith("_"):
            continue
        tr = _to_float(data.get("total_return_pct"))
        if tr is not None and tr > best_total:
            best_total = tr
            best_key = tkey

    if best_key is None:
        print("  [SKIP] No valid Phase 2 results")
        return {}

    print(f"  Best Phase 2 config: {best_key} (total return: {best_total:.1f}%)")

    # Extract threshold from key
    import re
    m = re.search(r'thresh_(\d+)pct', best_key)
    threshold = -int(m.group(1)) / 100.0 if m else -0.10

    m2 = re.search(r'bearzw(\d+)', best_key)
    bear_zw_override = int(m2.group(1)) if m2 else None

    zw_info = phase2.get("_optimal_zw", {"bull": 22, "bear": 22})
    bull_zw = zw_info["bull"]
    bear_zw = bear_zw_override if bear_zw_override else zw_info["bear"]

    # Run with static blacklist
    print(f"\n  Combined {best_key} WITH static blacklist (bull_zw={bull_zw}, bear_zw={bear_zw})...")
    backup_strategy()
    try:
        code = read_strategy()
        code = patch_combined_regime(code, bull_zw, bear_zw, threshold, blacklist="static")
        write_strategy(code)
        metrics, py = run_full_backtest(f"combined_{best_key}_static")
        result = {
            "static": {
                "total_return_pct": _to_float(metrics.get("total_return_pct")) if metrics else None,
                "sharpe_ratio": _to_float(metrics.get("sharpe_ratio")) if metrics else None,
                "max_drawdown_pct": _to_float(metrics.get("max_drawdown_pct")) if metrics else None,
                "total_trades": metrics.get("total_trades") if metrics else None,
                "per_year": py or {},
            },
            "_best_key": best_key,
            "_threshold": threshold,
            "_bull_zw": bull_zw,
            "_bear_zw": bear_zw,
        }
        print(f"    Total: {metrics.get('total_return_pct', 'N/A')}%  Per-year: {py}")
    finally:
        restore_strategy()

    return result


# ─── Summary ────────────────────────────────────────────────────────────────

def print_summary(phase1, phase2, phase3):
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)

    # Phase 1
    print("\n  PHASE 1: Z-Score Window Sweep (51-month runs)")
    print(f"  {'Mode':<15s} {'ZW':>4s} {'Total%':>9s} {'Sharpe':>7s} {'MaxDD':>7s} {'2022':>8s} {'2023':>8s} {'2024':>8s} {'2025':>8s} {'2026':>8s}")
    print(f"  {'-'*15} {'-'*4} {'-'*9} {'-'*7} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

    for mode in ["V2_BASE", "SHORT_HEAVY"]:
        for zw_key in sorted(phase1.get(mode, {}), key=lambda x: int(x)):
            data = phase1[mode][zw_key]
            tr = _to_float(data.get("total_return_pct"))
            sr = _to_float(data.get("sharpe_ratio"))
            dd = _to_float(data.get("max_drawdown_pct"))
            py = data.get("per_year", {})
            yr_vals = []
            for y in ["2022", "2023", "2024", "2025", "2026"]:
                v = py.get(y)
                yr_vals.append(f"{v:+.0f}%" if v is not None else "N/A")
            print(f"  {mode:<15s} {str(zw_key):>4s} {f'{tr:+.1f}%' if tr else 'N/A':>9s} "
                  f"{f'{sr:.2f}' if sr else 'N/A':>7s} {f'{dd:.1f}%' if dd else 'N/A':>7s} "
                  f"{yr_vals[0]:>8s} {yr_vals[1]:>8s} {yr_vals[2]:>8s} {yr_vals[3]:>8s} {yr_vals[4]:>8s}")
        if mode == "V2_BASE":
            print()

    # Phase 2
    if phase2:
        zw_info = phase2.get("_optimal_zw", {})
        print(f"\n  PHASE 2: Combined Regime Switch (bull_zw={zw_info.get('bull')}, bear_zw={zw_info.get('bear')})")
        print(f"  {'Config':<30s} {'Total%':>9s} {'Sharpe':>7s} {'MaxDD':>7s} {'2022':>8s} {'2023':>8s} {'2024':>8s} {'2025':>8s} {'2026':>8s} {'ALL+':>5s}")
        print(f"  {'-'*30} {'-'*9} {'-'*7} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*5}")

        for tkey in sorted(k for k in phase2 if not k.startswith("_")):
            data = phase2[tkey]
            tr = _to_float(data.get("total_return_pct"))
            sr = _to_float(data.get("sharpe_ratio"))
            dd = _to_float(data.get("max_drawdown_pct"))
            py = data.get("per_year", {})
            all_pos = True
            yr_vals = []
            for y in ["2022", "2023", "2024", "2025", "2026"]:
                v = py.get(y)
                if v is not None:
                    yr_vals.append(f"{v:+.0f}%")
                    if v < 0:
                        all_pos = False
                else:
                    yr_vals.append("N/A")
                    all_pos = False
            ap = "YES" if all_pos else "NO"
            print(f"  {tkey:<30s} {f'{tr:+.1f}%' if tr else 'N/A':>9s} "
                  f"{f'{sr:.2f}' if sr else 'N/A':>7s} {f'{dd:.1f}%' if dd else 'N/A':>7s} "
                  f"{yr_vals[0]:>8s} {yr_vals[1]:>8s} {yr_vals[2]:>8s} {yr_vals[3]:>8s} {yr_vals[4]:>8s} {ap:>5s}")

    # Phase 3
    if phase3 and phase3.get("static"):
        print(f"\n  PHASE 3: Blacklist Impact")
        best_key = phase3.get("_best_key", "?")
        sd = phase3["static"]
        # Compare with phase2 none-blacklist
        nd = phase2.get(best_key, {})
        print(f"  Config: {best_key}")
        print(f"  {'Blacklist':<12s} {'Total%':>9s} {'Sharpe':>7s} {'2022':>8s} {'2023':>8s} {'2024':>8s} {'2025':>8s} {'2026':>8s}")
        print(f"  {'-'*12} {'-'*9} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
        for bl, d in [("none", nd), ("static", sd)]:
            tr = _to_float(d.get("total_return_pct"))
            sr = _to_float(d.get("sharpe_ratio"))
            py = d.get("per_year", {})
            yr_vals = [f"{py.get(y, 0):+.0f}%" if py.get(y) is not None else "N/A"
                       for y in ["2022", "2023", "2024", "2025", "2026"]]
            print(f"  {bl:<12s} {f'{tr:+.1f}%' if tr else 'N/A':>9s} {f'{sr:.2f}' if sr else 'N/A':>7s} "
                  f"{yr_vals[0]:>8s} {yr_vals[1]:>8s} {yr_vals[2]:>8s} {yr_vals[3]:>8s} {yr_vals[4]:>8s}")

    # Best config
    print("\n" + "=" * 70)
    print("  SINGLE BEST CONFIG SPECIFICATION")
    print("=" * 70)
    best = find_best_overall(phase1, phase2, phase3)
    if best:
        for k, v in best.items():
            print(f"  {k}: {v}")


def find_best_overall(phase1, phase2, phase3):
    """Find best config: prefer all-years-positive, then highest total return."""
    candidates = []

    # Phase 1: pure V2_BASE and SHORT_HEAVY
    for mode in ["V2_BASE", "SHORT_HEAVY"]:
        for zw_key, data in phase1.get(mode, {}).items():
            tr = _to_float(data.get("total_return_pct"))
            py = data.get("per_year", {})
            all_pos = all(py.get(y, -1) > 0 for y in ["2022", "2023", "2024", "2025", "2026"])
            candidates.append({
                "config": f"{mode}_zw{zw_key}",
                "mode": mode,
                "total_return": tr,
                "all_years_positive": all_pos,
                "per_year": py,
                "sharpe": _to_float(data.get("sharpe_ratio")),
                "max_dd": _to_float(data.get("max_drawdown_pct")),
            })

    # Phase 2: combined
    if phase2:
        for tkey, data in phase2.items():
            if tkey.startswith("_"):
                continue
            tr = _to_float(data.get("total_return_pct"))
            py = data.get("per_year", {})
            all_pos = all(py.get(y, -1) > 0 for y in ["2022", "2023", "2024", "2025", "2026"])
            candidates.append({
                "config": f"COMBINED_{tkey}",
                "mode": "COMBINED",
                "total_return": tr,
                "all_years_positive": all_pos,
                "per_year": py,
                "sharpe": _to_float(data.get("sharpe_ratio")),
                "max_dd": _to_float(data.get("max_drawdown_pct")),
                "bull_zw": phase2.get("_optimal_zw", {}).get("bull"),
                "bear_zw": phase2.get("_optimal_zw", {}).get("bear"),
            })

    # Phase 3: static blacklist
    if phase3 and phase3.get("static"):
        sd = phase3["static"]
        tr = _to_float(sd.get("total_return_pct"))
        py = sd.get("per_year", {})
        all_pos = all(py.get(y, -1) > 0 for y in ["2022", "2023", "2024", "2025", "2026"])
        candidates.append({
            "config": f"COMBINED_{phase3.get('_best_key', '?')}_STATIC_BL",
            "mode": "COMBINED+STATIC",
            "total_return": tr,
            "all_years_positive": all_pos,
            "per_year": py,
            "sharpe": _to_float(sd.get("sharpe_ratio")),
            "max_dd": _to_float(sd.get("max_drawdown_pct")),
            "bull_zw": phase3.get("_bull_zw"),
            "bear_zw": phase3.get("_bear_zw"),
        })

    # Sort: all-years-positive first, then by total return
    candidates.sort(key=lambda x: (x.get("all_years_positive", False), x.get("total_return") or -9999), reverse=True)

    if candidates:
        return candidates[0]
    return None


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  s523 COMBINED PARAMETER SIMULATION (v2)")
    print(f"  Started: {datetime.now().isoformat()}")
    print("=" * 70)

    backup_strategy()

    # Check for resume
    all_results = {"started": datetime.now().isoformat()}
    if os.path.exists(RESULTS_PATH):
        try:
            with open(RESULTS_PATH) as f:
                existing = json.load(f)
            if "phase1" in existing and existing["phase1"]:
                # Check if it's the new format (has "V2_BASE" key)
                if "V2_BASE" in existing["phase1"]:
                    print("  [RESUME] Found existing v2 Phase 1 results")
                    all_results = existing
        except Exception:
            pass

    try:
        # Phase 1
        if "phase1" not in all_results or not all_results.get("phase1", {}).get("V2_BASE"):
            phase1 = run_phase1()
            all_results["phase1"] = phase1
            with open(RESULTS_PATH, "w") as f:
                json.dump(all_results, f, indent=2, default=str)
        else:
            phase1 = all_results["phase1"]
            print("  Phase 1 already complete, skipping")

        # Phase 2
        if "phase2" not in all_results or not all_results.get("phase2"):
            phase2 = run_phase2(phase1)
            all_results["phase2"] = phase2
            with open(RESULTS_PATH, "w") as f:
                json.dump(all_results, f, indent=2, default=str)
        else:
            phase2 = all_results["phase2"]
            print("  Phase 2 already complete, skipping")

        # Phase 3
        if "phase3" not in all_results or not all_results.get("phase3"):
            phase3 = run_phase3(phase2, phase1)
            all_results["phase3"] = phase3
        else:
            phase3 = all_results["phase3"]
            print("  Phase 3 already complete, skipping")

    except KeyboardInterrupt:
        print("\n  [INTERRUPTED]")
        phase1 = all_results.get("phase1", {})
        phase2 = all_results.get("phase2", {})
        phase3 = all_results.get("phase3", {})
    except Exception as e:
        print(f"\n  [ERROR] {e}")
        traceback.print_exc()
        phase1 = all_results.get("phase1", {})
        phase2 = all_results.get("phase2", {})
        phase3 = all_results.get("phase3", {})
    finally:
        restore_strategy()

    all_results["completed"] = datetime.now().isoformat()
    with open(RESULTS_PATH, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    print_summary(phase1, phase2, phase3)
    print(f"\n  Results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
