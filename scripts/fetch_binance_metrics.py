#!/usr/bin/env python3
"""
Fetch historical Long/Short ratio data from data.binance.vision metrics.

Binance publishes daily metrics ZIP files at 5-minute granularity containing:
  - count_toptrader_long_short_ratio  (top trader L/S by account count)
  - sum_toptrader_long_short_ratio    (top trader L/S by position value)
  - count_long_short_ratio            (global L/S by account count)
  - sum_taker_long_short_vol_ratio    (taker buy/sell volume ratio)
  - sum_open_interest / sum_open_interest_value

This bypasses Binance's 28-day API limit because data.binance.vision stores
the full archive (since 2020-09-01 for majors).

Usage:
    python scripts/fetch_binance_metrics.py [--symbols BTC ETH SOL] [--start 2023-01-01] [--daily-agg]
    python scripts/fetch_binance_metrics.py --extend  # fetch only new dates since last run
"""

import argparse
import io
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"
OUTPUT_DIR = Path("data/alternative/binance_metrics")
DAILY_DIR = Path("data/alternative/binance_metrics/daily")

# Top 30 perp symbols by typical volume/OI -- extend as needed
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "BNBUSDT", "DOTUSDT",
    "LTCUSDT", "UNIUSDT", "AAVEUSDT", "ARBUSDT", "APTUSDT",
    "NEARUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT",
    "MATICUSDT", "FILUSDT", "ATOMUSDT", "MKRUSDT", "IMXUSDT",
    "TRXUSDT", "PEPEUSDT", "WIFUSDT", "ONDOUSDT", "TIAUSDT",
]

# How far back to look for each symbol (will skip 404s gracefully)
DEFAULT_START = "2020-09-01"


def fetch_day(symbol: str, date_str: str, session: requests.Session) -> pd.DataFrame | None:
    """Fetch a single day's metrics ZIP file from data.binance.vision."""
    url = f"{BASE_URL}/{symbol}/{symbol}-metrics-{date_str}.zip"
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = f"{symbol}-metrics-{date_str}.csv"
        if csv_name not in z.namelist():
            csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f)
        return df
    except Exception as e:
        print(f"  WARN: {symbol} {date_str}: {e}")
        return None


def aggregate_daily(df: pd.DataFrame) -> pd.Series:
    """Aggregate 5-minute metrics into daily summary."""
    return pd.Series({
        # L/S ratios: use daily mean (most stable for signals)
        "count_toptrader_ls_ratio": df["count_toptrader_long_short_ratio"].mean(),
        "sum_toptrader_ls_ratio": df["sum_toptrader_long_short_ratio"].mean(),
        "count_ls_ratio": df["count_long_short_ratio"].mean(),
        "taker_buy_sell_ratio": df["sum_taker_long_short_vol_ratio"].mean(),
        # Also capture extremes for signal generation
        "count_ls_ratio_max": df["count_long_short_ratio"].max(),
        "count_ls_ratio_min": df["count_long_short_ratio"].min(),
        "taker_ratio_max": df["sum_taker_long_short_vol_ratio"].max(),
        "taker_ratio_min": df["sum_taker_long_short_vol_ratio"].min(),
        # OI
        "sum_open_interest": df["sum_open_interest"].mean(),
        "sum_open_interest_value": df["sum_open_interest_value"].mean(),
        # Data quality
        "n_records": len(df),
    })


def get_last_date(symbol: str) -> str | None:
    """Check if we already have data for this symbol and return the last date."""
    parquet_path = DAILY_DIR / f"{symbol}_ls_metrics.parquet"
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
        if len(df) > 0 and "date" in df.columns:
            return str(df["date"].max().date())
    return None


def fetch_symbol(
    symbol: str,
    start_date: str,
    end_date: str,
    session: requests.Session,
    extend: bool = False,
) -> pd.DataFrame | None:
    """Fetch all daily metrics for a single symbol."""
    if extend:
        last = get_last_date(symbol)
        if last:
            # Start from the day after the last date we have
            start_date = str(
                (pd.Timestamp(last) + timedelta(days=1)).date()
            )
            if start_date > end_date:
                print(f"  {symbol}: already up to date (last: {last})")
                return None

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    dates = pd.date_range(start, end, freq="D")

    rows = []
    consecutive_404 = 0
    max_consecutive_404 = 30  # skip symbol if 30+ consecutive 404s from start

    for i, dt in enumerate(dates):
        date_str = dt.strftime("%Y-%m-%d")
        df = fetch_day(symbol, date_str, session)

        if df is None:
            consecutive_404 += 1
            if consecutive_404 >= max_consecutive_404 and len(rows) == 0:
                print(f"  {symbol}: no data found after {max_consecutive_404} days from {start_date}, skipping")
                return None
            continue
        else:
            consecutive_404 = 0

        daily = aggregate_daily(df)
        daily["date"] = dt
        daily["symbol"] = symbol
        rows.append(daily)

        # Progress indicator every 100 days
        if (i + 1) % 200 == 0:
            print(f"    {symbol}: fetched {i+1}/{len(dates)} days ({len(rows)} with data)")

        # Rate limiting: be respectful
        time.sleep(0.05)

    if not rows:
        print(f"  {symbol}: no data found")
        return None

    result = pd.DataFrame(rows)
    result["date"] = pd.to_datetime(result["date"])
    return result


def save_symbol(df: pd.DataFrame, symbol: str, extend: bool = False):
    """Save or append symbol data to parquet."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = DAILY_DIR / f"{symbol}_ls_metrics.parquet"

    if extend and parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values("date").reset_index(drop=True)

    df.to_parquet(parquet_path, index=False)
    return parquet_path


def main():
    parser = argparse.ArgumentParser(description="Fetch Binance L/S metrics from data.binance.vision")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Symbols to fetch")
    parser.add_argument("--start", default=DEFAULT_START, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (default: yesterday)")
    parser.add_argument("--extend", action="store_true", help="Only fetch new dates since last run")
    parser.add_argument("--all-symbols", action="store_true", help="Fetch all available symbols (slow!)")
    args = parser.parse_args()

    end_date = args.end or str((datetime.utcnow() - timedelta(days=1)).date())

    if args.all_symbols:
        # Discover all symbols from S3
        import xml.etree.ElementTree as ET
        r = requests.get(
            "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
            "?prefix=data/futures/um/daily/metrics/&delimiter=/",
            timeout=10,
        )
        root = ET.fromstring(r.text)
        ns = "http://s3.amazonaws.com/doc/2006-03-01/"
        prefixes = root.findall(f".//{{{ns}}}CommonPrefixes/{{{ns}}}Prefix")
        symbols = [p.text.split("/")[-2] for p in prefixes]
        print(f"Discovered {len(symbols)} symbols")
    else:
        symbols = args.symbols

    print(f"Fetching metrics for {len(symbols)} symbols")
    print(f"Date range: {args.start} to {end_date}")
    print(f"Mode: {'extend' if args.extend else 'full'}")
    print()

    session = requests.Session()
    summary = {"success": [], "no_data": [], "error": []}

    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] {symbol}...")
        try:
            df = fetch_symbol(symbol, args.start, end_date, session, extend=args.extend)
            if df is not None and len(df) > 0:
                path = save_symbol(df, symbol, extend=args.extend)
                print(f"  Saved {len(df)} days to {path}")
                summary["success"].append((symbol, len(df)))
            else:
                summary["no_data"].append(symbol)
        except Exception as e:
            print(f"  ERROR: {e}")
            summary["error"].append((symbol, str(e)))

    # Print summary
    print("\n" + "=" * 60)
    print("FETCH SUMMARY")
    print("=" * 60)
    print(f"Success: {len(summary['success'])} symbols")
    for sym, n in summary["success"]:
        print(f"  {sym}: {n} days")
    if summary["no_data"]:
        print(f"No data: {summary['no_data']}")
    if summary["error"]:
        print(f"Errors: {summary['error']}")

    # Also create a combined file for easy analysis
    if summary["success"]:
        all_dfs = []
        for sym, _ in summary["success"]:
            path = DAILY_DIR / f"{sym}_ls_metrics.parquet"
            all_dfs.append(pd.read_parquet(path))
        combined = pd.concat(all_dfs, ignore_index=True)
        combined_path = OUTPUT_DIR / "all_symbols_daily_ls.parquet"
        combined.to_parquet(combined_path, index=False)
        print(f"\nCombined file: {combined_path} ({len(combined)} rows)")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
