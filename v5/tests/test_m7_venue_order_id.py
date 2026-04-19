"""M7 — Order.venue_order_id population on venue ack (AC-O4).

Covers:
  - AC-O4 — Live-mode venue ack (BinanceWSClient / BinanceRESTClient)
    maps FIX OrderID(37) into Order.venue_order_id BEFORE
    Strategy.on_order_accepted fires.
  - AC-O4 — Paper/backtest synthesize a deterministic venue_order_id of the
    form f"paper-{order_id:08x}" before the accept callback.

All tests MUST FAIL today — the live ack → venue_order_id mapping isn't wired.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestVenueOrderIdFieldExists:
    """AC-O4 — Order.venue_order_id is a public str | None attribute."""

    def test_order_has_venue_order_id_field(self):
        from v5.orders import Order
        ann = getattr(Order, "__annotations__", {}) or {}
        assert "venue_order_id" in ann, (
            "AC-O4: Order must declare 'venue_order_id: str | None' field"
        )

    def test_default_venue_order_id_is_none_pre_ack(self):
        """AC-O4 — newly-armed Order.venue_order_id is None until ack."""
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=10, seed=0)
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0, trigger_price=50_000.0,
        )
        # NOT YET acked
        assert order.venue_order_id is None


class TestPaperSynthesizesDeterministicId:
    """AC-O4 — paper/backtest mode synthesizes f'paper-{order_id:08x}'."""

    def test_paper_venue_order_id_is_deterministic(self):
        """AC-O4 — paper ack produces f'paper-[{runner_id}-]{hex8}' deterministic ID.

        Reviewer H3 fix: format allows optional runner-instance-id prefix to
        prevent cross-runner collision per FIX reviewer L1 / Task 13. Format
        accepted: `paper-<8hex>` OR `paper-<runner_id>-<8hex>`.
        """
        import re
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=10, seed=0, mode="paper")
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0, trigger_price=50_000.0,
        )
        ctx.orders.simulate_ack(order.order_id)
        assert order.venue_order_id is not None
        # Accept either `paper-<8hex>` OR `paper-<runner_id>-<8hex>`
        pattern = re.compile(r"^paper-(?:[\w]+-)?[0-9a-f]{8}$")
        assert pattern.match(order.venue_order_id), (
            f"AC-O4: venue_order_id must match 'paper-[<runner>-]<8hex>'; "
            f"got {order.venue_order_id!r}"
        )

    def test_paper_venue_order_id_unique_across_runner_instances(self):
        """Reviewer H5 — multiple paper runners with same seed must not collide."""
        from v5.universe_context import UniverseContext
        ctx_a = UniverseContext.build_test(
            tokens=["BTC"], bars=10, seed=42, mode="paper", runner_instance_id="runner_a",
        )
        ctx_b = UniverseContext.build_test(
            tokens=["BTC"], bars=10, seed=42, mode="paper", runner_instance_id="runner_b",
        )
        order_a = ctx_a.orders.arm(
            symbol="BTC", direction="LONG", size=1.0, trigger_price=50_000.0,
        )
        order_b = ctx_b.orders.arm(
            symbol="BTC", direction="LONG", size=1.0, trigger_price=50_000.0,
        )
        ctx_a.orders.simulate_ack(order_a.order_id)
        ctx_b.orders.simulate_ack(order_b.order_id)
        assert order_a.venue_order_id != order_b.venue_order_id, (
            "Same seed + same order sequence from different runners must produce "
            "distinct venue_order_ids (collision prevention per FIX L1 + Task 13)"
        )

    def test_venue_order_id_populated_before_on_order_accepted(self):
        """AC-O4 — on_order_accepted(order) sees a non-None venue_order_id."""
        from v5.strategy_api import BaseStrategy
        from v5.universe_context import UniverseContext

        seen: list[str | None] = []

        class _AckWatcher(BaseStrategy):
            def on_order_accepted(self, order):
                seen.append(order.venue_order_id)

        strat = _AckWatcher()
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=10, seed=0, mode="paper", strategies=[strat],
        )
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0, trigger_price=50_000.0,
        )
        ctx.orders.simulate_ack(order.order_id)

        assert len(seen) == 1
        assert seen[0] is not None, (
            "AC-O4: venue_order_id must be populated BEFORE on_order_accepted fires"
        )


class TestLiveAckMapsOrderIDTag37:
    """AC-O4 — live ack maps FIX OrderID(37) onto order.venue_order_id."""

    def test_binance_ws_ack_populates_venue_order_id(self):
        """AC-O4 — BinanceWSClient ack extracts OrderID(37) and updates order."""
        from v5.data.clients.binance_ws import BinanceWSClient
        from v5.orders import Order

        client = BinanceWSClient.build_for_test()  # harness constructor
        order = Order.build_for_test(order_id=0xABCDEF00)

        # Simulate venue ack with FIX OrderID(37) = "100500"
        ack_event = {
            "msg_type": "ExecutionReport",
            "order_id": "100500",  # FIX tag 37
            "cl_ord_id": order.order_id,
            "exec_type": "0",       # New / ack
        }
        client.on_venue_ack(ack_event, order)
        assert order.venue_order_id == "100500", (
            f"AC-O4: live ack must map OrderID(37) onto order.venue_order_id; "
            f"got {order.venue_order_id!r}"
        )

    def test_binance_rest_ack_populates_venue_order_id(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        from v5.orders import Order

        client = BinanceRESTClient.build_for_test()
        order = Order.build_for_test(order_id=0xDEADBEEF)
        rest_response = {"orderId": 987654, "clientOrderId": order.order_id}
        client.on_venue_ack(rest_response, order)
        assert order.venue_order_id == "987654"
