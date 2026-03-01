"""AC9: Performance tracking with dual benchmarks.

Tests verify:
- Equity tracker writes equity.csv with columns: timestamp, equity, cash, exposure
- Writes trades.jsonl with structured trade records
- Writes events.jsonl with event records
- Tracks BTC benchmark and equal-weight benchmark
- Regime tagging in events
- Gap markers on API failure
"""

import csv
import json
import os
import pytest

from paper_trading.equity_tracker import EquityTracker


class TestEquityCsvOutput:
    """equity.csv must have correct columns and data."""

    def test_equity_csv_created(self, tmp_path, sample_equity_rows):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for row in sample_equity_rows:
            tracker.record_equity(
                timestamp=row["timestamp"],
                equity=row["equity"],
                cash=row["cash"],
                exposure=row["exposure"],
            )
        tracker.flush()
        csv_path = os.path.join(str(tmp_path), "equity.csv")
        assert os.path.exists(csv_path)

    def test_equity_csv_columns(self, tmp_path, sample_equity_rows):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for row in sample_equity_rows:
            tracker.record_equity(
                timestamp=row["timestamp"],
                equity=row["equity"],
                cash=row["cash"],
                exposure=row["exposure"],
            )
        tracker.flush()
        csv_path = os.path.join(str(tmp_path), "equity.csv")
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames
        assert "timestamp" in columns
        assert "equity" in columns
        assert "cash" in columns
        assert "exposure" in columns

    def test_equity_csv_row_count(self, tmp_path, sample_equity_rows):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for row in sample_equity_rows:
            tracker.record_equity(
                timestamp=row["timestamp"],
                equity=row["equity"],
                cash=row["cash"],
                exposure=row["exposure"],
            )
        tracker.flush()
        csv_path = os.path.join(str(tmp_path), "equity.csv")
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == len(sample_equity_rows)

    def test_equity_values_match_input(self, tmp_path, sample_equity_rows):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for row in sample_equity_rows:
            tracker.record_equity(
                timestamp=row["timestamp"],
                equity=row["equity"],
                cash=row["cash"],
                exposure=row["exposure"],
            )
        tracker.flush()
        csv_path = os.path.join(str(tmp_path), "equity.csv")
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert float(rows[0]["equity"]) == pytest.approx(sample_equity_rows[0]["equity"])


class TestTradesJsonl:
    """trades.jsonl with structured trade records."""

    def test_trades_file_created(self, tmp_path, sample_trade_records):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for trade in sample_trade_records:
            tracker.record_trade(trade)
        tracker.flush()
        trades_path = os.path.join(str(tmp_path), "trades.jsonl")
        assert os.path.exists(trades_path)

    def test_trades_are_valid_jsonl(self, tmp_path, sample_trade_records):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for trade in sample_trade_records:
            tracker.record_trade(trade)
        tracker.flush()
        trades_path = os.path.join(str(tmp_path), "trades.jsonl")
        with open(trades_path, "r") as f:
            lines = f.readlines()
        for line in lines:
            parsed = json.loads(line.strip())
            assert "trade_id" in parsed

    def test_trade_count_matches(self, tmp_path, sample_trade_records):
        tracker = EquityTracker(output_dir=str(tmp_path))
        for trade in sample_trade_records:
            tracker.record_trade(trade)
        tracker.flush()
        trades_path = os.path.join(str(tmp_path), "trades.jsonl")
        with open(trades_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == len(sample_trade_records)

    def test_trade_record_has_required_fields(self, tmp_path, sample_trade_records):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_trade(sample_trade_records[0])
        tracker.flush()
        trades_path = os.path.join(str(tmp_path), "trades.jsonl")
        with open(trades_path, "r") as f:
            record = json.loads(f.readline().strip())
        required = {"trade_id", "strategy", "exchange", "pair", "side",
                     "entry_price", "exit_price", "size", "pnl", "fees"}
        assert required.issubset(set(record.keys()))


class TestEventsJsonl:
    """events.jsonl with event records."""

    def test_events_file_created(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_event(event_type="regime_change", data={"regime": "uptrend"})
        tracker.flush()
        events_path = os.path.join(str(tmp_path), "events.jsonl")
        assert os.path.exists(events_path)

    def test_event_record_is_valid_json(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_event(event_type="regime_change", data={"regime": "uptrend"})
        tracker.flush()
        events_path = os.path.join(str(tmp_path), "events.jsonl")
        with open(events_path, "r") as f:
            record = json.loads(f.readline().strip())
        assert "event_type" in record
        assert record["event_type"] == "regime_change"


class TestBenchmarks:
    """Tracks BTC benchmark and equal-weight benchmark."""

    def test_btc_benchmark_tracked(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_benchmark(
            timestamp="2024-01-15T00:00:00Z",
            btc_value=42000.0,
            equal_weight_value=1.05,
        )
        tracker.flush()
        benchmarks = tracker.get_benchmarks()
        assert len(benchmarks) > 0
        assert "btc_value" in benchmarks[0]

    def test_equal_weight_benchmark_tracked(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_benchmark(
            timestamp="2024-01-15T00:00:00Z",
            btc_value=42000.0,
            equal_weight_value=1.05,
        )
        tracker.flush()
        benchmarks = tracker.get_benchmarks()
        assert "equal_weight_value" in benchmarks[0]


class TestRegimeTagging:
    """Regime tagging in events."""

    def test_regime_change_event_tagged(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_event(
            event_type="regime_change",
            data={"regime": "downtrend", "token": "BTC/USDT"},
        )
        tracker.flush()
        events_path = os.path.join(str(tmp_path), "events.jsonl")
        with open(events_path, "r") as f:
            record = json.loads(f.readline().strip())
        assert record["data"]["regime"] == "downtrend"

    def test_trade_event_includes_regime(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_event(
            event_type="trade_entry",
            data={"pair": "BTC/USDT", "regime": "uptrend", "price": 42000.0},
        )
        tracker.flush()
        events_path = os.path.join(str(tmp_path), "events.jsonl")
        with open(events_path, "r") as f:
            record = json.loads(f.readline().strip())
        assert "regime" in record["data"]


class TestGapMarkers:
    """Gap markers on API failure."""

    def test_api_gap_marker_recorded(self, tmp_path):
        tracker = EquityTracker(output_dir=str(tmp_path))
        tracker.record_event(
            event_type="api_gap",
            data={
                "exchange": "binance",
                "gap_start": "2024-01-15T10:00:00Z",
                "gap_end": "2024-01-15T10:05:00Z",
                "reason": "connection_timeout",
            },
        )
        tracker.flush()
        events_path = os.path.join(str(tmp_path), "events.jsonl")
        with open(events_path, "r") as f:
            record = json.loads(f.readline().strip())
        assert record["event_type"] == "api_gap"
        assert "gap_start" in record["data"]
        assert "gap_end" in record["data"]
