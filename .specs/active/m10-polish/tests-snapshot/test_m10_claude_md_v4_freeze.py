"""M10 F3 — CLAUDE.md v4 freeze notice (AC #6).

Asserts that ``CLAUDE.md`` contains:
  1. A ``v4/ is FROZEN`` freeze notice mirroring the existing v3 language.
  2. A date stamp starting with ``2026-`` identifying the freeze date.
  3. The pre-existing ``v3/ is FROZEN`` line is NOT removed (regression
     guard — the v3 freeze remains intact).

MUST FAIL TODAY — CLAUDE.md Meta-Rule #6 still reads ``v3/ is FROZEN``
only; no v4 freeze notice has been added yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_CLAUDE_MD = _project_root / "CLAUDE.md"


class TestClaudeMdV4Freeze:
    """AC #6 — CLAUDE.md advertises v4 as FROZEN + dated."""

    def test_claude_md_exists(self):
        assert _CLAUDE_MD.exists(), f"CLAUDE.md missing at {_CLAUDE_MD}"

    def test_v4_is_frozen_notice_present(self):
        text = _CLAUDE_MD.read_text()
        assert "v4/ is FROZEN" in text, (
            "CLAUDE.md must contain the exact string 'v4/ is FROZEN' "
            "mirroring the v3 freeze language per AC #6."
        )

    def test_freeze_date_stamp_present(self):
        """CLAUDE.md must include a 2026- date stamp near the v4 freeze
        notice (freeze date per AC #6)."""
        text = _CLAUDE_MD.read_text()
        assert "2026-" in text, (
            "CLAUDE.md must include a 2026- date stamp indicating when "
            "v4/ was formally frozen (AC #6)."
        )

    def test_v3_freeze_notice_unchanged(self):
        """Regression guard — v3 freeze stays intact after v4 freeze add."""
        text = _CLAUDE_MD.read_text()
        assert "v3/ is FROZEN" in text, (
            "CLAUDE.md must still contain the pre-existing 'v3/ is "
            "FROZEN' line — do NOT remove the v3 freeze when adding v4."
        )
