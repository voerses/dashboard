"""M7 — Strategy Protocol shape + @runtime_checkable (AC-S1).

All tests MUST FAIL today — v5.strategy_api does not expose the new Protocol.
"""
# NOTE: brief ambiguous at AC-S1; §143-176 lists 19 methods while §233
# references "15 hooks". Strict interpretation tests all 19 methods.
from __future__ import annotations

import sys
import typing
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


REQUIRED_METHODS = (
    "on_start", "on_stop", "on_reset",
    "required_data", "generate",
    "check_scale", "check_exit", "filter_entry",
    "on_order_accepted", "on_order_rejected", "on_order_cancelled",
    "on_order_triggered", "on_order_partial_fill", "on_order_filled",
    "on_order_expired",
    "on_position_opened", "on_position_changed", "on_position_closed",
    "view_state",
)


class TestStrategyProtocolShape:
    """AC-S1 — Strategy Protocol declares all 19 callbacks."""

    def test_strategy_protocol_importable(self):
        from v5.strategy_api import Strategy  # noqa: F401

    def test_strategy_is_a_protocol(self):
        from v5.strategy_api import Strategy
        assert getattr(Strategy, "_is_protocol", False), (
            "Strategy must be a typing.Protocol subclass (AC-S1)"
        )

    def test_strategy_is_runtime_checkable(self):
        from v5.strategy_api import Strategy
        assert getattr(Strategy, "_is_runtime_protocol", False), (
            "Strategy Protocol must be @runtime_checkable (AC-S1)"
        )

    @pytest.mark.parametrize("method", REQUIRED_METHODS)
    def test_protocol_declares_method(self, method):
        from v5.strategy_api import Strategy
        assert hasattr(Strategy, method), (
            f"Strategy Protocol missing method {method!r} (AC-S1)"
        )

    def test_protocol_method_count_is_exactly_19(self):
        assert len(REQUIRED_METHODS) == 19
        assert len(set(REQUIRED_METHODS)) == 19


class TestBaseStrategyDefaults:
    """AC-S1 — BaseStrategy ships the documented default implementations."""

    def test_base_strategy_importable(self):
        from v5.strategy_api import BaseStrategy  # noqa: F401

    def test_base_strategy_implements_protocol(self):
        from v5.strategy_api import BaseStrategy, Strategy
        assert isinstance(BaseStrategy(), Strategy)

    def test_lifecycle_hooks_default_noop(self):
        from v5.strategy_api import BaseStrategy
        s = BaseStrategy()
        assert s.on_start(portfolio_config=None) is None
        assert s.on_reset() is None
        assert s.on_stop("shutdown") is None

    def test_check_scale_default_returns_none(self):
        from v5.strategy_api import BaseStrategy
        assert BaseStrategy().check_scale(pos=None, bar_ctx=None) is None

    def test_check_exit_default_returns_none(self):
        from v5.strategy_api import BaseStrategy
        assert BaseStrategy().check_exit(pos=None, bar_ctx=None) is None

    def test_filter_entry_default_returns_true(self):
        from v5.strategy_api import BaseStrategy
        assert BaseStrategy().filter_entry(candidate=None, bar_ctx=None) is True

    def test_view_state_default_returns_empty_dict(self):
        from v5.strategy_api import BaseStrategy
        assert BaseStrategy().view_state() == {}

    @pytest.mark.parametrize("hook", [
        "on_order_accepted", "on_order_rejected", "on_order_cancelled",
        "on_order_triggered", "on_order_partial_fill", "on_order_filled",
        "on_order_expired",
    ])
    def test_order_event_defaults_noop(self, hook):
        from v5.strategy_api import BaseStrategy
        fn = getattr(BaseStrategy(), hook)
        try:
            result = fn(object())
        except TypeError:
            result = fn(object(), "reason_or_fill")
        assert result is None

    @pytest.mark.parametrize("hook", [
        "on_position_opened", "on_position_changed", "on_position_closed",
    ])
    def test_position_event_defaults_noop(self, hook):
        from v5.strategy_api import BaseStrategy
        fn = getattr(BaseStrategy(), hook)
        try:
            result = fn(object())
        except TypeError:
            result = fn(object(), object())
        assert result is None


class TestRuntimeIsInstanceDuckCheck:
    """AC-S1 — @runtime_checkable allows duck-typed isinstance() check."""

    def test_duck_object_with_all_methods_passes_isinstance(self):
        from v5.strategy_api import Strategy

        class DuckStrategy:
            pass

        for name in REQUIRED_METHODS:
            setattr(DuckStrategy, name, lambda self, *a, **kw: None)
        DuckStrategy.required_data = lambda self: []  # type: ignore[assignment]
        DuckStrategy.generate = lambda self, ctx, bar_idx: None  # type: ignore[assignment]
        assert isinstance(DuckStrategy(), Strategy)

    def test_missing_callback_fails_isinstance(self):
        from v5.strategy_api import Strategy

        class IncompleteStrategy:
            def on_start(self, portfolio_config): pass

        assert not isinstance(IncompleteStrategy(), Strategy)


class TestStrategyTypeAnnotations:
    """AC-S1 — Protocol methods declare their signatures."""

    def test_generate_returns_universe_signals(self):
        from v5.strategy_api import Strategy
        hints = typing.get_type_hints(Strategy.generate)
        rt = hints.get("return", None)
        rt_name = getattr(rt, "__name__", str(rt))
        assert "UniverseSignals" in rt_name, (
            f"Strategy.generate must return UniverseSignals; got {rt!r}"
        )

    def test_required_data_returns_list_of_subscriptions(self):
        from v5.strategy_api import Strategy
        hints = typing.get_type_hints(Strategy.required_data)
        rt = hints.get("return", None)
        assert rt is not None, (
            "required_data must declare a return type (list[Subscription])"
        )
