# M6 — v5/data/ Package

**Summary**: Build a clean data architecture (`v5/data/`) with protocol-based clients, a message bus, clock abstraction, and strategy-declared subscriptions — replacing the fragmented 831-line PriceMonitor, separate LiveFetcher, and disconnected fetch scripts.

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

- `v5/data/__init__.py` — package root
- `v5/data/engine.py` — `DataEngine` orchestrator (manages clients, routes data to subscribers)
- `v5/data/bus.py` — `MessageBus` for pub/sub event routing
- `v5/data/cache.py` — `RollingCache` (bounded, memory-safe bar/event storage)
- `v5/data/protocols.py` — `DataClient` Protocol (connect, disconnect, subscribe, request)
- `v5/data/clients/binance_ws.py` — `BinanceWSClient` (consolidates PriceMonitor WS logic)
- `v5/data/clients/binance_rest.py` — `BinanceRESTClient` (consolidates LiveFetcher REST logic)
- `v5/data/clients/parquet_replay.py` — `ParquetReplayClient` for backtest (replays historical parquet data)
- `v5/data/aggregator.py` — `TimeBarAggregator` for 1m-to-Nm bar assembly
- `v5/data/types.py` — Non-bar event types: `FundingRate`, `OpenInterest`, `MarkPrice`, `Liquidation`
- `v5/data/instruments.py` — Instrument metadata registry (symbol, tick_size, lot_size, margin requirements)
- `v5/data/gaps.py` — Gap detection using ts_event + ts_init + sequence numbers
- `v5/clock.py` — `Clock` protocol + `TestClock` (deterministic) + `LiveClock` (real-time)
- Strategy-declared subscriptions: strategies implement `required_data() -> list[Subscription]`

### Out of scope

- Strategy API redesign (M7 — but `required_data()` is a forward-compatible hook)
- Order execution / exchange connectivity (future milestone)
- Historical data backfill automation (stays in tools/)
- Dashboard data streaming
- Multi-exchange support (Binance only for now)
- Tick-level data (bar-level minimum granularity)

---

## Core Abstractions

All data types use `@dataclass(slots=True)` for half the memory footprint vs regular dataclasses.

### Data Types (`v5/data/types.py`)

```python
@dataclass(frozen=True, slots=True)
class InstrumentId:
    symbol: str          # "BTCUSDT"
    venue: str           # "BINANCE"
    market: str          # "perp" | "spot"

@dataclass(frozen=True, slots=True)
class BarSpec:
    step: int                                   # 1, 5, 15, 60 ...
    unit: Literal["SECOND", "MINUTE", "HOUR", "DAY"]
    price: Literal["LAST", "MID", "MARK"] = "LAST"

@dataclass(frozen=True, slots=True)
class BarType:
    instrument: InstrumentId
    spec: BarSpec
    source: Literal["EXTERNAL", "INTERNAL"] = "EXTERNAL"  # INTERNAL = aggregator-produced

@dataclass(slots=True)
class Bar:
    bar_type: BarType
    ts_event: int        # epoch ns, close time (FIX TransactTime(60))
    ts_init: int         # epoch ns, local receive timestamp (no direct FIX analog; FIX SendingTime(52) is counterparty send time, not local receive)
    open: float; high: float; low: float; close: float; volume: float
```

### Subscription NamedTuple

```python
class Subscription(NamedTuple):
    bar_type: BarType
    handler: Callable[[Bar], None]
    warmup: int = 500   # min bars before handler starts firing
```

Strategies declare data needs via `required_data() -> list[Subscription]`. Runner unions across all strategies in the portfolio, computes the unique subscription set, and hands it to `DataEngine.subscribe_all(...)`. Only the data actually needed is fetched.

```python
class Strategy:
    def required_data(self) -> list[Subscription]:
        return [
            Subscription(BarType(inst, BarSpec(1, "HOUR")), self.on_1h_bar, warmup=500),
            Subscription(BarType(inst, BarSpec(5, "MINUTE")), self.on_5m_bar, warmup=100),
        ]
```

### DataClient Protocol (`v5/data/clients/base.py`)

```python
class DataClient(Protocol):
    name: str
    def supports(self, bar_type: BarType) -> bool: ...
    def subscribe(self, bar_type: BarType) -> None: ...
    def unsubscribe(self, bar_type: BarType) -> None: ...
    def request_bars(self, bar_type: BarType, start: int, end: int, limit: int) -> list[Bar]: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
```

Pluggable per (venue, transport). One `BinanceWSClient` multiplexes 1m + 1h + spot miniTicker streams (not 3 separate WS classes). This replaces the fragmented PriceMonitor pattern where 3 parallel WebSocket objects each manage their own connection/reconnect.

### RollingCache (`v5/data/cache.py`)

Memory-bounded, column-oriented ring buffers that physically cannot leak. Pre-allocated numpy arrays avoid per-bar allocation and enable zero-copy views.

```python
class RingBuffer:
    """Fixed-size column-oriented ring buffer backed by pre-allocated numpy arrays."""
    def __init__(self, capacity: int):
        self._capacity = capacity
        self._head = 0          # write pointer (next insert position)
        self._count = 0         # number of valid entries (0..capacity)
        # Column-oriented storage — one array per OHLCV field
        self.ts_event = np.empty(capacity, dtype=np.int64)
        self.ts_init  = np.empty(capacity, dtype=np.int64)
        self.open     = np.empty(capacity, dtype=np.float64)
        self.high     = np.empty(capacity, dtype=np.float64)
        self.low      = np.empty(capacity, dtype=np.float64)
        self.close    = np.empty(capacity, dtype=np.float64)
        self.volume   = np.empty(capacity, dtype=np.float64)

    def append(self, bar: Bar) -> None:
        idx = self._head % self._capacity
        self.ts_event[idx] = bar.ts_event
        self.ts_init[idx]  = bar.ts_init
        self.open[idx]     = bar.open
        self.high[idx]     = bar.high
        self.low[idx]      = bar.low
        self.close[idx]    = bar.close
        self.volume[idx]   = bar.volume
        self._head += 1
        self._count = min(self._count + 1, self._capacity)

    def _view(self, arr: np.ndarray) -> np.ndarray:
        """Return zero-copy view of valid data in chronological order."""
        if self._count < self._capacity:
            return arr[:self._count]
        tail = self._head % self._capacity
        return np.concatenate([arr[tail:], arr[:tail]])  # wraps around

class RollingCache:
    def __init__(self, max_bars_per_type: int = 2000):
        self._bars: dict[BarType, RingBuffer] = {}
        self._max = max_bars_per_type
    def on_bar(self, bar: Bar) -> None:
        rb = self._bars.get(bar.bar_type)
        if rb is None:
            rb = RingBuffer(capacity=self._max)  # pre-allocated, CANNOT grow
            self._bars[bar.bar_type] = rb
        rb.append(bar)
    def arrays(self, bar_type: BarType) -> BarArrays:
        """Return zero-copy views into ring buffer arrays."""
        rb = self._bars[bar_type]
        return BarArrays(
            ts_event=rb._view(rb.ts_event),
            open=rb._view(rb.open), high=rb._view(rb.high),
            low=rb._view(rb.low), close=rb._view(rb.close),
            volume=rb._view(rb.volume),
        )
```

Memory budget: 236 tokens x 3 BarTypes x 2000 bars x 7 columns x 8 bytes = ~34 MB (fixed ceiling).

Default `max_bars_per_type` depends on `BarSpec.unit`:
- **MINUTE** = 1440 (24h of 1-minute bars)
- **HOUR** = 2000 (83 days)
- **DAY** = 500 (1.4 years)

Strategies needing longer history declare higher `warmup` on their `Subscription`. Unbounded growth is impossible by construction.

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
    contract_type: str           # "perpetual" | "quarterly" | "spot"
```

Fetched from venue info endpoints at `connect()`. Used by sizing (min_notional clamp), dust threshold calculation, and liquidation math.

### Non-Bar Event Types (`v5/data/types.py`)

First-class event types routable through MessageBus via the same `Subscription` mechanism as bars:

```python
@dataclass(slots=True)
class FundingRate:
    instrument_id: InstrumentId
    ts_event: int               # epoch ns
    rate: float                 # funding rate (e.g., 0.0001 = 1bp)
    next_funding_ts: int        # epoch ns of next settlement

@dataclass(slots=True)
class OpenInterest:
    instrument_id: InstrumentId
    ts_event: int
    oi_contracts: float         # open interest in contracts
    oi_usd: float               # open interest in USD

@dataclass(slots=True)
class Liquidation:
    instrument_id: InstrumentId
    ts_event: int
    side: Literal["BUY", "SELL"]
    qty: float
    price: float

@dataclass(slots=True)
class MarkPrice:
    instrument_id: InstrumentId
    ts_event: int
    mark_price: float
    index_price: float

@dataclass(slots=True)
class BookSnapshot:
    instrument_id: InstrumentId
    ts_event: int
    bids: list[tuple[float, float]]  # [(price, qty), ...]
    asks: list[tuple[float, float]]
```

Strategies subscribe to these identically to bars: `Subscription(bar_type_or_event_type, handler, warmup)`.

### Clock Abstraction (`v5/clock.py`)

```python
class Clock(Protocol):
    def now(self) -> int: ...      # epoch nanoseconds

class TestClock:
    """Deterministic clock for backtest. Advances with bars."""
    def set_time(self, ns: int) -> None: ...
    def advance(self, delta_ns: int) -> None: ...
    def now(self) -> int: ...

class LiveClock:
    """Wall clock for paper/live trading."""
    def now(self) -> int: ...      # wraps time.time_ns()
```

- Strategies use `clock.now()`; direct `time.time()` is forbidden (raises at strategy init).
- `UniverseContext` never exposes bars with `ts_event > clock.now()` -- no look-ahead leakage in backtest.
- `TestClock` advances deterministically: set to bar close time as each bar is processed.
- `LiveClock` wraps real wall time.

---

## Multi-Source Routing

`DataEngine` holds a priority-ordered `list[DataClient]`. On `subscribe(bar_type)`, a 3-step cascade:

1. **Find supporting client by priority**: iterate clients where `client.supports(bar_type)` is True; pick highest priority (Binance-WS > Binance-REST > parquet-replay).
2. **Try TimeBarAggregator if no native support**: find a client with smaller `BarSpec` (e.g., 1m) and spawn a `TimeBarAggregator(source=1m, target=5m)` that subscribes upstream and publishes downstream as `source="INTERNAL"`.
3. **Raise at startup if still unavailable**: clear error (not silent no-op). Strategies know immediately if their data needs cannot be satisfied.

### TimeBarAggregator First-Period Handling

The first bar after subscription may be partial (e.g., strategy subscribes at minute 3 of a 5m period). `BarAggregator` DISCARDS the first partial bar (matches NautilusTrader convention). Strategies see their first bar only after a complete aggregation period.

### WS + REST + Reconnect Pattern (4-step)

One `DataClient` per (venue, transport), sharing connection state:

```
subscribe(bar_type):
  1. REST.request_bars(end - warmup*step, end)    -> seed cache
  2. WS.subscribe(bar_type)                        -> live bars appended
  3. On WS disconnect: REST polling fallback       -> same interface, strategies don't see disconnect
  4. On WS reconnect: REST.request_bars(last_seen, now) -> gap fill
```

Strategies see one continuous stream; reconnect + backfill is entirely internal.

### REST+WS Merge Strategy

On WS reconnect, engine enters a MERGE WINDOW (configurable, default 30s) during which both REST backfill and WS live bars arrive. A per-BarType merge buffer holds bars ordered by `ts_event`. Bars with `ts_event` already in the ring buffer are dropped (dedup). Out-of-order bars (WS delivers T+2 before REST returns T+1) are inserted at the correct position in the merge buffer, then flushed to the ring buffer in order. After the merge window closes, the merge buffer is drained and normal single-source mode resumes.

### BinanceWSClient Multiplexing

ONE class handles 1m + 1h + miniTicker (not 3 classes). This replaces v4's PriceMonitor which manages 3 parallel WebSocket objects (`ws_perp_1m`, `ws_perp_1h`, `ws_spot_miniTicker`) each with their own connection/reconnect logic. The single `BinanceWSClient` presents a unified `DataClient` interface.

### WS Connection Sharding

Binance enforces 200 streams per WebSocket connection. With 236 tokens x 2 streams (1m kline + 1h kline) = 472 streams minimum, `BinanceWSClient` MUST shard across 3+ connections automatically. Implementation: `BinanceWSClient._connections: list[WSConnection]`, each holding up to 200 streams. New subscriptions are assigned to the connection with fewest streams. Reconnect logic operates per-connection.

---

## Gap Detection (`v5/data/gaps.py`)

Every `Bar` carries:
- `ts_event` (epoch ns, close time) -- FIX `TransactTime(60)`
- `ts_init` (epoch ns, local receive timestamp -- no direct FIX analog; FIX `SendingTime(52)` is counterparty send time, not local receive)
- Monotonic per-BarType sequence enforced

Gap detection specifics:
- Cache rejects bars with `ts_event <= last_ts` for that BarType (duplicate/stale rejection)
- On rejection: logs the gap with BarType + expected vs actual ts_event
- On gap detection (ts_event jump > expected interval): triggers REST backfill request
- `GapDetected` event published to MessageBus for observability

---

## Key Acceptance Criteria

**AC-D1 -- Memory bounded by construction**: `RollingCache` uses pre-allocated numpy ring buffers (`RingBuffer`) per `BarType`. Column-oriented arrays with head/tail pointers; `arrays()` returns zero-copy views. Unbounded growth is impossible by construction. Memory: 34MB for 236 tokens x 3 BarTypes x 2000 bars. Default `max_bars_per_type` depends on `BarSpec.unit`: MINUTE=1440 (24h), HOUR=2000 (83d), DAY=500 (1.4y). Strategies can declare higher `warmup` on `Subscription`.

**AC-D2 -- Strategy-declared subscriptions**: `Strategy.required_data() -> list[Subscription]`. Runner unions across all strategies in the portfolio. Only declared BarTypes are fetched.

**AC-D3 -- DataClient protocol**: `supports/subscribe/unsubscribe/request_bars/connect/disconnect`. Pluggable per (venue, transport). One `BinanceWSClient` multiplexes 1m + 1h + miniTicker streams (not 3 separate WS classes).

**AC-D4 -- Multi-source routing with aggregation fallback**: `DataEngine` holds priority-ordered `list[DataClient]`. On `subscribe(bar_type)`: find supporting client; if none, try `TimeBarAggregator(source=smaller_spec, target=requested_spec)`. If still unavailable, raise at startup (not silent no-op).

**AC-D5 -- Gap detection**: every `Bar` carries `ts_event` (close time) + `ts_init` (receive time) + monotonic per-BarType sequence. Cache rejects bars with `ts_event <= last_ts`; logs gap and requests REST backfill on detection.

**AC-D6 -- Backtest/live parity via Clock abstraction**: `v5/clock.py` provides `TestClock` (virtual time, advances with bars) + `LiveClock` (wall clock). Strategies use `clock.now()`; direct `time.time()` forbidden. `UniverseContext` never exposes bars with `ts_event > clock.now()` -- no look-ahead leakage in backtest.

**AC-D7 -- Non-bar event types**: `FundingRate`, `OpenInterest`, `Liquidation`, `MarkPrice`, `BookSnapshot` are first-class event types (not bars). Strategies subscribe to them via same `Subscription` mechanism.

**AC-D8 -- Instrument metadata**: `Instrument` dataclass carries `tick_size`, `lot_size`, `min_notional`, `maker_fee_bps`, `taker_fee_bps`, `margin_tiers`, `max_leverage`, `funding_interval_s`, `contract_type`. Fetched from venue info endpoints at connect. Used by sizing (min_notional clamp), dust threshold, liquidation math.

**AC-D9 -- Backpressure**: If a subscriber callback is slow (>100ms for a 1m bar), DataEngine logs a warning. No backpressure or dropping -- all bars are delivered. Strategies with heavy computation should offload to background threads.

---

## Subsumption Table

What becomes a free consequence of the data architecture:

| Previously discussed item | Becomes free consequence of data arch |
|---|---|
| `bar_resolution` config (C-8) | Just a BarSpec on Subscription -- no dedicated config field |
| Sub-hourly dispatch (Q4 wiring) | Just a 5-MINUTE Subscription; bar_processor handles any BarType |
| Paper vs backtest switch | Swap `LiveDataClient` for `ParquetReplayClient` -- one config flag |
| `hist_cache` internalization (C-9) | `RollingCache` handles it natively; no parameter plumbing |
| ADV computation | Derived bar stream via `source="INTERNAL"` aggregator |
| TradingView / alt data integration | Client adapter -- strategies subscribe same as any bar |

---

## Phase 3 Test Plan

### Data architecture tests

- **T-D1** RollingCache `maxlen` enforced: append 3000 bars to cache with maxlen=2000 -> cache holds exactly 2000 (oldest evicted)
- **T-D2** Strategy-declared subscription union: 3 strategies x 2 bar types each -> DataEngine has 6 unique subscriptions, no dupes
- **T-D3** Multi-source routing: primary client (WS) unavailable -> fallback to REST client picks up
- **T-D4** Aggregation: only 1m client available; subscribe to 5m -> TimeBarAggregator produces correct 5m bars from 1m input
- **T-D5** Gap detection: inject duplicate `ts_event` -> cache rejects; inject stale `ts_event` -> cache rejects with log
- **T-D6** Clock isolation: strategy's `clock.now()` returns bar close time in backtest; never sees future bar

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M1** (v5 Fork) | **Required** — v5 namespace |
| **M3** (Memory Fix) | **Benefits from** — RollingCache may already exist from M3; reuse or extend |
| **M4** (BarProcessor) | **Benefits from** — BarProcessor consumes bars from DataEngine |

---

## Time Estimate

**50-70 hours**

- ~8h: DataEngine + MessageBus + DataClient Protocol design
- ~8h: RollingCache (or extend M3's)
- ~10h: BinanceWSClient (extract from PriceMonitor)
- ~6h: BinanceRESTClient (extract from LiveFetcher)
- ~6h: ParquetReplayClient
- ~5h: TimeBarAggregator
- ~4h: Clock abstraction (TestClock + LiveClock)
- ~4h: Non-bar event types + FundingRate wiring
- ~3h: Instrument metadata registry
- ~3h: Gap detection
- ~3h: Strategy subscription wiring
- ~5h: Integration tests + parity verification
- ~3h: Documentation

---

## Parity Gate

- Paper trader runs with v5/data/ clients instead of v4 PriceMonitor + LiveFetcher
- Same data received, same results produced (verified by 1-hour parallel run)
- Gap detection test catches intentionally missing bars
- Memory bounded: 24h simulated run with RollingCache stays under 100MB RSS growth
- All v5 tests pass
