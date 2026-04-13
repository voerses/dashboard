#!/usr/bin/env python3
"""
TOTAL2/TOTAL3 Daily Updater
============================

Fetches TOTAL2 and TOTAL3 (crypto market cap indices) from TradingView
via tvdatafeed and appends to data/alternative/total2_total3.parquet.

No login required. Fetches last 30 bars and deduplicates.

Usage:
  python tools/fetch_total2_total3.py           # update parquet
  python tools/fetch_total2_total3.py --dry-run  # fetch + print only

Cron (run after midnight UTC):
  5 0 * * * /workspace/venv/bin/python /workspace/crypto_backtest/tools/fetch_total2_total3.py >> /tmp/total2_update.log 2>&1
"""

import argparse
import os
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARQUET_PATH = PROJECT_ROOT / "data" / "alternative" / "total2_total3.parquet"


def fetch():
    from tvDatafeed import TvDatafeed, Interval

    tv = TvDatafeed()

    total2 = tv.get_hist(symbol="TOTAL2", exchange="CRYPTOCAP", interval=Interval.in_daily, n_bars=30)
    total3 = tv.get_hist(symbol="TOTAL3", exchange="CRYPTOCAP", interval=Interval.in_daily, n_bars=30)

    if total2 is None or total3 is None:
        raise RuntimeError("Failed to fetch TOTAL2/TOTAL3 from TradingView")

    # Align indices
    common_idx = total2.index.intersection(total3.index)
    total2 = total2.loc[common_idx]
    total3 = total3.loc[common_idx]

    # Build dataframe matching parquet schema
    df = pd.DataFrame({
        "total2_open": total2["open"].values,
        "total2_high": total2["high"].values,
        "total2_low": total2["low"].values,
        "total2_close": total2["close"].values,
        "total2_volume": total2["volume"].values,
        "total3_open": total3["open"].values,
        "total3_high": total3["high"].values,
        "total3_low": total3["low"].values,
        "total3_close": total3["close"].values,
        "total3_volume": total3["volume"].values,
    }, index=common_idx)
    df.index.name = "datetime"

    # Remove timezone if present
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df


def main():
    parser = argparse.ArgumentParser(description="Fetch TOTAL2/TOTAL3 from TradingView")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Fetching TOTAL2/TOTAL3 from TradingView...")
    new_data = fetch()
    print(f"Fetched {len(new_data)} bars: {new_data.index[0]} to {new_data.index[-1]}")

    if args.dry_run:
        print(new_data.tail(3))
        return

    if PARQUET_PATH.exists():
        existing = pd.read_parquet(PARQUET_PATH)
        if existing.index.tz is not None:
            existing.index = existing.index.tz_localize(None)
        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()
    else:
        combined = new_data

    combined.to_parquet(PARQUET_PATH)
    print(f"Updated {PARQUET_PATH}: {len(combined)} total rows, latest: {combined.index[-1]}")


if __name__ == "__main__":
    main()
