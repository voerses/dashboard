"""M7 — Post-fill Order-level validation (design §2.2 + AC-O3 adjacent).

Scope note (per user feedback on arm_bracket naming): post-fill SL/TP side
validation is an **Order-level** concern, not a bracket-factory concern. Any
multi-leg Order with MARKET entry needs fill-time re-validation because the
entry price is unknown at factory time. This file isolates those tests from
test_m7_arm_bracket.py (which tests only factory ergonomics).

All tests MUST FAIL today — post-fill validation hook does not exist.
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


class TestMarketEntryPostFillSideValidation:
    """Design §2.2 lines 207-213 — Order-level post-fill re-validation.

    When a MARKET entry fills at a price that makes a sibling SL/TP
    trigger_price mis-sided (e.g., SL above LONG entry's fill price),
    engine's `_order_reject_event` detects the violation and publishes
    a rejection → `Strategy.on_order_rejected` with
    `reason.startswith("post_fill_unwind:")`.
    """

    def test_long_market_fill_below_sl_triggers_reject(self, ctx):
        order = ctx.orders.arm_bracket(
            entry_spec={
                "symbol": "BTC", "direction": "LONG", "size": 1.0,
                "order_type": "MARKET",
            },
            sl_spec={"trigger_price": 49_500.0},  # requires entry > 49_500
            tp_spec={"trigger_price": 55_000.0},
        )
        # MARKET fills at 49_000 → SL at 49_500 is above fill (mis-sided for LONG)
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=49_000.0)
        rejection = ctx.orders.last_rejection_event()
        assert rejection is not None, (
            "Design §2.2: mis-sided SL at MARKET fill must publish reject event"
        )
        assert rejection.reason.startswith("post_fill_unwind:"), (
            f"reject reason must signal post-fill unwind; got {rejection.reason!r}"
        )

    def test_long_market_fill_above_tp_triggers_reject(self, ctx):
        order = ctx.orders.arm_bracket(
            entry_spec={
                "symbol": "BTC", "direction": "LONG", "size": 1.0,
                "order_type": "MARKET",
            },
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        # MARKET fills at 56_000 → TP at 55_000 is below fill (already past)
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=56_000.0)
        rejection = ctx.orders.last_rejection_event()
        assert rejection is not None
        assert rejection.reason.startswith("post_fill_unwind:")

    def test_long_market_fill_between_sl_and_tp_accepted(self, ctx):
        """Normal-case MARKET fill — SL below, TP above — no rejection."""
        order = ctx.orders.arm_bracket(
            entry_spec={
                "symbol": "BTC", "direction": "LONG", "size": 1.0,
                "order_type": "MARKET",
            },
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=50_000.0)
        assert ctx.orders.last_rejection_event() is None, (
            "MARKET fill between SL/TP must NOT trigger post-fill rejection"
        )

    def test_short_market_fill_above_sl_triggers_reject(self, ctx):
        """SHORT entry: SL must be ABOVE entry price (not below)."""
        order = ctx.orders.arm_bracket(
            entry_spec={
                "symbol": "BTC", "direction": "SHORT", "size": 1.0,
                "order_type": "MARKET",
            },
            sl_spec={"trigger_price": 50_500.0},  # requires entry < 50_500
            tp_spec={"trigger_price": 45_000.0},
        )
        # MARKET fills at 51_000 → SL at 50_500 is below fill (mis-sided for SHORT)
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=51_000.0)
        rejection = ctx.orders.last_rejection_event()
        assert rejection is not None
        assert rejection.reason.startswith("post_fill_unwind:")


class TestGenericMultiLegPostFillValidation:
    """The post-fill validation path is generic — applies to ANY Order with
    multi-leg + MARKET entry, not just brackets. Reviewer finding.
    """

    def test_arm_with_legs_kwarg_also_triggers_post_fill_validation(self, ctx):
        """Manual Order construction (bypassing arm_bracket factory) STILL
        gets post-fill validation — it's Order-level, not factory-level."""
        from v5.orders import Order, Leg, LegFillPolicy, ContingencyType, TriggerType
        from v5.data.streams import InstrumentId, Venue

        inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        entry_leg = Leg(
            leg_ref_id="entry", symbol="BTC", market="perp", direction=1,
            target_qty=1.0, size_share=1.0, order_type="MARKET",
            trigger_price=None,  # MARKET — unknown at arm time
        )
        sl_leg = Leg(
            leg_ref_id="sl", symbol="BTC", market="perp", direction=-1,
            target_qty=1.0, size_share=1.0, order_type="STOP",
            trigger_price=49_500.0,
        )
        # Construct Order directly (no factory sugar)
        order = Order(
            legs=(entry_leg, sl_leg),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.OTO,
        )
        ctx.orders.submit(order)
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=49_000.0)
        rejection = ctx.orders.last_rejection_event()
        assert rejection is not None, (
            "Post-fill validation must apply to ANY multi-leg Order with MARKET entry — "
            "not just bracket-factory-constructed ones"
        )
