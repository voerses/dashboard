"""
Equity Tracker — Background performance tracking for paper trading instances.

Writes equity.csv, trades.jsonl, and events.jsonl for each instance.
Tracks dual benchmarks (BTC buy-and-hold, equal-weight universe).
"""

import csv
import json
import os
from datetime import datetime, timezone


class EquityTracker:
    """Tracks equity, trades, events, and benchmarks for a paper trading instance.

    Output files:
    - equity.csv: timestamp, equity, cash, exposure
    - trades.jsonl: structured per-trade records
    - events.jsonl: regime changes, signals, gaps, errors
    """

    def __init__(self, output_dir: str = "paper_trading"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self._equity_rows = []
        self._trade_records = []
        self._event_records = []
        self._benchmark_records = []

    def record_equity(self, timestamp: str, equity: float, cash: float,
                      exposure: float):
        """Record an equity snapshot.

        Args:
            timestamp: ISO 8601 timestamp
            equity: Total equity value
            cash: Cash position
            exposure: Total exposure (equity - cash)
        """
        self._equity_rows.append({
            "timestamp": timestamp,
            "equity": equity,
            "cash": cash,
            "exposure": exposure,
        })

    def record_trade(self, trade: dict):
        """Record a completed trade.

        Args:
            trade: Dict with trade_id, strategy, exchange, pair, side,
                   entry_price, exit_price, size, pnl, fees, etc.
        """
        self._trade_records.append(trade)

    def record_event(self, event_type: str, data: dict):
        """Record an event (regime change, signal, gap, error).

        Args:
            event_type: Event type string (e.g., 'regime_change', 'api_gap')
            data: Event-specific data dict
        """
        self._event_records.append({
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })

    def record_benchmark(self, timestamp: str, btc_value: float,
                         equal_weight_value: float):
        """Record benchmark values for dual-benchmark comparison.

        Args:
            timestamp: ISO 8601 timestamp
            btc_value: BTC price or portfolio value
            equal_weight_value: Equal-weight universe value (normalized)
        """
        self._benchmark_records.append({
            "timestamp": timestamp,
            "btc_value": btc_value,
            "equal_weight_value": equal_weight_value,
        })

    def get_benchmarks(self) -> list:
        """Return all recorded benchmark entries."""
        return list(self._benchmark_records)

    def flush(self):
        """Write all buffered data to disk."""
        self._flush_equity()
        self._flush_trades()
        self._flush_events()

    def _flush_equity(self):
        """Write equity rows to CSV."""
        if not self._equity_rows:
            return

        csv_path = os.path.join(self.output_dir, "equity.csv")
        fieldnames = ["timestamp", "equity", "cash", "exposure"]

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self._equity_rows)

    def _flush_trades(self):
        """Write trade records to JSONL."""
        if not self._trade_records:
            return

        trades_path = os.path.join(self.output_dir, "trades.jsonl")
        with open(trades_path, "w") as f:
            for trade in self._trade_records:
                f.write(json.dumps(trade) + "\n")

    def _flush_events(self):
        """Write event records to JSONL."""
        if not self._event_records:
            return

        events_path = os.path.join(self.output_dir, "events.jsonl")
        with open(events_path, "w") as f:
            for event in self._event_records:
                f.write(json.dumps(event) + "\n")
