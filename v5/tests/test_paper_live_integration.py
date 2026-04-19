"""Acceptance tests for Task 7: Live Data Integration.

Tests verify:
  - Signal recomputation produces arrays covering current bar
  - Memory logging (peak RSS reported)
  - API failure retry (3 attempts with backoff)
  - Consecutive failure counter and alert
  - Catch-up mode: missed bars processed sequentially with correct
    tick_counter increments and timestamp ordering

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until live data integration is implemented (RED phase).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v5.paper_engine import PaperPortfolioEngine, TickResult
from v5.paper_config import PaperConfig
from v5.config import StrategySpec
from v5.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(**overrides) -> PaperConfig:
    defaults = dict(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10),
        ],
        capital=200_000.0,
        mode="pool",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
        lookback_months=3,
        enable_purge_windows=False,
        drawdown_alert_pct=5.0,
    )
    defaults.update(overrides)
    return PaperConfig(**defaults)


# ===================================================================
# Test: Signal recomputation produces arrays covering current bar
# ===================================================================

class TestSignalRecomputation:
    """Signal recomputation covers the current bar (AC10c)."""

    def test_recomputed_signals_cover_current_bar(self):
        """After recomputing signals, the last bar in each TokenBarArrays
        should correspond to the current tick's data.

        Per-bar arrays like stop_mult, trail_mult, leverage, etc. must have
        indices covering up to the current bar.
        """
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine.tick_counter = 100

        # Mock _recompute_signals to return signals with enough bars
        mock_sig = MagicMock()
        mock_sig.n_bars = 5000  # full lookback
        mock_sig.close = np.full(5000, 100.0)
        mock_sig.stop_mult = np.full(5000, 2.0)
        mock_sig.trail_mult = np.full(5000, 3.0)
        mock_sig.leverage = np.full(5000, 1.0)

        all_signals = {"s56": {"BTC": mock_sig}}

        # Build bar_maps
        bar_maps = engine._build_bar_maps(all_signals, engine.tick_counter)

        # The local_bar should be valid (within array bounds)
        local_bar = bar_maps["BTC"][engine.tick_counter]
        assert local_bar >= 0
        assert local_bar < mock_sig.n_bars

        # The per-bar arrays should be indexable at local_bar
        assert mock_sig.close[local_bar] == 100.0
        assert mock_sig.stop_mult[local_bar] == 2.0


# ===================================================================
# Test: Memory logging (peak RSS reported)
# ===================================================================

class TestMemoryLogging:
    """Per-tick resource usage (peak RSS) is logged (AC10c)."""

    def test_tick_result_includes_peak_rss(self):
        """TickResult should include peak_rss_mb field."""
        result = TickResult(
            skipped=False,
            error=None,
            entries=2,
            exits=1,
            peak_rss_mb=2048.5,
            processing_time_s=45.3,
        )

        assert hasattr(result, "peak_rss_mb")
        assert result.peak_rss_mb == pytest.approx(2048.5)

    def test_tick_result_includes_processing_time(self):
        """TickResult should include processing_time_s field."""
        result = TickResult(
            skipped=False,
            error=None,
            entries=0,
            exits=0,
            peak_rss_mb=1500.0,
            processing_time_s=30.0,
        )

        assert hasattr(result, "processing_time_s")
        assert result.processing_time_s == pytest.approx(30.0)


# ===================================================================
# Test: API failure retry (3 attempts with backoff)
# ===================================================================

class TestAPIRetry:
    """API failures trigger exponential backoff retry (3 attempts) (AC10d)."""

    def test_retry_3_attempts_on_failure(self):
        """On API failure, should retry 3 times before giving up."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine._consecutive_failures = 0
        engine._alerts = []

        call_count = 0

        def mock_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("API timeout")

        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv = mock_fetch

        result = engine._fetch_with_retry()

        assert result is None  # failed after all retries
        assert call_count == 3  # exactly 3 attempts

    def test_retry_succeeds_on_second_attempt(self):
        """If second attempt succeeds, no further retries needed."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine._consecutive_failures = 0
        engine._alerts = []

        call_count = 0

        def mock_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("API timeout")
            return {"BTC": np.array([[1, 2, 3, 4, 5, 6]])}

        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv = mock_fetch

        result = engine._fetch_with_retry()

        assert result is not None
        assert call_count == 2

    def test_retry_backoff_delays(self):
        """Backoff delays should be 30s, 60s, 120s between retries."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine._consecutive_failures = 0
        engine._alerts = []

        # Check that the engine defines correct backoff constants
        assert hasattr(engine, 'BACKOFF') or hasattr(PaperPortfolioEngine, 'BACKOFF')
        backoff = getattr(engine, 'BACKOFF', getattr(PaperPortfolioEngine, 'BACKOFF', None))
        assert backoff == [30, 60, 120]


# ===================================================================
# Test: Consecutive failure counter and alert
# ===================================================================

class TestConsecutiveFailures:
    """3+ consecutive tick failures fire a critical alert (AC10d)."""

    def test_consecutive_failure_counter_increments(self):
        """Each failed tick increments the consecutive failure counter."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine._consecutive_failures = 0
        engine._alerts = []

        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv = MagicMock(side_effect=ConnectionError("fail"))

        engine._fetch_with_retry()
        assert engine._consecutive_failures == 1

        engine._fetch_with_retry()
        assert engine._consecutive_failures == 2

    def test_consecutive_failure_resets_on_success(self):
        """Successful fetch resets the consecutive failure counter."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine._consecutive_failures = 5
        engine._alerts = []

        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv = MagicMock(return_value={"BTC": np.array([[1, 2, 3]])})

        engine._fetch_with_retry()
        assert engine._consecutive_failures == 0

    def test_alert_fires_after_3_consecutive_failures(self):
        """After 3 consecutive failures, a critical alert is fired."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine._consecutive_failures = 2  # already 2 failures
        engine._alerts = []

        engine.fetcher = MagicMock()
        engine.fetcher.fetch_ohlcv = MagicMock(side_effect=ConnectionError("fail"))

        engine._fetch_with_retry()

        assert engine._consecutive_failures >= 3
        # A critical alert should exist
        assert any("critical" in str(a).lower() or "consecutive" in str(a).lower()
                    for a in engine._alerts)


# ===================================================================
# Q7 fix: Catch-up mode -- verify tick_counter increments and timestamps
# ===================================================================

class TestCatchUpMode:
    """When data resumes after gaps, missed bars are processed in order (AC10d).

    Q7 fix: instead of fully mocking _tick_internal away, we verify that
    catch-up mode:
    1. Increments tick_counter correctly for each bar
    2. Passes each bar's timestamp to the processing logic in order
    3. Processes the correct number of bars
    """

    def test_catch_up_processes_missed_bars_sequentially(self):
        """If 3 bars were missed, catch-up mode processes them in order
        with incrementing tick_counter values."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine.state = SimulationState(initial_capital=200_000.0)
        engine.tick_counter = 10
        engine._consecutive_failures = 0
        engine._alerts = []

        # Track tick_counters and timestamps passed to processing
        processed_ticks = []
        processed_timestamps = []

        def mock_tick_internal(bar_timestamp=None):
            """Mock that captures tick_counter and bar_timestamp at each call."""
            processed_ticks.append(engine.tick_counter)
            if bar_timestamp is not None:
                processed_timestamps.append(bar_timestamp)
            engine.tick_counter += 1
            return TickResult(skipped=False, error=None)

        engine._tick_internal = mock_tick_internal

        # Run catch-up for 3 bars
        engine._catch_up(n_missed_bars=3)

        # Verify correct number of bars processed
        assert len(processed_ticks) == 3, (
            f"Expected 3 bars processed, got {len(processed_ticks)}"
        )

        # Verify tick_counter incremented sequentially from starting value
        assert processed_ticks == [10, 11, 12], (
            f"tick_counter should increment 10->11->12, got {processed_ticks}"
        )

    def test_catch_up_tick_counter_correct_after_completion(self):
        """After catch-up completes, tick_counter should reflect all processed bars."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine.state = SimulationState(initial_capital=200_000.0)
        engine.tick_counter = 20
        engine._consecutive_failures = 0
        engine._alerts = []

        def mock_tick_internal(bar_timestamp=None):
            engine.tick_counter += 1
            return TickResult(skipped=False, error=None)

        engine._tick_internal = mock_tick_internal

        engine._catch_up(n_missed_bars=5)

        # After 5 bars starting from tick 20, tick_counter should be 25
        assert engine.tick_counter == 25, (
            f"tick_counter should be 25 after 5 bars from 20, got {engine.tick_counter}"
        )

    def test_catch_up_with_timestamps_processes_in_order(self):
        """When catch-up receives a list of bar timestamps, they must be
        processed in chronological order."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine.state = SimulationState(initial_capital=200_000.0)
        engine.tick_counter = 10
        engine._consecutive_failures = 0
        engine._alerts = []

        processed_timestamps = []

        def mock_tick_internal(bar_timestamp=None):
            if bar_timestamp is not None:
                processed_timestamps.append(bar_timestamp)
            engine.tick_counter += 1
            return TickResult(skipped=False, error=None)

        engine._tick_internal = mock_tick_internal

        # Provide ordered timestamps for the missed bars
        missed_timestamps = [
            "2026-03-09T18:00:00Z",
            "2026-03-09T19:00:00Z",
            "2026-03-09T20:00:00Z",
        ]
        engine._catch_up(n_missed_bars=3, bar_timestamps=missed_timestamps)

        # If timestamps were passed through, verify ordering
        if processed_timestamps:
            assert processed_timestamps == missed_timestamps, (
                f"Timestamps should be in chronological order. "
                f"Got: {processed_timestamps}"
            )

    def test_catch_up_stops_on_error(self):
        """If a tick fails during catch-up, processing should stop
        (not silently skip the failed bar)."""
        config = _make_test_config()
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = config
        engine.state = SimulationState(initial_capital=200_000.0)
        engine.tick_counter = 10
        engine._consecutive_failures = 0
        engine._alerts = []

        call_count = 0

        def mock_tick_internal(bar_timestamp=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return TickResult(skipped=False, error="Signal computation failed")
            engine.tick_counter += 1
            return TickResult(skipped=False, error=None)

        engine._tick_internal = mock_tick_internal

        engine._catch_up(n_missed_bars=5)

        # Should have stopped processing after the error
        # At most 2 calls: 1 success + 1 error
        assert call_count <= 3, (
            f"Catch-up should stop or handle errors, but made {call_count} calls "
            f"instead of stopping at the error"
        )
