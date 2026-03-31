"""Acceptance tests for Task 3: Generalized backfill_gaps and consolidated filter.

Tests verify:
  - AC8: backfill_gaps(timeframe="1m") uses 60_000ms intervals, writes to 1m_cache,
         only iterates perp market, stops 2 minutes before now
  - AC8 gap_threshold_minutes: tokens with gaps < threshold are skipped
  - AC9: _find_last_timestamp_ms(token, market, timeframe="1m") checks 1m_cache
  - AC17: filter_closed_bars accepts timeframe param; filter_closed_bars_1m removed

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

from v4.live_fetcher import LiveFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_1m_bars(start_ts_ms: int, n: int) -> list[dict]:
    """Generate n synthetic 1-minute OHLCV bar dicts."""
    bars = []
    for i in range(n):
        ts = start_ts_ms + i * 60_000
        bars.append({
            "timestamp": ts,
            "open": 100.0 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "close": 100.2 + i * 0.1,
            "volume": 50.0 + i,
        })
    return bars


def _create_1m_parquet(tmp_path, token: str, bars: list[dict]) -> str:
    """Write 1m bars to a parquet file in the expected 1m_cache directory structure."""
    cache_dir = tmp_path / "perp" / "1m_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{token}_1m.parquet"

    df = pd.DataFrame(bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.set_index("timestamp")
    df.to_parquet(path)
    return str(path)


def _make_ccxt_ohlcv(bars: list[dict]) -> list[list]:
    """Convert bar dicts to ccxt-style OHLCV rows [[ts, o, h, l, c, v], ...]."""
    return [[b["timestamp"], b["open"], b["high"], b["low"], b["close"], b["volume"]]
            for b in bars]


# ===================================================================
# AC8: backfill_gaps(timeframe="1m") behavior
# ===================================================================

class TestBackfillGaps1m:
    """AC8: backfill_gaps generalized for 1m timeframe."""

    def test_backfill_1m_uses_append_to_1m_parquet(self, tmp_path):
        """AC8: backfill_gaps(timeframe='1m') writes via append_to_1m_parquet, not append_to_parquet."""
        now_ms = int(time.time() * 1000)
        # Place last known bar 10 minutes ago so there is a gap
        last_bar_ts = now_ms - 10 * 60_000

        bars = _make_1m_bars(last_bar_ts, 3)
        _create_1m_parquet(tmp_path, "BTC", _make_1m_bars(last_bar_ts - 5 * 60_000, 1))

        exchange = MagicMock()
        exchange.fetch_ohlcv.return_value = _make_ccxt_ohlcv(bars)

        fetcher = LiveFetcher(exchange=exchange, data_dir=str(tmp_path))
        fetcher.append_to_1m_parquet = MagicMock()
        fetcher.append_to_parquet = MagicMock()

        fetcher.backfill_gaps({"BTC"}, timeframe="1m", gap_threshold_minutes=1)

        assert fetcher.append_to_1m_parquet.called, \
            "backfill_gaps(timeframe='1m') should use append_to_1m_parquet"
        assert not fetcher.append_to_parquet.called, \
            "backfill_gaps(timeframe='1m') should NOT use append_to_parquet"

    def test_backfill_1m_only_iterates_perp(self, tmp_path):
        """AC8: backfill_gaps(timeframe='1m') only iterates perp market, not spot."""
        now_ms = int(time.time() * 1000)
        last_bar_ts = now_ms - 10 * 60_000

        # Create 1m data for BTC in perp/1m_cache
        _create_1m_parquet(tmp_path, "BTC", _make_1m_bars(last_bar_ts - 5 * 60_000, 1))

        # Also create spot data — should NOT be touched for 1m backfill
        spot_dir = tmp_path / "spot" / "1h_cache"
        spot_dir.mkdir(parents=True, exist_ok=True)

        exchange = MagicMock()
        exchange.fetch_ohlcv.return_value = _make_ccxt_ohlcv(_make_1m_bars(last_bar_ts, 3))

        fetcher = LiveFetcher(exchange=exchange, data_dir=str(tmp_path))
        fetcher.append_to_1m_parquet = MagicMock()

        fetcher.backfill_gaps({"BTC"}, timeframe="1m", gap_threshold_minutes=1)

        # Verify fetch_ohlcv was only called for perp market (ccxt perp = TOKEN/USDT:USDT)
        for call_obj in exchange.fetch_ohlcv.call_args_list:
            symbol = call_obj[0][0]
            assert "USDT:USDT" in symbol, \
                f"1m backfill should only fetch perp symbols (TOKEN/USDT:USDT), got: {symbol}"

    def test_backfill_1m_stops_2_minutes_before_now(self, tmp_path):
        """AC8: backfill_gaps(timeframe='1m') stops 2 minutes before now (temporal separation)."""
        now_ms = int(time.time() * 1000)
        two_min_ago = now_ms - 2 * 60_000
        last_bar_ts = now_ms - 10 * 60_000

        _create_1m_parquet(tmp_path, "BTC", _make_1m_bars(last_bar_ts - 5 * 60_000, 1))

        # Return 10 bars spanning from 10 minutes ago to now — some should be filtered out
        bars_returned = _make_1m_bars(last_bar_ts, 10)
        exchange = MagicMock()
        exchange.fetch_ohlcv.return_value = _make_ccxt_ohlcv(bars_returned)

        fetcher = LiveFetcher(exchange=exchange, data_dir=str(tmp_path))
        fetcher.append_to_1m_parquet = MagicMock()

        fetcher.backfill_gaps({"BTC"}, timeframe="1m", gap_threshold_minutes=1)

        # append_to_1m_parquet MUST have been called (bars exist to backfill)
        assert fetcher.append_to_1m_parquet.called, \
            "Should have written some bars for a 10-minute gap"

        # All written bars must be at least 2 minutes old
        for call_obj in fetcher.append_to_1m_parquet.call_args_list:
            written_bars = call_obj[0][1]  # second positional arg
            for bar in written_bars:
                assert bar["timestamp"] + 60_000 <= two_min_ago + 60_000, \
                    f"Bar at {bar['timestamp']} is too recent (within 2 min of now)"

    def test_backfill_1m_gap_threshold_minutes_skip(self, tmp_path):
        """AC8: Tokens with gaps smaller than gap_threshold_minutes are skipped."""
        now_ms = int(time.time() * 1000)
        # Place last bar only 3 minutes ago — should be skipped with 5-minute threshold
        last_bar_ts = now_ms - 3 * 60_000

        _create_1m_parquet(tmp_path, "BTC", _make_1m_bars(last_bar_ts, 1))

        exchange = MagicMock()
        fetcher = LiveFetcher(exchange=exchange, data_dir=str(tmp_path))

        results = fetcher.backfill_gaps(
            {"BTC"}, timeframe="1m", gap_threshold_minutes=5,
        )

        assert not exchange.fetch_ohlcv.called, \
            "Token with gap < gap_threshold_minutes should be skipped"

    def test_backfill_1h_default_unchanged(self, tmp_path):
        """AC8: backfill_gaps without timeframe param defaults to 1h (backward compatible)."""
        exchange = MagicMock()
        fetcher = LiveFetcher(exchange=exchange, data_dir=str(tmp_path))

        # Should not crash with default timeframe
        results = fetcher.backfill_gaps(set())
        assert isinstance(results, dict)


# ===================================================================
# AC9: _find_last_timestamp_ms with timeframe="1m"
# ===================================================================

class TestFindLastTimestamp1m:
    """AC9: _find_last_timestamp_ms checks 1m_cache when timeframe='1m'."""

    def test_find_last_timestamp_1m_checks_1m_cache(self, tmp_path):
        """AC9: timeframe='1m' looks in data/perp/1m_cache/{TOKEN}_1m.parquet."""
        now_ms = int(time.time() * 1000)
        bar_ts = now_ms - 5 * 60_000

        _create_1m_parquet(tmp_path, "BTC", _make_1m_bars(bar_ts, 3))

        fetcher = LiveFetcher(data_dir=str(tmp_path))
        last_ts = fetcher._find_last_timestamp_ms("BTC", "perp", timeframe="1m")

        expected_last = bar_ts + 2 * 60_000  # Last of 3 bars
        assert last_ts is not None, "Should find timestamp in 1m cache"
        assert last_ts == pytest.approx(expected_last, abs=1000), \
            f"Last timestamp should be {expected_last}, got: {last_ts}"

    def test_find_last_timestamp_1m_returns_none_when_no_file(self, tmp_path):
        """AC9: Returns None when no 1m parquet exists."""
        fetcher = LiveFetcher(data_dir=str(tmp_path))
        result = fetcher._find_last_timestamp_ms("NONEXISTENT", "perp", timeframe="1m")
        assert result is None

    def test_find_last_timestamp_1h_default_unchanged(self, tmp_path):
        """AC9: Default timeframe='1h' still checks live buffer + 1h_cache (existing behavior)."""
        # Create 1h cache data
        hist_dir = tmp_path / "perp" / "1h_cache"
        hist_dir.mkdir(parents=True, exist_ok=True)
        path = hist_dir / "BTC_1h.parquet"

        now_ms = int(time.time() * 1000)
        bar_ts = now_ms - 5 * 3_600_000

        df = pd.DataFrame({
            "open": [100.0], "high": [105.0], "low": [95.0],
            "close": [102.0], "volume": [1000.0],
        }, index=pd.to_datetime([bar_ts], unit="ms", utc=True).tz_localize(None))
        df.to_parquet(path)

        fetcher = LiveFetcher(data_dir=str(tmp_path))
        result = fetcher._find_last_timestamp_ms("BTC", "perp")

        assert result is not None, "Should find timestamp in 1h cache with default timeframe"

    def test_find_last_timestamp_1m_ignores_1h_cache(self, tmp_path):
        """AC9: timeframe='1m' does NOT check 1h_cache or live buffer."""
        # Create ONLY 1h cache data (no 1m cache)
        hist_dir = tmp_path / "perp" / "1h_cache"
        hist_dir.mkdir(parents=True, exist_ok=True)
        path = hist_dir / "BTC_1h.parquet"

        now_ms = int(time.time() * 1000)
        df = pd.DataFrame({
            "open": [100.0], "high": [105.0], "low": [95.0],
            "close": [102.0], "volume": [1000.0],
        }, index=pd.to_datetime([now_ms - 3_600_000], unit="ms", utc=True).tz_localize(None))
        df.to_parquet(path)

        fetcher = LiveFetcher(data_dir=str(tmp_path))
        result = fetcher._find_last_timestamp_ms("BTC", "perp", timeframe="1m")

        assert result is None, \
            "timeframe='1m' should NOT find timestamps in 1h_cache"


# ===================================================================
# AC17: Consolidated filter_closed_bars with timeframe param
# ===================================================================

class TestFilterClosedBarsConsolidated:
    """AC17: filter_closed_bars accepts timeframe; filter_closed_bars_1m removed."""

    def test_filter_closed_bars_1m_timeframe(self):
        """AC17: filter_closed_bars(bars, timeframe='1m') uses 60_000ms threshold."""
        now_ms = int(time.time() * 1000)
        # Bar from 2 minutes ago: closed (ts + 60_000 <= now)
        closed_bar = {"timestamp": now_ms - 2 * 60_000, "open": 100, "high": 105,
                      "low": 95, "close": 102, "volume": 1000}
        # Bar from 30 seconds ago: NOT closed (ts + 60_000 > now)
        open_bar = {"timestamp": now_ms - 30_000, "open": 103, "high": 107,
                    "low": 100, "close": 105, "volume": 500}

        fetcher = LiveFetcher.__new__(LiveFetcher)
        result = fetcher.filter_closed_bars(
            [closed_bar, open_bar], now_ms=now_ms, timeframe="1m",
        )

        assert len(result) == 1, "Only the closed bar should be returned for 1m"
        assert result[0]["timestamp"] == closed_bar["timestamp"]

    def test_filter_closed_bars_1h_default(self):
        """AC17: filter_closed_bars default timeframe='1h' uses 3_600_000ms threshold."""
        now_ms = int(time.time() * 1000)
        # Bar from 2 hours ago: closed
        closed_bar = {"timestamp": now_ms - 2 * 3_600_000, "open": 100, "high": 105,
                      "low": 95, "close": 102, "volume": 1000}
        # Bar from 30 minutes ago: NOT closed for 1h
        open_bar = {"timestamp": now_ms - 30 * 60_000, "open": 103, "high": 107,
                    "low": 100, "close": 105, "volume": 500}

        fetcher = LiveFetcher.__new__(LiveFetcher)
        result = fetcher.filter_closed_bars(
            [closed_bar, open_bar], now_ms=now_ms,
        )

        assert len(result) == 1, "Default 1h filter should only keep bars closed > 1h ago"

    def test_filter_closed_bars_1m_removed(self):
        """AC17: filter_closed_bars_1m no longer exists as a separate method."""
        assert not hasattr(LiveFetcher, "filter_closed_bars_1m"), \
            "filter_closed_bars_1m should be removed (consolidated into filter_closed_bars)"

    def test_filter_closed_bars_1h_explicit(self):
        """AC17: filter_closed_bars(timeframe='1h') uses 3_600_000ms threshold explicitly."""
        now_ms = int(time.time() * 1000)
        closed_bar = {"timestamp": now_ms - 2 * 3_600_000, "open": 100, "high": 105,
                      "low": 95, "close": 102, "volume": 1000}

        fetcher = LiveFetcher.__new__(LiveFetcher)
        result = fetcher.filter_closed_bars(
            [closed_bar], now_ms=now_ms, timeframe="1h",
        )

        assert len(result) == 1
