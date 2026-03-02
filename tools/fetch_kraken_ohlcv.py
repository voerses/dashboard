#!/workspace/venv/bin/python
"""Fetch 1h OHLCV candles from Kraken Futures charts API.

ccxt's fetch_ohlcv returns empty for Kraken Futures perps — this script
uses the native charts API directly which works fine.

Endpoint: GET https://futures.kraken.com/api/charts/v1/trade/{symbol}/1h
  - Public, rate-limit free
  - Returns candles with {time, open, high, low, close, volume}
  - Paginate with ?from=&to= (epoch seconds)
"""

import csv
import os
import sys
import time
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OHLCV_DIR = os.path.join(SCRIPT_DIR, "..", "data", "perp", "kraken", "1h_ohlcv")
FUNDING_DIR = os.path.join(SCRIPT_DIR, "..", "data", "perp", "kraken", "funding")

CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"
TICKERS_URL = "https://futures.kraken.com/derivatives/api/v3/tickers"

# Each page can cover ~720 candles (30 days). We'll request larger windows.
HOURS_PER_PAGE = 720
POLITE_DELAY = 0.1  # rate-limit free, but be polite

_thread_local = threading.local()

def _get_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session()
    return _thread_local.session


def discover_perp_symbols():
    """Get all active perpetual symbols from Kraken tickers."""
    session = _get_session()
    resp = session.get(TICKERS_URL, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    symbols = {}
    for t in data.get("tickers", []):
        sym = t.get("symbol", "")
        tag = t.get("tag", "")
        if tag == "perpetual" and sym.startswith("PF_") and sym.endswith("USD"):
            # Extract base: PF_XBTUSD -> BTC, PF_ETHUSD -> ETH
            base = sym[3:-3]  # strip PF_ and USD
            # Kraken uses XBT for BTC
            if base == "XBT":
                base = "BTC"
            symbols[base] = sym
    return symbols


def fetch_ohlcv_for_token(base: str, symbol: str, force: bool = False):
    """Fetch all 1h OHLCV for a single token via charts API."""
    outpath = os.path.join(OHLCV_DIR, f"{base}_perp_1h.csv")
    if os.path.exists(outpath) and not force:
        return {"token": base, "status": "skipped", "rows": 0}

    session = _get_session()
    all_candles = []

    # Start from 2018-01-01 (Kraken Futures launched mid-2018)
    from_ts = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())
    now_ts = int(time.time())

    page = 0
    while from_ts < now_ts:
        to_ts = min(from_ts + HOURS_PER_PAGE * 3600, now_ts)
        url = f"{CHARTS_BASE}/{symbol}/1h?from={from_ts}&to={to_ts}"

        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [ohlcv] {base}: error at page {page}: {e}", flush=True)
            break

        candles = data.get("candles", [])
        if candles:
            all_candles.extend(candles)

        page += 1
        from_ts = to_ts + 1  # next window

        if page % 20 == 0:
            print(f"  [ohlcv] {base}: {len(all_candles)} candles (page {page})...", flush=True)

        time.sleep(POLITE_DELAY)

    if not all_candles:
        return {"token": base, "status": "empty", "rows": 0}

    # Deduplicate by time
    seen = set()
    unique = []
    for c in all_candles:
        t = c["time"]
        if t not in seen:
            seen.add(t)
            unique.append(c)
    unique.sort(key=lambda c: c["time"])

    # Write CSV
    os.makedirs(OHLCV_DIR, exist_ok=True)
    with open(outpath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in unique:
            writer.writerow([c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"]])

    first = datetime.fromtimestamp(unique[0]["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last = datetime.fromtimestamp(unique[-1]["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"  [ohlcv] {base}: {len(unique)} candles ({first} to {last})", flush=True)
    return {"token": base, "status": "ok", "rows": len(unique), "range": f"{first} to {last}"}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch Kraken Futures 1h OHLCV via charts API")
    parser.add_argument("--tokens", type=str, default=None, help="Comma-separated tokens")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    print("Discovering Kraken Futures perpetual symbols...", flush=True)
    symbols = discover_perp_symbols()
    print(f"Found {len(symbols)} perpetuals", flush=True)

    if args.tokens:
        requested = [t.strip().upper() for t in args.tokens.split(",")]
        symbols = {k: v for k, v in symbols.items() if k in requested}

    print(f"Fetching 1h OHLCV for {len(symbols)} tokens with {args.workers} workers", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_ohlcv_for_token, base, sym, args.force): base
            for base, sym in symbols.items()
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                base = futures[future]
                print(f"  [ohlcv] {base}: FAILED - {e}", flush=True)
                results.append({"token": base, "status": "error", "rows": 0})

    # Summary
    ok = [r for r in results if r["status"] == "ok"]
    empty = [r for r in results if r["status"] == "empty"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"\n{'='*60}")
    print(f"Kraken OHLCV Summary")
    print(f"{'='*60}")
    print(f"  Fetched: {len(ok)}, Skipped: {len(skipped)}, Empty: {len(empty)}, Errors: {len(errors)}")
    total_rows = sum(r["rows"] for r in ok)
    print(f"  Total rows: {total_rows:,}")
    if ok:
        for r in sorted(ok, key=lambda x: x["token"]):
            print(f"    {r['token']:>8}: {r['rows']:>8,} candles  ({r.get('range','')})")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
