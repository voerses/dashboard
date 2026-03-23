#!/usr/bin/env python3
"""
Unified Alternative Data Fetcher
=================================
Collects data from 5 free alternative data sources:
1. Binance Futures Positioning (long/short ratios, taker buy/sell volume)
2. DefiLlama Stablecoin Supply
3. Fear & Greed Index
4. Binance Open Interest History
5. Cross-Market Macro via yfinance

All data saved to data/alternative/ as parquet or JSON.
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "alternative"

TOKENS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
    "SUIUSDT", "PEPEUSDT", "ARBUSDT", "OPUSDT", "NEARUSDT",
    "AAVEUSDT", "UNIUSDT", "LTCUSDT", "WIFUSDT", "INJUSDT",
]

BINANCE_FUTURES_BASE = "https://fapi.binance.com/futures/data"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; CryptoBacktest/1.0)"
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_request(url, params=None, retries=3, backoff=2.0):
    """Make an HTTP GET with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"    Rate limited (429). Sleeping {wait:.0f}s ...")
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                # Binance IP ban - wait longer
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


def report_file(path: Path):
    """Print file size and basic info."""
    if path.exists():
        size_kb = path.stat().st_size / 1024
        print(f"    Saved: {path.name} ({size_kb:.1f} KB)")
    else:
        print(f"    WARNING: {path.name} not found!")


def report_df(df: pd.DataFrame, label: str):
    """Print row count and date range for a dataframe."""
    print(f"    {label}: {len(df)} rows", end="")
    for col in ["timestamp", "date", "Date"]:
        if col in df.columns:
            try:
                dates = pd.to_datetime(df[col])
                print(f", range: {dates.min()} to {dates.max()}", end="")
            except Exception:
                pass
            break
    if df.index.name and "date" in str(df.index.name).lower():
        try:
            print(f", range: {df.index.min()} to {df.index.max()}", end="")
        except Exception:
            pass
    print()


# ===========================================================================
# SOURCE 1: Binance Futures Positioning Data
# ===========================================================================
def fetch_binance_positioning():
    print("\n" + "=" * 70)
    print("SOURCE 1: Binance Futures Positioning Data")
    print("=" * 70)

    out_dir = BASE_DIR / "binance_positioning"
    out_dir.mkdir(parents=True, exist_ok=True)

    endpoints = {
        "globalLongShortAccountRatio": "global_ls_ratio",
        "topLongShortAccountRatio": "top_ls_account_ratio",
        "topLongShortPositionRatio": "top_ls_position_ratio",
        "takerlongshortRatio": "taker_buy_sell_vol",
    }

    total_rows = 0
    for endpoint_name, file_prefix in endpoints.items():
        url = f"{BINANCE_FUTURES_BASE}/{endpoint_name}"
        print(f"\n  Endpoint: {endpoint_name}")
        all_frames = []

        for i, symbol in enumerate(TOKENS):
            params = {"symbol": symbol, "period": "1h", "limit": 500}
            try:
                resp = safe_request(url, params=params)
                if resp is None:
                    print(f"    {symbol}: failed after retries")
                    continue
                data = resp.json()
                if isinstance(data, dict) and "code" in data:
                    print(f"    {symbol}: API error: {data.get('msg', data)}")
                    continue
                if not data:
                    print(f"    {symbol}: empty response")
                    continue
                df = pd.DataFrame(data)
                df["symbol"] = symbol
                all_frames.append(df)
                print(f"    {symbol}: {len(df)} rows")
            except Exception as e:
                print(f"    {symbol}: ERROR - {e}")

            # Rate limit: sleep 200ms between requests
            if i < len(TOKENS) - 1:
                time.sleep(0.2)

        if all_frames:
            combined = pd.concat(all_frames, ignore_index=True)
            # Convert timestamp
            if "timestamp" in combined.columns:
                combined["timestamp"] = pd.to_datetime(
                    combined["timestamp"], unit="ms", utc=True
                )
            out_path = out_dir / f"{file_prefix}.parquet"
            combined.to_parquet(out_path, index=False)
            total_rows += len(combined)
            report_df(combined, file_prefix)
            report_file(out_path)
        else:
            print(f"    No data collected for {endpoint_name}")

        # Sleep between endpoints
        time.sleep(1.0)

    print(f"\n  TOTAL positioning rows: {total_rows}")
    return total_rows > 0


# ===========================================================================
# SOURCE 2: DefiLlama Stablecoin Supply
# ===========================================================================
def fetch_stablecoin_supply():
    print("\n" + "=" * 70)
    print("SOURCE 2: DefiLlama Stablecoin Supply")
    print("=" * 70)

    out_dir = BASE_DIR / "stablecoin_supply"
    out_dir.mkdir(parents=True, exist_ok=True)

    stablecoins = {
        1: "USDT",
        2: "USDC",
        4: "DAI",
        5: "BUSD",
    }

    for sc_id, name in stablecoins.items():
        url = f"https://stablecoins.llama.fi/stablecoin/{sc_id}"
        print(f"\n  Fetching {name} (id={sc_id}) ...")
        try:
            resp = safe_request(url)
            if resp is None:
                print(f"    {name}: failed after retries")
                continue
            data = resp.json()

            # Save raw JSON for reference
            json_path = out_dir / f"{name.lower()}_raw.json"
            with open(json_path, "w") as f:
                json.dump(data, f)
            report_file(json_path)

            # Extract chain-level time series into a parquet
            # The response has: tokens[chainName] -> [{date, totalCirculating, ...}]
            if "chainBalances" in data:
                rows = []
                for chain, chain_data in data["chainBalances"].items():
                    if "tokens" in chain_data:
                        for entry in chain_data["tokens"]:
                            row = {
                                "date": entry.get("date"),
                                "chain": chain,
                                "stablecoin": name,
                            }
                            circ = entry.get("circulating", {})
                            if isinstance(circ, dict):
                                row["circulating_usd"] = circ.get(
                                    "peggedUSD", 0
                                )
                            else:
                                row["circulating_usd"] = circ
                            rows.append(row)
                if rows:
                    df = pd.DataFrame(rows)
                    df["date"] = pd.to_datetime(df["date"], unit="s", utc=True)
                    pq_path = out_dir / f"{name.lower()}_supply.parquet"
                    df.to_parquet(pq_path, index=False)
                    report_df(df, name)
                    report_file(pq_path)
            else:
                print(f"    {name}: no chainBalances in response")

        except Exception as e:
            print(f"    {name}: ERROR - {e}")
            traceback.print_exc()

        time.sleep(0.5)

    return True


# ===========================================================================
# SOURCE 3: Fear & Greed Index
# ===========================================================================
def fetch_fear_greed():
    print("\n" + "=" * 70)
    print("SOURCE 3: Fear & Greed Index")
    print("=" * 70)

    out_dir = BASE_DIR / "fear_greed"
    out_dir.mkdir(parents=True, exist_ok=True)

    url = "https://api.alternative.me/fng/"
    params = {"limit": 0, "format": "json"}

    try:
        resp = safe_request(url, params=params)
        if resp is None:
            print("  Failed after retries")
            return False
        data = resp.json()

        # Save raw JSON
        json_path = out_dir / "fear_greed_raw.json"
        with open(json_path, "w") as f:
            json.dump(data, f)
        report_file(json_path)

        # Parse into dataframe
        if "data" in data:
            records = data["data"]
            df = pd.DataFrame(records)
            df["timestamp"] = pd.to_datetime(
                df["timestamp"].astype(int), unit="s", utc=True
            )
            df["value"] = df["value"].astype(int)
            # Sort chronologically
            df = df.sort_values("timestamp").reset_index(drop=True)

            pq_path = out_dir / "fear_greed_index.parquet"
            df.to_parquet(pq_path, index=False)
            report_df(df, "Fear & Greed")
            report_file(pq_path)
            return True
        else:
            print("  No 'data' key in response")
            return False

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return False


# ===========================================================================
# SOURCE 4: Binance Open Interest History
# ===========================================================================
def fetch_binance_oi():
    print("\n" + "=" * 70)
    print("SOURCE 4: Binance Open Interest History")
    print("=" * 70)

    out_dir = BASE_DIR / "binance_oi"
    out_dir.mkdir(parents=True, exist_ok=True)

    url = f"{BINANCE_FUTURES_BASE}/openInterestHist"
    all_frames = []

    for i, symbol in enumerate(TOKENS):
        params = {"symbol": symbol, "period": "1h", "limit": 500}
        try:
            resp = safe_request(url, params=params)
            if resp is None:
                print(f"    {symbol}: failed after retries")
                continue
            data = resp.json()
            if isinstance(data, dict) and "code" in data:
                print(f"    {symbol}: API error: {data.get('msg', data)}")
                continue
            if not data:
                print(f"    {symbol}: empty response")
                continue
            df = pd.DataFrame(data)
            df["symbol"] = symbol
            all_frames.append(df)
            print(f"    {symbol}: {len(df)} rows")
        except Exception as e:
            print(f"    {symbol}: ERROR - {e}")

        # Rate limit
        if i < len(TOKENS) - 1:
            time.sleep(0.2)

    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        if "timestamp" in combined.columns:
            combined["timestamp"] = pd.to_datetime(
                combined["timestamp"], unit="ms", utc=True
            )
        # Convert numeric columns
        for col in ["sumOpenInterest", "sumOpenInterestValue"]:
            if col in combined.columns:
                combined[col] = pd.to_numeric(combined[col], errors="coerce")

        pq_path = out_dir / "open_interest_history.parquet"
        combined.to_parquet(pq_path, index=False)
        report_df(combined, "Open Interest")
        report_file(pq_path)
        return True
    else:
        print("  No data collected")
        return False


# ===========================================================================
# SOURCE 5: Cross-Market Macro (yfinance)
# ===========================================================================
def fetch_macro():
    print("\n" + "=" * 70)
    print("SOURCE 5: Cross-Market Macro (yfinance)")
    print("=" * 70)

    out_dir = BASE_DIR / "macro"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import yfinance as yf
    except ImportError:
        print("  ERROR: yfinance not installed. pip install yfinance")
        return False

    symbols = {
        "DX-Y.NYB": "usd_index",
        "^VIX": "vix",
        "^GSPC": "sp500",
        "^IXIC": "nasdaq",
        "^TNX": "us10y_yield",
        "GC=F": "gold",
    }

    for ticker, name in symbols.items():
        print(f"\n  Fetching {name} ({ticker}) ...")
        try:
            t = yf.Ticker(ticker)
            df = t.history(period="max", interval="1d")
            if df.empty:
                print(f"    {name}: no data returned")
                continue

            # Reset index to make Date a column
            df = df.reset_index()
            # Flatten timezone-aware datetimes
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)

            pq_path = out_dir / f"{name}.parquet"
            df.to_parquet(pq_path, index=False)
            report_df(df, name)
            report_file(pq_path)

        except Exception as e:
            print(f"    {name}: ERROR - {e}")
            traceback.print_exc()

        time.sleep(0.5)

    return True


# ===========================================================================
# Main
# ===========================================================================
def main():
    start = datetime.now(timezone.utc)
    print(f"Alternative Data Fetcher — started at {start.isoformat()}")
    print(f"Output directory: {BASE_DIR}")

    results = {}

    # Source 1
    try:
        results["binance_positioning"] = fetch_binance_positioning()
    except Exception as e:
        print(f"  FATAL ERROR in Source 1: {e}")
        traceback.print_exc()
        results["binance_positioning"] = False

    # Source 2
    try:
        results["stablecoin_supply"] = fetch_stablecoin_supply()
    except Exception as e:
        print(f"  FATAL ERROR in Source 2: {e}")
        traceback.print_exc()
        results["stablecoin_supply"] = False

    # Source 3
    try:
        results["fear_greed"] = fetch_fear_greed()
    except Exception as e:
        print(f"  FATAL ERROR in Source 3: {e}")
        traceback.print_exc()
        results["fear_greed"] = False

    # Source 4
    try:
        results["binance_oi"] = fetch_binance_oi()
    except Exception as e:
        print(f"  FATAL ERROR in Source 4: {e}")
        traceback.print_exc()
        results["binance_oi"] = False

    # Source 5
    try:
        results["macro"] = fetch_macro()
    except Exception as e:
        print(f"  FATAL ERROR in Source 5: {e}")
        traceback.print_exc()
        results["macro"] = False

    # Summary
    end = datetime.now(timezone.utc)
    elapsed = (end - start).total_seconds()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for source, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {source:30s} {status}")
    print(f"\n  Elapsed: {elapsed:.1f}s")
    print(f"  Finished at {end.isoformat()}")

    # Final file listing
    print("\n  Files saved:")
    for p in sorted(BASE_DIR.rglob("*")):
        if p.is_file():
            size_kb = p.stat().st_size / 1024
            rel = p.relative_to(BASE_DIR)
            print(f"    {str(rel):55s} {size_kb:>10.1f} KB")

    failed = [s for s, ok in results.items() if not ok]
    if failed:
        print(f"\n  WARNING: {len(failed)} source(s) failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
