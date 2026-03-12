"""Acceptance tests for startup, restore, and PID lock (AC14-17).

Tests verify:
  - AC14: State restored from state.json on startup
  - AC15: PID lock acquired on startup (fcntl.flock)
  - AC15: PID lock prevents duplicate instances
  - AC15: PID lock cleaned up on shutdown
  - AC16: Recovery truncation: trades with tick > N removed
  - AC16: Recovery truncation: equity entries with tick > N removed
  - AC17: Cold start with no parquet cache logs warning
  - AC17: Catch-up runs for missed bars after gap

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until startup/restore logic is implemented (RED phase).
"""
from __future__ import annotations

import csv
import fcntl
import json
import os
import sys
import tempfile
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import pytest

from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.paper_engine import PaperPortfolioEngine
from v4.paper_state import serialize_state, atomic_write_state, append_trades, append_equity
from v4.position import Position, PositionManager
from v4.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(**overrides) -> PaperConfig:
    """Build a PaperConfig with sensible defaults."""
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
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
        lookback_months=3,
        enable_purge_windows=False,
        drawdown_alert_pct=5.0,
        state_dir="state/paper/",
        dashboard_push=False,
    )
    defaults.update(overrides)
    return PaperConfig(**defaults)


def _make_open_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_bar: int = 10,
    entry_price: float = 50_000.0,
) -> Position:
    """Build an open Position."""
    return Position(
        position_id=f"{token}:{strategy_id}:{entry_bar}:primary",
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=1,
        quantity=0.1,
        margin_usd=5_000.0,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.5,
        trail_mult=3.0,
        target_mult=6.0,
        no_stop_bars=6,
        min_hold=12,
        max_hold=720,
        exit_regimes={4},
        convex_exit=False,
        rsi_exit_level=999.0,
        trail_schedule=None,
        stop_price=48_000.0,
        highest=52_000.0,
        lowest=49_000.0,
        initial_risk=2_000.0,
        cumulative_funding=-10.0,
    )


def _write_state_json(state_dir: str, state: SimulationState, tick_counter: int, last_ts: str):
    """Write a state.json file to the given state_dir."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "state.json")
    atomic_write_state(state, tick_counter, last_ts, path)


# ===================================================================
# Test: AC14 — State restored from state.json on startup
# ===================================================================

class TestStateRestore:
    """AC14: State restored from state.json on startup."""

    def test_restore_state_on_startup(self):
        """Engine restores tick_counter, positions, equity, and last_timestamp from state.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a state.json with known values
            state = SimulationState(initial_capital=200_000.0)
            state.realized_pnl = 3000.0
            state.total_fees = 100.0
            state.total_funding = -50.0
            pos = _make_open_position()
            state.position_manager.open_position(pos)

            _write_state_json(tmpdir, state, tick_counter=15, last_ts="2025-01-15T12:00:00Z")

            config = _make_test_config(state_dir=tmpdir)

            # Engine should restore state on initialization
            engine = PaperPortfolioEngine(config)
            # restore_state is a new function in run_paper.py
            from v4.paper_utils import restore_state
            restore_state(engine, config)

            assert engine.tick_counter == 15
            assert engine.state.realized_pnl == pytest.approx(3000.0)
            assert engine.state.total_fees == pytest.approx(100.0)
            assert engine.state.position_manager.total_open() == 1
            # C8: last_timestamp must also be restored
            assert engine.last_timestamp == "2025-01-15T12:00:00Z", \
                f"Expected last_timestamp='2025-01-15T12:00:00Z', got {getattr(engine, 'last_timestamp', 'MISSING')}"
            # Funding must be restored
            assert engine.state.total_funding == pytest.approx(-50.0)


# ===================================================================
# Test: AC15 — PID lock acquired on startup
# ===================================================================

class TestPIDLock:
    """AC15: PID lock acquired on startup via fcntl.flock."""

    def test_pid_lock_acquired(self):
        """acquire_pid_lock creates paper.pid and acquires exclusive lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from v4.paper_utils import acquire_pid_lock

            lock_file = acquire_pid_lock(tmpdir)

            pid_path = os.path.join(tmpdir, "paper.pid")
            assert os.path.exists(pid_path)

            with open(pid_path) as f:
                pid_content = f.read().strip()
            assert pid_content == str(os.getpid())

            # Clean up
            lock_file.close()

    def test_pid_lock_prevents_duplicate(self):
        """acquire_pid_lock raises SystemExit if another instance holds the lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from v4.paper_utils import acquire_pid_lock

            # First lock succeeds
            lock1 = acquire_pid_lock(tmpdir)

            # Second lock should fail
            with pytest.raises(SystemExit):
                acquire_pid_lock(tmpdir)

            lock1.close()

    def test_pid_lock_cleaned_up_on_shutdown(self):
        """After closing the lock file, the lock is released and can be re-acquired."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from v4.paper_utils import acquire_pid_lock

            lock1 = acquire_pid_lock(tmpdir)
            lock1.close()

            # Should be able to re-acquire after close
            lock2 = acquire_pid_lock(tmpdir)
            assert lock2 is not None
            lock2.close()


# ===================================================================
# Test: AC16 — Recovery truncation: trades
# ===================================================================

class TestRecoveryTruncationTrades:
    """AC16: Recovery truncation removes trades with tick > N."""

    def test_truncates_trades_on_recovery(self):
        """On recovery with tick_counter=N, trades with tick > N are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trades_path = os.path.join(tmpdir, "trades.jsonl")

            # Write trades: tick 1, 2, 3, 4, 5
            with open(trades_path, "w") as f:
                for tick in range(1, 6):
                    f.write(json.dumps({"position_id": f"BTC:s56:{tick}:primary",
                                       "tick": tick, "pnl": tick * 100}) + "\n")

            from v4.paper_state import truncate_after_tick
            truncate_after_tick(trades_path, max_tick=3, format="jsonl")

            with open(trades_path) as f:
                remaining = [json.loads(line) for line in f]
            assert len(remaining) == 3
            assert all(t["tick"] <= 3 for t in remaining)


# ===================================================================
# Test: AC16 — Recovery truncation: equity
# ===================================================================

class TestRecoveryTruncationEquity:
    """AC16: Recovery truncation removes equity entries with tick > N."""

    def test_truncates_equity_on_recovery(self):
        """On recovery with tick_counter=N, equity rows with tick > N are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            equity_path = os.path.join(tmpdir, "equity.csv")

            with open(equity_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "tick", "portfolio_equity", "mark_to_market_equity"])
                for tick in range(1, 6):
                    writer.writerow([f"2025-01-{10+tick}T12:00:00Z", tick, 200000 + tick*100, 200100 + tick*100])

            from v4.paper_state import truncate_after_tick
            truncate_after_tick(equity_path, max_tick=3, format="csv")

            import pandas as pd
            df = pd.read_csv(equity_path)
            assert len(df) == 3
            assert df["tick"].max() <= 3


# ===================================================================
# Test: AC17 — Cold start with no parquet cache logs warning
# ===================================================================

class TestColdStart:
    """AC17: Cold start with no parquet cache logs warning."""

    def test_cold_start_logs_warning(self, caplog):
        """On first run with no parquet cache, a warning is logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)
            engine.fetcher = MagicMock()
            engine.fetcher.fetch_ohlcv.return_value = []
            engine.fetcher.filter_closed_bars.return_value = []
            engine.equity_history = []

            # Use a data_dir with no parquet files
            with tempfile.TemporaryDirectory() as empty_data_dir:
                with patch("v4.signals.DATA_DIR", empty_data_dir):
                    with caplog.at_level(logging.WARNING):
                        try:
                            engine._tick_internal(bar_timestamp="2025-01-15T12:00:00Z")
                        except Exception:
                            pass

            # Should have logged a warning about missing parquet cache
            warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
            assert any("parquet" in msg.lower() or "cache" in msg.lower() or "no data" in msg.lower()
                       for msg in warning_messages), \
                f"Expected a warning about missing parquet data. Got: {warning_messages}"


# ===================================================================
# Test: AC17 — Catch-up runs for missed bars after gap
# ===================================================================

class TestCatchUp:
    """AC17: Catch-up runs for missed bars after gap."""

    def test_catch_up_after_gap(self, caplog):
        """If last_timestamp is more than 1 hour old, restore_state detects the gap and logs it.

        Reviewer verdict (test dispute):
          The original test expected tick_counter > 10 after restore_state(),
          meaning restore_state would advance the counter during gap catch-up.
          Bug C3 showed that advancing tick_counter WITHOUT full data processing
          (no funding accrual, no stop checks, no signal recomputation) causes
          bar_maps misalignment and skipped position management.

          The engine's _catch_up() method exists for proper catch-up (calls
          _tick_internal per bar), but it requires a fully-initialized engine
          (fetcher, parquet cache, signal infrastructure) which restore_state()
          cannot guarantee at startup time.

          Correct behavior: restore_state() restores tick_counter to the stored
          value, detects the gap, and logs it.  The caller (main loop) is
          responsible for invoking _catch_up() after the engine is fully ready.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a state.json with a timestamp 3 hours ago
            state = SimulationState(initial_capital=200_000.0)
            # The last_timestamp is 3 hours old
            import datetime
            three_hours_ago = (
                datetime.datetime.utcnow() - datetime.timedelta(hours=3)
            ).isoformat() + "Z"
            _write_state_json(tmpdir, state, tick_counter=10, last_ts=three_hours_ago)

            config = _make_test_config(state_dir=tmpdir)
            engine = PaperPortfolioEngine(config)

            from v4.paper_utils import restore_state
            with caplog.at_level(logging.INFO):
                restore_state(engine, config)

            # tick_counter must equal the stored value — NOT advanced.
            # Advancing without full data processing (C3 bug) causes bar_maps
            # misalignment and skips stop-loss / funding / signal processing.
            assert engine.tick_counter == 10, (
                f"Expected tick_counter == 10 (stored value, no blind advancement), "
                f"got {engine.tick_counter}"
            )

            # The gap should be detected and logged so the caller can act on it.
            log_messages = [r.message for r in caplog.records]
            assert any("missed" in msg.lower() or "gap" in msg.lower()
                        for msg in log_messages), (
                f"Expected a log message about missed bars / gap detection. "
                f"Got: {log_messages}"
            )
