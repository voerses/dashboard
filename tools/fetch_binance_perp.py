#!/workspace/venv/bin/python
"""
Fetch perpetual futures data from Binance USDT-M for high-liquidity tokens.

Fetches three data types:
  - Funding rates (8h intervals)
  - 1h OHLCV candles
  - Open interest history (5m aggregated — Binance only supports 5m for OI)

Data is saved as CSV files under data/perp/binance/{funding,1h_ohlcv,oi}/.

Usage:
    python fetch_binance_perp.py                          # all liquid tokens, all data types
    python fetch_binance_perp.py --tokens BTC ETH SOL     # specific tokens
    python fetch_binance_perp.py --data-type funding       # only funding rates
    python fetch_binance_perp.py --force                   # re-fetch even if files exist
    python fetch_binance_perp.py --workers 8               # increase parallelism
"""

import argparse
import csv
import os
import sys
import time
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import ccxt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "perp", "binance")
FUNDING_DIR = os.path.join(BASE_DIR, "funding")
OHLCV_DIR = os.path.join(BASE_DIR, "1h_ohlcv")
OI_DIR = os.path.join(BASE_DIR, "oi")

HISTORY_START_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
MIN_24H_VOLUME_USD = 10_000_000  # $10M — easily handles $5K trades

# Rate limit / retry settings
MAX_RETRIES = 5
BASE_BACKOFF_S = 1.0

# ---------------------------------------------------------------------------
# Global rate limiter
# ---------------------------------------------------------------------------
# Binance USDT-M futures: 2400 weight/min. Most endpoints = 1-5 weight.
# Budget: ~40 weight/sec. With avg 5 weight/request → max ~8 req/s globally.
# We target 6 req/s (= 0.167s between requests) to stay at ~75% budget.
_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.167  # seconds between ANY request across all threads

def global_rate_limit():
    """Block until enough time has passed since the last global request."""
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


# ---------------------------------------------------------------------------
# Exchange factory (one per thread — ccxt objects are not thread-safe)
# ---------------------------------------------------------------------------
# Shared markets cache — loaded once, reused across threads.
_shared_markets = None

def _get_proxy():
    """Get HTTP proxy from environment if available."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
    if proxy:
        return {"https": proxy, "http": proxy}
    return {}

def make_exchange(load_markets=False):
    """Create a fresh binanceusdm instance for use in one thread."""
    ex = ccxt.binanceusdm({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
        "proxies": _get_proxy(),
    })
    if load_markets:
        ex.load_markets()
    elif _shared_markets is not None:
        ex.markets = _shared_markets
        ex.markets_by_id = {}  # ccxt rebuilds this as needed
    return ex


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------
def retry_call(fn, *args, max_retries=MAX_RETRIES, **kwargs):
    """Call fn with global rate limiting + exponential backoff on errors."""
    for attempt in range(max_retries):
        global_rate_limit()  # enforce cross-thread rate limit before every call
        try:
            return fn(*args, **kwargs)
        except (ccxt.RateLimitExceeded, ccxt.DDoSProtection) as e:
            wait = BASE_BACKOFF_S * (2 ** attempt)
            print(f"    Rate limited (attempt {attempt + 1}/{max_retries}), waiting {wait:.1f}s ...", flush=True)
            time.sleep(wait)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            wait = BASE_BACKOFF_S * (2 ** attempt)
            print(f"    Network error (attempt {attempt + 1}/{max_retries}): {e}, retrying in {wait:.1f}s ...", flush=True)
            time.sleep(wait)
        except ccxt.ExchangeError as e:
            # Some exchange errors are not retryable (e.g., invalid symbol)
            err_str = str(e).lower()
            if "rate" in err_str or "limit" in err_str or "too many" in err_str:
                wait = BASE_BACKOFF_S * (2 ** attempt)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed after {max_retries} retries")


# ---------------------------------------------------------------------------
# Token discovery
# ---------------------------------------------------------------------------
def discover_liquid_tokens(exchange):
    """Load ALL USDT-M perps, filter for 24h volume > threshold. Not limited to any token list."""
    print("Loading Binance USDT-M markets ...", flush=True)
    exchange.load_markets()

    # Get all active USDT-M perpetual swaps
    perp_symbols = []
    for symbol, market in exchange.markets.items():
        if (market.get("swap") and market.get("active", True)
                and market.get("settle") == "USDT" and market.get("linear")):
            perp_symbols.append(symbol)
    print(f"  Found {len(perp_symbols)} active USDT-M perps total", flush=True)

    # Fetch all tickers at once (one API call) and filter by volume
    print("  Fetching 24h volumes ...", flush=True)
    tickers = retry_call(exchange.fetch_tickers, perp_symbols)

    liquid = []
    for symbol, ticker in sorted(tickers.items()):
        vol_usd = ticker.get("quoteVolume") or 0
        base = ticker.get("base") or symbol.split("/")[0]
        if vol_usd >= MIN_24H_VOLUME_USD:
            liquid.append(base)
            print(f"  {base}: 24h vol ${vol_usd / 1e6:.1f}M -- OK", flush=True)

    print(f"\nFound {len(liquid)} liquid tokens (>= ${MIN_24H_VOLUME_USD / 1e6:.0f}M 24h vol)", flush=True)
    return sorted(set(liquid))


# ---------------------------------------------------------------------------
# Data fetching — Funding Rates
# ---------------------------------------------------------------------------
def fetch_funding_rates(token, force=False):
    """Fetch all funding rate history for a token. Returns (token, row_count) or raises."""
    outpath = os.path.join(FUNDING_DIR, f"{token}_funding.csv")
    if os.path.exists(outpath) and not force:
        existing_rows = _count_csv_rows(outpath)
        print(f"  [{token}] funding: already exists ({existing_rows} rows), skipping", flush=True)
        return token, existing_rows, True  # skipped

    ex = make_exchange()  # uses cached markets
    symbol = f"{token}/USDT:USDT"
    print(f"  [{token}] funding: fetching from {symbol} ...", flush=True)

    all_rows = []
    since = HISTORY_START_MS
    now_ms = int(time.time() * 1000)

    while since < now_ms:
        batch = retry_call(ex.fetch_funding_rate_history, symbol, since=since, limit=1000)
        if not batch:
            break
        for entry in batch:
            all_rows.append({
                "timestamp": entry["timestamp"],
                "datetime": entry.get("datetime", ""),
                "funding_rate": entry.get("fundingRate"),
            })
        last_ts = batch[-1]["timestamp"]
        if last_ts <= since:
            break
        since = last_ts + 1
        # rate limiting handled by global_rate_limit() in retry_call

    if not all_rows:
        print(f"  [{token}] funding: no data returned", flush=True)
        return token, 0, False

    _write_csv(outpath, all_rows, ["timestamp", "datetime", "funding_rate"])
    print(f"  [{token}] funding: {len(all_rows)} rows saved", flush=True)
    return token, len(all_rows), False


# ---------------------------------------------------------------------------
# Data fetching — 1h OHLCV
# ---------------------------------------------------------------------------
def fetch_ohlcv(token, force=False):
    """Fetch all 1h OHLCV candles for a token. Returns (token, row_count)."""
    outpath = os.path.join(OHLCV_DIR, f"{token}_perp_1h.csv")
    if os.path.exists(outpath) and not force:
        existing_rows = _count_csv_rows(outpath)
        print(f"  [{token}] ohlcv: already exists ({existing_rows} rows), skipping", flush=True)
        return token, existing_rows, True

    ex = make_exchange()  # uses cached markets
    symbol = f"{token}/USDT:USDT"
    print(f"  [{token}] ohlcv: fetching from {symbol} ...", flush=True)

    all_rows = []
    since = HISTORY_START_MS
    now_ms = int(time.time() * 1000)

    while since < now_ms:
        batch = retry_call(ex.fetch_ohlcv, symbol, timeframe="1h", since=since, limit=1500)
        if not batch:
            break
        for candle in batch:
            ts, o, h, l, c, v = candle
            all_rows.append({
                "timestamp": ts,
                "datetime": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            })
        last_ts = batch[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1
        # rate limiting handled by global_rate_limit() in retry_call

    if not all_rows:
        print(f"  [{token}] ohlcv: no data returned", flush=True)
        return token, 0, False

    _write_csv(outpath, all_rows, ["timestamp", "datetime", "open", "high", "low", "close", "volume"])
    print(f"  [{token}] ohlcv: {len(all_rows)} rows saved", flush=True)
    return token, len(all_rows), False


# ---------------------------------------------------------------------------
# Data fetching — Open Interest
# ---------------------------------------------------------------------------
def fetch_open_interest(token, force=False):
    """Fetch open interest history for a token. Returns (token, row_count)."""
    outpath = os.path.join(OI_DIR, f"{token}_oi.csv")
    if os.path.exists(outpath) and not force:
        existing_rows = _count_csv_rows(outpath)
        print(f"  [{token}] oi: already exists ({existing_rows} rows), skipping", flush=True)
        return token, existing_rows, True

    ex = make_exchange()  # uses cached markets
    symbol = f"{token}/USDT:USDT"
    print(f"  [{token}] oi: fetching from {symbol} ...", flush=True)

    all_rows = []
    since = HISTORY_START_MS
    now_ms = int(time.time() * 1000)

    # Binance OI history only supports 5m timeframe via ccxt
    while since < now_ms:
        try:
            batch = retry_call(
                ex.fetch_open_interest_history, symbol,
                timeframe="5m", since=since, limit=500,
            )
        except Exception as e:
            # Some tokens may not have OI history; break gracefully
            err_str = str(e).lower()
            if "not found" in err_str or "invalid" in err_str or "no data" in err_str:
                print(f"  [{token}] oi: endpoint error ({e}), stopping", flush=True)
                break
            raise
        if not batch:
            break
        for entry in batch:
            all_rows.append({
                "timestamp": entry.get("timestamp"),
                "datetime": entry.get("datetime", ""),
                "open_interest": entry.get("openInterestAmount"),
                "open_interest_value": entry.get("openInterestValue"),
            })
        last_ts = batch[-1].get("timestamp", 0)
        if last_ts <= since:
            break
        since = last_ts + 1
        # rate limiting handled by global_rate_limit() in retry_call

    if not all_rows:
        print(f"  [{token}] oi: no data returned", flush=True)
        return token, 0, False

    _write_csv(outpath, all_rows, ["timestamp", "datetime", "open_interest", "open_interest_value"])
    print(f"  [{token}] oi: {len(all_rows)} rows saved", flush=True)
    return token, len(all_rows), False


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def _write_csv(path, rows, fieldnames):
    """Write a list of dicts to a CSV file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _count_csv_rows(path):
    """Count data rows in a CSV (excluding header)."""
    try:
        with open(path, "r") as f:
            return sum(1 for _ in f) - 1  # subtract header
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Parallel orchestrator
# ---------------------------------------------------------------------------
def fetch_all_for_token(token, data_types, force):
    """Fetch all requested data types for one token. Returns results dict."""
    results = {}
    failures = {}

    if "funding" in data_types:
        try:
            _, count, skipped = fetch_funding_rates(token, force=force)
            results["funding"] = {"rows": count, "skipped": skipped}
        except Exception as e:
            failures["funding"] = str(e)
            print(f"  [{token}] funding: FAILED - {e}", flush=True)
            traceback.print_exc()

    if "ohlcv" in data_types:
        try:
            _, count, skipped = fetch_ohlcv(token, force=force)
            results["ohlcv"] = {"rows": count, "skipped": skipped}
        except Exception as e:
            failures["ohlcv"] = str(e)
            print(f"  [{token}] ohlcv: FAILED - {e}", flush=True)
            traceback.print_exc()

    if "oi" in data_types:
        try:
            _, count, skipped = fetch_open_interest(token, force=force)
            results["oi"] = {"rows": count, "skipped": skipped}
        except Exception as e:
            failures["oi"] = str(e)
            print(f"  [{token}] oi: FAILED - {e}", flush=True)
            traceback.print_exc()

    return token, results, failures


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(all_results, all_failures, elapsed_s):
    """Print a summary table at the end."""
    print("\n" + "=" * 70, flush=True)
    print("FETCH SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"Elapsed: {elapsed_s:.1f}s\n", flush=True)

    # Aggregate per data type
    type_stats = {}
    for token, results, failures in all_results:
        for dtype, info in results.items():
            if dtype not in type_stats:
                type_stats[dtype] = {"fetched": 0, "skipped": 0, "total_rows": 0, "tokens": []}
            if info["skipped"]:
                type_stats[dtype]["skipped"] += 1
            else:
                type_stats[dtype]["fetched"] += 1
            type_stats[dtype]["total_rows"] += info["rows"]
            type_stats[dtype]["tokens"].append(token)

    for dtype in ["funding", "ohlcv", "oi"]:
        if dtype not in type_stats:
            continue
        s = type_stats[dtype]
        print(f"  {dtype:10s}: {s['fetched']} fetched, {s['skipped']} skipped, {s['total_rows']:,} total rows", flush=True)

    # Failures
    fail_list = []
    for token, results, failures in all_results:
        for dtype, msg in failures.items():
            fail_list.append((token, dtype, msg))

    if fail_list:
        print(f"\nFAILURES ({len(fail_list)}):", flush=True)
        for token, dtype, msg in fail_list:
            print(f"  {token} / {dtype}: {msg}", flush=True)
    else:
        print("\nNo failures.", flush=True)

    # Token list
    all_tokens = sorted(set(t for t, _, _ in all_results))
    print(f"\nTokens processed: {len(all_tokens)}", flush=True)
    print(f"  {', '.join(all_tokens)}", flush=True)
    print("=" * 70, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Binance USDT-M perpetual futures data (funding, OHLCV, OI)."
    )
    parser.add_argument(
        "--tokens", nargs="+", default=None,
        help="Tokens to fetch (e.g., BTC ETH SOL). Default: auto-detect liquid from our 49."
    )
    parser.add_argument(
        "--data-type", choices=["funding", "ohlcv", "oi", "all"], default="all",
        help="Which data type to fetch. Default: all."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch even if data files already exist."
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel workers (default: 4, max recommended: 8)."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Determine data types to fetch
    if args.data_type == "all":
        data_types = ["funding", "ohlcv", "oi"]
    else:
        data_types = [args.data_type]

    # Ensure output directories exist
    for d in [FUNDING_DIR, OHLCV_DIR, OI_DIR]:
        os.makedirs(d, exist_ok=True)

    # Determine tokens — also initializes shared markets cache
    global _shared_markets
    if args.tokens:
        tokens = [t.upper() for t in args.tokens]
        print(f"Using user-specified tokens: {tokens}", flush=True)
        ex = make_exchange(load_markets=True)
        _shared_markets = ex.markets
    else:
        # Auto-detect liquid tokens
        ex = make_exchange(load_markets=True)
        _shared_markets = ex.markets
        tokens = discover_liquid_tokens(ex)
        if not tokens:
            print("No liquid tokens found. Exiting.", flush=True)
            sys.exit(1)

    print(f"\nFetching {', '.join(data_types)} for {len(tokens)} tokens with {args.workers} workers", flush=True)
    print(f"Force re-fetch: {args.force}", flush=True)
    print(f"History start: 2020-01-01\n", flush=True)

    t0 = time.time()
    all_results = []

    # Parallelize across tokens
    workers = max(1, min(args.workers, 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_all_for_token, token, data_types, args.force): token
            for token in tokens
        }
        for future in as_completed(futures):
            token = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"  [{token}] FATAL ERROR: {e}", flush=True)
                traceback.print_exc()
                all_results.append((token, {}, {"all": str(e)}))

    elapsed = time.time() - t0
    print_summary(all_results, [], elapsed)


if __name__ == "__main__":
    main()
