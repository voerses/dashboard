"""M5 — Order.state derivation table (F1).

Covers:
  - T-M5-21: Parent Order.state derives from leg status distribution +
    fill_policy. 9-row table per design §F1.

Rows exercised:
  1. all legs FILLED → FILLED
  2. any REJECTED + ≥1 FILLED + UNWIND_ON_REJECT → REJECTED (after unwind)
  3. any REJECTED + ≥1 FILLED + BEST_EFFORT → PARTIALLY_FILLED
  4. any REJECTED + 0 FILLED → REJECTED
  5. all CANCELLED → CANCELLED
  6. entry FILLED + SL/TP ARMED|WORKING + OTO_BRACKET → PARTIALLY_FILLED
  7. entry FILLED + one SL/TP FILLED + sibling CANCELLED + OTO_BRACKET → FILLED
  8. mixed ARMED+WORKING → ARMED (none triggered) or TRIGGERED (any triggered)
  9. all EXPIRED → EXPIRED

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


def _mk_leg(ref: str, status_name: str) -> object:
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        venue="binance",
        direction=1,
        target_qty=1.0,
        cum_qty=1.0,
        size_share=0.5,
        order_type="market",
        status=getattr(LegStatus, status_name),
        trigger_price=None,
        limit_price=None,
        currency="USDT",
    )


def _build_order(leg_states: list[tuple[str, str]],
                 fill_policy_name: str = "UNWIND_ON_REJECT",
                 contingency_name: str = "NONE"):
    from v5.orders import (
        Order, TriggerType, LegFillPolicy, ContingencyType,
    )
    legs = tuple(_mk_leg(ref, status) for ref, status in leg_states)
    policy = getattr(LegFillPolicy, fill_policy_name)
    cont = getattr(ContingencyType, contingency_name)
    return Order.arm(
        strategy_id="s1", token="BTC", direction=1,
        trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
        working_price_source="last",
        armed_at=_dt("2026-04-01T00:00:00"),
        expires_at=None, sizing_ctx={},
        legs=legs,
        fill_policy=policy,
        contingency=cont,
    )


class TestTM521OrderStatusDerivation:
    """T-M5-21: parametrize every row of the F1 derivation table."""

    @pytest.mark.parametrize("leg_states,fill_policy,contingency,expected_status", [
        # Row 1: all FILLED → FILLED
        ([("A", "FILLED"), ("B", "FILLED")],
         "UNWIND_ON_REJECT", "NONE", "FILLED"),
        # Row 2: REJECTED + ≥1 FILLED + UNWIND_ON_REJECT → REJECTED
        ([("A", "FILLED"), ("B", "REJECTED")],
         "UNWIND_ON_REJECT", "NONE", "REJECTED"),
        # Row 3: REJECTED + ≥1 FILLED + BEST_EFFORT → PARTIALLY_FILLED
        ([("A", "FILLED"), ("B", "REJECTED")],
         "BEST_EFFORT", "NONE", "PARTIALLY_FILLED"),
        # Row 4: REJECTED + 0 FILLED → REJECTED
        ([("A", "REJECTED"), ("B", "REJECTED")],
         "UNWIND_ON_REJECT", "NONE", "REJECTED"),
        ([("A", "ARMED"), ("B", "REJECTED")],
         "BEST_EFFORT", "NONE", "REJECTED"),
        # Row 5: all CANCELLED → CANCELLED
        ([("A", "CANCELLED"), ("B", "CANCELLED")],
         "UNWIND_ON_REJECT", "NONE", "CANCELLED"),
        # Row 6: OTO_BRACKET entry FILLED + SL/TP ARMED|WORKING → PARTIALLY_FILLED
        ([("E", "FILLED"), ("SL", "WORKING"), ("TP", "ARMED")],
         "OTO_BRACKET", "OTO", "PARTIALLY_FILLED"),
        # Row 7: OTO_BRACKET entry FILLED + SL FILLED + TP CANCELLED → FILLED
        ([("E", "FILLED"), ("SL", "FILLED"), ("TP", "CANCELLED")],
         "OTO_BRACKET", "OTO", "FILLED"),
        # Row 8a: mixed ARMED + WORKING no-trigger → ARMED
        ([("A", "ARMED"), ("B", "ARMED")],
         "UNWIND_ON_REJECT", "NONE", "ARMED"),
        # Row 9: all EXPIRED → EXPIRED
        ([("A", "EXPIRED"), ("B", "EXPIRED")],
         "UNWIND_ON_REJECT", "NONE", "EXPIRED"),
    ])
    def test_status_derivation_row(self, leg_states, fill_policy,
                                   contingency, expected_status):
        """T-M5-21: assert Order.state matches the F1 derivation table row."""
        from v5.orders import OrderStatus
        order = _build_order(leg_states, fill_policy, contingency)
        expected = getattr(OrderStatus, expected_status)
        assert order.state == expected, (
            f"legs={leg_states} policy={fill_policy} cont={contingency} "
            f"→ expected {expected_status}, got {order.state!r}"
        )

    def test_empty_legs_uses_implicit_single_leg_status(self):
        """T-M5-21 edge case: legs=() — status derives from Order's own
        direct transitions (single-leg). ARMED initial state."""
        from v5.orders import Order, OrderStatus, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(),
        )
        assert order.state == OrderStatus.ARMED
