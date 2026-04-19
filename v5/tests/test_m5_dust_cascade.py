"""M5 — Dust cascade invariants (AC-P10a/b/c).

Covers:
  - T-M5-06: AC-P10a idempotency — both linked legs dust-promote same bar →
    exactly ONE linked_exit ClosedTrade per position; no double-emission.
  - T-M5-07: AC-P10b ARMED-sibling cascade — WORKING leg dust-promotes →
    ARMED sibling transitions ARMED→CANCELLED (not force_close);
    cancel_reason='linked_exit'.
  - T-M5-08: AC-P10c per-leg dust thresholds — spot leg dust_usd=1.0 vs
    perp leg dust_usd=10.0; cascade uses promoting leg's threshold to
    identify promotion, cascaded leg's threshold to evaluate post-close.

All tests MUST FAIL today — v5.orders + DustHandler do not exist.
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


def _mk_leg(ref: str, market: str, dust_usd: float, status=None) -> object:
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        market=market,
        venue="binance",
        direction=1,
        target_qty=1.0,
        cum_qty=1.0,
        size_share=0.5,
        order_type="market",
        status=status or LegStatus.WORKING,
        trigger_price=None,
        limit_price=None,
        currency="USDT",
        dust_usd=dust_usd,
    )


class TestTM506DustCascadeIdempotency:
    """T-M5-06 / AC-P10a: exactly ONE linked_exit ClosedTrade per position."""

    def test_both_legs_dust_promote_same_bar_one_close(self):
        """T-M5-06 / AC-P10a: two legs both dust-promote in same bar → exactly
        one ClosedTrade emission per position (the linked_exit), not two.

        Tests observable behavior: count emitted ClosedTrade objects per
        position_id. Does NOT inspect private handler state.
        """
        from v5.orders import (
            Order, LegFillPolicy, TriggerType, ContingencyType,
        )
        from v5.dust_handler import DustHandler
        leg_a = _mk_leg("A", market="spot", dust_usd=1.0)
        leg_b = _mk_leg("B", market="perp", dust_usd=1.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.OCO,
            order_id="ord-dust-1",
        )
        # Materialize two linked positions (one per leg).
        positions = order.materialize_positions()
        assert len(positions) == 2
        handler = DustHandler()
        # Fire cascade by promoting leg A.
        handler.on_dust_promotion(
            order=order, leg_ref_id="A", positions=positions,
        )
        # Same bar: leg B also dust-promotes — must be idempotent (no-op).
        handler.on_dust_promotion(
            order=order, leg_ref_id="B", positions=positions,
        )
        emitted = list(handler.emitted_closed_trades())
        # Bucket by position_id and assert exactly 1 close per position.
        per_position: dict = {}
        for ct in emitted:
            pid = getattr(ct, "position_id", None)
            per_position.setdefault(pid, []).append(ct)
        for pid, trades in per_position.items():
            assert len(trades) == 1, (
                f"AC-P10a violated: position {pid} got {len(trades)} "
                f"ClosedTrades; expected exactly 1"
            )
        assert len(per_position) == 2, (
            f"Expected ClosedTrades for both positions; got {list(per_position)}"
        )

    def test_fired_order_ids_records_cascade(self):
        """T-M5-06: DustHandler._fired_order_ids tracks order_id after firing."""
        from v5.orders import (
            Order, LegFillPolicy, TriggerType, ContingencyType,
        )
        from v5.dust_handler import DustHandler  # noqa: F401 — expected ImportError
        leg_a = _mk_leg("A", market="spot", dust_usd=1.0)
        leg_b = _mk_leg("B", market="perp", dust_usd=1.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.OCO,
        )
        handler = DustHandler()
        handler.on_dust_promotion(order=order, leg_ref_id="A")
        # Second invocation must be a no-op.
        handler.on_dust_promotion(order=order, leg_ref_id="B")
        assert order.order_id in handler._fired_order_ids


class TestTM507ArmedSiblingCascade:
    """T-M5-07 / AC-P10b: WORKING leg dust-promotes → ARMED sibling CANCELLED."""

    def test_armed_sibling_cancelled_not_force_closed(self):
        """T-M5-07: leg A WORKING dust-promotes; leg B is ARMED (no position yet) →
        B transitions ARMED→CANCELLED; B is NOT force_closed (no position)."""
        from v5.orders import (
            Order, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )
        from v5.dust_handler import DustHandler
        leg_a = _mk_leg("A", market="perp", dust_usd=1.0,
                        status=LegStatus.WORKING)
        leg_b = _mk_leg("B", market="perp", dust_usd=1.0,
                        status=LegStatus.ARMED)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.OCO,
        )
        handler = DustHandler()
        new_order = handler.on_dust_promotion(order=order, leg_ref_id="A")
        legs_by_ref = {lg.leg_ref_id: lg for lg in new_order.legs}
        assert legs_by_ref["B"].status == LegStatus.CANCELLED
        # Retrieve cancel_reason from emitted events.
        events = list(new_order.emitted_events())
        cancel_events = [e for e in events
                         if e.get("event") == "cancelled"
                         and e.get("leg_ref_id") == "B"]
        assert cancel_events, f"Missing cancel event for leg B: {events}"
        assert cancel_events[0].get("cancel_reason") == "linked_exit"


class TestTM508PerLegDustThresholds:
    """T-M5-08 / AC-P10c: per-leg dust_usd fields independent."""

    def test_promoting_leg_uses_own_threshold(self):
        """T-M5-08: leg A (spot, dust_usd=1.0) promotes when residual < 1.0.
        Leg B (perp, dust_usd=10.0) cascade uses B's own threshold for its
        post-close evaluation."""
        from v5.orders import (
            Order, LegFillPolicy, TriggerType, ContingencyType,
        )
        from v5.dust_handler import DustHandler
        leg_a = _mk_leg("A", market="spot", dust_usd=1.0)
        leg_b = _mk_leg("B", market="perp", dust_usd=10.0)
        # Leg A's dust threshold is 1.0 — evaluate promotion relative to it.
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.OCO,
        )
        handler = DustHandler()
        # Simulate: leg A with $0.5 residual USD → promotes per spot's $1 threshold.
        promoted_ref = handler.identify_promotion(
            order=order,
            leg_residuals_usd={"A": 0.5, "B": 5.0},
        )
        assert promoted_ref == "A", (
            "Leg A ($0.5 residual) should promote at spot threshold $1.0; "
            "Leg B ($5.0) does NOT promote at perp threshold $10.0"
        )

    def test_cascaded_leg_uses_own_threshold_post_close(self):
        """T-M5-08: cascaded leg B applies its own dust_usd=10.0 for post-close
        evaluation (e.g. residual retention vs full cleanup)."""
        from v5.orders import Leg
        from v5.dust_handler import DustHandler
        leg_b = Leg(
            leg_ref_id="B",
            symbol="BTCUSDT",
            market="perp",
            venue="binance",
            direction=1, target_qty=1.0, cum_qty=1.0,
            size_share=0.5,
            order_type="market",
            status=__import__("v5.orders", fromlist=["LegStatus"]).LegStatus.WORKING,
            trigger_price=None, limit_price=None,
            currency="USDT", dust_usd=10.0,
        )
        handler = DustHandler()
        post_close_action = handler.evaluate_post_close(
            leg=leg_b, residual_usd=5.0,
        )
        # B's own dust_usd=10.0 → $5 residual is BELOW its threshold → 'zero_out'.
        assert post_close_action == "zero_out"
