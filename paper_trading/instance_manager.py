"""
Instance Manager — Paper trading instance lifecycle management.

Manages the start/stop/status lifecycle of Freqtrade paper trading instances.
Each instance is a strategy+exchange combo with its own state tracking.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import List, Optional


class InstanceManager:
    """Manages paper trading instance lifecycle.

    Each instance is identified by {strategy}_{exchange}.
    Tracks status (running/stopped/crashed), enforces per-exchange caps,
    and records staggered start timestamps.
    """

    def __init__(self, state_dir: str = "paper_trading",
                 instance_cap: int = 3):
        self.state_dir = state_dir
        self.instance_cap = instance_cap
        self.stagger_delay_seconds = 30
        self._instances = {}  # instance_id -> instance dict

    def make_instance_id(self, strategy: str, exchange: str) -> str:
        """Generate instance ID from strategy and exchange."""
        return f"{strategy}_{exchange}"

    def start(self, strategy: str, exchange: str,
              config_path: str) -> str:
        """Start a new paper trading instance.

        Args:
            strategy: Strategy name (e.g., 's11')
            exchange: Exchange name (e.g., 'binance')
            config_path: Path to Freqtrade config JSON (stored as metadata)

        Returns:
            Instance ID

        Raises:
            ValueError: If instance cap exceeded for this exchange
        """
        exchange = exchange.lower()
        instance_id = self.make_instance_id(strategy, exchange)

        # Check per-exchange cap
        running_on_exchange = sum(
            1 for inst in self._instances.values()
            if inst["exchange"] == exchange and inst["status"] == "running"
        )
        if running_on_exchange >= self.instance_cap:
            raise ValueError(
                f"Instance cap reached for {exchange} "
                f"(limit: {self.instance_cap} running instances)"
            )

        # Record instance
        now = datetime.now(timezone.utc).isoformat()
        self._instances[instance_id] = {
            "instance_id": instance_id,
            "strategy": strategy,
            "exchange": exchange,
            "config_path": config_path,
            "status": "running",
            "pid": os.getpid(),  # Use current process as placeholder
            "started_at": now,
        }

        # Persist state
        self._save_state()

        return instance_id

    def stop(self, instance_id: str):
        """Stop a running instance.

        Args:
            instance_id: Instance ID to stop

        Raises:
            KeyError: If instance doesn't exist
        """
        if instance_id not in self._instances:
            raise KeyError(f"Unknown instance: {instance_id}")

        self._instances[instance_id]["status"] = "stopped"
        self._instances[instance_id]["pid"] = None
        self._save_state()

    def status(self, instance_id: str) -> str:
        """Get status of an instance.

        Returns:
            'running', 'stopped', or 'crashed'

        Raises:
            KeyError: If instance doesn't exist
        """
        if instance_id not in self._instances:
            raise KeyError(f"Unknown instance: {instance_id}")

        return self._instances[instance_id]["status"]

    def list(self) -> List[dict]:
        """List all known instances.

        Returns:
            List of instance dicts with instance_id, exchange, status, etc.
        """
        return list(self._instances.values())

    def simulate_crash(self, instance_id: str):
        """Simulate a process crash for testing.

        Args:
            instance_id: Instance ID to crash
        """
        if instance_id not in self._instances:
            raise KeyError(f"Unknown instance: {instance_id}")

        self._instances[instance_id]["status"] = "crashed"
        self._instances[instance_id]["pid"] = None
        self._save_state()

    def _save_state(self):
        """Persist instance state to disk."""
        os.makedirs(self.state_dir, exist_ok=True)
        state_path = os.path.join(self.state_dir, "instances.json")
        data = {
            "instances": list(self._instances.values()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(state_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_state(self):
        """Load instance state from disk."""
        state_path = os.path.join(self.state_dir, "instances.json")
        if os.path.exists(state_path):
            with open(state_path) as f:
                data = json.load(f)
            for inst in data.get("instances", []):
                self._instances[inst["instance_id"]] = inst
