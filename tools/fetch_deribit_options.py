#!/workspace/venv/bin/python
"""
Fetch Deribit options book summary for BTC, ETH, SOL.

Deribit has NO historical options API -- every snapshot you miss is gone forever.
This script is designed for hourly cron execution (append mode).

Public API (no auth):
  GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency
    ?currency={BTC,ETH,SOL}&kind=option

Data saved:
  - Raw:     data/alternative/deribit_options/raw/snapshot_YYYYMMDD_HHMMSS.parquet
  - Summary: data/alternative/deribit_options/summary/options_summary.parquet (append)

Usage:
    python tools/fetch_deribit_options.py            # fetch all currencies
    python tools/fetch_deribit_options.py --currency BTC  # BTC only
"""

import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.join(_TOOLS_DIR, "..")
RAW_DIR = os.path.join(_PROJECT_DIR, "data", "alternative", "deribit_options", "raw")
SUMMARY_DIR = os.path.join(_PROJECT_DIR, "data", "alternative", "deribit_options", "summary")
SUMMARY_FILE = os.path.join(SUMMARY_DIR, "options_summary.parquet")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DERIBIT_BASE = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
CURRENCIES = ["BTC", "ETH", "SOL"]
MAX_RETRIES = 3
BASE_BACKOFF_S = 2.0


def fetch_options_summary(currency: str) -> list[dict]:
    """Fetch all option book summaries for a currency from Deribit."""
    url = DERIBIT_BASE
    params = {"currency": currency, "kind": "option"}

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "result" not in data:
                raise ValueError(f"No 'result' key in response: {list(data.keys())}")
            return data["result"]
        except (requests.RequestException, ValueError) as e:
            wait = BASE_BACKOFF_S * (2 ** attempt)
            print(f"  [WARN] {currency} attempt {attempt+1}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  Retrying in {wait:.0f}s...")
                time.sleep(wait)
            else:
                print(f"  [ERROR] {currency} failed after {MAX_RETRIES} attempts")
                raise


def parse_instrument_name(name: str) -> dict:
    """Parse Deribit instrument name like BTC-27JUN25-100000-C.

    Returns dict with: currency, expiry_str, expiry_date, strike, option_type.
    """
    parts = name.split("-")
    if len(parts) < 4:
        return {"currency": parts[0] if parts else "", "expiry_str": "",
                "expiry_date": None, "strike": 0, "option_type": ""}

    currency = parts[0]
    expiry_str = parts[1]
    strike = float(parts[2])
    option_type = parts[3]  # C or P

    # Parse expiry: format like 27JUN25 or 28MAR25
    try:
        expiry_date = datetime.strptime(expiry_str, "%d%b%y").replace(tzinfo=timezone.utc)
    except ValueError:
        expiry_date = None

    return {
        "currency": currency,
        "expiry_str": expiry_str,
        "expiry_date": expiry_date,
        "strike": strike,
        "option_type": option_type,
    }


def compute_days_to_expiry(expiry_date, now: datetime) -> float:
    """Compute days to expiry from now."""
    if expiry_date is None:
        return float("nan")
    delta = expiry_date - now
    return max(delta.total_seconds() / 86400.0, 0.0)


def compute_25delta_skew(df: pd.DataFrame, currency: str) -> float | None:
    """Estimate 25-delta skew from available strikes.

    For each expiry, find the 25-delta put and 25-delta call approximations
    using strikes ~25% OTM from the underlying price, then compute IV difference.

    Returns average skew across near-term expiries (< 60 days).
    """
    cdf = df[df["currency"] == currency].copy()
    if cdf.empty or "underlying_price" not in cdf.columns:
        return None

    underlying = cdf["underlying_price"].median()
    if underlying <= 0:
        return None

    # Focus on near-term (< 60 days)
    near = cdf[(cdf["days_to_expiry"] > 1) & (cdf["days_to_expiry"] < 60)].copy()
    if near.empty:
        return None

    skews = []
    for expiry in near["expiry_str"].unique():
        exp_df = near[near["expiry_str"] == expiry]
        puts = exp_df[exp_df["option_type"] == "P"].copy()
        calls = exp_df[exp_df["option_type"] == "C"].copy()

        if puts.empty or calls.empty:
            continue

        # 25-delta put: strike ~= underlying * 0.90 (roughly)
        # 25-delta call: strike ~= underlying * 1.10 (roughly)
        target_put_strike = underlying * 0.90
        target_call_strike = underlying * 1.10

        # Find closest strikes
        puts["strike_dist"] = (puts["strike"] - target_put_strike).abs()
        calls["strike_dist"] = (calls["strike"] - target_call_strike).abs()

        best_put = puts.loc[puts["strike_dist"].idxmin()]
        best_call = calls.loc[calls["strike_dist"].idxmin()]

        put_iv = best_put.get("mark_iv", 0)
        call_iv = best_call.get("mark_iv", 0)

        if put_iv > 0 and call_iv > 0:
            skews.append(put_iv - call_iv)

    if not skews:
        return None

    return sum(skews) / len(skews)


def build_term_structure(df: pd.DataFrame, currency: str) -> pd.DataFrame:
    """Build IV term structure: average IV per expiry bucket sorted by DTE."""
    cdf = df[(df["currency"] == currency) & (df["mark_iv"] > 0)].copy()
    if cdf.empty:
        return pd.DataFrame()

    ts = (
        cdf.groupby("expiry_str")
        .agg(
            avg_iv=("mark_iv", "mean"),
            days_to_expiry=("days_to_expiry", "first"),
            num_strikes=("instrument_name", "count"),
            total_oi=("open_interest", "sum"),
        )
        .sort_values("days_to_expiry")
        .reset_index()
    )
    return ts


def classify_term_structure(ts: pd.DataFrame) -> str:
    """Classify term structure shape: contango, backwardation, flat, humped."""
    if ts.empty or len(ts) < 2:
        return "insufficient_data"

    ivs = ts["avg_iv"].values
    if len(ivs) < 3:
        if ivs[-1] > ivs[0] * 1.05:
            return "contango"
        elif ivs[-1] < ivs[0] * 0.95:
            return "backwardation"
        else:
            return "flat"

    # Check if mid-term is highest (humped)
    mid = len(ivs) // 2
    if ivs[mid] > ivs[0] and ivs[mid] > ivs[-1]:
        return "humped"
    elif ivs[-1] > ivs[0] * 1.05:
        return "contango"
    elif ivs[-1] < ivs[0] * 0.95:
        return "backwardation"
    else:
        return "flat"


def main(currencies: list[str] | None = None):
    now = datetime.now(timezone.utc)
    ts_str = now.strftime("%Y%m%d_%H%M%S")

    if currencies is None:
        currencies = CURRENCIES

    # Ensure directories exist
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(SUMMARY_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Fetch raw data from all currencies
    # ------------------------------------------------------------------
    all_records = []
    for ccy in currencies:
        print(f"Fetching {ccy} options from Deribit...")
        try:
            records = fetch_options_summary(ccy)
            print(f"  {ccy}: {len(records)} instruments")
            all_records.extend(records)
        except Exception as e:
            print(f"  [ERROR] Skipping {ccy}: {e}")
            traceback.print_exc()

    if not all_records:
        print("[ERROR] No data fetched. Exiting.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Build raw DataFrame
    # ------------------------------------------------------------------
    raw_df = pd.DataFrame(all_records)
    raw_df["fetch_timestamp"] = now.isoformat()

    # Parse instrument names
    parsed = raw_df["instrument_name"].apply(parse_instrument_name)
    parsed_df = pd.DataFrame(parsed.tolist())
    raw_df = pd.concat([raw_df, parsed_df], axis=1)

    # Compute days to expiry
    raw_df["days_to_expiry"] = raw_df["expiry_date"].apply(
        lambda x: compute_days_to_expiry(x, now)
    )

    # ------------------------------------------------------------------
    # Save raw snapshot
    # ------------------------------------------------------------------
    raw_path = os.path.join(RAW_DIR, f"snapshot_{ts_str}.parquet")
    # Drop non-serializable columns for parquet
    save_df = raw_df.copy()
    # Convert expiry_date to string for parquet compatibility
    save_df["expiry_date"] = save_df["expiry_date"].apply(
        lambda x: x.isoformat() if x is not None else None
    )
    save_df.to_parquet(raw_path, index=False)
    print(f"\nRaw snapshot saved: {raw_path}")
    print(f"  Total instruments: {len(raw_df)}")

    # ------------------------------------------------------------------
    # Compute summary metrics per currency
    # ------------------------------------------------------------------
    summary_rows = []

    for ccy in currencies:
        cdf = raw_df[raw_df["currency"] == ccy].copy()
        if cdf.empty:
            continue

        # Put/Call split
        calls = cdf[cdf["option_type"] == "C"]
        puts = cdf[cdf["option_type"] == "P"]

        call_oi = calls["open_interest"].sum() if not calls.empty else 0
        put_oi = puts["open_interest"].sum() if not puts.empty else 0
        total_oi = call_oi + put_oi
        put_call_ratio = put_oi / call_oi if call_oi > 0 else float("nan")

        # Total OI in USD terms
        underlying_price = cdf["underlying_price"].median() if "underlying_price" in cdf.columns else 0
        total_oi_usd = total_oi * underlying_price

        # Volume
        total_volume_usd = cdf["volume_usd"].sum() if "volume_usd" in cdf.columns else 0

        # Near-term IV (< 14 days, > 0 days)
        near_term = cdf[(cdf["days_to_expiry"] > 0) & (cdf["days_to_expiry"] <= 14) & (cdf["mark_iv"] > 0)]
        near_term_iv = near_term["mark_iv"].mean() if not near_term.empty else float("nan")

        # Mid-term IV (14-60 days)
        mid_term = cdf[(cdf["days_to_expiry"] > 14) & (cdf["days_to_expiry"] <= 60) & (cdf["mark_iv"] > 0)]
        mid_term_iv = mid_term["mark_iv"].mean() if not mid_term.empty else float("nan")

        # Long-term IV (> 60 days)
        long_term = cdf[(cdf["days_to_expiry"] > 60) & (cdf["mark_iv"] > 0)]
        long_term_iv = long_term["mark_iv"].mean() if not long_term.empty else float("nan")

        # Term structure
        ts_df = build_term_structure(raw_df, ccy)
        ts_shape = classify_term_structure(ts_df)

        # 25-delta skew
        skew_25d = compute_25delta_skew(raw_df, ccy)

        # Max OI strike (most popular strike)
        if not cdf.empty and "open_interest" in cdf.columns:
            max_oi_idx = cdf["open_interest"].idxmax()
            max_oi_strike = cdf.loc[max_oi_idx, "strike"]
            max_oi_instrument = cdf.loc[max_oi_idx, "instrument_name"]
        else:
            max_oi_strike = float("nan")
            max_oi_instrument = ""

        summary_rows.append({
            "timestamp": now,
            "currency": ccy,
            "underlying_price": underlying_price,
            "total_instruments": len(cdf),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "put_call_ratio": round(put_call_ratio, 4) if not pd.isna(put_call_ratio) else float("nan"),
            "total_oi_usd": total_oi_usd,
            "total_volume_usd": total_volume_usd,
            "near_term_iv": round(near_term_iv, 2) if not pd.isna(near_term_iv) else float("nan"),
            "mid_term_iv": round(mid_term_iv, 2) if not pd.isna(mid_term_iv) else float("nan"),
            "long_term_iv": round(long_term_iv, 2) if not pd.isna(long_term_iv) else float("nan"),
            "term_structure_shape": ts_shape,
            "skew_25d": round(skew_25d, 2) if skew_25d is not None else float("nan"),
            "max_oi_strike": max_oi_strike,
            "max_oi_instrument": max_oi_instrument,
            "num_expiries": cdf["expiry_str"].nunique(),
        })

    summary_df = pd.DataFrame(summary_rows)

    # ------------------------------------------------------------------
    # Append to summary file
    # ------------------------------------------------------------------
    if os.path.exists(SUMMARY_FILE):
        existing = pd.read_parquet(SUMMARY_FILE)
        summary_df = pd.concat([existing, summary_df], ignore_index=True)

    summary_df.to_parquet(SUMMARY_FILE, index=False)
    print(f"Summary appended: {SUMMARY_FILE} ({len(summary_df)} total rows)")

    # ------------------------------------------------------------------
    # Print summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print(f"DERIBIT OPTIONS SNAPSHOT — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 80)

    for _, row in pd.DataFrame(summary_rows).iterrows():
        ccy = row["currency"]
        print(f"\n--- {ccy} ---")
        print(f"  Underlying price:    ${row['underlying_price']:,.2f}")
        print(f"  Total instruments:   {row['total_instruments']}")
        print(f"  Call OI:             {row['call_oi']:,.2f}")
        print(f"  Put OI:              {row['put_oi']:,.2f}")
        print(f"  Put/Call ratio:      {row['put_call_ratio']:.4f}" if not pd.isna(row['put_call_ratio']) else "  Put/Call ratio:      N/A")
        print(f"  Total OI (USD):      ${row['total_oi_usd']:,.0f}")
        print(f"  Volume (USD):        ${row['total_volume_usd']:,.0f}")
        print(f"  Near-term IV (<14d): {row['near_term_iv']:.2f}%" if not pd.isna(row['near_term_iv']) else "  Near-term IV (<14d): N/A")
        print(f"  Mid-term IV (14-60d):{row['mid_term_iv']:.2f}%" if not pd.isna(row['mid_term_iv']) else "  Mid-term IV (14-60d):N/A")
        print(f"  Long-term IV (>60d): {row['long_term_iv']:.2f}%" if not pd.isna(row['long_term_iv']) else "  Long-term IV (>60d): N/A")
        print(f"  Term structure:      {row['term_structure_shape']}")
        print(f"  25-delta skew:       {row['skew_25d']:.2f}%" if not pd.isna(row['skew_25d']) else "  25-delta skew:       N/A")
        print(f"  Max OI strike:       ${row['max_oi_strike']:,.0f} ({row['max_oi_instrument']})")
        print(f"  Number of expiries:  {row['num_expiries']}")

    # Print term structure for BTC
    if "BTC" in currencies:
        ts_btc = build_term_structure(raw_df, "BTC")
        if not ts_btc.empty:
            print(f"\n--- BTC IV Term Structure ---")
            print(f"  {'Expiry':<12} {'DTE':>6} {'Avg IV':>8} {'Strikes':>8} {'OI':>12}")
            for _, r in ts_btc.iterrows():
                print(f"  {r['expiry_str']:<12} {r['days_to_expiry']:>6.0f} {r['avg_iv']:>7.1f}% {r['num_strikes']:>8} {r['total_oi']:>12,.1f}")

    print("\n" + "=" * 80)
    return summary_rows


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch Deribit options data")
    parser.add_argument("--currency", type=str, nargs="+", default=None,
                        help="Currencies to fetch (default: BTC ETH SOL)")
    args = parser.parse_args()

    currencies = [c.upper() for c in args.currency] if args.currency else None
    main(currencies)
