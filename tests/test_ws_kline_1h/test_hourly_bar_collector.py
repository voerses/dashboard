"""Acceptance tests for Task 2: HourlyBarCollector.

Tests verify:
  - AC9:  State machine COLLECTING->READY->CONSUMED->COLLECTING, late bar drops,
          next-hour buffering while READY.
  - AC9a: consume() transitions READY->CONSUMED->COLLECTING, returns received
          tokens, replays buffered next-hour bars.
  - AC10: Readiness fires at >=90% expected tokens OR 60s timeout.
          Exposed as threading.Event.
  - AC11: get_missing_tokens() returns tokens not reported via WS.
  - AC11a: shutdown() stops writer thread, drains queue.

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.hourly_bar_collector import HourlyBarCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOUR_MS = 3_600_000


def _make_bar(hour_offset: int = 0, base_hour_ms: int = 1700000000 * 1000) -> dict:
    """Build a bar dict aligned to an hour boundary.

    hour_offset=0 => base hour, hour_offset=1 => next hour, etc.
    """
    # Align base to hour boundary
    base_aligned = (base_hour_ms // HOUR_MS) * HOUR_MS
    ts = base_aligned + hour_offset * HOUR_MS
    return {
        "timestamp": ts,
        "open": 100.0,
        "high": 105.0,
        "low": 98.0,
        "close": 103.0,
        "volume": 1000.0,
    }


def _mock_fetcher():
    """Return a mock LiveFetcher with append_to_parquet as a MagicMock."""
    fetcher = MagicMock()
    fetcher.append_to_parquet = MagicMock()
    return fetcher


def _small_expected():
    """Small expected token set for focused tests: 3 perp, 2 spot = 5 total."""
    return {
        "perp": {"BTC", "ETH", "SOL"},
        "spot": {"BTC", "ETH"},
    }


def _make_collector(
    expected_tokens=None,
    fetcher=None,
    readiness_pct=0.90,
    timeout_s=60.0,
):
    """Create an HourlyBarCollector with sensible test defaults."""
    if expected_tokens is None:
        expected_tokens = _small_expected()
    if fetcher is None:
        fetcher = _mock_fetcher()
    return HourlyBarCollector(
        expected_tokens=expected_tokens,
        fetcher=fetcher,
        readiness_pct=readiness_pct,
        timeout_s=timeout_s,
    )


# ===================================================================
# AC9: State machine — COLLECTING -> READY -> CONSUMED -> COLLECTING
# ===================================================================

class TestStateMachineBasic:
    """AC9: HourlyBarCollector implements COLLECTING->READY->CONSUMED->COLLECTING."""

    def test_initial_state_is_collecting(self):
        """AC9: Collector starts in COLLECTING state."""
        c = _make_collector()
        try:
            assert c._state == HourlyBarCollector.State.COLLECTING
        finally:
            c.shutdown()

    def test_state_transitions_to_ready_on_threshold(self):
        """AC9: State becomes READY when >=90% of expected tokens report."""
        # 5 total tokens, 90% = 4.5, so 5 is enough
        c = _make_collector()
        bar = _make_bar(0)
        try:
            # Report all 5 tokens
            c.on_bar("BTC", bar, "perp")
            c.on_bar("ETH", bar, "perp")
            c.on_bar("SOL", bar, "perp")
            c.on_bar("BTC", bar, "spot")
            c.on_bar("ETH", bar, "spot")

            assert c._state == HourlyBarCollector.State.READY
        finally:
            c.shutdown()

    def test_state_not_ready_below_threshold(self):
        """AC9: State stays COLLECTING when below 90% threshold."""
        # 5 total, need 5 (ceil(5*0.9)=5). Report only 4.
        c = _make_collector()
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")
            c.on_bar("ETH", bar, "perp")
            c.on_bar("SOL", bar, "perp")
            c.on_bar("BTC", bar, "spot")
            # Only 4 of 5 reported

            assert c._state == HourlyBarCollector.State.COLLECTING
        finally:
            c.shutdown()

    def test_consume_transitions_to_collecting(self):
        """AC9: consume() transitions READY -> CONSUMED -> COLLECTING."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            # Report all tokens to reach READY
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, bar, "spot")

            assert c._state == HourlyBarCollector.State.READY
            c.consume()
            assert c._state == HourlyBarCollector.State.COLLECTING
        finally:
            c.shutdown()


class TestLateBarsDropped:
    """AC9: Bars for already-consumed hours are silently dropped from tracking."""

    def test_late_bar_for_consumed_hour_not_tracked(self):
        """AC9: Bar arriving for a consumed hour does not reset state or tracking."""
        c = _make_collector()
        hour0_bar = _make_bar(0)
        hour1_bar = _make_bar(1)
        try:
            # Complete hour 0
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour0_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour0_bar, "spot")

            assert c._state == HourlyBarCollector.State.READY
            c.consume()  # hour 0 is now consumed

            # Start collecting hour 1
            c.on_bar("BTC", hour1_bar, "perp")
            assert c._state == HourlyBarCollector.State.COLLECTING

            # Late bar from hour 0 arrives — should be silently dropped
            c.on_bar("ETH", hour0_bar, "perp")

            # State should still be COLLECTING for hour 1, not reset
            assert c._state == HourlyBarCollector.State.COLLECTING
        finally:
            c.shutdown()

    def test_late_bar_for_consumed_hour_still_persisted(self):
        """AC9: Late bars ARE still persisted (enqueued to write queue) even though
        they are dropped from readiness tracking."""
        fetcher = _mock_fetcher()
        c = _make_collector(fetcher=fetcher)
        hour0_bar = _make_bar(0)
        try:
            # Complete and consume hour 0
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour0_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour0_bar, "spot")
            c.consume()

            # Late bar from hour 0 — should still be enqueued for persistence
            call_count_before = fetcher.append_to_parquet.call_count
            c.on_bar("NEW_LATE", hour0_bar, "perp")

            # Give writer thread time to drain
            c._write_queue.join()

            # append_to_parquet should have been called for the late bar
            assert fetcher.append_to_parquet.call_count > call_count_before
        finally:
            c.shutdown()


class TestNextHourBuffering:
    """AC9: Bars for the next hour are buffered when state is READY."""

    def test_next_hour_bar_buffered_while_ready(self):
        """AC9: When state is READY, bars for the next hour are buffered, not lost."""
        c = _make_collector()
        hour0_bar = _make_bar(0)
        hour1_bar = _make_bar(1)
        try:
            # Complete hour 0
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour0_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour0_bar, "spot")

            assert c._state == HourlyBarCollector.State.READY

            # Next-hour bar arrives while still READY (main loop hasn't consumed yet)
            c.on_bar("BTC", hour1_bar, "perp")

            # Should still be READY (for hour 0), not reset to COLLECTING
            assert c._state == HourlyBarCollector.State.READY

            # Buffer should contain the next-hour bar
            assert len(c._next_hour_buffer) >= 1
        finally:
            c.shutdown()

    def test_buffered_bars_replayed_on_consume(self):
        """AC9a: consume() replays buffered next-hour bars into the new hour's tracking."""
        c = _make_collector()
        hour0_bar = _make_bar(0)
        hour1_bar = _make_bar(1)
        try:
            # Complete hour 0
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour0_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour0_bar, "spot")

            # Buffer next-hour bar while READY
            c.on_bar("BTC", hour1_bar, "perp")
            c.on_bar("ETH", hour1_bar, "spot")

            # Consume hour 0 — this should replay buffered bars into hour 1
            c.consume()

            # After consume, the collector should be COLLECTING hour 1
            # and BTC/perp + ETH/spot should already be tracked
            assert c._state == HourlyBarCollector.State.COLLECTING

            # The replayed bars should show up in the received set
            assert "BTC" in c._received.get("perp", set())
            assert "ETH" in c._received.get("spot", set())
        finally:
            c.shutdown()


# ===================================================================
# AC9a: consume() returns received tokens
# ===================================================================

class TestConsume:
    """AC9a: consume() transitions state and returns received tokens."""

    def test_consume_returns_received_tokens(self):
        """AC9a: consume() returns dict of {market: set of tokens} received."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")
            c.on_bar("ETH", bar, "perp")
            c.on_bar("SOL", bar, "perp")
            c.on_bar("BTC", bar, "spot")
            c.on_bar("ETH", bar, "spot")

            received = c.consume()

            assert "perp" in received
            assert "spot" in received
            assert received["perp"] == {"BTC", "ETH", "SOL"}
            assert received["spot"] == {"BTC", "ETH"}
        finally:
            c.shutdown()

    def test_consume_clears_ready_event(self):
        """AC9a: consume() clears the readiness event."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, bar, "spot")

            assert c._ready.is_set()
            c.consume()
            assert not c._ready.is_set()
        finally:
            c.shutdown()

    def test_consume_adds_hour_to_consumed_set(self):
        """AC9a: consume() adds the current hour to _consumed_hours."""
        c = _make_collector()
        bar = _make_bar(0)
        hour_ts = (bar["timestamp"] // HOUR_MS) * HOUR_MS
        try:
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, bar, "spot")

            c.consume()
            assert hour_ts in c._consumed_hours
        finally:
            c.shutdown()


# ===================================================================
# AC10: Readiness — threshold OR timeout, exposed as threading.Event
# ===================================================================

class TestReadinessThreshold:
    """AC10: Readiness fires at >=90% expected tokens."""

    def test_ready_event_set_at_90_pct(self):
        """AC10: _ready threading.Event is set when >=90% tokens report."""
        # 10 perp tokens, 90% = 9
        tokens = {f"T{i}" for i in range(10)}
        c = _make_collector(
            expected_tokens={"perp": tokens, "spot": set()},
            readiness_pct=0.90,
        )
        bar = _make_bar(0)
        try:
            # Report 9 of 10 (90%)
            for i in range(9):
                c.on_bar(f"T{i}", bar, "perp")

            assert c._ready.is_set(), "ready event should be set at 90% threshold"
        finally:
            c.shutdown()

    def test_ready_event_not_set_below_threshold(self):
        """AC10: _ready is NOT set below 90%."""
        tokens = {f"T{i}" for i in range(10)}
        c = _make_collector(
            expected_tokens={"perp": tokens, "spot": set()},
            readiness_pct=0.90,
        )
        bar = _make_bar(0)
        try:
            # Report 8 of 10 (80%)
            for i in range(8):
                c.on_bar(f"T{i}", bar, "perp")

            assert not c._ready.is_set(), "ready event should NOT be set below threshold"
        finally:
            c.shutdown()

    def test_ready_is_threading_event(self):
        """AC10: Readiness is exposed as a threading.Event."""
        c = _make_collector()
        try:
            assert isinstance(c._ready, threading.Event)
        finally:
            c.shutdown()

    def test_wait_for_ready_returns_true_when_threshold_met(self):
        """AC10: wait_for_ready() returns True when enough tokens report."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            # Report all tokens in a background thread
            def report_all():
                time.sleep(0.05)
                for token in ["BTC", "ETH", "SOL"]:
                    c.on_bar(token, bar, "perp")
                for token in ["BTC", "ETH"]:
                    c.on_bar(token, bar, "spot")

            t = threading.Thread(target=report_all)
            t.start()

            result = c.wait_for_ready(timeout=5.0)
            t.join()

            assert result is True
        finally:
            c.shutdown()

    def test_wait_for_ready_returns_false_on_timeout(self):
        """AC10: wait_for_ready() returns False if timeout expires without readiness."""
        c = _make_collector(timeout_s=60.0)
        try:
            # Don't report any bars — wait should time out
            result = c.wait_for_ready(timeout=0.1)
            assert result is False
        finally:
            c.shutdown()


class TestReadinessTimeout:
    """AC10: Readiness fires on 60s timeout after hour boundary."""

    def test_timeout_parameter_stored(self):
        """AC10: timeout_s is stored and used for readiness timeout."""
        c = _make_collector(timeout_s=30.0)
        try:
            assert c._timeout_s == 30.0
        finally:
            c.shutdown()

    def test_custom_readiness_pct(self):
        """AC10: readiness_pct parameter is respected."""
        # With 50% threshold: 3 of 5 tokens is enough
        c = _make_collector(readiness_pct=0.50)
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")
            c.on_bar("ETH", bar, "perp")
            c.on_bar("BTC", bar, "spot")
            # 3 of 5 = 60% >= 50%

            assert c._ready.is_set(), "ready should fire at 60% when threshold is 50%"
        finally:
            c.shutdown()


# ===================================================================
# AC11: get_missing_tokens() returns unreported tokens
# ===================================================================

class TestMissingTokens:
    """AC11: get_missing_tokens() returns tokens not reported via WS."""

    def test_all_tokens_missing_initially(self):
        """AC11: Before any bars arrive, all tokens are missing."""
        c = _make_collector()
        try:
            # Start hour tracking by sending at least one bar
            bar = _make_bar(0)
            c.on_bar("BTC", bar, "perp")

            missing = c.get_missing_tokens()
            assert "ETH" in missing.get("perp", set())
            assert "SOL" in missing.get("perp", set())
        finally:
            c.shutdown()

    def test_partial_report_shows_missing(self):
        """AC11: After partial reporting, missing tokens are correct."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")
            c.on_bar("BTC", bar, "spot")

            missing = c.get_missing_tokens()

            # Perp: ETH and SOL missing
            assert "ETH" in missing.get("perp", set())
            assert "SOL" in missing.get("perp", set())
            assert "BTC" not in missing.get("perp", set())

            # Spot: ETH missing
            assert "ETH" in missing.get("spot", set())
            assert "BTC" not in missing.get("spot", set())
        finally:
            c.shutdown()

    def test_all_reported_no_missing(self):
        """AC11: When all tokens report, missing set is empty."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, bar, "spot")

            missing = c.get_missing_tokens()

            # All markets should have empty missing sets
            for market, tokens in missing.items():
                assert len(tokens) == 0, (
                    f"Expected no missing tokens for {market}, got {tokens}"
                )
        finally:
            c.shutdown()

    def test_missing_tokens_returns_dict_keyed_by_market(self):
        """AC11: get_missing_tokens() returns a dict keyed by market type."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")

            missing = c.get_missing_tokens()

            assert isinstance(missing, dict)
            assert "perp" in missing
            assert "spot" in missing
        finally:
            c.shutdown()


# ===================================================================
# AC11a: shutdown() stops writer thread, drains queue
# ===================================================================

class TestShutdown:
    """AC11a: shutdown() stops the writer thread and drains the queue."""

    def test_shutdown_stops_writer_thread(self):
        """AC11a: After shutdown(), the writer thread is no longer alive."""
        c = _make_collector()

        # Writer thread should be alive before shutdown
        assert c._writer_thread.is_alive()

        c.shutdown()

        # Writer thread should be stopped after shutdown
        assert not c._writer_thread.is_alive()

    def test_shutdown_drains_remaining_queue_items(self):
        """AC11a: shutdown() processes remaining items in the write queue."""
        fetcher = _mock_fetcher()
        c = _make_collector(fetcher=fetcher)
        bar = _make_bar(0)
        try:
            # Enqueue several bars
            c.on_bar("BTC", bar, "perp")
            c.on_bar("ETH", bar, "perp")
            c.on_bar("SOL", bar, "perp")

            c.shutdown()

            # All bars should have been persisted
            assert fetcher.append_to_parquet.call_count >= 3
        except Exception:
            c.shutdown()
            raise

    def test_shutdown_idempotent(self):
        """AC11a: Calling shutdown() twice does not raise."""
        c = _make_collector()
        c.shutdown()
        # Second call should not raise
        c.shutdown()

    def test_on_bar_after_shutdown_does_not_crash(self):
        """AC11a: Calling on_bar after shutdown does not raise."""
        c = _make_collector()
        c.shutdown()
        bar = _make_bar(0)
        # Should not raise — graceful degradation
        try:
            c.on_bar("BTC", bar, "perp")
        except Exception:
            pytest.fail("on_bar after shutdown should not raise")


# ===================================================================
# AC7: Write queue + persistence (per-token locks)
# ===================================================================

class TestWriteQueuePersistence:
    """AC7: Bars enqueued to write queue, drained by writer thread to append_to_parquet."""

    def test_on_bar_enqueues_to_write_queue(self):
        """AC7: on_bar persists bar via fetcher.append_to_parquet through write queue."""
        fetcher = _mock_fetcher()
        c = _make_collector(fetcher=fetcher)
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")

            # Wait for writer thread to drain
            c._write_queue.join()

            fetcher.append_to_parquet.assert_called()
            # Verify call args: token, market, [bar]
            args = fetcher.append_to_parquet.call_args
            assert args[0][0] == "BTC"
            assert args[0][1] == "perp"
            assert args[0][2] == [bar]
        finally:
            c.shutdown()

    def test_multiple_bars_all_persisted(self):
        """AC7: Multiple on_bar calls all get persisted."""
        fetcher = _mock_fetcher()
        c = _make_collector(fetcher=fetcher)
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")
            c.on_bar("ETH", bar, "perp")
            c.on_bar("SOL", bar, "perp")
            c.on_bar("BTC", bar, "spot")
            c.on_bar("ETH", bar, "spot")

            c._write_queue.join()

            assert fetcher.append_to_parquet.call_count == 5
        finally:
            c.shutdown()

    def test_writer_thread_is_daemon(self):
        """AC7: Writer thread should be a daemon so it doesn't block process exit."""
        c = _make_collector()
        try:
            assert c._writer_thread.daemon is True
        finally:
            c.shutdown()

    def test_per_token_write_locks_exist(self):
        """AC7: Collector maintains _1h_write_locks dict for per-token locking."""
        c = _make_collector()
        try:
            assert hasattr(c, "_1h_write_locks")
            assert isinstance(c._1h_write_locks, dict)
        finally:
            c.shutdown()


# ===================================================================
# Integration: Full hour cycle
# ===================================================================

class TestFullHourCycle:
    """Integration: Complete hour cycle through all state transitions."""

    def test_two_consecutive_hours(self):
        """Full cycle: hour0 COLLECTING->READY->consume, hour1 COLLECTING->READY->consume."""
        fetcher = _mock_fetcher()
        c = _make_collector(fetcher=fetcher)
        hour0_bar = _make_bar(0)
        hour1_bar = _make_bar(1)
        try:
            # Hour 0: collect all tokens
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour0_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour0_bar, "spot")

            assert c._state == HourlyBarCollector.State.READY
            received0 = c.consume()
            assert c._state == HourlyBarCollector.State.COLLECTING

            # Hour 1: collect all tokens
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour1_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour1_bar, "spot")

            assert c._state == HourlyBarCollector.State.READY
            received1 = c.consume()
            assert c._state == HourlyBarCollector.State.COLLECTING

            # Both hours should have full received sets
            assert received0["perp"] == {"BTC", "ETH", "SOL"}
            assert received1["perp"] == {"BTC", "ETH", "SOL"}
        finally:
            c.shutdown()

    def test_hour_with_buffered_next_hour_replay(self):
        """Full cycle: hour0 complete, next-hour bars buffer, consume replays them."""
        c = _make_collector()
        hour0_bar = _make_bar(0)
        hour1_bar = _make_bar(1)
        try:
            # Complete hour 0
            for token in ["BTC", "ETH", "SOL"]:
                c.on_bar(token, hour0_bar, "perp")
            for token in ["BTC", "ETH"]:
                c.on_bar(token, hour0_bar, "spot")

            # While READY, next-hour bars arrive
            c.on_bar("BTC", hour1_bar, "perp")
            c.on_bar("ETH", hour1_bar, "perp")
            c.on_bar("SOL", hour1_bar, "perp")
            c.on_bar("BTC", hour1_bar, "spot")
            c.on_bar("ETH", hour1_bar, "spot")

            # Consume hour 0 — should replay hour 1 bars
            c.consume()

            # After replay, hour 1 should be fully tracked and READY
            assert c._state == HourlyBarCollector.State.READY
            assert c._ready.is_set()
        finally:
            c.shutdown()

    def test_duplicate_bar_same_token_same_hour(self):
        """Duplicate bar from same token in same hour does not double-count."""
        tokens = {f"T{i}" for i in range(10)}
        c = _make_collector(
            expected_tokens={"perp": tokens, "spot": set()},
            readiness_pct=0.90,
        )
        bar = _make_bar(0)
        try:
            # Report T0 three times — should only count once
            c.on_bar("T0", bar, "perp")
            c.on_bar("T0", bar, "perp")
            c.on_bar("T0", bar, "perp")

            # Only 1 unique token, well below 90% of 10
            assert not c._ready.is_set()
        finally:
            c.shutdown()


# ===================================================================
# Constructor / configuration
# ===================================================================

class TestConstructor:
    """Verify constructor accepts documented parameters."""

    def test_constructor_accepts_required_params(self):
        """Constructor takes expected_tokens and fetcher."""
        fetcher = _mock_fetcher()
        c = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": {"BTC"}},
            fetcher=fetcher,
        )
        try:
            assert c is not None
        finally:
            c.shutdown()

    def test_constructor_accepts_optional_params(self):
        """Constructor accepts readiness_pct and timeout_s."""
        fetcher = _mock_fetcher()
        c = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
            readiness_pct=0.80,
            timeout_s=30.0,
        )
        try:
            assert c._readiness_pct == 0.80
            assert c._timeout_s == 30.0
        finally:
            c.shutdown()

    def test_constructor_starts_writer_thread(self):
        """Writer thread is started by the constructor."""
        c = _make_collector()
        try:
            assert hasattr(c, "_writer_thread")
            assert c._writer_thread.is_alive()
        finally:
            c.shutdown()

    def test_constructor_initializes_write_queue(self):
        """Write queue is initialized by the constructor."""
        c = _make_collector()
        try:
            assert hasattr(c, "_write_queue")
        finally:
            c.shutdown()

    def test_constructor_initializes_consumed_hours(self):
        """_consumed_hours set is initialized empty."""
        c = _make_collector()
        try:
            assert hasattr(c, "_consumed_hours")
            assert isinstance(c._consumed_hours, set)
            assert len(c._consumed_hours) == 0
        finally:
            c.shutdown()

    def test_on_bar_accepts_token_bar_market(self):
        """on_bar(token, bar, market) signature is correct."""
        c = _make_collector()
        bar = _make_bar(0)
        try:
            # Should not raise TypeError
            c.on_bar("BTC", bar, "perp")
        finally:
            c.shutdown()


# ===================================================================
# Edge cases (from adversarial review)
# ===================================================================

class TestEdgeCases:
    """Edge case tests identified by adversarial review."""

    def test_empty_expected_tokens_immediately_ready(self):
        """With no expected tokens, collector should be immediately ready or raise."""
        c = HourlyBarCollector(
            expected_tokens={"perp": set(), "spot": set()},
            fetcher=_mock_fetcher(),
        )
        try:
            # With 0 expected tokens, any bar should trigger readiness (0/0 >= 0.9)
            # Implementation should handle this edge case gracefully
            got = c.wait_for_ready(timeout=0.1)
            # Either immediately ready (True) or handled as special case
            # The important thing is no crash / division by zero
            assert isinstance(got, bool)
        finally:
            c.shutdown()

    def test_concurrent_on_bar_no_lost_tokens(self):
        """Multiple threads calling on_bar simultaneously don't lose tokens."""
        tokens = {f"T{i}" for i in range(100)}
        c = HourlyBarCollector(
            expected_tokens={"perp": tokens, "spot": set()},
            fetcher=_mock_fetcher(),
            readiness_pct=1.0,  # need ALL tokens for readiness
        )
        bar = _make_bar(0)
        errors = []

        def _report_token(t):
            try:
                c.on_bar(t, bar, "perp")
            except Exception as e:
                errors.append(e)

        try:
            threads = [threading.Thread(target=_report_token, args=(t,)) for t in tokens]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            assert len(errors) == 0, f"Errors during concurrent on_bar: {errors}"
            # All 100 tokens should be tracked
            with c._lock:
                assert len(c._received.get("perp", set())) == 100
            assert c._ready.is_set()
        finally:
            c.shutdown()

    def test_shutdown_during_active_write(self):
        """shutdown() waits for in-progress writes to complete."""
        write_started = threading.Event()
        write_can_finish = threading.Event()

        def slow_append(*args, **kwargs):
            write_started.set()
            write_can_finish.wait(timeout=5.0)

        fetcher = _mock_fetcher()
        fetcher.append_to_parquet.side_effect = slow_append

        c = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )
        bar = _make_bar(0)
        try:
            c.on_bar("BTC", bar, "perp")
            write_started.wait(timeout=2.0)
            assert write_started.is_set(), "Writer should have started"

            # Release the slow write and shutdown
            write_can_finish.set()
            c.shutdown()

            # The slow write should have completed
            assert fetcher.append_to_parquet.call_count >= 1
        finally:
            write_can_finish.set()  # ensure no deadlock
