"""M7 — v4→v5 paper runner swap with lock-file migration (AC-P2).

Covers:
  - AC-P2 — v5.run_paper_multi is the production runner.
  - AC-P2 — lock file migrates from state/v4_paper_multi/paper.pid to
    state/v5_paper_multi/paper.pid using copy-not-move semantics (v4 PID
    file must REMAIN during the transition to preserve live paper fleet).
  - AC-P2 — tools/start_all_services.sh references the new v5 path.

All tests MUST FAIL today — the migration shim does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestMigrationShimImport:
    """AC-P2 — shim callable exists."""

    def test_migrate_lock_file_importable(self):
        from v5.run_paper_multi import migrate_lock_file  # noqa: F401


class TestCopyNotMove:
    """AC-P2 — migration uses COPY semantics, not move."""

    def test_v4_pid_file_still_present_after_migration(self, tmp_path):
        """AC-P2 — after migrate_lock_file(), BOTH paths exist.

        Brief §"AC-P2" — preserves PID continuity for the running paper fleet.
        A move would orphan the v4 file mid-fleet and break start_all_services
        recovery until the next natural restart.
        """
        from v5.run_paper_multi import migrate_lock_file

        v4_state = tmp_path / "state" / "v4_paper_multi"
        v5_state = tmp_path / "state" / "v5_paper_multi"
        v4_state.mkdir(parents=True)
        v4_pid = v4_state / "paper.pid"
        v4_pid.write_text("12345")

        migrate_lock_file(v4_dir=v4_state, v5_dir=v5_state)

        assert v4_pid.exists(), "AC-P2: v4 PID file must REMAIN (copy-not-move)"
        v5_pid = v5_state / "paper.pid"
        assert v5_pid.exists(), "AC-P2: v5 PID file must be created"
        assert v5_pid.read_text() == "12345", (
            "AC-P2: v5 PID content must match v4 content"
        )

    def test_migration_idempotent(self, tmp_path):
        """AC-P2 — running the shim twice is safe (no error, same content)."""
        from v5.run_paper_multi import migrate_lock_file

        v4_state = tmp_path / "state" / "v4_paper_multi"
        v5_state = tmp_path / "state" / "v5_paper_multi"
        v4_state.mkdir(parents=True)
        (v4_state / "paper.pid").write_text("99999")

        migrate_lock_file(v4_dir=v4_state, v5_dir=v5_state)
        # Second call must not raise
        migrate_lock_file(v4_dir=v4_state, v5_dir=v5_state)
        assert (v5_state / "paper.pid").read_text() == "99999"

    def test_missing_v4_file_is_noop(self, tmp_path):
        """AC-P2 — absence of v4 PID is a clean no-op (no stale v5 created)."""
        from v5.run_paper_multi import migrate_lock_file

        v4_state = tmp_path / "state" / "v4_paper_multi"
        v5_state = tmp_path / "state" / "v5_paper_multi"
        # neither exists
        migrate_lock_file(v4_dir=v4_state, v5_dir=v5_state)
        assert not (v5_state / "paper.pid").exists(), (
            "AC-P2: missing v4 source must NOT synthesize a v5 PID file"
        )


class TestRunPaperMultiIsTheProductionEntry:
    """AC-P2 — v5.run_paper_multi is the production runner."""

    def test_main_entry_importable(self):
        import v5.run_paper_multi as mod
        assert callable(getattr(mod, "main", None)), (
            "AC-P2: v5/run_paper_multi.py must define a main() entry"
        )

    def test_default_lock_path_is_v5(self):
        """AC-P2 — default PID path references v5_paper_multi."""
        from v5.run_paper_multi import DEFAULT_PID_PATH
        assert "v5_paper_multi" in str(DEFAULT_PID_PATH), (
            f"AC-P2: default PID path must live under v5_paper_multi; "
            f"got {DEFAULT_PID_PATH!r}"
        )


@pytest.mark.xfail(
    reason="Per user directive 2026-04-19: the actual v4→v5 runner swap in "
    "tools/start_all_services.sh is deferred to end-of-all-milestones — "
    "NOT M7. M7 Task 15 ships migrate_lock_file() + v5 lock-path constant; "
    "the start script stays on v4 until all strategies are ported and the "
    "v5 runner is fully vetted. These tests will flip GREEN at that cutover.",
)
class TestStartAllServicesReferencesV5:
    """AC-P2 — tools/start_all_services.sh uses v5 path."""

    def test_start_script_references_v5_paper_multi(self):
        script = _project_root / "tools" / "start_all_services.sh"
        if not script.exists():
            pytest.fail(
                f"AC-P2: expected tools/start_all_services.sh at {script}"
            )
        text = script.read_text()
        assert "v5_paper_multi" in text or "v5.run_paper_multi" in text, (
            "AC-P2: tools/start_all_services.sh must reference v5 paper_multi"
        )

    def test_start_script_does_not_reference_v4_paper_multi_pid(self):
        """AC-P2 — lock-file PATH migrated to v5; v4 path must no longer be the primary."""
        script = _project_root / "tools" / "start_all_services.sh"
        if not script.exists():
            pytest.fail(f"expected {script}")
        text = script.read_text()
        # An incidental mention in a migration-shim comment is fine; the live
        # PID path read must not be the v4 one.
        live_lines = [
            ln for ln in text.splitlines()
            if "v4_paper_multi/paper.pid" in ln and not ln.lstrip().startswith("#")
        ]
        assert not live_lines, (
            "AC-P2: non-comment reference to v4_paper_multi/paper.pid remains"
        )
