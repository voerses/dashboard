#!/workspace/venv/bin/python
"""
Fetch perpetual futures data from Kraken Futures via ccxt.

Data types:
  - funding: Historical funding rates (full history in one API call)
  - ohlcv:   1h OHLCV candles (paginated backwards, max 2000 per request)

Kraken has no historical open interest API, so OI is not fetched.

Usage:
  python fetch_kraken_perp.py                          # all data, auto-detect tokens
  python fetch_kraken_perp.py --tokens BTC,ETH,SOL     # specific tokens
  python fetch_kraken_perp.py --data-type funding       # funding only
  python fetch_kraken_perp.py --force                   # overwrite existing files
"""

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import ccxt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, "..")
DATA_DIR = os.path.join(PROJECT_DIR, "data", "perp", "kraken")
FUNDING_DIR = os.path.join(DATA_DIR, "funding")
OHLCV_DIR = os.path.join(DATA_DIR, "1h_ohlcv")

# OHLCV pagination: Kraken returns max 2000 candles per request.
OHLCV_BATCH_SIZE = 2000
OHLCV_TIMEFRAME = "1h"
OHLCV_INTERVAL_MS = 3600 * 1000  # 1 hour in milliseconds

# Be polite even though Kraken public endpoints are rate-limit free.
POLITE_DELAY_S = 0.25

# Thread pool workers for parallel fetching.
DEFAULT_WORKERS = 6

# Shared markets cache — loaded once, reused across all threads to avoid
# 277 redundant load_markets() API calls.
_shared_markets = None


# ---------------------------------------------------------------------------
# Exchange initialization
# ---------------------------------------------------------------------------
def _get_proxy():
    """Get HTTP proxy from environment if available."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
    if proxy:
        return {"https": proxy, "http": proxy}
    return {}

def create_exchange() -> ccxt.krakenfutures:
    """Create a krakenfutures exchange instance."""
    return ccxt.krakenfutures({
        "enableRateLimit": True,
        "proxies": _get_proxy(),
    })


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------
def discover_available_tokens(exchange: ccxt.krakenfutures) -> dict[str, str]:
    """
    Load ALL active USD perpetual swaps on Kraken Futures.
    Not limited to any specific token list — fetches everything liquid.

    Returns a dict mapping token -> ccxt symbol (e.g. "BTC" -> "BTC/USD:USD").
    """
    exchange.load_markets()
    available = {}
    for symbol, market in exchange.markets.items():
        if market.get("swap") and market.get("active", True) and market.get("settle") == "USD":
            base = market.get("base", symbol.split("/")[0])
            available[base] = symbol
    print(f"  Found {len(available)} active USD perpetual swaps on Kraken", flush=True)
    return available


# ---------------------------------------------------------------------------
# Funding rate fetching
# ---------------------------------------------------------------------------
def fetch_funding_for_token(token: str, symbol: str, force: bool = False) -> dict:
    """
    Fetch full funding rate history for a single token.

    Kraken returns the entire history in one API call.
    Returns a result dict with status info.
    """
    outpath = os.path.join(FUNDING_DIR, f"{token}_funding.csv")
    if os.path.exists(outpath) and not force:
        return {"token": token, "status": "skipped", "reason": "file exists", "rows": 0}

    try:
        ex = create_exchange()
        ex.markets = _shared_markets  # reuse cached markets instead of 277 API calls

        print(f"  [funding] {token}: fetching...", flush=True)
        rates = ex.fetch_funding_rate_history(symbol)

        if not rates:
            return {"token": token, "status": "empty", "reason": "no data returned", "rows": 0}

        # Write CSV
        os.makedirs(FUNDING_DIR, exist_ok=True)
        with open(outpath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "datetime", "funding_rate"])
            for r in rates:
                writer.writerow([
                    r["timestamp"],
                    r["datetime"],
                    r["fundingRate"],
                ])

        date_range = f"{rates[0]['datetime'][:10]} to {rates[-1]['datetime'][:10]}"
        print(f"  [funding] {token}: {len(rates)} rates ({date_range})", flush=True)
        return {"token": token, "status": "ok", "rows": len(rates), "range": date_range}

    except Exception as e:
        print(f"  [funding] {token}: ERROR - {e}", flush=True)
        return {"token": token, "status": "error", "reason": str(e), "rows": 0}


# ---------------------------------------------------------------------------
# OHLCV fetching (paginated)
# ---------------------------------------------------------------------------
def fetch_ohlcv_for_token(token: str, symbol: str, force: bool = False) -> dict:
    """
    Fetch all available 1h OHLCV history for a single token.

    Paginates backwards from now in chunks of 2000 candles.
    Returns a result dict with status info.
    """
    outpath = os.path.join(OHLCV_DIR, f"{token}_perp_1h.csv")
    if os.path.exists(outpath) and not force:
        return {"token": token, "status": "skipped", "reason": "file exists", "rows": 0}

    try:
        ex = create_exchange()
        ex.markets = _shared_markets  # reuse cached markets

        print(f"  [ohlcv]   {token}: fetching (paginating backwards)...", flush=True)

        all_candles = []
        now_ms = int(time.time() * 1000)

        # Start from the earliest reasonable date: 2018-01-01 (Kraken Futures launched mid-2018).
        earliest_ms = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        since_ms = earliest_ms
        page = 0

        while since_ms < now_ms:
            candles = ex.fetch_ohlcv(
                symbol,
                timeframe=OHLCV_TIMEFRAME,
                since=since_ms,
                limit=OHLCV_BATCH_SIZE,
            )

            if not candles:
                break

            all_candles.extend(candles)
            page += 1

            # The last candle's timestamp tells us where to continue from.
            last_ts = candles[-1][0]

            # Move since forward: start after the last candle.
            since_ms = last_ts + OHLCV_INTERVAL_MS

            if page % 5 == 0:
                last_date = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                print(f"  [ohlcv]   {token}: {len(all_candles)} candles so far (up to {last_date})...", flush=True)

            # If we got fewer candles than requested, we've reached the end.
            if len(candles) < OHLCV_BATCH_SIZE:
                break

            time.sleep(POLITE_DELAY_S)

        if not all_candles:
            return {"token": token, "status": "empty", "reason": "no data returned", "rows": 0}

        # Deduplicate by timestamp (in case of overlap at page boundaries).
        seen = set()
        unique_candles = []
        for c in all_candles:
            if c[0] not in seen:
                seen.add(c[0])
                unique_candles.append(c)
        unique_candles.sort(key=lambda c: c[0])

        # Write CSV
        os.makedirs(OHLCV_DIR, exist_ok=True)
        with open(outpath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for c in unique_candles:
                writer.writerow(c)

        first_date = datetime.fromtimestamp(unique_candles[0][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        last_date = datetime.fromtimestamp(unique_candles[-1][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        date_range = f"{first_date} to {last_date}"
        print(f"  [ohlcv]   {token}: {len(unique_candles)} candles ({date_range})", flush=True)
        return {"token": token, "status": "ok", "rows": len(unique_candles), "range": date_range}

    except Exception as e:
        print(f"  [ohlcv]   {token}: ERROR - {e}", flush=True)
        return {"token": token, "status": "error", "reason": str(e), "rows": 0}


# ---------------------------------------------------------------------------
# Parallel orchestration
# ---------------------------------------------------------------------------
def fetch_parallel(fetch_fn, tokens_map: dict[str, str], force: bool, workers: int) -> list[dict]:
    """Run fetch_fn across tokens in parallel using ThreadPoolExecutor."""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_fn, token, symbol, force): token
            for token, symbol in tokens_map.items()
        }
        for future in as_completed(futures):
            token = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"  [{token}] Unexpected error: {e}", flush=True)
                results.append({"token": token, "status": "error", "reason": str(e), "rows": 0})
    return results


# ---------------------------------------------------------------------------
# Summary printing
# ---------------------------------------------------------------------------
def print_summary(label: str, results: list[dict]):
    """Print a summary table for a batch of fetch results."""
    if not results:
        return

    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    empty = [r for r in results if r["status"] == "empty"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"\n{'=' * 60}")
    print(f"  {label} Summary")
    print(f"{'=' * 60}")
    print(f"  Fetched:  {len(ok):>3} tokens")
    print(f"  Skipped:  {len(skipped):>3} tokens (already exist)")
    print(f"  Empty:    {len(empty):>3} tokens (no data)")
    print(f"  Errors:   {len(errors):>3} tokens")

    if ok:
        total_rows = sum(r["rows"] for r in ok)
        print(f"  Total rows: {total_rows:,}")
        print(f"\n  Details:")
        for r in sorted(ok, key=lambda x: x["token"]):
            print(f"    {r['token']:>6}: {r['rows']:>8,} rows  ({r.get('range', 'n/a')})")

    if errors:
        print(f"\n  Errors:")
        for r in sorted(errors, key=lambda x: x["token"]):
            print(f"    {r['token']:>6}: {r.get('reason', 'unknown')}")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Kraken Futures perpetual data (funding rates + 1h OHLCV)."
    )
    parser.add_argument(
        "--tokens",
        type=str,
        default=None,
        help="Comma-separated list of tokens (e.g. BTC,ETH,SOL). "
             "Default: auto-detect from our 49 tokens.",
    )
    parser.add_argument(
        "--data-type",
        type=str,
        choices=["funding", "ohlcv", "all"],
        default="all",
        help="Which data to fetch: funding, ohlcv, or all (default: all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing data files.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel workers (default: {DEFAULT_WORKERS}).",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("  Kraken Futures Perpetual Data Fetcher")
    print("=" * 60)
    print(f"  Data type: {args.data_type}")
    print(f"  Workers:   {args.workers}")
    print(f"  Force:     {args.force}")
    print()

    # --- Discover available markets (load once, share across threads) ---
    global _shared_markets
    print("Loading Kraken Futures markets...", flush=True)
    exchange = create_exchange()
    available = discover_available_tokens(exchange)
    _shared_markets = exchange.markets

    print(f"Found {len(available)} perpetual swaps on Kraken Futures.")
    print(f"Available: {', '.join(sorted(available.keys()))}")
    print()

    # --- Filter to requested tokens ---
    if args.tokens:
        requested = [t.strip().upper() for t in args.tokens.split(",")]
        tokens_map = {}
        for t in requested:
            if t in available:
                tokens_map[t] = available[t]
            else:
                print(f"WARNING: {t} not available on Kraken Futures, skipping.")
        if not tokens_map:
            print("ERROR: No valid tokens to fetch. Exiting.")
            sys.exit(1)
    else:
        tokens_map = available

    print(f"\nWill fetch data for {len(tokens_map)} tokens: {', '.join(sorted(tokens_map.keys()))}")
    print()

    # --- Fetch data ---
    funding_results = []
    ohlcv_results = []

    if args.data_type in ("funding", "all"):
        print("-" * 60)
        print("Fetching funding rates...")
        print("-" * 60)
        funding_results = fetch_parallel(
            fetch_funding_for_token, tokens_map, args.force, args.workers
        )

    if args.data_type in ("ohlcv", "all"):
        print("-" * 60)
        print("Fetching 1h OHLCV candles...")
        print("-" * 60)
        ohlcv_results = fetch_parallel(
            fetch_ohlcv_for_token, tokens_map, args.force, args.workers
        )

    # --- Print summaries ---
    if funding_results:
        print_summary("Funding Rates", funding_results)
    if ohlcv_results:
        print_summary("1h OHLCV", ohlcv_results)

    # --- Final summary ---
    all_results = funding_results + ohlcv_results
    total_ok = sum(1 for r in all_results if r["status"] == "ok")
    total_err = sum(1 for r in all_results if r["status"] == "error")
    total_skip = sum(1 for r in all_results if r["status"] == "skipped")

    print("=" * 60)
    print(f"  DONE. Fetched: {total_ok} | Skipped: {total_skip} | Errors: {total_err}")
    print(f"  Data stored in: {os.path.abspath(DATA_DIR)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
