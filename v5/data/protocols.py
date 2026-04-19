"""M6 — DataClient + LiveDataClient Protocols (AC-D3).

Split per Addition 5:
  - DataClient: historical + replay-capable. PULL_ONCE + REPLAY.
  - LiveDataClient(DataClient): live venues. Adds PUSH + PULL_SCHEDULED.

Both are @runtime_checkable so T-D15 can use isinstance() to exercise
unsupported-op paths.

FIX session-type analogy: the split mirrors FIX session-type separation —
  - DataClient consumes SubscriptionRequestType(263)=0 Snapshot (one-shot
    request/replay).
  - LiveDataClient additionally handles SubscriptionRequestType(263)=1
    Snapshot+Updates (subscribe for PUSH, subscribe_scheduled for cron-driven
    FullRefresh).
"""
from __future__ import annotations

from typing import Any, FrozenSet, Iterator, List, Protocol, runtime_checkable

from v5.data.streams import DataStream, TransportMode, Venue


@runtime_checkable
class DataClient(Protocol):
    """Historical + replay-capable client.

    Attributes:
      venue:           Venue enum (typed — not free string)
      supported_modes: frozenset[TransportMode] self-declared capability
    """

    venue: Venue
    supported_modes: FrozenSet[TransportMode]

    def supports(self, stream: DataStream, mode: TransportMode) -> bool: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    # PULL_ONCE — REST backfill / historical window
    def request(self, stream: DataStream, start_ns: int, end_ns: int) -> List[Any]: ...

    # REPLAY — parquet playback (backtest)
    def replay(self, stream: DataStream, start_ns: int, end_ns: int) -> Iterator[Any]: ...


@runtime_checkable
class LiveDataClient(DataClient, Protocol):
    """Live venue client — adds PUSH (WS streaming) + PULL_SCHEDULED (cron REST)."""

    def subscribe(self, stream: DataStream) -> None: ...  # PUSH
    def unsubscribe(self, stream: DataStream) -> None: ...  # PUSH teardown
    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None: ...  # PULL_SCHEDULED
