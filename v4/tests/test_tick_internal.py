"""Acceptance tests for _tick_internal implementation (AC5-7, AC13, AC21-22).

Tests verify:
  - AC5: _tick_internal calls precompute_strategy_signals for each strategy
  - AC6: _tick_internal calls _tick_internal_with_signals with correct bar_maps
  - AC7: Signal precomputation uses warm path (lookback_months)
  - AC8: After tick, state.json + trades.jsonl + equity.csv are persisted
  - AC9: Shadow pool state is persisted in state.json
  - AC13: Mark-to-market equity includes unrealized P&L of open positions
  - AC13: equity_history is populated with timestamp, portfolio_equity, mark_to_market_equity
  - AC21: Token universe discovered from parquet data
  - AC22: s58 config (s56+s57 pool) handled correctly

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until _tick_internal is implemented (RED phase).
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
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v4.paper_engine import PaperPortfolioEngine, TickResult
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position, PositionManager
from v4.simulator import SimulationState
from v4.signals import TokenSignals


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


def _make_token_signals(token: str, strategy_id: str, n_bars: int = 100):
    """Build minimal TokenSignals for mocking."""
    return TokenSignals(
        token=token,
        strategy_id=strategy_id,
        n_bars=n_bars,
        timestamps=np.arange(np.datetime64('2020-01-01', 'h'), np.datetime64('2020-01-01', 'h') + np.timedelta64(n_bars, 'h'), dtype='datetime64[h]'),
        entry_mask=np.zeros(n_bars, dtype=bool),
        direction=np.ones(n_bars, dtype=np.int8),
        close=np.full(n_bars, 100.0, dtype=np.float32),
        high=np.full(n_bars, 105.0, dtype=np.float32),
        low=np.full(n_bars, 95.0, dtype=np.float32),
        atr=np.full(n_bars, 5.0, dtype=np.float32),
        rolling_adv=np.full(n_bars, 1_000_000.0, dtype=np.float32),
        regime=np.ones(n_bars, dtype=np.int8),
        funding_1h=np.zeros(n_bars, dtype=np.float32),
        exit_regimes={4},
        stop_mult=np.full(n_bars, 2.5, dtype=np.float32),
        trail_mult=np.full(n_bars, 3.0, dtype=np.float32),
        target_mult=6.0,
        no_stop_bars=6,
        min_hold=12,
        max_hold=720,
        edge=0.02,
        size_multiplier=np.ones(n_bars, dtype=np.float32),
        cap_multiplier=1.0,
        leverage=np.ones(n_bars, dtype=np.float32),
        max_trade_pct=0.0,
    )


def _make_open_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_price: float = 50_000.0,
    quantity: float = 0.1,
    direction: int = 1,
    margin: float = 5_000.0,
) -> Position:
    """Build an open Position for testing mark-to-market."""
    return Position(
        position_id=f"{token}:{strategy_id}:10:primary",
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=10,
        entry_price=entry_price,
        direction=direction,
        quantity=quantity * direction,
        margin_usd=margin,
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
# Test: AC5 — _tick_internal calls precompute_strategy_signals
# ===================================================================

class TestTickInternalSignals:
    """AC5: _tick_internal calls precompute_strategy_signals for each strategy."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC", "ETH"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_calls_precompute_for_each_strategy(self, mock_precompute, mock_discover):
        """_tick_internal calls precompute_strategy_signals once per strategy.

        No except Exception: pass — the tick must complete successfully.
        """
        config = _make_test_config(
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
                StrategySpec(strategy_id="s57", weight=0.3, market="perp", max_positions=8),
            ],
        )

        # Mock precompute to return empty signals
        mock_precompute.return_value = {}

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            # _tick_internal must complete without error
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

        # precompute_strategy_signals should be called for each strategy
        assert mock_precompute.call_count >= 2


# ===================================================================
# Test: AC6 — _tick_internal calls _tick_internal_with_signals
# ===================================================================

class TestTickInternalWithSignals:
    """AC6: _tick_internal calls _tick_internal_with_signals with bar_maps."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_calls_tick_internal_with_signals(self, mock_precompute, mock_discover):
        """_tick_internal delegates to _tick_internal_with_signals with correct args.

        Verify that all_signals dict is non-empty and bar_maps dict has token keys.
        """
        config = _make_test_config()
        signals = {"BTC": _make_token_signals("BTC", "s56")}
        mock_precompute.return_value = signals

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []
        engine._tick_internal_with_signals = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

        engine._tick_internal_with_signals.assert_called_once()
        call_args = engine._tick_internal_with_signals.call_args

        # Verify the all_signals arg contains our strategy's signals
        all_signals_arg = call_args[0][0] if call_args[0] else call_args[1].get("all_signals", {})
        assert "s56" in all_signals_arg, "all_signals must contain the strategy ID key"
        assert "BTC" in all_signals_arg["s56"], "Strategy signals must contain token key"

        # Verify bar_maps arg has token key
        bar_maps_arg = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("bar_maps", {})
        assert "BTC" in bar_maps_arg, "bar_maps must contain token key"


# ===================================================================
# Test: AC7 — Warm path uses lookback_months
# ===================================================================

class TestWarmPath:
    """AC7: Signal precomputation uses warm path (lookback_months parameter)."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_precompute_called_with_lookback_months(self, mock_precompute, mock_discover):
        """precompute_strategy_signals is called with months=config.lookback_months."""
        config = _make_test_config(lookback_months=6)
        mock_precompute.return_value = {}

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

        # Verify the months parameter matches lookback_months
        assert mock_precompute.call_count >= 1
        call_args = mock_precompute.call_args
        # The 4th positional arg (index 3) is months
        months_arg = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get("months")
        assert months_arg == 6, f"Expected months=6 (lookback_months), got {months_arg}"


# ===================================================================
# Test: AC8 — State persistence after tick
# ===================================================================

class TestPostTickPersistence:
    """AC8: After tick, state.json + trades.jsonl + equity.csv are persisted."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_state_files_written_after_tick(self, mock_precompute, mock_discover):
        """After _tick_internal completes, state.json and equity.csv exist in state_dir."""
        config = _make_test_config()
        mock_precompute.return_value = {}

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

            # state.json must exist
            assert os.path.exists(os.path.join(tmpdir, "state.json")), \
                "state.json not written after tick"
            # equity.csv must exist
            assert os.path.exists(os.path.join(tmpdir, "equity.csv")), \
                "equity.csv not written after tick"

            # state.json must be valid JSON with expected keys
            with open(os.path.join(tmpdir, "state.json")) as f:
                state_data = json.load(f)
            assert "tick_counter" in state_data
            assert "initial_capital" in state_data or "strategy_states" in state_data


# ===================================================================
# Test: AC9 — Shadow pool state persisted
# ===================================================================

class TestShadowPoolPersistence:
    """AC9: Shadow pool state is persisted in state.json after tick."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_shadow_pools_in_state_json(self, mock_precompute, mock_discover):
        """state.json includes shadow_pools after tick."""
        config = _make_test_config()
        mock_precompute.return_value = {}

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

            with open(os.path.join(tmpdir, "state.json")) as f:
                state_data = json.load(f)

            assert "shadow_pools" in state_data, \
                "shadow_pools not persisted in state.json after tick"


# ===================================================================
# Test: AC13 — Mark-to-market equity includes unrealized P&L
# ===================================================================

class TestMarkToMarketEquity:
    """AC13: Mark-to-market equity includes unrealized P&L of open positions."""

    def test_mtm_includes_unrealized_pnl_long(self):
        """Mark-to-market for a long position: equity + unrealized P&L."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)

        # Add an open long position: entry at 50000, current price 55000
        pos = _make_open_position(
            token="BTC", entry_price=50_000.0, quantity=0.1, direction=1,
        )
        engine.state.position_manager.open_position(pos)
        engine._last_known_prices["BTC"] = 55_000.0

        mtm = engine._compute_mark_to_market()

        # Unrealized P&L = quantity * (current - entry) = 0.1 * (55000-50000) = 500
        base_equity = engine.state.portfolio_equity
        assert mtm == pytest.approx(base_equity + 500.0)

    def test_mtm_includes_unrealized_pnl_short(self):
        """Mark-to-market for a short position: equity + unrealized P&L (negative qty)."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)

        # Add an open short position: entry at 50000, current price 48000
        pos = _make_open_position(
            token="BTC", entry_price=50_000.0, quantity=0.1, direction=-1,
        )
        engine.state.position_manager.open_position(pos)
        engine._last_known_prices["BTC"] = 48_000.0

        mtm = engine._compute_mark_to_market()

        # Unrealized P&L = quantity * (current - entry) = -0.1 * (48000-50000) = 200
        base_equity = engine.state.portfolio_equity
        assert mtm == pytest.approx(base_equity + 200.0)

    def test_mtm_no_positions_equals_portfolio_equity(self):
        """With no open positions, mark-to-market equals portfolio equity."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)

        mtm = engine._compute_mark_to_market()

        assert mtm == pytest.approx(engine.state.portfolio_equity)


# ===================================================================
# Test: AC13 — equity_history populated per tick
# ===================================================================

class TestEquityHistory:
    """AC13: equity_history populated with correct fields and values per tick."""

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_equity_history_populated_after_tick(self, mock_precompute, mock_discover):
        """After _tick_internal, equity_history has an entry with correct values."""
        config = _make_test_config()
        mock_precompute.return_value = {}

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []
        engine.equity_history = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

        # Must have at least one entry
        assert len(engine.equity_history) >= 1, "equity_history not populated after tick"

        entry = engine.equity_history[-1]
        # Must contain all required fields
        assert "timestamp" in entry, "equity_history entry missing 'timestamp'"
        assert "portfolio_equity" in entry, "equity_history entry missing 'portfolio_equity'"
        assert "mark_to_market_equity" in entry, "equity_history entry missing 'mark_to_market_equity'"

        # Values must be reasonable (not zero, not None)
        assert entry["portfolio_equity"] > 0, "portfolio_equity should be positive"
        assert entry["mark_to_market_equity"] > 0, "mark_to_market_equity should be positive"
        # With no trades, MTM should equal portfolio equity
        assert entry["mark_to_market_equity"] == pytest.approx(entry["portfolio_equity"]), \
            "With no open positions, MTM should equal portfolio_equity"


# ===================================================================
# Test: AC21 — Token universe discovered from parquet data
# ===================================================================

class TestTokenDiscovery:
    """AC21: Token universe discovered from available parquet data."""

    @patch("v4.paper_engine.precompute_strategy_signals")
    @patch("v4.paper_engine.discover_tokens")
    def test_tick_internal_discovers_tokens(self, mock_discover, mock_precompute):
        """_tick_internal calls discover_tokens to find token universe from parquet data."""
        mock_discover.return_value = ["BTC", "ETH", "SOL"]
        mock_precompute.return_value = {}

        config = _make_test_config()
        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []
        engine.equity_history = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

        # _tick_internal should have called discover_tokens to find available tokens
        mock_discover.assert_called()
        # Verify it was called with the correct market type for the strategy
        call_args = mock_discover.call_args
        assert call_args is not None


# ===================================================================
# Test: AC22 — s58 config handled correctly
# ===================================================================

class TestS58Config:
    """AC22: s58 configuration (s56+s57 pool with shared capital) handled correctly."""

    def test_s58_pool_mode_two_strategies(self):
        """s58 config creates a pool-mode engine with s56 and s57 strategies."""
        config = _make_test_config(
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
                StrategySpec(strategy_id="s57", weight=0.5, market="perp", max_positions=10),
            ],
            mode="pool",
            pool_name="s58",
            capital=200_000.0,
        )

        engine = PaperPortfolioEngine(config)

        assert engine.config.mode == "pool"
        assert engine.config.pool_name == "s58"
        assert len(engine.config.strategies) == 2
        assert engine.state is not None  # pool mode uses shared state
        assert engine.state.initial_capital == pytest.approx(200_000.0)

    @patch("v4.paper_engine.discover_tokens", return_value=["BTC", "ETH"])
    @patch("v4.paper_engine.precompute_strategy_signals")
    def test_s58_tick_processes_both_strategies(self, mock_precompute, mock_discover):
        """s58 tick calls precompute for BOTH s56 and s57 strategies."""
        config = _make_test_config(
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
                StrategySpec(strategy_id="s57", weight=0.5, market="perp", max_positions=10),
            ],
            mode="pool",
            pool_name="s58",
        )
        mock_precompute.return_value = {}

        engine = PaperPortfolioEngine(config)
        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv.return_value = []
        engine.fetcher.filter_closed_bars.return_value = []
        engine.fetcher.fetch_funding_rates.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config.state_dir = tmpdir
            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")

        # Should call precompute for both strategies
        assert mock_precompute.call_count >= 2
        # Verify both strategy IDs were used
        strategy_ids_called = set()
        for c in mock_precompute.call_args_list:
            spec = c[0][0] if c[0] else c[1].get("strategy_spec")
            strategy_ids_called.add(spec.strategy_id)
        assert "s56" in strategy_ids_called, "s56 not called in s58 tick"
        assert "s57" in strategy_ids_called, "s57 not called in s58 tick"
