"""M7 — FIX wire serializers for OrderStatus / TriggerType / LegStatus (AC-O2).

ARMED/TRIGGERED/RELEASED → FIX OrdStatus='A' + custom tag 9001/9002/9003.
Other states map to standard FIX OrdStatus chars.

All tests MUST FAIL today — the serializers raise NotImplementedError.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestOrderStatusToFixOrdStatus:
    """AC-O2 — OrderStatus.to_fix_ordstatus() returns the correct wire char."""

    @pytest.mark.parametrize("state_name, expected", [
        ("ARMED", "A"),
        ("TRIGGERED", "A"),
        ("RELEASED", "A"),
        ("PARTIALLY_FILLED", "1"),
        ("FILLED", "2"),
        ("CANCELLED", "4"),
        ("REJECTED", "8"),
        ("EXPIRED", "C"),
    ])
    def test_to_fix_ordstatus_mapping(self, state_name, expected):
        from v5.orders import OrderStatus
        state = getattr(OrderStatus, state_name)
        assert state.to_fix_ordstatus() == expected

    @pytest.mark.parametrize("state_name, expected_tag", [
        ("ARMED", 9001),
        ("TRIGGERED", 9002),
        ("RELEASED", 9003),
    ])
    def test_engine_internal_states_have_custom_tag(self, state_name, expected_tag):
        """Reviewer H3 fix — no silent default. Missing attribute fails the test."""
        from v5.orders import OrderStatus
        state = getattr(OrderStatus, state_name)
        assert hasattr(state, "fix_custom_tag"), (
            f"AC-O2: OrderStatus.{state_name} must declare fix_custom_tag attribute "
            f"(engine-internal state tag per G8 decision)"
        )
        tag = state.fix_custom_tag
        if callable(tag):
            tag = tag()
        assert tag == expected_tag, (
            f"AC-O2: OrderStatus.{state_name}.fix_custom_tag must be {expected_tag}; "
            f"got {tag!r}"
        )

    def test_audit_log_records_engine_internal_state(self):
        """Reviewer H3 — G8 decision says ARMED/TRIGGERED/RELEASED wire-map is
        'A' + custom tag + **audit log annotation**. Audit log must be present.

        M5 shipped Order as frozen + `state` (not `status`) field. State
        transitions go via M5 transition methods that use object.__setattr__;
        this test uses log_fix_state_change() helper to record an audit entry
        directly for wire-serialization testing.
        """
        from v5.orders import OrderStatus, Order

        order = Order.build_for_test(order_id=0xDEADBEEF)
        # Audit log attribute present
        assert hasattr(order, "fix_audit_log"), (
            "AC-O2: Order must expose fix_audit_log for engine-internal state tracking"
        )
        # M7 state transitions explicitly log via log_fix_state_change
        order.log_fix_state_change(OrderStatus.ARMED)
        order.log_fix_state_change(OrderStatus.TRIGGERED)
        entries = list(order.fix_audit_log)
        assert any("ARMED" in str(e) for e in entries), (
            f"AC-O2: audit log must record ARMED state; got {entries!r}"
        )
        assert any("TRIGGERED" in str(e) for e in entries), (
            f"AC-O2: audit log must record TRIGGERED state; got {entries!r}"
        )


class TestTriggerTypeToFixTriggerType:
    """AC-O2 — TriggerType.to_fix_trigger_type() returns FIX TriggerType int."""

    @pytest.mark.parametrize("kind_name", [
        "PRICE_ABOVE", "PRICE_BELOW", "MARK_ABOVE", "MARK_BELOW",
    ])
    def test_price_and_mark_map_to_price_movement_4(self, kind_name):
        from v5.orders import TriggerType
        assert getattr(TriggerType, kind_name).to_fix_trigger_type() == 4


class TestTriggerTypeToFixTriggerPriceDirection:
    """AC-O2 — to_fix_trigger_price_direction() returns 'U' (up) or 'D' (down)."""

    @pytest.mark.parametrize("kind_name, expected", [
        ("PRICE_ABOVE", "U"),
        ("PRICE_BELOW", "D"),
        ("MARK_ABOVE", "U"),
        ("MARK_BELOW", "D"),
    ])
    def test_direction_mapping(self, kind_name, expected):
        from v5.orders import TriggerType
        assert getattr(TriggerType, kind_name).to_fix_trigger_price_direction() == expected


class TestLegStatusToFixOrdStatus:
    """AC-O2 — LegStatus.to_fix_ordstatus() for each leg status."""

    @pytest.mark.parametrize("state_name, expected", [
        ("ARMED", "A"),
        ("WORKING", "A"),
        ("PARTIALLY_FILLED", "1"),
        ("FILLED", "2"),
        ("CANCELLED", "4"),
        ("REJECTED", "8"),
        ("EXPIRED", "C"),
    ])
    def test_leg_to_fix_ordstatus_mapping(self, state_name, expected):
        from v5.orders import LegStatus
        assert getattr(LegStatus, state_name).to_fix_ordstatus() == expected


class TestWireSerializersNoLongerNotImplemented:
    """AC-O2 — serializers must no longer raise NotImplementedError."""

    def test_order_status_serializer_does_not_raise(self):
        from v5.orders import OrderStatus
        for s in OrderStatus:
            r = s.to_fix_ordstatus()
            assert isinstance(r, str) and len(r) == 1

    def test_trigger_type_serializer_does_not_raise(self):
        from v5.orders import TriggerType
        for t in TriggerType:
            if t.name in ("TIME_AT", "BAR_CLOSE"):
                continue
            assert isinstance(t.to_fix_trigger_type(), int)

    def test_leg_status_serializer_does_not_raise(self):
        from v5.orders import LegStatus
        for s in LegStatus:
            r = s.to_fix_ordstatus()
            assert isinstance(r, str) and len(r) == 1
