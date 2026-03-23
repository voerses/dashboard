#!/usr/bin/env python3
"""
Build a synthetic Long/Short ratio proxy from funding rate data.

Theory:
- When funding rate is positive, longs pay shorts -> longs are dominant
- When funding rate is negative, shorts pay longs -> shorts are dominant
- Extreme positive funding = crowded longs (contrarian signal: sell)
- Extreme negative funding = crowded shorts (contrarian signal: buy)

This constructs a daily "positioning proxy" from 8-hourly funding rates by:
1. Computing daily average funding rate
2. Computing rolling z-score of funding rate (positioning intensity)
3. Mapping to a synthetic L/S ratio:
   - funding_ls_proxy = 1 + k * zscore(funding_rate)
   - where k scales the z-score to match observed L/S ratio ranges

Also computes:
- Cumulative funding rate (trend indicator)
- Funding rate momentum (acceleration of positioning)
- Cross-sectional funding rank (relative positioning across tokens)

Saves per-symbol and combined parquet files.
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd


CACHE_DIR = Path("data/perp/1h_cache")
OUTPUT_DIR = Path("data/alternative/funding_ls_proxy")
MIN_HISTORY_DAYS = 180  # Require at least 6 months

# Target symbols (will process all with sufficient history)
PRIORITY_SYMBOLS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK",
    "BNB", "DOT", "LTC", "UNI", "AAVE", "ARB", "APT", "NEAR",
    "OP", "INJ", "SUI", "SEI", "ATOM", "FIL", "CRV", "SNX",
    "ALGO", "AXS", "SAND", "GALA", "DYDX", "SHIB",
]


def load_funding_rate(symbol: str) -> pd.Series | None:
    """Load hourly funding rate for a symbol from 1h cache."""
    path = CACHE_DIR / f"{symbol}_1h.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if "funding_rate" not in df.columns:
        return None
    fr = df["funding_rate"].dropna()
    if len(fr) == 0:
        return None
    return fr


def build_daily_proxy(funding_rate: pd.Series, symbol: str) -> pd.DataFrame:
    """
    Build daily L/S proxy from hourly funding rates.

    Funding rates are typically set every 8 hours on Binance (00:00, 08:00, 16:00 UTC).
    Between settlements, the rate is constant, so we can resample to daily.
    """
    # Resample to daily: use mean of funding rates in that day
    daily_fr = funding_rate.resample("1D").mean().dropna()

    if len(daily_fr) < 30:
        return pd.DataFrame()

    df = pd.DataFrame({"funding_rate_daily": daily_fr})
    df["symbol"] = symbol

    # Annualized funding rate (for context: ~10% means longs paying 10%/yr)
    df["funding_annualized"] = df["funding_rate_daily"] * 3 * 365  # 3 settlements/day * 365 days

    # Rolling statistics for z-score computation
    for window in [7, 14, 30, 60]:
        col = f"fr_zscore_{window}d"
        rolling_mean = df["funding_rate_daily"].rolling(window, min_periods=max(window // 2, 5)).mean()
        rolling_std = df["funding_rate_daily"].rolling(window, min_periods=max(window // 2, 5)).std()
        df[col] = (df["funding_rate_daily"] - rolling_mean) / rolling_std.clip(lower=1e-8)

    # Synthetic L/S ratio proxy
    # Calibrated so that zscore of +2 maps to L/S ~1.5 and -2 maps to L/S ~0.67
    # This approximates the observed range from actual L/S data
    k = 0.15  # scaling factor; will be calibrated against actual data
    df["ls_proxy_7d"] = 1.0 + k * df["fr_zscore_7d"]
    df["ls_proxy_14d"] = 1.0 + k * df["fr_zscore_14d"]
    df["ls_proxy_30d"] = 1.0 + k * df["fr_zscore_30d"]

    # Cumulative funding (trend of positioning over time)
    df["cum_funding_7d"] = df["funding_rate_daily"].rolling(7).sum()
    df["cum_funding_14d"] = df["funding_rate_daily"].rolling(14).sum()
    df["cum_funding_30d"] = df["funding_rate_daily"].rolling(30).sum()

    # Funding rate momentum (is positioning accelerating?)
    df["fr_momentum_3d"] = df["funding_rate_daily"].rolling(3).mean() - df["funding_rate_daily"].rolling(14).mean()
    df["fr_momentum_7d"] = df["funding_rate_daily"].rolling(7).mean() - df["funding_rate_daily"].rolling(30).mean()

    # Percentile rank over trailing window (0 = most bearish, 1 = most bullish)
    def pct_rank(s, window):
        return s.rolling(window, min_periods=window // 2).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )

    df["fr_pctrank_60d"] = pct_rank(df["funding_rate_daily"], 60)
    df["fr_pctrank_90d"] = pct_rank(df["funding_rate_daily"], 90)

    # Binary signal: extreme positioning
    df["extreme_long"] = (df["fr_zscore_14d"] > 2.0).astype(int)
    df["extreme_short"] = (df["fr_zscore_14d"] < -2.0).astype(int)

    return df


def compute_cross_sectional_ranks(all_dfs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Add cross-sectional rank of funding rate across all tokens for each day."""
    # Combine all daily funding rates into a single frame
    fr_matrix = pd.DataFrame({
        sym: df["funding_rate_daily"]
        for sym, df in all_dfs.items()
    })

    # Rank each day (higher rank = more positive funding = more longs crowded)
    ranks = fr_matrix.rank(axis=1, pct=True)

    for sym in all_dfs:
        if sym in ranks.columns:
            all_dfs[sym]["fr_cross_rank"] = ranks[sym]

    return all_dfs


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load all symbols
    files = sorted(CACHE_DIR.glob("*_1h.parquet"))
    print(f"Found {len(files)} token files in {CACHE_DIR}")

    all_proxies = {}
    skipped = []

    for f in files:
        sym = f.stem.replace("_1h", "")
        fr = load_funding_rate(sym)
        if fr is None:
            continue

        days = (fr.index.max() - fr.index.min()).days
        if days < MIN_HISTORY_DAYS:
            skipped.append((sym, days))
            continue

        proxy = build_daily_proxy(fr, sym)
        if len(proxy) < 30:
            skipped.append((sym, days))
            continue

        all_proxies[sym] = proxy

    print(f"Built proxy for {len(all_proxies)} symbols (skipped {len(skipped)} with <{MIN_HISTORY_DAYS} days)")

    # Add cross-sectional ranks
    all_proxies = compute_cross_sectional_ranks(all_proxies)

    # Save per-symbol files
    for sym, df in all_proxies.items():
        path = OUTPUT_DIR / f"{sym}_funding_proxy.parquet"
        df.to_parquet(path)

    # Save combined file
    combined = pd.concat(all_proxies.values(), ignore_index=False)
    combined.to_parquet(OUTPUT_DIR / "all_funding_proxy.parquet")

    # Summary
    print(f"\nSaved {len(all_proxies)} proxy files to {OUTPUT_DIR}")
    print(f"Combined: {len(combined)} rows")
    print(f"\nSample date ranges:")
    for sym in PRIORITY_SYMBOLS[:10]:
        if sym in all_proxies:
            df = all_proxies[sym]
            print(f"  {sym}: {df.index.min().date()} to {df.index.max().date()} ({len(df)} days)")

    # Statistics
    print(f"\nColumns per symbol: {list(combined.columns)}")

    return all_proxies


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
