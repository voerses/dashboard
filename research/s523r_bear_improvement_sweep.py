"""
s523r bear market improvement sweep.
Tests variants to improve 2022 (+27%) and 2026Q1 (+36%) without hurting 2024.

Variants:
A1. Tighter dilution: no longs >40% remaining (bear only)
A2. Tighter dilution: no longs >30% remaining (bear only)
A3. Short-only in bear years (no longs at all)
B1. zw=30 in bear years (test 2022 and 2026 separately with zw=30)
C1. Static blacklist in bear years only

Each variant is tested by patching s523r_year_split.py, running the engine,
and restoring the original.
"""

import subprocess
import json
import os
import re
import sys
import shutil
import time

STRAT_FILE = "/workspace/crypto_backtest/strategies/s523r_year_split.py"
BACKUP_FILE = "/workspace/crypto_backtest/strategies/s523r_year_split.py.bak"
PYTHON = "/workspace/venv/bin/python"
ENGINE = "v4/portfolio_backtest.py"
CWD = "/workspace/crypto_backtest"

BASE_ARGS = [
    PYTHON, ENGINE,
    "--strategy", "s523r_year_split",
    "--capital", "50000",
    "--market", "perp",
    "--conviction-mode", "ranked",
    "--max-portfolio-positions", "40",
    "--concentration", "0.30",
    "--skip-wf",
]

# Test periods
PERIODS = {
    "2022": {"months": "12", "end_date": "2023-01-01"},
    "2026Q1": {"months": "3", "end_date": "2026-04-05"},
    "2024": {"months": "12", "end_date": "2025-01-01"},
}


def backup():
    shutil.copy2(STRAT_FILE, BACKUP_FILE)


def restore():
    shutil.copy2(BACKUP_FILE, STRAT_FILE)


def run_backtest(period_name: str) -> dict:
    """Run engine for a period, return parsed results."""
    p = PERIODS[period_name]
    args = BASE_ARGS + ["--months", p["months"], "--end-date", p["end_date"]]

    print(f"  Running {period_name}...", flush=True)
    t0 = time.time()
    result = subprocess.run(args, capture_output=True, text=True, cwd=CWD, timeout=600)
    elapsed = time.time() - t0
    print(f"  {period_name} done in {elapsed:.0f}s", flush=True)

    output = result.stdout + "\n" + result.stderr

    # Parse total return from output
    ret = parse_return(output)
    dd = parse_max_dd(output)
    trades = parse_trades(output)

    return {"return_pct": ret, "max_dd_pct": dd, "trades": trades, "raw_last_lines": output[-2000:]}


def parse_return(output: str) -> float:
    """Parse total return % from engine output."""
    # Look for "Total Return:" or "Return:" patterns
    patterns = [
        r'Total Return:\s*([-+]?[\d,.]+)%',
        r'Return:\s*([-+]?[\d,.]+)%',
        r'total_return_pct["\s:=]+([-+]?[\d,.]+)',
        r'TOTAL RETURN:\s*([-+]?[\d,.]+)%',
        r'Return\s*:\s*([-+]?[\d,.]+)\s*%',
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", ""))
    # Try to find it in JSON-like output
    m = re.search(r'"total_return":\s*([-+]?[\d.]+)', output)
    if m:
        return float(m.group(1)) * 100  # convert decimal to pct
    return float('nan')


def parse_max_dd(output: str) -> float:
    """Parse max drawdown from engine output."""
    patterns = [
        r'Max Drawdown:\s*([-+]?[\d,.]+)%',
        r'max_drawdown["\s:=]+([-+]?[\d,.]+)',
        r'Max DD:\s*([-+]?[\d,.]+)%',
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", ""))
    return float('nan')


def parse_trades(output: str) -> int:
    """Parse total trades from engine output."""
    patterns = [
        r'Total Trades:\s*(\d+)',
        r'Trades:\s*(\d+)',
        r'"total_trades":\s*(\d+)',
        r'(\d+)\s+trades',
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return -1


def patch_file(content: str, variant_name: str) -> str:
    """Apply a variant patch to the strategy source."""
    return content


def apply_variant(variant_name: str):
    """Modify the strategy file for a specific variant."""
    with open(STRAT_FILE, 'r') as f:
        content = f.read()

    if variant_name == "baseline":
        return  # no changes needed

    elif variant_name == "A1_tighter_dilution_40":
        # Change dilution: no longs >40% remaining in bear years, no trades >60% remaining
        # Original: if _rem>75:_doa[:]=False / elif _rem>50:_dol[:]=False
        # New: bear years tighten to >60 / >40
        # We add bear-year-specific dilution AFTER the existing dilution block
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"
        new = """    # A1: tighter dilution in bear years — no longs >40% remaining
    if _rem > 40 and _rem <= 50:
        # In bear years, block longs for 40-50% remaining tokens
        _dol_bear = ~_bear_years
        _dol = _dol & (_dol_bear | np.ones(n, dtype=bool))
        # Actually: just override _dol in bear year bars
        _bear_mask = _bear_years
        if _rem > 40:
            _dol[_bear_mask] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"""
        content = content.replace(old, new)

    elif variant_name == "A2_tighter_dilution_30":
        # No longs if remaining > 30% in bear years
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"
        new = """    # A2: tighter dilution in bear years — no longs >30% remaining
    if _rem > 30:
        _bear_mask = _bear_years
        _dol[_bear_mask] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"""
        content = content.replace(old, new)

    elif variant_name == "A3_short_only_bear":
        # No longs at all in bear years
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"
        new = """    # A3: short-only in bear years — no longs at all
    long_signal[_bear_years] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"""
        content = content.replace(old, new)

    elif variant_name == "B1_zw30":
        # Change ZSCORE_WINDOW_DAYS from 22 to 30
        content = content.replace(
            "ZSCORE_WINDOW_DAYS = 22",
            "ZSCORE_WINDOW_DAYS = 30"
        )

    elif variant_name == "C1_bear_blacklist":
        # Apply legacy blacklist only in bear years
        # Replace TOKEN_BLACKLIST = set() with the legacy set
        # And add bear-year gating
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa\n    short_signal=short_signal&day_change&rsi_short_window&_sof&_doa"
        new = """    # C1: apply legacy blacklist in bear years only
    _bl_tokens = {
        "EIGEN", "BAN", "CETUS", "ONT", "BANANA", "DOT",
        "STRK", "SOL", "PIPPIN", "DEGO", "SUI", "NEIRO",
        "ZEN", "MINA", "STEEM", "ANKR", "AVAX", "BCH",
        "BTC", "CRV", "DASH", "DUSK", "ENA", "ETC",
        "FET", "FIL", "G", "GRASS", "INJ", "JUP",
        "KAS", "KAVA", "NEO", "OGN", "POLYX", "RENDER",
        "RVN", "SAND", "SIREN", "TAO", "UNI", "WLD",
        "LTC", "LINK", "AAVE", "XRP", "ADA", "TRX",
        "DOGE", "SHIB",
    }
    if ticker in _bl_tokens:
        long_signal[_bear_years] = False
        short_signal[_bear_years] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa
    short_signal=short_signal&day_change&rsi_short_window&_sof&_doa"""
        content = content.replace(old, new)

    elif variant_name == "A1A3_short_only_bear_tight_dilution":
        # Combo: short-only in bear + tighter overall dilution
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"
        new = """    # Combo: short-only in bear years
    long_signal[_bear_years] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"""
        content = content.replace(old, new)

    elif variant_name == "A3_C1_short_only_bear_blacklist":
        # Combo: short-only bear + blacklist in bear
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa\n    short_signal=short_signal&day_change&rsi_short_window&_sof&_doa"
        new = """    # Combo: short-only + blacklist in bear years
    _bl_tokens = {
        "EIGEN", "BAN", "CETUS", "ONT", "BANANA", "DOT",
        "STRK", "SOL", "PIPPIN", "DEGO", "SUI", "NEIRO",
        "ZEN", "MINA", "STEEM", "ANKR", "AVAX", "BCH",
        "BTC", "CRV", "DASH", "DUSK", "ENA", "ETC",
        "FET", "FIL", "G", "GRASS", "INJ", "JUP",
        "KAS", "KAVA", "NEO", "OGN", "POLYX", "RENDER",
        "RVN", "SAND", "SIREN", "TAO", "UNI", "WLD",
        "LTC", "LINK", "AAVE", "XRP", "ADA", "TRX",
        "DOGE", "SHIB",
    }
    long_signal[_bear_years] = False
    if ticker in _bl_tokens:
        short_signal[_bear_years] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa
    short_signal=short_signal&day_change&rsi_short_window&_sof&_doa"""
        content = content.replace(old, new)

    elif variant_name == "B1_A3_zw30_short_only_bear":
        # zw=30 + short-only in bear
        content = content.replace(
            "ZSCORE_WINDOW_DAYS = 22",
            "ZSCORE_WINDOW_DAYS = 30"
        )
        old = "    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"
        new = """    # Combo: zw=30 + short-only in bear years
    long_signal[_bear_years] = False
    long_signal=long_signal&day_change&rsi_long_window&_dol&_doa"""
        content = content.replace(old, new)

    else:
        raise ValueError(f"Unknown variant: {variant_name}")

    with open(STRAT_FILE, 'w') as f:
        f.write(content)


def main():
    results = {}

    # Define variants to test
    variants = [
        "baseline",
        "A3_short_only_bear",
        "A1_tighter_dilution_40",
        "A2_tighter_dilution_30",
        "B1_zw30",
        "C1_bear_blacklist",
        "A3_C1_short_only_bear_blacklist",
        "B1_A3_zw30_short_only_bear",
    ]

    # Periods to test (2024 sanity check last since it's slowest)
    test_periods = ["2022", "2026Q1", "2024"]

    backup()

    try:
        for variant in variants:
            print(f"\n{'='*60}")
            print(f"VARIANT: {variant}")
            print(f"{'='*60}", flush=True)

            results[variant] = {}

            for period in test_periods:
                # Restore and re-apply variant for each period
                # (engine may cache module state)
                restore()
                apply_variant(variant)

                try:
                    r = run_backtest(period)
                    results[variant][period] = r
                    ret_str = f"{r['return_pct']:+.1f}%" if not (r['return_pct'] != r['return_pct']) else "PARSE_ERR"
                    print(f"  {period}: {ret_str} (DD: {r['max_dd_pct']:.1f}%, trades: {r['trades']})", flush=True)
                except Exception as e:
                    print(f"  {period}: ERROR - {e}", flush=True)
                    results[variant][period] = {"return_pct": float('nan'), "error": str(e)}

            # Skip 2024 sanity check for obviously bad variants
            # (we always run all three)

    finally:
        restore()
        if os.path.exists(BACKUP_FILE):
            os.remove(BACKUP_FILE)

    # Print summary table
    print(f"\n\n{'='*80}")
    print("RESULTS SUMMARY")
    print(f"{'='*80}")
    print(f"{'Variant':<35} {'2022':>10} {'2026Q1':>10} {'2024':>10}")
    print(f"{'-'*35} {'-'*10} {'-'*10} {'-'*10}")

    for variant in variants:
        vals = []
        for period in test_periods:
            r = results[variant].get(period, {})
            ret = r.get("return_pct", float('nan'))
            if ret != ret:  # NaN check
                vals.append("ERR")
            else:
                vals.append(f"{ret:+.1f}%")
        print(f"{variant:<35} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10}")

    # Compute improvement scores
    print(f"\n{'='*80}")
    print("IMPROVEMENT vs BASELINE")
    print(f"{'='*80}")

    baseline = results.get("baseline", {})
    b_2022 = baseline.get("2022", {}).get("return_pct", 0)
    b_2026 = baseline.get("2026Q1", {}).get("return_pct", 0)
    b_2024 = baseline.get("2024", {}).get("return_pct", 0)

    scored = []
    for variant in variants:
        if variant == "baseline":
            continue
        r = results[variant]
        v_2022 = r.get("2022", {}).get("return_pct", float('nan'))
        v_2026 = r.get("2026Q1", {}).get("return_pct", float('nan'))
        v_2024 = r.get("2024", {}).get("return_pct", float('nan'))

        if any(x != x for x in [v_2022, v_2026, v_2024]):
            continue

        # Improvement in bear years
        bear_improve = (v_2022 - b_2022) + (v_2026 - b_2026)
        # Penalty for hurting 2024
        bull_hurt = max(0, b_2024 - v_2024)
        score = bear_improve - bull_hurt

        scored.append({
            "variant": variant,
            "bear_improve": bear_improve,
            "bull_hurt": bull_hurt,
            "score": score,
            "2022": v_2022,
            "2026Q1": v_2026,
            "2024": v_2024,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'Variant':<35} {'Bear Δ':>10} {'Bull hurt':>10} {'Score':>10}")
    print(f"{'-'*35} {'-'*10} {'-'*10} {'-'*10}")
    for s in scored:
        print(f"{s['variant']:<35} {s['bear_improve']:>+10.1f}pp {s['bull_hurt']:>10.1f}pp {s['score']:>+10.1f}")

    # Save results
    output_path = "/workspace/crypto_backtest/research/s523_2022_2026_improvement.json"

    # Clean results for JSON (remove raw output to save space)
    clean_results = {}
    for variant in variants:
        clean_results[variant] = {}
        for period in test_periods:
            r = results[variant].get(period, {})
            clean_results[variant][period] = {
                "return_pct": r.get("return_pct"),
                "max_dd_pct": r.get("max_dd_pct"),
                "trades": r.get("trades"),
            }

    output = {
        "description": "s523r bear market improvement sweep",
        "baseline_target": {"2022": "+27%", "2026Q1": "+36%", "s523c_reference": {"2022": "+3%", "2026Q1": "+179%"}},
        "results": clean_results,
        "ranked": scored,
        "best_variant": scored[0]["variant"] if scored else None,
    }

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
