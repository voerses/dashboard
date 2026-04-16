#!/usr/bin/env python3
"""
Brute-force short gate testing — Agent B (cross-sectional / market structure gates).
Tests 15 gates x 4 years = 60 engine runs.
"""
import subprocess
import json
import os
import re
import sys
import time

STRATEGY_FILE = "/workspace/crypto_backtest/strategies/s523k_bruteB.py"
ENGINE = "/workspace/venv/bin/python"
BACKTEST = "/workspace/crypto_backtest/v4/portfolio_backtest.py"
RESULTS_FILE = "/workspace/crypto_backtest/research/s523_shortgate_bruteforce_B_results.json"

# The original short_ok line to replace
ORIGINAL_LINE = "        _short_ok = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)"

# We also need the strategy name references
ORIGINAL_NAME = "s523k_dilution_filtered"
NEW_NAME = "s523k_bruteB"

# Year configs: (months, end_date)
YEARS = {
    "2022": (12, "2023-01-01T00:00:00"),
    "2023": (12, "2024-01-01T00:00:00"),
    "2024": (12, "2025-01-01T00:00:00"),
    "2025": (3, "2026-04-05T16:00:00"),  # partial year
}

# ETH loading code (inserted before the short_ok line)
ETH_LOAD_BLOCK = """
        # --- Agent B: Load ETH for cross-sectional gates ---
        _eth_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "perp", "binance", "1h_ohlcv", "ETH_perp_1h.csv",
        )
        if not hasattr(_get_composite_aligned, '_eth_close_cache'):
            _get_composite_aligned._eth_close_cache = None
        if _get_composite_aligned._eth_close_cache is None:
            _eth_df = pd.read_csv(_eth_path, parse_dates=["datetime"]).set_index("datetime").sort_index()
            if _eth_df.index.tz is not None:
                _eth_df.index = _eth_df.index.tz_convert(None)
            _get_composite_aligned._eth_close_cache = _eth_df["close"].astype(np.float64)
        _eth_aligned = _get_composite_aligned._eth_close_cache.reindex(ctx.idx_1h, method="ffill")
        # --- end ETH load ---
"""

# Gate definitions: name -> code that replaces the _short_ok line
# All assume btc_aligned and _eth_aligned are available, plus _m_ret_1mo_h
GATES = {
    "G01_btc_eth_ratio_30d_5pct": """
        _ratio = btc_aligned / _eth_aligned.replace(0, np.nan)
        _ratio_chg = (_ratio / _ratio.shift(30*24) - 1)
        _short_ok = (_ratio_chg > 0.05).values""",

    "G02_btc_eth_ratio_30d_10pct": """
        _ratio = btc_aligned / _eth_aligned.replace(0, np.nan)
        _ratio_chg = (_ratio / _ratio.shift(30*24) - 1)
        _short_ok = (_ratio_chg > 0.10).values""",

    "G03_btc_eth_ratio_30d_15pct": """
        _ratio = btc_aligned / _eth_aligned.replace(0, np.nan)
        _ratio_chg = (_ratio / _ratio.shift(30*24) - 1)
        _short_ok = (_ratio_chg > 0.15).values""",

    "G04_btc_eth_ratio_60d_10pct": """
        _ratio = btc_aligned / _eth_aligned.replace(0, np.nan)
        _ratio_chg60 = (_ratio / _ratio.shift(60*24) - 1)
        _short_ok = (_ratio_chg60 > 0.10).values""",

    "G05_1mo_red_OR_btceth_10pct": """
        _ratio = btc_aligned / _eth_aligned.replace(0, np.nan)
        _ratio_chg = (_ratio / _ratio.shift(30*24) - 1)
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _short_ok = _short_ok_1mo | (_ratio_chg > 0.10).values""",

    "G06_1mo_red_OR_btceth_5pct": """
        _ratio = btc_aligned / _eth_aligned.replace(0, np.nan)
        _ratio_chg = (_ratio / _ratio.shift(30*24) - 1)
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _short_ok = _short_ok_1mo | (_ratio_chg > 0.05).values""",

    "G07_eth_underperf_30d_5pct": """
        _eth_ret = _eth_aligned / _eth_aligned.shift(30*24) - 1
        _btc_ret = btc_aligned / btc_aligned.shift(30*24) - 1
        _short_ok = ((_eth_ret - _btc_ret) < -0.05).values""",

    "G08_eth_underperf_60d_10pct": """
        _eth_ret = _eth_aligned / _eth_aligned.shift(60*24) - 1
        _btc_ret = btc_aligned / btc_aligned.shift(60*24) - 1
        _short_ok = ((_eth_ret - _btc_ret) < -0.10).values""",

    "G09_1mo_red_OR_eth_underperf_30d": """
        _eth_ret = _eth_aligned / _eth_aligned.shift(30*24) - 1
        _btc_ret = btc_aligned / btc_aligned.shift(30*24) - 1
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _short_ok = _short_ok_1mo | ((_eth_ret - _btc_ret) < -0.05).values""",

    "G10_btc_90d_rvol_50pct": """
        _rv = np.log(btc_aligned/btc_aligned.shift(1)).rolling(90*24).std() * np.sqrt(8760)
        _short_ok = (_rv > 0.50).values""",

    "G11_1mo_red_OR_high_vol": """
        _rv = np.log(btc_aligned/btc_aligned.shift(1)).rolling(90*24).std() * np.sqrt(8760)
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _short_ok = _short_ok_1mo | (_rv > 0.50).values""",

    "G12_btc_dd_6m_15pct": """
        _peak6m = btc_aligned.rolling(180*24).max()
        _dd = (btc_aligned - _peak6m) / _peak6m
        _short_ok = (_dd < -0.15).values""",

    "G13_btc_dd_6m_20pct": """
        _peak6m = btc_aligned.rolling(180*24).max()
        _dd = (btc_aligned - _peak6m) / _peak6m
        _short_ok = (_dd < -0.20).values""",

    "G14_1mo_red_OR_dd_15pct": """
        _peak6m = btc_aligned.rolling(180*24).max()
        _dd = (btc_aligned - _peak6m) / _peak6m
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _short_ok = _short_ok_1mo | (_dd < -0.15).values""",

    "G15_1mo_red_AND_NOT_ath90": """
        _short_ok_1mo = (np.nan_to_num(_m_ret_1mo_h, nan=0) < 0)
        _ath90 = (btc_aligned >= btc_aligned.rolling(90*24).max() * 0.98).values
        _short_ok = _short_ok_1mo & ~_ath90""",
}


def read_base_strategy():
    """Read the original s523k_dilution_filtered.py as base template."""
    with open("/workspace/crypto_backtest/strategies/s523k_dilution_filtered.py") as f:
        return f.read()


def patch_strategy(base_code, gate_code):
    """Patch the strategy with ETH loading and new short gate."""
    # Replace strategy name
    code = base_code.replace(ORIGINAL_NAME, NEW_NAME)

    # Insert ETH loading block before the short_ok line
    code = code.replace(ORIGINAL_LINE, ETH_LOAD_BLOCK + gate_code)

    return code


def run_backtest(year, months, end_date):
    """Run backtest and return (return_pct, max_dd) or None on failure."""
    cmd = [
        ENGINE, BACKTEST,
        "--strategy", "s523k_bruteB",
        "--months", str(months),
        "--capital", "100000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30",
        "--skip-wf",
        "--end-date", end_date,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/workspace/crypto_backtest",
        )
        output = result.stdout + result.stderr

        # Parse return and max DD from output
        ret_match = re.search(r'Total Return:\s*([-+]?\d+\.?\d*)%', output)
        dd_match = re.search(r'Max Drawdown:\s*([-]?\d+\.?\d*)%', output)

        if ret_match:
            ret_pct = float(ret_match.group(1))
            dd_pct = float(dd_match.group(1)) if dd_match else None
            return ret_pct, dd_pct
        else:
            # Try to find any percentage in the last lines
            print(f"  WARNING: Could not parse output for {year}. Last 500 chars:")
            print(output[-500:])
            return None, None

    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT for {year}")
        return None, None
    except Exception as e:
        print(f"  ERROR for {year}: {e}")
        return None, None


def main():
    base_code = read_base_strategy()
    results = {}
    total_runs = len(GATES) * len(YEARS)
    completed = 0

    print(f"Starting {total_runs} backtest runs ({len(GATES)} gates x {len(YEARS)} years)")
    print("=" * 80)

    overall_start = time.time()

    for gate_name, gate_code in GATES.items():
        print(f"\n--- {gate_name} ---")
        gate_start = time.time()

        # Patch strategy file
        patched = patch_strategy(base_code, gate_code)
        with open(STRATEGY_FILE, 'w') as f:
            f.write(patched)

        # Clear any cached BTC/ETH data between gates (module-level caches)
        # The engine reimports each run, so caches reset naturally

        gate_results = {}
        for year, (months, end_date) in YEARS.items():
            ret_pct, dd_pct = run_backtest(year, months, end_date)
            gate_results[year] = {"return_pct": ret_pct, "max_dd": dd_pct}
            completed += 1

            status = f"{ret_pct:+.1f}%" if ret_pct is not None else "FAIL"
            elapsed = time.time() - overall_start
            eta = (elapsed / completed) * (total_runs - completed) if completed > 0 else 0
            print(f"  {year}: {status}  [{completed}/{total_runs}, ETA {eta/60:.0f}m]")

        gate_elapsed = time.time() - gate_start
        results[gate_name] = gate_results
        print(f"  Gate time: {gate_elapsed:.0f}s")

    # Save results
    with open(RESULTS_FILE, 'w') as f:
        json.dump(results, f, indent=2)

    total_elapsed = time.time() - overall_start
    print(f"\n{'=' * 80}")
    print(f"Total time: {total_elapsed/60:.1f} minutes")
    print(f"Results saved to {RESULTS_FILE}")

    # Print summary matrix
    print(f"\n{'=' * 80}")
    print("RESULTS MATRIX (sorted by SUM return)")
    print(f"{'=' * 80}")
    print(f"{'Gate':<38} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'SUM':>8} {'AllPos':>6}")
    print("-" * 80)

    # Compute summaries
    summaries = []
    for gate_name, gate_results in results.items():
        vals = []
        for year in ["2022", "2023", "2024", "2025"]:
            v = gate_results.get(year, {}).get("return_pct")
            vals.append(v if v is not None else 0)
        total = sum(vals)
        all_pos = all(v > 0 for v in vals)
        summaries.append((gate_name, vals, total, all_pos))

    # Sort: all-positive first, then by SUM descending
    summaries.sort(key=lambda x: (-x[3], -x[2]))

    for gate_name, vals, total, all_pos in summaries:
        flag = " *" if all_pos else ""
        print(f"{gate_name:<38} {vals[0]:>+7.1f}% {vals[1]:>+7.1f}% {vals[2]:>+7.1f}% {vals[3]:>+7.1f}% {total:>+7.1f}%{flag}")

    # Also print baseline reference
    print("-" * 80)
    print(f"{'BASELINE (1mo_red)':<38} {'+135.0':>8} {'+349.0':>8} {'+620.0':>8} {'+38.0':>8} {'+1142.0':>8}")


if __name__ == "__main__":
    main()
