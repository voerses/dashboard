#!/usr/bin/env python3
"""
Fetch raw 5-min L/S metrics from data.binance.vision and save as parquet.

Saves one parquet per symbol with all 288 5-min rows per day.
Resumable: skips symbols that already have data up to end_date.

Usage:
    python scripts/fetch_metrics_5min.py
    python scripts/fetch_metrics_5min.py --workers 30
"""

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"
OUTPUT_DIR = Path("data/alternative/binance_metrics/5min")

def get_all_symbols():
    perp_dir = Path("data/perp/1h_cache")
    tokens = [f.name.replace("_1h.parquet", "") for f in perp_dir.glob("*_1h.parquet")]
    return sorted([t + "USDT" for t in tokens])


def find_earliest(symbol, session):
    lo = date(2019, 9, 1)
    hi = date.today() - timedelta(days=1)
    url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{hi}.zip"
    try:
        r = session.head(url, timeout=10)
        if r.status_code != 200:
            return None
    except:
        return None
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) // 2
        url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{mid}.zip"
        try:
            r = session.head(url, timeout=10)
            if r.status_code == 200:
                hi = mid
            else:
                lo = mid
        except:
            lo = mid
    return hi


def fetch_day(args_tuple):
    symbol, date_str, session = args_tuple
    url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{date_str}.zip"
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            return (symbol, date_str, None)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = pd.read_csv(z.open(z.namelist()[0]))
        df['create_time'] = pd.to_datetime(df['create_time'])
        return (symbol, date_str, df)
    except:
        return (symbol, date_str, None)


def get_last_date(symbol):
    path = OUTPUT_DIR / f"{symbol}_5min.parquet"
    if path.exists():
        df = pd.read_parquet(path, columns=['create_time'])
        if len(df) > 0:
            return df['create_time'].max().date()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    symbols = args.symbols or get_all_symbols()
    end_date = date.today() - timedelta(days=1)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching 5-min metrics for {len(symbols)} symbols")
    print(f"Workers: {args.workers}, end date: {end_date}")

    # Phase 1: Find date ranges (parallel)
    print("\nPhase 1: Finding date ranges...")
    t0 = time.time()
    session = requests.Session()
    symbol_ranges = {}
    skipped = []
    up_to_date = []
    need_search = []

    for symbol in symbols:
        last = get_last_date(symbol)
        if last and last >= end_date:
            up_to_date.append(symbol)
            continue
        if last:
            symbol_ranges[symbol] = (last + timedelta(days=1), end_date)
            continue
        need_search.append(symbol)

    print(f"  {len(up_to_date)} up-to-date, {len(symbol_ranges)} extending, {len(need_search)} need search")

    if need_search:
        print(f"  Binary-searching {len(need_search)} symbols...")
        def _search(sym):
            s = requests.Session()
            return (sym, find_earliest(sym, s))
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(_search, s): s for s in need_search}
            done = 0
            for f in as_completed(futs):
                sym, earliest = f.result()
                done += 1
                if earliest:
                    symbol_ranges[sym] = (earliest, end_date)
                else:
                    skipped.append(sym)
                if done % 50 == 0:
                    print(f"    {done}/{len(need_search)} searched ({len(symbol_ranges)} found)")

    print(f"  {len(symbol_ranges)} symbols to fetch, {len(skipped)} no data")
    print(f"  Phase 1: {time.time()-t0:.0f}s")

    # Phase 2: Download per-symbol, save immediately
    total_downloads = sum((e - s).days + 1 for s, e in symbol_ranges.values())
    print(f"\nPhase 2: Downloading {total_downloads:,} day-files across {len(symbol_ranges)} symbols")

    t1 = time.time()
    total_done = 0
    total_rows = 0
    session = requests.Session()

    for sym_idx, (symbol, (start, end)) in enumerate(sorted(symbol_ranges.items())):
        dates = pd.date_range(start, end, freq="D")
        tasks = [(symbol, dt.strftime("%Y-%m-%d"), session) for dt in dates]

        all_dfs = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch_day, t): t for t in tasks}
            for f in as_completed(futs):
                sym, ds, df = f.result()
                total_done += 1
                if df is not None:
                    all_dfs.append(df)

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            combined = combined.sort_values('create_time').reset_index(drop=True)

            # Merge with existing
            path = OUTPUT_DIR / f"{symbol}_5min.parquet"
            if path.exists():
                existing = pd.read_parquet(path)
                combined = pd.concat([existing, combined], ignore_index=True)
                combined = combined.drop_duplicates(subset=['create_time'], keep='last')
                combined = combined.sort_values('create_time').reset_index(drop=True)

            combined.to_parquet(path, index=False)
            total_rows += len(combined)

        elapsed = time.time() - t1
        rate = total_done / elapsed if elapsed > 0 else 0
        remaining = total_downloads - total_done
        eta = remaining / rate / 60 if rate > 0 else 0
        print(f"  [{sym_idx+1}/{len(symbol_ranges)}] {symbol}: {len(all_dfs)} days "
              f"| {total_done:,}/{total_downloads:,} ({total_done*100//max(total_downloads,1)}%) "
              f"| {rate:.0f}/s | ETA {eta:.1f}m")

    elapsed = time.time() - t1
    print(f"\nDone: {len(symbol_ranges)} symbols, {total_rows:,} total rows in {elapsed:.0f}s")
    print(f"Storage: {sum(f.stat().st_size for f in OUTPUT_DIR.glob('*.parquet'))/1024/1024:.0f} MB")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
