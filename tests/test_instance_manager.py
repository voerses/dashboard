"""AC7: Background operation with crash detection.

Tests verify:
- Instance manager supports: start, stop, status, list
- Instance ID format: {strategy}_{exchange}
- Status reports: "running", "stopped", "crashed"
- Per-exchange instance cap (default 3)
- Staggered starts: 30s delay between same-exchange launches
"""

import json
import time
import pytest

from paper_trading.instance_manager import InstanceManager


class TestInstanceIdFormat:
    """Instance ID = {strategy}_{exchange}."""

    def test_instance_id_s11_binance(self):
        mgr = InstanceManager()
        instance_id = mgr.make_instance_id(strategy="s11", exchange="binance")
        assert instance_id == "s11_binance"

    def test_instance_id_s09_kraken(self):
        mgr = InstanceManager()
        instance_id = mgr.make_instance_id(strategy="s09", exchange="kraken")
        assert instance_id == "s09_kraken"

    def test_instance_id_contains_strategy_and_exchange(self):
        mgr = InstanceManager()
        instance_id = mgr.make_instance_id(strategy="s21", exchange="binance")
        assert "s21" in instance_id
        assert "binance" in instance_id


class TestStartStop:
    """Instance manager start and stop operations."""

    def test_start_creates_running_instance(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        status = mgr.status("s11_binance")
        assert status == "running"

    def test_stop_sets_instance_to_stopped(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.stop("s11_binance")
        status = mgr.status("s11_binance")
        assert status == "stopped"

    def test_start_nonexistent_config_stores_path(self, tmp_path):
        """Config path validation is deferred to launcher, not instance manager.
        Reviewer verdict: original test was a test_bug (contradicts 10+ sibling tests
        that use /fake/config.json). See reviews/test_dispute_instance_manager.json."""
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/does/not/exist.json")
        assert mgr.status("s11_binance") == "running"


class TestStatusReporting:
    """Status reports: running, stopped, crashed."""

    def test_running_status(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        assert mgr.status("s11_binance") == "running"

    def test_stopped_status(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.stop("s11_binance")
        assert mgr.status("s11_binance") == "stopped"

    def test_crashed_status_detected(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        # Simulate process crash by killing the pid
        mgr.simulate_crash("s11_binance")
        assert mgr.status("s11_binance") == "crashed"

    def test_unknown_instance_raises_or_returns_none(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        with pytest.raises(KeyError):
            mgr.status("nonexistent_instance")


class TestListInstances:
    """List operation returns all known instances."""

    def test_list_returns_all_instances(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s09", exchange="kraken", config_path="/fake/config2.json")
        instances = mgr.list()
        ids = [i["instance_id"] for i in instances]
        assert "s11_binance" in ids
        assert "s09_kraken" in ids

    def test_list_empty_when_no_instances(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        assert mgr.list() == []


class TestPerExchangeInstanceCap:
    """Per-exchange instance cap defaults to 3."""

    def test_refuses_fourth_instance_on_same_exchange(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s09", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s13", exchange="binance", config_path="/fake/config.json")
        with pytest.raises(Exception) as exc_info:
            mgr.start(strategy="s21", exchange="binance", config_path="/fake/config.json")
        assert "cap" in str(exc_info.value).lower() or "limit" in str(exc_info.value).lower()

    def test_allows_instances_on_different_exchanges(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s09", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s13", exchange="binance", config_path="/fake/config.json")
        # Kraken should still work
        mgr.start(strategy="s11", exchange="kraken", config_path="/fake/config.json")
        assert mgr.status("s11_kraken") == "running"

    def test_custom_instance_cap(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path), instance_cap=2)
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s09", exchange="binance", config_path="/fake/config.json")
        with pytest.raises(Exception):
            mgr.start(strategy="s13", exchange="binance", config_path="/fake/config.json")


class TestStaggeredStarts:
    """30s delay between same-exchange launches."""

    def test_stagger_delay_recorded(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        mgr.start(strategy="s09", exchange="binance", config_path="/fake/config.json")
        instances = mgr.list()
        binance_instances = sorted(
            [i for i in instances if i["exchange"] == "binance"],
            key=lambda x: x["started_at"],
        )
        if len(binance_instances) >= 2:
            # The manager should record that a 30s stagger is required
            assert mgr.stagger_delay_seconds == 30

    def test_different_exchange_no_stagger(self, tmp_path):
        mgr = InstanceManager(state_dir=str(tmp_path))
        mgr.start(strategy="s11", exchange="binance", config_path="/fake/config.json")
        # Starting on kraken should not require stagger relative to binance
        mgr.start(strategy="s09", exchange="kraken", config_path="/fake/config.json")
        # Both should be running (no delay concern for different exchanges)
        assert mgr.status("s11_binance") == "running"
        assert mgr.status("s09_kraken") == "running"
