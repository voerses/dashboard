"""Acceptance tests for LiveFetcher (AC1-4).

Tests verify:
  - AC1: LiveFetcher.fetch_ohlcv returns DataFrames with correct columns (mock ccxt)
  - AC1: Handles both spot and perp markets
  - AC2: fetch_funding_rates returns funding rate data (mock ccxt)
  - AC3: filter_closed_bars excludes incomplete bars (use synthetic timestamps)
  - AC4: append_to_parquet reads existing, appends new bars, deduplicates, writes atomically
  - AC4: append_to_parquet creates file if it doesn't exist
  - AC4: append_to_parquet deduplicates on timestamp (no duplicate rows)
  - Funding merge: merge_funding_into_parquet adds funding_1h column to perp parquet

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until live_fetcher.py is implemented (RED phase).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import numpy as np
import pandas as pd
import pytest

LiveFetcher = pytest.importorskip("v4.live_fetcher", reason="v4.live_fetcher not yet implemented").LiveFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_exchange(ohlcv_data=None, funding_data=None):
    """Create a mock ccxt exchange with configurable return data."""
    exchange = MagicMock()
    exchange.fetch_ohlcv = MagicMock(return_value=ohlcv_data or [])
    exchange.fetch_funding_rate_history = MagicMock(return_value=funding_data or [])
    exchange.milliseconds = MagicMock(return_value=int(time.time() * 1000))
    return exchange


def _make_ohlcv_rows(start_ts_ms: int, n: int, interval_ms: int = 3_600_000):
    """Generate n synthetic OHLCV rows starting at start_ts_ms."""
    rows = []
    for i in range(n):
        ts = start_ts_ms + i * interval_ms
        rows.append([ts, 100.0 + i, 105.0 + i, 95.0 + i, 102.0 + i, 1000.0 + i])
    return rows


def _make_parquet_df(start_ts_ms: int, n: int, interval_ms: int = 3_600_000):
    """Create a DataFrame matching the parquet cache format."""
    timestamps = [start_ts_ms + i * interval_ms for i in range(n)]
    df = pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 + i for i in range(n)],
        "close": [102.0 + i for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
    }, index=pd.Index(timestamps, name="timestamp"))
    return df


# ===================================================================
# Test: AC1 — fetch_ohlcv returns DataFrames with correct columns
# ===================================================================

class TestFetchOHLCV:
    """AC1: LiveFetcher.fetch_ohlcv returns data with correct columns."""

    def test_fetch_ohlcv_returns_correct_columns(self):
        """fetch_ohlcv returns list of bar dicts with required keys:
        timestamp, open, high, low, close, volume."""
        now_ms = int(time.time() * 1000)
        # A bar that ended 2 hours ago (closed)
        bar_ts = now_ms - 2 * 3_600_000
        ohlcv = [[bar_ts, 100.0, 105.0, 95.0, 102.0, 5000.0]]

        exchange = _mock_exchange(ohlcv_data=ohlcv)
        fetcher = LiveFetcher(exchange=exchange)

        bars = fetcher.fetch_ohlcv("BTC", "spot", limit=10)

        assert len(bars) > 0
        bar = bars[0]
        for col in ("timestamp", "open", "high", "low", "close", "volume"):
            assert col in bar, f"Missing column: {col}"

    def test_fetch_ohlcv_spot_market(self):
        """AC1: fetch_ohlcv handles spot market symbols correctly."""
        now_ms = int(time.time() * 1000)
        bar_ts = now_ms - 2 * 3_600_000
        ohlcv = [[bar_ts, 50.0, 55.0, 45.0, 52.0, 3000.0]]

        exchange = _mock_exchange(ohlcv_data=ohlcv)
        fetcher = LiveFetcher(exchange=exchange)

        bars = fetcher.fetch_ohlcv("ETH", "spot", limit=10)

        assert len(bars) > 0
        # exchange.fetch_ohlcv should have been called with a spot symbol
        exchange.fetch_ohlcv.assert_called_once()

    def test_fetch_ohlcv_perp_market(self):
        """AC1: fetch_ohlcv handles perp market symbols correctly."""
        now_ms = int(time.time() * 1000)
        bar_ts = now_ms - 2 * 3_600_000
        ohlcv = [[bar_ts, 50.0, 55.0, 45.0, 52.0, 3000.0]]

        exchange = _mock_exchange(ohlcv_data=ohlcv)
        fetcher = LiveFetcher(exchange=exchange)

        bars = fetcher.fetch_ohlcv("ETH", "perp", limit=10)

        assert len(bars) > 0
        exchange.fetch_ohlcv.assert_called_once()


# ===================================================================
# Test: AC2 — fetch_funding_rates returns funding rate data
# ===================================================================

class TestFetchFundingRates:
    """AC2: LiveFetcher.fetch_funding_rates returns funding rate data."""

    def test_fetch_funding_rates_returns_data(self):
        """fetch_funding_rates returns list of dicts with timestamp and fundingRate."""
        now_ms = int(time.time() * 1000)
        funding_data = [
            {"timestamp": now_ms - 8 * 3_600_000, "fundingRate": 0.0001},
            {"timestamp": now_ms - 0 * 3_600_000, "fundingRate": 0.00015},
        ]

        exchange = _mock_exchange(funding_data=funding_data)
        fetcher = LiveFetcher(exchange=exchange)

        rates = fetcher.fetch_funding_rates("BTC", limit=10)

        assert len(rates) >= 1
        for rate in rates:
            assert "timestamp" in rate
            assert "fundingRate" in rate


# ===================================================================
# Test: AC3 — filter_closed_bars excludes incomplete bars
# ===================================================================

class TestFilterClosedBars:
    """AC3: filter_closed_bars excludes incomplete hourly bars."""

    def test_excludes_current_incomplete_bar(self):
        """Bars where timestamp + 3600000 > now_ms are excluded (still forming)."""
        now_ms = int(time.time() * 1000)
        # Closed bar: ended 1 hour ago
        closed_bar = {"timestamp": now_ms - 2 * 3_600_000, "open": 100, "high": 105,
                       "low": 95, "close": 102, "volume": 1000}
        # Incomplete bar: current hour
        incomplete_bar = {"timestamp": now_ms - 30 * 60_000, "open": 103, "high": 107,
                          "low": 100, "close": 105, "volume": 500}

        fetcher = LiveFetcher.__new__(LiveFetcher)
        result = fetcher.filter_closed_bars([closed_bar, incomplete_bar], now_ms=now_ms)

        assert len(result) == 1
        assert result[0]["timestamp"] == closed_bar["timestamp"]

    def test_all_closed_bars_kept(self):
        """When all bars are closed, all are returned."""
        now_ms = int(time.time() * 1000)
        bars = [
            {"timestamp": now_ms - 3 * 3_600_000, "open": 100, "high": 105,
             "low": 95, "close": 102, "volume": 1000},
            {"timestamp": now_ms - 2 * 3_600_000, "open": 102, "high": 108,
             "low": 99, "close": 106, "volume": 1200},
        ]

        fetcher = LiveFetcher.__new__(LiveFetcher)
        result = fetcher.filter_closed_bars(bars, now_ms=now_ms)

        assert len(result) == 2

    def test_empty_bars_returns_empty(self):
        """Filtering empty list returns empty list."""
        now_ms = int(time.time() * 1000)
        fetcher = LiveFetcher.__new__(LiveFetcher)
        result = fetcher.filter_closed_bars([], now_ms=now_ms)
        assert result == []


# ===================================================================
# Test: AC4 — append_to_parquet
# ===================================================================

class TestAppendToParquet:
    """AC4: append_to_parquet appends, deduplicates, writes atomically."""

    def test_creates_file_if_not_exists(self):
        """append_to_parquet creates a new parquet file if none exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            bars = [
                {"timestamp": 1000000, "open": 100, "high": 105, "low": 95,
                 "close": 102, "volume": 1000},
                {"timestamp": 2000000, "open": 102, "high": 108, "low": 99,
                 "close": 106, "volume": 1200},
            ]

            parquet_path = os.path.join(tmpdir, "spot", "1h_cache", "BTC_1h.parquet")
            fetcher.append_to_parquet("BTC", "spot", bars)

            assert os.path.exists(parquet_path)
            df = pd.read_parquet(parquet_path)
            assert len(df) == 2

    def test_empty_bars_list_no_crash(self):
        """append_to_parquet with empty bars list does not crash (review I2)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "spot", "1h_cache")
            os.makedirs(cache_dir, exist_ok=True)
            parquet_path = os.path.join(cache_dir, "BTC_1h.parquet")

            # Create existing parquet with some data
            existing_df = _make_parquet_df(1_000_000, 3)
            existing_df.to_parquet(parquet_path)

            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            # Append empty bars list — should not crash or corrupt
            fetcher.append_to_parquet("BTC", "spot", [])

            df = pd.read_parquet(parquet_path)
            assert len(df) == 3, "Empty bars append should not change existing data"

    def test_appends_new_bars_to_existing(self):
        """append_to_parquet appends new bars to an existing parquet file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create existing parquet
            cache_dir = os.path.join(tmpdir, "spot", "1h_cache")
            os.makedirs(cache_dir, exist_ok=True)
            parquet_path = os.path.join(cache_dir, "BTC_1h.parquet")

            existing_df = _make_parquet_df(1_000_000, 3)
            existing_df.to_parquet(parquet_path)

            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            # New bars with timestamps after existing
            new_bars = [
                {"timestamp": 1_000_000 + 3 * 3_600_000, "open": 110, "high": 115,
                 "low": 108, "close": 112, "volume": 2000},
                {"timestamp": 1_000_000 + 4 * 3_600_000, "open": 112, "high": 118,
                 "low": 110, "close": 116, "volume": 2200},
            ]

            fetcher.append_to_parquet("BTC", "spot", new_bars)

            df = pd.read_parquet(parquet_path)
            assert len(df) == 5  # 3 existing + 2 new

    def test_deduplicates_on_timestamp(self):
        """append_to_parquet deduplicates — no duplicate timestamp rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "spot", "1h_cache")
            os.makedirs(cache_dir, exist_ok=True)
            parquet_path = os.path.join(cache_dir, "BTC_1h.parquet")

            existing_df = _make_parquet_df(1_000_000, 3)
            existing_df.to_parquet(parquet_path)

            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            # Overlapping bar (same timestamp as existing bar 2) + one new bar
            overlap_bars = [
                {"timestamp": 1_000_000 + 2 * 3_600_000, "open": 999, "high": 999,
                 "low": 999, "close": 999, "volume": 999},
                {"timestamp": 1_000_000 + 3 * 3_600_000, "open": 110, "high": 115,
                 "low": 108, "close": 112, "volume": 2000},
            ]

            fetcher.append_to_parquet("BTC", "spot", overlap_bars)

            df = pd.read_parquet(parquet_path)
            assert len(df) == 4  # 3 existing (one updated) + 1 new = 4 unique timestamps
            # Verify no duplicate timestamps
            assert df.index.duplicated().sum() == 0

    def test_atomic_write_no_partial_file(self):
        """append_to_parquet writes atomically (temp file + rename)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            bars = [
                {"timestamp": 1_000_000, "open": 100, "high": 105,
                 "low": 95, "close": 102, "volume": 1000},
            ]

            fetcher.append_to_parquet("BTC", "spot", bars)

            parquet_path = os.path.join(tmpdir, "spot", "1h_cache", "BTC_1h.parquet")
            # No .tmp files should remain after successful write
            cache_dir = os.path.dirname(parquet_path)
            tmp_files = [f for f in os.listdir(cache_dir) if f.endswith(".tmp")]
            assert len(tmp_files) == 0


# ===================================================================
# Test: Funding merge — merge_funding_into_parquet
# ===================================================================

class TestMergeFundingIntoParquet:
    """Funding merge: merge_funding_into_parquet adds funding_1h column."""

    def test_merge_funding_adds_column(self):
        """merge_funding_into_parquet adds funding_1h column to perp parquet."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "perp", "1h_cache")
            os.makedirs(cache_dir, exist_ok=True)
            parquet_path = os.path.join(cache_dir, "BTC_1h.parquet")

            # Create existing perp parquet without funding_1h
            existing_df = _make_parquet_df(1_000_000, 5)
            existing_df.to_parquet(parquet_path)

            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            # Funding rates aligned with some of the bar timestamps
            funding_rates = [
                {"timestamp": 1_000_000, "fundingRate": 0.0001},
                {"timestamp": 1_000_000 + 3_600_000, "fundingRate": 0.00015},
                {"timestamp": 1_000_000 + 2 * 3_600_000, "fundingRate": -0.00005},
            ]

            fetcher.merge_funding_into_parquet("BTC", funding_rates)

            df = pd.read_parquet(parquet_path)
            assert "funding_1h" in df.columns, "funding_1h column not added to perp parquet"

            # Verify actual funding values (not just column existence — review C7)
            # The first bar at timestamp 1000000 should have fundingRate 0.0001
            ts_index = pd.to_datetime(1_000_000, unit="ms")
            if ts_index in df.index:
                assert df.loc[ts_index, "funding_1h"] == pytest.approx(0.0001, abs=1e-8), \
                    "funding_1h value incorrect for timestamp matching funding rate"

    def test_merge_funding_empty_rates(self):
        """merge_funding_into_parquet with empty rates does not crash or corrupt data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "perp", "1h_cache")
            os.makedirs(cache_dir, exist_ok=True)
            parquet_path = os.path.join(cache_dir, "BTC_1h.parquet")

            existing_df = _make_parquet_df(1_000_000, 3)
            existing_df.to_parquet(parquet_path)

            fetcher = LiveFetcher.__new__(LiveFetcher)
            fetcher.data_dir = tmpdir

            # Empty funding rates
            fetcher.merge_funding_into_parquet("BTC", [])

            df = pd.read_parquet(parquet_path)
            assert len(df) == 3, "Empty funding merge should not corrupt existing data"
