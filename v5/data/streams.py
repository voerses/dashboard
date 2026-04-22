"""M6/M11 — Pub/sub routing primitives.

M11 (ADR-0002 move #1): the closed ``DataKind`` enum is REPLACED by a
polymorphic ``Data`` class hierarchy. Each subclass owns its own payload
invariants (enforced in ``__post_init__``); ``DataStream`` is typed by the
``Data`` class the stream carries; ``DataClientRegistry`` matches clients
against ``(Venue, Data subclass)``.

FIX vocabulary (owned-by-type; see `v5/data/__init__.py` for full master block):
  Data base class       → MDEntry (payload keyed by MDEntryType(269))
  InstrumentId          → SecurityID(48), SecurityIDSource(22),
                          SecurityExchange(207), Product(460)
  DataStream            → NoMDEntryTypes(267) (plural-semantics aspirational)
  Subscription          → MarketDataRequest(V), MDReqID(262),
                          SubscriptionRequestType(263)
  TransportMode         → MDUpdateType(265)
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Literal, Optional

from v5.bar_spec import BarSpec


class Venue(str, Enum):
    """Typed venue identifier.

    Engine consults DataClientRegistry keyed by Venue — adding a new venue is
    a registry entry, not an engine edit.
    """

    BINANCE = "BINANCE"
    OKX = "OKX"
    BYBIT = "BYBIT"
    DERIBIT = "DERIBIT"


class TransportMode(Enum):
    """How a DataClient delivers data.

    FIX mapping (`MDUpdateType(265)`):
      - PUSH            → MDUpdateType(265)=1 IncrementalRefresh (long-lived session)
      - PULL_ONCE       → MDUpdateType(265)=0 FullRefresh (caller-initiated snapshot)
      - PULL_SCHEDULED  → FullRefresh on engine-driven cadence
      - REPLAY          → deterministic parquet playback (no FIX analog)
    """

    PUSH = "push"
    PULL_ONCE = "pull_once"
    PULL_SCHEDULED = "pull_scheduled"
    REPLAY = "replay"


class GapPolicy(Enum):
    """Per-subscription gap-handling policy.

    FIX mapping note: approximate; MDUpdateType(265) has no direct gap-handling
    analog. STRICT/NAN_FILL/SKIP are engine semantics.
    """

    STRICT = "strict"
    NAN_FILL = "nan_fill"
    SKIP = "skip"


_VALID_PRICE_TYPES = frozenset({"LAST", "MID", "MARK", "INDEX"})
_VALID_ASSET_CLASSES = frozenset({"spot", "perp", "future", "option"})
_VALID_SOURCES = frozenset({"EXTERNAL", "INTERNAL"})
_VALID_TRADE_SIDES = frozenset({"BUY", "SELL", "UNKNOWN"})
# Canonical role vocabulary. Per brief §G1 (reviewer H1 recommendation),
# `role` is a free string — these four are reserved canonical names,
# but strategies may declare domain-specific roles (e.g. "regime",
# "alpha", "risk"). The runtime only requires that role be a non-empty
# string; no hard gate.
_CANONICAL_ROLES = frozenset({"signal", "entry", "exit"})
_VALID_TRANSPORT_PREFERENCE = frozenset({"WS", "REST", "AUTO"})


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Canonical instrument identity.

    FIX mapping:
      - symbol       → SecurityID(48) + SecurityIDSource(22) (venue-symbol scheme)
      - venue        → SecurityExchange(207) (typed as `Venue` enum, not free string)
      - asset_class  → Product(460). Crypto uses Literal["spot","perp","future","option"] —
                       forward-compat for dated futures (quarterly perps) and options.
    """

    symbol: str
    venue: Venue
    asset_class: Literal["spot", "perp", "future", "option"]

    def __post_init__(self):
        if self.asset_class not in _VALID_ASSET_CLASSES:
            raise ValueError(
                f"asset_class must be one of {sorted(_VALID_ASSET_CLASSES)}, "
                f"got {self.asset_class!r}"
            )


# ----------------------------------------------------------------------
# M11 — polymorphic Data class hierarchy (ADR-0002 move #1)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Data:
    """Abstract base for all market-data payloads.

    FIX mapping: ``Data`` is the conceptual equivalent of the FIX ``MDEntry``
    (``MDEntryType(269)``-keyed payload). Subclasses enforce per-type
    invariants in ``__post_init__`` (via ``_validate_base_data(self)`` for
    the shared ``ts_event``/``ts_init`` invariant).

    Not instantiated directly — strategies, clients, and the cache dispatch
    on concrete subclasses (``BarData``, ``TradeData``, etc.). Direct
    instantiation raises ``TypeError``.
    """

    instrument: InstrumentId
    ts_event: int    # nanoseconds since epoch; PIT-authoritative
    ts_init: int     # nanoseconds when this event was constructed

    def __post_init__(self):
        # Abstract base — direct instantiation is forbidden. Subclasses do
        # NOT chain into this method (the @dataclass(slots=True) subclass
        # pattern makes super() resolution fragile); each subclass calls
        # `_validate_base_data(self)` to enforce the ts_event/ts_init
        # invariant instead.
        if type(self) is Data:
            raise TypeError(
                "Data is an abstract base class; instantiate a subclass "
                "(BarData, TradeData, FundingRateData, etc.)"
            )


def _validate_base_data(obj: "Data") -> None:
    """Shared base-class invariant check, called from every subclass's
    ``__post_init__``. Avoids the ``@dataclass(slots=True) + super()``
    MRO pitfall (slots-generated subclass is a NEW class, so ``super()``
    without args can't locate the parent).
    """
    if obj.ts_init < obj.ts_event:
        raise ValueError(
            f"ts_init {obj.ts_init} cannot precede ts_event {obj.ts_event}"
        )


@dataclass(frozen=True, slots=True)
class BarData(Data):
    """OHLCV bar at a declared resolution.

    FIX mapping: composite ``MDEntryType(269)`` 4/5/7/8
    (Open/Closing/TradingSessionHigh/TradingSessionLow) at ``bar_spec``
    resolution.
    """

    bar_spec: BarSpec
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        _validate_base_data(self)
        if not (self.low <= self.open <= self.high):
            raise ValueError(
                f"bar OHLC violates low<=open<=high: "
                f"low={self.low} open={self.open} high={self.high}"
            )
        if not (self.low <= self.close <= self.high):
            raise ValueError(
                f"bar OHLC violates low<=close<=high: "
                f"low={self.low} close={self.close} high={self.high}"
            )


@dataclass(frozen=True, slots=True)
class TradeData(Data):
    """Executed trade (aggregate tick).

    FIX mapping: ``MDEntryType(269)=2`` Trade; aggressor side via ``Side(54)``.
    """

    price: float
    size: float
    side: Literal["BUY", "SELL", "UNKNOWN"]

    def __post_init__(self):
        _validate_base_data(self)
        if self.side not in _VALID_TRADE_SIDES:
            raise ValueError(
                f"side must be one of {sorted(_VALID_TRADE_SIDES)}, got {self.side!r}"
            )
        if self.price <= 0:
            raise ValueError(f"price must be > 0, got {self.price}")
        if self.size <= 0:
            raise ValueError(f"size must be > 0, got {self.size}")


@dataclass(frozen=True, slots=True)
class FundingRateData(Data):
    """Perpetual-futures funding rate sample.

    FIX mapping: no FIX standard; vendor extension (``MDEntryType='f'``
    proposed).
    """

    rate: float            # per-interval funding rate (fraction, e.g., 0.0001)
    next_funding_ts: int   # ns timestamp of next scheduled settlement

    def __post_init__(self):
        _validate_base_data(self)
        if self.next_funding_ts <= self.ts_event:
            raise ValueError(
                f"next_funding_ts {self.next_funding_ts} must be strictly "
                f"greater than ts_event {self.ts_event}"
            )


@dataclass(frozen=True, slots=True)
class MarkPriceData(Data):
    """Mark price sample.

    FIX mapping: ``MDEntryType(269)=6`` SettlementPrice (approximate).
    """

    price: float

    def __post_init__(self):
        _validate_base_data(self)
        if self.price <= 0:
            raise ValueError(f"price must be > 0, got {self.price}")


@dataclass(frozen=True, slots=True)
class MetricData(Data):
    """Vendor-extension metric sample (open interest, liquidation level, etc.).

    The ``metric_id`` is the discriminator that names the metric; a stream's
    ``DataStream.discriminator`` must match.

    FIX mapping: vendor extension; no FIX standard. ``metric_id`` takes the
    role of the custom ``MDEntryType`` tag for vendor-defined metrics.
    """

    metric_id: str
    value: float

    def __post_init__(self):
        _validate_base_data(self)
        if not isinstance(self.metric_id, str) or not self.metric_id:
            raise ValueError(
                f"MetricData requires a non-empty metric_id, got {self.metric_id!r}"
            )


@dataclass(frozen=True, slots=True)
class OrderBookData(Data):
    """L2 order-book snapshot.

    Invariants: ``bids`` descending by price, ``asks`` ascending by price,
    non-overlapping (best bid < best ask when both sides non-empty).

    FIX mapping: composite MDEntry set with ``MDEntryType(269)=0`` Bid and
    ``=1`` Offer, ordered by ``MDEntryPositionNo(290)``.
    """

    bids: tuple  # tuple[tuple[float, float], ...] descending by price
    asks: tuple  # tuple[tuple[float, float], ...] ascending by price

    def __post_init__(self):
        _validate_base_data(self)
        for i in range(1, len(self.bids)):
            if self.bids[i][0] >= self.bids[i - 1][0]:
                raise ValueError(
                    f"OrderBookData.bids must be strictly descending; "
                    f"{self.bids[i - 1]} followed by {self.bids[i]}"
                )
        for i in range(1, len(self.asks)):
            if self.asks[i][0] <= self.asks[i - 1][0]:
                raise ValueError(
                    f"OrderBookData.asks must be strictly ascending; "
                    f"{self.asks[i - 1]} followed by {self.asks[i]}"
                )
        if self.bids and self.asks:
            best_bid = self.bids[0][0]
            best_ask = self.asks[0][0]
            if best_bid >= best_ask:
                raise ValueError(
                    f"OrderBookData book crosses: best_bid={best_bid} >= "
                    f"best_ask={best_ask}"
                )


@dataclass(frozen=True, slots=True)
class CustomData(Data):
    """User-declared, strategy-owned data type.

    The ``type_name`` is the discriminator that names the custom kind;
    ``payload`` is strategy-owned. Use sparingly — prefer a first-class
    subclass when the payload has engine-wide semantics.
    """

    type_name: str
    payload: Any

    def __post_init__(self):
        _validate_base_data(self)
        if not isinstance(self.type_name, str) or not self.type_name:
            raise ValueError(
                f"CustomData requires a non-empty type_name, got {self.type_name!r}"
            )


# ----------------------------------------------------------------------
# M11 — DataStream refactor (ADR-0002 move #1)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DataStream:
    """Pub/sub routing key.

    M11: ``data_class`` replaces the legacy ``data_kind`` enum. The stream is
    typed by the ``Data`` subclass it carries; per-subclass invariants enforce
    payload-shape consistency at construction time.

    FIX mapping: carries ``NoMDEntryTypes(267)`` semantics — the declared
    ``data_class`` is the single entry type; plural-semantics aspirational for
    later multi-kind subscriptions.

    Invariants:
      - ``data_class`` must be a subclass of ``Data``
      - ``data_class is BarData`` ⇔ ``bar_spec is not None``
      - ``data_class is MetricData`` ⇒ ``discriminator`` required (metric_id)
      - ``price_type`` in {LAST, MID, MARK, INDEX}
    """

    instrument: InstrumentId
    data_class: type
    bar_spec: Optional[BarSpec] = None
    discriminator: Optional[str] = None
    price_type: Literal["LAST", "MID", "MARK", "INDEX"] = "LAST"
    source: Literal["EXTERNAL", "INTERNAL"] = "EXTERNAL"

    def __post_init__(self):
        if not isinstance(self.data_class, type) or not issubclass(self.data_class, Data):
            raise TypeError(
                f"data_class must be a subclass of Data, got {self.data_class!r}"
            )
        if self.data_class is BarData and self.bar_spec is None:
            raise ValueError("BarData stream requires bar_spec")
        if self.data_class is not BarData and self.bar_spec is not None:
            raise ValueError(
                f"{self.data_class.__name__} stream must have bar_spec=None, "
                f"got {self.bar_spec!r}"
            )
        if self.data_class is MetricData and (
            not isinstance(self.discriminator, str) or not self.discriminator
        ):
            raise ValueError(
                "MetricData stream requires discriminator (metric_id) as a "
                "non-empty string"
            )
        if self.data_class is CustomData and (
            not isinstance(self.discriminator, str) or not self.discriminator
        ):
            raise ValueError(
                "CustomData stream requires discriminator (type_name) as a "
                "non-empty string"
            )
        if self.price_type not in _VALID_PRICE_TYPES:
            raise ValueError(
                f"price_type must be one of {sorted(_VALID_PRICE_TYPES)}, "
                f"got {self.price_type!r}"
            )
        if self.source not in _VALID_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(_VALID_SOURCES)}, got {self.source!r}"
            )


# ----------------------------------------------------------------------
# M11 — per-subclass subscription validation (ADR-0002 move #1)
# ----------------------------------------------------------------------


def _validate_subscription(
    stream: DataStream,
    handler: Callable[[Any], None],
    warmup: int,
    role: str,
    gap_policy: GapPolicy,
    poll_interval_s: Optional[int],
    transport_preference: str,
    fallback_allowed: bool,
    lookback_days_override: Optional[int],
) -> None:
    """Enforce AC-D18 invariants at Subscription construction time.

    M11: dispatch branches on the Data subclass (isinstance-equivalent check
    via ``data_class is X``), not on a string discriminator.

    C3 price-type vs venue-capabilities check is NOT here — it lives in
    DataEngine.subscribe() since capabilities aren't known until connect()
    populates the registry.
    """
    dc = stream.data_class

    # Gap policy: STRICT required for everything except BarData/TradeData —
    # other streams are event-shaped (funding rates, marks, metrics, book
    # snapshots, custom) and cannot legitimately NAN_FILL or SKIP (there is
    # no fixed cadence to fill).
    if gap_policy != GapPolicy.STRICT and dc not in (BarData, TradeData):
        raise ValueError(
            f"gap_policy={gap_policy} requires data_class in "
            f"(BarData, TradeData), got {dc.__name__}"
        )

    # Poll-interval rules per subclass:
    #   BarData / TradeData — PUSH-only; must have poll_interval_s=None
    #   FundingRateData      — no PUSH analog on Binance; poll_interval_s required
    #   MarkPriceData        — depends on transport preference (PUSH None, REST required)
    #   MetricData           — permits either (client decides based on manifest)
    #   OrderBookData        — PUSH-oriented but REST snapshots allowed; permissive
    #   CustomData           — permissive
    if dc in (BarData, TradeData):
        if poll_interval_s is not None:
            raise ValueError(
                f"{dc.__name__} requires poll_interval_s=None, got {poll_interval_s}"
            )
    elif dc is FundingRateData:
        if poll_interval_s is None:
            raise ValueError(
                f"{dc.__name__} requires poll_interval_s (cron poll interval)"
            )
    elif dc is MarkPriceData:
        if transport_preference in ("WS", "AUTO"):
            if poll_interval_s is not None:
                raise ValueError(
                    f"{dc.__name__} under transport={transport_preference} "
                    f"requires poll_interval_s=None (PUSH delivers mark updates); "
                    f"got {poll_interval_s}"
                )
        else:
            if poll_interval_s is None:
                raise ValueError(
                    f"{dc.__name__} under transport=REST requires "
                    f"poll_interval_s (cron poll interval)"
                )
    # MetricData / OrderBookData / CustomData: no hard poll_interval_s rule here.

    if warmup < 0:
        raise ValueError(f"warmup must be >= 0, got {warmup}")
    # Role is free-string per brief §G1 / reviewer H1. Only require a
    # non-empty string — {signal, entry, exit} are the reserved canonical
    # values, but domain-specific roles (e.g. "regime", "alpha", "risk")
    # are explicitly allowed.
    if not isinstance(role, str) or not role:
        raise ValueError(f"role must be a non-empty string, got {role!r}")
    if transport_preference not in _VALID_TRANSPORT_PREFERENCE:
        raise ValueError(
            f"transport_preference must be one of {sorted(_VALID_TRANSPORT_PREFERENCE)}, "
            f"got {transport_preference!r}"
        )
    if lookback_days_override is not None and lookback_days_override <= 0:
        raise ValueError(
            f"lookback_days_override must be > 0 when provided, got {lookback_days_override}"
        )


_SubscriptionBase = namedtuple(
    "_SubscriptionBase",
    [
        "stream", "handler", "warmup", "role", "gap_policy",
        "poll_interval_s", "transport_preference", "fallback_allowed",
        "lookback_days_override",
    ],
)


class Subscription(_SubscriptionBase):
    """Strategy-declared data subscription.

    FIX mapping:
      - MarketDataRequest(V) with MDReqID(262) = subscription handle
      - SubscriptionRequestType(263) = 1 Snapshot+Updates for PUSH live,
        0 Snapshot-only for REPLAY

    Built on collections.namedtuple (not typing.NamedTuple) because
    typing.NamedTuple prohibits __new__ override — and we need validation
    at construction time per AC-D18.

    Phase-4 implementer note: adding a field requires updating THREE sites
    in sync — the _SubscriptionBase field list, the __new__ signature, and
    the super().__new__(cls, ...) call.
    """

    __slots__ = ()

    def __new__(
        cls,
        stream: DataStream,
        handler: Callable[[Any], None],
        warmup: int = 500,
        role: str = "signal",
        gap_policy: GapPolicy = GapPolicy.STRICT,
        poll_interval_s: Optional[int] = None,
        transport_preference: Literal["WS", "REST", "AUTO"] = "AUTO",
        fallback_allowed: bool = True,
        lookback_days_override: Optional[int] = None,
    ):
        _validate_subscription(
            stream, handler, warmup, role, gap_policy, poll_interval_s,
            transport_preference, fallback_allowed, lookback_days_override,
        )
        return super().__new__(
            cls,
            stream, handler, warmup, role, gap_policy, poll_interval_s,
            transport_preference, fallback_allowed, lookback_days_override,
        )
