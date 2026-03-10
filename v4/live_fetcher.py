"""V4 Live Data Fetcher — fetches OHLCV + funding, appends to parquet cache.

Unlike v3's WAL-based approach, v4 writes directly to parquet files so that
precompute_strategy_signals() can read updated data without a merge step.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_HOUR_MS = 3_600_000


class LiveFetcher:
    """Fetches live OHLCV + funding data and appends to parquet cache."""

    def __init__(self, exchange=None, data_dir: str = "data"):
        self.exchange = exchange
        self.data_dir = data_dir

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    def _resolve_symbol(self, token: str, market: str) -> str:
        """Resolve ccxt symbol for the given token and market type."""
        if market == "perp":
            return f"{token}/USDT:USDT"
        return f"{token}/USDT"

    # ------------------------------------------------------------------
    # AC1: OHLCV fetching
    # ------------------------------------------------------------------

    def fetch_ohlcv(
        self,
        token: str,
        market: str = "spot",
        timeframe: str = "1h",
        limit: int = 10,
    ) -> list[dict]:
        """Fetch recent OHLCV candles from the exchange.

        Returns a list of bar dicts with keys:
        timestamp, open, high, low, close, volume.
        """
        symbol = self._resolve_symbol(token, market)
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        bars = []
        for candle in raw:
            bars.append({
                "timestamp": int(candle[0]),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })
        return bars

    # ------------------------------------------------------------------
    # AC2: Funding rate fetching
    # ------------------------------------------------------------------

    def fetch_funding_rates(self, token: str, limit: int = 10) -> list[dict]:
        """Fetch recent funding rate records for a perpetual."""
        symbol = self._resolve_symbol(token, "perp")
        raw = self.exchange.fetch_funding_rate_history(symbol, limit=limit)
        rates = []
        for entry in raw:
            rates.append({
                "timestamp": int(entry.get("timestamp", 0)),
                "fundingRate": float(entry.get("fundingRate", 0)),
            })
        return rates

    # ------------------------------------------------------------------
    # AC3: Closed bar filter
    # ------------------------------------------------------------------

    def filter_closed_bars(
        self, bars: list[dict], now_ms: Optional[int] = None,
    ) -> list[dict]:
        """Exclude incomplete (still-forming) hourly bars.

        A bar is closed if timestamp + 3600000 <= now_ms.
        """
        if now_ms is None:
            import time
            now_ms = int(time.time() * 1000)
        return [b for b in bars if b["timestamp"] + _HOUR_MS <= now_ms]

    # ------------------------------------------------------------------
    # AC4: Parquet append
    # ------------------------------------------------------------------

    def _parquet_path(self, token: str, market: str) -> str:
        """Return the parquet cache path for a token/market."""
        return os.path.join(
            self.data_dir, market, "1h_cache", f"{token}_1h.parquet",
        )

    def append_to_parquet(
        self, token: str, market: str, bars: list[dict],
    ) -> None:
        """Append bars to the parquet cache file.

        - Creates file and directories if they don't exist.
        - Deduplicates on timestamp (keeps last occurrence).
        - Writes atomically via temp file + rename.
        """
        if not bars:
            return

        path = self._parquet_path(token, market)
        cache_dir = os.path.dirname(path)
        os.makedirs(cache_dir, exist_ok=True)

        # Build new DataFrame from bars
        new_df = pd.DataFrame(bars)
        # Convert ms-epoch timestamps to DatetimeIndex matching existing parquet format
        new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
        new_df = new_df.set_index("timestamp")

        # Read existing data if present
        if os.path.exists(path):
            existing_df = pd.read_parquet(path)
            combined = pd.concat([existing_df, new_df])
        else:
            combined = new_df

        # Deduplicate: keep last occurrence (new data wins)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()

        # Atomic write: temp file + rename
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
        os.close(fd)
        try:
            combined.to_parquet(tmp_path)
            os.rename(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Funding merge into parquet
    # ------------------------------------------------------------------

    def merge_funding_into_parquet(
        self, token: str, funding_rates: list[dict],
    ) -> None:
        """Merge funding rates into the perp parquet's funding_1h column.

        Funding rates are mapped by timestamp to the parquet index.
        Exchange timestamps are rounded to the nearest hour to align with
        the hourly parquet index.  Timestamps without a matching funding
        rate keep their existing value (or 0.0 if the column is new).
        """
        if not funding_rates:
            return

        path = self._parquet_path(token, "perp")
        if not os.path.exists(path):
            logger.warning("No perp parquet for %s at %s — skipping funding merge", token, path)
            return

        df = pd.read_parquet(path)

        # Build mapping: round exchange ms timestamps to nearest hour, then
        # convert to tz-naive Timestamp to match the parquet index type.
        funding_map = {}
        for r in funding_rates:
            ts_ms = int(r["timestamp"])
            # Round to nearest hour boundary (floor to 3600s)
            rounded_ms = (ts_ms // _HOUR_MS) * _HOUR_MS
            ts = pd.Timestamp(rounded_ms, unit="ms")
            funding_map[ts] = float(r["fundingRate"])

        # Map parquet index to funding values
        new_funding = df.index.map(lambda ts: funding_map.get(ts))
        mask = new_funding.notna()
        if not mask.any():
            return  # No matching timestamps — skip write

        if "funding_1h" in df.columns:
            df.loc[mask, "funding_1h"] = new_funding[mask].astype(float)
        else:
            df["funding_1h"] = new_funding.astype(float)
            df["funding_1h"] = df["funding_1h"].fillna(0.0)

        # Atomic write
        cache_dir = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
        os.close(fd)
        try:
            df.to_parquet(tmp_path)
            os.rename(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
