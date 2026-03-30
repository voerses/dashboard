"""V4 Live Data Fetcher — fetches OHLCV + funding, appends to parquet cache.

Unlike v3's WAL-based approach, v4 writes directly to parquet files so that
precompute_strategy_signals() can read updated data without a merge step.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_HOUR_MS = 3_600_000
_MINUTE_MS = 60_000


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

    @staticmethod
    def _parse_ohlcv(raw: list) -> list[dict]:
        """Parse ccxt OHLCV response into bar dicts."""
        return [
            {
                "timestamp": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }
            for c in raw
        ]

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
        return self._parse_ohlcv(raw)

    def fetch_ohlcv_since(
        self,
        token: str,
        market: str = "spot",
        timeframe: str = "1h",
        since_ms: int = 0,
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch OHLCV candles starting from a specific timestamp.

        Like fetch_ohlcv() but passes `since` to ccxt for paginated backfill.
        Separate method to avoid risk to existing fetch_ohlcv() call sites.
        """
        symbol = self._resolve_symbol(token, market)
        raw = self.exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since_ms, limit=limit,
        )
        return self._parse_ohlcv(raw)

    # ------------------------------------------------------------------
    # Backfill: find last timestamp + paginated gap fill
    # ------------------------------------------------------------------

    @staticmethod
    def _ts_to_ms(ts) -> int:
        """Convert a parquet index value (Timestamp or int) to epoch milliseconds.

        Tz-naive Timestamps are treated as UTC (matching how append_to_parquet
        stores them: pd.to_datetime(..., utc=True).dt.tz_localize(None)).
        """
        if isinstance(ts, pd.Timestamp):
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            # Avoid datetime.timestamp() which assumes local tz for naive timestamps
            return int((ts - pd.Timestamp("1970-01-01")).total_seconds() * 1000)
        return int(ts)

    def _find_last_timestamp_ms(self, token: str, market: str) -> Optional[int]:
        """Find the ms timestamp of the last bar in live buffer or historical cache.

        Checks live buffer first (most recent data), then falls back to
        historical 1h_cache. Returns None if no data exists for this token/market.
        """
        # Check live buffer first
        live_path = self._parquet_path(token, market)
        if os.path.exists(live_path):
            try:
                idx = pd.read_parquet(live_path, columns=[]).index
                if len(idx) > 0:
                    return self._ts_to_ms(idx.max())
            except Exception as e:
                logger.warning("Failed to read live parquet %s: %s", live_path, e)

        # Fall back to historical cache
        hist_path = os.path.join(
            self.data_dir, market, "1h_cache", f"{token}_1h.parquet",
        )
        if os.path.exists(hist_path):
            try:
                idx = pd.read_parquet(hist_path, columns=[]).index
                if len(idx) > 0:
                    return self._ts_to_ms(idx.max())
            except Exception as e:
                logger.warning("Failed to read hist parquet %s: %s", hist_path, e)

        return None

    def backfill_gaps(
        self,
        tokens: set[str],
        gap_threshold_hours: int = 24,
        chunk_limit: int = 1000,
        max_errors: int = 5,
        deadline_s: float | None = None,
    ) -> dict[str, int]:
        """Backfill data gaps >gap_threshold_hours for each token/market pair.

        Called once at startup to repair gaps from outages.
        Returns a dict of "TOKEN/market" -> bars_backfilled.
        Aborts early after max_errors consecutive fetch failures (network down).
        If deadline_s is set, stops fetching when wall-clock time exceeds it.
        """
        now_ms = int(time.time() * 1000)
        threshold_ms = gap_threshold_hours * _HOUR_MS
        results: dict[str, int] = {}
        consecutive_errors = 0
        _deadline = time.monotonic() + deadline_s if deadline_s is not None else None

        for token in sorted(tokens):
            for market in ("spot", "perp"):
                if _deadline is not None and time.monotonic() >= _deadline:
                    logger.info("Backfill deadline reached — stopping with %d tokens done", len(results))
                    return results
                if consecutive_errors >= max_errors:
                    logger.warning(
                        "Backfill aborted: %d consecutive errors (network down?)",
                        consecutive_errors,
                    )
                    return results

                last_ms = self._find_last_timestamp_ms(token, market)
                if last_ms is None:
                    continue

                gap_ms = now_ms - last_ms
                if gap_ms <= threshold_ms:
                    continue

                gap_hours = gap_ms // _HOUR_MS
                key = f"{token}/{market}"
                logger.info(
                    "Backfilling %s: %d missing hours (%s -> now)",
                    key, gap_hours,
                    pd.Timestamp(last_ms, unit="ms").strftime("%b %d %H:%M"),
                )

                # Paginate from last known bar + 1 hour
                since_ms = last_ms + _HOUR_MS
                total_bars = 0

                while since_ms < now_ms:
                    if _deadline is not None and time.monotonic() >= _deadline:
                        logger.info("Backfill deadline reached mid-token %s/%s", token, market)
                        break
                    try:
                        bars = self.fetch_ohlcv_since(
                            token, market, since_ms=since_ms, limit=chunk_limit,
                        )
                        consecutive_errors = 0  # Reset on success
                    except Exception as e:
                        consecutive_errors += 1
                        logger.warning("Backfill fetch %s failed: %s", key, e)
                        break

                    if not bars:
                        break

                    closed = self.filter_closed_bars(bars, now_ms=now_ms)
                    if closed:
                        self.append_to_parquet(token, market, closed)
                        total_bars += len(closed)

                    # Advance: next page starts after last fetched bar
                    next_since = bars[-1]["timestamp"] + _HOUR_MS
                    if next_since <= since_ms:
                        # Guard against infinite loop (exchange returning same data)
                        break
                    since_ms = next_since

                    # Rate-limit courtesy: small sleep between pagination pages
                    # (ccxt enableRateLimit handles most throttling, this is extra safety)
                    time.sleep(0.1)

                if total_bars > 0:
                    results[key] = total_bars

        return results

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
            now_ms = int(time.time() * 1000)
        return [b for b in bars if b["timestamp"] + _HOUR_MS <= now_ms]

    def filter_closed_bars_1m(
        self, bars: list[dict], now_ms: Optional[int] = None,
    ) -> list[dict]:
        """Exclude incomplete (still-forming) 1-minute bars.

        A bar is closed if timestamp + 60_000 <= now_ms.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        return [b for b in bars if b["timestamp"] + _MINUTE_MS <= now_ms]

    # ------------------------------------------------------------------
    # AC4: Parquet append
    # ------------------------------------------------------------------

    def _parquet_path(self, token: str, market: str) -> str:
        """Return the live buffer parquet path for a token/market.

        Live data is written to data/{market}/live/{TOKEN}.parquet,
        separate from the immutable historical cache in 1h_cache/.
        Consumers use v4/data_loader.load_token_data() to merge both
        at read time.
        """
        return os.path.join(
            self.data_dir, market, "live", f"{token}.parquet",
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
            # Ensure index types match: convert integer index to DatetimeIndex
            if not isinstance(existing_df.index, pd.DatetimeIndex):
                existing_df.index = pd.to_datetime(existing_df.index, unit="ms")
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
    # 1m parquet support
    # ------------------------------------------------------------------

    def _1m_parquet_path(self, token: str) -> str:
        """Return the 1m cache parquet path for a perp token.

        1m data writes directly to data/perp/1m_cache/{TOKEN}_1m.parquet
        (no live buffer — consistent with tools/fetch_today_data.py).
        """
        return os.path.join(
            self.data_dir, "perp", "1m_cache", f"{token}_1m.parquet",
        )

    def append_to_1m_parquet(self, token: str, bars: list[dict]) -> None:
        """Append 1m bars to the perp 1m cache parquet file.

        - Creates file and directories if they don't exist.
        - Deduplicates on timestamp (keeps last occurrence).
        - Writes atomically via temp file + rename.
        """
        if not bars:
            return

        path = self._1m_parquet_path(token)
        cache_dir = os.path.dirname(path)
        os.makedirs(cache_dir, exist_ok=True)

        # Build new DataFrame from bars
        new_df = pd.DataFrame(bars)
        new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
        new_df = new_df.set_index("timestamp")

        # Read existing data if present
        if os.path.exists(path):
            existing_df = pd.read_parquet(path)
            if not isinstance(existing_df.index, pd.DatetimeIndex):
                existing_df.index = pd.to_datetime(existing_df.index, unit="ms")
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

        # Ensure index is DatetimeIndex so timestamp lookups match
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, unit="ms")

        # Detect settlement interval from timestamp spacing (8h Binance,
        # 4h Hyperliquid, 1h some).  Divide raw rate by interval hours so
        # the stored funding_1h is the per-hour portion — matching what the
        # simulator applies at each hourly bar.
        timestamps_ms = sorted(int(r["timestamp"]) for r in funding_rates)
        if len(timestamps_ms) >= 2:
            diffs_hours = [(timestamps_ms[i+1] - timestamps_ms[i]) / 3_600_000
                           for i in range(len(timestamps_ms) - 1)]
            median_h = sorted(diffs_hours)[len(diffs_hours) // 2]
            if median_h < 2:
                interval_hours = 1
            elif median_h < 6:
                interval_hours = 4
            else:
                interval_hours = 8
        else:
            interval_hours = 8  # default to Binance 8h settlement

        # Build mapping: convert ms timestamps to tz-naive Timestamp to match
        # the parquet DatetimeIndex.  Floor to the nearest hour so that
        # settlement timestamps (e.g. 00:00:03.456) align with the hourly
        # OHLCV index (00:00:00).
        funding_map = {}
        for r in funding_rates:
            ts_ms = int(r["timestamp"])
            ts = pd.Timestamp(ts_ms, unit="ms").floor("h")
            funding_map[ts] = float(r["fundingRate"]) / interval_hours

        # Map parquet index to funding values
        new_funding = df.index.map(lambda idx: funding_map.get(idx))
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
