"""M9 AC #10 — Replay-parity gate for flag flips.

Tests the byte-identity archive comparison for `use_m8_clamps` and
`use_multi_leg_orders` flag flips. Fixture source: 7-day historical
window from parquet cache (NOT live WS), engineered to force each of
the 6 M8 clamps to bind at least once.

Tests SKIP if fixture manifest not yet built (T34 gates fixture
construction; these tests run after T34 in Wave D).

All tests MUST FAIL today — fixture, multi-leg replay helper, and
archive-diff utilities do not exist yet.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_DIR = _project_root / "v5" / "tests" / "fixtures" / "m9_replay_parity_7d"
MANIFEST = FIXTURE_DIR / "manifest.json"


def _fixture_exists():
    return MANIFEST.exists()


@pytest.fixture
def fixture_manifest():
    """Fixture that skips tests when the 7-day parquet window manifest
    doesn't exist yet (T34 builds it). After T34, `manifest.json` lives
    at `v5/tests/fixtures/m9_replay_parity_7d/manifest.json` with
    token-list + window bounds + clamp-binding expectations."""
    if not _fixture_exists():
        pytest.skip(
            f"Replay-parity fixture not built yet (expected at {MANIFEST}). "
            "Build via `python -m v5.tests.fixtures.generate_m9_replay_parity_7d`."
        )
    with open(MANIFEST) as f:
        return json.load(f)


class TestUseM8ClampsReplayParity:
    """AC #10 — `use_m8_clamps` flag-flip gate via replay-parity."""

    def test_use_m8_clamps_nonbinding_bars_byte_identical(self, fixture_manifest):
        """Running paper_engine with flag=False vs flag=True on the
        engineered 7-day fixture → non-binding bars must be byte-identical
        between the two archives; binding bars have a matching
        sizing_fills.jsonl entry with the expected clamp name."""
        from v5.tools.replay_parity import (
            run_replay_parity_m8_clamps,
            ArchiveComparisonResult,
        )

        result: ArchiveComparisonResult = run_replay_parity_m8_clamps(
            fixture_manifest=fixture_manifest,
            flag_off_output="/tmp/m9_replay_off.json",
            flag_on_output="/tmp/m9_replay_on.json",
        )

        assert result.nonbinding_bars_byte_identical, (
            f"Non-binding bars diverge between flag-off and flag-on: "
            f"{result.divergence_summary}"
        )
        assert result.all_binding_bars_have_sizing_fills_entry, (
            f"Binding bars missing from sizing_fills.jsonl: "
            f"{result.missing_fills}"
        )
        assert result.each_clamp_bound_at_least_once, (
            f"Not all 6 M8 clamps bound: "
            f"bound={result.clamps_bound}, expected=all 6"
        )


class TestUseMultiLegOrdersReplayParity:
    """AC #10 — `use_multi_leg_orders` flag-flip gate via replay-parity."""

    def test_use_multi_leg_orders_nonbinding_bars_byte_identical(
        self, fixture_manifest
    ):
        """Running s513_v5 OTOCO bracket on the 7-day fixture with
        flag=False vs flag=True → non-contingent single-leg orders
        byte-identical; multi-leg OTOCO branches exercised when
        flag=True."""
        from v5.tools.replay_parity import (
            run_replay_parity_multi_leg,
            ArchiveComparisonResult,
        )

        result: ArchiveComparisonResult = run_replay_parity_multi_leg(
            fixture_manifest=fixture_manifest,
            strategy_id="s513_v5",
            flag_off_output="/tmp/m9_multi_leg_off.json",
            flag_on_output="/tmp/m9_multi_leg_on.json",
        )

        assert result.nonbinding_bars_byte_identical, (
            f"Single-leg orders diverge between flag-off and flag-on: "
            f"{result.divergence_summary}"
        )
        assert result.contingent_orders_only_in_flag_on, (
            f"OTOCO contingent orders present in flag-off archive "
            f"(should only be in flag-on): {result.unexpected_contingent}"
        )
