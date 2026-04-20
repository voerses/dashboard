"""M10 G8 — v4/v5 parallel-ops dashboard wiring smoke (AC #12).

Pytest-fast orchestrator smoke test — NOT a 30-60 min wall-clock soak.
Starts both the v4 and v5 runners in a single-tick test mode (controlled
via ``V5_PAPER_TEST_MODE=1`` env flag so the runners exit after the first
bar tick), then asserts that the dashboard wiring is correct:

  * ``/srv/data/state.json`` exists and is populated by v4 runner
  * ``/srv/data/state_v5.json`` exists and is populated by v5 runner
  * Dashboard endpoints ``/`` and ``/v5`` both return HTTP 200

The 30-60 min wall-clock visual smoke (AC #12's operator-facing side) is
an operational procedure documented in MIGRATION.md step 6.5 — this
pytest verifies wiring only.

MUST FAIL TODAY — ``V5_PAPER_TEST_MODE`` is not wired in either runner;
``/srv/data/state_v5.json`` is not produced by the current start script;
dashboard does not serve ``/v5`` route yet (M9 C-8 landed but runner
still under v4).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_START_V4 = _project_root / "tools" / "start_all_services.sh"
_START_V5 = _project_root / "tools" / "start_v5_paper.sh"
_STOP_V4 = _project_root / "tools" / "stop_all_services.sh"
_STOP_V5 = _project_root / "tools" / "stop_v5_paper.sh"

_V4_STATE_JSON = Path("/srv/data/state.json")
_V5_STATE_JSON = Path("/srv/data/state_v5.json")

_DASHBOARD_BASE = os.environ.get(
    "V5_DASHBOARD_BASE", "http://127.0.0.1:8080"
)


def _wait_for_file(path: Path, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.2)
    return False


def _http_get(url: str, timeout_s: float = 5.0) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


@pytest.fixture
def both_runners_running(monkeypatch):
    """Start both runners in test-mode; tear down at end."""
    monkeypatch.setenv("V5_PAPER_TEST_MODE", "1")
    monkeypatch.setenv("V4_PAPER_TEST_MODE", "1")

    v4_proc = subprocess.Popen(
        ["bash", str(_START_V4)],
        cwd=str(_project_root),
        env={**os.environ, "V4_PAPER_TEST_MODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    v5_proc = subprocess.Popen(
        ["bash", str(_START_V5)],
        cwd=str(_project_root),
        env={**os.environ, "V5_PAPER_TEST_MODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        yield (v4_proc, v5_proc)
    finally:
        subprocess.run(
            ["bash", str(_STOP_V5)], cwd=str(_project_root), timeout=30
        )
        subprocess.run(
            ["bash", str(_STOP_V4)], cwd=str(_project_root), timeout=30
        )
        for proc in (v4_proc, v5_proc):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()


class TestParallelOpsSmoke:
    """AC #12 — dashboard wiring smoke."""

    def test_start_scripts_exist(self):
        assert _START_V4.exists(), f"Missing: {_START_V4}"
        assert _START_V5.exists(), f"Missing: {_START_V5}"

    def test_v4_state_json_populated(self, both_runners_running):
        assert _wait_for_file(_V4_STATE_JSON, timeout_s=30.0), (
            f"{_V4_STATE_JSON} must be created + populated by v4 runner "
            f"in test-mode (AC #12)."
        )
        payload = json.loads(_V4_STATE_JSON.read_text())
        assert payload, f"{_V4_STATE_JSON} content is empty."

    def test_v5_state_json_populated(self, both_runners_running):
        assert _wait_for_file(_V5_STATE_JSON, timeout_s=30.0), (
            f"{_V5_STATE_JSON} must be created + populated by v5 runner "
            f"in test-mode (AC #12)."
        )
        payload = json.loads(_V5_STATE_JSON.read_text())
        assert payload, f"{_V5_STATE_JSON} content is empty."

    def test_dashboard_root_endpoint_200(self, both_runners_running):
        status = _http_get(f"{_DASHBOARD_BASE}/")
        assert status == 200, (
            f"Dashboard root `/` returned HTTP {status}; expected 200 "
            f"(AC #12 — v4 legacy view)."
        )

    def test_dashboard_v5_endpoint_200(self, both_runners_running):
        status = _http_get(f"{_DASHBOARD_BASE}/v5")
        assert status == 200, (
            f"Dashboard `/v5` returned HTTP {status}; expected 200 "
            f"(AC #12 — v5 parallel view per M9 C-8)."
        )
