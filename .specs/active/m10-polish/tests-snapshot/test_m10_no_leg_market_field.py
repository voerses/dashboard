"""M10 B7 — `Leg.market` field DELETED; `settlement_type` is sole field (AC #13).

Test enforces:
    1. `"market"` NOT present in `Leg.__dataclass_fields__`.
    2. `"settlement_type"` IS present in `Leg.__dataclass_fields__`.

The legacy `market: Literal["spot", "perp"]` field duplicates the
FIX-aligned `settlement_type: Literal["spot", "perp", "futures"]`
(mapping to FIX LegSettlType(587)). M10 keeps only `settlement_type`.

MUST FAIL TODAY — `Leg.market` is still declared at `v5/orders.py:397`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestLegMarketFieldDeleted:
    """B7 — `Leg.market` is gone; `Leg.settlement_type` stays."""

    def test_market_field_not_in_leg(self):
        """`Leg.__dataclass_fields__` must not contain `market`."""
        from v5.orders import Leg
        fields = Leg.__dataclass_fields__
        assert "market" not in fields, (
            f"Leg.market must be DELETED in M10 (AC #13); "
            f"`settlement_type` (FIX LegSettlType(587)) is canonical. "
            f"Found fields: {list(fields.keys())}"
        )

    def test_settlement_type_field_present(self):
        """`Leg.__dataclass_fields__` must contain `settlement_type`."""
        from v5.orders import Leg
        fields = Leg.__dataclass_fields__
        assert "settlement_type" in fields, (
            f"Leg.settlement_type is the FIX-aligned successor — it must "
            f"remain. Found fields: {list(fields.keys())}"
        )
