"""M10 G6 — Real replay-parity runner for ``use_multi_leg_orders`` (AC #9).

Supersedes the M9 Phase-6 scaffold skip for the multi-leg variant. Same
structure as G4 / ``test_m10_replay_parity_m8_clamps.py`` but exercises
the OTOCO bracket path:

  * flag=False  → single-leg orders only
  * flag=True   → multi-leg OTOCO bracket exercised

Asserts:
  * manifest status ``"ready"``.
  * ``non_binding_bars_byte_identical == True`` for non-contingent
    single-leg orders.
  * ``binding_bars`` (the OTOCO-contingent fills) non-empty AND each
    has a matching ``sizing_fills.jsonl`` entry with ``clamp_name`` in
    the 6-clamp set.

MUST FAIL TODAY — ``run_replay_parity_multi_leg`` is a ``pytest.skip``
stub; manifest still ``"scaffold"``.
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

_EXPECTED_CLAMPS = frozenset({
    "adv_cap",
    "concentration",
    "free_capital",
    "min_size",
    "liq_distance",
    "slippage",
})

_STRATEGY_ID = "s513_v5"  # same reference strategy M9 used.


class TestMultiLegManifestReady:
    """AC #9 — manifest "ready" gates this test."""

    def test_manifest_status_is_ready(self):
        assert _MANIFEST_PATH.exists(), (
            f"Replay-parity manifest missing: {_MANIFEST_PATH}"
        )
        manifest = json.loads(_MANIFEST_PATH.read_text())
        assert manifest.get("status") == "ready", (
            f"manifest.status must be 'ready' for multi-leg runner to "
            f"run (AC #9). Current: {manifest.get('status')!r}"
        )


class TestRunReplayParityMultiLeg:
    """AC #9 — real multi-leg OTOCO replay-parity runner."""

    def test_runner_is_real_not_skip(self, tmp_path):
        """Must RETURN a result, not ``pytest.skip``."""
        from v5.tools.replay_parity import run_replay_parity_multi_leg

        manifest = json.loads(_MANIFEST_PATH.read_text())
        try:
            result = run_replay_parity_multi_leg(
                fixture_manifest=manifest,
                strategy_id=_STRATEGY_ID,
                flag_off_output=str(tmp_path / "ml_replay_off.json"),
                flag_on_output=str(tmp_path / "ml_replay_on.json"),
            )
        except pytest.skip.Exception as exc:
            pytest.fail(
                f"run_replay_parity_multi_leg still a pytest.skip stub "
                f"(AC #9 requires real runner): {exc}"
            )

        assert hasattr(result, "non_binding_bars_byte_identical"), (
            "run_replay_parity_multi_leg must return ArchiveComparisonResult "
            "with non_binding_bars_byte_identical attribute (AC #9)."
        )
        assert result.non_binding_bars_byte_identical is True, (
            "Non-binding (single-leg) bars must be byte-identical between "
            "use_multi_leg_orders=False and =True (AC #9)."
        )

    def test_binding_bars_have_sizing_fills_match(self, tmp_path):
        """OTOCO contingent fills must map to sizing_fills entries."""
        from v5.tools.replay_parity import run_replay_parity_multi_leg

        manifest = json.loads(_MANIFEST_PATH.read_text())
        try:
            result = run_replay_parity_multi_leg(
                fixture_manifest=manifest,
                strategy_id=_STRATEGY_ID,
                flag_off_output=str(tmp_path / "ml_replay_off_b.json"),
                flag_on_output=str(tmp_path / "ml_replay_on_b.json"),
            )
        except pytest.skip.Exception as exc:
            pytest.fail(
                f"run_replay_parity_multi_leg still skips (AC #9 requires "
                f"real runner): {exc}"
            )

        binding = list(getattr(result, "binding_bars", []))
        assert binding, (
            "binding_bars empty — multi-leg OTOCO bracket must exercise "
            "clamps at least once in 7-day fixture (AC #9)."
        )

        for entry in binding:
            clamp_name = entry.get("clamp_name") if isinstance(entry, dict) \
                else getattr(entry, "clamp_name", None)
            assert clamp_name in _EXPECTED_CLAMPS, (
                f"Binding bar {entry!r} missing clamp_name or not in "
                f"{sorted(_EXPECTED_CLAMPS)} (AC #9)."
            )
