"""M7 — s524m v4→v5 reference parity (AC-S10).

Covers:
  - AC-S10 — s524m ported to the v5 Strategy Protocol produces metrics
    within 0.5% of the v4 reference output over the Q-DEC4 validation period.

All tests MUST FAIL today — the v5 port of s524m does not exist and the
reference metrics fixture has not been generated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


PARITY_TOLERANCE = 0.005  # 0.5% per brief AC-S10

REFERENCE_METRICS_PATH = (
    _project_root / "v5" / "tests" / "fixtures"
    / "m7_s524m_parity" / "v4_reference_metrics.json"
)


class TestReferenceFixtureAvailable:
    """AC-S10 — reference fixture must be checked in before Phase 4 completes."""

    def test_reference_metrics_fixture_exists(self):
        assert REFERENCE_METRICS_PATH.exists(), (
            f"AC-S10: expected v4 reference metrics JSON at "
            f"{REFERENCE_METRICS_PATH} (Phase 4 deliverable)"
        )


class TestS524MV5ProtocolCompliance:
    """AC-S10 — the v5 port of s524m satisfies Strategy Protocol."""

    def test_s524m_v5_importable(self):
        from v5.strategies.s524m_v5 import S524M  # noqa: F401

    def test_s524m_v5_satisfies_strategy_protocol(self):
        from v5.strategies.s524m_v5 import S524M
        from v5.strategy_api import Strategy
        assert isinstance(S524M(), Strategy), (
            "AC-S10: S524M v5 port must satisfy runtime-checkable Strategy"
        )


class TestS524MMetricParityWithinHalfPercent:
    """AC-S10 — key metrics within 0.5% of v4 reference."""

    METRICS = ("total_return", "sharpe", "sortino", "calmar", "max_drawdown")

    def _load_reference(self):
        """Reviewer fix: was ERRORing in parametrize; now FAILs cleanly with
        RED signal. When fixture lands (Task 19 s524m migration), flip to PASS."""
        if not REFERENCE_METRICS_PATH.exists():
            pytest.fail(
                f"AC-S10 fixture missing at {REFERENCE_METRICS_PATH}. "
                "Populate via Task 19 s524m v4 reference run."
            )
        return json.loads(REFERENCE_METRICS_PATH.read_text())

    def _run_v5(self):
        from v5.strategies.s524m_v5 import S524M
        from v5.validation import WalkForwardRunner
        runner = WalkForwardRunner(
            strategy_factory=S524M,
            n_folds=1,
            train_bars=0,
            oos_bars=-1,  # full Q-DEC4 window
            validation_window="Q-DEC4",
        )
        return runner.run(tokens=["BTC", "ETH", "SOL"], seed=42).metrics

    @pytest.mark.parametrize("metric", METRICS)
    def test_metric_within_half_percent(self, metric):
        reference = self._load_reference()
        got = self._run_v5()
        ref_v = float(reference[metric])
        got_v = float(got[metric])
        denom = max(abs(ref_v), 1e-9)
        rel_err = abs(got_v - ref_v) / denom
        assert rel_err <= PARITY_TOLERANCE, (
            f"AC-S10: {metric} drift {rel_err:.4%} exceeds 0.5% tolerance; "
            f"v4_ref={ref_v:.6f} v5={got_v:.6f}"
        )
