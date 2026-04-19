"""M7 — 13 pre-existing test failures cleared (AC-H1).

Covers:
  - AC-H1 — the 13 tests enumerated in brief §"In scope — Hygiene" all pass.
  - Uses pytest subprocess-free introspection: a meta-harness asserts each
    named test's node id returns PASS via pytest's own collection + run API
    using pytest.main.

All tests MUST FAIL today — the peers still fail (that is the whole point
of AC-H1). Once the peer implementations are fixed, these meta-tests go green.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# The 13 tests enumerated in brief §"In scope — Hygiene" (AC-H1).
THIRTEEN_TESTS = (
    "v5/tests/test_conviction_to_priority.py::TestAC2PrioritySort",
    "v5/tests/test_conviction_to_priority.py::TestAC3PrioritySort",
    "v5/tests/test_conviction_to_priority.py::TestAC6FullSuiteGreenMeta",
    "v5/tests/test_conviction_to_priority.py::TestAC3StrategyResolvedPriority",
    "v5/tests/test_m1_fork_verification.py::test_ac01_v5_file_count",
    "v5/tests/test_m1_fork_verification.py::test_ac05_v5_tests_pass",
    "v5/tests/test_m1_fork_verification.py::test_ac11_signal_field_absent",
    "v5/tests/test_m1_fork_verification.py::test_ac16_exit_resolution_absent",
    "v5/tests/test_m3_slots.py::TestAC15bM2Regression::test_full_v5_suite_passes",
    "v5/tests/test_m4_intra_bar_fill.py::TestAC38MarkTriggerBacktestFallback::test_audit_log_entry_emitted_in_backtest",
    "v5/tests/test_paper_determinism.py::test_per_bar_seed_reproducibility",
    "v5/tests/test_paper_determinism.py::test_state_replay_deterministic",
    "v5/tests/test_paper_state.py::TestClosedTradesEmptyAfterDeserialize",
)


class TestEnumeratedThirteenCount:
    """AC-H1 — exactly 13 tests enumerated."""

    def test_count_is_13(self):
        assert len(THIRTEEN_TESTS) == 13
        assert len(set(THIRTEEN_TESTS)) == 13


class TestThirteenPreExistingFailuresCleared:
    """AC-H1 — each of the 13 named tests passes.

    # NOTE: brief ambiguous at AC-H1; strict interpretation — we verify each
    # listed node id *individually* passes, so a regression in any one surfaces.
    """

    @pytest.mark.parametrize("node_id", THIRTEEN_TESTS)
    def test_pre_existing_failure_now_passes(self, node_id):
        """AC-H1 — run pytest in-process on the named node id."""
        # Use subprocess to isolate test state; safer than pytest.main() which
        # mutates global plugin state.
        result = subprocess.run(
            [sys.executable, "-m", "pytest", node_id, "-q",
             "--no-header", "--tb=line", "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=str(_project_root),
            timeout=120,
        )
        assert result.returncode == 0, (
            f"AC-H1: node {node_id!r} still fails (returncode={result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
