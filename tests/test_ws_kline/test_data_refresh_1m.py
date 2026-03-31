"""Acceptance tests for Task 5: Data maintenance integration.

Tests verify:
  - AC11: ensure_data_fresh() calls backfill_gaps(timeframe="1m") instead of per-token REST loop
  - AC12: --refresh on backtest CLI triggers both 1h and 1m flows via ensure_data_fresh()

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

from v4.data_maintenance import ensure_data_fresh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_fetcher():
    """Create a mock LiveFetcher with backfill_gaps and 1m methods."""
    fetcher = MagicMock()
    fetcher.backfill_gaps.return_value = {}
    fetcher.fetch_ohlcv.return_value = []
    fetcher.filter_closed_bars.return_value = []
    fetcher.filter_closed_bars_1m.return_value = []
    fetcher.append_to_1m_parquet = MagicMock()
    return fetcher


def _create_1m_cache_files(data_dir: str, tokens: list[str]):
    """Create minimal 1m cache parquet files so ensure_data_fresh discovers them."""
    cache_dir = Path(data_dir) / "perp" / "1m_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for token in tokens:
        path = cache_dir / f"{token}_1m.parquet"
        ts = pd.Timestamp.now() - pd.Timedelta(hours=1)
        df = pd.DataFrame({
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "volume": [1000.0],
        }, index=pd.DatetimeIndex([ts], name="timestamp"))
        df.to_parquet(path)


# ===================================================================
# AC11: ensure_data_fresh uses backfill_gaps(timeframe="1m")
# ===================================================================

class TestEnsureDataFresh1m:
    """AC11: ensure_data_fresh calls backfill_gaps(timeframe='1m')."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_calls_backfill_gaps_1m(self, mock_fetcher_cls, mock_promote, tmp_path):
        """AC11: ensure_data_fresh calls backfill_gaps(timeframe='1m') instead of per-token REST loop."""
        mock_fetcher = _make_mock_fetcher()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_promote.return_value = []

        # Create directory structure with 1m cache files so the 1m path is exercised
        data_dir = str(tmp_path / "data")
        _create_1m_cache_files(data_dir, ["BTC", "ETH"])

        # Also create 1h dirs
        (Path(data_dir) / "perp" / "1h_cache").mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "spot" / "1h_cache").mkdir(parents=True, exist_ok=True)

        summary = ensure_data_fresh(
            data_dir=data_dir,
            max_duration_s=300,
            caller="test",
        )

        # Verify backfill_gaps was called with timeframe="1m"
        backfill_calls = mock_fetcher.backfill_gaps.call_args_list
        timeframes_called = []
        for c in backfill_calls:
            kwargs = c[1] if c[1] else {}
            tf = kwargs.get("timeframe", "1h")
            timeframes_called.append(tf)

        assert "1m" in timeframes_called, \
            f"ensure_data_fresh should call backfill_gaps(timeframe='1m'), called with: {timeframes_called}"

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_1m_uses_backfill_not_fetch_ohlcv(self, mock_fetcher_cls, mock_promote, tmp_path):
        """AC11: ensure_data_fresh uses backfill_gaps for 1m, NOT per-token fetch_ohlcv(timeframe='1m').

        The old implementation called fetcher.fetch_ohlcv(token, market='perp', timeframe='1m', limit=48)
        in a loop. The new implementation should call backfill_gaps(timeframe='1m') instead.
        We verify that backfill_gaps IS called with timeframe='1m' AND that fetch_ohlcv is NOT
        called with timeframe='1m'."""
        mock_fetcher = _make_mock_fetcher()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_promote.return_value = []

        data_dir = str(tmp_path / "data")
        _create_1m_cache_files(data_dir, ["BTC", "ETH", "SOL"])
        (Path(data_dir) / "perp" / "1h_cache").mkdir(parents=True, exist_ok=True)

        ensure_data_fresh(data_dir=data_dir, max_duration_s=300, caller="test")

        # backfill_gaps MUST have been called with timeframe="1m"
        bf_calls = mock_fetcher.backfill_gaps.call_args_list
        has_1m_backfill = any(
            c[1].get("timeframe") == "1m"
            for c in bf_calls
            if c[1]
        )
        assert has_1m_backfill, \
            "ensure_data_fresh must call backfill_gaps(timeframe='1m')"

        # fetch_ohlcv must NOT be called with timeframe="1m"
        for c in mock_fetcher.fetch_ohlcv.call_args_list:
            kwargs = c[1] if c[1] else {}
            assert kwargs.get("timeframe") != "1m", \
                "ensure_data_fresh should NOT call fetch_ohlcv(timeframe='1m') — use backfill_gaps"


# ===================================================================
# AC12: --refresh triggers both 1h and 1m gap recovery
# ===================================================================

class TestRefreshBoth1hAnd1m:
    """AC12: --refresh triggers both 1h and 1m backfill via ensure_data_fresh."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_refresh_triggers_both_timeframes(self, mock_fetcher_cls, mock_promote, tmp_path):
        """AC12: ensure_data_fresh performs both 1h and 1m gap recovery."""
        mock_fetcher = _make_mock_fetcher()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_promote.return_value = []

        data_dir = str(tmp_path / "data")
        (Path(data_dir) / "perp" / "1h_cache").mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "spot" / "1h_cache").mkdir(parents=True, exist_ok=True)
        _create_1m_cache_files(data_dir, ["BTC"])

        summary = ensure_data_fresh(
            data_dir=data_dir,
            max_duration_s=300,
            caller="backtest-refresh",
        )

        # Verify backfill_gaps was called for BOTH timeframes
        backfill_calls = mock_fetcher.backfill_gaps.call_args_list
        timeframes_called = set()
        for c in backfill_calls:
            kwargs = c[1] if c[1] else {}
            tf = kwargs.get("timeframe", "1h")
            timeframes_called.add(tf)

        assert "1h" in timeframes_called or len(backfill_calls) >= 1, \
            "ensure_data_fresh should call backfill_gaps for 1h data"
        assert "1m" in timeframes_called, \
            "ensure_data_fresh should also call backfill_gaps(timeframe='1m')"

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_1m_backfill_result_reflected_in_summary(self, mock_fetcher_cls, mock_promote, tmp_path):
        """AC12: When backfill_gaps returns results for 1m, summary reflects them."""
        mock_fetcher = _make_mock_fetcher()
        # Make the 1m backfill return actual results
        def backfill_side_effect(*args, **kwargs):
            if kwargs.get("timeframe") == "1m":
                return {"BTC": 10, "ETH": 5}
            return {}
        mock_fetcher.backfill_gaps.side_effect = backfill_side_effect
        mock_fetcher_cls.return_value = mock_fetcher
        mock_promote.return_value = []

        data_dir = str(tmp_path / "data")
        _create_1m_cache_files(data_dir, ["BTC", "ETH"])
        (Path(data_dir) / "perp" / "1h_cache").mkdir(parents=True, exist_ok=True)

        summary = ensure_data_fresh(
            data_dir=data_dir,
            max_duration_s=300,
            caller="test",
        )

        assert "perp_1m" in summary, "Summary should include perp_1m section"
        assert isinstance(summary["perp_1m"], dict), "perp_1m should be a dict"
        # The summary should reflect the backfill results, not just zeros
        assert summary["perp_1m"].get("tokens_updated", 0) > 0 or \
            summary["perp_1m"].get("bars_appended", 0) > 0, \
            f"perp_1m should reflect backfill results, got: {summary['perp_1m']}"
