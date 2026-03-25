#!/usr/bin/env python3
"""
GMGN Smart Money & DexScreener Data Fetcher
=============================================
Fetches smart money proxy signals for meme/volatile tokens:

1. GMGN.AI REST -- smart money wallet activity per token (if accessible)
2. DexScreener (fallback) -- DEX pair volume/liquidity/txns as smart money proxy

Data is aggregated to hourly frequency and stored as parquet per token.

Schema (hourly):
    timestamp | smart_buy_count | smart_sell_count | smart_net_flow_usd |
    dex_volume_usd | dex_buys | dex_sells | dex_liquidity_usd

Storage: data/alternative/gmgn_ai/{TOKEN}_smart_money.parquet

Usage:
    python tools/fetch_gmgn_smart_money.py [--tokens BONK,WIF,...] [--force]
"""

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "alternative" / "gmgn_ai"
TOKEN_MAP_PATH = DATA_DIR / "token_map.json"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; CryptoBacktest/1.0)",
    "Accept": "application/json",
})

# Rate limiting
DEXSCREENER_DELAY = 0.25
GMGN_DELAY = 5.0

# DexScreener base URL
DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"


# ---------------------------------------------------------------------------
# Helpers (following fetch_alternative_data.py pattern)
# ---------------------------------------------------------------------------

def safe_request(url, params=None, retries=3, backoff=2.0, timeout=30):
    """Make an HTTP GET with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"    Rate limited (429). Sleeping {wait:.0f}s ...")
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                wait = 60
                print(f"    IP banned (418). Sleeping {wait}s ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"    Request error: {e}. Retrying in {wait:.0f}s ...")
                time.sleep(wait)
            else:
                raise
    return None


def report_file(path):
    """Print file size info."""
    if path.exists():
        size_kb = path.stat().st_size / 1024
        print(f"    Saved: {path.name} ({size_kb:.1f} KB)")
    else:
        print(f"    WARNING: {path.name} not found!")


def load_token_map():
    """Load the token address mapping."""
    if not TOKEN_MAP_PATH.exists():
        raise FileNotFoundError(f"Token map not found: {TOKEN_MAP_PATH}")
    with open(TOKEN_MAP_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Source 1: GMGN.AI Smart Money Endpoints (experimental)
# ---------------------------------------------------------------------------

def try_gmgn_smart_money(chain, address, token):
    """Attempt to fetch smart money data from GMGN.AI REST API.

    GMGN endpoints are undocumented and may require auth.
    Returns None if unavailable.
    """
    endpoints = [
        f"https://gmgn.ai/defi/quotation/v1/tokens/top_buyers/{chain}/{address}",
        f"https://gmgn.ai/defi/quotation/v1/tokens/smart_money/{chain}/{address}",
        f"https://gmgn.ai/api/v1/token_stat/{chain}/{address}",
    ]

    for url in endpoints:
        try:
            resp = safe_request(url, retries=1, timeout=15)
            if resp is None:
                continue
            data = resp.json()
            if isinstance(data, dict) and data.get("code") == 0 and "data" in data:
                print(f"    GMGN: Got data from endpoint")
                return _parse_gmgn_response(data, token)
        except Exception:
            continue
        time.sleep(GMGN_DELAY)

    return None


def _parse_gmgn_response(data, token):
    """Parse GMGN JSON response into hourly DataFrame."""
    try:
        inner = data.get("data", {})

        if isinstance(inner, list) and len(inner) > 0:
            rows = []
            for item in inner:
                ts = item.get("timestamp") or item.get("time") or item.get("created_at")
                if ts is None:
                    continue
                row = {
                    "timestamp": pd.to_datetime(ts, unit="s" if isinstance(ts, (int, float)) else None),
                    "smart_buy_count": 1 if item.get("type") in ("buy", "Buy") else 0,
                    "smart_sell_count": 1 if item.get("type") in ("sell", "Sell") else 0,
                    "smart_net_flow_usd": float(item.get("amount_usd", 0) or 0),
                }
                if item.get("type") in ("sell", "Sell"):
                    row["smart_net_flow_usd"] *= -1
                rows.append(row)

            if rows:
                df = pd.DataFrame(rows)
                df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
                df = df.set_index("timestamp").resample("1h").agg({
                    "smart_buy_count": "sum",
                    "smart_sell_count": "sum",
                    "smart_net_flow_usd": "sum",
                }).dropna(how="all")
                df = df.reset_index()
                return df
    except Exception as e:
        print(f"    GMGN parse error: {e}")

    return None


# ---------------------------------------------------------------------------
# Source 2: DexScreener (free, reliable fallback)
# ---------------------------------------------------------------------------

def fetch_dexscreener_pairs(chain, address, token):
    """Fetch token pair data from DexScreener.

    Returns a single-row DataFrame with current hour snapshot.
    """
    url = f"{DEXSCREENER_BASE}/tokens/{address}"
    try:
        resp = safe_request(url, retries=2, timeout=15)
        if resp is None:
            return None
        data = resp.json()

        pairs = data.get("pairs", [])
        if not pairs:
            print(f"    DexScreener: no pairs found for {token}")
            return None

        # Use the highest-liquidity pair
        pairs_sorted = sorted(
            pairs,
            key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0),
            reverse=True,
        )
        pair = pairs_sorted[0]

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        now_naive = now.replace(tzinfo=None)

        txns = pair.get("txns", {})
        h1_txns = txns.get("h1", {})

        dex_buys = int(h1_txns.get("buys", 0) or 0)
        dex_sells = int(h1_txns.get("sells", 0) or 0)
        dex_volume = float(pair.get("volume", {}).get("h1", 0) or 0)
        dex_liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)

        # Smart money proxy: net buy pressure from txn counts + volume
        total_txns = dex_buys + dex_sells
        if total_txns > 0:
            buy_ratio = dex_buys / total_txns
            smart_net_flow = dex_volume * (buy_ratio - 0.5) * 2
        else:
            smart_net_flow = 0.0

        row = {
            "timestamp": now_naive,
            "smart_buy_count": dex_buys,
            "smart_sell_count": dex_sells,
            "smart_net_flow_usd": smart_net_flow,
            "dex_volume_usd": dex_volume,
            "dex_buys": dex_buys,
            "dex_sells": dex_sells,
            "dex_liquidity_usd": dex_liquidity,
        }

        return pd.DataFrame([row])

    except Exception as e:
        print(f"    DexScreener error for {token}: {e}")
        return None


# ---------------------------------------------------------------------------
# Storage: incremental parquet append
# ---------------------------------------------------------------------------

SCHEMA_COLS = [
    "timestamp", "smart_buy_count", "smart_sell_count", "smart_net_flow_usd",
    "dex_volume_usd", "dex_buys", "dex_sells", "dex_liquidity_usd",
]


def load_existing(token):
    """Load existing parquet data for a token, or empty DataFrame."""
    path = DATA_DIR / f"{token}_smart_money.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        return df
    return pd.DataFrame(columns=SCHEMA_COLS)


def save_token_data(token, df):
    """Save token data to parquet, deduplicating by timestamp."""
    if df.empty:
        return

    for col in SCHEMA_COLS:
        if col not in df.columns:
            df[col] = 0 if col != "timestamp" else pd.NaT

    keep_cols = [c for c in df.columns if c in SCHEMA_COLS or c.startswith("dex_")]
    df = df[keep_cols].copy()

    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").reset_index(drop=True)

    path = DATA_DIR / f"{token}_smart_money.parquet"
    df.to_parquet(path, index=False)
    report_file(path)


# ---------------------------------------------------------------------------
# Main fetch logic
# ---------------------------------------------------------------------------

def fetch_token(token, chain, address, force=False):
    """Fetch smart money data for a single token."""
    print(f"\n  {token} ({chain}/{address[:16]}...):")

    existing = load_existing(token)
    if not existing.empty:
        last_ts = existing["timestamp"].max()
        if not force:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if (now - last_ts).total_seconds() < 7200:
                print(f"    Already up to date (last: {last_ts})")
                return True
        print(f"    Existing: {len(existing)} rows, last: {last_ts}")

    new_data = None

    # Try GMGN first
    if address != "native":
        try:
            gmgn_df = try_gmgn_smart_money(chain, address, token)
            if gmgn_df is not None and not gmgn_df.empty:
                print(f"    GMGN: {len(gmgn_df)} hourly rows")
                new_data = gmgn_df
        except Exception as e:
            print(f"    GMGN failed: {e}")

    # Fall back to DexScreener
    if new_data is None and address != "native":
        try:
            dex_df = fetch_dexscreener_pairs(chain, address, token)
            if dex_df is not None and not dex_df.empty:
                print(f"    DexScreener: {len(dex_df)} rows (snapshot)")
                new_data = dex_df
        except Exception as e:
            print(f"    DexScreener failed: {e}")
        time.sleep(DEXSCREENER_DELAY)
    elif address == "native":
        print(f"    Skipping {token} (native token, no DEX contract)")
        return False

    if new_data is None:
        print(f"    No data fetched for {token}")
        return False

    # Merge with existing
    if not existing.empty:
        combined = pd.concat([existing, new_data], ignore_index=True)
    else:
        combined = new_data

    save_token_data(token, combined)
    print(f"    Total: {len(combined)} rows")
    return True


def main():
    parser = argparse.ArgumentParser(description="Fetch GMGN smart money / DexScreener data")
    parser.add_argument("--tokens", type=str, default=None,
                        help="Comma-separated token list (default: all in token_map)")
    parser.add_argument("--force", action="store_true",
                        help="Force re-fetch even if recent data exists")
    args = parser.parse_args()

    start = datetime.now(timezone.utc)
    print(f"Smart Money Data Fetcher -- started at {start.isoformat()}")
    print(f"Output directory: {DATA_DIR}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    token_map = load_token_map()

    if args.tokens:
        selected = [t.strip().upper() for t in args.tokens.split(",")]
        token_map = {k: v for k, v in token_map.items() if k in selected}

    print(f"Tokens to fetch: {len(token_map)}")

    results = {}
    for token, info in token_map.items():
        chain = info["chain"]
        address = info["address"]
        try:
            ok = fetch_token(token, chain, address, force=args.force)
            results[token] = ok
        except Exception as e:
            print(f"    FATAL ERROR for {token}: {e}")
            traceback.print_exc()
            results[token] = False

    # Summary
    end = datetime.now(timezone.utc)
    elapsed = (end - start).total_seconds()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok_count = sum(1 for v in results.values() if v)
    fail_count = sum(1 for v in results.values() if not v)
    print(f"  Success: {ok_count}, Failed: {fail_count}")
    for token, ok in sorted(results.items()):
        status = "OK" if ok else "FAILED"
        print(f"    {token:12s} {status}")
    print(f"\n  Elapsed: {elapsed:.1f}s")

    # List parquet files
    print("\n  Data files:")
    for p in sorted(DATA_DIR.glob("*_smart_money.parquet")):
        size_kb = p.stat().st_size / 1024
        df = pd.read_parquet(p)
        print(f"    {p.name:40s} {size_kb:>8.1f} KB  {len(df):>6d} rows")

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
