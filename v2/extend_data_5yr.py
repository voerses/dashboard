"""
Extend 1H and 4H data cache to 5 years (2021-01 → 2026-02).
=============================================================
Downloads hourly klines from Binance Vision for all tokens.
Saves to real_data/1h_cache/ and real_data/4h_cache/.

Does NOT modify existing daily CSVs or enriched data.

Usage:
    python extend_data_5yr.py              # Download all tokens
    python extend_data_5yr.py --token BTC  # Download single token
    python extend_data_5yr.py --check      # Check what we have
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import time
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import the download function from existing code
from mtf_swing_strategy import _download_1h_month, aggregate_1h_to_4h
from fetch_1m_data import BINANCE_VISION_TOKENS

START_YEAR = 2021
END_YEAR = 2026

CACHE_1H = 'real_data/1h_cache'
CACHE_4H = 'real_data/4h_cache'
# Backup dir for old 2-year data
BACKUP_1H = 'real_data/1h_cache_2yr_backup'
BACKUP_4H = 'real_data/4h_cache_2yr_backup'


def check_data():
    """Report current data coverage."""
    print("=" * 70)
    print("CURRENT DATA COVERAGE")
    print("=" * 70)

    for cache_dir, label in [(CACHE_1H, '1H'), (CACHE_4H, '4H')]:
        if not os.path.exists(cache_dir):
            print(f"\n{label} cache: MISSING")
            continue

        files = [f for f in os.listdir(cache_dir) if f.endswith('.parquet')]
        print(f"\n{label} cache: {len(files)} files")

        if files:
            # Sample a few
            for f in sorted(files)[:3] + sorted(files)[-2:]:
                path = os.path.join(cache_dir, f)
                df = pd.read_parquet(path)
                ticker = f.replace(f'_{label.lower()}.parquet', '')
                days = (df.index[-1] - df.index[0]).days
                print(f"  {ticker:>8}: {df.index[0].strftime('%Y-%m-%d')} → "
                      f"{df.index[-1].strftime('%Y-%m-%d')} "
                      f"({len(df):,} bars, {days/365:.1f}yr)")


def download_token(ticker, force=False):
    """Download 1H data for one token, INCREMENTAL — only fetches missing months.

    PROTECTED: This function ALWAYS does incremental downloads.
    It checks what months are already cached and only downloads new ones.
    This means changing START_YEAR or re-running will only fetch the diff.
    Data source is tagged as 'binance_vision' in parquet metadata.
    """
    os.makedirs(CACHE_1H, exist_ok=True)
    os.makedirs(CACHE_4H, exist_ok=True)

    cache_1h = os.path.join(CACHE_1H, f'{ticker}_1h.parquet')
    cache_4h = os.path.join(CACHE_4H, f'{ticker}_4h.parquet')

    # Load existing data to determine what months we already have
    existing_df = None
    existing_months = set()
    if os.path.exists(cache_1h) and not force:
        existing_df = pd.read_parquet(cache_1h)
        # Build set of (year, month) we already have
        if len(existing_df) > 0:
            for dt in existing_df.index:
                existing_months.add((dt.year, dt.month))

    # Check if we already cover the full target range
    if existing_df is not None and not force:
        start = existing_df.index[0]
        end = existing_df.index[-1]
        target_start_covered = start.year <= START_YEAR and start.month <= 2
        target_end_covered = end.year >= datetime.now().year and end.month >= datetime.now().month - 1
        if target_start_covered and target_end_covered:
            days = (end - start).days
            return ticker, len(existing_df), days, 'CACHED'

    # Only download months we DON'T already have (incremental diff)
    new_1h = []
    end_year = datetime.now().year
    months_downloaded = 0
    months_skipped = 0
    months_failed = 0

    for year in range(START_YEAR, end_year + 1):
        for month in range(1, 13):
            if year == end_year and month > datetime.now().month:
                break

            # SKIP months we already have (incremental!)
            if (year, month) in existing_months:
                months_skipped += 1
                continue

            df_1h = _download_1h_month(ticker, year, month)
            if df_1h is not None and len(df_1h) > 10:
                new_1h.append(df_1h)
                months_downloaded += 1
            else:
                months_failed += 1

            time.sleep(0.05)  # Rate limiting

    if not new_1h and existing_df is None:
        return ticker, 0, 0, 'NO_DATA'

    if not new_1h and existing_df is not None:
        # Nothing new to download, existing data is fine
        days = (existing_df.index[-1] - existing_df.index[0]).days
        return ticker, len(existing_df), days, 'CACHED'

    # Merge new data with existing
    frames = []
    if existing_df is not None:
        frames.append(existing_df)
    frames.extend(new_1h)
    all_df = pd.concat(frames)
    all_df = all_df[~all_df.index.duplicated(keep='last')]
    all_df.sort_index(inplace=True)

    # Save 1H with data source metadata
    import pyarrow as pa
    import pyarrow.parquet as pq

    table_1h = pa.Table.from_pandas(all_df)
    metadata = {
        b'data_source': b'binance_vision',
        b'interval': b'1h',
        b'ticker': ticker.encode(),
        b'download_date': datetime.now().isoformat().encode(),
        b'start_year': str(START_YEAR).encode(),
        b'incremental': b'true',
        b'months_downloaded': str(months_downloaded).encode(),
        b'months_from_cache': str(months_skipped).encode(),
    }
    # Merge with existing arrow metadata
    existing_meta = table_1h.schema.metadata or {}
    existing_meta.update(metadata)
    table_1h = table_1h.replace_schema_metadata(existing_meta)
    pq.write_table(table_1h, cache_1h)

    # Build and save 4H with metadata
    df_4h = aggregate_1h_to_4h(all_df)
    table_4h = pa.Table.from_pandas(df_4h)
    metadata[b'interval'] = b'4h'
    existing_meta_4h = table_4h.schema.metadata or {}
    existing_meta_4h.update(metadata)
    table_4h = table_4h.replace_schema_metadata(existing_meta_4h)
    pq.write_table(table_4h, cache_4h)

    days = (all_df.index[-1] - all_df.index[0]).days
    status = f'+{months_downloaded}mo' if months_skipped > 0 else f'{months_downloaded}mo'
    return ticker, len(all_df), days, status


def main():
    parser = argparse.ArgumentParser(description='Extend data to 5 years')
    parser.add_argument('--check', action='store_true', help='Check current coverage')
    parser.add_argument('--token', type=str, help='Download single token')
    parser.add_argument('--force', action='store_true', help='Force re-download')
    parser.add_argument('--backup', action='store_true', help='Backup old 2yr data first')
    parser.add_argument('--workers', type=int, default=4, help='Parallel downloads')
    args = parser.parse_args()

    if args.check:
        check_data()
        return

    tokens = [args.token] if args.token else BINANCE_VISION_TOKENS

    # Backup old data if requested
    if args.backup:
        import shutil
        for src, dst in [(CACHE_1H, BACKUP_1H), (CACHE_4H, BACKUP_4H)]:
            if os.path.exists(src) and not os.path.exists(dst):
                print(f"Backing up {src} → {dst}")
                shutil.copytree(src, dst)

    print("=" * 70)
    print(f"DOWNLOADING {START_YEAR}-{END_YEAR} DATA FOR {len(tokens)} TOKENS")
    print(f"Workers: {args.workers}  Force: {args.force}")
    print("=" * 70)

    t0 = time.time()
    results = []

    # Sequential to respect rate limits (Binance Vision is generous but let's be safe)
    for i, ticker in enumerate(tokens, 1):
        print(f"  [{i:>2}/{len(tokens)}] {ticker:>8}...", end=' ', flush=True)
        tk, bars, days, status = download_token(ticker, force=args.force)
        yrs = days / 365 if days > 0 else 0
        print(f"{bars:>6,} bars  {yrs:.1f}yr  [{status}]")
        results.append((tk, bars, days, status))

    elapsed = time.time() - t0

    print()
    print("=" * 70)
    print(f"COMPLETE — {elapsed:.0f}s")
    print("=" * 70)

    # Summary
    total_bars = sum(r[1] for r in results)
    with_data = sum(1 for r in results if r[1] > 0)
    five_yr = sum(1 for r in results if r[2] >= 1800)

    print(f"  Tokens with data: {with_data}/{len(tokens)}")
    print(f"  Tokens with 5yr+: {five_yr}/{len(tokens)}")
    print(f"  Total 1H bars:    {total_bars:,}")

    # Report tokens with less than 5 years
    short = [(r[0], r[2]) for r in results if 0 < r[2] < 1800]
    if short:
        print(f"\n  Tokens with < 5 years:")
        for tk, days in sorted(short, key=lambda x: x[1]):
            print(f"    {tk:>8}: {days/365:.1f} years ({days} days)")


if __name__ == '__main__':
    main()
