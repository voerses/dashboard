"""Acceptance tests for Task 14: Dashboard Exit Sentinel tab (AC19).

Tests verify:
  - sentinel_recent.json parsed correctly
  - Event status lifecycle: PENDING -> TRUE_POSITIVE
  - Event status lifecycle: PENDING -> FALSE_TRIGGER
  - 24h summary computation
  - Per-strategy breakdown rendering
  - Ring buffer capped at 50 events

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until dashboard sentinel tab is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from tools.generate_dashboard_v2 import (
    parse_sentinel_recent,
    resolve_event_status,
    compute_24h_summary,
    build_strategy_breakdown,
    SentinelDashboardData,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recent_event(**overrides) -> dict:
    """Build a synthetic sentinel_recent.json event."""
    defaults = dict(
        timestamp="2025-10-15T14:30:30Z",
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        direction=1,
        event_type="breach_confirmed",
        exit_reason="sentinel_stop",
        entry_price=66_000.0,
        stop_price=64_000.0,
        breach_price=63_800.0,
        exit_price=63_500.0,
        confirmation_delay_s=30,
        liquidity_tier="btc_eth",
        status="PENDING",
        margin_usd=10_000.0,
    )
    defaults.update(overrides)
    return defaults


def _make_trade_record(**overrides) -> dict:
    """Build a synthetic trade from trades.jsonl."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        exit_reason="stop",
        exit_price=63_000.0,
        pnl=-450.0,
    )
    defaults.update(overrides)
    return defaults


# ===================================================================
# Test: sentinel_recent.json parsed correctly
# ===================================================================

class TestSentinelRecentParsing:
    """sentinel_recent.json is parsed into structured data."""

    def test_parse_single_event(self, tmp_path):
        """Single event in sentinel_recent.json is parsed."""
        events = [_make_recent_event()]
        recent_file = tmp_path / "sentinel_recent.json"
        recent_file.write_text(json.dumps(events))

        result = parse_sentinel_recent(recent_file)
        assert len(result) == 1
        assert result[0]["position_id"] == "BTC:s56:100:primary"
        assert result[0]["status"] == "PENDING"

    def test_parse_multiple_events(self, tmp_path):
        """Multiple events are all parsed."""
        events = [
            _make_recent_event(position_id="BTC:s56:100:primary"),
            _make_recent_event(position_id="ETH:s60:200:primary", token="ETH"),
        ]
        recent_file = tmp_path / "sentinel_recent.json"
        recent_file.write_text(json.dumps(events))

        result = parse_sentinel_recent(recent_file)
        assert len(result) == 2

    def test_parse_empty_file(self, tmp_path):
        """Empty sentinel_recent.json returns empty list."""
        recent_file = tmp_path / "sentinel_recent.json"
        recent_file.write_text("[]")

        result = parse_sentinel_recent(recent_file)
        assert result == []

    def test_parse_missing_file(self, tmp_path):
        """Missing sentinel_recent.json returns empty list."""
        result = parse_sentinel_recent(tmp_path / "nonexistent.json")
        assert result == []


# ===================================================================
# Test: Event status lifecycle: PENDING -> TRUE_POSITIVE
# ===================================================================

class TestStatusLifecycleTruePositive:
    """PENDING events become TRUE_POSITIVE when hourly also exits."""

    def test_pending_to_true_positive(self):
        """PENDING event resolved to TRUE_POSITIVE when matched trade found."""
        event = _make_recent_event(status="PENDING", exit_reason="sentinel_stop")
        trade = _make_trade_record(exit_reason="stop")

        resolved = resolve_event_status(event, trade)
        assert resolved == "TRUE_POSITIVE"

    def test_pending_stays_pending_without_trade(self):
        """PENDING event stays PENDING when hourly tick hasn't processed yet."""
        event = _make_recent_event(status="PENDING")
        resolved = resolve_event_status(event, trade=None)
        assert resolved == "PENDING"


# ===================================================================
# Test: Event status lifecycle: PENDING -> FALSE_TRIGGER
# ===================================================================

class TestStatusLifecycleFalseTrigger:
    """PENDING events become FALSE_TRIGGER when position recovered."""

    def test_pending_to_false_trigger(self):
        """PENDING event resolved to FALSE_TRIGGER when no trade (recovered)."""
        event = _make_recent_event(status="PENDING")
        # Trade is None = position was NOT exited by hourly -> false trigger
        # But we need to know the hourly tick processed (bar is complete)
        resolved = resolve_event_status(event, trade=None, bar_complete=True)
        assert resolved == "FALSE_TRIGGER"

    def test_pending_to_preempted(self):
        """PENDING event resolved to PREEMPTED when hourly exits for different reason."""
        event = _make_recent_event(status="PENDING", exit_reason="sentinel_stop")
        trade = _make_trade_record(exit_reason="regime")

        resolved = resolve_event_status(event, trade)
        assert resolved == "PREEMPTED"


# ===================================================================
# Test: 24h summary computation
# ===================================================================

class TestSummary24h:
    """24h summary aggregates events correctly."""

    def test_summary_counts(self):
        """24h summary counts breaches, wick filters, true positives, etc."""
        now = time.time()
        events = [
            _make_recent_event(
                timestamp=_iso_from_epoch(now - 3600),  # 1h ago
                status="TRUE_POSITIVE",
                event_type="breach_confirmed",
            ),
            _make_recent_event(
                timestamp=_iso_from_epoch(now - 7200),  # 2h ago
                status="FALSE_TRIGGER",
                event_type="breach_confirmed",
                position_id="ETH:s60:200:primary",
            ),
            _make_recent_event(
                timestamp=_iso_from_epoch(now - 1800),  # 30min ago
                status="FILTERED",
                event_type="wick_filtered",
                position_id="SOL:s63:300:primary",
            ),
        ]

        summary = compute_24h_summary(events, current_time=now)
        assert summary["total_breaches"] >= 2
        assert summary["wick_filters"] >= 1
        assert summary["true_positives"] >= 1
        assert summary["false_triggers"] >= 1

    def test_summary_excludes_old_events(self):
        """Events older than 24h are excluded from summary."""
        now = time.time()
        events = [
            _make_recent_event(
                timestamp=_iso_from_epoch(now - 90_000),  # 25h ago
                status="TRUE_POSITIVE",
                event_type="breach_confirmed",
            ),
        ]

        summary = compute_24h_summary(events, current_time=now)
        assert summary["total_breaches"] == 0


# ===================================================================
# Test: Per-strategy breakdown rendering
# ===================================================================

class TestStrategyBreakdown:
    """Per-strategy breakdown table is generated."""

    def test_breakdown_by_strategy(self):
        """Events grouped by strategy_id."""
        events = [
            _make_recent_event(strategy_id="s56", status="TRUE_POSITIVE"),
            _make_recent_event(strategy_id="s56", status="TRUE_POSITIVE",
                               position_id="BTC:s56:200:primary"),
            _make_recent_event(strategy_id="s60", status="FALSE_TRIGGER",
                               position_id="ETH:s60:100:primary"),
        ]

        breakdown = build_strategy_breakdown(events)
        assert "s56" in breakdown
        assert "s60" in breakdown
        assert breakdown["s56"]["count"] == 2
        assert breakdown["s60"]["count"] == 1

    def test_breakdown_empty_events(self):
        """Empty event list produces empty breakdown."""
        breakdown = build_strategy_breakdown([])
        assert breakdown == {}


# ===================================================================
# Test: Ring buffer capped at 50 events
# ===================================================================

class TestRingBufferCap:
    """sentinel_recent.json ring buffer never exceeds 50."""

    def test_cap_at_50(self, tmp_path):
        """Parsing 60 events only keeps 50."""
        events = [
            _make_recent_event(position_id=f"TOKEN{i}:s56:100:primary")
            for i in range(60)
        ]
        recent_file = tmp_path / "sentinel_recent.json"
        recent_file.write_text(json.dumps(events))

        result = parse_sentinel_recent(recent_file)
        assert len(result) <= 50

    def test_most_recent_kept(self, tmp_path):
        """When capped, the most recent events are kept."""
        events = [
            _make_recent_event(
                position_id=f"TOKEN{i}:s56:100:primary",
                timestamp=_iso_from_epoch(1697380200 + i * 60),
            )
            for i in range(60)
        ]
        recent_file = tmp_path / "sentinel_recent.json"
        recent_file.write_text(json.dumps(events))

        result = parse_sentinel_recent(recent_file)
        # Most recent should be TOKEN59 (last entry)
        pids = {e["position_id"] for e in result}
        assert "TOKEN59:s56:100:primary" in pids


# ===================================================================
# Test: SentinelDashboardData integration
# ===================================================================

class TestSentinelDashboardData:
    """SentinelDashboardData aggregates all dashboard components."""

    def test_build_from_components(self, tmp_path):
        """SentinelDashboardData integrates parsed events, summary, and breakdown."""
        events = [
            _make_recent_event(status="TRUE_POSITIVE"),
            _make_recent_event(
                position_id="ETH:s56:100:primary", token="ETH",
                status="FALSE_TRIGGER",
            ),
        ]
        recent_file = tmp_path / "sentinel_recent.json"
        recent_file.write_text(json.dumps(events))

        parsed = parse_sentinel_recent(recent_file)
        summary = compute_24h_summary(parsed)
        breakdown = build_strategy_breakdown(parsed)

        data = SentinelDashboardData(
            events=parsed,
            summary=summary,
            strategy_breakdown=breakdown,
        )

        assert len(data.events) == 2
        assert isinstance(data.summary, dict)
        assert isinstance(data.strategy_breakdown, dict)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _iso_from_epoch(epoch: float) -> str:
    """Convert epoch seconds to ISO 8601 UTC string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
