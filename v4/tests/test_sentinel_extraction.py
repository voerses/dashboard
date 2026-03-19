"""Acceptance tests for Task 4: StopLevel extraction + estimated liq price (AC2, AC17).

Tests verify:
  - _extract_stop_levels produces correct StopLevel for a long position
  - _extract_stop_levels produces correct StopLevel for a short position
  - cb_price computation from StrategySpec
  - target_price computation
  - estimated_liq_price formula (AC17)
  - stop_active = bars_held >= no_stop_bars OR convex_exit
  - Carry strategies excluded
  - Linked positions excluded
  - convex_exit flag set correctly
  - stops.json NOT written when sentinel_mode="off"
  - stops.json written after state.json commit when sentinel_mode != "off"

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until extraction is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_engine import PaperPortfolioEngine
from v4.stop_store import StopLevel, StopStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(
    position_id: str = "BTC:s56:100:primary",
    token: str = "BTC",
    strategy_id: str = "s56",
    direction: int = 1,
    entry_price: float = 66_000.0,
    margin_usd: float = 10_000.0,
    quantity: float = 0.15,
    leverage: float = 1.0,
    is_perp: bool = True,
    entry_bar: int = 0,
    cumulative_funding: float = -12.5,
    fee_rate: float = 0.0004,
    stop_price: float = 64_000.0,
    convex_exit: bool = False,
    linked_position_id: str | None = None,
    highest: float = 70_000.0,
    lowest: float = 62_000.0,
    # Fields sourced from Position dataclass for StopLevel extraction
    no_stop_bars: int = 0,
    trail_mult: float = 3.0,
    target_mult: float = 5.0,
    stop_mult: float = 3.0,
    initial_risk: float = 3_600.0,  # stop_mult * atr at entry
    chandelier_lookback: int = 0,
    trail_schedule=None,
    time_trail_schedule=None,
) -> MagicMock:
    """Build a mock Position with all fields needed for stop extraction.

    Note: bars_held is NOT a Position field — it is computed as
    tick_counter - entry_bar by _extract_stop_levels.
    """
    pos = MagicMock()
    pos.position_id = position_id
    pos.token = token
    pos.strategy_id = strategy_id
    pos.direction = direction
    pos.entry_price = entry_price
    pos.margin_usd = margin_usd
    pos.quantity = quantity
    pos.leverage = leverage
    pos.is_perp = is_perp
    pos.entry_bar = entry_bar
    pos.cumulative_funding = cumulative_funding
    pos.fee_rate = fee_rate
    pos.stop_price = stop_price
    pos.convex_exit = convex_exit
    pos.linked_position_id = linked_position_id
    pos.highest = highest
    pos.lowest = lowest
    pos.no_stop_bars = no_stop_bars
    pos.trail_mult = trail_mult
    pos.target_mult = target_mult
    pos.stop_mult = stop_mult
    pos.initial_risk = initial_risk
    pos.chandelier_lookback = chandelier_lookback
    pos.trail_schedule = trail_schedule
    pos.time_trail_schedule = time_trail_schedule
    return pos


def _make_strategy_spec(
    strategy_id: str = "s56",
    circuit_breaker_r: float = 3.0,
) -> MagicMock:
    """Build a mock StrategySpec for stop extraction.

    Only fields that live on StrategySpec (not Position) belong here.
    trail_mult, target_mult, no_stop_bars, chandelier_lookback are Position fields.
    """
    spec = MagicMock()
    spec.strategy_id = strategy_id
    spec.circuit_breaker_r = circuit_breaker_r
    return spec


# ===================================================================
# Test: Long position extraction
# ===================================================================

class TestLongPositionExtraction:
    """_extract_stop_levels produces correct StopLevel for a long."""

    def test_long_position_stop_price(self):
        """Long position stop_price passes through from position."""
        pos = _make_position(direction=1, stop_price=64_000.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert len(result) == 1
        assert result[0].stop_price == pytest.approx(64_000.0)
        assert result[0].direction == 1

    def test_long_position_basic_fields(self):
        """Long position has all basic StopLevel fields."""
        pos = _make_position(direction=1)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        sl = result[0]
        assert sl.token == "BTC"
        assert sl.strategy_id == "s56"
        assert sl.entry_price == pytest.approx(66_000.0)
        assert sl.margin_usd == pytest.approx(10_000.0)
        assert sl.quantity == pytest.approx(0.15)
        assert sl.leverage == pytest.approx(1.0)
        assert sl.is_perp is True
        assert sl.cur_atr == pytest.approx(1200.0)
        assert sl.trail_mult == pytest.approx(3.0)
        assert sl.highest == pytest.approx(70_000.0)
        assert sl.lowest == pytest.approx(62_000.0)


# ===================================================================
# Test: Short position extraction
# ===================================================================

class TestShortPositionExtraction:
    """_extract_stop_levels produces correct StopLevel for a short."""

    def test_short_position_direction(self):
        """Short position has direction=-1."""
        pos = _make_position(direction=-1, stop_price=68_000.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].direction == -1
        assert result[0].stop_price == pytest.approx(68_000.0)


# ===================================================================
# Test: cb_price computation from StrategySpec
# ===================================================================

class TestCbPriceComputation:
    """cb_price is pre-computed from StrategySpec fields."""

    def test_long_cb_price(self):
        """Long cb_price = entry_price - circuit_breaker_r * initial_risk."""
        # initial_risk = stop_mult * atr_at_entry (frozen on Position at entry)
        # With initial_risk=3600 and circuit_breaker_r=3.0:
        # cb_price = 66000 - 3.0 * 3600 = 66000 - 10800 = 55200
        pos = _make_position(direction=1, entry_price=66_000.0, initial_risk=3_600.0)
        spec = _make_strategy_spec(circuit_breaker_r=3.0)
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        # cb_price = entry - circuit_breaker_r * initial_risk
        assert result[0].cb_price == pytest.approx(55_200.0)

    def test_short_cb_price(self):
        """Short cb_price = entry_price + circuit_breaker_r * initial_risk."""
        pos = _make_position(direction=-1, entry_price=66_000.0, initial_risk=3_600.0)
        spec = _make_strategy_spec(circuit_breaker_r=3.0)
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        # cb_price = 66000 + 3.0 * 3600 = 76800
        assert result[0].cb_price == pytest.approx(76_800.0)

    def test_no_circuit_breaker_cb_price_zero(self):
        """cb_price = 0.0 when circuit_breaker_r is 0 (no circuit breaker)."""
        pos = _make_position(direction=1)
        spec = _make_strategy_spec(circuit_breaker_r=0.0)
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].cb_price == pytest.approx(0.0)


# ===================================================================
# Test: target_price computation
# ===================================================================

class TestTargetPriceComputation:
    """target_price is pre-computed for sentinel monitoring."""

    def test_long_target_price(self):
        """Long target_price = entry_price + target_mult * cur_atr."""
        pos = _make_position(direction=1, entry_price=66_000.0, target_mult=5.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        # 66000 + 5.0 * 1200 = 72000
        assert result[0].target_price == pytest.approx(72_000.0)

    def test_short_target_price(self):
        """Short target_price = entry_price - target_mult * cur_atr."""
        pos = _make_position(direction=-1, entry_price=66_000.0, target_mult=5.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        # 66000 - 5.0 * 1200 = 60000
        assert result[0].target_price == pytest.approx(60_000.0)

    def test_no_meaningful_target_returns_zero(self):
        """target_price = 0.0 when target_mult >= 100 (no meaningful target)."""
        pos = _make_position(direction=1, target_mult=999.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].target_price == pytest.approx(0.0)


# ===================================================================
# Test: estimated_liq_price formula (AC17)
# ===================================================================

class TestEstimatedLiqPrice:
    """estimated_liq_price computed for perp positions with leverage > 1."""

    def test_long_liq_price(self):
        """Long liq_price = entry - (margin - cum_funding - notional * mmr) / quantity.

        Using tier-1 mmr = 0.004 (0.4%).
        entry_notional = margin * leverage = 10000 * 3 = 30000
        liq_price = 66000 - (10000 - (-12.5) - 30000 * 0.004) / 0.45
                  = 66000 - (10000 + 12.5 - 120) / 0.45
                  = 66000 - 9892.5 / 0.45
                  = 66000 - 21983.33
                  = 44016.67
        """
        pos = _make_position(
            direction=1, entry_price=66_000.0, margin_usd=10_000.0,
            leverage=3.0, quantity=0.45, is_perp=True,
            cumulative_funding=-12.5,
        )
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        # entry_notional = 10000 * 3 = 30000
        # liq = 66000 - (10000 - (-12.5) - 30000 * 0.004) / 0.45
        entry_notional = 10_000.0 * 3.0
        mmr = 0.004
        expected = 66_000.0 - (10_000.0 - (-12.5) - entry_notional * mmr) / 0.45
        assert result[0].estimated_liq_price == pytest.approx(expected, rel=1e-4)

    def test_short_liq_price(self):
        """Short liq_price = entry + (margin - cum_funding - notional * mmr) / abs(quantity)."""
        pos = _make_position(
            direction=-1, entry_price=66_000.0, margin_usd=10_000.0,
            leverage=3.0, quantity=-0.45, is_perp=True,
            cumulative_funding=-12.5,
        )
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        entry_notional = 10_000.0 * 3.0
        mmr = 0.004
        expected = 66_000.0 + (10_000.0 - (-12.5) - entry_notional * mmr) / abs(-0.45)
        assert result[0].estimated_liq_price == pytest.approx(expected, rel=1e-4)

    def test_spot_position_liq_price_zero(self):
        """Spot positions have estimated_liq_price = 0.0."""
        pos = _make_position(is_perp=False, leverage=1.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].estimated_liq_price == pytest.approx(0.0)

    def test_leverage_1_liq_price_zero(self):
        """Perp position with leverage <= 1 has estimated_liq_price = 0.0."""
        pos = _make_position(is_perp=True, leverage=1.0)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].estimated_liq_price == pytest.approx(0.0)


# ===================================================================
# Test: stop_active computation
# ===================================================================

class TestStopActive:
    """stop_active = bars_held >= no_stop_bars OR convex_exit."""

    def test_stop_active_when_bars_held_exceeds_no_stop(self):
        """stop_active=True when bars_held >= no_stop_bars.

        bars_held = tick_counter - entry_bar = 10 - 0 = 10 >= 5.
        """
        pos = _make_position(entry_bar=0, no_stop_bars=5)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].stop_active is True

    def test_stop_inactive_during_grace_period(self):
        """stop_active=False when bars_held < no_stop_bars and not convex_exit.

        bars_held = tick_counter - entry_bar = 10 - 8 = 2 < 48.
        """
        pos = _make_position(entry_bar=8, no_stop_bars=48, convex_exit=False)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].stop_active is False

    def test_stop_active_when_convex_exit(self):
        """stop_active=True when convex_exit=True even in grace period.

        bars_held = tick_counter - entry_bar = 10 - 8 = 2 < 48, but convex_exit overrides.
        """
        pos = _make_position(entry_bar=8, no_stop_bars=48, convex_exit=True)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].stop_active is True


# ===================================================================
# Test: Carry strategies excluded
# ===================================================================

class TestCarryExclusion:
    """Carry strategies are excluded from stop extraction."""

    def test_carry_strategy_excluded(self):
        """Position with carry strategy_id is not in extracted stops."""
        pos = _make_position(strategy_id="s57")
        spec = _make_strategy_spec(strategy_id="s57")
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s57": spec},
            last_known_atrs=atrs, carry_strategies=["s57"], tick_counter=10,
        )

        assert len(result) == 0

    def test_non_carry_strategy_included(self):
        """Position with non-carry strategy is included."""
        pos = _make_position(strategy_id="s56")
        spec = _make_strategy_spec(strategy_id="s56")
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=["s57"], tick_counter=10,
        )

        assert len(result) == 1


# ===================================================================
# Test: Linked positions excluded
# ===================================================================

class TestLinkedPositionExclusion:
    """Positions with linked_position_id are excluded."""

    def test_linked_position_excluded(self):
        """Position with linked_position_id is not in extracted stops."""
        pos = _make_position(linked_position_id="ETH:s57:100:hedge")
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert len(result) == 0

    def test_unlinked_position_included(self):
        """Position without linked_position_id is included."""
        pos = _make_position(linked_position_id=None)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert len(result) == 1


# ===================================================================
# Test: convex_exit flag
# ===================================================================

class TestConvexExitFlag:
    """convex_exit flag is correctly passed through."""

    def test_convex_exit_true(self):
        """convex_exit=True when position has convex_exit set."""
        pos = _make_position(convex_exit=True)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].convex_exit is True

    def test_convex_exit_false(self):
        """convex_exit=False when position does not have convex_exit."""
        pos = _make_position(convex_exit=False)
        spec = _make_strategy_spec()
        atrs = {"BTC": 1200.0}

        result = PaperPortfolioEngine._extract_stop_levels(
            positions=[pos], strategy_specs={"s56": spec},
            last_known_atrs=atrs, carry_strategies=[], tick_counter=10,
        )

        assert result[0].convex_exit is False


# ===================================================================
# Test: stops.json gating by sentinel_mode
# ===================================================================

class TestStopsJsonGating:
    """stops.json NOT written when sentinel_mode='off', written otherwise."""

    def test_stops_not_written_when_off(self, tmp_path):
        """No stops.json file when sentinel_mode='off'."""
        # Create an engine mock with sentinel_mode="off" and an open position
        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.config = MagicMock()
        engine.config.sentinel_mode = "off"
        engine.config.state_dir = str(tmp_path)

        pos = _make_position()
        engine.state = MagicMock()
        engine.state.position_manager.open_positions = [pos]

        # Call _write_stops -- should be a no-op when mode is "off"
        PaperPortfolioEngine._write_stops(engine)

        stops_file = tmp_path / "stops.json"
        assert not stops_file.exists()

    def test_stops_written_when_shadow(self, tmp_path):
        """_write_stops writes stops.json when sentinel_mode='shadow'."""
        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.config = MagicMock()
        engine.config.sentinel_mode = "shadow"
        engine.stop_store = StopStore(state_dir=tmp_path)
        engine.tick_counter = 10

        pos = _make_position(entry_bar=0)
        engine.state = MagicMock()
        engine.state.position_manager.open_positions = [pos]

        spec = _make_strategy_spec()
        engine.strategy_specs = {"s56": spec}
        engine.last_known_atrs = {"BTC": 1200.0}
        engine.config.carry_strategies = []

        PaperPortfolioEngine._write_stops(engine)

        stops_file = tmp_path / "stops.json"
        assert stops_file.exists()
