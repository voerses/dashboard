"""M8 — Clamp hook location: inside Order.release_atomic ONLY.

The 6 clamps fire inside Order.release_atomic(available_capital_usd, ...)
at v5/orders.py. NO clamp logic runs in v5.simulator or v5.paper_engine.

Canonical extended signature:
  def release_atomic(
      self, *,
      available_capital_usd: float,
      market_state: MarketState,
      policy: CapitalAllocationPolicy = SharedPoolPolicy(),
      config: ClampsConfig | None = None,
  ) -> Order: ...

All tests MUST FAIL today — clamp hook not wired.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=150_000.0)


class _SyntheticMarketState:
    def __init__(self, **state):
        self._state = dict(state)

    def adv(self, token):
        return self._state["adv"]

    def rolling_adv(self, token, window_hours=24):
        return self._state["adv"]

    def mark_price(self, token):
        return self._state["mark_price"]

    def free_margin(self, strategy_id, policy):
        from v5.sizing.allocation import AllocationState
        state = AllocationState(
            available_margin=self._state["available_margin"],
            per_strategy_equity={strategy_id: self._state["equity"]},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position, leverage):
        return self._state.get("liquidation_distance_bps", 10_000.0)

    def equity(self, strategy_id):
        return self._state["equity"]


def _market_state(**kw):
    base = dict(
        adv=1_000_000_000.0,
        mark_price=50_000.0,
        available_margin=150_000.0,
        equity=150_000.0,
        liquidation_distance_bps=10_000.0,
    )
    base.update(kw)
    return _SyntheticMarketState(**base)


def _policy():
    from v5.sizing.allocation import SharedPoolPolicy
    return SharedPoolPolicy()


def _config(**kw):
    from v5.sizing.clamps import ClampsConfig
    base = dict(
        adv_cap_pct=1.0,
        concentration_limit=1.0,
        min_position_usd=10.0,
        min_liquidation_distance_bps=100.0,
    )
    base.update(kw)
    return ClampsConfig(**base)


class TestReleaseAtomicCallsClamps:
    """Clamps run at release_atomic, not during arm."""

    def test_release_atomic_accepts_clamp_args(self, ctx, tmp_path):
        from v5.orders import TriggerType
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        log_path = tmp_path / "sizing_fills.jsonl"
        # release_atomic signature must accept market_state + config + policy
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=150_000.0,
            market_state=_market_state(),
            policy=_policy(),
            config=_config(log_path=log_path),
        )
        assert order is not None
        # Binding-log entry was emitted → clamps ran inside release_atomic
        assert log_path.exists()

    def test_release_atomic_emits_binding_log_entry(self, ctx, tmp_path):
        from v5.orders import TriggerType
        log_path = tmp_path / "sizing_fills.jsonl"
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=150_000.0,
            market_state=_market_state(),
            policy=_policy(),
            config=_config(log_path=log_path),
        )
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "binding_constraint" in entry


class TestClampsNotInSimulatorOrPaper:
    """Clamp logic lives ONLY in release_atomic — not simulator, not paper."""

    def test_simulator_does_not_import_clamps(self):
        # Positive invariant: clamps module MUST exist + expose run_clamp_pipeline
        from v5.sizing.clamps import run_clamp_pipeline  # noqa: F401
        sim_path = Path(__file__).resolve().parent.parent / "simulator.py"
        text = sim_path.read_text()
        # Simulator must not import run_clamp_pipeline directly (clamps run via Order)
        assert "from v5.sizing.clamps import run_clamp_pipeline" not in text
        assert "import run_clamp_pipeline" not in text

    def test_paper_engine_does_not_import_clamps(self):
        # Positive invariant: clamps module MUST exist + expose run_clamp_pipeline
        from v5.sizing.clamps import run_clamp_pipeline  # noqa: F401
        paper_path = Path(__file__).resolve().parent.parent / "paper_engine.py"
        assert paper_path.exists(), "v5/paper_engine.py must exist for this AC"
        text = paper_path.read_text()
        assert "from v5.sizing.clamps import run_clamp_pipeline" not in text
        assert "import run_clamp_pipeline" not in text


class TestScalarOnlyMarketState:
    """AC-Sz2 — market_state scalars only; engine never reads bar-arrays.

    Injects an ndarray into market_state to verify release_atomic either
    raises or coerces.
    """

    def test_array_valued_market_state_rejected(self, ctx):
        """release_atomic must reject an ndarray-valued market_state scalar."""
        from v5.orders import TriggerType
        import numpy as np

        class _ArrayState:
            def adv(self, token): return np.array([1e9, 2e9])
            def rolling_adv(self, token, window_hours=24): return np.array([1e9, 2e9])
            def mark_price(self, token): return 50_000.0
            def free_margin(self, strategy_id, policy): return 150_000.0
            def liquidation_distance(self, position, leverage): return 10_000.0
            def equity(self, strategy_id): return 150_000.0

        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        order = order.trigger_immediately()
        # Release must either reject or raise when adv() returns ndarray.
        try:
            result = order.release_atomic(
                available_capital_usd=150_000.0,
                market_state=_ArrayState(),
                policy=_policy(),
                config=_config(),
            )
            # If it doesn't raise, must be REJECTED with a clamp_error_* reason
            from v5.orders import OrderStatus
            assert result.state == OrderStatus.REJECTED
        except (TypeError, ValueError):
            pass  # explicit raise is also an acceptable outcome
