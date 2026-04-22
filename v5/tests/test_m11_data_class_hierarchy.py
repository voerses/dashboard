"""M11 AC-4 — Polymorphic `Data` class hierarchy + `DataStream` subclass typing.

All tests RED today — the classes and module attributes referenced here don't
exist on the current codebase. Tests import from the final post-M11 surface
and assert the invariants the brief and design pin.

Covers:
  - Per-subclass `__post_init__` invariants on Data subclasses
  - `DataStream` typed by `data_class: type[Data]`
  - `DataClientRegistry` keyed by `(venue, Data subclass)`
  - `DataKind` enum deletion
  - `_validate_subscription` dispatch per subclass
"""
from __future__ import annotations

import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue


# ----------------------------------------------------------------------
# 1. Per-subclass invariants
# ----------------------------------------------------------------------


def _btc_inst() -> InstrumentId:
    return InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")


def test_bar_data_invariants():
    """BarData enforces low <= open/close <= high; invalid OHLC raises."""
    from v5.data.streams import BarData

    inst = _btc_inst()
    bar_spec = BarSpec.from_minutes(60)
    ts_event = 1_700_000_000_000_000_000
    ts_init = ts_event + 1_000_000

    # Valid bar should construct
    bd = BarData(
        instrument=inst, ts_event=ts_event, ts_init=ts_init,
        bar_spec=bar_spec,
        open=100.0, high=110.0, low=95.0, close=105.0, volume=1_000.0,
    )
    assert bd.open == 100.0
    assert bd.high == 110.0

    # low > open — invalid
    with pytest.raises(ValueError):
        BarData(
            instrument=inst, ts_event=ts_event, ts_init=ts_init,
            bar_spec=bar_spec,
            open=50.0, high=110.0, low=60.0, close=105.0, volume=1_000.0,
        )
    # low > close — invalid
    with pytest.raises(ValueError):
        BarData(
            instrument=inst, ts_event=ts_event, ts_init=ts_init,
            bar_spec=bar_spec,
            open=100.0, high=110.0, low=106.0, close=105.0, volume=1_000.0,
        )
    # high < open — invalid
    with pytest.raises(ValueError):
        BarData(
            instrument=inst, ts_event=ts_event, ts_init=ts_init,
            bar_spec=bar_spec,
            open=120.0, high=110.0, low=95.0, close=105.0, volume=1_000.0,
        )


def test_trade_data_invariants():
    """TradeData: side in {BUY,SELL,UNKNOWN}; price > 0; size > 0."""
    from v5.data.streams import TradeData

    inst = _btc_inst()
    ts = 1_700_000_000_000_000_000

    td = TradeData(
        instrument=inst, ts_event=ts, ts_init=ts + 10,
        price=50_000.0, size=0.5, side="BUY",
    )
    assert td.side == "BUY"

    # Invalid side
    with pytest.raises((ValueError, TypeError)):
        TradeData(
            instrument=inst, ts_event=ts, ts_init=ts + 10,
            price=50_000.0, size=0.5, side="BOGUS",
        )
    # price must be > 0
    with pytest.raises(ValueError):
        TradeData(
            instrument=inst, ts_event=ts, ts_init=ts + 10,
            price=0.0, size=0.5, side="BUY",
        )
    # size must be > 0
    with pytest.raises(ValueError):
        TradeData(
            instrument=inst, ts_event=ts, ts_init=ts + 10,
            price=50_000.0, size=0.0, side="SELL",
        )


def test_funding_rate_data_invariants():
    """FundingRateData: next_funding_ts > ts_event."""
    from v5.data.streams import FundingRateData

    inst = _btc_inst()
    ts_event = 1_700_000_000_000_000_000
    next_ts = ts_event + 8 * 3600 * 1_000_000_000

    fd = FundingRateData(
        instrument=inst, ts_event=ts_event, ts_init=ts_event + 1,
        rate=0.0001, next_funding_ts=next_ts,
    )
    assert fd.rate == pytest.approx(0.0001, abs=1e-12)

    # next_funding_ts must be strictly after ts_event
    with pytest.raises(ValueError):
        FundingRateData(
            instrument=inst, ts_event=ts_event, ts_init=ts_event + 1,
            rate=0.0001, next_funding_ts=ts_event,  # not strictly >
        )
    with pytest.raises(ValueError):
        FundingRateData(
            instrument=inst, ts_event=ts_event, ts_init=ts_event + 1,
            rate=0.0001, next_funding_ts=ts_event - 1,
        )


def test_mark_price_data_invariants():
    """MarkPriceData: price > 0."""
    from v5.data.streams import MarkPriceData

    inst = _btc_inst()
    ts = 1_700_000_000_000_000_000

    mp = MarkPriceData(instrument=inst, ts_event=ts, ts_init=ts + 1, price=50_000.0)
    assert mp.price == 50_000.0

    with pytest.raises(ValueError):
        MarkPriceData(instrument=inst, ts_event=ts, ts_init=ts + 1, price=0.0)
    with pytest.raises(ValueError):
        MarkPriceData(instrument=inst, ts_event=ts, ts_init=ts + 1, price=-1.0)


def test_metric_data_invariants():
    """MetricData: metric_id non-empty; requires discriminator."""
    from v5.data.streams import MetricData

    inst = _btc_inst()
    ts = 1_700_000_000_000_000_000

    m = MetricData(
        instrument=inst, ts_event=ts, ts_init=ts + 1,
        metric_id="binance.open_interest.5m", value=1_234_567.0,
    )
    assert m.metric_id == "binance.open_interest.5m"

    with pytest.raises(ValueError):
        MetricData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            metric_id="", value=42.0,
        )


def test_orderbook_data_invariants():
    """OrderBookData: bids desc-sorted, asks asc-sorted, non-overlapping."""
    from v5.data.streams import OrderBookData

    inst = _btc_inst()
    ts = 1_700_000_000_000_000_000

    # Valid snapshot
    ob = OrderBookData(
        instrument=inst, ts_event=ts, ts_init=ts + 1,
        bids=((100.0, 1.0), (99.0, 2.0), (98.0, 3.0)),
        asks=((101.0, 1.0), (102.0, 2.0), (103.0, 3.0)),
    )
    assert ob.bids[0][0] == 100.0
    assert ob.asks[0][0] == 101.0

    # Bids must be descending
    with pytest.raises(ValueError):
        OrderBookData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            bids=((98.0, 1.0), (100.0, 2.0)),  # not descending
            asks=((101.0, 1.0),),
        )
    # Asks must be ascending
    with pytest.raises(ValueError):
        OrderBookData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            bids=((100.0, 1.0),),
            asks=((103.0, 1.0), (101.0, 2.0)),  # not ascending
        )
    # Overlapping book — best bid >= best ask
    with pytest.raises(ValueError):
        OrderBookData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            bids=((102.0, 1.0), (100.0, 2.0)),
            asks=((101.0, 1.0), (103.0, 2.0)),  # best ask 101 < best bid 102
        )


def test_custom_data_invariants():
    """CustomData: type_name non-empty."""
    from v5.data.streams import CustomData

    inst = _btc_inst()
    ts = 1_700_000_000_000_000_000

    cd = CustomData(
        instrument=inst, ts_event=ts, ts_init=ts + 1,
        type_name="my_custom_feed", payload={"foo": 1},
    )
    assert cd.type_name == "my_custom_feed"

    with pytest.raises(ValueError):
        CustomData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            type_name="", payload=None,
        )


# ----------------------------------------------------------------------
# 2. DataStream subclass typing
# ----------------------------------------------------------------------


def test_datastream_subclass_typing():
    """DataStream(instrument, data_class=BarData, bar_spec=...) constructs.
    Non-Data class in data_class= raises TypeError."""
    from v5.data.streams import BarData, DataStream

    inst = _btc_inst()
    bar_spec = BarSpec.from_minutes(60)

    ds = DataStream(
        instrument=inst, data_class=BarData, bar_spec=bar_spec,
    )
    assert ds.data_class is BarData
    assert ds.bar_spec == bar_spec

    # Non-Data class — must raise TypeError with informative match
    with pytest.raises(TypeError, match="data_class"):
        DataStream(instrument=inst, data_class=int, bar_spec=bar_spec)


def test_datastream_validates_per_subclass():
    """BarData requires bar_spec; MetricData requires discriminator;
    non-BarData stream forbids bar_spec."""
    from v5.data.streams import BarData, MetricData, TradeData, DataStream

    inst = _btc_inst()
    bar_spec = BarSpec.from_minutes(60)

    # BarData without bar_spec — must raise
    with pytest.raises(ValueError, match="bar_spec"):
        DataStream(instrument=inst, data_class=BarData, bar_spec=None)

    # MetricData requires discriminator (metric_id)
    with pytest.raises(ValueError, match="discriminator"):
        DataStream(instrument=inst, data_class=MetricData, discriminator=None)

    # Non-BarData stream must not pass bar_spec
    with pytest.raises(ValueError):
        DataStream(instrument=inst, data_class=TradeData, bar_spec=bar_spec)

    # MetricData with discriminator — ok
    mds = DataStream(
        instrument=inst, data_class=MetricData,
        discriminator="binance.open_interest.5m",
    )
    assert mds.discriminator == "binance.open_interest.5m"


# ----------------------------------------------------------------------
# 3. Registry keyed by (venue, Data subclass)
# ----------------------------------------------------------------------


def test_registry_matches_on_data_class():
    """Registering factory for (BINANCE, BarData) → get_clients(inst, BarData)
    returns it; TradeData query does not."""
    from v5.data.registry import DataClientRegistry
    from v5.data.streams import BarData, TradeData

    class _Dummy:
        supported_modes = frozenset()

        def __init__(self, _cfg=None):
            pass

    reg = DataClientRegistry()
    inst = _btc_inst()
    # New signature: (venue, data_class, factory)
    reg.register(Venue.BINANCE, BarData, lambda _: _Dummy())

    bar_clients = reg.get_clients(inst, BarData)
    assert len(bar_clients) == 1
    assert isinstance(bar_clients[0], _Dummy)

    trade_clients = reg.get_clients(inst, TradeData)
    assert trade_clients == []


# ----------------------------------------------------------------------
# 4. DataKind enum deletion
# ----------------------------------------------------------------------


def test_datakind_enum_deleted():
    """`DataKind` no longer exists on `v5.data.streams`."""
    import v5.data.streams as streams_mod

    assert not hasattr(streams_mod, "DataKind"), (
        "DataKind enum must be deleted per M11 AC-4 — replaced by polymorphic "
        "Data class hierarchy. Use data_class=<Data subclass> on DataStream."
    )
    with pytest.raises(ImportError):
        # Explicit re-import via exec — mirrors a user's `from v5.data.streams
        # import DataKind` that must now break.
        exec("from v5.data.streams import DataKind", {})


# ----------------------------------------------------------------------
# 5. _validate_subscription dispatches per Data subclass
# ----------------------------------------------------------------------


def test_subscription_validation_per_subclass():
    """Gap policy STRICT ok for BarData; SKIP/NAN_FILL forbidden for anything
    but BarData. TradeData/MetricData forbid bar_spec at the stream level."""
    from v5.data.streams import (
        BarData,
        DataStream,
        GapPolicy,
        MetricData,
        Subscription,
    )

    inst = _btc_inst()
    bar_spec = BarSpec.from_minutes(60)
    bar_stream = DataStream(
        instrument=inst, data_class=BarData, bar_spec=bar_spec,
    )
    metric_stream = DataStream(
        instrument=inst, data_class=MetricData,
        discriminator="binance.open_interest.5m",
    )

    # STRICT + BarData — ok
    Subscription(stream=bar_stream, handler=lambda _: None, gap_policy=GapPolicy.STRICT)

    # NAN_FILL + non-BarData — must raise (per-subclass invariant)
    with pytest.raises(ValueError):
        Subscription(
            stream=metric_stream, handler=lambda _: None,
            gap_policy=GapPolicy.NAN_FILL,
        )
