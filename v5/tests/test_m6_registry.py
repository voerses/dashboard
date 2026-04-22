"""M6/M11 — DataClientRegistry priority cascade + venue isolation (T-D17 / AC-D17).

Covers:
  - AC-D17 T-D17: DataClientRegistry sorts clients by PUSH > PULL_ONCE > REPLAY
    within a venue. M11: ``register(venue, data_class, factory)`` is keyed by
    ``(Venue, type[Data])``; ``get_clients(instrument)`` returns
    venue-filtered, priority-sorted clients. registered_venues() exposes the
    active venue set; different venues are isolated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class _FakeClient:
    """Minimal DataClient-shaped fake for registry tests."""

    def __init__(self, venue, supported_modes, label=""):
        self.venue = venue
        self.supported_modes = supported_modes
        self.label = label

    def supports(self, stream, mode):
        return mode in self.supported_modes

    def connect(self):
        pass

    def disconnect(self):
        pass

    def request(self, stream, start_ns, end_ns):
        return []

    def replay(self, stream, start_ns, end_ns):
        return iter([])


def _make_instrument(symbol="BTCUSDT", venue=None):
    from v5.data.streams import InstrumentId, Venue
    if venue is None:
        venue = Venue.BINANCE
    return InstrumentId(symbol=symbol, venue=venue, asset_class="perp")


class TestRegistryPriorityCascade:
    """T-D17 / AC-D17 — PUSH > PULL_ONCE > REPLAY within a venue."""

    def test_empty_registry_returns_no_clients(self):
        from v5.data.registry import DataClientRegistry
        reg = DataClientRegistry()
        assert reg.get_clients(_make_instrument()) == []

    def test_priority_push_before_pull_once_before_replay(self):
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, TransportMode, Venue

        reg = DataClientRegistry()
        replay = _FakeClient(Venue.BINANCE, frozenset({TransportMode.REPLAY}), "replay")
        rest = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PULL_ONCE}), "rest")
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}), "ws")

        # Register in deliberately-reversed priority order; registry must sort.
        reg.register(Venue.BINANCE, BarData, lambda _cfg: replay)
        reg.register(Venue.BINANCE, BarData, lambda _cfg: rest)
        reg.register(Venue.BINANCE, BarData, lambda _cfg: ws)

        clients = reg.get_clients(_make_instrument())
        labels = [c.label for c in clients]
        assert labels == ["ws", "rest", "replay"]

    def test_priority_includes_pull_scheduled_tier(self):
        """Brief line 355: priority is `PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY`.

        Covers the PULL_SCHEDULED tier explicitly — otherwise an impl that
        swaps PULL_SCHEDULED above PULL_ONCE would ship green.
        """
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, TransportMode, Venue

        reg = DataClientRegistry()
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}), "ws")
        pull_once = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PULL_ONCE}), "pull_once")
        pull_sched = _FakeClient(
            Venue.BINANCE, frozenset({TransportMode.PULL_SCHEDULED}), "pull_sched"
        )
        replay = _FakeClient(Venue.BINANCE, frozenset({TransportMode.REPLAY}), "replay")

        # Register in reversed priority to force the sort to do real work
        reg.register(Venue.BINANCE, BarData, lambda _c: replay)
        reg.register(Venue.BINANCE, BarData, lambda _c: pull_sched)
        reg.register(Venue.BINANCE, BarData, lambda _c: pull_once)
        reg.register(Venue.BINANCE, BarData, lambda _c: ws)

        clients = reg.get_clients(_make_instrument())
        labels = [c.label for c in clients]
        assert labels == ["ws", "pull_once", "pull_sched", "replay"], (
            f"PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY priority broken; got {labels}"
        )

    def test_registered_venues_exposes_active_set(self):
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, TransportMode, Venue

        reg = DataClientRegistry()
        binance = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}))
        reg.register(Venue.BINANCE, BarData, lambda _c: binance)

        venues = reg.registered_venues()
        assert venues == frozenset({Venue.BINANCE})

    def test_venue_isolation(self):
        """A client registered for OKX is invisible to Binance lookups."""
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, TransportMode, Venue

        reg = DataClientRegistry()
        binance = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}), "bin")
        okx = _FakeClient(Venue.OKX, frozenset({TransportMode.PUSH}), "okx")
        reg.register(Venue.BINANCE, BarData, lambda _c: binance)
        reg.register(Venue.OKX, BarData, lambda _c: okx)

        clients = reg.get_clients(_make_instrument(venue=Venue.BINANCE))
        assert [c.label for c in clients] == ["bin"]
        assert Venue.OKX not in {c.venue for c in clients}

    def test_two_clients_same_tier_insertion_order_preserved(self):
        """Within a tier, insertion order is preserved (deterministic)."""
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, TransportMode, Venue

        reg = DataClientRegistry()
        rest_a = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PULL_ONCE}), "rest_a")
        rest_b = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PULL_ONCE}), "rest_b")
        reg.register(Venue.BINANCE, BarData, lambda _c: rest_a)
        reg.register(Venue.BINANCE, BarData, lambda _c: rest_b)

        clients = reg.get_clients(_make_instrument())
        assert [c.label for c in clients] == ["rest_a", "rest_b"]
