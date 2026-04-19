"""M5 — Multi-leg fill policy semantics.

Covers:
  - T-M5-03: UNWIND_ON_REJECT — leg A filled, leg B rejected → engine
    market-closes A with documented slippage; sibling_unwound event.
  - T-M5-04: BEST_EFFORT — leg A filled, leg B rejected → A stays, B CANCELLED.
  - T-M5-05: OTO_BRACKET — entry fills → SL + TP atomically ARMED→WORKING;
    bracket_activated event.
  - T-M5-22: pre-fill atomic rejection — 2-leg Order, leg B fails risk check
    at TRIGGERED→RELEASED transition → NEITHER leg opens; Order.state=REJECTED
    with reject_reason="risk_on_release_atomic".

Design note (R13/R14): per-leg margin is stored in ``Leg.sizing_ctx["margin_usd"]``
(consistent with PendingEntry's sizing_ctx pattern). The Leg dataclass does NOT
get a new top-level ``margin_usd`` field. Tests that need per-leg margin read
``leg.sizing_ctx["margin_usd"]``.

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


def _mk_leg(ref: str, market: str, direction: int, qty: float = 1.0,
            margin_usd: float = 100.0) -> object:
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        market=market,
        venue="binance",
        direction=direction,
        target_qty=qty,
        cum_qty=0.0,
        size_share=0.5,
        order_type="market",
        status=LegStatus.ARMED,
        trigger_price=None,
        limit_price=None,
        currency="USDT",
        # R13/R14: per-leg margin stored in sizing_ctx (not as Leg field).
        sizing_ctx={"margin_usd": margin_usd},
    )


class TestTM503UnwindOnReject:
    """T-M5-03: UNWIND_ON_REJECT — sibling unwind when post-fill rejection hits."""

    def test_unwound_sibling_marked_terminal(self):
        """T-M5-03: leg A filled, leg B rejected → A is force-closed (terminal).

        Under UNWIND_ON_REJECT the engine emits a ClosedTrade with
        exit_reason='sibling_unwound' for leg A. Leg A itself remains FILLED
        (terminal — the leg is closed via a ClosedTrade, the leg status stays
        FILLED as the terminal post-fill state). Leg B is REJECTED.
        This is the post-fill path (mock venue REJECT) per design F2.
        """
        from v5.orders import (
            Order, OrderStatus, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )
        leg_a = _mk_leg("A", market="spot", direction=1)
        leg_b = _mk_leg("B", market="perp", direction=-1)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.NONE,
        )
        order = order.on_leg_fill(leg_ref_id="A", filled_qty=1.0)
        order = order.on_leg_reject(leg_ref_id="B", reason="venue_reject")
        legs_by_ref = {lg.leg_ref_id: lg for lg in order.legs}
        # Leg A stays FILLED (terminal post-fill); the force-close is
        # expressed as a ClosedTrade with exit_reason='sibling_unwound'.
        assert legs_by_ref["A"].status == LegStatus.FILLED, (
            f"Leg A must be FILLED (terminal); got {legs_by_ref['A'].status}"
        )
        # Leg B is REJECTED.
        assert legs_by_ref["B"].status == LegStatus.REJECTED, (
            f"Leg B must be REJECTED; got {legs_by_ref['B'].status}"
        )
        # A ClosedTrade with exit_reason='sibling_unwound' must exist.
        closed_trades = list(order.emitted_closed_trades())
        unwound = [ct for ct in closed_trades
                   if getattr(ct, "exit_reason", None) == "sibling_unwound"]
        assert len(unwound) == 1, (
            f"Expected exactly 1 ClosedTrade with exit_reason='sibling_unwound'; "
            f"got {len(unwound)} (all: {[getattr(ct, 'exit_reason', None) for ct in closed_trades]})"
        )
        # Parent Order is REJECTED under UNWIND_ON_REJECT per F1.
        assert order.state == OrderStatus.REJECTED

    def test_sibling_unwound_event_emitted(self):
        """T-M5-03: orders_log emits 'sibling_unwound' event naming leg A."""
        from v5.orders import (
            Order, LegFillPolicy, TriggerType, ContingencyType,
        )
        leg_a = _mk_leg("A", market="spot", direction=1)
        leg_b = _mk_leg("B", market="perp", direction=-1)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.NONE,
        )
        order = order.on_leg_fill(leg_ref_id="A", filled_qty=1.0)
        order = order.on_leg_reject(leg_ref_id="B", reason="venue_reject")
        events = list(order.emitted_events())
        names = [e.get("event") for e in events]
        assert "sibling_unwound" in names, (
            f"Expected 'sibling_unwound' in emitted events; got {names}"
        )


class TestTM504BestEffort:
    """T-M5-04: BEST_EFFORT — rejected sibling is CANCELLED; filled sibling stays."""

    def test_filled_leg_stays_open_rejected_cancelled(self):
        """T-M5-04: A filled + B rejected → A FILLED, B CANCELLED, Order PARTIALLY_FILLED."""
        from v5.orders import (
            Order, OrderStatus, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )
        leg_a = _mk_leg("A", market="spot", direction=1)
        leg_b = _mk_leg("B", market="perp", direction=-1)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.BEST_EFFORT,
            contingency=ContingencyType.NONE,
        )
        order = order.on_leg_fill(leg_ref_id="A", filled_qty=1.0)
        order = order.on_leg_reject(leg_ref_id="B", reason="venue_reject")
        legs_by_ref = {lg.leg_ref_id: lg for lg in order.legs}
        assert legs_by_ref["A"].status == LegStatus.FILLED
        # Under BEST_EFFORT, engine initiates a cancellation of sibling B
        # when the venue rejects it; leg B must be CANCELLED (engine action),
        # not REJECTED (which would leak venue state through the engine).
        assert legs_by_ref["B"].status == LegStatus.CANCELLED, (
            f"Leg B must be engine-CANCELLED under BEST_EFFORT after venue "
            f"REJECT; got {legs_by_ref['B'].status}"
        )
        assert order.state == OrderStatus.PARTIALLY_FILLED


class TestTM505OtoBracket:
    """T-M5-05: OTO_BRACKET — entry fill ARMs sibling protective legs."""

    def test_entry_fill_arms_sibling_stops_atomically(self):
        """T-M5-05: entry leg fills → SL + TP transition ARMED→WORKING atomically."""
        from v5.orders import (
            Order, LegStatus, LegFillPolicy, TriggerType, ContingencyType,
        )
        entry = _mk_leg("E", market="perp", direction=1)
        sl = _mk_leg("SL", market="perp", direction=-1)
        tp = _mk_leg("TP", market="perp", direction=-1)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(entry, sl, tp),
            fill_policy=LegFillPolicy.OTO_BRACKET,
            contingency=ContingencyType.OTO,
        )
        order = order.on_leg_fill(leg_ref_id="E", filled_qty=1.0)
        legs_by_ref = {lg.leg_ref_id: lg for lg in order.legs}
        assert legs_by_ref["E"].status == LegStatus.FILLED
        assert legs_by_ref["SL"].status == LegStatus.WORKING
        assert legs_by_ref["TP"].status == LegStatus.WORKING

    def test_bracket_activated_event_emitted(self):
        """T-M5-05: orders_log emits 'bracket_activated' event on entry fill."""
        from v5.orders import (
            Order, LegFillPolicy, TriggerType, ContingencyType,
        )
        entry = _mk_leg("E", market="perp", direction=1)
        sl = _mk_leg("SL", market="perp", direction=-1)
        tp = _mk_leg("TP", market="perp", direction=-1)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(entry, sl, tp),
            fill_policy=LegFillPolicy.OTO_BRACKET,
            contingency=ContingencyType.OTO,
        )
        order = order.on_leg_fill(leg_ref_id="E", filled_qty=1.0)
        events = list(order.emitted_events())
        names = [e.get("event") for e in events]
        assert "bracket_activated" in names


class TestTM522PreFillAtomic:
    """T-M5-22: pre-fill atomic rejection at TRIGGERED→RELEASED."""

    def test_any_leg_fails_risk_no_leg_opens(self):
        """T-M5-22: leg B fails aggregate capital check → NEITHER leg opens;
        Order.state=REJECTED, reject_reason='risk_on_release_atomic'."""
        from v5.orders import (
            Order, OrderStatus, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )
        leg_a = _mk_leg("A", market="spot", direction=1, margin_usd=100.0)
        leg_b = _mk_leg("B", market="perp", direction=-1, margin_usd=100.0)
        # R13/R14: per-leg margin is read from leg.sizing_ctx["margin_usd"].
        assert leg_a.sizing_ctx["margin_usd"] == 100.0
        assert leg_b.sizing_ctx["margin_usd"] == 100.0
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={"margin_usd": 200.0},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.NONE,
        )
        # Trigger + attempt release with insufficient capital to satisfy
        # aggregate (sum of both legs' margins from each leg's sizing_ctx).
        order = order.trigger_immediately()
        order = order.release_atomic(available_capital_usd=50.0)
        assert order.state == OrderStatus.REJECTED
        assert order.reject_reason == "risk_on_release_atomic"
        for lg in order.legs:
            assert lg.status != LegStatus.FILLED, (
                f"No leg may open on atomic reject; leg {lg.leg_ref_id} "
                f"status={lg.status}"
            )

    def test_atomic_release_passes_all_legs_work(self):
        """T-M5-22: sufficient capital → all legs transition to WORKING."""
        from v5.orders import (
            Order, OrderStatus, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )
        leg_a = _mk_leg("A", market="spot", direction=1, margin_usd=100.0)
        leg_b = _mk_leg("B", market="perp", direction=-1, margin_usd=100.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={"margin_usd": 200.0},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.NONE,
        )
        order = order.trigger_immediately()
        order = order.release_atomic(available_capital_usd=500.0)
        assert order.state == OrderStatus.RELEASED
