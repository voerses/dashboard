#!/workspace/venv/bin/python
"""
Fetch today's data from Binance REST API (klines endpoint).

Fills the gap between Binance Vision daily archives (lag ~1 day) and now.
Uses the public market data endpoint (no API key needed).

Fetches 1h and optionally 1m data for perp and spot.

Usage:
    python tools/fetch_today_data.py                    # 1h perp + spot
    python tools/fetch_today_data.py --include-1m       # also fetch 1m
    python tools/fetch_today_data.py --market perp      # perp only
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

_PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = _PROJECT_DIR / "data"

# Use the public data API endpoint (reliable, no API key)
SPOT_BASE = "https://data-api.binance.vision"
FUTURES_BASE = "https://fapi.binance.com"

_1000_TOKENS = {
    "PEPE", "SHIB", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
    "CHEEMS", "WHY", "X", "APU", "NEIRO", "XEC",
}

_session = None
_rate_limit_delay = 0.1  # 100ms between requests


def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "crypto-backtest/1.0"})
    return _session


def get_pair(symbol, market):
    if symbol in _1000_TOKENS:
        return f"1000{symbol}USDT"
    return f"{symbol}USDT"


def fetch_klines(symbol, market, interval, start_ms, limit=1000):
    """Fetch klines from Binance. Returns DataFrame or None."""
    pair = get_pair(symbol, market)
    session = get_session()

    if market == "perp":
        url = f"{FUTURES_BASE}/fapi/v1/klines"
    else:
        url = f"{SPOT_BASE}/api/v3/klines"

    params = {
        "symbol": pair,
        "interval": interval,
        "startTime": start_ms,
        "limit": limit,
    }

    for attempt in range(3):
        try:
            time.sleep(_rate_limit_delay)
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if not data:
                    return None
                return _parse_klines(data, symbol, pair)
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                print(f"  Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
            elif resp.status_code in (404, 400):
                return None
            else:
                if attempt < 2:
                    time.sleep(2 ** attempt)
        except (requests.ConnectionError, requests.Timeout):
            if attempt < 2:
                time.sleep(2 ** attempt)

    return None


def _parse_klines(data, symbol, pair):
    """Parse Binance kline response into DataFrame."""
    rows = []
    for k in data:
        rows.append({
            "timestamp": pd.Timestamp(k[0], unit="ms", tz=None),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    df = pd.DataFrame(rows)
    df = df.set_index("timestamp")
    df.index.name = None

    # Handle 1000X tokens
    is_1000 = pair.startswith("1000") and symbol in _1000_TOKENS
    if is_1000:
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] / 1000.0

    return df


def discover_tokens(market, resolution="1h"):
    """Discover tokens from existing cache."""
    if resolution == "1m":
        cache_dir = DATA_DIR / "perp" / "1m_cache"
        suffix = "_1m.parquet"
    else:
        cache_dir = DATA_DIR / market / "1h_cache"
        suffix = "_1h.parquet"

    if not cache_dir.is_dir():
        return []
    tokens = []
    for f in sorted(os.listdir(cache_dir)):
        if f.endswith(suffix):
            sym = f.replace(suffix, "")
            if sym.isascii() and sym.isalnum():
                tokens.append(sym)
    return tokens


def get_cache_end(symbol, market, resolution="1h"):
    """Get the latest timestamp in cache."""
    if resolution == "1m":
        path = DATA_DIR / "perp" / "1m_cache" / f"{symbol}_1m.parquet"
    else:
        path = DATA_DIR / market / "1h_cache" / f"{symbol}_1h.parquet"

    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["close"])
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, unit="ms")
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        return df.index.max() if len(df) > 0 else None
    except Exception:
        return None


def merge_into_cache(symbol, new_df, market, resolution="1h"):
    """Append new data to parquet cache."""
    if resolution == "1m":
        path = DATA_DIR / "perp" / "1m_cache" / f"{symbol}_1m.parquet"
    else:
        path = DATA_DIR / market / "1h_cache" / f"{symbol}_1h.parquet"

    if not path.exists():
        return 0

    try:
        existing = pd.read_parquet(path)
        if not isinstance(existing.index, pd.DatetimeIndex):
            existing.index = pd.to_datetime(existing.index, unit="ms")
        if hasattr(existing.index, "tz") and existing.index.tz is not None:
            existing.index = existing.index.tz_convert("UTC").tz_localize(None)

        existing_end = existing.index.max()
        new_bars = new_df[new_df.index > existing_end]

        if len(new_bars) == 0:
            return 0

        for col in existing.columns:
            if col not in new_bars.columns:
                new_bars[col] = 0.0 if col in ("funding_rate", "funding_1h") else float("nan")

        combined = pd.concat([existing, new_bars[existing.columns]])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()

        for col in ("funding_rate", "funding_1h"):
            if col in combined.columns:
                combined[col] = combined[col].fillna(0.0)

        combined.to_parquet(path, engine="pyarrow")
        return len(new_bars)
    except Exception as e:
        print(f"  ERROR {symbol}: {e}")
        return 0


def backfill_market(market, resolution, workers):
    """Backfill a single market/resolution combination."""
    interval = "1m" if resolution == "1m" else "1h"
    tokens = discover_tokens(market, resolution)
    if not tokens:
        print(f"  No tokens for {market}/{resolution}")
        return

    print(f"\n  {market.upper()} {resolution}: {len(tokens)} tokens")

    # Sample a reference token to find start time
    ref = next((t for t in ["BTC", "ETH", "SOL"] if t in tokens), tokens[0])
    cache_end = get_cache_end(ref, market, resolution)
    if cache_end is None:
        print(f"  Could not determine cache end for {ref}")
        return

    delta = pd.Timedelta(hours=1) if resolution == "1h" else pd.Timedelta(minutes=1)
    start_ms = int((cache_end + delta).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    if start_ms >= now_ms:
        print(f"  Already up to date (ends {cache_end})")
        return

    hours_gap = (now_ms - start_ms) / 3600000
    print(f"  Gap: {cache_end} → now ({hours_gap:.1f}h)")

    fetched = 0
    bars_added = 0

    for i, token in enumerate(tokens, 1):
        # Get this token's specific cache end
        token_end = get_cache_end(token, market, resolution)
        if token_end is None:
            continue
        token_delta = pd.Timedelta(hours=1) if resolution == "1h" else pd.Timedelta(minutes=1)
        token_start_ms = int((token_end + token_delta).timestamp() * 1000)

        if token_start_ms >= now_ms:
            continue

        df = fetch_klines(token, market, interval, token_start_ms, limit=1000)
        if df is not None and len(df) > 0:
            added = merge_into_cache(token, df, market, resolution)
            bars_added += added
            fetched += 1

        if i % 20 == 0 or i == len(tokens):
            print(f"    [{i}/{len(tokens)}] +{bars_added:,} bars so far", flush=True)

    print(f"  Done: {fetched} tokens, +{bars_added:,} bars")


def main():
    parser = argparse.ArgumentParser(description="Fetch today's data from Binance REST API")
    parser.add_argument("--market", choices=["perp", "spot", "both"], default="both")
    parser.add_argument("--no-1m", action="store_true", help="Skip 1m data")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    markets = ["perp", "spot"] if args.market == "both" else [args.market]

    print("=" * 70)
    print("  Fetch Today's Data — Binance REST API")
    print("=" * 70)

    for market in markets:
        backfill_market(market, "1h", args.workers)

    if not args.no_1m:
        backfill_market("perp", "1m", args.workers)

    print("\nDone!")


if __name__ == "__main__":
    main()
