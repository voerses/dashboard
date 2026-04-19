"""M6 — FIX docstring coverage (T-D16 / AC-D16).

All 14 FIX tags listed in the brief's FIX vocabulary block MUST appear in at
least one DOCSTRING under v5/data/ — not a line comment, not an identifier.
Uses stdlib ast.parse + ast.get_docstring (NOT subprocess, NOT raw text grep).
A tag in `# TODO handle MDEntryType(269)` must NOT satisfy the test.

All tests MUST FAIL today — v5/data/ tree does not exist.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


REQUIRED_FIX_TAGS = (
    "MDEntryType(269)",
    "Side(54)",
    "SecurityID(48)",
    "SecurityIDSource(22)",
    "SecurityExchange(207)",
    "Product(460)",
    "MinPriceIncrement(969)",
    "MinTradeVol(562)",
    "MarketDataRequest(V)",
    "MDReqID(262)",
    "SubscriptionRequestType(263)",
    "NoMDEntryTypes(267)",
    "MDUpdateType(265)",
    "TransactTime(60)",
)


def _collect_docstrings() -> str:
    """Concatenate docstrings (module/class/function) under v5/data/.

    AC-D16 says "grep over v5/data/ source DOCSTRINGS" — not raw text. A stray
    comment must not satisfy the AC. Uses ast.get_docstring so only real
    PEP-257 docstrings are scanned. Also skips __pycache__ and generated files.
    """
    root = _project_root / "v5" / "data"
    if not root.exists():
        pytest.fail(f"v5/data/ does not exist yet (RED state expected for Phase 3): {root}")
    docstrings: list[str] = []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            pytest.fail(f"Unparsable source in {p}: {e}")
        # Module docstring
        module_doc = ast.get_docstring(tree)
        if module_doc:
            docstrings.append(module_doc)
        # Nested class/function docstrings
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                if doc:
                    docstrings.append(doc)
    return "\n\n".join(docstrings)


class TestFIXTagCoverage:
    """T-D16 / AC-D16 — every FIX tag appears ≥ 1 time in v5/data/ docstrings."""

    @pytest.mark.parametrize("tag", REQUIRED_FIX_TAGS)
    def test_fix_tag_present_in_docstring(self, tag):
        docstrings = _collect_docstrings()
        assert tag in docstrings, (
            f"FIX tag {tag!r} not found in any v5/data/ DOCSTRING — breaks AC-D16. "
            f"Line comments / identifiers do not count; tag must live in a PEP-257 "
            f"module/class/function docstring."
        )

    def test_all_14_tags_covered_count(self):
        """Sanity: exactly 14 FIX tags enumerated by the brief."""
        assert len(REQUIRED_FIX_TAGS) == 14
        assert len(set(REQUIRED_FIX_TAGS)) == 14

    def test_init_module_docstring_mentions_fix_block(self):
        """FIX vocabulary block must live in v5/data/__init__.py per the brief."""
        init = _project_root / "v5" / "data" / "__init__.py"
        assert init.exists(), f"expected {init} to exist for RED check"
        # Parse AST to ensure the block lives in a real module docstring, not a raw comment
        tree = ast.parse(init.read_text())
        module_doc = ast.get_docstring(tree) or ""
        assert "FIX vocabulary mapping" in module_doc, (
            "v5/data/__init__.py MODULE DOCSTRING must contain the 'FIX vocabulary mapping' "
            "block (AC-D16). Line comments do not satisfy this AC."
        )
