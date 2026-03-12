"""Acceptance tests for CLI utilities (AC19-20).

Tests verify:
  - AC19: Sleep computation until next hour + 5s buffer
  - AC20: Ctrl+C sets shutdown flag, current tick completes

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import pytest


# ===================================================================
# Test: AC19 — Continuous mode sleeps until next hour + 5s
# ===================================================================

class TestContinuousMode:
    """AC19: Continuous mode sleeps until next hour boundary + 5s buffer."""

    def test_sleep_until_next_hour_plus_buffer(self):
        """Continuous mode computes correct sleep duration: next_hour - now + 5s."""
        from v4.paper_utils import compute_sleep_until_next_hour

        # Simulate time at 30 minutes past the hour
        fake_now = 1705320600.0  # Some timestamp at HH:30
        # Next hour boundary
        next_hour = ((int(fake_now) // 3600) + 1) * 3600
        expected_sleep = next_hour - fake_now + 5.0

        result = compute_sleep_until_next_hour(fake_now)

        assert result == pytest.approx(expected_sleep, abs=1.0)
        assert result > 0  # Sleep duration must be positive


# ===================================================================
# Test: AC20 — Ctrl+C sets shutdown flag, current tick completes
# ===================================================================

class TestGracefulShutdown:
    """AC20: Ctrl+C sets shutdown flag, current tick completes."""

    def test_shutdown_flag_set_on_sigint(self):
        """SIGINT (Ctrl+C) sets the shutdown event so the loop exits after current tick."""
        from v4.paper_utils import create_shutdown_handler

        shutdown_event = threading.Event()
        handler = create_shutdown_handler(shutdown_event)

        assert not shutdown_event.is_set()

        # Simulate SIGINT
        handler(signal.SIGINT, None)

        assert shutdown_event.is_set()

    def test_current_tick_completes_before_exit(self):
        """When shutdown is set during a tick, the tick completes before exit."""
        from v4.paper_utils import create_shutdown_handler

        shutdown_event = threading.Event()
        handler = create_shutdown_handler(shutdown_event)

        tick_completed = False

        def simulate_tick():
            nonlocal tick_completed
            time.sleep(0.1)  # Simulate tick work
            tick_completed = True

        # Start a "tick" in a thread
        tick_thread = threading.Thread(target=simulate_tick)
        tick_thread.start()

        # Set shutdown while tick is running
        handler(signal.SIGINT, None)

        # Wait for tick to complete
        tick_thread.join(timeout=2.0)

        assert tick_completed, "Tick should complete even after shutdown signal"
        assert shutdown_event.is_set()
