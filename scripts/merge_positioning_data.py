#!/usr/bin/env python3
"""
Merge all positioning data sources into unified extended dataset.

Data sources (in priority order for the L/S ratio):
1. Binance globalLongShortAccountRatio API (actual L/S ratio) - Mar 3-23, 2026 + Feb 23-Mar 3 extension
2. Funding rate derived L/S proxy (Jul 2025 - Feb 2026)
3. Taker buy/sell volume ratio (Jul 2025 - Feb 2026)

Also validates proxy quality by checking correlation in the overlapping period.
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, timezone

POSITIONING_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning"
EXTENDED_DIR = f"{POSITIONING_DIR}/extended"

TOKENS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "AAVEUSDT", "ADAUSDT", "ARBUSDT", "AVAXUSDT", "DOTUSDT", "INJUSDT",
    "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SUIUSDT", "UNIUSDT", "WIFUSDT"
]


def load_original_ls_data() -> pd.DataFrame:
    """Load the original 20-day L/S ratio data."""
    path = os.path.join(POSITIONING_DIR, "global_ls_ratio.parquet")
    df = pd.read_parquet(path)
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    df["source"] = "binance_api"
    return df


def load_extended_ls_data(symbol: str) -> pd.DataFrame:
    """Load backward-paginated L/S data (extra ~7 days)."""
    path = os.path.join(EXTENDED_DIR, f"{symbol}_ls_ratio.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    df["source"] = "binance_api_extended"
    return df


def load_funding_rate_data(symbol: str) -> pd.DataFrame:
    """Load funding rate proxy data."""
    path = os.path.join(EXTENDED_DIR, f"{symbol}_funding_rate.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    df["source"] = "funding_rate_proxy"
    return df


def load_taker_buysell_data(symbol: str) -> pd.DataFrame:
    """Load taker buy/sell volume ratio data."""
    path = os.path.join(EXTENDED_DIR, f"{symbol}_taker_buysell.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    df["source"] = "taker_buysell"
    return df


def validate_proxy_correlation(original: pd.DataFrame, proxy: pd.DataFrame,
                                symbol: str, proxy_name: str) -> dict:
    """Check correlation between actual L/S and proxy in overlapping period."""
    orig = original[original["symbol"] == symbol][["timestamp", "longShortRatio"]].copy()
    orig = orig.rename(columns={"longShortRatio": "actual_ls"})

    prox = proxy[["timestamp", "longShortRatio"]].copy()
    prox = prox.rename(columns={"longShortRatio": "proxy_ls"})

    merged = orig.merge(prox, on="timestamp", how="inner")

    if len(merged) < 10:
        return {"overlap_rows": len(merged), "correlation": None}

    corr = merged["actual_ls"].corr(merged["proxy_ls"])
    rank_corr = merged["actual_ls"].rank().corr(merged["proxy_ls"].rank())

    return {
        "overlap_rows": len(merged),
        "pearson_corr": round(corr, 4),
        "spearman_corr": round(rank_corr, 4),
        "proxy_mean": round(merged["proxy_ls"].mean(), 4),
        "actual_mean": round(merged["actual_ls"].mean(), 4),
    }


def main():
    print("=" * 70)
    print("Merge & Validate Extended Positioning Data")
    print("=" * 70)

    # Load original data
    original = load_original_ls_data()
    print(f"Original L/S data: {len(original)} rows, {original['symbol'].nunique()} symbols")
    print(f"  Date range: {original['timestamp'].min()} to {original['timestamp'].max()}")

    # Validate proxy quality
    print("\n--- PROXY VALIDATION (overlapping period) ---\n")
    validation_results = {}

    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        print(f"{symbol}:")

        fr_data = load_funding_rate_data(symbol)
        if not fr_data.empty:
            result = validate_proxy_correlation(original, fr_data, symbol, "funding_rate")
            print(f"  Funding Rate proxy: {result}")
            validation_results[f"{symbol}_funding_rate"] = result

        tb_data = load_taker_buysell_data(symbol)
        if not tb_data.empty:
            result = validate_proxy_correlation(original, tb_data, symbol, "taker_buysell")
            print(f"  Taker Buy/Sell proxy: {result}")
            validation_results[f"{symbol}_taker_buysell"] = result

    # Build merged dataset per symbol
    print("\n--- BUILDING MERGED DATASETS ---\n")

    merge_results = {}

    for symbol in TOKENS:
        # Priority: actual L/S ratio > extended L/S > taker buy/sell > funding rate
        # Taker buy/sell is preferred over funding rate because it's hourly-native (not forward-filled from 8h)

        parts = []

        # 1. Original API data (Mar 3-23)
        orig = original[original["symbol"] == symbol].copy()
        if not orig.empty:
            parts.append(("binance_api", orig))

        # 2. Extended backward pagination (Feb 23 - Mar 3)
        ext = load_extended_ls_data(symbol)
        if not ext.empty:
            parts.append(("binance_api_ext", ext))

        # 3. Taker buy/sell volume (Jul 2025 - Feb 2026) - hourly native
        tb = load_taker_buysell_data(symbol)
        if not tb.empty:
            parts.append(("taker_buysell", tb))

        # 4. Funding rate proxy (Jul 2025 - Feb 2026) - 8h forward-filled to 1h
        fr = load_funding_rate_data(symbol)
        if not fr.empty:
            parts.append(("funding_rate", fr))

        if not parts:
            print(f"  {symbol}: NO DATA")
            merge_results[symbol] = {"rows": 0}
            continue

        # Merge: for each timestamp, take the highest-priority source
        # Build a combined df, then for duplicate timestamps, keep highest priority
        all_data = []
        source_priority = {
            "binance_api": 1,
            "binance_api_extended": 2,
            "binance_api_ext": 2,
            "taker_buysell": 3,
            "funding_rate_proxy": 4,
        }

        for source_name, df in parts:
            df = df.copy()
            df["_priority"] = df["source"].map(source_priority).fillna(99)
            all_data.append(df[["timestamp", "symbol", "longShortRatio", "longAccount",
                               "shortAccount", "source", "_priority"]])

        combined = pd.concat(all_data, ignore_index=True)
        combined = combined.sort_values(["timestamp", "_priority"])
        combined = combined.drop_duplicates(subset=["timestamp"], keep="first")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        combined = combined.drop(columns=["_priority"])

        # Save merged file
        output_path = os.path.join(EXTENDED_DIR, f"{symbol}_merged.parquet")
        combined.to_parquet(output_path, index=False)

        date_range = combined["timestamp"].max() - combined["timestamp"].min()
        months = date_range.days / 30.44

        # Count rows by source
        source_counts = combined["source"].value_counts().to_dict()

        merge_results[symbol] = {
            "total_rows": len(combined),
            "months": round(months, 1),
            "start": str(combined["timestamp"].min()),
            "end": str(combined["timestamp"].max()),
            "source_breakdown": source_counts,
        }

        print(f"  {symbol}: {len(combined):,} rows, {months:.1f} months | Sources: {source_counts}")

    # Final summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    total_rows = sum(r["total_rows"] for r in merge_results.values() if "total_rows" in r)
    tokens_ok = sum(1 for r in merge_results.values() if r.get("total_rows", 0) > 0)

    all_months = [r["months"] for r in merge_results.values() if "months" in r]

    print(f"\nTokens with data: {tokens_ok}/{len(TOKENS)}")
    print(f"Total rows across all tokens: {total_rows:,}")
    if all_months:
        print(f"Date coverage: {min(all_months)} to {max(all_months)} months")

    print(f"\nData sources used:")
    print(f"  1. Binance L/S Ratio API (actual): ~28 days (Feb 23 - Mar 23, 2026)")
    print(f"  2. Taker Buy/Sell Volume (klines bulk): 8 months (Jul 2025 - Feb 2026)")
    print(f"  3. Funding Rate proxy (bulk): 8 months (Jul 2025 - Feb 2026)")

    print(f"\nProxy validation (overlapping period):")
    for key, val in validation_results.items():
        print(f"  {key}: pearson={val.get('pearson_corr', 'N/A')}, spearman={val.get('spearman_corr', 'N/A')}")

    # Save overall summary
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tokens_with_data": tokens_ok,
        "total_tokens": len(TOKENS),
        "total_rows": total_rows,
        "per_token": merge_results,
        "proxy_validation": validation_results,
    }

    summary_path = os.path.join(EXTENDED_DIR, "_merge_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nMerge summary saved to: {summary_path}")

    print(f"\nOutput files:")
    for symbol in TOKENS:
        path = os.path.join(EXTENDED_DIR, f"{symbol}_merged.parquet")
        if os.path.exists(path):
            size = os.path.getsize(path)
            print(f"  {path} ({size:,} bytes)")


if __name__ == "__main__":
    main()
