"""M6/M11 — v5/data/ data architecture package.

M11 (ADR-0002 move #1): the closed ``DataKind`` enum is replaced by a
polymorphic ``Data`` class hierarchy. ``DataStream`` is typed by the ``Data``
subclass the stream carries; ``DataClientRegistry`` matches clients against
``(Venue, Data subclass)``.

FIX vocabulary mapping (parallel to M4/M5 discipline):
  - BarData → MDEntryType(269) = 4 OpeningPrice / 5 ClosingPrice /
                             7 TradingSessionHighPrice / 8 TradingSessionLowPrice
                             (composite at BarSpec resolution)
  - TradeData → MDEntryType(269) = 2 Trade; aggressor side via Side(54)
  - FundingRateData → no FIX standard; vendor extension (MDEntryType='f' proposed)
  - MarkPriceData → MDEntryType(269) = 6 SettlementPrice (approximate mapping)
  - MetricData → vendor extension; metric_id discriminator
  - OrderBookData → composite MDEntry set (bids/asks)
  - CustomData → strategy-owned; type_name discriminator
  - InstrumentId → SecurityID(48) + SecurityIDSource(22) + SecurityExchange(207)
  - InstrumentId.asset_class → Product(460)
  - Instrument.tick_size → MinPriceIncrement(969)
  - Instrument.min_notional → MinTradeVol(562)
  - Subscription → MarketDataRequest(V) with MDReqID(262) = subscription handle;
                    SubscriptionRequestType(263) = 1 Snapshot+Updates
  - DataStream → NoMDEntryTypes(267) + MDEntryType(269) set
  - TransportMode.PUSH → MDUpdateType(265)=1 IncrementalRefresh (long-lived WS)
  - TransportMode.PULL_ONCE / PULL_SCHEDULED → MDUpdateType(265)=0 FullRefresh
  - Bar.ts_event → TransactTime(60)
  - GapPolicy → approximate; MDUpdateType has no direct gap-handling analog

Runtime plumbing (no direct FIX mapping — engine internals):
  - DataClient / LiveDataClient — runtime plumbing, no FIX analog
  - DataClientRegistry — runtime plumbing, no FIX analog
  - InstrumentRegistry — adjacent to SecurityList(35=y), but our registry is
    runtime-populated from venue exchangeInfo, not a FIX message
  - DataEngine — engine orchestrator, no FIX analog
  - MessageBus — pub/sub transport, no FIX analog
  - VenueCapabilities — declarative venue-state meta; adjacent to
    TradingSessionStatus(340) semantics but runtime-populated
  - DataGapError, RateLimitExceeded, PriceTypeNotSupported, SymbolNotFound,
    ClockDriftHigh — engine exceptions, no FIX mapping
"""
from v5.data.bus import MessageBus, SubscriptionHandle
from v5.data.exceptions import (
    ClockDriftHigh,
    DataGapError,
    PriceTypeNotSupported,
    RateLimitExceeded,
    SymbolNotFound,
)
from v5.data.strategy import DataDeclaringStrategy, union_subscriptions
from v5.data.streams import (
    BarData,
    CustomData,
    Data,
    DataStream,
    FundingRateData,
    GapPolicy,
    InstrumentId,
    MarkPriceData,
    MetricData,
    OrderBookData,
    Subscription,
    TradeData,
    TransportMode,
    Venue,
)

__all__ = [
    "BarData",
    "ClockDriftHigh",
    "CustomData",
    "Data",
    "DataDeclaringStrategy",
    "DataGapError",
    "DataStream",
    "FundingRateData",
    "GapPolicy",
    "InstrumentId",
    "MarkPriceData",
    "MessageBus",
    "MetricData",
    "OrderBookData",
    "PriceTypeNotSupported",
    "RateLimitExceeded",
    "Subscription",
    "SubscriptionHandle",
    "SymbolNotFound",
    "TradeData",
    "TransportMode",
    "Venue",
    "union_subscriptions",
]
