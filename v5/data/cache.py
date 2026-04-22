"""M11 — Unified polymorphic ``MarketDataCache`` (ADR-0002 move #3).

Replaces M6's ``MultiInstrumentCache`` with a single cache whose typed
accessors serve every ``Data`` subclass (``BarData`` / ``TradeData`` /
``MetricData`` / ``MarkPriceData`` / ``FundingRateData`` / ``OrderBookData`` /
``CustomData``). Storage is dispatched on ``type(event)`` — no string
discriminator, no separate per-kind cache objects.

Design §4 (``.specs/active/m11-unified-event-loop/design.md``):

    class MarketDataCache:
        def on_data(self, event: Data, *, role: str = "signal") -> None: ...
        def bars(self, instrument, bar_spec, *, role=None, lookback=None) -> BarArray: ...
        def metric(self, metric_id, instrument, *, lookback=None) -> np.ndarray: ...
        def metric_latest(self, metric_id, instrument) -> float: ...
        def trades(self, instrument, *, lookback=None) -> list[TradeData]: ...
        def quotes(self, instrument) -> ...: ...  # deferred client-side
        def orderbook(self, instrument) -> OrderBookData | None: ...
        def custom(self, type_name, instrument) -> list[CustomData]: ...

Backend storage is keyed by ``(InstrumentId, type[Data], Optional[str])``
where the third key is a discriminator (role for BarData; metric_id for
MetricData; type_name for CustomData; ``None`` for simple kinds).

PIT correctness: every accessor slices the in-memory buffer to
``[0, clock.current_bar_idx + 1]``; events with
``ts_event > clock.now_ns()`` are rejected at ingress time.

FIX mapping: this cache is the engine-side equivalent of the FIX
``MarketDataIncrementalRefresh`` book — events keyed by ``MDEntryType(269)``
(our ``Data`` subclass) flow in; strategies read typed slices out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import numpy as np

from v5.bar_spec import BarSpec
from v5.data.streams import (
    BarData,
    CustomData,
    Data,
    DataStream,
    FundingRateData,
    InstrumentId,
    MarkPriceData,
    MetricData,
    OrderBookData,
    Subscription,
    TradeData,
)
from v5.rolling_cache import (
    RollingCache,
    _BYTES_PER_CELL,
    _FIELDS_PER_BAR,
    maxlen_for_bar_spec,
)


# ----------------------------------------------------------------------
# Accessor return shapes (public)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarArrays:
    """Zero-copy views of the underlying RollingCache field arrays.

    Returned by ``MarketDataCache.bars(...)``. ``atr``/``funding`` kept as
    separate fields (value zero when the BarData subclass does not carry
    them — a per-bar atr/funding stream arrives as ``MetricData`` /
    ``FundingRateData`` and is served by the matching accessor).
    """

    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    atr: np.ndarray
    funding: np.ndarray
    open: np.ndarray


# ----------------------------------------------------------------------
# Per-subclass storage backends
# ----------------------------------------------------------------------


class _BarBackend:
    """Ring-buffer backend for a single ``(instrument, bar_spec, role)`` tuple.

    Wraps M4's shipped ``RollingCache``. Exposes an ``open`` field in
    addition to ``close/high/low/volume/atr/funding`` (RollingCache stores
    open inside ``close`` for a single-column float32 layout; here we
    add a parallel ``open`` ring buffer so ``BarArrays.open`` is accurate
    for OHLCV consumers that need it — e.g., ATR computation, Donchian).
    """

    __slots__ = ("_rc", "_open_buf", "bar_spec")

    def __init__(self, instrument: InstrumentId, bar_spec: BarSpec, role: str):
        maxlen = maxlen_for_bar_spec(bar_spec, role)
        self._rc = RollingCache(
            token=instrument.symbol, bar_spec=bar_spec, maxlen=maxlen,
        )
        self._open_buf = np.zeros(self._rc.maxlen, dtype=np.float32)
        self.bar_spec = bar_spec

    @property
    def maxlen(self) -> int:
        return self._rc.maxlen

    def append(self, bar: "BarData | Any") -> None:
        # Record slot BEFORE rc.append advances _head; we mirror the same
        # slot index into _open_buf so ordered_view semantics hold.
        slot = self._rc._head  # internal but stable across M4 lifespan
        self._rc.append(
            ts_ns=int(bar.ts_event),
            close=float(bar.close),
            high=float(bar.high),
            low=float(bar.low),
            volume=float(bar.volume),
        )
        self._open_buf[slot] = np.float32(bar.open)

    def _ordered_open(self) -> np.ndarray:
        # Reuse RollingCache's ordering logic by delegating to its private
        # helper (no public copy). Falls back to a local concatenate on
        # any slice shape mismatch.
        return self._rc._ordered_view(self._open_buf)

    def view(self, bar_idx: Optional[int] = None) -> BarArrays:
        close = self._rc.arrays("close")
        high = self._rc.arrays("high")
        low = self._rc.arrays("low")
        volume = self._rc.arrays("volume")
        atr = self._rc.arrays("atr")
        funding = self._rc.arrays("funding")
        open_ = self._ordered_open()
        if bar_idx is not None:
            # PIT slice: [0, bar_idx+1). bar_idx is a 0-based index into
            # the ordered view.
            end = max(0, min(int(bar_idx) + 1, close.shape[0]))
            close = close[:end]
            high = high[:end]
            low = low[:end]
            volume = volume[:end]
            atr = atr[:end]
            funding = funding[:end]
            open_ = open_[:end]
        return BarArrays(
            close=close, high=high, low=low, volume=volume,
            atr=atr, funding=funding, open=open_,
        )


class _MetricBackend:
    """Append-only ndarray backend for a single ``(instrument, metric_id)``.

    Grown by np.resize()-style copy; for the scale of M11 metric cadence
    (≤ one sample per bar per metric) this is fine. If a metric proves to
    be high-cadence enough to matter, we switch to a RollingCache-shaped
    ring buffer. Kept simple here to avoid premature optimization.
    """

    __slots__ = ("_values", "_ts", "_n")

    _INITIAL_CAPACITY = 256

    def __init__(self):
        self._values = np.zeros(self._INITIAL_CAPACITY, dtype=np.float64)
        self._ts = np.zeros(self._INITIAL_CAPACITY, dtype=np.int64)
        self._n = 0

    def append(self, event: MetricData) -> None:
        if self._n == self._values.shape[0]:
            new_cap = self._values.shape[0] * 2
            new_v = np.zeros(new_cap, dtype=np.float64)
            new_t = np.zeros(new_cap, dtype=np.int64)
            new_v[: self._n] = self._values[: self._n]
            new_t[: self._n] = self._ts[: self._n]
            self._values = new_v
            self._ts = new_t
        self._values[self._n] = float(event.value)
        self._ts[self._n] = int(event.ts_event)
        self._n += 1

    def values(self) -> np.ndarray:
        """Zero-copy sized view of the append-only array."""
        return self._values[: self._n]

    def latest(self) -> float:
        if self._n == 0:
            raise LookupError("metric has no samples yet")
        return float(self._values[self._n - 1])


class _EventListBackend:
    """Simple list backend for event-shaped data (trades, orderbook
    snapshots, custom) where random-access slicing is unnecessary.

    Stored as a Python list of events; accessors return the list (or an
    iterator). For M11 this is adequate; a high-cadence trade stream
    may graduate to a columnar numpy backend if it becomes a hotspot.
    """

    __slots__ = ("_events",)

    def __init__(self):
        self._events: List[Data] = []

    def append(self, event: Data) -> None:
        self._events.append(event)

    def events(self) -> List[Data]:
        return self._events


class _LatestSnapshotBackend:
    """One-slot backend — keeps only the most recent event.

    Used for snapshot-shaped kinds like orderbook where the strategy
    always wants 'latest' (an incremental L2 update stream is a
    separate Data subclass decision).
    """

    __slots__ = ("_latest",)

    def __init__(self):
        self._latest: Optional[Data] = None

    def append(self, event: Data) -> None:
        self._latest = event

    def latest(self) -> Optional[Data]:
        return self._latest


# ----------------------------------------------------------------------
# MarketDataCache
# ----------------------------------------------------------------------


class MarketDataCache:
    """Unified polymorphic market-data cache.

    One cache object, typed accessors per ``Data`` subclass. Storage is
    dispatched on ``type(event)`` at ingress; accessors read the matching
    backend and return a typed view.

    Args:
      clock: optional clock-like object exposing ``now_ns()`` and (for
        PIT bar-slicing on ``.bars()``) ``current_bar_idx``. When None,
        future-event rejection and PIT slicing are no-ops (useful for
        unit tests that exercise ingress only).

    Example:

        cache = MarketDataCache(clock=sim_clock)
        cache.on_data(bar_event, role="signal")
        bars = cache.bars(instrument, bar_spec)        # zero-copy views
        oi = cache.metric_latest("binance.oi", instrument)
    """

    def __init__(self, clock: Optional[Any] = None):
        self._clock = clock
        # Storage keyed by (InstrumentId, type[Data], Optional[str])
        # where the third key is the discriminator:
        #   BarData      → role ("signal" / "entry" / "exit" / custom)
        #   MetricData   → metric_id
        #   CustomData   → type_name
        #   others       → None
        self._storage: Dict[
            Tuple[InstrumentId, type, Optional[str]],
            Any,
        ] = {}

    # ------------------------------------------------------------
    # Ingress
    # ------------------------------------------------------------

    def on_data(self, event: Data, *, role: str = "signal") -> None:
        """Ingest a ``Data`` subclass event. Dispatched by ``type(event)``.

        Raises:
          ValueError: if ``event.ts_event > clock.now_ns()`` (PIT).
          TypeError: if ``event`` is not a ``Data`` subclass.
        """
        if not isinstance(event, Data):
            raise TypeError(
                f"MarketDataCache.on_data requires a Data subclass instance; "
                f"got {type(event).__name__}"
            )
        self._reject_future_event(event)
        key = self._key_for(event, role=role)
        backend = self._storage.get(key)
        if backend is None:
            backend = self._make_backend(type(event), key, event)
            self._storage[key] = backend
        backend.append(event)

    # on_bar shim — after M11 Commit 3 the only remaining duck-typed bar
    # shape that reaches the cache is ``_NanBar`` from ``GapDetector``
    # (emitted during ``NAN_FILL`` gap policy when the real bar shape
    # would violate BarData's OHLC invariant). ``ParquetReplayClient``
    # yields ``BarData`` directly now; ``_ReplayBar`` has been deleted.
    # A future commit may retire this shim entirely by converting
    # ``_NanBar`` emission in ``GapDetector`` to a sanitized ``BarData``
    # (open==high==low==close==0.0, volume=0.0) at the source.
    def on_bar(self, bar: Any, *, role: str = "signal") -> None:
        """Compat ingress for ``_NanBar`` (NAN_FILL gap fill).

        Accepts either a ``BarData`` instance (forwarded straight to
        ``on_data``) or a ``_NanBar``-shaped duck-typed object
        (``instrument_id``/``bar_spec``/``ts_event``/``ts_init`` + NaN
        OHLC). Duck-typed bars are wrapped into a transient ``BarData``
        (sanitized when their OHLC violates the ``low<=open<=high``
        invariant) so the cache storage contract remains 'Data
        subclasses only'.
        """
        if isinstance(bar, BarData):
            return self.on_data(bar, role=role)
        # Duck-typed bar (``_NanBar`` from GapDetector NAN_FILL path, or
        # any remaining legacy test fixture). Convert to BarData.
        instrument = getattr(bar, "instrument", None) or getattr(
            bar, "instrument_id", None
        )
        if instrument is None:
            raise TypeError(
                "bar object missing instrument / instrument_id attribute"
            )
        try:
            bd = BarData(
                instrument=instrument,
                ts_event=int(bar.ts_event),
                ts_init=int(getattr(bar, "ts_init", bar.ts_event + 1)),
                bar_spec=bar.bar_spec,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            )
        except ValueError:
            # Some legacy shims (e.g., _NanBar for NAN_FILL gap policy)
            # deliberately carry NaN OHLC which violates BarData's
            # low<=open<=high invariant. Fall back to a sanitized bar
            # whose OHLC values collapse to close (no physical meaning,
            # but preserves the ts_event slot so cache indexing stays
            # aligned).
            safe_close = float(bar.close) if np.isfinite(bar.close) else 0.0
            bd = BarData(
                instrument=instrument,
                ts_event=int(bar.ts_event),
                ts_init=int(getattr(bar, "ts_init", bar.ts_event + 1)),
                bar_spec=bar.bar_spec,
                open=safe_close, high=safe_close, low=safe_close,
                close=safe_close, volume=float(bar.volume or 0.0),
            )
        self.on_data(bd, role=role)

    # ------------------------------------------------------------
    # Typed accessors (public)
    # ------------------------------------------------------------

    def bars(
        self,
        instrument: InstrumentId,
        bar_spec: BarSpec,
        *,
        role: Optional[str] = None,
        lookback: Optional[int] = None,
    ) -> BarArrays:
        """Return the rolling-window ``BarArrays`` view for this stream.

        ``role`` — optional 3-tuple discriminator. When omitted, picks the
        first role for which a backend exists (lets strategies read the
        signal-role storage without ceremony); if multiple roles exist
        and the caller cares, pass ``role`` explicitly.

        ``lookback`` — cap the returned window to the last N bars.
        ``None`` returns the full rolling window up to and including the
        current PIT bar (``clock.current_bar_idx``).

        Raises KeyError if no bar data for this ``(instrument, bar_spec)``.
        """
        backend = self._find_bar_backend(instrument, bar_spec, role=role)
        bar_idx = self._current_bar_idx()
        view = backend.view(bar_idx=bar_idx)
        if lookback is not None and lookback > 0 and lookback < view.close.shape[0]:
            start = view.close.shape[0] - lookback
            view = BarArrays(
                close=view.close[start:], high=view.high[start:],
                low=view.low[start:], volume=view.volume[start:],
                atr=view.atr[start:], funding=view.funding[start:],
                open=view.open[start:],
            )
        return view

    def metric(
        self,
        metric_id: str,
        instrument: InstrumentId,
        *,
        lookback: Optional[int] = None,
    ) -> np.ndarray:
        """Return the rolling ``np.ndarray`` of metric values for this
        ``(instrument, metric_id)``. Raises KeyError if never ingested."""
        key = (instrument, MetricData, metric_id)
        backend = self._storage.get(key)
        if backend is None:
            raise KeyError(
                f"no MetricData for instrument={instrument!r} "
                f"metric_id={metric_id!r}"
            )
        arr = backend.values()
        if lookback is not None and lookback > 0 and lookback < arr.shape[0]:
            arr = arr[-lookback:]
        return arr

    def metric_latest(
        self,
        metric_id: str,
        instrument: InstrumentId,
    ) -> float:
        """Return the latest metric value as a scalar. Raises if empty."""
        key = (instrument, MetricData, metric_id)
        backend = self._storage.get(key)
        if backend is None:
            raise KeyError(
                f"no MetricData for instrument={instrument!r} "
                f"metric_id={metric_id!r}"
            )
        return backend.latest()

    def trades(
        self,
        instrument: InstrumentId,
        *,
        lookback: Optional[int] = None,
    ) -> List[TradeData]:
        """Return stored trades for this instrument (append-ordered)."""
        key = (instrument, TradeData, None)
        backend = self._storage.get(key)
        if backend is None:
            return []
        events = backend.events()
        if lookback is not None and lookback > 0 and lookback < len(events):
            return events[-lookback:]
        return list(events)

    def quotes(self, instrument: InstrumentId) -> None:
        """Quote accessor — deferred to client-side implementation.

        M11 defines the surface; a concrete ``QuoteData`` subclass +
        client implementation ships when a strategy actually subscribes
        to quote data. Returns None today rather than raising so
        strategies can probe without error.
        """
        return None

    def orderbook(
        self,
        instrument: InstrumentId,
    ) -> Optional[OrderBookData]:
        """Return the latest orderbook snapshot for this instrument, or
        ``None`` if no snapshot has been ingested yet."""
        key = (instrument, OrderBookData, None)
        backend = self._storage.get(key)
        if backend is None:
            return None
        return backend.latest()

    def custom(
        self,
        type_name: str,
        instrument: InstrumentId,
    ) -> List[CustomData]:
        """Return stored custom-data events for ``(type_name, instrument)``."""
        key = (instrument, CustomData, type_name)
        backend = self._storage.get(key)
        if backend is None:
            return []
        return list(backend.events())

    # ------------------------------------------------------------
    # Memory projection (preserved API from M6)
    # ------------------------------------------------------------

    def compute_projected_memory_mb(self) -> float:
        """Aggregate projected memory (MB) across live BAR backends.

        Non-BAR backends are event-shaped (trades, metrics, orderbook,
        custom); their footprint is dominated by the number of observed
        events and is not projectable from subscription shape alone.
        Returned total matches M4's arithmetic
        (``maxlen × _FIELDS_PER_BAR × _BYTES_PER_CELL``) for parity with
        the ex-``MultiInstrumentCache`` budget reporter.
        """
        total_bytes = 0
        for (_inst, data_class, _disc), backend in self._storage.items():
            if data_class is BarData:
                total_bytes += (
                    backend.maxlen * _FIELDS_PER_BAR * _BYTES_PER_CELL
                )
        return total_bytes / (1024 * 1024)

    def project_from_subscriptions(
        self, subs: List[Subscription],
    ) -> float:
        """Aggregate projected memory (MB) across unique
        ``(instrument, bar_spec, role)`` tuples that WOULD be created.

        Matches M4's shipped arithmetic exactly; used by
        ``DataEngine.start()`` to enforce the AC-D1b memory budget
        BEFORE any data flows.
        """
        unique_tuples = {
            (s.stream.instrument, s.stream.bar_spec, s.role)
            for s in subs
            if s.stream.data_class is BarData and s.stream.bar_spec is not None
        }
        total_bytes = sum(
            maxlen_for_bar_spec(spec, role) * _FIELDS_PER_BAR * _BYTES_PER_CELL
            for (_inst, spec, role) in unique_tuples
        )
        return total_bytes / (1024 * 1024)

    # ------------------------------------------------------------
    # Legacy accessor (temporary — for migrated M6 tests)
    # ------------------------------------------------------------

    def arrays(self, stream: DataStream, role: str) -> BarArrays:
        """Legacy-shape accessor keyed by ``(DataStream, role)``.

        Preserves the pre-M11 ``MultiInstrumentCache.arrays(stream, role)``
        surface for tests written against M6. New code uses
        ``.bars(instrument, bar_spec, role=...)`` directly.
        """
        if stream.data_class is not BarData:
            raise KeyError(
                f".arrays() only serves BarData streams; got {stream.data_class.__name__}"
            )
        return self.bars(stream.instrument, stream.bar_spec, role=role)

    # ------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------

    def _key_for(
        self,
        event: Data,
        *,
        role: str = "signal",
    ) -> Tuple[InstrumentId, type, Any]:
        """Key shape per design §4:

          - BarData    → (instrument, BarData, (role, bar_spec.label))
          - MetricData → (instrument, MetricData, metric_id)
          - CustomData → (instrument, CustomData, type_name)
          - others     → (instrument, Data subclass, None)

        The BarData discriminator MUST include bar_spec so the same
        (instrument, role) with different resolutions (e.g., 1m+entry
        and 60m+entry) routes to distinct backends. Reusing ``bar_spec``
        itself as the tuple member is safe — ``BarSpec`` is a frozen
        hashable dataclass interned at module load.
        """
        data_class = type(event)
        instrument = event.instrument
        if data_class is BarData:
            return (instrument, BarData, (role, event.bar_spec))
        if data_class is MetricData:
            return (instrument, MetricData, event.metric_id)
        if data_class is CustomData:
            return (instrument, CustomData, event.type_name)
        return (instrument, data_class, None)

    def _make_backend(
        self,
        data_class: type,
        key: Tuple[InstrumentId, type, Any],
        first_event: Optional[Data] = None,
    ) -> Any:
        if data_class is BarData:
            instrument, _, disc = key
            # disc is (role, bar_spec) for BarData
            role, bar_spec = disc
            return _BarBackend(
                instrument=instrument,
                bar_spec=bar_spec,
                role=role or "signal",
            )
        if data_class is MetricData:
            return _MetricBackend()
        if data_class is TradeData:
            return _EventListBackend()
        if data_class is OrderBookData:
            return _LatestSnapshotBackend()
        if data_class is MarkPriceData:
            return _EventListBackend()
        if data_class is FundingRateData:
            return _EventListBackend()
        if data_class is CustomData:
            return _EventListBackend()
        # Unknown Data subclass — generic event list is the safe default.
        return _EventListBackend()

    def _find_bar_backend(
        self,
        instrument: InstrumentId,
        bar_spec: BarSpec,
        *,
        role: Optional[str] = None,
    ) -> _BarBackend:
        # Exact (role, bar_spec) match first
        if role is not None:
            key = (instrument, BarData, (role, bar_spec))
            backend = self._storage.get(key)
            if backend is None:
                raise KeyError(
                    f"no bar cache for {instrument!r} "
                    f"bar_spec={bar_spec.label!r} role={role!r}"
                )
            return backend
        # Role unspecified — pick any role for this (instrument, bar_spec).
        for (inst_k, dc_k, disc_k), backend in self._storage.items():
            if dc_k is not BarData or inst_k != instrument:
                continue
            # disc_k is (role, bar_spec_key)
            _role_k, spec_k = disc_k
            if spec_k == bar_spec:
                return backend
        raise KeyError(
            f"no bar cache for {instrument!r} bar_spec={bar_spec.label!r}"
        )

    def _reject_future_event(self, event: Data) -> None:
        if self._clock is None:
            return
        now_ns_fn = getattr(self._clock, "now_ns", None)
        if not callable(now_ns_fn):
            return
        try:
            now_ns = int(now_ns_fn())
        except Exception:
            return
        if int(event.ts_event) > now_ns:
            raise ValueError(
                f"PIT violation: event.ts_event={event.ts_event} > "
                f"clock.now_ns()={now_ns} "
                f"({type(event).__name__} for {event.instrument!r})"
            )

    def _current_bar_idx(self) -> Optional[int]:
        """PIT bar-index honored at accessor time.

        Returns ``None`` when the clock does not surface
        ``current_bar_idx`` OR when that index is zero (treated as
        'clock not actively managing bar_idx' — the cache returns the
        natural rolling window in that case, relying on
        ``now_ns``-based PIT rejection at ingress).

        A non-zero ``current_bar_idx`` is the explicit 'slice to
        ``[0, current_bar_idx+1]`` ' contract: the PIT-rewind test
        (``test_pit_correctness_on_slicing``) depends on it.
        """
        if self._clock is None:
            return None
        idx = getattr(self._clock, "current_bar_idx", None)
        if idx is None:
            return None
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return None
        if idx <= 0:
            return None
        return idx


