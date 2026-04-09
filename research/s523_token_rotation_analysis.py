"""
s523 Token Rotation Analysis
=============================
Analyzes static IC signs vs empirical per-year IC for s523c_growth tokens.
Tests dynamic token selection / sign recalibration approaches.
"""

import json
import os
import sys
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

BASE = Path("/workspace/crypto_backtest")
CONFIG_PATH = BASE / "data/alternative/s521_token_config.json"
METRICS_DIR = BASE / "data/alternative/binance_metrics/5min"
OHLCV_DIR = BASE / "data/perp/binance/1h_ohlcv"

# Token name mapping for 1h OHLCV files (some have 1000 prefix)
OHLCV_PREFIX_MAP = {
    "BONK": "1000BONK",
    "FLOKI": "1000FLOKI",
    "PEPE": "1000PEPE",
    "SHIB": "1000SHIB",
    "SATS": "1000SATS",
}

YEARS = [2022, 2023, 2024, 2025]
ZW = 22  # rolling z-score window (matches s523i v2)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_ohlcv_path(symbol):
    mapped = OHLCV_PREFIX_MAP.get(symbol, symbol)
    return OHLCV_DIR / f"{mapped}_perp_1h.csv"


def get_metrics_path(symbol):
    return METRICS_DIR / f"{symbol}USDT_5min.parquet"


def load_daily_returns(symbol):
    """Load 1h OHLCV, compute daily returns."""
    path = get_ohlcv_path(symbol)
    if not path.exists():
        return None
    df = pd.read_csv(path, usecols=["datetime", "close"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    # Resample to daily close
    daily = df["close"].resample("1D").last().dropna()
    daily_ret = daily.pct_change().shift(-1)  # forward 1-day return
    daily_ret.name = "fwd_ret"
    return daily_ret


def load_daily_metrics(symbol):
    """Load 5min positioning data, resample to daily, compute z-scores."""
    path = get_metrics_path(symbol)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["create_time"] = pd.to_datetime(df["create_time"], utc=True)
    df = df.set_index("create_time").sort_index()

    # Resample to daily (last value)
    daily = df.resample("1D").last().dropna(how="all")

    result = pd.DataFrame(index=daily.index)

    # OI z-score
    if "sum_open_interest_value" in daily.columns:
        oi = daily["sum_open_interest_value"]
        result["oi_z"] = (oi - oi.rolling(ZW).mean()) / oi.rolling(ZW).std()

    # Position z-score (toptrader L/S ratio)
    if "sum_toptrader_long_short_ratio" in daily.columns:
        pos = daily["sum_toptrader_long_short_ratio"]
        result["pos_z"] = (pos - pos.rolling(ZW).mean()) / pos.rolling(ZW).std()

    # Flow z-score (taker L/S vol ratio)
    if "sum_taker_long_short_vol_ratio" in daily.columns:
        flow = daily["sum_taker_long_short_vol_ratio"]
        result["flow_z"] = (flow - flow.rolling(ZW).mean()) / flow.rolling(ZW).std()

    return result.dropna(how="all")


def compute_ic_per_year(metrics, returns):
    """Compute Spearman IC between each metric z-score and forward return, per year."""
    merged = metrics.join(returns, how="inner")
    if len(merged) < 30:
        return None

    results = {}
    for year in YEARS:
        mask = merged.index.year == year
        yr_data = merged[mask]
        if len(yr_data) < 10:
            continue
        yr_results = {}
        for col in ["oi_z", "pos_z", "flow_z"]:
            if col not in yr_data.columns:
                continue
            valid = yr_data[[col, "fwd_ret"]].dropna()
            if len(valid) < 20:
                continue
            ic, _ = stats.spearmanr(valid[col], valid["fwd_ret"])
            yr_results[col] = ic
        if yr_results:
            results[year] = yr_results
    return results


# ============================================================
# STEP 1: Config analysis
# ============================================================
def step1_config_analysis():
    config = load_config()
    n_tokens = len(config)

    # Sign distributions
    all_positive = 0  # all signs +1
    all_negative = 0  # all signs -1
    mixed = 0
    directions = defaultdict(int)

    weights = {"oi": [], "pos": [], "flow": []}
    best_ics = []

    for sym, cfg in config.items():
        signs = [cfg.get("oi_sign", 0), cfg.get("pos_sign", 0), cfg.get("flow_sign", 0)]
        active_signs = [s for s in signs if s != 0]
        if all(s == 1 for s in active_signs):
            all_positive += 1
        elif all(s == -1 for s in active_signs):
            all_negative += 1
        else:
            mixed += 1

        directions[cfg.get("direction", "unknown")] += 1
        weights["oi"].append(cfg.get("oi_weight", 0))
        weights["pos"].append(cfg.get("pos_weight", 0))
        weights["flow"].append(cfg.get("flow_weight", 0))
        best_ics.append((sym, cfg.get("best_ic", 0)))

    # Sort by best_ic descending
    best_ics.sort(key=lambda x: -x[1])

    print("=" * 70)
    print("STEP 1: CURRENT TOKEN CONFIG ANALYSIS")
    print("=" * 70)
    print(f"\nTotal tokens: {n_tokens}")
    print(f"\nSign distributions:")
    print(f"  All positive (+1): {all_positive}")
    print(f"  All negative (-1): {all_negative}")
    print(f"  Mixed signs:       {mixed}")
    print(f"\nDirection counts:")
    for d, c in sorted(directions.items(), key=lambda x: -x[1]):
        print(f"  {d}: {c}")

    print(f"\nWeight distributions:")
    for w_name, w_vals in weights.items():
        arr = np.array(w_vals)
        non_zero = arr[arr > 0]
        print(f"  {w_name}: mean={non_zero.mean():.3f}, min={non_zero.min():.3f}, max={non_zero.max():.3f}")

    print(f"\nTop 10 tokens by best_ic (strongest configured signal):")
    for sym, ic in best_ics[:10]:
        print(f"  {sym}: {ic:.4f}")

    print(f"\nBottom 10 tokens by best_ic:")
    for sym, ic in best_ics[-10:]:
        print(f"  {sym}: {ic:.4f}")

    return config


# ============================================================
# STEP 2: Empirical IC per year
# ============================================================
def step2_empirical_ic(config):
    print("\n" + "=" * 70)
    print("STEP 2: EMPIRICAL PER-TOKEN IC PER YEAR")
    print("=" * 70)

    tokens = list(config.keys())
    all_ic_data = {}
    sign_mismatches = {y: 0 for y in YEARS}
    sign_total = {y: 0 for y in YEARS}
    token_ic_by_year = {y: {} for y in YEARS}

    processed = 0
    for sym in tokens:
        returns = load_daily_returns(sym)
        if returns is None:
            continue
        metrics = load_daily_metrics(sym)
        if metrics is None:
            continue

        ics = compute_ic_per_year(metrics, returns)
        if ics is None:
            continue

        all_ic_data[sym] = ics
        processed += 1

        cfg = config[sym]
        sign_map = {"oi_z": "oi_sign", "pos_z": "pos_sign", "flow_z": "flow_sign"}

        for year, yr_ics in ics.items():
            for metric, ic_val in yr_ics.items():
                cfg_sign = cfg.get(sign_map[metric], 0)
                if cfg_sign == 0:
                    continue
                empirical_sign = 1 if ic_val > 0 else -1
                sign_total[year] = sign_total.get(year, 0) + 1
                if empirical_sign != cfg_sign:
                    sign_mismatches[year] = sign_mismatches.get(year, 0) + 1

            # Store per-token composite IC for rotation strategies
            # Composite = weighted sum of signed ICs
            composite_ic = 0
            for metric in ["oi_z", "pos_z", "flow_z"]:
                if metric in yr_ics:
                    w_key = metric.replace("_z", "_weight")
                    w = cfg.get(w_key, 0.33)
                    composite_ic += w * yr_ics[metric]
            token_ic_by_year[year][sym] = composite_ic

    print(f"\nProcessed {processed}/{len(tokens)} tokens with data")

    print(f"\nSign mismatches per year (config sign != empirical IC sign):")
    for year in YEARS:
        total = sign_total.get(year, 0)
        mismatches = sign_mismatches.get(year, 0)
        pct = (mismatches / total * 100) if total > 0 else 0
        print(f"  {year}: {mismatches}/{total} mismatches ({pct:.1f}%)")

    # Show per-year median IC magnitude
    print(f"\nMedian |IC| per year across all token-metric pairs:")
    for year in YEARS:
        all_ics_yr = []
        for sym, ics in all_ic_data.items():
            if year in ics:
                all_ics_yr.extend(ics[year].values())
        if all_ics_yr:
            arr = np.abs(np.array(all_ics_yr))
            print(f"  {year}: median={np.median(arr):.4f}, mean={np.mean(arr):.4f}, "
                  f"n_pairs={len(all_ics_yr)}")

    # Tokens with MOST year-over-year sign flips
    flip_counts = defaultdict(int)
    for sym, ics in all_ic_data.items():
        cfg = config[sym]
        sign_map = {"oi_z": "oi_sign", "pos_z": "pos_sign", "flow_z": "flow_sign"}
        for metric in ["oi_z", "pos_z", "flow_z"]:
            prev_sign = None
            for year in YEARS:
                if year in ics and metric in ics[year]:
                    curr_sign = 1 if ics[year][metric] > 0 else -1
                    if prev_sign is not None and curr_sign != prev_sign:
                        flip_counts[sym] += 1
                    prev_sign = curr_sign

    top_flippers = sorted(flip_counts.items(), key=lambda x: -x[1])[:15]
    print(f"\nTop 15 tokens by IC sign flips across years (unstable):")
    for sym, flips in top_flippers:
        print(f"  {sym}: {flips} flips")

    # Tokens with STABLE signs (0 flips)
    stable = [sym for sym, flips in flip_counts.items() if flips == 0]
    print(f"\nTokens with perfectly stable IC signs (0 flips): {len(stable)}")
    if stable:
        print(f"  {', '.join(stable[:20])}")

    return all_ic_data, token_ic_by_year, flip_counts


# ============================================================
# STEP 3: Simulate rotation approaches (IC analysis only, no engine)
# ============================================================
def step3_rotation_analysis(config, all_ic_data, token_ic_by_year, flip_counts):
    print("\n" + "=" * 70)
    print("STEP 3: TOKEN ROTATION APPROACH ANALYSIS")
    print("=" * 70)

    # Rather than running full engine backtests (expensive), we compute
    # the expected signal quality under each approach by measuring
    # how well the recalibrated signs align with realized ICs.

    # ===== A. Yearly IC recalibration =====
    print("\n--- A. Yearly IC Recalibration (use prior year's IC to set signs) ---")
    # For year Y, use year Y-1's empirical IC to set signs
    # Measure: what % of signs are correct in year Y?
    for year in YEARS:
        prev_year = year - 1
        correct = 0
        total = 0
        for sym, ics in all_ic_data.items():
            if prev_year not in ics or year not in ics:
                continue
            for metric in ["oi_z", "pos_z", "flow_z"]:
                if metric not in ics.get(prev_year, {}) or metric not in ics.get(year, {}):
                    continue
                prev_sign = 1 if ics[prev_year][metric] > 0 else -1
                curr_sign = 1 if ics[year][metric] > 0 else -1
                total += 1
                if prev_sign == curr_sign:
                    correct += 1
        pct = (correct / total * 100) if total > 0 else 0
        print(f"  {year}: {correct}/{total} signs predicted correctly from {prev_year} ({pct:.1f}%)")

    # Compare to static config accuracy
    print("\n  vs. Static config accuracy:")
    for year in YEARS:
        correct = 0
        total = 0
        sign_map = {"oi_z": "oi_sign", "pos_z": "pos_sign", "flow_z": "flow_sign"}
        for sym, ics in all_ic_data.items():
            if year not in ics:
                continue
            cfg = config[sym]
            for metric in ["oi_z", "pos_z", "flow_z"]:
                if metric not in ics[year]:
                    continue
                cfg_sign = cfg.get(sign_map[metric], 0)
                if cfg_sign == 0:
                    continue
                empirical_sign = 1 if ics[year][metric] > 0 else -1
                total += 1
                if cfg_sign == empirical_sign:
                    correct += 1
        pct = (correct / total * 100) if total > 0 else 0
        print(f"  {year}: {correct}/{total} static signs correct ({pct:.1f}%)")

    # ===== B. Top-N selection by trailing IC magnitude =====
    print("\n--- B. Top-N Selection by Trailing IC Magnitude ---")
    for N in [20, 30, 40, 50]:
        print(f"\n  Top-{N} tokens:")
        for year in YEARS:
            prev_year = year - 1
            # Get tokens with IC data for prev year, rank by |composite_ic|
            prev_ics = token_ic_by_year.get(prev_year, {})
            if not prev_ics:
                print(f"    {year}: no prior year data")
                continue
            ranked = sorted(prev_ics.items(), key=lambda x: -abs(x[1]))[:N]
            selected = set(t[0] for t in ranked)

            # Measure: what's the average |IC| of selected tokens in the current year?
            curr_ics = token_ic_by_year.get(year, {})
            selected_curr_ics = [curr_ics[s] for s in selected if s in curr_ics]
            if selected_curr_ics:
                arr = np.array(selected_curr_ics)
                mean_ic = np.mean(arr)
                median_ic = np.median(arr)
                pct_positive = np.mean(arr > 0) * 100
                print(f"    {year}: mean_ic={mean_ic:.4f}, median_ic={median_ic:.4f}, "
                      f"%positive={pct_positive:.0f}%, n={len(selected_curr_ics)}")

    # ===== C. IC stability filter =====
    print("\n--- C. IC Stability Filter (drop tokens with sign flips) ---")
    # Only include tokens with 0 or 1 sign flips
    for max_flips in [0, 1, 2]:
        stable_tokens = [sym for sym, flips in flip_counts.items() if flips <= max_flips]
        print(f"\n  Max {max_flips} flips: {len(stable_tokens)} tokens")
        for year in YEARS:
            curr_ics = token_ic_by_year.get(year, {})
            selected_ics = [curr_ics[s] for s in stable_tokens if s in curr_ics]
            if selected_ics:
                arr = np.array(selected_ics)
                mean_ic = np.mean(arr)
                pct_positive = np.mean(arr > 0) * 100
                print(f"    {year}: mean_ic={mean_ic:.4f}, %positive={pct_positive:.0f}%, n={len(selected_ics)}")

    # ===== D. Combined: recalibrate signs + top-N + stability =====
    print("\n--- D. Combined: Recalibrate Signs + Top-30 + Stability (max 2 flips) ---")
    stable_set = set(sym for sym, flips in flip_counts.items() if flips <= 2)
    for year in YEARS:
        prev_year = year - 1
        prev_ics = token_ic_by_year.get(prev_year, {})
        if not prev_ics:
            print(f"  {year}: no prior year data")
            continue
        # Filter to stable tokens
        candidates = {s: ic for s, ic in prev_ics.items() if s in stable_set}
        # Top 30 by |IC|
        ranked = sorted(candidates.items(), key=lambda x: -abs(x[1]))[:30]
        selected = set(t[0] for t in ranked)

        # Check sign prediction quality
        correct = 0
        total = 0
        for sym in selected:
            if sym not in all_ic_data:
                continue
            ics = all_ic_data[sym]
            if prev_year not in ics or year not in ics:
                continue
            for metric in ["oi_z", "pos_z", "flow_z"]:
                if metric not in ics.get(prev_year, {}) or metric not in ics.get(year, {}):
                    continue
                prev_sign = 1 if ics[prev_year][metric] > 0 else -1
                curr_sign = 1 if ics[year][metric] > 0 else -1
                total += 1
                if prev_sign == curr_sign:
                    correct += 1

        curr_ics = token_ic_by_year.get(year, {})
        selected_ics = [curr_ics[s] for s in selected if s in curr_ics]
        pct_correct = (correct / total * 100) if total > 0 else 0
        if selected_ics:
            arr = np.array(selected_ics)
            print(f"  {year}: mean_ic={np.mean(arr):.4f}, sign_accuracy={pct_correct:.1f}%, "
                  f"n_tokens={len(selected_ics)}")

    # ===== E. Per-year optimal configs (oracle) =====
    print("\n--- E. Oracle: Per-Year Optimal Signs (upper bound) ---")
    for year in YEARS:
        correct = 0
        total = 0
        for sym, ics in all_ic_data.items():
            if year not in ics:
                continue
            for metric in ["oi_z", "pos_z", "flow_z"]:
                if metric in ics[year]:
                    total += 1
                    correct += 1  # oracle always picks right sign
        # Mean |IC| across all tokens
        curr_ics = token_ic_by_year.get(year, {})
        if curr_ics:
            arr = np.abs(np.array(list(curr_ics.values())))
            print(f"  {year}: mean_|IC|={np.mean(arr):.4f}, n_tokens={len(curr_ics)}, "
                  f"all signs correct by definition")

    return


# ============================================================
# STEP 4: Generate recalibrated configs and run engine backtests
# ============================================================
def step4_engine_backtests(config, all_ic_data, token_ic_by_year, flip_counts):
    """Run actual engine backtests with modified token configs."""
    print("\n" + "=" * 70)
    print("STEP 4: ENGINE BACKTESTS WITH RECALIBRATED CONFIGS")
    print("=" * 70)

    import subprocess
    import shutil

    # Backup original config
    backup_path = CONFIG_PATH.with_suffix(".json.bak")
    shutil.copy2(CONFIG_PATH, backup_path)
    print(f"\nOriginal config backed up to {backup_path}")

    results = {}

    def run_backtest(year, label):
        """Run engine backtest for a given year, return total return %."""
        # Set date range: full year
        end_date = f"{year + 1}-01-01" if year < 2026 else "2026-04-08"
        start_months = 12

        cmd = [
            "/workspace/venv/bin/python", "v4/portfolio_backtest.py",
            "--strategy", "s523c_growth",
            "--months", str(start_months),
            "--capital", "50000",
            "--market", "perp",
            "--conviction-mode", "ranked",
            "--max-portfolio-positions", "30",
            "--concentration", "0.30",
            "--skip-wf",
            "--end-date", end_date,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                cwd=str(BASE)
            )
            import re
            output = result.stdout + result.stderr
            # Parse "Total Return:    +15.5%" pattern
            for line in output.split("\n"):
                if "Total Return:" in line:
                    match = re.search(r'[-+]?\d+\.?\d*%', line)
                    if match:
                        return float(match.group().replace("%", ""))
            print(f"    WARNING: Could not parse return from output for {label} {year}")
            lines = [l for l in output.split("\n") if l.strip()]
            for l in lines[-15:]:
                print(f"      | {l}")
            return None
        except subprocess.TimeoutExpired:
            print(f"    TIMEOUT for {label} {year}")
            return None
        except Exception as e:
            print(f"    ERROR for {label} {year}: {e}")
            return None

    def write_config(new_config):
        """Write modified config to the standard path."""
        with open(CONFIG_PATH, "w") as f:
            json.dump(new_config, f, indent=2)

    def restore_config():
        """Restore original config from backup."""
        shutil.copy2(backup_path, CONFIG_PATH)

    try:
        # ===== Baseline: static config =====
        print("\n--- Baseline: Static Config ---")
        restore_config()
        results["baseline"] = {}
        for year in YEARS:
            ret = run_backtest(year, "baseline")
            results["baseline"][year] = ret
            print(f"  {year}: {ret}%" if ret is not None else f"  {year}: FAILED")

        # ===== A. Yearly IC recalibration =====
        print("\n--- A. Yearly IC Recalibration ---")
        results["yearly_recalib"] = {}
        for year in YEARS:
            # Build new config with signs from prior year
            prev_year = year - 1
            new_config = json.loads(json.dumps(config))  # deep copy

            for sym in new_config:
                if sym not in all_ic_data:
                    continue
                ics = all_ic_data[sym]
                if prev_year not in ics:
                    continue
                yr_ics = ics[prev_year]
                if "oi_z" in yr_ics and new_config[sym].get("oi_sign", 0) != 0:
                    new_config[sym]["oi_sign"] = 1 if yr_ics["oi_z"] > 0 else -1
                if "pos_z" in yr_ics and new_config[sym].get("pos_sign", 0) != 0:
                    new_config[sym]["pos_sign"] = 1 if yr_ics["pos_z"] > 0 else -1
                if "flow_z" in yr_ics and new_config[sym].get("flow_sign", 0) != 0:
                    new_config[sym]["flow_sign"] = 1 if yr_ics["flow_z"] > 0 else -1

            write_config(new_config)
            ret = run_backtest(year, "yearly_recalib")
            results["yearly_recalib"][year] = ret
            print(f"  {year}: {ret}%" if ret is not None else f"  {year}: FAILED")
            restore_config()

        # ===== B. Top-30 by trailing IC (remove low-IC tokens) =====
        print("\n--- B. Top-30 by Trailing IC ---")
        results["top30_trailing"] = {}
        for year in YEARS:
            prev_year = year - 1
            prev_ics = token_ic_by_year.get(prev_year, {})
            if not prev_ics:
                results["top30_trailing"][year] = None
                print(f"  {year}: no prior year data")
                continue

            # Rank by |IC|, keep top 30
            ranked = sorted(prev_ics.items(), key=lambda x: -abs(x[1]))[:30]
            selected = set(t[0] for t in ranked)

            # Build config with only selected tokens, recalibrated signs
            new_config = {}
            for sym in selected:
                if sym not in config:
                    continue
                new_config[sym] = json.loads(json.dumps(config[sym]))
                # Recalibrate signs from prev year
                if sym in all_ic_data and prev_year in all_ic_data[sym]:
                    yr_ics = all_ic_data[sym][prev_year]
                    if "oi_z" in yr_ics and new_config[sym].get("oi_sign", 0) != 0:
                        new_config[sym]["oi_sign"] = 1 if yr_ics["oi_z"] > 0 else -1
                    if "pos_z" in yr_ics and new_config[sym].get("pos_sign", 0) != 0:
                        new_config[sym]["pos_sign"] = 1 if yr_ics["pos_z"] > 0 else -1
                    if "flow_z" in yr_ics and new_config[sym].get("flow_sign", 0) != 0:
                        new_config[sym]["flow_sign"] = 1 if yr_ics["flow_z"] > 0 else -1

            write_config(new_config)
            ret = run_backtest(year, "top30_trailing")
            results["top30_trailing"][year] = ret
            print(f"  {year}: {ret}%" if ret is not None else f"  {year}: FAILED")
            restore_config()

        # ===== C. Stability filter (max 2 flips) + recalibration =====
        print("\n--- C. Stability Filter + Recalibration ---")
        results["stable_recalib"] = {}
        stable_set = set(sym for sym, flips in flip_counts.items() if flips <= 2)
        for year in YEARS:
            prev_year = year - 1
            new_config = {}
            for sym in config:
                if sym not in stable_set:
                    continue
                new_config[sym] = json.loads(json.dumps(config[sym]))
                if sym in all_ic_data and prev_year in all_ic_data[sym]:
                    yr_ics = all_ic_data[sym][prev_year]
                    if "oi_z" in yr_ics and new_config[sym].get("oi_sign", 0) != 0:
                        new_config[sym]["oi_sign"] = 1 if yr_ics["oi_z"] > 0 else -1
                    if "pos_z" in yr_ics and new_config[sym].get("pos_sign", 0) != 0:
                        new_config[sym]["pos_sign"] = 1 if yr_ics["pos_z"] > 0 else -1
                    if "flow_z" in yr_ics and new_config[sym].get("flow_sign", 0) != 0:
                        new_config[sym]["flow_sign"] = 1 if yr_ics["flow_z"] > 0 else -1

            write_config(new_config)
            ret = run_backtest(year, "stable_recalib")
            results["stable_recalib"][year] = ret
            print(f"  {year}: {ret}%" if ret is not None else f"  {year}: FAILED")
            restore_config()

        # ===== D. Combined: stable + top30 + recalib =====
        print("\n--- D. Combined: Stable + Top-30 + Recalibration ---")
        results["combined"] = {}
        for year in YEARS:
            prev_year = year - 1
            prev_ics = token_ic_by_year.get(prev_year, {})
            if not prev_ics:
                results["combined"][year] = None
                continue

            candidates = {s: ic for s, ic in prev_ics.items() if s in stable_set}
            ranked = sorted(candidates.items(), key=lambda x: -abs(x[1]))[:30]
            selected = set(t[0] for t in ranked)

            new_config = {}
            for sym in selected:
                if sym not in config:
                    continue
                new_config[sym] = json.loads(json.dumps(config[sym]))
                if sym in all_ic_data and prev_year in all_ic_data[sym]:
                    yr_ics = all_ic_data[sym][prev_year]
                    if "oi_z" in yr_ics and new_config[sym].get("oi_sign", 0) != 0:
                        new_config[sym]["oi_sign"] = 1 if yr_ics["oi_z"] > 0 else -1
                    if "pos_z" in yr_ics and new_config[sym].get("pos_sign", 0) != 0:
                        new_config[sym]["pos_sign"] = 1 if yr_ics["pos_z"] > 0 else -1
                    if "flow_z" in yr_ics and new_config[sym].get("flow_sign", 0) != 0:
                        new_config[sym]["flow_sign"] = 1 if yr_ics["flow_z"] > 0 else -1

            write_config(new_config)
            ret = run_backtest(year, "combined")
            results["combined"][year] = ret
            print(f"  {year}: {ret}%" if ret is not None else f"  {year}: FAILED")
            restore_config()

        # ===== E. Oracle: use same-year IC signs =====
        print("\n--- E. Oracle: Same-Year IC Signs (lookahead, upper bound) ---")
        results["oracle"] = {}
        for year in YEARS:
            new_config = json.loads(json.dumps(config))
            for sym in new_config:
                if sym not in all_ic_data:
                    continue
                ics = all_ic_data[sym]
                if year not in ics:
                    continue
                yr_ics = ics[year]
                if "oi_z" in yr_ics and new_config[sym].get("oi_sign", 0) != 0:
                    new_config[sym]["oi_sign"] = 1 if yr_ics["oi_z"] > 0 else -1
                if "pos_z" in yr_ics and new_config[sym].get("pos_sign", 0) != 0:
                    new_config[sym]["pos_sign"] = 1 if yr_ics["pos_z"] > 0 else -1
                if "flow_z" in yr_ics and new_config[sym].get("flow_sign", 0) != 0:
                    new_config[sym]["flow_sign"] = 1 if yr_ics["flow_z"] > 0 else -1

            write_config(new_config)
            ret = run_backtest(year, "oracle")
            results["oracle"][year] = ret
            print(f"  {year}: {ret}%" if ret is not None else f"  {year}: FAILED")
            restore_config()

    finally:
        # ALWAYS restore original config
        restore_config()
        print(f"\nOriginal config restored from {backup_path}")

    return results


def save_results(config_analysis_tokens, all_ic_data, results):
    """Save all results to JSON."""
    output = {
        "config_summary": {
            "n_tokens": config_analysis_tokens,
        },
        "empirical_ic": {},
        "engine_results": results,
    }

    # Summarize IC data
    for sym, ics in all_ic_data.items():
        output["empirical_ic"][sym] = {}
        for year, yr_ics in ics.items():
            output["empirical_ic"][sym][str(year)] = {k: round(v, 5) for k, v in yr_ics.items()}

    out_path = BASE / "research/s523_token_rotation_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")


def main():
    config = step1_config_analysis()
    all_ic_data, token_ic_by_year, flip_counts = step2_empirical_ic(config)
    step3_rotation_analysis(config, all_ic_data, token_ic_by_year, flip_counts)
    results = step4_engine_backtests(config, all_ic_data, token_ic_by_year, flip_counts)
    save_results(len(config), all_ic_data, results)

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n{'Approach':<30} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8}")
    print("-" * 70)
    for approach, yr_results in results.items():
        row = f"{approach:<30}"
        for year in YEARS:
            val = yr_results.get(year)
            if val is not None:
                row += f" {val:>7.1f}%"
            else:
                row += f" {'N/A':>7}"
        print(row)


if __name__ == "__main__":
    main()
