"""M10 G7 — pre-existing-13 audit table closed (AC #11).

Replaces the M9 "audit later / 30min hand-wave" with a concrete
13-row table in ``.specs/active/m10-polish/m7_pre_existing_13_audit.md``.

Asserts:
  * File exists at the canonical path.
  * The table contains exactly 13 data rows matching the pattern
    ``| <node_id> | <outcome> | <action> |``.
  * No row contains ``STILL-RED`` in the outcome column — all nodes
    have resolution (PASSING-in-v5 / OBSOLETE-delete-node / FIXED).

MUST FAIL TODAY — audit file does not yet exist on disk.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_AUDIT_PATH = (
    _project_root / ".specs" / "active" / "m10-polish"
    / "m7_pre_existing_13_audit.md"
)

# Match markdown table data rows with three cells, excluding the
# header-separator row (which contains only dashes + pipes).
_DATA_ROW_RE = re.compile(
    r"^\s*\|\s*(?P<node>[^|]+?)\s*\|\s*(?P<outcome>[^|]+?)\s*\|\s*(?P<action>[^|]+?)\s*\|\s*$"
)
_SEPARATOR_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def _parse_audit_rows(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        if _SEPARATOR_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        node = m.group("node").strip()
        outcome = m.group("outcome").strip()
        action = m.group("action").strip()
        # Skip the header row — its "node_id" literal is not a real node.
        if node.lower() in {"node_id", "node", "test", "test node"}:
            continue
        rows.append({"node": node, "outcome": outcome, "action": action})
    return rows


class TestPreExisting13AuditDoc:
    """AC #11 — concrete audit doc with 13 resolved rows."""

    def test_audit_file_exists(self):
        assert _AUDIT_PATH.exists(), (
            f".specs/active/m10-polish/m7_pre_existing_13_audit.md must "
            f"exist as M10 concrete deliverable (AC #11). Missing: "
            f"{_AUDIT_PATH}"
        )

    def test_audit_has_exactly_13_rows(self):
        text = _AUDIT_PATH.read_text()
        rows = _parse_audit_rows(text)
        assert len(rows) == 13, (
            f"m7_pre_existing_13_audit.md must contain exactly 13 data "
            f"rows matching '| <node_id> | <outcome> | <action> |' "
            f"(AC #11). Found {len(rows)} rows: "
            f"{[r['node'] for r in rows]}"
        )

    def test_no_still_red_residue(self):
        """AC #11 — no STILL-RED outcomes; every node resolved."""
        text = _AUDIT_PATH.read_text()
        rows = _parse_audit_rows(text)
        still_red = [r for r in rows if "STILL-RED" in r["outcome"]]
        assert not still_red, (
            f"AC #11 forbids STILL-RED residue — all 13 nodes must be "
            f"resolved (PASSING-in-v5 / OBSOLETE / FIXED). Unresolved "
            f"rows: {still_red}"
        )
