"""Acceptance tests for observability (AC23-25).

Tests verify:
  - AC23: Peak RSS memory reported in TickResult
  - AC24: Tick logs include all required fields
  - AC25: Heartbeat JSON written with correct schema

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until observability is implemented (RED phase).
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
sys.path.insert(0, str(_project_root / "v4"))

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
# Test: AC23 — Peak RSS memory reported in TickResult
# ===================================================================

class TestPeakRSSMemory:
    """AC23: Peak RSS memory reported in TickResult."""

    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_tick_result_has_peak_rss(self, mock_precompute):
        """After a tick, TickResult.peak_rss_mb is a positive float."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []

            result = engine.tick()

            assert hasattr(result, "peak_rss_mb")
            assert isinstance(result.peak_rss_mb, float)
            assert result.peak_rss_mb > 0, "Peak RSS should be positive after a tick"


# ===================================================================
# Test: AC24 — Tick logs include all required fields
# ===================================================================

class TestTickLogging:
    """AC24: Tick logs include all required fields."""

    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_tick_result_has_required_fields(self, mock_precompute):
        """TickResult includes timestamp, entries, exits, open_positions,
        portfolio_equity, and processing_time_s."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []

            result = engine.tick()

            assert hasattr(result, "timestamp")
            assert hasattr(result, "entries")
            assert hasattr(result, "exits")
            assert hasattr(result, "open_positions")
            assert hasattr(result, "portfolio_equity")
            assert hasattr(result, "processing_time_s")
            assert result.processing_time_s >= 0

    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_tick_result_has_mark_to_market(self, mock_precompute):
        """TickResult includes mark_to_market_equity field per AC24."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []

            result = engine.tick()

            assert hasattr(result, "mark_to_market_equity")


# ===================================================================
# Test: AC25 — Heartbeat JSON written with correct schema
# ===================================================================

class TestHeartbeat:
    """AC25: Heartbeat JSON written with correct schema after each tick."""

    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_heartbeat_written_after_tick(self, mock_precompute):
        """After a tick, state_dir/heartbeat.json exists with correct schema."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []

            engine.tick()

            heartbeat_path = os.path.join(tmpdir, "heartbeat.json")
            assert os.path.exists(heartbeat_path), "heartbeat.json should exist after tick"

            with open(heartbeat_path) as f:
                hb = json.load(f)

            # Verify required fields per AC25
            assert "timestamp" in hb
            assert "tick_counter" in hb
            assert "open_positions" in hb
            assert "portfolio_equity" in hb
            assert "mark_to_market_equity" in hb
            assert "processing_time_s" in hb
            assert "errors" in hb

    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_heartbeat_tick_counter_matches(self, mock_precompute):
        """heartbeat.json tick_counter matches the engine's tick_counter."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []

            result = engine.tick()

            heartbeat_path = os.path.join(tmpdir, "heartbeat.json")
            with open(heartbeat_path) as f:
                hb = json.load(f)

            assert hb["tick_counter"] == result.tick_counter
