#!/usr/bin/env python3
"""
Fetch historical taker buy/sell volume ratio from Binance Futures 1h klines.

Binance futures klines contain:
- Column 9: Taker buy base asset volume
- Column 5: Total volume
- Taker sell volume = Total volume - Taker buy volume
- Buy/Sell ratio = Taker buy vol / Taker sell vol

This is a direct measure of positioning pressure (who's aggressive buyer vs seller)
and is available in bulk download going back to inception.

Data source: https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1h/
"""

import os
import io
import zipfile
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

OUTPUT_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning/extended"
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"

# We want July 2025 through Feb 2026 (8 months, matching funding rate data)
MONTHS = [
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02"
]

TOKENS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "AAVEUSDT", "ADAUSDT", "ARBUSDT", "AVAXUSDT", "DOTUSDT", "INJUSDT",
    "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SUIUSDT", "UNIUSDT", "WIFUSDT"
]

# Kline columns:
# 0: Open time, 1: Open, 2: High, 3: Low, 4: Close, 5: Volume,
# 6: Close time, 7: Quote asset volume, 8: Number of trades,
# 9: Taker buy base asset volume, 10: Taker buy quote asset volume, 11: Ignore
KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "num_trades",
    "taker_buy_base_vol", "taker_buy_quote_vol", "ignore"
]


def download_klines(symbol: str, month: str) -> pd.DataFrame | None:
    """Download one monthly 1h kline ZIP and return as DataFrame."""
    url = f"{BASE_URL}/{symbol}/1h/{symbol}-1h-{month}.zip"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                # Try reading with header detection
                df = pd.read_csv(f)
                # If first row looks like a header, columns are already named
                if len(df.columns) == len(KLINE_COLS):
                    df.columns = KLINE_COLS
                else:
                    # Fall back to reading without header
                    f.seek(0)
                    df = pd.read_csv(f, header=None, names=KLINE_COLS)
                # Drop any rows where open_time is not numeric (header rows)
                df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
                return df
    except Exception as e:
        print(f"    Error fetching {symbol} {month}: {e}")
        return None


def main():
    print("=" * 70)
    print("Binance Futures Taker Buy/Sell Volume Fetcher (from klines)")
    print("=" * 70)
    print(f"Months: {MONTHS[0]} to {MONTHS[-1]}")
    print(f"Tokens: {len(TOKENS)}")
    print()

    results = {}

    for i, symbol in enumerate(TOKENS):
        print(f"\n[{i+1}/{len(TOKENS)}] {symbol}")

        all_dfs = []
        for month in MONTHS:
            df = download_klines(symbol, month)
            if df is not None:
                all_dfs.append(df)
                # print(f"    {month}: {len(df)} candles")

        if not all_dfs:
            print(f"  No data available for {symbol}")
            results[symbol] = {"rows": 0, "months": 0}
            continue

        combined = pd.concat(all_dfs, ignore_index=True)

        # Parse
        combined["timestamp"] = pd.to_datetime(combined["open_time"].astype(float), unit="ms", utc=True)
        combined["volume"] = combined["volume"].astype(float)
        combined["taker_buy_base_vol"] = combined["taker_buy_base_vol"].astype(float)

        # Compute taker sell volume and buy/sell ratio
        combined["taker_sell_vol"] = combined["volume"] - combined["taker_buy_base_vol"]
        # Avoid division by zero
        combined["buySellRatio"] = np.where(
            combined["taker_sell_vol"] > 0,
            combined["taker_buy_base_vol"] / combined["taker_sell_vol"],
            1.0
        )

        # Convert buySellRatio to a longShortRatio-like metric
        # buySellRatio > 1 means more aggressive buying (long pressure)
        # buySellRatio < 1 means more aggressive selling (short pressure)
        # This IS the positioning signal
        combined["longShortRatio"] = combined["buySellRatio"].round(4)
        combined["longAccount"] = (combined["buySellRatio"] / (1 + combined["buySellRatio"])).round(4)
        combined["shortAccount"] = (1 - combined["longAccount"]).round(4)

        combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        combined["symbol"] = symbol

        # Save
        out = combined[["timestamp", "symbol", "longShortRatio", "longAccount", "shortAccount",
                         "buySellRatio", "taker_buy_base_vol", "taker_sell_vol", "volume"]].copy()
        # Convert ratio columns to string for consistency
        for col in ["longShortRatio", "longAccount", "shortAccount"]:
            out[col] = out[col].astype(str)

        output_path = os.path.join(OUTPUT_DIR, f"{symbol}_taker_buysell.parquet")
        out.to_parquet(output_path, index=False)

        date_range = out["timestamp"].max() - out["timestamp"].min()
        months = date_range.days / 30.44

        results[symbol] = {
            "rows": len(out),
            "start": str(out["timestamp"].min()),
            "end": str(out["timestamp"].max()),
            "months": round(months, 1),
        }
        print(f"  Saved {len(out)} hourly rows ({months:.1f} months)")

    # Summary
    print("\n" + "=" * 70)
    print("TAKER BUY/SELL VOLUME DATA SUMMARY")
    print("=" * 70)

    for symbol, info in results.items():
        if info["rows"] > 0:
            print(f"  {symbol}: {info['rows']:,} rows, {info['months']} months ({info['start']} to {info['end']})")
        else:
            print(f"  {symbol}: NO DATA")

    total_tokens = sum(1 for v in results.values() if v["rows"] > 0)
    print(f"\nTokens with data: {total_tokens}/{len(TOKENS)}")


if __name__ == "__main__":
    main()
