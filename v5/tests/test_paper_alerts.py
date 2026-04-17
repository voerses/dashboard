"""Acceptance tests for Task 9: Alerts.

Tests verify:
  - Position open alert logged
  - Position close alert logged
  - Drawdown alert fires when equity drops > threshold from peak
  - Drawdown threshold is configurable
  - Drawdown repeated firing suppression (cooldown)
  - Consecutive failure alert after 3 failures
  - heartbeat.json updated each tick with correct fields
  - Heartbeat atomic write (valid JSON after each write)
  - Structured log file created with daily rotation naming
  - rejection_reasons structure persisted in log_tick
  - Delisting alert when position force-closed due to data disappearance

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until alerts are implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.paper_engine import PaperPortfolioEngine, AlertManager
from v5.paper_config import PaperConfig
from v5.config import StrategySpec
from v5.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alert_manager(
    tmpdir: str,
    drawdown_pct: float = 5.0,
) -> AlertManager:
    """Build an AlertManager for testing."""
    return AlertManager(
        state_dir=tmpdir,
        drawdown_alert_pct=drawdown_pct,
        webhook_url="",
    )


# ===================================================================
# Test: Position open alert logged
# ===================================================================

class TestPositionOpenAlert:
    """Alert fires when a new position is opened."""

    def test_position_open_alert(self):
        """Opening a position should produce a structured alert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.on_position_open(
                token="BTC",
                strategy_id="s56",
                direction=1,
                margin_usd=10_000.0,
                entry_price=68_000.0,
                leverage=1.0,
                timestamp="2026-03-09T20:00:00Z",
            )

            alerts = mgr.get_pending_alerts()
            assert len(alerts) >= 1
            alert = alerts[0]
            assert "BTC" in alert["message"] or alert["token"] == "BTC"
            assert alert["type"] == "position_open"


# ===================================================================
# Test: Position close alert logged
# ===================================================================

class TestPositionCloseAlert:
    """Alert fires when a position is closed."""

    def test_position_close_alert(self):
        """Closing a position should produce a structured alert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.on_position_close(
                token="BTC",
                strategy_id="s56",
                direction=1,
                pnl=500.0,
                exit_reason="target",
                hold_bars=25,
                timestamp="2026-03-09T20:00:00Z",
            )

            alerts = mgr.get_pending_alerts()
            assert len(alerts) >= 1
            alert = alerts[0]
            assert alert["type"] == "position_close"
            assert "target" in alert.get("exit_reason", "") or "target" in alert.get("message", "")


# ===================================================================
# Test: Drawdown alert fires when equity drops > threshold from peak
# ===================================================================

class TestDrawdownAlert:
    """Drawdown alert fires when equity drops > threshold% from peak (AC14)."""

    def test_drawdown_alert_fires(self):
        """5% drawdown from peak should trigger alert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=5.0)

            # Peak equity = $200k
            mgr.update_peak_equity(200_000.0)

            # Current equity = $189k (5.5% drawdown)
            fired = mgr.check_drawdown(189_000.0, "2026-03-09T20:00:00Z")

            assert fired is True
            alerts = mgr.get_pending_alerts()
            assert any(a["type"] == "drawdown" for a in alerts)

    def test_no_drawdown_alert_within_threshold(self):
        """4% drawdown (below 5% threshold) should NOT trigger alert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=5.0)

            mgr.update_peak_equity(200_000.0)

            # Current equity = $192k (4% drawdown)
            fired = mgr.check_drawdown(192_000.0, "2026-03-09T20:00:00Z")

            assert fired is False


# ===================================================================
# Test: Drawdown threshold is configurable
# ===================================================================

class TestDrawdownThresholdConfigurable:
    """Drawdown alert threshold comes from config (AC14)."""

    def test_custom_drawdown_threshold_10pct(self):
        """With 10% threshold, 8% drawdown should NOT fire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=10.0)

            mgr.update_peak_equity(200_000.0)

            fired = mgr.check_drawdown(184_000.0, "2026-03-09T20:00:00Z")  # 8% dd

            assert fired is False

    def test_custom_drawdown_threshold_10pct_exceeds(self):
        """With 10% threshold, 11% drawdown SHOULD fire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=10.0)

            mgr.update_peak_equity(200_000.0)

            fired = mgr.check_drawdown(178_000.0, "2026-03-09T20:00:00Z")  # 11% dd

            assert fired is True

    def test_custom_drawdown_threshold_2pct(self):
        """With 2% threshold, small drops trigger alert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=2.0)

            mgr.update_peak_equity(200_000.0)

            fired = mgr.check_drawdown(195_000.0, "2026-03-09T20:00:00Z")  # 2.5% dd

            assert fired is True


# ===================================================================
# M5: Drawdown repeated firing suppression
# ===================================================================

class TestDrawdownRepeatedFiringSuppression:
    """Drawdown alert should not fire repeatedly every tick while equity
    stays below threshold. After the first alert fires, subsequent calls
    to check_drawdown with equity still below threshold should be
    suppressed until equity recovers above the threshold (or a new,
    deeper drawdown level is reached)."""

    def test_drawdown_fires_once_then_suppressed(self):
        """First check_drawdown triggers alert. Second call with equity
        still below threshold should NOT fire again (suppressed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=5.0)

            mgr.update_peak_equity(200_000.0)

            # First trigger: 5.5% drawdown fires
            fired1 = mgr.check_drawdown(189_000.0, "2026-03-09T20:00:00Z")
            assert fired1 is True

            # Clear pending alerts to isolate second check
            _ = mgr.get_pending_alerts()

            # Second trigger: equity still below threshold (6% dd now)
            fired2 = mgr.check_drawdown(188_000.0, "2026-03-09T21:00:00Z")

            # Should be suppressed -- the alert already fired for this drawdown
            assert fired2 is False, (
                "Drawdown alert fired again while equity remained below threshold. "
                "Expected suppression after initial firing."
            )

    def test_drawdown_refires_after_recovery(self):
        """After equity recovers above threshold and then drops again,
        the alert should fire again."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir, drawdown_pct=5.0)

            mgr.update_peak_equity(200_000.0)

            # First drawdown: fires
            fired1 = mgr.check_drawdown(189_000.0, "2026-03-09T20:00:00Z")
            assert fired1 is True
            _ = mgr.get_pending_alerts()

            # Recovery: equity back above threshold
            mgr.update_peak_equity(200_000.0)
            mgr.check_drawdown(196_000.0, "2026-03-09T21:00:00Z")  # 2% dd, below threshold

            # New drawdown: should fire again
            fired3 = mgr.check_drawdown(189_000.0, "2026-03-09T22:00:00Z")
            assert fired3 is True


# ===================================================================
# Test: Consecutive failure alert after 3 failures
# ===================================================================

class TestConsecutiveFailureAlert:
    """Alert fires after 3 consecutive tick failures (AC14)."""

    def test_3_consecutive_failures_alert(self):
        """After 3 consecutive failures, a critical alert is generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.on_tick_failure("API timeout", "2026-03-09T20:00:00Z")
            mgr.on_tick_failure("API timeout", "2026-03-09T21:00:00Z")
            mgr.on_tick_failure("API timeout", "2026-03-09T22:00:00Z")

            alerts = mgr.get_pending_alerts()
            critical = [a for a in alerts if a.get("severity") == "critical"
                        or a.get("type") == "consecutive_failures"]
            assert len(critical) >= 1

    def test_success_resets_failure_counter(self):
        """A successful tick resets the consecutive failure counter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.on_tick_failure("fail", "2026-03-09T20:00:00Z")
            mgr.on_tick_failure("fail", "2026-03-09T21:00:00Z")
            mgr.on_tick_success("2026-03-09T22:00:00Z")
            mgr.on_tick_failure("fail", "2026-03-09T23:00:00Z")

            # Only 1 consecutive failure now (counter was reset)
            assert mgr.consecutive_failures == 1


# ===================================================================
# Test: heartbeat.json updated each tick
# ===================================================================

class TestHeartbeatFile:
    """heartbeat.json updated each tick with correct fields (AC18)."""

    def test_heartbeat_file_created(self):
        """heartbeat.json should be created after a tick."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.write_heartbeat(
                timestamp="2026-03-09T20:00:00Z",
                last_processed_bar="2026-03-09T19:00:00Z",
                open_positions=5,
                portfolio_equity=215_000.0,
                errors=[],
            )

            heartbeat_path = os.path.join(tmpdir, "heartbeat.json")
            assert os.path.exists(heartbeat_path)

            with open(heartbeat_path) as f:
                data = json.load(f)

            assert data["timestamp"] == "2026-03-09T20:00:00Z"
            assert data["last_processed_bar"] == "2026-03-09T19:00:00Z"
            assert data["open_positions"] == 5
            assert data["portfolio_equity"] == pytest.approx(215_000.0)
            assert data["errors"] == []

    def test_heartbeat_has_required_fields(self):
        """heartbeat.json must have: timestamp, last_processed_bar,
        open_positions, portfolio_equity, errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.write_heartbeat(
                timestamp="2026-03-09T20:00:00Z",
                last_processed_bar="2026-03-09T19:00:00Z",
                open_positions=3,
                portfolio_equity=200_000.0,
                errors=["timeout on BTC"],
            )

            with open(os.path.join(tmpdir, "heartbeat.json")) as f:
                data = json.load(f)

            required = ["timestamp", "last_processed_bar", "open_positions",
                        "portfolio_equity", "errors"]
            for field in required:
                assert field in data, f"heartbeat.json missing field: {field}"


# ===================================================================
# M6: Heartbeat atomic write test
# ===================================================================

class TestHeartbeatAtomicWrite:
    """heartbeat.json must be written atomically and always be valid JSON (AC9, AC18)."""

    def test_heartbeat_valid_json_after_write(self):
        """After write_heartbeat, the file must contain valid, parseable JSON.
        This verifies the atomic write pattern (write-to-temp, rename) produces
        a complete file -- not a partial write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            # Write multiple heartbeats in succession
            for i in range(5):
                mgr.write_heartbeat(
                    timestamp=f"2026-03-09T{20+i:02d}:00:00Z",
                    last_processed_bar=f"2026-03-09T{19+i:02d}:00:00Z",
                    open_positions=i,
                    portfolio_equity=200_000.0 + i * 1000,
                    errors=[],
                )

                # After each write, the file must be valid JSON
                heartbeat_path = os.path.join(tmpdir, "heartbeat.json")
                assert os.path.exists(heartbeat_path), (
                    f"heartbeat.json does not exist after write {i}"
                )
                with open(heartbeat_path) as f:
                    content = f.read()
                data = json.loads(content)  # Must not raise JSONDecodeError
                assert data["open_positions"] == i
                assert data["portfolio_equity"] == pytest.approx(200_000.0 + i * 1000)

    def test_heartbeat_overwrites_previous(self):
        """Each write_heartbeat fully replaces the previous content.
        The file should contain only the latest heartbeat, not appended data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.write_heartbeat(
                timestamp="2026-03-09T20:00:00Z",
                last_processed_bar="2026-03-09T19:00:00Z",
                open_positions=5,
                portfolio_equity=200_000.0,
                errors=[],
            )

            mgr.write_heartbeat(
                timestamp="2026-03-09T21:00:00Z",
                last_processed_bar="2026-03-09T20:00:00Z",
                open_positions=7,
                portfolio_equity=201_000.0,
                errors=[],
            )

            heartbeat_path = os.path.join(tmpdir, "heartbeat.json")
            with open(heartbeat_path) as f:
                data = json.load(f)

            # Should reflect the second write, not the first
            assert data["timestamp"] == "2026-03-09T21:00:00Z"
            assert data["open_positions"] == 7
            assert data["portfolio_equity"] == pytest.approx(201_000.0)


# ===================================================================
# Test: Structured log file with daily rotation naming
# ===================================================================

class TestStructuredLogging:
    """Structured logging to JSON lines with daily rotation (AC19)."""

    def test_log_file_created(self):
        """Structured log file should be created on first write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.log_tick(
                timestamp="2026-03-09T20:00:00Z",
                tick=42,
                entries_attempted=3,
                entries_accepted=2,
                entries_rejected=1,
                rejection_reasons={"min_size": 1},
                exits_triggered=1,
                exit_reasons={"target": 1},
                equity_snapshot={"portfolio_equity": 215_000.0},
            )

            log_dir = os.path.join(tmpdir, "logs")
            assert os.path.isdir(log_dir)

    def test_log_file_daily_rotation_naming(self):
        """Log files should be named with daily rotation: paper_engine_YYYY-MM-DD.jsonl."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.log_tick(
                timestamp="2026-03-09T20:00:00Z",
                tick=42,
                entries_attempted=0,
                entries_accepted=0,
                entries_rejected=0,
                rejection_reasons={},
                exits_triggered=0,
                exit_reasons={},
                equity_snapshot={},
            )

            log_dir = os.path.join(tmpdir, "logs")
            log_files = os.listdir(log_dir)
            assert any("2026-03-09" in f for f in log_files)
            assert any(f.endswith(".jsonl") for f in log_files)

    def test_log_entry_is_valid_json(self):
        """Each log entry should be valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.log_tick(
                timestamp="2026-03-09T20:00:00Z",
                tick=42,
                entries_attempted=1,
                entries_accepted=1,
                entries_rejected=0,
                rejection_reasons={},
                exits_triggered=0,
                exit_reasons={},
                equity_snapshot={"portfolio_equity": 200_000.0},
            )

            log_dir = os.path.join(tmpdir, "logs")
            log_files = [f for f in os.listdir(log_dir) if f.endswith(".jsonl")]
            assert len(log_files) >= 1

            with open(os.path.join(log_dir, log_files[0])) as f:
                for line in f:
                    data = json.loads(line)  # must not raise
                    assert "timestamp" in data
                    assert "tick" in data


# ===================================================================
# M4: rejection_reasons structure in log_tick
# ===================================================================

class TestLogTickRejectionReasons:
    """log_tick must persist rejection_reasons dict in the JSON log entry (AC19)."""

    def test_rejection_reasons_persisted_in_log(self):
        """Call log_tick with rejection_reasons dict. Parse the log file.
        Verify the rejection_reasons field matches the input dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            rejection_reasons = {"min_size": 3, "concentration": 1}

            mgr.log_tick(
                timestamp="2026-03-09T20:00:00Z",
                tick=42,
                entries_attempted=5,
                entries_accepted=1,
                entries_rejected=4,
                rejection_reasons=rejection_reasons,
                exits_triggered=0,
                exit_reasons={},
                equity_snapshot={"portfolio_equity": 200_000.0},
            )

            log_dir = os.path.join(tmpdir, "logs")
            log_files = [f for f in os.listdir(log_dir) if f.endswith(".jsonl")]
            assert len(log_files) >= 1

            with open(os.path.join(log_dir, log_files[0])) as f:
                lines = f.readlines()

            assert len(lines) >= 1
            data = json.loads(lines[-1])

            assert "rejection_reasons" in data, (
                "Log entry missing 'rejection_reasons' field"
            )
            assert data["rejection_reasons"]["min_size"] == 3
            assert data["rejection_reasons"]["concentration"] == 1


# ===================================================================
# M12: Delisting alert test
# ===================================================================

class TestDelistingAlert:
    """When a position is force-closed because the token's data has
    disappeared (delisted), a delisting-specific alert must be
    generated (AC14, AC15b)."""

    def test_delisting_alert_on_force_close(self):
        """Force-close a position due to token data disappearing.
        Verify a delisting-specific alert is generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.on_delisting(
                token="LUNA",
                strategy_id="s56",
                position_id="LUNA:s56:10:primary",
                last_price=0.50,
                timestamp="2026-03-09T20:00:00Z",
            )

            alerts = mgr.get_pending_alerts()
            assert len(alerts) >= 1

            delisting_alerts = [
                a for a in alerts
                if a.get("type") == "delisting"
                or a.get("alert_type") == "delisting"
                or "delist" in a.get("type", "").lower()
            ]
            assert len(delisting_alerts) >= 1, (
                f"Expected a delisting alert but got types: "
                f"{[a.get('type') for a in alerts]}"
            )

            alert = delisting_alerts[0]
            # Alert should reference the token
            assert "LUNA" in alert.get("token", "") or "LUNA" in alert.get("message", "")

    def test_delisting_alert_has_severity_warning(self):
        """Delisting alert should have warning severity (AC14 mentions
        'warning alert is fired')."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _make_alert_manager(tmpdir)

            mgr.on_delisting(
                token="FTT",
                strategy_id="s57",
                position_id="FTT:s57:20:primary",
                last_price=1.25,
                timestamp="2026-03-09T20:00:00Z",
            )

            alerts = mgr.get_pending_alerts()
            delisting_alerts = [
                a for a in alerts
                if a.get("type") == "delisting"
                or a.get("alert_type") == "delisting"
                or "delist" in a.get("type", "").lower()
            ]
            assert len(delisting_alerts) >= 1
            alert = delisting_alerts[0]
            assert alert.get("severity") == "warning", (
                f"Delisting alert severity should be 'warning', got: {alert.get('severity')}"
            )
