"""M6 — Pub/sub routing primitives.

FIX vocabulary (owned-by-type; see `v5/data/__init__.py` for full master block):
  DataKind              → MDEntryType(269)
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


class DataKind(Enum):
    """What flavour of market data a DataStream carries.

    FIX mapping (`MDEntryType(269)`):
      - BAR             → composite MDEntryType(269) 4/5/7/8 (Open/Close/High/Low)
                          at BarSpec resolution. Canonical FIX mapping:
                          4=Opening, 5=Closing, 7=TradingSessionHighPrice,
                          8=TradingSessionLowPrice.
      - TRADE           → 2 (Trade); aggressor side via Side(54)
      - FUNDING_RATE    → no FIX standard; vendor extension (MDEntryType='f' proposed)
      - MARK_PRICE      → 6 (SettlementPrice) — approximate
      - INSTRUMENT_INFO → one-shot metadata fetch (no streaming analog in FIX)
    """

    BAR = "bar"
    TRADE = "trade"
    FUNDING_RATE = "funding_rate"
    MARK_PRICE = "mark_price"
    INSTRUMENT_INFO = "instrument_info"


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


@dataclass(frozen=True, slots=True)
class DataStream:
    """Pub/sub routing key.

    FIX mapping: carries NoMDEntryTypes(267) semantics — `data_kind` is the
    single entry type; plural-semantics aspirational for M10+ multi-kind
    subscriptions.

    Invariants:
      - data_kind==BAR requires bar_spec is not None
      - data_kind!=BAR requires bar_spec is None
      - price_type in {LAST, MID, MARK, INDEX}
    """

    instrument: InstrumentId
    data_kind: DataKind
    bar_spec: Optional[BarSpec] = None
    price_type: Literal["LAST", "MID", "MARK", "INDEX"] = "LAST"
    source: Literal["EXTERNAL", "INTERNAL"] = "EXTERNAL"

    def __post_init__(self):
        if self.data_kind == DataKind.BAR and self.bar_spec is None:
            raise ValueError("BAR data_kind requires bar_spec")
        if self.data_kind != DataKind.BAR and self.bar_spec is not None:
            raise ValueError(
                f"{self.data_kind} must have bar_spec=None, got {self.bar_spec!r}"
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

    C3 price-type vs venue-capabilities check is NOT here — it lives in
    DataEngine.subscribe() since capabilities aren't known until connect()
    populates the registry.
    """
    if gap_policy != GapPolicy.STRICT and stream.data_kind != DataKind.BAR:
        raise ValueError(
            f"gap_policy={gap_policy} requires data_kind=BAR, got {stream.data_kind}"
        )
    # FUNDING_RATE always pull-scheduled (no push analog on Binance).
    # MARK_PRICE under PULL_SCHEDULED needs a poll interval; under PUSH
    # the venue streams mark updates, so poll_interval_s must be None.
    if stream.data_kind == DataKind.FUNDING_RATE and poll_interval_s is None:
        raise ValueError(
            f"{stream.data_kind} requires poll_interval_s (cron poll interval)"
        )
    if stream.data_kind == DataKind.MARK_PRICE:
        if transport_preference in ("WS", "AUTO"):
            # PUSH-capable transports — poll_interval_s must be None.
            if poll_interval_s is not None:
                raise ValueError(
                    f"{stream.data_kind} under transport={transport_preference} "
                    f"requires poll_interval_s=None (PUSH delivers mark updates); "
                    f"got {poll_interval_s}"
                )
        else:
            # PULL_SCHEDULED / REST-only — poll_interval_s is required.
            if poll_interval_s is None:
                raise ValueError(
                    f"{stream.data_kind} under transport=REST requires "
                    f"poll_interval_s (cron poll interval)"
                )
    if stream.data_kind in {DataKind.BAR, DataKind.TRADE}:
        if poll_interval_s is not None:
            raise ValueError(
                f"{stream.data_kind} requires poll_interval_s=None, got {poll_interval_s}"
            )
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
