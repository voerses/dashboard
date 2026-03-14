"""AC8, AC10: Position management with atomic persistence and funding rates.

Tests verify:
- AC8: PositionManager tracks open positions with atomic persistence
        (write-tmp-then-rename pattern)
- AC10: Funding rates applied to perp positions on 8h settlement schedule
"""

import json
import os
import time

import pytest

from v3.position_manager import Position, PositionManager


# ---------------------------------------------------------------------------
# AC8: Position tracking and atomic persistence
# ---------------------------------------------------------------------------


class TestPositionDataclass:
    """Position has all required fields."""

    def test_position_has_entry_price(self):
        pos = Position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            funding_accrued=0.0, last_funding_time=1700000000,
            entry_bar=205, strategy_id="s11",
        )
        assert pos.entry_price == 40000.0

    def test_position_has_size_usd_and_units(self):
        pos = Position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            funding_accrued=0.0, last_funding_time=1700000000,
            entry_bar=205, strategy_id="s11",
        )
        assert pos.size_usd == 10000.0
        assert pos.size_units == pytest.approx(0.25)

    def test_position_has_stop_and_trail(self):
        pos = Position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            funding_accrued=0.0, last_funding_time=1700000000,
            entry_bar=205, strategy_id="s11",
        )
        assert pos.stop_price == 38800.0
        assert pos.trail_price == 39500.0

    def test_position_has_funding_fields(self):
        pos = Position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            funding_accrued=0.0, last_funding_time=1700000000,
            entry_bar=205, strategy_id="s11",
        )
        assert pos.funding_accrued == 0.0
        assert pos.last_funding_time == 1700000000

    def test_position_has_strategy_id(self):
        pos = Position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            funding_accrued=0.0, last_funding_time=1700000000,
            entry_bar=205, strategy_id="s11",
        )
        assert pos.strategy_id == "s11"


class TestPositionManagerOpenClose:
    """PositionManager tracks open positions."""

    def test_open_position_added(self, tmp_path):
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        positions = mgr.get_open_positions()
        assert len(positions) == 1
        assert positions[0].token == "BTC/USDT"

    def test_close_position_removes_it(self, tmp_path):
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.close_position(token="BTC/USDT", strategy_id="s11")
        positions = mgr.get_open_positions()
        assert len(positions) == 0

    def test_multiple_positions_tracked(self, tmp_path):
        mgr = PositionManager(state_dir=str(tmp_path))
        for token in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
            mgr.open_position(
                token=token, market="perp", side="long",
                entry_price=100.0, size_usd=5000.0, size_units=50.0,
                stop_price=95.0, trail_price=98.0,
                entry_bar=200, strategy_id="s11",
            )
        assert len(mgr.get_open_positions()) == 3

    def test_close_nonexistent_raises(self, tmp_path):
        mgr = PositionManager(state_dir=str(tmp_path))
        with pytest.raises(KeyError):
            mgr.close_position(token="DOGE/USDT", strategy_id="s11")


class TestAtomicPersistence:
    """Atomic persistence: write-tmp-then-rename."""

    def test_persist_creates_state_file(self, tmp_path):
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.persist()
        state_path = os.path.join(str(tmp_path), "positions.json")
        assert os.path.exists(state_path)

    def test_persisted_state_is_valid_json(self, tmp_path):
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.persist()
        state_path = os.path.join(str(tmp_path), "positions.json")
        with open(state_path, "r") as f:
            data = json.load(f)
        assert "positions" in data
        assert len(data["positions"]) == 1

    def test_no_tmp_file_remains_after_persist(self, tmp_path):
        """Atomic write: .tmp file is renamed, not left behind."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.persist()
        tmp_file = os.path.join(str(tmp_path), "positions.json.tmp")
        assert not os.path.exists(tmp_file)

    def test_load_restores_positions(self, tmp_path):
        """Loading from persisted state restores all positions."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.persist()

        # Load into a new manager instance
        mgr2 = PositionManager(state_dir=str(tmp_path))
        mgr2.load()
        positions = mgr2.get_open_positions()
        assert len(positions) == 1
        assert positions[0].token == "BTC/USDT"
        assert positions[0].entry_price == pytest.approx(40000.0)

    def test_persist_roundtrip_preserves_all_fields(self, tmp_path):
        """Persist-then-load preserves every position field."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="ETH/USDT", market="perp", side="long",
            entry_price=2500.0, size_usd=5000.0, size_units=2.0,
            stop_price=2400.0, trail_price=2450.0,
            entry_bar=210, strategy_id="s09",
        )
        mgr.persist()

        mgr2 = PositionManager(state_dir=str(tmp_path))
        mgr2.load()
        pos = mgr2.get_open_positions()[0]
        assert pos.token == "ETH/USDT"
        assert pos.market == "perp"
        assert pos.side == "long"
        assert pos.entry_price == pytest.approx(2500.0)
        assert pos.size_usd == pytest.approx(5000.0)
        assert pos.size_units == pytest.approx(2.0)
        assert pos.stop_price == pytest.approx(2400.0)
        assert pos.trail_price == pytest.approx(2450.0)
        assert pos.entry_bar == 210
        assert pos.strategy_id == "s09"

    def test_persist_during_crash_does_not_corrupt(self, tmp_path):
        """If .tmp exists but rename didn't happen, old state is still valid."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.persist()

        # Simulate a crash: write a new .tmp but don't rename
        tmp_file = os.path.join(str(tmp_path), "positions.json.tmp")
        with open(tmp_file, "w") as f:
            f.write("CORRUPT DATA")

        # Loading should use the valid positions.json, not the .tmp
        mgr2 = PositionManager(state_dir=str(tmp_path))
        mgr2.load()
        positions = mgr2.get_open_positions()
        assert len(positions) == 1
        assert positions[0].token == "BTC/USDT"


# ---------------------------------------------------------------------------
# AC10: Funding rates applied on 8h settlement schedule
# ---------------------------------------------------------------------------


class TestFundingRateApplication:
    """Funding rates applied to perp positions on 8h settlement schedule."""

    def test_apply_funding_updates_accrued(self, tmp_path):
        """Applying a funding rate adds to funding_accrued."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.apply_funding(
            token="BTC/USDT",
            strategy_id="s11",
            funding_rate=0.0001,
            settlement_time=1700000000 + 8 * 3600,
        )
        pos = mgr.get_position(token="BTC/USDT", strategy_id="s11")
        # funding_accrued = size_usd * funding_rate = 10000 * 0.0001 = 1.0
        assert pos.funding_accrued == pytest.approx(1.0)

    def test_funding_only_applied_at_8h_intervals(self, tmp_path):
        """Funding is not applied if less than 8h since last settlement."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        # Try applying funding only 4h after position open
        result = mgr.apply_funding(
            token="BTC/USDT",
            strategy_id="s11",
            funding_rate=0.0001,
            settlement_time=1700000000 + 4 * 3600,  # Only 4h
        )
        pos = mgr.get_position(token="BTC/USDT", strategy_id="s11")
        # Should NOT have been applied (less than 8h)
        assert pos.funding_accrued == pytest.approx(0.0)

    def test_multiple_funding_settlements_accumulate(self, tmp_path):
        """Multiple 8h settlements accumulate funding."""
        mgr = PositionManager(state_dir=str(tmp_path))
        base_time = 1700000000
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )

        # First settlement at +8h
        mgr.apply_funding(
            token="BTC/USDT", strategy_id="s11",
            funding_rate=0.0001,
            settlement_time=base_time + 8 * 3600,
        )
        # Second settlement at +16h
        mgr.apply_funding(
            token="BTC/USDT", strategy_id="s11",
            funding_rate=0.00015,
            settlement_time=base_time + 16 * 3600,
        )

        pos = mgr.get_position(token="BTC/USDT", strategy_id="s11")
        # 10000 * 0.0001 + 10000 * 0.00015 = 1.0 + 1.5 = 2.5
        assert pos.funding_accrued == pytest.approx(2.5)

    def test_funding_updates_last_funding_time(self, tmp_path):
        """After settlement, last_funding_time is updated."""
        mgr = PositionManager(state_dir=str(tmp_path))
        base_time = 1700000000
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        settlement_time = base_time + 8 * 3600
        mgr.apply_funding(
            token="BTC/USDT", strategy_id="s11",
            funding_rate=0.0001,
            settlement_time=settlement_time,
        )
        pos = mgr.get_position(token="BTC/USDT", strategy_id="s11")
        assert pos.last_funding_time == settlement_time

    def test_negative_funding_rate_decreases_accrued(self, tmp_path):
        """Negative funding rates decrease (or go negative) the accrued amount."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="perp", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.apply_funding(
            token="BTC/USDT", strategy_id="s11",
            funding_rate=-0.0002,
            settlement_time=1700000000 + 8 * 3600,
        )
        pos = mgr.get_position(token="BTC/USDT", strategy_id="s11")
        # 10000 * -0.0002 = -2.0
        assert pos.funding_accrued == pytest.approx(-2.0)

    def test_funding_not_applied_to_spot_positions(self, tmp_path):
        """Spot positions should not receive funding rate settlements."""
        mgr = PositionManager(state_dir=str(tmp_path))
        mgr.open_position(
            token="BTC/USDT", market="spot", side="long",
            entry_price=40000.0, size_usd=10000.0, size_units=0.25,
            stop_price=38800.0, trail_price=39500.0,
            entry_bar=205, strategy_id="s11",
        )
        mgr.apply_funding(
            token="BTC/USDT", strategy_id="s11",
            funding_rate=0.0001,
            settlement_time=1700000000 + 8 * 3600,
        )
        pos = mgr.get_position(token="BTC/USDT", strategy_id="s11")
        assert pos.funding_accrued == pytest.approx(0.0)
