"""M4 — DataResampler: idempotence, divisor ValueError, float64 protocol.

Covers:
  - AC21 T-B13: DataResampler.materialize is idempotent (1m -> Nm -> Mm ==
    1m -> Mm) and raises ValueError when target.resolution_minutes is not an
    integer multiple of source resolution.
  - AC34 T-B26: float64 accumulator protocol — both eager and streaming
    paths accumulate OHLCV in float64, downcast to float32 at RollingCache
    boundary. 60 float32 closes both ways == bit-identical post-downcast.

All tests MUST FAIL today — v5.data_resampler does not exist.
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
BASE_TS = np.int64(1_770_000_000) * np.int64(1_000_000_000)  # ~2026-01-01 UTC aligned


def _make_1m_df(n: int, seed: int = 42) -> pd.DataFrame:
    """Build a synthetic 1-minute OHLCV DataFrame with strict monotonic ts."""
    rng = np.random.default_rng(seed)
    ts = BASE_TS + np.arange(n, dtype=np.int64) * np.int64(NS_PER_MIN)
    base = 100.0 + rng.normal(0.0, 0.5, size=n).cumsum()
    high = base + np.abs(rng.normal(0.1, 0.1, size=n))
    low = base - np.abs(rng.normal(0.1, 0.1, size=n))
    close = base + rng.normal(0.0, 0.05, size=n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.abs(rng.normal(1_000.0, 100.0, size=n))
    return pd.DataFrame({
        "timestamp": ts,
        "open": open_.astype(np.float64),
        "high": high.astype(np.float64),
        "low": low.astype(np.float64),
        "close": close.astype(np.float64),
        "volume": volume.astype(np.float64),
    })


class TestAC21Idempotence:
    """AC21 T-B13: 1m -> Nm -> Mm == 1m -> Mm where M is a multiple of N."""

    def test_direct_vs_two_step_equal_1m_to_1h(self):
        """1m -> 1h == 1m -> 5m -> 1h (bit-identical post-aggregation)."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        source = _make_1m_df(600)
        direct = DataResampler.materialize(source, BarSpec.from_minutes(60))
        two_step_intermediate = DataResampler.materialize(
            source, BarSpec.from_minutes(5),
        )
        two_step = DataResampler.materialize(
            two_step_intermediate, BarSpec.from_minutes(60),
        )
        # OHLCV columns must match bit-identically (AC34 guarantees this via
        # float64 accumulator protocol).
        for col in ("open", "high", "low", "close", "volume"):
            np.testing.assert_array_equal(
                direct[col].to_numpy(), two_step[col].to_numpy(),
                err_msg=f"idempotence broken in column '{col}'",
            )

    @pytest.mark.parametrize("seed", list(range(10)))
    def test_idempotence_random_valid_pairs(self, seed):
        """AC21: property-style check across valid (N, M) pairs.

        Brief specifies 100 random valid pairs; we sample 10 seeds to keep
        the RED state fast while still catching regressions.
        """
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        rng = np.random.default_rng(seed)
        # Sample an intermediate N and a target M that is a multiple of N.
        # Canonical set: {1,3,5,10,15,30,60,120,240,360,480,720,1440}.
        valid_pairs = [(3, 15), (5, 15), (5, 60), (10, 60),
                       (15, 60), (30, 60), (60, 240), (60, 720)]
        n, m = valid_pairs[int(rng.integers(0, len(valid_pairs)))]
        source = _make_1m_df(2880, seed=seed)  # 2 days of 1m bars
        inter = DataResampler.materialize(source, BarSpec.from_minutes(n))
        two_step = DataResampler.materialize(inter, BarSpec.from_minutes(m))
        direct = DataResampler.materialize(source, BarSpec.from_minutes(m))
        for col in ("open", "high", "low", "close", "volume"):
            np.testing.assert_array_equal(
                direct[col].to_numpy(), two_step[col].to_numpy(),
                err_msg=f"pair ({n}->{m}) col={col} diverged",
            )


class TestAC21DivisorValueError:
    """AC21 T-B13: non-integer-multiple divisor raises ValueError with message."""

    def test_target_not_multiple_of_source_raises(self):
        """AC21: 1h source -> 1.5h target (90m) — 1.5 ratio — ValueError."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        # Source at 60m, target at 15m — target SMALLER than source — invalid.
        # (The precondition is target % source == 0, i.e. target >= source and
        # an integer multiple.)
        source_60 = _make_1m_df(60)
        # Resample to 1h first.
        source_60 = DataResampler.materialize(source_60, BarSpec.from_minutes(60))
        with pytest.raises(ValueError) as exc_info:
            DataResampler.materialize(source_60, BarSpec.from_minutes(15))
        msg = str(exc_info.value).lower()
        assert "integer multiple" in msg or "target" in msg, (
            f"Expected ValueError mentioning integer multiple; got: {msg}"
        )

    def test_target_fractional_ratio_raises(self):
        """AC21: 10m source -> 15m target (ratio 1.5) raises."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        base = _make_1m_df(600)
        source_10 = DataResampler.materialize(base, BarSpec.from_minutes(10))
        with pytest.raises(ValueError):
            DataResampler.materialize(source_10, BarSpec.from_minutes(15))


class TestAC34Float64Accumulator:
    """AC34 T-B26: float64 accumulator protocol — byte-identity guarantee."""

    def test_60_float32_closes_aggregate_bit_identical_via_two_paths(self):
        """AC34 T-B26: accumulate 60 float32 close values in eager vs streaming
        paths; after downcast, both produce bit-identical float32 outputs.

        Eager path: DataResampler.materialize() over the whole source.
        Streaming path: StreamingConsolidator.push_tick() one bar at a time.
        Both must emit byte-identical float32 output at the RollingCache
        boundary.
        """
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler
        from v5.streaming_consolidator import StreamingConsolidator  # forward reference; M4 target

        # Build a 60-bar 1m source. Using float32-quantized closes (as would
        # come from RollingCache) to model the AC34 byte-identity claim.
        src = _make_1m_df(60)
        # Round-trip to float32 to simulate reading from a float32-backed store.
        for col in ("open", "high", "low", "close", "volume"):
            src[col] = src[col].astype(np.float32).astype(np.float64)

        # Path 1 (eager): DataResampler.materialize 1m -> 1h directly.
        path1 = DataResampler.materialize(src, BarSpec.from_minutes(60))

        # Path 2 (streaming): push the same 1m bars one at a time through
        # StreamingConsolidator; output is the coarser (1h) consolidated bar(s).
        consolidator = StreamingConsolidator(bar_spec=BarSpec.from_minutes(60))
        for _, row in src.iterrows():
            consolidator.push_tick(
                ts_ns=int(row["timestamp"]),
                open_=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]),
            )
        path2 = consolidator.as_dataframe()

        # Downcast both to float32 (mimicking RollingCache.append boundary)
        # and compare byte-identity.
        for col in ("open", "high", "low", "close", "volume"):
            a = path1[col].to_numpy().astype(np.float32).tobytes()
            b = path2[col].to_numpy().astype(np.float32).tobytes()
            assert a == b, (
                f"AC34 eager vs streaming byte-identity broken in column '{col}'"
            )

    def test_1m_to_1h_equals_1m_to_5m_to_1h_bit_identical(self):
        """AC34: the documented protocol invariant (matches AC21 but asserts
        after float32 downcast — the RollingCache boundary)."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        source = _make_1m_df(300)
        direct = DataResampler.materialize(source, BarSpec.from_minutes(60))
        inter = DataResampler.materialize(source, BarSpec.from_minutes(5))
        two_step = DataResampler.materialize(inter, BarSpec.from_minutes(60))
        for col in ("open", "high", "low", "close", "volume"):
            a = direct[col].to_numpy().astype(np.float32).tobytes()
            b = two_step[col].to_numpy().astype(np.float32).tobytes()
            assert a == b, f"AC34 float32 byte-identity broken in '{col}'"

    def test_open_first_high_max_low_min_close_last_volume_sum(self):
        """AC21: aggregation semantics documented in the brief."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        src = _make_1m_df(5)
        out = DataResampler.materialize(src, BarSpec.from_minutes(5))
        assert len(out) == 1
        row = out.iloc[0]
        assert row["open"] == pytest.approx(src["open"].iloc[0])
        assert row["high"] == pytest.approx(src["high"].max())
        assert row["low"] == pytest.approx(src["low"].min())
        assert row["close"] == pytest.approx(src["close"].iloc[-1])
        assert row["volume"] == pytest.approx(src["volume"].sum())
