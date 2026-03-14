"""Tests for v4/manifest.py — versioned promotion log and integrity checks.

Tests verify:
  - Manifest entries are written correctly with SHA-256 hashes
  - read_manifest returns entries in order
  - verify_integrity detects tampered files
  - verify_integrity passes for untouched files
  - get_data_boundary_at returns correct boundary for point-in-time queries
  - Point-in-time loading (load_token_data_at) returns data trimmed to boundary
  - Full roundtrip: promote → manifest → verify → point-in-time load
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

import pandas as pd
import pytest

from v4.data_loader import (
    historical_path,
    live_path,
    load_token_data_at,
)
from v4.manifest import (
    write_manifest_entry,
    read_manifest,
    verify_integrity,
    get_data_boundary_at,
    manifest_path,
    _hash_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(start_ts_ms: int, n: int, interval_ms: int = 3_600_000) -> pd.DataFrame:
    """Create a DataFrame with DatetimeIndex."""
    timestamps = pd.to_datetime(
        [start_ts_ms + i * interval_ms for i in range(n)], unit="ms"
    )
    return pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 + i for i in range(n)],
        "close": [102.0 + i for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
    }, index=timestamps)


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ===================================================================
# Test: write_manifest_entry
# ===================================================================

class TestWriteManifestEntry:
    """Manifest entries are written with correct fields."""

    def test_writes_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a historical parquet
            df = _make_df(0, 10)
            _write(df, historical_path("BTC", "perp", tmpdir))

            entry = write_manifest_entry(
                token="BTC",
                market="perp",
                bars_promoted=5,
                ts_start=df.index[5],
                ts_end=df.index[9],
                hist_total_bars=10,
                data_dir=tmpdir,
            )

            assert entry["token"] == "BTC"
            assert entry["market"] == "perp"
            assert entry["bars_promoted"] == 5
            assert entry["hist_total_bars"] == 10
            assert entry["hist_sha256"] is not None
            assert len(entry["hist_sha256"]) == 64  # SHA-256 hex length
            assert "promoted_at" in entry

    def test_creates_manifest_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            _write(df, historical_path("BTC", "perp", tmpdir))

            write_manifest_entry(
                "BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir
            )

            mf = manifest_path("perp", tmpdir)
            assert mf.exists()

            lines = mf.read_text().strip().split("\n")
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["token"] == "BTC"

    def test_appends_multiple_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            _write(df, historical_path("BTC", "perp", tmpdir))

            write_manifest_entry("BTC", "perp", 5, df.index[0], df.index[4], 5, tmpdir)
            write_manifest_entry("BTC", "perp", 5, df.index[5], df.index[9], 10, tmpdir)

            entries = read_manifest("perp", tmpdir)
            assert len(entries) == 2
            assert entries[0]["hist_total_bars"] == 5
            assert entries[1]["hist_total_bars"] == 10


# ===================================================================
# Test: read_manifest
# ===================================================================

class TestReadManifest:
    """read_manifest returns entries correctly."""

    def test_empty_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = read_manifest("perp", tmpdir)
            assert entries == []

    def test_reads_multiple_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            _write(df, historical_path("BTC", "perp", tmpdir))
            _write(df, historical_path("ETH", "perp", tmpdir))

            write_manifest_entry("BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir)
            write_manifest_entry("ETH", "perp", 10, df.index[0], df.index[9], 10, tmpdir)

            entries = read_manifest("perp", tmpdir)
            assert len(entries) == 2
            tokens = {e["token"] for e in entries}
            assert tokens == {"BTC", "ETH"}


# ===================================================================
# Test: verify_integrity
# ===================================================================

class TestVerifyIntegrity:
    """verify_integrity detects tampered historical files."""

    def test_passes_for_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            _write(df, historical_path("BTC", "perp", tmpdir))
            write_manifest_entry("BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir)

            ok, msg = verify_integrity("BTC", "perp", tmpdir)
            assert ok is True
            assert "OK" in msg

    def test_fails_for_tampered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            hist_pq = historical_path("BTC", "perp", tmpdir)
            _write(df, hist_pq)
            write_manifest_entry("BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir)

            # Tamper with the file
            df_tampered = _make_df(0, 11)  # different data
            df_tampered.to_parquet(hist_pq)

            ok, msg = verify_integrity("BTC", "perp", tmpdir)
            assert ok is False
            assert "mismatch" in msg

    def test_fails_for_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            hist_pq = historical_path("BTC", "perp", tmpdir)
            _write(df, hist_pq)
            write_manifest_entry("BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir)

            # Delete historical file
            os.remove(hist_pq)

            ok, msg = verify_integrity("BTC", "perp", tmpdir)
            assert ok is False
            assert "missing" in msg

    def test_passes_when_untracked(self):
        """No manifest entries means the token is untracked — passes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ok, msg = verify_integrity("BTC", "perp", tmpdir)
            assert ok is True
            assert "untracked" in msg

    def test_allow_rebuild_passes_on_mismatch(self):
        """allow_rebuild=True tolerates hash mismatch from rebuild."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            hist_pq = historical_path("BTC", "perp", tmpdir)
            _write(df, hist_pq)
            write_manifest_entry("BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir)

            # Tamper (simulating rebuild)
            _make_df(0, 11).to_parquet(hist_pq)

            ok, msg = verify_integrity("BTC", "perp", tmpdir, allow_rebuild=True)
            assert ok is True
            assert "rebuild" in msg.lower() or "warning" in msg.lower()


# ===================================================================
# Test: read_manifest robustness
# ===================================================================

class TestReadManifestRobustness:
    """read_manifest handles corrupt JSONL lines gracefully."""

    def test_skips_corrupt_json_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 10)
            _write(df, historical_path("BTC", "perp", tmpdir))
            write_manifest_entry("BTC", "perp", 10, df.index[0], df.index[9], 10, tmpdir)

            # Inject a corrupt line
            mf = manifest_path("perp", tmpdir)
            with open(mf, "a") as f:
                f.write("THIS IS NOT JSON\n")

            # Write another valid entry after the corrupt one
            write_manifest_entry("ETH", "perp", 5, df.index[0], df.index[4], 5, tmpdir)

            entries = read_manifest("perp", tmpdir)
            assert len(entries) == 2  # corrupt line skipped
            tokens = {e["token"] for e in entries}
            assert tokens == {"BTC", "ETH"}


# ===================================================================
# Test: _hash_file safety
# ===================================================================

class TestHashFile:
    """_hash_file returns None for missing files."""

    def test_returns_none_for_missing(self):
        assert _hash_file(Path("/nonexistent/file.parquet")) is None

    def test_returns_hash_for_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test.bin"
            p.write_bytes(b"hello")
            h = _hash_file(p)
            assert h is not None
            assert len(h) == 64  # SHA-256 hex


# ===================================================================
# Test: get_data_boundary_at
# ===================================================================

class TestGetDataBoundaryAt:
    """get_data_boundary_at finds the correct data boundary."""

    def test_returns_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 100)
            _write(df, historical_path("BTC", "perp", tmpdir))

            # Promotion 1: bars 0-49
            write_manifest_entry("BTC", "perp", 50, df.index[0], df.index[49], 50, tmpdir)
            # Promotion 2: bars 50-99
            write_manifest_entry("BTC", "perp", 50, df.index[50], df.index[99], 100, tmpdir)

            entries = read_manifest("perp", tmpdir)

            # Query at a time after promotion 1 but before promotion 2
            after_p1 = entries[0]["promoted_at"]
            before_p2 = entries[1]["promoted_at"]

            # Get boundary at time of promotion 1
            boundary = get_data_boundary_at(
                "BTC", "perp",
                datetime.fromisoformat(after_p1),
                tmpdir,
            )
            assert boundary is not None
            assert boundary == df.index[49]

    def test_returns_none_when_no_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            boundary = get_data_boundary_at(
                "BTC", "perp",
                datetime.now(timezone.utc),
                tmpdir,
            )
            assert boundary is None

    def test_returns_latest_before_as_of(self):
        """When as_of is after all promotions, returns the latest boundary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 100)
            _write(df, historical_path("BTC", "perp", tmpdir))

            write_manifest_entry("BTC", "perp", 50, df.index[0], df.index[49], 50, tmpdir)
            write_manifest_entry("BTC", "perp", 50, df.index[50], df.index[99], 100, tmpdir)

            # Query well in the future
            future = datetime(2099, 1, 1, tzinfo=timezone.utc)
            boundary = get_data_boundary_at("BTC", "perp", future, tmpdir)
            assert boundary == df.index[99]


# ===================================================================
# Test: load_token_data_at (point-in-time loading)
# ===================================================================

class TestLoadTokenDataAt:
    """Point-in-time data loading using manifest boundaries."""

    def test_trims_to_manifest_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Historical has 100 bars
            df = _make_df(0, 100)
            _write(df, historical_path("BTC", "perp", tmpdir))

            # Manifest says at promotion time, only 50 bars existed
            write_manifest_entry("BTC", "perp", 50, df.index[0], df.index[49], 50, tmpdir)

            entries = read_manifest("perp", tmpdir)
            as_of = datetime.fromisoformat(entries[0]["promoted_at"])

            result = load_token_data_at("BTC", "perp", pd.Timestamp(as_of), tmpdir)
            assert result is not None
            assert len(result) == 50
            assert result.index[-1] == df.index[49]

    def test_falls_back_to_as_of_without_manifest(self):
        """Without manifest, trims to as_of timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 100)
            _write(df, historical_path("BTC", "perp", tmpdir))

            # as_of at bar 30
            as_of = df.index[30]
            result = load_token_data_at("BTC", "perp", as_of, tmpdir)
            assert result is not None
            assert len(result) == 31  # bars 0-30 inclusive
            assert result.index[-1] == df.index[30]

    def test_returns_none_for_missing_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_token_data_at(
                "BTC", "perp", pd.Timestamp.now(), tmpdir
            )
            assert result is None

    def test_returns_none_when_as_of_before_all_data(self):
        """If as_of is before all data, returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Data starts at hour 100
            df = _make_df(100 * 3_600_000, 50)
            _write(df, historical_path("BTC", "perp", tmpdir))

            # as_of is before all data
            as_of = pd.Timestamp("1970-01-01")
            result = load_token_data_at("BTC", "perp", as_of, tmpdir)
            assert result is None


# ===================================================================
# Test: Full roundtrip — promote → manifest → verify → load
# ===================================================================

class TestFullRoundtrip:
    """End-to-end test: promote live data, check manifest, verify, load at point-in-time."""

    def test_promotion_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from tools.promote_live import promote_token

            data_dir = Path(tmpdir)

            # Historical: bars 0-49
            hist = _make_df(0, 50)
            _write(hist, historical_path("BTC", "perp", tmpdir))

            # Live: bars 50-59
            live = _make_df(50 * 3_600_000, 10)
            _write(live, live_path("BTC", "perp", tmpdir))

            # Promote
            rec = promote_token("BTC", "perp", data_dir)
            assert rec is not None
            assert rec["bars_promoted"] == 10

            # Manifest was written
            entries = read_manifest("perp", tmpdir)
            assert len(entries) == 1
            assert entries[0]["token"] == "BTC"
            assert entries[0]["bars_promoted"] == 10
            assert entries[0]["hist_total_bars"] == 60

            # Integrity check passes
            ok, msg = verify_integrity("BTC", "perp", tmpdir)
            assert ok is True

            # Point-in-time load returns full 60 bars
            future = datetime(2099, 1, 1, tzinfo=timezone.utc)
            result = load_token_data_at("BTC", "perp", pd.Timestamp(future), tmpdir)
            assert result is not None
            assert len(result) == 60
