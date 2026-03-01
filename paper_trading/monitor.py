"""
Monitor — Terminal display for paper trading instances.

Auto-discovers running instances and displays per-instance metrics.
Supports single-instance view and aggregate summary table.
"""

import json
import os


class Monitor:
    """Terminal monitor for paper trading instances.

    Reads instances.json to discover running instances, and provides
    per-instance data views and summary tables.
    """

    def __init__(self, instances_path: str = "paper_trading/instances.json"):
        self.instances_path = instances_path
        self._instances = None

    def _load_instances(self):
        """Load instances from JSON file."""
        if not os.path.exists(self.instances_path):
            raise FileNotFoundError(
                f"Instances file not found: {self.instances_path}"
            )

        with open(self.instances_path) as f:
            data = json.load(f)

        self._instances = data.get("instances", [])

    def discover(self) -> list:
        """Discover all instances from instances.json.

        Returns:
            List of all instance dicts (running, stopped, crashed)
        """
        if self._instances is None:
            self._load_instances()
        return list(self._instances)

    def get_instance_data(self, instance_id: str) -> dict:
        """Get detailed data for a specific instance.

        Args:
            instance_id: Instance ID (e.g., 's11_binance')

        Returns:
            Dict with strategy, exchange, equity, drawdown, etc.

        Raises:
            KeyError: If instance not found
        """
        if self._instances is None:
            self._load_instances()

        for inst in self._instances:
            if inst["instance_id"] == instance_id:
                return {
                    "instance_id": instance_id,
                    "strategy": inst["strategy"],
                    "exchange": inst["exchange"],
                    "status": inst["status"],
                    "equity": inst.get("equity", 200000.0),
                    "drawdown": inst.get("drawdown", 0.0),
                    "pid": inst.get("pid"),
                    "api_port": inst.get("api_port"),
                    "started_at": inst.get("started_at"),
                }

        raise KeyError(f"Unknown instance: {instance_id}")

    def single_instance_view(self, instance_id: str) -> dict:
        """Get single-instance view data.

        Args:
            instance_id: Instance ID

        Returns:
            Dict with full instance details for display
        """
        return self.get_instance_data(instance_id)

    def summary_table(self) -> list:
        """Produce a summary table of all running instances.

        Returns:
            List of dicts (one per running instance) with required fields:
            instance_id, strategy, exchange, equity, drawdown
        """
        if self._instances is None:
            self._load_instances()

        table = []
        for inst in self._instances:
            if inst["status"] == "running":
                table.append({
                    "instance_id": inst["instance_id"],
                    "strategy": inst["strategy"],
                    "exchange": inst["exchange"],
                    "equity": inst.get("equity", 200000.0),
                    "drawdown": inst.get("drawdown", 0.0),
                    "status": inst["status"],
                })
        return table
