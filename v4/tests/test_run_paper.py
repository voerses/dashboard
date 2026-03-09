"""Acceptance tests for Task 8: CLI Runner (run_paper.py).

Tests verify:
  - --once mode: single tick then exit
  - --status mode: prints current state
  - --config loads specified JSON file

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until run_paper.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import pytest

from v4.run_paper import main, parse_args


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_test_config(tmpdir: str) -> str:
    """Write a minimal config JSON for CLI tests."""
    config = {
        "initial_capital": 100_000.0,
        "mode": "pool",
        "strategies": [
            {"strategy_id": "s56", "weight": 1.0, "market": "perp", "max_positions": 5},
        ],
        "max_portfolio_positions": 10,
        "concentration_limit": 0.10,
        "exchange": "binance",
        "seed": 42,
    }
    path = os.path.join(tmpdir, "test_config.json")
    with open(path, "w") as f:
        json.dump(config, f)
    return path


# ===================================================================
# Test: --once mode
# ===================================================================

class TestOnceMode:
    """--once mode: process a single tick, then exit."""

    def test_parse_once_flag(self):
        """Argument parser recognizes --once flag."""
        args = parse_args(["--config", "config.json", "--once"])
        assert args.once is True

    def test_once_mode_exits_after_one_tick(self):
        """In --once mode, main() processes exactly one tick then returns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_test_config(tmpdir)

            tick_count = 0

            with patch("v4.run_paper.PaperPortfolioEngine") as MockEngine:
                instance = MockEngine.return_value
                instance.tick_counter = 0

                def mock_tick():
                    nonlocal tick_count
                    tick_count += 1
                    from v4.paper_engine import TickResult
                    return TickResult(skipped=False, error=None)

                instance.tick = mock_tick

                main(["--config", config_path, "--once"])

            assert tick_count == 1


# ===================================================================
# Test: --status mode
# ===================================================================

class TestStatusMode:
    """--status mode: print current state and exit."""

    def test_parse_status_flag(self):
        """Argument parser recognizes --status flag."""
        args = parse_args(["--config", "config.json", "--status"])
        assert args.status is True

    def test_status_mode_prints_state(self, capsys):
        """In --status mode, main() prints state information and returns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_test_config(tmpdir)

            with patch("v4.run_paper.PaperPortfolioEngine") as MockEngine:
                instance = MockEngine.return_value
                instance.tick_counter = 42
                instance.state = MagicMock()
                instance.state.portfolio_equity = 215_000.0
                instance.state.position_manager = MagicMock()
                instance.state.position_manager.total_open.return_value = 5
                instance.state.realized_pnl = 15_000.0
                instance.state.total_fees = 500.0
                instance.state.total_funding = -100.0
                instance.last_timestamp = "2026-03-09T20:00:00Z"

                main(["--config", config_path, "--status"])

            captured = capsys.readouterr()
            # Should print equity, open positions, tick counter
            assert "215000" in captured.out or "215,000" in captured.out
            assert "42" in captured.out or "tick" in captured.out.lower()


# ===================================================================
# Test: --config loads specified JSON file
# ===================================================================

class TestConfigLoading:
    """--config flag specifies the JSON config file path."""

    def test_parse_config_flag(self):
        """Argument parser recognizes --config flag."""
        args = parse_args(["--config", "/path/to/config.json"])
        assert args.config == "/path/to/config.json"

    def test_config_required(self):
        """--config is required."""
        with pytest.raises(SystemExit):
            parse_args([])

    def test_config_file_loaded(self):
        """The specified config file is passed to PaperPortfolioEngine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_test_config(tmpdir)

            with patch("v4.run_paper.PaperPortfolioEngine") as MockEngine:
                instance = MockEngine.return_value
                instance.tick = MagicMock(return_value=MagicMock(skipped=True))

                main(["--config", config_path, "--once"])

            # Verify the engine was initialized with the config path
            MockEngine.assert_called_once()
            call_args = MockEngine.call_args
            assert config_path in str(call_args)
