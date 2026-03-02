#!/workspace/venv/bin/python
"""Fetch perpetual futures data (funding rates + OHLCV candles) from Hyperliquid.

Uses the raw HTTP API at POST https://api.hyperliquid.xyz/info.
Parallelizes across tokens with ThreadPoolExecutor (4 workers).
Rate limit: 1200 weight/min. Each info request = 20 + items/20 (funding) or items/60 (candles).
"""

import argparse
import csv
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_URL = "https://api.hyperliquid.xyz/info"

BASE_DIR = "/workspace/crypto_backtest/data/perp/hyperliquid"
FUNDING_DIR = os.path.join(BASE_DIR, "funding")
OHLCV_DIR = os.path.join(BASE_DIR, "ohlcv")

# Hyperliquid launched ~Feb 2023
HL_LAUNCH_MS = int(datetime(2023, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)

# Pagination limits
FUNDING_PAGE_SIZE = 500
CANDLE_PAGE_SIZE = 5000

MIN_DAILY_VOLUME_USD = 5_000_000  # $5M filter

MAX_WORKERS = 4  # keep low — rate limit is tight

# ---------------------------------------------------------------------------
# Global rate limiter
# ---------------------------------------------------------------------------
# Hyperliquid: 1200 weight/min = 20 weight/sec.
# Historical endpoints cost ~20-100 weight each (20 base + items returned).
# Worst case: fundingHistory returning 500 items = 20 + 500/20 = 45 weight.
# At 45 weight/req: max 0.44 req/s globally.
# We target 0.4 req/s = 2.5s between requests to stay under budget.
# This is conservative but safe — the penalty for hitting the limit is a
# long cooldown that's worse than going slow.
_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 2.5  # seconds between ANY request across all threads

def _global_rate_limit():
    """Block until enough time has passed since the last global request."""
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()

# ---------------------------------------------------------------------------
# HTTP helpers (thread-safe: one session per thread via threading.local)
# ---------------------------------------------------------------------------

_thread_local = threading.local()

def _get_session() -> requests.Session:
    """Return a thread-local requests.Session."""
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        _thread_local.session = s
    return _thread_local.session


def _post(body: dict, retries: int = 3) -> list | dict:
    """POST to Hyperliquid /info with global rate limiting + retry."""
    session = _get_session()
    for attempt in range(retries):
        _global_rate_limit()  # enforce cross-thread rate limit
        try:
            resp = session.post(API_URL, json=body, timeout=30)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  [429 rate limited] waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  [retry] {exc} — waiting {wait}s", flush=True)
                time.sleep(wait)
            else:
                raise
    return []  # unreachable, but keeps linters happy


# ---------------------------------------------------------------------------
# Meta / liquidity filter
# ---------------------------------------------------------------------------

def get_liquid_tokens(verbose: bool = True) -> list[str]:
    """Return ALL tokens on Hyperliquid with >$5M daily volume. Not limited to any preset list."""
    data = _post({"type": "metaAndAssetCtxs"})
    meta = data[0]  # {"universe": [{"name": "BTC", ...}, ...]}
    asset_ctxs = data[1]  # list of dicts with "dayNtlVlm", etc.

    universe = meta.get("universe", [])

    liquid = []
    skipped_low_vol = []

    for i, coin_info in enumerate(universe):
        name = coin_info.get("name", coin_info.get("coin", ""))
        if i < len(asset_ctxs):
            ctx = asset_ctxs[i]
            day_vol = float(ctx.get("dayNtlVlm", 0))
            if day_vol >= MIN_DAILY_VOLUME_USD:
                liquid.append(name)
            else:
                skipped_low_vol.append((name, day_vol))

    if verbose:
        print(f"\n=== Liquidity Filter ===")
        print(f"Total listed on Hyperliquid: {len(universe)}")
        print(f"Above ${MIN_DAILY_VOLUME_USD / 1e6:.0f}M daily volume: {len(liquid)}")
        print(f"Below threshold: {len(skipped_low_vol)}")
        if liquid:
            print(f"Fetching: {', '.join(sorted(liquid))}")
        print()

    return sorted(liquid)


# ---------------------------------------------------------------------------
# Funding rates
# ---------------------------------------------------------------------------

def fetch_funding(token: str, force: bool = False) -> int:
    """Fetch full funding history for a token. Returns number of records."""
    outpath = os.path.join(FUNDING_DIR, f"{token}_funding.csv")
    if os.path.exists(outpath) and not force:
        print(f"  [funding] {token}: exists, skipping (use --force to refetch)", flush=True)
        return -1

    all_records = []
    start_ms = HL_LAUNCH_MS
    now_ms = int(time.time() * 1000)

    page = 0
    while start_ms < now_ms:
        body = {
            "type": "fundingHistory",
            "coin": token,
            "startTime": start_ms,
            "endTime": now_ms,
        }
        records = _post(body)
        # rate limiting handled by _global_rate_limit() in _post()

        if not records:
            break

        all_records.extend(records)
        page += 1

        if len(records) < FUNDING_PAGE_SIZE:
            # Got everything
            break

        # Paginate: use last record's time as next startTime
        last_time = records[-1].get("time", 0)
        if isinstance(last_time, str):
            # Some endpoints return ISO strings — convert to ms
            last_time = int(datetime.fromisoformat(last_time.replace("Z", "+00:00")).timestamp() * 1000)
        # Move 1ms past the last record to avoid duplicates
        start_ms = last_time + 1

        if page % 10 == 0:
            print(f"  [funding] {token}: {len(all_records)} records (page {page})...", flush=True)

    if not all_records:
        print(f"  [funding] {token}: no data returned", flush=True)
        return 0

    # Write CSV
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    fieldnames = sorted(all_records[0].keys())
    with open(outpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_records)

    print(f"  [funding] {token}: {len(all_records)} records saved", flush=True)
    return len(all_records)


# ---------------------------------------------------------------------------
# OHLCV candles
# ---------------------------------------------------------------------------

def fetch_candles(token: str, interval: str, force: bool = False) -> int:
    """Fetch full candle history for a token at a given interval (1h or 4h).

    Paginates *backwards* from now because the API returns up to 5000 candles
    and we want the most complete history.
    Returns number of candles.
    """
    suffix = interval  # "1h" or "4h"
    outpath = os.path.join(OHLCV_DIR, f"{token}_perp_{suffix}.csv")
    if os.path.exists(outpath) and not force:
        print(f"  [ohlcv-{suffix}] {token}: exists, skipping (use --force to refetch)", flush=True)
        return -1

    all_candles = []
    now_ms = int(time.time() * 1000)
    end_ms = now_ms
    earliest_allowed = HL_LAUNCH_MS

    page = 0
    while end_ms > earliest_allowed:
        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": token,
                "interval": interval,
                "startTime": earliest_allowed,
                "endTime": end_ms,
            },
        }
        candles = _post(body)
        # rate limiting handled by _global_rate_limit() in _post()

        if not candles:
            break

        # Candles are returned sorted ascending by time (t field = open time in ms)
        all_candles = candles + all_candles  # prepend
        page += 1

        if len(candles) < CANDLE_PAGE_SIZE:
            # Got everything back to launch
            break

        # Paginate backwards: the earliest candle's open time becomes the new endTime
        earliest_candle_t = candles[0].get("t", 0)
        if earliest_candle_t <= earliest_allowed:
            break
        # Move endTime to 1ms before the earliest candle we got
        end_ms = earliest_candle_t - 1

        if page % 5 == 0:
            print(f"  [ohlcv-{suffix}] {token}: {len(all_candles)} candles (page {page})...", flush=True)

    if not all_candles:
        print(f"  [ohlcv-{suffix}] {token}: no data returned", flush=True)
        return 0

    # Deduplicate by open time (t), keep first occurrence
    seen = set()
    deduped = []
    for c in all_candles:
        t = c.get("t", 0)
        if t not in seen:
            seen.add(t)
            deduped.append(c)
    all_candles = deduped

    # Write CSV
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    fieldnames = sorted(all_candles[0].keys())
    with open(outpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_candles)

    print(f"  [ohlcv-{suffix}] {token}: {len(all_candles)} candles saved", flush=True)
    return len(all_candles)


# ---------------------------------------------------------------------------
# Per-token orchestrator
# ---------------------------------------------------------------------------

def fetch_token(token: str, data_type: str, force: bool = False) -> dict:
    """Fetch all requested data for a single token. Returns a summary dict."""
    result = {"token": token}

    if data_type in ("funding", "all"):
        result["funding"] = fetch_funding(token, force=force)

    if data_type in ("ohlcv", "all"):
        result["ohlcv_1h"] = fetch_candles(token, "1h", force=force)
        result["ohlcv_4h"] = fetch_candles(token, "4h", force=force)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch Hyperliquid perpetual futures data (funding + OHLCV)."
    )
    parser.add_argument(
        "--tokens",
        type=str,
        default=None,
        help="Comma-separated list of tokens (default: all 49 that pass liquidity filter)",
    )
    parser.add_argument(
        "--data-type",
        choices=["funding", "ohlcv", "all"],
        default="all",
        help="Which data to fetch (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refetch even if CSV already exists",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Hyperliquid Perpetual Futures Data Fetcher")
    print("=" * 60)
    print(f"Data type : {args.data_type}")
    print(f"Force     : {args.force}")
    print(f"API       : {API_URL}")
    print(f"Output    : {BASE_DIR}")
    print()

    # Ensure output directories exist
    os.makedirs(FUNDING_DIR, exist_ok=True)
    os.makedirs(OHLCV_DIR, exist_ok=True)

    # Get liquid tokens from Hyperliquid
    print("Fetching exchange metadata for liquidity filter...", flush=True)
    liquid_tokens = get_liquid_tokens()

    # Override with user-supplied tokens if provided
    if args.tokens:
        requested = [t.strip().upper() for t in args.tokens.split(",")]
        # Warn about tokens not in our list
        for t in requested:
            if t not in liquid_tokens:
                print(f"  WARNING: {t} did not pass liquidity filter, fetching anyway", flush=True)
        tokens = requested
    else:
        tokens = liquid_tokens

    if not tokens:
        print("No tokens to fetch. Exiting.", flush=True)
        sys.exit(0)

    print(f"Fetching data for {len(tokens)} tokens: {', '.join(tokens)}")
    print(f"Workers: {MAX_WORKERS}")
    print()

    # Parallel fetch
    start_time = time.time()
    summaries = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_token, token, args.data_type, args.force): token
            for token in tokens
        }
        for future in as_completed(futures):
            token = futures[future]
            try:
                result = future.result()
                summaries.append(result)
            except Exception as exc:
                print(f"  ERROR: {token} failed: {exc}", flush=True)
                summaries.append({"token": token, "error": str(exc)})

    elapsed = time.time() - start_time

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Tokens processed: {len(summaries)}")
    print()

    # Sort summaries by token name for readability
    summaries.sort(key=lambda s: s["token"])

    total_funding = 0
    total_candles_1h = 0
    total_candles_4h = 0
    skipped_funding = 0
    skipped_1h = 0
    skipped_4h = 0
    errors = 0

    header = f"{'Token':<10}"
    if args.data_type in ("funding", "all"):
        header += f"{'Funding':>12}"
    if args.data_type in ("ohlcv", "all"):
        header += f"{'OHLCV 1h':>12}{'OHLCV 4h':>12}"
    print(header)
    print("-" * len(header))

    for s in summaries:
        line = f"{s['token']:<10}"
        if "error" in s:
            line += f"  ERROR: {s['error']}"
            errors += 1
        else:
            if args.data_type in ("funding", "all"):
                n = s.get("funding", 0)
                if n == -1:
                    line += f"{'skipped':>12}"
                    skipped_funding += 1
                else:
                    line += f"{n:>12,}"
                    total_funding += n

            if args.data_type in ("ohlcv", "all"):
                n1 = s.get("ohlcv_1h", 0)
                if n1 == -1:
                    line += f"{'skipped':>12}"
                    skipped_1h += 1
                else:
                    line += f"{n1:>12,}"
                    total_candles_1h += n1

                n4 = s.get("ohlcv_4h", 0)
                if n4 == -1:
                    line += f"{'skipped':>12}"
                    skipped_4h += 1
                else:
                    line += f"{n4:>12,}"
                    total_candles_4h += n4

        print(line)

    print("-" * len(header))
    totals_line = f"{'TOTAL':<10}"
    if args.data_type in ("funding", "all"):
        totals_line += f"{total_funding:>12,}"
    if args.data_type in ("ohlcv", "all"):
        totals_line += f"{total_candles_1h:>12,}"
        totals_line += f"{total_candles_4h:>12,}"
    print(totals_line)

    if skipped_funding or skipped_1h or skipped_4h:
        skip_parts = []
        if skipped_funding:
            skip_parts.append(f"funding={skipped_funding}")
        if skipped_1h:
            skip_parts.append(f"1h={skipped_1h}")
        if skipped_4h:
            skip_parts.append(f"4h={skipped_4h}")
        print(f"Skipped (already exist): {', '.join(skip_parts)}")

    if errors:
        print(f"Errors: {errors}")

    print()
    print("Output directories:")
    if args.data_type in ("funding", "all"):
        print(f"  Funding: {FUNDING_DIR}/")
    if args.data_type in ("ohlcv", "all"):
        print(f"  OHLCV:   {OHLCV_DIR}/")
    print()


if __name__ == "__main__":
    main()
