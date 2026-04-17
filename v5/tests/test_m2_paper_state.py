"""Acceptance tests for M2 — paper_state serialization of scaling state.

Covers:
  - AC31: New Position / ClosedTrade fields roundtrip through paper_state
  - AC31a: v4 paper-state compat loader accepts v4 JSON and synthesizes defaults
  - AC33: load_trade_log() compat parser reads v4 ':partial' suffix trades
  - AC34: legacy position_id retained, ':partial' suffix retired in v5
  - Q6: scaling_events flush cadence (heartbeat granularity tolerance)

All tests MUST FAIL until paper_state changes and load_trade_log land.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.position import Position, ClosedTrade, ScalingEvent  # noqa: E402
from v5.simulator import SimulationState  # noqa: E402


FIXTURE_PATH = (
    _project_root
    / ".specs"
    / "active"
    / "m2-position-scaling"
    / "tests-snapshot"
    / "fixtures"
    / "v4_paper_state_s501.json"
)


def _make_position_with_scaling() -> Position:
    pos = Position(
        position_id="BTC:s30:5:primary",
        token="BTC", strategy_id="s30", leg="primary",
        entry_bar=5, entry_price=100.0, direction=1,
        quantity=7.0, margin_usd=700.0, leverage=1.0, is_perp=True,
        fee_rate=0.0005, stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
        no_stop_bars=6, min_hold=6, max_hold=720,
        stop_price=95.0, highest=110.0, lowest=95.0,
        initial_risk=5.0, cumulative_funding=-1.23,
    )
    # Populate scaling-related state
    pos.scale_count = 2
    pos.r_anchor_price = 100.0
    pos._scale_action_bar = 42
    pos._helper_state = {"tp_ladder_atr__": {"fired": [0, 1]}}
    pos.scaling_events = [
        ScalingEvent(
            bar=10, kind="increase", fill_price=98.0,
            qty_delta=2.0, requested_qty_delta=2.0,
            margin_delta=196.0, fill_notional=196.0,
            entry_fee_delta=0.098, exit_fee=0.0,
            slippage_bps=2.0, atr_at_event=5.0, is_stop_like=False,
        ),
        ScalingEvent(
            bar=20, kind="reduce", fill_price=108.0,
            qty_delta=-3.0, requested_qty_delta=-3.0,
            margin_delta=-324.0, fill_notional=324.0,
            entry_fee_delta=0.0, exit_fee=0.162,
            slippage_bps=3.0, atr_at_event=5.0, is_stop_like=False,
        ),
    ]
    return pos


# ===================================================================
# AC31 — new Position fields roundtrip
# ===================================================================

class TestAC31PositionFieldsRoundtrip:
    """serialize_state <-> deserialize_state preserves new scaling fields."""

    def test_scale_count_roundtrips(self):
        from v5.paper_state import serialize_state, deserialize_state
        pos = _make_position_with_scaling()
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        data = serialize_state(state, tick_counter=10,
                               timestamp="2026-04-01T00:00:00Z")
        restored, _ = deserialize_state(data)
        rp = restored.position_manager.open_positions[0]
        assert rp.scale_count == 2

    def test_scaling_events_roundtrip_preserves_order_and_fields(self):
        from v5.paper_state import serialize_state, deserialize_state
        pos = _make_position_with_scaling()
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        data = serialize_state(state, tick_counter=10,
                               timestamp="2026-04-01T00:00:00Z")
        restored, _ = deserialize_state(data)
        rp = restored.position_manager.open_positions[0]
        assert len(rp.scaling_events) == 2
        # Order preserved
        assert rp.scaling_events[0].kind == "increase"
        assert rp.scaling_events[1].kind == "reduce"
        # Field fidelity on at least one event
        ev = rp.scaling_events[1]
        assert ev.fill_price == pytest.approx(108.0)
        assert ev.qty_delta == pytest.approx(-3.0)
        assert ev.atr_at_event == pytest.approx(5.0)

    def test_r_anchor_price_roundtrips(self):
        from v5.paper_state import serialize_state, deserialize_state
        pos = _make_position_with_scaling()
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0
        data = serialize_state(state, tick_counter=10,
                               timestamp="2026-04-01T00:00:00Z")
        restored, _ = deserialize_state(data)
        rp = restored.position_manager.open_positions[0]
        assert rp.r_anchor_price == pytest.approx(100.0)

    def test_helper_state_roundtrips(self):
        from v5.paper_state import serialize_state, deserialize_state
        pos = _make_position_with_scaling()
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0
        data = serialize_state(state, tick_counter=10,
                               timestamp="2026-04-01T00:00:00Z")
        restored, _ = deserialize_state(data)
        rp = restored.position_manager.open_positions[0]
        assert "tp_ladder_atr__" in rp._helper_state


# ===================================================================
# AC31a — v4 paper-state compat loader
# ===================================================================

class TestAC31aV4CompatLoader:
    """The v4 paper-state fixture loads via v5 compat path; defaults synthesized."""

    def test_fixture_file_exists(self):
        assert FIXTURE_PATH.exists(), (
            f"v4 paper-state fixture missing at {FIXTURE_PATH}"
        )

    def test_v4_state_loads_via_compat(self):
        """v4 paper-state loads without error and new fields get defaults."""
        # v4 paper-state shape - see AC31a
        with open(FIXTURE_PATH) as f:
            v4_data = json.load(f)

        from v5.paper_state import load_v4_compat
        with tempfile.TemporaryDirectory() as tmpdir:
            v4_path = os.path.join(tmpdir, "v4_state.json")
            with open(v4_path, "w") as f:
                json.dump(v4_data, f)
            state, tick = load_v4_compat(v4_path)

        assert state.position_manager.total_open() >= 1
        # New fields have defaults
        for pos in state.position_manager.open_positions:
            assert pos.scale_count == 0
            assert pos.scaling_events == []
            assert pos.r_anchor_price == pytest.approx(pos.entry_price) or \
                   pos.r_anchor_price == 0.0


# ===================================================================
# AC33 — load_trade_log compat parser for v4 ':partial' suffix
# ===================================================================

class TestAC33LoadTradeLog:
    """load_trade_log reads v4 analysis/*.json trades with :partial suffix."""

    def test_reads_v4_partial_suffix_trades(self):
        from v5.paper_state import load_trade_log
        # v4-shape trade log: 'position_id' with ':partial' suffix
        trades_v4 = [
            {
                "position_id": "BTC:s30:5:primary:partial",
                "token": "BTC", "strategy_id": "s30", "leg": "primary",
                "entry_bar": 5, "exit_bar": 15, "entry_price": 100.0,
                "exit_price": 105.0, "direction": 1, "margin_usd": 500.0,
                "pnl": 25.0, "funding_cost": 0.0, "entry_fee": 2.5,
                "exit_fee": 2.625, "hold_bars": 10,
                "exit_reason": "partial_tp", "is_perp": True,
            },
            {
                "position_id": "BTC:s30:5:primary",
                "token": "BTC", "strategy_id": "s30", "leg": "primary",
                "entry_bar": 5, "exit_bar": 20, "entry_price": 100.0,
                "exit_price": 110.0, "direction": 1, "margin_usd": 500.0,
                "pnl": 50.0, "funding_cost": 0.0, "entry_fee": 2.5,
                "exit_fee": 2.75, "hold_bars": 15,
                "exit_reason": "target", "is_perp": True,
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.json")
            with open(path, "w") as f:
                json.dump(trades_v4, f)
            trades = load_trade_log(path)

        assert len(trades) == 2
        partial = next(t for t in trades if "partial" in t.position_id)
        terminal = next(t for t in trades if "partial" not in t.position_id)
        assert partial.exec_type == "reduce"
        assert partial.is_terminal is False
        assert partial.has_scaling is True
        assert partial.parent_position_id == "BTC:s30:5:primary"
        assert partial.exec_seq == 1

        assert terminal.exec_type == "exit"
        assert terminal.is_terminal is True

    def test_mixed_format_log_raises(self):
        """Logs with v5 ':scale_' suffix but no identity fields must raise ValueError."""
        from v5.paper_state import load_trade_log
        bad_trades = [
            {
                "position_id": "BTC:s30:5:primary:scale_1",
                "token": "BTC", "strategy_id": "s30", "leg": "primary",
                "entry_bar": 5, "exit_bar": 15, "entry_price": 100.0,
                "exit_price": 105.0, "direction": 1, "margin_usd": 500.0,
                "pnl": 25.0, "funding_cost": 0.0, "entry_fee": 2.5,
                "exit_fee": 2.625, "hold_bars": 10,
                "exit_reason": "partial_reduce", "is_perp": True,
                # missing identity fields like parent_position_id, exec_seq
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.json")
            with open(path, "w") as f:
                json.dump(bad_trades, f)
            with pytest.raises(ValueError):
                load_trade_log(path)


# ===================================================================
# AC34 — :partial retired in v5, legacy position_id retained
# ===================================================================

class TestAC34PartialRetired:
    """v5 synthesizes :scale_ suffix; position_id string field is retained."""

    def test_loaded_v4_trades_have_original_position_id(self):
        """load_trade_log preserves original v4 position_id for traceability."""
        from v5.paper_state import load_trade_log
        trades_v4 = [
            {
                "position_id": "BTC:s30:5:primary:partial",
                "token": "BTC", "strategy_id": "s30", "leg": "primary",
                "entry_bar": 5, "exit_bar": 15, "entry_price": 100.0,
                "exit_price": 105.0, "direction": 1, "margin_usd": 500.0,
                "pnl": 25.0, "funding_cost": 0.0, "entry_fee": 2.5,
                "exit_fee": 2.625, "hold_bars": 10,
                "exit_reason": "partial_tp", "is_perp": True,
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.json")
            with open(path, "w") as f:
                json.dump(trades_v4, f)
            trades = load_trade_log(path)
        # v4 legacy position_id retained verbatim
        assert trades[0].position_id == "BTC:s30:5:primary:partial"


# ===================================================================
# Q6 — heartbeat cadence for scaling_events flush
# ===================================================================

class TestQ6HeartbeatCadence:
    """scaling_events are flushed at heartbeat/snapshot granularity; the latest
    snapshot must contain all events accumulated since the last snapshot."""

    def test_all_events_present_in_snapshot(self):
        from v5.paper_state import serialize_state, deserialize_state
        pos = _make_position_with_scaling()
        # Append one more event between snapshots
        pos.scaling_events.append(
            ScalingEvent(
                bar=30, kind="reduce", fill_price=112.0,
                qty_delta=-1.0, requested_qty_delta=-1.0,
                margin_delta=-112.0, fill_notional=112.0,
                entry_fee_delta=0.0, exit_fee=0.056,
                slippage_bps=3.5, atr_at_event=5.0, is_stop_like=True,
            )
        )
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        data = serialize_state(state, tick_counter=10,
                               timestamp="2026-04-01T00:00:00Z")
        restored, _ = deserialize_state(data)
        rp = restored.position_manager.open_positions[0]
        assert len(rp.scaling_events) == 3
        assert rp.scaling_events[-1].is_stop_like is True
