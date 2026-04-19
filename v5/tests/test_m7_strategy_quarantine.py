"""M7 — Strategy quarantine: exception_counter + StrategyQuarantined event.

Post-AC-S5 enforcement: recurring exceptions in the logged-but-swallowed
callback tier (generate / check_* / filter_entry / on_order_* / on_position_*)
increment a per-strategy counter; at threshold the engine publishes a
`StrategyQuarantined` event and stops dispatching to that strategy.

All tests MUST FAIL today — the quarantine machinery does not exist.
"""
# NOTE: brief ambiguous on quarantine threshold; strict interpretation —
# counter + event + dispatch-stop for offender + peers keep receiving.
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestExceptionCounter:
    """Strategy instance carries an exception_counter that increments per-error."""

    def test_counter_attribute_exists(self):
        from v5.strategy_api import BaseStrategy
        s = BaseStrategy()
        assert hasattr(s, "exception_counter"), (
            "BaseStrategy must expose exception_counter for quarantine tracking"
        )

    def test_counter_starts_at_zero(self):
        from v5.strategy_api import BaseStrategy
        assert BaseStrategy().exception_counter == 0

    def test_counter_increments_on_logged_exception(self):
        from v5.strategy_api import BaseStrategy
        from v5.universe_context import UniverseContext

        class _Flaky(BaseStrategy):
            def generate(self, ctx, bar_idx):
                raise RuntimeError("sometimes_fails")

        strat = _Flaky()
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=5, seed=0,
                                          strategies=[strat])
        ctx.run_full_lifecycle()
        assert strat.exception_counter == 5, (
            f"exception_counter must tick per swallowed error; got {strat.exception_counter}"
        )


class TestStrategyQuarantinedEvent:
    """StrategyQuarantined event publishes at the configured threshold."""

    def test_event_class_importable(self):
        from v5.strategy_api import StrategyQuarantined  # noqa: F401

    def test_quarantine_event_published_at_threshold(self):
        from v5.strategy_api import BaseStrategy, StrategyQuarantined
        from v5.universe_context import UniverseContext

        class _AlwaysBad(BaseStrategy):
            def generate(self, ctx, bar_idx):
                raise RuntimeError("oops")

        strat = _AlwaysBad()
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=20, seed=0, strategies=[strat],
            quarantine_threshold=5,
        )
        events = []
        ctx.subscribe_events(lambda e: events.append(e))
        ctx.run_full_lifecycle()

        quarantine_events = [e for e in events if isinstance(e, StrategyQuarantined)]
        assert len(quarantine_events) == 1, (
            f"StrategyQuarantined must publish exactly once when threshold crossed; "
            f"got {len(quarantine_events)} events"
        )


class TestDispatchStopsForQuarantinedStrategy:
    """After quarantine, the offending strategy no longer receives dispatches."""

    def test_generate_not_called_after_quarantine(self):
        from v5.strategy_api import BaseStrategy
        from v5.universe_context import UniverseContext

        class _AlwaysBad(BaseStrategy):
            hits = 0

            def generate(self, ctx, bar_idx):
                type(self).hits += 1
                raise RuntimeError("oops")

        strat = _AlwaysBad()
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=20, seed=0, strategies=[strat],
            quarantine_threshold=5,
        )
        ctx.run_full_lifecycle()

        # After 5 strikes the strategy is quarantined; remaining bars do NOT
        # call generate(). Allow up to threshold+1 for edge timing but no more.
        assert strat.hits <= 6, (
            f"quarantined strategy must be skipped on later bars; got {strat.hits} hits"
        )


class TestPeerStrategiesUnaffected:
    """Peer strategies continue to dispatch normally when a neighbor is quarantined."""

    def test_peer_still_runs(self):
        from v5.strategy_api import BaseStrategy
        from v5.universe_context import UniverseContext

        class _Bad(BaseStrategy):
            def generate(self, ctx, bar_idx):
                raise RuntimeError("oops")

        peer_calls: list[int] = []

        class _Peer(BaseStrategy):
            def generate(self, ctx, bar_idx):
                peer_calls.append(bar_idx)
                return None

        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=10, seed=0,
            strategies=[_Bad(), _Peer()],
            quarantine_threshold=3,
        )
        ctx.run_full_lifecycle()
        assert len(peer_calls) == 10, (
            f"peer strategy must keep receiving bar dispatches; got {len(peer_calls)}"
        )
