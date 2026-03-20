#!/workspace/venv/bin/python
"""
Fetch 1-minute USDT-M perpetual futures OHLCV data from Binance Vision (bulk archives).

Downloads monthly ZIP files from data.binance.vision (free, no API key needed),
extracts CSVs, and builds per-symbol parquet caches.

Source URL pattern:
    https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}USDT/1m/{SYMBOL}USDT-1m-{YEAR}-{MONTH:02d}.zip

Each ZIP contains a CSV with columns:
    open_time, open, high, low, close, volume, close_time,
    quote_volume, count, taker_buy_base, taker_buy_quote, ignore

Usage:
    python tools/fetch_binance_futures_1m.py                                    # all symbols from perp 1h_cache
    python tools/fetch_binance_futures_1m.py --symbols BTC ETH SOL             # specific symbols
    python tools/fetch_binance_futures_1m.py --start-year 2023 --end-year 2025 # custom date range
    python tools/fetch_binance_futures_1m.py --force                           # re-download everything
    python tools/fetch_binance_futures_1m.py --workers 8                       # increase parallelism
"""

import argparse
import io
import os
import sys
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.join(_TOOLS_DIR, "..")
DATA_DIR = os.path.join(_PROJECT_DIR, "data")
PERP_1H_CACHE = os.path.join(DATA_DIR, "perp", "1h_cache")
DEFAULT_OUTPUT_DIR = os.path.join(DATA_DIR, "perp", "1m_cache")

# Binance Vision URL pattern for USDT-M futures
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
# {BASE_URL}/{SYMBOL}USDT/1m/{SYMBOL}USDT-1m-{YEAR}-{MONTH:02d}.zip

# CSV columns from Binance Vision kline archives
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

# HTTP settings
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
BASE_BACKOFF_S = 2.0

# Session with connection pooling
_session = None


def get_session():
    """Get or create a requests session with connection pooling."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "crypto-backtest/1.0 (data fetcher)",
        })
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
                 or os.environ.get("https_proxy") or os.environ.get("http_proxy"))
        if proxy:
            _session.proxies = {"https": proxy, "http": proxy}
    return _session


# ---------------------------------------------------------------------------
# Symbol Discovery
# ---------------------------------------------------------------------------
def discover_symbols_from_cache(cache_dir):
    """Discover symbols from existing 1h parquet cache files.

    Files are named like BTC_1h.parquet -> symbol is BTC.
    """
    if not os.path.isdir(cache_dir):
        return []
    symbols = []
    for f in sorted(os.listdir(cache_dir)):
        if f.endswith("_1h.parquet"):
            sym = f.replace("_1h.parquet", "")
            if sym.isascii() and sym.isalnum():
                symbols.append(sym)
    return symbols


# ---------------------------------------------------------------------------
# Symbol name mapping (handle Binance futures naming quirks)
# ---------------------------------------------------------------------------

# Some tokens trade on Binance futures with a "1000" prefix
# (e.g., 1000PEPE, 1000SHIB). Our 1h_cache uses the plain name (PEPE, SHIB).
# We need to try both the plain name and the 1000-prefixed name.
_1000_TOKENS = {
    "PEPE", "SHIB", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
    "CHEEMS", "MOGUSDT", "WHY", "X", "APU", "NEIRO",
}


def get_binance_pair_names(symbol):
    """Return list of possible Binance pair names to try for a symbol."""
    pairs = [f"{symbol}USDT"]
    if symbol in _1000_TOKENS:
        pairs.insert(0, f"1000{symbol}USDT")
    return pairs


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def download_monthly_zip(symbol, year, month):
    """Download a single monthly kline ZIP from Binance Vision.

    Returns bytes of the ZIP file, or None if not available (404).
    Tries multiple pair name variants (e.g., PEPE -> 1000PEPEUSDT first).
    """
    pair_names = get_binance_pair_names(symbol)
    session = get_session()

    for pair in pair_names:
        url = f"{BASE_URL}/{pair}/1m/{pair}-1m-{year}-{month:02d}.zip"

        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 404:
                    break  # Try next pair name
                if resp.status_code == 200:
                    return resp.content
                # Other errors — retry
                if attempt < MAX_RETRIES - 1:
                    wait = BASE_BACKOFF_S * (2 ** attempt)
                    time.sleep(wait)
                else:
                    return None
            except (requests.ConnectionError, requests.Timeout):
                if attempt < MAX_RETRIES - 1:
                    wait = BASE_BACKOFF_S * (2 ** attempt)
                    time.sleep(wait)
                else:
                    return None

    return None  # All pair name variants returned 404


def parse_zip_csv(zip_bytes, symbol, year, month):
    """Extract and parse the CSV from a monthly kline ZIP.

    Returns a DataFrame with standard columns, or None if corrupt/empty.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                return None

            with zf.open(csv_names[0]) as csv_file:
                # Futures CSVs have headers; detect by checking if first field is numeric
                first_line = csv_file.readline().decode("utf-8", errors="replace")
                csv_file.seek(0)
                first_field = first_line.split(",")[0].strip()
                if first_field.isdigit():
                    df = pd.read_csv(csv_file, header=None, names=KLINE_COLUMNS)
                else:
                    df = pd.read_csv(csv_file)
                    df.columns = KLINE_COLUMNS[:len(df.columns)]

    except (zipfile.BadZipFile, Exception):
        return None

    if df.empty:
        return None

    # Detect timestamp unit
    sample_ts = df["open_time"].iloc[0]
    if sample_ts > 1e15:
        ts_unit = "us"
    elif sample_ts > 1e12:
        ts_unit = "ms"
    else:
        ts_unit = "s"

    df["timestamp"] = pd.to_datetime(df["open_time"], unit=ts_unit, utc=True).dt.tz_localize(None)

    out = pd.DataFrame({
        "timestamp": df["timestamp"],
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    })

    return out


# ---------------------------------------------------------------------------
# Per-symbol pipeline
# ---------------------------------------------------------------------------
def get_resume_start(parquet_path):
    """If parquet exists, return (year, month) to resume from."""
    if not os.path.exists(parquet_path):
        return None

    try:
        df = pd.read_parquet(parquet_path)
        if df.empty:
            return None
        last_ts = df.index.max()
        return (last_ts.year, last_ts.month)
    except Exception:
        return None


def generate_month_list(start_year, start_month, end_year, end_month):
    """Generate list of (year, month) tuples from start to end inclusive."""
    months = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def fetch_symbol(symbol, start_year, end_year, output_dir, force=False):
    """Fetch all 1m data for a single symbol. Returns (symbol, row_count, status)."""
    parquet_path = os.path.join(output_dir, f"{symbol}_1m.parquet")

    now = datetime.now(timezone.utc)
    end_y = min(end_year, now.year)
    end_m = now.month if end_y == now.year else 12

    # Determine start point (resume support)
    dl_start_year, dl_start_month = start_year, 1

    if not force and os.path.exists(parquet_path):
        resume = get_resume_start(parquet_path)
        if resume:
            r_year, r_month = resume
            if (r_year, r_month) >= (end_y, end_m):
                existing = pd.read_parquet(parquet_path)
                print(f"  [{symbol}] up to date ({len(existing):,} rows), skipping", flush=True)
                return symbol, len(existing), "skipped"
            dl_start_year, dl_start_month = r_year, r_month
            print(f"  [{symbol}] resuming from {r_year}-{r_month:02d} ...", flush=True)

    months = generate_month_list(dl_start_year, dl_start_month, end_y, end_m)
    if not months:
        return symbol, 0, "no_data"

    print(f"  [{symbol}] downloading {len(months)} months "
          f"({months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}) ...",
          flush=True)

    all_dfs = []
    downloaded = 0
    skipped_404 = 0

    for year, month in months:
        zip_bytes = download_monthly_zip(symbol, year, month)
        if zip_bytes is None:
            skipped_404 += 1
            continue

        df = parse_zip_csv(zip_bytes, symbol, year, month)
        if df is not None and not df.empty:
            all_dfs.append(df)
            downloaded += 1

    if not all_dfs and not force and os.path.exists(parquet_path):
        existing = pd.read_parquet(parquet_path)
        print(f"  [{symbol}] no new data available, keeping existing ({len(existing):,} rows)",
              flush=True)
        return symbol, len(existing), "unchanged"

    if not all_dfs:
        print(f"  [{symbol}] no data available ({skipped_404} months returned 404)", flush=True)
        return symbol, 0, "no_data"

    # Concatenate new data
    new_df = pd.concat(all_dfs, ignore_index=True)

    # If resuming, merge with existing data
    if not force and os.path.exists(parquet_path):
        try:
            existing = pd.read_parquet(parquet_path)
            existing = existing.reset_index()
            existing.rename(columns={existing.columns[0]: "timestamp"}, inplace=True)
            new_df = pd.concat([existing, new_df], ignore_index=True)
        except Exception as e:
            print(f"    WARNING: could not merge with existing parquet: {e}", flush=True)

    # Deduplicate and sort
    new_df = new_df.drop_duplicates(subset=["timestamp"], keep="last")
    new_df = new_df.sort_values("timestamp").reset_index(drop=True)

    # Set timestamp as index (matching 1h parquet schema)
    new_df = new_df.set_index("timestamp")
    new_df.index.name = None

    # Keep only OHLCV columns, ensure float64
    for col in ["open", "high", "low", "close", "volume"]:
        new_df[col] = new_df[col].astype("float64")
    new_df = new_df[["open", "high", "low", "close", "volume"]]

    # Drop any NaN rows
    new_df = new_df.dropna()

    # Save parquet
    os.makedirs(output_dir, exist_ok=True)
    new_df.to_parquet(parquet_path, engine="pyarrow")

    print(f"  [{symbol}] saved {len(new_df):,} rows "
          f"({new_df.index[0]} to {new_df.index[-1]}), "
          f"downloaded {downloaded} months, {skipped_404} 404s", flush=True)

    return symbol, len(new_df), "fetched"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(results, elapsed_s):
    """Print a summary of the fetch operation."""
    print("\n" + "=" * 70, flush=True)
    print("1M FUTURES FETCH SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"Elapsed: {elapsed_s:.1f}s\n", flush=True)

    fetched = [(s, n) for s, n, status in results if status == "fetched"]
    skipped = [(s, n) for s, n, status in results if status == "skipped"]
    unchanged = [(s, n) for s, n, status in results if status == "unchanged"]
    no_data = [(s, n) for s, n, status in results if status == "no_data"]
    errors = [(s, n) for s, n, status in results if status == "error"]

    total_rows = sum(n for _, n in fetched) + sum(n for _, n in skipped) + sum(n for _, n in unchanged)

    print(f"  Fetched:    {len(fetched)} symbols, "
          f"{sum(n for _, n in fetched):,} total rows", flush=True)
    print(f"  Skipped:    {len(skipped)} symbols (already up to date)", flush=True)
    print(f"  Unchanged:  {len(unchanged)} symbols (no new months available)", flush=True)
    print(f"  No data:    {len(no_data)} symbols (not on Binance futures)", flush=True)
    print(f"  Errors:     {len(errors)} symbols", flush=True)
    print(f"\n  Total rows: {total_rows:,}", flush=True)

    # Estimate disk usage
    if fetched:
        avg_bytes_per_row = 48  # approximate for 5 float64 cols + datetime index
        est_gb = total_rows * avg_bytes_per_row / 1e9
        print(f"  Est. disk:  {est_gb:.1f} GB (parquet compressed)", flush=True)

    if no_data:
        print(f"\n  Missing: {', '.join(s for s, _ in no_data[:20])}", flush=True)
        if len(no_data) > 20:
            print(f"  ... and {len(no_data) - 20} more", flush=True)
    if errors:
        print(f"\n  Errored: {', '.join(s for s, _ in errors)}", flush=True)

    print("=" * 70, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Binance USDT-M futures 1m OHLCV data from data.binance.vision."
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="Symbols to fetch (e.g., BTC ETH SOL). "
             "Default: all symbols from data/perp/1h_cache/."
    )
    parser.add_argument(
        "--start-year", type=int, default=2020,
        help="Start year for data download (default: 2020)."
    )
    parser.add_argument(
        "--end-year", type=int, default=2026,
        help="End year for data download (default: 2026)."
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help=f"Output directory for parquet files (default: {DEFAULT_OUTPUT_DIR})."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if parquet files already exist."
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel workers for symbol-level parallelism (default: 4)."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    # Determine symbols
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = discover_symbols_from_cache(PERP_1H_CACHE)
        if not symbols:
            print("ERROR: No symbols found in perp 1h_cache and none specified via --symbols.",
                  file=sys.stderr, flush=True)
            sys.exit(1)
        print(f"Discovered {len(symbols)} symbols from perp 1h_cache", flush=True)

    now = datetime.now(timezone.utc)
    print("=" * 70, flush=True)
    print("Fetch 1M Futures OHLCV — Binance Vision", flush=True)
    print("=" * 70, flush=True)
    print(f"Symbols:    {len(symbols)}", flush=True)
    print(f"Date range: {args.start_year}-01 to {min(args.end_year, now.year)}-{now.month:02d}", flush=True)
    print(f"Output:     {output_dir}", flush=True)
    print(f"Workers:    {args.workers}", flush=True)
    print(f"Force:      {args.force}", flush=True)
    print(flush=True)

    t0 = time.time()
    all_results = []

    workers = max(1, min(args.workers, 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                fetch_symbol, symbol, args.start_year, args.end_year,
                output_dir, args.force
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"  [{symbol}] FATAL ERROR: {e}", flush=True)
                traceback.print_exc()
                all_results.append((symbol, 0, "error"))

    elapsed = time.time() - t0
    print_summary(all_results, elapsed)


if __name__ == "__main__":
    main()
