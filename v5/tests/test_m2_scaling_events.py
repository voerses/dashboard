"""Acceptance tests for M2 — ScalingEvent dataclass and ClosedTrade identity.

Covers:
  - AC13: ScalingEvent 12-field definition
  - AC29: ClosedTrade identity fields (parent_position_id, exec_seq, exec_type,
          is_terminal, triggered_by, has_scaling, scaling_events)
  - AC30: exec_type orthogonal to exit_reason; exactly one terminal ClosedTrade per parent
  - has_scaling trigger: True when len(pos.scaling_events) >= 1 at booking time

All tests MUST FAIL until ScalingEvent / new ClosedTrade fields land.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.position import ClosedTrade, ScalingEvent  # noqa: E402


# ===================================================================
# AC13 — ScalingEvent 12-field definition
# ===================================================================

class TestAC13ScalingEventFields:
    """ScalingEvent exposes exactly 12 fields per AC13."""

    def test_has_all_12_fields(self):
        ev = ScalingEvent(
            bar=10,
            kind="increase",
            fill_price=100.0,
            qty_delta=1.0,
            requested_qty_delta=1.0,
            margin_delta=100.0,
            fill_notional=100.0,
            entry_fee_delta=0.5,
            exit_fee=0.0,
            slippage_bps=2.0,
            atr_at_event=5.0,
            is_stop_like=False,
        )
        for name in (
            "bar", "kind", "fill_price", "qty_delta",
            "requested_qty_delta", "margin_delta", "fill_notional",
            "entry_fee_delta", "exit_fee", "slippage_bps",
            "atr_at_event", "is_stop_like",
        ):
            assert hasattr(ev, name), f"ScalingEvent missing field {name}"

    def test_kind_accepts_increase_or_reduce(self):
        ev1 = ScalingEvent(
            bar=1, kind="increase", fill_price=100.0, qty_delta=1.0,
            requested_qty_delta=1.0, margin_delta=100.0, fill_notional=100.0,
            entry_fee_delta=0.5, exit_fee=0.0, slippage_bps=2.0,
            atr_at_event=5.0, is_stop_like=False,
        )
        ev2 = ScalingEvent(
            bar=1, kind="reduce", fill_price=100.0, qty_delta=-1.0,
            requested_qty_delta=-1.0, margin_delta=-100.0, fill_notional=100.0,
            entry_fee_delta=0.0, exit_fee=0.5, slippage_bps=2.0,
            atr_at_event=5.0, is_stop_like=True,
        )
        assert ev1.kind == "increase"
        assert ev2.kind == "reduce"

    def test_requested_equals_actual_for_unclamped(self):
        """When no constraint clamps the action, requested == actual."""
        ev = ScalingEvent(
            bar=1, kind="increase", fill_price=100.0,
            qty_delta=5.0, requested_qty_delta=5.0,
            margin_delta=500.0, fill_notional=500.0,
            entry_fee_delta=0.25, exit_fee=0.0, slippage_bps=2.0,
            atr_at_event=5.0, is_stop_like=False,
        )
        assert ev.qty_delta == ev.requested_qty_delta

    def test_requested_greater_than_actual_on_clamped_increase(self):
        """AC10 clamp leaves |requested| > |actual| for increases."""
        ev = ScalingEvent(
            bar=1, kind="increase", fill_price=100.0,
            qty_delta=6.0, requested_qty_delta=10.0,
            margin_delta=600.0, fill_notional=600.0,
            entry_fee_delta=0.3, exit_fee=0.0, slippage_bps=2.0,
            atr_at_event=5.0, is_stop_like=False,
        )
        assert abs(ev.requested_qty_delta) > abs(ev.qty_delta)


# ===================================================================
# AC29 — ClosedTrade identity fields with backward-compat defaults
# ===================================================================

class TestAC29ClosedTradeIdentityFields:
    """The 7 new identity fields on ClosedTrade have safe defaults."""

    def _minimal_trade(self, **overrides) -> ClosedTrade:
        base = dict(
            position_id="BTC:s30:5:primary", token="BTC", strategy_id="s30",
            leg="primary", entry_bar=5, exit_bar=20, entry_price=100.0,
            exit_price=110.0, direction=1, margin_usd=1_000.0,
            pnl=50.0, funding_cost=0.0, entry_fee=5.0, exit_fee=5.0,
            hold_bars=15, exit_reason="target", is_perp=True,
        )
        base.update(overrides)
        return ClosedTrade(**base)

    def test_parent_position_id_default_empty(self):
        t = self._minimal_trade()
        assert t.parent_position_id == ""

    def test_exec_seq_default_zero(self):
        t = self._minimal_trade()
        assert t.exec_seq == 0

    def test_exec_type_default_exit(self):
        t = self._minimal_trade()
        assert t.exec_type == "exit"

    def test_is_terminal_default_true(self):
        """Legacy trades default to is_terminal=True (backwards-compat)."""
        t = self._minimal_trade()
        assert t.is_terminal is True

    def test_triggered_by_default_empty(self):
        t = self._minimal_trade()
        assert t.triggered_by == ""

    def test_has_scaling_default_false(self):
        t = self._minimal_trade()
        assert t.has_scaling is False

    def test_scaling_events_default_empty_list(self):
        t = self._minimal_trade()
        assert isinstance(t.scaling_events, list)
        assert t.scaling_events == []

    def test_explicit_identity_fields_roundtrip(self):
        """Construct a ClosedTrade with fully populated identity fields."""
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=110.0, qty_delta=-3.0,
            requested_qty_delta=-3.0, margin_delta=-300.0, fill_notional=330.0,
            entry_fee_delta=0.0, exit_fee=0.165, slippage_bps=5.0,
            atr_at_event=5.0, is_stop_like=False,
        )
        t = self._minimal_trade(
            position_id="BTC:s30:5:primary:scale_1",
            parent_position_id="BTC:s30:5:primary",
            exec_seq=1,
            exec_type="reduce",
            is_terminal=False,
            triggered_by="tp_rung_1",
            has_scaling=True,
            scaling_events=[ev],
            exit_reason="partial_reduce",
        )
        assert t.parent_position_id == "BTC:s30:5:primary"
        assert t.exec_seq == 1
        assert t.exec_type == "reduce"
        assert t.is_terminal is False
        assert t.triggered_by == "tp_rung_1"
        assert t.has_scaling is True
        assert len(t.scaling_events) == 1


# ===================================================================
# AC30 — field semantics: exec_type orthogonal to exit_reason
# ===================================================================

class TestAC30FieldSemantics:
    """exec_type and exit_reason are independent; exactly one terminal per parent."""

    def test_exec_type_and_exit_reason_are_independent(self):
        """A reduce ClosedTrade can carry exit_reason='partial_reduce' with
        exec_type='reduce' — they are orthogonal dimensions."""
        t = ClosedTrade(
            position_id="BTC:s30:5:primary:scale_1",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, exit_bar=10, entry_price=100.0, exit_price=110.0,
            direction=1, margin_usd=500.0, pnl=25.0, funding_cost=0.0,
            entry_fee=2.5, exit_fee=2.75, hold_bars=5,
            exit_reason="partial_reduce", is_perp=True,
            parent_position_id="BTC:s30:5:primary",
            exec_seq=1, exec_type="reduce", is_terminal=False,
        )
        assert t.exec_type == "reduce"
        assert t.exit_reason == "partial_reduce"

    def test_terminal_carries_legitimate_exit_reason(self):
        """Terminal trade can have any exit_reason (stop, target, etc.)."""
        t = ClosedTrade(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, exit_bar=20, entry_price=100.0, exit_price=90.0,
            direction=1, margin_usd=1_000.0, pnl=-105.0, funding_cost=0.0,
            entry_fee=5.0, exit_fee=4.5, hold_bars=15,
            exit_reason="stop", is_perp=True,
            parent_position_id="BTC:s30:5:primary",
            exec_seq=2, exec_type="exit", is_terminal=True,
        )
        assert t.is_terminal is True
        assert t.exec_type == "exit"
        assert t.exit_reason == "stop"


# ===================================================================
# has_scaling trigger
# ===================================================================

class TestHasScalingTrigger:
    """has_scaling=True on any ClosedTrade whose parent had >=1 ScalingEvent at close."""

    def test_has_scaling_when_events_present(self):
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=110.0, qty_delta=-3.0,
            requested_qty_delta=-3.0, margin_delta=-300.0, fill_notional=330.0,
            entry_fee_delta=0.0, exit_fee=0.165, slippage_bps=5.0,
            atr_at_event=5.0, is_stop_like=False,
        )
        t = ClosedTrade(
            position_id="BTC:s30:5:primary:scale_1",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, exit_bar=10, entry_price=100.0, exit_price=110.0,
            direction=1, margin_usd=500.0, pnl=25.0, funding_cost=0.0,
            entry_fee=2.5, exit_fee=2.75, hold_bars=5,
            exit_reason="partial_reduce", is_perp=True,
            has_scaling=True, scaling_events=[ev],
        )
        assert t.has_scaling is True
        assert len(t.scaling_events) >= 1
