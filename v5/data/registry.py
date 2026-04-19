"""M6 — DataClientRegistry (AC-D17).

Venue-keyed client factory + lookup. Adding a new venue is a registry entry,
not an engine edit.

Priority ordering per brief line 355:
    PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY

Clients are sorted by the minimum transport-priority index across their
supported_modes — which corresponds to the "fastest" transport each client
offers.
"""
from __future__ import annotations

from typing import Callable, Dict, FrozenSet, List, Optional

from v5.data.streams import InstrumentId, TransportMode, Venue

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
    """Venue-keyed client factory. DataEngine consults this on subscribe().

    `register(venue, factory)` preserves insertion order within a priority
    tier (deterministic across runs). `get_clients(instrument)` filters by
    instrument.venue and sorts by transport priority.
    """

    def __init__(self):
        # Per-venue factory list, insertion order preserved
        self._factories: Dict[Venue, List[Callable[..., object]]] = {}
        # Lazy-instantiated client singletons, keyed by (venue, factory_id)
        self._clients: Dict[Venue, List[object]] = {}

    def register(
        self,
        venue: Venue,
        factory: Callable[..., object],
    ) -> None:
        """Register a DataClient factory for `venue`.

        Contract: factory is called eagerly with a single positional argument
        (`config` — passed as None by this registry; Wave F wires the real
        PaperConfig/BacktestConfig). Factory must accept the argument but
        may ignore it. Clients persist; `get_clients()` returns stable
        instances sorted by transport priority.
        """
        if venue not in self._factories:
            self._factories[venue] = []
            self._clients[venue] = []
        self._factories[venue].append(factory)
        # Eagerly instantiate so the client is stable across lookups.
        client = factory(None)
        self._clients[venue].append(client)

    def get_clients(self, instrument: InstrumentId) -> List[object]:
        """Return clients for instrument.venue, sorted PUSH > PULL_ONCE >
        PULL_SCHEDULED > REPLAY. Empty list if venue unregistered."""
        clients = list(self._clients.get(instrument.venue, []))
        # Stable sort — preserves insertion order WITHIN the same priority tier
        clients.sort(key=_client_priority)
        return clients

    def registered_venues(self) -> FrozenSet[Venue]:
        """Expose the active venue set for observability."""
        return frozenset(
            v for v, clients in self._clients.items() if clients
        )
