#!/usr/bin/env python3
"""
Coinalyze Data Fetcher — Optimized
====================================
Fetches daily OI and liquidation data from Coinalyze for our 28 traded tokens
across the top 7 exchanges (Binance, Bybit, OKX, BitMEX, Huobi, Bitfinex,
Hyperliquid).

Optimizations:
  - Uses pre-discovered manifest (manifest.json) with exact start dates per
    symbol — zero wasted calls on empty date ranges
  - Batches ALL exchanges for a token into ONE API request per yearly chunk
    (e.g. 7 symbols = 1 API call instead of 7)
  - Writes each exchange file to disk immediately after parsing (crash-safe)
  - Resume: reads last timestamp per file, only fetches newer data
  - Rate limit: 30s between batched requests (each symbol in a batch counts
    as 1 API call toward the 40/min limit)

Directory structure:
  data/alternative/coinalyze/
    manifest.json                          # pre-discovered start dates
    liquidations/{TOKEN}/{SYMBOL}.csv      # e.g. BTC/BTCUSDT_PERP.A.csv
    open_interest/{TOKEN}/{SYMBOL}.csv

Usage:
  python tools/fetch_coinalyze.py                    # fetch all (both OI + liq)
  python tools/fetch_coinalyze.py --tokens BTC ETH   # specific tokens
  python tools/fetch_coinalyze.py --data-type liq     # liquidations only
  python tools/fetch_coinalyze.py --data-type oi      # open interest only
  python tools/fetch_coinalyze.py --dry-run           # show plan without fetching
  python tools/fetch_coinalyze.py --discover          # re-discover manifest
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_KEY = os.environ.get(
    "COINALYZE_API_KEY", "3ff08019-ded5-4902-9351-400d57eb72db"
)
BASE_URL = "https://api.coinalyze.net/v1"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "alternative" / "coinalyze"
MANIFEST_PATH = DATA_DIR / "manifest.json"

def _discover_perp_tokens() -> list[str]:
    """Load perp token universe from v4.signals if available, else use fallback."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from v4.signals import discover_tokens
        return sorted(discover_tokens("perp"))
    except Exception:
        pass
    # Fallback: core 28 tokens
    return [
        "AAVE", "ADA", "APT", "ARB", "ATOM", "AVAX", "BNB", "BTC",
        "DOGE", "DOT", "ETH", "FIL", "IMX", "INJ", "LINK", "LTC",
        "NEAR", "ONDO", "OP", "POL", "SEI", "SOL", "SUI", "TIA",
        "TRX", "UNI", "WIF", "XRP",
    ]

TOKENS = _discover_perp_tokens()

TOP7 = {"A": "Binance", "6": "Bybit", "3": "OKX", "0": "BitMEX",
        "4": "Huobi", "F": "Bitfinex", "H": "Hyperliquid"}

# Rate limit: 40 calls/min. A batch of N symbols = N calls.
# With ~7 symbols/batch, that's 7 calls. Wait 12s between batches
# to stay well under 40/min (7 calls / 12s = 35/min).
BATCH_DELAY = 12.0

# Daily data per-request cap is ~429 points. Use 400-day chunks.
CHUNK_DAYS = 400

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "api_key": API_KEY,
    "User-Agent": "CryptoBacktest/1.0 (coinalyze-fetcher)",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_get(endpoint: str, params: dict, retries: int = 4) -> list | None:
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                wait = max(retry_after, 30) + 5
                print(f"    429 rate-limited. Waiting {wait}s ...", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code == 401:
                print("ERROR: 401 Unauthorized — check API key", flush=True)
                sys.exit(1)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            wait = BATCH_DELAY * (2 ** attempt)
            print(f"    Error: {e}. Retry {attempt+1}/{retries} in {wait:.0f}s", flush=True)
            time.sleep(wait)
    return None


def ts_to_date(ts: int) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


def last_timestamp(filepath: Path) -> int | None:
    if not filepath.exists() or filepath.stat().st_size == 0:
        return None
    with open(filepath, "rb") as f:
        try:
            f.seek(-512, 2)
        except OSError:
            f.seek(0)
        lines = f.readlines()
    if len(lines) < 2:
        return None
    last_line = lines[-1].decode("utf-8").strip()
    if not last_line or last_line.startswith("t,"):
        return None
    try:
        return int(last_line.split(",")[0])
    except (ValueError, IndexError):
        return None


def write_rows(filepath: Path, rows: list[dict], fieldnames: list[str]):
    exists = filepath.exists() and filepath.stat().st_size > 0
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------

def discover_manifest() -> dict:
    """Discover all available symbols and their data start dates.
    Uses batched requests to minimize API calls."""
    print("Discovering markets ...", flush=True)
    markets = api_get("future-markets", {})
    if not markets:
        print("ERROR: Failed to fetch markets")
        sys.exit(1)
    time.sleep(2)

    # Find primary USDT perp per token per exchange
    token_symbols: dict[str, dict[str, str]] = {}
    for token in TOKENS:
        token_symbols[token] = {}
        for m in markets:
            if not m.get("is_perpetual") or m["base_asset"] != token:
                continue
            ex = m["exchange"]
            if ex not in TOP7:
                continue
            existing = token_symbols[token].get(ex)
            if existing is None:
                token_symbols[token][ex] = m["symbol"]
            elif m.get("margined") == "STABLE" and "USDT" in m["symbol"].upper():
                token_symbols[token][ex] = m["symbol"]

    all_syms = []
    sym_info = {}
    for token in TOKENS:
        for ex, sym in token_symbols[token].items():
            all_syms.append(sym)
            sym_info[sym] = {"token": token, "exchange": ex, "exchange_name": TOP7[ex]}

    print(f"Found {len(all_syms)} symbols. Discovering start dates ...", flush=True)

    # Batch 18 symbols per discovery request
    DISC_BATCH = 18
    batches = [all_syms[i:i+DISC_BATCH] for i in range(0, len(all_syms), DISC_BATCH)]
    EPOCH = 1546300800  # 2019-01-01
    now_ts = int(time.time())

    for i, batch in enumerate(batches):
        sym_str = ",".join(batch)

        # OI
        data = api_get("open-interest-history",
                       {"symbols": sym_str, "interval": "daily", "from": EPOCH, "to": now_ts})
        if data:
            for d in data:
                h = d.get("history", [])
                if h and d["symbol"] in sym_info:
                    sym_info[d["symbol"]]["oi_start"] = h[0]["t"]
        time.sleep(30)

        # Liquidations
        data = api_get("liquidation-history",
                       {"symbols": sym_str, "interval": "daily", "from": EPOCH, "to": now_ts,
                        "convert_to_usd": "true"})
        if data:
            for d in data:
                h = d.get("history", [])
                if h and d["symbol"] in sym_info:
                    sym_info[d["symbol"]]["liq_start"] = h[0]["t"]
        time.sleep(30)

        print(f"  Batch {i+1}/{len(batches)} ({len(batch)} symbols)", flush=True)

    # Save manifest
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(sym_info, f, indent=2)

    oi_n = sum(1 for v in sym_info.values() if v.get("oi_start"))
    liq_n = sum(1 for v in sym_info.values() if v.get("liq_start"))
    print(f"Manifest saved: {len(sym_info)} symbols ({oi_n} OI, {liq_n} liq)", flush=True)
    return sym_info


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return discover_manifest()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_token(token: str, manifest: dict, data_type: str, dry_run: bool):
    """Fetch one data type for one token across all exchanges in batched requests."""
    if data_type == "liq":
        endpoint = "liquidation-history"
        fieldnames = ["t", "date", "l", "s"]
        subdir = "liquidations"
        start_key = "liq_start"
        extra = {"convert_to_usd": "true"}
    else:
        endpoint = "open-interest-history"
        fieldnames = ["t", "date", "o", "h", "l", "c"]
        subdir = "open_interest"
        start_key = "oi_start"
        extra = {"convert_to_usd": "true"}

    # Collect symbols for this token that have data
    symbols = {}  # symbol -> manifest entry
    for sym, info in manifest.items():
        if info["token"] == token and info.get(start_key):
            symbols[sym] = info

    if not symbols:
        return

    token_dir = DATA_DIR / subdir / token
    now_ts = int(time.time())

    # Check resume state per symbol
    sym_start: dict[str, int] = {}  # symbol -> effective start timestamp
    any_work = False
    for sym, info in symbols.items():
        filepath = token_dir / f"{sym}.csv"
        resume_ts = last_timestamp(filepath)
        data_start = info[start_key]
        start = max(data_start, resume_ts + 86400) if resume_ts else data_start
        sym_start[sym] = start
        if start < now_ts:
            any_work = True

    if not any_work:
        ex_list = ", ".join(info["exchange_name"] for info in symbols.values())
        print(f"  [{token}] {len(symbols)} ex — up to date ({ex_list})", flush=True)
        return

    # Print plan
    ex_list = ", ".join(symbols[s]["exchange_name"] for s in symbols)
    print(f"  [{token}] {len(symbols)} ex: {ex_list}", flush=True)
    if dry_run:
        for sym, start in sym_start.items():
            label = "up to date" if start >= now_ts else f"from {ts_to_date(start)}"
            print(f"    {sym:25s} — {label}", flush=True)
        return

    # Fetch in yearly chunks, all symbols batched per request
    global_start = min(sym_start.values())
    sym_list_str = ",".join(symbols.keys())
    chunk_secs = CHUNK_DAYS * 86400
    total_new = {sym: 0 for sym in symbols}

    cursor = global_start
    while cursor < now_ts:
        chunk_end = min(cursor + chunk_secs, now_ts)

        params = {
            "symbols": sym_list_str,
            "interval": "daily",
            "from": cursor,
            "to": chunk_end,
            **extra,
        }
        data = api_get(endpoint, params)

        if data:
            for entry in data:
                sym = entry.get("symbol")
                if sym not in symbols:
                    continue
                history = entry.get("history", [])
                if not history:
                    continue

                # Filter to rows newer than resume point
                resume = sym_start[sym]
                new_rows = [r for r in history if r["t"] >= resume]
                if not new_rows:
                    continue

                for row in new_rows:
                    row["date"] = ts_to_date(row["t"])

                filepath = token_dir / f"{sym}.csv"
                write_rows(filepath, new_rows, fieldnames)

                sym_start[sym] = new_rows[-1]["t"] + 86400
                total_new[sym] += len(new_rows)

        time.sleep(BATCH_DELAY)
        cursor = chunk_end + 86400

    # Report
    for sym, count in total_new.items():
        info = symbols[sym]
        if count > 0:
            fp = token_dir / f"{sym}.csv"
            kb = fp.stat().st_size / 1024 if fp.exists() else 0
            print(f"    {sym:25s} ({info['exchange_name']:12s}): +{count:>4d} days ({kb:.0f} KB)", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch Coinalyze OI + liquidation data")
    parser.add_argument("--tokens", nargs="+", default=None)
    parser.add_argument("--data-type", choices=["liq", "oi", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--discover", action="store_true",
                        help="Re-discover manifest (start dates per symbol)")
    args = parser.parse_args()

    tokens = [t.upper() for t in args.tokens] if args.tokens else TOKENS
    data_types = ["liq", "oi"] if args.data_type == "all" else [args.data_type]

    if args.discover or not MANIFEST_PATH.exists():
        manifest = discover_manifest()
    else:
        manifest = load_manifest()
        print(f"Loaded manifest: {len(manifest)} symbols", flush=True)

    for data_type in data_types:
        label = "Liquidations" if data_type == "liq" else "Open Interest"
        print(f"\n{'='*60}", flush=True)
        print(f"  {label} — daily, USD-converted", flush=True)
        print(f"{'='*60}\n", flush=True)

        for token in tokens:
            fetch_token(token, manifest, data_type, args.dry_run)

    # Summary
    print(f"\n{'='*60}", flush=True)
    print(f"Done. Data in {DATA_DIR}", flush=True)
    for subdir in ["liquidations", "open_interest"]:
        d = DATA_DIR / subdir
        if d.exists():
            files = list(d.rglob("*.csv"))
            total_size = sum(f.stat().st_size for f in files)
            print(f"  {subdir}: {len(files)} files, {total_size/1024:.0f} KB", flush=True)


if __name__ == "__main__":
    main()
