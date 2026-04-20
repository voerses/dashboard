"""M10 B4 — `trigger_combined_entry` legacy branch DELETED (AC #13).

Test enforces:
    `v5.simulator` module does NOT expose `trigger_combined_entry`.

The ~140-line legacy branch at `v5/simulator.py:~3159-3290` is the
pre-M5 single-entry path. M5 multi-leg OTOCO is canonical (flag flipped
in M9 Phase 5). Once replay-parity passes on the multi-leg fixture,
this branch is deleted.

MUST FAIL TODAY — `trigger_combined_entry` is still defined.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestTriggerCombinedEntryDeleted:
    """B4 — `v5.simulator.trigger_combined_entry` absent."""

    def test_not_present_as_attribute(self):
        """`hasattr(v5.simulator, "trigger_combined_entry")` is False."""
        import v5.simulator
        assert hasattr(v5.simulator, "trigger_combined_entry") is False, (
            "v5.simulator.trigger_combined_entry (~140 lines) must be "
            "DELETED in M10 (AC #13). M5 multi-leg OTOCO is canonical."
        )

    def test_not_in_module_dir(self):
        """Secondary guard: the name is not in `dir(v5.simulator)`."""
        import v5.simulator
        assert "trigger_combined_entry" not in dir(v5.simulator), (
            "trigger_combined_entry still exported from v5.simulator; "
            "delete the legacy branch per AC #13."
        )
