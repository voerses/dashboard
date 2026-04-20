"""M10 B3 — `Position.leg` field DELETED; `leg_ref_id` is sole source (AC #13).

Test enforces:
    1. `"leg"` NOT present in `Position.__dataclass_fields__`.
    2. `"leg_ref_id"` IS present in `Position.__dataclass_fields__`.

The legacy string `leg: str = "primary"` field (at `v5/position.py:68`)
is superseded by the M5 FIX-aligned `leg_ref_id: Optional[str] = None`
back-link (FIX LegRefID(654)). 8 simulator read-sites must migrate to
`leg_ref_id` + venue/market lookup before deletion.

MUST FAIL TODAY — `Position.leg` is still declared.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestPositionLegFieldDeleted:
    """B3 — `Position.leg` is gone; `Position.leg_ref_id` stays."""

    def test_leg_field_not_in_position(self):
        """`Position.__dataclass_fields__` must not contain `leg`."""
        from v5.position import Position
        fields = Position.__dataclass_fields__
        assert "leg" not in fields, (
            f"Position.leg must be DELETED in M10 (AC #13). Found in "
            f"__dataclass_fields__: keys={list(fields.keys())}. "
            f"Migrate 8 simulator read-sites to leg_ref_id."
        )

    def test_leg_ref_id_field_present(self):
        """`Position.__dataclass_fields__` must contain `leg_ref_id`."""
        from v5.position import Position
        fields = Position.__dataclass_fields__
        assert "leg_ref_id" in fields, (
            f"Position.leg_ref_id is the M5 FIX-aligned successor — it "
            f"must remain. Found fields: {list(fields.keys())}"
        )
