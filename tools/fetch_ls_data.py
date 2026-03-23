#!/usr/bin/env python3
"""
Fetch historical Long/Short Account Ratio data for crypto perpetual futures.

Supports multiple data sources:
  - binance_vision: data.binance.vision bulk metrics (PRIMARY - best coverage)
  - bybit: Bybit v5 API (no auth required)
  - okx: OKX Rubik API (no auth required)

Usage:
    # Download from data.binance.vision (recommended)
    python fetch_ls_data.py --source binance_vision \
        --symbols BTCUSDT ETHUSDT SOLUSDT \
        --start-date 2024-01-01 --end-date 2026-03-22 \
        --output-dir ../data/ls_ratio

    # Download from Bybit API
    python fetch_ls_data.py --source bybit \
        --symbols BTCUSDT ETHUSDT SOLUSDT \
        --start-date 2022-01-01 --end-date 2026-03-22 \
        --output-dir ../data/ls_ratio

    # Download from OKX API
    python fetch_ls_data.py --source okx \
        --symbols BTC ETH SOL \
        --start-date 2025-01-01 --end-date 2026-03-22 \
        --output-dir ../data/ls_ratio

    # Aggregate 5-min data to daily
    python fetch_ls_data.py --source binance_vision \
        --symbols BTCUSDT --start-date 2024-01-01 --end-date 2024-12-31 \
        --output-dir ../data/ls_ratio --aggregate daily
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# data.binance.vision Metrics Fetcher
# ---------------------------------------------------------------------------

BINANCE_VISION_BASE = "https://data.binance.vision/data/futures/um/daily/metrics"


def fetch_binance_vision(symbols, start_date, end_date, output_dir, aggregate=None):
    """Download metrics from data.binance.vision and extract L/S ratio data."""
    os.makedirs(output_dir, exist_ok=True)

    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"Fetching {symbol} from data.binance.vision")
        print(f"Range: {start_date} to {end_date}")
        print(f"{'='*60}")

        all_rows = []
        current_date = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        failed_dates = []
        success_count = 0

        while current_date <= end_dt:
            date_str = current_date.strftime("%Y-%m-%d")
            url = f"{BINANCE_VISION_BASE}/{symbol}/{symbol}-metrics-{date_str}.zip"

            try:
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "crypto-backtest-fetcher/1.0")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    zip_data = resp.read()

                with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                    csv_name = zf.namelist()[0]
                    with zf.open(csv_name) as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                        seen_times = set()
                        for row in reader:
                            # Deduplicate (some early files have duplicate rows)
                            ts = row["create_time"]
                            if ts in seen_times:
                                continue
                            seen_times.add(ts)
                            all_rows.append(row)

                success_count += 1
                if success_count % 30 == 0:
                    print(f"  Downloaded {success_count} days... (current: {date_str})")

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    failed_dates.append(date_str)
                else:
                    print(f"  HTTP {e.code} for {date_str}: {e.reason}")
                    failed_dates.append(date_str)
            except Exception as e:
                print(f"  Error for {date_str}: {e}")
                failed_dates.append(date_str)

            current_date += timedelta(days=1)
            # Be polite - small delay between requests
            time.sleep(0.05)

        print(f"\n  Downloaded {success_count} days, {len(failed_dates)} failed/missing")
        if failed_dates and len(failed_dates) <= 10:
            print(f"  Missing dates: {', '.join(failed_dates)}")

        if not all_rows:
            print(f"  WARNING: No data found for {symbol}")
            continue

        # Sort by timestamp
        all_rows.sort(key=lambda r: r["create_time"])

        if aggregate == "daily":
            all_rows = _aggregate_to_daily(all_rows, symbol)
            granularity_suffix = "daily"
        elif aggregate == "hourly":
            all_rows = _aggregate_to_hourly(all_rows, symbol)
            granularity_suffix = "hourly"
        else:
            granularity_suffix = "5min"

        # Write output CSV
        output_file = os.path.join(
            output_dir, f"{symbol}_ls_ratio_binance_{granularity_suffix}.csv"
        )
        _write_standardized_csv(all_rows, output_file, source="binance_vision")

        print(f"  Wrote {len(all_rows)} rows to {output_file}")
        print(f"  Date range: {all_rows[0]['create_time']} to {all_rows[-1]['create_time']}")


def _aggregate_to_daily(rows, symbol):
    """Aggregate 5-min data to daily using mean of ratios."""
    from collections import defaultdict

    daily = defaultdict(list)
    for row in rows:
        date = row["create_time"][:10]  # YYYY-MM-DD
        daily[date].append(row)

    result = []
    for date in sorted(daily.keys()):
        day_rows = daily[date]
        n = len(day_rows)

        avg_ls = sum(float(r["count_long_short_ratio"]) for r in day_rows) / n
        avg_top_acct = sum(float(r["count_toptrader_long_short_ratio"]) for r in day_rows) / n
        avg_top_pos = sum(float(r["sum_toptrader_long_short_ratio"]) for r in day_rows) / n
        avg_taker = sum(float(r["sum_taker_long_short_vol_ratio"]) for r in day_rows) / n
        avg_oi = sum(float(r["sum_open_interest"]) for r in day_rows) / n
        avg_oi_val = sum(float(r["sum_open_interest_value"]) for r in day_rows) / n

        result.append({
            "create_time": f"{date} 00:00:00",
            "symbol": symbol,
            "sum_open_interest": f"{avg_oi:.8f}",
            "sum_open_interest_value": f"{avg_oi_val:.8f}",
            "count_toptrader_long_short_ratio": f"{avg_top_acct:.8f}",
            "sum_toptrader_long_short_ratio": f"{avg_top_pos:.8f}",
            "count_long_short_ratio": f"{avg_ls:.8f}",
            "sum_taker_long_short_vol_ratio": f"{avg_taker:.8f}",
        })

    return result


def _aggregate_to_hourly(rows, symbol):
    """Aggregate 5-min data to hourly using mean of ratios."""
    from collections import defaultdict

    hourly = defaultdict(list)
    for row in rows:
        hour_key = row["create_time"][:13]  # YYYY-MM-DD HH
        hourly[hour_key].append(row)

    result = []
    for hour_key in sorted(hourly.keys()):
        hour_rows = hourly[hour_key]
        n = len(hour_rows)

        avg_ls = sum(float(r["count_long_short_ratio"]) for r in hour_rows) / n
        avg_top_acct = sum(float(r["count_toptrader_long_short_ratio"]) for r in hour_rows) / n
        avg_top_pos = sum(float(r["sum_toptrader_long_short_ratio"]) for r in hour_rows) / n
        avg_taker = sum(float(r["sum_taker_long_short_vol_ratio"]) for r in hour_rows) / n
        avg_oi = sum(float(r["sum_open_interest"]) for r in hour_rows) / n
        avg_oi_val = sum(float(r["sum_open_interest_value"]) for r in hour_rows) / n

        result.append({
            "create_time": f"{hour_key}:00:00",
            "symbol": symbol,
            "sum_open_interest": f"{avg_oi:.8f}",
            "sum_open_interest_value": f"{avg_oi_val:.8f}",
            "count_toptrader_long_short_ratio": f"{avg_top_acct:.8f}",
            "sum_toptrader_long_short_ratio": f"{avg_top_pos:.8f}",
            "count_long_short_ratio": f"{avg_ls:.8f}",
            "sum_taker_long_short_vol_ratio": f"{avg_taker:.8f}",
        })

    return result


def _write_standardized_csv(rows, output_file, source="binance_vision"):
    """Write rows to a standardized CSV format."""
    if source == "binance_vision":
        fieldnames = [
            "create_time",
            "symbol",
            "count_long_short_ratio",
            "count_toptrader_long_short_ratio",
            "sum_toptrader_long_short_ratio",
            "sum_taker_long_short_vol_ratio",
            "sum_open_interest",
            "sum_open_interest_value",
        ]
    elif source == "bybit":
        fieldnames = [
            "timestamp",
            "symbol",
            "buy_ratio",
            "sell_ratio",
            "ls_ratio",
        ]
    elif source == "okx":
        fieldnames = [
            "timestamp",
            "symbol",
            "ls_ratio",
        ]
    else:
        fieldnames = list(rows[0].keys()) if rows else []

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Bybit API Fetcher
# ---------------------------------------------------------------------------

BYBIT_BASE = "https://api.bybit.com/v5/market/account-ratio"


def fetch_bybit(symbols, start_date, end_date, output_dir, period="1d"):
    """Fetch L/S ratio data from Bybit API."""
    os.makedirs(output_dir, exist_ok=True)

    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"Fetching {symbol} from Bybit API")
        print(f"Range: {start_date} to {end_date}, period={period}")
        print(f"{'='*60}")

        all_rows = []
        # Bybit returns newest first, paginate using cursor
        # We'll use startTime/endTime windows
        current_end = end_ts
        page = 0

        while True:
            params = {
                "category": "linear",
                "symbol": symbol,
                "period": period,
                "limit": 500,
                "startTime": start_ts,
                "endTime": current_end,
            }
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{BYBIT_BASE}?{query}"

            try:
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "crypto-backtest-fetcher/1.0")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())

                if data["retCode"] != 0:
                    print(f"  API error: {data['retMsg']}")
                    break

                items = data["result"]["list"]
                if not items:
                    break

                for item in items:
                    ts = int(item["timestamp"])
                    buy_r = float(item["buyRatio"])
                    sell_r = float(item["sellRatio"])
                    ls_ratio = buy_r / sell_r if sell_r > 0 else 0

                    all_rows.append({
                        "timestamp": datetime.utcfromtimestamp(ts / 1000).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "symbol": symbol,
                        "buy_ratio": f"{buy_r:.4f}",
                        "sell_ratio": f"{sell_r:.4f}",
                        "ls_ratio": f"{ls_ratio:.8f}",
                    })

                # Get oldest timestamp from this batch for next page
                oldest_ts = min(int(item["timestamp"]) for item in items)

                cursor = data["result"].get("nextPageCursor", "")
                if not cursor or oldest_ts <= start_ts:
                    break

                current_end = oldest_ts
                page += 1
                if page % 5 == 0:
                    print(f"  Page {page}, {len(all_rows)} rows so far...")

                # Rate limit: be polite
                time.sleep(0.15)

            except Exception as e:
                print(f"  Error on page {page}: {e}")
                break

        if not all_rows:
            print(f"  WARNING: No data found for {symbol}")
            continue

        # Sort by timestamp (oldest first)
        all_rows.sort(key=lambda r: r["timestamp"])

        # Deduplicate
        seen = set()
        deduped = []
        for row in all_rows:
            if row["timestamp"] not in seen:
                seen.add(row["timestamp"])
                deduped.append(row)
        all_rows = deduped

        output_file = os.path.join(
            output_dir, f"{symbol}_ls_ratio_bybit_{period}.csv"
        )
        _write_standardized_csv(all_rows, output_file, source="bybit")

        print(f"  Wrote {len(all_rows)} rows to {output_file}")
        print(f"  Date range: {all_rows[0]['timestamp']} to {all_rows[-1]['timestamp']}")


# ---------------------------------------------------------------------------
# OKX API Fetcher
# ---------------------------------------------------------------------------

OKX_CURRENCY_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
OKX_CONTRACT_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract"


def fetch_okx(symbols, start_date, end_date, output_dir, period="1D", endpoint="contract"):
    """
    Fetch L/S ratio data from OKX API.

    For endpoint='currency', symbols should be currency codes like BTC, ETH, SOL.
    For endpoint='contract', symbols should be instrument IDs like BTC-USDT-SWAP.
    If USDT symbols are passed (e.g., BTCUSDT), they'll be auto-converted.
    """
    os.makedirs(output_dir, exist_ok=True)

    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    for raw_symbol in symbols:
        # Auto-convert symbol formats
        if endpoint == "contract":
            if raw_symbol.endswith("USDT"):
                inst_id = raw_symbol.replace("USDT", "-USDT-SWAP")
            elif "-SWAP" in raw_symbol:
                inst_id = raw_symbol
            else:
                inst_id = f"{raw_symbol}-USDT-SWAP"
            display_symbol = inst_id
        else:
            # Currency-level: extract base currency
            ccy = raw_symbol.replace("USDT", "").replace("-USDT-SWAP", "")
            display_symbol = ccy

        print(f"\n{'='*60}")
        print(f"Fetching {display_symbol} from OKX API ({endpoint})")
        print(f"Range: {start_date} to {end_date}, period={period}")
        print(f"{'='*60}")

        all_rows = []
        current_end = end_ts + 86400000  # Start from slightly after end_date

        while True:
            if endpoint == "contract":
                url = f"{OKX_CONTRACT_URL}?instId={inst_id}&period={period}&end={current_end}"
            else:
                url = f"{OKX_CURRENCY_URL}?ccy={ccy}&period={period}"

            try:
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "crypto-backtest-fetcher/1.0")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())

                if data["code"] != "0":
                    print(f"  API error: code={data['code']}, msg={data.get('msg','')}")
                    break

                items = data["data"]
                if not items:
                    break

                for item in items:
                    ts = int(item[0])
                    ratio = float(item[1])

                    if ts < start_ts:
                        continue

                    all_rows.append({
                        "timestamp": datetime.utcfromtimestamp(ts / 1000).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "symbol": display_symbol,
                        "ls_ratio": f"{ratio:.8f}",
                    })

                oldest_ts = min(int(item[0]) for item in items)

                # For currency-level, no pagination possible
                if endpoint == "currency":
                    break

                # Stop if we've gone past start_date
                if oldest_ts <= start_ts:
                    break

                current_end = oldest_ts
                time.sleep(0.15)

            except Exception as e:
                print(f"  Error: {e}")
                break

        if not all_rows:
            print(f"  WARNING: No data found for {display_symbol}")
            continue

        # Sort by timestamp (oldest first)
        all_rows.sort(key=lambda r: r["timestamp"])

        # Deduplicate
        seen = set()
        deduped = []
        for row in all_rows:
            if row["timestamp"] not in seen:
                seen.add(row["timestamp"])
                deduped.append(row)
        all_rows = deduped

        safe_symbol = raw_symbol.replace("-", "_")
        output_file = os.path.join(
            output_dir, f"{safe_symbol}_ls_ratio_okx_{endpoint}_{period}.csv"
        )
        _write_standardized_csv(all_rows, output_file, source="okx")

        print(f"  Wrote {len(all_rows)} rows to {output_file}")
        print(f"  Date range: {all_rows[0]['timestamp']} to {all_rows[-1]['timestamp']}")


# ---------------------------------------------------------------------------
# Symbol discovery
# ---------------------------------------------------------------------------

def list_binance_vision_symbols():
    """List all available symbols in data.binance.vision metrics."""
    import re

    url = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/daily/metrics/&delimiter=/&max-keys=1000"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "crypto-backtest-fetcher/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode()

    prefixes = re.findall(
        r"<Prefix>data/futures/um/daily/metrics/([^/]+)/</Prefix>", content
    )
    return sorted(prefixes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "NEARUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "TRXUSDT",
    "FILUSDT",
    "ARBUSDT",
    "OPUSDT",
    "INJUSDT",
    "APTUSDT",
]


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical Long/Short Account Ratio data for crypto perpetual futures"
    )
    parser.add_argument(
        "--source",
        choices=["binance_vision", "bybit", "okx"],
        default="binance_vision",
        help="Data source (default: binance_vision)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to fetch (default: top 20 tokens)",
    )
    parser.add_argument(
        "--start-date",
        default="2024-01-01",
        help="Start date YYYY-MM-DD (default: 2024-01-01)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--output-dir",
        default="../data/ls_ratio",
        help="Output directory (default: ../data/ls_ratio)",
    )
    parser.add_argument(
        "--aggregate",
        choices=["daily", "hourly", None],
        default=None,
        help="Aggregate 5-min data (binance_vision only)",
    )
    parser.add_argument(
        "--period",
        default=None,
        help="Data period for API sources (bybit: 5min/15min/30min/1h/4h/1d, okx: 5m/1H/1D)",
    )
    parser.add_argument(
        "--okx-endpoint",
        choices=["currency", "contract"],
        default="contract",
        help="OKX endpoint type (default: contract)",
    )
    parser.add_argument(
        "--list-symbols",
        action="store_true",
        help="List all available symbols on data.binance.vision and exit",
    )

    args = parser.parse_args()

    if args.list_symbols:
        print("Fetching symbol list from data.binance.vision...")
        symbols = list_binance_vision_symbols()
        print(f"\nFound {len(symbols)} symbols:\n")
        for s in symbols:
            print(f"  {s}")
        return

    symbols = args.symbols or DEFAULT_SYMBOLS

    if args.end_date is None:
        args.end_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"Source: {args.source}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Date range: {args.start_date} to {args.end_date}")
    print(f"Output: {args.output_dir}")

    if args.source == "binance_vision":
        fetch_binance_vision(
            symbols,
            args.start_date,
            args.end_date,
            args.output_dir,
            aggregate=args.aggregate,
        )

    elif args.source == "bybit":
        period = args.period or "1d"
        fetch_bybit(symbols, args.start_date, args.end_date, args.output_dir, period=period)

    elif args.source == "okx":
        period = args.period or "1D"
        fetch_okx(
            symbols,
            args.start_date,
            args.end_date,
            args.output_dir,
            period=period,
            endpoint=args.okx_endpoint,
        )

    print("\nDone!")


if __name__ == "__main__":
    main()
