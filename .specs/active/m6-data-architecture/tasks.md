# M6 Task Decomposition

**Phase**: 3 (Decompose + Tests)
**Waves**: A (primitives) → B (instruments) → C (cache+gap) → D (protocols+clients) → E (engine) → F (paper migration)
**Task count**: 21 tasks. Each task touches 1-2 files. Independent tasks marked `[P]`.
**Test IDs**: T-D1…T-D18 from brief §"Phase 3 Test Plan"; T-D19 (shadow replay), T-D20 (bus determinism grep), T-D21 (infra wall-clock docs) per brief ACs D19/D20.

Legend: `[P]` = parallel-safe (no blocked dependencies); `(after: N, M)` = blocked by tasks N and M.

---

## Wave A — Primitives (foundation for all other waves)

### Task 1 [P] — clock.py
- **Files**: `v5/clock.py` (new, ≤50 LOC)
- **Delivers**: `Clock` Protocol (`@runtime_checkable`, `now_ns() -> int`), `LiveClock` impl, module docstring with "Infrastructure wall-clock reads: time.time_ns()" section.
- **Acceptance**: AC-D6 (partial — Clock abstraction only; AST scan deferred to M7 per design §4.3). AC-D20 docstring.
- **Tests covered**: T-D6 (Clock isolation + TestClock interop), T-D21 (grep docstring — partial, extended in Wave D).
- **Blast risk**: new file, zero importers.

### Task 2 [P] — data/streams.py (enums + InstrumentId + DataStream + Subscription)
- **Files**: `v5/data/streams.py` (new, ≤300 LOC)
- **Delivers**: `DataKind`, `Venue`, `TransportMode`, `GapPolicy` enums; `InstrumentId` frozen dataclass; `DataStream` frozen dataclass with `__post_init__` validator; `Subscription` NamedTuple with `__new__` validator per design §2.2.
- **Acceptance**: AC-D14 (DataKind routing validation), AC-D18 (Subscription validation), parts of AC-D3/D17 (Venue enum).
- **Tests covered**: T-D14 (DataKind routing), T-D17 (Venue typing), T-D18 (Subscription validation — except C3 price-type check which is engine-time).

### Task 3 [P] — data/bus.py
- **Files**: `v5/data/bus.py` (new, ≤120 LOC)
- **Delivers**: `SubscriptionHandle` frozen dataclass with FIX note docstring, `MessageBus` with dict-based FIFO dispatch, per-handler try/except.
- **Acceptance**: AC-D19 (FIFO determinism).
- **Tests covered**: T-D20 (bus determinism grep + FIFO assertion).

### Task 4 [P] — data/exceptions.py
- **Files**: `v5/data/exceptions.py` (new, ≤40 LOC)
- **Delivers**: `DataGapError`, `RateLimitExceeded`, `PriceTypeNotSupported`, `SymbolNotFound`, `ClockDriftHigh` exception classes.
- **Acceptance**: supporting AC-D5/D10/D11/D18.
- **Tests covered**: indirect (consumed by Wave B/C/D tests).

### Task 5 — data/__init__.py FIX vocabulary docstring (after: 2, 3, 4)
- **Files**: `v5/data/__init__.py` (new, ≤80 LOC)
- **Delivers**: Package root docstring with the 14-tag FIX vocabulary block verbatim from brief §"FIX vocabulary block" lines 124-158. Re-exports `DataStream`, `Subscription`, `DataKind`, `Venue`, `TransportMode`, `GapPolicy`, `MessageBus`, `SubscriptionHandle`, all 5 exceptions.
- **Acceptance**: AC-D16 (FIX docstring coverage).
- **Tests covered**: T-D16 (FIX tag grep test).

---

## Wave B — Instrument layer (after Wave A types)

### Task 6 — data/instruments.py (after: 2)
- **Files**: `v5/data/instruments.py` (new, ≤250 LOC)
- **Delivers**: `Instrument` dataclass (tick_size, lot_size, min_notional, fees, margin tiers, `contract_subtype` Literal + deprecated `contract_type` alias per brief C2), `VenueCapabilities` frozen dataclass, `InstrumentRegistry` with `resolve`, `metadata`, `capabilities`, `list_perp_universe` methods + venue-keyed `_symbol_aliases` for 1000-prefix tokens.
- **Acceptance**: AC-D8 (Instrument metadata + registry), parts of AC-D18 (capabilities populate for C3 check).
- **Tests covered**: T-D11 (instrument registry + venue capabilities).

### Task 7 [P] — data/registry.py (DataClientRegistry) (after: 2)
- **Files**: `v5/data/registry.py` (new, ≤150 LOC)
- **Delivers**: `DataClientRegistry` with `register`, `get_clients` (priority-sorted PUSH > PULL_ONCE > PULL_SCHEDULED > REPLAY), `registered_venues`.
- **Acceptance**: AC-D17 (Venue + DataClientRegistry).
- **Tests covered**: T-D17 (isolation by venue, priority sort, missing-venue raise).

---

## Wave C — Cache + gap detection (after Wave A types)

### Task 8 — data/cache.py (MultiInstrumentCache) (after: 2)
- **Files**: `v5/data/cache.py` (new, ≤180 LOC)
- **Delivers**: `MultiInstrumentCache` with 3-tuple key `(InstrumentId, BarSpec, role)`, `on_bar`, `arrays`, `compute_projected_memory_mb`, `project_from_subscriptions` per design §2.4 (uses M4 constants via import).
- **Acceptance**: AC-D1, AC-D1a, AC-D1b (cache + 3-tuple key + aggregate budget).
- **Tests covered**: T-D1 (memory bounds), T-D1a (3-tuple distinctness), T-D1b (aggregate ≤ 1200 MB).

### Task 9 [P] — data/gaps.py (GapDetector) (after: 2)
- **Files**: `v5/data/gaps.py` (new, ≤220 LOC)
- **Delivers**: `GapDetector` with per-stream monotonicity state, `on_bar` dispatch by `GapPolicy`, `_handle_gap` with STRICT/NAN_FILL/SKIP branches, `GapDetected` event dataclass.
- **Acceptance**: AC-D5 (gap detection with 3-mode policy).
- **Tests covered**: T-D5 (STRICT), T-D5a (NAN_FILL), T-D5b (SKIP).

---

## Wave D — Protocols + clients (after Wave A + B)

### Task 10 — data/protocols.py (after: 2)
- **Files**: `v5/data/protocols.py` (new, ≤80 LOC)
- **Delivers**: `DataClient` Protocol (`@runtime_checkable`, fields + `supports`, `connect`, `disconnect`, `request`, `replay`), `LiveDataClient(DataClient)` Protocol with `subscribe`, `unsubscribe`, `subscribe_scheduled`.
- **Acceptance**: AC-D3 (DataClient protocol split).
- **Tests covered**: T-D3 (Protocol shapes), T-D15 (transport-mode dispatch — structural).

### Task 11 [P] — data/clients/parquet_replay.py (after: 10, 6)
- **Files**: `v5/data/clients/parquet_replay.py` (new, ≤180 LOC)
- **Delivers**: `ParquetReplayClient` implementing `DataClient` (NOT LiveDataClient); `supported_modes = {REPLAY, PULL_ONCE}`; reads from `data/{market}/1h_cache/`, `data/{market}/live/`, globally sorted + dedup. Module docstring with "Infrastructure wall-clock reads: (none — replay is timestamp-driven)".
- **Acceptance**: AC-D3 (no streaming), AC-D20 (docstring).
- **Tests covered**: T-D3 (parquet client), T-D15 (unsupported subscribe raises `NotImplementedError`).

### Task 12 — data/clients/binance_rest.py (after: 10, 6)
- **Files**: `v5/data/clients/binance_rest.py` (new, ≤400 LOC; extract from v4/live_fetcher.py)
- **Delivers**: `BinanceRESTClient` implementing `LiveDataClient`; weight tracker (`_check_budget`, `_on_response` reading `X-MBX-USED-WEIGHT-1M`); time-drift sync (`GET /api/v3/time` / `GET /fapi/v1/time`, `venue_clock_offset_ms`); module docstring with "Infrastructure wall-clock reads: time.time_ns() for rate-limit window, drift sync interval".
- **Acceptance**: AC-D10 (rate-limit tracker), AC-D11 (time-drift sync), AC-D20 (docstring).
- **Tests covered**: T-D9 (rate-limit tracker), T-D10 (time-drift sync).

### Task 13 — data/clients/binance_ws.py (after: 10, 6, 12)
- **Files**: `v5/data/clients/binance_ws.py` (new, ≤600 LOC; extract from v4/price_monitor.py)
- **Delivers**: `BinanceWSClient` implementing `LiveDataClient`; multiplexed 1m+1h+aggTrades+markPrice; 3+ connection sharding at 200-stream cap; reconnect + 30s merge window with `(DataStream, ts_event)` dedup; `INFO "ws_shard_opened"` log. Module docstring with "Infrastructure wall-clock reads: time.time_ns() for heartbeat, reconnect backoff, drift sync".
- **Budget**: 14h nominal, 18h stop-trigger per AC-D13. If trigger fires → stop and escalate; fallback is wrap v4/price_monitor behind `DataClient` shim.
- **Acceptance**: AC-D3 (WS multiplexing), AC-D13 (budget + stop-trigger), AC-D20 (docstring).
- **Tests covered**: T-D3 (WS routing), T-D13 (Trade events), T-D15 (unsupported replay raises).

---

## Wave E — Engine wiring (after Waves A + B + C + D)

### Task 14 — data/engine.py (DataEngine) (after: 3, 7, 8, 9, 10)
- **Files**: `v5/data/engine.py` (new, ≤500 LOC)
- **Delivers**: `DataEngine` orchestrator with `start`, `stop`, `subscribe`, `subscribe_all` (dedup by `(DataStream, role)`), `unsubscribe`, `request` (public accessor), `_resolve_mode(subscription, clients)` honoring `fallback_allowed`. Routing cascade per design §2.8: get_clients → price-type check → mode resolve → BAR-or-event dispatch → M4 aggregation fallback → raise. Startup memory check via `MultiInstrumentCache.project_from_subscriptions`.
- **Acceptance**: AC-D2, AC-D4, AC-D15, AC-D17, AC-D18 (C3), AC-D1b startup.
- **Tests covered**: T-D2 (strategy-declared union), T-D4 (multi-source routing + aggregation), T-D15 (transport-mode dispatch + fallback_allowed), T-D17 (registry cascade), T-D18 (C3 price-type check).

### Task 15 [P] — Strategy `required_data()` Protocol + runner union helper (after: 2)
- **Files**: `v5/strategy_spec.py` (modify — add `required_data()` Protocol scaffold; deep wiring is M7's) + `v5/data/__init__.py` (extend re-exports)
- **Delivers**: `Strategy.required_data() -> list[Subscription]` as a Protocol (not enforced yet; M7 wires the AST scan). Runner-side `union_subscriptions(strategies) -> list[Subscription]` helper.
- **Acceptance**: scaffold for AC-D2 consumer side.
- **Tests covered**: T-D2 (union logic on synthetic strategies).

---

## Wave F — Paper migration + shadow replay (after Waves A–E)

### Task 16 — PaperConfig.use_data_engine + BacktestConfig.use_data_engine (after: 14)
- **Files**: `v5/paper_config.py` + `v5/backtest.py` (modify — add `use_data_engine: bool = False` field)
- **Delivers**: Symmetric feature flag across paper + backtest configs.
- **Acceptance**: AC-D12 (flag mechanism).
- **Tests covered**: T-D8 (flag=OFF byte-identity on paper). Adds new `test_m6_backtest_parity.py` for flag=ON backtest path.

### Task 17 — paper_engine.py 8-site dispatch (after: 16)
- **Files**: `v5/paper_engine.py` (modify — 8 sites per design §4 table)
- **Delivers**: Each of the 8 sites (`__init__` 859-912, CandleAggregator 893-911, shutdown 1245-1248, `update_subscriptions` 2046-2068, OHLCV fetch 3213-3217, funding rate fetch 3225-3229, hourly settlement 3237-3252) gains `if self._use_data_engine:` branch wiring to `DataEngine`. Flag=OFF is byte-identical to current main.
- **Acceptance**: AC-D12 (paper migration + flag).
- **Tests covered**: T-D8 (paper_engine parity both directions).

### Task 18 — run_paper_multi.py DataEngine fan-out (after: 17)
- **Files**: `v5/run_paper_multi.py` (modify — lines 360-363, 381-401)
- **Delivers**: Shared `DataEngine` fan-out replaces shared `PriceMonitor` fan-out under flag.
- **Acceptance**: AC-D12 completeness.
- **Tests covered**: T-D8 (multi-engine fan-out parity).

### Task 19 [P] — record_ws_tap.py standalone recording script (after: 2)
- **Files**: `v5/tools/record_ws_tap.py` (new, ≤200 LOC)
- **Delivers**: Standalone script that connects directly to Binance WS, records frames to `v5/tests/fixtures/shadow_replay_24h/*.jsonl.zst`. **Does NOT touch paper_runner**. Used one-shot in Phase 4 for 24h; also drives Phase-3 1h proxy fixture.
- **Acceptance**: supports AC-D12 hard merge gate.
- **Tests covered**: smoke test on short recording.

### Task 20 — shadow_replay_harness.py (after: 17, 18, 19)
- **Files**: `v5/tests/shadow_replay_harness.py` (new, ≤300 LOC) + Phase-3 1h proxy fixture
- **Delivers**: Harness feeds recording into two PaperEngine instances (flag=OFF + flag=ON) against same TestClock. Bar divergence=0 tolerance. Funding/Mark/Trade 1 bps. Multi-shard reconnect scenario with `(DataStream, ts_event)` dedup key.
- **Acceptance**: AC-D12 (hard merge gate).
- **Tests covered**: T-D19 (24h shadow replay harness, RED on 1h proxy fixture in Phase 3, green gate in Phase 4).

### Task 21 — Deferred-types registry guard (after: 14)
- **Files**: `v5/data/engine.py` (extend)
- **Delivers**: `DataEngine.subscribe` raises `NotImplementedError("BookSnapshot deferred to M10")` (etc.) for the 5 deferred event types per brief Fix 3.
- **Acceptance**: AC-D7 deferred-type guard.
- **Tests covered**: T-D12 (deferred event types not plumbed).

---

## Dependency graph (critical path)

```
Wave A: [1] [2] [3] [4] → [5]
Wave B: [2] → [6] [2] → [7]
Wave C: [2] → [8] [2] → [9]
Wave D: [2] → [10] → [11]
                 → [12]
                 → [13] (after 12)
Wave E: [3, 7, 8, 9, 10] → [14]
       [2] → [15]
Wave F: [14] → [16] → [17] → [18] → [20]
       [2] → [19] → [20]
       [14] → [21]
```

Critical path: 2 → 10 → 12 → 13 → 14 → 17 → 18 → 20. Estimated 50-60h of the 62-80h budget. Parallel tracks (1, 3, 4, 5, 6, 7, 8, 9, 11, 15, 19, 21) absorb the balance.

---

## Stop-redecompose triggers (recap from brief + design §10)

- Task 13 (BinanceWSClient) exceeds 18h → STOP; wrap v4/price_monitor behind `DataClient` shim.
- Task 20 (shadow replay) shows divergence > tolerance → STOP; defer paper migration to M6.5.
- Any task blows past 2-file scope → STOP and re-decompose.

---

## Test files delivered (Phase 3 output)

Written in Phase 3 by an isolated subagent, per `.claude/rules/subagent-patterns.md`:

- `v5/tests/test_m6_clock.py` (T-D6)
- `v5/tests/test_m6_streams.py` (T-D14 DataKind / D17 Venue / D18 Subscription)
- `v5/tests/test_m6_bus.py` (T-D20 determinism)
- `v5/tests/test_m6_fix_docstrings.py` (T-D16)
- `v5/tests/test_m6_instruments.py` (T-D11)
- `v5/tests/test_m6_registry.py` (T-D17 cascade)
- `v5/tests/test_m6_cache.py` (T-D1, D1a, D1b)
- `v5/tests/test_m6_gaps.py` (T-D5, D5a, D5b)
- `v5/tests/test_m6_protocols.py` (T-D3)
- `v5/tests/test_m6_parquet_replay.py` (T-D3 client, T-D15 unsupported ops)
- `v5/tests/test_m6_binance_rest.py` (T-D9 rate-limit, T-D10 time-drift)
- `v5/tests/test_m6_binance_ws.py` (T-D3 multiplex, T-D13 Trade events)
- `v5/tests/test_m6_engine.py` (T-D2 union, T-D4 routing, T-D15 dispatch, T-D17 cascade, T-D18 C3)
- `v5/tests/test_m6_deferred_types.py` (T-D12)
- `v5/tests/test_m6_paper_migration.py` (T-D8 flag=OFF byte-identity + flag=ON 1h proxy parity)
- `v5/tests/test_m6_backtest_parity.py` (flag=ON backtest parity)
- `v5/tests/test_m6_infra_clock_docs.py` (T-D21 docstring grep)
- `v5/tests/shadow_replay_harness.py` (T-D19, blocking test driver)

Total: 18 test files. Target: ≥30 total test functions (brief's Phase 3 Test Plan enumerates 19 ACs; some test files host multiple T-IDs).

All tests MUST FAIL on Phase 3 completion (RED state verified before Phase 4 starts).
