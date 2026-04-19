"""Acceptance tests for Task 4: State Persistence.

Tests verify:
  - Serialize SimulationState to JSON and deserialize back (round-trip identity)
  - Position serialization (including numpy arrays like trail_schedule)
  - Atomic write (write to temp, rename)
  - Atomic write crash recovery (C8)
  - trades.jsonl append (new closed trades appended, existing preserved)
  - equity.csv append
  - equity.csv shadow pool columns (H8/AC25)
  - State restoration: load state, verify open positions, equity components, tick_counter
  - Shadow rebalancer state in serialize/deserialize (H5)
  - closed_trades is empty after deserialization (M1)
  - linked_exit reason preserved in trades.jsonl (M11)
  - Position ID format includes tick_counter

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until paper_state.py is implemented (RED phase).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v5.paper_state import (
    serialize_state,
    deserialize_state,
    atomic_write_state,
    append_trades,
    append_equity,
    make_position_id,
)
from v5.position import Position, ClosedTrade, PositionManager
from v5.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_position(
    token: str = "BTC",
    strategy_id: str = "s30",
    entry_bar: int = 42,
    entry_price: float = 68_000.0,
    margin: float = 10_000.0,
    leverage: float = 1.0,
    direction: int = 1,
    is_perp: bool = True,
    cumulative_funding: float = -45.23,
    trail_schedule: np.ndarray | None = None,
) -> Position:
    """Build a Position with representative fields for serialization tests."""
    notional = margin * leverage
    quantity = direction * notional / entry_price
    pid = f"{token}:{strategy_id}:{entry_bar}:primary"
    return Position(
        position_id=pid,
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=quantity,
        margin_usd=margin,
        leverage=leverage,
        is_perp=is_perp,
        fee_rate=0.0005,
        stop_mult=2.5,
        trail_mult=3.0,
        target_mult=6.0,
        no_stop_bars=6,
        min_hold=12,
        max_hold=720,
        convex_exit=False,
        rsi_exit_level=999.0,
        trail_schedule=trail_schedule,
        stop_price=65_000.0,
        highest=72_000.0,
        lowest=68_000.0,
        initial_risk=2_000.0,
        cumulative_funding=cumulative_funding,
    )


def _make_test_state() -> tuple[SimulationState, int]:
    """Build a SimulationState with open positions and equity components."""
    state = SimulationState(initial_capital=200_000.0)
    state.realized_pnl = 15_234.56
    state.total_fees = 1_234.56
    state.total_funding = -567.89
    pos1 = _make_test_position()
    pos2 = _make_test_position(token="ETH", entry_price=3_500.0, margin=5_000.0)
    state.position_manager.open_position(pos1)
    state.position_manager.open_position(pos2)
    state._entry_fees_by_pos["BTC:s30:42:primary"] = 5.0
    state._entry_fees_by_pos["ETH:s30:42:primary"] = 2.5
    tick_counter = 42
    return state, tick_counter


# ===================================================================
# Test: Serialize/Deserialize round-trip identity
# ===================================================================

class TestSerializationRoundTrip:
    """SimulationState must serialize to JSON and deserialize back identically."""

    def test_state_round_trip(self):
        """Serialize state -> JSON -> deserialize => identical state."""
        state, tick_counter = _make_test_state()

        json_data = serialize_state(state, tick_counter, "2026-03-09T20:00:00Z")
        restored_state, restored_tick = deserialize_state(json_data)

        assert restored_tick == tick_counter
        assert restored_state.initial_capital == pytest.approx(state.initial_capital)
        assert restored_state.realized_pnl == pytest.approx(state.realized_pnl)
        assert restored_state.total_fees == pytest.approx(state.total_fees)
        assert restored_state.total_funding == pytest.approx(state.total_funding)
        assert restored_state.portfolio_equity == pytest.approx(state.portfolio_equity)
        assert restored_state.position_manager.total_open() == 2

    def test_round_trip_preserves_position_fields(self):
        """Each Position field must survive the round-trip."""
        state, tick_counter = _make_test_state()

        json_data = serialize_state(state, tick_counter, "2026-03-09T20:00:00Z")
        restored_state, _ = deserialize_state(json_data)

        orig_pos = state.position_manager.open_positions[0]
        rest_pos = restored_state.position_manager.open_positions[0]

        assert rest_pos.position_id == orig_pos.position_id
        assert rest_pos.token == orig_pos.token
        assert rest_pos.strategy_id == orig_pos.strategy_id
        assert rest_pos.leg == orig_pos.leg
        assert rest_pos.entry_bar == orig_pos.entry_bar
        assert rest_pos.entry_price == pytest.approx(orig_pos.entry_price)
        assert rest_pos.direction == orig_pos.direction
        assert rest_pos.quantity == pytest.approx(orig_pos.quantity)
        assert rest_pos.margin_usd == pytest.approx(orig_pos.margin_usd)
        assert rest_pos.leverage == pytest.approx(orig_pos.leverage)
        assert rest_pos.is_perp == orig_pos.is_perp
        assert rest_pos.fee_rate == pytest.approx(orig_pos.fee_rate)
        assert rest_pos.stop_mult == pytest.approx(orig_pos.stop_mult)
        assert rest_pos.trail_mult == pytest.approx(orig_pos.trail_mult)
        assert rest_pos.target_mult == pytest.approx(orig_pos.target_mult)
        assert rest_pos.no_stop_bars == orig_pos.no_stop_bars
        assert rest_pos.min_hold == orig_pos.min_hold
        assert rest_pos.max_hold == orig_pos.max_hold
        assert rest_pos.stop_price == pytest.approx(orig_pos.stop_price)
        assert rest_pos.highest == pytest.approx(orig_pos.highest)
        assert rest_pos.lowest == pytest.approx(orig_pos.lowest)
        assert rest_pos.initial_risk == pytest.approx(orig_pos.initial_risk)
        assert rest_pos.cumulative_funding == pytest.approx(orig_pos.cumulative_funding)

    def test_entry_fees_by_pos_round_trip(self):
        """_entry_fees_by_pos dict must survive round-trip."""
        state, tick_counter = _make_test_state()

        json_data = serialize_state(state, tick_counter, "2026-03-09T20:00:00Z")
        restored_state, _ = deserialize_state(json_data)

        assert "BTC:s30:42:primary" in restored_state._entry_fees_by_pos
        assert restored_state._entry_fees_by_pos["BTC:s30:42:primary"] == pytest.approx(5.0)
        assert restored_state._entry_fees_by_pos["ETH:s30:42:primary"] == pytest.approx(2.5)


# ===================================================================
# Test: Position serialization with numpy arrays and sets
# ===================================================================

class TestPositionSerialization:
    """Position fields with numpy arrays and sets must serialize correctly."""

    def test_trail_schedule_numpy_array_preserved(self):
        """trail_schedule (numpy array) must survive serialization."""
        trail_sched = np.array([1.0, 1.5, 2.0, 2.5, 3.0], dtype=np.float64)
        pos = _make_test_position(trail_schedule=trail_sched)

        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)

        json_data = serialize_state(state, 10, "2026-03-09T20:00:00Z")
        restored_state, _ = deserialize_state(json_data)

        rest_pos = restored_state.position_manager.open_positions[0]
        assert rest_pos.trail_schedule is not None
        np.testing.assert_array_almost_equal(rest_pos.trail_schedule, trail_sched)

    def test_none_trail_schedule_preserved(self):
        """trail_schedule=None must survive serialization."""
        pos = _make_test_position(trail_schedule=None)

        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)

        json_data = serialize_state(state, 10, "2026-03-09T20:00:00Z")
        restored_state, _ = deserialize_state(json_data)

        rest_pos = restored_state.position_manager.open_positions[0]
        assert rest_pos.trail_schedule is None


# ===================================================================
# Test: Atomic write (write to temp, rename)
# ===================================================================

class TestAtomicWrite:
    """State file is written atomically (write to temp, rename)."""

    def test_atomic_write_creates_file(self):
        """atomic_write_state writes the state to the target path."""
        state, tick_counter = _make_test_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "state.json")

            atomic_write_state(state, tick_counter, "2026-03-09T20:00:00Z", target)

            assert os.path.exists(target)
            with open(target) as f:
                data = json.load(f)
            assert data["tick_counter"] == 42
            assert data["initial_capital"] == pytest.approx(200_000.0)

    def test_atomic_write_is_valid_json(self):
        """The written file must be valid JSON."""
        state, tick_counter = _make_test_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "state.json")
            atomic_write_state(state, tick_counter, "2026-03-09T20:00:00Z", target)

            with open(target) as f:
                data = json.load(f)  # must not raise
            assert "version" in data
            assert "open_positions" in data

    def test_atomic_write_overwrites_existing(self):
        """Re-writing state should overwrite the existing file."""
        state, tick_counter = _make_test_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "state.json")

            atomic_write_state(state, tick_counter, "2026-03-09T20:00:00Z", target)
            state.realized_pnl = 99_999.0
            atomic_write_state(state, tick_counter + 1, "2026-03-09T21:00:00Z", target)

            with open(target) as f:
                data = json.load(f)
            assert data["realized_pnl"] == pytest.approx(99_999.0)
            assert data["tick_counter"] == 43


# ===================================================================
# Test: trades.jsonl append
# ===================================================================

class TestTradesAppend:
    """trades.jsonl: new closed trades appended, existing preserved."""

    def test_append_new_trades(self):
        """New trades are appended as JSON lines."""
        trades = [
            ClosedTrade(
                position_id="BTC:s30:10:primary", token="BTC", strategy_id="s30",
                leg="primary", entry_bar=10, exit_bar=37, entry_price=65_000.0,
                exit_price=68_000.0, direction=1, margin_usd=10_000.0,
                pnl=432.10, funding_cost=-23.45, entry_fee=5.0, exit_fee=5.10,
                hold_bars=27, exit_reason="target", is_perp=True,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.jsonl")
            append_trades(trades, path)

            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["position_id"] == "BTC:s30:10:primary"
            assert data["pnl"] == pytest.approx(432.10)
            assert data["exit_reason"] == "target"

    def test_append_preserves_existing(self):
        """Appending does not overwrite existing lines."""
        trade1 = ClosedTrade(
            position_id="BTC:s30:10:primary", token="BTC", strategy_id="s30",
            leg="primary", entry_bar=10, exit_bar=37, entry_price=65_000.0,
            exit_price=68_000.0, direction=1, margin_usd=10_000.0,
            pnl=432.10, funding_cost=-23.45, entry_fee=5.0, exit_fee=5.10,
            hold_bars=27, exit_reason="target", is_perp=True,
        )
        trade2 = ClosedTrade(
            position_id="ETH:s30:15:primary", token="ETH", strategy_id="s30",
            leg="primary", entry_bar=15, exit_bar=40, entry_price=3_500.0,
            exit_price=3_600.0, direction=1, margin_usd=5_000.0,
            pnl=142.85, funding_cost=-5.0, entry_fee=2.5, exit_fee=2.6,
            hold_bars=25, exit_reason="regime", is_perp=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.jsonl")
            append_trades([trade1], path)
            append_trades([trade2], path)

            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 2
            assert json.loads(lines[0])["token"] == "BTC"
            assert json.loads(lines[1])["token"] == "ETH"


# ===================================================================
# Test: equity.csv append
# ===================================================================

class TestEquityAppend:
    """equity.csv: one row per tick, appended."""

    def test_append_equity_creates_with_header(self):
        """First write creates CSV with header row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "equity.csv")
            append_equity(
                path=path,
                timestamp="2026-03-09T20:00:00Z",
                tick=42,
                portfolio_equity=215_234.56,
                mark_to_market_equity=218_500.0,
                free_capital=165_234.56,
                open_positions=8,
                spot_shadow_free=95_000.0,
                perp_shadow_free=105_000.0,
                spot_deployed=30_000.0,
                perp_deployed=50_000.0,
                imbalance_pct=5.0,
            )

            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 2  # header + 1 data row
            assert "timestamp" in lines[0]
            assert "portfolio_equity" in lines[0]
            assert "215234.56" in lines[1]

    def test_append_equity_preserves_existing(self):
        """Second write appends without duplicate header."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "equity.csv")
            for tick in range(3):
                append_equity(
                    path=path,
                    timestamp=f"2026-03-09T{20 + tick}:00:00Z",
                    tick=tick,
                    portfolio_equity=200_000.0 + tick * 100,
                    mark_to_market_equity=200_000.0 + tick * 150,
                    free_capital=180_000.0,
                    open_positions=tick,
                    spot_shadow_free=95_000.0,
                    perp_shadow_free=105_000.0,
                    spot_deployed=0.0,
                    perp_deployed=0.0,
                    imbalance_pct=0.0,
                )

            with open(path) as f:
                lines = f.readlines()
            # 1 header + 3 data rows
            assert len(lines) == 4
            assert lines[0].startswith("timestamp")


# ===================================================================
# Test: State restoration
# ===================================================================

class TestStateRestoration:
    """Load state, verify open positions, equity components, tick_counter."""

    def test_restore_from_file(self):
        """Write state to file, load it back, verify all components."""
        state, tick_counter = _make_test_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "state.json")
            atomic_write_state(state, tick_counter, "2026-03-09T20:00:00Z", target)

            with open(target) as f:
                json_data = json.load(f)

            restored_state, restored_tick = deserialize_state(json_data)

            assert restored_tick == 42
            assert restored_state.initial_capital == pytest.approx(200_000.0)
            assert restored_state.realized_pnl == pytest.approx(15_234.56)
            assert restored_state.total_fees == pytest.approx(1_234.56)
            assert restored_state.total_funding == pytest.approx(-567.89)
            assert restored_state.position_manager.total_open() == 2

            tokens = {p.token for p in restored_state.position_manager.open_positions}
            assert tokens == {"BTC", "ETH"}


# ===================================================================
# Test: Position ID format includes tick_counter
# ===================================================================

class TestPositionIdFormat:
    """Position IDs use tick_counter: '{token}:{strategy_id}:{tick_counter}:{leg}'."""

    def test_make_position_id_primary(self):
        pid = make_position_id("BTC", "s56", 42, "primary")
        assert pid == "BTC:s56:42:primary"

    def test_make_position_id_secondary(self):
        pid = make_position_id("ETH", "s57", 100, "secondary")
        assert pid == "ETH:s57:100:secondary"

    def test_position_id_contains_tick_counter(self):
        """The tick_counter (not bar index or timestamp) is in the ID."""
        pid = make_position_id("SOL", "s30", 9999, "primary")
        parts = pid.split(":")
        assert len(parts) == 4
        assert parts[0] == "SOL"
        assert parts[1] == "s30"
        assert parts[2] == "9999"
        assert parts[3] == "primary"


# ===================================================================
# Test: Atomic write crash recovery (C8)
# ===================================================================

class TestAtomicWriteCrashRecovery:
    """Crash during atomic write must not corrupt existing state file (AC9)."""

    def test_rename_failure_preserves_original_state(self):
        """If os.rename raises OSError after temp file is written, original
        state file must still contain v1 data with no partial writes."""
        state_v1, tick_v1 = _make_test_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "state.json")

            # Write v1 successfully
            atomic_write_state(state_v1, tick_v1, "2026-03-09T20:00:00Z", target)

            # Read v1 back to confirm baseline
            with open(target) as f:
                v1_data = json.load(f)
            assert v1_data["tick_counter"] == tick_v1
            assert v1_data["realized_pnl"] == pytest.approx(15_234.56)

            # Prepare v2 state (different values)
            state_v2, tick_v2 = _make_test_state()
            state_v2.realized_pnl = 99_999.0
            tick_v2 = tick_v1 + 1

            # Patch os.rename to raise OSError (simulating crash after temp write)
            with patch("os.rename", side_effect=OSError("disk failure")):
                with pytest.raises(OSError):
                    atomic_write_state(state_v2, tick_v2, "2026-03-09T21:00:00Z", target)

            # Original file must still contain v1 data
            with open(target) as f:
                preserved_data = json.load(f)
            assert preserved_data["tick_counter"] == tick_v1
            assert preserved_data["realized_pnl"] == pytest.approx(15_234.56)

    def test_no_partial_writes_on_crash(self):
        """After a failed write, no temp files are left behind AND the
        original state file is not a mix of v1+v2 data."""
        state_v1, tick_v1 = _make_test_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "state.json")

            # Write v1 successfully
            atomic_write_state(state_v1, tick_v1, "2026-03-09T20:00:00Z", target)

            state_v2, _ = _make_test_state()
            state_v2.realized_pnl = 77_777.0

            with patch("os.rename", side_effect=OSError("disk failure")):
                with pytest.raises(OSError):
                    atomic_write_state(state_v2, tick_v1 + 1, "2026-03-09T21:00:00Z", target)

            # Verify original file is complete valid JSON (no partial writes)
            with open(target) as f:
                raw = f.read()
            data = json.loads(raw)  # must parse without error
            assert data["realized_pnl"] == pytest.approx(15_234.56)
            # v2 value must NOT appear anywhere in the file
            assert "77777" not in raw


# ===================================================================
# Test: Shadow rebalancer state in serialize/deserialize (H5)
# ===================================================================

class TestShadowPoolSerialization:
    """Shadow pool values (spot_funds, perp_funds, spot_deployed, perp_deployed)
    must survive serialize -> deserialize round-trip (AC22, design notes)."""

    def test_shadow_pools_round_trip(self):
        """Create state with shadow pools, serialize, deserialize, verify all
        shadow pool values match."""
        state, tick_counter = _make_test_state()

        # Serialize with shadow pool data
        json_data = serialize_state(
            state, tick_counter, "2026-03-09T20:00:00Z",
            shadow_pools={
                "spot_funds": 95_000.0,
                "perp_funds": 105_000.0,
                "spot_deployed": 30_000.0,
                "perp_deployed": 50_000.0,
            },
        )

        # shadow_pools must be present in the serialized data
        assert "shadow_pools" in json_data
        assert json_data["shadow_pools"]["spot_funds"] == pytest.approx(95_000.0)
        assert json_data["shadow_pools"]["perp_funds"] == pytest.approx(105_000.0)
        assert json_data["shadow_pools"]["spot_deployed"] == pytest.approx(30_000.0)
        assert json_data["shadow_pools"]["perp_deployed"] == pytest.approx(50_000.0)

        # Deserialize and verify shadow pool values are returned
        restored_state, restored_tick, restored_shadow = deserialize_state(
            json_data, return_shadow=True,
        )

        assert restored_shadow["spot_funds"] == pytest.approx(95_000.0)
        assert restored_shadow["perp_funds"] == pytest.approx(105_000.0)
        assert restored_shadow["spot_deployed"] == pytest.approx(30_000.0)
        assert restored_shadow["perp_deployed"] == pytest.approx(50_000.0)


# ===================================================================
# Test: equity.csv shadow pool columns (H8 / AC25)
# ===================================================================

class TestEquityShadowColumns:
    """equity.csv must include shadow pool columns per AC25."""

    def test_shadow_columns_present_in_csv(self):
        """Write equity row with shadow values, read CSV back, parse shadow
        columns, verify values match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "equity.csv")
            append_equity(
                path=path,
                timestamp="2026-03-09T20:00:00Z",
                tick=42,
                portfolio_equity=215_234.56,
                mark_to_market_equity=218_500.0,
                free_capital=165_234.56,
                open_positions=8,
                spot_shadow_free=65_000.0,
                perp_shadow_free=55_000.0,
                spot_deployed=30_000.0,
                perp_deployed=50_000.0,
                imbalance_pct=8.3,
            )

            # Read CSV and parse with csv.DictReader for column-level access
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            assert len(rows) == 1
            row = rows[0]

            # AC25 shadow columns must be present and correct
            assert float(row["spot_shadow_free"]) == pytest.approx(65_000.0)
            assert float(row["perp_shadow_free"]) == pytest.approx(55_000.0)
            assert float(row["spot_deployed"]) == pytest.approx(30_000.0)
            assert float(row["perp_deployed"]) == pytest.approx(50_000.0)
            assert float(row["imbalance_pct"]) == pytest.approx(8.3)


# ===================================================================
# Test: closed_trades is empty after deserialization (M1)
# ===================================================================

class TestClosedTradesEmptyAfterDeserialize:
    """Closed trades go to trades.jsonl, not state.json. After deserialization
    the position_manager.closed_trades must be an empty list."""

    def test_closed_trades_empty_after_deserialize(self):
        """Deserialize state and verify position_manager.closed_trades is empty."""
        state, tick_counter = _make_test_state()

        # Add a closed trade to the state before serialization
        # (this simulates a trade that was closed during the session)
        pos = _make_test_position(token="SOL", entry_price=150.0, margin=2_000.0)
        state.position_manager.open_position(pos)
        state.position_manager.close_position(
            pos, exit_bar=50, exit_price=160.0,
            pnl=133.0, funding_cost=0.0, entry_fee=1.0,
            exit_fee=1.0, exit_reason="target",
        )
        # Confirm closed_trades is non-empty before serialization
        assert len(state.position_manager.closed_trades) == 1

        json_data = serialize_state(state, tick_counter, "2026-03-09T20:00:00Z")
        restored_state, _ = deserialize_state(json_data)

        # closed_trades must be empty after deserialization
        # (they belong in trades.jsonl, not state.json).
        # M7 AC-H1 row #13: M3 shipped bounded deque(maxlen=1000); test
        # originally pinned `list`. Per CLAUDE.md meta-rule #1 (code over
        # specs), accept either container — the invariant is "empty after
        # deserialize", not the container type.
        import collections
        assert isinstance(
            restored_state.position_manager.closed_trades,
            (list, collections.deque),
        )
        assert len(restored_state.position_manager.closed_trades) == 0


# ===================================================================
# Test: linked_exit reason in trades.jsonl (M11)
# ===================================================================

class TestLinkedExitReasonInTradesJSONL:
    """linked_exit is a valid exit_reason and must be preserved in trades.jsonl."""

    def test_linked_exit_reason_preserved(self):
        """Create ClosedTrade with exit_reason='linked_exit', append to
        trades.jsonl, read back, verify exit_reason is preserved."""
        trade = ClosedTrade(
            position_id="BTC:s56:10:secondary",
            token="BTC",
            strategy_id="s56",
            leg="secondary",
            entry_bar=10,
            exit_bar=25,
            entry_price=68_000.0,
            exit_price=70_000.0,
            direction=-1,
            margin_usd=10_000.0,
            pnl=-294.12,
            funding_cost=-12.5,
            entry_fee=5.0,
            exit_fee=5.25,
            hold_bars=15,
            exit_reason="linked_exit",
            is_perp=True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.jsonl")
            append_trades([trade], path)

            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["exit_reason"] == "linked_exit"
            assert data["position_id"] == "BTC:s56:10:secondary"
            assert data["leg"] == "secondary"
