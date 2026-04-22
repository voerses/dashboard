"""M6/M11 — DataClientRegistry (AC-D17).

M11 (ADR-0002 move #1): the registry key is now ``(Venue, type[Data])`` —
clients register per (venue, Data subclass) pair, and ``get_clients`` filters
by both. A single client may register for multiple data-class pairs (e.g.,
``BinanceWSClient`` serves ``BarData``, ``TradeData``, ``MarkPriceData``).

Priority ordering per brief line 355:
    PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY

Clients are sorted by the minimum transport-priority index across their
supported_modes — which corresponds to the "fastest" transport each client
offers.
"""
from __future__ import annotations

from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from v5.data.streams import Data, InstrumentId, TransportMode, Venue

# Lower index = higher priority
_MODE_PRIORITY: Dict[TransportMode, int] = {
    TransportMode.PUSH: 0,
    TransportMode.PULL_ONCE: 1,
    TransportMode.PULL_SCHEDULED: 2,
    TransportMode.REPLAY: 3,
}


def _client_priority(client) -> int:
    """Client's priority = min (fastest) transport tier it supports.

    A client supporting {PUSH, PULL_ONCE} gets priority 0 (PUSH).
    A client supporting only {REPLAY} gets priority 3.
    """
    modes: FrozenSet[TransportMode] = getattr(client, "supported_modes", frozenset())
    if not modes:
        return _MODE_PRIORITY[TransportMode.REPLAY] + 1  # unknown → last
    return min(_MODE_PRIORITY[m] for m in modes)


class DataClientRegistry:
    """Registry keyed by ``(Venue, type[Data])``.

    M11: ``register(venue, data_class, factory)`` binds a factory to a
    ``(venue, Data subclass)`` pair. ``get_clients(instrument, data_class)``
    filters by both. A single factory may be called once per pair, producing
    the same underlying client instance — the registry dedupes on factory
    identity so shared clients serve multiple data classes.

    Priority ordering within a ``(venue, data_class)`` bucket preserves
    insertion order for the same transport tier; tiers are sorted PUSH >
    PULL_ONCE > PULL_SCHEDULED > REPLAY.
    """

    def __init__(self):
        # Primary store keyed by (venue, data_class): list of clients
        self._clients_by_key: Dict[Tuple[Venue, type], List[object]] = {}
        # Back-compat observability: all registered venues.
        self._venues_seen: set = set()

    def register(
        self,
        venue: Venue,
        data_class: type,
        factory: Callable[..., object],
    ) -> None:
        """Register a DataClient factory for ``(venue, data_class)``.

        Contract: factory is called eagerly with a single positional argument
        (``config`` — passed as ``None`` by this registry; real engine
        wiring passes a concrete PaperConfig/BacktestConfig). If a factory
        returns a client that is already present in the target bucket (by
        object identity), the duplicate registration is a no-op so the
        caller may safely re-register the same client under multiple
        ``(venue, data_class)`` pairs.
        """
        if not isinstance(data_class, type) or not issubclass(data_class, Data):
            raise TypeError(
                f"data_class must be a subclass of Data, got {data_class!r}"
            )
        client = factory(None)
        key = (venue, data_class)
        bucket = self._clients_by_key.setdefault(key, [])
        if client not in bucket:
            bucket.append(client)
        self._venues_seen.add(venue)

    def get_clients(
        self,
        instrument: InstrumentId,
        data_class: Optional[type] = None,
    ) -> List[object]:
        """Return clients matching ``(instrument.venue, data_class)``, sorted
        PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY.

        If ``data_class`` is None, returns the UNION of all clients
        registered under ``instrument.venue`` across any ``Data`` subclass —
        preserved for back-compat with M6 engine code paths that did not
        yet type their subscription lookups by ``Data`` subclass.
        """
        if data_class is not None:
            key = (instrument.venue, data_class)
            clients = list(self._clients_by_key.get(key, []))
        else:
            seen: List[object] = []
            for (venue, _dc), bucket in self._clients_by_key.items():
                if venue != instrument.venue:
                    continue
                for c in bucket:
                    if c not in seen:
                        seen.append(c)
            clients = seen
        # Stable sort — preserves insertion order WITHIN the same priority tier
        clients.sort(key=_client_priority)
        return clients

    def registered_venues(self) -> FrozenSet[Venue]:
        """Expose the active venue set for observability."""
        return frozenset(
            venue for (venue, _dc), bucket in self._clients_by_key.items()
            if bucket
        )
