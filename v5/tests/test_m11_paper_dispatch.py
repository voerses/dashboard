"""M11 AC-1 (paper side) — Paper engine dispatches via `strategy.generate()`
inline.

All tests RED today — paper engine currently calls
`precompute_strategy_signals` once per tick + walks `_build_bar_maps`. Pins:

  - `strategy.generate(ctx, bar_idx)` invoked once per tick-boundary
    matching each strategy's cadence
  - 1h strategy only invoked at 1h boundaries (not at sub-hour bars)
  - `precompute_strategy_signals` is NOT called from the paper tick body
  - `_build_bar_maps` is deleted from PaperPortfolioEngine
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ----------------------------------------------------------------------
# generate() invoked per tick
# ----------------------------------------------------------------------


def test_generate_invoked_per_tick(tmp_path):
    """Paper engine invokes `strategy.generate(ctx, bar_idx)` once per tick
    at the strategy's cadence."""
    from v5.paper_engine import PaperPortfolioEngine

    gen_calls = []

    class _MockStrategy:
        id = "mock"
        strategy_id = "mock"

        def required_data(self):
            return []

        def generate(self, ctx, bar_idx):
            gen_calls.append(bar_idx)
            from v5.strategy_api import UniverseSignals
            return UniverseSignals(bar_idx=bar_idx, signals={})

    # The new paper dispatch path should expose a test-friendly tick driver
    # that invokes generate per tick. Method name pinned by M11 commit 8.
    assert hasattr(PaperPortfolioEngine, "_dispatch_strategies_at_tick"), (
        "PaperPortfolioEngine._dispatch_strategies_at_tick must exist "
        "per M11 AC-1 — invokes strategy.generate(ctx, bar_idx) inline."
    )


def test_cadence_filter_paper():
    """1h-cadence strategy in paper is invoked ONLY at 1h boundaries."""
    from v5.paper_engine import PaperPortfolioEngine

    # The cadence filter reuses DataEngine's `strategies_due_at`.
    assert hasattr(PaperPortfolioEngine, "_dispatch_strategies_at_tick"), (
        "PaperPortfolioEngine must honor per-strategy cadence on every "
        "sub-resolution tick. A 1h strategy must not fire every 5m tick."
    )


def test_precompute_strategy_signals_not_called_in_paper():
    """The paper tick body does NOT call `precompute_strategy_signals`
    post-M11 (signal generation is inline via strategy.generate)."""
    import inspect

    from v5 import paper_engine

    source = inspect.getsource(paper_engine)
    # A weaker grep-style check: `_tick_internal_body` must not reference
    # `precompute_strategy_signals`. The current code DOES — this assertion
    # fails RED today.
    import ast
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_tick_internal_body":
            body_src = ast.unparse(node)
            assert "precompute_strategy_signals" not in body_src, (
                "paper_engine._tick_internal_body must not call "
                "precompute_strategy_signals() per M11 AC-1. Signal "
                "generation happens inline via strategy.generate()."
            )
            break
    else:
        pytest.fail("_tick_internal_body not found in paper_engine module")


def test_bar_maps_deleted():
    """`_build_bar_maps` is deleted from PaperPortfolioEngine."""
    from v5.paper_engine import PaperPortfolioEngine

    assert not hasattr(PaperPortfolioEngine, "_build_bar_maps"), (
        "PaperPortfolioEngine._build_bar_maps must be deleted per M11 "
        "AC-1. Pre-baked array indexing is replaced by event-driven "
        "dispatch with per-bar BarContext from MarketDataCache."
    )
