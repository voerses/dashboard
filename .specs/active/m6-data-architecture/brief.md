# M6 — v5/data/ Package

**Summary**: Build a clean data architecture (`v5/data/`) with protocol-based clients, a message bus, clock abstraction, and strategy-declared subscriptions — replacing the fragmented 831-line PriceMonitor, separate LiveFetcher, and disconnected fetch scripts.

---

## Meta-Rule: design over code (M5-M10 v5 rebuild mode)

This milestone operates under the **design over code** meta-rule, inherited from the M5-M10 collective v5 rebuild policy. The v5 rebuild is a redesign, not incremental maintenance. Legacy behaviors exposed by the rebuild are **fixed, not preserved**, unless a hard parity gate explicitly requires preservation (e.g. hourly-only trade archives bit-identical per AC14).

**Consequences**:
- Hourly-only strategies: bit-identical backtest preservation (AC14-style).
- Non-trivial migrations: shadow-replay validation (AC41-style, 4 ULP / 5 bps / 10 bps tolerances) with documented per-strategy deltas in `fleet_behavior_delta.md`.
- Determinism (AC24-style within-build): absolute.
- Sign-off thresholds for documented behavior changes: ≤20 bps auto / 20-100 bps quant / >100 bps design review.

**Sunset**: meta-rule applies to M5-M10 (the v5 rebuild). Post-M10 reverts to CLAUDE.md default (`code over specs`).

---

## Problem

v4's data layer is a mess of independent, tightly-coupled components:

1. **PriceMonitor** (831 lines): 3 parallel WebSocket streams, hardcoded Binance URLs, mixed concerns (connection management + bar assembly + alert checking + position monitoring)
2. **LiveFetcher**: Separate from PriceMonitor, different API patterns, no shared client
3. **MinuteExitCache**: Yet another separate cache, disconnected from both
4. **~25 `tools/fetch_*.py` scripts**: Cron-driven, no live integration, no strategy awareness
5. **No strategy-declared data needs**: Paper engine eagerly subscribes to every token regardless of what the strategy actually needs
6. **No clock abstraction**: Backtest uses loop indices, live uses `datetime.now()` — making backtest/live parity hard to verify
7. **No non-bar events**: Funding rates, OI, mark price, liquidations are fetched ad-hoc or not at all
8. **No gap detection**: Missing bars are silently ignored, corrupting indicator calculations

---

## Scope

### In scope

- `v5/data/__init__.py` — package root with re-exports (`DataStream`, `Subscription`, `DataEngine`, ...)
- `v5/data/engine.py` — `DataEngine` orchestrator (manages clients, routes data via MessageBus, composes M4's `RollingCache` per instrument)
- `v5/data/bus.py` — `MessageBus` for pub/sub event routing with deterministic replay order
- `v5/data/streams.py` — `DataStream(instrument, bar_spec, source)` (renamed per D2) + `Subscription` NamedTuple
- `v5/data/cache.py` — `MultiInstrumentCache` wrapper that composes M4's `v5/rolling_cache.py::RollingCache` keyed by the 3-tuple `(InstrumentId, BarSpec, role)` (per D3 — role-aware to respect M4's signal=250d/entry=7d/exit=2d lookback budget)
- `v5/data/protocols.py` — `DataClient` Protocol (connect, disconnect, subscribe, request)
- `v5/data/clients/binance_ws.py` — `BinanceWSClient` (consolidates PriceMonitor WS logic; includes time-drift sync)
- `v5/data/clients/binance_rest.py` — `BinanceRESTClient` (consolidates LiveFetcher REST logic; includes rate-limit weight tracker + time-drift sync)
- `v5/data/clients/parquet_replay.py` — `ParquetReplayClient` for backtest (replays historical parquet data)
- `v5/data/types.py` — Non-bar event types: `FundingRate`, `MarkPrice`, `Trade` (see Scope trim below — `BookSnapshot`, `Liquidation`, `OpenInterest`, `Ticker`, `IndexPrice` all deferred)
- `v5/data/instruments.py` — `InstrumentRegistry` with symbol-to-instrument resolution (`resolve("BTCUSDT") -> InstrumentId(...)`) + venue metadata (tick_size, lot_size, margin requirements)
- `v5/data/gaps.py` — Gap detection using ts_event + ts_init + sequence numbers + 3-mode `GapPolicy` (STRICT / NAN_FILL / SKIP)
- `v5/clock.py` — `Clock` Protocol + `LiveClock` (real-time). `TestClock` already exists in `v5/testing.py` (M4-shipped).
- Strategy-declared subscriptions: strategies implement `required_data() -> list[Subscription]`
- `v5/paper_engine.py` migration — route live feeds through `DataEngine` instead of direct PriceMonitor/LiveFetcher wiring. **Feature-flagged**: `DataEngine.use_data_engine_flag: bool = False` default for first ship. Flip per-strategy after 24h shadow-replay parity gate passes (see Paper Migration Safeguards below).

**[M4-shipped, no duplicate]**: `BarSpec` (`v5/bar_spec.py`), `DataResampler` (`v5/data_resampler.py`), `StreamingConsolidator` (`v5/streaming_consolidator.py`), `RollingCache` (`v5/rolling_cache.py`), `TestClock` (`v5/testing.py`). M6 imports these — no re-definition.
**[M5-shipped, no duplicate]**: `Order`/`Leg`/order lifecycle (`v5/orders.py`), `dust_handler`, `orders_log`, `backtest`.

### Out of scope

- Strategy API redesign (M7 — but `required_data()` is a forward-compatible hook)
- Order execution / exchange connectivity (future milestone)
- Historical data backfill automation (stays in tools/)
- Dashboard data streaming
- Multi-exchange support (Binance only for now)
- Tick-level data (bar-level minimum granularity)

### Deferred event types (dropped from M6 scope)

Per quant-architect review, M6 ships only event types with a **current or near-term consumer**. Speculative types are deferred to M10 (orderbook/microstructure milestone) or a future-feature milestone to keep M6 focused and to avoid committing to L2 routing ergonomics before a real strategy demands them.

| Event type | M6 decision | Rationale |
|---|---|---|
| `FundingRate` | **Keep** | M4 AC38 + funding ceiling handler are existing consumers. |
| `MarkPrice` | **Keep** | M4 mark-trigger stop/liquidation path consumes it. |
| `Trade` (aggTrades tape/print) | **Add (new in M6)** | Crypto microstructure signals use aggTrades; Binance WS endpoint is well-defined; cheap to ship now. |
| `BookSnapshot` | **Drop → deferred to M10** | No current consumer; L2 routing (depth snapshot + diff stream) is a perf footgun not worth paying for speculatively. |
| `Liquidation` | **Drop → deferred** | Speculative; no strategy in the fleet consumes it. |
| `OpenInterest` | **Drop → deferred** | Speculative; no strategy in the fleet consumes it. |
| `Ticker` | **Drop** | Bars already cover best bid/ask/last at 1m resolution — adding Ticker is duplicate plumbing. |
| `IndexPrice` | **Drop → deferred** | Basis/carry strategies are not in the M5-M10 roadmap. Revisit if/when they are. |

Deferred types can be added incrementally via the same `Subscription` + `DataClient` mechanism — no M6 redesign required when they land.

---

## Architectural Decisions (post-M4+M5 reconciliation)

Three shipped-code collisions were resolved before this brief advances to design:

### D1 — BarSpec schema: **keep M4's `resolution_minutes: int`**

M4 shipped `v5/bar_spec.py::BarSpec(resolution_minutes, label_side, closed_side, anchor_utc)` with a canonical minutes-only set `{1, 3, 5, 10, 15, 30, 60, 120, 240, 360, 480, 720, 1440}` — interned and hashable. M6's original draft proposed `(step, unit)` for second-level support.

**Decision**: M6 uses M4's `BarSpec` unchanged. Crypto strategies in this codebase have no second-level use case today; adding `(step, unit)` would fork a shared primitive with 3+ importers. If seconds are ever needed, revisit as its own AIPIP.

**Rationale**: CLAUDE.md meta-rule #1 (code over specs) + meta-rule #2 (pattern over principle). M4 has shipped and is already consumed by `RollingCache`, `DataResampler`, `StreamingConsolidator`.

### D2 — BarType name collision: **rename M6's concept to `DataStream`**

M4 shipped a legacy namespace class `v5/rolling_cache.py::BarType` (with aliases like `ONE_MIN = BarSpec.from_minutes(1)`) for M3 back-compat. M6's original draft redefined `BarType` as `(InstrumentId, BarSpec, source)` — a richer pub/sub routing key. Same name, different concept → collision.

**Decision**: Rename M6's routing-key concept to `DataStream(instrument, bar_spec, source)`. Lives in `v5/data/streams.py`. `Subscription` references `DataStream`, not `BarType`. M4's legacy `BarType` stays as-is (zero churn for M3 callers).

**Rationale**: "DataStream" is a better semantic match for pub/sub routing (instrument + spec + source = one stream on the bus). Avoids a rename of the shipped M4 back-compat alias.

### D3 — RollingCache reuse: **compose, don't redefine; key by `(InstrumentId, BarSpec, role)` 3-tuple**

M4 shipped `v5/rolling_cache.py::RollingCache(bar_spec, lookback)` with role-aware lookback (signal=250d, entry=7d, exit=2d). M6's original draft defined a new `RollingCache(max_bars_per_type)` keyed by `BarType` for multi-instrument routing.

**Decision**: M6 does NOT redefine `RollingCache`. Instead, M6 builds a thin `MultiInstrumentCache` wrapper in `v5/data/cache.py` that composes M4's `RollingCache`. The cache key is the **3-tuple `(InstrumentId, BarSpec, role)`** — not a 2-tuple with a single `default_role`. Each distinct `role` declared by the strategy produces a distinct underlying `RollingCache` instance with the correct M4 lookback (signal=250d / entry=7d / exit=2d). The same `(BTC, 1h)` pair can exist as three separate caches — one per role — if strategies declare those roles against that stream.

**Rationale for the 3-tuple** (post-quant-architect review): a 2-tuple with a single `default_role="signal"` forces every 1m cache to the 250-day signal lookback, which blows M4's 1.2 GB memory budget for a 236-token fleet. Role is a per-subscription property, not a per-cache-instance property — so it belongs in the key. Composition preserves M4's behavior while adding both the multi-instrument dimension AND the role dimension M6 needs.

**Subscription implication**: `Subscription` gains a `role: Literal["signal","entry","exit"]` field (default `"signal"`). The cache reads the role from the subscription context when routing a bar, so the wrapper API is still `on_bar(bar, role)`.

---

## Core Abstractions

All data types use `@dataclass(slots=True)` for half the memory footprint vs regular dataclasses.

### FIX vocabulary block (`v5/data/__init__.py` module docstring) — Addition 7

The `v5/data/__init__.py` module docstring MUST include this FIX mapping block verbatim. It is the shared vocabulary for every type in the package and keeps M6 parallel to the M4/M5 FIX discipline.

```text
FIX vocabulary mapping (parallel to M4/M5 discipline):
  - Bar → MDEntryType(269) = 4 OpeningPrice / 5 ClosingPrice /
                             7 TradingSessionHighPrice / 8 TradingSessionLowPrice
                             (composite at BarSpec resolution)
  - Trade → MDEntryType(269) = 2 Trade; aggressor side via Side(54)
  - FundingRate → no FIX standard; vendor extension (MDEntryType='f' proposed)
  - MarkPrice → MDEntryType(269) = 6 SettlementPrice (approximate mapping)
  - InstrumentId → SecurityID(48) + SecurityIDSource(22) + SecurityExchange(207)
  - InstrumentId.asset_class → Product(460)
  - Instrument.tick_size → MinPriceIncrement(969)
  - Instrument.min_notional → MinTradeVol(562)
  - Subscription → MarketDataRequest(V) with MDReqID(262) = subscription handle;
                    SubscriptionRequestType(263) = 1 Snapshot+Updates
  - DataStream → NoMDEntryTypes(267) + MDEntryType(269) set
  - TransportMode.PUSH/PULL_ONCE → MDUpdateType(265) = 0 FullRefresh / 1 Incremental
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
```

The FIX tags referenced (14 total): `MDEntryType(269)`, `Side(54)`, `SecurityID(48)`, `SecurityIDSource(22)`, `SecurityExchange(207)`, `Product(460)`, `MinPriceIncrement(969)`, `MinTradeVol(562)`, `MarketDataRequest(V)`, `MDReqID(262)`, `SubscriptionRequestType(263)`, `NoMDEntryTypes(267)`, `MDUpdateType(265)`, `TransactTime(60)` (from Gap Detection section). The "no FIX" markers explicitly cover 8 additional runtime-plumbing/meta types: `DataClient`, `LiveDataClient`, `DataClientRegistry`, `InstrumentRegistry`, `DataEngine`, `MessageBus`, `VenueCapabilities`, and engine exceptions (`DataGapError`, `RateLimitExceeded`, `PriceTypeNotSupported`, `SymbolNotFound`, `ClockDriftHigh`). AC-D16 greps the package docstrings for these tags.

### Data Types (`v5/data/types.py`, `v5/data/streams.py`)

```python
class DataKind(Enum):
    """What flavour of market data a DataStream carries (Addition 2).

    FIX mapping (`MDEntryType(269)`):
      - BAR             → composite of 4/5/7/8 (Open/Close/High/Low) at BarSpec resolution
      - TRADE           → 2 (Trade); aggressor side via Side(54)
      - FUNDING_RATE    → no FIX standard; vendor extension (MDEntryType='f' proposed)
      - MARK_PRICE      → 6 (SettlementPrice) — approximate
      - INSTRUMENT_INFO → one-shot metadata fetch (no streaming analog in FIX)
    """
    BAR = "bar"
    TRADE = "trade"
    FUNDING_RATE = "funding_rate"
    MARK_PRICE = "mark_price"
    INSTRUMENT_INFO = "instrument_info"   # one-shot metadata fetch (Addition 2)


class Venue(str, Enum):
    """Typed venue identifier (Addition 4). Engine consults DataClientRegistry keyed
    by Venue — adding a new venue is a registry entry, not an engine edit."""
    BINANCE = "BINANCE"
    OKX = "OKX"          # reserved for M11+
    BYBIT = "BYBIT"      # reserved for M11+
    DERIBIT = "DERIBIT"  # reserved for M11+


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Canonical instrument identity. FIX mapping:
      - `symbol`       → `SecurityID(48)` + `SecurityIDSource(22)` (venue-symbol scheme)
      - `venue`        → `SecurityExchange(207)` (typed as `Venue` enum, not free string)
      - `asset_class`  → `Product(460)` (FIX tag 460: 1=AGENCY, 2=COMMODITY, 4=CORPORATE,
                                         5=CURRENCY, 6=EQUITY, 7=GOVERNMENT, 8=INDEX,
                                         9=LOAN, 10=MONEYMARKET, 11=MORTGAGE,
                                         12=MUNICIPAL, 13=OTHER, 14=FINANCING).
                        Crypto uses a `Literal["spot","perp","future","option"]` vocabulary —
                        forward-compat for dated futures (quarterly perps) and options.
    """
    symbol: str                                                    # "BTCUSDT"
    venue: "Venue"                                                 # Venue.BINANCE — typed enum (Addition 4)
    asset_class: Literal["spot", "perp", "future", "option"]       # was `market` — renamed per FIX Product(460)

# [M4-shipped: `v5/bar_spec.py::BarSpec(resolution_minutes, label_side, closed_side, anchor_utc)` — no duplicate needed]

@dataclass(frozen=True, slots=True)
class DataStream:
    """Pub/sub routing key — renamed from the original M6 "BarType" concept per D2
    to avoid collision with M4's legacy `v5/rolling_cache.py::BarType` namespace class.

    Extended (Addition 2) with `data_kind` + `price_type` + optional `bar_spec` so the
    same routing key type carries bar, trade, funding, mark, and instrument-info streams.

    Note (N2, Round 2): frozen dataclasses DO support `__post_init__` — it's called by
    the generated `__init__` after the instance is fully constructed but before it is
    returned. Because the dataclass is frozen, `__post_init__` can only READ fields
    (via `self.x`) to enforce invariants; it cannot mutate them directly. Any field
    mutation would require `object.__setattr__(self, "x", ...)` (used e.g. in M5's
    `Order` for auto-generated `order_id`). The validators below only raise — no mutation.
    """
    instrument: InstrumentId
    data_kind: "DataKind"                                              # Addition 2 — bar/trade/funding/mark/info
    bar_spec: "BarSpec | None" = None                                  # required iff data_kind == BAR; None otherwise
    price_type: Literal["LAST", "MID", "MARK", "INDEX"] = "LAST"       # FIX MDEntryType-adjacent (269)
    source: Literal["EXTERNAL", "INTERNAL"] = "EXTERNAL"               # INTERNAL = aggregator-produced

    def __post_init__(self):
        if self.data_kind == DataKind.BAR and self.bar_spec is None:
            raise ValueError("BAR data_kind requires bar_spec")
        if self.data_kind != DataKind.BAR and self.bar_spec is not None:
            raise ValueError(f"{self.data_kind} must have bar_spec=None")

# [M4-shipped: `Bar` struct used by `RollingCache` / `DataResampler` / `StreamingConsolidator` — M6 reuses the M4 representation. If a richer Bar is needed (e.g., adding ts_init), propose an extension, not a redefinition.]
```

### Subscription NamedTuple

```python
class GapPolicy(Enum):
    STRICT = "strict"       # default — raise on unfilled gap after N REST retries
    NAN_FILL = "nan_fill"   # emit Bar with NaN OHLCV at missing ts_event slot
    SKIP = "skip"           # silently drop gap; legacy v4 behavior, opt-in only

class Subscription(NamedTuple):
    stream: DataStream                              # was `bar_type`, renamed per D2
    handler: Callable[[Any], None]                  # payload type driven by stream.data_kind (Addition 2)
    warmup: int = 500                               # min bars before handler starts firing (BAR only)
    role: Literal["signal","entry","exit"] = "signal"   # per D3 — drives M4 RollingCache lookback
    gap_policy: GapPolicy = GapPolicy.STRICT        # per Fix 5 — 3-mode gap handling
    # Addition 3 — transport + cadence controls for non-bar streams
    poll_interval_s: int | None = None                                # required for FUNDING_RATE/MARK_PRICE; None for BAR/TRADE
    transport_preference: Literal["WS","REST","AUTO"] = "AUTO"        # caller's transport hint
    fallback_allowed: bool = True                                     # WS+fallback_allowed=False ⇒ no REST fallback on WS drop
    lookback_days_override: int | None = None                         # advanced-mode M4 BarSubscription lookback_days passthrough; None = use role default

# Construction-time validation (N1, Round 2):
#   Subscription is a NamedTuple — it does NOT support __post_init__. Use a
#   Subscription.__new__ override that calls _validate_subscription(stream, warmup, ...)
#   before returning the tuple. Raises ValueError (or PriceTypeNotSupported, per C3)
#   on invalid combinations.
#   - gap_policy != STRICT requires data_kind == BAR
#   - poll_interval_s is not None requires data_kind in {FUNDING_RATE, MARK_PRICE}
#   - poll_interval_s is None for data_kind BAR/TRADE
#   - transport_preference="WS" + fallback_allowed=False means NO REST fallback on WS failure
#   - (C3) stream.price_type must be in VenueCapabilities.supported_price_types
#          for the stream's venue — checked at DataEngine.subscribe(...) time
```

**Relationship to M4's BarSubscription**: M4 ships `BarSubscription(spec, role, lookback_days=...)`
as the internal type used by `MultiInstrumentCache` to size individual RollingCache
instances. M6's `Subscription` is the STRATEGY-FACING declaration; it does NOT
expose `lookback_days` override directly. The default lookbacks from M4
(signal=250d, entry=7d, exit=2d per `maxlen_for_bar_spec(spec, role)`) apply
across all M6 Subscriptions; strategies requiring non-default lookback must
compose their own M4 BarSubscription and pass it to DataEngine as an advanced-mode
override (new kwarg `lookback_days_override: int | None = None` on Subscription).

This keeps 95% of strategy code simple (role determines lookback) while
preserving the advanced override path for custom horizons. M7 Strategy API may
expose the override through a more ergonomic facade.

Strategies declare data needs via `required_data() -> list[Subscription]`. Runner unions across all strategies in the portfolio, computes the unique subscription set, and hands it to `DataEngine.subscribe_all(...)`. Only the data actually needed is fetched.

```python
from v5.bar_spec import BarSpec              # M4-shipped
from v5.data.streams import DataStream, Subscription, GapPolicy

class Strategy:
    def required_data(self) -> list[Subscription]:
        return [
            Subscription(DataStream(inst, BarSpec.from_minutes(60)), self.on_1h_bar, warmup=500, role="signal"),
            Subscription(DataStream(inst, BarSpec.from_minutes(5)),  self.on_5m_bar, warmup=100, role="entry"),
            Subscription(DataStream(inst, BarSpec.from_minutes(1)),  self.on_1m_bar, warmup=30,  role="exit"),
        ]
```

The same `(inst, BarSpec.from_minutes(1))` `DataStream` subscribed with `role="exit"` produces a **different** underlying `RollingCache` (2-day lookback) than the same stream subscribed with `role="signal"` (250-day lookback). This is the crux of Fix 1: lookback is role-driven, so role is part of the cache key.

### DataClient Protocol (`v5/data/clients/base.py`) — with TransportMode (Addition 5)

```python
class TransportMode(Enum):
    """How a DataClient delivers data (Addition 5). FIX mapping:
      - PUSH            → MDUpdateType(265)=1 IncrementalRefresh over a long-lived session
      - PULL_ONCE       → MDUpdateType(265)=0 FullRefresh, caller-initiated
      - PULL_SCHEDULED  → PULL_ONCE on a cron (engine-initiated cadence)
      - REPLAY          → deterministic parquet playback (backtest; no FIX analog)
    """
    PUSH = "push"                      # WebSocket streaming (server-initiated)
    PULL_ONCE = "pull_once"            # REST request/response (caller-initiated)
    PULL_SCHEDULED = "pull_scheduled"  # REST on a cron (engine-initiated)
    REPLAY = "replay"                  # deterministic parquet playback (backtest)


class DataClient(Protocol):
    """Historical + replay-capable client. Paper/live venues implement the richer
    `LiveDataClient(DataClient)` protocol below."""
    venue: Venue                                              # typed (Addition 4)
    supported_modes: frozenset[TransportMode]                 # self-declared capability

    def supports(self, stream: DataStream, mode: TransportMode) -> bool: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    # PULL_ONCE — REST backfill / historical window
    def request(self, stream: DataStream, start_ns: int, end_ns: int) -> list: ...
    # REPLAY — parquet playback (backtest)
    def replay(self, stream: DataStream, start_ns: int, end_ns: int) -> Iterator: ...


class LiveDataClient(DataClient, Protocol):
    """Adds PUSH (WS streaming) + PULL_SCHEDULED (cron REST) for live venues."""
    def subscribe(self, stream: DataStream) -> None: ...                         # PUSH
    def unsubscribe(self, stream: DataStream) -> None: ...                       # PUSH
    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None:  # PULL_SCHEDULED
        ...
```

**Split policy**: `BinanceWSClient` + `BinanceRESTClient` implement `LiveDataClient`; `ParquetReplayClient` implements `DataClient` only (no `subscribe`, REPLAY+PULL_ONCE only). Implementations declare `supported_modes` and raise `NotImplementedError` on unsupported ops. Pluggable per (venue, transport). One `BinanceWSClient` multiplexes 1m + 1h + aggTrades streams (not 3 separate WS classes). This replaces the fragmented PriceMonitor pattern where 3 parallel WebSocket objects each manage their own connection/reconnect.

### Venue + DataClientRegistry (Addition 4)

```python
class DataClientRegistry:
    """Venue-keyed client factory + lookup. DataEngine consults this instead of
    holding a hardcoded client list — adding a new venue is a registry entry,
    zero engine changes.
    """
    def register(self, venue: Venue, factory: Callable[[Config], DataClient]) -> None: ...
    def get_clients(self, instrument: InstrumentId) -> list[DataClient]: ...
    def registered_venues(self) -> frozenset[Venue]: ...
```

**Priority ordering (C1, Round 2)**: within a venue, clients are prioritized in the order `PUSH (WebSocket) > PULL_ONCE (REST) > REPLAY (parquet)`. `DataClientRegistry.get_clients(instrument)` returns clients sorted by this priority. `DataEngine.subscribe(...)` walks the priority list and binds to the first client whose `supports(stream, mode)` returns True. `register(venue, factory)` preserves insertion order per venue; registered factories declare their transport tier (via `supported_modes`) so the priority cascade is deterministic across venues.

`DataEngine.__init__` consults the registry (by `instrument.venue`) on every subscribe rather than iterating a hardcoded `list[DataClient]`. This is the unblock-point for M11+ multi-venue work: OKX/Bybit/Deribit ship as registered factories, no engine diff.

### MultiInstrumentCache (`v5/data/cache.py`) — per D3 (3-tuple key)

**[M4-shipped: `v5/rolling_cache.py::RollingCache(bar_spec, lookback)` with role-aware lookback (signal=250d, entry=7d, exit=2d) — no duplicate needed]**

M6 adds a thin wrapper that composes M4's `RollingCache` across instruments AND roles (D3 decision, post-review fix). The ring-buffer / column-oriented storage / zero-copy views all live in M4 and are reused verbatim.

```python
from v5.rolling_cache import RollingCache  # M4-shipped

class MultiInstrumentCache:
    """Routes (instrument, bar_spec, role) → M4 RollingCache.
    Adds instrument-level AND role-level keying; inherits M4's memory bounds and role-aware lookback.

    Why 3-tuple: M4's RollingCache lookback depends on role (signal=250d, entry=7d, exit=2d).
    A 2-tuple key with a single default_role forces every cache to the signal=250d lookback,
    which blows M4's 1.2 GB aggregate budget for a 236-token fleet. Role belongs in the key.
    """
    def __init__(self):
        self._caches: dict[tuple[InstrumentId, "BarSpec", str], RollingCache] = {}

    def on_bar(self, bar: Bar, role: Literal["signal", "entry", "exit"]) -> None:
        key = (bar.instrument_id, bar.bar_spec, role)
        rc = self._caches.get(key)
        if rc is None:
            rc = RollingCache(bar_spec=bar.bar_spec, lookback_role=role)
            self._caches[key] = rc
        rc.append(bar)

    def arrays(self, stream: DataStream, role: Literal["signal", "entry", "exit"]) -> "BarArrays":
        return self._caches[(stream.instrument, stream.bar_spec, role)].arrays()

    def compute_projected_memory_mb(self) -> float:
        """Aggregate projected memory across all (inst, spec, role) caches — per Fix 1 AC."""
        return sum(rc.projected_memory_mb() for rc in self._caches.values())
```

Memory bounds and per-(BarSpec, role) lookback remain M4's responsibility. Strategies needing longer history declare the appropriate `role` on their `Subscription` — not a new M6 knob.

**Aggregate memory budget**: M4's 1.2 GB ceiling applies to the sum across all 3-tuple entries in the wrapper. `MultiInstrumentCache.compute_projected_memory_mb()` is the engine-wide observable; AC asserts it stays ≤ 1200 MB for the full declared subscription set of any portfolio the engine loads.

### Instrument Metadata (`v5/data/instruments.py`)

```python
@dataclass(slots=True)
class Instrument:
    instrument_id: InstrumentId
    tick_size: float             # min price increment
    lot_size: float              # min quantity increment
    min_notional: float          # min order value in quote currency
    maker_fee_bps: float         # maker fee in basis points
    taker_fee_bps: float         # taker fee in basis points
    margin_tiers: list[tuple[float, float]]  # [(notional_threshold, maint_margin_rate), ...]
    max_leverage: float          # max allowed leverage
    funding_interval_s: int      # funding settlement interval in seconds (typically 28800 = 8h)
    # C2 (Round 2): replaces free-string `contract_type`. Narrows the vocabulary
    # and makes it an *asset-class sub-type*, not a re-statement of asset_class.
    contract_subtype: Literal["perpetual", "quarterly", "dated", "european", "american"] | None = None
    # Deprecated alias during M6 migration; removed in M9 (see M9 brief "M6 Impact").
    contract_type: str | None = None   # DEPRECATED — use contract_subtype; dropped in M9 (N3/C2)
```

**`contract_subtype` semantics (C2, Round 2)**:
- `asset_class="spot"` → `contract_subtype=None` (no contract variants for spot)
- `asset_class="perp"` → `contract_subtype="perpetual"` (always)
- `asset_class="future"` → `contract_subtype="quarterly"` | `"dated"` (specific expiry variant)
- `asset_class="option"` → `contract_subtype="european"` | `"american"` (exercise style)

M6 keeps the `contract_type` field as a deprecated alias during migration; M9 consolidation renames callers and drops it per the M9 brief's M6-impact section.

Fetched from venue info endpoints at `connect()`. Used by sizing (min_notional clamp), dust threshold calculation, and liquidation math.

### Non-Bar Event Types (`v5/data/types.py`)

Per the Fix 3 scope trim, M6 ships **only** event types with a current or near-term consumer. First-class event types routable through MessageBus via the same `Subscription` mechanism as bars:

```python
@dataclass(slots=True)
class FundingRate:
    instrument_id: InstrumentId
    ts_event: int               # epoch ns
    rate: float                 # funding rate (e.g., 0.0001 = 1bp)
    next_funding_ts: int        # epoch ns of next settlement

@dataclass(slots=True)
class MarkPrice:
    instrument_id: InstrumentId
    ts_event: int
    mark_price: float
    index_price: float          # venue-reported index price at mark time (NOT a standalone IndexPrice stream)

@dataclass(slots=True)
class Trade:
    """Tape/print from Binance aggTrades WS — new in M6 for crypto microstructure signals."""
    instrument_id: InstrumentId
    ts_event: int               # epoch ns
    price: float
    qty: float
    side: Literal["BUY", "SELL"]   # aggressor side
    trade_id: int               # monotonic per-instrument dedupe id
```

`BookSnapshot`, `Liquidation`, `OpenInterest`, `Ticker`, `IndexPrice` are **deferred** — see the "Deferred event types" table in Scope.

Strategies subscribe to these identically to bars: `Subscription(stream, handler, warmup)`.

### Clock Abstraction (`v5/clock.py`)

**[M4-shipped: `v5/testing.py::TestClock` — no duplicate needed]**

M6 adds only the `Clock` Protocol and `LiveClock` wall-clock implementation. `TestClock` stays in `v5/testing.py` as M4 shipped it.

```python
class Clock(Protocol):
    def now(self) -> int: ...      # epoch nanoseconds

class LiveClock:
    """Wall clock for paper/live trading."""
    def now(self) -> int: ...      # wraps time.time_ns()

# TestClock: [M4-shipped in `v5/testing.py`] — M6 imports it for tests, does not redefine.
```

**Clock-read prohibition** (applies to STRATEGY CODE only):
  - Strategies MUST use `clock.now()` via `UniverseContext.clock`
  - Direct `time.time()`, `time.time_ns()`, `datetime.now()`, `time.monotonic()`,
    `time.perf_counter()` in strategy code raise at strategy `__init__` time
    (enforced via an AST scan in the strategy loader)
  - Infrastructure code (DataClient subclasses, rate-limit trackers, WS
    heartbeat timers, reconnect backoff) MAY read wall-clock directly —
    these reads do NOT affect determinism because they don't feed into
    per-bar signal computation. Infrastructure wall-clock reads are
    documented in the module docstring of each client class.
  - Test helpers (TestClock `set_time`, `advance`) bypass the prohibition
    by construction (they ARE the clock).

Sim-loop / simulator.py hot path retains the strict AC24 prohibition
(no wall-clock reads at all — enforced by existing M4 test
test_m4_determinism.py::test_no_wall_clock_in_simulator).

- `UniverseContext` never exposes bars with `ts_event > clock.now()` — no look-ahead leakage in backtest.
- `TestClock` (M4) advances deterministically: set to bar close time as each bar is processed.
- `LiveClock` wraps real wall time.

### MessageBus (`v5/data/bus.py`)

```python
@dataclass(frozen=True, slots=True)
class SubscriptionHandle:
    """Opaque handle returned by MessageBus.subscribe. Pass to unsubscribe."""
    _id: int

class MessageBus:
    """Pub/sub with deterministic ordering for replay.

    Subscribers are dispatched in registration order — FIFO.
    Implementation uses an ordered dict (Python 3.7+ insertion-order) keyed
    by SubscriptionHandle._id to preserve determinism across runs.

    No set iteration on hot paths — AC24 within-build determinism invariant.
    """
    def publish(self, topic: DataStream, event: Any) -> None:
        """Dispatch `event` to every handler subscribed to `topic`, in
        registration order. Returns after all handlers complete synchronously.
        Handler exceptions are caught per-handler and logged; they do NOT
        interrupt dispatch to remaining handlers."""

    def subscribe(
        self,
        topic: DataStream,
        handler: Callable[[Any], None],
    ) -> SubscriptionHandle:
        """Register handler for topic. Returns opaque handle for unsubscribe."""

    def unsubscribe(self, handle: SubscriptionHandle) -> None: ...
```

---

## Multi-Source Routing

`DataEngine` holds a priority-ordered `list[DataClient]`. On `subscribe(stream)`, a 3-step cascade:

1. **Find supporting client by priority**: iterate clients where `client.supports(stream)` is True; pick highest priority (Binance-WS > Binance-REST > parquet-replay).
2. **Try M4 aggregation if no native support**: find a client with a smaller `BarSpec` (e.g., 1m) and wire it through the M4-shipped aggregator appropriate for the mode:
   - **Backtest**: `v5/data_resampler.py::DataResampler` (eager pre-materialize).
   - **Paper/live streaming**: `v5/streaming_consolidator.py::StreamingConsolidator` (byte-identical to eager per M4 AC33).

   The aggregator emits `source="INTERNAL"` on the bus. M6 adds only the routing glue — the aggregation primitives are shipped.
3. **Raise at startup if still unavailable**: clear error (not silent no-op). Strategies know immediately if their data needs cannot be satisfied.

First-period handling (discard partial first bar) is already specified + implemented in `StreamingConsolidator` per M4. M6 does not re-specify.

### WS + REST + Reconnect Pattern (4-step)

One `DataClient` per (venue, transport), sharing connection state:

```
subscribe(stream):
  1. REST.request_bars(end - warmup*step, end)      -> seed cache
  2. WS.subscribe(stream)                           -> live bars appended
  3. On WS disconnect: REST polling fallback        -> same interface, strategies don't see disconnect
  4. On WS reconnect: REST.request_bars(last_seen, now) -> gap fill
```

Strategies see one continuous stream; reconnect + backfill is entirely internal.

### REST+WS Merge Strategy

On WS reconnect, engine enters a MERGE WINDOW (configurable, default 30s) during which both REST backfill and WS live bars arrive. A per-`DataStream` merge buffer holds bars ordered by `ts_event`. Bars with `ts_event` already in the ring buffer are dropped (dedup). Out-of-order bars (WS delivers T+2 before REST returns T+1) are inserted at the correct position in the merge buffer, then flushed to the ring buffer in order. After the merge window closes, the merge buffer is drained and normal single-source mode resumes.

### BinanceWSClient Multiplexing

ONE class handles 1m + 1h + miniTicker + aggTrades (not 4 classes). This replaces v4's PriceMonitor which manages 3 parallel WebSocket objects (`ws_perp_1m`, `ws_perp_1h`, `ws_spot_miniTicker`) each with their own connection/reconnect logic. The single `BinanceWSClient` presents a unified `DataClient` interface.

### BinanceWSClient stop-and-redecompose trigger (Fix 6)

Budget is raised to **14h** (see Time Estimate) to reflect v4 PriceMonitor's real complexity: 831 lines, 3 parallel WS, sharding, reconnect, merge windows, partial-bar suppression. Reality is 14-18h, not 10h.

**Trigger**: if `BinanceWSClient` extraction exceeds **18h**, OR any WS edge case surfaces that v4 PriceMonitor handled but the M6 rewrite doesn't:

- **STOP** the extraction
- Report to user with the specific edge case + hours invested
- Consider falling back to wrapping v4 `PriceMonitor` behind the `DataClient` Protocol (bridging-shim pattern) rather than a full rewrite. The shim route preserves all v4 WS battle-hardening at the cost of carrying 831 lines of legacy code through the migration — acceptable trade-off if the rewrite is bleeding schedule.

This trigger exists because v4's PriceMonitor is the single most battle-tested piece of live-venue code in the codebase. Rewriting it from scratch has asymmetric risk: a subtle WS reconnect bug in M6 costs real money on live paper strategies.

### WS Connection Sharding

Binance enforces 200 streams per WebSocket connection. With 236 tokens x 2 streams (1m kline + 1h kline) = 472 streams minimum, `BinanceWSClient` MUST shard across 3+ connections automatically. Implementation: `BinanceWSClient._connections: list[WSConnection]`, each holding up to 200 streams. New subscriptions are assigned to the connection with fewest streams. Reconnect logic operates per-connection.

---

## Rate-Limit Tracker + Time Drift + Symbol Resolution (Fix 4)

Three venue-hygiene concerns live inside the Binance clients. They're scoped here so the design phase doesn't invent them ad-hoc.

### REST rate-limit weight tracker (`BinanceRESTClient`)

Binance REST endpoints return a weight budget header; the account-level cap is **1200 req/min weight**. A reconnect storm (e.g. 3 sharded WS connections drop simultaneously and each triggers 236 parallel 1m backfills = 708 REST calls in one burst) will blow the budget and get the IP banned for up to 15 minutes.

```python
class BinanceRESTClient:
    _weight_budget: int = 1200           # current window remaining weight
    _weight_window_reset_ts: int = 0     # epoch ns when the 1-min window resets

    def _check_budget(self, weight_cost: int) -> None:
        if self._weight_budget < weight_cost:
            wait_ms = max(0, (self._weight_window_reset_ts - time.time_ns()) // 1_000_000)
            if wait_ms > 0:
                raise RateLimitExceeded(retry_after_ms=wait_ms)
        self._weight_budget -= weight_cost

    def _on_response(self, headers: dict) -> None:
        # Trust the venue's returned X-MBX-USED-WEIGHT-1M header as source of truth.
        self._weight_budget = 1200 - int(headers.get("X-MBX-USED-WEIGHT-1M", 0))
```

`RateLimitExceeded` is a recoverable exception — the calling code (typically gap-fill logic inside `DataEngine`) must back off with exponential jitter before retrying. Under sustained saturation, `DataEngine` should prioritize live WS over backfill (freshness > history).

### Time-drift sync

Both `BinanceWSClient` and `BinanceRESTClient` periodically sync against `GET /api/v3/time` (spot) / `GET /fapi/v1/time` (perp) to measure clock skew:

- Sync interval: every 5 min (configurable)
- `venue_clock_offset_ms: int` — the engine-wide offset between venue clock and local wall clock
- If |drift| > 500 ms: log `WARN` (strong hint of NTP issues on the host)
- If |drift| > 5000 ms: log `ERROR` and publish a `ClockDriftHigh` event on the bus (backtest/live parity is suspect when drift is large)
- `Bar.ts_event` is always the venue's reported close time (never re-offset); the drift value is informational and feeds the `ts_init - ts_event` spread observability.

### Symbol resolution + VenueCapabilities (`InstrumentRegistry`) — Addition 6

Strategies MUST NOT manually construct `InstrumentId("BTCUSDT", Venue.BINANCE, "perp")` — the symbol namespace is venue-specific (e.g., Binance uses `BTCUSDT` for both spot and perp; the disambiguator is the asset_class, and humans type `"BTCUSDT"` without thinking about it).

```python
@dataclass(frozen=True, slots=True)
class VenueCapabilities:
    """Declared by each venue DataClient at connect(). Lets DataEngine fail fast
    when a Subscription asks for data the venue cannot serve (Addition 6 + C3)."""
    venue: Venue
    supported_asset_classes: frozenset[Literal["spot", "perp", "future", "option"]]
    supported_data_kinds: frozenset[DataKind]
    min_bar_resolution_minutes: int        # e.g. 1 for Binance (1m bars)
    has_funding: bool
    has_mark_price: bool
    has_trade_tape: bool
    rest_weight_budget_per_min: int        # e.g. 1200 for Binance spot+perp
    # C3 (Round 2): which DataStream.price_type values the venue actually publishes.
    # Populated per-venue at connect(); consumed by Subscription.__new__ validation to
    # fail fast when a strategy requests an unsupported price_type (e.g. MID on a
    # venue that only publishes LAST via aggTrades). Binance example:
    #   frozenset({"LAST","MARK","INDEX"})  — MID requires L1 book (not in M6 scope)
    supported_price_types: frozenset[Literal["LAST", "MID", "MARK", "INDEX"]]


class InstrumentRegistry:
    """Venue-keyed metadata + capability store (Addition 6). Each venue DataClient's
    `connect()` populates its own slice of `_metadata` and `_capabilities` — the
    registry is a read cache, not a live API call on the resolve() hot path."""
    _metadata: dict[Venue, dict[InstrumentId, "Instrument"]]
    _capabilities: dict[Venue, VenueCapabilities]

    def resolve(self, symbol: str, venue: Venue, asset_class: str) -> InstrumentId:
        """Canonical symbol→InstrumentId lookup. Raises SymbolNotFound if unknown at this venue."""
        ...

    def metadata(self, instrument: InstrumentId) -> "Instrument":
        """Return the full Instrument metadata (tick_size, lot_size, margin_tiers, ...)."""
        ...

    def capabilities(self, venue: Venue) -> VenueCapabilities:
        """Return the declared capabilities for a venue — used by DataEngine to fail
        fast if a Subscription asks for data the venue does not serve."""
        ...

    def list_perp_universe(self, venue: Venue = Venue.BINANCE) -> list[InstrumentId]:
        """Enumerate all active perp contracts at a venue — replaces v4's ad-hoc universe file scanning."""
        ...
```

The registry is populated at `DataEngine.start()` by each venue's `DataClient.connect()` writing its own `_metadata[venue]` and `_capabilities[venue]` slice (via `exchangeInfo` for Binance spot + perp). Strategies' `required_data()` calls `registry.resolve(...)` to construct the `DataStream`s. Subscriptions that would require a capability the venue doesn't declare (e.g. `DataKind.TRADE` on a venue with `has_trade_tape=False`) raise at `DataEngine.subscribe(...)` — not silently.

---

## Paper Migration Safeguards (Fix 2)

Per user decision, the `paper_engine.py` migration stays in M6 (not split into a follow-up milestone). Three safeguards make this safe:

### 1. Feature flag (default OFF)

```python
class DataEngine:
    use_data_engine_flag: bool = False   # first ship: legacy PriceMonitor path remains active
```

When `False`, `paper_engine.py` uses its existing v4 PriceMonitor + LiveFetcher wiring — M6's new clients are imported but not activated. When `True` (per-strategy or fleet-wide), data routes through `DataEngine` + `BinanceWSClient` + `BinanceRESTClient`. Flag flip is a single config change, restart-safe.

### 2. Hard merge gate — 24h recorded-WS shadow replay

Before `use_data_engine_flag=True` can be rolled out to **any** live paper strategy, M6 must pass a 24-hour shadow replay:

- Record 24h of real Binance WS traffic (via the existing `PriceMonitor` or a tap) for the current live paper fleet's universe (≥50 instruments, 1m + 1h streams)
- Replay the recorded traffic twice in parallel:
  - **Pre-M6 path**: v4 `PriceMonitor` → strategy `on_bar`
  - **Post-M6 path**: M6 `BinanceWSClient` (fed by the recording) → `DataEngine` → `MultiInstrumentCache` → strategy `on_bar`
- **Parity criterion**: bit-identical bar streams (OHLCV + ts_event) delivered to each strategy's `on_bar` across the full 24h window
- Tolerance: **zero** on OHLCV (the recording is byte-deterministic, so any divergence is an M6 bug). Non-zero tolerance would mask real bugs.

The shadow-replay harness is part of M6 scope (budgeted separately — see time estimate). The brief's Parity Gate section incorporates this as a required gate.

### 3. Rollback plan

- If any live strategy shows unexpected behavior post-flag-flip: set `use_data_engine_flag=False`, restart, done. Old path is still the default so revert is zero-code.
- Keep the v4 PriceMonitor + LiveFetcher code paths intact through M7 (don't delete). Delete only after the full fleet has run ≥2 weeks on the M6 path with no incidents.

### Stop-and-redecompose trigger (paper migration)

If 24h shadow replay exceeds parity tolerance on ANY live strategy: **STOP**. Do NOT merge the paper migration. Options to present to the user:

1. Defer paper migration to a follow-up milestone (M6.5 or M7) — ship M6's data architecture without flipping the flag on any strategy.
2. Wrap v4 `PriceMonitor` behind the `DataClient` Protocol as a bridging shim, migrate strategies one at a time onto the shim, and replace the shim's internals in a later milestone.

Either option keeps the core v5/data/ architecture landing in M6 but unblocks the schedule if paper shadow replay reveals unexpected complexity.

### Shadow Replay Ops Runbook (AC-D12 operational details)

**Fixture acquisition** (Phase 4 one-shot):
  - Run a read-only PriceMonitor tap alongside the live paper engine for 24h
  - Capture all WS frames + REST calls + timestamps to `v5/tests/fixtures/shadow_replay_24h/`
  - Files: `binance_ws_tap_{date}.jsonl` + `binance_rest_tap_{date}.jsonl`
  - Size budget: ≤ 500MB (rotate if exceeded)
  - Committed in-repo under Git LFS (or compressed zstd sidecar)

**Replay execution**:
  - `v5/tests/shadow_replay.py` harness (M4-shipped) feeds the tap into both engines
  - Feature flag OFF: routes through legacy PriceMonitor + LiveFetcher
  - Feature flag ON: routes through DataEngine + BinanceWSClient + BinanceRESTClient
  - Both engines must run against the same TestClock seeding

**Divergence threshold** (AC-D12 hard merge gate):
  - Bar output: byte-identical (OHLCV per token per ts_event must match exactly)
  - Funding rate: within 1 bps (floating-point tolerance)
  - Mark price: within 1 bps
  - Trade tape: within 1 trade-count delta per second (ordering may vary by ≤ 1ms)
  - Any divergence beyond these thresholds = HARD FAIL, block merge

**Divergence reporting**:
  - Output: `v5/tests/fixtures/shadow_replay_report_{date}.md` — per-strategy delta summary
  - Format: same as M4 AC41 shadow replay report (markdown table + structured diffs)
  - PR attachment on merge review: link to report + explicit sign-off from reviewer

**Rollback trigger**:
  - If hard-fail divergence detected: flip feature flag OFF in `configs/paper.json`;
    restart paper_engine; data path reverts to legacy PriceMonitor in ≤ 30s
  - File an AIPIP with divergence root cause before retrying migration

**Stop-redecompose trigger** (re-iterating the paper-migration stop trigger above):
  - If ANY live strategy shows unexpected behavior AFTER paper migration
    (positions diverge vs paper_engine flag=OFF baseline by > 20 bps P&L):
    STOP, flip flag OFF, capture state, file AIPIP, do NOT force-merge

---

## Gap Detection (`v5/data/gaps.py`) — 3-mode policy (Fix 5)

Every `Bar` carries:
- `ts_event` (epoch ns, close time) -- FIX `TransactTime(60)`
- `ts_init` (epoch ns, local receive timestamp -- no direct FIX analog; FIX `SendingTime(52)` is counterparty send time, not local receive)
- Monotonic per-`DataStream` sequence enforced

Gap detection is **upstream of `MultiInstrumentCache`** — the detector sits between the `DataClient` and the cache wrapper, so monotonicity is guaranteed before any cache write. This preserves M3's monotonicity invariant inside `RollingCache` (no duplicate/stale/out-of-order bars ever reach the ring buffer).

Gap detection specifics:
- Cache rejects bars with `ts_event <= last_ts` for that `DataStream` (duplicate/stale rejection)
- On rejection: logs the gap with `DataStream` + expected vs actual ts_event
- On gap detection (ts_event jump > expected interval): N REST backfill retries (default N=3)
- `GapDetected` event published to MessageBus for observability

### GapPolicy — 3 modes

Per-`Subscription` policy field governs what happens when REST backfill cannot close the gap:

```python
class GapPolicy(Enum):
    STRICT = "strict"       # default — raise DataGapError after N REST retries fail
    NAN_FILL = "nan_fill"   # emit Bar with NaN OHLCV at missing ts_event slot(s); downstream must handle NaN
    SKIP = "skip"           # silently drop gap; legacy v4 behavior, opt-in only (loud log)
```

- **STRICT** is the default and the recommended mode. Silent data loss is a research-grade footgun; corrupted indicator state from skipped bars is worse than a loud failure.
- **NAN_FILL** suits strategies that already handle NaN defensively (e.g., ratio strategies that propagate NaN through the indicator stack cleanly). NAN bars are still delivered to the cache at the correct `ts_event` slot so monotonicity and bar-count arithmetic hold.
- **SKIP** preserves v4 PriceMonitor behavior for any strategy migrating verbatim. The engine emits a `WARN` log per gap and a `ERROR`-level summary every 60s if gap rate exceeds 0.1% of bars.

---

## Key Acceptance Criteria

**AC-D1 -- Memory bounded by construction (via M4 reuse, 3-tuple key)**: `v5/data/cache.py::MultiInstrumentCache` composes M4's `v5/rolling_cache.py::RollingCache` keyed by the 3-tuple `(InstrumentId, BarSpec, role)`. Memory bounds, ring-buffer storage, and role-aware lookback (signal=250d/entry=7d/exit=2d) are inherited from M4 — M6 does not redefine them.

**AC-D1a -- 3-tuple cache key (Fix 1)**: `MultiInstrumentCache` MUST key caches by `(InstrumentId, BarSpec, role)`. The same `(BTC, 1h)` `DataStream` subscribed with `role="signal"` and `role="entry"` produces **two distinct** underlying `RollingCache` instances with lookbacks 250d and 7d respectively. A single `default_role` collapsing all caches into one lookback is explicitly rejected as a design.

**AC-D1b -- Aggregate memory budget (Fix 1)**: `MultiInstrumentCache.compute_projected_memory_mb()` returns the aggregate across all 3-tuple entries. For any portfolio the engine loads, this aggregate MUST be ≤ **1200 MB** (M4's declared budget). The engine logs this value on startup and on any subscription change.

**AC-D2 -- Strategy-declared subscriptions**: `Strategy.required_data() -> list[Subscription]`. Runner unions across all strategies in the portfolio. Only declared `DataStream`s are fetched. `Subscription` carries `stream`, `handler`, `warmup`, `role`, `gap_policy`, `poll_interval_s`, `transport_preference`, and `fallback_allowed` (the last three per Addition 3).

**AC-D3 -- DataClient protocol (split per Addition 5)**: `DataClient` Protocol declares `venue: Venue`, `supported_modes: frozenset[TransportMode]`, `supports(stream, mode)`, `connect`, `disconnect`, `request(stream, start_ns, end_ns)` (PULL_ONCE), `replay(stream, start_ns, end_ns)` (REPLAY). `LiveDataClient(DataClient)` extends with `subscribe`/`unsubscribe` (PUSH) and `subscribe_scheduled` (PULL_SCHEDULED). Pluggable per (venue, transport). One `BinanceWSClient` multiplexes 1m + 1h + aggTrades streams (not 3 separate WS classes). `ParquetReplayClient` implements `DataClient` only — REPLAY + PULL_ONCE, no streaming.

**AC-D4 -- Multi-source routing with aggregation fallback (M4 reuse + Addition 4)**: `DataEngine` resolves clients via the `DataClientRegistry` keyed by `stream.instrument.venue`, not a hardcoded `list[DataClient]`. On `subscribe(stream)`: `registry.get_clients(instrument)` returns priority-ordered venue clients; find supporting client (given `stream.data_kind` + required `TransportMode`); if none, wire a smaller-spec client through M4's `DataResampler` (backtest) or `StreamingConsolidator` (paper/live). If still unavailable, raise at startup (not silent no-op). M6 does NOT re-implement aggregation.

**AC-D5 -- Gap detection with 3-mode policy (Fix 5)**: every `Bar` carries `ts_event` (close time) + `ts_init` (receive time) + monotonic per-`DataStream` sequence. Gap detection runs upstream of `MultiInstrumentCache`. `GapPolicy.STRICT` (default) raises `DataGapError` after N REST retries; `GapPolicy.NAN_FILL` emits NaN-OHLCV bars at missing slots; `GapPolicy.SKIP` logs and drops. Cache rejects bars with `ts_event <= last_ts` regardless of mode (monotonicity is invariant).

**AC-D6 -- Backtest/live parity via Clock abstraction**: `v5/clock.py` provides `Clock` Protocol + `LiveClock` (wall clock). `TestClock` is M4-shipped in `v5/testing.py` and reused. Strategies use `clock.now()`; direct `time.time()` forbidden. `UniverseContext` never exposes bars with `ts_event > clock.now()` — no look-ahead leakage in backtest.

**AC-D7 -- Non-bar event types (trimmed per Fix 3)**: `FundingRate`, `MarkPrice`, and `Trade` are first-class event types (not bars). Strategies subscribe to them via the same `Subscription` mechanism. `BookSnapshot`, `Liquidation`, `OpenInterest`, `Ticker`, `IndexPrice` are explicitly **deferred** from M6 (see Scope / Deferred event types).

**AC-D8 -- Instrument metadata + registry (Fix 4 + Additions 1/4/6 + C2)**: `Instrument` dataclass carries `tick_size`, `lot_size`, `min_notional`, `maker_fee_bps`, `taker_fee_bps`, `margin_tiers`, `max_leverage`, `funding_interval_s`, and `contract_subtype: Literal["perpetual","quarterly","dated","european","american"] | None` (C2 — narrowed from free-string `contract_type`, which remains as a deprecated alias through M6 and is dropped in M9). Fetched from venue info endpoints at connect. `InstrumentRegistry.resolve(symbol: str, venue: Venue, asset_class: str) -> InstrumentId` is the only sanctioned way strategies construct instrument ids. Registry is venue-keyed (`_metadata: dict[Venue, dict[InstrumentId, Instrument]]`) and exposes `VenueCapabilities` per venue.

**AC-D9 -- Backpressure**: If a subscriber callback is slow (>100ms for a 1m bar), DataEngine logs a warning. No backpressure or dropping -- all bars are delivered. Strategies with heavy computation should offload to background threads.

**AC-D10 -- REST rate-limit tracker (Fix 4)**: `BinanceRESTClient` tracks the venue-reported weight budget via `X-MBX-USED-WEIGHT-1M`, exposes a `_check_budget(weight_cost)` method that raises `RateLimitExceeded(retry_after_ms=...)` when the budget would go negative, and trusts the venue header as source of truth on every response. Callers (gap-fill in `DataEngine`) back off with exponential jitter. Under saturation, live WS is prioritized over backfill.

**AC-D11 -- Time-drift sync (Fix 4)**: Both WS and REST clients periodically sync against Binance's `time` endpoint every 5 min. `venue_clock_offset_ms` is exposed engine-wide. Drift > 500 ms logs `WARN`; drift > 5000 ms logs `ERROR` and publishes `ClockDriftHigh` on the bus.

**AC-D12 -- Paper migration feature flag + shadow replay (Fix 2)**: `DataEngine.use_data_engine_flag: bool = False` default. Before `True` can ship on any live paper strategy, a 24h recorded-WS shadow replay MUST demonstrate bit-identical bar streams between the pre-M6 PriceMonitor path and the post-M6 `DataEngine` path for the current live fleet universe. Tolerance: zero on OHLCV. Any divergence blocks the flag flip.

**AC-D13 -- BinanceWSClient realistic budget + stop-trigger (Fix 6)**: WS extraction is budgeted at 14h with a stop-and-redecompose trigger at 18h. If the trigger fires, the fallback is to wrap v4 `PriceMonitor` behind the `DataClient` Protocol as a bridging shim rather than completing the rewrite.

**AC-D14 -- DataKind routing (Addition 2)**: `DataStream` carries `data_kind: DataKind` + optional `bar_spec` + `price_type`. Construction MUST raise `ValueError` when (`data_kind==BAR` and `bar_spec is None`) or (`data_kind != BAR` and `bar_spec is not None`). `DataEngine` dispatches to the appropriate handler code path per `data_kind`: BAR goes through the gap detector + `MultiInstrumentCache`; TRADE/FUNDING_RATE/MARK_PRICE bypass the cache (they are event streams, not bar streams); INSTRUMENT_INFO is a one-shot fetch, no streaming. `price_type` is accepted in `{"LAST","MID","MARK","INDEX"}` and propagated to the venue client.

**AC-D15 -- Transport-mode dispatch (Addition 5)**: `DataClient.supported_modes: frozenset[TransportMode]` is declared by each implementation. `DataEngine.subscribe(subscription)` MUST choose the transport by (a) the subscription's `transport_preference`, (b) the client's `supported_modes`, (c) fallback policy. A client that does not support a requested mode raises `NotImplementedError` on the corresponding method. `ParquetReplayClient.supported_modes == {TransportMode.REPLAY, TransportMode.PULL_ONCE}` (no PUSH / PULL_SCHEDULED). `BinanceWSClient.supported_modes ⊇ {TransportMode.PUSH}`; `BinanceRESTClient.supported_modes ⊇ {TransportMode.PULL_ONCE, TransportMode.PULL_SCHEDULED}`. If `transport_preference="WS"` and `fallback_allowed=False`, WS drop does NOT trigger REST fallback — the subscription enters a degraded state observable on the bus.

**AC-D16 -- FIX docstring coverage (Addition 7)**: A grep over `v5/data/` source docstrings MUST return all 14 FIX tags listed in the Core Abstractions FIX vocabulary block: `MDEntryType(269)`, `Side(54)`, `SecurityID(48)`, `SecurityIDSource(22)`, `SecurityExchange(207)`, `Product(460)`, `MinPriceIncrement(969)`, `MinTradeVol(562)`, `MarketDataRequest(V)`, `MDReqID(262)`, `SubscriptionRequestType(263)`, `NoMDEntryTypes(267)`, `MDUpdateType(265)`, `TransactTime(60)`. Missing any tag fails the AC. The verifier is the acceptance test `T-D16`, which shells out to `grep -R` and asserts count ≥ 1 per tag.

**AC-D17 -- Venue + DataClientRegistry (Addition 4 + C1 priority)**: `InstrumentId.venue` is typed as `Venue` (enum, not `str`). `DataEngine` holds a `DataClientRegistry`, not a hardcoded `list[DataClient]`. On `subscribe(stream)`, the engine calls `registry.get_clients(stream.instrument)` — which returns only the clients registered for `stream.instrument.venue`, **sorted by transport priority `PUSH > PULL_ONCE > REPLAY`**. `DataEngine.subscribe(...)` walks the priority list and binds to the first client whose `supports(stream, mode)` returns True. Adding a new venue (OKX, Bybit, Deribit) is a `registry.register(venue, factory)` call — the engine is **not** edited. `registry.registered_venues()` exposes the active set for observability.

**AC-D18 -- Subscription validation (Addition 3 + N1 + C3)**: `Subscription` is a `NamedTuple`; construction-time validation is performed via a `Subscription.__new__` override (NamedTuple does **not** have `__post_init__`) that calls a private `_validate_subscription(stream, warmup, ...)` helper before returning the tuple. The following invariants MUST be enforced:
  - `gap_policy != STRICT` requires `stream.data_kind == DataKind.BAR` (gap modes apply only to bars)
  - `poll_interval_s is not None` requires `stream.data_kind in {FUNDING_RATE, MARK_PRICE}`
  - `poll_interval_s is None` for `stream.data_kind in {BAR, TRADE}`
  - `transport_preference="WS"` + `fallback_allowed=False` MUST be honored — no implicit REST fallback on WS drop (AC-D15 is the dispatch consequence)
  - **(C3)** `subscription.stream.price_type` MUST be in `registry.capabilities(venue).supported_price_types`; else raise `PriceTypeNotSupported(venue, price_type)`. Enforced at `DataEngine.subscribe(...)` time once the venue's capabilities are known.
Invariant violations raise `ValueError` (or `PriceTypeNotSupported` for the price-type rule) with a descriptive message naming the offending field.

**AC-D19 -- MessageBus subscriber dispatch is insertion-order deterministic**: `MessageBus.publish(topic, event)` MUST dispatch to handlers in strict registration (FIFO) order. Internal storage MUST be an ordered dict keyed by `SubscriptionHandle._id` — no `set` / `frozenset` iteration on the hot-path publish/dispatch functions. A grep test (`T-D20`) asserts zero matches of `set(` / `frozenset(` iteration inside `v5/data/bus.py` hot-path functions (`publish`, `subscribe`, `unsubscribe`). Handler exceptions are caught per-handler and logged without interrupting dispatch to remaining handlers (determinism invariant AC24 — within-build reproducibility).

**AC-D20 -- Infrastructure wall-clock carve-out documented**: Each `DataClient` subclass module (`v5/data/clients/binance_ws.py`, `v5/data/clients/binance_rest.py`, `v5/data/clients/parquet_replay.py`) MUST document in its module docstring the specific wall-clock reads it performs (e.g. `time.time_ns()` for rate-limit window tracking, WS heartbeat, reconnect backoff). These reads are permitted infrastructure reads and do NOT violate AC-D6's strategy-facing clock prohibition. The strategy loader's AST scan (per AC-D6) applies to strategy files only — it MUST NOT flag `v5/data/clients/**` modules. A grep test (`T-D21`) asserts each DataClient module docstring contains an "Infrastructure wall-clock reads:" section enumerating the permitted reads.

---

## Subsumption Table

What becomes a free consequence of the data architecture:

| Previously discussed item | Becomes free consequence of data arch |
|---|---|
| `bar_resolution` config (C-8) | Just a M4 `BarSpec` on `Subscription` — no dedicated config field |
| Sub-hourly dispatch (Q4 wiring) | Just a 5-minute `Subscription`; M4 bar_processor handles any `BarSpec` |
| Paper vs backtest switch | Swap `LiveDataClient` for `ParquetReplayClient` — one config flag |
| `hist_cache` internalization (C-9) | M4's `RollingCache` handles it natively; no parameter plumbing |
| ADV computation | Derived bar stream via `source="INTERNAL"` (M4 aggregator) |
| TradingView / alt data integration | Client adapter — strategies subscribe same as any bar |

---

## Phase 3 Test Plan

### Data architecture tests

- **T-D1** `MultiInstrumentCache` memory bounds (via M4 reuse, 3-tuple key): append 3000 bars keyed by `(inst, 1h, "signal")` — cache holds exactly M4's configured lookback (oldest evicted). No redundant re-test of M4's `RollingCache` internals; we test composition only.
- **T-D1a** 3-tuple key distinctness (Fix 1): subscribe same `(inst, 1h)` stream twice with `role="signal"` and `role="entry"`; assert `MultiInstrumentCache` creates TWO distinct `RollingCache` instances with lookbacks 250d and 7d respectively. Appending a bar to one does not mutate the other's arrays.
- **T-D1b** Aggregate memory budget (Fix 1): build a `MultiInstrumentCache` populated with the full 236-token × 3-spec × 3-role declared subscription set for a representative portfolio; assert `compute_projected_memory_mb()` ≤ 1200.
- **T-D2** Strategy-declared subscription union: 3 strategies × 2 `DataStream`s each → `DataEngine` has 6 unique subscriptions, no dupes.
- **T-D3** Multi-source routing: primary client (WS) unavailable → fallback to REST client picks up.
- **T-D4** Aggregation via M4: only 1m client available; subscribe to `BarSpec.from_minutes(5)` → `DataResampler` (backtest path) / `StreamingConsolidator` (live path) produce correct 5m bars from 1m input. (Aggregator correctness is M4-tested; M6 tests only the wiring.)
- **T-D5** Gap detection STRICT: inject duplicate `ts_event` → cache rejects; inject stale `ts_event` → cache rejects with log; `GapDetected` event observed on bus; after N REST retries with no fill, raises `DataGapError`.
- **T-D5a** Gap detection NAN_FILL (Fix 5): same inputs as T-D5 but `GapPolicy.NAN_FILL`; assert Bar with NaN OHLCV is delivered to handler at the missing ts_event slot; cache monotonicity preserved.
- **T-D5b** Gap detection SKIP (Fix 5): same inputs with `GapPolicy.SKIP`; assert gap silently dropped (no NaN Bar delivered) but `WARN` logged.
- **T-D6** Clock isolation: strategy's `clock.now()` returns bar close time in backtest (using M4's `TestClock`); never sees future bar.
- **T-D7** `DataStream` vs M4 `BarType` disambiguation: importing `from v5.data.streams import DataStream` does not shadow `v5.rolling_cache.BarType`; both coexist with M3 back-compat callers unaffected.
- **T-D8** `paper_engine.py` migration parity with feature flag (Fix 2): with `use_data_engine_flag=False`, paper engine uses legacy PriceMonitor path and delivers identical bars as pre-M6 main. With `use_data_engine_flag=True`, over a 1-hour recorded-WS replay, `DataEngine` delivers bit-identical bars to handlers vs the legacy path.
- **T-D9** REST rate-limit tracker (Fix 4): simulate 1200 weight consumed; next call raises `RateLimitExceeded` with non-zero `retry_after_ms`; after window reset, calls succeed again.
- **T-D10** Time-drift sync (Fix 4): inject a mocked venue clock 1500ms ahead of local; assert `venue_clock_offset_ms` is populated; assert `WARN` log at 500ms threshold; force 6000ms drift and assert `ClockDriftHigh` event is published.
- **T-D11** Instrument registry (Fix 4 + Additions 1/4/6): `registry.resolve("BTCUSDT", venue=Venue.BINANCE, asset_class="perp")` returns the expected `InstrumentId` (with `venue: Venue.BINANCE`, `asset_class: "perp"`); unknown symbol raises `SymbolNotFound`; `metadata()` returns populated `Instrument` with all venue fields; `capabilities(Venue.BINANCE)` returns a `VenueCapabilities` with `has_funding=True`, `has_mark_price=True`, `has_trade_tape=True`, `min_bar_resolution_minutes=1`, `rest_weight_budget_per_min=1200`.
- **T-D12** Deferred event types not plumbed (Fix 3): attempting to subscribe to a `BookSnapshot`/`Liquidation`/`OpenInterest`/`Ticker`/`IndexPrice` event type raises a clear "deferred to future milestone" error at engine construction — not a silent no-op.
- **T-D13** `Trade` event (Fix 3): subscribe to Binance aggTrades via `BinanceWSClient`; assert `Trade` events are delivered to the handler with populated `price`, `qty`, `side`, `trade_id`; duplicate `trade_id` deduped.
- **T-D14** DataKind routing (Addition 2 → AC-D14): construct `DataStream(inst, data_kind=DataKind.BAR, bar_spec=None)` → raises `ValueError`. Construct `DataStream(inst, data_kind=DataKind.FUNDING_RATE, bar_spec=BarSpec.from_minutes(1))` → raises `ValueError`. Valid BAR stream routes through gap detector + `MultiInstrumentCache`; valid TRADE/FUNDING_RATE/MARK_PRICE streams bypass the cache (no `RollingCache` instance created for them) and flow only through the bus to the handler. `price_type` values outside `{"LAST","MID","MARK","INDEX"}` rejected at construction.
- **T-D15** Transport-mode dispatch (Addition 5 → AC-D15): `ParquetReplayClient.supported_modes == {TransportMode.REPLAY, TransportMode.PULL_ONCE}`; calling `.subscribe(stream)` on it raises `NotImplementedError`. `BinanceWSClient.supported_modes` includes `PUSH`; calling `.replay(...)` on it raises `NotImplementedError`. Subscription with `transport_preference="WS"` + `fallback_allowed=False`: simulate WS drop → engine marks subscription degraded on the bus, does NOT invoke REST client.
- **T-D16** FIX docstring coverage (Addition 7 → AC-D16): `grep -R -F` over `v5/data/` docstrings for each of the 14 FIX tags (`MDEntryType(269)`, `Side(54)`, `SecurityID(48)`, `SecurityIDSource(22)`, `SecurityExchange(207)`, `Product(460)`, `MinPriceIncrement(969)`, `MinTradeVol(562)`, `MarketDataRequest(V)`, `MDReqID(262)`, `SubscriptionRequestType(263)`, `NoMDEntryTypes(267)`, `MDUpdateType(265)`, `TransactTime(60)`) returns ≥ 1 match per tag. Missing any tag fails.
- **T-D17** Venue + DataClientRegistry (Addition 4 → AC-D17): `InstrumentId.venue` is `Venue.BINANCE`, not `"BINANCE"` (type check). `DataEngine` constructed with an empty `DataClientRegistry` and a Binance subscription raises at `subscribe()` with "no client registered for venue BINANCE". After `registry.register(Venue.BINANCE, factory)`, the same subscription succeeds. `registry.registered_venues() == frozenset({Venue.BINANCE})`. A registered factory for `Venue.OKX` is invisible to a Binance subscription (isolation).
- **T-D18** Subscription validation (Addition 3 → AC-D18): `Subscription(stream=<TRADE stream>, ..., gap_policy=GapPolicy.NAN_FILL)` raises `ValueError` (gap modes only apply to BAR). `Subscription(stream=<BAR stream>, ..., poll_interval_s=60)` raises (BAR must have `poll_interval_s=None`). `Subscription(stream=<FUNDING_RATE stream>, ..., poll_interval_s=None)` raises (FUNDING_RATE requires `poll_interval_s`). `Subscription(stream=<FUNDING_RATE stream>, ..., poll_interval_s=480)` valid.
- **T-D19** 24h shadow replay harness (Fix 2) [formerly T-D14]: given a recorded WS fixture ≥ 24h, the harness runs pre-M6 and post-M6 paths in parallel and reports any divergence as a blocking failure. (This test shells out to the harness; the harness itself is a deliverable of M6.)

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M1** (v5 Fork) | **Required** — v5 namespace |
| **M3** (Memory Fix) | **Benefits from** — `RollingCache` origin; M4 now owns the canonical version |
| **M4** (BarProcessor) | **Required** — M6 imports M4's `BarSpec`, `RollingCache`, `DataResampler`, `StreamingConsolidator`, `TestClock`. M4 must ship before M6. |
| **M5** (Multi-leg Orders) | **Required** — `DataEngine`/`paper_engine.py` must stay compatible with M5's `Order`/`Leg` lifecycle when routing fills derived from bar events. |

---

## Time Estimate

**62-80 hours** (post-7-schema-additions; was 55-70h, 30-45h pre-quant-review, 35-50h earlier, 50-70h originally)

Raw total with all 7 original fixes **plus 7 schema additions** applied: **69h**. Banded range allows for estimation error and absorbs the BinanceWSClient stop-trigger budget.

| Item | Hours |
|---|---|
| `v5/data/` package layout + `__init__.py` + FIX-block docstring (Addition 7) + migration of M4-shipped module imports | 5 |
| `DataEngine` orchestrator (DataClientRegistry lookup per Addition 4, subscription table, routing cascade, data-kind dispatch per Addition 2) | 4 |
| `MessageBus` pub/sub with deterministic ordering for replay determinism | 3 |
| `BinanceWSClient` (3+ connection sharding, reconnect, merge window, partial-bar suppression; `supported_modes` + TransportMode dispatch per Addition 5; **raised from 10h per Fix 6**) | 14 |
| `BinanceRESTClient` (extract from LiveFetcher + weight tracker + time-drift sync per Fix 4; `PULL_ONCE` + `PULL_SCHEDULED` per Addition 5) | 6 |
| `ParquetReplayClient` (implements `DataClient` only — REPLAY + PULL_ONCE; Addition 5 split) | 3 |
| `MultiInstrumentCache` wrapper (3-tuple key + `compute_projected_memory_mb`, per Fix 1 / D3) | 3 |
| Gap detection with 3-mode `GapPolicy` (per Fix 5) | 3 |
| `Clock` Protocol + `LiveClock` (TestClock is M4-shipped) | 2 |
| `InstrumentRegistry` venue-keyed + `VenueCapabilities` (Addition 6) + symbol resolution + venue metadata (per Fix 4) | 4 |
| `Venue` enum + `DataClientRegistry` (Addition 4) | 2 |
| `DataKind` enum + extended `DataStream` validation + `price_type` dispatch (Addition 2) | 2 |
| Extended `Subscription` fields + construction validation (Addition 3 — `poll_interval_s`, `transport_preference`, `fallback_allowed`) | 1 |
| `TransportMode` enum + `DataClient`/`LiveDataClient` protocol split (Addition 5) | 1 |
| `FundingRate` + `MarkPrice` + `Trade` event types + MessageBus integration (per Fix 3) | 2 |
| `required_data()` Protocol on Strategy + runner union logic | 3 |
| `paper_engine.py` migration behind `use_data_engine_flag` + 24h shadow replay harness (per Fix 2) | 6 |
| Tests (acceptance tests T-D1–T-D19, including the 5 new T-D14–T-D18 + shadow replay harness smoke tests) | 6 |
| Documentation + deferred-feature notes + InstrumentId.asset_class rename audit (Addition 1) | 2 |
| TimeBarAggregator — **[M4-shipped: `DataResampler` + `StreamingConsolidator` — no duplicate needed]** | 0 |
| **Raw total** | **69** |

**Banded estimate: 62-80h.** The band reflects uncertainty on `BinanceWSClient` (14h nominal, 18h stop-trigger), shadow-replay harness debugging time, and the interaction surface between the 7 new schema elements (Venue typing / DataKind validation / TransportMode dispatch / VenueCapabilities fail-fast). If the WS extraction stop-trigger fires, the bridging-shim fallback is cheaper (~4h) than completing the rewrite, which keeps the ceiling from exploding past 80h.

---

## Parity Gate

- Paper trader runs with `v5/data/` clients **behind `use_data_engine_flag`** (default OFF)
- **Hard merge gate**: 24h recorded-WS shadow replay demonstrates bit-identical bar streams between pre-M6 PriceMonitor path and post-M6 `DataEngine` path for the current live paper fleet universe. Zero OHLCV tolerance. (Per AC-D12 + Paper Migration Safeguards.)
- Short-form parity check: 1-hour parallel run produces the same bar stream both ways
- Gap detection test catches intentionally missing bars under all three `GapPolicy` modes
- Memory bounded: `MultiInstrumentCache.compute_projected_memory_mb()` ≤ 1200 MB for the full declared subscription set; 24h simulated run stays under 100 MB RSS growth on top of the declared projection
- All v5 tests pass
- Flag rollout is per-strategy after shadow replay parity; rollback is `use_data_engine_flag=False` + restart

---

## M4 Impact (SHIPPED — duplicates pruned from M6)

M4 has shipped. The following items are **delivered by M4** and removed from M6 scope as duplicates. M6 imports these modules directly.

| M4-shipped module | M6 usage |
|---|---|
| `v5/bar_spec.py::BarSpec(resolution_minutes, label_side, closed_side, anchor_utc)` | `DataStream.bar_spec: BarSpec` — used as-is. (D1) |
| `v5/data_resampler.py::DataResampler` | Backtest aggregation path in `DataEngine` routing cascade. |
| `v5/streaming_consolidator.py::StreamingConsolidator` | Paper/live aggregation path in `DataEngine` routing cascade. |
| `v5/rolling_cache.py::RollingCache(bar_spec, lookback)` | Composed inside `v5/data/cache.py::MultiInstrumentCache`. (D3) |
| `v5/rolling_cache.py::BarType` (legacy namespace class w/ `ONE_MIN`, etc.) | Untouched. M6's routing key renamed to `DataStream`. (D2) |
| `v5/testing.py::TestClock` | Imported by M6 tests. `v5/clock.py` only adds `Clock` Protocol + `LiveClock`. |

**Dependency**: M6 now **depends on** M4 (not the other way around). M4 shipped first.

---

## M5 Impact (SHIPPED — compatibility preserved)

M5 has shipped the canonical order/leg lifecycle. M6 does not redefine order types; `DataEngine` / `paper_engine.py` migration must remain compatible with M5's public surface:

| M5-shipped module | M6 constraint |
|---|---|
| `v5/orders.py` (`Order`, `Leg`, `OrderStatus`, `TriggerType`, `LegStatus`, `LegFillPolicy`, `ContingencyType`, `TimeInForce`) | Any fill events derived from bar data must flow through M5's order lifecycle — M6 does not invent a parallel order type. |
| `v5/dust_handler.py` | M6's `Instrument` metadata feeds the dust thresholds; M6 does not re-implement dust logic. |
| `v5/orders_log.py`, `v5/backtest.py` | Backtest loop integration point for M6's `ParquetReplayClient` → `DataEngine` → strategy → `Order` path. |

**No duplicate scope.** M6 does not touch order lifecycle; it only ensures the data pipeline feeds it cleanly.
