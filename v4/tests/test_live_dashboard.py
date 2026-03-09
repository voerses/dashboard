"""Acceptance tests for dashboard integration (AC26-28).

Tests verify:
  - AC26: Dashboard generation triggered after tick
  - AC28: SIMS includes open positions with status="open", current_price, unrealized_pnl
  - AC28: Equity chart uses mark_to_market_equity from equity_history
  - AC27: Dashboard push controlled by config.dashboard_push

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until dashboard integration is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import pytest

from v4.paper_engine import PaperPortfolioEngine, TickResult
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position
from v4.simulator import SimulationState


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


def _make_open_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_price: float = 50_000.0,
    quantity: float = 0.1,
    direction: int = 1,
) -> Position:
    """Build an open Position for dashboard tests."""
    return Position(
        position_id=f"{token}:{strategy_id}:10:primary",
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=10,
        entry_price=entry_price,
        direction=direction,
        quantity=quantity * direction,
        margin_usd=5_000.0,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.5,
        trail_mult=3.0,
        target_mult=6.0,
        no_stop_bars=6,
        min_hold=12,
        max_hold=720,
        exit_regimes={4},
        convex_exit=False,
        rsi_exit_level=999.0,
        trail_schedule=None,
        stop_price=48_000.0,
        highest=52_000.0,
        lowest=49_000.0,
        initial_risk=2_000.0,
        cumulative_funding=0.0,
    )


# ===================================================================
# Test: AC26 — Dashboard generation triggered after tick
# ===================================================================

class TestDashboardTriggered:
    """AC26: Dashboard generation triggered after tick."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_dashboard_triggered_after_tick(self, mock_precompute, mock_discover):
        """After a successful tick, dashboard generation is invoked."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir, dashboard_push=False)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []
            engine.fetcher.fetch_funding_rates.return_value = []

            # Mock the dashboard trigger method
            engine._trigger_dashboard = MagicMock()

            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

            engine._trigger_dashboard.assert_called_once()


# ===================================================================
# Test: AC28 — SIMS includes open positions with open status
# ===================================================================

class TestSIMSOpenPositions:
    """AC28: SIMS dict includes open positions with status='open' fields."""

    def test_sims_includes_open_positions(self):
        """to_dashboard_sim includes open positions with status, current_price, unrealized_pnl.

        Note: the actual return key is 'all_trades' (paper_engine.py:677), not 'trades'.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)

            # Add an open position
            pos = _make_open_position(token="BTC", entry_price=50_000.0, quantity=0.1)
            engine.state.position_manager.open_position(pos)
            engine._last_known_prices["BTC"] = 55_000.0

            sims = engine.to_dashboard_sim()

            # Find the open position entry in SIMS — key is "all_trades"
            all_trades = sims.get("all_trades", [])
            open_trades = [t for t in all_trades if t.get("status") == "open"]
            assert len(open_trades) >= 1, \
                f"No open trades found in SIMS all_trades (got {len(all_trades)} total trades)"

            open_trade = open_trades[0]
            assert open_trade["status"] == "open"
            assert "current_price" in open_trade, "open trade missing 'current_price'"
            assert "unrealized_pnl" in open_trade, "open trade missing 'unrealized_pnl'"
            assert open_trade["current_price"] == pytest.approx(55_000.0)

    def test_sims_unrealized_pnl_correct(self):
        """Open position unrealized_pnl = quantity * (current - entry)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)

            pos = _make_open_position(token="ETH", entry_price=3_000.0, quantity=1.0)
            engine.state.position_manager.open_position(pos)
            engine._last_known_prices["ETH"] = 3_500.0

            sims = engine.to_dashboard_sim()

            all_trades = sims.get("all_trades", [])
            open_trades = [t for t in all_trades if t.get("status") == "open"]
            assert len(open_trades) >= 1, "No open trades found in SIMS"

            # Unrealized P&L = 1.0 * (3500 - 3000) = 500
            assert open_trades[0]["unrealized_pnl"] == pytest.approx(500.0)


# ===================================================================
# Test: AC28 — Equity chart uses mark_to_market_equity
# ===================================================================

class TestEquityChartMTM:
    """AC28: Equity chart uses mark_to_market_equity from equity_history."""

    def test_dashboard_equity_uses_mtm(self):
        """to_dashboard_sim equity_history entries use mark_to_market_equity values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.equity_history = [
                {"timestamp": "2025-01-15T10:00:00Z", "portfolio_equity": 200_000.0,
                 "mark_to_market_equity": 200_500.0},
                {"timestamp": "2025-01-15T11:00:00Z", "portfolio_equity": 200_100.0,
                 "mark_to_market_equity": 200_800.0},
            ]

            sims = engine.to_dashboard_sim()

            # The equity_history in SIMS must contain mark_to_market_equity values
            equity_data = sims.get("equity_history", [])
            assert len(equity_data) >= 2, \
                f"Expected at least 2 equity entries, got {len(equity_data)}"

            # Verify the entries contain mark_to_market_equity field
            for entry in equity_data:
                assert "mark_to_market_equity" in entry, \
                    f"equity_history entry missing 'mark_to_market_equity': {entry}"

            # The first entry should have MTM of 200500, not the realized-only 200000
            assert equity_data[0]["mark_to_market_equity"] == pytest.approx(200_500.0)
            assert equity_data[1]["mark_to_market_equity"] == pytest.approx(200_800.0)


# ===================================================================
# Test: AC27 — Dashboard push controlled by config
# ===================================================================

class TestDashboardPush:
    """AC27: Dashboard push controlled by config.dashboard_push."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_dashboard_push_when_enabled(self, mock_precompute, mock_discover):
        """When dashboard_push=True, dashboard push is triggered after tick."""
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir, dashboard_push=True)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []
            engine.fetcher.fetch_funding_rates.return_value = []

            engine._trigger_dashboard = MagicMock()

            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

            engine._trigger_dashboard.assert_called()

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_no_dashboard_push_when_disabled(self, mock_precompute, mock_discover):
        """When dashboard_push=False, dashboard push is NOT triggered.

        Dashboard may still be generated locally, but push behavior must not activate.
        """
        mock_precompute.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir, dashboard_push=False)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []
            engine.fetcher.fetch_funding_rates.return_value = []

            # Mock a _push_dashboard method that should NOT be called
            engine._push_dashboard = MagicMock()

            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

            # _push_dashboard must NOT be called when dashboard_push=False
            engine._push_dashboard.assert_not_called()
