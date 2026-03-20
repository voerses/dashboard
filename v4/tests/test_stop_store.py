"""Acceptance tests for Task 2: StopStore class (AC1).

Tests verify:
  - StopStore write_stops/read_stops round-trip
  - append_exit_event + read_and_clear_exit_events round-trip
  - Rename-based atomic clearing (file disappears after read_and_clear,
    new appends go to fresh file)
  - read_and_clear returns empty list when no file exists
  - Malformed JSONL lines are skipped gracefully

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until stop_store.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.stop_store import StopStore, StopLevel, ExitEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stop_level(**overrides) -> StopLevel:
    """Build a StopLevel with representative defaults."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        direction=1,
        stop_price=64_000.0,
        cb_price=60_000.0,
        target_price=80_000.0,
        estimated_liq_price=55_000.0,
        entry_price=66_000.0,
        margin_usd=10_000.0,
        quantity=0.15,
        leverage=1.0,
        is_perp=True,
        no_stop_bars=0,
        bars_held=10,
        stop_active=True,
        convex_exit=False,
        trail_mult=3.0,
        cur_atr=1200.0,
        highest=70_000.0,
        lowest=62_000.0,
        has_trail_schedule=False,
        chandelier_lookback=0,
        cumulative_funding=-12.5,
        fee_rate=0.0004,
    )
    defaults.update(overrides)
    return StopLevel(**defaults)


def _make_exit_event(**overrides) -> ExitEvent:
    """Build an ExitEvent with representative defaults."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        direction=1,
        exit_price=63_500.0,
        stop_price=64_000.0,
        breach_price=63_800.0,
        breach_timestamp="2025-10-15T14:30:00Z",
        confirm_timestamp="2025-10-15T14:30:30Z",
        slippage_bps=78.0,
        sentinel_timestamp="2025-10-15T14:30:30Z",
        margin_usd=10_000.0,
        quantity=0.15,
        entry_price=66_000.0,
        exit_reason="sentinel_stop",
    )
    defaults.update(overrides)
    return ExitEvent(**defaults)


# ===================================================================
# Test: write_stops / read_stops round-trip
# ===================================================================

class TestStopStoreRoundTrip:
    """StopStore write_stops and read_stops produce identical data."""

    def test_write_then_read_single_stop(self, tmp_path):
        """Single StopLevel survives write/read round-trip."""
        store = StopStore(state_dir=tmp_path)
        stop = _make_stop_level()
        store.write_stops([stop])

        result = store.read_stops()
        assert len(result) == 1
        assert result[0].position_id == stop.position_id
        assert result[0].token == stop.token
        assert result[0].stop_price == pytest.approx(stop.stop_price)
        assert result[0].cb_price == pytest.approx(stop.cb_price)
        assert result[0].target_price == pytest.approx(stop.target_price)
        assert result[0].estimated_liq_price == pytest.approx(stop.estimated_liq_price)
        assert result[0].direction == stop.direction
        assert result[0].trail_mult == pytest.approx(stop.trail_mult)
        assert result[0].cur_atr == pytest.approx(stop.cur_atr)
        assert result[0].highest == pytest.approx(stop.highest)
        assert result[0].lowest == pytest.approx(stop.lowest)
        assert result[0].stop_active == stop.stop_active
        assert result[0].convex_exit == stop.convex_exit
        assert result[0].has_trail_schedule == stop.has_trail_schedule
        assert result[0].chandelier_lookback == stop.chandelier_lookback
        assert result[0].fee_rate == pytest.approx(stop.fee_rate)
        assert result[0].cumulative_funding == pytest.approx(stop.cumulative_funding)

    def test_write_then_read_multiple_stops(self, tmp_path):
        """Multiple StopLevels survive write/read round-trip."""
        store = StopStore(state_dir=tmp_path)
        stops = [
            _make_stop_level(position_id="BTC:s56:100:primary", token="BTC"),
            _make_stop_level(position_id="ETH:s60:100:primary", token="ETH",
                             stop_price=3200.0, entry_price=3500.0),
            _make_stop_level(position_id="SOL:s63:100:primary", token="SOL",
                             stop_price=140.0, entry_price=160.0, direction=-1),
        ]
        store.write_stops(stops)

        result = store.read_stops()
        assert len(result) == 3
        tokens = {s.token for s in result}
        assert tokens == {"BTC", "ETH", "SOL"}

    def test_write_empty_list(self, tmp_path):
        """Writing empty list produces empty read."""
        store = StopStore(state_dir=tmp_path)
        store.write_stops([])
        result = store.read_stops()
        assert result == []

    def test_read_stops_no_file_returns_empty(self, tmp_path):
        """read_stops returns empty list when stops.json does not exist."""
        store = StopStore(state_dir=tmp_path)
        result = store.read_stops()
        assert result == []

    def test_write_overwrites_previous(self, tmp_path):
        """Second write_stops replaces first write entirely."""
        store = StopStore(state_dir=tmp_path)
        store.write_stops([_make_stop_level(token="BTC")])
        store.write_stops([_make_stop_level(token="ETH", position_id="ETH:s60:100:primary")])
        result = store.read_stops()
        assert len(result) == 1
        assert result[0].token == "ETH"


# ===================================================================
# Test: append_exit_event + read_and_clear_exit_events
# ===================================================================

class TestExitEventRoundTrip:
    """ExitEvent append and read_and_clear produce correct results."""

    def test_append_then_read_and_clear(self, tmp_path):
        """Single exit event survives append + read_and_clear."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event()
        store.append_exit_event(event)

        events = store.read_and_clear_exit_events()
        assert len(events) == 1
        assert events[0].position_id == event.position_id
        assert events[0].exit_price == pytest.approx(event.exit_price)
        assert events[0].exit_reason == event.exit_reason

    def test_append_multiple_events(self, tmp_path):
        """Multiple appended events are all returned."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event(position_id="BTC:s56:100:primary"))
        store.append_exit_event(_make_exit_event(position_id="ETH:s60:100:primary",
                                                  token="ETH"))
        store.append_exit_event(_make_exit_event(position_id="SOL:s63:100:primary",
                                                  token="SOL"))

        events = store.read_and_clear_exit_events()
        assert len(events) == 3
        pids = {e.position_id for e in events}
        assert "BTC:s56:100:primary" in pids
        assert "ETH:s60:100:primary" in pids
        assert "SOL:s63:100:primary" in pids


# ===================================================================
# Test: Rename-based atomic clearing
# ===================================================================

class TestAtomicClearing:
    """read_and_clear uses rename-based clearing for atomicity."""

    def test_file_gone_after_read_and_clear(self, tmp_path):
        """exit_events.jsonl file should not exist after read_and_clear."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event())

        # File should exist before clearing
        events_file = tmp_path / "exit_events.jsonl"
        assert events_file.exists()

        store.read_and_clear_exit_events()

        # Original file should be gone after clearing
        assert not events_file.exists()
        # Consumed file should also be gone (deleted after read)
        consumed_file = tmp_path / "exit_events.jsonl.consumed"
        assert not consumed_file.exists()

    def test_new_appends_after_clear_go_to_fresh_file(self, tmp_path):
        """Appends after read_and_clear go to a new file, not mixed with old data."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event(position_id="OLD:s56:100:primary"))
        store.read_and_clear_exit_events()

        # Append new event
        store.append_exit_event(_make_exit_event(position_id="NEW:s56:200:primary"))
        events = store.read_and_clear_exit_events()

        assert len(events) == 1
        assert events[0].position_id == "NEW:s56:200:primary"

    def test_read_and_clear_empty_returns_empty(self, tmp_path):
        """read_and_clear returns empty list when no file exists."""
        store = StopStore(state_dir=tmp_path)
        events = store.read_and_clear_exit_events()
        assert events == []


# ===================================================================
# Test: Malformed JSONL handling
# ===================================================================

class TestMalformedJsonl:
    """Malformed JSONL lines are skipped gracefully."""

    def test_malformed_line_skipped(self, tmp_path):
        """A malformed JSONL line is skipped; valid lines are returned."""
        store = StopStore(state_dir=tmp_path)
        # Write one valid event
        store.append_exit_event(_make_exit_event(position_id="VALID:s56:100:primary"))

        # Manually inject a malformed line
        events_file = tmp_path / "exit_events.jsonl"
        with open(events_file, "a") as f:
            f.write("this is not valid json\n")

        # Append another valid event
        store.append_exit_event(_make_exit_event(position_id="ALSO_VALID:s56:200:primary"))

        events = store.read_and_clear_exit_events()
        pids = {e.position_id for e in events}
        assert "VALID:s56:100:primary" in pids
        assert "ALSO_VALID:s56:200:primary" in pids
        # Malformed line should have been skipped, so only 2 events
        assert len(events) == 2

    def test_empty_lines_skipped(self, tmp_path):
        """Empty lines in JSONL are silently skipped."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event())

        events_file = tmp_path / "exit_events.jsonl"
        with open(events_file, "a") as f:
            f.write("\n\n\n")

        events = store.read_and_clear_exit_events()
        assert len(events) == 1
