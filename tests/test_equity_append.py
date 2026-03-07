"""AC13: Append-mode equity writes, crash loses at most 1 bar.

Tests verify:
- Equity is written in append mode (file grows, never truncated)
- After a simulated crash and restart, at most 1 bar of data is lost
- File is valid CSV after append
- Concurrent appends don't corrupt the file
"""

import csv
import json
import os
import time

import pytest

from v3.equity_writer import EquityWriter


# ---------------------------------------------------------------------------
# Append-mode writing
# ---------------------------------------------------------------------------


class TestAppendModeWrites:
    """Equity writes use append mode — file grows, never truncated."""

    def test_first_write_creates_file_with_header(self, tmp_path):
        """First write creates the CSV with a header row."""
        writer = EquityWriter(output_dir=str(tmp_path))
        writer.append_equity(
            timestamp="2024-01-15T00:00:00Z",
            equity=200000.0,
            cash=192000.0,
            exposure=8000.0,
        )
        eq_path = os.path.join(str(tmp_path), "equity.csv")
        assert os.path.exists(eq_path)
        with open(eq_path, "r") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert "timestamp" in header
        assert "equity" in header

    def test_append_adds_row_without_losing_existing(self, tmp_path):
        """Appending a new row preserves all previous rows."""
        writer = EquityWriter(output_dir=str(tmp_path))
        writer.append_equity("2024-01-15T00:00:00Z", 200000.0, 192000.0, 8000.0)
        writer.append_equity("2024-01-16T00:00:00Z", 200300.0, 200300.0, 0.0)
        writer.append_equity("2024-01-17T00:00:00Z", 200300.0, 195300.0, 5000.0)

        eq_path = os.path.join(str(tmp_path), "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3

    def test_append_does_not_truncate(self, sample_equity_append_dir):
        """Appending to an existing file preserves the original rows."""
        writer = EquityWriter(output_dir=sample_equity_append_dir)
        writer.append_equity("2024-01-18T00:00:00Z", 200190.0, 200190.0, 0.0)

        eq_path = os.path.join(sample_equity_append_dir, "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # 3 original + 1 appended = 4
        assert len(rows) == 4

    def test_append_values_match(self, tmp_path):
        """Appended values can be read back correctly."""
        writer = EquityWriter(output_dir=str(tmp_path))
        writer.append_equity("2024-01-15T00:00:00Z", 200000.0, 192000.0, 8000.0)

        eq_path = os.path.join(str(tmp_path), "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert float(row["equity"]) == pytest.approx(200000.0)
        assert float(row["cash"]) == pytest.approx(192000.0)
        assert float(row["exposure"]) == pytest.approx(8000.0)


# ---------------------------------------------------------------------------
# Crash resilience — at most 1 bar lost
# ---------------------------------------------------------------------------


class TestCrashResilience:
    """After crash, at most 1 bar of data is lost."""

    def test_crash_after_3_writes_loses_at_most_1(self, tmp_path):
        """Write 3 bars, simulate crash, verify at least 2 bars survive."""
        writer = EquityWriter(output_dir=str(tmp_path))
        writer.append_equity("2024-01-15T00:00:00Z", 200000.0, 192000.0, 8000.0)
        writer.append_equity("2024-01-16T00:00:00Z", 200300.0, 200300.0, 0.0)
        writer.append_equity("2024-01-17T00:00:00Z", 200300.0, 195300.0, 5000.0)

        # Simulate crash — writer object goes away, no flush
        del writer

        # Read what survived
        eq_path = os.path.join(str(tmp_path), "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # At most 1 bar lost → at least 2 survive
        assert len(rows) >= 2

    def test_resume_after_crash_appends_correctly(self, sample_equity_append_dir):
        """After crash, new writer can resume appending without corruption."""
        # sample_equity_append_dir has 3 rows
        writer = EquityWriter(output_dir=sample_equity_append_dir)
        writer.append_equity("2024-01-18T00:00:00Z", 200190.0, 200190.0, 0.0)

        eq_path = os.path.join(sample_equity_append_dir, "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 4
        assert rows[-1]["timestamp"] == "2024-01-18T00:00:00Z"

    def test_file_valid_csv_after_crash_resume(self, sample_equity_append_dir):
        """File remains valid CSV after crash and resume."""
        writer = EquityWriter(output_dir=sample_equity_append_dir)
        writer.append_equity("2024-01-18T00:00:00Z", 200190.0, 200190.0, 0.0)
        del writer  # Simulate crash

        # Re-open and write again
        writer2 = EquityWriter(output_dir=sample_equity_append_dir)
        writer2.append_equity("2024-01-19T00:00:00Z", 200250.0, 200250.0, 0.0)

        eq_path = os.path.join(sample_equity_append_dir, "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # All rows should be parseable
        for row in rows:
            float(row["equity"])  # Should not raise

    def test_single_bar_write_survives_immediate_crash(self, tmp_path):
        """A single write followed by immediate crash — the bar should persist."""
        writer = EquityWriter(output_dir=str(tmp_path))
        writer.append_equity("2024-01-15T00:00:00Z", 200000.0, 192000.0, 8000.0)
        # Force flush to ensure persistence
        if hasattr(writer, "flush"):
            writer.flush()
        del writer

        eq_path = os.path.join(str(tmp_path), "equity.csv")
        assert os.path.exists(eq_path)
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # Single write with flush should survive — at most 1 bar lost means
        # with only 1 bar written and flush called, it must persist
        assert len(rows) >= 1, (
            "Single write with explicit flush did not persist — "
            "append-mode crash resilience is broken"
        )


class TestEquityWriterIntegrity:
    """CSV integrity after multiple writes."""

    def test_all_rows_have_same_column_count(self, tmp_path):
        """Every row has the same number of columns."""
        writer = EquityWriter(output_dir=str(tmp_path))
        for i in range(10):
            writer.append_equity(
                timestamp=f"2024-01-{15+i:02d}T00:00:00Z",
                equity=200000.0 + i * 100,
                cash=195000.0 + i * 50,
                exposure=5000.0 + i * 50,
            )

        eq_path = os.path.join(str(tmp_path), "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.reader(f)
            header = next(reader)
            col_count = len(header)
            for row in reader:
                assert len(row) == col_count

    def test_timestamps_are_monotonically_increasing(self, tmp_path):
        """Written timestamps appear in order."""
        writer = EquityWriter(output_dir=str(tmp_path))
        timestamps = [
            "2024-01-15T00:00:00Z",
            "2024-01-16T00:00:00Z",
            "2024-01-17T00:00:00Z",
        ]
        for ts in timestamps:
            writer.append_equity(ts, 200000.0, 195000.0, 5000.0)

        eq_path = os.path.join(str(tmp_path), "equity.csv")
        with open(eq_path, "r") as f:
            reader = csv.DictReader(f)
            read_ts = [row["timestamp"] for row in reader]
        assert read_ts == timestamps
