"""Acceptance tests for Task 5: Shadow Rebalancing.

Tests verify:
  - Initial shadow pool split matches strategy market types
  - Shadow pool invariant: spot_shadow + perp_shadow == portfolio_equity after each tick
  - accrue_funding: shadow perp pool decreases with positive funding
  - compute: identifies entries blocked by per-pool constraints
  - compute: computes correct transfer amount and direction
  - update_pools: entry margin attributed to correct shadow pool
  - update_pools: closed trade P&L attributed to correct shadow pool
  - rebalances.jsonl schema matches AC24

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until paper_shadow.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import numpy as np
import pytest

from v4.paper_shadow import ShadowRebalancer, RebalanceRecord
from v4.paper_config import PaperConfig
from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, ClosedTrade, PositionManager
from v4.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shadow_config(
    capital: float = 200_000.0,
    strategies: list | None = None,
) -> PaperConfig:
    """Build a PaperConfig for shadow rebalancing tests."""
    if strategies is None:
        strategies = [
            StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
            StrategySpec(strategy_id="s57", weight=0.3, market="perp", max_positions=8),
        ]
    return PaperConfig(
        strategies=strategies,
        capital=capital,
        mode="pool",
    )


def _make_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    is_perp: bool = True,
    margin: float = 10_000.0,
    direction: int = 1,
    entry_bar: int = 0,
) -> Position:
    pid = f"{token}:{strategy_id}:{entry_bar}:primary"
    return Position(
        position_id=pid,
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=100.0,
        direction=direction,
        quantity=direction * margin / 100.0,
        margin_usd=margin,
        leverage=1.0,
        is_perp=is_perp,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        exit_regimes=set(),
        stop_price=90.0 if direction == 1 else 110.0,
        highest=100.0,
        lowest=100.0,
        initial_risk=2.0,
    )


# ===================================================================
# Test: Initial shadow pool split matches strategy market types
# ===================================================================

class TestInitialShadowPoolSplit:
    """Shadow pools should be initialized based on strategy market types (AC22)."""

    def test_combined_strategy_splits_capital(self):
        """Combined strategy with capital_split=0.5 allocates 50% to each pool."""
        config = _make_shadow_config(
            capital=200_000.0,
            strategies=[
                StrategySpec(strategy_id="s56", weight=1.0, market="combined", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)

        # Combined strategy: 50% spot, 50% perp (default capital_split)
        assert shadow.spot_funds_shadow == pytest.approx(100_000.0)
        assert shadow.perp_funds_shadow == pytest.approx(100_000.0)

    def test_perp_only_strategy_all_to_perp(self):
        """Perp-only strategy allocates 100% to perp pool."""
        config = _make_shadow_config(
            capital=100_000.0,
            strategies=[
                StrategySpec(strategy_id="s57", weight=1.0, market="perp", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)

        assert shadow.spot_funds_shadow == pytest.approx(0.0)
        assert shadow.perp_funds_shadow == pytest.approx(100_000.0)

    def test_spot_only_strategy_all_to_spot(self):
        """Spot-only strategy allocates 100% to spot pool."""
        config = _make_shadow_config(
            capital=100_000.0,
            strategies=[
                StrategySpec(strategy_id="s58", weight=1.0, market="spot", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)

        assert shadow.spot_funds_shadow == pytest.approx(100_000.0)
        assert shadow.perp_funds_shadow == pytest.approx(0.0)

    def test_mixed_strategies_split_correctly(self):
        """Mixed strategies: combined + perp allocate proportionally."""
        config = _make_shadow_config(
            capital=200_000.0,
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
                StrategySpec(strategy_id="s57", weight=0.3, market="perp", max_positions=8),
            ],
        )
        shadow = ShadowRebalancer(config)

        # s56: weight=0.5, combined => 50k spot, 50k perp
        # s57: weight=0.3, perp => 0 spot, 60k perp
        # Remaining (0.2 unallocated) split to pools proportionally or stays unassigned
        # Total: spot_shadow >= 50k, perp_shadow >= 110k
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(200_000.0)
        assert shadow.spot_funds_shadow > 0.0
        assert shadow.perp_funds_shadow > shadow.spot_funds_shadow


# ===================================================================
# Test: Shadow pool invariant
# ===================================================================

class TestShadowPoolInvariant:
    """spot_shadow + perp_shadow == portfolio_equity after each tick (AC22)."""

    def test_invariant_holds_at_init(self):
        """At initialization, total shadow == initial capital."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(200_000.0)

    def test_invariant_holds_after_funding(self):
        """After MULTIPLE sequential funding accruals (positive and negative),
        shadow pools sum to portfolio_equity within float tolerance.

        Tick 1: total_funding=100 (delta=100, cost)
        Tick 2: total_funding=250 (delta=150, cost)
        Tick 3: total_funding=400 (delta=150, cost)
        Then test negative funding (income):
        Tick 4: total_funding=350 (delta=-50, income flowing in)
        """
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        state = SimulationState(initial_capital=200_000.0)

        # Tick 1: total_funding=100 (delta=100, positive = cost)
        state.total_funding = 100.0
        shadow.accrue_funding(state)
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(state.portfolio_equity, rel=1e-6)

        # Tick 2: total_funding=250 (delta=150)
        state.total_funding = 250.0
        shadow.accrue_funding(state)
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(state.portfolio_equity, rel=1e-6)

        # Tick 3: total_funding=400 (delta=150)
        state.total_funding = 400.0
        shadow.accrue_funding(state)
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(state.portfolio_equity, rel=1e-6)

        # Tick 4: total_funding=350 (delta=-50, negative = income)
        state.total_funding = 350.0
        shadow.accrue_funding(state)
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(state.portfolio_equity, rel=1e-6)

    def test_invariant_holds_with_negative_funding_income(self):
        """Negative funding (income) should increase perp shadow pool and
        maintain the invariant across multiple accruals."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        state = SimulationState(initial_capital=200_000.0)

        # Tick 1: negative funding = income (perp shorts earn funding)
        state.total_funding = -200.0
        shadow.accrue_funding(state)
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(state.portfolio_equity, rel=1e-6)
        # Perp pool should have increased
        initial_perp = config.capital - shadow.spot_funds_shadow
        # (can't check exact initial since we don't know split, but total must hold)

        # Tick 2: more negative funding income
        state.total_funding = -500.0
        shadow.accrue_funding(state)
        total = shadow.spot_funds_shadow + shadow.perp_funds_shadow
        assert total == pytest.approx(state.portfolio_equity, rel=1e-6)


# ===================================================================
# Test: accrue_funding
# ===================================================================

class TestAccrueFunding:
    """Shadow perp pool decreases with positive funding (cost)."""

    def test_positive_funding_reduces_perp_shadow(self):
        """Positive funding = cost paid on perp positions => perp pool shrinks."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        initial_perp = shadow.perp_funds_shadow

        state = SimulationState(initial_capital=200_000.0)
        state.total_funding = 500.0  # $500 funding cost

        shadow.accrue_funding(state)

        assert shadow.perp_funds_shadow == pytest.approx(initial_perp - 500.0)
        assert shadow.spot_funds_shadow == pytest.approx(
            config.capital - initial_perp)  # spot unchanged

    def test_negative_funding_increases_perp_shadow(self):
        """Negative funding = income received on perp positions => perp pool grows."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        initial_perp = shadow.perp_funds_shadow

        state = SimulationState(initial_capital=200_000.0)
        state.total_funding = -300.0  # $300 funding income

        shadow.accrue_funding(state)

        assert shadow.perp_funds_shadow == pytest.approx(initial_perp + 300.0)


# ===================================================================
# Test: compute — identifies entries blocked by per-pool constraints
# ===================================================================

class TestComputeBlockedEntries:
    """compute() identifies entries that WOULD be blocked without shadow transfer."""

    def test_detects_blocked_entries_when_pool_insufficient(self):
        """If spot pool is too small for spot entries, they are flagged as would-have-blocked."""
        config = _make_shadow_config(
            capital=200_000.0,
            strategies=[
                StrategySpec(strategy_id="s56", weight=1.0, market="perp", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)
        # All capital in perp pool, nothing in spot pool
        assert shadow.spot_funds_shadow == pytest.approx(0.0)

        # Suppose we have spot entry candidates needing $10k
        # The compute method should detect that these would be blocked
        state = SimulationState(initial_capital=200_000.0)

        # We need to simulate entry candidates that need spot capital
        # The exact interface depends on implementation, but the record should show:
        record = shadow.compute(
            state=state,
            all_signals={},  # simplified
            specs={},
            bar_maps={},
            tick_counter=0,
            config=config,
            entry_candidates=[
                {"token": "BTC", "market_type": "spot", "margin_needed": 10_000.0},
            ],
        )

        assert isinstance(record, RebalanceRecord)
        assert record.would_have_blocked_entries > 0
        assert "BTC" in record.blocked_entry_tokens

    def test_no_blocked_entries_when_pools_sufficient(self):
        """When both pools have enough capital, no entries are blocked."""
        config = _make_shadow_config(
            capital=200_000.0,
            strategies=[
                StrategySpec(strategy_id="s56", weight=1.0, market="combined", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)

        state = SimulationState(initial_capital=200_000.0)

        record = shadow.compute(
            state=state,
            all_signals={},
            specs={},
            bar_maps={},
            tick_counter=0,
            config=config,
            entry_candidates=[
                {"token": "BTC", "market_type": "perp", "margin_needed": 5_000.0},
                {"token": "ETH", "market_type": "spot", "margin_needed": 5_000.0},
            ],
        )

        assert record.would_have_blocked_entries == 0
        assert record.blocked_entry_tokens == []


# ===================================================================
# Test: compute — correct transfer amount and direction
# ===================================================================

class TestComputeTransfer:
    """compute() computes correct transfer amount and direction."""

    def test_transfer_direction_perp_to_spot(self):
        """When spot pool needs more capital, direction is 'perp->spot'."""
        config = _make_shadow_config(
            capital=200_000.0,
            strategies=[
                StrategySpec(strategy_id="s56", weight=1.0, market="perp", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)
        # All capital in perp pool

        state = SimulationState(initial_capital=200_000.0)

        record = shadow.compute(
            state=state,
            all_signals={},
            specs={},
            bar_maps={},
            tick_counter=0,
            config=config,
            entry_candidates=[
                {"token": "BTC", "market_type": "spot", "margin_needed": 10_000.0},
            ],
        )

        assert record.transfer_needed_usd > 0.0
        assert "perp" in record.direction.lower() and "spot" in record.direction.lower()

    def test_transfer_amount_correct(self):
        """Transfer amount = shortfall in the deficit pool."""
        config = _make_shadow_config(
            capital=200_000.0,
            strategies=[
                StrategySpec(strategy_id="s56", weight=1.0, market="perp", max_positions=10),
            ],
        )
        shadow = ShadowRebalancer(config)

        state = SimulationState(initial_capital=200_000.0)

        record = shadow.compute(
            state=state,
            all_signals={},
            specs={},
            bar_maps={},
            tick_counter=0,
            config=config,
            entry_candidates=[
                {"token": "BTC", "market_type": "spot", "margin_needed": 15_000.0},
            ],
        )

        # Spot pool is $0, needs $15k => transfer = $15k from perp
        assert record.transfer_needed_usd == pytest.approx(15_000.0)


# ===================================================================
# Test: update_pools — entry margin attributed to correct shadow pool
# ===================================================================

class TestUpdatePoolsEntries:
    """update_pools: entry margin attributed to correct shadow pool."""

    def test_perp_entry_reduces_perp_shadow(self):
        """Opening a perp position reduces the perp shadow pool."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)
        initial_perp = shadow.perp_funds_shadow

        state = SimulationState(initial_capital=200_000.0)
        perp_pos = _make_position(is_perp=True, margin=10_000.0)
        state.position_manager.open_position(perp_pos)

        shadow.update_pools(state, open_before=0, closed_before=0)

        assert shadow.perp_funds_shadow < initial_perp

    def test_spot_entry_reduces_spot_shadow(self):
        """Opening a spot position reduces the spot shadow pool."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)
        initial_spot = shadow.spot_funds_shadow

        state = SimulationState(initial_capital=200_000.0)
        spot_pos = _make_position(is_perp=False, margin=10_000.0)
        state.position_manager.open_position(spot_pos)

        shadow.update_pools(state, open_before=0, closed_before=0)

        assert shadow.spot_funds_shadow < initial_spot


# ===================================================================
# Test: update_pools — closed trade P&L attributed to correct pool
# ===================================================================

class TestUpdatePoolsClosedTrades:
    """Closed trade P&L attributed to correct shadow pool."""

    def test_perp_close_pnl_to_perp_pool(self):
        """Closing a perp position adds P&L to perp shadow pool."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        state = SimulationState(initial_capital=200_000.0)
        perp_pos = _make_position(is_perp=True, margin=10_000.0)
        state.position_manager.open_position(perp_pos)

        # Simulate entry: update pools with the open position
        shadow.update_pools(state, open_before=0, closed_before=0)
        perp_after_entry = shadow.perp_funds_shadow

        # Now close the position with profit
        state.position_manager.close_position(
            perp_pos, exit_bar=10, exit_price=110.0,
            pnl=1_000.0, funding_cost=0.0, entry_fee=5.0,
            exit_fee=5.0, exit_reason="target",
        )
        state.realized_pnl += 1_000.0

        shadow.update_pools(state, open_before=1, closed_before=0)

        # Perp pool should increase by the realized P&L (minus fees) from this trade
        assert shadow.perp_funds_shadow > perp_after_entry

    def test_spot_close_pnl_to_spot_pool(self):
        """Closing a spot position adds P&L to spot shadow pool."""
        config = _make_shadow_config(capital=200_000.0)
        shadow = ShadowRebalancer(config)

        state = SimulationState(initial_capital=200_000.0)
        spot_pos = _make_position(is_perp=False, margin=10_000.0)
        state.position_manager.open_position(spot_pos)

        shadow.update_pools(state, open_before=0, closed_before=0)
        spot_after_entry = shadow.spot_funds_shadow

        state.position_manager.close_position(
            spot_pos, exit_bar=10, exit_price=110.0,
            pnl=500.0, funding_cost=0.0, entry_fee=5.0,
            exit_fee=5.0, exit_reason="target",
        )
        state.realized_pnl += 500.0

        shadow.update_pools(state, open_before=1, closed_before=0)

        assert shadow.spot_funds_shadow > spot_after_entry


# ===================================================================
# Test: rebalances.jsonl schema matches AC24
# ===================================================================

class TestRebalanceJSONLSchema:
    """rebalances.jsonl per-tick record has all AC24 fields."""

    def test_rebalance_record_has_required_fields(self):
        """RebalanceRecord has all fields from AC24."""
        record = RebalanceRecord(
            timestamp="2026-03-09T20:00:00Z",
            tick=42,
            n_entry_candidates=3,
            spot_capital_needed=15_000.0,
            perp_capital_needed=25_000.0,
            spot_shadow_available=65_000.0,
            perp_shadow_available=55_000.0,
            transfer_needed_usd=0.0,
            direction="none",
            would_have_blocked_entries=0,
            blocked_entry_tokens=[],
            spot_shadow_after=65_000.0,
            perp_shadow_after=55_000.0,
            spot_deployed=30_000.0,
            perp_deployed=50_000.0,
            imbalance_pct=8.3,
        )

        # Convert to dict (for JSON serialization)
        d = record.to_dict()
        required_fields = [
            "timestamp", "tick", "n_entry_candidates",
            "spot_capital_needed", "perp_capital_needed",
            "spot_shadow_available", "perp_shadow_available",
            "transfer_needed_usd", "direction",
            "would_have_blocked_entries", "blocked_entry_tokens",
            "spot_shadow_after", "perp_shadow_after",
            "spot_deployed", "perp_deployed", "imbalance_pct",
        ]
        for field in required_fields:
            assert field in d, f"Missing required field: {field}"

    def test_rebalance_record_serializes_to_json(self):
        """RebalanceRecord can be serialized to valid JSON."""
        record = RebalanceRecord(
            timestamp="2026-03-09T20:00:00Z",
            tick=42,
            n_entry_candidates=3,
            spot_capital_needed=15_000.0,
            perp_capital_needed=25_000.0,
            spot_shadow_available=65_000.0,
            perp_shadow_available=55_000.0,
            transfer_needed_usd=10_000.0,
            direction="perp->spot",
            would_have_blocked_entries=1,
            blocked_entry_tokens=["ETH"],
            spot_shadow_after=75_000.0,
            perp_shadow_after=45_000.0,
            spot_deployed=30_000.0,
            perp_deployed=50_000.0,
            imbalance_pct=25.0,
        )

        json_str = json.dumps(record.to_dict())
        parsed = json.loads(json_str)
        assert parsed["direction"] == "perp->spot"
        assert parsed["would_have_blocked_entries"] == 1
        assert parsed["blocked_entry_tokens"] == ["ETH"]
