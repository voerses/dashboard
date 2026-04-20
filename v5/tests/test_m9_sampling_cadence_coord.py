"""M9 AC #24 — Sampling-cadence coordination between RiskComponent + CapitalAllocationPolicy.

Covers AC #24:
- RiskComponent(sampling_cadence="bar_close") + CapitalAllocationPolicy(sampling_cadence="bar_close")
  configured on the same bar receive IDENTICAL clock_now_ns values.

Prevents phase-ordering drift between the risk layer and the allocation
layer (M8 Sz9-class incident at the risk layer).

All tests MUST FAIL today — the engine-side coordination of sampling_cadence
timestamps across risk + allocation does not exist yet.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestSamplingCadenceCoordination:
    """AC #24 — risk and allocation receive identical clock_now_ns on same bar."""

    def test_bar_close_clock_ns_identical_across_risk_and_allocation(self):
        from v5.config import PortfolioConfig
        from v5.risk import RiskComponent, RiskDecision
        from v5.sizing.allocation import CapitalAllocationPolicy
        from v5.simulator import simulate_portfolio
        from v5.strategies.s524m_v5 import S524M
        from v5.universe_context import UniverseContext

        risk_captures: list[int] = []
        alloc_captures: list[int] = []

        class _CapturingRisk(RiskComponent):
            sampling_cadence = "bar_close"
            def check(self, candidate, state, clock_now_ns):
                risk_captures.append(clock_now_ns)
                return RiskDecision.ACCEPT

        class _CapturingAlloc(CapitalAllocationPolicy):
            sampling_cadence = "bar_close"
            def available_capital(self, strategy_id, state, clock_now_ns):
                alloc_captures.append(clock_now_ns)
                return 1_000.0

        cfg = PortfolioConfig(
            strategies=[],
            capital=10_000.0,
            risk_components=[_CapturingRisk()],
            capital_allocation_policy=_CapturingAlloc(),
        )
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=30, seed=0, equity=10_000.0
        )
        simulate_portfolio(strategies={"s524m": S524M()}, config=cfg, ctx=ctx)

        # Both layers invoked at least once
        assert risk_captures, "RiskComponent.check() never called"
        assert alloc_captures, "CapitalAllocationPolicy.available_capital() never called"

        # For every bar where both layers were sampled, clock_now_ns must match.
        # Simplest invariant: intersection of captured clock values is non-empty and
        # every risk capture appears in alloc captures (or vice versa) per bar.
        assert set(risk_captures) == set(alloc_captures), (
            f"clock_now_ns mismatch between risk {sorted(risk_captures)[:3]}... "
            f"and allocation {sorted(alloc_captures)[:3]}..."
        )
