#!/usr/bin/env python3
"""
Bruteforce short gate optimization for s523k_dilution_filtered.
Tests 20 different short gate conditions across 4 year periods.
"""
import os
import sys
import json
import time
import shutil
import subprocess

STRAT_FILE = "/workspace/crypto_backtest/strategies/s523k_dilution_filtered.py"
BACKUP_FILE = STRAT_FILE + ".bak"
RESULTS_FILE = "/workspace/crypto_backtest/research/s523_shortgate_bruteforce_results.json"
METRICS_12 = "/workspace/crypto_backtest/results/v4/s523k_dilution_filtered_12mo_50k_metrics.json"
METRICS_15 = "/workspace/crypto_backtest/results/v4/s523k_dilution_filtered_15mo_50k_metrics.json"
PYTHON = "/workspace/venv/bin/python"

# Year configs: (label, months, end_date, metrics_file)
YEARS = [
    ("2022", 12, "2023-01-01", METRICS_12),
    ("2023", 12, "2024-01-01", METRICS_12),
    ("2024", 12, "2025-01-01", METRICS_12),
    ("2025", 15, "2026-04-05", METRICS_15),
]

# The line to find and replace
ORIGINAL_LINE = "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)"

# Gate definitions: (name, replacement_code)
# Some gates need extra lines BEFORE the _short_ok line
# Format: (name, pre_lines, short_ok_line)
GATES = [
    ("1mo_red (baseline)",
     "",
     "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)"),

    ("1mo < -5%",
     "",
     "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.05)"),

    ("1mo < -10%",
     "",
     "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < -0.10)"),

    ("3mo_red",
     "        _m3 = btc_aligned.resample('MS').last().pct_change().rolling(3).mean().reindex(ctx.idx_1h, method='ffill').values",
     "        _short_ok = (np.nan_to_num(_m3, nan=0) < 0)"),

    ("3mo < -5%",
     "        _m3 = btc_aligned.resample('MS').last().pct_change().rolling(3).mean().reindex(ctx.idx_1h, method='ffill').values",
     "        _short_ok = (np.nan_to_num(_m3, nan=0) < -0.05)"),

    ("below_w50",
     "",
     "        _short_ok = ~_above_w50"),

    ("below_sma200d",
     "        _sma200d = btc_aligned.rolling(200*24, min_periods=100*24).mean()",
     "        _short_ok = (btc_aligned < _sma200d).values"),

    ("1mo_red AND below_sma200d",
     "        _sma200d = btc_aligned.rolling(200*24, min_periods=100*24).mean()\n        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = _short_ok_1mo & (btc_aligned < _sma200d).values"),

    ("1mo_red AND 3mo_red",
     "        _m3 = btc_aligned.resample('MS').last().pct_change().rolling(3).mean().reindex(ctx.idx_1h, method='ffill').values\n        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = _short_ok_1mo & (np.nan_to_num(_m3, nan=0) < 0)"),

    ("1mo_red OR below_sma200d",
     "        _sma200d = btc_aligned.rolling(200*24, min_periods=100*24).mean()\n        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = _short_ok_1mo | (btc_aligned < _sma200d).values"),

    ("1mo_red OR 3mo_red",
     "        _m3 = btc_aligned.resample('MS').last().pct_change().rolling(3).mean().reindex(ctx.idx_1h, method='ffill').values\n        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = _short_ok_1mo | (np.nan_to_num(_m3, nan=0) < 0)"),

    ("trending_down",
     "",
     "        _short_ok = _is_trending & ~_above_w50"),

    ("NOT trending_up",
     "",
     "        _short_ok = ~(_is_trending & _above_w50)"),

    ("choppy OR 1mo_red",
     "        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = _is_choppy | _short_ok_1mo"),

    ("choppy OR below_sma200d",
     "        _sma200d = btc_aligned.rolling(200*24, min_periods=100*24).mean()",
     "        _short_ok = _is_choppy | (btc_aligned < _sma200d).values"),

    ("60d_ret < 0",
     "        _r60 = btc_aligned / btc_aligned.shift(60*24) - 1",
     "        _short_ok = (_r60 < 0).values"),

    ("60d_ret < -10%",
     "        _r60 = btc_aligned / btc_aligned.shift(60*24) - 1",
     "        _short_ok = (_r60 < -0.10).values"),

    ("1mo_red OR 60d < -10%",
     "        _r60 = btc_aligned / btc_aligned.shift(60*24) - 1\n        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = _short_ok_1mo | (_r60 < -0.10).values"),

    ("below_w100",
     "        _sma100w = btc_aligned.rolling(100*168, min_periods=50*168).mean()",
     "        _short_ok = (btc_aligned < _sma100w).values"),

    ("2of3 (1mo,sma200d,60d)",
     "        _sma200d = btc_aligned.rolling(200*24, min_periods=100*24).mean()\n        _r60 = btc_aligned / btc_aligned.shift(60*24) - 1\n        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)",
     "        _short_ok = ((_short_ok_1mo.astype(int) + (btc_aligned < _sma200d).values.astype(int) + (_r60 < 0).values.astype(int)) >= 2)"),
]


def patch_strategy(gate_name, pre_lines, short_ok_line):
    """Patch the strategy file with a new short gate."""
    with open(STRAT_FILE, 'r') as f:
        content = f.read()

    if pre_lines:
        replacement = pre_lines + "\n" + short_ok_line
    else:
        replacement = short_ok_line

    new_content = content.replace(ORIGINAL_LINE, replacement)
    if ORIGINAL_LINE not in content:
        print(f"  WARNING: Could not find original line to replace for {gate_name}")
        return False

    with open(STRAT_FILE, 'w') as f:
        f.write(new_content)
    return True


def restore_strategy():
    """Restore original strategy file."""
    shutil.copy2(BACKUP_FILE, STRAT_FILE)


def run_backtest(months, end_date):
    """Run backtest and return metrics dict."""
    # Clear BTC cache between runs to avoid stale state
    cmd = [
        PYTHON, "v4/portfolio_backtest.py",
        "--strategy", "s523k_dilution_filtered",
        "--months", str(months),
        "--capital", "50000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30",
        "--concentration", "0.30",
        "--skip-wf",
        "--end-date", end_date,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           cwd="/workspace/crypto_backtest")
    if result.returncode != 0:
        print(f"  BACKTEST FAILED: {result.stderr[-500:]}")
        return None

    metrics_file = f"/workspace/crypto_backtest/results/v4/s523k_dilution_filtered_{months}mo_50k_metrics.json"
    try:
        with open(metrics_file) as f:
            return json.load(f)
    except Exception as e:
        print(f"  Could not read metrics: {e}")
        return None


def clear_btc_cache():
    """Clear the BTC close cache between gate variants."""
    # We need to invalidate the module-level cache
    # Easiest: just delete any .pyc files and let it reload
    pass  # The subprocess creates a fresh Python process each time, so cache is clean


def main():
    # Backup original
    shutil.copy2(STRAT_FILE, BACKUP_FILE)
    print(f"Backed up strategy to {BACKUP_FILE}")

    results = {}
    total_runs = len(GATES) * len(YEARS)
    completed = 0
    start_time = time.time()

    try:
        for gate_idx, (gate_name, pre_lines, short_ok_line) in enumerate(GATES):
            gate_results = {}
            print(f"\n{'='*60}")
            print(f"Gate {gate_idx+1}/20: {gate_name}")
            print(f"{'='*60}")

            for year_label, months, end_date, metrics_file in YEARS:
                # Restore original first, then patch
                restore_strategy()

                if not patch_strategy(gate_name, pre_lines, short_ok_line):
                    gate_results[year_label] = {"ret": None, "error": "patch_failed"}
                    completed += 1
                    continue

                print(f"  Running {year_label} ({months}mo -> {end_date})...", end=" ", flush=True)
                t0 = time.time()
                metrics = run_backtest(months, end_date)
                elapsed = time.time() - t0
                completed += 1

                if metrics:
                    ret = float(metrics.get("total_return_pct", 0))
                    gate_results[year_label] = {
                        "ret": ret,
                        "sharpe": metrics.get("sharpe_ratio"),
                        "maxdd": metrics.get("max_drawdown_pct"),
                        "trades": metrics.get("total_trades"),
                        "calmar": metrics.get("calmar_ratio"),
                        "win_rate": metrics.get("win_rate_pct"),
                    }
                    print(f"{ret:+.1f}% ({elapsed:.1f}s) [{completed}/{total_runs}]")
                else:
                    gate_results[year_label] = {"ret": None, "error": "run_failed"}
                    print(f"FAILED ({elapsed:.1f}s) [{completed}/{total_runs}]")

            results[gate_name] = gate_results

            # Print running summary for this gate
            rets = [gate_results[y]["ret"] for y, _, _, _ in YEARS if gate_results.get(y, {}).get("ret") is not None]
            if rets:
                total = sum(rets)
                all_pos = all(r > 0 for r in rets)
                print(f"  -> SUM: {total:+.1f}%  AllPos: {'YES' if all_pos else 'NO'}")

    finally:
        # ALWAYS restore original
        restore_strategy()
        print(f"\nRestored original strategy file")
        # Clean up backup
        if os.path.exists(BACKUP_FILE):
            os.remove(BACKUP_FILE)

    # Save results
    with open(RESULTS_FILE, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {RESULTS_FILE}")

    # Print summary matrix
    elapsed_total = time.time() - start_time
    print(f"\nTotal time: {elapsed_total/60:.1f} minutes")
    print(f"\n{'='*90}")
    print(f"{'Gate':<30} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'SUM':>9} {'AllPos':>7}")
    print(f"{'='*90}")

    # Sort: all-positive first, then by SUM
    gate_summaries = []
    for gate_name, gate_results in results.items():
        rets = []
        for y, _, _, _ in YEARS:
            r = gate_results.get(y, {}).get("ret")
            rets.append(r)
        if all(r is not None for r in rets):
            total = sum(rets)
            all_pos = all(r > 0 for r in rets)
            gate_summaries.append((gate_name, rets, total, all_pos))
        else:
            gate_summaries.append((gate_name, rets, -9999, False))

    gate_summaries.sort(key=lambda x: (-int(x[3]), -x[2]))

    for gate_name, rets, total, all_pos in gate_summaries:
        ret_strs = []
        for r in rets:
            if r is not None:
                ret_strs.append(f"{r:+.0f}%")
            else:
                ret_strs.append("ERR")
        pos_str = "YES" if all_pos else "NO"
        if total > -9000:
            print(f"{gate_name:<30} {ret_strs[0]:>8} {ret_strs[1]:>8} {ret_strs[2]:>8} {ret_strs[3]:>8} {total:>+9.0f}% {pos_str:>7}")
        else:
            print(f"{gate_name:<30} {ret_strs[0]:>8} {ret_strs[1]:>8} {ret_strs[2]:>8} {ret_strs[3]:>8} {'ERR':>9} {'ERR':>7}")

    print(f"{'='*90}")

    # Highlight winners
    print("\n** WINNERS (all-positive, highest SUM): **")
    for gate_name, rets, total, all_pos in gate_summaries[:5]:
        if all_pos:
            print(f"  {gate_name}: {total:+.0f}% ({rets[0]:+.0f}/{rets[1]:+.0f}/{rets[2]:+.0f}/{rets[3]:+.0f})")


if __name__ == "__main__":
    main()
