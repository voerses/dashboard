"""M10 G1 — Grep-zero compat shim invariant (AC #1).

Asserts two things:

  1. ``tools/grep_zero_shims.sh`` exits 0 on current v5/ non-test source.
  2. Inline ripgrep-free scan: the four marker strings
     ``v4 compat`` / ``backward compat`` / ``# TODO: remove`` /
     ``DEPRECATED`` have zero hits in ``v5/*.py`` outside ``v5/tests/**``
     (excluding the allowlisted renamed banner at ``v5/paper_state.py``
     line 1178 per AC #1).

MUST FAIL TODAY — shell script does not exist; v5/paper_state.py:1178,
v5/paper_config.py (confirmation_tiers), v5/cpcv.py (deflated_sharpe),
and v5/engine.py + v5/validation.py docstrings still carry marker
strings.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_GREP_SCRIPT = _project_root / "tools" / "grep_zero_shims.sh"
_V5_DIR = _project_root / "v5"

_MARKER_STRINGS = (
    "v4 compat",
    "backward compat",
    "# TODO: remove",
    "DEPRECATED",
)

# Allowlist — exactly one line per AC #1 guidance: the renamed
# back-compat loader banner in v5/paper_state.py at line 1178.
_ALLOWED_SENTINEL = "paper_state.py:1178"


class TestGrepZeroShimsScript:
    """AC #1 — shell script runs clean."""

    def test_script_exists(self):
        assert _GREP_SCRIPT.exists(), (
            f"tools/grep_zero_shims.sh must exist (AC #1). Missing: "
            f"{_GREP_SCRIPT}"
        )

    def test_script_is_executable(self):
        assert _GREP_SCRIPT.stat().st_mode & 0o111, (
            "tools/grep_zero_shims.sh must be executable (chmod +x)."
        )

    def test_script_returns_zero(self):
        result = subprocess.run(
            ["bash", str(_GREP_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_project_root),
            timeout=60,
        )
        assert result.returncode == 0, (
            f"tools/grep_zero_shims.sh returned {result.returncode} — "
            f"v5/ non-test source still contains compat marker strings.\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )


class TestInlineMarkerScan:
    """AC #1 — inline walk-the-tree scan for the 4 marker strings."""

    def _scan_v5(self, marker: str) -> list[str]:
        hits: list[str] = []
        for py_file in sorted(_V5_DIR.rglob("*.py")):
            # Exclude tests per AC #1 allowlist.
            rel = py_file.relative_to(_project_root).as_posix()
            if rel.startswith("v5/tests/") or "/tests/" in rel:
                continue
            try:
                for lineno, line in enumerate(
                    py_file.read_text().splitlines(), start=1
                ):
                    if marker in line:
                        location = f"{rel}:{lineno}"
                        # Allowlisted sentinel per AC #1 — the renamed
                        # v4-log loader banner.
                        if _ALLOWED_SENTINEL in location:
                            continue
                        hits.append(f"{location}: {line.rstrip()}")
            except OSError:
                continue
        return hits

    @pytest.mark.parametrize("marker", _MARKER_STRINGS)
    def test_marker_has_zero_hits_in_v5_non_test(self, marker):
        hits = self._scan_v5(marker)
        assert hits == [], (
            f"Compat marker {marker!r} still present in v5/ non-test "
            f"source (AC #1 requires zero hits).\n"
            + "\n".join(hits)
        )
