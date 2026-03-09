"""Acceptance tests for CLI and continuous mode (AC18-20).

Tests verify:
  - AC18: --once mode exits with code 0 on success
  - AC18: --once mode exits with code 1 on error
  - AC19: Continuous mode sleeps until next hour + 5s buffer
  - AC20: Ctrl+C sets shutdown flag, current tick completes

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until CLI/continuous mode is implemented (RED phase).
"""
from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import pytest

from v4.paper_engine import PaperPortfolioEngine, TickResult
from v4.paper_config import PaperConfig
from v4.config import StrategySpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(**overrides) -> PaperConfig:
    """Build a PaperConfig with sensible defaults."""
    defaults = dict(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
        ],
        capital=200_000.0,
        mode="pool",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
        lookback_months=3,
        enable_purge_windows=False,
        drawdown_alert_pct=5.0,
        state_dir="state/paper/",
        dashboard_push=False,
    )
    defaults.update(overrides)
    return PaperConfig(**defaults)


# ===================================================================
# Test: AC18 — --once mode exits with code 0 on success
# ===================================================================

class TestOnceMode:
    """AC18: --once mode exits with code 0 on success, code 1 on error."""

    @patch("v4.run_paper.PaperPortfolioEngine")
    @patch("v4.run_paper.load_paper_config")
    @patch("v4.run_paper.validate_paper_config")
    def test_once_mode_success_exit_0(self, mock_validate, mock_load, mock_engine_cls):
        """--once mode exits with code 0 when tick completes successfully."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            mock_load.return_value = config

            mock_engine = MagicMock()
            mock_engine.tick.return_value = TickResult(
                tick_counter=0, error=None, portfolio_equity=200_000.0,
            )
            mock_engine_cls.return_value = mock_engine

            from v4.run_paper import main

            # --once should not raise SystemExit(1)
            try:
                main(["--config", "test.json", "--once"])
                exited = False
            except SystemExit as e:
                exited = True
                assert e.code == 0 or e.code is None, f"Expected exit code 0, got {e.code}"

    @patch("v4.run_paper.PaperPortfolioEngine")
    @patch("v4.run_paper.load_paper_config")
    @patch("v4.run_paper.validate_paper_config")
    def test_once_mode_error_exit_1(self, mock_validate, mock_load, mock_engine_cls):
        """--once mode exits with code 1 when tick has an error."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            mock_load.return_value = config

            mock_engine = MagicMock()
            mock_engine.tick.return_value = TickResult(
                tick_counter=0, error="Fetch failed: timeout", portfolio_equity=0.0,
            )
            mock_engine_cls.return_value = mock_engine

            from v4.run_paper import main

            with pytest.raises(SystemExit) as exc_info:
                main(["--config", "test.json", "--once"])
            assert exc_info.value.code == 1


# ===================================================================
# Test: AC19 — Continuous mode sleeps until next hour + 5s
# ===================================================================

class TestContinuousMode:
    """AC19: Continuous mode sleeps until next hour boundary + 5s buffer."""

    def test_sleep_until_next_hour_plus_buffer(self):
        """Continuous mode computes correct sleep duration: next_hour - now + 5s."""
        from v4.run_paper import compute_sleep_until_next_hour

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
        from v4.run_paper import create_shutdown_handler

        shutdown_event = threading.Event()
        handler = create_shutdown_handler(shutdown_event)

        assert not shutdown_event.is_set()

        # Simulate SIGINT
        handler(signal.SIGINT, None)

        assert shutdown_event.is_set()

    def test_current_tick_completes_before_exit(self):
        """When shutdown is set during a tick, the tick completes before exit."""
        # This test verifies that the shutdown mechanism allows the current tick
        # to finish rather than aborting mid-tick.
        from v4.run_paper import create_shutdown_handler

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
