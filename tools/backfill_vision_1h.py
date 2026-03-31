#!/workspace/venv/bin/python
"""
Backfill 1h OHLCV gaps using Binance Vision daily archives.

Downloads daily ZIP files from data.binance.vision for both perp and spot,
parses the CSVs, and appends to existing CSV files or rebuilds parquet cache.

Usage:
    python tools/backfill_vision_1h.py                    # auto-detect gap, fill both markets
    python tools/backfill_vision_1h.py --market perp      # perp only
    python tools/backfill_vision_1h.py --market spot      # spot only
    python tools/backfill_vision_1h.py --start 2026-03-06 --end 2026-03-29
    python tools/backfill_vision_1h.py --rebuild-cache    # also rebuild parquet cache after
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

import pandas as pd
import requests

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TOOLS_DIR.parent
DATA_DIR = _PROJECT_DIR / "data"

# Binance Vision URL patterns
FUTURES_DAILY_URL = "https://data.binance.vision/data/futures/um/daily/klines/{pair}/1h/{pair}-1h-{date}.zip"
SPOT_DAILY_URL = "https://data.binance.vision/data/spot/daily/klines/{pair}/1h/{pair}-1h-{date}.zip"

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

# Symbol mapping (same as other fetchers)
_1000_TOKENS = {
    "PEPE", "SHIB", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
    "CHEEMS", "WHY", "X", "XEC",
}

REQUEST_TIMEOUT = 30
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


def get_pair_names(symbol, market):
    """Return possible Binance pair names."""
    if market == "spot":
        pairs = [f"{symbol}USDT"]
        if symbol in _1000_TOKENS:
            pairs.insert(0, f"1000{symbol}USDT")
        return pairs
    else:
        pairs = [f"{symbol}USDT"]
        if symbol in _1000_TOKENS:
            pairs.insert(0, f"1000{symbol}USDT")
        return pairs


def download_daily(symbol, date_str, market):
    """Download a single daily kline ZIP. Returns DataFrame or None."""
    pairs = get_pair_names(symbol, market)
    session = get_session()
    url_template = FUTURES_DAILY_URL if market == "perp" else SPOT_DAILY_URL

    for pair in pairs:
        url = url_template.format(pair=pair, date=date_str)
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
    """Parse a daily ZIP into a DataFrame."""
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

    df["timestamp"] = pd.to_datetime(df["open_time"], unit=unit, utc=True).dt.tz_localize(None)

    # 1000X tokens: store raw exchange prices (no division)

    out = pd.DataFrame({
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    }, index=df["timestamp"])
    out.index.name = "datetime"
    return out


def discover_tokens(market):
    """Discover tokens from existing 1h_cache parquet files."""
    cache_dir = DATA_DIR / market / "1h_cache"
    if not cache_dir.is_dir():
        return []
    tokens = []
    for f in sorted(os.listdir(cache_dir)):
        if f.endswith("_1h.parquet"):
            sym = f.replace("_1h.parquet", "")
            if sym.isascii() and sym.isalnum():
                tokens.append(sym)
    return tokens


def detect_gap(market):
    """Find the latest end date across all tokens to determine gap start."""
    cache_dir = DATA_DIR / market / "1h_cache"
    latest = pd.Timestamp.min
    for f in os.listdir(cache_dir):
        if not f.endswith("_1h.parquet"):
            continue
        try:
            df = pd.read_parquet(cache_dir / f, columns=[])
            if len(df.index) > 0:
                idx = df.index
                if not isinstance(idx, pd.DatetimeIndex):
                    idx = pd.to_datetime(idx, unit="ms")
                end = idx.max()
                if end > latest:
                    latest = end
        except Exception:
            continue
    return latest


def fetch_token(symbol, dates, market):
    """Fetch all missing daily data for a token. Returns (symbol, DataFrame)."""
    all_dfs = []
    for d in dates:
        df = download_daily(symbol, d.strftime("%Y-%m-%d"), market)
        if df is not None:
            all_dfs.append(df)
    if not all_dfs:
        return symbol, None
    combined = pd.concat(all_dfs)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return symbol, combined


def merge_into_parquet(symbol, new_df, market):
    """Merge new data into existing parquet cache file."""
    cache_path = DATA_DIR / market / "1h_cache" / f"{symbol}_1h.parquet"

    if cache_path.exists():
        try:
            existing = pd.read_parquet(cache_path)
            if not isinstance(existing.index, pd.DatetimeIndex):
                existing.index = pd.to_datetime(existing.index, unit="ms")
            if hasattr(existing.index, "tz") and existing.index.tz is not None:
                existing.index = existing.index.tz_convert("UTC").tz_localize(None)

            # Only append bars after existing end
            existing_end = existing.index.max()
            new_bars = new_df[new_df.index > existing_end]

            if len(new_bars) == 0:
                return 0

            # Ensure column compatibility
            for col in existing.columns:
                if col not in new_bars.columns:
                    new_bars[col] = 0.0 if col in ("funding_rate", "funding_1h") else float("nan")

            combined = pd.concat([existing, new_bars[existing.columns]])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()

            for col in ("funding_rate", "funding_1h"):
                if col in combined.columns:
                    combined[col] = combined[col].fillna(0.0)

            combined.to_parquet(cache_path, engine="pyarrow")
            return len(new_bars)
        except Exception as e:
            print(f"  ERROR merging {symbol}: {e}")
            return 0
    else:
        new_df.to_parquet(cache_path, engine="pyarrow")
        return len(new_df)


def main():
    parser = argparse.ArgumentParser(description="Backfill 1h gaps from Binance Vision daily archives")
    parser.add_argument("--market", choices=["perp", "spot", "both"], default="both")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--rebuild-cache", action="store_true", help="Also rebuild full parquet cache")
    args = parser.parse_args()

    markets = ["perp", "spot"] if args.market == "both" else [args.market]

    now = datetime.now(timezone.utc)
    # Vision daily archives usually lag by ~1 day
    end_date = datetime.strptime(args.end, "%Y-%m-%d") if args.end else (now - timedelta(days=1))
    end_date = end_date.replace(tzinfo=None)

    for market in markets:
        print("=" * 70)
        print(f"  Backfill 1H {market.upper()} — Binance Vision Daily Archives")
        print("=" * 70)

        tokens = discover_tokens(market)
        if not tokens:
            print(f"  No tokens found in {market}/1h_cache, skipping")
            continue

        # Auto-detect gap start
        if args.start:
            start_date = datetime.strptime(args.start, "%Y-%m-%d")
        else:
            gap_end = detect_gap(market)
            start_date = (gap_end + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            start_date = start_date.to_pydatetime()

        # Generate date list
        dates = []
        d = start_date
        while d.date() <= end_date.date():
            dates.append(d)
            d += timedelta(days=1)

        print(f"  Tokens: {len(tokens)}")
        print(f"  Date range: {dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')} ({len(dates)} days)")
        print()

        t0 = time.time()
        fetched = 0
        bars_added = 0
        no_data = 0

        workers = max(1, min(args.workers, 8))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(fetch_token, token, dates, market): token
                for token in tokens
            }
            for i, future in enumerate(as_completed(futures), 1):
                token = futures[future]
                try:
                    _, new_df = future.result()
                    if new_df is not None and len(new_df) > 0:
                        added = merge_into_parquet(token, new_df, market)
                        bars_added += added
                        fetched += 1
                        if i % 20 == 0 or i == len(tokens):
                            print(f"  [{i}/{len(tokens)}] {token}: +{added} bars", flush=True)
                    else:
                        no_data += 1
                except Exception as e:
                    print(f"  [{token}] ERROR: {e}")

        elapsed = time.time() - t0
        print()
        print(f"  Done in {elapsed:.0f}s: {fetched} tokens updated, {bars_added:,} bars added, {no_data} no data")
        print()

    if args.rebuild_cache:
        print("Rebuilding parquet cache...")
        for market in markets:
            os.system(f"/workspace/venv/bin/python tools/build_parquet_cache.py --market {market} --force --verbose")


if __name__ == "__main__":
    main()
