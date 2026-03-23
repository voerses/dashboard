#!/workspace/venv/bin/python
"""
Fetch hourly Open Interest history from Bybit for top tokens.
Bybit's /v5/market/open-interest endpoint supports deep historical data
with cursor-based and endTime-based pagination.

Saves CSV files to data/perp/bybit_oi/{TOKEN}_oi_1h.csv
"""

import csv
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ──────────────────────────────────────────────────────────────
PROXIES = {
    'http': os.environ.get('HTTP_PROXY', 'http://10.100.2.10:3128'),
    'https': os.environ.get('HTTPS_PROXY', 'http://10.100.2.10:3128'),
}

BASE_URL = 'https://api.bybit.com/v5/market/open-interest'
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'perp', 'bybit_oi')

# Top 15 tokens by perpetual volume (cross-listed on Binance + Bybit)
TOKENS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE',
    'ADA', 'AVAX', 'LINK', 'DOT', 'MATIC',
    'ARB', 'OP', 'SUI', 'APT', 'NEAR',
]

# Fetch from 2024-01-01 to present (gives train: Jan-Jun 2025, test: Jul 2025 - Mar 2026)
START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Rate limit: Bybit allows 120 req/5s for public endpoints
RATE_LIMIT_INTERVAL = 0.15  # seconds between requests


# ── Fetcher ─────────────────────────────────────────────────────────────
def fetch_oi_for_token(token: str) -> tuple:
    """Fetch all hourly OI data for a token from START_DATE to now."""
    symbol = f'{token}USDT'
    outpath = os.path.join(OUT_DIR, f'{token}_oi_1h.csv')

    # If file already exists with sufficient data, skip
    if os.path.exists(outpath):
        with open(outpath, 'r') as f:
            lines = sum(1 for _ in f) - 1
        if lines > 1000:
            print(f'  [{token}] already has {lines} rows, skipping', flush=True)
            return token, lines, True

    print(f'  [{token}] fetching OI from Bybit...', flush=True)
    all_rows = []
    now_ms = int(time.time() * 1000)
    start_ms = int(START_DATE.timestamp() * 1000)

    # Bybit returns data in DESCENDING order (newest first)
    # We paginate backwards using endTime
    end_ms = now_ms
    page = 0

    while end_ms > start_ms:
        page += 1
        params = {
            'category': 'linear',
            'symbol': symbol,
            'intervalTime': '1h',
            'limit': 200,
            'endTime': end_ms,
        }

        for attempt in range(5):
            try:
                time.sleep(RATE_LIMIT_INTERVAL)
                resp = requests.get(BASE_URL, params=params, proxies=PROXIES, timeout=30)
                data = resp.json()
                break
            except Exception as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise

        if data.get('retCode') != 0:
            print(f'  [{token}] API error: {data.get("retMsg")}', flush=True)
            break

        entries = data['result'].get('list', [])
        if not entries:
            break

        for entry in entries:
            ts = int(entry['timestamp'])
            all_rows.append({
                'timestamp': ts,
                'datetime': datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
                'open_interest': float(entry['openInterest']),
            })

        # Move endTime to before the oldest entry in this batch
        oldest_ts = int(entries[-1]['timestamp'])
        if oldest_ts >= end_ms:
            break  # No progress
        end_ms = oldest_ts - 1

        if page % 50 == 0:
            oldest_dt = datetime.fromtimestamp(oldest_ts / 1000, tz=timezone.utc)
            print(f'  [{token}] page {page}, {len(all_rows)} rows, oldest: {oldest_dt.date()}', flush=True)

    if not all_rows:
        print(f'  [{token}] no data returned', flush=True)
        return token, 0, False

    # Sort chronologically (we fetched in reverse order)
    all_rows.sort(key=lambda r: r['timestamp'])

    # Deduplicate by timestamp
    seen = set()
    deduped = []
    for row in all_rows:
        if row['timestamp'] not in seen:
            seen.add(row['timestamp'])
            deduped.append(row)

    # Filter to only >= START_DATE
    deduped = [r for r in deduped if r['timestamp'] >= start_ms]

    # Write CSV
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(outpath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['timestamp', 'datetime', 'open_interest'])
        writer.writeheader()
        writer.writerows(deduped)

    first_dt = datetime.fromtimestamp(deduped[0]['timestamp'] / 1000, tz=timezone.utc)
    last_dt = datetime.fromtimestamp(deduped[-1]['timestamp'] / 1000, tz=timezone.utc)
    print(f'  [{token}] done: {len(deduped)} rows, {first_dt.date()} to {last_dt.date()}', flush=True)
    return token, len(deduped), False


# ── Main ────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    tokens = sys.argv[1:] if len(sys.argv) > 1 else TOKENS

    print(f'Fetching Bybit OI for {len(tokens)} tokens: {", ".join(tokens)}', flush=True)
    print(f'Start date: {START_DATE.date()}', flush=True)
    print(f'Output: {OUT_DIR}', flush=True)
    print(flush=True)

    t0 = time.time()
    results = []

    # Sequential to respect rate limits
    for token in tokens:
        try:
            result = fetch_oi_for_token(token)
            results.append(result)
        except Exception as e:
            print(f'  [{token}] FAILED: {e}', flush=True)
            results.append((token, 0, False))

    elapsed = time.time() - t0
    print(f'\n{"="*60}', flush=True)
    print(f'FETCH COMPLETE in {elapsed:.0f}s', flush=True)
    for token, count, skipped in results:
        status = 'skipped' if skipped else f'{count:,} rows'
        print(f'  {token:8s}: {status}', flush=True)
    print(f'{"="*60}', flush=True)


if __name__ == '__main__':
    main()
