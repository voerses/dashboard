"""M4 — Crypto bar-close convention (AC14 + AC22 defaults).

label='left', closed='left', origin='epoch', anchor UTC 00:00. Bar ts=12:00
covers [12:00, 13:00); close == 12:59 minute's close; 13:00 starts next bar.
All tests MUST FAIL RED — v5.data_resampler / v5.bar_spec do not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


NS_PER_MIN = 60 * 1_000_000_000
# Choose an anchor at 2026-01-01T12:00:00 UTC — a "known-aligned" timestamp.
# 1735689600 = 2025-01-01 UTC (off by a year for illustration); compute precisely:
BASE_TS_12H = pd.Timestamp("2026-01-01T12:00:00Z").value  # ns since epoch
# UTC 00:00-aligned anchor — used for 1440m (daily) cases where origin='epoch'
# bins are anchored to UTC 00:00 (AC14). A 12:00Z start would split 1440m
# windows across two days under origin='epoch' (producing 2 bars), defeating
# the "single-bar left-closed" assertion. Reviewer fix per dispute on Task 27.
BASE_TS_00H = pd.Timestamp("2026-01-01T00:00:00Z").value  # ns since epoch


def _minute_df_12h_window(n_minutes: int = 60) -> pd.DataFrame:
    """1m DataFrame [12:00, 12:00+n); close encoded as minute_idx + 100."""
    ts = BASE_TS_12H + np.arange(n_minutes, dtype=np.int64) * np.int64(NS_PER_MIN)
    close = np.arange(n_minutes, dtype=np.float64) + 100.0
    return pd.DataFrame({
        "timestamp": ts,
        "open": close - 0.1,
        "high": close + 0.25,
        "low": close - 0.25,
        "close": close,
        "volume": np.ones(n_minutes, dtype=np.float64),
    })


def _minute_df_00h_window(n_minutes: int) -> pd.DataFrame:
    """1m DataFrame [00:00, 00:00+n); close encoded as minute_idx + 100.

    Reviewer fix (Task 27 dispute): used for 1440m (daily) parametrize rows
    since origin='epoch' bins 1440m bars at UTC 00:00 — a 12:00Z start would
    straddle two day-boundaries. Using 00:00Z keeps the `origin='epoch'`
    contract intact while still exercising left-closed / left-labeled
    convention at the daily resolution.
    """
    ts = BASE_TS_00H + np.arange(n_minutes, dtype=np.int64) * np.int64(NS_PER_MIN)
    close = np.arange(n_minutes, dtype=np.float64) + 100.0
    return pd.DataFrame({
        "timestamp": ts,
        "open": close - 0.1,
        "high": close + 0.25,
        "low": close - 0.25,
        "close": close,
        "volume": np.ones(n_minutes, dtype=np.float64),
    })


# ---------------------------------------------------------------------------
# AC22 — BarSpec defaults
# ---------------------------------------------------------------------------


class TestAC22BarSpecDefaults:
    """AC22 + AC14: BarSpec default fields match the crypto convention."""

    def test_default_label_side_is_left(self):
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60).label_side == "left"

    def test_default_closed_side_is_left(self):
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60).closed_side == "left"

    def test_default_anchor_utc(self):
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60).anchor_utc == "00:00"


# ---------------------------------------------------------------------------
# AC14 — left-closed / left-labeled bar convention
# ---------------------------------------------------------------------------


class TestAC14BarCloseConvention:
    """AC14: ts=12:00 covers [12:00, 13:00); close == 12:59 minute's close."""

    def test_single_1h_bar_from_60_minutes_12_to_13(self):
        """60 minutes of 1m data [12:00, 13:00) => exactly one 1h bar at 12:00."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        df = _minute_df_12h_window(60)
        out = DataResampler.materialize(df, BarSpec.from_minutes(60))

        assert len(out) == 1, (
            f"AC14: expected exactly one 1h bar, got {len(out)}"
        )
        assert int(out["timestamp"].iloc[0]) == BASE_TS_12H, (
            "AC14: 1h bar must be left-labeled at 12:00"
        )

    def test_1h_bar_close_equals_12_59_close(self):
        """AC14: the left-closed interval [12:00, 13:00) includes 12:59.
        So close of the 1h bar == close of the 12:59 minute bar (== 159.0)."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        df = _minute_df_12h_window(60)
        out = DataResampler.materialize(df, BarSpec.from_minutes(60))
        # close = minute-index + 100; minute 59 -> 159.
        assert float(out["close"].iloc[0]) == pytest.approx(159.0), (
            "AC14: 1h bar close must equal 12:59 minute's close (left-closed)"
        )

    def test_13_00_minute_starts_next_1h_bar(self):
        """AC14: include 13:00 minute-bar => 2 output bars (ts=12:00 and ts=13:00);
        13:00's close is in the SECOND bar, not the first."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        df = _minute_df_12h_window(61)  # 12:00 .. 13:00 inclusive = 61 minutes
        out = DataResampler.materialize(df, BarSpec.from_minutes(60))

        assert len(out) == 2, (
            f"AC14: 61 minutes must emit 2 1h bars, got {len(out)}"
        )
        # First bar (ts=12:00) close == 12:59 -> 159.0 (not 160.0).
        assert float(out["close"].iloc[0]) == pytest.approx(159.0)
        # Second bar (ts=13:00) contains only the 13:00 minute -> close 160.0.
        assert int(out["timestamp"].iloc[1]) == BASE_TS_12H + 60 * NS_PER_MIN
        assert float(out["close"].iloc[1]) == pytest.approx(160.0)

    def test_13_00_minute_not_in_12_00_bar(self):
        """AC14 (right-open): 13:00 minute's close (160) is NEVER in the 12:00 1h bar."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        df = _minute_df_12h_window(61)
        out = DataResampler.materialize(df, BarSpec.from_minutes(60))
        first_bar_close = float(out["close"].iloc[0])
        assert first_bar_close != pytest.approx(160.0), (
            "AC14: 13:00 minute must NOT aggregate into 12:00 1h bar"
        )

    # Reviewer fix (Task 27 dispute): 1440m rows use an 00:00Z-aligned window.
    # origin='epoch' bins 1440m bars at UTC 00:00, so a 12:00Z start splits
    # 1440 minutes across two daily bars — incompatible with the "single bar"
    # assertion. 60m / 240m rows keep the 12:00Z start because epoch-aligned
    # 1h/4h boundaries fall on the hour and align naturally to 12:00.
    @pytest.mark.parametrize("period_minutes,n_minutes,last_close_in_first_bar,base_hour", [
        (60, 60, 159.0, 12),       # 1h  — minute 59 closes the 12:00 bar
        (240, 240, 339.0, 12),     # 4h  — minute 239 closes the 12:00 bar
        (1440, 1440, 1539.0, 0),   # 1d  — minute 1439 closes the 00:00 bar
    ])
    def test_left_closed_convention_across_resolutions(
        self, period_minutes, n_minutes, last_close_in_first_bar, base_hour,
    ):
        """AC14: left-label / left-closed holds for 1h, 4h, 1d BarSpecs."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        if base_hour == 0:
            df = _minute_df_00h_window(n_minutes)
        else:
            df = _minute_df_12h_window(n_minutes)
        out = DataResampler.materialize(df, BarSpec.from_minutes(period_minutes))

        # Single coarse bar spanning exactly the window.
        assert len(out) == 1, (
            f"expected 1 bar for {period_minutes}m, got {len(out)}"
        )
        assert float(out["close"].iloc[0]) == pytest.approx(last_close_in_first_bar), (
            f"AC14 left-closed: {period_minutes}m close should equal "
            f"last-interior minute's close ({last_close_in_first_bar})"
        )

    # Reviewer fix (Task 27 dispute): see above — 1440m uses 00:00Z anchor.
    @pytest.mark.parametrize("period_minutes,base_hour", [
        (60, 12),
        (240, 12),
        (1440, 0),
    ])
    def test_right_open_boundary_across_resolutions(self, period_minutes, base_hour):
        """AC14: the very next minute after the coarse window starts the next bar."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        if base_hour == 0:
            df = _minute_df_00h_window(period_minutes + 1)
            base_ts = BASE_TS_00H
        else:
            df = _minute_df_12h_window(period_minutes + 1)
            base_ts = BASE_TS_12H
        out = DataResampler.materialize(df, BarSpec.from_minutes(period_minutes))
        # Two bars: ts=base and ts=base + period
        assert len(out) == 2, (
            f"AC14 boundary: {period_minutes}m must emit 2 bars, got {len(out)}"
        )
        next_ts_expected = base_ts + period_minutes * NS_PER_MIN
        assert int(out["timestamp"].iloc[1]) == next_ts_expected

    def test_bar_spec_label_is_left_labeled(self):
        """AC14 + AC22: a resampled bar's `timestamp` is the LEFT edge of [a,b)."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        df = _minute_df_12h_window(60)
        out = DataResampler.materialize(df, BarSpec.from_minutes(60))
        # Left label == window start (12:00), NOT right label (13:00).
        assert int(out["timestamp"].iloc[0]) == BASE_TS_12H
        assert int(out["timestamp"].iloc[0]) != BASE_TS_12H + 60 * NS_PER_MIN
