"""M6 — DataEngine wiring: subscription union, routing, transport, cascade,
C3 price-type validation (T-D2, T-D4, T-D15, T-D17, T-D18 C3 slice).

All tests MUST FAIL today — v5.data.engine does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _noop(_ev): return None


def _bar_stream(minutes=60, price_type="LAST"):
    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, DataStream, InstrumentId, Venue
    inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=inst, data_class=BarData,
                      bar_spec=BarSpec.from_minutes(minutes), price_type=price_type)


class _FakeClient:
    def __init__(self, venue, modes, specs=None, label=""):
        self.venue = venue; self.supported_modes = modes
        self._specs = specs; self.label = label
        self.subscribed: list = []

    def supports(self, stream, mode):
        if mode not in self.supported_modes: return False
        if self._specs is None: return True
        key = (stream.data_class, stream.bar_spec.resolution_minutes if stream.bar_spec else None)
        return key in self._specs

    def connect(self): pass
    def disconnect(self): pass
    def request(self, s, a, b): return []
    def replay(self, s, a, b): return iter([])
    def subscribe(self, s): self.subscribed.append(s)
    def unsubscribe(self, s): pass
    def subscribe_scheduled(self, s, interval_s): pass


class TestSubscriptionUnion:
    """T-D2 / AC-D2 — engine unions + dedupes strategy-declared subscriptions."""

    def test_multiple_strategies_deduped(self):
        from v5.bar_spec import BarSpec
        from v5.data.engine import DataEngine
        from v5.data.streams import BarData, DataStream, InstrumentId, Subscription, Venue

        btc = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        eth = InstrumentId(symbol="ETHUSDT", venue=Venue.BINANCE, asset_class="perp")
        def _s(i, m):
            return DataStream(instrument=i, data_class=BarData,
                              bar_spec=BarSpec.from_minutes(m))
        subs = [
            Subscription(stream=_s(btc, 60), handler=_noop),
            Subscription(stream=_s(btc, 1), handler=_noop),
            Subscription(stream=_s(btc, 60), handler=_noop),  # dupe
            Subscription(stream=_s(eth, 60), handler=_noop),
            Subscription(stream=_s(eth, 60), handler=_noop),  # dupe
            Subscription(stream=_s(eth, 5), handler=_noop),
        ]
        engine = DataEngine()
        engine.subscribe_all(subs)
        assert len({s for s in engine.active_streams()}) == 4


class TestRegistryCascade:
    """T-D17 / AC-D17 — engine uses registry, not hardcoded client list."""

    def test_empty_registry_raises_with_venue_in_message(self):
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import Subscription
        engine = DataEngine(registry=DataClientRegistry())
        with pytest.raises(RuntimeError) as ei:
            engine.subscribe(Subscription(stream=_bar_stream(60), handler=_noop))
        assert "BINANCE" in str(ei.value), (
            f"missing-venue error must name the venue; got {ei.value!r}"
        )
        assert "no client registered" in str(ei.value).lower() or "no venue" in str(ei.value).lower(), (
            f"missing-venue error should explain the condition; got {ei.value!r}"
        )

    def test_registered_factory_unblocks_subscribe(self):
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Subscription, TransportMode, Venue
        reg = DataClientRegistry()
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}), label="ws")
        reg.register(Venue.BINANCE, BarData, lambda _c: ws)
        engine = DataEngine(registry=reg)
        engine.subscribe(Subscription(stream=_bar_stream(60), handler=_noop))
        assert len(ws.subscribed) == 1


class TestAggregationFallback:
    """T-D4 / AC-D4 — M4 aggregator wired when only smaller-spec client exists."""

    def test_5m_via_1m_client_uses_aggregator(self):
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Subscription, TransportMode, Venue
        reg = DataClientRegistry()
        c1m = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}),
                          specs=[(BarData, 1)], label="ws_1m")
        reg.register(Venue.BINANCE, BarData, lambda _c: c1m)
        engine = DataEngine(registry=reg)
        engine.subscribe(Subscription(stream=_bar_stream(5), handler=_noop))
        assert any(s.bar_spec.resolution_minutes == 1 for s in c1m.subscribed
                   if s.bar_spec is not None)
        assert engine.has_internal_aggregator_for(_bar_stream(5))

    def test_raises_when_no_aggregatable_source(self):
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Subscription, TransportMode, Venue
        reg = DataClientRegistry()
        only15 = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}),
                             specs=[(BarData, 15)])
        reg.register(Venue.BINANCE, BarData, lambda _c: only15)
        engine = DataEngine(registry=reg)
        with pytest.raises(RuntimeError):
            engine.subscribe(Subscription(stream=_bar_stream(5), handler=_noop))


class TestTransportModeDispatch:
    """T-D15 / AC-D15 — transport_preference + fallback_allowed."""

    def test_no_fallback_on_ws_drop_when_disallowed(self):
        from v5.data.bus import MessageBus
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Subscription, TransportMode, Venue
        reg = DataClientRegistry()
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}), label="ws")
        rest = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PULL_ONCE}), label="rest")
        reg.register(Venue.BINANCE, BarData, lambda _c: ws)
        reg.register(Venue.BINANCE, BarData, lambda _c: rest)
        engine = DataEngine(registry=reg, bus=MessageBus())
        stream = _bar_stream(60)
        degraded: list = []
        engine.subscribe_degraded_events(degraded.append)
        engine.subscribe(Subscription(stream=stream, handler=_noop,
                                      transport_preference="WS", fallback_allowed=False))
        engine._simulate_ws_drop(stream)
        assert rest.subscribed == []
        assert len(degraded) >= 1


class TestEngineC3PriceTypeValidation:
    """T-D18 (C3) / AC-D18 — price_type must be in VenueCapabilities."""

    def test_unsupported_price_type_raises(self):
        from v5.data.engine import DataEngine
        from v5.data.exceptions import PriceTypeNotSupported
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Subscription, TransportMode, Venue
        reg = DataClientRegistry()
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}))
        reg.register(Venue.BINANCE, BarData, lambda _c: ws)
        engine = DataEngine(registry=reg)
        with pytest.raises(PriceTypeNotSupported):
            engine.subscribe(Subscription(stream=_bar_stream(60, price_type="MID"),
                                          handler=_noop))


class TestDataClassRouting:
    """T-D14 / AC-D14 — BarData routes through cache; non-BarData bypasses cache."""

    def test_bar_stream_creates_cache_entry(self):
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Subscription, TransportMode, Venue
        reg = DataClientRegistry()
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}))
        reg.register(Venue.BINANCE, BarData, lambda _c: ws)
        engine = DataEngine(registry=reg)
        stream = _bar_stream(60)
        engine.subscribe(Subscription(stream=stream, handler=_noop))
        assert engine.cache_has_entry_for(stream, role="signal")

    def test_trade_stream_bypasses_cache(self):
        from v5.data.engine import DataEngine
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import (BarData, DataStream, InstrumentId,
                                     Subscription, TradeData, TransportMode, Venue)
        reg = DataClientRegistry()
        ws = _FakeClient(Venue.BINANCE, frozenset({TransportMode.PUSH}))
        # The engine looks up by venue only for back-compat; register the WS
        # client for both BarData and TradeData so the routing test has a
        # client for the trade subscription.
        reg.register(Venue.BINANCE, BarData, lambda _c: ws)
        reg.register(Venue.BINANCE, TradeData, lambda _c: ws)
        engine = DataEngine(registry=reg)
        inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        trade = DataStream(instrument=inst, data_class=TradeData, bar_spec=None)
        engine.subscribe(Subscription(stream=trade, handler=_noop))
        assert not engine.cache_has_entry_for(trade, role="signal")
