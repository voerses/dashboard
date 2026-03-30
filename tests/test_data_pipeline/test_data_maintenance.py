"""Acceptance tests for v4/data_maintenance.py (AC1-AC6, AC19-AC21).

Tests verify:
  - AC3: ensure_data_fresh calls backfill_gaps and run_promotion with correct args
  - AC2: promote_only=True skips fetch, calls run_promotion only
  - AC4: ensure_data_fresh acquires .maintenance.lock (verified by trying to acquire externally)
  - AC4: Lock timeout returns empty summary with zeros
  - AC5: ensure_data_fresh continues past individual token failures (per-token, not whole-function)
  - AC6: max_duration_s timeout sets timed_out=True in summary
  - AC5: Return dict has required keys including perp_1m sub-dict
  - AC19: Logs to data/maintenance.jsonl with correct format
  - AC21: Passes Path to run_promotion (not str) and promotion records are logged
  - AC20: check_data_staleness reports per data type
  - AC20: is_stale=True when data >6h old
  - AC20: is_stale=False when data fresh
  - AC1: CLI --verbose passes verbose=True
  - AC2: CLI --promote-only passes promote_only=True
  - AC20: CLI --check calls check_data_staleness and exits with code 1 if stale

All tests use synthetic data -- no real exchange required.
These tests MUST FAIL until data_maintenance.py is implemented (RED phase).
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

# Direct import — will raise ImportError (FAIL) until module is implemented.
# This ensures RED phase: tests FAIL, not SKIP.
from v4.data_maintenance import ensure_data_fresh, check_data_staleness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_summary():
    """Expected shape of a summary dict with zeros."""
    return {
        "gaps_filled": 0,
        "bars_fetched": 0,
        "tokens_failed": 0,
        "tokens_promoted": 0,
        "timed_out": False,
        "tokens_skipped": 0,
        "perp_1m": {"tokens_updated": 0, "bars_appended": 0},
    }


def _make_stale_parquet(path: Path, hours_old: float):
    """Create a minimal parquet file with a timestamp `hours_old` hours ago."""
    ts = pd.Timestamp.utcnow() - pd.Timedelta(hours=hours_old)
    df = pd.DataFrame({
        "open": [100.0],
        "high": [101.0],
        "low": [99.0],
        "close": [100.5],
        "volume": [1000.0],
    }, index=pd.DatetimeIndex([ts], name="timestamp"))
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ===================================================================
# AC3: ensure_data_fresh calls backfill and promote with correct args
# ===================================================================

class TestEnsureDataFreshCallsBackfillAndPromote:
    """AC3: ensure_data_fresh reuses LiveFetcher.backfill_gaps() and run_promotion()."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_calls_backfill_and_promote(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC3: Both backfill_gaps and run_promotion are called with correct args."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.return_value = {"BTC/spot": 10, "ETH/perp": 5}
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        summary = ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            caller="test",
        )

        # Verify backfill_gaps was called
        mock_fetcher.backfill_gaps.assert_called()

        # Verify run_promotion was called with both markets
        mock_run_promotion.assert_called()
        call_args = mock_run_promotion.call_args
        markets_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("markets")
        assert "spot" in markets_arg and "perp" in markets_arg, (
            f"run_promotion should be called with ['spot', 'perp'], got {markets_arg}"
        )

        assert isinstance(summary, dict)

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_promote_only_skips_fetch(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC2: promote_only=True skips backfill_gaps but calls run_promotion."""
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        summary = ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            promote_only=True,
            caller="test",
        )

        mock_fetcher.backfill_gaps.assert_not_called()
        mock_run_promotion.assert_called()


# ===================================================================
# AC4: File lock — verify lock is actually HELD during execution
# ===================================================================

class TestEnsureDataFreshLocking:
    """AC4: ensure_data_fresh acquires .maintenance.lock with exclusive flock."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_acquires_lock(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC4: .maintenance.lock is held (exclusive flock) during execution."""
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir, exist_ok=True)

        lock_actually_held = False

        def check_lock_held(*args, **kwargs):
            nonlocal lock_actually_held
            lock_path = os.path.join(data_dir, ".maintenance.lock")
            if os.path.exists(lock_path):
                # Try to acquire the lock — should FAIL because ensure_data_fresh holds it
                test_fd = open(lock_path, "w")
                try:
                    fcntl.flock(test_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    # If we got here, the lock was NOT held — fail
                    fcntl.flock(test_fd.fileno(), fcntl.LOCK_UN)
                    lock_actually_held = False
                except (IOError, OSError):
                    # Good — lock IS held by ensure_data_fresh
                    lock_actually_held = True
                finally:
                    test_fd.close()
            return {}

        mock_fetcher.backfill_gaps.side_effect = check_lock_held

        ensure_data_fresh(data_dir=data_dir, caller="test")
        assert lock_actually_held, (
            ".maintenance.lock should be held (exclusive flock) during execution"
        )

    def test_ensure_data_fresh_lock_timeout_returns_empty(self, tmp_path):
        """AC4: If lock is held externally, returns summary with zeros after timeout."""
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir, exist_ok=True)
        lock_path = os.path.join(data_dir, ".maintenance.lock")

        # Hold the lock externally
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            summary = ensure_data_fresh(
                data_dir=data_dir,
                caller="test",
                max_duration_s=5,
            )

            # Should return without raising, with zeroed summary
            assert isinstance(summary, dict)
            assert summary.get("gaps_filled", -1) == 0
            assert summary.get("bars_fetched", -1) == 0
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()


# ===================================================================
# AC5: Continues past per-token failures (not whole-function)
# ===================================================================

class TestEnsureDataFreshErrorHandling:
    """AC5: ensure_data_fresh continues past individual token failures."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_continues_past_backfill_failure(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC5: backfill_gaps raises but function returns summary with tokens_failed > 0."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.side_effect = RuntimeError("Exchange timeout")
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        summary = ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            caller="test",
        )

        # Should NOT raise — AC5 says "never raises on partial failure"
        assert isinstance(summary, dict)
        assert summary.get("tokens_failed", 0) > 0

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_1m_per_token_failure(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC5: Individual 1m token fetch failures don't stop other tokens."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.return_value = {}

        # 1m fetch succeeds for some tokens, fails for others
        call_count = [0]

        def fetch_1m_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Rate limited for first token")
            return [[1000000, 100.0, 101.0, 99.0, 100.5, 500.0]]

        mock_fetcher.fetch_ohlcv.side_effect = fetch_1m_side_effect
        mock_fetcher.filter_closed_bars_1m.return_value = []
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        summary = ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            caller="test",
        )

        # Should NOT raise
        assert isinstance(summary, dict)


# ===================================================================
# AC6: max_duration_s timeout
# ===================================================================

class TestEnsureDataFreshTimeout:
    """AC6: ensure_data_fresh respects max_duration_s."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_max_duration_timeout(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC6: timed_out=True in summary after max_duration_s exceeded."""
        mock_fetcher = MagicMock()

        def slow_backfill(*args, **kwargs):
            time.sleep(2)
            return {}

        mock_fetcher.backfill_gaps.side_effect = slow_backfill
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        summary = ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            caller="test",
            max_duration_s=0.5,  # very short timeout
        )

        assert summary.get("timed_out") is True


# ===================================================================
# AC5: Return dict structure including perp_1m sub-dict
# ===================================================================

class TestEnsureDataFreshReturnStructure:
    """AC5: ensure_data_fresh returns a well-formed summary dict."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_returns_summary_dict(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC5: Return dict has required keys including perp_1m sub-dict."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.return_value = {}
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        summary = ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            caller="test",
        )

        required_keys = {
            "gaps_filled", "bars_fetched", "tokens_failed",
            "tokens_promoted", "timed_out", "tokens_skipped",
        }
        assert required_keys.issubset(set(summary.keys())), (
            f"Missing keys: {required_keys - set(summary.keys())}"
        )

        # Also verify perp_1m sub-dict exists
        assert "perp_1m" in summary, "Summary should include perp_1m sub-dict"
        assert "tokens_updated" in summary["perp_1m"]
        assert "bars_appended" in summary["perp_1m"]


# ===================================================================
# AC19: Logging to maintenance.jsonl
# ===================================================================

class TestEnsureDataFreshLogging:
    """AC19: ensure_data_fresh logs every run to data/maintenance.jsonl."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_logs_to_maintenance_jsonl(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC19: After calling, data/maintenance.jsonl contains a JSON line with required fields."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.return_value = {}
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = []

        data_dir = str(tmp_path / "data")
        ensure_data_fresh(data_dir=data_dir, caller="test_log")

        log_path = os.path.join(data_dir, "maintenance.jsonl")
        assert os.path.exists(log_path), "maintenance.jsonl should be created"

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) >= 1, "At least one log line should be written"

        entry = json.loads(lines[-1])
        assert "timestamp" in entry, "Log entry should have timestamp"
        assert "caller" in entry, "Log entry should have caller"
        assert entry["caller"] == "test_log"
        # AC19: operation type
        assert "operation" in entry, "Log entry should have operation type"
        # AC19: duration_s
        assert "duration_s" in entry, "Log entry should have duration_s"


# ===================================================================
# AC21: Passes Path to run_promotion AND promotion records are logged
# ===================================================================

class TestEnsureDataFreshPromotionPath:
    """AC21: run_promotion receives a Path and promotion records are captured."""

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_ensure_data_fresh_passes_path_to_run_promotion(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC21: run_promotion receives a Path for data_dir."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.return_value = {}
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = [
            {"token": "BTC", "market": "perp", "bars_promoted": 10},
        ]

        ensure_data_fresh(
            data_dir=str(tmp_path / "data"),
            promote_only=True,
            caller="test",
        )

        # Check the data_dir arg passed to run_promotion is a Path
        assert mock_run_promotion.called
        call_args = mock_run_promotion.call_args
        all_args = list(call_args.args) + list(call_args.kwargs.values())
        path_found = any(isinstance(a, Path) for a in all_args)
        assert path_found, (
            "run_promotion should receive a Path object, got: "
            f"{[type(a).__name__ for a in all_args]}"
        )

    @patch("v4.data_maintenance.run_promotion")
    @patch("v4.data_maintenance.LiveFetcher")
    def test_promotion_records_in_maintenance_log(
        self, mock_fetcher_cls, mock_run_promotion, tmp_path,
    ):
        """AC21: Promotion records from run_promotion are captured in maintenance.jsonl."""
        mock_fetcher = MagicMock()
        mock_fetcher.backfill_gaps.return_value = {}
        mock_fetcher_cls.return_value = mock_fetcher
        mock_run_promotion.return_value = [
            {"token": "BTC", "market": "perp", "bars_promoted": 10},
            {"token": "ETH", "market": "perp", "bars_promoted": 5},
        ]

        data_dir = str(tmp_path / "data")
        summary = ensure_data_fresh(data_dir=data_dir, promote_only=True, caller="test")

        # Summary should reflect promoted tokens
        assert summary.get("tokens_promoted", 0) > 0

        # maintenance.jsonl should capture promotion info
        log_path = os.path.join(data_dir, "maintenance.jsonl")
        assert os.path.exists(log_path)
        with open(log_path) as f:
            entry = json.loads(f.readlines()[-1])
        # Should have promotion-related data
        assert "tokens_promoted" in str(entry) or "bars_promoted" in str(entry), (
            "Maintenance log should include promotion records"
        )


# ===================================================================
# AC20: check_data_staleness
# ===================================================================

class TestCheckDataStaleness:
    """AC20: check_data_staleness reports per data type with staleness info."""

    def test_check_data_staleness_reports_per_type(self, tmp_path):
        """AC20: Return dict has spot_1h, perp_1h, perp_1m keys with age_hours."""
        data_dir = tmp_path / "data"

        _make_stale_parquet(data_dir / "spot" / "1h_cache" / "BTC_1h.parquet", hours_old=2.0)
        _make_stale_parquet(data_dir / "perp" / "1h_cache" / "BTC_1h.parquet", hours_old=3.0)
        _make_stale_parquet(data_dir / "perp" / "1m_cache" / "BTC_1m.parquet", hours_old=1.0)

        result = check_data_staleness(data_dir=str(data_dir))

        assert "spot_1h" in result, "Should report spot_1h staleness"
        assert "perp_1h" in result, "Should report perp_1h staleness"
        assert "perp_1m" in result, "Should report perp_1m staleness"
        assert "age_hours" in result["spot_1h"]

    def test_check_data_staleness_is_stale_true_when_old(self, tmp_path):
        """AC20: is_stale=True when any data type >6h old."""
        data_dir = tmp_path / "data"

        _make_stale_parquet(data_dir / "spot" / "1h_cache" / "BTC_1h.parquet", hours_old=8.0)
        _make_stale_parquet(data_dir / "perp" / "1h_cache" / "BTC_1h.parquet", hours_old=8.0)
        _make_stale_parquet(data_dir / "perp" / "1m_cache" / "BTC_1m.parquet", hours_old=8.0)

        result = check_data_staleness(data_dir=str(data_dir))
        assert result["is_stale"] is True

    def test_check_data_staleness_is_stale_false_when_fresh(self, tmp_path):
        """AC20: is_stale=False when all data types are recent."""
        data_dir = tmp_path / "data"

        _make_stale_parquet(data_dir / "spot" / "1h_cache" / "BTC_1h.parquet", hours_old=1.0)
        _make_stale_parquet(data_dir / "perp" / "1h_cache" / "BTC_1h.parquet", hours_old=1.0)
        _make_stale_parquet(data_dir / "perp" / "1m_cache" / "BTC_1m.parquet", hours_old=0.5)

        result = check_data_staleness(data_dir=str(data_dir))
        assert result["is_stale"] is False


# ===================================================================
# AC1, AC2, AC20: CLI modes
# ===================================================================

class TestDataMaintenanceCLI:
    """AC1/AC2/AC20: CLI interface for data_maintenance module."""

    @patch("v4.data_maintenance.ensure_data_fresh")
    def test_cli_verbose_mode(self, mock_ensure, tmp_path):
        """AC1: --verbose passes verbose=True to ensure_data_fresh."""
        mock_ensure.return_value = _empty_summary()

        from v4 import data_maintenance
        with patch.object(sys, "argv", ["data_maintenance", "--verbose",
                                         "--data-dir", str(tmp_path / "data")]):
            try:
                data_maintenance.main()
            except SystemExit:
                pass

        mock_ensure.assert_called_once()
        call_kwargs = mock_ensure.call_args.kwargs
        assert call_kwargs.get("verbose") is True

    @patch("v4.data_maintenance.ensure_data_fresh")
    def test_cli_promote_only_mode(self, mock_ensure, tmp_path):
        """AC2: --promote-only passes promote_only=True."""
        mock_ensure.return_value = _empty_summary()

        from v4 import data_maintenance
        with patch.object(sys, "argv", ["data_maintenance", "--promote-only",
                                         "--data-dir", str(tmp_path / "data")]):
            try:
                data_maintenance.main()
            except SystemExit:
                pass

        mock_ensure.assert_called_once()
        call_kwargs = mock_ensure.call_args.kwargs
        assert call_kwargs.get("promote_only") is True

    @patch("v4.data_maintenance.check_data_staleness")
    def test_cli_check_mode_stale_exits_1(self, mock_check, tmp_path):
        """AC20: --check exits with code 1 when data is stale."""
        mock_check.return_value = {
            "spot_1h": {"age_hours": 8.0},
            "perp_1h": {"age_hours": 8.0},
            "perp_1m": {"age_hours": 8.0},
            "is_stale": True,
        }

        from v4 import data_maintenance
        with patch.object(sys, "argv", ["data_maintenance", "--check",
                                         "--data-dir", str(tmp_path / "data")]):
            with pytest.raises(SystemExit) as exc_info:
                data_maintenance.main()
            assert exc_info.value.code == 1, (
                f"--check should exit with code 1 when stale, got {exc_info.value.code}"
            )

    @patch("v4.data_maintenance.check_data_staleness")
    def test_cli_check_mode_fresh_exits_0(self, mock_check, tmp_path):
        """AC20: --check exits with code 0 when data is fresh."""
        mock_check.return_value = {
            "spot_1h": {"age_hours": 1.0},
            "perp_1h": {"age_hours": 1.0},
            "perp_1m": {"age_hours": 0.5},
            "is_stale": False,
        }

        from v4 import data_maintenance
        with patch.object(sys, "argv", ["data_maintenance", "--check",
                                         "--data-dir", str(tmp_path / "data")]):
            with pytest.raises(SystemExit) as exc_info:
                data_maintenance.main()
            assert exc_info.value.code == 0, (
                f"--check should exit with code 0 when fresh, got {exc_info.value.code}"
            )
