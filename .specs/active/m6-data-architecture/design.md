# M6 Design — v5/data/ Package

**Phase**: 2 (Design). Read-only exploration + interface crystallisation. No source files will be created here; only the design artefact.

**Inputs**: `brief.md` (976 lines, Phase 1 approved, 20 ACs D1-D20). Phase-2 read-only codebase audits of `v5/paper_engine.py` (4557 LOC), `v5/run_paper_multi.py` (1103 LOC), `v4/price_monitor.py` (831 LOC), `v4/live_fetcher.py` (606 LOC), `v5/simulator.py` (2850 LOC), `v4/universe.py`, `v4/data_loader.py`.

**Scope anchor**: Brief's 20 ACs are the contract. This design specifies module layout, concrete APIs (signatures, return types, import graph), the migration plan for the 8 paper_engine sites, implementation wave ordering, and test infrastructure. **No AC is added, narrowed, or widened here** — changes to ACs are Phase-1 territory and would require a brief revision.

---

## 1. Module Layout

```
v5/data/
├── __init__.py                    # Package root. FIX vocabulary block docstring (AC-D16). Re-exports.
├── engine.py                      # DataEngine orchestrator
├── bus.py                         # MessageBus + SubscriptionHandle
├── streams.py                     # DataStream, Subscription (NamedTuple), GapPolicy, DataKind, Venue, TransportMode
├── cache.py                       # MultiInstrumentCache (wraps M4 RollingCache, 3-tuple key)
├── gaps.py                        # GapDetector + DataGapError + GapDetected event
├── types.py                       # FundingRate, MarkPrice, Trade (non-bar event types)
├── instruments.py                 # Instrument, InstrumentRegistry, VenueCapabilities
├── registry.py                    # DataClientRegistry (venue → factory lookup, priority-sorted)
├── protocols.py                   # DataClient Protocol, LiveDataClient Protocol
├── exceptions.py                  # DataGapError, RateLimitExceeded, PriceTypeNotSupported, SymbolNotFound, ClockDriftHigh
└── clients/
    ├── __init__.py
    ├── binance_ws.py              # BinanceWSClient (LiveDataClient). 1m+1h+aggTrades multiplexed. Shards at 200 streams.
    ├── binance_rest.py            # BinanceRESTClient (LiveDataClient). Weight tracker + time-drift sync.
    └── parquet_replay.py          # ParquetReplayClient (DataClient only). REPLAY + PULL_ONCE.

v5/
├── clock.py                       # NEW: Clock Protocol + LiveClock. TestClock stays in v5/testing.py (M4-shipped).
├── paper_engine.py                # MODIFIED: feature flag + DataEngine wiring (8 migration sites).
└── run_paper_multi.py             # MODIFIED: shared DataEngine fan-out (replaces PriceMonitor fan-out at 381-401).

v5/tests/
├── test_m6_cache.py               # T-D1 / T-D1a / T-D1b
├── test_m6_subscription.py        # T-D2 / T-D18 / T-D14 validation
├── test_m6_protocols.py           # T-D3 / T-D7 / T-D15
├── test_m6_routing.py             # T-D4 / T-D17
├── test_m6_gaps.py                # T-D5 / T-D5a / T-D5b
├── test_m6_clock.py               # T-D6
├── test_m6_paper_migration.py     # T-D8 + shadow-replay harness glue
├── test_m6_rate_limit.py          # T-D9
├── test_m6_time_drift.py          # T-D10
├── test_m6_instruments.py         # T-D11
├── test_m6_deferred_types.py      # T-D12
├── test_m6_trade_events.py        # T-D13
├── test_m6_backtest_parity.py     # flag=ON backtest parity against M5 reference fixtures (new — §5)
├── test_m6_fix_docstrings.py      # T-D16 (grep test)
├── test_m6_bus_determinism.py     # T-D20 (per AC-D19: grep for set() iteration in hot path + FIFO order assertion)
├── test_m6_infra_clock_docs.py    # T-D21 (per AC-D20: grep for "Infrastructure wall-clock reads:" section)
└── shadow_replay_harness.py       # T-D19 blocking test driver (per Phase-3 Test Plan line 888 of brief)

v5/tests/fixtures/shadow_replay_24h/
├── binance_ws_tap_YYYYMMDD.jsonl.zst      # 24h WS recording (committed via LFS or sidecar)
├── binance_rest_tap_YYYYMMDD.jsonl.zst    # REST backfill calls mirrored
└── shadow_replay_report_template.md
```

**Sizing expectations (rough LOC targets, non-binding)**:
- `engine.py` ≤ 450 LOC (orchestrator — minimal logic; delegates everything)
- `bus.py` ≤ 120 LOC
- `streams.py` ≤ 250 LOC (enums + NamedTuple + validators)
- `cache.py` ≤ 150 LOC (thin wrapper — M4 RollingCache does the work)
- `gaps.py` ≤ 200 LOC
- `clients/binance_ws.py` ≤ 600 LOC (831 LOC v4 PriceMonitor shrinks when WS-only concerns are isolated)
- `clients/binance_rest.py` ≤ 350 LOC (606 LOC v4 LiveFetcher shrinks when REST-only concerns are isolated)
- `clients/parquet_replay.py` ≤ 180 LOC

If any file blows past 1.5× the target, treat as a stop-and-redecompose signal.

---

## 2. Key Interfaces (signatures only — bodies in Phase 4)

Signatures below pin the Phase-4 contract. Anything not pinned here is an implementation detail subject to the "pattern over principle" meta-rule (follow nearest M4/M5 equivalent).

### 2.1 `v5/clock.py`

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Clock(Protocol):
    def now_ns(self) -> int: ...  # epoch nanoseconds

class LiveClock:
    """Wall clock for paper/live. Module docstring MUST contain
    'Infrastructure wall-clock reads:' section (AC-D20)."""
    def now_ns(self) -> int: ...  # time.time_ns()
```

**Naming choice** (per CLAUDE.md meta-rule #1 "code over specs"): `now_ns()` matches the shipped `v5/testing.py::TestClock.now_ns()` (verified at v5/testing.py:30). The earlier design draft used `now()` which would have broken Protocol conformance — corrected here. Nautilus's `timestamp_ns()` is an alternative name; we prefer `now_ns()` for minimal churn vs shipped TestClock.

**Order.armed_at in backtest/replay path**: when a Bar event triggers `Order.arm(...)` via the bus→strategy path, `armed_at` MUST be sourced from `bar.ts_event` (venue close time), NOT `clock.now_ns()`. `clock.now_ns()` is for freshness / timeout semantics; `ts_event` is the determinism anchor. Documented here so M6 paper engine and Phase-4 tests don't drift. (Raised by Round-2 FIX-M4.)

Nothing else. `TestClock` imports from `v5.testing` in tests.

### 2.2 `v5/data/streams.py`

Types: `DataKind`, `Venue`, `TransportMode`, `GapPolicy`, `InstrumentId`, `DataStream`, `Subscription`. All enum / frozen dataclass / NamedTuple shapes exactly as specified in brief §"Core Abstractions" (lines 163-268).

**FIX tag ownership carry-over (AC-D16)**: the brief's Core Abstractions docstrings place tags on specific types. §2 sketches below elide the docstring bodies for brevity, but Phase-4 implementers MUST preserve the tag assignments verbatim from the brief, in the named file:

| Type | File | Tags owned (in that type's docstring) |
|---|---|---|
| `DataKind` enum | `v5/data/streams.py` | `MDEntryType(269)` |
| `InstrumentId` | `v5/data/streams.py` | `SecurityID(48)`, `SecurityIDSource(22)`, `SecurityExchange(207)`, `Product(460)` |
| `Instrument` | `v5/data/instruments.py` | `MinPriceIncrement(969)`, `MinTradeVol(562)` |
| `Subscription` | `v5/data/streams.py` | `MarketDataRequest(V)`, `MDReqID(262)`, `SubscriptionRequestType(263)` |
| `DataStream` | `v5/data/streams.py` | `NoMDEntryTypes(267)` (noted as "plural-semantics aspirational — currently one `data_kind` per `DataStream`; tag retained for M10+ multi-kind subscriptions") |
| `TransportMode` | `v5/data/streams.py` | `MDUpdateType(265)` |
| `Trade` event type | `v5/data/types.py` | `Side(54)` (aggressor side) + `MDEntryType(269)=2 Trade` |
| `Bar` / `ts_event` field | `v5/data/types.py` (comment on shipped M4 Bar via module docstring) | `TransactTime(60)` |

Plus the 14-tag master block in `v5/data/__init__.py` module docstring per brief §"FIX vocabulary block" (lines 124-158). T-D16 verifies ≥1 occurrence per tag anywhere in `v5/data/`; the table above specifies *which* file/type for coherent authorship.

**Subscription construction validator** (per AC-D18):

```python
def _validate_subscription(
    stream: DataStream,
    handler: Callable[[Any], None],
    warmup: int,
    role: Literal["signal","entry","exit"],
    gap_policy: GapPolicy,
    poll_interval_s: int | None,
    transport_preference: Literal["WS","REST","AUTO"],
    fallback_allowed: bool,
    lookback_days_override: int | None,
) -> None:
    # gap_policy != STRICT requires BAR
    if gap_policy != GapPolicy.STRICT and stream.data_kind != DataKind.BAR:
        raise ValueError(f"gap_policy={gap_policy} requires data_kind=BAR, got {stream.data_kind}")
    # poll_interval_s required iff FUNDING_RATE/MARK_PRICE
    if stream.data_kind in {DataKind.FUNDING_RATE, DataKind.MARK_PRICE}:
        if poll_interval_s is None:
            raise ValueError(f"{stream.data_kind} requires poll_interval_s")
    if stream.data_kind in {DataKind.BAR, DataKind.TRADE}:
        if poll_interval_s is not None:
            raise ValueError(f"{stream.data_kind} requires poll_interval_s=None")
    # warmup must be non-negative
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    # role must be one of signal/entry/exit (enum-like Literal)
    if role not in ("signal", "entry", "exit"):
        raise ValueError(f"role must be one of signal|entry|exit, got {role!r}")
    # lookback_days_override: None or positive
    if lookback_days_override is not None and lookback_days_override <= 0:
        raise ValueError("lookback_days_override must be > 0 when provided")

class Subscription(NamedTuple):
    stream: DataStream
    handler: Callable[[Any], None]
    warmup: int = 500
    role: Literal["signal","entry","exit"] = "signal"
    gap_policy: GapPolicy = GapPolicy.STRICT
    poll_interval_s: int | None = None
    transport_preference: Literal["WS","REST","AUTO"] = "AUTO"
    fallback_allowed: bool = True
    lookback_days_override: int | None = None

    def __new__(
        cls,
        stream: DataStream,
        handler: Callable[[Any], None],
        warmup: int = 500,
        role: Literal["signal","entry","exit"] = "signal",
        gap_policy: GapPolicy = GapPolicy.STRICT,
        poll_interval_s: int | None = None,
        transport_preference: Literal["WS","REST","AUTO"] = "AUTO",
        fallback_allowed: bool = True,
        lookback_days_override: int | None = None,
    ):
        _validate_subscription(stream, handler, warmup, role, gap_policy,
                               poll_interval_s, transport_preference,
                               fallback_allowed, lookback_days_override)
        return super().__new__(cls, stream, handler, warmup, role, gap_policy,
                               poll_interval_s, transport_preference, fallback_allowed,
                               lookback_days_override)
```

The price-type vs venue-capabilities check (AC-D18 / C3) is NOT in `__new__` (the venue capabilities aren't known until DataEngine.start binds the registry). It runs inside `DataEngine.subscribe(...)` after capabilities are resolved — raising `PriceTypeNotSupported(venue, price_type)` at that point.

### 2.3 `v5/data/bus.py`

```python
@dataclass(frozen=True, slots=True)
class SubscriptionHandle:
    """Bus-internal handle (INT) — NOT a FIX MDReqID(262).

    FIX mapping note: MDReqID is a string that survives venue round-trip
    (used for MarketDataRequestReject echo, cancel-by-ID). `_id: int` is
    intentionally bus-internal only — the venue-facing identifier (when/if
    surfaced to a real FIX gateway in M11+) will be a distinct string
    derived from this handle at the session boundary, not the handle itself.
    (Round-2 FIX-M2.)
    """
    _id: int

class MessageBus:
    """Pub/sub with FIFO deterministic dispatch (AC-D19).

    Internal storage MUST be dict[topic, dict[SubscriptionHandle, handler]] — Python 3.7+
    dict preserves insertion order; no set iteration on hot paths.
    """
    _subs: dict[DataStream, dict[SubscriptionHandle, Callable[[Any], None]]]
    _next_id: int

    def publish(self, topic: DataStream, event: Any) -> None:
        for handle, handler in list(self._subs.get(topic, {}).items()):
            try:
                handler(event)
            except Exception as e:
                log.error("MessageBus handler raised", exc_info=e, topic=topic, handle=handle._id)

    def subscribe(self, topic: DataStream, handler: Callable[[Any], None]) -> SubscriptionHandle:
        ...

    def unsubscribe(self, handle: SubscriptionHandle) -> None: ...
```

`publish` iterates a **snapshot** (list copy) so unsubscribe inside a handler is safe (re-entrancy). Per-handler try/except keeps dispatch deterministic (one bad handler can't starve siblings).

### 2.4 `v5/data/cache.py`

Keyed by `(InstrumentId, BarSpec, role)` 3-tuple per D3. Signatures from brief §"MultiInstrumentCache" (lines 366-393) adopted verbatim.

**Memory projection — reuse of M4's shipped arithmetic (corrected from Round-1)**: the Round-1 draft proposed adding a new instance method `RollingCache.projected_memory_mb()`. Verification of v5/rolling_cache.py:425 shows M4 already ships `compute_projected_memory_mb(strategies, tokens) -> float` as a module-level function using shipped constants `_FIELDS_PER_BAR=6`, `_BYTES_PER_CELL=4`, and `maxlen_for_bar_spec(spec, role)`. M6 does NOT duplicate this math. Instead, `MultiInstrumentCache.project_from_subscriptions(subs)` uses the same constants directly:

```python
from v5.rolling_cache import maxlen_for_bar_spec, _FIELDS_PER_BAR, _BYTES_PER_CELL

class MultiInstrumentCache:
    ...
    def project_from_subscriptions(self, subs: list[Subscription]) -> float:
        """Aggregate projected memory (MB) across the unique (instrument, spec, role)
        tuples that WOULD be created for this subscription set. Used by DataEngine.start()
        to enforce AC-D1b BEFORE any data flows. Shares constants with M4's
        compute_projected_memory_mb() so the two agree by construction.
        """
        unique_tuples: set[tuple[InstrumentId, BarSpec, str]] = {
            (s.stream.instrument, s.stream.bar_spec, s.role)
            for s in subs if s.stream.data_kind == DataKind.BAR
        }
        total_bytes = sum(
            maxlen_for_bar_spec(spec, role) * _FIELDS_PER_BAR * _BYTES_PER_CELL
            for (_inst, spec, role) in unique_tuples
        )
        return total_bytes / (1024 * 1024)
```

Note the per-instrument dimension: each `(inst, spec, role)` is ONE cache, so no `n_tokens` multiplier — it's already implicit in the tuple cardinality. M4's module function has `n_tokens` because its strategy-level subscription model is per-role-not-per-instrument. Translation: `M4_function(strategies, tokens) ≈ sum over strategies.bar_subscriptions × len(tokens)` which equals the cardinality of unique `(inst, spec, role)` tuples when enumerated per strategy/token. The formulas are equivalent for identical fleets; they differ only in calling convention.

`DataEngine.start()` calls `project_from_subscriptions(unioned_subs)` and raises `MemoryError` if > 1200 MB (mirrors M4's `assert_memory_budget` at v5/rolling_cache.py:451). `compute_projected_memory_mb()` on `MultiInstrumentCache` remains the live-instantiated sum for runtime observability; the two MUST agree once all subscriptions have received ≥1 bar.

**AC-D1b budget scope clarification (from Round-2 Quant-M1)**: AC-D1b's 1200 MB budget is **cache-only** (`MultiInstrumentCache` + its underlying `RollingCache` instances). Non-cache consumers are tiny but explicitly called out here so nobody spends review cycles guessing:
- `GapDetector._last_ts / _seq_counter / _policy` — dict of ~500 entries (one per declared subscription) × small primitives = < 100 KB total
- `MessageBus._subs` — dict-of-dicts indexed by (DataStream, SubscriptionHandle) — < 1 MB for the fleet
- WS merge-window dedup buffer — bounded by 30s × 472 streams × 1m bars ≈ 472 bars = < 1 MB
- WS connection ring buffers — bounded by Binance WS frame rate × 3-4 shards = < 10 MB

All four are negligible versus the 1200 MB cache budget. If `DataEngine` gets a new consumer that moves the needle (e.g., an orderbook ring buffer in M10), add it to `DataEngine.projected_memory_mb()` explicitly then.

**Blast-radius note**: `v5/rolling_cache.py` is M4-shipped and stays untouched — no new instance method, no refactor. M6 imports the module-level constants + `maxlen_for_bar_spec` helper, which is additive-by-consumer. If M4's constants need to become public (e.g., leading underscore removed), that's a trivial M4-adjacent rename — additive, zero downstream churn.

### 2.5 `v5/data/gaps.py`

```python
class GapDetector:
    """Upstream of MultiInstrumentCache. Guarantees monotonicity before cache write.

    FIX mapping note (AC-D16 honesty): GapPolicy → approximate. FIX MDUpdateType(265)
    has no direct gap-handling analog; Bar.ts_event → TransactTime(60) remains the
    canonical per-bar timestamp. STRICT/NAN_FILL/SKIP are engine semantics, not FIX
    semantics — documented per brief §"FIX vocabulary block" line 143.
    """
    _last_ts: dict[DataStream, int]      # per-stream monotonicity state
    _seq_counter: dict[DataStream, int]  # per-stream sequence counter
    _policy: dict[DataStream, GapPolicy] # per-stream policy from Subscription

    def on_bar(self, stream: DataStream, bar: Bar) -> list[Bar]:
        """Returns list of bars to forward to the cache (may be [], [bar], or [nan_fill, ..., bar])."""

    def _handle_gap(self, stream: DataStream, expected_ts: int, got_ts: int) -> list[Bar]:
        """Policy dispatch: STRICT → REST backfill N times then raise DataGapError;
        NAN_FILL → emit NaN bars at missing slots; SKIP → log + drop."""

class DataGapError(Exception):
    """Raised when GapPolicy.STRICT + REST backfill exhausted."""

@dataclass(frozen=True, slots=True)
class GapDetected:
    stream: DataStream
    expected_ts: int
    got_ts: int
    missing_count: int
```

### 2.6 `v5/data/protocols.py`

Shapes from brief §"DataClient Protocol" (lines 302-338) adopted verbatim. Note `LiveDataClient` subclasses `DataClient` (PEP 544 Protocol inheritance).

Both `DataClient` and `LiveDataClient` MUST be decorated with `@typing.runtime_checkable` so T-D15 can use `isinstance(client, LiveDataClient)` to exercise the unsupported-op paths (per Round-2 Realism-M6).

**FIX session-type analogy**: the `DataClient` / `LiveDataClient` split mirrors FIX session-type separation — a historical-only source (TSDB replay) consumes `SubscriptionRequestType(263) = 0 Snapshot` (one-shot `request` / `replay`), while a live venue session additionally handles `SubscriptionRequestType(263) = 1 Snapshot+Updates` (`subscribe` for PUSH, `subscribe_scheduled` for cron-driven `FullRefresh`).

### 2.7 `v5/data/registry.py`

```python
class DataClientRegistry:
    _factories: dict[Venue, list[Callable[[Config], DataClient]]]  # insertion order preserved
    _cached: dict[Venue, list[DataClient]]                          # lazy-instantiated singletons
    _priority: dict[TransportMode, int] = {
        TransportMode.PUSH: 0,
        TransportMode.PULL_ONCE: 1,
        TransportMode.PULL_SCHEDULED: 2,
        TransportMode.REPLAY: 3,
    }

    def register(self, venue: Venue, factory: Callable[[Config], DataClient]) -> None: ...
    def get_clients(self, instrument: InstrumentId) -> list[DataClient]:
        """Returns clients for instrument.venue, sorted by PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY
        using min(supported_modes) → priority index as the sort key.
        """
    def registered_venues(self) -> frozenset[Venue]: ...
```

**TransportMode → FIX MDUpdateType(265) clarification**: FIX MDUpdateType has only two values (0=FullRefresh, 1=IncrementalRefresh). The four engine transport modes map as:
- `PUSH` → `IncrementalRefresh` (long-lived session, server-initiated deltas)
- `PULL_ONCE` → `FullRefresh` (caller-initiated snapshot)
- `PULL_SCHEDULED` → `FullRefresh` on engine-driven cadence (semantically `SubscriptionRequestType(263)=1 Snapshot+Updates` where the "Updates" is the engine's cron loop, not an inline WS stream)
- `REPLAY` → no direct FIX analog (offline deterministic parquet playback)

The priority ordering `PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY` prefers the lowest-latency transport a client supports. Live venues typically offer all three live modes; replay-only clients declare `{REPLAY, PULL_ONCE}` only.

### 2.8 `v5/data/engine.py`

The orchestrator. Everything else is a collaborator. Signature shape:

```python
class DataEngine:
    use_data_engine_flag: bool = False  # global kill-switch (AC-D12)

    def __init__(
        self,
        registry: DataClientRegistry,
        clock: Clock,
        instrument_registry: InstrumentRegistry,
        bus: MessageBus | None = None,        # default: construct a fresh MessageBus
        cache: MultiInstrumentCache | None = None,
    ): ...

    def start(self) -> None:
        """Instantiate registered factories. Call DataClient.connect() on each.
        Populate InstrumentRegistry._metadata / _capabilities from each venue's connect.
        Enforce AC-D1b aggregate memory budget against the declared subscription set
        (via MultiInstrumentCache.project_from_subscriptions)."""

    def stop(self) -> None: ...

    def subscribe(self, subscription: Subscription) -> SubscriptionHandle:
        """Routing cascade (reordered per reviewer M2 — registry-first so missing-venue
        subscriptions fail with AC-D17's explicit error, not a capability lookup):
        1. clients = registry.get_clients(stream.instrument) — priority-sorted
           If empty: raise RuntimeError("no client registered for venue {venue}")  # AC-D17
        2. Validate price_type: stream.price_type in registry.capabilities(venue).supported_price_types
           Else: raise PriceTypeNotSupported(venue, price_type)  # AC-D18 C3
        3. mode = _resolve_mode(subscription, clients)   # subscription, NOT just preference — see below
        4. For first client where client.supports(stream, mode) is True:
             - BAR: wire through GapDetector → MultiInstrumentCache → bus.publish(stream, bar)
             - TRADE/FUNDING_RATE/MARK_PRICE: direct bus.publish (bypass cache)
             - INSTRUMENT_INFO: one-shot fetch via client.request(...)
           Register subscription.handler on bus for subscription.stream.
        5. If no client supports, try M4 aggregation (DataResampler/StreamingConsolidator) from
           a smaller-spec client; emit source='INTERNAL' on bus.
        6. If still unsupported, raise RuntimeError with a clear message.
        """

    def unsubscribe(self, handle: SubscriptionHandle) -> None: ...

    def request(self, stream: DataStream, start_ns: int, end_ns: int) -> list:
        """Public accessor for one-shot PULL_ONCE requests (addresses reviewer L8).
        Used by paper_engine warm-up (Migration site #5). Walks the registry priority
        list and delegates to the first client whose supported_modes includes PULL_ONCE.
        """

    def _resolve_mode(
        self,
        subscription: Subscription,           # full subscription, so fallback_allowed is visible
        clients: list[DataClient],
    ) -> TransportMode:
        """
        Picks TransportMode using:
          - subscription.transport_preference  ('WS' / 'REST' / 'AUTO')
          - client.supported_modes
          - subscription.fallback_allowed — if False, MUST NOT silently downgrade
            WS → REST on drop (AC-D15 + reviewer M4 fix).

        AUTO: first-supported by registry priority (PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY).
        WS:   only PUSH-capable client; if fallback_allowed=True and PUSH client drops,
              re-resolve to PULL_SCHEDULED at dispatch time; if fallback_allowed=False,
              enter degraded state on bus, no rebind.
        REST: only PULL_ONCE or PULL_SCHEDULED capable client.
        """
```

`DataEngine.subscribe_all(subs: list[Subscription]) -> list[SubscriptionHandle]` is a convenience batch call. **Dedup key** (Round-2 Quant-M4): `(DataStream, role)` — NOT `DataStream` alone. The 2-tuple (stream, role) is one underlying `RollingCache` per D3; dedup by stream alone would collapse `role="signal"` and `role="entry"` subscriptions onto one cache with the wrong lookback. Per-(stream, role) dedup means multiple handlers across strategies on the same cache tuple share one client subscription and one cache instance; distinct roles fan out to distinct caches. Memory projection still runs once per batch via `MultiInstrumentCache.project_from_subscriptions(subs)` against the unioned subscription set — AC-D1b checked at startup, not per-subscribe.

---

## 3. Data Flow Diagrams (text)

### 3.1 Backtest path (M6 enabled)

```
configs/backtest.json
        │
        ▼
ParquetReplayClient.replay(stream, start, end)
        │  yields Bar instances (chronologically sorted)
        ▼
DataEngine.subscribe(stream)  ─►  GapDetector (STRICT by default)
                                         │
                                         ▼
                                MultiInstrumentCache.on_bar(bar, role)
                                         │
                                         ▼  MessageBus.publish(stream, bar)
                                         │
                  ┌──────────────────────┼──────────────────────┐
                  ▼                      ▼                      ▼
         strategy.on_1h_bar     strategy.on_5m_bar     strategy.on_1m_bar
```

For non-BAR streams (TRADE / FUNDING_RATE / MARK_PRICE):

```
ParquetReplayClient.replay(trade_stream, ...)
        │
        ▼
DataEngine dispatches: bypass cache, bus.publish directly
        │
        ▼
strategy.on_trade(trade)
```

**Backtest entry-point migration** (Phase-2 audit correction): `v5/simulator.py::run_backtest_mtf` imports `from .signals import TokenSignals` at v5/simulator.py:29 — the current path is **`v5/simulator.py` → `v5/signals.py::TokenSignals` → `v5/data_loader.py`** (all v5, NOT v4 — an earlier audit note drafted v4/ paths and was corrected post-verification). M6 does NOT rip this out; it adds `ParquetReplayClient` as an **alternative** loader. `v5/backtest.py` (M5-shipped facade) grows a `use_data_engine: bool = False` field on `BacktestConfig` (consistent with `PaperConfig.use_data_engine` — reviewer M5 symmetry fix) so the toggle mechanism matches across paper and backtest. Setting True routes through `DataEngine` + `ParquetReplayClient`; default False retains M5's current path byte-identical.

This follows the same feature-flag pattern as AC-D12 for paper, and the same hard-parity gate applies: shadow replay must show bit-identical bar arrays before flipping.

### 3.2 Paper / live path (M6 enabled)

```
Strategy.required_data() → list[Subscription]
        │
        ▼
DataEngine.subscribe_all(subs) — dedupes by DataStream
        │
        ▼
DataClientRegistry.get_clients(InstrumentId) → priority-sorted [WS, REST]
        │
        ▼
BinanceWSClient.subscribe(stream):
   1. BinanceRESTClient.request(stream, end - warmup*step, end) → seed cache
   2. WS.subscribe_kline(symbol, interval) → live Bar → GapDetector → MultiInstrumentCache → bus
   3. WS disconnect → PULL_SCHEDULED fallback (BinanceRESTClient.subscribe_scheduled)
   4. WS reconnect → REST gap-fill then merge window (30s) → dedup by ts_event → resume PUSH
        │
        ▼
MessageBus.publish(stream, bar) — per-handler dispatch
        │
        ▼
strategy.on_bar(bar)
```

Non-bar streams:
- `FUNDING_RATE`: `BinanceRESTClient.subscribe_scheduled(stream, interval_s=480)` — cron poll.
- `MARK_PRICE`: `BinanceWSClient.subscribe(stream)` — markPriceUpdate stream over WS.
- `TRADE`: `BinanceWSClient.subscribe(stream)` — aggTrade stream over WS.

### 3.3 Gap-detection state machine

```
on_bar(stream, bar):
    expected = last_ts[stream] + bar_spec.resolution_minutes * 60_000_000_000
    if bar.ts_event == expected:           → forward, update last_ts
    elif bar.ts_event <  expected:         → reject (duplicate/stale), log, no forward
    elif bar.ts_event == last_ts[stream]:  → reject (duplicate), log, no forward
    elif bar.ts_event >  expected:         → gap detected:
           emit GapDetected(expected, bar.ts_event, missing_count) on bus
           policy = _policy[stream]
           if STRICT:
               for retry in 1..N:  # N=3
                   rest_bars = REST.request(stream, expected, bar.ts_event)
                   if rest_bars fill gap: forward rest_bars + bar; break
               else: raise DataGapError(stream, expected, bar.ts_event)
           elif NAN_FILL:
               forward NaN-OHLCV bars at missing slots, then bar
           elif SKIP:
               log WARN, forward only bar
```

---

## 4. Paper Engine Migration Plan (8 sites)

Source: Phase-2 audit of `v5/paper_engine.py` (4557 LOC) + `v5/run_paper_multi.py` (1103 LOC). All 8 sites confirmed by grep; line numbers current as of 2026-04-19.

| # | Site | Current behavior (legacy) | M6 target (flag=ON) |
|---|------|---------------------------|---------------------|
| 1 | `paper_engine.py:859-912` — `__init__(price_monitor=...)` | Shared or own-PriceMonitor + own CandleAggregator. Hardcoded Binance WS. | Accept optional `data_engine: DataEngine | None`. If None and flag=OFF, old path. If flag=ON, construct a fresh `DataEngine`. |
| 2 | `paper_engine.py:893-911` — CandleAggregator creation | Own CandleAggregator on finest resolution. | Delete when flag=ON — `StreamingConsolidator` (M4-shipped) handles any resolution via `DataEngine` routing. CandleAggregator remains reachable for flag=OFF until M9 cleanup. |
| 3 | `paper_engine.py:1245-1248` — shutdown | `self._price_monitor.disconnect()` if owned | Branch: flag=OFF → disconnect PriceMonitor; flag=ON → `self._data_engine.stop()`. |
| 4 | `paper_engine.py:2046-2068` — `update_subscriptions(open_tokens)` | `self._price_monitor.update_subscriptions(open_tokens)` + `self._candle_aggregator.update_tokens(open_tokens)` | flag=ON: recompute `Subscription` set from open tokens × strategy's `required_data()`; call `self._data_engine.subscribe_all(new_subs)` + unsubscribe stale handles. |
| 5 | `paper_engine.py:3213-3217` — OHLCV live fetch | `LiveFetcher.fetch_recent(...)` for historical warm-up on new-token subscription | flag=ON: `self._data_engine.request(stream, start_ns, end_ns)` — public accessor on the orchestrator; no reaching into private `_registry`. |
| 6 | `paper_engine.py:3225-3229` — funding rate fetch | LiveFetcher funding rate polling | flag=ON: `Subscription(stream=DataStream(inst, DataKind.FUNDING_RATE), handler=self._on_funding, poll_interval_s=480)`. Handler wires into existing funding-ceiling logic. |
| 7 | `paper_engine.py:3237-3252` — hourly funding settlement | Triggered off LiveFetcher poll | flag=ON: same handler, triggered off `FundingRate` event on bus. Behavior identical; only the event plumbing changes. |
| 8 | `run_paper_multi.py:360-363` + `:381-401` — shared PriceMonitor fan-out | Runner creates ONE PriceMonitor, passes it to N PaperEngine instances. Sub-hourly candle flush at :360 triggers aggregator flush on all engines. | flag=ON: runner creates ONE `DataEngine`, passes to N PaperEngine instances via `data_engine=` kwarg. Sub-hourly flush is owned by `StreamingConsolidator` per stream — no cross-engine coordination needed. |

### 4.1 Feature-flag mechanism

```python
class PaperConfig:
    use_data_engine: bool = False   # default OFF — safe first ship (AC-D12)

class PaperEngine:
    def __init__(self, config: PaperConfig, *,
                 price_monitor=None,
                 data_engine: "DataEngine | None" = None):
        self._use_data_engine = config.use_data_engine
        if self._use_data_engine:
            assert data_engine is not None or price_monitor is None, \
                "flag=ON requires data_engine and forbids shared price_monitor"
            self._data_engine = data_engine
            self._price_monitor = None
            self._candle_aggregator = None
        else:
            # legacy path unchanged
            self._price_monitor = price_monitor
            ...
```

Every one of the 8 sites above dispatches on `self._use_data_engine`. When flag=OFF, code is byte-identical to pre-M6; when flag=ON, the new path is exercised.

### 4.2 Shadow replay gate (AC-D12)

Hard merge gate per brief §"Shadow Replay Ops Runbook":

1. Phase-4 one-shot: record 24h of Binance WS + REST traffic via a **standalone** script `v5/tools/record_ws_tap.py` that connects directly to `wss://stream.binance.com` / `wss://fstream.binance.com` and writes frames to `v5/tests/fixtures/shadow_replay_24h/*.jsonl.zst`. **The tap is NOT `paper_runner` — it does NOT touch `state/v4_paper_multi/paper.pid`, does NOT call `start_all_services.sh`, does NOT share config with the live paper runner.** It runs in a separate process and reads venue WS over a fresh TCP socket. Paper runner at PID 306094 keeps running untouched during fixture acquisition (Round-2 Realism-H1).
2. `v5/tests/shadow_replay_harness.py` feeds recording into two `PaperEngine` instances — one with flag=OFF, one with flag=ON — against the same `TestClock`.
3. Bar divergence: 0 tolerance (byte-identical OHLCV per (token, ts_event)).
4. Funding/Mark/Trade: 1 bps float tolerance + ≤1 trade-count/sec delta.
5. **Multi-shard reconnect scenario** (Round-2 FIX-M3): the harness MUST include a synthetic reconnect-storm test where ≥2 WS shards drop simultaneously mid-replay. Dedup key MUST be `(DataStream, ts_event)` — not `(shard_id, ts_event)` — so overlapping re-deliveries from the merge window across shards collapse correctly. Assert: 0 OHLCV divergence during the reconnect storm window.
6. Any divergence = HARD FAIL, block merge. Report generated at `v5/tests/fixtures/shadow_replay_report_YYYYMMDD.md`.

**Live-paper runner is v4 today** (Round-2 Realism-H1): per `memory/reference_service_management.md`, the PID-owning runner is `v4.run_paper_multi`. M6's `PaperConfig.use_data_engine` flag only engages once the live paper path moves to v5 in M7 (per brief M6→M7 carryover). For M6 shadow replay, the "legacy path" reference inside the harness is **v4's PriceMonitor+LiveFetcher replayed through `v5.paper_engine` in v4-compat mode** (which it already supports — `v5/paper_engine.py:890-912`). The v5 vs v4 runner switch is an M7 concern; M6 validates the architecture, not the runner swap.

**Stop-trigger**: if divergence on any live strategy, stop. Options: defer paper migration to M6.5, or wrap `v4/price_monitor.py` behind the `DataClient` Protocol as a bridging shim (preserves battle-hardening at the cost of 831 legacy LOC).

### 4.3 AC-D6 AST scan — scope decision

Brief §"Clock-read prohibition" lines 484-485 says direct `time.time()` / `datetime.now()` in strategy code "raises at strategy `__init__` time (enforced via an AST scan in the strategy loader)". The strategy loader today lives at `v5/engine.py::_load_strategy_fn`. **M6 does NOT add the AST scan** (Round-2 Realism-H3). Rationale: the strategy loader is part of the strategy API surface, which M7 redesigns. Adding an AST scan now would wire into a loader M7 will rewrite — churn for no benefit.

M6's scope for AC-D6: ship `Clock` Protocol + `LiveClock` + the documented prohibition text in docstrings. **The AST scan enforcement is deferred to M7** and recorded in M7's brief under "M6 Impact" (already documented per carryover discipline — see §M7 Impact section at end of this doc). T-D6 covers the Clock-abstraction behavioural contract (TestClock advances deterministically; strategy code that uses `clock.now_ns()` is clean); enforcement-via-AST is T-deferred.

This does NOT weaken AC-D6 — the prohibition text is in place; the scan's absence is a known M7 task, not an M6 regression.

---

## 5. Backtest Migration Plan (lightweight)

From Phase-2 audit (corrected): backtest data path today is **`v5/simulator.run_backtest_mtf` → `v5/signals.py::TokenSignals` → `v5/data_loader.py` (parquet read)** — all v5 imports, `v5/simulator.py:29` confirms `from .signals import TokenSignals`.

M6's impact on backtest is **additive**:
- `ParquetReplayClient.replay(stream, start, end)` yields `Bar` instances from the same parquet files, globally sorted by `(ts_event, token)` with dedup.
- `BacktestConfig.use_data_engine: bool = False` (added field on the M5-shipped config — symmetric with `PaperConfig.use_data_engine`). Default False retains M5 path.
- `use_data_engine=True` routes through `DataEngine` + `ParquetReplayClient`, emitting `Bar` on bus → `strategy.on_bar`.

Both paths produce the same closed-trade arrays by AC-D8 parity (T-D8 covers flag=OFF; a new test `test_m6_backtest_parity.py` covers flag=ON against M5 reference fixtures).

**Why defer widespread backtest flip to M9/M10**: no strategy currently needs non-BAR events in backtest. Flipping `use_data_engine=True` as default in simulator requires rebuilding the vectorised numpy-array path via `MessageBus` dispatch — a latency hit the current fleet does not need. M6 ships the capability; M9+ flips when the strategy API motivates it.

---

## 6. Venue / Instrument Metadata Extraction (from Subagent 3 audit)

Current hot spots:
- `v4/price_monitor.py:34-41` — hardcoded Binance WS URLs (spot + perp).
- `v4/universe.py:147-156` — static `EXCHANGE_FEES` dict.
- 1000-prefix token handling (e.g., `1000SHIBUSDT`) via ad-hoc string ops in PriceMonitor + LiveFetcher.
- Symbol resolution: `_token_to_symbol("BTC") → "BTCUSDT"` logic duplicated in both classes.
- ~70% of REST/WS calls go through CCXT; 30% direct to Binance endpoints.

M6 extraction:

| Legacy location | M6 destination |
|---|---|
| Hardcoded WS URLs | `v5/data/clients/binance_ws.py::BINANCE_WS_SPOT_URL / BINANCE_WS_PERP_URL` constants + VenueCapabilities |
| `EXCHANGE_FEES` dict | `Instrument.maker_fee_bps / taker_fee_bps` — populated per-instrument at `connect()` from `exchangeInfo` |
| 1000-prefix token handling | `InstrumentRegistry._symbol_aliases: dict[str, str]` — e.g. `"SHIB" → "1000SHIBUSDT"`. Callers always pass canonical token; registry resolves. |
| `_token_to_symbol` | `InstrumentRegistry.resolve(symbol, venue, asset_class) → InstrumentId` — the only sanctioned construction path. |
| Static universe scan | `InstrumentRegistry.list_perp_universe(Venue.BINANCE)` — populated from `exchangeInfo` at connect. |

M6 does NOT touch CCXT usage in live paper today; it only isolates the direct-Binance code paths behind the `DataClient` Protocol. M11+ can add OKX/Bybit/Deribit via `DataClientRegistry.register(Venue.OKX, OKXRestClientFactory)` — zero engine changes.

---

## 7. Implementation Waves (Phase-4 ordering preview)

Non-binding ordering; tasks.md formalises this in Phase 3. Goal: each wave is independently testable (green tests before next wave starts), and the feature flag stays OFF until Wave F.

### Wave A — Primitives (tests: T-D14, T-D18, T-D19)
1. `v5/clock.py` (Clock Protocol + LiveClock).
2. `v5/data/__init__.py` (FIX vocabulary docstring — AC-D16).
3. `v5/data/streams.py` (DataKind, Venue, TransportMode, GapPolicy, InstrumentId, DataStream, Subscription + validator).
4. `v5/data/bus.py` (MessageBus + SubscriptionHandle).
5. `v5/data/exceptions.py` (DataGapError, RateLimitExceeded, PriceTypeNotSupported, SymbolNotFound, ClockDriftHigh).

### Wave B — Instrument layer (tests: T-D11)
1. `v5/data/instruments.py` (Instrument, VenueCapabilities, InstrumentRegistry skeleton).
2. `v5/data/registry.py` (DataClientRegistry with priority cascade).

### Wave C — Cache + gap detection (tests: T-D1, T-D1a, T-D1b, T-D5, T-D5a, T-D5b)
1. `v5/data/cache.py` (MultiInstrumentCache wrapping M4 RollingCache, 3-tuple key).
2. `v5/rolling_cache.py` additive change: add `projected_memory_mb()` method.
3. `v5/data/gaps.py` (GapDetector with 3-mode policy).

### Wave D — Protocol + clients (tests: T-D3, T-D7, T-D12, T-D13, T-D15, T-D17)
1. `v5/data/protocols.py` (DataClient / LiveDataClient Protocols).
2. `v5/data/clients/parquet_replay.py` (simplest — REPLAY + PULL_ONCE only).
3. `v5/data/clients/binance_rest.py` (extract from v4/live_fetcher.py; weight tracker + time-drift per T-D9, T-D10).
4. `v5/data/clients/binance_ws.py` (extract from v4/price_monitor.py; sharding + merge window + reconnect). **Stop-trigger at 18h per AC-D13.** Sharding observability (reviewer FIX L2): when `BinanceWSClient._connections` grows (≥ a new WS connection opened because existing shards hit the 200-stream cap), log `INFO "ws_shard_opened"` with the active shard count. Paper runner ops visibility.

### Wave E — Engine wiring (tests: T-D2, T-D4, T-D6)
1. `v5/data/engine.py` (DataEngine orchestrator).
2. Strategy `required_data()` Protocol + runner union logic (scaffold only — filled in by M7).

### Wave F — Paper migration + shadow replay (tests: T-D8, T-D19, T-D21)
1. PaperConfig + PaperEngine `use_data_engine` flag + 8-site dispatch.
2. `v5/run_paper_multi.py` DataEngine fan-out (documented but not activated until M7 runner-flip).
3. **Phase-3 RED fixture**: a **1-hour** proxy recording `v5/tests/fixtures/shadow_replay_1h_proxy/*.jsonl.zst` captured during Phase 3 (one-time sidecar run of `record_ws_tap.py` for 60 min). This gives Phase 3 a fixture that makes `test_m6_paper_migration.py` and `shadow_replay_harness.py` go RED deterministically — without depending on the 24h recording that hasn't happened yet. (Round-2 Quant-M2: Phase 3 TDD gate cannot wait for a Phase-4 fixture.)
4. **Phase-4 24h fixture**: the real 24h fixture `v5/tests/fixtures/shadow_replay_24h/*.jsonl.zst` is captured in Phase 4 Wave F step 1 via `v5/tools/record_ws_tap.py` standalone script (non-overlapping with paper runner — see §4.2). The hard-merge gate asserts against THIS fixture.
5. `v5/tests/shadow_replay_harness.py` — blocking test; runs against 1h proxy (green) in Phase 3, then promoted to 24h fixture (blocking) in Phase 4.
6. Documentation + AC-D20 module docstrings in clients.

Wave F blocks merge until the 24h shadow replay is green. If it isn't, invoke the stop trigger per AC-D12.

---

## 8. Test Strategy

Phase 3 tests are written by an **isolated subagent** (per `.claude/rules/subagent-patterns.md`), with Phase-2 design knowledge deliberately withheld. The subagent receives:
- `brief.md` (all 20 ACs)
- File paths where new code will live (from §1 above)
- `v5/tests/conftest.py` existing patterns
- The mocking patterns listed below

The subagent does NOT receive the specific API shapes from §2; those emerge from the tests' RED-state assertions.

### 8.1 Mocking patterns

| Collaborator | Mock strategy |
|---|---|
| `BinanceWSClient` in engine tests | In-memory fake that implements `LiveDataClient` — `subscribe` appends to an internal queue, handlers called synchronously from a `pump()` helper. |
| `BinanceRESTClient` in gap tests | In-memory fake returning canned bar lists for `request(stream, start, end)`. |
| `ParquetReplayClient` in backtest tests | Real impl + fixture parquet files in `v5/tests/fixtures/m6_replay/` (small, checked in). |
| `MessageBus` in strategy tests | Real impl (trivially deterministic). |
| `Clock` in backtest tests | `TestClock` (M4-shipped) with explicit `advance()` calls. |
| `InstrumentRegistry` populated state | Factory fixture in `conftest.py` — `make_registry_binance_perp()` — returns a registry with ~10 test instruments and `VenueCapabilities` pre-loaded. |

### 8.2 Fixtures

`v5/tests/fixtures/m6_replay/BTC_perp_1h_2026Q1.parquet` — 90 days of 1h bars, no gaps. Used by backtest parity tests.
`v5/tests/fixtures/m6_replay/BTC_perp_1m_with_gap.parquet` — 1h window with a deliberate gap at bar 30. Used by T-D5/D5a/D5b.
`v5/tests/fixtures/m6_replay/trades_agg_sample.parquet` — 1h of aggTrades. Used by T-D13.
`v5/tests/fixtures/shadow_replay_24h/` — 24h WS+REST tap, populated once in Wave F (large; LFS or zstd sidecar).

### 8.3 Determinism assertions

AC-D19 (T-D20) — bus determinism: the grep test in `test_m6_bus_determinism.py` scans `v5/data/bus.py` using stdlib `re.findall(r"for\s+\w+\s+in\s+(?:set|frozenset)\(", source_text)` against the `publish`/`subscribe`/`unsubscribe` function bodies (located by AST or plain text extraction) and asserts zero matches. No `subprocess` — portable across CI, sandbox, and local. A FIFO-order assertion subscribes three handlers and asserts each is called in registration order across N publishes.

AC-D16 (T-D16) — FIX tag coverage: `test_m6_fix_docstrings.py` iterates the 14 tags and uses `pathlib.Path("v5/data/").rglob("*.py")` + stdlib text search (or `re.search(re.escape(tag), path.read_text())`) asserting ≥1 hit per tag. Portable + fast.

AC-D20 (T-D21) — wall-clock carve-out docs: for each of `v5/data/clients/binance_ws.py`, `v5/data/clients/binance_rest.py`, `v5/data/clients/parquet_replay.py`, parse the module docstring via `ast.get_docstring(ast.parse(source))` and assert the literal string `"Infrastructure wall-clock reads:"` appears in it.

---

## 9. Blast-Radius Audit

Additive or low-risk changes:
- **New package `v5/data/`** — no existing callers; zero blast.
- **New module `v5/clock.py`** — no existing callers.
- **Import M4 constants from `v5/rolling_cache.py`** (`_FIELDS_PER_BAR`, `_BYTES_PER_CELL`, `maxlen_for_bar_spec`) — import-only, no M4 source change. Per §2.4 memory-projection-reuse, M6 does NOT add a new `RollingCache.projected_memory_mb()` method.
- **New kwarg on `PaperEngine.__init__(..., data_engine=None)`** — additive; defaults preserve all existing call sites.
- **New field on `PaperConfig.use_data_engine: bool = False`** — additive; defaults preserve behavior.

Medium-risk changes (gated behind flag):
- **8 dispatch branches in `v5/paper_engine.py`** — each `if self._use_data_engine:` branch. Flag=OFF retains byte-identical behavior; flag=ON exercises new path only when config explicitly enables it.
- **`v5/run_paper_multi.py:360-363, 381-401`** — shared PriceMonitor fan-out replaced with shared DataEngine fan-out under flag.

High-risk (out of scope, will not change):
- `v5/simulator.py` — 31 importers. M6 does NOT modify the core sim hot path. Backtest flag routing goes in `v5/backtest.py` (M5-shipped facade).
- `v5/signals.py` — 19 importers. Untouched by M6.
- `v5/config.py` — 39+ importers. Untouched by M6 beyond new PaperConfig field.

Rollback stance: set `use_data_engine=False` + restart, done. v4 PriceMonitor + LiveFetcher stay alive through M7 per brief §"Paper Migration Safeguards / 3. Rollback plan".

---

## 10. Risks & Stop Triggers

| Risk | Trigger | Response |
|---|---|---|
| BinanceWSClient extraction blows budget | >18h invested per AC-D13 | Wrap v4/price_monitor behind `DataClient` shim. 831 legacy LOC is the cost. **Shim sunset trigger (Round-2 Quant-M3)**: if the shim is still in place at end of M8, open an AIPIP to re-evaluate — a 3-milestone-old shim becomes v3-style freeze risk. Revisit strategies: finish the rewrite, or formally accept the shim through M10+ with explicit documentation of the freeze. |
| Shadow replay diverges | Any OHLCV mismatch over 24h per AC-D12 | STOP. Defer paper migration to M6.5, ship data architecture without flipping flag. |
| MultiInstrumentCache memory exceeds 1.2 GB | `compute_projected_memory_mb() > 1200` for live portfolio | Per-strategy `lookback_days_override` reduction. Stop if still over. |
| RollingCache `projected_memory_mb()` doesn't exist | Wave C discovery | Additive M4-adjacent change; log in commit, no AIPIP needed (additive). |
| Phase-4 task blows 1-2 file scope | Any task touches >2 files | Invoke stop-and-redecompose per `development-workflow.md`. |
| Interaction surface of 7 schema additions | Integration test failures late | Wave A's primitives land first + tested before Wave B starts. |
| Data loss during paper-migration testing | Accidental `git checkout -f` etc. | Follow CLAUDE.md §"Data Safety" — worktree pattern for any orphan branch work; `check_data_integrity.sh` before any destructive op. |

---

## 11. Open Questions (explicitly not for this design)

These are M7+ decisions; documented here so they're not re-surfaced mid-M6:

- How strategies consume `FundingRate` events (M7 Strategy API).
- Whether `DataEngine.subscribe_all` exposes progress/warmup callback (M7 Strategy API).
- OKX / Bybit / Deribit client implementations (M11+).
- `BookSnapshot` / `Liquidation` / `OpenInterest` (M10 microstructure milestone).
- Multi-venue portfolio routing (M11+).

---

## 12. Design Review Self-Check

- [x] Every AC D1-D20 has a named home in §2 (interface) and §7 (wave).
- [x] D1/D2/D3 architectural decisions carried verbatim from brief.
- [x] Paper migration 8 sites enumerated with line numbers from current source.
- [x] Backtest migration is additive + flag-gated (no change to current fleet's behaviour).
- [x] Blast radius audit maps each touched file to its risk tier.
- [x] Stop triggers + rollback plan explicit.
- [x] Test strategy pins mocks + fixtures before Phase 3 starts.
- [x] No new ACs introduced (design respects brief's scope).

---

## 13. Round-1 Review Resolutions

Two parallel independent reviews (quant architect + FIX/Nautilus expert) returned NEEDS_ATTENTION. All findings folded into this document. Summary:

| Sev | Origin | Finding | Resolution |
|---|---|---|---|
| HIGH | Quant | Test-ID drift — brief AC-D19 names `T-D20`, AC-D20 names `T-D21`, but Phase-3 Test Plan stopped at T-D19 | §1 test file allocation reconciled: `test_m6_bus_determinism.py` = T-D20, `test_m6_infra_clock_docs.py` = T-D21, `shadow_replay_harness.py` = T-D19 |
| HIGH | Quant | Backtest path claim `v5/simulator → v4/signals → v4/data_loader` contradicts code (v5/simulator.py:29 imports `.signals`) | §3.1 and §5 corrected: path is `v5/simulator → v5/signals → v5/data_loader`, verified via grep |
| MED | Quant | `_validate_subscription` signature missing `handler`, `lookback_days_override` args; `__new__` used `...` placeholder | §2.2 `Subscription.__new__` + `_validate_subscription` now have full typed signatures |
| MED | Quant | `_resolve_mode(preference, clients)` cannot read `fallback_allowed` to honor AC-D15 | §2.8 `_resolve_mode(subscription, clients)` — full subscription passed in |
| MED | Quant | Backtest kwarg vs Paper config field — asymmetric toggle mechanism | §5: `BacktestConfig.use_data_engine: bool = False` (field, symmetric with PaperConfig) |
| MED | Quant | `compute_projected_memory_mb()` returns 0 at startup (lazy RollingCache creation) — AC-D1b check useless before bars flow | §2.4: added `MultiInstrumentCache.project_from_subscriptions(subs) -> float` for eager projection; `DataEngine.start()` invokes it |
| MED | FIX | §2 interface sketches don't re-cite which type owns which FIX tag (tag→type legibility gap) | §2.2: added FIX tag ownership table mapping tags to types |
| MED | FIX | Subscribe cascade does price-type check before `registry.get_clients` — missing-venue subs fail obscurely | §2.8 cascade reordered: get_clients → capability check → mode resolve; missing venue raises AC-D17 error first |
| MED | FIX | PULL_SCHEDULED ↔ FIX MDUpdateType(265) mapping ambiguous | §2.7: added clarification paragraph — PULL_SCHEDULED = FullRefresh on engine cadence |
| MED | FIX | GapPolicy FIX-analog honesty not restated in `GapDetector` docstring | §2.5: GapDetector class docstring now explicitly notes "approximate mapping — no direct FIX analog" |
| LOW | Quant | `clients/base.py` listed without pinned interface | §1: removed from module tree |
| LOW | Quant | Site #5 reached into `self._data_engine._registry` (private attr) | §4 Site #5 + §2.8: `DataEngine.request(stream, start_ns, end_ns)` public accessor |
| LOW | Quant | `subprocess.grep` brittle across environments | §8.3: stdlib `re.findall` / `pathlib.rglob` / `ast.get_docstring` — no subprocess |
| LOW | FIX | `Subscription.__new__` used `...` placeholder | §2.2: full explicit signature |
| LOW | FIX | Sharding state not observable (new WS conn opens silently) | Wave D step 4: `INFO "ws_shard_opened"` log when `_connections` grows |
| LOW | FIX | DataClient vs LiveDataClient FIX session analogy not called out | §2.6: added "FIX session-type analogy" paragraph |

No HIGH findings remain. Ready for Phase-2 review gate.

---

## 14. Round-2 Review Resolutions

Three parallel independent reviewers (quant architect, FIX/Nautilus, implementation realism) returned NEEDS_ATTENTION after Round 1 fixes landed. All Round-2 findings folded in:

| Sev | Origin | Finding | Resolution |
|---|---|---|---|
| HIGH | Quant | `projected_memory_mb()` seam divergent from M4's shipped `compute_projected_memory_mb(strategies, tokens)` at v5/rolling_cache.py:425 | §2.4 rewritten: `MultiInstrumentCache.project_from_subscriptions` uses M4's shipped constants directly (`_FIELDS_PER_BAR`, `_BYTES_PER_CELL`, `maxlen_for_bar_spec`); no new instance method on RollingCache; no math duplication |
| HIGH | Realism | Shadow-replay fixture tap would conflict with `paper.pid` lock if implementer misread §4.2 | §4.2 explicit: standalone `v5/tools/record_ws_tap.py` script, connects directly to Binance WS, does NOT touch paper_runner/paper.pid |
| HIGH | Realism | `Clock.now()` in design conflicts with shipped `TestClock.now_ns()` at v5/testing.py:30 | §2.1 renamed to `now_ns()`; Protocol now conforms with shipped TestClock per CLAUDE.md meta-rule #1 |
| HIGH | Realism | AC-D6 AST scan (strategy loader) has no home in M6 | §4.3 explicit scope decision: AST scan DEFERRED to M7; M6 ships Clock Protocol + docstring prohibition text only. Documented in §15 M7 Impact |
| MED | Quant | AC-D1b budget scope unclear (only cache or whole engine?) | §2.4 budget-scope-clarification: 1200 MB is cache-only; non-cache consumers enumerated and shown to be < 12 MB aggregate |
| MED | Quant | Phase-3 RED fixture blocked by 24h Phase-4 recording | §7 Wave F: Phase-3 uses 1h proxy fixture; Phase-4 uses real 24h fixture; TDD gate works both phases |
| MED | Quant | Shim path economics — no sunset trigger if it carries through M7-M9 | §10: end-of-M8 AIPIP re-evaluation trigger added |
| MED | Quant | `subscribe_all` dedup by `DataStream` vs 3-tuple cache key | §2.8: dedup key corrected to `(DataStream, role)` 2-tuple; distinct roles fan out to distinct caches |
| MED | FIX | FIX tag ownership table missing file column | §2.2: table now has File column per type (streams.py / instruments.py / types.py) |
| MED | FIX | `SubscriptionHandle._id: int` vs FIX MDReqID(262) string — venue interop ambiguity | §2.3: explicit docstring — bus-internal INT, FIX MDReqID mapping deferred to M11+ at session boundary |
| MED | FIX | 24h shadow replay needs multi-shard reconnect scenario | §4.2 step 5: dedup key `(DataStream, ts_event)`, multi-shard reconnect-storm synthetic test required |
| MED | FIX | `Clock.now()` vs Nautilus `timestamp_ns()`; Bar→Order.armed_at clock source undefined | §2.1: renamed to `now_ns()`; explicit note that `Order.armed_at` sources `bar.ts_event` in backtest/replay (NOT `clock.now_ns()`) |
| MED | Realism | Wave D 14h budget optimism — v4/price_monitor has threading, shard, reconnect, fan-out complexity | Wave D step 4 note added: "greenfield (new) vs extracted (forked)" labelling so Phase 4 plans realistically; 18h stop-trigger + shim fallback unchanged |
| MED | Realism | `@runtime_checkable` missing on DataClient/LiveDataClient Protocols — breaks T-D15 isinstance checks | §2.6: both Protocols explicitly `@runtime_checkable` |
| LOW | Quant | `subscribe_all` docstring said "drives Wave-C eager projection" — redundant with start-time check | §2.8: wording clarified, projection is startup-level |
| LOW | Quant | MessageBus logger syntax "pattern over principle" drift | §2.3 — keeping sketch, Phase-4 implementer follows M4/M5 logger style per CLAUDE.md meta-rule #2 |
| LOW | FIX | INSTRUMENT_INFO as one-shot vs refresh | Left as one-shot for M6; refresh via PULL_SCHEDULED is a M11+ concern documented in §15 |
| LOW | FIX | Gap detection crypto-WS vs FIX MsgSeqNum honesty | §2.5 GapDetector docstring already states "approximate mapping"; crypto-WS convention comment appended implicitly (no edit needed) |
| LOW | FIX | Binance per-IP rate limit beyond per-connection 200 cap | Wave D step 4 note: implementer to include 5-msg/sec subscribe rate + 300-conn/5min limit in backoff — Phase-4 concern |
| LOW | FIX | VenueCapabilities missing `supported_transport_modes` — M7 strategy API will want | Deferred to M7: VenueCapabilities can be extended additively without M6 rework |
| LOW | Realism | Subscription `__new__` field-add guardrail | §2.2: Phase-4 implementer comment — when adding field, update 3 sites (class body, __new__ sig, super().__new__ call) |

All HIGH + MED findings resolved or explicitly scoped to M7+ with carryover documentation. LOW findings are either resolved or acknowledged.

---

## 15. M7 Impact — what M6 leaves for M7

Explicit carryover to M7 (Strategy API Redesign) so nothing is lost across the phase boundary:

1. **AC-D6 AST scan in strategy loader** — M6 ships Clock Protocol + docstring prohibition; M7 wires the AST scan into `v5/engine.py::_load_strategy_fn` (or its M7 replacement). Test target: strategy with direct `time.time()` raises at `__init__`, not at first tick.

2. **`PaperConfig.use_data_engine` → `True` live rollout** — M6 ships flag + harness + 24h shadow replay gate; M7 flips the flag per-strategy after live ops review. The v4→v5 paper runner swap is ALSO M7's — M6 only validates the architecture.

3. **`VenueCapabilities.supported_transport_modes`** — additive field M7 can add when strategies need to declare `venue.supports(TransportMode.PUSH)` symmetry.

4. **`arm_bracket(entry, sl, tp)` factory** — M5 carryover; now M7 has the data pipeline it needs to exercise bracket arming on real bar events.

5. **FIX MDReqID string binding at session boundary** — when M11+ surfaces a FIX gateway, `SubscriptionHandle._id: int` is mapped to a venue-string at the gateway layer (not in MessageBus). No M6 code change; design discipline for M11+.

6. **Backtest flag default flip** — `BacktestConfig.use_data_engine` ships False in M6; M9+ flips default to True once the vectorised numpy-array path is rebuilt via `MessageBus` without latency regression.

All of these appear in M7's brief under "M6 Impact" — cross-milestone carryover discipline per the M5→M6→M7 pattern.
