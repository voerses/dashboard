"""M9 C-8 — v5 paper runner deployment + feature flag + kill-switch drill.

Covers AC #11 kill-switch sub-ACs:
- V5_PAPER_ENABLED=0 → start_v5_paper.sh no-ops (exit 0, no PID file)
- v4 paper runner PID stable across v5 start/stop
- Feature-flag removal drill: deleting v5 tools/symlink/config leaves v4 untouched

These tests use mocked PIDs/state only; they MUST NOT touch the live v4
paper runner PID at /tmp/paper_runner.pid.

All tests MUST FAIL today — tools/start_v5_paper.sh, stop_v5_paper.sh,
configs/runner_pool_config_v5.json do not exist.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestFeatureFlagGate:
    """AC #11 — V5_PAPER_ENABLED=0 disables start_v5_paper.sh cleanly."""

    def test_start_v5_paper_noops_when_flag_disabled(self):
        """V5_PAPER_ENABLED=0 (or unset) → exit 0, no PID file created."""
        script = _project_root / "tools" / "start_v5_paper.sh"
        if not script.exists():
            pytest.fail(f"tools/start_v5_paper.sh not found (M9 must create it)")

        with tempfile.TemporaryDirectory() as tmp:
            fake_pid_path = Path(tmp) / "paper_runner_v5.pid"
            env = {
                **os.environ,
                "V5_PAPER_ENABLED": "0",
                "V5_PAPER_PID_PATH": str(fake_pid_path),
            }
            result = subprocess.run(
                ["bash", str(script)],
                capture_output=True,
                env=env,
                timeout=30,
                text=True,
            )
            assert result.returncode == 0, (
                f"start_v5_paper.sh exited {result.returncode} "
                f"(expected clean no-op): stderr={result.stderr}"
            )
            assert not fake_pid_path.exists(), (
                "PID file created despite V5_PAPER_ENABLED=0"
            )
            assert "disabled" in (result.stdout + result.stderr).lower()


class TestV4PidStableAcrossV5Ops:
    """AC #11 — v4 paper runner PID unchanged across v5 start/stop operations."""

    def test_v4_paper_pid_stable_across_v5_start_stop(self, tmp_path):
        """Simulate v4 PID captured before/after v5 start+stop; assert unchanged.

        Uses mocked PID paths so we never touch the real /tmp/paper_runner.pid.
        """
        from v5.run_paper_multi import start_runner, stop_runner

        # Simulate v4 PID file that predates v5 activity
        v4_pid_path = tmp_path / "paper_runner.pid"
        v4_pid_path.write_text("12345")
        v4_pid_before = v4_pid_path.read_text()

        v5_pid_path = tmp_path / "paper_runner_v5.pid"

        with patch.dict(os.environ, {"V5_PAPER_ENABLED": "1",
                                     "V5_PAPER_PID_PATH": str(v5_pid_path)}):
            # Use mocked subprocess so we don't actually start a runner
            with patch("v5.run_paper_multi._spawn_runner") as mock_spawn:
                mock_spawn.return_value = 99999
                start_runner(pid_path=v5_pid_path)
                stop_runner(pid_path=v5_pid_path)

        v4_pid_after = v4_pid_path.read_text()
        assert v4_pid_before == v4_pid_after, (
            "v4 paper runner PID file modified during v5 start/stop"
        )


class TestFeatureFlagRemovalDrill:
    """AC #11 kill-switch — deleting v5 entirely leaves v4 path untouched."""

    def test_v5_removal_leaves_v4_path_unchanged(self, tmp_path):
        """Simulate deleting v5 tools + symlink + config; v4 files have empty git diff."""
        from v5.tools.removal_drill import simulate_v5_removal

        # Stage fake v4 files alongside v5 files
        v4_files = {
            "tools/start_all_services.sh": b"#!/bin/bash\necho v4\n",
            "configs/runner_pool_config.json": b'{"v": 4}',
            "dashboard/index.html": b"<html>v4</html>",
        }
        v5_files = {
            "tools/start_v5_paper.sh": b"#!/bin/bash\necho v5\n",
            "tools/stop_v5_paper.sh": b"#!/bin/bash\n",
            "configs/runner_pool_config_v5.json": b'{"v": 5}',
        }
        root = tmp_path
        for rel, content in {**v4_files, **v5_files}.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

        diff = simulate_v5_removal(root=root)

        # diff returns a dict mapping file paths → "added/modified/deleted/unchanged"
        for v4_rel in v4_files:
            assert diff.get(v4_rel, "unchanged") == "unchanged", (
                f"v4 file {v4_rel} modified during v5 removal drill"
            )
        for v5_rel in v5_files:
            assert diff.get(v5_rel) == "deleted", (
                f"v5 file {v5_rel} not deleted by removal drill"
            )
