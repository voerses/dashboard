#!/usr/bin/env python3
"""
Fetch historical funding rate data from Binance data.binance.vision bulk downloads.

Funding rate is a strong positioning proxy:
- Positive FR = more longs than shorts (longs pay shorts)
- Negative FR = more shorts than longs (shorts pay longs)

We convert FR to a synthetic longShortRatio for compatibility with the existing signal.

Data source: https://data.binance.vision
Available: 2020-01 through 2026-02 (monthly zips)
"""

import os
import io
import zipfile
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# Configuration
OUTPUT_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning/extended"
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/fundingRate"

# We want Sep 2025 through Feb 2026 (6 months)
MONTHS = [
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02"
]

# All tokens
TOKENS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "AAVEUSDT", "ADAUSDT", "ARBUSDT", "AVAXUSDT", "DOTUSDT", "INJUSDT",
    "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SUIUSDT", "UNIUSDT", "WIFUSDT"
]


def download_funding_csv(symbol: str, month: str) -> pd.DataFrame | None:
    """Download one monthly funding rate ZIP and return as DataFrame."""
    url = f"{BASE_URL}/{symbol}/{symbol}-fundingRate-{month}.zip"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                # Funding rate CSV columns: calc_time, funding_interval_hours,
                # last_funding_rate, (sometimes more columns)
                df = pd.read_csv(f)
                return df
    except Exception as e:
        print(f"    Error fetching {symbol} {month}: {e}")
        return None


def process_funding_data(symbol: str) -> pd.DataFrame:
    """Download and process all monthly funding rate data for a symbol."""
    all_dfs = []

    for month in MONTHS:
        df = download_funding_csv(symbol, month)
        if df is not None:
            all_dfs.append(df)
            print(f"    {month}: {len(df)} records")
        else:
            print(f"    {month}: not available")

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)

    # Parse columns - the CSV structure varies, but typically:
    # Column 0: calc_time (timestamp ms) or sometimes symbol
    # Let's inspect and handle
    print(f"    Raw columns: {combined.columns.tolist()}, shape: {combined.shape}")
    print(f"    Sample row: {combined.iloc[0].tolist()}")

    return combined


def main():
    print("=" * 70)
    print("Binance Funding Rate Bulk Data Fetcher")
    print("=" * 70)
    print(f"Months: {MONTHS[0]} to {MONTHS[-1]}")
    print(f"Tokens: {len(TOKENS)}")
    print()

    # First, download one file to understand the format
    print("Step 1: Examining data format with BTCUSDT...")
    test_df = download_funding_csv("BTCUSDT", "2025-09")
    if test_df is not None:
        print(f"  Shape: {test_df.shape}")
        print(f"  Columns: {test_df.columns.tolist()}")
        print(f"  First 3 rows:")
        print(test_df.head(3).to_string())
    else:
        print("  Failed to download test file!")
        return

    # Now process all tokens
    print("\nStep 2: Downloading all tokens...")
    results = {}

    for i, symbol in enumerate(TOKENS):
        print(f"\n[{i+1}/{len(TOKENS)}] {symbol}")

        all_dfs = []
        for month in MONTHS:
            df = download_funding_csv(symbol, month)
            if df is not None:
                all_dfs.append(df)

        if not all_dfs:
            print(f"  No data available for {symbol}")
            results[symbol] = {"rows": 0, "months": 0}
            continue

        combined = pd.concat(all_dfs, ignore_index=True)

        # Columns are: calc_time, funding_interval_hours, last_funding_rate
        combined["timestamp"] = pd.to_datetime(combined["calc_time"].astype(float), unit="ms", utc=True)
        combined["fundingRate"] = combined["last_funding_rate"].astype(float)
        combined["symbol"] = symbol

        # Convert funding rate to synthetic L/S ratio
        # FR = 0.01% (0.0001) is "neutral" (Binance default)
        # FR > 0.0001: more longs -> ratio > 1
        # FR < 0.0001: more shorts -> ratio < 1
        #
        # Simple mapping: longShortRatio = 1 + (FR - 0.0001) * 1000
        # This maps:  FR=0.0001 -> ratio=1.0 (balanced)
        #             FR=0.001  -> ratio=1.9 (very long-heavy)
        #             FR=-0.001 -> ratio=0.1 (very short-heavy)
        #
        # More realistic: use sigmoid-like mapping
        # longShortRatio = exp(FR * 5000) / (1 + exp(FR * 5000)) * 2
        # Or simpler: longShortRatio = (1 + FR * 1000)
        combined["longShortRatio"] = (1 + combined["fundingRate"] * 1000).clip(0.1, 10)

        # Also compute long/short account percentages
        combined["longAccount"] = combined["longShortRatio"] / (1 + combined["longShortRatio"])
        combined["shortAccount"] = 1 - combined["longAccount"]

        # Funding rate is 8-hourly; resample to 1h by forward-filling
        combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        combined = combined.set_index("timestamp")

        # Resample to 1h and forward-fill
        hourly = combined[["fundingRate", "longShortRatio", "longAccount", "shortAccount"]].resample("1h").ffill()
        hourly = hourly.dropna()
        hourly["symbol"] = symbol
        hourly = hourly.reset_index()

        # Convert longAccount/shortAccount to strings for consistency with original data
        hourly["longShortRatio"] = hourly["longShortRatio"].round(4).astype(str)
        hourly["longAccount"] = hourly["longAccount"].round(4).astype(str)
        hourly["shortAccount"] = hourly["shortAccount"].round(4).astype(str)

        # Save
        output_path = os.path.join(OUTPUT_DIR, f"{symbol}_funding_rate.parquet")
        hourly[["timestamp", "symbol", "longShortRatio", "longAccount", "shortAccount", "fundingRate"]].to_parquet(
            output_path, index=False
        )

        date_range = hourly["timestamp"].max() - hourly["timestamp"].min()
        months = date_range.days / 30.44

        results[symbol] = {
            "rows": len(hourly),
            "start": str(hourly["timestamp"].min()),
            "end": str(hourly["timestamp"].max()),
            "months": round(months, 1),
        }
        print(f"  Saved {len(hourly)} hourly rows ({months:.1f} months)")

    # Summary
    print("\n" + "=" * 70)
    print("FUNDING RATE DATA SUMMARY")
    print("=" * 70)

    for symbol, info in results.items():
        if info["rows"] > 0:
            print(f"  {symbol}: {info['rows']:,} rows, {info['months']} months ({info['start']} to {info['end']})")
        else:
            print(f"  {symbol}: NO DATA")

    total_tokens = sum(1 for v in results.values() if v["rows"] > 0)
    print(f"\nTokens with data: {total_tokens}/{len(TOKENS)}")

    return results


if __name__ == "__main__":
    main()
