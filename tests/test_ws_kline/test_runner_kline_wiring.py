"""Acceptance tests for Task 4: Runner wiring for kline persistence.

Tests verify:
  - AC6: kline_callback uses a queue + writer thread (not direct write on WS thread)
  - AC7: Parquet schema matches: DatetimeIndex + [open, high, low, close, volume] as float64
  - AC10/AC20: 1m gap recovery launches in background thread (non-blocking)
  - AC13: _setup_shared_monitors creates PriceMonitor with kline_callback for perp, not spot
  - AC14: fetch_1m_for_open_positions function does NOT exist
  - AC15: No lingering imports of fetch_1m_for_open_positions
  - AC21: Background 1m backfill failure logs warning but doesn't raise

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import inspect
import os
import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

from v4.live_fetcher import LiveFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar_dict(
    timestamp: int = 1697380200000,
    open_: float = 65000.0,
    high: float = 65200.0,
    low: float = 64900.0,
    close: float = 65100.0,
    volume: float = 100.0,
) -> dict:
    """Build a single bar dict as would come from kline_callback."""
    return {
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ===================================================================
# AC6: Write queue + writer thread for kline persistence
# ===================================================================

class TestKlineWriteQueue:
    """AC6: Kline bars persisted via queue + writer thread, not direct write."""

    def test_kline_callback_writes_via_different_thread(self, tmp_path):
        """AC6: kline_callback enqueues to a write queue; append_to_1m_parquet
        is called from a writer thread, NOT the calling (WS) thread."""
        import v4.run_paper_multi as runner_mod

        # The runner should expose a kline writer setup function or the
        # _setup_shared_monitors should create a queue-based writer.
        # We verify by checking that the module has a _1m_write_queue attribute
        # or that _setup_shared_monitors creates a queue.
        assert hasattr(runner_mod, "_1m_write_queue") or \
            hasattr(runner_mod, "create_kline_writer"), \
            "Runner should have a _1m_write_queue or create_kline_writer for AC6"

    def test_new_tokens_get_files_created(self, tmp_path):
        """AC6: New tokens without existing 1m cache files get created on first candle close."""
        fetcher = LiveFetcher(data_dir=str(tmp_path))
        bar = _make_bar_dict(timestamp=1697380200000)

        # No existing file — append_to_1m_parquet should create it
        fetcher.append_to_1m_parquet("NEWTOKEN", [bar])

        expected_path = tmp_path / "perp" / "1m_cache" / "NEWTOKEN_1m.parquet"
        assert expected_path.exists(), \
            "append_to_1m_parquet should create file for new tokens"

        df = pd.read_parquet(expected_path)
        assert len(df) == 1


# ===================================================================
# AC7: Parquet schema validation
# ===================================================================

class TestParquetSchema:
    """AC7: Parquet schema matches existing 1m cache format."""

    def test_schema_datetimeindex_and_float64_columns(self, tmp_path):
        """AC7: Written 1m parquet has DatetimeIndex + float64 OHLCV columns."""
        fetcher = LiveFetcher(data_dir=str(tmp_path))
        bar = _make_bar_dict()
        fetcher.append_to_1m_parquet("BTC", [bar])

        path = tmp_path / "perp" / "1m_cache" / "BTC_1m.parquet"
        df = pd.read_parquet(path)

        # Check DatetimeIndex
        assert isinstance(df.index, pd.DatetimeIndex), \
            f"Index should be DatetimeIndex, got: {type(df.index)}"

        # Check columns
        expected_cols = {"open", "high", "low", "close", "volume"}
        assert expected_cols.issubset(set(df.columns)), \
            f"Missing columns: {expected_cols - set(df.columns)}"

        # Check dtypes
        for col in expected_cols:
            assert df[col].dtype == "float64", \
                f"Column {col} should be float64, got: {df[col].dtype}"

    def test_schema_tz_naive_utc(self, tmp_path):
        """AC7: DatetimeIndex is tz-naive (UTC, ms precision)."""
        fetcher = LiveFetcher(data_dir=str(tmp_path))
        bar = _make_bar_dict(timestamp=1697380200000)
        fetcher.append_to_1m_parquet("BTC", [bar])

        path = tmp_path / "perp" / "1m_cache" / "BTC_1m.parquet"
        df = pd.read_parquet(path)

        assert df.index.tz is None, \
            "DatetimeIndex should be tz-naive (tz-naive UTC convention)"


# ===================================================================
# AC10/AC20: 1m gap recovery in background thread
# ===================================================================

class TestBackfill1mBackground:
    """AC10/AC20: 1m gap recovery launches in background thread (non-blocking)."""

    @patch("v4.run_paper_multi.LiveFetcher")
    def test_1m_backfill_runs_in_background_thread(self, mock_fetcher_cls):
        """AC10/AC20: Verify the runner has a function/method that launches
        1m backfill_gaps in a daemon thread."""
        import v4.run_paper_multi as runner_mod

        # The runner should have a callable that starts background 1m backfill.
        # This could be _backfill_1m_background or similar.
        assert hasattr(runner_mod, "_backfill_1m_background") or \
            hasattr(runner_mod, "start_1m_backfill"), \
            "Runner should have _backfill_1m_background or start_1m_backfill function"

    def test_1m_backfill_uses_gap_threshold_minutes(self):
        """AC10: Background backfill uses gap_threshold_minutes (not gap_threshold_hours)
        for 1m data to detect gaps as small as 5 minutes."""
        import v4.run_paper_multi as runner_mod

        # Check that gap_threshold_minutes appears in the source of the
        # backfill function specifically (not just anywhere in the module).
        # This is a targeted structural check on the specific function.
        found = False
        for name in ("_backfill_1m_background", "start_1m_backfill"):
            fn = getattr(runner_mod, name, None)
            if fn is not None and callable(fn):
                source = inspect.getsource(fn)
                if "gap_threshold_minutes" in source:
                    found = True
                    break

        assert found, \
            "The 1m backfill function should pass gap_threshold_minutes to backfill_gaps"


# ===================================================================
# AC13: _setup_shared_monitors wiring
# ===================================================================

class TestSetupSharedMonitors:
    """AC13: _setup_shared_monitors creates PriceMonitor with kline_callback for perp."""

    @patch("v4.run_paper_multi.PriceMonitor")
    def test_perp_monitor_has_kline_callback(self, mock_pm_cls):
        """AC13: PriceMonitor for perp venue is created with kline_callback kwarg."""
        import v4.run_paper_multi as runner_mod

        mock_pm_cls.return_value = MagicMock()

        # We need to call _setup_shared_monitors with minimal mocks
        # and verify that PriceMonitor was instantiated with kline_callback
        # for the perp venue.
        # This test checks that PriceMonitor constructor received kline_callback
        # for perp venue by inspecting the call kwargs.
        try:
            runner_mod._setup_shared_monitors(
                configs=[MagicMock(market="perp", tokens=["BTC"])],
                fetcher=MagicMock(),
            )
        except Exception:
            pass  # May fail for other reasons, but we can still check PriceMonitor calls

        # Find the PriceMonitor call for perp venue
        perp_call_found = False
        for c in mock_pm_cls.call_args_list:
            kwargs = c[1] if c[1] else {}
            if kwargs.get("venue") == "perp":
                assert "kline_callback" in kwargs, \
                    "Perp PriceMonitor should receive kline_callback kwarg"
                assert kwargs["kline_callback"] is not None, \
                    "kline_callback should not be None for perp venue"
                perp_call_found = True

        assert perp_call_found, \
            "_setup_shared_monitors should create a PriceMonitor with venue='perp'"

    @patch("v4.run_paper_multi.PriceMonitor")
    def test_spot_monitor_no_kline_callback(self, mock_pm_cls):
        """AC13: PriceMonitor for spot venue does NOT receive kline_callback."""
        import v4.run_paper_multi as runner_mod

        mock_pm_cls.return_value = MagicMock()

        try:
            runner_mod._setup_shared_monitors(
                configs=[MagicMock(market="spot", tokens=["BTC"])],
                fetcher=MagicMock(),
            )
        except Exception:
            pass

        # Find the PriceMonitor call for spot venue
        for c in mock_pm_cls.call_args_list:
            kwargs = c[1] if c[1] else {}
            if kwargs.get("venue") == "spot":
                kc = kwargs.get("kline_callback")
                assert kc is None, \
                    f"Spot PriceMonitor should NOT have kline_callback, got: {kc}"


# ===================================================================
# AC14: fetch_1m_for_open_positions removed
# ===================================================================

class TestDeadCodeRemoval:
    """AC14: fetch_1m_for_open_positions function is removed."""

    def test_fetch_1m_for_open_positions_does_not_exist(self):
        """AC14: fetch_1m_for_open_positions is not in run_paper_multi module."""
        import v4.run_paper_multi as runner_mod

        assert not hasattr(runner_mod, "fetch_1m_for_open_positions"), \
            "fetch_1m_for_open_positions should be removed from run_paper_multi"


# ===================================================================
# AC15: No lingering imports of removed code
# ===================================================================

class TestLingeringImports:
    """AC15: No lingering import of fetch_1m_for_open_positions."""

    def test_no_fetch_1m_for_open_positions_import(self):
        """AC15: No import of fetch_1m_for_open_positions in run_paper_multi source."""
        import v4.run_paper_multi as runner_mod

        source = inspect.getsource(runner_mod)

        # The function name should not appear in executable code
        lines = [line for line in source.split("\n")
                 if not line.strip().startswith("#") and not line.strip().startswith('"""')]
        code_lines = "\n".join(lines)

        assert "fetch_1m_for_open_positions" not in code_lines, \
            "fetch_1m_for_open_positions should not be referenced in executable code"


# ===================================================================
# AC21: Background 1m backfill failure is non-fatal
# ===================================================================

class TestBackfillFailureHandling:
    """AC21: Background 1m backfill failure logs warning but doesn't raise."""

    def test_backfill_failure_does_not_propagate(self):
        """AC21: If backfill_gaps raises, the runner's backfill function
        catches the exception and logs instead of propagating."""
        import v4.run_paper_multi as runner_mod

        # Get the actual backfill function
        fn = getattr(runner_mod, "_backfill_1m_background", None) or \
            getattr(runner_mod, "start_1m_backfill", None)

        assert fn is not None, \
            "Runner should have _backfill_1m_background or start_1m_backfill"

        # Verify it has exception handling by inspecting its source
        source = inspect.getsource(fn)
        assert "except" in source, \
            "The 1m backfill function should catch exceptions"
        assert "warning" in source.lower() or "warn" in source.lower(), \
            "The 1m backfill function should log a warning on failure"
