#!/workspace/venv/bin/python
"""
Fetch spot OHLCV data from Binance for all universe tokens.

Fetches 1h OHLCV candles from Binance spot markets.
Data is saved as CSV files under data/spot/binance/1h_ohlcv/.

Usage:
    python fetch_binance_spot.py                          # all universe tokens
    python fetch_binance_spot.py --tokens BTC ETH SOL     # specific tokens
    python fetch_binance_spot.py --force                   # re-fetch even if files exist
    python fetch_binance_spot.py --workers 8               # increase parallelism
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
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.join(_TOOLS_DIR, "..")
BASE_DIR = os.path.join(_PROJECT_DIR, "data", "spot", "binance")
OHLCV_DIR = os.path.join(BASE_DIR, "1h_ohlcv")

HISTORY_START_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

# Import universe tokens
sys.path.insert(0, os.path.join(_PROJECT_DIR, "v3"))
from universe import LIQUID_TOKENS

# Rate limit / retry settings
MAX_RETRIES = 5
BASE_BACKOFF_S = 1.0

# ---------------------------------------------------------------------------
# Global rate limiter (same pattern as fetch_binance_perp.py)
# ---------------------------------------------------------------------------
# Binance spot: 6000 weight/min. Klines = 2 weight per request.
# Budget: ~100 weight/sec → max ~50 req/s.
# We target 6 req/s (= 0.167s) to stay safe with parallel workers.
_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.167

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
# Exchange factory
# ---------------------------------------------------------------------------
_shared_markets = None

def _get_proxy():
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
             or os.environ.get("https_proxy") or os.environ.get("http_proxy"))
    if proxy:
        return {"https": proxy, "http": proxy}
    return {}

def make_exchange(load_markets=False):
    """Create a Binance spot exchange instance."""
    ex = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
        "proxies": _get_proxy(),
    })
    if load_markets:
        ex.load_markets()
    elif _shared_markets is not None:
        ex.markets = _shared_markets
        ex.markets_by_id = {}
    return ex


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------
def retry_call(fn, *args, max_retries=MAX_RETRIES, **kwargs):
    """Call fn with global rate limiting + exponential backoff on errors."""
    for attempt in range(max_retries):
        global_rate_limit()
        try:
            return fn(*args, **kwargs)
        except (ccxt.RateLimitExceeded, ccxt.DDoSProtection):
            wait = BASE_BACKOFF_S * (2 ** attempt)
            print(f"    Rate limited (attempt {attempt + 1}/{max_retries}), waiting {wait:.1f}s ...", flush=True)
            time.sleep(wait)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            wait = BASE_BACKOFF_S * (2 ** attempt)
            print(f"    Network error (attempt {attempt + 1}/{max_retries}): {e}, retrying in {wait:.1f}s ...", flush=True)
            time.sleep(wait)
        except ccxt.ExchangeError as e:
            err_str = str(e).lower()
            if "rate" in err_str or "limit" in err_str or "too many" in err_str:
                wait = BASE_BACKOFF_S * (2 ** attempt)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed after {max_retries} retries")


# ---------------------------------------------------------------------------
# Symbol mapping: perp token name → Binance spot symbol
# ---------------------------------------------------------------------------
# Futures use 1000X prefix for low-value tokens; spot uses the real name
PERP_TO_SPOT = {
    '1000PEPE': 'PEPE', '1000BONK': 'BONK', '1000FLOKI': 'FLOKI',
    '1000SHIB': 'SHIB', '1000LUNC': 'LUNC', '1000SATS': 'SATS',
    '1000XEC': 'XEC', '1000RATS': 'RATS', '1000CAT': 'CAT',
}


def get_spot_symbol(token, exchange):
    """Resolve the Binance spot symbol for a token (handles 1000X prefix)."""
    # Normalize 1000X perp names to spot names
    spot_name = PERP_TO_SPOT.get(token, token)

    symbol = f"{spot_name}/USDT"
    if symbol in exchange.markets:
        return symbol, spot_name

    return None, spot_name


def discover_perp_tokens():
    """Get all tokens from existing perp data directory."""
    perp_dir = os.path.join(_PROJECT_DIR, "data", "perp", "binance", "1h_ohlcv")
    if not os.path.isdir(perp_dir):
        return []
    tokens = []
    for f in sorted(os.listdir(perp_dir)):
        if f.endswith("_perp_1h.csv"):
            tokens.append(f.replace("_perp_1h.csv", ""))
    return tokens


# ---------------------------------------------------------------------------
# Data fetching — 1h OHLCV
# ---------------------------------------------------------------------------
def fetch_ohlcv(token, force=False):
    """Fetch all 1h OHLCV candles for a spot token. Returns (token, row_count, skipped)."""
    ex = make_exchange()
    symbol, spot_name = get_spot_symbol(token, ex)

    # Save under the spot name (e.g., PEPE not 1000PEPE)
    outpath = os.path.join(OHLCV_DIR, f"{spot_name}_spot_1h.csv")
    if os.path.exists(outpath) and not force:
        existing_rows = _count_csv_rows(outpath)
        print(f"  [{spot_name}] spot ohlcv: already exists ({existing_rows} rows), skipping", flush=True)
        return spot_name, existing_rows, True

    if symbol is None:
        print(f"  [{spot_name}] spot ohlcv: no USDT spot pair found, skipping", flush=True)
        return spot_name, 0, False

    print(f"  [{spot_name}] spot ohlcv: fetching {symbol} ...", flush=True)

    all_rows = []
    since = HISTORY_START_MS
    now_ms = int(time.time() * 1000)

    while since < now_ms:
        batch = retry_call(ex.fetch_ohlcv, symbol, timeframe="1h", since=since, limit=1000)
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

    if not all_rows:
        print(f"  [{spot_name}] spot ohlcv: no data returned", flush=True)
        return spot_name, 0, False

    _write_csv(outpath, all_rows, ["timestamp", "datetime", "open", "high", "low", "close", "volume"])
    print(f"  [{spot_name}] spot ohlcv: {len(all_rows)} rows saved", flush=True)
    return spot_name, len(all_rows), False


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def _write_csv(path, rows, fieldnames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _count_csv_rows(path):
    try:
        with open(path, "r") as f:
            return sum(1 for _ in f) - 1
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(results, elapsed_s):
    print("\n" + "=" * 70, flush=True)
    print("SPOT FETCH SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"Elapsed: {elapsed_s:.1f}s\n", flush=True)

    fetched = [(t, n) for t, n, skipped in results if not skipped and n > 0]
    skipped = [(t, n) for t, n, skipped in results if skipped]
    no_data = [(t, n) for t, n, skipped in results if not skipped and n == 0]

    print(f"  Fetched:  {len(fetched)} tokens, {sum(n for _, n in fetched):,} total rows", flush=True)
    print(f"  Skipped:  {len(skipped)} tokens (already exist)", flush=True)
    print(f"  No data:  {len(no_data)} tokens (no spot USDT pair)", flush=True)

    if no_data:
        print(f"\n  Missing spot pairs: {', '.join(t for t, _ in no_data)}", flush=True)

    print("=" * 70, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Binance spot 1h OHLCV data."
    )
    parser.add_argument(
        "--tokens", nargs="+", default=None,
        help="Tokens to fetch (e.g., BTC ETH SOL)."
    )
    parser.add_argument(
        "--all-perp", action="store_true",
        help="Fetch spot for ALL tokens that exist in perp/binance/ (not just universe)."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch even if data files already exist."
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel workers (default: 4)."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    os.makedirs(OHLCV_DIR, exist_ok=True)

    # Determine tokens
    global _shared_markets
    if args.tokens:
        tokens = [t.upper() for t in args.tokens]
    elif args.all_perp:
        tokens = discover_perp_tokens()
        print(f"Discovered {len(tokens)} tokens from perp data", flush=True)
    else:
        # Default: fetch for all tokens that have perp data (broadest coverage)
        tokens = discover_perp_tokens()
        if not tokens:
            tokens = list(LIQUID_TOKENS)
        print(f"Discovered {len(tokens)} tokens from perp data", flush=True)

    # Load markets once, share across threads
    print("Loading Binance spot markets ...", flush=True)
    ex = make_exchange(load_markets=True)
    _shared_markets = ex.markets

    # Check which tokens have spot pairs (handles 1000X → real name)
    available = []   # (perp_name, spot_name) pairs
    missing = []
    for token in tokens:
        sym, spot_name = get_spot_symbol(token, ex)
        if sym:
            available.append(token)
        else:
            missing.append(f"{token} ({spot_name})" if spot_name != token else token)

    if missing:
        print(f"\nNo spot USDT pair for {len(missing)} tokens:", flush=True)
        # Print in columns
        for i in range(0, len(missing), 8):
            print(f"  {', '.join(missing[i:i+8])}", flush=True)
    print(f"\nFetching 1h spot OHLCV for {len(available)} tokens with {args.workers} workers", flush=True)
    print(f"Force re-fetch: {args.force}", flush=True)
    print(f"History start: 2020-01-01\n", flush=True)

    t0 = time.time()
    all_results = []

    workers = max(1, min(args.workers, 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_ohlcv, token, args.force): token
            for token in available
        }
        for future in as_completed(futures):
            token = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"  [{token}] FATAL ERROR: {e}", flush=True)
                traceback.print_exc()
                all_results.append((token, 0, False))

    elapsed = time.time() - t0
    print_summary(all_results, elapsed)


if __name__ == "__main__":
    main()
