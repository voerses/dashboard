"""Tests for walk-forward window computation — AC24 (window schemes).

Tests the new v4/walk_forward.py module: WalkForwardWindow dataclass,
compute_wf_windows() pure function, BacktestManifest, log_manifest().
"""
import json

import numpy as np
import pandas as pd
import pytest

from v4.walk_forward import (
    WalkForwardWindow,
    compute_wf_windows,
    BacktestManifest,
    log_manifest,
)


class TestComputeWfWindowsRolling:
    """AC24: compute_wf_windows() with rolling scheme produces correct windows."""

    def test_rolling_produces_multiple_windows(self):
        """AC24: Rolling scheme with enough bars produces multiple non-overlapping windows."""
        # 2 years of hourly data = 17520 bars
        # train_bars=8760 (365d), recal_bars=2160 (90d), purge_bars=168
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        assert len(windows) >= 2
        for w in windows:
            assert isinstance(w, WalkForwardWindow)

    def test_rolling_window_boundaries_are_correct(self):
        """AC24: Each rolling window has data_cap = oos_start - purge_bars."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        for w in windows:
            expected_data_cap = w.oos_start - 168
            assert w.data_cap == expected_data_cap, (
                f"Window {w.window_idx}: data_cap={w.data_cap} should be "
                f"oos_start({w.oos_start}) - purge_bars(168) = {expected_data_cap}"
            )

    def test_rolling_oos_ranges_are_contiguous(self):
        """AC24: OOS ranges in rolling windows are contiguous — no gaps, no overlaps."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        for i in range(1, len(windows)):
            prev_end = windows[i - 1].oos_end
            curr_start = windows[i].oos_start
            assert curr_start == prev_end, (
                f"Gap or overlap between window {i-1} (oos_end={prev_end}) "
                f"and window {i} (oos_start={curr_start})"
            )

    def test_rolling_first_window_starts_after_train_plus_purge(self):
        """AC24: First OOS window starts at train_bars + purge_bars."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        assert len(windows) >= 1
        # First OOS starts after train + purge
        assert windows[0].oos_start == 8760 + 168, (
            f"First OOS should start at train_bars + purge_bars = {8760 + 168}, "
            f"got {windows[0].oos_start}"
        )

    def test_rolling_oos_window_size_equals_recal_bars(self):
        """AC24: Each OOS window has recal_bars bars (except possibly the last)."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        # All windows except possibly the last should have size == recal_bars
        for w in windows[:-1]:
            oos_size = w.oos_end - w.oos_start
            assert oos_size == 2160, (
                f"Window {w.window_idx}: OOS size = {oos_size}, expected recal_bars=2160"
            )


class TestComputeWfWindowsExpanding:
    """AC24: compute_wf_windows() with expanding scheme."""

    def test_expanding_produces_multiple_windows(self):
        """AC24: Expanding scheme produces windows."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="expanding",
        )
        assert len(windows) >= 2
        for w in windows:
            assert isinstance(w, WalkForwardWindow)

    def test_expanding_oos_ranges_are_contiguous(self):
        """AC24: OOS ranges in expanding windows are contiguous."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="expanding",
        )
        for i in range(1, len(windows)):
            prev_end = windows[i - 1].oos_end
            curr_start = windows[i].oos_start
            assert curr_start == prev_end

    def test_expanding_data_cap_grows_each_window(self):
        """AC24: In expanding scheme, data_cap increases with each window."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="expanding",
        )
        for i in range(1, len(windows)):
            assert windows[i].data_cap > windows[i - 1].data_cap, (
                f"Expanding window {i} data_cap ({windows[i].data_cap}) should be "
                f"greater than window {i-1} ({windows[i-1].data_cap})"
            )


class TestComputeWfWindowsEdgeCases:
    """Edge cases for compute_wf_windows()."""

    def test_too_few_bars_returns_empty(self):
        """When n_bars is too small for even one window, returns empty list."""
        windows = compute_wf_windows(
            n_bars=500,  # way too few for train_bars=8760
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        assert windows == []

    def test_data_cap_strictly_less_than_oos_start(self):
        """AC7/AC24: data_cap is strictly less than oos_start for every window
        (purge gap prevents data bleed into OOS)."""
        windows = compute_wf_windows(
            n_bars=17520,
            train_bars=8760,
            recal_bars=2160,
            purge_bars=168,
            scheme="rolling",
        )
        for w in windows:
            assert w.data_cap < w.oos_start, (
                f"Window {w.window_idx}: data_cap ({w.data_cap}) must be "
                f"< oos_start ({w.oos_start})"
            )


class TestBacktestManifest:
    """AC20: BacktestManifest serialization and logging."""

    def test_manifest_can_be_created(self):
        """AC20: BacktestManifest dataclass can be instantiated with required fields."""
        manifest = BacktestManifest(
            token_data_ranges={"BTC": {"first_bar": "2025-01-01", "last_bar": "2026-01-01"}},
            end_date_used="2026-02-28",
            config_hash="abc123def456",
            run_timestamp="2026-03-01T00:00:00",
        )
        assert manifest.end_date_used == "2026-02-28"
        assert manifest.config_hash == "abc123def456"
        assert "BTC" in manifest.token_data_ranges

    def test_log_manifest_writes_jsonl(self, tmp_path):
        """AC20: log_manifest() writes manifest to a JSONL file."""
        manifest = BacktestManifest(
            token_data_ranges={"BTC": {"first_bar": "2025-01-01", "last_bar": "2026-01-01"}},
            end_date_used="2026-02-28",
            config_hash="abc123def456",
            run_timestamp="2026-03-01T00:00:00",
        )
        outfile = tmp_path / "manifest.jsonl"
        log_manifest(manifest, str(outfile))

        assert outfile.exists()
        line = outfile.read_text().strip()
        data = json.loads(line)
        assert data["end_date_used"] == "2026-02-28"
        assert data["config_hash"] == "abc123def456"
        assert "BTC" in data["token_data_ranges"]

    def test_log_manifest_appends(self, tmp_path):
        """AC20: log_manifest() appends (not overwrites) to JSONL file."""
        outfile = tmp_path / "manifest.jsonl"
        for i in range(3):
            manifest = BacktestManifest(
                token_data_ranges={},
                end_date_used=f"2026-0{i+1}-01",
                config_hash=f"hash{i}",
                run_timestamp=f"2026-0{i+1}-01T00:00:00",
            )
            log_manifest(manifest, str(outfile))

        lines = outfile.read_text().strip().split("\n")
        assert len(lines) == 3


class TestWalkForwardWindowDataclass:
    """WalkForwardWindow dataclass structure."""

    def test_window_has_required_fields(self):
        """WalkForwardWindow has window_idx, data_cap, oos_start, oos_end."""
        w = WalkForwardWindow(
            window_idx=0,
            data_cap=8592,
            oos_start=8928,
            oos_end=11088,
        )
        assert w.window_idx == 0
        assert w.data_cap == 8592
        assert w.oos_start == 8928
        assert w.oos_end == 11088
