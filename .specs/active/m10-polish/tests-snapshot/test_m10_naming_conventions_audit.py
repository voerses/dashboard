"""M10 F1 — Naming conventions canonical doc + audit script (AC #2).

Asserts:
  1. ``knowledge/NAMING_CONVENTIONS.md`` exists as the source-of-truth doc.
  2. The doc enumerates the 5 canonical terms: symbol / instrument / bar /
     position / closed_trade.
  3. ``tools/audit_naming.sh`` returns 0 on the current v5/ source tree
     (i.e. no ``\\btoken\\b`` or ``\\bcandle\\b`` violations outside
     documented exceptions).

MUST FAIL TODAY — neither the doc, the exceptions file, nor the audit
shell script exist on disk; v5/ source still contains ``token`` /
``candle`` in many places.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_NAMING_DOC = _project_root / "knowledge" / "NAMING_CONVENTIONS.md"
_AUDIT_SCRIPT = _project_root / "tools" / "audit_naming.sh"

_CANONICAL_TERMS = ("symbol", "instrument", "bar", "position", "closed_trade")


class TestNamingConventionsDocExists:
    """AC #2 — ``knowledge/NAMING_CONVENTIONS.md`` is source-of-truth."""

    def test_doc_file_exists(self):
        assert _NAMING_DOC.exists(), (
            f"knowledge/NAMING_CONVENTIONS.md must exist as the canonical "
            f"terminology source-of-truth for M10 (AC #2). Missing: "
            f"{_NAMING_DOC}"
        )

    def test_doc_is_substantive(self):
        """Reject a stub — minimum 500 chars of actual content."""
        text = _NAMING_DOC.read_text()
        assert len(text) > 500, (
            f"knowledge/NAMING_CONVENTIONS.md is too short ({len(text)} "
            f"chars); must be a real canonical doc, not a stub."
        )

    @pytest.mark.parametrize("term", _CANONICAL_TERMS)
    def test_doc_mentions_canonical_term(self, term):
        text = _NAMING_DOC.read_text()
        assert term in text, (
            f"knowledge/NAMING_CONVENTIONS.md must enumerate canonical "
            f"term {term!r} (symbol/instrument/bar/position/closed_trade)."
        )


class TestAuditNamingScriptPasses:
    """AC #2 — ``tools/audit_naming.sh`` exits 0 against current v5/."""

    def test_script_exists_and_executable(self):
        assert _AUDIT_SCRIPT.exists(), (
            f"tools/audit_naming.sh must exist (AC #2). Missing: "
            f"{_AUDIT_SCRIPT}"
        )
        # stat().st_mode & 0o111 — at least one execute bit set
        assert _AUDIT_SCRIPT.stat().st_mode & 0o111, (
            f"tools/audit_naming.sh must be executable (chmod +x)."
        )

    def test_script_returns_zero(self, tmp_path):
        """AC #2 — audit script returns 0 on cleaned v5/ source."""
        result = subprocess.run(
            ["bash", str(_AUDIT_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_project_root),
            timeout=60,
        )
        assert result.returncode == 0, (
            f"tools/audit_naming.sh returned {result.returncode} — v5/ "
            f"source still contains non-canonical terms.\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
