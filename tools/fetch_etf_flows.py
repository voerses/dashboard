#!/workspace/venv/bin/python
"""
Fetch daily BTC/ETH ETF inflow/outflow data from two free sources.

Sources:
  - Farside Investors: HTML table scrape with per-fund daily flows
    (requires environment without Cloudflare blocking — may fail from some IPs)
  - SoSoValue API: JSON endpoint with aggregate daily totals (300-day window)

Data is saved as parquet + CSV files under data/etf_flows/.

Usage:
    python tools/fetch_etf_flows.py                                  # both sources, btc only
    python tools/fetch_etf_flows.py --source farside                 # farside only
    python tools/fetch_etf_flows.py --source sosovalue --assets btc eth
    python tools/fetch_etf_flows.py --source both --assets btc eth   # all sources, all assets
    python tools/fetch_etf_flows.py --force                          # re-fetch everything
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests
import urllib3

# Suppress SSL warnings when going through corporate proxies
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.join(_TOOLS_DIR, "..")
DEFAULT_OUTPUT_DIR = os.path.join(_PROJECT_DIR, "data", "etf_flows")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
BASE_BACKOFF_S = 2.0

FARSIDE_URLS = {
    "btc": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "eth": "https://farside.co.uk/ethereum-etf-flow-all-data/",
}

SOSOVALUE_API_URL = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"
SOSOVALUE_TYPES = {
    "btc": "us-btc-spot",
    "eth": "us-eth-spot",
}


# ---------------------------------------------------------------------------
# HTTP session factory
# ---------------------------------------------------------------------------
def _make_session():
    """Create a requests session that works through corporate proxies."""
    session = requests.Session()
    # Use system proxy settings from environment
    session.trust_env = True
    # Disable SSL verification — needed when going through MITM proxies
    session.verify = False
    return session


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------
def retry_request(fn, *args, max_retries=MAX_RETRIES, **kwargs):
    """Call fn with exponential backoff on network errors."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError,
                requests.exceptions.SSLError) as e:
            last_exc = e
            wait = BASE_BACKOFF_S * (2 ** attempt)
            print(f"    Network error (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                print(f"    Retrying in {wait:.1f}s ...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed after {max_retries} retries: {last_exc}")


# ---------------------------------------------------------------------------
# Numeric cleaning
# ---------------------------------------------------------------------------
def clean_numeric(val):
    """
    Clean a raw cell value to a float (millions USD).
    Handles: "$123.4", "1,234.5", "(123.4)" for negatives, "-" or "--" as 0, empty as NaN.
    """
    if val is None:
        return float("nan")
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s in ("", "-", "--", "\u2013", "\u2014", "\u2012"):
        return 0.0
    # Handle parentheses for negative: (123.4) -> -123.4
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    # Strip dollar sign, commas, whitespace
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    # Handle explicit negative sign
    if s.startswith("-"):
        negative = True
        s = s[1:]
    try:
        value = float(s)
        return -value if negative else value
    except ValueError:
        return float("nan")


# ---------------------------------------------------------------------------
# Farside Investors — HTML scrape
# ---------------------------------------------------------------------------
def fetch_farside(asset, output_dir, force=False):
    """
    Fetch daily ETF flow data from Farside Investors.
    Returns a DataFrame with date, per-fund flows, and total.

    NOTE: Farside uses Cloudflare protection. This will fail from IPs/environments
    where Cloudflare blocks automated requests. In that case, the tool falls back
    to cached data or returns None.
    """
    url = FARSIDE_URLS.get(asset)
    if not url:
        print(f"  [farside/{asset}] Unknown asset, skipping", flush=True)
        return None

    print(f"  [farside/{asset}] Fetching from {url} ...", flush=True)

    # Load existing data for incremental mode
    parquet_path = os.path.join(output_dir, f"{asset}_etf_flows_farside.parquet")
    existing_df = None
    if not force and os.path.exists(parquet_path):
        try:
            existing_df = pd.read_parquet(parquet_path)
            last_date = existing_df["date"].max()
            print(f"  [farside/{asset}] Existing data through {last_date.date()}", flush=True)
        except Exception:
            existing_df = None

    # Fetch the HTML page with browser-like headers
    session = _make_session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })

    def _do_fetch():
        # First hit the homepage to get cookies
        try:
            session.get("https://farside.co.uk/", timeout=15)
        except Exception:
            pass
        time.sleep(1)
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        return resp

    try:
        resp = retry_request(_do_fetch)
    except Exception as e:
        err_msg = str(e)
        if "403" in err_msg:
            print(f"  [farside/{asset}] Blocked by Cloudflare (403). This is expected from some environments.", flush=True)
            print(f"  [farside/{asset}] Falling back to cached data (if available) or SoSoValue.", flush=True)
        else:
            print(f"  [farside/{asset}] FAILED to fetch HTML: {e}", flush=True)
        return existing_df

    html = resp.text

    # Check if we got a Cloudflare challenge page instead of real content
    if "Just a moment" in html and "challenge" in html:
        print(f"  [farside/{asset}] Got Cloudflare challenge page, not real data.", flush=True)
        print(f"  [farside/{asset}] Falling back to cached data (if available) or SoSoValue.", flush=True)
        return existing_df

    # Parse tables using pandas read_html
    try:
        tables = pd.read_html(StringIO(html))
    except Exception as e:
        print(f"  [farside/{asset}] WARNING: Could not parse HTML tables: {e}", flush=True)
        return existing_df

    if not tables:
        print(f"  [farside/{asset}] WARNING: No tables found in HTML", flush=True)
        return existing_df

    # The main data table is typically the largest table
    raw_df = max(tables, key=lambda t: t.shape[0] * t.shape[1])
    print(f"  [farside/{asset}] Found table: {raw_df.shape[0]} rows x {raw_df.shape[1]} cols", flush=True)

    # Process the table
    df = _process_farside_table(raw_df, asset)
    if df is None or df.empty:
        print(f"  [farside/{asset}] WARNING: Could not process table into usable data", flush=True)
        return existing_df

    # Add source column
    df["source"] = "farside"

    # Merge with existing data if incremental
    if existing_df is not None and not force:
        existing_dates = set(existing_df["date"])
        new_dates = set(df["date"])
        combined = pd.concat([
            existing_df[~existing_df["date"].isin(new_dates)],
            df,
        ]).sort_values("date").reset_index(drop=True)
        df = combined

    # Save intermediate farside-only file
    os.makedirs(output_dir, exist_ok=True)
    df.to_parquet(parquet_path, index=False)

    print(f"  [farside/{asset}] {len(df)} days of data ({df['date'].min().date()} to {df['date'].max().date()})", flush=True)
    return df


def _process_farside_table(raw_df, asset):
    """
    Process a raw Farside HTML table into a clean DataFrame.
    The table has a date column plus per-fund columns plus a Total column.
    """
    df = raw_df.copy()

    # Normalize column names to lowercase, strip whitespace
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Identify the date column
    date_col = None
    for col in df.columns:
        if "date" in col:
            date_col = col
            break
    if date_col is None:
        date_col = df.columns[0]

    # Try to parse dates
    dates_parsed = []
    valid_rows = []
    for idx, val in enumerate(df[date_col]):
        try:
            dt = pd.to_datetime(val)
            dates_parsed.append(dt)
            valid_rows.append(idx)
        except (ValueError, TypeError):
            continue

    if not dates_parsed:
        return None

    df = df.iloc[valid_rows].copy()
    df["date"] = dates_parsed

    # Identify fund columns vs total vs date
    skip_cols = {date_col, "date", "source"}
    total_col = None
    for col in df.columns:
        if col in skip_cols:
            continue
        if "total" in col:
            total_col = col
            skip_cols.add(col)

    # Clean numeric values for all fund columns
    fund_cols = []
    for col in list(df.columns):
        if col in skip_cols:
            continue
        clean_name = re.sub(r"[^a-z0-9]", "", col.lower())
        if clean_name:
            df[clean_name] = df[col].apply(clean_numeric)
            if clean_name != col:
                df = df.drop(columns=[col])
            fund_cols.append(clean_name)

    # Compute total
    if total_col is not None:
        df["total_inflow_mm"] = df[total_col].apply(clean_numeric)
    elif fund_cols:
        df["total_inflow_mm"] = df[fund_cols].sum(axis=1)
    else:
        df["total_inflow_mm"] = 0.0

    # Build final column list
    keep_cols = ["date", "total_inflow_mm"] + sorted(fund_cols)
    available_cols = [c for c in keep_cols if c in df.columns]
    df = df[available_cols].copy()

    # Sort and clean
    df = df.sort_values("date").reset_index(drop=True)
    numeric_cols = [c for c in df.columns if c not in ("date", "source")]
    df = df.dropna(subset=numeric_cols, how="all").reset_index(drop=True)

    return df


# ---------------------------------------------------------------------------
# SoSoValue API — JSON endpoint
# ---------------------------------------------------------------------------
def fetch_sosovalue(asset, output_dir, force=False):
    """
    Fetch daily ETF flow data from SoSoValue API.
    Returns a DataFrame with date, total_inflow_mm, source.

    The free API returns the latest ~300 days of data per call. Date parameters
    are not supported for pagination, so incremental mode accumulates history
    over time by merging with previously saved data.
    """
    etf_type = SOSOVALUE_TYPES.get(asset)
    if not etf_type:
        print(f"  [sosovalue/{asset}] Unknown asset, skipping", flush=True)
        return None

    print(f"  [sosovalue/{asset}] Fetching from SoSoValue API ...", flush=True)

    # Load existing data for incremental mode
    parquet_path = os.path.join(output_dir, f"{asset}_etf_flows_sosovalue.parquet")
    existing_df = None
    if not force and os.path.exists(parquet_path):
        try:
            existing_df = pd.read_parquet(parquet_path)
            last_date = existing_df["date"].max()
            print(f"  [sosovalue/{asset}] Existing data through {last_date.date()} ({len(existing_df)} days)", flush=True)
        except Exception:
            existing_df = None

    # Single API call — returns latest ~300 days
    session = _make_session()

    def _do_fetch():
        resp = session.post(
            SOSOVALUE_API_URL,
            json={"type": etf_type},
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp

    try:
        resp = retry_request(_do_fetch)
    except Exception as e:
        print(f"  [sosovalue/{asset}] API error: {e}", flush=True)
        return existing_df

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        print(f"  [sosovalue/{asset}] JSON decode error: {e}", flush=True)
        return existing_df

    # Parse response
    records = _parse_sosovalue_response(data, asset)
    if not records:
        print(f"  [sosovalue/{asset}] No data in API response", flush=True)
        return existing_df

    print(f"  [sosovalue/{asset}] Got {len(records)} days from API", flush=True)

    # Build DataFrame
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df["source"] = "sosovalue"

    # De-duplicate by date (keep latest)
    df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    # Merge with existing data (incremental: keep old data, update overlapping dates)
    if existing_df is not None and not force:
        new_dates = set(df["date"])
        # Keep existing rows that are NOT in the new data (they extend history further back)
        old_only = existing_df[~existing_df["date"].isin(new_dates)]
        combined = pd.concat([old_only, df]).sort_values("date").reset_index(drop=True)
        print(f"  [sosovalue/{asset}] Merged: {len(old_only)} old + {len(df)} new = {len(combined)} total days", flush=True)
        df = combined

    # Save intermediate sosovalue-only file
    os.makedirs(output_dir, exist_ok=True)
    df.to_parquet(parquet_path, index=False)

    print(f"  [sosovalue/{asset}] {len(df)} days ({df['date'].min().date()} to {df['date'].max().date()})", flush=True)
    return df


def _parse_sosovalue_response(data, asset):
    """
    Parse SoSoValue API response into list of {date, total_inflow_mm} dicts.

    Known response format:
    {
        "code": 0,
        "data": [
            {
                "date": "2024-01-11",
                "totalNetInflow": 628735069.54,      # raw USD
                "totalValueTraded": 4661717386.8,     # raw USD
                "totalNetAssets": 30113645826.76,     # raw USD
                "cumNetInflow": 628735069.54          # raw USD
            },
            ...
        ]
    }
    """
    records = []

    if not isinstance(data, dict) or data.get("code") != 0:
        return records

    items = data.get("data")
    if not isinstance(items, list):
        return records

    for item in items:
        if not isinstance(item, dict):
            continue

        # Extract date
        date_val = item.get("date")
        if date_val is None:
            continue

        # Handle timestamp values
        if isinstance(date_val, (int, float)):
            if date_val > 1e12:
                date_val = datetime.fromtimestamp(date_val / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            elif date_val > 1e9:
                date_val = datetime.fromtimestamp(date_val, tz=timezone.utc).strftime("%Y-%m-%d")

        # Extract net inflow — try field names in priority order
        inflow_val = None
        for key in ("totalNetInflow", "totalInflow", "netInflow", "inflow",
                     "netFlow", "value", "amount"):
            if key in item and item[key] is not None:
                inflow_val = item[key]
                break

        if inflow_val is None:
            continue

        try:
            # Convert from raw USD to millions USD
            amount_usd = float(inflow_val)
            amount_mm = amount_usd / 1e6
            records.append({
                "date": str(date_val),
                "total_inflow_mm": round(amount_mm, 2),
            })
        except (ValueError, TypeError):
            continue

    return records


# ---------------------------------------------------------------------------
# Data reconciliation
# ---------------------------------------------------------------------------
def reconcile_and_merge(farside_df, sosovalue_df, asset, output_dir):
    """
    Reconcile data from both sources and produce final merged output.
    Prefers Farside data (per-fund breakdown) when both available.
    Uses SoSoValue to fill gaps.
    """
    print(f"\n  [{asset}] Reconciling data sources ...", flush=True)

    if farside_df is None and sosovalue_df is None:
        print(f"  [{asset}] No data from any source", flush=True)
        return None

    if farside_df is not None and sosovalue_df is None:
        print(f"  [{asset}] Farside only -- {len(farside_df)} days", flush=True)
        return farside_df

    if farside_df is None and sosovalue_df is not None:
        print(f"  [{asset}] SoSoValue only -- {len(sosovalue_df)} days", flush=True)
        return sosovalue_df

    # Both sources available — compare and merge
    farside_dates = set(farside_df["date"])
    soso_dates = set(sosovalue_df["date"])
    overlap_dates = farside_dates & soso_dates

    print(f"  [{asset}] Farside: {len(farside_dates)} days, SoSoValue: {len(soso_dates)} days, Overlap: {len(overlap_dates)} days", flush=True)

    # Check discrepancies on overlapping dates
    if overlap_dates:
        farside_totals = farside_df[farside_df["date"].isin(overlap_dates)].set_index("date")["total_inflow_mm"]
        soso_totals = sosovalue_df[sosovalue_df["date"].isin(overlap_dates)].set_index("date")["total_inflow_mm"]

        discrepancy_count = 0
        for date in sorted(overlap_dates):
            f_val = farside_totals.get(date, 0)
            s_val = soso_totals.get(date, 0)
            if f_val == 0 and s_val == 0:
                continue
            denom = max(abs(f_val), abs(s_val), 1e-6)
            pct_diff = abs(f_val - s_val) / denom
            if pct_diff > 0.05:
                discrepancy_count += 1
                if discrepancy_count <= 5:
                    print(f"    DISCREPANCY {date.date()}: farside={f_val:.1f}M, sosovalue={s_val:.1f}M ({pct_diff:.0%})", flush=True)

        if discrepancy_count > 5:
            print(f"    ... and {discrepancy_count - 5} more discrepancies >5%", flush=True)
        elif discrepancy_count == 0:
            print(f"  [{asset}] No significant discrepancies between sources", flush=True)

    # Merge: prefer Farside, use SoSoValue to fill gaps
    gap_dates = soso_dates - farside_dates
    if gap_dates:
        gap_df = sosovalue_df[sosovalue_df["date"].isin(gap_dates)].copy()
        merged = pd.concat([farside_df, gap_df]).sort_values("date").reset_index(drop=True)
        print(f"  [{asset}] Filled {len(gap_dates)} gap days from SoSoValue", flush=True)
    else:
        merged = farside_df.copy()

    return merged


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def save_output(df, asset, output_dir):
    """Save final merged data as parquet and CSV."""
    if df is None or df.empty:
        print(f"  [{asset}] No data to save", flush=True)
        return

    os.makedirs(output_dir, exist_ok=True)

    parquet_path = os.path.join(output_dir, f"{asset}_etf_flows.parquet")
    csv_path = os.path.join(output_dir, f"{asset}_etf_flows.csv")

    df.to_parquet(parquet_path, index=False)
    df.to_csv(csv_path, index=False)

    print(f"  [{asset}] Saved {len(df)} rows to:", flush=True)
    print(f"    {parquet_path}", flush=True)
    print(f"    {csv_path}", flush=True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(results, elapsed_s):
    print("\n" + "=" * 70, flush=True)
    print("ETF FLOW FETCH SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"Elapsed: {elapsed_s:.1f}s\n", flush=True)

    for asset, df in results.items():
        if df is not None and not df.empty:
            sources = df["source"].unique() if "source" in df.columns else ["unknown"]
            print(f"  {asset.upper()}: {len(df)} days ({df['date'].min().date()} to {df['date'].max().date()}) "
                  f"[sources: {', '.join(sources)}]", flush=True)

            total_col = "total_inflow_mm"
            if total_col in df.columns:
                net = df[total_col].sum()
                avg = df[total_col].mean()
                print(f"    Net flow: {net:,.1f}M | Avg daily: {avg:,.1f}M", flush=True)
        else:
            print(f"  {asset.upper()}: NO DATA", flush=True)

    print("=" * 70, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch daily BTC/ETH ETF inflow/outflow data."
    )
    parser.add_argument(
        "--source", choices=["farside", "sosovalue", "both"], default="both",
        help="Data source to fetch from (default: both)."
    )
    parser.add_argument(
        "--assets", nargs="+", default=["btc"],
        help="Assets to fetch (default: btc). Options: btc, eth."
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: data/etf_flows)."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch everything (default: incremental)."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    assets = [a.lower() for a in args.assets]
    output_dir = os.path.abspath(args.output_dir)

    print("ETF Flow Data Fetcher", flush=True)
    print(f"  Source:  {args.source}", flush=True)
    print(f"  Assets:  {', '.join(a.upper() for a in assets)}", flush=True)
    print(f"  Output:  {output_dir}", flush=True)
    print(f"  Force:   {args.force}", flush=True)
    print("", flush=True)

    os.makedirs(output_dir, exist_ok=True)

    t0 = time.time()
    results = {}

    for asset in assets:
        print(f"\n{'='*40} {asset.upper()} {'='*40}", flush=True)

        farside_df = None
        soso_df = None

        # Fetch from requested sources
        if args.source in ("farside", "both"):
            try:
                farside_df = fetch_farside(asset, output_dir, force=args.force)
            except Exception as e:
                print(f"  [farside/{asset}] FATAL ERROR: {e}", flush=True)
                traceback.print_exc()

        if args.source in ("sosovalue", "both"):
            try:
                soso_df = fetch_sosovalue(asset, output_dir, force=args.force)
            except Exception as e:
                print(f"  [sosovalue/{asset}] FATAL ERROR: {e}", flush=True)
                traceback.print_exc()

        # Reconcile and merge
        if args.source == "both":
            merged_df = reconcile_and_merge(farside_df, soso_df, asset, output_dir)
        elif args.source == "farside":
            merged_df = farside_df
        else:
            merged_df = soso_df

        # Save final output
        save_output(merged_df, asset, output_dir)
        results[asset] = merged_df

    elapsed = time.time() - t0
    print_summary(results, elapsed)


if __name__ == "__main__":
    main()
