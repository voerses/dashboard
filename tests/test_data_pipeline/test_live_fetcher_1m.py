"""Acceptance tests for LiveFetcher 1m support (AC7, AC8).

Tests verify:
  - AC8: filter_closed_bars_1m excludes bars where timestamp + 60_000 > now_ms
  - AC8: filter_closed_bars_1m includes bars where timestamp + 60_000 <= now_ms
  - AC8: filter_closed_bars_1m returns empty list for empty input
  - AC7: append_to_1m_parquet creates new parquet at data/perp/1m_cache/{TOKEN}_1m.parquet
  - AC7: append_to_1m_parquet appends to existing file and deduplicates
  - AC7: append_to_1m_parquet deduplicates on timestamp (keeps last)
  - AC7: append_to_1m_parquet writes atomically (file always valid)
  - AC7: append_to_1m_parquet with empty bars is a no-op

All tests use synthetic data -- no real exchange required.
These tests MUST FAIL until 1m support is implemented (RED phase).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

from v4.live_fetcher import LiveFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINUTE_MS = 60_000


def _mock_exchange():
    """Create a mock ccxt exchange."""
    exchange = MagicMock()
    exchange.fetch_ohlcv = MagicMock(return_value=[])
    exchange.milliseconds = MagicMock(return_value=int(time.time() * 1000))
    return exchange


def _make_1m_bars(start_ts_ms: int, n: int) -> list[dict]:
    """Generate n synthetic 1m OHLCV bar dicts starting at start_ts_ms."""
    bars = []
    for i in range(n):
        ts = start_ts_ms + i * _MINUTE_MS
        bars.append({
            "timestamp": ts,
            "open": 100.0 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "close": 100.2 + i * 0.1,
            "volume": 500.0 + i,
        })
    return bars


# ===================================================================
# AC8: filter_closed_bars_1m
# ===================================================================

class TestFilterClosedBars1m:
    """AC8: filter_closed_bars_1m excludes bars where timestamp + 60_000 > now_ms."""

    def test_filter_closed_bars_1m_excludes_incomplete(self):
        """AC8: Bars where timestamp + 60_000 > now_ms are excluded."""
        exchange = _mock_exchange()
        fetcher = LiveFetcher(exchange=exchange, data_dir="/tmp/unused")

        # Bar at ts=1000000, closes at ts + 60_000 = 1060000
        # now_ms=1059999 means bar is still forming -> exclude
        bars = [{"timestamp": 1_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
        result = fetcher.filter_closed_bars(bars, now_ms=1_059_999, timeframe="1m")
        assert len(result) == 0, "Incomplete 1m bar should be excluded"

    def test_filter_closed_bars_1m_includes_closed(self):
        """AC8: Bars where timestamp + 60_000 <= now_ms are kept."""
        exchange = _mock_exchange()
        fetcher = LiveFetcher(exchange=exchange, data_dir="/tmp/unused")

        # Bar at ts=1000000, closes at ts + 60_000 = 1060000
        # now_ms=1060000 means bar is closed -> include
        bars = [{"timestamp": 1_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
        result = fetcher.filter_closed_bars(bars, now_ms=1_060_000, timeframe="1m")
        assert len(result) == 1, "Closed 1m bar should be included"
        assert result[0]["timestamp"] == 1_000_000

    def test_filter_closed_bars_1m_mixed(self):
        """AC8: With multiple bars, only closed ones are returned."""
        exchange = _mock_exchange()
        fetcher = LiveFetcher(exchange=exchange, data_dir="/tmp/unused")

        now_ms = 1_200_000
        bars = _make_1m_bars(start_ts_ms=1_100_000, n=5)
        # Bar timestamps: 1100000, 1160000, 1220000, 1280000, 1340000
        # Closed if ts + 60_000 <= 1200000 -> ts <= 1140000
        # Only bar at 1100000 is closed (1100000 + 60000 = 1160000 <= 1200000)
        # Bar at 1160000: 1160000 + 60000 = 1220000 > 1200000 -> excluded
        result = fetcher.filter_closed_bars(bars, now_ms=now_ms, timeframe="1m")
        assert len(result) == 1  # only bar at 1100000: 1100000 + 60000 = 1160000 <= 1200000
        result = fetcher.filter_closed_bars(bars, now_ms=1_220_000, timeframe="1m")
        # 1100000 + 60000 = 1160000 <= 1220000 -> included
        # 1160000 + 60000 = 1220000 <= 1220000 -> included
        # 1220000 + 60000 = 1280000 > 1220000 -> excluded
        assert len(result) == 2

    def test_filter_closed_bars_1m_empty_input(self):
        """AC8: Empty list returns empty list."""
        exchange = _mock_exchange()
        fetcher = LiveFetcher(exchange=exchange, data_dir="/tmp/unused")

        result = fetcher.filter_closed_bars([], now_ms=9_999_999, timeframe="1m")
        assert result == []


# ===================================================================
# AC7: append_to_1m_parquet
# ===================================================================

class TestAppendTo1mParquet:
    """AC7: append_to_1m_parquet writes 1m bars with dedup and atomic writes."""

    def test_append_to_1m_parquet_creates_new_file(self, tmp_path):
        """AC7: Creates file at data/perp/1m_cache/{TOKEN}_1m.parquet with correct columns."""
        exchange = _mock_exchange()
        data_dir = str(tmp_path / "data")
        fetcher = LiveFetcher(exchange=exchange, data_dir=data_dir)

        bars = _make_1m_bars(start_ts_ms=1_700_000_000_000, n=5)
        fetcher.append_to_1m_parquet("BTC", bars)

        expected_path = tmp_path / "data" / "perp" / "1m_cache" / "BTC_1m.parquet"
        assert expected_path.exists(), f"Parquet file should be created at {expected_path}"

        df = pd.read_parquet(expected_path)
        assert len(df) == 5
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in df.columns, f"Column {col} missing from parquet"

    def test_append_to_1m_parquet_appends_to_existing(self, tmp_path):
        """AC7: Reads existing data, appends new bars, writes combined."""
        exchange = _mock_exchange()
        data_dir = str(tmp_path / "data")
        fetcher = LiveFetcher(exchange=exchange, data_dir=data_dir)

        # Write initial batch
        bars1 = _make_1m_bars(start_ts_ms=1_700_000_000_000, n=3)
        fetcher.append_to_1m_parquet("ETH", bars1)

        # Append second batch (non-overlapping)
        bars2 = _make_1m_bars(start_ts_ms=1_700_000_000_000 + 3 * _MINUTE_MS, n=3)
        fetcher.append_to_1m_parquet("ETH", bars2)

        expected_path = tmp_path / "data" / "perp" / "1m_cache" / "ETH_1m.parquet"
        df = pd.read_parquet(expected_path)
        assert len(df) == 6, f"Expected 6 rows after append, got {len(df)}"

    def test_append_to_1m_parquet_deduplicates(self, tmp_path):
        """AC7: Overlapping timestamps keep last occurrence (no duplicates)."""
        exchange = _mock_exchange()
        data_dir = str(tmp_path / "data")
        fetcher = LiveFetcher(exchange=exchange, data_dir=data_dir)

        # Write initial 5 bars
        bars1 = _make_1m_bars(start_ts_ms=1_700_000_000_000, n=5)
        fetcher.append_to_1m_parquet("SOL", bars1)

        # Write overlapping bars (same timestamps, different close prices)
        bars2 = _make_1m_bars(start_ts_ms=1_700_000_000_000, n=5)
        for b in bars2:
            b["close"] = 999.0  # distinct value to verify dedup keeps last
        fetcher.append_to_1m_parquet("SOL", bars2)

        expected_path = tmp_path / "data" / "perp" / "1m_cache" / "SOL_1m.parquet"
        df = pd.read_parquet(expected_path)
        assert len(df) == 5, f"Expected 5 rows after dedup, got {len(df)}"
        # All close values should be 999.0 (last write wins)
        assert (df["close"] == 999.0).all(), "Dedup should keep last occurrence"

    def test_append_to_1m_parquet_atomic_write(self, tmp_path):
        """AC7: File exists and is valid after write (atomic via temp+rename)."""
        exchange = _mock_exchange()
        data_dir = str(tmp_path / "data")
        fetcher = LiveFetcher(exchange=exchange, data_dir=data_dir)

        bars = _make_1m_bars(start_ts_ms=1_700_000_000_000, n=10)
        fetcher.append_to_1m_parquet("DOGE", bars)

        expected_path = tmp_path / "data" / "perp" / "1m_cache" / "DOGE_1m.parquet"
        assert expected_path.exists(), "File must exist after write"
        # Verify file is valid parquet (not corrupted)
        df = pd.read_parquet(expected_path)
        assert len(df) == 10

    def test_append_to_1m_parquet_empty_bars_noop(self, tmp_path):
        """AC7: Empty bars list does nothing (no file created)."""
        exchange = _mock_exchange()
        data_dir = str(tmp_path / "data")
        fetcher = LiveFetcher(exchange=exchange, data_dir=data_dir)

        fetcher.append_to_1m_parquet("AVAX", [])

        expected_path = tmp_path / "data" / "perp" / "1m_cache" / "AVAX_1m.parquet"
        assert not expected_path.exists(), "No file should be created for empty bars"
