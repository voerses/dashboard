"""M9 C-7 — AC-S10 simulator <-> Strategy Protocol bridge.

**M11 Commit 8 migration note (2026-04-22):** the bridge infrastructure
this file tested (`_engine_precompute_fallback`, `VectorizedStrategy`
upfront dispatch, ``simulate_portfolio(strategies=, ctx=)`` signature,
``paper_replay_to_token_bar_arrays``) is DELETED per ADR-0001 +
ADR-0002. Event-driven dispatch is now the single simulation loop; the
top-level orchestrator is :func:`v5.run_backtest.run_backtest`. Tests in
this module exercise the deleted bridge API surface directly and are
retired — the architectural invariant is now enforced structurally by
:mod:`v5.tests.test_m11_architecture_invariants` + behaviorally by
:mod:`v5.tests.test_m11_run_backtest_orchestrator`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_M11_BRIDGE_DELETED = pytest.mark.skip(reason=(
    "M11 Commit 8 deleted the bridge infrastructure this class tested "
    "(_engine_precompute_fallback / paper_replay_to_token_bar_arrays / "
    "simulate_portfolio(strategies=, ctx=)). Coverage migrated to "
    "v5.tests.test_m11_architecture_invariants (structural) + "
    "v5.tests.test_m11_run_backtest_orchestrator (behavioral) + "
    "v5.tests.test_m11_signal_native_processing (parity)."
))

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestVectorizedStrategyProtocol:
    """AC #7 — VectorizedStrategy Protocol is a sub-Protocol of Strategy."""

    def test_vectorized_strategy_is_subprotocol_of_strategy(self):
        from typing import get_type_hints

        from v5.strategy_api import Strategy
        from v5.strategy_api import VectorizedStrategy

        # VectorizedStrategy must be a Protocol that structurally includes Strategy.
        # Any VectorizedStrategy instance must also satisfy isinstance(x, Strategy).
        assert issubclass(VectorizedStrategy, Strategy) or (
            Strategy in VectorizedStrategy.__mro__
        ), "VectorizedStrategy must be a sub-Protocol of Strategy"

    @pytest.mark.skip(reason=(
        "M11 RF-2: to_token_bar_arrays methods deleted from S524M/S523C; "
        "VectorizedStrategy Protocol has no concrete impls"
    ))
    def test_s524m_is_instance_of_vectorized_strategy(self):
        """s524m implements to_token_bar_arrays — opt-in fast path."""
        from v5.strategies.s524m_v5 import S524M
        from v5.strategy_api import VectorizedStrategy

        assert isinstance(S524M(), VectorizedStrategy), (
            "s524m must implement VectorizedStrategy (has to_token_bar_arrays)"
        )

    def test_s513_is_not_instance_of_vectorized_strategy(self):
        """s513 is bar-reactive; uses engine fallback — NOT VectorizedStrategy."""
        from v5.strategies.s513_v5 import S513
        from v5.strategy_api import VectorizedStrategy

        assert not isinstance(S513(), VectorizedStrategy), (
            "s513 must NOT implement VectorizedStrategy (engine fallback only)"
        )


@_M11_BRIDGE_DELETED
class TestEnginePrecomputeFallback:
    """AC #7 — engine fallback produces dict[str, TokenBarArrays] for non-vectorized."""

    def test_fallback_returns_token_bar_arrays_dict_with_expected_fields(self):
        from v5.simulator import _engine_precompute_fallback
        from v5.strategies.s513_v5 import S513
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTC", "ETH"], bars=100, seed=0, equity=10_000.0
        )
        result = _engine_precompute_fallback(
            strategy=S513(), n_bars=100, ctx=ctx
        )
        assert isinstance(result, dict)
        assert set(result.keys()) >= {"BTC", "ETH"}
        for token, tba in result.items():
            # Expected fields populated (not all-NaN / all-zero "uninitialized" markers)
            assert hasattr(tba, "direction"), f"{token}: direction field missing"
            assert hasattr(tba, "priority"), f"{token}: priority field missing"
            assert hasattr(tba, "stop_mult"), f"{token}: stop_mult field missing"
            assert len(tba.direction) == 100


@_M11_BRIDGE_DELETED
class TestS524MBitIdentityParity:
    """AC #7 — to_token_bar_arrays() output byte-identical to fallback output."""

    def test_s524m_vectorized_matches_fallback_bit_identical(self):
        """Zero-tolerance equality between vectorized fast-path and engine fallback."""
        from v5.simulator import _engine_precompute_fallback
        from v5.strategies.s524m_v5 import S524M
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTC", "ETH", "SOL"], bars=200, seed=42, equity=10_000.0
        )
        strat = S524M()

        # Vectorized fast-path
        vec = strat.to_token_bar_arrays(ctx)

        # Engine fallback by calling generate() each bar
        fallback = _engine_precompute_fallback(
            strategy=S524M(), n_bars=200, ctx=ctx
        )

        assert set(vec.keys()) == set(fallback.keys())
        for token in vec:
            v, f = vec[token], fallback[token]
            # Zero tolerance on integer fields
            np.testing.assert_array_equal(
                v.direction, f.direction,
                err_msg=f"{token}: direction arrays differ"
            )
            np.testing.assert_array_equal(
                v.priority, f.priority,
                err_msg=f"{token}: priority arrays differ"
            )
            # NaN-aware equality on float fields
            assert np.array_equal(v.stop_mult, f.stop_mult, equal_nan=True), (
                f"{token}: stop_mult arrays differ"
            )


@_M11_BRIDGE_DELETED
class TestFallbackStateMutationGuard:
    """AC #21 — fallback dispatches via __setattr__-guarded proxy."""

    def test_strategy_writing_self_attr_in_generate_raises(self):
        """A strategy that mutates self during fallback generate() must error."""
        from v5.simulator import _engine_precompute_fallback
        from v5.strategy_api import Strategy, StrategyStateMutationError
        from v5.universe_context import UniverseContext

        class _MutatingStrategy(Strategy):
            def on_start(self, ctx): pass
            def on_stop(self): pass
            def generate(self, ctx, bar_idx):
                # VIOLATION: writing to self during fallback setup loop
                self._last_bar = bar_idx
                return []
            def check_exit(self, pos, bar_ctx): return None
            def check_scale(self, pos, bar_ctx): return None

        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=50, seed=0, equity=10_000.0
        )
        with pytest.raises(StrategyStateMutationError):
            _engine_precompute_fallback(
                strategy=_MutatingStrategy(), n_bars=50, ctx=ctx
            )


@_M11_BRIDGE_DELETED
class TestSimulatePortfolioSignatureExtension:
    """AC #7 — simulate_portfolio accepts strategies + config kwargs and returns SimulationState."""

    def test_simulate_portfolio_accepts_strategies_dict_and_returns_state(self):
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, simulate_portfolio
        from v5.strategies.s524m_v5 import S524M
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=100, seed=0, equity=10_000.0
        )
        cfg = PortfolioConfig(strategies=[], capital=10_000.0)
        state = simulate_portfolio(
            strategies={"s524m": S524M()},
            config=cfg,
            ctx=ctx,
        )
        assert isinstance(state, SimulationState)


@_M11_BRIDGE_DELETED
class TestPaperVsVectorizedParity:
    """AC #23 — paper tick-dispatch and vectorized fast-path produce identical signals."""

    def test_s524m_paper_vs_vectorized_parity_one_day(self):
        """Replay one day of paper ticks; assemble TokenBarArrays; compare to vectorized."""
        from v5.simulator import paper_replay_to_token_bar_arrays
        from v5.strategies.s524m_v5 import S524M
        from v5.universe_context import UniverseContext

        # One-day fixture: 24 hourly bars (or 1440 minute-bars — fixture decides)
        ctx = UniverseContext.build_test(
            tokens=["BTC", "ETH"], bars=24, seed=7, equity=10_000.0
        )
        strat = S524M()

        vec = strat.to_token_bar_arrays(ctx)
        paper = paper_replay_to_token_bar_arrays(strategy=S524M(), ctx=ctx)

        assert set(vec.keys()) == set(paper.keys())
        for token in vec:
            v, p = vec[token], paper[token]
            np.testing.assert_array_equal(v.direction, p.direction)
            np.testing.assert_array_equal(v.priority, p.priority)
            assert np.array_equal(v.stop_mult, p.stop_mult, equal_nan=True)
