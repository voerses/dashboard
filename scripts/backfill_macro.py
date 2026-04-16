#!/usr/bin/env python3
"""
Backfill macro data sources that have gaps.

Updates:
  1. SP500, VIX, Gold, Oil, DXY, US10Y, Nasdaq — via yfinance
  2. Fear & Greed Index — via alternative.me API
  3. ETF flows (BTC/ETH) — via SoSoValue API (delegates to fetch_etf_flows.py)

Usage:
    /workspace/venv/bin/python scripts/backfill_macro.py
"""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
MACRO_DIR = DATA_DIR / "alternative" / "macro"
FEAR_GREED_DIR = DATA_DIR / "alternative" / "fear_greed"
ETF_DIR = DATA_DIR / "etf_flows"
ALT_ETF_DIR = DATA_DIR / "alternative" / "etf_flows"

# ---------------------------------------------------------------------------
# Macro tickers (yfinance)
# ---------------------------------------------------------------------------
MACRO_TICKERS = {
    "sp500": {"ticker": "^GSPC", "file": "sp500.parquet"},
    "vix": {"ticker": "^VIX", "file": "vix.parquet"},
    "gold": {"ticker": "GC=F", "file": "gold.parquet"},
    "oil": {"ticker": "CL=F", "file": "oil_wti.parquet"},
    "dxy": {"ticker": "DX-Y.NYB", "file": "usd_index.parquet"},
    "us10y": {"ticker": "^TNX", "file": "us10y_yield.parquet"},
    "nasdaq": {"ticker": "^IXIC", "file": "nasdaq.parquet"},
}


def backfill_macro_yfinance():
    """Backfill all macro parquet files using yfinance."""
    print("\n" + "=" * 70)
    print("MACRO DATA BACKFILL (yfinance)")
    print("=" * 70)

    try:
        import yfinance as yf
    except ImportError:
        print("  Installing yfinance...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "yfinance", "-q"]
        )
        import yfinance as yf

    results = {}

    for name, cfg in MACRO_TICKERS.items():
        ticker = cfg["ticker"]
        parquet_path = MACRO_DIR / cfg["file"]

        print(f"\n  {name} ({ticker}):")

        # Load existing data
        if parquet_path.exists():
            existing = pd.read_parquet(parquet_path)
            last_date = pd.to_datetime(existing["Date"]).max()
            print(f"    Existing: {len(existing)} rows, last={last_date.date()}")

            # Fetch from 2 days before last date to ensure overlap
            start_date = (last_date - timedelta(days=2)).strftime("%Y-%m-%d")
        else:
            existing = None
            last_date = None
            start_date = "2000-01-01"
            print(f"    No existing data, fetching full history from {start_date}")

        # Fetch new data
        try:
            t = yf.Ticker(ticker)
            new_df = t.history(start=start_date, interval="1d")

            if new_df.empty:
                print(f"    WARNING: No new data returned from yfinance")
                results[name] = {"status": "NO_NEW_DATA", "last_date": str(last_date)}
                continue

            # Reset index, make Date a column, strip timezone
            new_df = new_df.reset_index()
            if "Date" in new_df.columns:
                new_df["Date"] = pd.to_datetime(new_df["Date"]).dt.tz_localize(None)

            print(f"    Fetched: {len(new_df)} rows, {new_df['Date'].min().date()} to {new_df['Date'].max().date()}")

            # Merge with existing
            if existing is not None:
                existing["Date"] = pd.to_datetime(existing["Date"])
                # Ensure same columns
                common_cols = [c for c in existing.columns if c in new_df.columns]
                new_df = new_df[common_cols]

                # Concat, deduplicate by Date (keep new data)
                combined = pd.concat([existing[common_cols], new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["Date"], keep="last")
                combined = combined.sort_values("Date").reset_index(drop=True)
            else:
                combined = new_df.sort_values("Date").reset_index(drop=True)

            # Save
            combined.to_parquet(parquet_path, index=False)
            new_last = combined["Date"].max()
            print(f"    Saved: {len(combined)} rows, last={new_last.date()}")
            results[name] = {
                "status": "OK",
                "rows": len(combined),
                "last_date": str(new_last.date()),
            }

        except Exception as e:
            print(f"    ERROR: {e}")
            traceback.print_exc()
            results[name] = {
                "status": "FAILED",
                "error": str(e),
                "last_date": str(last_date) if last_date else None,
            }

        time.sleep(0.5)

    return results


def backfill_fear_greed():
    """Backfill Fear & Greed Index from alternative.me API."""
    print("\n" + "=" * 70)
    print("FEAR & GREED INDEX BACKFILL")
    print("=" * 70)

    FEAR_GREED_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = FEAR_GREED_DIR / "fear_greed_index.parquet"

    # Load existing
    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        last_ts = pd.to_datetime(existing["timestamp"]).max()
        print(f"  Existing: {len(existing)} rows, last={last_ts}")
    else:
        existing = None
        last_ts = None
        print("  No existing data")

    # Fetch all available data (limit=0 means all)
    url = "https://api.alternative.me/fng/"
    params = {"limit": 0, "format": "json"}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if "data" not in data:
            print("  ERROR: No 'data' key in response")
            return {"status": "FAILED", "error": "no data key"}

        records = data["data"]
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        df["value"] = df["value"].astype(int)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Save (full replace -- API returns full history)
        df.to_parquet(parquet_path, index=False)

        # Also save raw JSON
        json_path = FEAR_GREED_DIR / "fear_greed_raw.json"
        with open(json_path, "w") as f:
            json.dump(data, f)

        # Also update the full JSON in parent dir
        full_json_path = DATA_DIR / "alternative" / "fear_greed_index_full.json"
        with open(full_json_path, "w") as f:
            json.dump(data, f)

        new_last = df["timestamp"].max()
        print(f"  Saved: {len(df)} rows, last={new_last}")
        return {
            "status": "OK",
            "rows": len(df),
            "last_date": str(new_last.date()),
        }

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return {"status": "FAILED", "error": str(e)}


def backfill_etf_flows():
    """Backfill ETF flows using the existing fetch_etf_flows.py tool."""
    print("\n" + "=" * 70)
    print("ETF FLOWS BACKFILL")
    print("=" * 70)

    fetch_script = PROJECT_DIR / "tools" / "fetch_etf_flows.py"
    if not fetch_script.exists():
        print("  ERROR: tools/fetch_etf_flows.py not found")
        return {"status": "FAILED", "error": "script not found"}

    try:
        result = subprocess.run(
            [sys.executable, str(fetch_script), "--source", "sosovalue", "--assets", "btc", "eth"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_DIR),
        )
        print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
        if result.stderr:
            print("  STDERR:", result.stderr[-500:])

        # Also copy sosovalue data to alternative/etf_flows for btc
        btc_src = ETF_DIR / "btc_etf_flows.parquet"
        btc_dst = ALT_ETF_DIR / "btc_etf_daily.parquet"
        if btc_src.exists():
            ALT_ETF_DIR.mkdir(parents=True, exist_ok=True)
            df = pd.read_parquet(btc_src)
            # The alt format uses: date, total_inflow_mm, source
            if "date" in df.columns and "total_inflow_mm" in df.columns:
                alt_df = df[["date", "total_inflow_mm"]].copy()
                alt_df["source"] = "sosovalue"
                alt_df.to_parquet(btc_dst, index=False)
                print(f"  Also updated {btc_dst.relative_to(PROJECT_DIR)}")

        # Report end dates
        etf_results = {}
        for asset in ["btc", "eth"]:
            path = ETF_DIR / f"{asset}_etf_flows.parquet"
            if path.exists():
                df = pd.read_parquet(path)
                last = pd.to_datetime(df["date"]).max()
                etf_results[asset] = {
                    "status": "OK",
                    "rows": len(df),
                    "last_date": str(last.date()),
                }
                print(f"  {asset.upper()}: {len(df)} rows, last={last.date()}")
            else:
                etf_results[asset] = {"status": "NO_FILE"}

        return etf_results

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return {"status": "FAILED", "error": str(e)}


def verify_all():
    """Verify all data sources have recent data."""
    print("\n" + "=" * 70)
    print("VERIFICATION")
    print("=" * 70)

    # Markets are closed on weekends, yfinance lags ~1 day.
    # Accept data through yesterday (April 2) as current.
    target_date = pd.Timestamp("2026-04-01")
    all_ok = True

    # Macro files
    for name, cfg in MACRO_TICKERS.items():
        path = MACRO_DIR / cfg["file"]
        if path.exists():
            df = pd.read_parquet(path)
            last = pd.to_datetime(df["Date"]).max()
            ok = last >= target_date
            status = "OK" if ok else "GAP"
            if not ok:
                all_ok = False
            print(f"  {name:12s} last={last.date()}  {status}")
        else:
            print(f"  {name:12s} MISSING")
            all_ok = False

    # Fear & Greed
    fg_path = FEAR_GREED_DIR / "fear_greed_index.parquet"
    if fg_path.exists():
        df = pd.read_parquet(fg_path)
        last = pd.to_datetime(df["timestamp"]).max()
        # Strip timezone for comparison
        if last.tzinfo is not None:
            last = last.tz_localize(None)
        ok = last >= target_date
        status = "OK" if ok else "GAP"
        if not ok:
            all_ok = False
        print(f"  {'fear_greed':12s} last={last.date()}  {status}")

    # ETF flows
    for asset in ["btc", "eth"]:
        path = ETF_DIR / f"{asset}_etf_flows.parquet"
        if path.exists():
            df = pd.read_parquet(path)
            last = pd.to_datetime(df["date"]).max()
            if last.tzinfo is not None:
                last = last.tz_localize(None)
            # ETF data can lag by a few business days
            ok = last >= target_date - timedelta(days=3)
            status = "OK" if ok else "GAP"
            if not ok:
                all_ok = False
            print(f"  {f'etf_{asset}':12s} last={last.date()}  {status}")

    print(f"\n  Overall: {'ALL OK' if all_ok else 'GAPS REMAIN'}")
    return all_ok


def main():
    start = datetime.now(timezone.utc)
    print(f"Macro Data Backfill — started at {start.isoformat()}")
    print(f"Project: {PROJECT_DIR}")

    # Step 1: Backfill macro data via yfinance
    macro_results = backfill_macro_yfinance()

    # Step 2: Backfill Fear & Greed
    fg_result = backfill_fear_greed()

    # Step 3: Backfill ETF flows
    etf_results = backfill_etf_flows()

    # Step 4: Verify
    all_ok = verify_all()

    # Summary
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print("\n  Macro:")
    for name, res in macro_results.items():
        print(f"    {name:12s} {res['status']:12s} last={res.get('last_date', 'N/A')}")

    print(f"\n  Fear & Greed: {fg_result['status']} last={fg_result.get('last_date', 'N/A')}")

    print("\n  ETF Flows:")
    if isinstance(etf_results, dict) and "status" not in etf_results:
        for asset, res in etf_results.items():
            print(f"    {asset:12s} {res['status']:12s} last={res.get('last_date', 'N/A')}")
    else:
        print(f"    {etf_results}")

    print(f"\n  Elapsed: {elapsed:.1f}s")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
