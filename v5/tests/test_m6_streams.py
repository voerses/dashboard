"""M6/M11 — DataStream / Data subclass hierarchy / Venue / TransportMode /
GapPolicy / Subscription (T-D14, T-D17 Venue typing, T-D18 Subscription
validation non-C3).

M11 migration (Commit 1): the closed ``DataKind`` enum is replaced by the
polymorphic ``Data`` class hierarchy. ``DataStream`` is typed by ``data_class``.

C3 price-type capability check lives in test_m6_engine.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _bar_stream():
    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, DataStream, InstrumentId, Venue
    i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=i, data_class=BarData, bar_spec=BarSpec.from_minutes(60))


def _trade_stream():
    from v5.data.streams import DataStream, InstrumentId, TradeData, Venue
    i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=i, data_class=TradeData, bar_spec=None)


def _funding_stream():
    from v5.data.streams import DataStream, FundingRateData, InstrumentId, Venue
    i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=i, data_class=FundingRateData, bar_spec=None)


def _noop(_ev): return None


class TestVenueEnum:
    """T-D17 typing slice."""

    def test_venue_enum_members(self):
        from v5.data.streams import Venue
        assert Venue.BINANCE.value == "BINANCE"
        assert Venue.OKX.value == "OKX"
        assert Venue.BYBIT.value == "BYBIT"
        assert Venue.DERIBIT.value == "DERIBIT"

    def test_instrument_id_venue_is_enum(self):
        from v5.data.streams import InstrumentId, Venue
        i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        assert i.venue is Venue.BINANCE


class TestDataClassExport:
    """M11 — each Data subclass must be exported from v5.data.streams as
    a callable class (post-DataKind migration)."""

    @pytest.mark.parametrize(
        "name",
        ["BarData", "TradeData", "FundingRateData", "MarkPriceData",
         "MetricData", "OrderBookData", "CustomData"],
    )
    def test_data_subclass_exported(self, name):
        import v5.data.streams as streams_mod
        from v5.data.streams import Data
        cls = getattr(streams_mod, name, None)
        assert cls is not None, f"{name} must be exported from v5.data.streams"
        assert isinstance(cls, type) and issubclass(cls, Data)

    @pytest.mark.parametrize("name", ["PUSH", "PULL_ONCE", "PULL_SCHEDULED", "REPLAY"])
    def test_transport_mode(self, name):
        from v5.data.streams import TransportMode
        assert hasattr(TransportMode, name)

    @pytest.mark.parametrize("name", ["STRICT", "NAN_FILL", "SKIP"])
    def test_gap_policy(self, name):
        from v5.data.streams import GapPolicy
        assert hasattr(GapPolicy, name)


class TestDataStreamConstructionValidation:
    """T-D14 / AC-D14."""

    def test_bar_without_bar_spec_raises(self):
        from v5.data.streams import BarData, DataStream, InstrumentId, Venue
        i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        with pytest.raises(ValueError):
            DataStream(instrument=i, data_class=BarData, bar_spec=None)

    def test_non_bar_with_bar_spec_raises(self):
        from v5.bar_spec import BarSpec
        from v5.data.streams import DataStream, FundingRateData, InstrumentId, Venue
        i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        with pytest.raises(ValueError):
            DataStream(
                instrument=i,
                data_class=FundingRateData,
                bar_spec=BarSpec.from_minutes(1),
            )

    def test_valid_streams_construct(self):
        assert _bar_stream().bar_spec is not None
        assert _funding_stream().bar_spec is None

    @pytest.mark.parametrize("bad", ["OPEN", "CLOSE", "lowercase", "BOGUS"])
    def test_invalid_price_type_rejected(self, bad):
        """# NOTE: brief ambiguous at AC-D14; strict — ValueError/TypeError."""
        from v5.bar_spec import BarSpec
        from v5.data.streams import BarData, DataStream, InstrumentId, Venue
        i = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        with pytest.raises((ValueError, TypeError)):
            DataStream(
                instrument=i,
                data_class=BarData,
                bar_spec=BarSpec.from_minutes(60),
                price_type=bad,  # type: ignore[arg-type]
            )


class TestSubscriptionValidation:
    """T-D18 / AC-D18 (non-C3)."""

    def test_gap_policy_nan_fill_on_funding_raises(self):
        """NAN_FILL is allowed only for BarData/TradeData — per M11 subclass
        dispatch. Non-bar streams (funding) must reject NAN_FILL.
        """
        from v5.data.streams import GapPolicy, Subscription
        with pytest.raises(ValueError):
            Subscription(
                stream=_funding_stream(), handler=_noop,
                gap_policy=GapPolicy.NAN_FILL, poll_interval_s=480,
            )

    def test_gap_policy_skip_on_funding_raises(self):
        from v5.data.streams import GapPolicy, Subscription
        with pytest.raises(ValueError):
            Subscription(stream=_funding_stream(), handler=_noop,
                         gap_policy=GapPolicy.SKIP, poll_interval_s=480)

    def test_bar_with_poll_interval_raises(self):
        from v5.data.streams import Subscription
        with pytest.raises(ValueError):
            Subscription(stream=_bar_stream(), handler=_noop, poll_interval_s=60)

    def test_trade_with_poll_interval_raises(self):
        from v5.data.streams import Subscription
        with pytest.raises(ValueError):
            Subscription(stream=_trade_stream(), handler=_noop, poll_interval_s=60)

    def test_funding_without_poll_interval_raises(self):
        from v5.data.streams import Subscription
        with pytest.raises(ValueError):
            Subscription(stream=_funding_stream(), handler=_noop, poll_interval_s=None)

    def test_funding_with_poll_interval_valid(self):
        from v5.data.streams import Subscription
        sub = Subscription(stream=_funding_stream(), handler=_noop, poll_interval_s=480)
        assert sub.poll_interval_s == 480

    def test_subscription_defaults(self):
        from v5.data.streams import GapPolicy, Subscription
        sub = Subscription(stream=_bar_stream(), handler=_noop)
        assert sub.role == "signal"
        assert sub.gap_policy == GapPolicy.STRICT
        assert sub.transport_preference == "AUTO"
        assert sub.fallback_allowed is True
