#!/usr/bin/env python3
"""
Fetch extended Long/Short ratio data from Binance using backward pagination.

Strategy: Use endTime parameter to page backwards from the earliest data we have.
Each request returns up to 500 hourly candles. We keep paginating until the API
returns no data or we reach 6+ months back.
"""

import os
import time
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# Configuration
BASE_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
OUTPUT_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning/extended"
EXISTING_DATA = "/workspace/crypto_backtest/data/alternative/binance_positioning/global_ls_ratio.parquet"

# Tokens to fetch - priority list (the 6 requested + all 19 existing)
PRIORITY_TOKENS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
ALL_TOKENS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "AAVEUSDT", "ADAUSDT", "ARBUSDT", "AVAXUSDT", "DOTUSDT", "INJUSDT",
    "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SUIUSDT", "UNIUSDT", "WIFUSDT"
]

PERIOD = "1h"
LIMIT = 500
# Target: 6 months back from earliest existing data (Mar 3, 2026)
TARGET_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
# Earliest existing timestamp
EARLIEST_EXISTING = datetime(2026, 3, 3, 2, 0, 0, tzinfo=timezone.utc)

# Rate limiting: Binance futures data endpoints have rate limits
REQUEST_DELAY = 0.5  # seconds between requests


def fetch_page(symbol: str, end_time_ms: int) -> list[dict]:
    """Fetch one page of L/S ratio data ending before end_time_ms."""
    params = {
        "symbol": symbol,
        "period": PERIOD,
        "limit": LIMIT,
        "endTime": end_time_ms,
    }

    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            if resp.status_code == 429:
                # Rate limited - back off
                wait = 10 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                # IP ban - longer backoff
                print(f"    IP temporarily banned (418), waiting 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"    Request error (attempt {attempt+1}/3): {e}")
            time.sleep(5 * (attempt + 1))

    return []


def fetch_symbol_backward(symbol: str) -> pd.DataFrame:
    """Fetch all available historical data for a symbol using backward pagination."""
    all_data = []
    # Start from just before our earliest existing data
    end_time = EARLIEST_EXISTING - timedelta(hours=1)
    end_time_ms = int(end_time.timestamp() * 1000)

    page = 0
    consecutive_empty = 0

    while True:
        page += 1
        data = fetch_page(symbol, end_time_ms)

        if not data:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                print(f"    No more data available (page {page})")
                break
            # Try going back a bit more
            end_time_ms -= 500 * 3600 * 1000  # skip 500 hours back
            time.sleep(REQUEST_DELAY)
            continue

        consecutive_empty = 0
        all_data.extend(data)

        # Find the earliest timestamp in this batch
        timestamps = [int(d["timestamp"]) for d in data]
        earliest_ms = min(timestamps)
        earliest_dt = datetime.fromtimestamp(earliest_ms / 1000, tz=timezone.utc)
        latest_ms = max(timestamps)
        latest_dt = datetime.fromtimestamp(latest_ms / 1000, tz=timezone.utc)

        print(f"    Page {page}: got {len(data)} records, range {earliest_dt.strftime('%Y-%m-%d %H:%M')} to {latest_dt.strftime('%Y-%m-%d %H:%M')}")

        # Check if we've reached our target
        if earliest_dt <= TARGET_START:
            print(f"    Reached target start date!")
            break

        # Check if we got fewer than limit (means we're near the end of available data)
        if len(data) < LIMIT:
            print(f"    Got {len(data)} < {LIMIT} records, may be near data boundary")

        # Set next endTime to just before the earliest record in this batch
        end_time_ms = earliest_ms - 1

        time.sleep(REQUEST_DELAY)

    if not all_data:
        return pd.DataFrame()

    # Convert to DataFrame
    df = pd.DataFrame(all_data)

    # Parse timestamp
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)

    # Ensure required columns
    df["symbol"] = symbol
    for col in ["longShortRatio", "longAccount", "shortAccount"]:
        if col not in df.columns:
            df[col] = None

    # Deduplicate by timestamp
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    return df[["timestamp", "symbol", "longShortRatio", "longAccount", "shortAccount"]]


def main():
    print("=" * 70)
    print("Binance L/S Ratio Extended Data Fetcher (Backward Pagination)")
    print("=" * 70)
    print(f"Target: {TARGET_START.strftime('%Y-%m-%d')} to {EARLIEST_EXISTING.strftime('%Y-%m-%d')}")
    print(f"Tokens: {len(ALL_TOKENS)}")
    print(f"Output: {OUTPUT_DIR}")
    print()

    # First, test with BTCUSDT to see how far back we can go
    results = {}

    for i, symbol in enumerate(ALL_TOKENS):
        print(f"\n[{i+1}/{len(ALL_TOKENS)}] Fetching {symbol}...")

        df = fetch_symbol_backward(symbol)

        if df.empty:
            print(f"  No data retrieved for {symbol}")
            results[symbol] = {"rows": 0, "months": 0}
            continue

        # Save to parquet
        output_path = os.path.join(OUTPUT_DIR, f"{symbol}_ls_ratio.parquet")
        df.to_parquet(output_path, index=False)

        # Calculate stats
        date_range = df["timestamp"].max() - df["timestamp"].min()
        months = date_range.days / 30.44

        results[symbol] = {
            "rows": len(df),
            "start": df["timestamp"].min().strftime("%Y-%m-%d %H:%M"),
            "end": df["timestamp"].max().strftime("%Y-%m-%d %H:%M"),
            "months": round(months, 1),
            "file": output_path,
        }

        print(f"  Saved {len(df)} rows ({months:.1f} months) to {output_path}")

        # Small delay between symbols
        time.sleep(1)

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_rows = 0
    tokens_with_data = 0
    min_months = float("inf")
    max_months = 0

    for symbol, info in results.items():
        if info["rows"] > 0:
            tokens_with_data += 1
            total_rows += info["rows"]
            min_months = min(min_months, info["months"])
            max_months = max(max_months, info["months"])
            print(f"  {symbol}: {info['rows']:,} rows, {info['months']} months ({info['start']} to {info['end']})")
        else:
            print(f"  {symbol}: NO DATA")

    if tokens_with_data > 0:
        print(f"\nTokens with data: {tokens_with_data}/{len(ALL_TOKENS)}")
        print(f"Total rows: {total_rows:,}")
        print(f"Date range: {min_months} to {max_months} months")
    else:
        print("\nNo extended data retrieved from Binance API.")
        print("Will need to try alternative sources.")

    return results


if __name__ == "__main__":
    results = main()

    # Save results summary as JSON for downstream scripts
    summary_path = os.path.join(OUTPUT_DIR, "_fetch_summary.json")
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSummary saved to {summary_path}")
