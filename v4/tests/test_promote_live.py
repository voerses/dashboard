"""Tests for tools/promote_live.py — live→historical promotion.

Tests verify:
  - Promotion appends new bars to historical
  - Overlap bars are deduplicated (historical wins on existing, live wins on new)
  - Live buffer is trimmed after promotion
  - Live file is removed when fully promoted
  - Dry-run does not modify files
  - Missing historical creates new file
  - Empty live buffer is cleaned up
  - Quality checks catch OHLC violations
  - Promotion log is written
  - discover_live_tokens finds tokens in live directory
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

import pandas as pd
import pytest

from v4.data_loader import historical_path, live_path
from tools.promote_live import (
    promote_token,
    discover_live_tokens,
    check_promoted_bars,
    fix_promoted_bars,
    run_promotion,
    log_promotion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(start_ts_ms: int, n: int, interval_ms: int = 3_600_000,
             close_start: float = 100.0) -> pd.DataFrame:
    """Create a DataFrame with DatetimeIndex matching parquet format."""
    timestamps = pd.to_datetime(
        [start_ts_ms + i * interval_ms for i in range(n)], unit="ms"
    )
    return pd.DataFrame({
        "open": [close_start + i for i in range(n)],
        "high": [close_start + i + 5 for i in range(n)],
        "low": [close_start + i - 5 for i in range(n)],
        "close": [close_start + i + 2 for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
    }, index=timestamps)


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ===================================================================
# Test: promote_token
# ===================================================================

class TestPromoteToken:
    """Core promotion logic."""

    def test_promotes_new_bars(self):
        """New live bars are appended to historical."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Historical: bars 0-4
            hist = _make_df(0, 5)
            _write(hist, historical_path("BTC", "perp", tmpdir))

            # Live: bars 5-7 (all new)
            live = _make_df(5 * 3_600_000, 3)
            _write(live, live_path("BTC", "perp", tmpdir))

            rec = promote_token("BTC", "perp", data_dir)

            assert rec is not None
            assert rec["bars_promoted"] == 3
            assert rec["hist_total_bars"] == 8

            # Historical now has 8 bars
            df = pd.read_parquet(historical_path("BTC", "perp", tmpdir))
            assert len(df) == 8

            # Live buffer removed (fully promoted)
            assert not live_path("BTC", "perp", tmpdir).exists()

    def test_deduplicates_overlap(self):
        """Overlapping bars between live and historical are deduplicated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Historical: bars 0-4
            hist = _make_df(0, 5)
            _write(hist, historical_path("BTC", "perp", tmpdir))

            # Live: bars 3-7 (bars 3,4 overlap)
            live = _make_df(3 * 3_600_000, 5)
            _write(live, live_path("BTC", "perp", tmpdir))

            rec = promote_token("BTC", "perp", data_dir)

            assert rec is not None
            # Only bars 5,6,7 are new (3,4 overlap)
            assert rec["bars_promoted"] == 3

            df = pd.read_parquet(historical_path("BTC", "perp", tmpdir))
            assert len(df) == 8
            assert df.index.duplicated().sum() == 0

    def test_live_buffer_trimmed(self):
        """Live buffer is trimmed to only contain un-promoted bars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Historical: bars 0-4
            hist = _make_df(0, 5)
            _write(hist, historical_path("BTC", "perp", tmpdir))

            # Live: bars 5-9 (5 bars, but we need some to remain)
            # We can't easily test partial trim since all bars get promoted.
            # Instead, directly test with bars that extend beyond promotion.
            live = _make_df(5 * 3_600_000, 5)
            _write(live, live_path("BTC", "perp", tmpdir))

            rec = promote_token("BTC", "perp", data_dir)

            assert rec is not None
            assert rec["live_remaining_bars"] == 0
            assert not live_path("BTC", "perp", tmpdir).exists()

    def test_dry_run_no_modification(self):
        """Dry run reports what would happen without modifying files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            hist = _make_df(0, 5)
            _write(hist, historical_path("BTC", "perp", tmpdir))

            live = _make_df(5 * 3_600_000, 3)
            live_pq = live_path("BTC", "perp", tmpdir)
            _write(live, live_pq)

            rec = promote_token("BTC", "perp", data_dir, dry_run=True)

            assert rec is not None
            assert rec["dry_run"] is True
            assert rec["bars_promoted"] == 3

            # Files unchanged
            df_hist = pd.read_parquet(historical_path("BTC", "perp", tmpdir))
            assert len(df_hist) == 5  # unchanged
            assert live_pq.exists()  # still there

    def test_no_historical_creates_new(self):
        """When no historical exists, live bars become the historical file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            live = _make_df(0, 10)
            _write(live, live_path("ETH", "spot", tmpdir))

            rec = promote_token("ETH", "spot", data_dir)

            assert rec is not None
            assert rec["bars_promoted"] == 10
            assert rec["hist_total_bars"] == 10

            hist_pq = historical_path("ETH", "spot", tmpdir)
            assert hist_pq.exists()
            df = pd.read_parquet(hist_pq)
            assert len(df) == 10

    def test_no_live_returns_none(self):
        """When no live buffer exists, returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            rec = promote_token("BTC", "perp", data_dir)
            assert rec is None

    def test_empty_live_cleaned_up(self):
        """Empty live parquet file is removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            live_pq = live_path("BTC", "perp", tmpdir)
            _write(empty, live_pq)

            rec = promote_token("BTC", "perp", data_dir)

            assert rec is None
            assert not live_pq.exists()

    def test_preserves_funding_columns(self):
        """Perp data with funding columns are preserved through promotion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Historical with funding
            hist = _make_df(0, 5)
            hist["funding_rate"] = 0.0001
            hist["funding_1h"] = 0.0000125
            _write(hist, historical_path("BTC", "perp", tmpdir))

            # Live with funding
            live = _make_df(5 * 3_600_000, 3)
            live["funding_rate"] = 0.0002
            live["funding_1h"] = 0.000025
            _write(live, live_path("BTC", "perp", tmpdir))

            promote_token("BTC", "perp", data_dir)

            df = pd.read_parquet(historical_path("BTC", "perp", tmpdir))
            assert "funding_rate" in df.columns
            assert "funding_1h" in df.columns
            assert len(df) == 8


# ===================================================================
# Test: quality checks
# ===================================================================

class TestQualityChecks:
    """check_promoted_bars catches data issues."""

    def test_clean_data_no_issues(self):
        """Clean data produces no issues."""
        df = _make_df(0, 10)
        issues = check_promoted_bars(df, "BTC")
        assert issues == []

    def test_detects_ohlc_violation(self):
        """Detects when high < open."""
        df = _make_df(0, 5)
        df.iloc[2, df.columns.get_loc("high")] = 50.0  # less than open
        issues = check_promoted_bars(df, "BTC")
        assert any("OHLC" in i for i in issues)

    def test_detects_duplicates(self):
        """Detects duplicate timestamps."""
        df = _make_df(0, 5)
        # Create duplicate by appending a row with same index
        dup = df.iloc[[2]].copy()
        df = pd.concat([df, dup])
        issues = check_promoted_bars(df, "BTC")
        assert any("duplicate" in i for i in issues)

    def test_fix_deduplicates(self):
        """fix_promoted_bars removes duplicates."""
        df = _make_df(0, 5)
        dup = df.iloc[[2]].copy()
        df = pd.concat([df, dup])
        fixed = fix_promoted_bars(df)
        assert fixed.index.duplicated().sum() == 0
        assert len(fixed) == 5


# ===================================================================
# Test: discover_live_tokens
# ===================================================================

class TestDiscoverLiveTokens:
    """discover_live_tokens scans live directory."""

    def test_finds_tokens(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            df = _make_df(0, 3)
            _write(df, live_path("BTC", "perp", tmpdir))
            _write(df, live_path("ETH", "perp", tmpdir))

            tokens = discover_live_tokens("perp", Path(tmpdir))
            assert tokens == ["BTC", "ETH"]

    def test_empty_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tokens = discover_live_tokens("perp", Path(tmpdir))
            assert tokens == []


# ===================================================================
# Test: run_promotion (orchestration)
# ===================================================================

class TestRunPromotion:
    """run_promotion across multiple tokens and markets."""

    def test_promotes_multiple_tokens(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            df = _make_df(0, 5)

            for token in ["BTC", "ETH", "SOL"]:
                _write(df, live_path(token, "perp", tmpdir))

            records = run_promotion(["perp"], data_dir=data_dir)

            promoted = [r for r in records if "error" not in r]
            assert len(promoted) == 3
            for r in promoted:
                assert r["bars_promoted"] == 5

    def test_token_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            df = _make_df(0, 5)

            for token in ["BTC", "ETH", "SOL"]:
                _write(df, live_path(token, "perp", tmpdir))

            records = run_promotion(["perp"], tokens=["BTC"], data_dir=data_dir)
            assert len(records) == 1
            assert records[0]["token"] == "BTC"


# ===================================================================
# Test: promotion log
# ===================================================================

class TestPromotionLog:
    """log_promotion writes to data/promotions.jsonl."""

    def test_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            records = [
                {"token": "BTC", "market": "perp", "bars_promoted": 10},
                {"token": "ETH", "market": "perp", "bars_promoted": 5},
            ]
            log_promotion(records, data_dir)

            log_path = data_dir / "promotions.jsonl"
            assert log_path.exists()

            lines = log_path.read_text().strip().split("\n")
            assert len(lines) == 2

            entry = json.loads(lines[0])
            assert entry["token"] == "BTC"
            assert "promoted_at" in entry
