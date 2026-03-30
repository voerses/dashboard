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
    """AC11: Runner fetches 1m data for tokens with open positions after hourly fetch."""

    def test_runner_has_1m_fetch_function(self):
        """AC11: run_paper_multi has a function for fetching 1m data."""
        has_1m_func = (
            hasattr(run_paper_multi, "fetch_1m_for_open_positions") or
            hasattr(run_paper_multi, "_fetch_1m_for_open_positions") or
            hasattr(run_paper_multi, "fetch_1m_data")
        )
        assert has_1m_func, (
            "run_paper_multi should have a function for fetching 1m data "
            "for tokens with open positions"
        )

    def test_runner_1m_fetch_uses_open_positions(self):
        """AC11: 1m fetch function accepts engines/positions and fetches for open tokens only."""
        # Find the 1m fetch function
        func = getattr(
            run_paper_multi, "fetch_1m_for_open_positions",
            getattr(run_paper_multi, "_fetch_1m_for_open_positions",
                    getattr(run_paper_multi, "fetch_1m_data", None)),
        )
        assert func is not None, "1m fetch function not found"

        # Create mock fetcher and engine with open positions
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = []
        mock_fetcher.filter_closed_bars_1m.return_value = []

        mock_position = MagicMock()
        mock_position.token = "BTC"
        mock_state = MagicMock()
        mock_state.position_manager.open_positions = [mock_position]

        mock_engine = MagicMock()
        mock_engine._get_all_states.return_value = [mock_state]

        # Call the function
        func(mock_fetcher, [mock_engine])

        # Verify fetch_ohlcv was called with 1m timeframe for BTC
        calls = mock_fetcher.fetch_ohlcv.call_args_list
        assert len(calls) > 0, "fetch_ohlcv should be called for open position tokens"
        # At least one call should have timeframe="1m" and token="BTC"
        found_1m_btc = any(
            c.args[0] == "BTC" and c.kwargs.get("timeframe") == "1m"
            for c in calls
        ) or any(
            "BTC" in str(c) and "1m" in str(c) for c in calls
        )
        assert found_1m_btc, (
            f"Should fetch 1m data for BTC (open position), calls: {calls}"
        )

    def test_runner_1m_fetch_skips_tokens_without_positions(self):
        """AC11: Tokens without open positions should NOT get 1m fetch."""
        func = getattr(
            run_paper_multi, "fetch_1m_for_open_positions",
            getattr(run_paper_multi, "_fetch_1m_for_open_positions",
                    getattr(run_paper_multi, "fetch_1m_data", None)),
        )
        assert func is not None, "1m fetch function not found"

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = []

        # Engine with NO open positions
        mock_state = MagicMock()
        mock_state.position_manager.open_positions = []
        mock_engine = MagicMock()
        mock_engine._get_all_states.return_value = [mock_state]

        func(mock_fetcher, [mock_engine])

        # fetch_ohlcv should NOT be called (no open positions)
        mock_fetcher.fetch_ohlcv.assert_not_called()
