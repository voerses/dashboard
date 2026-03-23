#!/usr/bin/env python3
"""
Binance Long/Short Ratio Historical Data Fetcher (with Pagination)
==================================================================
Fetches the maximum available L/S ratio history from Binance Futures API
using backward pagination via endTime parameter.

Binance hard-limits this data to ~28 days of history. This script:
1. Fetches all 4 positioning endpoints (global L/S, top trader L/S account,
   top trader L/S position, taker buy/sell volume ratio)
2. Pages backwards using endTime to get the full 28 days (not just 500 records)
3. Saves to parquet with proper timestamps
4. Supports incremental updates (merges with existing data)
5. Designed to run on a daily cron to accumulate history over time

Usage:
    python fetch_binance_ls_history.py                # Full fetch for all tokens
    python fetch_binance_ls_history.py --tokens BTC   # Single token
    python fetch_binance_ls_history.py --no-merge      # Don't merge with existing
    python fetch_binance_ls_history.py --period 4h     # Use 4h candles

Data is saved to: data/alternative/binance_positioning/
"""

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "alternative" / "binance_positioning"
BINANCE_FUTURES_BASE = "https://fapi.binance.com/futures/data"

# 20 tokens requested + a few extras from the original fetcher
TOKENS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT",
    "UNIUSDT", "AAVEUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "SUIUSDT", "INJUSDT", "SEIUSDT", "WLDUSDT",
]

ENDPOINTS = {
    "globalLongShortAccountRatio": "global_ls_ratio",
    "topLongShortAccountRatio": "top_ls_account_ratio",
    "topLongShortPositionRatio": "top_ls_position_ratio",
    "takerlongshortRatio": "taker_buy_sell_vol",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; CryptoBacktest/1.0)"
})

# Rate limiting
REQUEST_DELAY = 0.25  # seconds between requests
ENDPOINT_DELAY = 1.0  # seconds between endpoint groups
MAX_PAGES = 10        # safety limit per symbol/endpoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_request(url, params=None, retries=3, backoff=2.0):
    """HTTP GET with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"      Rate limited (429). Sleeping {wait:.0f}s ...")
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                wait = 60
                print(f"      IP banned (418). Sleeping {wait}s ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"      Request error: {e}. Retrying in {wait:.0f}s ...")
                time.sleep(wait)
            else:
                raise
    return None


def fetch_all_pages(url, symbol, period, limit=500):
    """
    Fetch all available data for a symbol by paging backwards with endTime.
    Returns a list of raw records (dicts).
    """
    all_records = []
    end_time = None
    seen_timestamps = set()

    for page in range(MAX_PAGES):
        params = {"symbol": symbol, "period": period, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time

        resp = safe_request(url, params=params)
        if resp is None:
            break

        data = resp.json()

        # Handle API errors
        if isinstance(data, dict) and "code" in data:
            if page == 0:
                # First page error means this symbol doesn't exist for this endpoint
                return None, data.get("msg", str(data))
            break

        if not data:
            break

        # Deduplicate
        new_records = []
        for r in data:
            ts = int(r["timestamp"])
            if ts not in seen_timestamps:
                seen_timestamps.add(ts)
                new_records.append(r)

        if not new_records:
            break

        all_records.extend(new_records)

        # Set endTime for next page: earliest timestamp minus 1ms
        earliest_ts = min(int(r["timestamp"]) for r in data)
        end_time = earliest_ts - 1

        # If we got fewer than limit records, we've exhausted history
        if len(data) < limit:
            break

        time.sleep(REQUEST_DELAY)

    return all_records, None


def coerce_dtypes(df):
    """Ensure all columns have proper types (handles str->float from API)."""
    if "timestamp" in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(
                df["timestamp"].astype(int), unit="ms", utc=True
            )
    numeric_cols = [
        "longShortRatio", "longAccount", "shortAccount",
        "longPosition", "shortPosition",
        "buySellRatio", "buyVol", "sellVol",
        "sumOpenInterest", "sumOpenInterestValue",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def merge_with_existing(new_df, out_path):
    """Merge new data with existing parquet, deduplicating by (symbol, timestamp)."""
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            existing = coerce_dtypes(existing)
            combined = pd.concat([existing, new_df], ignore_index=True)
            # Deduplicate: keep latest fetch for each (symbol, timestamp)
            combined = combined.drop_duplicates(
                subset=["symbol", "timestamp"], keep="last"
            )
            combined = combined.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
            return combined
        except Exception as e:
            print(f"    Warning: Could not read existing file, overwriting: {e}")
    return new_df


# ---------------------------------------------------------------------------
# Main fetch logic
# ---------------------------------------------------------------------------
def fetch_endpoint(endpoint_name, file_prefix, tokens, period, merge=True):
    """Fetch one endpoint for all tokens, save to parquet."""
    url = f"{BINANCE_FUTURES_BASE}/{endpoint_name}"
    print(f"\n  Endpoint: {endpoint_name} -> {file_prefix}.parquet")

    all_frames = []
    errors = []

    for i, symbol in enumerate(tokens):
        records, err = fetch_all_pages(url, symbol, period)

        if err:
            errors.append((symbol, err))
            print(f"    {symbol}: error - {err}")
            continue

        if records is None or len(records) == 0:
            print(f"    {symbol}: no data")
            continue

        df = pd.DataFrame(records)
        df["symbol"] = symbol
        all_frames.append(df)

        # Calculate date range for this symbol
        all_ts = sorted([int(r["timestamp"]) for r in records])
        ts_min = pd.to_datetime(all_ts[0], unit="ms", utc=True)
        ts_max = pd.to_datetime(all_ts[-1], unit="ms", utc=True)
        span_days = (ts_max - ts_min).total_seconds() / 86400
        print(f"    {symbol}: {len(records)} records, {span_days:.1f} days ({ts_min.strftime('%Y-%m-%d')} to {ts_max.strftime('%Y-%m-%d')})")

        if i < len(tokens) - 1:
            time.sleep(REQUEST_DELAY)

    if not all_frames:
        print(f"    No data collected for {endpoint_name}")
        return 0, errors

    combined = pd.concat(all_frames, ignore_index=True)

    # Convert all columns to proper types
    combined = coerce_dtypes(combined)

    out_path = BASE_DIR / f"{file_prefix}.parquet"

    if merge:
        combined = merge_with_existing(combined, out_path)

    combined.to_parquet(out_path, index=False)

    # Report
    total_rows = len(combined)
    n_symbols = combined["symbol"].nunique()
    ts_range = combined["timestamp"]
    print(f"    SAVED: {total_rows} rows, {n_symbols} symbols, "
          f"{ts_range.min()} to {ts_range.max()}")

    return total_rows, errors


def main():
    parser = argparse.ArgumentParser(description="Fetch Binance L/S ratio history")
    parser.add_argument("--tokens", nargs="+", default=None,
                        help="Specific tokens to fetch (default: all 20)")
    parser.add_argument("--period", default="1h", choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
                        help="Candle period (default: 1h)")
    parser.add_argument("--no-merge", action="store_true",
                        help="Don't merge with existing data (overwrite)")
    parser.add_argument("--endpoints", nargs="+", default=None,
                        choices=list(ENDPOINTS.keys()),
                        help="Specific endpoints to fetch")
    args = parser.parse_args()

    tokens = args.tokens if args.tokens else TOKENS
    # Normalize: add USDT suffix if missing
    tokens = [t if t.endswith("USDT") else t + "USDT" for t in tokens]

    endpoints = {k: v for k, v in ENDPOINTS.items()
                 if args.endpoints is None or k in args.endpoints}

    merge = not args.no_merge

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    start = datetime.now(timezone.utc)
    print(f"Binance L/S History Fetcher — {start.isoformat()}")
    print(f"Tokens: {len(tokens)} | Period: {args.period} | Merge: {merge}")
    print(f"Endpoints: {list(endpoints.keys())}")
    print(f"Output: {BASE_DIR}")

    total_rows = 0
    all_errors = []

    for endpoint_name, file_prefix in endpoints.items():
        try:
            rows, errors = fetch_endpoint(
                endpoint_name, file_prefix, tokens, args.period, merge
            )
            total_rows += rows
            all_errors.extend(errors)
        except Exception as e:
            print(f"  FATAL ERROR for {endpoint_name}: {e}")
            traceback.print_exc()

        time.sleep(ENDPOINT_DELAY)

    # Summary
    end = datetime.now(timezone.utc)
    elapsed = (end - start).total_seconds()

    print(f"\n{'=' * 70}")
    print(f"SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Total rows saved: {total_rows:,}")
    print(f"  Errors: {len(all_errors)}")
    if all_errors:
        for sym, err in all_errors[:10]:
            print(f"    {sym}: {err}")
        if len(all_errors) > 10:
            print(f"    ... and {len(all_errors) - 10} more")
    print(f"  Elapsed: {elapsed:.1f}s")

    # File listing
    print(f"\n  Files:")
    for p in sorted(BASE_DIR.glob("*.parquet")):
        size_kb = p.stat().st_size / 1024
        try:
            df = pd.read_parquet(p)
            n_rows = len(df)
            n_sym = df["symbol"].nunique() if "symbol" in df.columns else "?"
            ts_col = "timestamp" if "timestamp" in df.columns else None
            if ts_col:
                rng = f"{df[ts_col].min()} to {df[ts_col].max()}"
            else:
                rng = "unknown range"
            print(f"    {p.name:40s} {size_kb:>8.1f} KB  {n_rows:>6,} rows  {n_sym} symbols  {rng}")
        except Exception:
            print(f"    {p.name:40s} {size_kb:>8.1f} KB")

    return 0 if not all_errors else 1


if __name__ == "__main__":
    sys.exit(main())
