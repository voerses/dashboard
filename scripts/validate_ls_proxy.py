#!/usr/bin/env python3
"""
Validate the funding rate L/S proxy against actual L/S ratio data.

This script:
1. Loads actual L/S ratio data from Binance metrics (data.binance.vision)
2. Loads the funding rate proxy
3. Computes correlation between the two for the overlap period
4. Calibrates the proxy scaling factor to match observed L/S distributions
5. Reports correlation and signal fidelity metrics

The goal is to validate that our funding-based proxy is a reliable substitute
for actual L/S data, especially for the Retail Contrarian signal.
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

METRICS_DIR = Path("data/alternative/binance_metrics/daily")
PROXY_DIR = Path("data/alternative/funding_ls_proxy")
OUTPUT_DIR = Path("data/alternative/ls_validation")


def load_actual_ls(symbol: str) -> pd.DataFrame | None:
    """Load actual L/S metrics from Binance data.binance.vision."""
    # Convert symbol format: BTC -> BTCUSDT
    usdt_sym = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol
    path = METRICS_DIR / f"{usdt_sym}_ls_metrics.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df.set_index("date").sort_index()
    return df


def load_proxy(symbol: str) -> pd.DataFrame | None:
    """Load funding rate proxy."""
    path = PROXY_DIR / f"{symbol}_funding_proxy.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def correlate_symbol(symbol: str) -> dict | None:
    """Compute correlation between actual L/S and funding proxy for one symbol."""
    actual = load_actual_ls(symbol)
    proxy = load_proxy(symbol)

    if actual is None or proxy is None:
        return None

    # Align on date index
    actual.index = pd.to_datetime(actual.index).tz_localize(None)
    proxy.index = pd.to_datetime(proxy.index).tz_localize(None)

    # Find overlapping dates
    common = actual.index.intersection(proxy.index)
    if len(common) < 30:
        return None

    a = actual.loc[common]
    p = proxy.loc[common]

    result = {
        "symbol": symbol,
        "overlap_days": len(common),
        "overlap_start": str(common.min().date()),
        "overlap_end": str(common.max().date()),
    }

    # Correlate different L/S metrics with funding proxy
    ls_metrics = {
        "count_ls_ratio": "Global L/S (account count)",
        "count_toptrader_ls_ratio": "Top Trader L/S (account count)",
        "sum_toptrader_ls_ratio": "Top Trader L/S (position value)",
        "taker_buy_sell_ratio": "Taker Buy/Sell Vol",
    }

    proxy_cols = {
        "funding_rate_daily": "Daily Funding Rate",
        "fr_zscore_14d": "Funding Z-Score (14d)",
        "ls_proxy_14d": "L/S Proxy (14d)",
        "cum_funding_7d": "Cumulative Funding (7d)",
        "fr_pctrank_60d": "Funding Percentile (60d)",
    }

    for ls_col, ls_name in ls_metrics.items():
        if ls_col not in a.columns:
            continue
        for proxy_col, proxy_name in proxy_cols.items():
            if proxy_col not in p.columns:
                continue

            ls_vals = a[ls_col].values
            proxy_vals = p[proxy_col].values

            # Remove NaN pairs
            mask = ~(np.isnan(ls_vals) | np.isnan(proxy_vals))
            if mask.sum() < 20:
                continue

            ls_clean = ls_vals[mask]
            proxy_clean = proxy_vals[mask]

            corr_pearson, p_val = stats.pearsonr(ls_clean, proxy_clean)
            corr_spearman, sp_val = stats.spearmanr(ls_clean, proxy_clean)

            key = f"{ls_col}_vs_{proxy_col}"
            result[f"{key}_pearson"] = round(corr_pearson, 4)
            result[f"{key}_spearman"] = round(corr_spearman, 4)
            result[f"{key}_p_val"] = round(p_val, 6)

    # Check signal agreement: when actual L/S is extreme, is proxy also extreme?
    if "count_ls_ratio" in a.columns and "fr_zscore_14d" in p.columns:
        ls = a["count_ls_ratio"].dropna()
        zs = p["fr_zscore_14d"].dropna()
        common2 = ls.index.intersection(zs.index)
        if len(common2) >= 50:
            ls_c = ls.loc[common2]
            zs_c = zs.loc[common2]

            # Define "extreme" as top/bottom 20th percentile
            ls_high = ls_c > ls_c.quantile(0.8)
            ls_low = ls_c < ls_c.quantile(0.2)
            zs_high = zs_c > zs_c.quantile(0.8)
            zs_low = zs_c < zs_c.quantile(0.2)

            # Agreement rate
            result["extreme_long_agreement"] = round((ls_high & zs_high).sum() / ls_high.sum(), 4) if ls_high.sum() > 0 else None
            result["extreme_short_agreement"] = round((ls_low & zs_low).sum() / ls_low.sum(), 4) if ls_low.sum() > 0 else None

    return result


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Determine which symbols have both actual and proxy data
    symbols = set()
    for f in PROXY_DIR.glob("*_funding_proxy.parquet"):
        sym = f.stem.replace("_funding_proxy", "")
        symbols.add(sym)

    print(f"Checking {len(symbols)} proxy symbols against actual L/S data\n")

    results = []
    for sym in sorted(symbols):
        r = correlate_symbol(sym)
        if r:
            results.append(r)
            # Print key correlation
            key = "count_ls_ratio_vs_funding_rate_daily_spearman"
            corr = r.get(key, None)
            days = r["overlap_days"]
            if corr is not None:
                print(f"  {sym:8s}: {days:5d} days, Spearman(L/S vs FundingRate) = {corr:+.4f}")
            else:
                print(f"  {sym:8s}: {days:5d} days, no correlation computed")

    if not results:
        print("\nNo overlapping data found. Run fetch_binance_metrics_fast.py first.")
        return

    df = pd.DataFrame(results)
    df.to_parquet(OUTPUT_DIR / "ls_proxy_validation.parquet", index=False)
    df.to_csv(OUTPUT_DIR / "ls_proxy_validation.csv", index=False)

    # Summary statistics
    print(f"\n{'='*70}")
    print("VALIDATION SUMMARY")
    print(f"{'='*70}")
    print(f"Symbols with overlap: {len(df)}")

    # Key correlation: Global L/S vs Funding Rate
    key_col = "count_ls_ratio_vs_funding_rate_daily_spearman"
    if key_col in df.columns:
        vals = df[key_col].dropna()
        print(f"\nGlobal L/S vs Daily Funding Rate (Spearman):")
        print(f"  Mean: {vals.mean():.4f}")
        print(f"  Median: {vals.median():.4f}")
        print(f"  Min: {vals.min():.4f}")
        print(f"  Max: {vals.max():.4f}")
        print(f"  Positive: {(vals > 0).sum()}/{len(vals)}")

    # Key correlation: Global L/S vs Z-Score proxy
    key_col2 = "count_ls_ratio_vs_fr_zscore_14d_spearman"
    if key_col2 in df.columns:
        vals = df[key_col2].dropna()
        print(f"\nGlobal L/S vs Funding Z-Score 14d (Spearman):")
        print(f"  Mean: {vals.mean():.4f}")
        print(f"  Median: {vals.median():.4f}")
        print(f"  Min: {vals.min():.4f}")
        print(f"  Max: {vals.max():.4f}")

    # Signal agreement
    if "extreme_long_agreement" in df.columns:
        vals = df["extreme_long_agreement"].dropna()
        if len(vals) > 0:
            print(f"\nExtreme Long Signal Agreement (top 20pct overlap):")
            print(f"  Mean: {vals.mean():.2%}")

    if "extreme_short_agreement" in df.columns:
        vals = df["extreme_short_agreement"].dropna()
        if len(vals) > 0:
            print(f"Extreme Short Signal Agreement (bottom 20pct overlap):")
            print(f"  Mean: {vals.mean():.2%}")

    # Top correlations
    if key_col in df.columns:
        print(f"\nTop 10 by L/S vs Funding Rate correlation:")
        top = df.nlargest(10, key_col)[["symbol", "overlap_days", key_col]]
        print(top.to_string(index=False))

    print(f"\nResults saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
