"""Tests for CandleAggregator.

Tests verify:
  - Prices within the same interval update high/low/close correctly
  - Interval boundary emits completed candle on flush
  - flush_completed returns empty dict when no candles complete
  - Multiple tokens tracked independently
  - update_tokens removes stale token data
  - Thread safety: concurrent on_price + flush_completed
  - Multiple completed candles for same token: last wins
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

import pytest

from v5.candle_aggregator import CandleAggregator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_ms(minutes: float) -> int:
    """Convert minutes to timestamp in milliseconds."""
    return int(minutes * 60 * 1000)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCandleAggregator:
    """Unit tests for CandleAggregator."""

    def test_init_rejects_zero_resolution(self):
        with pytest.raises(ValueError):
            CandleAggregator(0)

    def test_init_rejects_negative_resolution(self):
        with pytest.raises(ValueError):
            CandleAggregator(-5)

    def test_single_price_no_completed(self):
        """A single price within an interval should not produce a completed candle."""
        agg = CandleAggregator(5)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        result = agg.flush_completed()
        assert result == {}

    def test_same_interval_updates_hlc(self):
        """Multiple prices in the same interval should update high/low/close."""
        agg = CandleAggregator(5)
        # All within minute 0-4 (interval 0 for 5m resolution)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 105.0, _ts_ms(1))
        agg.on_price("BTC", 95.0, _ts_ms(2))
        agg.on_price("BTC", 102.0, _ts_ms(3))

        # No completed candle yet
        result = agg.flush_completed()
        assert result == {}

    def test_interval_boundary_emits_candle(self):
        """When price arrives in a new interval, the previous candle is completed."""
        agg = CandleAggregator(5)
        # Interval 0 (minutes 0-4)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 110.0, _ts_ms(2))
        agg.on_price("BTC", 95.0, _ts_ms(3))
        agg.on_price("BTC", 103.0, _ts_ms(4))

        # Interval 1 (minute 5) — triggers completion of interval 0
        agg.on_price("BTC", 104.0, _ts_ms(5))

        result = agg.flush_completed()
        assert "BTC" in result
        h, l, c = result["BTC"]
        assert h == 110.0
        assert l == 95.0
        assert c == 103.0

    def test_flush_clears_completed(self):
        """flush_completed should clear the buffer after returning."""
        agg = CandleAggregator(5)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 200.0, _ts_ms(5))  # triggers interval 0 completion

        result1 = agg.flush_completed()
        assert "BTC" in result1

        result2 = agg.flush_completed()
        assert result2 == {}

    def test_multiple_tokens_independent(self):
        """Each token should have its own candle tracking."""
        agg = CandleAggregator(5)

        # BTC in interval 0
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 110.0, _ts_ms(1))

        # ETH in interval 0
        agg.on_price("ETH", 3000.0, _ts_ms(0))
        agg.on_price("ETH", 2900.0, _ts_ms(2))

        # Trigger interval 1 for BTC only
        agg.on_price("BTC", 105.0, _ts_ms(5))

        result = agg.flush_completed()
        assert "BTC" in result
        # ETH hasn't crossed interval boundary yet
        assert "ETH" not in result

    def test_multiple_tokens_both_complete(self):
        """Both tokens complete when both get new-interval prices."""
        agg = CandleAggregator(5)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("ETH", 3000.0, _ts_ms(0))

        agg.on_price("BTC", 105.0, _ts_ms(5))
        agg.on_price("ETH", 3100.0, _ts_ms(5))

        result = agg.flush_completed()
        assert "BTC" in result
        assert "ETH" in result
        assert result["BTC"] == (100.0, 100.0, 100.0)
        assert result["ETH"] == (3000.0, 3000.0, 3000.0)

    def test_update_tokens_removes_stale(self):
        """update_tokens should remove tokens not in the active set."""
        agg = CandleAggregator(5)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("ETH", 3000.0, _ts_ms(0))

        agg.update_tokens({"BTC"})

        # ETH should be removed from tracking
        # Trigger new interval — only BTC should have a completed candle
        agg.on_price("BTC", 105.0, _ts_ms(5))
        # ETH gets a new price in interval 1 — but its interval 0 data was removed
        agg.on_price("ETH", 3100.0, _ts_ms(5))

        result = agg.flush_completed()
        assert "BTC" in result
        assert "ETH" not in result  # Was removed, so no completed candle for interval 0

    def test_1_minute_resolution(self):
        """1-minute resolution: candle completes every minute."""
        agg = CandleAggregator(1)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 105.0, _ts_ms(0.5))  # 30 seconds in

        # New minute
        agg.on_price("BTC", 102.0, _ts_ms(1))

        result = agg.flush_completed()
        assert "BTC" in result
        h, l, c = result["BTC"]
        assert h == 105.0
        assert l == 100.0
        assert c == 105.0

    def test_30_minute_resolution(self):
        """30-minute resolution: candle completes every 30 minutes."""
        agg = CandleAggregator(30)
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 110.0, _ts_ms(15))
        agg.on_price("BTC", 95.0, _ts_ms(25))
        agg.on_price("BTC", 103.0, _ts_ms(29))

        # Still in interval 0 — no completed candle
        result = agg.flush_completed()
        assert result == {}

        # Minute 30 triggers interval 1
        agg.on_price("BTC", 104.0, _ts_ms(30))
        result = agg.flush_completed()
        assert "BTC" in result
        h, l, c = result["BTC"]
        assert h == 110.0
        assert l == 95.0
        assert c == 103.0

    def test_multiple_completed_candles_merged_conservatively(self):
        """If multiple candles complete before flush, merge: high=max, low=min, close=last."""
        agg = CandleAggregator(5)
        # Interval 0: single price 100
        agg.on_price("BTC", 100.0, _ts_ms(0))
        # Interval 1: single price 110 — completes interval 0 (h=100, l=100, c=100)
        agg.on_price("BTC", 110.0, _ts_ms(5))
        # Interval 2: single price 120 — completes interval 1 (h=110, l=110, c=110)
        agg.on_price("BTC", 120.0, _ts_ms(10))

        result = agg.flush_completed()
        assert "BTC" in result
        # Merged: max(100,110)=110, min(100,110)=100, close=110 (from last candle)
        h, l, c = result["BTC"]
        assert h == 110.0
        assert l == 100.0
        assert c == 110.0

    def test_thread_safety_concurrent_access(self):
        """Concurrent on_price and flush_completed should not crash."""
        agg = CandleAggregator(1)
        errors = []

        def writer():
            try:
                for i in range(1000):
                    ts = _ts_ms(i)
                    agg.on_price("BTC", 100.0 + i * 0.01, ts)
                    agg.on_price("ETH", 3000.0 + i * 0.1, ts)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(500):
                    agg.flush_completed()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Thread safety errors: {errors}"

    def test_thread_safety_update_tokens(self):
        """Concurrent on_price and update_tokens should not crash."""
        agg = CandleAggregator(1)
        errors = []

        def writer():
            try:
                for i in range(500):
                    agg.on_price("BTC", 100.0, _ts_ms(i))
                    agg.on_price("ETH", 3000.0, _ts_ms(i))
            except Exception as e:
                errors.append(e)

        def updater():
            try:
                for _ in range(200):
                    agg.update_tokens({"BTC"})
                    agg.update_tokens({"BTC", "ETH"})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=updater)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Thread safety errors: {errors}"
