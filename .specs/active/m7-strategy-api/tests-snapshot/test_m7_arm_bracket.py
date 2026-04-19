"""M7 — arm + arm_bracket factories (AC-O1).

All tests MUST FAIL today — OrderFactoryView does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=150_000.0)


def _long_bracket(sl=48_000.0, tp=55_000.0):
    return dict(
        entry_spec={"symbol": "BTC", "direction": "LONG", "size": 1.0, "trigger_price": 50_000.0},
        sl_spec={"trigger_price": sl},
        tp_spec={"trigger_price": tp},
    )


def _short_bracket(sl=52_000.0, tp=45_000.0):
    return dict(
        entry_spec={"symbol": "BTC", "direction": "SHORT", "size": 1.0, "trigger_price": 50_000.0},
        sl_spec={"trigger_price": sl},
        tp_spec={"trigger_price": tp},
    )


class TestArmSingleLeg:
    """AC-O1 — ctx.orders.arm(...) yields a single-leg Order."""

    def test_arm_returns_order(self, ctx):
        from v5.orders import Order, TriggerType
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        assert isinstance(order, Order)

    def test_arm_order_has_single_leg(self, ctx):
        from v5.orders import TriggerType
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        assert len(order.legs) == 1

    def test_arm_published_to_bus(self, ctx):
        from v5.orders import TriggerType
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        assert order.order_id in [o.order_id for o in ctx.orders.active_orders()]


class TestArmBracketFactory:
    """AC-O1 — arm_bracket(entry, sl, tp) assembles a 3-leg Order."""

    def test_arm_bracket_returns_order_with_three_legs(self, ctx):
        from v5.orders import Order
        order = ctx.orders.arm_bracket(**_long_bracket())
        assert isinstance(order, Order)
        assert len(order.legs) == 3

    def test_arm_bracket_uses_oto_bracket_fill_policy(self, ctx):
        from v5.orders import LegFillPolicy
        order = ctx.orders.arm_bracket(**_long_bracket())
        assert order.fill_policy == LegFillPolicy.OTO_BRACKET

    def test_arm_bracket_uses_oto_contingency(self, ctx):
        from v5.orders import ContingencyType
        order = ctx.orders.arm_bracket(**_long_bracket())
        assert order.contingency == ContingencyType.OTO


class TestBracketValidation:
    """AC-O1 — invalid bracket combos raise ValueError at factory call."""

    def test_long_bracket_sl_above_entry_rejected(self, ctx):
        with pytest.raises(ValueError):
            ctx.orders.arm_bracket(**_long_bracket(sl=51_000.0))  # SL above entry

    def test_long_bracket_tp_below_entry_rejected(self, ctx):
        with pytest.raises(ValueError):
            ctx.orders.arm_bracket(**_long_bracket(tp=49_000.0))  # TP below entry

    def test_short_bracket_sl_below_entry_rejected(self, ctx):
        with pytest.raises(ValueError):
            ctx.orders.arm_bracket(**_short_bracket(sl=49_000.0))  # SL below entry

    def test_short_bracket_tp_above_entry_rejected(self, ctx):
        with pytest.raises(ValueError):
            ctx.orders.arm_bracket(**_short_bracket(tp=51_000.0))  # TP above entry


class TestArmBracketPublishes:
    """AC-O1 — arm_bracket publishes via M5 pre-fill atomic path."""

    def test_bracket_registered_as_active(self, ctx):
        order = ctx.orders.arm_bracket(**_long_bracket())
        assert order.order_id in [o.order_id for o in ctx.orders.active_orders()]


class TestArmBracketAcceptsMarketEntry:
    """arm_bracket factory accepts MARKET entry_spec (no trigger_price).

    Post-fill side-validation is an Order-level concern tested in
    test_m7_post_fill_validation.py — NOT a bracket-factory concern.
    Factory's job here is just to allow the MARKET construction.
    """

    def test_market_entry_bracket_factory_accepts_no_trigger_price(self, ctx):
        order = ctx.orders.arm_bracket(
            entry_spec={
                "symbol": "BTC", "direction": "LONG", "size": 1.0,
                "order_type": "MARKET",
            },
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        assert order is not None
        assert order.legs[0].trigger_price is None
