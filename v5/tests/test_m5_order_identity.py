"""M5 — Order → Position → ClosedTrade identity back-link chain (F9).

Covers:
  - T-M5-12: `Position.order_id` back-link: 1 Order × N legs → N Positions
    sharing order_id. `max_positions_per_symbol` counts the Order as 1
    logical entry (not N Position rows).
  - T-M5-12 extension (F9): ClosedTrade.order_id + leg_ref_id back-link fields
    copied from Position at close. Identity chain preserved through partial-
    close and terminal-close cycles.

All tests MUST FAIL today — v5.orders + Position.order_id field do not exist.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _mk_leg(ref: str, market: str) -> object:
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        market=market,
        venue="binance",
        direction=1,
        target_qty=1.0,
        cum_qty=0.0,
        size_share=0.5,
        order_type="market",
        status=LegStatus.ARMED,
        trigger_price=None, limit_price=None,
        currency="USDT",
    )


class TestTM512PositionOrderIdBackLink:
    """T-M5-12: Position.order_id field + shared order_id across legs."""

    def test_position_has_order_id_field(self):
        """T-M5-12: Position exposes order_id and leg_ref_id fields."""
        from v5.position import Position
        # Field existence probe — dataclasses must expose these as fields.
        fields = getattr(Position, "__dataclass_fields__", {})
        assert "order_id" in fields, (
            f"Position missing 'order_id' field; got {list(fields)}"
        )
        assert "leg_ref_id" in fields, (
            f"Position missing 'leg_ref_id' field; got {list(fields)}"
        )

    def test_two_leg_order_creates_two_positions_same_order_id(self):
        """T-M5-12: Order with 2 legs spawns 2 Positions with identical order_id."""
        from v5.orders import (
            Order, TriggerType, LegFillPolicy, ContingencyType,
        )
        leg_a = _mk_leg("A", market="spot")
        leg_b = _mk_leg("B", market="perp")
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.NONE,
            order_id="ord-001",
        )
        positions = order.materialize_positions()
        assert len(positions) == 2
        assert all(p.order_id == "ord-001" for p in positions)
        # Each Position references its leg's leg_ref_id.
        assert {p.leg_ref_id for p in positions} == {"A", "B"}

    def test_max_positions_per_symbol_counts_order_not_legs(self):
        """T-M5-12: `max_positions_per_symbol` counts Orders as 1 logical entry."""
        from v5.orders import (
            Order, TriggerType, LegFillPolicy, ContingencyType,
            count_logical_positions,
        )
        leg_a = _mk_leg("A", market="spot")
        leg_b = _mk_leg("B", market="perp")
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.OCO,
            order_id="ord-002",
        )
        positions = order.materialize_positions()
        count = count_logical_positions([order])
        assert count == 1, (
            f"2-leg Order should count as 1 logical position, not {count}"
        )
        # While the raw Position count is 2 — the per-symbol guard must use the
        # logical count.
        assert len(positions) == 2


class TestTM512ClosedTradeBackLink:
    """T-M5-12 / F9 extension: ClosedTrade carries order_id + leg_ref_id."""

    def test_closed_trade_has_order_id_and_leg_ref_id_fields(self):
        """F9: ClosedTrade exposes order_id and leg_ref_id fields."""
        from v5.position import ClosedTrade
        fields = getattr(ClosedTrade, "__dataclass_fields__", {})
        assert "order_id" in fields
        assert "leg_ref_id" in fields

    def test_identity_chain_preserved_through_partial_close(self):
        """T-M5-12 / F9: Position.order_id + leg_ref_id propagate to
        ClosedTrade on close. Identity chain Order → Position → ClosedTrade
        traceable via direct back-links through partial + terminal close.
        """
        from v5.position import Position
        pos = Position(
            position_id="pos-1",
            token="BTC",
            strategy_id="s1",
            entry_price=100.0,
            direction=1,
            quantity=1.0,
            margin_usd=100.0,
            order_id="ord123",
            leg_ref_id="lg_primary",
        )
        # Partial close (50%) — produces first ClosedTrade.
        result1 = pos.reduce(close_fraction=0.5, fill_price=105.0)
        ct1 = pos.book_closed_trade(result1)
        assert ct1.order_id == "ord123"
        assert ct1.leg_ref_id == "lg_primary"
        assert ct1.is_terminal is False

        # Terminal close (100% of remainder) — produces second ClosedTrade.
        result2 = pos.reduce(close_fraction=1.0, fill_price=110.0)
        ct2 = pos.book_closed_trade(result2)
        assert ct2.order_id == "ord123"
        assert ct2.leg_ref_id == "lg_primary"
        assert ct2.is_terminal is True
        # parent_position_id chain: second trade's parent == first trade's position.
        assert ct2.parent_position_id == ct1.position_id, (
            f"Expected ct2.parent_position_id == ct1.position_id "
            f"({ct1.position_id!r}); got {ct2.parent_position_id!r}"
        )
