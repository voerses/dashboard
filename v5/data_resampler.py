"""M4 — DataResampler: eager OHLCV resampling with float64 accumulator protocol.

Covers AC21 (idempotence + strict-divisor ValueError), AC34 (float64
accumulator protocol — the materialized output is float64 so downstream
downcast to float32 at the RollingCache boundary is byte-identical regardless
of whether resampling happened in one step (1m -> Nm) or two (1m -> Km -> Nm
where N is a multiple of K), and AC36 (demand-driven materialization with
chunked iteration above a 2GB projected-memory threshold).

Bit-identity protocol (AC21/AC34)
---------------------------------
For open/high/low/close the aggregation is first / max / min / last — these
are order-independent on float64 so nested resampling is naturally associative.

For volume, float64 summation is NOT associative in general. A two-step
resample (1m -> 5m -> 60m) rounds twice whereas a one-step resample rounds
once, which produces ULP-level divergence. To make volume summation exactly
associative under partition we scale volumes into fixed-point int64 at the
first materialize call and carry the exact integer count through any
subsequent resamples (via a round-trip that is lossless whenever the integer
sum fits in a float64 mantissa, which it does for any realistic crypto
volume). All final outputs are float64 (= int_sum / SCALE), giving:

    materialize(source_1m, M)['volume']
         == materialize(materialize(source_1m, N), M)['volume']

byte-for-byte whenever N divides M.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from v5.bar_spec import BarSpec


_NS_PER_MIN: int = 60 * 1_000_000_000
_NS_PER_DAY: int = 86_400 * 1_000_000_000

# AC36 demand-driven / chunked-iteration constants.
_CHUNK_MEMORY_THRESHOLD_MB: float = 2048.0  # 2 GB — switch to chunked iteration
_CHUNK_WINDOW_DAYS: int = 30                # chunk size when chunked mode active
_FIELDS_PER_BAR: int = 6                    # close, high, low, volume, atr, funding
_BYTES_PER_CELL: int = 4                    # float32 at cache boundary

# Fixed-point scale for volume accumulation. 2^20 ≈ 1.05e6 ticks per unit —
# preserves microgram precision for volumes of magnitude 1e3 while leaving
# ~30 bits of headroom in the int64 accumulator even for summing 10^9 bars.
# Chosen as a power of 2 so that int_sum / SCALE is an exact float64 division
# whenever int_sum fits in 53 bits (and thus the round-trip is lossless).
_VOLUME_SCALE: int = 1 << 20


def _scale_volume_to_int64(vol_f64: np.ndarray) -> np.ndarray:
    """Quantise float64 volumes to int64 ticks of size 1/_VOLUME_SCALE.

    The quantisation error is bounded by 0.5 / _VOLUME_SCALE per element —
    well inside the per-field tolerance from testing.py. The critical property
    is determinism: the SAME float64 input always produces the SAME int64
    output, which is what lets volume.sum be associative under partition.
    """
    return np.rint(vol_f64.astype(np.float64, copy=False) * _VOLUME_SCALE).astype(np.int64)


def _int64_volume_to_f64(int_vol: np.ndarray) -> np.ndarray:
    """Convert int64 scaled-volume back to float64 (int_sum / SCALE).

    Exact division when int_sum has <= 53 bits (SCALE is a power of 2).
    """
    return int_vol.astype(np.float64) / float(_VOLUME_SCALE)


# OHLCV aggregation semantics — pinned by AC21 T-B13.
_OHLCV_AGG: dict[str, str] = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
}


class DataResampler:
    """Eager OHLCV resampler. Uses origin='epoch', label='left', closed='left'.

    Public API:
      - :meth:`materialize` (staticmethod) — eager 1m -> target resolution.
      - :meth:`declare_subscriptions` / :meth:`materialized_specs` — AC36
        demand-driven: only materialize resolutions present in declared
        subscriptions.
      - :meth:`iteration_mode` / :meth:`chunk_window_days` /
        :meth:`chunk_boundaries_ns` — AC36 LRU chunked iteration when
        projected memory > 2 GB (30-day windows aligned to UTC 00:00).
    """

    def __init__(self) -> None:
        # AC36 demand-driven state. Populated by declare_subscriptions().
        self._subscribed_specs: set[BarSpec] = set()
        self._tokens: list[str] = []
        self._duration_days: int = 0
        self._start_ts_ns: int = 0  # UTC 00:00-aligned chunk origin

    # -----------------------------------------------------------------
    # AC36 — Demand-driven materialization + chunked iteration.
    # -----------------------------------------------------------------

    def declare_subscriptions(
        self,
        specs,
        tokens=None,
        duration_days: int = 0,
        start_ts_ns: int = 0,
    ) -> None:
        """Declare the union of BarSpecs downstream subscribers need.

        Only resolutions in ``specs`` will be materialized (see
        :meth:`materialized_specs`). If the projected total memory (a simple
        ``bars_per_day * duration_days * n_tokens * fields * bytes`` sum)
        exceeds the 2 GB threshold, iteration switches to chunked mode.

        Args:
            specs: iterable of BarSpec — subscribed resolutions.
            tokens: optional iterable of token symbols (length used for
                projection). Defaults to empty.
            duration_days: projection horizon in days. Defaults to 0.
            start_ts_ns: UTC 00:00-aligned chunk origin (epoch ns). Must be
                a whole-day multiple (determinism guarantee: same inputs ->
                same chunk sequence). Defaults to epoch 0 (also UTC 00:00).

        Raises:
            ValueError: if ``start_ts_ns`` is not aligned to UTC 00:00.
        """
        if int(start_ts_ns) % _NS_PER_DAY != 0:
            raise ValueError(
                f"start_ts_ns={start_ts_ns} is not aligned to UTC 00:00 "
                f"(must be a multiple of {_NS_PER_DAY})."
            )
        self._subscribed_specs = {s for s in specs if isinstance(s, BarSpec)}
        self._tokens = list(tokens or [])
        self._duration_days = int(duration_days or 0)
        self._start_ts_ns = int(start_ts_ns)

    def materialized_specs(self) -> set[BarSpec]:
        """Return the set of BarSpecs that WILL be materialized (AC36).

        Strictly equal to the declared subscription set — unsubscribed
        resolutions are never produced.
        """
        return set(self._subscribed_specs)

    def _projected_memory_mb(self) -> float:
        """Project RSS (MB) for the declared subscription footprint.

        ``bars_per_day * duration_days * tokens * fields * bytes_per_cell``
        summed across every declared resolution.
        """
        n_tokens = len(self._tokens)
        if n_tokens == 0 or self._duration_days <= 0:
            return 0.0
        total_bytes = 0
        for spec in self._subscribed_specs:
            bars_per_day = 1440 // int(spec.resolution_minutes)
            total_bytes += (
                bars_per_day
                * self._duration_days
                * n_tokens
                * _FIELDS_PER_BAR
                * _BYTES_PER_CELL
            )
        return total_bytes / (1024 * 1024)

    def iteration_mode(self) -> str:
        """Return ``"eager"`` (projection <= 2 GB) or ``"chunked"`` (> 2 GB).

        Controls whether :meth:`iter_chunks` yields a single whole-window
        chunk (eager) or a stream of 30-day chunks (chunked).
        """
        if self._projected_memory_mb() > _CHUNK_MEMORY_THRESHOLD_MB:
            return "chunked"
        return "eager"

    def chunk_window_days(self) -> int:
        """Chunk size in days for chunked iteration (AC36: fixed at 30)."""
        return _CHUNK_WINDOW_DAYS

    def chunk_boundaries_ns(self) -> list[int]:
        """Deterministic UTC 00:00-aligned chunk boundaries (epoch ns).

        In eager mode returns a single-element list ``[start_ts_ns]``.
        In chunked mode returns ``[start, start+30d, start+60d, ...,
        start+duration_days]`` with the final inclusive end boundary also
        aligned to UTC 00:00.
        """
        start = int(self._start_ts_ns)
        # Sanity: start must be day-aligned (enforced in declare_subscriptions)
        if self._duration_days <= 0:
            return [start]
        if self.iteration_mode() == "eager":
            return [start, start + self._duration_days * _NS_PER_DAY]
        boundaries: list[int] = []
        step_ns = _CHUNK_WINDOW_DAYS * _NS_PER_DAY
        end_ns = start + self._duration_days * _NS_PER_DAY
        cur = start
        while cur < end_ns:
            boundaries.append(cur)
            cur += step_ns
        # Final inclusive end boundary — also UTC 00:00 aligned because
        # duration_days is integer days from an aligned start.
        boundaries.append(end_ns)
        return boundaries

    # -----------------------------------------------------------------
    # Eager materialize — used unchanged by callers outside the AC36 path.
    # -----------------------------------------------------------------

    @staticmethod
    def _infer_source_minutes(source_df: pd.DataFrame) -> int:
        """Infer the source resolution (minutes) from the timestamp column.

        Uses the median of positive first-differences — robust against any gaps.
        Raises ValueError if fewer than 2 rows are present.
        """
        ts = source_df["timestamp"].to_numpy(dtype=np.int64, copy=False)
        if ts.shape[0] < 2:
            raise ValueError(
                "Cannot infer source resolution from fewer than 2 rows; "
                "provide a source_df with at least 2 timestamps."
            )
        diffs = np.diff(ts)
        if np.any(diffs <= 0):
            raise ValueError("source_df['timestamp'] must be strictly monotonic.")
        med_ns = int(np.median(diffs))
        if med_ns % _NS_PER_MIN != 0:
            raise ValueError(
                f"Source resolution {med_ns} ns is not a whole number of minutes."
            )
        return med_ns // _NS_PER_MIN

    @staticmethod
    def materialize(source_df: pd.DataFrame, target_spec: BarSpec) -> pd.DataFrame:
        """Resample source_df (OHLCV + timestamp) to target_spec resolution.

        Args:
            source_df: pd.DataFrame with columns
                ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                where 'timestamp' is int64 epoch-nanoseconds.
            target_spec: BarSpec. Its resolution_minutes MUST be an integer
                multiple of the inferred source resolution.

        Returns:
            pd.DataFrame with the same columns, at target_spec resolution.
            OHLCV columns are float64 (AC34 accumulator protocol — downcast
            to float32 only happens at RollingCache.append boundary).

        Raises:
            ValueError: if target_spec.resolution_minutes is not an integer
                multiple of the inferred source resolution.
        """
        source_minutes = DataResampler._infer_source_minutes(source_df)
        target_minutes = int(target_spec.resolution_minutes)
        if target_minutes < source_minutes or target_minutes % source_minutes != 0:
            raise ValueError(
                f"target resolution {target_minutes}m is not an integer multiple "
                f"of source resolution {source_minutes}m (ratio must be an "
                f"integer >= 1)."
            )
        if target_minutes == source_minutes:
            # No-op resample — normalize columns but preserve values.
            out = source_df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
            for col in ("open", "high", "low", "close", "volume"):
                # pandas 3.0: Copy-on-Write makes copy= a no-op; dropping the
                # kwarg for M10 AC #26 (zero-warnings cleanup).
                out[col] = out[col].astype(np.float64)
            return out.reset_index(drop=True)

        # Build a DatetimeIndex from the int64 timestamps and resample.
        ts = source_df["timestamp"].to_numpy(dtype=np.int64, copy=False)
        vol_f64 = source_df["volume"].to_numpy(dtype=np.float64, copy=False)
        # Quantise volume to int64 ticks for exact summation across nested
        # resamples (AC21 idempotence under partition).
        vol_int = _scale_volume_to_int64(vol_f64)

        df = pd.DataFrame({
            "open": source_df["open"].to_numpy(dtype=np.float64, copy=False),
            "high": source_df["high"].to_numpy(dtype=np.float64, copy=False),
            "low": source_df["low"].to_numpy(dtype=np.float64, copy=False),
            "close": source_df["close"].to_numpy(dtype=np.float64, copy=False),
            "volume_i64": vol_int,
        })
        df.index = pd.DatetimeIndex(ts.astype("datetime64[ns]"), name="ts")
        rule = f"{target_minutes}min"
        resampler = df.resample(
            rule, origin="epoch", label="left", closed="left",
        )
        agg_ohlc = resampler.agg(_OHLCV_AGG)
        # Volume summed exactly in int64 (pandas sum over int64 is exact).
        agg_vol = resampler["volume_i64"].sum()
        # Drop empty bins (OHLC rows where no source row landed -> NaN).
        mask = ~agg_ohlc.isna().any(axis=1)
        agg_ohlc = agg_ohlc[mask]
        agg_vol = agg_vol[mask]

        out_ts = agg_ohlc.index.astype("datetime64[ns]").astype(np.int64)
        out = pd.DataFrame({
            "timestamp": out_ts,
            "open": agg_ohlc["open"].to_numpy(dtype=np.float64, copy=False),
            "high": agg_ohlc["high"].to_numpy(dtype=np.float64, copy=False),
            "low": agg_ohlc["low"].to_numpy(dtype=np.float64, copy=False),
            "close": agg_ohlc["close"].to_numpy(dtype=np.float64, copy=False),
            "volume": _int64_volume_to_f64(
                agg_vol.to_numpy(dtype=np.int64, copy=False)
            ),
        })
        return out.reset_index(drop=True)
