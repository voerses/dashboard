"""Acceptance tests for Task 5: Sentinel exit processing in hourly engine (AC11, AC16).

Tests verify:
  - _process_sentinel_exits closes position and records trade
  - PnL uses raw_pnl (not net) for state.realized_pnl
  - No ADV slippage applied
  - Exit fee computed from fee_rate
  - Idempotent: skip if position already closed
  - Runs BEFORE _process_exits_for_tick
  - Gated: not called when sentinel_mode="off"
  - exit_reason recorded as sentinel_*
  - Atomic rename-based clearing of exit_events.jsonl

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until sentinel exit processing is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_engine import PaperPortfolioEngine
from v4.position import PositionManager
from v4.stop_store import StopStore, ExitEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exit_event(**overrides) -> ExitEvent:
    """Build an ExitEvent with representative defaults."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        direction=1,
        exit_price=63_500.0,
        stop_price=64_000.0,
        breach_price=63_800.0,
        breach_timestamp="2025-10-15T14:30:00Z",
        confirm_timestamp="2025-10-15T14:30:30Z",
        slippage_bps=78.0,
        sentinel_timestamp="2025-10-15T14:30:30Z",
        margin_usd=10_000.0,
        quantity=0.15,
        entry_price=66_000.0,
        exit_reason="sentinel_stop",
    )
    defaults.update(overrides)
    return ExitEvent(**defaults)


def _make_position_mock(
    position_id: str = "BTC:s56:100:primary",
    token: str = "BTC",
    strategy_id: str = "s56",
    leg: str = "primary",
    direction: int = 1,
    entry_price: float = 66_000.0,
    quantity: float = 0.15,
    margin_usd: float = 10_000.0,
    leverage: float = 1.0,
    fee_rate: float = 0.0004,
    cumulative_funding: float = -12.5,
    is_perp: bool = True,
    entry_bar: int = 0,
    entry_timestamp: str = "2025-10-15T12:00:00Z",
) -> MagicMock:
    """Build a mock position for exit processing tests.

    Includes all fields that PositionManager.close_position() reads
    when creating a ClosedTrade record.
    """
    pos = MagicMock()
    pos.position_id = position_id
    pos.token = token
    pos.strategy_id = strategy_id
    pos.leg = leg
    pos.direction = direction
    pos.entry_price = entry_price
    pos.quantity = quantity
    pos.margin_usd = margin_usd
    pos.leverage = leverage
    pos.fee_rate = fee_rate
    pos.cumulative_funding = cumulative_funding
    pos.is_perp = is_perp
    pos.entry_bar = entry_bar
    pos.entry_timestamp = entry_timestamp
    return pos


# ===================================================================
# Test: _process_sentinel_exits closes position and records trade
# ===================================================================

class TestSentinelExitProcessing:
    """_process_sentinel_exits closes positions based on exit events."""

    def test_position_closed_on_exit_event(self, tmp_path):
        """Exit event causes the matching position to be closed."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event(position_id="BTC:s56:100:primary")
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        # Use real PositionManager so close_position() actually modifies lists
        pos = _make_position_mock(position_id="BTC:s56:100:primary")
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        # Position should have been removed from open_positions
        remaining_ids = [p.position_id for p in pm.open_positions]
        assert "BTC:s56:100:primary" not in remaining_ids

    def test_trade_recorded_on_exit(self, tmp_path):
        """Exit event produces a trade record in closed_trades."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event()
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        # Use real PositionManager so close_position() actually appends trade
        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        assert len(pm.closed_trades) >= 1


# ===================================================================
# Test: PnL uses raw_pnl (not net) for state.realized_pnl
# ===================================================================

class TestSentinelPnlAccounting:
    """PnL accounting uses raw_pnl for realized_pnl updates.

    Key invariant from design: state.realized_pnl += raw_pnl (NOT net_pnl).
    raw_pnl = quantity * (exit_price - entry_price) for longs.
    Fees go to state.total_fees separately.
    """

    def test_realized_pnl_updated_by_raw_pnl(self, tmp_path):
        """state.realized_pnl receives raw_pnl, not net_pnl after fees/funding."""
        store = StopStore(state_dir=tmp_path)
        # Long position: entry 66000, exit 63500 → raw_pnl = 0.15 * (63500 - 66000) = -375
        event = _make_exit_event(
            exit_price=63_500.0, entry_price=66_000.0, quantity=0.15,
        )
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        pos = _make_position_mock(
            entry_price=66_000.0, quantity=0.15, fee_rate=0.0004,
            cumulative_funding=-12.5,
        )
        # Use real PositionManager so close_position() works correctly
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        # raw_pnl = 0.15 * (63500 - 66000) = -375.0
        # realized_pnl should be raw_pnl, NOT raw_pnl - exit_fee - funding
        assert engine.state.realized_pnl == pytest.approx(-375.0)

    def test_exit_fee_added_to_total_fees(self, tmp_path):
        """Exit fee goes to state.total_fees, not subtracted from realized_pnl."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event(
            exit_price=63_500.0, entry_price=66_000.0, quantity=0.15,
        )
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        pos = _make_position_mock(fee_rate=0.0004, quantity=0.15)
        # Use real PositionManager so close_position() works correctly
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        # exit_fee = abs(0.15) * 63500 * 0.0004 = 3.81
        assert engine.state.total_fees == pytest.approx(3.81)


# ===================================================================
# Test: No ADV slippage applied
# ===================================================================

class TestNoAdvSlippage:
    """Sentinel exits use exit_price directly, no ADV-based slippage."""

    def test_trade_records_exact_sentinel_exit_price(self, tmp_path):
        """The closed trade records the sentinel exit_price with no ADV adjustment."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event(exit_price=63_500.0)
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        # Use real PositionManager so close_position() creates actual ClosedTrade
        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        # The trade should record 63500.0 exactly -- no ADV slippage applied
        assert len(pm.closed_trades) >= 1
        trade = pm.closed_trades[0]
        assert trade.exit_price == pytest.approx(63_500.0)


# ===================================================================
# Test: Idempotent -- skip if position already closed
# ===================================================================

class TestIdempotentExit:
    """Exit events for already-closed positions are no-ops."""

    def test_skip_already_closed_position(self, tmp_path):
        """Exit event for a missing position_id is silently skipped."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event(position_id="ALREADY_CLOSED:s56:100:primary")
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        # Use real PositionManager with no positions
        pm = PositionManager()
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        # Should not raise
        PaperPortfolioEngine._process_sentinel_exits(engine)

        # No trades should have been recorded
        assert len(pm.closed_trades) == 0


# ===================================================================
# Test: Runs BEFORE _process_exits_for_tick
# ===================================================================

class TestExecutionOrder:
    """_process_sentinel_exits runs before _process_exits_for_tick."""

    def test_sentinel_exits_before_regular_exits(self):
        """The sentinel exit method is called before regular exit processing.

        Verify by mocking both methods and checking call order in tick pipeline.
        """
        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.config = MagicMock()
        engine.config.sentinel_mode = "shadow"

        call_order = []
        engine._process_sentinel_exits = lambda: call_order.append("sentinel")
        engine._process_exits_for_tick = lambda *a, **kw: call_order.append("regular")
        engine._process_margin_calls_for_tick = lambda *a, **kw: None
        engine._process_entries_for_tick = lambda *a, **kw: None

        PaperPortfolioEngine._tick_internal_with_signals(
            engine, all_signals={}, strategy_specs={}, bar_maps={},
        )

        assert call_order.index("sentinel") < call_order.index("regular")


# ===================================================================
# Test: Gated by sentinel_mode
# ===================================================================

class TestSentinelModeGating:
    """_process_sentinel_exits is not called when sentinel_mode='off'."""

    def test_not_called_when_off(self, tmp_path):
        """No exit processing when sentinel_mode='off'."""
        store = StopStore(state_dir=tmp_path)
        event = _make_exit_event()
        store.append_exit_event(event)

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "off"
        engine.tick_counter = 10

        # Use real PositionManager — position should NOT be removed
        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm

        PaperPortfolioEngine._process_sentinel_exits(engine)

        # When mode is off, position should still be open
        remaining_ids = [p.position_id for p in pm.open_positions]
        assert "BTC:s56:100:primary" in remaining_ids


# ===================================================================
# Test: exit_reason recorded as sentinel_*
# ===================================================================

class TestExitReason:
    """Exit events have sentinel-specific exit reasons passed through to ClosedTrade."""

    def test_sentinel_stop_reason_in_trade(self, tmp_path):
        """exit_reason='sentinel_stop' is recorded on the ClosedTrade."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event(exit_reason="sentinel_stop"))

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        assert len(pm.closed_trades) == 1
        assert pm.closed_trades[0].exit_reason == "sentinel_stop"

    def test_sentinel_cb_reason_in_trade(self, tmp_path):
        """exit_reason='sentinel_cb' is recorded on the ClosedTrade."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event(exit_reason="sentinel_cb"))

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        assert len(pm.closed_trades) == 1
        assert pm.closed_trades[0].exit_reason == "sentinel_cb"

    def test_sentinel_target_reason_in_trade(self, tmp_path):
        """exit_reason='sentinel_target' is recorded on the ClosedTrade."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event(exit_reason="sentinel_target"))

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        assert len(pm.closed_trades) == 1
        assert pm.closed_trades[0].exit_reason == "sentinel_target"

    def test_sentinel_liq_reason_in_trade(self, tmp_path):
        """exit_reason='sentinel_liq' is recorded on the ClosedTrade."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event(exit_reason="sentinel_liq"))

        engine = MagicMock(spec=PaperPortfolioEngine)
        engine.stop_store = store
        engine.config = MagicMock()
        engine.config.sentinel_mode = "live"
        engine.tick_counter = 10

        pos = _make_position_mock()
        pm = PositionManager()
        pm.open_positions.append(pos)
        engine.state = MagicMock()
        engine.state.position_manager = pm
        engine.state.realized_pnl = 0.0
        engine.state.total_fees = 0.0

        PaperPortfolioEngine._process_sentinel_exits(engine)

        assert len(pm.closed_trades) == 1
        assert pm.closed_trades[0].exit_reason == "sentinel_liq"


# ===================================================================
# Test: Atomic rename-based clearing
# ===================================================================

class TestAtomicClearingOnProcess:
    """Exit events file is atomically cleared via rename after processing."""

    def test_exit_events_cleared_after_processing(self, tmp_path):
        """exit_events.jsonl is cleared after _process_sentinel_exits."""
        store = StopStore(state_dir=tmp_path)
        store.append_exit_event(_make_exit_event())

        events_file = tmp_path / "exit_events.jsonl"
        assert events_file.exists()

        # read_and_clear should remove the file
        events = store.read_and_clear_exit_events()
        assert len(events) == 1
        assert not events_file.exists()
