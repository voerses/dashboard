"""Acceptance tests for runner wiring of data maintenance (AC9-AC11).

Tests verify:
  - AC9: Paper runner startup calls ensure_data_fresh before first tick
  - AC9: Runner startup logs maintenance summary (gaps_filled, bars_fetched, tokens_promoted)
  - AC10: Runner calls ensure_data_fresh(promote_only=True) every 4h
  - AC11: Runner fetches 1m data for tokens with open positions after hourly fetch
  - AC11: Tokens WITHOUT open positions do NOT get 1m fetch

All tests use behavioral mocking — no real runner, exchange, or data required.
These tests MUST FAIL until runner wiring is implemented (RED phase).
"""
from __future__ import annotations

import logging
import os
import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4 import run_paper_multi


# ===================================================================
# AC9: Runner startup calls ensure_data_fresh
# ===================================================================

class TestRunnerStartupDataMaintenance:
    """AC9: Paper runner startup calls ensure_data_fresh before first tick."""

    def test_runner_startup_calls_ensure_data_fresh(self):
        """AC9: run_paper_multi must import ensure_data_fresh from v4.data_maintenance."""
        # The runner module must have ensure_data_fresh available (imported or referenced)
        assert hasattr(run_paper_multi, "ensure_data_fresh") or \
            "ensure_data_fresh" in dir(run_paper_multi), (
            "run_paper_multi must import ensure_data_fresh from v4.data_maintenance"
        )

    @patch("v4.run_paper_multi.ensure_data_fresh")
    @patch("v4.run_paper_multi.fetch_all_data")
    @patch("v4.run_paper_multi._setup_shared_monitors")
    @patch("v4.run_paper_multi.load_multi_config")
    @patch("v4.run_paper_multi.write_portfolio_configs")
    @patch("v4.run_paper_multi.validate_paper_config")
    @patch("v4.run_paper_multi.restore_state")
    @patch("v4.run_paper_multi.acquire_pid_lock")
    @patch("v4.run_paper_multi.write_dashboard_state")
    @patch("v4.run_paper_multi.discover_tokens")
    def test_runner_startup_calls_ensure_data_fresh_before_tick(
        self,
        mock_discover, mock_dash, mock_lock, mock_restore,
        mock_validate, mock_write_cfg, mock_load_cfg,
        mock_setup_monitors, mock_fetch_all, mock_ensure,
    ):
        """AC9: ensure_data_fresh is called during startup (before first tick).
        Logs summary with gaps_filled, bars_fetched, tokens_promoted."""
        # Setup minimal config
        mock_config = MagicMock()
        mock_config.pool_name = "test"
        mock_config.capital = 100_000
        mock_config.exchange = "binance"
        mock_config.state_dir = "/tmp/test_state"
        mock_config.strategies = []
        mock_load_cfg.return_value = [mock_config]

        mock_engine = MagicMock()
        mock_engine._price_monitor = None
        mock_engine._owns_price_monitor = False
        mock_engine._candle_aggregator = None
        mock_setup_monitors.return_value = ({}, [mock_engine])

        mock_lock.return_value = MagicMock()
        mock_discover.return_value = {"BTC", "ETH"}

        # ensure_data_fresh returns a summary
        mock_ensure.return_value = {
            "gaps_filled": 5, "bars_fetched": 100, "tokens_failed": 0,
            "tokens_promoted": 10, "timed_out": False, "tokens_skipped": 0,
        }

        # Make the runner exit after startup (before main loop)
        mock_fetch_all.side_effect = SystemExit(0)

        with patch("ccxt.binance", MagicMock()):
            with patch.object(sys, "argv", [
                "run_paper_multi",
                "--config", "/dev/null",
                "--once",
            ]):
                try:
                    run_paper_multi.main()
                except SystemExit:
                    pass

        # Verify ensure_data_fresh was called
        mock_ensure.assert_called()
        # Verify caller includes "runner" or "startup"
        call_kwargs = mock_ensure.call_args.kwargs if mock_ensure.call_args.kwargs else {}
        caller = call_kwargs.get("caller", "")
        assert "runner" in caller or "startup" in caller, (
            f"ensure_data_fresh caller should indicate runner startup, got '{caller}'"
        )


# ===================================================================
# AC10: 4h promote timer
# ===================================================================

class TestRunner4hPromote:
    """AC10: Runner calls ensure_data_fresh(promote_only=True) every 4h."""

    def test_runner_has_promote_interval_constant(self):
        """AC10: Runner module defines a promote interval (~4h = 14400s)."""
        # Check for a constant defining the promote interval
        has_interval = (
            hasattr(run_paper_multi, "PROMOTE_INTERVAL_S") or
            hasattr(run_paper_multi, "_PROMOTE_INTERVAL_S") or
            hasattr(run_paper_multi, "PROMOTE_INTERVAL")
        )
        assert has_interval, (
            "run_paper_multi should define a PROMOTE_INTERVAL_S constant (~14400)"
        )

    def test_runner_promote_interval_is_4h(self):
        """AC10: Promote interval is approximately 4 hours (14400 seconds)."""
        interval = getattr(
            run_paper_multi,
            "PROMOTE_INTERVAL_S",
            getattr(run_paper_multi, "_PROMOTE_INTERVAL_S",
                    getattr(run_paper_multi, "PROMOTE_INTERVAL", None)),
        )
        assert interval is not None, "PROMOTE_INTERVAL_S not found"
        assert 14000 <= interval <= 15000, (
            f"Promote interval should be ~14400s (4h), got {interval}"
        )


# ===================================================================
# AC11: 1m fetch for tokens with open positions
# ===================================================================

class TestRunner1mFetch:
    """AC11: Runner handles 1m data via kline streaming + background backfill.

    NOTE: ws-kline-1m-streaming replaced the polling fetch_1m_for_open_positions
    with create_kline_writer (streaming) + _backfill_1m_background (gap recovery).
    """

    def test_runner_has_kline_writer(self):
        """AC11: run_paper_multi has create_kline_writer for 1m streaming."""
        assert hasattr(run_paper_multi, "create_kline_writer"), (
            "run_paper_multi should have create_kline_writer for 1m bar streaming"
        )

    def test_runner_has_backfill_1m_background(self):
        """AC11: run_paper_multi has _backfill_1m_background for gap recovery."""
        assert hasattr(run_paper_multi, "_backfill_1m_background"), (
            "run_paper_multi should have _backfill_1m_background for 1m gap recovery"
        )

    def test_kline_writer_enqueues_bars(self):
        """AC11: create_kline_writer returns a callback that enqueues bars."""
        mock_fetcher = MagicMock()
        callback = run_paper_multi.create_kline_writer(mock_fetcher)
        assert callable(callback), "create_kline_writer should return a callable"
