#!/usr/bin/env python3
"""
Daily Metrics Updater for s523 Paper Trading
=============================================

Fetches yesterday's 5-min metrics from data.binance.vision for all tokens,
aggregates to daily (last value), and appends to:
  data/alternative/binance_metrics/all_symbols_daily_ls.parquet

Run this once per day (e.g., via cron at 01:00 UTC):
  python tools/update_daily_metrics.py

It's idempotent — re-running won't duplicate data.
"""

import io
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAILY_PARQUET = PROJECT_ROOT / "data" / "alternative" / "binance_metrics" / "all_symbols_daily_ls.parquet"
FIVEMIN_DIR = PROJECT_ROOT / "data" / "alternative" / "binance_metrics" / "5min"

# data.binance.vision metrics URL pattern
VISION_URL = "https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "CryptoBacktest/1.0"})


def get_symbols_to_update():
    """Get list of symbols from existing 5-min parquet files."""
    if FIVEMIN_DIR.exists():
        return sorted(f.stem.replace("_5min", "") for f in FIVEMIN_DIR.glob("*_5min.parquet"))
    # Fallback: use daily parquet
    if DAILY_PARQUET.exists():
        df = pd.read_parquet(DAILY_PARQUET, columns=["symbol"])
        return sorted(df["symbol"].unique())
    return []


def fetch_day_metrics(symbol: str, date_str: str) -> pd.DataFrame | None:
    """Fetch one day's metrics ZIP from data.binance.vision."""
    url = VISION_URL.format(symbol=symbol, date=date_str)
    try:
        resp = SESSION.get(url, timeout=30)
        if resp.status_code == 404:
            return None  # no data for this symbol/date
        resp.raise_for_status()
    except requests.RequestException:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
        return df
    except Exception:
        return None


def aggregate_to_daily(df_5min: pd.DataFrame, symbol: str, date_str: str) -> dict | None:
    """Aggregate 5-min metrics to one daily row (last value)."""
    if df_5min is None or len(df_5min) == 0:
        return None

    # Take last row (end of day)
    last = df_5min.iloc[-1]

    return {
        "count_toptrader_ls_ratio": last.get("count_toptrader_long_short_ratio", None),
        "sum_toptrader_ls_ratio": last.get("sum_toptrader_long_short_ratio", None),
        "count_ls_ratio": last.get("count_long_short_ratio", None),
        "taker_buy_sell_ratio": last.get("sum_taker_long_short_vol_ratio", None),
        "count_ls_ratio_max": df_5min["count_long_short_ratio"].max() if "count_long_short_ratio" in df_5min else None,
        "count_ls_ratio_min": df_5min["count_long_short_ratio"].min() if "count_long_short_ratio" in df_5min else None,
        "taker_ratio_max": df_5min["sum_taker_long_short_vol_ratio"].max() if "sum_taker_long_short_vol_ratio" in df_5min else None,
        "taker_ratio_min": df_5min["sum_taker_long_short_vol_ratio"].min() if "sum_taker_long_short_vol_ratio" in df_5min else None,
        "sum_open_interest": last.get("sum_open_interest", None),
        "sum_open_interest_value": last.get("sum_open_interest_value", None),
        "n_records": len(df_5min),
        "date": pd.Timestamp(date_str),
        "symbol": symbol,
    }


def update_5min_parquet(symbol: str, df_5min: pd.DataFrame, date_str: str):
    """Append today's 5-min data to the per-symbol 5-min parquet."""
    if df_5min is None or len(df_5min) == 0:
        return

    # Normalize columns
    df_5min = df_5min.rename(columns={"create_time": "create_time"})
    if "create_time" in df_5min.columns:
        df_5min["create_time"] = pd.to_datetime(df_5min["create_time"])

    fivemin_path = FIVEMIN_DIR / f"{symbol}_5min.parquet"
    if fivemin_path.exists():
        existing = pd.read_parquet(fivemin_path)
        existing["create_time"] = pd.to_datetime(existing["create_time"])
        combined = pd.concat([existing, df_5min], ignore_index=True)
        combined = combined.drop_duplicates(subset=["create_time"], keep="last")
        combined = combined.sort_values("create_time").reset_index(drop=True)
    else:
        combined = df_5min.sort_values("create_time").reset_index(drop=True)

    combined.to_parquet(fivemin_path, index=False)


def main():
    # Determine which date to fetch (yesterday UTC)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    # Check if already up to date
    if DAILY_PARQUET.exists():
        df_existing = pd.read_parquet(DAILY_PARQUET)
        df_existing["date"] = pd.to_datetime(df_existing["date"])
        latest = df_existing["date"].max().strftime("%Y-%m-%d")
        if latest >= yesterday:
            print(f"Already up to date (latest: {latest})")
            return
        print(f"Last date in parquet: {latest}. Fetching {yesterday}.")
    else:
        df_existing = pd.DataFrame()
        print(f"No existing parquet. Fetching {yesterday}.")

    symbols = get_symbols_to_update()
    print(f"Updating {len(symbols)} symbols for {yesterday}")

    new_rows = []
    updated_5min = 0
    for i, symbol in enumerate(symbols):
        df_5min = fetch_day_metrics(symbol, yesterday)
        if df_5min is not None:
            row = aggregate_to_daily(df_5min, symbol, yesterday)
            if row:
                new_rows.append(row)
            # Also update 5-min parquet
            update_5min_parquet(symbol, df_5min, yesterday)
            updated_5min += 1

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(symbols)} fetched ({len(new_rows)} with data)")
        time.sleep(0.05)  # gentle rate limiting

    if not new_rows:
        print("No new data fetched.")
        return

    df_new = pd.DataFrame(new_rows)
    if len(df_existing) > 0:
        combined = pd.concat([df_existing, df_new], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "symbol"], keep="last")
    else:
        combined = df_new

    combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)
    combined.to_parquet(DAILY_PARQUET, index=False)

    print(f"Done. Added {len(new_rows)} rows for {yesterday}.")
    print(f"Updated {updated_5min} 5-min parquets.")
    print(f"Daily parquet: {len(combined)} total rows, {combined['symbol'].nunique()} symbols")


if __name__ == "__main__":
    main()
