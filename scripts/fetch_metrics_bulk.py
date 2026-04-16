#!/usr/bin/env python3
"""
Bulk parallel fetcher for Binance metrics from data.binance.vision.

Smarter than fetch_binance_metrics_fast.py:
  1. Binary-searches each token's earliest date (avoids 2000 404s)
  2. Parallelizes across symbols AND dates
  3. Skips already-fetched data (--extend mode)
  4. data.binance.vision is S3 — no rate limits, safe to parallelize heavily

Usage:
    python scripts/fetch_metrics_bulk.py                    # Full fetch all tokens
    python scripts/fetch_metrics_bulk.py --extend           # Only new data
    python scripts/fetch_metrics_bulk.py --workers 20       # More parallelism
"""

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"
OUTPUT_DIR = Path("data/alternative/binance_metrics")
DAILY_DIR = Path("data/alternative/binance_metrics/daily")

# All perp tokens from our universe
def get_all_symbols():
    perp_dir = Path("data/perp/1h_cache")
    tokens = [f.name.replace("_1h.parquet", "") for f in perp_dir.glob("*_1h.parquet")]
    return sorted([t + "USDT" for t in tokens])


def check_exists(symbol, date_str, session):
    """HEAD request — fast check if data exists."""
    url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{date_str}.zip"
    try:
        r = session.head(url, timeout=10)
        return r.status_code == 200
    except:
        return False


def find_earliest(symbol, session):
    """Binary search for earliest available date."""
    lo = date(2019, 9, 1)
    hi = date.today() - timedelta(days=1)  # Yesterday (today not published yet)

    if not check_exists(symbol, hi.strftime("%Y-%m-%d"), session):
        return None  # No data at all

    while (hi - lo).days > 1:
        mid = lo + (hi - lo) // 2
        if check_exists(symbol, mid.strftime("%Y-%m-%d"), session):
            hi = mid
        else:
            lo = mid
    return hi


def fetch_day(args_tuple):
    """Fetch single day's metrics ZIP. Returns (symbol, date_str, df_or_None)."""
    symbol, date_str, session = args_tuple
    url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{date_str}.zip"
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            return (symbol, date_str, None)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f)
        return (symbol, date_str, df)
    except Exception as e:
        return (symbol, date_str, None)


def aggregate_daily(df):
    """Aggregate 5-min metrics to daily means."""
    return {
        "count_toptrader_ls_ratio": df["count_toptrader_long_short_ratio"].mean(),
        "sum_toptrader_ls_ratio": df["sum_toptrader_long_short_ratio"].mean(),
        "count_ls_ratio": df["count_long_short_ratio"].mean(),
        "taker_buy_sell_ratio": df["sum_taker_long_short_vol_ratio"].mean(),
        "count_ls_ratio_max": df["count_long_short_ratio"].max(),
        "count_ls_ratio_min": df["count_long_short_ratio"].min(),
        "taker_ratio_max": df["sum_taker_long_short_vol_ratio"].max(),
        "taker_ratio_min": df["sum_taker_long_short_vol_ratio"].min(),
        "sum_open_interest": df["sum_open_interest"].mean(),
        "sum_open_interest_value": df["sum_open_interest_value"].mean(),
        "n_records": len(df),
    }


def get_last_date(symbol):
    """Get last date in existing data."""
    path = DAILY_DIR / f"{symbol}_ls_metrics.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        if len(df) > 0 and "date" in df.columns:
            return df["date"].max().date()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=30,
                        help="Parallel download threads")
    parser.add_argument("--extend", action="store_true",
                        help="Only fetch new data since last download")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Override symbol list")
    args = parser.parse_args()

    symbols = args.symbols or get_all_symbols()
    end_date = date.today() - timedelta(days=1)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching metrics for {len(symbols)} symbols")
    print(f"Workers: {args.workers}, end date: {end_date}")
    print(f"Mode: {'extend' if args.extend else 'full'}\n")

    # Phase 1: Find date ranges (parallel)
    print("Phase 1: Finding date ranges...")
    t0 = time.time()
    session = requests.Session()

    symbol_ranges = {}
    skipped = []
    up_to_date = []
    need_search = []

    # First pass: separate extend-able vs needs-search
    for symbol in symbols:
        if args.extend:
            last = get_last_date(symbol)
            if last and last >= end_date:
                up_to_date.append(symbol)
                continue
            if last:
                symbol_ranges[symbol] = (last + timedelta(days=1), end_date)
                continue
        need_search.append(symbol)

    print(f"  {len(up_to_date)} up-to-date, {len(symbol_ranges)} extending, "
          f"{len(need_search)} need date search")

    # Parallel binary search for new symbols
    if need_search:
        print(f"  Binary-searching {len(need_search)} symbols in parallel...")

        def _search_one(sym):
            s = requests.Session()
            earliest = find_earliest(sym, s)
            return (sym, earliest)

        with ThreadPoolExecutor(max_workers=min(20, len(need_search))) as executor:
            futures = {executor.submit(_search_one, s): s for s in need_search}
            done_count = 0
            for future in as_completed(futures):
                sym, earliest = future.result()
                done_count += 1
                if earliest:
                    symbol_ranges[sym] = (earliest, end_date)
                else:
                    skipped.append(sym)
                if done_count % 50 == 0:
                    print(f"    Searched {done_count}/{len(need_search)} "
                          f"({len(symbol_ranges)} found, {len(skipped)} no data)")

    print(f"  Found {len(symbol_ranges)} symbols to fetch, {len(up_to_date)} up-to-date, {len(skipped)} no data")
    print(f"  Phase 1 took {time.time()-t0:.1f}s\n")

    # Phase 2: Build task list
    all_tasks = []
    for symbol, (start, end) in symbol_ranges.items():
        dates = pd.date_range(start, end, freq="D")
        for dt in dates:
            all_tasks.append((symbol, dt.strftime("%Y-%m-%d"), session))

    total_downloads = len(all_tasks)
    print(f"Phase 2: Downloading {total_downloads:,} day-files across {len(symbol_ranges)} symbols")

    # Phase 3: Download per-symbol and save immediately (resumable)
    t1 = time.time()
    saved_symbols = 0
    total_done = 0

    for sym_idx, (symbol, (start, _end)) in enumerate(sorted(symbol_ranges.items())):
        dates = pd.date_range(start, end_date, freq="D")
        tasks = [(symbol, dt.strftime("%Y-%m-%d"), session) for dt in dates]

        day_data = {}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(fetch_day, t): t for t in tasks}
            for future in as_completed(futures):
                sym, date_str, df = future.result()
                total_done += 1
                if df is not None:
                    day_data[date_str] = df

        if day_data:
            rows = []
            for date_str in sorted(day_data.keys()):
                daily = aggregate_daily(day_data[date_str])
                daily["date"] = pd.Timestamp(date_str)
                daily["symbol"] = symbol
                rows.append(daily)

            df = pd.DataFrame(rows)

            # Merge with existing
            path = DAILY_DIR / f"{symbol}_ls_metrics.parquet"
            if path.exists():
                existing = pd.read_parquet(path)
                df = pd.concat([existing, df], ignore_index=True)
                df = df.drop_duplicates(subset=["date"], keep="last")
                df = df.sort_values("date").reset_index(drop=True)

            df.to_parquet(path, index=False)
            saved_symbols += 1

        elapsed = time.time() - t1
        rate = total_done / elapsed if elapsed > 0 else 0
        remaining = total_downloads - total_done
        eta = remaining / rate / 60 if rate > 0 else 0
        print(f"  [{sym_idx+1}/{len(symbol_ranges)}] {symbol}: {len(day_data)} days saved "
              f"| {total_done:,}/{total_downloads:,} ({total_done*100//max(total_downloads,1)}%) "
              f"| {rate:.0f}/s | ETA {eta:.1f}m")

    elapsed = time.time() - t1
    print(f"\n  Done: {saved_symbols} symbols saved in {elapsed:.0f}s")

    # Phase 5: Build combined parquets — one per metric type + one combined
    print("Phase 4: Building parquets...")
    all_dfs = []
    for f in DAILY_DIR.glob("*_ls_metrics.parquet"):
        all_dfs.append(pd.read_parquet(f))
    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)

        # Combined file (backwards compatible with s506/s507/s514)
        combined_path = OUTPUT_DIR / "all_symbols_daily_ls.parquet"
        combined.to_parquet(combined_path, index=False)
        n_sym = combined['symbol'].nunique()
        print(f"\n  Combined: {combined_path}")
        print(f"    {len(combined):,} rows, {n_sym} symbols")
        print(f"    {combined['date'].min().date()} to {combined['date'].max().date()}")
        print(f"    {combined_path.stat().st_size/1024/1024:.1f} MB")

        # Per-metric parquets for selective loading
        metric_dir = OUTPUT_DIR / "by_metric"
        metric_dir.mkdir(exist_ok=True)

        metric_defs = {
            "toptrader_ls_account": ["date", "symbol", "count_toptrader_ls_ratio"],
            "toptrader_ls_position": ["date", "symbol", "sum_toptrader_ls_ratio"],
            "global_ls_account": ["date", "symbol", "count_ls_ratio"],
            "taker_buy_sell_ratio": ["date", "symbol", "taker_buy_sell_ratio"],
            "open_interest": ["date", "symbol", "sum_open_interest", "sum_open_interest_value"],
        }

        for name, cols in metric_defs.items():
            avail_cols = [c for c in cols if c in combined.columns]
            if len(avail_cols) >= 3 or (len(avail_cols) == 2 and "date" in avail_cols):
                mf = combined[avail_cols].copy()
                mpath = metric_dir / f"{name}.parquet"
                mf.to_parquet(mpath, index=False)
                print(f"  {name}: {mpath} ({mf.shape[0]:,} rows, {mpath.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
