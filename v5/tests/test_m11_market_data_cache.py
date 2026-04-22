"""M11 AC-6 — Unified `MarketDataCache` + `TokenView` delegation.

All tests RED today. `MarketDataCache` does not exist; `TokenView` constructs
from a dict of numpy arrays (not a cache). The tests here describe the
post-M11 surface:

  - Single polymorphic cache with typed accessors per data subclass
  - Storage keyed by (InstrumentId, Data subclass, discriminator)
  - PIT correctness: accessors return slices [0, bar_idx+1]; future events
    rejected at ingress time
  - TokenView delegates to cache — no direct array ownership
  - MultiInstrumentCache is deleted
"""
from __future__ import annotations

import numpy as np
import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue


def _inst(sym: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


def _bar(ts_event: int, close: float, open_: float = None,
         high: float = None, low: float = None, volume: float = 1_000.0,
         instrument: InstrumentId = None, bar_spec: BarSpec = None):
    """Construct a BarData event — import deferred to test body (RED-friendly)."""
    from v5.data.streams import BarData

    if instrument is None:
        instrument = _inst()
    if bar_spec is None:
        bar_spec = BarSpec.from_minutes(60)
    if open_ is None:
        open_ = close
    if high is None:
        high = max(close, open_) + 1.0
    if low is None:
        low = min(close, open_) - 1.0
    return BarData(
        instrument=instrument,
        ts_event=ts_event, ts_init=ts_event + 1,
        bar_spec=bar_spec,
        open=open_, high=high, low=low, close=close, volume=volume,
    )


class _StubClock:
    def __init__(self, now_ns: int = 0):
        self._now = now_ns
        self.current_bar_idx = 0

    def now_ns(self) -> int:
        return self._now

    def advance_to(self, ts_ns: int):
        self._now = ts_ns


# ----------------------------------------------------------------------
# 1. Ingress + typed accessors
# ----------------------------------------------------------------------


def test_cache_ingress_and_bars_accessor():
    """`cache.on_data(BarData(...))`; `cache.bars(inst, bar_spec).close` ndarray."""
    from v5.data.cache import MarketDataCache

    clk = _StubClock(now_ns=10_000_000_000_000_000)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    for i in range(3):
        ts = 1_000_000_000_000_000 + i * 3_600 * 1_000_000_000
        clk.advance_to(ts)
        cache.on_data(_bar(ts_event=ts, close=100.0 + i, instrument=inst, bar_spec=bs))

    bars = cache.bars(inst, bs)
    assert isinstance(bars.close, np.ndarray)
    assert bars.close.tolist() == [100.0, 101.0, 102.0]


def test_per_subclass_storage():
    """Separate storage for BarData, MetricData, TradeData on same instrument."""
    from v5.data.cache import MarketDataCache
    from v5.data.streams import MetricData, TradeData

    clk = _StubClock(now_ns=10_000_000_000_000_000_000)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    ts0 = 1_000_000_000_000_000
    cache.on_data(_bar(ts_event=ts0, close=100.0, instrument=inst, bar_spec=bs))
    cache.on_data(
        MetricData(
            instrument=inst, ts_event=ts0, ts_init=ts0 + 1,
            metric_id="binance.open_interest.5m", value=1_000_000.0,
        )
    )
    cache.on_data(
        TradeData(
            instrument=inst, ts_event=ts0, ts_init=ts0 + 1,
            price=100.5, size=0.1, side="BUY",
        )
    )

    # Each accessor returns storage specific to its subclass
    bars = cache.bars(inst, bs)
    metric_vals = cache.metric("binance.open_interest.5m", inst)
    trades = cache.trades(inst)

    assert len(bars.close) == 1
    assert float(metric_vals[-1]) == pytest.approx(1_000_000.0)
    # trades accessor — at minimum lets us see 1 event was stored
    assert len(list(trades)) == 1 or trades.size == 1 or getattr(trades, "__len__", lambda: 0)() == 1


def test_pit_correctness_on_slicing():
    """`cache.bars(...)` returns slice [0, bar_idx+1] — never future data."""
    from v5.data.cache import MarketDataCache

    clk = _StubClock(now_ns=0)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    ts_list = [1_000_000_000_000_000 + i * 3_600 * 1_000_000_000 for i in range(5)]
    for i, ts in enumerate(ts_list):
        clk.advance_to(ts)
        clk.current_bar_idx = i
        cache.on_data(_bar(ts_event=ts, close=100.0 + i, instrument=inst, bar_spec=bs))

    # Rewind clock — cache must expose only bars at or before bar_idx=2
    clk.current_bar_idx = 2
    bars = cache.bars(inst, bs)
    assert len(bars.close) == 3
    assert bars.close[-1] == pytest.approx(102.0)


def test_pit_rejects_future_events():
    """Ingress of an event with ts_event > clock.now_ns raises."""
    from v5.data.cache import MarketDataCache

    clk = _StubClock(now_ns=1_000_000_000_000_000)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    # Future event — clock is at 1e15 ns, event at 1e15 + 1 hour
    future_ts = clk.now_ns() + 3_600 * 1_000_000_000
    with pytest.raises(ValueError):
        cache.on_data(_bar(ts_event=future_ts, close=99.0, instrument=inst, bar_spec=bs))


# ----------------------------------------------------------------------
# 2. TokenView delegates to cache
# ----------------------------------------------------------------------


def test_tokenview_delegation_ohlcv():
    """TokenView built against MarketDataCache; `.close` equals
    `cache.bars(...).close`."""
    from v5.data.cache import MarketDataCache
    from v5.universe_context import TokenView

    clk = _StubClock(now_ns=0)
    cache = MarketDataCache(clock=clk)
    inst = _inst("ETHUSDT")
    bs = BarSpec.from_minutes(60)
    for i in range(4):
        ts = 1_000_000_000_000_000 + i * 3_600 * 1_000_000_000
        clk.advance_to(ts)
        clk.current_bar_idx = i
        cache.on_data(
            _bar(ts_event=ts, close=200.0 + i, instrument=inst, bar_spec=bs)
        )

    # Post-M11 TokenView takes a cache reference (not raw arrays)
    tv = TokenView(
        token="ETHUSDT", cache=cache, instrument=inst, bar_spec=bs, bar_idx=3,
    )
    np.testing.assert_array_equal(tv.close, cache.bars(inst, bs).close)


def test_tokenview_metric_accessor():
    """`TokenView.metric('binance.open_interest.5m')` returns ndarray."""
    from v5.data.cache import MarketDataCache
    from v5.data.streams import MetricData
    from v5.universe_context import TokenView

    clk = _StubClock(now_ns=0)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    # Drop one bar so TokenView has a valid bar_idx context
    ts = 1_000_000_000_000_000
    clk.advance_to(ts)
    clk.current_bar_idx = 0
    cache.on_data(_bar(ts_event=ts, close=100.0, instrument=inst, bar_spec=bs))
    cache.on_data(
        MetricData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            metric_id="binance.open_interest.5m", value=42.0,
        )
    )

    tv = TokenView(
        token="BTCUSDT", cache=cache, instrument=inst, bar_spec=bs, bar_idx=0,
    )
    arr = tv.metric("binance.open_interest.5m")
    assert isinstance(arr, np.ndarray)
    assert arr[-1] == pytest.approx(42.0)


def test_tokenview_metric_latest():
    """`.metric_latest(metric_id)` returns scalar at current bar."""
    from v5.data.cache import MarketDataCache
    from v5.data.streams import MetricData
    from v5.universe_context import TokenView

    clk = _StubClock(now_ns=0)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    for i in range(3):
        ts = 1_000_000_000_000_000 + i * 3_600 * 1_000_000_000
        clk.advance_to(ts)
        clk.current_bar_idx = i
        cache.on_data(_bar(ts_event=ts, close=100.0, instrument=inst, bar_spec=bs))
        cache.on_data(
            MetricData(
                instrument=inst, ts_event=ts, ts_init=ts + 1,
                metric_id="binance.open_interest.5m", value=100.0 + i * 10.0,
            )
        )

    tv = TokenView(
        token="BTCUSDT", cache=cache, instrument=inst, bar_spec=bs, bar_idx=2,
    )
    v = tv.metric_latest("binance.open_interest.5m")
    assert isinstance(v, float)
    assert v == pytest.approx(120.0)


# ----------------------------------------------------------------------
# 3. Trades / custom accessors
# ----------------------------------------------------------------------


def test_cache_trades_accessor():
    """`cache.trades(instrument)` returns an accessor of stored trades."""
    from v5.data.cache import MarketDataCache
    from v5.data.streams import TradeData

    clk = _StubClock(now_ns=10_000_000_000_000_000)
    cache = MarketDataCache(clock=clk)
    inst = _inst()

    for i in range(5):
        ts = 1_000_000_000_000_000 + i
        cache.on_data(
            TradeData(
                instrument=inst, ts_event=ts, ts_init=ts + 1,
                price=100.0 + i * 0.5, size=0.1, side="BUY" if i % 2 == 0 else "SELL",
            )
        )

    trades = cache.trades(inst)
    # Accessor might return a list-like or TradeArray; both are acceptable
    count = len(trades) if hasattr(trades, "__len__") else sum(1 for _ in trades)
    assert count == 5


def test_multi_instrument_cache_deleted():
    """`MultiInstrumentCache` must be deleted per AC-6."""
    import v5.data.cache as cache_mod

    assert not hasattr(cache_mod, "MultiInstrumentCache"), (
        "MultiInstrumentCache must be deleted per M11 AC-6 — replaced by the "
        "unified polymorphic MarketDataCache with typed accessors."
    )
    with pytest.raises(ImportError):
        exec("from v5.data.cache import MultiInstrumentCache", {})


def test_cache_custom_data_accessor():
    """`cache.custom(type_name, instrument)` roundtrips a CustomData event."""
    from v5.data.cache import MarketDataCache
    from v5.data.streams import CustomData

    clk = _StubClock(now_ns=10_000_000_000_000_000)
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    ts = 1_000_000_000_000_000

    evt = CustomData(
        instrument=inst, ts_event=ts, ts_init=ts + 1,
        type_name="my_custom_feed", payload={"x": 42},
    )
    cache.on_data(evt)

    view = cache.custom("my_custom_feed", inst)
    # Accessor should surface at least one event — list-like or iterable
    items = list(view) if not hasattr(view, "__len__") else list(view)
    assert len(items) == 1
    # Payload is preserved (may be wrapped)
    item = items[0]
    pl = getattr(item, "payload", item)
    assert pl == {"x": 42} or (isinstance(pl, dict) and pl.get("x") == 42)
