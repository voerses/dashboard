"""Acceptance tests for Task 10: Dashboard V2 Generator.

Tests verify:
  - to_dashboard_sim() produces valid SIMS JSON schema
  - SIMS JSON has required fields: id, name, capital, strategies, all_trades, equity_history
  - all_trades entries have: token, strategy, market_type, direction, pnl, exit_reason, signal
  - Pool aggregation: pool strategies show as single entity
  - Fund allocation panel data: shadow pools, imbalance_pct, blocked_entries
  - rebalance_history in SIMS output
  - "Last updated" timestamp in output
  - Stale data warning (>2 hours) with exact boundary tests

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until dashboard_v2 is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_engine import PaperPortfolioEngine
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position, ClosedTrade, PositionManager
from v4.simulator import SimulationState
from v4.paper_shadow import ShadowRebalancer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine_with_trades() -> PaperPortfolioEngine:
    """Build a PaperPortfolioEngine with sample state for dashboard tests."""
    engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
    engine.config = PaperConfig(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
            StrategySpec(strategy_id="s57", weight=0.3, market="perp", max_positions=8),
        ],
        capital=200_000.0,
        mode="pool",
        pool_name="s58_combined",
    )
    engine.state = SimulationState(initial_capital=200_000.0)
    engine.state.realized_pnl = 15_000.0
    engine.state.total_fees = 500.0
    engine.state.total_funding = -100.0
    engine.tick_counter = 100
    engine.last_timestamp = "2026-03-09T20:00:00Z"

    # Add a closed trade
    trade = ClosedTrade(
        position_id="BTC:s56:10:primary",
        token="BTC",
        strategy_id="s56",
        leg="primary",
        entry_bar=10,
        exit_bar=37,
        entry_price=65_000.0,
        exit_price=68_000.0,
        direction=1,
        margin_usd=10_000.0,
        pnl=432.10,
        funding_cost=-23.45,
        entry_fee=5.0,
        exit_fee=5.10,
        hold_bars=27,
        exit_reason="target",
        is_perp=True,
    )
    engine.state.position_manager.closed_trades.append(trade)

    # Add an open position
    pos = Position(
        position_id="ETH:s57:50:primary",
        token="ETH",
        strategy_id="s57",
        leg="primary",
        entry_bar=50,
        entry_price=3_500.0,
        direction=1,
        quantity=1.428,
        margin_usd=5_000.0,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        exit_regimes=set(),
        stop_price=3_200.0,
        highest=3_600.0,
        lowest=3_500.0,
        initial_risk=200.0,
    )
    engine.state.position_manager.open_position(pos)

    # Shadow rebalancer
    engine.shadow = MagicMock(spec=ShadowRebalancer)
    engine.shadow.spot_funds_shadow = 90_000.0
    engine.shadow.perp_funds_shadow = 110_000.0
    engine.shadow.spot_deployed = 20_000.0
    engine.shadow.perp_deployed = 40_000.0
    engine.shadow.rebalance_log = [
        {
            "timestamp": "2026-03-09T19:00:00Z",
            "transfer_needed_usd": 5_000.0,
            "direction": "perp->spot",
            "would_have_blocked_entries": 1,
        }
    ]

    # Equity history
    engine.equity_history = [
        {"timestamp": "2026-03-09T18:00:00Z", "mark_to_market_equity": 214_000.0},
        {"timestamp": "2026-03-09T19:00:00Z", "mark_to_market_equity": 214_500.0},
        {"timestamp": "2026-03-09T20:00:00Z", "mark_to_market_equity": 215_000.0},
    ]

    # Sub-hourly exit/entry resolution attributes
    engine._effective_exit_resolution = 0
    engine._strategy_exit_resolution = {}

    # Armed order observability attributes
    import threading
    engine._armed_tokens = {}
    engine._armed_tokens_lock = threading.Lock()
    engine._last_armed_skip_reasons = {}
    engine._last_expired_orders = []

    return engine


# ===================================================================
# Test: to_dashboard_sim() produces valid SIMS JSON schema
# ===================================================================

class TestDashboardSimSchema:
    """to_dashboard_sim() produces valid SIMS JSON schema (AC11b)."""

    def test_produces_dict(self):
        """to_dashboard_sim() returns a dictionary."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert isinstance(sims, dict)

    def test_sims_is_json_serializable(self):
        """The SIMS dict should be JSON serializable."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        json_str = json.dumps(sims)  # must not raise
        assert len(json_str) > 0


# ===================================================================
# Test: SIMS JSON has required fields
# ===================================================================

class TestSimsRequiredFields:
    """SIMS JSON must have: id, name, capital, strategies, all_trades, equity_history."""

    def test_has_id(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert "id" in sims

    def test_has_name(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert "name" in sims

    def test_has_capital(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert "capital" in sims
        assert sims["capital"] == pytest.approx(200_000.0)

    def test_has_strategies(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert "strategies" in sims
        assert isinstance(sims["strategies"], list)

    def test_has_all_trades(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert "all_trades" in sims
        assert isinstance(sims["all_trades"], list)
        assert len(sims["all_trades"]) >= 1

    def test_has_equity_history(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        assert "equity_history" in sims
        assert isinstance(sims["equity_history"], list)


# ===================================================================
# Test: all_trades entries have required fields
# ===================================================================

class TestTradeEntryFields:
    """all_trades entries must have: token, strategy, market_type, direction,
    pnl, exit_reason, signal."""

    def test_trade_has_token(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "token" in trade
        assert trade["token"] == "BTC"

    def test_trade_has_strategy(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "strategy" in trade
        assert trade["strategy"] == "s56"

    def test_trade_has_market_type(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "market_type" in trade
        assert trade["market_type"] in ("spot", "perp")

    def test_trade_has_direction(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "direction" in trade
        assert trade["direction"] in (1, -1, "long", "short")

    def test_trade_has_pnl(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "pnl" in trade
        assert trade["pnl"] == pytest.approx(432.10)

    def test_trade_has_exit_reason(self):
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "exit_reason" in trade
        assert trade["exit_reason"] == "target"

    def test_trade_has_signal(self):
        """Trade entry should include signal snapshot from entry time."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()
        trade = sims["all_trades"][0]
        assert "signal" in trade


# ===================================================================
# Test: Pool aggregation
# ===================================================================

class TestPoolAggregation:
    """Pool strategies show as single entity in dashboard (AC12)."""

    def test_pool_shown_as_single_strategy(self):
        """When mode=pool with pool_name, strategies appear as one combined entity."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        # In pool mode with pool_name="s58_combined", there should be one strategy
        # entry representing the combined pool
        strategies = sims["strategies"]
        pool_strategy = [s for s in strategies if s.get("name") == "s58_combined"
                         or s.get("id") == "s58_combined"]
        assert len(pool_strategy) >= 1

    def test_pool_has_combined_metrics(self):
        """Pool entity should have combined equity, trade count, etc."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        strategies = sims["strategies"]
        assert len(strategies) >= 1
        strategy = strategies[0]  # pool entity
        assert "final_equity" in strategy or "equity" in strategy


# ===================================================================
# Q3 fix: Fund allocation panel data -- tightened assertions
# ===================================================================

class TestFundAllocationPanel:
    """Fund allocation data: shadow pools, imbalance_pct, blocked_entries (AC26).

    Q3 fix: replaced weak string-matching assertions with exact dict key
    checks and structural assertions.
    """

    def test_sims_has_shadow_pools_structure(self):
        """SIMS output must have a shadow_pools dict with spot_funds and perp_funds."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        # Must have shadow_pools as a dict (not just somewhere in a string)
        assert "shadow_pools" in sims, (
            f"SIMS output missing 'shadow_pools' key. Top-level keys: {list(sims.keys())}"
        )
        shadow = sims["shadow_pools"]
        assert isinstance(shadow, dict), (
            f"shadow_pools should be a dict, got {type(shadow)}"
        )
        assert "spot_funds" in shadow, (
            f"shadow_pools missing 'spot_funds'. Keys: {list(shadow.keys())}"
        )
        assert "perp_funds" in shadow, (
            f"shadow_pools missing 'perp_funds'. Keys: {list(shadow.keys())}"
        )
        assert shadow["spot_funds"] == pytest.approx(90_000.0)
        assert shadow["perp_funds"] == pytest.approx(110_000.0)

    def test_sims_has_imbalance_pct_in_shadow_pools(self):
        """SIMS output must have imbalance_pct inside shadow_pools dict."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        assert "shadow_pools" in sims
        assert "imbalance_pct" in sims["shadow_pools"], (
            f"shadow_pools missing 'imbalance_pct'. Keys: {list(sims['shadow_pools'].keys())}"
        )
        # imbalance_pct should be a numeric value
        assert isinstance(sims["shadow_pools"]["imbalance_pct"], (int, float))

    def test_sims_has_blocked_entries_count_in_shadow_pools(self):
        """SIMS output must have blocked_entries_count inside shadow_pools dict."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        assert "shadow_pools" in sims
        assert "blocked_entries_count" in sims["shadow_pools"], (
            f"shadow_pools missing 'blocked_entries_count'. "
            f"Keys: {list(sims['shadow_pools'].keys())}"
        )
        assert isinstance(sims["shadow_pools"]["blocked_entries_count"], int)

    def test_shadow_pools_has_deployed_fields(self):
        """shadow_pools should include spot_deployed and perp_deployed."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        shadow = sims["shadow_pools"]
        assert "spot_deployed" in shadow, (
            f"shadow_pools missing 'spot_deployed'. Keys: {list(shadow.keys())}"
        )
        assert "perp_deployed" in shadow, (
            f"shadow_pools missing 'perp_deployed'. Keys: {list(shadow.keys())}"
        )
        assert shadow["spot_deployed"] == pytest.approx(20_000.0)
        assert shadow["perp_deployed"] == pytest.approx(40_000.0)


# ===================================================================
# Test: rebalance_history in SIMS output
# ===================================================================

class TestRebalanceHistory:
    """SIMS output includes rebalance_history."""

    def test_sims_has_rebalance_history(self):
        """SIMS output should contain rebalance history for the dashboard."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        assert "rebalance_history" in sims
        assert isinstance(sims["rebalance_history"], list)
        assert len(sims["rebalance_history"]) >= 1

    def test_rebalance_entry_has_fields(self):
        """Each rebalance entry should have transfer info."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        entry = sims["rebalance_history"][0]
        assert "timestamp" in entry
        assert "transfer_needed_usd" in entry
        assert "direction" in entry


# ===================================================================
# Test: "Last updated" timestamp in output
# ===================================================================

class TestLastUpdatedTimestamp:
    """Output includes 'Last updated' timestamp (AC11)."""

    def test_sims_has_last_updated(self):
        """SIMS output should have a last_updated field."""
        engine = _make_engine_with_trades()
        sims = engine.to_dashboard_sim()

        assert "last_updated" in sims
        assert "2026-03-09" in sims["last_updated"]


# ===================================================================
# Test: Stale data warning (>2 hours) + M7 exact boundary tests
# ===================================================================

class TestStaleDataWarning:
    """Data >2 hours old shows a stale warning (AC11).

    M7 fix: added exact 2-hour boundary tests to verify is_stale
    at 1h59m59s (not stale), 2h0m0s (boundary), and 2h0m1s (stale).
    """

    def test_stale_when_data_old(self):
        """If last_updated is >2 hours ago, is_stale should be True."""
        engine = _make_engine_with_trades()
        # Set timestamp to 3 hours ago
        three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=3)
        engine.last_timestamp = three_hours_ago.strftime("%Y-%m-%dT%H:%M:%SZ")

        sims = engine.to_dashboard_sim()

        assert "is_stale" in sims, (
            f"SIMS output missing 'is_stale'. Keys: {list(sims.keys())}"
        )
        assert sims["is_stale"] is True

    def test_not_stale_when_data_recent(self):
        """If last_updated is <2 hours ago, is_stale should be False."""
        engine = _make_engine_with_trades()
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        engine.last_timestamp = one_hour_ago.strftime("%Y-%m-%dT%H:%M:%SZ")

        sims = engine.to_dashboard_sim()

        assert "is_stale" in sims
        assert sims["is_stale"] is False

    def test_not_stale_at_1h59m59s(self):
        """Data exactly 1h59m59s old should NOT be stale."""
        engine = _make_engine_with_trades()
        almost_two_hours = datetime.now(timezone.utc) - timedelta(
            hours=1, minutes=59, seconds=59
        )
        engine.last_timestamp = almost_two_hours.strftime("%Y-%m-%dT%H:%M:%SZ")

        sims = engine.to_dashboard_sim()

        assert "is_stale" in sims
        assert sims["is_stale"] is False, (
            "Data 1h59m59s old should NOT be considered stale (threshold is 2h)"
        )

    def test_stale_at_2h0m1s(self):
        """Data exactly 2h0m1s old should be stale."""
        engine = _make_engine_with_trades()
        just_over_two_hours = datetime.now(timezone.utc) - timedelta(
            hours=2, seconds=1
        )
        engine.last_timestamp = just_over_two_hours.strftime("%Y-%m-%dT%H:%M:%SZ")

        sims = engine.to_dashboard_sim()

        assert "is_stale" in sims
        assert sims["is_stale"] is True, (
            "Data 2h0m1s old should be considered stale (threshold is 2h)"
        )

    def test_boundary_at_exactly_2h(self):
        """Data exactly 2h0m0s old -- boundary case. Document whether
        is_stale is True or False. The implementation must be consistent:
        either >= 2h is stale, or > 2h is stale. This test accepts either
        convention but requires is_stale to be present and boolean."""
        engine = _make_engine_with_trades()
        exactly_two_hours = datetime.now(timezone.utc) - timedelta(hours=2)
        engine.last_timestamp = exactly_two_hours.strftime("%Y-%m-%dT%H:%M:%SZ")

        sims = engine.to_dashboard_sim()

        assert "is_stale" in sims
        assert isinstance(sims["is_stale"], bool), (
            f"is_stale should be a boolean, got: {type(sims['is_stale'])}"
        )
        # Document the boundary behavior: at exactly 2h, the implementation
        # decides. This test just verifies the field exists and is boolean.
        # Stricter tests above/below the boundary verify the direction.
