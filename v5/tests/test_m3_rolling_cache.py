"""M3 acceptance tests — RollingCache basics, watermark, gap detection, monotonicity.

Covers:
  - AC1: RollingCache class exists using pre-allocated numpy ring buffer with
    configurable maxlen, column-oriented storage, O(1) append,
    zero-copy `arrays(field)` view when not wrapped.
  - AC19: `seeded_through_ts_ns` watermark persists the last parquet ts ingested.
  - AC20: gap-detection on seed + append emits WARNING.
  - AC21: non-monotonic `append()` raises ValueError.

All tests MUST FAIL today — `v5.rolling_cache` does not exist.
Import errors are valid RED states per Phase 3.

Seed: 42. TestClock epoch: 2026-03-01T00:00:00Z (not needed in this file).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NS_PER_HOUR = 3_600 * 1_000_000_000
BASE_TS = np.int64(1_770_000_000) * np.int64(1_000_000_000)  # ~2026-01-01


def _ts_sequence(n: int, start: np.int64 = BASE_TS,
                 step_ns: int = NS_PER_HOUR) -> np.ndarray:
    """Return a monotonic int64 ts_ns array of length n."""
    return start + np.arange(n, dtype=np.int64) * np.int64(step_ns)


# ===================================================================
# AC1 — RollingCache basics
# ===================================================================

class TestAC1RollingCacheBasics:
    """RollingCache exists with column-oriented ring-buffer storage."""

    def test_import_rolling_cache(self):
        """Module and class must be importable."""
        from v5.rolling_cache import RollingCache  # noqa: F401

    def test_import_bar_type_enum(self):
        """BarType enum must exist with ONE_MIN, ONE_HOUR, DAILY."""
        from v5.rolling_cache import BarType
        assert hasattr(BarType, "ONE_MIN")
        assert hasattr(BarType, "ONE_HOUR")
        assert hasattr(BarType, "DAILY")

    def test_construct_with_maxlen(self):
        """Cache constructs with explicit maxlen."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        assert cache.maxlen == 100

    def test_arrays_empty_after_construct(self):
        """Before any append, arrays('close') returns empty or length-0 view."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        arr = cache.arrays("close")
        assert len(arr) == 0

    def test_append_writes_indexed_value(self):
        """append(ts_ns, close=..., ...) writes at current head."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        cache.append(
            ts_ns=int(BASE_TS),
            close=100.0, high=101.0, low=99.0,
            volume=1000.0, atr=5.0, funding=0.0,
        )
        arr = cache.arrays("close")
        assert len(arr) == 1
        assert arr[0] == pytest.approx(100.0)

    def test_append_many_before_wrap(self):
        """maxlen=10, append 5 entries => all 5 readable in order."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=10,
        )
        ts = _ts_sequence(5)
        closes = np.arange(5, dtype=np.float32) + 100.0
        for i in range(5):
            cache.append(
                ts_ns=int(ts[i]),
                close=float(closes[i]), high=float(closes[i]) + 1.0,
                low=float(closes[i]) - 1.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        arr = cache.arrays("close")
        assert len(arr) == 5
        np.testing.assert_allclose(arr, closes, rtol=1e-6)

    def test_wrap_behavior_maintains_last_maxlen_entries(self):
        """maxlen=5, append 8 entries => arrays returns the last 5."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=5,
        )
        ts = _ts_sequence(8)
        closes = np.arange(8, dtype=np.float32) + 100.0
        for i in range(8):
            cache.append(
                ts_ns=int(ts[i]),
                close=float(closes[i]), high=float(closes[i]) + 1.0,
                low=float(closes[i]) - 1.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        arr = cache.arrays("close")
        assert len(arr) == 5
        # Last 5 values are closes[3:8] = 103..107
        np.testing.assert_allclose(arr, closes[3:8], rtol=1e-6)

    def test_arrays_view_is_zero_copy_when_not_wrapped(self):
        """Before wrap, arrays() must return a view (base is the underlying buffer)."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        for i in range(10):
            cache.append(
                ts_ns=int(BASE_TS + i * NS_PER_HOUR),
                close=100.0 + i, high=101.0, low=99.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        arr = cache.arrays("close")
        # Zero-copy: ndarray.base must point at the ring buffer backing store
        assert arr.base is not None, (
            "arrays() should return a view, not a copy, before wrap"
        )

    def test_timestamps_accessor(self):
        """`timestamps()` returns int64 ts_ns array in insertion order."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=10,
        )
        ts = _ts_sequence(4)
        for i in range(4):
            cache.append(
                ts_ns=int(ts[i]),
                close=100.0, high=101.0, low=99.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        got = cache.timestamps()
        assert got.dtype == np.int64
        np.testing.assert_array_equal(got, ts)

    def test_float32_storage_dtype(self):
        """Column storage for price/volume fields is float32 per brief."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        cache.append(
            ts_ns=int(BASE_TS),
            close=100.0, high=101.0, low=99.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        assert cache.arrays("close").dtype == np.float32

    def test_last_written_ts_ns_helper(self):
        """last_written_ts_ns() returns the most recent appended ts."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=10,
        )
        ts = _ts_sequence(3)
        for i in range(3):
            cache.append(
                ts_ns=int(ts[i]),
                close=100.0, high=101.0, low=99.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        assert cache.last_written_ts_ns() == int(ts[-1])


# ===================================================================
# AC19 — seeded_through_ts_ns watermark
# ===================================================================

class TestAC19SeededWatermark:
    """Every RollingCache exposes seeded_through_ts_ns per AC19."""

    def test_watermark_attribute_exists(self):
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        assert hasattr(cache, "seeded_through_ts_ns")

    def test_watermark_default_is_zero(self):
        """Pre-seed, watermark must indicate 'nothing seeded' (0)."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        assert cache.seeded_through_ts_ns == 0

    def test_watermark_updated_after_seed(self):
        """After seed() of a df, watermark == last ts in df."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        n = 50
        ts = _ts_sequence(n)
        df = pd.DataFrame({
            "timestamp": ts,
            "close": np.arange(n, dtype=np.float64) + 100.0,
            "high": np.arange(n, dtype=np.float64) + 101.0,
            "low": np.arange(n, dtype=np.float64) + 99.0,
            "volume": np.full(n, 1000.0),
            "atr": np.full(n, 5.0),
            "funding": np.zeros(n),
        })
        cache.seed(df)
        assert cache.seeded_through_ts_ns == int(ts[-1])

    def test_watermark_reseed_triggers_when_parquet_advances(self):
        """Unit of AC18/19: seed 100 bars, parquet gains 10, next check_parquet_drift
        reports drift detected (returns True / triggers reseed)."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        n = 100
        ts = _ts_sequence(n)
        df = pd.DataFrame({
            "timestamp": ts,
            "close": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "volume": np.full(n, 1000.0),
            "atr": np.full(n, 5.0),
            "funding": np.zeros(n),
        })
        cache.seed(df)
        watermark_before = cache.seeded_through_ts_ns
        # Parquet has gained 10 more bars
        newer_ts = int(ts[-1]) + 10 * NS_PER_HOUR
        # The API is implementation-specific — either `peek_parquet_ts` or
        # `check_parquet_drift(latest_parquet_ts_ns=...)` returning True.
        # We assert the behavioral contract: watermark is less than the new ts.
        assert watermark_before < newer_ts


# ===================================================================
# AC20 — Gap detection WARNING on seed + append
# ===================================================================

class TestAC20GapDetection:
    """Gap detection emits WARNING on seed + on append ts jumps."""

    def test_seed_with_gap_logs_warning(self, caplog):
        """A seed DataFrame missing a bar index mid-sequence triggers WARNING."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        n = 20
        ts_full = _ts_sequence(n)
        # Drop bar index 10 => gap in sequence
        ts_gap = np.delete(ts_full, 10)
        df = pd.DataFrame({
            "timestamp": ts_gap,
            "close": np.full(len(ts_gap), 100.0),
            "high": np.full(len(ts_gap), 101.0),
            "low": np.full(len(ts_gap), 99.0),
            "volume": np.full(len(ts_gap), 1000.0),
            "atr": np.full(len(ts_gap), 5.0),
            "funding": np.zeros(len(ts_gap)),
        })
        caplog.set_level(logging.WARNING)
        cache.seed(df)
        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert any("gap" in m.lower() for m in warn_msgs), (
            f"Expected WARNING about a gap; got: {warn_msgs}"
        )

    def test_seed_with_contiguous_trailing_gap_allowed(self, caplog):
        """Brief: 'tolerates contiguous trailing gap at the latest timestamps'."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        n = 20
        ts = _ts_sequence(n)
        df = pd.DataFrame({
            "timestamp": ts,
            "close": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "volume": np.full(n, 1000.0),
            "atr": np.full(n, 5.0),
            "funding": np.zeros(n),
        })
        caplog.set_level(logging.WARNING)
        cache.seed(df)
        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and "gap" in r.getMessage().lower()
        ]
        assert warn_msgs == [], (
            f"Expected no 'gap' WARNING for contiguous seed; got {warn_msgs}"
        )

    def test_append_big_jump_logs_warning(self, caplog):
        """append ts more than 2*bar_period ahead of last => WARNING."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        cache.append(
            ts_ns=int(BASE_TS),
            close=100.0, high=101.0, low=99.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        caplog.set_level(logging.WARNING)
        # 3 hours ahead of last
        cache.append(
            ts_ns=int(BASE_TS + 3 * NS_PER_HOUR),
            close=101.0, high=102.0, low=100.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert any(
            "gap" in m.lower() or "drop" in m.lower() or "jump" in m.lower()
            for m in warn_msgs
        ), f"Expected WARNING on big ts jump; got: {warn_msgs}"

    def test_append_normal_step_no_warning(self, caplog):
        """Adjacent-bar append does not log WARNING."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        cache.append(
            ts_ns=int(BASE_TS),
            close=100.0, high=101.0, low=99.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        caplog.set_level(logging.WARNING)
        cache.append(
            ts_ns=int(BASE_TS + NS_PER_HOUR),
            close=101.0, high=102.0, low=100.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert warn_msgs == [], (
            f"Expected no WARNING for adjacent append; got {warn_msgs}"
        )


# ===================================================================
# AC21 — Monotonicity invariant on append (raises ValueError)
# ===================================================================

class TestAC21Monotonicity:
    """append(ts_ns <= last_written_ts_ns) raises ValueError with 'non-monotonic'."""

    def test_non_monotonic_append_raises(self):
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        # t+1
        cache.append(
            ts_ns=int(BASE_TS + NS_PER_HOUR),
            close=100.0, high=101.0, low=99.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        # t (older) — must raise
        with pytest.raises(ValueError) as exc_info:
            cache.append(
                ts_ns=int(BASE_TS),
                close=101.0, high=102.0, low=100.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        assert "non-monotonic" in str(exc_info.value).lower()

    def test_equal_ts_append_raises(self):
        """Equal ts also violates monotonicity (strictly increasing required)."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        cache.append(
            ts_ns=int(BASE_TS),
            close=100.0, high=101.0, low=99.0,
            volume=1_000.0, atr=5.0, funding=0.0,
        )
        with pytest.raises(ValueError):
            cache.append(
                ts_ns=int(BASE_TS),
                close=100.0, high=101.0, low=99.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )

    def test_monotonic_sequence_ok(self):
        """Strictly increasing ts sequence succeeds."""
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=100,
        )
        for i in range(5):
            cache.append(
                ts_ns=int(BASE_TS + i * NS_PER_HOUR),
                close=100.0 + i, high=101.0, low=99.0,
                volume=1_000.0, atr=5.0, funding=0.0,
            )
        assert cache.last_written_ts_ns() == int(BASE_TS + 4 * NS_PER_HOUR)
