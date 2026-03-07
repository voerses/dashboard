"""CLI interface for the paper trading engine.

Provides start/stop/status/list subcommands for managing paper
trading engine instances.
"""

from __future__ import annotations

import argparse
from typing import Optional

from v3.paper_engine import PaperEngine


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments for the paper engine."""
    parser = argparse.ArgumentParser(
        prog="paper_engine",
        description="Live paper trading engine CLI",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # start
    start_p = sub.add_parser("start", help="Start a paper engine")
    start_p.add_argument("--strategy", required=True)
    start_p.add_argument("--exchange", required=True)
    start_p.add_argument("--tokens", nargs="*", default=[])

    # stop
    stop_p = sub.add_parser("stop", help="Stop a running engine")
    stop_p.add_argument("--strategy", required=True)

    # status
    status_p = sub.add_parser("status", help="Check engine status")
    status_p.add_argument("--strategy", required=True)

    # list
    sub.add_parser("list", help="List all engines")

    return parser.parse_args(argv)


class PaperCLI:
    """Manages multiple paper engine instances."""

    def __init__(self, state_dir: str = "state"):
        self.state_dir = state_dir
        self._engines: dict[str, dict] = {}

    def start(
        self,
        strategy: str,
        exchange: str,
        tokens: list[str] | None = None,
    ) -> None:
        """Start a new paper engine for the given strategy."""
        config = {
            "strategy_id": strategy,
            "tokens": tokens or [],
            "market": "spot",
            "exchange": exchange,
            "timeframe": "1h",
            "capital": 200_000.0,
            "state_dir": self.state_dir,
            "data_dir": "data",
        }
        engine = PaperEngine(config=config)
        engine.start()
        self._engines[strategy] = {
            "engine": engine,
            "strategy_id": strategy,
            "exchange": exchange,
            "status": "running",
        }

    def stop(self, strategy: str) -> None:
        """Stop a running engine.  Raises KeyError if not found."""
        if strategy not in self._engines:
            raise KeyError(f"No engine for strategy '{strategy}'")
        self._engines[strategy]["engine"].stop()
        self._engines[strategy]["status"] = "stopped"

    def status(self, strategy: str) -> str:
        """Return the status of an engine.  Raises KeyError if not found."""
        if strategy not in self._engines:
            raise KeyError(f"No engine for strategy '{strategy}'")
        return self._engines[strategy]["status"]

    def list(self) -> list[dict]:
        """List all engine instances."""
        return [
            {
                "strategy_id": info["strategy_id"],
                "exchange": info["exchange"],
                "status": info["status"],
            }
            for info in self._engines.values()
        ]

    def resume(self, strategy: str, exchange: str) -> None:
        """Resume an engine from persisted state."""
        config = {
            "strategy_id": strategy,
            "tokens": [],
            "market": "spot",
            "exchange": exchange,
            "timeframe": "1h",
            "capital": 200_000.0,
            "state_dir": self.state_dir,
            "data_dir": "data",
        }
        engine = PaperEngine(config=config)
        engine.resume()
        self._engines[strategy] = {
            "engine": engine,
            "strategy_id": strategy,
            "exchange": exchange,
            "status": "running",
        }
