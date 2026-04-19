"""M6 — Infrastructure wall-clock carve-out documented (T-D21 / AC-D20).

Covers:
  - AC-D20 T-D21: each DataClient module (binance_ws, binance_rest,
    parquet_replay) must document in its module docstring the specific
    wall-clock reads it performs. Grep test asserts each module's
    docstring contains an "Infrastructure wall-clock reads:" section.

All tests MUST FAIL today — v5/data/clients/ modules do not exist.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


INFRA_MODULES = (
    "v5/data/clients/binance_ws.py",
    "v5/data/clients/binance_rest.py",
    "v5/data/clients/parquet_replay.py",
)


@pytest.mark.parametrize("rel_path", INFRA_MODULES)
class TestInfraWallClockDocs:
    """T-D21 / AC-D20 — infra modules declare their wall-clock reads."""

    def test_module_exists(self, rel_path):
        path = _project_root / rel_path
        assert path.exists(), f"expected infra module at {path}"

    def test_module_docstring_has_infra_wallclock_section(self, rel_path):
        """Module docstring must contain 'Infrastructure wall-clock reads:'."""
        path = _project_root / rel_path
        if not path.exists():
            pytest.fail(f"expected infra module at {path}")
        tree = ast.parse(path.read_text())
        doc = ast.get_docstring(tree) or ""
        assert "Infrastructure wall-clock reads:" in doc, (
            f"{rel_path} module docstring must contain "
            f"'Infrastructure wall-clock reads:' section per AC-D20"
        )

    def test_infra_section_enumerates_permitted_reads(self, rel_path):
        """AC-D20 brief line 843: section must ENUMERATE the permitted reads
        (not just have an empty header). A header with no bullet content
        satisfies the weak string check but violates the AC spirit —
        enforce at least one enumeration marker after the header.
        """
        path = _project_root / rel_path
        if not path.exists():
            pytest.fail(f"expected infra module at {path}")
        tree = ast.parse(path.read_text())
        doc = ast.get_docstring(tree) or ""
        header = "Infrastructure wall-clock reads:"
        if header not in doc:
            pytest.fail(f"{rel_path}: header missing (covered by sibling test)")
        # Content after the header — must have at least one bullet / line
        tail = doc.split(header, 1)[1].strip()
        # Non-empty; first line after header references either a known time call
        # or "(none)" for REPLAY clients. Sufficient-enumeration markers:
        markers = ("time.", "datetime", "- ", "* ", "(none", "None")
        assert any(m in tail.split("\n")[0:5].__str__() for m in markers), (
            f"{rel_path}: 'Infrastructure wall-clock reads:' header present but "
            f"first 5 lines after it do not enumerate reads (expected bullets, "
            f"time.time_ns()/datetime references, or '(none — ...)'. "
            f"Got section head: {tail[:200]!r}"
        )
