"""Acceptance tests for CLI Runner (run_paper_multi.py).

Tests verify:
  - --config flag is required and recognized
  - --once and --status flags are recognized
  - parse_args works correctly

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.run_paper_multi import parse_args


# ===================================================================
# Test: CLI argument parsing
# ===================================================================

class TestParseArgs:
    """Verify CLI argument parsing for run_paper_multi."""

    def test_parse_config_flag(self):
        """Argument parser recognizes --config flag."""
        args = parse_args(["--config", "/path/to/config.json"])
        assert args.config == "/path/to/config.json"

    def test_config_required(self):
        """--config is required."""
        with pytest.raises(SystemExit):
            parse_args([])

    def test_parse_once_flag(self):
        """Argument parser recognizes --once flag."""
        args = parse_args(["--config", "config.json", "--once"])
        assert args.once is True

    def test_parse_status_flag(self):
        """Argument parser recognizes --status flag."""
        args = parse_args(["--config", "config.json", "--status"])
        assert args.status is True
