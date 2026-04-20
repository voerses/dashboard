"""M10 G9 — Zero-warnings invariant for the v5 test suite (AC #26).

Subprocess-runs ``pytest v5/tests/`` with default warning filters and
asserts the summary line reports 0 warnings. The 743-warning baseline
(measured 2026-04-20) is driven primarily by:

  * ~739 UserWarnings at ``v5/signals.py:434`` (s56 combined-mode
    fallback) — M10 fix: once-per-strategy emission policy.
  * 3 PytestCollectionWarnings at ``v5/testing.py:17`` — M10 fix:
    ``TestClock.__test__ = False``.
  * 1 Pandas4Warning at ``v5/data_resampler.py:270`` — M10 fix: drop
    ``copy=False`` kwarg.

Marked ``@pytest.mark.spawns_pytest_subprocess`` so the v5 conftest
depth-guard auto-skips this test at nested depth >= 2 (preventing
infinite recursion when this test's own subprocess re-collects the
v5 suite).

MUST FAIL TODAY — running the v5 suite today yields 743 warnings.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# Match "NN warning" / "NN warnings" in pytest short summary line.
_WARNINGS_RE = re.compile(r"(?P<count>\d+)\s+warnings?\b")


@pytest.mark.spawns_pytest_subprocess
class TestZeroWarnings:
    """AC #26 — pytest v5/tests/ reports 0 warnings."""

    def test_v5_suite_emits_zero_warnings(self):
        """Run ``pytest v5/tests/`` subprocess; assert summary has 0 warnings.

        Uses ``-W default`` to ensure warnings surface (pytest may
        otherwise silence some categories). Captures stdout+stderr and
        searches the short-summary region for a ``N warning(s)`` token.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "v5/tests/",
                "--no-header",
                "-p",
                "no:cacheprovider",
                "-q",
                "-W",
                "default",
            ],
            capture_output=True,
            text=True,
            cwd=str(_project_root),
            timeout=1800,
        )

        output = (result.stdout or "") + (result.stderr or "")
        matches = list(_WARNINGS_RE.finditer(output))

        if not matches:
            # No warning count token at all → implicitly zero warnings.
            return

        # At least one "N warning(s)" token present — any positive count
        # fails AC #26.
        positive_counts = [
            int(m.group("count")) for m in matches if int(m.group("count")) > 0
        ]
        assert not positive_counts, (
            f"AC #26 requires 0 warnings from v5/tests/. Found counts: "
            f"{positive_counts}.\n"
            f"--- tail of output ---\n{output[-3000:]}"
        )
