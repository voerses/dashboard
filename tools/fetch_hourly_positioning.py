#!/usr/bin/env python3
"""
Hourly Binance Positioning Data Fetcher
========================================
Lightweight scheduled script that fetches hourly positioning data from
Binance Futures API and appends to a growing parquet file. Designed to
run via cron every hour to accumulate the dataset needed for re-testing
hourly positioning signals over ~90 days.

Endpoints fetched (no auth required):
  1. topLongShortPositionRatio   - Top trader L/S position ratio
  2. globalLongShortAccountRatio - Global L/S account ratio
  3. topLongShortAccountRatio    - Top trader L/S account ratio
  4. takerlongshortRatio         - Taker buy/sell volume ratio

Usage:
    # Default: fetch BTC only
    python fetch_hourly_positioning.py

    # Multiple symbols
    python fetch_hourly_positioning.py --symbols BTC ETH SOL

    # Dry-run (fetch + print, don't save)
    python fetch_hourly_positioning.py --dry-run

Cron setup (add to crontab -e):
    0 * * * * /workspace/venv/bin/python /workspace/crypto_backtest/tools/fetch_hourly_positioning.py >> /tmp/hourly_positioning.log 2>&1
"""

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "alternative" / "binance_positioning_hourly"
BINANCE_FUTURES_BASE = "https://fapi.binance.com/futures/data"

ENDPOINTS = {
    "topLongShortPositionRatio": "top_ls_position_ratio",
    "globalLongShortAccountRatio": "global_ls_ratio",
    "topLongShortAccountRatio": "top_ls_account_ratio",
    "takerlongshortRatio": "taker_buy_sell_vol",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; CryptoBacktest/1.0)"
})

REQUEST_DELAY = 0.2  # 200ms between API calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(msg: str):
    """Print a timestamped log line."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


def safe_request(url, params=None, retries=3, backoff=2.0):
    """HTTP GET with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                log(f"  Rate limited (429). Sleeping {wait:.0f}s ...")
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                wait = 60
                log(f"  IP banned (418). Sleeping {wait}s ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                log(f"  Request error: {e}. Retrying in {wait:.0f}s ...")
                time.sleep(wait)
            else:
                raise
    return None


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
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def merge_with_existing(new_df, out_path):
    """Read existing parquet, append new rows, deduplicate by (symbol, endpoint, timestamp)."""
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            existing = coerce_dtypes(existing)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.drop_duplicates(
                subset=["symbol", "endpoint", "timestamp"], keep="last"
            )
            combined = combined.sort_values(
                ["symbol", "endpoint", "timestamp"]
            ).reset_index(drop=True)
            return combined
        except Exception as e:
            log(f"  Warning: Could not read existing file, overwriting: {e}")
    return new_df


# ---------------------------------------------------------------------------
# Main fetch logic
# ---------------------------------------------------------------------------
def fetch_one(symbol: str):
    """Fetch the latest 1h data point for a symbol across all 4 endpoints.

    Returns a list of DataFrames (one per endpoint), plus a list of errors.
    """
    frames = []
    errors = []

    for endpoint_name, label in ENDPOINTS.items():
        url = f"{BINANCE_FUTURES_BASE}/{endpoint_name}"
        params = {"symbol": symbol, "period": "1h", "limit": 1}

        try:
            resp = safe_request(url, params=params)
            if resp is None:
                errors.append((endpoint_name, "No response after retries"))
                continue

            data = resp.json()

            # Handle API errors (e.g., invalid symbol)
            if isinstance(data, dict) and "code" in data:
                errors.append((endpoint_name, data.get("msg", str(data))))
                continue

            if not data:
                errors.append((endpoint_name, "Empty response"))
                continue

            df = pd.DataFrame(data)
            df["symbol"] = symbol
            df["endpoint"] = label
            frames.append(df)

            log(f"  {symbol} / {label}: OK ({len(data)} row(s))")

        except Exception as e:
            errors.append((endpoint_name, str(e)))
            log(f"  {symbol} / {label}: ERROR - {e}")

        time.sleep(REQUEST_DELAY)

    return frames, errors


def main():
    parser = argparse.ArgumentParser(
        description="Fetch hourly Binance positioning data and append to parquet"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=["BTC"],
        help="Symbols to fetch (default: BTC). Appends USDT automatically."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and print data without saving to disk"
    )
    args = parser.parse_args()

    # Normalize symbols
    symbols = [s.upper() if s.upper().endswith("USDT") else s.upper() + "USDT"
               for s in args.symbols]

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    start = datetime.now(timezone.utc)
    log(f"Hourly Positioning Fetcher - start")
    log(f"Symbols: {symbols}")

    all_frames = []
    all_errors = []

    for symbol in symbols:
        frames, errors = fetch_one(symbol)
        all_frames.extend(frames)
        all_errors.extend([(symbol, ep, msg) for ep, msg in errors])

    if not all_frames:
        log("No data collected. Exiting.")
        if all_errors:
            for sym, ep, msg in all_errors:
                log(f"  ERROR {sym}/{ep}: {msg}")
        return 1

    combined = pd.concat(all_frames, ignore_index=True)
    combined = coerce_dtypes(combined)
    combined["fetched_at"] = start

    if args.dry_run:
        log("DRY RUN - data not saved:")
        print(combined.to_string(index=False))
        return 0

    # Per-symbol output files
    for symbol in symbols:
        symbol_df = combined[combined["symbol"] == symbol]
        if symbol_df.empty:
            continue

        # File name: btc_hourly.parquet, eth_hourly.parquet, etc.
        ticker = symbol.replace("USDT", "").lower()
        out_path = BASE_DIR / f"{ticker}_hourly.parquet"

        merged = merge_with_existing(symbol_df, out_path)
        merged.to_parquet(out_path, index=False)

        n_rows = len(merged)
        ts_range = merged["timestamp"]
        log(f"  Saved {out_path.name}: {n_rows} rows "
            f"({ts_range.min()} to {ts_range.max()})")

    # Summary
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log(f"Done in {elapsed:.1f}s. Errors: {len(all_errors)}")
    if all_errors:
        for sym, ep, msg in all_errors:
            log(f"  {sym}/{ep}: {msg}")

    return 0 if not all_errors else 1


if __name__ == "__main__":
    sys.exit(main())
