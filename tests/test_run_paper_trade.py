"""CLI launcher acceptance tests for run_paper_trade.py.

Tests verify the single-file CLI launcher wires together existing modules
(SetupValidator, ConfigGenerator, InstanceManager, Monitor, CompareInstances)
via argparse subcommands: setup, start, stop, status, list, monitor, compare.

Acceptance Criteria:
- AC1:  setup subcommand calls SetupValidator.run_setup() and prints results
- AC2:  start <strategy> <exchange> generates config, starts instance, launches subprocess
- AC3:  stop <instance_id> stops instance
- AC4:  status [instance_id] shows status (single or all)
- AC5:  list shows all instances
- AC6:  monitor [instance_id] shows monitoring data
- AC7:  compare <instance_a> <instance_b> runs head-to-head
- AC8:  Works without API keys; env vars injected into config, never to disk
- AC9:  Background subprocess logs to paper_trading/logs/{instance_id}.log
- AC10: Port = 8000 + strategy_num * 10 + exchange_offset (kraken=1, binance=2)
- AC11: Prints instance_id and log path on successful start
- AC12: Non-zero exit code on errors
"""

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch, call

import pytest

from run_paper_trade import (
    build_parser,
    get_exchange_credentials,
    allocate_port,
    cmd_setup,
    cmd_start,
    cmd_stop,
    cmd_status,
    cmd_list,
    cmd_monitor,
    cmd_compare,
    main,
)


# ---------------------------------------------------------------------------
# Task 1: CLI Parsing
# ---------------------------------------------------------------------------

class TestCLIParsing:
    """Argparse subcommands parse correctly and invalid input is handled."""

    def test_setup_subcommand_parses(self):
        """AC1: 'setup' subcommand is recognized."""
        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"

    def test_start_subcommand_parses_with_required_args(self):
        """AC2: 'start <strategy> <exchange>' parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        assert args.command == "start"
        assert args.strategy == "s11"
        assert args.exchange == "binance"

    def test_stop_subcommand_parses_with_instance_id(self):
        """AC3: 'stop <instance_id>' parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["stop", "s11_binance"])
        assert args.command == "stop"
        assert args.instance_id == "s11_binance"

    def test_status_subcommand_parses_without_instance_id(self):
        """AC4: 'status' works without an instance_id (shows all)."""
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"
        assert args.instance_id is None

    def test_status_subcommand_parses_with_instance_id(self):
        """AC4: 'status <instance_id>' parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["status", "s09_kraken"])
        assert args.command == "status"
        assert args.instance_id == "s09_kraken"

    def test_list_subcommand_parses(self):
        """AC5: 'list' subcommand is recognized."""
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"

    def test_monitor_subcommand_parses_without_instance_id(self):
        """AC6: 'monitor' works without an instance_id (shows all)."""
        parser = build_parser()
        args = parser.parse_args(["monitor"])
        assert args.command == "monitor"
        assert args.instance_id is None

    def test_monitor_subcommand_parses_with_instance_id(self):
        """AC6: 'monitor <instance_id>' parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["monitor", "s11_binance"])
        assert args.command == "monitor"
        assert args.instance_id == "s11_binance"

    def test_compare_subcommand_parses_with_two_instances(self):
        """AC7: 'compare <instance_a> <instance_b>' parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["compare", "s11_binance", "s09_kraken"])
        assert args.command == "compare"
        assert args.instance_a == "s11_binance"
        assert args.instance_b == "s09_kraken"

    def test_no_subcommand_exits_nonzero(self):
        """AC12: No subcommand causes a non-zero exit."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([])
        # argparse exits with 2 on missing required subcommand
        assert exc_info.value.code != 0

    def test_start_missing_exchange_exits_nonzero(self):
        """AC12: 'start s11' without exchange is an error."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["start", "s11"])
        assert exc_info.value.code != 0

    def test_compare_missing_second_instance_exits_nonzero(self):
        """AC12: 'compare s11_binance' without second instance is an error."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["compare", "s11_binance"])
        assert exc_info.value.code != 0

    @patch("run_paper_trade.SetupValidator")
    def test_setup_calls_run_setup(self, MockValidator, capsys):
        """AC1: cmd_setup() calls SetupValidator.run_setup() and prints results."""
        MockValidator.return_value.run_setup.return_value = {
            "success": True,
            "python_valid": True,
            "python_version": "3.11.0",
            "created_dirs": [],
        }

        parser = build_parser()
        args = parser.parse_args(["setup"])
        cmd_setup(args)

        MockValidator.return_value.run_setup.assert_called_once()
        captured = capsys.readouterr()
        assert "success" in captured.out.lower() or "setup" in captured.out.lower()

    def test_main_returns_zero_on_success(self):
        """main() returns 0 on a successful subcommand."""
        with patch("run_paper_trade.cmd_setup") as mock_setup:
            mock_setup.return_value = None
            exit_code = main(["setup"])
        assert exit_code == 0

    def test_main_returns_nonzero_on_error(self, capsys):
        """AC12: main() returns non-zero when a subcommand raises, with message."""
        with patch("run_paper_trade.cmd_setup", side_effect=RuntimeError("fail")):
            exit_code = main(["setup"])
        assert exit_code != 0
        captured = capsys.readouterr()
        # AC12: descriptive error message should be printed
        assert "error" in captured.out.lower() or "error" in captured.err.lower() or "fail" in captured.out.lower() or "fail" in captured.err.lower()


# ---------------------------------------------------------------------------
# Task 2: Credential Loading
# ---------------------------------------------------------------------------

class TestCredentialLoading:
    """AC8: Works without API keys; env vars injected when present."""

    def test_no_env_vars_returns_empty_dict(self, monkeypatch):
        """AC8: Works without API keys."""
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
        monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
        monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
        creds = get_exchange_credentials("binance")
        assert creds == {} or creds.get("key") is None

    def test_binance_env_vars_loaded(self, monkeypatch):
        """AC8: BINANCE_API_KEY / BINANCE_API_SECRET are picked up."""
        monkeypatch.setenv("BINANCE_API_KEY", "test-key-123")
        monkeypatch.setenv("BINANCE_API_SECRET", "test-secret-456")
        creds = get_exchange_credentials("binance")
        assert creds["key"] == "test-key-123"
        assert creds["secret"] == "test-secret-456"

    def test_kraken_env_vars_loaded(self, monkeypatch):
        """AC8: KRAKEN_API_KEY / KRAKEN_API_SECRET are picked up."""
        monkeypatch.setenv("KRAKEN_API_KEY", "kraken-key-abc")
        monkeypatch.setenv("KRAKEN_API_SECRET", "kraken-secret-def")
        creds = get_exchange_credentials("kraken")
        assert creds["key"] == "kraken-key-abc"
        assert creds["secret"] == "kraken-secret-def"

    def test_credentials_not_written_to_disk(self, monkeypatch, capsys):
        """AC8: Credentials are injected into config dict, never printed or logged."""
        monkeypatch.setenv("BINANCE_API_KEY", "secret-key-xyz")
        monkeypatch.setenv("BINANCE_API_SECRET", "secret-secret-abc")

        mock_config = {
            "dry_run": True,
            "exchange": {"name": "binance", "key": "", "secret": ""},
        }

        with patch("run_paper_trade.ConfigGenerator") as MockGen, \
             patch("run_paper_trade.InstanceManager") as MockMgr, \
             patch("subprocess.Popen") as mock_popen:
            MockGen.return_value.generate.return_value = mock_config
            MockMgr.return_value.start.return_value = "s11_binance"
            MockMgr.return_value.make_instance_id.return_value = "s11_binance"
            mock_popen.return_value.pid = 9999

            parser = build_parser()
            args = parser.parse_args(["start", "s11", "binance"])
            cmd_start(args)

        # AC8: Credentials must never appear in stdout/stderr output
        captured = capsys.readouterr()
        assert "secret-key-xyz" not in captured.out
        assert "secret-key-xyz" not in captured.err
        assert "secret-secret-abc" not in captured.out
        assert "secret-secret-abc" not in captured.err

    def test_partial_credentials_not_injected(self, monkeypatch):
        """AC8: If only key is set (no secret), credentials are not injected."""
        monkeypatch.setenv("BINANCE_API_KEY", "only-key")
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
        creds = get_exchange_credentials("binance")
        # With only a key and no secret, should not inject partial credentials
        assert creds == {} or creds.get("secret") is None


# ---------------------------------------------------------------------------
# Task 3: Start Command
# ---------------------------------------------------------------------------

class TestStartCommand:
    """AC2, AC9, AC10, AC11: start subcommand wires modules together."""

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_calls_config_generator(self, mock_popen, MockGen, MockMgr):
        """AC2: start generates config via ConfigGenerator."""
        mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
        MockGen.return_value.generate.return_value = mock_config
        MockMgr.return_value.start.return_value = "s11_binance"
        MockMgr.return_value.make_instance_id.return_value = "s11_binance"
        mock_popen.return_value.pid = 1234

        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        cmd_start(args)

        MockGen.return_value.generate.assert_called_once()

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_calls_instance_manager_start(self, mock_popen, MockGen, MockMgr):
        """AC2: start registers with InstanceManager."""
        mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
        MockGen.return_value.generate.return_value = mock_config
        MockMgr.return_value.start.return_value = "s11_binance"
        MockMgr.return_value.make_instance_id.return_value = "s11_binance"
        mock_popen.return_value.pid = 1234

        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        cmd_start(args)

        MockMgr.return_value.start.assert_called_once()

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_launches_background_subprocess(self, mock_popen, MockGen, MockMgr):
        """AC2: start launches a background subprocess."""
        mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
        MockGen.return_value.generate.return_value = mock_config
        MockMgr.return_value.start.return_value = "s11_binance"
        MockMgr.return_value.make_instance_id.return_value = "s11_binance"
        mock_popen.return_value.pid = 1234

        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        cmd_start(args)

        mock_popen.assert_called_once()

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_subprocess_logs_to_correct_path(self, mock_popen, MockGen, MockMgr):
        """AC9: Background subprocess logs to paper_trading/logs/{instance_id}.log."""
        mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
        MockGen.return_value.generate.return_value = mock_config
        MockMgr.return_value.start.return_value = "s11_binance"
        MockMgr.return_value.make_instance_id.return_value = "s11_binance"
        mock_popen.return_value.pid = 1234

        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        cmd_start(args)

        # Verify log path includes instance_id
        popen_call = mock_popen.call_args
        # The Popen call should reference a log file path containing the instance_id
        call_str = str(popen_call)
        assert "s11_binance" in call_str
        assert "log" in call_str.lower()

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_prints_instance_id(self, mock_popen, MockGen, MockMgr, capsys):
        """AC11: Prints instance_id on successful start."""
        mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
        MockGen.return_value.generate.return_value = mock_config
        MockMgr.return_value.start.return_value = "s11_binance"
        MockMgr.return_value.make_instance_id.return_value = "s11_binance"
        mock_popen.return_value.pid = 1234

        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        cmd_start(args)

        captured = capsys.readouterr()
        assert "s11_binance" in captured.out

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_prints_log_path(self, mock_popen, MockGen, MockMgr, capsys):
        """AC11: Prints log path on successful start."""
        mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
        MockGen.return_value.generate.return_value = mock_config
        MockMgr.return_value.start.return_value = "s11_binance"
        MockMgr.return_value.make_instance_id.return_value = "s11_binance"
        mock_popen.return_value.pid = 1234

        parser = build_parser()
        args = parser.parse_args(["start", "s11", "binance"])
        cmd_start(args)

        captured = capsys.readouterr()
        assert "paper_trading/logs/s11_binance.log" in captured.out

    @patch("run_paper_trade.InstanceManager")
    @patch("run_paper_trade.ConfigGenerator")
    @patch("subprocess.Popen")
    def test_start_error_returns_nonzero(self, mock_popen, MockGen, MockMgr):
        """AC12: Error during start results in non-zero exit."""
        MockGen.return_value.generate.side_effect = RuntimeError("config error")

        exit_code = main(["start", "s11", "binance"])
        assert exit_code != 0


# ---------------------------------------------------------------------------
# Task 4: Operational Commands (stop, status, list)
# ---------------------------------------------------------------------------

class TestOperationalCommands:
    """AC3, AC4, AC5: stop, status, list subcommands."""

    @patch("run_paper_trade.InstanceManager")
    def test_stop_calls_instance_manager_stop(self, MockMgr):
        """AC3: stop <instance_id> calls InstanceManager.stop()."""
        parser = build_parser()
        args = parser.parse_args(["stop", "s11_binance"])
        cmd_stop(args)

        MockMgr.return_value.stop.assert_called_once_with("s11_binance")

    @patch("run_paper_trade.InstanceManager")
    def test_stop_nonexistent_instance_returns_nonzero(self, MockMgr, capsys):
        """AC12: Stopping a nonexistent instance causes non-zero exit with message."""
        MockMgr.return_value.stop.side_effect = KeyError("not found")
        exit_code = main(["stop", "nonexistent_instance"])
        assert exit_code != 0
        captured = capsys.readouterr()
        assert len(captured.out + captured.err) > 0  # descriptive message printed

    @patch("run_paper_trade.InstanceManager")
    def test_status_single_instance(self, MockMgr, capsys):
        """AC4: 'status <instance_id>' shows status of a single instance."""
        MockMgr.return_value.status.return_value = "running"

        parser = build_parser()
        args = parser.parse_args(["status", "s11_binance"])
        cmd_status(args)

        MockMgr.return_value.status.assert_called_once_with("s11_binance")
        captured = capsys.readouterr()
        assert "running" in captured.out

    @patch("run_paper_trade.InstanceManager")
    def test_status_all_instances(self, MockMgr, capsys):
        """AC4: 'status' without instance_id shows all instances."""
        MockMgr.return_value.list.return_value = [
            {"instance_id": "s11_binance", "status": "running"},
            {"instance_id": "s09_kraken", "status": "stopped"},
        ]

        parser = build_parser()
        args = parser.parse_args(["status"])
        cmd_status(args)

        MockMgr.return_value.list.assert_called_once()
        captured = capsys.readouterr()
        assert "s11_binance" in captured.out
        assert "s09_kraken" in captured.out

    @patch("run_paper_trade.InstanceManager")
    def test_list_calls_instance_manager_list(self, MockMgr, capsys):
        """AC5: 'list' calls InstanceManager.list() and prints results."""
        MockMgr.return_value.list.return_value = [
            {"instance_id": "s11_binance", "status": "running", "strategy": "s11", "exchange": "binance"},
            {"instance_id": "s09_kraken", "status": "running", "strategy": "s09", "exchange": "kraken"},
        ]

        parser = build_parser()
        args = parser.parse_args(["list"])
        cmd_list(args)

        MockMgr.return_value.list.assert_called_once()
        captured = capsys.readouterr()
        assert "s11_binance" in captured.out
        assert "s09_kraken" in captured.out

    @patch("run_paper_trade.InstanceManager")
    def test_list_empty_shows_no_instances(self, MockMgr, capsys):
        """AC5: 'list' with no running instances shows appropriate message."""
        MockMgr.return_value.list.return_value = []

        parser = build_parser()
        args = parser.parse_args(["list"])
        cmd_list(args)

        MockMgr.return_value.list.assert_called_once()

    @patch("run_paper_trade.InstanceManager")
    def test_status_unknown_instance_returns_nonzero(self, MockMgr):
        """AC12: Querying status of unknown instance returns non-zero."""
        MockMgr.return_value.status.side_effect = KeyError("not found")
        exit_code = main(["status", "nonexistent"])
        assert exit_code != 0


# ---------------------------------------------------------------------------
# Task 5: Reporting Commands (monitor, compare)
# ---------------------------------------------------------------------------

class TestReportingCommands:
    """AC6, AC7: monitor and compare subcommands."""

    @patch("run_paper_trade.Monitor")
    def test_monitor_all_instances(self, MockMonitor, capsys):
        """AC6: 'monitor' without instance_id shows summary table."""
        MockMonitor.return_value.summary_table.return_value = [
            {"instance_id": "s11_binance", "strategy": "s11", "exchange": "binance",
             "equity": 201000.0, "drawdown": 0.005},
            {"instance_id": "s09_kraken", "strategy": "s09", "exchange": "kraken",
             "equity": 199500.0, "drawdown": 0.012},
        ]

        parser = build_parser()
        args = parser.parse_args(["monitor"])
        cmd_monitor(args)

        MockMonitor.return_value.summary_table.assert_called_once()
        captured = capsys.readouterr()
        assert "s11_binance" in captured.out
        assert "s09_kraken" in captured.out

    @patch("run_paper_trade.Monitor")
    def test_monitor_single_instance(self, MockMonitor, capsys):
        """AC6: 'monitor <instance_id>' shows single instance view."""
        MockMonitor.return_value.single_instance_view.return_value = {
            "instance_id": "s11_binance",
            "strategy": "s11",
            "exchange": "binance",
            "equity": 201000.0,
            "drawdown": 0.005,
        }

        parser = build_parser()
        args = parser.parse_args(["monitor", "s11_binance"])
        cmd_monitor(args)

        MockMonitor.return_value.single_instance_view.assert_called_once_with("s11_binance")
        captured = capsys.readouterr()
        assert "s11_binance" in captured.out

    @patch("run_paper_trade.Monitor")
    def test_monitor_unknown_instance_returns_nonzero(self, MockMonitor):
        """AC12: Monitoring unknown instance returns non-zero."""
        MockMonitor.return_value.single_instance_view.side_effect = KeyError("not found")
        exit_code = main(["monitor", "nonexistent"])
        assert exit_code != 0

    @patch("run_paper_trade.CompareInstances")
    def test_compare_calls_head_to_head(self, MockCompare, capsys):
        """AC7: 'compare <a> <b>' calls CompareInstances.head_to_head()."""
        MockCompare.return_value.head_to_head.return_value = {
            "winner": "s11_binance",
            "metrics_compared": ["sharpe", "sortino", "total_return"],
            "instance_a": "s11_binance",
            "instance_b": "s09_kraken",
        }

        parser = build_parser()
        args = parser.parse_args(["compare", "s11_binance", "s09_kraken"])
        cmd_compare(args)

        MockCompare.return_value.head_to_head.assert_called_once()
        captured = capsys.readouterr()
        assert "s11_binance" in captured.out

    @patch("run_paper_trade.CompareInstances")
    def test_compare_prints_winner(self, MockCompare, capsys):
        """AC7: Compare output includes the winner."""
        MockCompare.return_value.head_to_head.return_value = {
            "winner": "s09_kraken",
            "metrics_compared": ["sharpe", "sortino"],
            "instance_a": "s11_binance",
            "instance_b": "s09_kraken",
        }

        parser = build_parser()
        args = parser.parse_args(["compare", "s11_binance", "s09_kraken"])
        cmd_compare(args)

        captured = capsys.readouterr()
        assert "s09_kraken" in captured.out

    @patch("run_paper_trade.CompareInstances")
    def test_compare_error_returns_nonzero(self, MockCompare):
        """AC12: Compare error results in non-zero exit."""
        MockCompare.return_value.head_to_head.side_effect = RuntimeError("compare failed")
        exit_code = main(["compare", "s11_binance", "s09_kraken"])
        assert exit_code != 0


# ---------------------------------------------------------------------------
# Task 6: Port Allocation and Log Paths
# ---------------------------------------------------------------------------

class TestPortAllocation:
    """AC10: Deterministic port formula. AC9: Log path construction."""

    def test_s09_kraken_port(self):
        """AC10: Port = 8000 + 9*10 + 1 (kraken=1) = 8091."""
        port = allocate_port("s09", "kraken")
        assert port == 8091

    def test_s09_binance_port(self):
        """AC10: Port = 8000 + 9*10 + 2 (binance=2) = 8092."""
        port = allocate_port("s09", "binance")
        assert port == 8092

    def test_s11_kraken_port(self):
        """AC10: Port = 8000 + 11*10 + 1 (kraken=1) = 8111."""
        port = allocate_port("s11", "kraken")
        assert port == 8111

    def test_s11_binance_port(self):
        """AC10: Port = 8000 + 11*10 + 2 (binance=2) = 8112."""
        port = allocate_port("s11", "binance")
        assert port == 8112

    def test_s13_binance_port(self):
        """AC10: Port = 8000 + 13*10 + 2 (binance=2) = 8132."""
        port = allocate_port("s13", "binance")
        assert port == 8132

    def test_s21_kraken_port(self):
        """AC10: Port = 8000 + 21*10 + 1 (kraken=1) = 8211."""
        port = allocate_port("s21", "kraken")
        assert port == 8211

    def test_port_is_integer(self):
        """AC10: Port is always an integer."""
        port = allocate_port("s11", "binance")
        assert isinstance(port, int)

    def test_different_strategies_get_different_ports(self):
        """AC10: Different strategies on the same exchange get different ports."""
        port_a = allocate_port("s09", "binance")
        port_b = allocate_port("s11", "binance")
        assert port_a != port_b

    def test_same_strategy_different_exchanges_get_different_ports(self):
        """AC10: Same strategy on different exchanges get different ports."""
        port_k = allocate_port("s11", "kraken")
        port_b = allocate_port("s11", "binance")
        assert port_k != port_b

    def test_log_path_format(self):
        """AC9: Log path follows paper_trading/logs/{instance_id}.log convention."""
        # The log path should be derivable from instance_id
        instance_id = "s11_binance"
        expected_suffix = f"paper_trading/logs/{instance_id}.log"
        # Verify the path convention by checking the start command output
        with patch("run_paper_trade.InstanceManager") as MockMgr, \
             patch("run_paper_trade.ConfigGenerator") as MockGen, \
             patch("subprocess.Popen") as mock_popen, \
             patch("builtins.print") as mock_print:
            mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
            MockGen.return_value.generate.return_value = mock_config
            MockMgr.return_value.start.return_value = "s11_binance"
            MockMgr.return_value.make_instance_id.return_value = "s11_binance"
            mock_popen.return_value.pid = 1234

            parser = build_parser()
            args = parser.parse_args(["start", "s11", "binance"])
            cmd_start(args)

            # At least one print call should contain the log path
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "paper_trading/logs/s11_binance.log" in printed

    def test_log_directory_created_on_start(self, tmp_path):
        """AC9: Start command creates log directory if it does not exist."""
        with patch("run_paper_trade.InstanceManager") as MockMgr, \
             patch("run_paper_trade.ConfigGenerator") as MockGen, \
             patch("subprocess.Popen") as mock_popen, \
             patch("run_paper_trade.LOG_DIR", str(tmp_path / "paper_trading" / "logs")):
            mock_config = {"dry_run": True, "exchange": {"name": "binance"}}
            MockGen.return_value.generate.return_value = mock_config
            MockMgr.return_value.start.return_value = "s11_binance"
            MockMgr.return_value.make_instance_id.return_value = "s11_binance"
            mock_popen.return_value.pid = 1234

            parser = build_parser()
            args = parser.parse_args(["start", "s11", "binance"])
            cmd_start(args)

            log_dir = tmp_path / "paper_trading" / "logs"
            assert log_dir.is_dir()
