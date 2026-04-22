"""M11 Commit 4 — ParquetMetricsReplayClient (Task 4.4-4.5).

Generic, strategy-agnostic replay client for ``MetricData`` streams. One
client instance serves ANY metric that has an entry in the supplied
``MetricsManifest``; adding a new metric is a manifest entry, NOT engine
code.

Design §2 (``.specs/active/m11-unified-event-loop/design.md`` scope map):
serves ``MetricData`` in REPLAY + PULL_ONCE modes, mirrors the streaming
pattern of ``ParquetReplayClient`` (Commit 3).

Source file layout per manifest:
  ``{fixture_root}/{source_file_pattern_resolved_with_{token}}``

Where ``{token}`` is replaced by ``stream.instrument.symbol``. The client
is symbol-convention-agnostic — whatever the instrument's symbol field
contains is the value substituted.

Replay semantics:
  1. Look up ``MetricDefinition`` for ``stream.discriminator`` (metric_id).
     Unknown metric_id → ``KeyError`` at replay time (BEFORE yielding).
  2. Resolve source path; missing file → empty iterator (not an error).
  3. Stream-read parquet via ``pyarrow.parquet.ParquetFile.iter_batches``
     to keep memory bounded — matches Commit 3's pattern.
  4. Filter raw rows to ``[start_ns, end_ns)`` on the timestamp column.
  5. Resample native → target resolution per manifest entry
     (``resample_to``, ``resample_agg``). ``label='right', closed='left'``
     makes each emitted event PIT-aligned: ``ts_event`` is the end of
     the resample window, so strategies reading ``metric_latest`` at
     ``clock.now_ns == ts_event`` see the newly-closed window's value.
  6. Yield ``MetricData`` for each non-NaN resampled value.

FIX mapping: ``MetricData`` is a vendor extension; ``metric_id`` takes the
role of a custom ``MDEntryType(269)`` tag. The replay client is the
analog of a ``MarketDataRequest(V)`` / ``MarketDataIncrementalRefresh``
responder in REPLAY mode.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, FrozenSet, Iterator, List, Optional

import numpy as np
import pandas as pd

from v5.data.clients.parquet_replay import _extract_ts_ns_column
from v5.data.metrics import MetricDefinition, MetricsManifest
from v5.data.streams import (
    DataStream,
    MetricData,
    TransportMode,
    Venue,
)


# Map manifest ``resample_to`` string → pandas offset alias. Most aliases
# pass through unchanged (``"1h"``, ``"5min"``, ``"1d"``), but pandas
# deprecated upper-case ``H`` / ``T`` / ``S`` aliases in 2.2; this map lets
# us normalize without hard-coding assumptions elsewhere in the codebase.
_RESAMPLE_ALIASES = {
    "1h": "1h",
    "5m": "5min",
    "1m": "1min",
    "1d": "1d",
}


def _resolve_resample_alias(label: str) -> str:
    return _RESAMPLE_ALIASES.get(label, label)


class ParquetMetricsReplayClient:
    """Deterministic parquet playback for ``MetricData`` streams.

    One client, one manifest, arbitrary many metrics. Backtest-only
    (``supported_modes == {REPLAY, PULL_ONCE}``); ``subscribe`` /
    ``subscribe_scheduled`` raise ``NotImplementedError`` identical to
    :class:`ParquetReplayClient` (Commit 3 pattern).

    Args:
      manifest: ``MetricsManifest`` describing each known metric's file
        pattern, column names, and resampling rule.
      fixture_root: base directory to resolve ``source_file_pattern``
        against. Defaults to the project-level ``data/`` tree (same
        convention as :class:`ParquetReplayClient`).
    """

    venue: Venue = Venue.BINANCE
    supported_modes: FrozenSet[TransportMode] = frozenset({
        TransportMode.REPLAY, TransportMode.PULL_ONCE,
    })

    _DEFAULT_BATCH_SIZE: int = 10_000

    def __init__(
        self,
        manifest: MetricsManifest,
        fixture_root: Optional[Path] = None,
    ):
        self.manifest = manifest
        self._fixture_root = (
            Path(fixture_root)
            if fixture_root is not None
            else Path(__file__).resolve().parents[3] / "data"
        )

    # ------------------------------------------------------------
    # Protocol conformance
    # ------------------------------------------------------------

    def supports(self, stream: DataStream, mode: TransportMode) -> bool:
        """Return True iff this client can serve ``stream`` in ``mode``.

        Accepts only ``MetricData`` streams whose ``discriminator``
        (metric_id) has a manifest entry, and only in REPLAY/PULL_ONCE.
        """
        if mode not in self.supported_modes:
            return False
        if stream.data_class is not MetricData:
            return False
        metric_id = stream.discriminator
        if not metric_id:
            return False
        return self.manifest.get(metric_id) is not None

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def request(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> List[MetricData]:
        """PULL_ONCE snapshot — materialize the requested window."""
        return list(self.replay(stream, start_ns, end_ns))

    def replay(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[MetricData]:
        """REPLAY — yield ``MetricData`` events in chronological order.

        Looks up the manifest entry for ``stream.discriminator``. Raises
        ``KeyError`` at replay time if the metric_id is unknown — this is
        the eager failure mode expected by
        ``test_unknown_metric_id_rejected``: passing a bogus discriminator
        must fail visibly, not silently return zero events.
        """
        if stream.data_class is not MetricData:
            raise ValueError(
                f"ParquetMetricsReplayClient only serves MetricData streams; "
                f"got data_class={stream.data_class.__name__}"
            )
        metric_id = stream.discriminator
        if not metric_id:
            raise ValueError(
                "MetricData stream requires a non-empty discriminator "
                "(metric_id); DataStream validation should have caught this."
            )
        mdef = self.manifest.get(metric_id)
        if mdef is None:
            raise KeyError(
                f"metric_id {metric_id!r} is not defined in the manifest; "
                f"known ids={list(self.manifest.ids())}"
            )
        yield from self._replay_metric(
            stream, mdef, int(start_ns), int(end_ns),
        )

    # ------------------------------------------------------------
    # Core replay body
    # ------------------------------------------------------------

    def _replay_metric(
        self,
        stream: DataStream,
        mdef: MetricDefinition,
        start_ns: int,
        end_ns: int,
    ) -> Iterator[MetricData]:
        path = self._resolve_path(stream, mdef)
        if not path.exists():
            # A missing file is not an error — the metric may simply have
            # no samples for this token. Empty iterator is the correct
            # behavior (mirrors ParquetReplayClient's missing-file branch).
            return

        # Collect the filtered, native-resolution samples into a single
        # Series indexed by ts_event (ns, UTC). Streaming row-group
        # iteration keeps memory bounded per batch; the resample step
        # then runs on the already-windowed subset.
        ts_list: List[np.ndarray] = []
        val_list: List[np.ndarray] = []
        for ts_arr, val_arr in self._iter_metric_batches(
            path, mdef, start_ns, end_ns,
        ):
            if ts_arr.size == 0:
                continue
            ts_list.append(ts_arr)
            val_list.append(val_arr)
        if not ts_list:
            return

        ts_ns = np.concatenate(ts_list)
        values = np.concatenate(val_list)
        # Sort by ts (pyarrow batches are usually in order but the parquet
        # spec doesn't guarantee it for all writer configurations).
        order = np.argsort(ts_ns, kind="stable")
        ts_ns = ts_ns[order]
        values = values[order]

        # Resample per manifest. When ``resample_to`` is empty (or equals
        # the native resolution), yield native-resolution events directly.
        target = (mdef.resample_to or "").strip()
        native = mdef.native_resolution.strip()
        if not target or target == native:
            yield from self._yield_native(stream, mdef, ts_ns, values)
            return
        yield from self._yield_resampled(stream, mdef, ts_ns, values)

    # ------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------

    def _resolve_path(
        self, stream: DataStream, mdef: MetricDefinition,
    ) -> Path:
        """Fill ``{token}`` (and legacy ``{SYMBOL}``) placeholder and join
        to ``fixture_root``. The client is symbol-convention-agnostic —
        whatever ``instrument.symbol`` contains is substituted as-is.
        """
        token = stream.instrument.symbol
        resolved = mdef.source_file_pattern.format(
            token=token, SYMBOL=token, symbol=token,
        )
        return self._fixture_root / resolved

    # ------------------------------------------------------------
    # Batch-streaming read
    # ------------------------------------------------------------

    def _iter_metric_batches(
        self,
        path: Path,
        mdef: MetricDefinition,
        start_ns: int,
        end_ns: int,
    ) -> Iterator[tuple]:
        """Yield ``(ts_ns[int64 array], values[float64 array])`` tuples
        per batch, already filtered to ``[start_ns, end_ns)``.

        Uses pyarrow's row-group iterator for bounded memory; falls back
        to a single ``pd.read_parquet`` when pyarrow is unavailable.
        """
        try:
            import pyarrow.parquet as pq
        except ImportError:  # pragma: no cover — pyarrow is a hard dep
            pq = None

        if pq is not None:
            try:
                pf = pq.ParquetFile(path)
                for batch in pf.iter_batches(
                    batch_size=self._DEFAULT_BATCH_SIZE,
                ):
                    df = batch.to_pandas()
                    ts_ns, vals = self._extract_ts_and_values(
                        df, mdef, start_ns, end_ns,
                    )
                    yield ts_ns, vals
                return
            except (KeyError, ValueError):
                # Column-shape mismatch or missing column — fall through
                # to the pandas fallback path which tolerates a wider
                # range of parquet layouts. Narrow the catch to avoid
                # masking unrelated pyarrow errors.
                pass

        df = pd.read_parquet(path, engine="pyarrow")
        ts_ns, vals = self._extract_ts_and_values(
            df, mdef, start_ns, end_ns,
        )
        yield ts_ns, vals

    def _extract_ts_and_values(
        self,
        df: pd.DataFrame,
        mdef: MetricDefinition,
        start_ns: int,
        end_ns: int,
    ) -> tuple:
        """Derive ``(ts_ns, values)`` arrays from a batch DataFrame.

        Supports three timestamp shapes:
          1. ``mdef.timestamp_col`` is a DataFrame column → use it
          2. DataFrame's index IS the timestamp (e.g. saved with
             ``df.set_index('timestamp').to_parquet``) → use the index
          3. Generic fallback via ``_extract_ts_ns_column`` (the same
             helper ``ParquetReplayClient`` uses) — handles DatetimeIndex
             in ms/us/ns resolution and legacy column names
        """
        ts_col = mdef.timestamp_col
        if ts_col in df.columns:
            ts_series = pd.to_datetime(df[ts_col], utc=True)
            ts_ns_full = (
                ts_series
                .astype("datetime64[ns, UTC]")
                .astype("int64")
                .to_numpy()
            )
        elif isinstance(df.index, pd.DatetimeIndex) and (
            df.index.name == ts_col or ts_col == df.index.name
        ):
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            else:
                idx = idx.tz_convert("UTC")
            ts_ns_full = idx.astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        elif ts_col in ("timestamp", "open_time", "ts_event", "time"):
            # Use the shared timestamp-extraction helper from
            # ParquetReplayClient for consistent ms/us/ns normalization.
            ts_ns_full = _extract_ts_ns_column(df).to_numpy()
        elif isinstance(df.index, pd.DatetimeIndex):
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            else:
                idx = idx.tz_convert("UTC")
            ts_ns_full = idx.astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        else:
            raise KeyError(
                f"timestamp_col {ts_col!r} not found in parquet; "
                f"columns={list(df.columns)}, "
                f"index_type={type(df.index).__name__}"
            )

        val_col = mdef.value_col
        if val_col not in df.columns:
            raise KeyError(
                f"value_col {val_col!r} not found in parquet; "
                f"columns={list(df.columns)}"
            )
        values_full = df[val_col].to_numpy(dtype=mdef.dtype or "float64")

        # In-window mask with [start_ns, end_ns) semantics.
        ts_ns_full = ts_ns_full.astype("int64", copy=False)
        mask = (ts_ns_full >= start_ns) & (ts_ns_full < end_ns)
        return ts_ns_full[mask], values_full[mask]

    # ------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------

    def _yield_native(
        self,
        stream: DataStream,
        mdef: MetricDefinition,
        ts_ns: np.ndarray,
        values: np.ndarray,
    ) -> Iterator[MetricData]:
        """Emit native-resolution events without resampling."""
        for t, v in zip(ts_ns.tolist(), values.tolist()):
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if np.isnan(fv):
                continue
            yield MetricData(
                instrument=stream.instrument,
                ts_event=int(t),
                ts_init=int(t),
                metric_id=mdef.metric_id,
                value=fv,
            )

    def _yield_resampled(
        self,
        stream: DataStream,
        mdef: MetricDefinition,
        ts_ns: np.ndarray,
        values: np.ndarray,
    ) -> Iterator[MetricData]:
        """Resample native samples to ``mdef.resample_to`` and emit.

        Uses ``pandas.Series.resample(rule, label='right', closed='left')``:
        the bin ``[t0, t1)`` is labeled by ``t1`` (end-of-window). That
        makes emitted events PIT-aligned — the event's ``ts_event`` is
        the instant at which the window CLOSED, so a strategy reading
        ``metric_latest`` at ``clock.now_ns >= t1`` sees the newly-closed
        value without look-ahead.

        Supported ``resample_agg``: ``"last"``, ``"mean"``, ``"max"``,
        ``"min"``. Unknown aggregation raises ``ValueError``.
        """
        series = pd.Series(
            values,
            index=pd.DatetimeIndex(
                pd.to_datetime(ts_ns, utc=True),
                name="ts",
            ),
        )
        rule = _resolve_resample_alias(mdef.resample_to)
        resampler = series.resample(rule, label="right", closed="left")
        agg = mdef.resample_agg.lower()
        if agg == "last":
            resampled = resampler.last()
        elif agg == "mean":
            resampled = resampler.mean()
        elif agg == "max":
            resampled = resampler.max()
        elif agg == "min":
            resampled = resampler.min()
        else:
            raise ValueError(
                f"resample_agg {mdef.resample_agg!r} not recognized; "
                f"supported: last|mean|max|min"
            )
        # Drop NaN buckets (empty windows have no sample to emit).
        resampled = resampled.dropna()
        if resampled.empty:
            return
        idx_ns = resampled.index.astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        vals = resampled.to_numpy(dtype=mdef.dtype or "float64")
        for t, v in zip(idx_ns.tolist(), vals.tolist()):
            fv = float(v)
            if np.isnan(fv):
                continue
            yield MetricData(
                instrument=stream.instrument,
                ts_event=int(t),
                ts_init=int(t),
                metric_id=mdef.metric_id,
                value=fv,
            )

    # ------------------------------------------------------------
    # LiveDataClient ops — refused (mirrors ParquetReplayClient)
    # ------------------------------------------------------------

    def subscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "ParquetMetricsReplayClient does not support PUSH (WS streaming). "
            "Use a venue-native metric client for live subscriptions."
        )

    def unsubscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "ParquetMetricsReplayClient does not support PUSH (WS streaming)."
        )

    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None:
        raise NotImplementedError(
            "ParquetMetricsReplayClient does not support PULL_SCHEDULED "
            "(cron REST)."
        )


__all__ = ["ParquetMetricsReplayClient"]
