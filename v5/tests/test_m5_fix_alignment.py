"""M5 — FIX-alignment documentation + serialization stubs.

Covers:
  - T-M5-11: grep v5/orders.py for FIX tag references (ClOrdID(11),
    OrdStatus(39), LegGrp(555), TriggerType(1100), ContingencyType(1385)).
  - T-M5-27: module docstring contains cross-leg atomicity rationale —
    "no venue supports atomic multi-leg across spot+perp".
  - T-M5-28: FIX serialization stubs raise NotImplementedError in M5
    (to be implemented in M7) — OrderStatus.to_fix_ordstatus(),
    TriggerType.to_fix_trigger_type(), to_fix_trigger_price_direction().
  - T-M5-29: Order.venue_order_id field defaults to None in paper/backtest
    and roundtrips through paper_state serialization.

All tests MUST FAIL today — v5.orders does not exist.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


_REQUIRED_FIX_TAGS = (
    "FIX ClOrdID(11)",
    "FIX OrdStatus(39)",
    "FIX LegGrp(555)",
    "FIX TriggerType(1100)",
    "FIX ContingencyType(1385)",
)


class TestTM511FixDocstringTags:
    """T-M5-11: grep v5/orders.py docstrings for required FIX tags."""

    def test_orders_module_file_exists(self):
        """T-M5-11: v5/orders.py exists (post-rename from pending_entry.py)."""
        orders_path = _project_root / "v5" / "orders.py"
        assert orders_path.is_file(), (
            f"v5/orders.py does not exist at {orders_path}"
        )

    @pytest.mark.parametrize("tag", _REQUIRED_FIX_TAGS)
    def test_fix_tag_in_orders_module(self, tag):
        """T-M5-11: each required FIX tag appears somewhere in v5/orders.py."""
        orders_path = _project_root / "v5" / "orders.py"
        if not orders_path.is_file():
            pytest.fail(f"v5/orders.py missing — cannot grep for {tag}")
        content = orders_path.read_text()
        assert tag in content, (
            f"FIX tag {tag!r} not found in v5/orders.py"
        )


class TestTM527CrossLegAtomicityRationale:
    """T-M5-27: module docstring states the cross-leg atomicity rationale."""

    def test_cross_leg_atomicity_rationale_in_module_docstring(self):
        """T-M5-27: v5/orders.py module-level docstring references the
        "no venue supports atomic multi-leg across spot+perp" rationale."""
        orders_path = _project_root / "v5" / "orders.py"
        if not orders_path.is_file():
            pytest.fail("v5/orders.py missing — cannot read module docstring")
        content = orders_path.read_text()
        # Accept either a phrase-level or keyword-level match.
        assert "no venue supports atomic multi-leg across spot+perp" in content, (
            "module docstring missing cross-leg atomicity rationale; "
            "expected phrase: "
            "'no venue supports atomic multi-leg across spot+perp'"
        )


class TestTM528FixSerializationStubs:
    """T-M5-28: FIX serialization helpers.

    M5 shipped these as `NotImplementedError` stubs. M7 Wave C implemented
    them as real FIX wire serializers (commit 58895bf). Tests updated to
    assert the current behavior per CLAUDE.md meta-rule #1 (code over specs).
    """

    def test_order_status_to_fix_ordstatus_implemented(self):
        """T-M5-28 (M7 evolution): OrderStatus.FILLED → FIX OrdStatus(39)='2'."""
        from v5.orders import OrderStatus
        assert OrderStatus.FILLED.to_fix_ordstatus() == "2"

    def test_trigger_type_to_fix_trigger_type_implemented(self):
        """T-M5-28 (M7 evolution): TriggerType.PRICE_ABOVE → FIX TriggerType(1100)=4."""
        from v5.orders import TriggerType
        assert TriggerType.PRICE_ABOVE.to_fix_trigger_type() == 4

    def test_trigger_type_to_fix_price_direction_implemented(self):
        """T-M5-28 (M7 evolution): PRICE_ABOVE → TriggerPriceDirection(1109)='U' (Up)."""
        from v5.orders import TriggerType
        assert TriggerType.PRICE_ABOVE.to_fix_trigger_price_direction() == "U"


class TestTM529VenueOrderIdStub:
    """T-M5-29: Order.venue_order_id field (FIX OrderID(37)) stub."""

    def test_venue_order_id_default_none(self):
        """T-M5-29: paper/backtest Orders have venue_order_id=None by default."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        assert order.venue_order_id is None

    def test_venue_order_id_roundtrips_through_paper_state(self):
        """T-M5-29: venue_order_id None roundtrips through to_json/from_json."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        dumped = order.to_json()
        # Field is present in JSON output even when None (explicit stub).
        assert "venue_order_id" in dumped, (
            f"venue_order_id missing from Order.to_json() output: {dumped}"
        )
        restored = Order.from_json(json.loads(json.dumps(dumped)))
        assert restored.venue_order_id is None
