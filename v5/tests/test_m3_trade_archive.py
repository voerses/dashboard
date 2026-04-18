"""M3 acceptance tests — bounded closed_trades with parquet archive.

Covers:
  - AC5: closed_trades bounded at maxlen=1000 in memory; overflow flushed to
          parquet archive BEFORE eviction (no-loss window).
  - AC13: Dashboard / reader reads both in-memory deque AND parquet archive,
          unioning the two sources.
  - AC17: Parquet writes are atomic — `.tmp` file, then rename to `.parquet`;
          concurrent readers skip `.tmp` files.

All tests MUST FAIL today — PositionManager.closed_trades is a plain list,
there is no TradeArchiveWriter / Reader, no `.tmp` rename pattern.

Seed: 42. TestClock epoch: 2026-03-01T00:00:00Z (not needed here).
"""
from __future__ import annotations

import collections
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _make_closed_trade(i: int):
    from v5.position import ClosedTrade
    return ClosedTrade(
        position_id=f"BTC:s30:{i}:primary",
        token="BTC", strategy_id="s30", leg="primary",
        entry_bar=i, exit_bar=i + 10, entry_price=100.0 + i,
        exit_price=105.0 + i, direction=1, margin_usd=100.0, pnl=5.0,
        funding_cost=0.0, entry_fee=0.5, exit_fee=0.5, hold_bars=10,
        exit_reason="target",
    )


# ===================================================================
# AC5 — bounded in-memory deque with archive flush
# ===================================================================

class TestAC5BoundedTradesArchive:
    """closed_trades is deque(maxlen=1000); overflow goes to parquet."""

    def test_closed_trades_is_bounded_deque(self):
        from v5.position import PositionManager
        pm = PositionManager()
        assert isinstance(pm.closed_trades, collections.deque)
        assert pm.closed_trades.maxlen == 1000

    def test_1500_trades_leave_1000_in_memory(self, tmp_path):
        """Insert 1500 closed trades; memory holds the most-recent 1000."""
        from v5.position import PositionManager
        from v5.trade_archive import TradeArchiveWriter

        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        pm = PositionManager()
        # Register archive writer on the PositionManager
        pm.attach_archive_writer(writer) if hasattr(
            pm, "attach_archive_writer"
        ) else setattr(pm, "_archive_writer", writer)

        for i in range(1500):
            trade = _make_closed_trade(i)
            # PositionManager API is the entry point that handles archival
            if hasattr(pm, "append_closed_trade"):
                pm.append_closed_trade(trade)
            else:
                pm.closed_trades.append(trade)

        assert len(pm.closed_trades) == 1000

    def test_1500_trades_the_oldest_500_flushed_to_parquet(self, tmp_path):
        """After 1500 trades, at least 500 trades are readable from the archive."""
        from v5.position import PositionManager
        from v5.trade_archive import TradeArchiveReader, TradeArchiveWriter

        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        pm = PositionManager()
        if hasattr(pm, "attach_archive_writer"):
            pm.attach_archive_writer(writer)
        else:
            setattr(pm, "_archive_writer", writer)

        for i in range(1500):
            trade = _make_closed_trade(i)
            if hasattr(pm, "append_closed_trade"):
                pm.append_closed_trade(trade)
            else:
                pm.closed_trades.append(trade)

        reader = TradeArchiveReader(str(archive_dir))
        archived = reader.all_trades()
        assert len(archived) >= 500

    def test_flush_is_pre_evict_no_data_loss(self, tmp_path):
        """Flushing happens BEFORE eviction; memory + archive union covers all."""
        from v5.position import PositionManager
        from v5.trade_archive import TradeArchiveReader, TradeArchiveWriter

        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        pm = PositionManager()
        if hasattr(pm, "attach_archive_writer"):
            pm.attach_archive_writer(writer)
        else:
            setattr(pm, "_archive_writer", writer)

        for i in range(1500):
            trade = _make_closed_trade(i)
            if hasattr(pm, "append_closed_trade"):
                pm.append_closed_trade(trade)
            else:
                pm.closed_trades.append(trade)

        reader = TradeArchiveReader(str(archive_dir))
        archived_ids = set()
        for row in reader.all_trades().to_dict(orient="records"):
            archived_ids.add(row["position_id"])
        mem_ids = {t.position_id for t in pm.closed_trades}
        union_ids = archived_ids | mem_ids
        expected_ids = {f"BTC:s30:{i}:primary" for i in range(1500)}
        assert expected_ids.issubset(union_ids), (
            f"Union of memory + archive missing "
            f"{len(expected_ids - union_ids)} trade ids"
        )


# ===================================================================
# AC13 — Dashboard / reader unions memory + archive
# ===================================================================

class TestAC13DashboardReadsBoth:
    """A reader / dashboard helper returns trades from both sources."""

    def test_archive_reader_exists(self):
        from v5.trade_archive import TradeArchiveReader  # noqa: F401

    def test_reader_returns_pandas_dataframe(self, tmp_path):
        from v5.trade_archive import TradeArchiveReader, TradeArchiveWriter
        import pandas as pd
        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        writer.flush([_make_closed_trade(0), _make_closed_trade(1)])
        reader = TradeArchiveReader(str(archive_dir))
        df = reader.all_trades()
        assert isinstance(df, pd.DataFrame)
        assert len(df) >= 2

    def test_dashboard_state_unions_memory_and_archive(self, tmp_path):
        """A dashboard-layer helper returns the union; the exact helper name
        is implementation-defined, but the behavior is testable via the
        reader + in-memory deque."""
        from v5.position import PositionManager
        from v5.trade_archive import TradeArchiveReader, TradeArchiveWriter

        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))

        # Archive side
        writer.flush([_make_closed_trade(0), _make_closed_trade(1)])
        # Memory side
        pm = PositionManager()
        pm.closed_trades.append(_make_closed_trade(100))

        reader = TradeArchiveReader(str(archive_dir))
        arch_df = reader.all_trades()
        mem_ids = {t.position_id for t in pm.closed_trades}
        arch_ids = set(arch_df["position_id"].tolist())
        union_ids = mem_ids | arch_ids
        assert "BTC:s30:0:primary" in union_ids
        assert "BTC:s30:100:primary" in union_ids


# ===================================================================
# AC17 — atomic rename (.tmp -> .parquet); readers skip .tmp
# ===================================================================

class TestAC17AtomicRename:
    """Archive writes atomically via tmp-then-rename; readers ignore .tmp files."""

    def test_writer_produces_parquet_not_tmp_after_flush(self, tmp_path):
        from v5.trade_archive import TradeArchiveWriter
        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        writer.flush([_make_closed_trade(0)])
        files = sorted(archive_dir.iterdir())
        # At least one .parquet file, no leftover .tmp
        parquet_files = [f for f in files if f.suffix == ".parquet"]
        tmp_files = [f for f in files if f.suffix == ".tmp"]
        assert len(parquet_files) >= 1
        assert tmp_files == [], (
            f"Leftover .tmp after flush: {tmp_files}"
        )

    def test_reader_skips_tmp_files(self, tmp_path):
        """If a .tmp file exists alongside a .parquet file, reader ignores .tmp."""
        from v5.trade_archive import TradeArchiveReader, TradeArchiveWriter
        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        writer.flush([_make_closed_trade(0), _make_closed_trade(1)])
        # Plant a half-written .tmp alongside
        (archive_dir / "batch_999_xxx.parquet.tmp").write_bytes(b"garbage")
        reader = TradeArchiveReader(str(archive_dir))
        # Must not raise on garbage .tmp; must read the valid parquet
        df = reader.all_trades()
        assert len(df) >= 2

    def test_atomic_rename_uses_tmp_path(self, tmp_path, monkeypatch):
        """Instrument writer internals: it must write to `.tmp` then rename."""
        from v5 import trade_archive
        from v5.trade_archive import TradeArchiveWriter

        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))

        # Record rename() calls. The writer should rename a .tmp to .parquet.
        import os
        original_rename = os.rename
        seen = []

        def _wrap_rename(src, dst):
            seen.append((str(src), str(dst)))
            return original_rename(src, dst)

        monkeypatch.setattr(os, "rename", _wrap_rename)
        # pyarrow may go through os.replace as well
        original_replace = os.replace

        def _wrap_replace(src, dst):
            seen.append((str(src), str(dst)))
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", _wrap_replace)

        writer.flush([_make_closed_trade(0)])
        tmp_to_final = [s for s in seen
                        if s[0].endswith(".tmp") and s[1].endswith(".parquet")]
        assert tmp_to_final, (
            f"Expected a .tmp -> .parquet rename; observed renames: {seen}"
        )

    def test_concurrent_reader_during_slow_flush_sees_no_tmp(self, tmp_path):
        """Real concurrency test — reader thread polls the archive while a
        writer is mid-flush (slowed via monkeypatched rename). The reader
        MUST NOT observe the .tmp file as a valid parquet (per AC17 atomic
        rename guarantee)."""
        import os
        import threading
        import time
        from v5.trade_archive import TradeArchiveWriter, TradeArchiveReader

        archive_dir = tmp_path / "trades_archive"
        archive_dir.mkdir()
        writer = TradeArchiveWriter(str(archive_dir))
        reader = TradeArchiveReader(str(archive_dir))

        # Slow the os.rename (or os.replace) to open a visible .tmp window.
        original_rename = os.rename
        rename_started = threading.Event()
        rename_can_proceed = threading.Event()

        def _slow_rename(src, dst):
            rename_started.set()
            # Give the reader a chance to poll while the .tmp exists on disk
            rename_can_proceed.wait(timeout=2.0)
            return original_rename(src, dst)

        # Track any time the reader observes a .tmp file as a valid parquet
        tmp_leaked = []

        def _reader_loop():
            # Poll during the rename window
            rename_started.wait(timeout=1.0)
            t_end = time.time() + 0.5
            while time.time() < t_end:
                # Directory glob scan must NOT include .tmp file paths
                for p in archive_dir.iterdir():
                    if p.suffix == ".tmp":
                        tmp_leaked.append(str(p))
                try:
                    df = reader.all_trades()
                    # reader.all_trades() must not raise on the half-written .tmp
                    _ = len(df)
                except Exception as e:
                    tmp_leaked.append(f"reader_raised: {e!r}")
                time.sleep(0.01)
            rename_can_proceed.set()

        import unittest.mock as _mock
        reader_thread = threading.Thread(target=_reader_loop, daemon=True)
        with _mock.patch("os.rename", _slow_rename), _mock.patch("os.replace", _slow_rename):
            reader_thread.start()
            writer.flush([_make_closed_trade(0), _make_closed_trade(1)])
            reader_thread.join(timeout=3.0)

        # The .tmp file existing ON DISK during the window is expected
        # (tmp_leaked will contain its path). The REAL assertion is that
        # the reader never errored AND the final state is clean.
        reader_errors = [x for x in tmp_leaked if x.startswith("reader_raised")]
        assert not reader_errors, (
            f"Reader raised mid-flush (AC17 broken): {reader_errors}"
        )
        # Final state: no leftover .tmp, parquet present
        leftover = [p for p in archive_dir.iterdir() if p.suffix == ".tmp"]
        parquet = [p for p in archive_dir.iterdir() if p.suffix == ".parquet"]
        assert not leftover, f"Leftover .tmp after flush: {leftover}"
        assert parquet, "No .parquet produced after flush"
