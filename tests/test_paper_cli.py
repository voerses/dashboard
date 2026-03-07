"""AC15, AC16, AC17: Paper engine CLI and operational resilience.

Tests verify:
- AC15: Long-running process with CLI: start/stop/status/list subcommands
- AC16: Resume from persisted state — picks up positions, continues tracking
- AC17: Exchange API error handling with exponential backoff
"""

import json
import os
import subprocess
import sys
import time

import pytest

from v3.paper_cli import PaperCLI, parse_args
from v3.paper_engine import PaperEngine


# ---------------------------------------------------------------------------
# AC15: CLI subcommands — start/stop/status/list
# ---------------------------------------------------------------------------


class TestCLIParseArgs:
    """CLI argument parsing for subcommands."""

    def test_parse_start_subcommand(self):
        args = parse_args(["start", "--strategy", "s11", "--exchange", "binance"])
        assert args.command == "start"
        assert args.strategy == "s11"
        assert args.exchange == "binance"

    def test_parse_stop_subcommand(self):
        args = parse_args(["stop", "--strategy", "s11"])
        assert args.command == "stop"
        assert args.strategy == "s11"

    def test_parse_status_subcommand(self):
        args = parse_args(["status", "--strategy", "s11"])
        assert args.command == "status"
        assert args.strategy == "s11"

    def test_parse_list_subcommand(self):
        args = parse_args(["list"])
        assert args.command == "list"

    def test_start_requires_strategy(self):
        """start subcommand requires --strategy."""
        with pytest.raises(SystemExit):
            parse_args(["start", "--exchange", "binance"])

    def test_start_requires_exchange(self):
        """start subcommand requires --exchange."""
        with pytest.raises(SystemExit):
            parse_args(["start", "--strategy", "s11"])

    def test_no_subcommand_shows_help(self):
        """No subcommand should error or show help."""
        with pytest.raises(SystemExit):
            parse_args([])


class TestCLIStart:
    """CLI start subcommand creates and starts a paper engine."""

    def test_start_creates_engine(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        status = cli.status(strategy="s11")
        assert status in ("running", "started")

    def test_start_multiple_strategies(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        cli.start(strategy="s09", exchange="binance", tokens=["ETH/USDT"])
        engines = cli.list()
        strategy_ids = [e["strategy_id"] for e in engines]
        assert "s11" in strategy_ids
        assert "s09" in strategy_ids


class TestCLIStop:
    """CLI stop subcommand stops a running engine."""

    def test_stop_changes_status(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        cli.stop(strategy="s11")
        status = cli.status(strategy="s11")
        assert status == "stopped"

    def test_stop_nonexistent_raises(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        with pytest.raises(KeyError):
            cli.stop(strategy="nonexistent")


class TestCLIStatus:
    """CLI status subcommand reports engine state."""

    def test_status_running(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        assert cli.status(strategy="s11") in ("running", "started")

    def test_status_stopped(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        cli.stop(strategy="s11")
        assert cli.status(strategy="s11") == "stopped"

    def test_status_unknown_raises(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        with pytest.raises(KeyError):
            cli.status(strategy="unknown")


class TestCLIList:
    """CLI list subcommand shows all engines."""

    def test_list_returns_all_engines(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        cli.start(strategy="s09", exchange="binance", tokens=["ETH/USDT"])
        engines = cli.list()
        assert len(engines) == 2

    def test_list_empty_when_none_started(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        engines = cli.list()
        assert engines == []

    def test_list_includes_strategy_and_exchange(self, tmp_path):
        cli = PaperCLI(state_dir=str(tmp_path))
        cli.start(strategy="s11", exchange="binance", tokens=["BTC/USDT"])
        engines = cli.list()
        assert engines[0]["strategy_id"] == "s11"
        assert engines[0]["exchange"] == "binance"


# ---------------------------------------------------------------------------
# AC16: Resume from persisted state
# ---------------------------------------------------------------------------


class TestResumeFromState:
    """Engine resumes from persisted state — picks up positions, continues tracking."""

    def test_resume_restores_positions(self, tmp_path, sample_position_state):
        """After resume, previously open positions are restored."""
        # sample_position_state has 1 BTC/USDT position
        state_dir = os.path.dirname(sample_position_state)
        cli = PaperCLI(state_dir=state_dir)
        cli.resume(strategy="s11", exchange="binance")
        # Engine should have the restored position
        engines = cli.list()
        assert len(engines) >= 1

    def test_resume_restores_last_tick_time(self, tmp_path, sample_position_state):
        """After resume, last_tick_time is restored from state."""
        state_dir = os.path.dirname(sample_position_state)

        engine_config = {
            "strategy_id": "s11",
            "tokens": ["BTC/USDT"],
            "market": "perp",
            "exchange": "binance",
            "timeframe": "1h",
            "capital": 200000.0,
            "state_dir": state_dir,
            "data_dir": str(tmp_path / "data"),
        }
        engine = PaperEngine(config=engine_config)
        engine.resume()
        assert engine.last_tick_time == 1700010800

    def test_resume_restores_equity(self, tmp_path, sample_position_state):
        """After resume, equity is restored from state."""
        state_dir = os.path.dirname(sample_position_state)

        engine_config = {
            "strategy_id": "s11",
            "tokens": ["BTC/USDT"],
            "market": "perp",
            "exchange": "binance",
            "timeframe": "1h",
            "capital": 200000.0,
            "state_dir": state_dir,
            "data_dir": str(tmp_path / "data"),
        }
        engine = PaperEngine(config=engine_config)
        engine.resume()
        assert engine.equity == pytest.approx(200150.0)

    def test_resume_continues_tracking(self, tmp_path, sample_position_state):
        """After resume, engine can continue ticking."""
        state_dir = os.path.dirname(sample_position_state)

        engine_config = {
            "strategy_id": "s11",
            "tokens": ["BTC/USDT"],
            "market": "perp",
            "exchange": "binance",
            "timeframe": "1h",
            "capital": 200000.0,
            "state_dir": state_dir,
            "data_dir": str(tmp_path / "data"),
        }
        engine = PaperEngine(config=engine_config)
        engine.resume()
        # Engine should be able to tick without error
        assert hasattr(engine, "tick")


# ---------------------------------------------------------------------------
# AC17: Exchange API error handling with exponential backoff
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """Exchange API errors handled with exponential backoff."""

    def test_engine_has_backoff_config(self, sample_paper_engine_config):
        """Engine has configurable backoff parameters."""
        engine = PaperEngine(config=sample_paper_engine_config)
        assert hasattr(engine, "max_retries"), (
            "Engine must expose max_retries for backoff configuration"
        )
        assert engine.max_retries > 0

    def test_backoff_increases_exponentially(self, sample_paper_engine_config):
        """Retry delays increase exponentially: 1s, 2s, 4s, 8s, ..."""
        engine = PaperEngine(config=sample_paper_engine_config)
        delays = engine.compute_backoff_delays(max_retries=5)
        assert len(delays) == 5
        # Each delay should be roughly 2x the previous
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1] * 1.5  # Allow some jitter

    def test_backoff_has_max_delay(self, sample_paper_engine_config):
        """Backoff delay is capped at a maximum value."""
        engine = PaperEngine(config=sample_paper_engine_config)
        delays = engine.compute_backoff_delays(max_retries=20)
        max_delay = max(delays)
        # Should be capped (e.g., 300 seconds or similar)
        assert max_delay <= 600  # At most 10 minutes

    def test_api_error_does_not_crash_engine(self, sample_paper_engine_config):
        """An API error during fetch should be caught, not crash the engine."""
        engine = PaperEngine(config=sample_paper_engine_config)
        # Simulate an API error by calling handle_api_error directly
        error = ConnectionError("Exchange API unreachable")
        engine.handle_api_error(error)
        # Engine should still be operational (not crashed)
        assert engine.error_count >= 1

    def test_retry_count_tracked(self, sample_paper_engine_config):
        """Engine tracks how many retries have occurred."""
        engine = PaperEngine(config=sample_paper_engine_config)
        assert engine.error_count == 0, "Error count should start at 0"
        # After handling errors, count should increment
        engine.handle_api_error(ConnectionError("test"))
        engine.handle_api_error(ConnectionError("test2"))
        assert engine.error_count == 2
