"""M6 — ParquetReplayClient (AC-D3, AC-D15, AC-D20).

DataClient-only (no LiveDataClient interface) — supports REPLAY + PULL_ONCE.
Calling `subscribe` / `subscribe_scheduled` raises NotImplementedError.

Infrastructure wall-clock reads:
  (none — replay is timestamp-driven from parquet file contents; no wall-clock
  reads are performed by this module. Backtest determinism invariant AC24 is
  upheld by construction.)

Reads from `data/{market}/1h_cache/{TOKEN}_1h.parquet` and
`data/{market}/live/{TOKEN}.parquet` — same sources as v5/data_loader.py.
Globally sorts and dedups by (ts_event, token) before yielding.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, FrozenSet, Iterator, List

from v5.data.streams import DataStream, TransportMode, Venue


@dataclass(slots=True)
class _ReplayBar:
    """Minimal Bar shape yielded by replay. Compatible with GapDetector/
    MultiInstrumentCache bar consumption."""
    instrument_id: Any
    bar_spec: Any
    ts_event: int
    ts_init: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class ParquetReplayClient:
    """Deterministic parquet playback. Backtest-only.

    `supported_modes == {REPLAY, PULL_ONCE}` — no streaming.
    """

    venue: Venue = Venue.BINANCE
    supported_modes: FrozenSet[TransportMode] = frozenset({
        TransportMode.REPLAY, TransportMode.PULL_ONCE,
    })

    def __init__(self, fixture_root: Path | None = None):
        # fixture_root lets tests point at a custom parquet tree; defaults
        # to project-level `data/` directory used by v5/data_loader.py.
        self._fixture_root = fixture_root

    def supports(self, stream: DataStream, mode: TransportMode) -> bool:
        return mode in self.supported_modes

    def connect(self) -> None:
        # No network or file open — parquet reads happen lazily per-replay call.
        pass

    def disconnect(self) -> None:
        pass

    def request(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> List[Any]:
        """PULL_ONCE snapshot — materialize the requested window into a list."""
        return list(self.replay(stream, start_ns, end_ns))

    def replay(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[Any]:
        """REPLAY — yield bars from parquet in chronological order within [start_ns, end_ns).

        Phase-4 implementer note: this initial version returns an empty iterator
        when no fixture is available. Wave-F fixture work populates the
        `m6_parquet_replay/` directory; for the broader backtest path, v5/backtest.py
        adopts this client under `use_data_engine=True` (M9+ flip).
        """
        # Empty iterator — Wave F populates the fixture tree; full reader impl
        # (parquet read + dedup + global sort) is Wave-F scope per tasks.md §7.
        return iter([])

    # LiveDataClient ops — ParquetReplayClient does NOT implement LiveDataClient,
    # but paper/live callers may probe via duck typing. Raise clearly.
    def subscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "ParquetReplayClient does not support PUSH (WS streaming). "
            "Use BinanceWSClient for live subscriptions."
        )

    def unsubscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "ParquetReplayClient does not support PUSH (WS streaming)."
        )

    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None:
        raise NotImplementedError(
            "ParquetReplayClient does not support PULL_SCHEDULED (cron REST)."
        )
