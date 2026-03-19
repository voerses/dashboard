"""Acceptance tests for Task 10: Sentinel metrics + state consistency (AC8, AC9).

Tests verify:
  - Metrics JSON written with all required fields
  - State consistency check: detects position_id mismatch
  - Stale entry purge when position closed by hourly cycle

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until sentinel_metrics.py is implemented (RED phase).
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

from v4.sentinel_metrics import SentinelMetrics
from v4.stop_store import StopLevel, StopStore


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
        estimated_liq_price=50_000.0,
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


# ===================================================================
# Test: Metrics JSON with all required fields
# ===================================================================

class TestMetricsOutput:
    """Sentinel metrics JSON contains all required fields."""

    def test_metrics_file_written(self, tmp_path):
        """sentinel_metrics.json is written to state_dir."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.record_breach("BTC:s56:100:primary")
        metrics.record_wick_filtered("BTC:s56:100:primary")
        metrics.record_exit_triggered("BTC:s56:100:primary")
        metrics.record_ws_reconnection()
        metrics.record_message()

        metrics.write_metrics()

        metrics_file = tmp_path / "sentinel_metrics.json"
        assert metrics_file.exists()

    def test_metrics_contains_required_fields(self, tmp_path):
        """Metrics JSON has all required fields from AC8."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.record_breach("BTC:s56:100:primary")
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        required_fields = [
            "total_breaches",
            "wick_filtered",
            "exits_triggered",
            "ws_reconnections",
            "avg_confirmation_time_s",
            "messages_per_second",
            "uptime_pct",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_breach_count_increments(self, tmp_path):
        """total_breaches increments on each breach."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.record_breach("BTC:s56:100:primary")
        metrics.record_breach("ETH:s60:100:primary")
        metrics.record_breach("SOL:s63:100:primary")
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        assert data["total_breaches"] == 3

    def test_wick_filtered_count(self, tmp_path):
        """wick_filtered count is accurate."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.record_wick_filtered("BTC:s56:100:primary")
        metrics.record_wick_filtered("ETH:s60:100:primary")
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        assert data["wick_filtered"] == 2

    def test_exits_triggered_count(self, tmp_path):
        """exits_triggered count is accurate."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.record_exit_triggered("BTC:s56:100:primary")
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        assert data["exits_triggered"] == 1

    def test_ws_reconnections_count(self, tmp_path):
        """ws_reconnections count is accurate."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.record_ws_reconnection()
        metrics.record_ws_reconnection()
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        assert data["ws_reconnections"] == 2

    def test_messages_per_second(self, tmp_path):
        """messages_per_second is a non-negative float."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        for _ in range(100):
            metrics.record_message()
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        assert isinstance(data["messages_per_second"], (int, float))
        assert data["messages_per_second"] >= 0

    def test_uptime_pct(self, tmp_path):
        """uptime_pct is between 0 and 100."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        metrics.write_metrics()

        data = json.loads((tmp_path / "sentinel_metrics.json").read_text())
        assert 0 <= data["uptime_pct"] <= 100


# ===================================================================
# Test: State consistency check -- position_id mismatch
# ===================================================================

class TestStateConsistencyCheck:
    """State consistency check detects mismatches between cache and stops.json."""

    def test_detects_position_mismatch(self, tmp_path):
        """Mismatch between cached position_ids and stops.json flagged."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        cached_ids = {"BTC:s56:100:primary", "ETH:s60:100:primary", "STALE:s99:50:primary"}
        stops_ids = {"BTC:s56:100:primary", "ETH:s60:100:primary"}

        divergences = metrics.check_consistency(cached_ids, stops_ids)
        assert len(divergences) >= 1
        assert "STALE:s99:50:primary" in divergences

    def test_no_divergence_when_matching(self, tmp_path):
        """No divergence when cached IDs match stops.json."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        ids = {"BTC:s56:100:primary", "ETH:s60:100:primary"}
        divergences = metrics.check_consistency(ids, ids)
        assert len(divergences) == 0


# ===================================================================
# Test: Stale entry purge
# ===================================================================

class TestStaleEntryPurge:
    """Stale entries purged when position closed by hourly cycle."""

    def test_stale_entries_identified(self, tmp_path):
        """Positions in cache but not in stops.json are identified as stale."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        cached_ids = {"BTC:s56:100:primary", "CLOSED:s56:50:primary"}
        stops_ids = {"BTC:s56:100:primary"}

        stale = metrics.check_consistency(cached_ids, stops_ids)
        assert "CLOSED:s56:50:primary" in stale

    def test_purge_returns_ids_to_remove(self, tmp_path):
        """Consistency check returns the IDs that should be purged."""
        metrics = SentinelMetrics(state_dir=tmp_path)
        cached = {"A", "B", "C"}
        current = {"A", "B"}

        stale = metrics.check_consistency(cached, current)
        assert "C" in stale
        assert "A" not in stale
        assert "B" not in stale
