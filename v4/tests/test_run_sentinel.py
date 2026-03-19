"""Acceptance tests for Task 11: Sentinel process entry point (AC6).

Tests verify:
  - CLI argument parsing
  - PID lock acquisition
  - Graceful shutdown on SIGTERM
  - Portfolios with sentinel_mode="off" are skipped
  - Heartbeat written every interval

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until run_sentinel.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.run_sentinel import (
    parse_sentinel_args,
    acquire_pid_lock,
    release_pid_lock,
    SentinelProcess,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(data: dict, tmp_path: Path) -> str:
    """Write a config dict to a temp JSON file and return the path."""
    config_file = tmp_path / "test_config.json"
    config_file.write_text(json.dumps(data))
    return str(config_file)


def _make_multi_config(**overrides) -> dict:
    """Build a minimal multi_v4_paper config dict."""
    base = {
        "portfolios": [
            {
                "name": "portfolio_a",
                "initial_capital": 200_000.0,
                "mode": "pool",
                "pool_name": "test_a",
                "sentinel_mode": "shadow",
                "strategies": [
                    {"strategy_id": "s56", "weight": 0.5, "market": "combined",
                     "max_positions": 10},
                ],
                "max_portfolio_positions": 40,
            },
            {
                "name": "portfolio_b",
                "initial_capital": 100_000.0,
                "mode": "pool",
                "pool_name": "test_b",
                "sentinel_mode": "off",
                "strategies": [
                    {"strategy_id": "s60", "weight": 1.0, "market": "perp",
                     "max_positions": 10},
                ],
                "max_portfolio_positions": 20,
            },
        ],
        "state_dir": "/tmp/test_state",
    }
    base.update(overrides)
    return base


# ===================================================================
# Test: CLI argument parsing
# ===================================================================

class TestCliArgumentParsing:
    """Sentinel CLI parses --config argument."""

    def test_parse_config_path(self):
        """--config argument is parsed correctly."""
        args = parse_sentinel_args(["--config", "/path/to/config.json"])
        assert args.config == "/path/to/config.json"

    def test_missing_config_raises(self):
        """Missing --config raises SystemExit."""
        with pytest.raises(SystemExit):
            parse_sentinel_args([])

    def test_parse_returns_namespace(self):
        """parse_sentinel_args returns an argparse Namespace."""
        args = parse_sentinel_args(["--config", "test.json"])
        assert hasattr(args, "config")


# ===================================================================
# Test: PID lock acquisition
# ===================================================================

class TestPidLock:
    """PID lock prevents multiple sentinel instances."""

    def test_acquire_pid_lock(self, tmp_path):
        """PID lock can be acquired in an empty directory."""
        pid_file = tmp_path / "sentinel.pid"
        lock = acquire_pid_lock(str(pid_file))
        assert lock is not None
        assert pid_file.exists()
        release_pid_lock(lock, str(pid_file))

    def test_pid_file_contains_pid(self, tmp_path):
        """PID file contains the current process PID."""
        pid_file = tmp_path / "sentinel.pid"
        lock = acquire_pid_lock(str(pid_file))
        content = pid_file.read_text().strip()
        assert content == str(os.getpid())
        release_pid_lock(lock, str(pid_file))

    def test_release_removes_pid_file(self, tmp_path):
        """Releasing the lock removes the PID file."""
        pid_file = tmp_path / "sentinel.pid"
        lock = acquire_pid_lock(str(pid_file))
        release_pid_lock(lock, str(pid_file))
        assert not pid_file.exists()


# ===================================================================
# Test: Graceful shutdown on SIGTERM
# ===================================================================

class TestGracefulShutdown:
    """Sentinel handles SIGTERM/SIGINT gracefully."""

    def test_sigterm_handler_registered(self):
        """SentinelProcess.setup_signal_handlers registers SIGTERM handler."""
        process = SentinelProcess.__new__(SentinelProcess)
        process._shutdown_requested = False
        with patch("signal.signal") as mock_signal:
            process.setup_signal_handlers()
            # Verify signal.signal was called for SIGTERM
            sigterm_calls = [
                c for c in mock_signal.call_args_list
                if c[0][0] == signal.SIGTERM
            ]
            assert len(sigterm_calls) >= 1, "SIGTERM handler not registered"

    def test_shutdown_flag_set_on_signal(self):
        """Signal handler sets a shutdown flag."""
        process = SentinelProcess.__new__(SentinelProcess)
        process._shutdown_requested = False
        process._handle_shutdown_signal(signal.SIGTERM, None)
        assert process._shutdown_requested is True


# ===================================================================
# Test: Portfolios with sentinel_mode="off" are skipped
# ===================================================================

class TestPortfolioFiltering:
    """Portfolios with sentinel_mode='off' are excluded from monitoring."""

    def test_off_portfolios_skipped(self, tmp_path):
        """Portfolios with sentinel_mode='off' are not monitored."""
        config = _make_multi_config()
        config_path = _write_config(config, tmp_path)

        process = SentinelProcess.__new__(SentinelProcess)
        portfolios = process._filter_active_portfolios(config["portfolios"])

        names = [p["name"] for p in portfolios]
        assert "portfolio_a" in names
        assert "portfolio_b" not in names

    def test_all_off_results_in_empty_list(self, tmp_path):
        """When all portfolios are 'off', result is empty."""
        config = _make_multi_config()
        for p in config["portfolios"]:
            p["sentinel_mode"] = "off"

        process = SentinelProcess.__new__(SentinelProcess)
        portfolios = process._filter_active_portfolios(config["portfolios"])
        assert len(portfolios) == 0

    def test_shadow_and_live_included(self, tmp_path):
        """Both 'shadow' and 'live' modes are included."""
        config = _make_multi_config()
        config["portfolios"][0]["sentinel_mode"] = "shadow"
        config["portfolios"][1]["sentinel_mode"] = "live"

        process = SentinelProcess.__new__(SentinelProcess)
        portfolios = process._filter_active_portfolios(config["portfolios"])
        assert len(portfolios) == 2


# ===================================================================
# Test: Heartbeat written every interval
# ===================================================================

class TestHeartbeat:
    """Sentinel writes heartbeat file periodically."""

    def test_heartbeat_written(self, tmp_path):
        """Heartbeat file is written to state_dir."""
        process = SentinelProcess.__new__(SentinelProcess)
        process._state_dir = tmp_path

        process.write_heartbeat()

        heartbeat_file = tmp_path / "sentinel_heartbeat.json"
        assert heartbeat_file.exists()

    def test_heartbeat_contains_timestamp(self, tmp_path):
        """Heartbeat JSON contains a timestamp field."""
        process = SentinelProcess.__new__(SentinelProcess)
        process._state_dir = tmp_path

        process.write_heartbeat()

        data = json.loads((tmp_path / "sentinel_heartbeat.json").read_text())
        assert "timestamp" in data

    def test_heartbeat_updates_on_successive_writes(self, tmp_path):
        """Successive heartbeat writes update the timestamp."""
        process = SentinelProcess.__new__(SentinelProcess)
        process._state_dir = tmp_path

        process.write_heartbeat()
        data1 = json.loads((tmp_path / "sentinel_heartbeat.json").read_text())

        process.write_heartbeat()
        data2 = json.loads((tmp_path / "sentinel_heartbeat.json").read_text())

        assert data2["timestamp"] >= data1["timestamp"]

    def test_heartbeat_contains_spot_ws_connected(self, tmp_path):
        """Heartbeat JSON contains spot_ws_connected field."""
        process = SentinelProcess.__new__(SentinelProcess)
        process._state_dir = tmp_path

        process.write_heartbeat(ws_connected=True, spot_ws_connected=True, active_tokens=5)

        data = json.loads((tmp_path / "sentinel_heartbeat.json").read_text())
        assert "spot_ws_connected" in data
        assert data["spot_ws_connected"] is True
        assert data["ws_connected"] is True


# ===================================================================
# Test: Dual-venue monitoring — sentinel creates both monitors
# ===================================================================

class TestDualVenueMonitoring:
    """Sentinel creates both perp and spot PriceMonitor instances."""

    def test_sentinel_has_dual_monitors(self):
        """SentinelProcess has both _perp_monitor and _spot_monitor attributes."""
        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        assert hasattr(process, "_perp_monitor")
        assert hasattr(process, "_spot_monitor")

    def test_sentinel_has_venue_callbacks(self):
        """SentinelProcess has _on_perp_price and _on_spot_price methods."""
        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        assert hasattr(process, "_on_perp_price")
        assert hasattr(process, "_on_spot_price")

    def test_perp_callback_passes_is_perp_true(self):
        """_on_perp_price passes is_perp=True to _on_price_update."""
        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")

        calls = []
        original = process._on_price_update
        process._on_price_update = lambda t, p, ts, is_perp=None: calls.append(
            {"token": t, "is_perp": is_perp}
        )

        process._on_perp_price("BTC", 65000.0, 1000)
        assert len(calls) == 1
        assert calls[0]["is_perp"] is True

    def test_spot_callback_passes_is_perp_false(self):
        """_on_spot_price passes is_perp=False to _on_price_update."""
        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")

        calls = []
        process._on_price_update = lambda t, p, ts, is_perp=None: calls.append(
            {"token": t, "is_perp": is_perp}
        )

        process._on_spot_price("BTC", 65000.0, 1000)
        assert len(calls) == 1
        assert calls[0]["is_perp"] is False


# ===================================================================
# Test: Token partitioning — tokens correctly split by venue
# ===================================================================

class TestTokenPartitioning:
    """_refresh_stops partitions tokens into perp and spot sets."""

    def test_perp_only_stops(self, tmp_path):
        """All-perp stops produce (perp_tokens, empty spot_tokens)."""
        from v4.breach_detector import BreachDetector
        from v4.stop_store import StopLevel, StopStore

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        store = StopStore(state_dir=state_dir)

        # Write perp-only stops
        stops = [
            StopLevel(
                position_id="BTC:s56:100:primary", token="BTC", strategy_id="s56",
                direction=1, stop_price=64000, cb_price=60000, target_price=80000,
                estimated_liq_price=55000, entry_price=66000, margin_usd=10000,
                quantity=0.15, leverage=1.0, is_perp=True, no_stop_bars=0,
                bars_held=10, stop_active=True, convex_exit=False, trail_mult=3.0,
                cur_atr=1200, highest=70000, lowest=62000, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=-12.5, fee_rate=0.0004,
            ),
        ]
        store.write_stops(stops)

        detector = BreachDetector(
            portfolio_name="test", stop_store=store, state_dir=state_dir,
        )

        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        process._detectors = {"test": detector}
        process._metrics = None

        perp_tokens, spot_tokens = process._refresh_stops()
        assert "BTC" in perp_tokens
        assert len(spot_tokens) == 0

    def test_spot_only_stops(self, tmp_path):
        """All-spot stops produce (empty perp_tokens, spot_tokens)."""
        from v4.breach_detector import BreachDetector
        from v4.stop_store import StopLevel, StopStore

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        store = StopStore(state_dir=state_dir)

        stops = [
            StopLevel(
                position_id="BTC:s86:100:primary", token="BTC", strategy_id="s86",
                direction=1, stop_price=64000, cb_price=60000, target_price=80000,
                estimated_liq_price=0, entry_price=66000, margin_usd=10000,
                quantity=0.15, leverage=1.0, is_perp=False, no_stop_bars=0,
                bars_held=10, stop_active=True, convex_exit=False, trail_mult=3.0,
                cur_atr=1200, highest=70000, lowest=62000, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=0, fee_rate=0.001,
            ),
        ]
        store.write_stops(stops)

        detector = BreachDetector(
            portfolio_name="test", stop_store=store, state_dir=state_dir,
        )

        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        process._detectors = {"test": detector}
        process._metrics = None

        perp_tokens, spot_tokens = process._refresh_stops()
        assert len(perp_tokens) == 0
        assert "BTC" in spot_tokens

    def test_mixed_stops(self, tmp_path):
        """Mixed perp+spot stops put token in both sets."""
        from v4.breach_detector import BreachDetector
        from v4.stop_store import StopLevel, StopStore

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        store = StopStore(state_dir=state_dir)

        stops = [
            StopLevel(
                position_id="BTC:s56:100:primary", token="BTC", strategy_id="s56",
                direction=1, stop_price=64000, cb_price=60000, target_price=80000,
                estimated_liq_price=55000, entry_price=66000, margin_usd=10000,
                quantity=0.15, leverage=1.0, is_perp=True, no_stop_bars=0,
                bars_held=10, stop_active=True, convex_exit=False, trail_mult=3.0,
                cur_atr=1200, highest=70000, lowest=62000, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=-12.5, fee_rate=0.0004,
            ),
            StopLevel(
                position_id="BTC:s86:100:primary", token="BTC", strategy_id="s86",
                direction=1, stop_price=63000, cb_price=59000, target_price=80000,
                estimated_liq_price=0, entry_price=66000, margin_usd=10000,
                quantity=0.15, leverage=1.0, is_perp=False, no_stop_bars=0,
                bars_held=10, stop_active=True, convex_exit=False, trail_mult=3.0,
                cur_atr=1200, highest=70000, lowest=62000, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=0, fee_rate=0.001,
            ),
        ]
        store.write_stops(stops)

        detector = BreachDetector(
            portfolio_name="test", stop_store=store, state_dir=state_dir,
        )

        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        process._detectors = {"test": detector}
        process._metrics = None

        perp_tokens, spot_tokens = process._refresh_stops()
        assert "BTC" in perp_tokens
        assert "BTC" in spot_tokens


# ===================================================================
# Test: Spot-only portfolio gets spot monitoring
# ===================================================================

class TestSpotOnlyPortfolio:
    """Portfolio with only spot positions gets spot monitoring."""

    def test_spot_only_partition(self, tmp_path):
        """Spot-only portfolio produces only spot tokens."""
        from v4.breach_detector import BreachDetector
        from v4.stop_store import StopLevel, StopStore

        state_dir = tmp_path / "spot_portfolio"
        state_dir.mkdir()
        store = StopStore(state_dir=state_dir)

        stops = [
            StopLevel(
                position_id="ETH:s87:100:primary", token="ETH", strategy_id="s87",
                direction=1, stop_price=3200, cb_price=3000, target_price=4000,
                estimated_liq_price=0, entry_price=3400, margin_usd=5000,
                quantity=1.5, leverage=1.0, is_perp=False, no_stop_bars=0,
                bars_held=5, stop_active=True, convex_exit=False, trail_mult=2.5,
                cur_atr=80, highest=3500, lowest=3100, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=0, fee_rate=0.001,
            ),
            StopLevel(
                position_id="SOL:s88:100:primary", token="SOL", strategy_id="s88",
                direction=1, stop_price=140, cb_price=120, target_price=200,
                estimated_liq_price=0, entry_price=155, margin_usd=3000,
                quantity=20, leverage=1.0, is_perp=False, no_stop_bars=0,
                bars_held=8, stop_active=True, convex_exit=False, trail_mult=2.0,
                cur_atr=5, highest=160, lowest=135, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=0, fee_rate=0.001,
            ),
        ]
        store.write_stops(stops)

        detector = BreachDetector(
            portfolio_name="spot_only", stop_store=store, state_dir=state_dir,
        )

        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        process._detectors = {"spot_only": detector}
        process._metrics = None

        perp_tokens, spot_tokens = process._refresh_stops()
        assert len(perp_tokens) == 0
        assert "ETH" in spot_tokens
        assert "SOL" in spot_tokens


# ===================================================================
# Test: Mixed portfolio — combined strategy tokens in both sets
# ===================================================================

class TestMixedPortfolio:
    """Combined strategy puts same token in both perp and spot sets."""

    def test_combined_strategy_both_venues(self, tmp_path):
        """Combined strategy BTC long-spot + BTC short-perp in both sets."""
        from v4.breach_detector import BreachDetector
        from v4.stop_store import StopLevel, StopStore

        state_dir = tmp_path / "combined"
        state_dir.mkdir()
        store = StopStore(state_dir=state_dir)

        stops = [
            # Spot long leg
            StopLevel(
                position_id="BTC:s86:100:spot", token="BTC", strategy_id="s86",
                direction=1, stop_price=63000, cb_price=59000, target_price=80000,
                estimated_liq_price=0, entry_price=65000, margin_usd=10000,
                quantity=0.15, leverage=1.0, is_perp=False, no_stop_bars=0,
                bars_held=10, stop_active=True, convex_exit=False, trail_mult=3.0,
                cur_atr=1200, highest=70000, lowest=62000, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=0, fee_rate=0.001,
            ),
            # Perp short leg
            StopLevel(
                position_id="BTC:s86:100:perp", token="BTC", strategy_id="s86",
                direction=-1, stop_price=68000, cb_price=72000, target_price=55000,
                estimated_liq_price=80000, entry_price=65000, margin_usd=10000,
                quantity=0.15, leverage=1.0, is_perp=True, no_stop_bars=0,
                bars_held=10, stop_active=True, convex_exit=False, trail_mult=3.0,
                cur_atr=1200, highest=70000, lowest=62000, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=-5.0, fee_rate=0.0004,
            ),
            # Pure perp position (different token)
            StopLevel(
                position_id="ETH:s60:100:primary", token="ETH", strategy_id="s60",
                direction=1, stop_price=3200, cb_price=3000, target_price=4000,
                estimated_liq_price=2800, entry_price=3400, margin_usd=5000,
                quantity=1.5, leverage=2.0, is_perp=True, no_stop_bars=0,
                bars_held=5, stop_active=True, convex_exit=False, trail_mult=2.5,
                cur_atr=80, highest=3500, lowest=3100, has_trail_schedule=False,
                chandelier_lookback=0, cumulative_funding=-2.0, fee_rate=0.0004,
            ),
        ]
        store.write_stops(stops)

        detector = BreachDetector(
            portfolio_name="combined", stop_store=store, state_dir=state_dir,
        )

        process = SentinelProcess.__new__(SentinelProcess)
        process.__init__("/dummy/config.json")
        process._detectors = {"combined": detector}
        process._metrics = None

        perp_tokens, spot_tokens = process._refresh_stops()

        # BTC in both (combined strategy)
        assert "BTC" in perp_tokens
        assert "BTC" in spot_tokens
        # ETH only in perp
        assert "ETH" in perp_tokens
        assert "ETH" not in spot_tokens
