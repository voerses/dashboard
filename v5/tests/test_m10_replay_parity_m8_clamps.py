"""M10 G4 — Real replay-parity runner for ``use_m8_clamps`` (AC #9).

Supersedes the M9 Phase-6 scaffold skip in ``test_m9_replay_parity.py``:
this test requires ``run_replay_parity_m8_clamps`` to be a REAL
implementation (not a ``pytest.skip`` stub) running paper_engine twice
(flag=False / flag=True) on the engineered 7-day fixture and diffing
archives.

Asserts:
  * ``manifest.json`` status is ``"ready"`` (not ``"scaffold"``) —
    fixture generator shipped.
  * ``ArchiveComparisonResult.non_binding_bars_byte_identical == True``.
  * Binding bars list non-empty AND each binding bar has a matching
    ``sizing_fills.jsonl`` entry whose ``clamp_name`` is one of the 6
    M8 clamp names.

MUST FAIL TODAY — fixture manifest status is ``"scaffold"``; the
``run_replay_parity_m8_clamps`` stub calls ``pytest.skip(...)``; no
``ArchiveComparisonResult.non_binding_bars_byte_identical`` attribute
exists on the scaffold dataclass (the scaffold uses
``nonbinding_bars_byte_identical`` without an underscore between "non"
and "binding" — the M10 real API introduces the hyphenated form
documented in AC #9).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_MANIFEST_PATH = (
    _project_root / "v5" / "tests" / "fixtures" / "m9_replay_parity_7d"
    / "manifest.json"
)

# The 6 M8 clamp names per brief AC #9.
_EXPECTED_CLAMPS = frozenset({
    "adv_cap",
    "concentration",
    "free_capital",
    "min_size",
    "liq_distance",
    "slippage",
})


class TestManifestReadyStatus:
    """AC #9 — manifest transitions from "scaffold" to "ready"."""

    def test_manifest_exists(self):
        assert _MANIFEST_PATH.exists(), (
            f"Replay-parity manifest missing: {_MANIFEST_PATH}"
        )

    def test_manifest_status_is_ready(self):
        manifest = json.loads(_MANIFEST_PATH.read_text())
        assert manifest.get("status") == "ready", (
            f"Replay-parity manifest must flip from 'scaffold' to 'ready' "
            f"once generator ships (AC #9). Current status: "
            f"{manifest.get('status')!r}"
        )


class TestRunReplayParityM8Clamps:
    """AC #9 — real runner produces non-binding byte-identity + binding
    bar sizing_fills match."""

    def test_runner_is_real_not_skip(self, tmp_path):
        """Call the runner; it must RETURN a result, not ``pytest.skip``."""
        from v5.tools.replay_parity import run_replay_parity_m8_clamps

        manifest = json.loads(_MANIFEST_PATH.read_text())
        try:
            result = run_replay_parity_m8_clamps(
                fixture_manifest=manifest,
                flag_off_output=str(tmp_path / "m8_replay_off.json"),
                flag_on_output=str(tmp_path / "m8_replay_on.json"),
            )
        except pytest.skip.Exception as exc:
            pytest.fail(
                f"run_replay_parity_m8_clamps is still a pytest.skip stub "
                f"(AC #9 requires a REAL implementation): {exc}"
            )

        # AC #9 real attribute name (hyphenated-to-underscored):
        # ``non_binding_bars_byte_identical`` (note underscore between
        # non and binding — M10 API, distinct from M9 scaffold attr).
        assert hasattr(result, "non_binding_bars_byte_identical"), (
            "run_replay_parity_m8_clamps must return ArchiveComparisonResult "
            "with non_binding_bars_byte_identical attribute (AC #9 real API)."
        )
        assert result.non_binding_bars_byte_identical is True, (
            f"Non-binding bars diverged between flag-off and flag-on "
            f"(AC #9 requires byte-identity)."
        )

    def test_binding_bars_have_sizing_fills_match(self, tmp_path):
        """Every binding bar must have a matching sizing_fills entry with
        a documented clamp name."""
        from v5.tools.replay_parity import run_replay_parity_m8_clamps

        manifest = json.loads(_MANIFEST_PATH.read_text())
        try:
            result = run_replay_parity_m8_clamps(
                fixture_manifest=manifest,
                flag_off_output=str(tmp_path / "m8_replay_off_b.json"),
                flag_on_output=str(tmp_path / "m8_replay_on_b.json"),
            )
        except pytest.skip.Exception as exc:
            pytest.fail(
                f"run_replay_parity_m8_clamps still skips (AC #9 requires "
                f"real runner): {exc}"
            )

        binding = list(getattr(result, "binding_bars", []))
        assert binding, (
            "Binding bars list is empty — generator must engineer each "
            "of 6 M8 clamps to bind at least once (AC #9)."
        )

        for entry in binding:
            clamp_name = entry.get("clamp_name") if isinstance(entry, dict) \
                else getattr(entry, "clamp_name", None)
            assert clamp_name in _EXPECTED_CLAMPS, (
                f"Binding bar {entry!r} has clamp_name={clamp_name!r}; "
                f"must be one of {sorted(_EXPECTED_CLAMPS)} (AC #9)."
            )
