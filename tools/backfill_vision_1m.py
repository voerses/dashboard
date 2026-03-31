#!/workspace/venv/bin/python
"""
Backfill 1m OHLCV gaps using Binance Vision daily archives.

Downloads daily ZIP files from data.binance.vision for perp futures,
parses the CSVs, and appends to existing 1m parquet cache files.

Usage:
    python tools/backfill_vision_1m.py                    # auto-detect gap, fill all
    python tools/backfill_vision_1m.py --start 2026-03-01 --end 2026-03-29
    python tools/backfill_vision_1m.py --symbols BTC ETH SOL
    python tools/backfill_vision_1m.py --workers 6
"""

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TOOLS_DIR.parent
DATA_DIR = _PROJECT_DIR / "data"
CACHE_DIR = DATA_DIR / "perp" / "1m_cache"

DAILY_URL = "https://data.binance.vision/data/futures/um/daily/klines/{pair}/1m/{pair}-1m-{date}.zip"

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

_1000_TOKENS = {
    "PEPE", "SHIB", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
    "CHEEMS", "WHY", "X", "XEC",
}

REQUEST_TIMEOUT = 60
MAX_RETRIES = 3

_session = None

def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "crypto-backtest/1.0"})
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
                 or os.environ.get("https_proxy") or os.environ.get("http_proxy"))
        if proxy:
            _session.proxies = {"https": proxy, "http": proxy}
    return _session


def get_pair_names(symbol):
    pairs = [f"{symbol}USDT"]
    if symbol in _1000_TOKENS:
        pairs.insert(0, f"1000{symbol}USDT")
    return pairs


def download_daily(symbol, date_str):
    """Download a single daily 1m kline ZIP. Returns DataFrame or None."""
    pairs = get_pair_names(symbol)
    session = get_session()

    for pair in pairs:
        url = DAILY_URL.format(pair=pair, date=date_str)
        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 404:
                    break
                if resp.status_code == 200:
                    return _parse_zip(resp.content, symbol, pair)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1.0 * (2 ** attempt))
            except (requests.ConnectionError, requests.Timeout):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1.0 * (2 ** attempt))
    return None


def _parse_zip(zip_bytes, symbol, pair):
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                return None
            with zf.open(csv_names[0]) as f:
                first_line = f.readline().decode("utf-8", errors="replace")
                f.seek(0)
                first_field = first_line.split(",")[0].strip()
                if first_field.isdigit():
                    df = pd.read_csv(f, header=None, names=KLINE_COLUMNS)
                else:
                    df = pd.read_csv(f)
                    df.columns = KLINE_COLUMNS[:len(df.columns)]
    except (zipfile.BadZipFile, Exception):
        return None

    if df.empty:
        return None

    sample_ts = df["open_time"].iloc[0]
    if sample_ts > 1e15:
        unit = "us"
    elif sample_ts > 1e12:
        unit = "ms"
    else:
        unit = "s"

    ts = pd.to_datetime(df["open_time"], unit=unit, utc=True).dt.tz_localize(None)

    # 1000X tokens: store raw exchange prices (no division)

    out = pd.DataFrame({
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    }, index=ts)
    out.index.name = None
    return out


def discover_tokens():
    """Discover tokens from existing 1m_cache parquet files."""
    if not CACHE_DIR.is_dir():
        return []
    tokens = []
    for f in sorted(os.listdir(CACHE_DIR)):
        if f.endswith("_1m.parquet"):
            sym = f.replace("_1m.parquet", "")
            if sym.isascii() and sym.isalnum():
                tokens.append(sym)
    return tokens


def detect_gap_start():
    """Find the latest end date across 1m cache to determine gap start."""
    # Sample a few tokens
    for ref in ["BTC", "ETH", "SOL"]:
        path = CACHE_DIR / f"{ref}_1m.parquet"
        if path.exists():
            try:
                df = pd.read_parquet(path, columns=["close"])
                if not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index, unit="ms")
                return df.index.max()
            except Exception:
                continue
    return None


def fetch_token_days(symbol, dates):
    """Fetch all daily 1m data for a token, return concatenated DataFrame."""
    all_dfs = []
    for d in dates:
        df = download_daily(symbol, d.strftime("%Y-%m-%d"))
        if df is not None:
            all_dfs.append(df)
    if not all_dfs:
        return symbol, None
    combined = pd.concat(all_dfs)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return symbol, combined


def merge_into_parquet(symbol, new_df):
    """Append new 1m data to existing parquet cache."""
    cache_path = CACHE_DIR / f"{symbol}_1m.parquet"

    if cache_path.exists():
        try:
            existing = pd.read_parquet(cache_path)
            if not isinstance(existing.index, pd.DatetimeIndex):
                existing.index = pd.to_datetime(existing.index, unit="ms")
            if hasattr(existing.index, "tz") and existing.index.tz is not None:
                existing.index = existing.index.tz_convert("UTC").tz_localize(None)

            existing_end = existing.index.max()
            new_bars = new_df[new_df.index > existing_end]

            if len(new_bars) == 0:
                return 0

            # Match columns
            for col in existing.columns:
                if col not in new_bars.columns:
                    new_bars[col] = float("nan")

            combined = pd.concat([existing, new_bars[existing.columns]])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
            combined.to_parquet(cache_path, engine="pyarrow")
            return len(new_bars)
        except Exception as e:
            print(f"  ERROR merging {symbol}: {e}")
            return 0
    else:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        new_df.to_parquet(cache_path, engine="pyarrow")
        return len(new_df)


def main():
    parser = argparse.ArgumentParser(description="Backfill 1m gaps from Binance Vision daily archives")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    end_date = datetime.strptime(args.end, "%Y-%m-%d") if args.end else (now - timedelta(days=1))
    end_date = end_date.replace(tzinfo=None)

    if args.symbols:
        tokens = [s.upper() for s in args.symbols]
    else:
        tokens = discover_tokens()

    if not tokens:
        print("No tokens found")
        sys.exit(1)

    # Auto-detect gap start
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        gap_end = detect_gap_start()
        if gap_end is None:
            print("Could not detect gap start")
            sys.exit(1)
        start_date = (gap_end + timedelta(minutes=1)).replace(second=0, microsecond=0)
        start_date = start_date.to_pydatetime()

    # Generate date list
    dates = []
    d = start_date
    while d.date() <= end_date.date():
        dates.append(d)
        d += timedelta(days=1)

    print("=" * 70)
    print(f"  Backfill 1M PERP — Binance Vision Daily Archives")
    print("=" * 70)
    print(f"  Tokens: {len(tokens)}")
    print(f"  Date range: {dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')} ({len(dates)} days)")
    print(f"  Workers: {args.workers}")
    print()

    t0 = time.time()
    fetched = 0
    bars_added = 0
    no_data = 0

    # Process tokens sequentially to avoid memory issues (1m data is large)
    # But parallelize the daily downloads within each token
    for i, token in enumerate(tokens, 1):
        _, new_df = fetch_token_days(token, dates)
        if new_df is not None and len(new_df) > 0:
            added = merge_into_parquet(token, new_df)
            bars_added += added
            fetched += 1
            if i % 10 == 0 or i == len(tokens):
                print(f"  [{i}/{len(tokens)}] {token}: +{added:,} bars ({bars_added:,} total)", flush=True)
        else:
            no_data += 1
            if i % 50 == 0:
                print(f"  [{i}/{len(tokens)}] ... ({no_data} no data)", flush=True)

        # Memory management: force GC periodically
        if i % 20 == 0:
            import gc
            gc.collect()

    elapsed = time.time() - t0
    print()
    print(f"  Done in {elapsed:.0f}s: {fetched} tokens updated, {bars_added:,} bars added, {no_data} no data")


if __name__ == "__main__":
    main()
