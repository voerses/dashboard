"""M10 B2 — `v5/sizing_legacy.py` DELETED (AC #13).

Test enforces:
    1. `v5/sizing_legacy.py` file does not exist on disk.
    2. `import v5.sizing_legacy` raises `ModuleNotFoundError`.

After B1 proves FIXED_FRACTION pipeline equivalence, the legacy shim
must be deleted. This test locks that outcome.

MUST FAIL TODAY — `v5/sizing_legacy.py` is still present (consumed by
`v5/simulator.py:35` + `v5/paper_engine.py:1629`).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_SIZING_LEGACY_PATH = Path(__file__).resolve().parent.parent / "sizing_legacy.py"


class TestSizingLegacyDeleted:
    """B2 — post-Phase-4 assertion: the shim is gone."""

    def test_sizing_legacy_file_absent(self):
        """The file `v5/sizing_legacy.py` must not exist."""
        assert _SIZING_LEGACY_PATH.exists() is False, (
            f"v5/sizing_legacy.py must be DELETED in M10 (found: "
            f"{_SIZING_LEGACY_PATH}). FIXED_FRACTION clamp pipeline "
            f"supersedes the legacy KellySizing shim (AC #13)."
        )

    def test_import_raises_modulenotfounderror(self):
        """`import v5.sizing_legacy` must raise ModuleNotFoundError."""
        # Evict any cached import from prior tests so the assertion is real.
        for mod_name in list(sys.modules):
            if mod_name == "v5.sizing_legacy" or mod_name.startswith("v5.sizing_legacy."):
                del sys.modules[mod_name]
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("v5.sizing_legacy")
