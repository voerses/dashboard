"""M5 — TimeInForce enum + FOK liquidity + GTC default.

Covers:
  - T-M5-25: TimeInForce FOK — Order with FOK + qty exceeding simulated
    liquidity → REJECTED with reason='fok_liquidity'.
  - T-M5-26: TimeInForce GTC default — unchanged behavior (AC14 parity).
    Order constructed without time_in_force uses GTC; behavior identical to
    pre-M5 path.

All tests MUST FAIL today — v5.orders + TimeInForce do not exist.
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


class TestTM525TimeInForceFOK:
    """T-M5-25: FOK with insufficient liquidity → REJECTED with fok_liquidity."""

    def test_fok_enum_value(self):
        """T-M5-25: TimeInForce.FOK maps to FIX 59 value '4'."""
        from v5.orders import TimeInForce
        assert TimeInForce.FOK.value == "4"

    def test_fok_order_rejects_on_insufficient_liquidity(self):
        """T-M5-25: FOK Order whose qty exceeds bar liquidity → REJECTED
        with reject_reason='fok_liquidity'. Neither partial fill nor the
        order rests."""
        from v5.orders import (
            Order, OrderStatus, TriggerType, TimeInForce,
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={"qty": 1000.0},
            time_in_force=TimeInForce.FOK,
        )
        order = order.trigger_immediately()
        # Liquidity cap set low — qty exceeds it.
        order = order.apply_liquidity_check(available_qty=10.0)
        assert order.state == OrderStatus.REJECTED
        assert order.reject_reason == "fok_liquidity"

    def test_fok_order_fills_when_liquidity_suffices(self):
        """T-M5-25: FOK Order whose qty fits liquidity → RELEASED (proceeds)."""
        from v5.orders import (
            Order, OrderStatus, TriggerType, TimeInForce,
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={"qty": 10.0},
            time_in_force=TimeInForce.FOK,
        )
        order = order.trigger_immediately()
        order = order.apply_liquidity_check(available_qty=1000.0)
        assert order.state != OrderStatus.REJECTED
        assert order.reject_reason != "fok_liquidity"


class TestTM526TimeInForceGTCDefault:
    """T-M5-26: GTC is default; zero behavior change from pre-M5."""

    def test_default_time_in_force_is_gtc(self):
        """T-M5-26: Order constructed without explicit time_in_force = GTC."""
        from v5.orders import Order, TriggerType, TimeInForce
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        assert order.time_in_force == TimeInForce.GTC

    def test_gtc_fix_value(self):
        """T-M5-26: TimeInForce.GTC maps to FIX 59 value '1'."""
        from v5.orders import TimeInForce
        assert TimeInForce.GTC.value == "1"

    def test_all_tif_fix_values(self):
        """T-M5-26: full FIX 59 enum values present."""
        from v5.orders import TimeInForce
        assert TimeInForce.DAY.value == "0"
        assert TimeInForce.GTC.value == "1"
        assert TimeInForce.IOC.value == "3"
        assert TimeInForce.FOK.value == "4"
        assert TimeInForce.GTD.value == "6"
        assert TimeInForce.GTX.value == "8"

    def test_gtc_order_does_not_reject_on_liquidity(self):
        """T-M5-26: GTC Order is NOT subject to FOK single-order atomicity.
        apply_liquidity_check on a GTC order does not force REJECTED."""
        from v5.orders import (
            Order, OrderStatus, TriggerType, TimeInForce,
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={"qty": 1000.0},
            time_in_force=TimeInForce.GTC,
        )
        order = order.trigger_immediately()
        order = order.apply_liquidity_check(available_qty=10.0)
        # GTC under low liquidity does NOT get fok_liquidity rejection.
        assert order.reject_reason != "fok_liquidity"
