"""Tests for v4/data_loader.py — unified data loading with live/historical separation.

Tests verify:
  - load_token_data merges historical + live correctly
  - load_token_data handles missing files gracefully
  - Live buffer wins on duplicate timestamps (deduplication)
  - discover_tokens_from_data scans both directories
  - infer_data_end_date checks both directories
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

import pandas as pd
import pytest

from v4.data_loader import (
    load_token_data,
    load_token_data_cached,
    discover_tokens_from_data,
    infer_data_end_date,
    historical_path,
    live_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(start_ts_ms: int, n: int, interval_ms: int = 3_600_000,
             extra_cols: dict | None = None) -> pd.DataFrame:
    """Create a DataFrame matching the parquet cache format with DatetimeIndex."""
    timestamps = pd.to_datetime(
        [start_ts_ms + i * interval_ms for i in range(n)], unit="ms"
    )
    data = {
        "open": [100.0 + i for i in range(n)],
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 + i for i in range(n)],
        "close": [102.0 + i for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
    }
    if extra_cols:
        data.update(extra_cols)
    return pd.DataFrame(data, index=timestamps)


def _write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """Write DataFrame to parquet, creating directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ===================================================================
# Test: load_token_data
# ===================================================================

class TestLoadTokenData:
    """load_token_data merges historical + live data correctly."""

    def test_historical_only(self):
        """Returns historical data when no live buffer exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(1_000_000, 5)
            _write_parquet(df, historical_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert len(result) == 5

    def test_live_only(self):
        """Returns live data when no historical exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(1_000_000, 3)
            _write_parquet(df, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert len(result) == 3

    def test_neither_exists(self):
        """Returns None when no data exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is None

    def test_merge_historical_and_live(self):
        """Merges historical + live, deduplicates, sorted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Historical: bars 0-4
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            # Live: bars 3-6 (overlap at 3 and 4)
            live = _make_df(3 * 3_600_000, 4)
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            # 0,1,2 from hist + 3,4,5,6 from live (3,4 deduplicated, live wins)
            assert len(result) == 7
            assert result.index.is_monotonic_increasing
            assert result.index.duplicated().sum() == 0

    def test_live_wins_on_overlap(self):
        """When timestamps overlap, live data takes precedence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Historical: bar at t=0 with close=100
            hist = pd.DataFrame(
                {"open": [100.0], "high": [105.0], "low": [95.0],
                 "close": [100.0], "volume": [1000.0]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(hist, historical_path("BTC", "spot", tmpdir))

            # Live: bar at t=0 with close=999 (updated value)
            live = pd.DataFrame(
                {"open": [100.0], "high": [105.0], "low": [95.0],
                 "close": [999.0], "volume": [1000.0]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(live, live_path("BTC", "spot", tmpdir))

            result = load_token_data("BTC", "spot", data_dir=tmpdir)
            assert len(result) == 1
            assert result.iloc[0]["close"] == 999.0

    def test_overlap_preserves_funding_when_live_lacks_funding(self):
        """Historical funding data preserved when live has only OHLCV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = pd.DataFrame(
                {"open": [100.0], "high": [105.0], "low": [95.0],
                 "close": [100.0], "volume": [1000.0],
                 "funding_rate": [0.0003], "funding_1h": [3e-5]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            live = pd.DataFrame(
                {"open": [101.0], "high": [106.0], "low": [96.0],
                 "close": [999.0], "volume": [1100.0]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert len(result) == 1
            # Live OHLCV wins
            assert result.iloc[0]["close"] == 999.0
            # Historical funding preserved (not zeroed)
            assert result.iloc[0]["funding_rate"] == 0.0003
            assert result.iloc[0]["funding_1h"] == 3e-5

    def test_empty_historical_returns_live(self):
        """0-row historical file + live data returns live data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty historical (file exists but 0 rows)
            empty_hist = pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"],
            )
            empty_hist.index = pd.DatetimeIndex([], name="timestamp")
            _write_parquet(empty_hist, historical_path("BTC", "perp", tmpdir))

            live = _make_df(0, 3)
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert len(result) == 3

    def test_live_fills_gap_in_historical(self):
        """Live bar at timestamp missing from historical (gap fill)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Historical has hours 0 and 2 (gap at hour 1)
            hist = pd.DataFrame(
                {"open": [100.0, 102.0], "high": [105.0, 107.0],
                 "low": [95.0, 97.0], "close": [101.0, 103.0],
                 "volume": [1000.0, 1002.0]},
                index=pd.to_datetime([0, 7_200_000], unit="ms"),
            )
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            # Live fills gap at hour 1 and overlaps at hour 2
            live = pd.DataFrame(
                {"open": [110.0, 112.0], "high": [115.0, 117.0],
                 "low": [105.0, 107.0], "close": [111.0, 999.0],
                 "volume": [1100.0, 1200.0]},
                index=pd.to_datetime([3_600_000, 7_200_000], unit="ms"),
            )
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert len(result) == 3  # hours 0, 1, 2
            assert result.index.is_monotonic_increasing
            # Hour 1 filled from live
            assert result.iloc[1]["close"] == 111.0
            # Hour 2 updated by live
            assert result.iloc[2]["close"] == 999.0

    def test_live_has_extra_columns_in_overlap(self):
        """Live has funding_1h but historical doesn't; overlap preserves live funding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = pd.DataFrame(
                {"open": [100.0], "high": [105.0], "low": [95.0],
                 "close": [100.0], "volume": [1000.0]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            live = pd.DataFrame(
                {"open": [101.0], "high": [106.0], "low": [96.0],
                 "close": [999.0], "volume": [1100.0],
                 "funding_1h": [0.0001]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert len(result) == 1
            assert result.iloc[0]["close"] == 999.0
            assert result.iloc[0]["funding_1h"] == 0.0001

    def test_corrupt_historical_falls_back_to_live(self):
        """Corrupt historical parquet doesn't crash — returns live data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write garbage to historical path
            hist_pq = historical_path("BTC", "perp", tmpdir)
            hist_pq.parent.mkdir(parents=True, exist_ok=True)
            hist_pq.write_bytes(b"this is not a parquet file")

            # Write valid live data
            live = _make_df(0, 3)
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert len(result) == 3

    def test_corrupt_live_falls_back_to_historical(self):
        """Corrupt live parquet doesn't crash — returns historical data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            # Write garbage to live path
            live_pq = live_path("BTC", "perp", tmpdir)
            live_pq.parent.mkdir(parents=True, exist_ok=True)
            live_pq.write_bytes(b"corrupt data")

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert len(result) == 5

    def test_handles_integer_index_in_historical(self):
        """Historical files with integer index are converted to DatetimeIndex."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write historical with integer index (old format)
            df = pd.DataFrame(
                {"open": [100.0], "high": [105.0], "low": [95.0],
                 "close": [102.0], "volume": [1000.0]},
                index=pd.Index([1_000_000], name="timestamp"),
            )
            _write_parquet(df, historical_path("BTC", "perp", tmpdir))

            result = load_token_data("BTC", "perp", data_dir=tmpdir)
            assert result is not None
            assert isinstance(result.index, pd.DatetimeIndex)


# ===================================================================
# Test: discover_tokens_from_data
# ===================================================================

class TestDiscoverTokensFromData:
    """discover_tokens_from_data scans both historical and live directories."""

    def test_historical_only(self):
        """Finds tokens in 1h_cache when no live directory exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 3)
            _write_parquet(df, historical_path("BTC", "perp", tmpdir))
            _write_parquet(df, historical_path("ETH", "perp", tmpdir))

            tokens = discover_tokens_from_data("perp", data_dir=tmpdir)
            assert tokens == {"BTC", "ETH"}

    def test_live_only(self):
        """Finds tokens in live directory when no historical exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 3)
            _write_parquet(df, live_path("SOL", "perp", tmpdir))

            tokens = discover_tokens_from_data("perp", data_dir=tmpdir)
            assert tokens == {"SOL"}

    def test_union_of_both(self):
        """Returns union of historical + live tokens."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 3)
            _write_parquet(df, historical_path("BTC", "perp", tmpdir))
            _write_parquet(df, live_path("SOL", "perp", tmpdir))

            tokens = discover_tokens_from_data("perp", data_dir=tmpdir)
            assert tokens == {"BTC", "SOL"}

    def test_empty_directory(self):
        """Returns empty set when no data exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tokens = discover_tokens_from_data("perp", data_dir=tmpdir)
            assert tokens == set()


# ===================================================================
# Test: infer_data_end_date
# ===================================================================

class TestInferDataEndDate:
    """infer_data_end_date checks both historical and live data."""

    def test_historical_only(self):
        """Returns latest timestamp from historical data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # BTC perp with 100 bars
            df = _make_df(0, 100)
            _write_parquet(df, historical_path("BTC", "perp", tmpdir))

            result = infer_data_end_date("perp", data_dir=tmpdir)
            assert result == df.index[-1]

    def test_live_extends_beyond_historical(self):
        """Returns latest timestamp from live when it extends further."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 100)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            # Live extends 10 bars beyond historical
            live = _make_df(100 * 3_600_000, 10)
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result = infer_data_end_date("perp", data_dir=tmpdir)
            assert result == live.index[-1]
            assert result > hist.index[-1]

    def test_fallback_to_now(self):
        """Returns now() when no data exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = infer_data_end_date("perp", data_dir=tmpdir)
            # Should be close to current time
            assert abs((result - pd.Timestamp.now()).total_seconds()) < 60


# ===================================================================
# Test: path helpers
# ===================================================================

class TestPaths:
    """Verify path conventions."""

    def test_historical_path(self):
        p = historical_path("BTC", "perp", "/data")
        assert str(p) == "/data/perp/1h_cache/BTC_1h.parquet"

    def test_live_path(self):
        p = live_path("BTC", "perp", "/data")
        assert str(p) == "/data/perp/live/BTC.parquet"


# ===================================================================
# Test: load_token_data_cached
# ===================================================================

class TestLoadTokenDataCached:
    """load_token_data_cached caches historical reads, always reads live fresh."""

    def test_cache_miss_populates_cache(self):
        """First call reads from disk and populates the cache dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            cache = {}
            result = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert result is not None
            assert len(result) == 5
            assert ("BTC", "perp") in cache

    def test_cache_hit_skips_disk_read(self):
        """Second call uses cached historical — no disk read."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            cache = {}
            # First call: populates cache
            load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)

            # Delete the file — cache hit should still work
            historical_path("BTC", "perp", tmpdir).unlink()

            result = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert result is not None
            assert len(result) == 5

    def test_cache_hit_returns_same_object(self):
        """Cache retrieval returns the cached DataFrame directly (zero-copy).

        Callers are expected to slice (df[mask]) which creates new DataFrames.
        The cache is cleared after context loading in portfolio_signals.py,
        so mutation-after-retrieval is not a concern in production.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            cache = {}
            result1 = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            result2 = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            # Both retrievals return the same cached object (no defensive copy)
            assert len(result1) == len(result2) == 5

    def test_cache_insertion_is_independent_from_disk_read(self):
        """Cache stores a copy on first read, independent from returned df."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            cache = {}
            result1 = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            original_len = len(cache[("BTC", "perp")])

            # Delete the parquet file — cache should still have data
            historical_path("BTC", "perp", tmpdir).unlink()
            result2 = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert result2 is not None
            assert len(result2) == original_len

    def test_live_always_read_fresh(self):
        """Live buffer is re-read from disk on every call (not cached)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            live1 = _make_df(5 * 3_600_000, 2)
            _write_parquet(live1, live_path("BTC", "perp", tmpdir))

            cache = {}
            result1 = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert len(result1) == 7  # 5 hist + 2 live

            # Update live buffer with more bars
            live2 = _make_df(5 * 3_600_000, 4)
            _write_parquet(live2, live_path("BTC", "perp", tmpdir))

            result2 = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert len(result2) == 9  # 5 hist + 4 live (new bars captured)

    def test_cache_clear_forces_reread(self):
        """After cache.clear(), next call re-reads from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            cache = {}
            load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert ("BTC", "perp") in cache

            cache.clear()
            assert ("BTC", "perp") not in cache

            # Write updated historical
            hist2 = _make_df(0, 10)
            _write_parquet(hist2, historical_path("BTC", "perp", tmpdir))

            result = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            assert len(result) == 10  # Reads updated file

    def test_none_cache_behaves_like_load_token_data(self):
        """hist_cache=None falls back to identical behavior as load_token_data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hist = _make_df(0, 5)
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))
            live = _make_df(3 * 3_600_000, 4)
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            result_cached = load_token_data_cached("BTC", "perp", hist_cache=None, data_dir=tmpdir)
            result_original = load_token_data("BTC", "perp", data_dir=tmpdir)

            pd.testing.assert_frame_equal(result_cached, result_original)

    def test_merge_correctness_matches_load_token_data(self):
        """Cached path produces identical merge result to uncached path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Historical with funding
            hist = pd.DataFrame(
                {"open": [100.0], "high": [105.0], "low": [95.0],
                 "close": [100.0], "volume": [1000.0],
                 "funding_rate": [0.0003], "funding_1h": [3e-5]},
                index=pd.to_datetime([0], unit="ms"),
            )
            _write_parquet(hist, historical_path("BTC", "perp", tmpdir))

            # Live overlaps + extends, no funding columns
            live = pd.DataFrame(
                {"open": [101.0, 110.0], "high": [106.0, 115.0],
                 "low": [96.0, 105.0], "close": [999.0, 111.0],
                 "volume": [1100.0, 1200.0]},
                index=pd.to_datetime([0, 3_600_000], unit="ms"),
            )
            _write_parquet(live, live_path("BTC", "perp", tmpdir))

            cache = {}
            result_cached = load_token_data_cached("BTC", "perp", hist_cache=cache, data_dir=tmpdir)
            result_original = load_token_data("BTC", "perp", data_dir=tmpdir)

            pd.testing.assert_frame_equal(result_cached, result_original)
