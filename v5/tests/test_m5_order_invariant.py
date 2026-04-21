"""M5 — Order.legs invariant enforcement.

Covers:
  - T-M5-20: Order(legs=(single_leg,)) raises ValueError —
    `len(legs) == 1` is forbidden. Either empty tuple (single-leg via the
    Order's bare fields) or ≥2 legs (true multi-leg).

All tests MUST FAIL today — v5.orders does not exist.
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


def _mk_leg(ref: str) -> object:
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        venue="binance",
        direction=1,
        target_qty=1.0,
        cum_qty=0.0,
        size_share=1.0,
        order_type="market",
        status=LegStatus.ARMED,
        trigger_price=None, limit_price=None,
        currency="USDT",
    )


class TestTM520LegsInvariant:
    """T-M5-20: `len(legs) != 1` — either empty tuple or ≥2."""

    def test_single_leg_raises_value_error(self):
        """T-M5-20: Order(legs=(one_leg,)) raises ValueError on construction."""
        from v5.orders import Order, TriggerType
        with pytest.raises(ValueError):
            Order.arm(
                strategy_id="s1", token="BTC", direction=1,
                trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
                working_price_source="last",
                armed_at=_dt("2026-04-01T00:00:00"),
                expires_at=None, sizing_ctx={},
                legs=(_mk_leg("only"),),
            )

    def test_empty_legs_accepted(self):
        """T-M5-20: Order(legs=()) is the valid single-leg-via-bare-fields shape."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(),
        )
        assert len(order.legs) == 0

    def test_two_legs_accepted(self):
        """T-M5-20: Order(legs=(a, b)) is valid multi-leg."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(_mk_leg("a"), _mk_leg("b")),
        )
        assert len(order.legs) == 2

    def test_three_legs_accepted(self):
        """T-M5-20: Order(legs=(entry, sl, tp)) — a 3-leg bracket — is valid."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(_mk_leg("e"), _mk_leg("sl"), _mk_leg("tp")),
        )
        assert len(order.legs) == 3
