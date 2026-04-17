"""V5 Live Data Fetcher — fetches OHLCV + funding, appends to parquet cache.

Unlike v3's WAL-based approach, v4 writes directly to parquet files so that
precompute_strategy_signals() can read updated data without a merge step.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_HOUR_MS = 3_600_000
_MINUTE_MS = 60_000

# Spot tokens delisted from Binance — skip backfill to avoid retrying
# gaps that can never be filled. XMR delisted Feb 2024, LIT delisted Feb 2025.
DELISTED_SPOT = frozenset({'XMR', 'LIT'})


class LiveFetcher:
    """Fetches live OHLCV + funding data and appends to parquet cache."""

    write_to_history: bool = False

    def __init__(self, exchange=None, data_dir: str = "data", write_to_history: bool = False):
        self.exchange = exchange
        self.data_dir = data_dir
        self.write_to_history = write_to_history
        # Per-token locks for 1m parquet writes — prevents lost-update race
        # when writer thread and backfill thread write concurrently.
        self._1m_write_locks: dict[str, threading.Lock] = {}
        self._1m_write_locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    # Tokens that Binance lists with a 1000x prefix on futures
    _1000_PREFIX_TOKENS = {
        "SHIB", "PEPE", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
        "CHEEMS", "WHY", "X", "XEC",
    }

    def _resolve_symbol(self, token: str, market: str) -> str:
        """Resolve ccxt symbol for the given token and market type."""
        name = token
        if market == "perp" and token in self._1000_PREFIX_TOKENS:
            name = f"1000{token}"
        if market == "perp":
            return f"{name}/USDT:USDT"
        return f"{name}/USDT"

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

    def _find_last_timestamp_ms(
        self, token: str, market: str, timeframe: str = "1h",
    ) -> Optional[int]:
        """Find the ms timestamp of the last bar in the appropriate cache.

        For timeframe="1h": checks live buffer first, then 1h_cache.
        For timeframe="1m": checks 1m_cache only (no live buffer for 1m data).
        Returns None if no data exists for this token/market/timeframe.
        """
        if timeframe == "1m":
            path = self._1m_parquet_path(token)
            if os.path.exists(path):
                try:
                    idx = pd.read_parquet(path, columns=[]).index
                    if len(idx) > 0:
                        return self._ts_to_ms(idx.max())
                except Exception as e:
                    logger.warning("Failed to read 1m parquet %s: %s", path, e)
            return None

        # 1h: Check both live buffer and historical cache, return the max.
        best_ms: Optional[int] = None
        live_path = os.path.join(
            self.data_dir, market, "live", f"{token}.parquet",
        )
        hist_path = os.path.join(
            self.data_dir, market, "1h_cache", f"{token}_1h.parquet",
        )
        for path in (live_path, hist_path):
            if os.path.exists(path):
                try:
                    idx = pd.read_parquet(path, columns=[]).index
                    if len(idx) > 0:
                        ts_ms = self._ts_to_ms(idx.max())
                        if best_ms is None or ts_ms > best_ms:
                            best_ms = ts_ms
                except Exception as e:
                    logger.warning("Failed to read parquet %s: %s", path, e)

        return best_ms

    def backfill_gaps(
        self,
        tokens: set[str],
        gap_threshold_hours: int = 1,
        gap_threshold_minutes: int | None = None,
        chunk_limit: int = 1000,
        max_errors: int = 5,
        deadline_s: float | None = None,
        timeframe: str = "1h",
    ) -> dict[str, int]:
        """Backfill data gaps for each token/market pair.

        For timeframe="1h" (default): iterates spot+perp, uses gap_threshold_hours,
        writes via append_to_parquet, uses _HOUR_MS pagination.

        For timeframe="1m": iterates perp only, uses gap_threshold_minutes,
        writes via append_to_1m_parquet, uses _MINUTE_MS pagination,
        stops 2 minutes before now (temporal separation with live WS).

        Returns a dict of "TOKEN/market" -> bars_backfilled.
        Aborts early after max_errors consecutive fetch failures (network down).
        If deadline_s is set, stops fetching when wall-clock time exceeds it.
        """
        now_ms = int(time.time() * 1000)
        is_1m = timeframe == "1m"
        interval_ms = _MINUTE_MS if is_1m else _HOUR_MS
        markets = ("perp",) if is_1m else ("spot", "perp")

        # Temporal separation: for 1m, stop 2 minutes before now
        stop_ms = now_ms - 2 * _MINUTE_MS if is_1m else now_ms

        if is_1m:
            # Default to 5 minutes if not specified for 1m timeframe
            effective_minutes = gap_threshold_minutes if gap_threshold_minutes is not None else 5
            threshold_ms = effective_minutes * _MINUTE_MS
        else:
            threshold_ms = gap_threshold_hours * _HOUR_MS

        results: dict[str, int] = {}
        consecutive_errors = 0
        _deadline = time.monotonic() + deadline_s if deadline_s is not None else None

        for token in sorted(tokens):
            for market in markets:
                if market == "spot" and token in DELISTED_SPOT:
                    continue
                if _deadline is not None and time.monotonic() >= _deadline:
                    logger.info("Backfill deadline reached — stopping with %d tokens done", len(results))
                    return results
                if consecutive_errors >= max_errors:
                    logger.warning(
                        "Backfill aborted: %d consecutive errors (network down?)",
                        consecutive_errors,
                    )
                    return results

                last_ms = self._find_last_timestamp_ms(token, market, timeframe=timeframe)
                if last_ms is None:
                    continue

                gap_ms = now_ms - last_ms
                if gap_ms <= threshold_ms:
                    continue

                key = f"{token}/{market}"
                if is_1m:
                    gap_minutes = gap_ms // _MINUTE_MS
                    logger.info(
                        "Backfilling 1m %s: %d missing minutes (%s -> now)",
                        key, gap_minutes,
                        pd.Timestamp(last_ms, unit="ms").strftime("%b %d %H:%M"),
                    )
                else:
                    gap_hours = gap_ms // _HOUR_MS
                    logger.info(
                        "Backfilling %s: %d missing hours (%s -> now)",
                        key, gap_hours,
                        pd.Timestamp(last_ms, unit="ms").strftime("%b %d %H:%M"),
                    )

                # Paginate from last known bar + one interval
                since_ms = last_ms + interval_ms
                total_bars = 0

                while since_ms < stop_ms:
                    if _deadline is not None and time.monotonic() >= _deadline:
                        logger.info("Backfill deadline reached mid-token %s/%s", token, market)
                        break
                    try:
                        bars = self.fetch_ohlcv_since(
                            token, market, timeframe=timeframe,
                            since_ms=since_ms, limit=chunk_limit,
                        )
                        consecutive_errors = 0  # Reset on success
                    except Exception as e:
                        consecutive_errors += 1
                        logger.warning("Backfill fetch %s failed: %s", key, e)
                        break

                    if not bars:
                        break

                    closed = self.filter_closed_bars(
                        bars, now_ms=stop_ms, timeframe=timeframe,
                    )
                    if closed:
                        if is_1m:
                            self.append_to_1m_parquet(token, closed)
                        else:
                            self.append_to_parquet(token, market, closed)
                        total_bars += len(closed)

                    # Advance: next page starts after last fetched bar
                    next_since = bars[-1]["timestamp"] + interval_ms
                    if next_since <= since_ms:
                        # Guard against infinite loop (exchange returning same data)
                        break
                    since_ms = next_since

                    # Rate-limit courtesy: small sleep between pagination pages
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
        self,
        bars: list[dict],
        now_ms: Optional[int] = None,
        timeframe: str = "1h",
    ) -> list[dict]:
        """Exclude incomplete (still-forming) bars.

        For timeframe="1h": bar is closed if timestamp + 3_600_000 <= now_ms.
        For timeframe="1m": bar is closed if timestamp + 60_000 <= now_ms.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        interval_ms = _MINUTE_MS if timeframe == "1m" else _HOUR_MS
        return [b for b in bars if b["timestamp"] + interval_ms <= now_ms]

    # ------------------------------------------------------------------
    # AC4: Parquet append
    # ------------------------------------------------------------------

    def _parquet_path(self, token: str, market: str) -> str:
        """Return the parquet path for a token/market.

        When write_to_history=True, writes directly to the historical
        cache (data/{market}/1h_cache/{TOKEN}_1h.parquet).
        Otherwise writes to the live buffer (data/{market}/live/{TOKEN}.parquet).
        """
        if self.write_to_history:
            return os.path.join(
                self.data_dir, market, "1h_cache", f"{token}_1h.parquet",
            )
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

        # Drop rows with NaN prices (skeleton bars from failed fetches)
        price_cols = ["open", "high", "low", "close"]
        nan_mask = new_df[price_cols].isna().any(axis=1)
        if nan_mask.any():
            logger.warning("Dropping %d NaN bars for %s/%s", nan_mask.sum(), token, market)
            new_df = new_df[~nan_mask]
        if len(new_df) == 0:
            return

        # Read existing data if present; recover from corrupted files
        if os.path.exists(path):
            try:
                existing_df = pd.read_parquet(path)
                # Ensure index types match: convert integer index to DatetimeIndex
                if not isinstance(existing_df.index, pd.DatetimeIndex):
                    existing_df.index = pd.to_datetime(existing_df.index, unit="ms")
                combined = pd.concat([existing_df, new_df])
            except Exception as e:
                logger.warning(
                    "Corrupted parquet for %s/%s, starting fresh: %s", token, market, e,
                )
                combined = new_df
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

    def _get_1m_lock(self, token: str) -> threading.Lock:
        """Get or create a per-token lock for 1m parquet writes."""
        with self._1m_write_locks_guard:
            if token not in self._1m_write_locks:
                self._1m_write_locks[token] = threading.Lock()
            return self._1m_write_locks[token]

    def append_to_1m_parquet(self, token: str, bars: list[dict]) -> None:
        """Append 1m bars to the perp 1m cache parquet file.

        - Creates file and directories if they don't exist.
        - Deduplicates on timestamp (keeps last occurrence).
        - Writes atomically via temp file + rename.
        - Thread-safe: per-token lock prevents lost-update race.
        """
        if not bars:
            return

        lock = self._get_1m_lock(token)
        with lock:
            self._append_to_1m_parquet_locked(token, bars)

    def _append_to_1m_parquet_locked(self, token: str, bars: list[dict]) -> None:
        """Inner implementation of append_to_1m_parquet (must hold per-token lock)."""
        path = self._1m_parquet_path(token)
        cache_dir = os.path.dirname(path)
        os.makedirs(cache_dir, exist_ok=True)

        # Build new DataFrame from bars
        new_df = pd.DataFrame(bars)
        new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
        new_df = new_df.set_index("timestamp")

        # Drop rows with NaN prices (skeleton bars from failed fetches)
        price_cols = ["open", "high", "low", "close"]
        nan_mask = new_df[price_cols].isna().any(axis=1)
        if nan_mask.any():
            logger.warning("Dropping %d NaN 1m bars for %s", nan_mask.sum(), token)
            new_df = new_df[~nan_mask]
        if len(new_df) == 0:
            return

        # Read existing data if present; recover from corrupted files
        if os.path.exists(path):
            try:
                existing_df = pd.read_parquet(path)
                if not isinstance(existing_df.index, pd.DatetimeIndex):
                    existing_df.index = pd.to_datetime(existing_df.index, unit="ms")
                combined = pd.concat([existing_df, new_df])
            except Exception as e:
                logger.warning(
                    "Corrupted 1m parquet for %s, starting fresh: %s", token, e,
                )
                combined = new_df
        else:
            combined = new_df

        # Deduplicate: keep last occurrence (new data wins)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()

        # Scrub any pre-existing NaN rows from combined (1m cache is never promoted)
        nan_combined = combined[["open", "high", "low", "close"]].isna().any(axis=1)
        if nan_combined.any():
            logger.warning("Scrubbing %d pre-existing NaN bars from 1m %s", nan_combined.sum(), token)
            combined = combined[~nan_combined]

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

        Always writes to the 1h_cache (historical) parquet regardless of
        write_to_history setting, because funding is a periodic settlement
        event that belongs in the main historical data — not the tiny
        live buffer which only has a few recent OHLCV rows.

        Funding rates are mapped by timestamp to the parquet index.
        Exchange timestamps are rounded to the nearest hour to align with
        the hourly parquet index.  Timestamps without a matching funding
        rate keep their existing value (or 0.0 if the column is new).
        """
        if not funding_rates:
            return

        # Always target 1h_cache for funding (not the live buffer)
        path = os.path.join(
            self.data_dir, "perp", "1h_cache", f"{token}_1h.parquet",
        )
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
        # Forward-fill each settlement rate across all hours in the window
        # (e.g., 8h settlement → same rate/8 for each of the 8 hourly rows).
        # This matches the format produced by build_parquet_cache.py.
        funding_map = {}
        for r in funding_rates:
            ts_ms = int(r["timestamp"])
            ts = pd.Timestamp(ts_ms, unit="ms").floor("h")
            rate_per_hour = float(r["fundingRate"]) / interval_hours
            for h_offset in range(interval_hours):
                funding_map[ts + pd.Timedelta(hours=h_offset)] = rate_per_hour

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
