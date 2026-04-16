#!/usr/bin/env python3
"""
Brute-force composite short-gate variants for s523k.
Tests 15 multi-condition / regime-adaptive gates across 2022-2025.

Approach: Uses a frozen snapshot of the source strategy (saved at /tmp/s523k_base_snapshot.py)
and replaces the short gate block between the marker comments for each variant.
"""
import os
import sys
import json
import shutil
import subprocess
import time
import re
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_PATH = "/tmp/s523k_base_snapshot.py"
STRATEGY_DST = os.path.join(BASE_DIR, "strategies", "s523k_bruteC.py")
RESULTS_DIR = os.path.join(BASE_DIR, "results", "v4")
PYTHON = "/workspace/venv/bin/python"
BACKTEST = os.path.join(BASE_DIR, "v4", "portfolio_backtest.py")

# Save snapshot if not already done
if not os.path.exists(SNAPSHOT_PATH):
    src = os.path.join(BASE_DIR, "strategies", "s523k_dilution_filtered.py")
    shutil.copy2(src, SNAPSHOT_PATH)
    print(f"Saved snapshot to {SNAPSHOT_PATH}")


# Gate definitions: each replaces the short gate assignment.
# Available variables: btc_aligned, _m_ret_1mo_h, _above_w50, _is_trending, _is_choppy, n, np, pd, ctx.idx_1h
GATES = {
    "G01_adaptive_sma200d": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _sma200d_g = btc_aligned.rolling(200*24, min_periods=100*24).mean()
        _bull = (btc_aligned > _sma200d_g).values
        _short_ok = np.where(_bull, _short_ok_1mo, True)""",

    "G02_adaptive_sma50w": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _short_ok = np.where(_above_w50, _short_ok_1mo, True)""",

    "G03_graduated_90d": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _r90 = (btc_aligned / btc_aligned.shift(90*24) - 1).values
        _strong_bull = np.nan_to_num(_r90, nan=0) > 0.20
        _weak = np.nan_to_num(_r90, nan=0) < 0
        _short_ok = np.where(_strong_bull, _short_ok_1mo & _is_choppy, np.where(_weak, True, _short_ok_1mo))""",

    "G04_momentum_decay": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _r30 = btc_aligned / btc_aligned.shift(30*24) - 1
        _r90g = btc_aligned / btc_aligned.shift(90*24) - 1
        _decel = (_r30 < _r90g).values
        _short_ok = _short_ok_1mo | _decel""",

    "G05_2month_confirm": """\
        _m2 = btc_aligned.resample('MS').last().pct_change().rolling(2).min().reindex(ctx.idx_1h, method='ffill').values
        _short_ok = (np.nan_to_num(_m2, nan=0) < 0)""",

    "G06_loose_bear_threshold": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _sma200d_g = btc_aligned.rolling(200*24, min_periods=100*24).mean()
        _bear = (btc_aligned < _sma200d_g).values
        _short_ok = np.where(_bear, np.nan_to_num(_m_ret_1mo_h, nan=0) < 0.03, _short_ok_1mo)""",

    "G07_peak_detect_10pct": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _peak = btc_aligned.rolling(90*24).max()
        _frompeakpct = ((btc_aligned - _peak) / _peak).values
        _short_ok = _short_ok_1mo | (_frompeakpct < -0.10)""",

    "G08_peak_detect_15pct": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _peak = btc_aligned.rolling(90*24).max()
        _frompeakpct = ((btc_aligned - _peak) / _peak).values
        _short_ok = _short_ok_1mo | (_frompeakpct < -0.15)""",

    "G09_peak_detect_20pct": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _peak = btc_aligned.rolling(90*24).max()
        _frompeakpct = ((btc_aligned - _peak) / _peak).values
        _short_ok = _short_ok_1mo | (_frompeakpct < -0.20)""",

    "G10_regime_persist_3mo": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _below200 = (btc_aligned < btc_aligned.rolling(200*24, min_periods=100*24).mean())
        _months_below = _below200.resample('MS').mean().rolling(3).sum().reindex(ctx.idx_1h, method='ffill').values
        _short_ok = _short_ok_1mo | (np.nan_to_num(_months_below, nan=0) >= 2.5)""",

    "G11_trend_break_6mo": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _sma200d_g = btc_aligned.rolling(200*24, min_periods=100*24).mean()
        _above_now = (btc_aligned > _sma200d_g).values
        _above_6mo = (btc_aligned.shift(180*24) > _sma200d_g.shift(180*24)).values
        _trend_break = _above_6mo & ~_above_now
        _short_ok = _short_ok_1mo | _trend_break""",

    "G12_bear_confirm_90d": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _sma200d_g = btc_aligned.rolling(200*24, min_periods=100*24).mean()
        _r90 = (btc_aligned / btc_aligned.shift(90*24) - 1).values
        _bear_conf = (np.nan_to_num(_r90, nan=0) < 0) & (btc_aligned < _sma200d_g).values
        _short_ok = _short_ok_1mo | _bear_conf""",

    "G13_vol_adaptive_threshold": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _rv = np.log(btc_aligned/btc_aligned.shift(1)).rolling(30*24).std().values * np.sqrt(8760)
        _high_vol = np.nan_to_num(_rv, nan=0.5) > 0.50
        _short_ok = np.where(_high_vol, np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.05, _short_ok_1mo)""",

    "G14_ath_drawdown_20pct": """\
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _peak_all = btc_aligned.expanding().max()
        _dd_from_ath = ((btc_aligned - _peak_all) / _peak_all).values
        _short_ok = _short_ok_1mo | (_dd_from_ath < -0.20)""",

    "G15_hysteresis_3mo": """\
        _red_raw = np.nan_to_num(_m_ret_1mo_h, nan=0) < 0
        _red_recent = pd.Series(_red_raw.astype(np.float64)).rolling(3*30*24, min_periods=1).max().values > 0
        _short_ok = _red_recent.astype(bool)""",
}


def create_patched_strategy(gate_code: str):
    """Create s523k_bruteC.py from frozen snapshot with the given gate code."""
    with open(SNAPSHOT_PATH, "r") as f:
        content = f.read()

    # Replace strategy name
    content = content.replace("s523k_dilution_filtered", "s523k_bruteC")

    # Use regex to replace the block between the comment and conviction boost.
    # Pattern: from "# 1mo_red short gate:" line through "        _short_ok = ..." line(s)
    # until the blank line before "# Conviction boost"
    pattern = r'(        # 1mo_red short gate:.*?\n)(.*?)((\n\n        # Conviction boost))'

    def replacer(m):
        return m.group(1) + gate_code + m.group(3)

    new_content, count = re.subn(pattern, replacer, content, flags=re.DOTALL)
    if count == 0:
        # Fallback: find lines between markers manually
        lines = content.split('\n')
        new_lines = []
        skip = False
        inserted = False
        for line in lines:
            if '# 1mo_red short gate:' in line:
                new_lines.append(line)
                new_lines.append(gate_code)
                skip = True
                inserted = True
                continue
            if skip and (line.strip() == '' or '# Conviction boost' in line):
                skip = False
            if skip:
                continue
            new_lines.append(line)
        if not inserted:
            raise ValueError("Could not find short gate marker in strategy file")
        new_content = '\n'.join(new_lines)

    with open(STRATEGY_DST, "w") as f:
        f.write(new_content)


def run_backtest():
    """Run 52-month backtest and return equity curve dict."""
    cmd = [
        PYTHON, BACKTEST,
        "--strategy", "s523k_bruteC",
        "--months", "52",
        "--capital", "100000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30",
        "--skip-wf",
        "--end-date", "2026-04-05T16:00:00",
    ]
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR, timeout=600)
    elapsed = time.time() - t0

    stdout = result.stdout + result.stderr

    # Load equity curve
    eq_path = os.path.join(RESULTS_DIR, "s523k_bruteC_52mo_100k_equity_curve.json")
    if not os.path.exists(eq_path):
        print(f"  ERROR: No equity curve at {eq_path}")
        print(f"  STDOUT: {stdout[-500:]}")
        return None, elapsed, stdout

    with open(eq_path) as f:
        eq = json.load(f)

    # Extract key metrics from stdout
    metrics = {}
    for line in stdout.split('\n'):
        if 'Max Drawdown:' in line:
            try:
                metrics['max_dd'] = float(line.split(':')[1].strip().replace('%', ''))
            except:
                pass
        if 'Sharpe:' in line and 'Sortino' not in line:
            try:
                metrics['sharpe'] = float(line.split(':')[1].strip())
            except:
                pass
        if 'Total Trades:' in line:
            try:
                metrics['trades'] = int(line.split(':')[1].strip())
            except:
                pass

    return eq, elapsed, metrics


def compute_yearly_returns(eq_dict):
    """Compute per-year returns from equity curve."""
    if not eq_dict:
        return {}
    eq = pd.Series(eq_dict, dtype=float).sort_index()
    eq.index = pd.to_datetime(eq.index)

    yearly = {}
    for year in [2022, 2023, 2024, 2025]:
        y_data = eq[eq.index.year == year]
        if len(y_data) < 2:
            yearly[year] = None
            continue
        ret = (y_data.iloc[-1] / y_data.iloc[0] - 1) * 100
        yearly[year] = round(ret, 1)
    return yearly


def main():
    results = {}
    total_gates = len(GATES)

    partial_path = os.path.join(BASE_DIR, "research", "s523_shortgate_bruteforce_C_results_partial.json")

    # Verify snapshot exists and has the marker
    with open(SNAPSHOT_PATH) as f:
        snap = f.read()
    if '# 1mo_red short gate:' not in snap:
        print("ERROR: Snapshot does not contain short gate marker. Cannot proceed.")
        sys.exit(1)
    print(f"Using snapshot from {SNAPSHOT_PATH}")

    for i, (gate_name, gate_code) in enumerate(GATES.items(), 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{total_gates}] Testing: {gate_name}")
        print(f"{'='*60}")

        # Create patched strategy from snapshot
        try:
            create_patched_strategy(gate_code)
        except ValueError as e:
            print(f"  PATCH ERROR: {e}")
            results[gate_name] = {"error": str(e)}
            continue

        # Quick sanity: verify _short_ok is set in the file
        with open(STRATEGY_DST) as f:
            dst_content = f.read()
        if '_short_ok' not in dst_content.split('# Conviction boost')[0].split('# 1mo_red')[1]:
            print(f"  WARNING: _short_ok may not be set correctly")

        # Run backtest
        try:
            result = run_backtest()
            eq, elapsed = result[0], result[1]
            extra = result[2] if len(result) > 2 else {}
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT after 600s")
            results[gate_name] = {"error": "timeout"}
            continue
        except Exception as e:
            print(f"  RUN ERROR: {e}")
            results[gate_name] = {"error": str(e)}
            continue

        if eq is None:
            results[gate_name] = {"error": f"no equity curve"}
            continue

        yearly = compute_yearly_returns(eq)

        # Compute total return
        eq_series = pd.Series(eq, dtype=float).sort_index()
        total_ret = round((eq_series.iloc[-1] / eq_series.iloc[0] - 1) * 100, 1)

        entry = {
            "yearly": yearly,
            "total_return": total_ret,
            "elapsed_s": round(elapsed, 1),
        }
        if isinstance(extra, dict):
            entry.update(extra)

        results[gate_name] = entry

        # Summary line
        y_str = " | ".join(f"{y}: {r:+.1f}%" if r is not None else f"{y}: N/A"
                           for y, r in yearly.items())
        all_positive = all(r is not None and r > 0 for r in yearly.values())
        marker = " ***" if all_positive else ""
        print(f"  {y_str} | Total: {total_ret:+.1f}%{marker}")

        # Save partial results after each gate
        with open(partial_path, "w") as f:
            json.dump(results, f, indent=2)

    # ===== Final output =====
    print(f"\n\n{'='*80}")
    print("COMPOSITE SHORT GATE BRUTE FORCE RESULTS (s523k_bruteC)")
    print(f"{'='*80}")
    print(f"Baseline (s523k current): 2022 +143%, 2023 +206%, 2024 +399%, 2025 -65%")
    print()

    # Header
    print(f"{'Gate':<30} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'SUM':>8} {'All+':>5}")
    print("-" * 80)

    # Sort by: all-positive first, then by SUM
    def sort_key(item):
        name, data = item
        if "error" in data:
            return (0, -9999)
        yearly = data.get("yearly", {})
        vals = [v for v in yearly.values() if v is not None]
        all_pos = all(v > 0 for v in vals) if vals else False
        s = sum(vals)
        return (1 if all_pos else 0, s)

    for gate_name, data in sorted(results.items(), key=sort_key, reverse=True):
        if "error" in data:
            print(f"{gate_name:<30} ERROR: {data['error']}")
            continue
        yearly = data["yearly"]
        vals = [yearly.get(y) for y in [2022, 2023, 2024, 2025]]
        all_pos = all(v is not None and v > 0 for v in vals)
        s = sum(v for v in vals if v is not None)
        row = f"{gate_name:<30}"
        for v in vals:
            row += f" {v:+8.1f}%" if v is not None else f" {'N/A':>7} "
        row += f" {s:+8.1f}%"
        row += f" {'YES' if all_pos else 'no':>5}"
        if isinstance(data.get('max_dd'), (int, float)):
            row += f"  DD:{data['max_dd']:.0f}%"
        if isinstance(data.get('trades'), int):
            row += f"  T:{data['trades']}"
        print(row)

    # Save final JSON
    output_path = os.path.join(BASE_DIR, "research", "s523_shortgate_bruteforce_C_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    # Clean up partial
    if os.path.exists(partial_path):
        os.remove(partial_path)


if __name__ == "__main__":
    main()
