"""Acceptance tests for Task 5: Runner sub-hourly entry processing (AC26).

Tests verify:
  - Runner main loop calls process_sub_hourly_exits(candles) BEFORE
    process_sub_hourly_entries(candles) on each 1m candle flush
  - Exit-before-entry ordering is enforced
  - Both methods receive the same candles dict from a single flush
  - If flush returns empty/None, neither method is called

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until process_sub_hourly_entries is implemented (RED phase).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_engine import PaperPortfolioEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candles() -> dict[str, tuple[float, float, float]]:
    """Build a representative candles dict: {token: (high, low, close)}."""
    return {
        "BTC": (68_500.0, 67_200.0, 67_900.0),
        "ETH": (3_850.0, 3_780.0, 3_820.0),
    }


def _make_engine_mock(*, has_aggregator: bool = True):
    """Build a mock engine with candle aggregator and sub-hourly methods.

    The mock tracks call order across process_sub_hourly_exits and
    process_sub_hourly_entries so we can assert exit-before-entry ordering.
    """
    engine = MagicMock()
    engine._candle_aggregator = MagicMock() if has_aggregator else None
    # Track call ordering across both methods via a shared list
    engine._call_order = []
    engine.process_sub_hourly_exits.side_effect = (
        lambda c: engine._call_order.append("exits")
    )
    engine.process_sub_hourly_entries.side_effect = (
        lambda c: engine._call_order.append("entries")
    )
    return engine


def _make_config_mock(pool_name: str = "test-pool"):
    """Build a mock config object."""
    config = MagicMock()
    config.pool_name = pool_name
    return config


def _run_sub_hourly_block(engines, configs):
    """Execute the runner's sub-hourly processing block.

    This extracts the sub-hourly processing logic from
    run_paper_multi.py (lines 778-798) by importing and calling it.
    We import _process_sub_hourly_candles which is the refactored
    function that Task 5 must create.
    """
    from v4.run_paper_multi import _process_sub_hourly_candles
    candle_errors: dict[int, int] = {}
    _process_sub_hourly_candles(engines, configs, candle_errors)


# ===================================================================
# Test: Refactored sub-hourly function exists
# ===================================================================

class TestSubHourlyFunctionExists:
    """The runner must expose _process_sub_hourly_candles with entry support."""

    def test_process_sub_hourly_candles_importable(self):
        """_process_sub_hourly_candles is importable from run_paper_multi.

        This test will FAIL until the runner is refactored to extract
        the sub-hourly block into a testable function that calls both
        exits and entries.
        """
        from v4.run_paper_multi import _process_sub_hourly_candles
        assert callable(_process_sub_hourly_candles)


# ===================================================================
# Test: Exit-before-entry call ordering (AC26)
# ===================================================================

class TestSubHourlyExitBeforeEntry:
    """AC26: Runner calls exits BEFORE entries on each 1m candle flush."""

    def test_exits_called_before_entries(self):
        """process_sub_hourly_exits is called before process_sub_hourly_entries."""
        candles = _make_candles()
        engine = _make_engine_mock()
        config = _make_config_mock()
        engine._candle_aggregator.flush_completed.return_value = candles

        _run_sub_hourly_block([engine], [config])

        assert engine._call_order == ["exits", "entries"], (
            f"Expected exits before entries, got: {engine._call_order}"
        )

    def test_both_receive_same_candles(self):
        """Both exits and entries receive the identical candles dict from flush."""
        candles = _make_candles()
        engine = _make_engine_mock()
        config = _make_config_mock()
        engine._candle_aggregator.flush_completed.return_value = candles

        _run_sub_hourly_block([engine], [config])

        engine.process_sub_hourly_exits.assert_called_once_with(candles)
        engine.process_sub_hourly_entries.assert_called_once_with(candles)

    def test_flush_called_once_per_cycle(self):
        """flush_completed is called exactly once per engine per sub-hourly cycle."""
        candles = _make_candles()
        engine = _make_engine_mock()
        config = _make_config_mock()
        engine._candle_aggregator.flush_completed.return_value = candles

        _run_sub_hourly_block([engine], [config])

        engine._candle_aggregator.flush_completed.assert_called_once()

    def test_ordering_with_multiple_engines(self):
        """Each engine gets exits-before-entries independently."""
        candles_a = {"BTC": (68_500.0, 67_200.0, 67_900.0)}
        candles_b = {"ETH": (3_850.0, 3_780.0, 3_820.0)}

        engine_a = _make_engine_mock()
        engine_b = _make_engine_mock()
        config_a = _make_config_mock("pool-a")
        config_b = _make_config_mock("pool-b")
        engine_a._candle_aggregator.flush_completed.return_value = candles_a
        engine_b._candle_aggregator.flush_completed.return_value = candles_b

        _run_sub_hourly_block([engine_a, engine_b], [config_a, config_b])

        assert engine_a._call_order == ["exits", "entries"], (
            f"Engine A: expected exits before entries, got: {engine_a._call_order}"
        )
        assert engine_b._call_order == ["exits", "entries"], (
            f"Engine B: expected exits before entries, got: {engine_b._call_order}"
        )


# ===================================================================
# Test: Empty/None flush skips both methods
# ===================================================================

class TestSubHourlyEmptyFlush:
    """If flush returns empty or None, neither exits nor entries are called."""

    def test_empty_dict_skips_both(self):
        """Empty candles dict means no exits or entries processing."""
        engine = _make_engine_mock()
        config = _make_config_mock()
        engine._candle_aggregator.flush_completed.return_value = {}

        _run_sub_hourly_block([engine], [config])

        engine.process_sub_hourly_exits.assert_not_called()
        engine.process_sub_hourly_entries.assert_not_called()

    def test_none_flush_skips_both(self):
        """None from flush_completed means no exits or entries processing."""
        engine = _make_engine_mock()
        config = _make_config_mock()
        engine._candle_aggregator.flush_completed.return_value = None

        _run_sub_hourly_block([engine], [config])

        engine.process_sub_hourly_exits.assert_not_called()
        engine.process_sub_hourly_entries.assert_not_called()

    def test_no_aggregator_skips_both(self):
        """Engine without candle aggregator skips sub-hourly processing entirely."""
        engine = _make_engine_mock(has_aggregator=False)
        config = _make_config_mock()

        _run_sub_hourly_block([engine], [config])

        engine.process_sub_hourly_exits.assert_not_called()
        engine.process_sub_hourly_entries.assert_not_called()


# ===================================================================
# Test: process_sub_hourly_entries exists on the real engine
# ===================================================================

class TestEngineHasSubHourlyEntries:
    """The real PaperPortfolioEngine must expose process_sub_hourly_entries."""

    def test_engine_has_process_sub_hourly_entries_method(self):
        """PaperPortfolioEngine has a callable process_sub_hourly_entries method.

        This test will FAIL in RED phase because the method does not exist yet.
        """
        assert hasattr(PaperPortfolioEngine, "process_sub_hourly_entries"), (
            "PaperPortfolioEngine must have a process_sub_hourly_entries method"
        )
        assert callable(getattr(PaperPortfolioEngine, "process_sub_hourly_entries")), (
            "process_sub_hourly_entries must be callable"
        )

    def test_process_sub_hourly_entries_accepts_candles_dict(self):
        """process_sub_hourly_entries accepts a candles dict parameter.

        This test will FAIL in RED phase because the method does not exist yet.
        """
        import inspect
        sig = inspect.signature(PaperPortfolioEngine.process_sub_hourly_entries)
        # Should accept self + candles (at minimum 2 params)
        params = list(sig.parameters.keys())
        assert len(params) >= 2, (
            f"process_sub_hourly_entries should accept (self, candles), got params: {params}"
        )
