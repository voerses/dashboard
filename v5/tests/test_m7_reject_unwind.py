"""M7 — _order_reject_event post-fill unwind (AC-O3).

Covers:
  - AC-O3 — a venue REJECTED exec report arriving on LEG_B whose sibling
    LEG_A has already FILLED triggers the LegFillPolicy.UNWIND_ON_REJECT
    cascade — engine publishes a reversal order to close the sibling.
  - AC-O3 — Strategy.on_order_rejected(order, reason) fires once the cascade
    is scheduled, with reason prefixed "post_fill_unwind:".

All tests MUST FAIL today — the post-fill unwind wire does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestPostFillUnwindCascade:
    """AC-O3 — sibling leg auto-closes when other leg is rejected after fill."""

    def test_post_fill_reject_publishes_reversal(self):
        """AC-O3 — REJECTED on leg B with leg A filled → reversal order created."""
        from v5.orders import LegFillPolicy
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0, equity=150_000.0)
        order = ctx.orders.arm_bracket(
            entry_spec={"symbol": "BTC", "direction": "LONG", "size": 1.0,
                        "trigger_price": 50_000.0},
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        # Simulate the cascade: entry leg fills, SL/TP leg rejects post-fill
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=50_000.0)
        ctx.orders.simulate_reject(order.order_id, leg_idx=1, reason="venue_reject")

        # Engine publishes a reversal order to unwind the now-orphan entry fill
        reversal = ctx.orders.last_reversal_order()
        assert reversal is not None, (
            "AC-O3: post-fill reject on sibling leg must publish a reversal order"
        )
        # Reversal closes the entry direction (LONG entry → reversal is SHORT)
        assert reversal.legs[0].direction == "SHORT"
        assert reversal.legs[0].target_qty == 1.0


class TestStrategyOnOrderRejectedFires:
    """AC-O3 — Strategy.on_order_rejected fires with the documented reason prefix."""

    @pytest.mark.xfail(
        reason="BarProcessor 15-callback dispatch is Wave D Task 11; "
        "simulate_reject records the rejection event but does not yet fire "
        "Strategy.on_order_rejected. Unblocks when Task 11 lands.",
    )
    def test_on_order_rejected_called_with_post_fill_unwind_reason(self):
        """AC-O3 — reason.startswith('post_fill_unwind:') after cascade."""
        from v5.strategy_api import BaseStrategy
        from v5.universe_context import UniverseContext

        captured: list[tuple[object, str]] = []

        class _RejectLogger(BaseStrategy):
            def on_order_rejected(self, order, reason):
                captured.append((order, reason))

        strat = _RejectLogger()
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=50, seed=0, equity=150_000.0,
            strategies=[strat],
        )
        order = ctx.orders.arm_bracket(
            entry_spec={"symbol": "BTC", "direction": "LONG", "size": 1.0,
                        "trigger_price": 50_000.0},
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=50_000.0)
        ctx.orders.simulate_reject(order.order_id, leg_idx=1, reason="venue_reject")

        assert len(captured) >= 1, (
            "AC-O3: on_order_rejected must fire after the cascade"
        )
        order_seen, reason_seen = captured[-1]
        assert reason_seen.startswith("post_fill_unwind:"), (
            f"AC-O3: reason must start with 'post_fill_unwind:'; got {reason_seen!r}"
        )


class TestRejectFillCarriesExecTypeRejected:
    """Reviewer H4 — Fill passed to on_order_rejected must have
    exec_type == ExecType.REJECTED (not just a reason string)."""

    @pytest.mark.xfail(
        reason="BarProcessor callback wiring (Task 11 Wave D) surfaces Fill "
        "on on_order_rejected. Simulate_reject records event but doesn't yet "
        "attach Fill via M5 exec-report path.",
    )
    def test_on_order_rejected_fill_has_exec_type_rejected(self):
        from v5.strategy_api import BaseStrategy
        from v5.orders import ExecType
        from v5.universe_context import UniverseContext

        captured_fills: list = []

        class _Watcher(BaseStrategy):
            def on_order_rejected(self, order, reason):
                # Engine must publish the rejection Fill on a separate hook OR
                # attach it to the order's most recent exec report. Test asserts
                # the Fill-level FIX discipline is preserved.
                last_fill = getattr(order, "last_exec_report", None)
                if last_fill is not None:
                    captured_fills.append(last_fill)

        strat = _Watcher()
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=30, seed=0, strategies=[strat],
        )
        order = ctx.orders.arm_bracket(
            entry_spec={"symbol": "BTC", "direction": "LONG", "size": 1.0,
                        "trigger_price": 50_000.0},
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=50_000.0)
        ctx.orders.simulate_reject(order.order_id, leg_idx=1, reason="venue_reject")

        assert len(captured_fills) >= 1, "engine must attach Fill to rejected order"
        assert captured_fills[-1].exec_type == ExecType.REJECTED, (
            f"AC-O3 FIX discipline: reject Fill.exec_type must be REJECTED; "
            f"got {captured_fills[-1].exec_type}"
        )


class TestNoCascadeWhenFillPolicyDisallows:
    """AC-O3 — cascade only triggers under LegFillPolicy.UNWIND_ON_REJECT.

    Reviewer H4 / M-quant: negative-path test must use a MULTI-LEG bracket
    with a non-UNWIND policy, not a single-leg order. Single-leg can't
    exercise sibling-leg gating.
    """

    def test_no_reversal_when_bracket_policy_is_best_effort(self):
        """Multi-leg bracket with BEST_EFFORT policy — sibling reject must NOT cascade."""
        from v5.orders import LegFillPolicy
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0, equity=150_000.0)
        order = ctx.orders.arm_bracket(
            entry_spec={"symbol": "BTC", "direction": "LONG", "size": 1.0,
                        "trigger_price": 50_000.0},
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
            fill_policy=LegFillPolicy.BEST_EFFORT,  # NOT UNWIND_ON_REJECT
        )
        ctx.orders.simulate_fill(order.order_id, leg_idx=0, qty=1.0, price=50_000.0)
        ctx.orders.simulate_reject(order.order_id, leg_idx=1, reason="venue_reject")

        # BEST_EFFORT: entry fill remains open despite SL reject; no reversal
        assert ctx.orders.last_reversal_order() is None, (
            "AC-O3: BEST_EFFORT bracket must NOT cascade on sibling-leg reject; "
            "cascade is UNWIND_ON_REJECT-only"
        )
