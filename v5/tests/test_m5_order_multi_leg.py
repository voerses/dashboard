"""M5 — Multi-leg Order ARMED lifecycle.

Covers:
  - T-M5-02: Multi-leg ARMED lifecycle. Price crossing one leg's trigger
    transitions that leg ARMED -> WORKING while the sibling stays ARMED.

All tests MUST FAIL today — v5.orders does not exist (post-rename target).
Import failures are the expected RED state.
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


def _mk_stop_leg(ref: str, direction: int, stop: float) -> object:
    """Factory: stop leg (ARMED awaiting trigger)."""
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        market="perp",
        venue="binance",
        direction=direction,
        target_qty=1.0,
        cum_qty=0.0,
        size_share=0.5,
        order_type="stop",
        status=LegStatus.ARMED,
        trigger_price=stop,
        limit_price=None,
        currency="USDT",
    )


def _mk_tp_leg(ref: str, direction: int, take: float) -> object:
    """Factory: TP leg (ARMED awaiting trigger)."""
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        market="perp",
        venue="binance",
        direction=direction,
        target_qty=1.0,
        cum_qty=0.0,
        size_share=0.5,
        order_type="limit",
        status=LegStatus.ARMED,
        trigger_price=None,
        limit_price=take,
        currency="USDT",
    )


class TestTM502MultiLegArmedLifecycle:
    """T-M5-02: per-leg ARMED -> WORKING on price cross; sibling stays ARMED."""

    def test_order_constructed_with_two_legs(self):
        """T-M5-02: Order(legs=(stop_leg, tp_leg)) is well-formed."""
        from v5.orders import Order, OrderStatus, TriggerType
        stop_leg = _mk_stop_leg("sl", direction=-1, stop=95.0)
        tp_leg = _mk_tp_leg("tp", direction=-1, take=110.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(stop_leg, tp_leg),
        )
        assert len(order.legs) == 2
        assert order.state == OrderStatus.ARMED

    def test_stop_leg_triggers_sibling_stays_armed(self):
        """T-M5-02: price hits stop level → stop leg ARMED→WORKING; TP sibling ARMED."""
        from v5.orders import Order, TriggerType, LegStatus
        stop_leg = _mk_stop_leg("sl", direction=-1, stop=95.0)
        tp_leg = _mk_tp_leg("tp", direction=-1, take=110.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(stop_leg, tp_leg),
        )
        # Price drops through the stop level.
        order2 = order.on_leg_price(leg_ref_id="sl", price=94.0)
        legs_by_ref = {lg.leg_ref_id: lg for lg in order2.legs}
        assert legs_by_ref["sl"].status == LegStatus.WORKING
        assert legs_by_ref["tp"].status == LegStatus.ARMED

    def test_tp_leg_triggers_sibling_stays_armed(self):
        """T-M5-02: price hits TP level → TP leg ARMED→WORKING; stop sibling ARMED."""
        from v5.orders import Order, TriggerType, LegStatus
        stop_leg = _mk_stop_leg("sl", direction=-1, stop=95.0)
        tp_leg = _mk_tp_leg("tp", direction=-1, take=110.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(stop_leg, tp_leg),
        )
        order2 = order.on_leg_price(leg_ref_id="tp", price=111.0)
        legs_by_ref = {lg.leg_ref_id: lg for lg in order2.legs}
        assert legs_by_ref["tp"].status == LegStatus.WORKING
        assert legs_by_ref["sl"].status == LegStatus.ARMED

    def test_leg_lifecycle_working_to_filled(self):
        """T-M5-02: Leg transitions WORKING -> FILLED after fill dispatch."""
        from v5.orders import Order, TriggerType, LegStatus
        stop_leg = _mk_stop_leg("sl", direction=-1, stop=95.0)
        tp_leg = _mk_tp_leg("tp", direction=-1, take=110.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(stop_leg, tp_leg),
        )
        order = order.on_leg_price(leg_ref_id="tp", price=111.0)
        order = order.on_leg_fill(leg_ref_id="tp", filled_qty=1.0)
        legs_by_ref = {lg.leg_ref_id: lg for lg in order.legs}
        assert legs_by_ref["tp"].status == LegStatus.FILLED
        assert legs_by_ref["tp"].cum_qty == 1.0


class TestF10LegFIXFieldAdditions:
    """F10: Leg dataclass gets FIX LegExpireTime(621) + LegSettlType(587)."""

    def test_leg_has_expires_at_field(self):
        """F10: Leg constructed without expires_at → defaults to None."""
        from v5.orders import Leg, LegStatus
        leg = Leg(
            leg_ref_id="L1", symbol="BTCUSDT", market="perp",
            venue="binance", direction=1, target_qty=1.0, cum_qty=0.0,
            size_share=1.0, order_type="market", status=LegStatus.ARMED,
            trigger_price=None, limit_price=None, currency="USDT",
        )
        assert leg.expires_at is None

    def test_leg_has_settlement_type_field(self):
        """F10: Leg constructed without settlement_type → defaults to 'spot'."""
        from v5.orders import Leg, LegStatus
        leg = Leg(
            leg_ref_id="L1", symbol="BTCUSDT", market="spot",
            venue="binance", direction=1, target_qty=1.0, cum_qty=0.0,
            size_share=1.0, order_type="market", status=LegStatus.ARMED,
            trigger_price=None, limit_price=None, currency="USDT",
        )
        assert leg.settlement_type == "spot"

    def test_leg_expires_at_settable(self):
        """F10: Leg.expires_at accepts explicit datetime."""
        from v5.orders import Leg, LegStatus
        dt = _dt("2026-04-01T12:00:00")
        leg = Leg(
            leg_ref_id="L1", symbol="BTCUSDT", market="perp",
            venue="binance", direction=1, target_qty=1.0, cum_qty=0.0,
            size_share=1.0, order_type="market", status=LegStatus.ARMED,
            trigger_price=None, limit_price=None, currency="USDT",
            expires_at=dt,
        )
        assert leg.expires_at == dt

    def test_leg_settlement_type_perp(self):
        """F10: Leg.settlement_type accepts 'perp' explicitly."""
        from v5.orders import Leg, LegStatus
        leg = Leg(
            leg_ref_id="L1", symbol="BTCUSDT", market="perp",
            venue="binance", direction=1, target_qty=1.0, cum_qty=0.0,
            size_share=1.0, order_type="market", status=LegStatus.ARMED,
            trigger_price=None, limit_price=None, currency="USDT",
            settlement_type="perp",
        )
        assert leg.settlement_type == "perp"
