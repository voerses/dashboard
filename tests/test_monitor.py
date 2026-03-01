"""AC8: Terminal monitor.

Tests verify:
- Monitor auto-discovers running instances from instances.json
- Shows per-instance data: strategy, exchange, equity, drawdown
- Supports --instance flag for single-instance view
"""

import json
import pytest

from paper_trading.monitor import Monitor


class TestAutoDiscovery:
    """Monitor auto-discovers running instances from instances.json."""

    def test_discovers_running_instances(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        instances = mon.discover()
        running = [i for i in instances if i["status"] == "running"]
        assert len(running) == 2  # s11_binance and s09_kraken

    def test_ignores_stopped_instances_in_discovery(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        instances = mon.discover()
        running_ids = [i["instance_id"] for i in instances if i["status"] == "running"]
        assert "s13_binance" not in running_ids

    def test_discover_returns_all_instances(self, sample_instances_json):
        """Discover returns all instances regardless of status."""
        mon = Monitor(instances_path=sample_instances_json)
        instances = mon.discover()
        assert len(instances) == 3

    def test_missing_instances_file_raises(self):
        with pytest.raises(FileNotFoundError):
            mon = Monitor(instances_path="/nonexistent/instances.json")
            mon.discover()


class TestPerInstanceData:
    """Shows per-instance data: strategy, exchange, equity, drawdown."""

    def test_instance_data_has_strategy(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        data = mon.get_instance_data("s11_binance")
        assert "strategy" in data
        assert data["strategy"] == "s11"

    def test_instance_data_has_exchange(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        data = mon.get_instance_data("s11_binance")
        assert "exchange" in data
        assert data["exchange"] == "binance"

    def test_instance_data_has_equity(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        data = mon.get_instance_data("s11_binance")
        assert "equity" in data

    def test_instance_data_has_drawdown(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        data = mon.get_instance_data("s11_binance")
        assert "drawdown" in data

    def test_unknown_instance_raises(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        with pytest.raises(KeyError):
            mon.get_instance_data("nonexistent_instance")


class TestSingleInstanceView:
    """Supports --instance flag for single-instance view."""

    def test_single_instance_view(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        view = mon.single_instance_view("s09_kraken")
        assert view["instance_id"] == "s09_kraken"
        assert "strategy" in view
        assert "exchange" in view

    def test_single_instance_view_contains_equity(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        view = mon.single_instance_view("s11_binance")
        assert "equity" in view

    def test_single_instance_view_contains_drawdown(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        view = mon.single_instance_view("s11_binance")
        assert "drawdown" in view


class TestMonitorTableOutput:
    """Monitor can produce a summary table of all instances."""

    def test_summary_table_has_all_running_instances(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        table = mon.summary_table()
        # Table should be a list of dicts (one per running instance)
        assert isinstance(table, list)
        ids = [row["instance_id"] for row in table]
        assert "s11_binance" in ids
        assert "s09_kraken" in ids

    def test_summary_table_rows_have_required_fields(self, sample_instances_json):
        mon = Monitor(instances_path=sample_instances_json)
        table = mon.summary_table()
        required = {"instance_id", "strategy", "exchange", "equity", "drawdown"}
        for row in table:
            assert required.issubset(set(row.keys()))
