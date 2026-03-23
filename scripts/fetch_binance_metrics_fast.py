#!/usr/bin/env python3
"""
Fast parallel fetcher for Binance metrics from data.binance.vision.

Uses concurrent.futures for parallel downloads (10x faster than sequential).
Fetches daily L/S ratio metrics for all target symbols.

Usage:
    python scripts/fetch_binance_metrics_fast.py
    python scripts/fetch_binance_metrics_fast.py --start 2023-01-01
    python scripts/fetch_binance_metrics_fast.py --extend  # only new data
"""

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"
OUTPUT_DIR = Path("data/alternative/binance_metrics")
DAILY_DIR = Path("data/alternative/binance_metrics/daily")

MAX_WORKERS = 10  # Concurrent download threads

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "BNBUSDT", "DOTUSDT",
    "LTCUSDT", "UNIUSDT", "AAVEUSDT", "ARBUSDT", "APTUSDT",
    "NEARUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT",
    "MATICUSDT", "FILUSDT", "ATOMUSDT", "MKRUSDT", "IMXUSDT",
    "TRXUSDT", "PEPEUSDT", "WIFUSDT", "ONDOUSDT", "TIAUSDT",
]

DEFAULT_START = "2020-09-01"


def fetch_day(args_tuple):
    """Fetch a single day's data. Returns (date_str, dataframe_or_none)."""
    symbol, date_str, session = args_tuple
    url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{date_str}.zip"
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            return (date_str, None)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f)
        return (date_str, df)
    except Exception as e:
        return (date_str, None)


def aggregate_daily(df: pd.DataFrame) -> dict:
    """Aggregate 5-minute metrics into daily summary."""
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


def get_last_date(symbol: str) -> str | None:
    """Check existing data for this symbol."""
    path = DAILY_DIR / f"{symbol}_ls_metrics.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        if len(df) > 0 and "date" in df.columns:
            return str(df["date"].max().date())
    return None


def fetch_symbol_parallel(symbol: str, start_date: str, end_date: str, extend: bool = False):
    """Fetch all daily metrics for a symbol using parallel downloads."""
    if extend:
        last = get_last_date(symbol)
        if last:
            start_date = str((pd.Timestamp(last) + timedelta(days=1)).date())
            if start_date > end_date:
                return None, "up_to_date"

    dates = pd.date_range(start_date, end_date, freq="D")
    session = requests.Session()

    # Prepare tasks
    tasks = [(symbol, dt.strftime("%Y-%m-%d"), session) for dt in dates]

    # Fetch in parallel
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_day, t): t[1] for t in tasks}
        for future in as_completed(futures):
            date_str, df = future.result()
            if df is not None:
                results[date_str] = df

    if not results:
        return None, "no_data"

    # Aggregate to daily
    rows = []
    for date_str in sorted(results.keys()):
        daily = aggregate_daily(results[date_str])
        daily["date"] = pd.Timestamp(date_str)
        daily["symbol"] = symbol
        rows.append(daily)

    df = pd.DataFrame(rows)
    return df, "success"


def save_symbol(df: pd.DataFrame, symbol: str, extend: bool = False):
    """Save or append data."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_DIR / f"{symbol}_ls_metrics.parquet"

    if extend and path.exists():
        existing = pd.read_parquet(path)
        df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values("date").reset_index(drop=True)

    df.to_parquet(path, index=False)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None)
    parser.add_argument("--extend", action="store_true")
    args = parser.parse_args()

    end_date = args.end or str((datetime.utcnow() - timedelta(days=1)).date())

    print(f"Fetching {len(args.symbols)} symbols, {args.start} to {end_date}")
    print(f"Using {MAX_WORKERS} parallel workers per symbol\n")

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    summary = {"success": [], "no_data": [], "up_to_date": [], "error": []}

    for i, symbol in enumerate(args.symbols):
        t0 = time.time()
        print(f"[{i+1}/{len(args.symbols)}] {symbol}...", end=" ", flush=True)
        try:
            df, status = fetch_symbol_parallel(symbol, args.start, end_date, args.extend)
            if status == "up_to_date":
                print("already up to date")
                summary["up_to_date"].append(symbol)
            elif df is not None:
                path = save_symbol(df, symbol, args.extend)
                elapsed = time.time() - t0
                print(f"{len(df)} days in {elapsed:.1f}s")
                summary["success"].append((symbol, len(df)))
            else:
                print("no data")
                summary["no_data"].append(symbol)
        except Exception as e:
            print(f"ERROR: {e}")
            summary["error"].append((symbol, str(e)))

    # Summary
    print(f"\n{'='*60}")
    print(f"Success: {len(summary['success'])} symbols")
    for sym, n in summary["success"]:
        print(f"  {sym}: {n} days")
    if summary["no_data"]:
        print(f"No data: {summary['no_data']}")
    if summary["up_to_date"]:
        print(f"Up to date: {summary['up_to_date']}")

    # Create combined file
    if summary["success"] or summary["up_to_date"]:
        all_dfs = []
        for f in DAILY_DIR.glob("*_ls_metrics.parquet"):
            all_dfs.append(pd.read_parquet(f))
        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            combined_path = OUTPUT_DIR / "all_symbols_daily_ls.parquet"
            combined.to_parquet(combined_path, index=False)
            print(f"\nCombined: {combined_path} ({len(combined)} rows, {combined['symbol'].nunique()} symbols)")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
