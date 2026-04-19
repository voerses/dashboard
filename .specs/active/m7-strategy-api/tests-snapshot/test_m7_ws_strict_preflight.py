"""M7 — record_ws_tap.py --strict preflight flag.

Covers the operational preflight for AC-P3 recordings:
  - --strict flag causes the recorder to fail-fast on symbol misses, partial
    subscriptions, and REST-WS sequence gaps (before writing any sample).

All tests MUST FAIL today — the --strict flag is a Phase 4 add.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


RECORDER = _project_root / "v5" / "tools" / "record_ws_tap.py"


class TestRecorderExists:
    """The tool must exist on disk before flag tests are meaningful."""

    def test_recorder_file_exists(self):
        assert RECORDER.exists(), f"expected recorder at {RECORDER}"


class TestStrictFlagCLI:
    """--strict flag is advertised in --help output."""

    def test_strict_flag_advertised_in_help(self):
        result = subprocess.run(
            [sys.executable, str(RECORDER), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "--strict" in result.stdout, (
            f"--strict flag must be documented in --help;\n{result.stdout}"
        )


class TestStrictFlagImport:
    """The internal preflight helper is importable."""

    def test_strict_preflight_importable(self):
        from v5.tools.record_ws_tap import preflight_strict  # noqa: F401


class TestStrictPreflightFailFast:
    """--strict fails fast on missing subscriptions."""

    def test_symbol_miss_raises_before_write(self, tmp_path):
        from v5.tools.record_ws_tap import StrictPreflightError, preflight_strict
        with pytest.raises(StrictPreflightError, match="symbol"):
            preflight_strict(
                output_dir=tmp_path,
                expected_symbols=["BTCUSDT", "NO_SUCH_SYMBOL"],
                venue="BINANCE",
            )

    def test_strict_partial_subscription_detected(self, tmp_path):
        """AC-P3 preflight — partial WS subscription (e.g. trade but no kline)
        must abort before any samples land on disk.
        """
        from v5.tools.record_ws_tap import StrictPreflightError, preflight_strict
        with pytest.raises(StrictPreflightError, match="subscription"):
            preflight_strict(
                output_dir=tmp_path,
                expected_symbols=["BTCUSDT"],
                expected_streams=["trade", "kline"],
                available_streams=["trade"],  # kline missing
                venue="BINANCE",
            )

    def test_strict_noop_when_all_checks_pass(self, tmp_path):
        """--strict returns without raising when the environment is healthy."""
        from v5.tools.record_ws_tap import preflight_strict
        preflight_strict(
            output_dir=tmp_path,
            expected_symbols=["BTCUSDT"],
            expected_streams=["trade", "kline"],
            available_streams=["trade", "kline"],
            venue="BINANCE",
        )
