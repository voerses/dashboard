"""M9 C-10 — TickCadencePolicy production scaffolding.

Covers C-10 item 3:
- v5/sizing/tick_cadence.py::TickCadencePolicy exists with sampling_cadence="tick"
- parity_fixture.py synthetic "5% available_margin nudge" replaced with
  real tick-level equity sampling.

All tests MUST FAIL today — v5/sizing/tick_cadence.py not yet shipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestTickCadencePolicyExists:
    """C-10 — TickCadencePolicy class ships at v5/sizing/tick_cadence.py."""

    def test_tick_cadence_policy_importable_and_tick_cadence(self):
        from v5.sizing.tick_cadence import TickCadencePolicy

        policy = TickCadencePolicy()
        assert policy.sampling_cadence == "tick", (
            f"TickCadencePolicy.sampling_cadence must == 'tick'; got "
            f"{policy.sampling_cadence!r}"
        )


class TestParityFixtureTickSampling:
    """C-10 — parity_fixture replaces synthetic nudge with real tick sampling."""

    def test_parity_fixture_uses_real_tick_equity_not_synthetic_nudge(self):
        """parity_fixture.py must call TickCadencePolicy sampling, not inject 5%."""
        from v5.sizing.parity_fixture import build_parity_fixture
        from v5.sizing.tick_cadence import TickCadencePolicy

        fixture = build_parity_fixture()
        # The tick-equity sampling source must be the TickCadencePolicy,
        # not a synthetic nudge. The fixture exposes its sampling policy
        # to enable this audit.
        assert hasattr(fixture, "tick_policy"), (
            "parity_fixture must expose a tick_policy attribute wired to "
            "TickCadencePolicy (replaces synthetic '5% available_margin' nudge)"
        )
        assert isinstance(fixture.tick_policy, TickCadencePolicy), (
            f"parity_fixture.tick_policy must be TickCadencePolicy; got "
            f"{type(fixture.tick_policy).__name__}"
        )

        parity_path = _project_root / "v5" / "sizing" / "parity_fixture.py"
        source = parity_path.read_text()
        # Negative: M8 synthetic-nudge scaffolding must be gone
        assert "synthetic" not in source.lower() or "# replaced by TickCadencePolicy" in source, (
            "parity_fixture must either remove all 'synthetic' nudge references "
            "or annotate them as replaced by TickCadencePolicy"
        )
