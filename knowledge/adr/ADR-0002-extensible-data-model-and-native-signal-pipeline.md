# ADR-0002 — Extensible Data Model and Native Signal Pipeline

**Status:** accepted (2026-04-21, via M11 scope confirmation)
**Author:** Claude Opus 4.7 (research + synthesis)
**Acceptance:** approved by kinstler.alexander@gmail.com on 2026-04-21 via explicit "option B" confirmation after side-by-side comparison of pragmatic vs clean-slate architectures.
**Related:** Extends [ADR-0001](ADR-0001-unified-event-driven-execution.md). Future sessions must cite both before re-opening either decision; any reversal requires a superseding ADR.

## Context

ADR-0001 established event-driven per-bar dispatch as the single simulation loop shared by backtest and paper. That decision answers *how strategies are invoked*. ADR-0002 answers the adjacent, equally-load-bearing question:

> **How extensible is the engine to future data kinds, strategy shapes, and signal semantics — without another round-trip refactor when the first one arrives?**

Survey of reference 2026 engines (NautilusTrader, QuantConnect LEAN, Zipline, pysystemtrade) identified four patterns in mature systems that our engine lacks as a consequence of incremental AI-authored design under the earlier "design over code" meta-rule:

1. **Data is typically modeled as a polymorphic class hierarchy**, not a closed enum with a string discriminator. NautilusTrader: `Data` base class → `Bar`, `Trade`, `Quote`, `CustomData`. LEAN: `BaseData` inheritance. New data types (order book snapshots, sentiment, on-chain, liquidations) slot in by subclassing — registry routing, cache storage, and validation follow from the class, not from engine branching on a string.
2. **Strategies emit signals/orders/targets in the engine's canonical signal type**, which the order-processing pipeline consumes *directly*. No impedance wrappers between strategy output and order creation.
3. **Cache is polymorphic and typed**: one cache object with typed accessors (`.bars(...)`, `.metric(...)`, `.orderbook(...)`), not parallel per-kind cache instances.
4. **Cross-strategy cadence semantics are explicit**: whenever a portfolio mixes strategies with different native resolutions (e.g., 1h factor + 5m breakout), the clock advances at the GCD of subscribed resolutions and each strategy's per-bar callback is invoked only on its own declared cadence. **Armed entries (price-triggered conditional orders) are engine order-manager state, not strategy callbacks.** A strategy emits a `TokenSignal` with trigger semantics at its main cadence; the engine creates an `Order` / `ArmedEntry` (already defined in `v5/orders.py` per M5) with `trigger_price` + `TimeInForce`; the order manager evaluates pending orders against current tick price on every sub-bar tick and fires or expires as appropriate. Strategies do not subscribe at finer resolution for armed-entry evaluation — the intra-bar trigger firing lives entirely in the engine's pending-Order infrastructure. A strategy subscribes at finer resolution only if it needs fresh factor computation at that cadence.

The initial M11 design honored ADR-0001 but kept enum-plus-discriminator DataKind, a per-bar TokenBarArrays wrapper between signals and order-processing, parallel caches, cached-hourly-plus-reread armed-tokens, and parked cadence as an open question. This delivered ~95% of the event-driven value at ~40% of the theoretically-correct cost — a pragmatic trade. Reviewing against future extensibility needs (orderbook-driven strategies, HFT cadence, multi-venue arbitrage, non-systematic execution overlays, custom alternative data), this is not sufficient.

## Decision

**The engine adopts four extensibility-first architectural patterns alongside ADR-0001's event-driven dispatch:**

### 1. Polymorphic `Data` class hierarchy

The closed `DataKind` enum is replaced with an abstract `Data` base class and concrete subclasses: `BarData`, `TradeData`, `FundingRateData`, `MarkPriceData`, `MetricData`, `OrderBookData`, `CustomData` as the baseline, with room for future subclasses. `DataStream` is typed by the `Data` class the stream carries, not an enum value. Per-class invariants (gap policy, transport constraints, payload shape) move into each subclass. `DataClientRegistry` matches clients against `Data` subclasses. Adding a new data type is a subclass declaration — zero engine code changes elsewhere.

### 2. Native signal consumption

The signal-to-order pipeline consumes the engine's canonical strategy return type (`UniverseSignals`, containing `TokenSignal` per instrument) directly. The order-creation path, exit-check path, 6-clamp sizing pipeline, margin check, and position manager are rewritten to read signals natively. No `TokenBarArrays`-shaped wrappers exist anywhere in the event-driven path. The legacy batch-shape simulator entrypoint (`simulate_portfolio(all_signals, strategy_specs, config)`) is retained for pre-Protocol strategies during the sunset window — that path continues to consume `TokenBarArrays` because its callers *do* pre-bake.

### 3. Unified typed cache

One `MarketDataCache` replaces the per-kind caches (today's `MultiInstrumentCache` and any would-be `MetricCache`). Typed accessors: `.bars(instrument, bar_spec, lookback=None)`, `.metric(metric_id, instrument, lookback=None)`, `.trades(instrument, lookback=None)`, `.quotes(instrument)`, `.orderbook(instrument)`, `.custom(type_name, instrument)`. Storage is keyed by `(InstrumentId, Data subclass, discriminator)` and returns PIT-correct rolling-window views sliced to `[0, current_bar_idx]`. `TokenView` delegates to this cache. New data kinds add one accessor + one storage backend; strategy-facing API is stable.

### 4. Explicit clock/cadence semantics

The engine's simulation loop advances at the greatest-common-divisor resolution of all subscribed `Data` streams across all registered strategies (finest tick). Each strategy's per-bar callback (`generate`) is invoked only on that strategy's declared cadence — derived from `required_data()`'s declared bar specs. The `DataEngine` tracks which bar boundary each strategy is synchronized to and dispatches accordingly. Cross-cadence strategies coexist without ambiguity: a 1h factor strategy and a 5m breakout strategy both run in the same portfolio without either being called off-schedule.

**Armed-entry evaluation is order-manager state, not strategy state.** v5 already has `Order.trigger_price` (FIX StopPx(99)) + `Order.time_in_force` (FIX TimeInForce(59)) + `ArmedEntry` frozen state records (defined in `v5/orders.py` per M5). A strategy emits a `TokenSignal` with trigger semantics at its main cadence; `_process_orders_native` (M11 commit 6) creates an `Order` with `trigger_price` + TIF; the engine's order manager evaluates pending orders against current tick price every sub-bar tick and fires or expires accordingly. Removing the `_cache_armed_levels` pre-baked-array pattern from paper_engine consolidates armed-entry handling to this single correct path — the redundant second implementation goes away, and nothing else needs to replace it. One dispatch model, one order-manager abstraction.

## Consequences

**Positive — extensibility:**
- Adding a new data type (order book snapshots, sentiment, liquidations) is a `Data` subclass + storage backend + one `MarketDataCache` accessor. No engine branching, no enum growth.
- Adding a new strategy style (HFT per-tick, orderbook-reactive, non-systematic execution overlay) does not require refactoring the order-processing pipeline. Each emits `UniverseSignals`; the pipeline consumes them.
- Cross-cadence portfolios are first-class. The engine's cadence model is explicit, not emergent.
- The `Cache` surface is stable. Strategy code insulates from engine internals.

**Positive — correctness:**
- The research-vs-paper drift class is eliminated not just at the dispatch level (ADR-0001) but at the data-shape level. Strategies see `Data` subclass instances in both modes; engine invariants (PIT, monotonicity, lookback windows) live in one place per kind.
- Sub-resolution logic is strategy-owned, not engine-simulated. A trailing-stop-armed-at-1h-trigger is a per-tick callback, not an array index lookup against stale data.

**Costs:**
- Simulator rewrite: `_process_orders`, `_process_exits`, sizing clamp pipeline, margin checks, position-manager entry/close paths all read `UniverseSignals` natively. Estimate ~800–1200 LOC rewritten.
- Data-model migration: every `DataStream(..., data_kind=DataKind.X, ...)` call site updates to the new `Data` subclass type. ~40–60 sites across `v5/` and tests.
- Cache consolidation: `MultiInstrumentCache` refactored into `MarketDataCache`; `TokenView` accessors updated.
- Paper engine armed-tokens refactor: the existing `_cache_armed_levels` pattern (cache hourly signal + re-read intra-bar) is replaced by subscribing strategies at finer resolution and letting `generate()` evaluate per tick. Paper dispatch refactor covers this inline.
- Test migration: ~30 files across dispatch, fixtures, replay parity, data pipeline.
- Time: 4–5 weeks (≈1 month of focused work).

**Explicitly out of scope:**
- Migration of pre-Protocol strategies off the legacy batch-shape simulator entrypoint. Sunset happens as each strategy is rewritten; separate work per strategy.
- Implementation of clients for every `Data` subclass (e.g., `OrderBookData`, `LiquidationData`). The hierarchy is in place; concrete clients land when a strategy actually subscribes to those types.
- Performance tuning beyond basic benchmark regression checks. First cut is correctness.

## Rollback plan

The refactor proceeds as a sequence of ordered commits on a feature branch (`feat/m11-unified-event-loop`):

1. `Data` class hierarchy + `DataStream` migration + validation rewrites
2. `MarketDataCache` consolidation + `TokenView` delegation
3. `ParquetReplayClient` real implementation + `ParquetMetricsReplayClient` new
4. `DataEngine` cadence-aware clock advance
5. Signal-native `_process_orders` / `_process_exits` rewrite
6. Paper engine dispatch refactor (inline `strategy.generate()`; armed-tokens cached pattern deleted)
7. `run_backtest` orchestrator + bridge deletion
8. Test migration + parity tests

Per-commit revertibility: commits 1–4 are infrastructure-only, safe to revert independently. Commits 5–6 are the load-bearing behavioral changes; revert affects parity tests but not production paths (which don't exist yet on the new loop during development). Commit 7 is the cutover; revert restores the old dispatch entry. If revert is needed post-cutover, commits 5–7 roll back together.

## References

**Industry:**
- NautilusTrader data architecture (class hierarchy): https://nautilustrader.io/docs/latest/concepts/architecture/
- QuantConnect LEAN custom data: https://www.quantconnect.com/docs/v2/writing-algorithms/importing-data/key-concepts
- Zipline Pipeline inside event loop: https://stefan-jansen.github.io/machine-learning-for-trading/08_ml4t_workflow/04_ml4t_workflow_with_zipline/

**Concrete references in this codebase (for implementers):**
- Current enum-plus-discriminator pattern: `v5/data/streams.py` `DataKind`, `DataStream.data_kind`
- Signal-shape impedance: `v5/simulator.py::_process_orders` consuming `TokenBarArrays`
- Parallel caches: `v5/data/cache.py::MultiInstrumentCache`
- Armed-tokens cached-hourly pattern: `v5/paper_engine.py::_cache_armed_levels`
- Cadence model absence: `v5/data/engine.py` (tick loop is bar-cadence only)

---

## Addendum (2026-04-22) — Clauses from M11 Commit 8 rework

Added after the Commit-8 rework stages 1a–5 (documented in `.specs/active/m11-unified-event-loop/tasks.md` Rework section). These clauses cement clauses that were implicit in the original ADR but not load-bearing until the rework exposed gaps.

### Clause B1 — Declarative subscription contract

Strategies declare their data dependencies via `required_data()` returning a list of `Subscription` / `DataStream` entries. The orchestrator delivers them; it does not synthesize them on the strategy's behalf and does not mutate the strategy's method table.

> **Orchestrator → strategy is READ-ONLY.** Augmentation (e.g., orchestrator-wide defaults, universe expansion) flows via an `effective_subs_by_sid: Dict[str, List[Subscription]]` dict threaded through the data-plumbing call graph (`_subscribe_strategies_with_default` → `_collect_subscribed_streams` → `_hydrate_cache_up_to_now` → `DataEngine.register_strategy_cadences`) — never via `strat.required_data = lambda ...`.

Strategies that need per-instrument parameterization may declare it in one of three ways:
1. Constructor args: `S513TripleTriggerSwing(tokens=[...])` → `required_data()` returns per-token `BarData` subs.
2. Class-level declarative tuples: `METRIC_IDS = ("binance.open_interest.5m", ...)`, `EXTRA_BAR_RESOLUTIONS_MIN = (240,)`. The orchestrator reads these to synthesize subscriptions for instruments in the active universe. Strategy writes nothing at runtime; contract is static.
3. Default subs (if both above are absent): orchestrator synthesizes a 1h `BarData` sub per instrument as a universally-applicable default.

AC-10 invariant: `strat.required_data =` / `strategy.required_data =` assignment is forbidden in `v5/run_backtest.py` (the orchestrator must not mutate the strategy's method table).

### Clause B2 — Armed-entry firing is engine state

Per move #4, armed-entry intra-bar trigger firing lives in the engine's order manager, not in strategy code. M11 implements this as `v5/simulator.py::_fire_armed_orders_native(sim, ctx, bar_idx)` — the native twin of legacy `_stage1_trigger_armed_orders`. The dispatch order is identical on both paths (ADR-0001 rule A6 parity):

```
_process_exits_native  →  _process_orders_native  →  _fire_armed_orders_native
```

`_fire_armed_orders_native` evaluates every `ARMED` order in `sim.open_orders` against the current bar's OHLC (cache read) and materializes a `Position` onto `sim.position_manager.open_positions` via the order's `sizing_ctx` payload. Trigger semantics: `PRICE_ABOVE` (high≥trigger), `PRICE_BELOW` (low≤trigger), `BAR_CLOSE` (unconditional). No strategy code subscribes to or observes this sweep.

### Clause B3 — Data staleness is a cache concern

Detection of "data feed went cold" belongs in `MarketDataCache` (gap detector on last-bar-timestamp vs clock time). If staleness protection is required for paper-live, it flows as a `DataStalenessEvent` that an opt-in observer converts to `force_close`. The dispatch loop MUST NOT contain a sweep that force-closes positions based on the strategy's signal-emission pattern — a strategy that doesn't emit on a given bar is normal behavior (e.g., a day-boundary strategy emitting once per 24 bars on a 1h cadence), not a data-loss indicator.

> **"Signal absence" ≠ "data absence".** The former is a strategy-intent signal; the latter is a feed-health signal. They have different causes and different correct responses.

AC-10 invariant: no `_handle_disappeared_tokens*` or equivalent signal-set-comparison sweep in the paper tick body.

### Clause B4 — Cache layer is the time authority (for replay)

The `MarketDataCache` holds a reference to the sim clock (`cache._clock`). Any code that needs to know "what time is it at bar N" reads from this attached clock (via `ctx.cache._clock.now_ns()` or equivalent), not from wall-clock (`time.time()`, `datetime.now()`), not from paper's internal `tick_counter`, and not from any strategy-local clock surrogate.

Strategies requiring a timestamp for decisions (session filters, UTC day boundary, funding windows) read through `ctx`:
- Current (M11): via `ctx.cache._clock.now_ns()` — the 4-hop traversal works but is fragile.
- Planned (M12 ergonomic follow-up per ADR-0001 rule A1): `generate(ctx, bar_idx, bar_close_ns)` signature change + `BaseStrategy.is_cadence_boundary(bar_idx, cadence_hours)` helper eliminate the need for timestamp reads in most common cases.

AC-10 invariants: (a) no unconditional wall-clock reads (`time.time()`, `time.time_ns()`, `time.strftime()`) in `_tick_internal_body` / `_build_ctx_for_tick` / `_dispatch_strategies_at_tick` replay-dispatch bodies; (b) on every `generate()` invocation in replay, `arg_bar_idx == ctx.cache._clock.current_bar_idx`.

### Clause B5 — Legacy path retention policy

ADR-0002 removes the pre-baked bar-array pipeline from the event-driven path but retains the legacy positional `simulate_portfolio(precomputed_signals, strategy_specs, config)` branch in `v5/simulator.py` for in-tree clients that have not yet migrated. M11 Commit 8 Task 8.4 ("Delete `_get_bar_data`") was DEFERRED because `_get_bar_data` has 10 consumers inside the retained legacy branch (`_process_exits`, `_process_orders`, `_close_all_remaining`, etc.). This is not a violation — it is the correct scope boundary per ADR-0002 move #2 (the move applies to the new event-driven path; legacy is retained as-is until its consumers migrate).

`TokenBarArrays` (class in `v5/signals.py`) similarly remains for validation (`v5/validation.py`), reporting (`v5/report.py`), and legacy exit handlers (`v5/exit_handlers.py`). Migration of these consumers is out-of-scope for M11; they will be addressed when their respective milestones (M12+) port them.

### Clause B6 — State persistence asymmetry is a smell

Per ADR-0001 rule A6, any persistence, counter, or state field that exists on only one transport (paper vs backtest) is a parity-breaking relic candidate. M11 Stage 1b removed `last_known_prices`, `armed_tokens`, `filled_4h_windows`, `last_known_regimes` from `atomic_write_state` / `serialize_state` / `serialize_engine_state` because:

1. The M11 backtest orchestrator has no per-engine state persistence (returns `SimulationState` to caller).
2. Paper's equivalent persistence of those fields was pre-M5 scaffolding — superseded by M5's `Order`-based armed-entry state (captured in `SimulationState.pending_orders`), M11's GCD cadence dispatch (replacing `filled_4h_windows` TTL gating), and M11's `MarketDataCache` (replacing `last_known_prices` reconstruction on resume).

Future milestones should apply the same audit whenever a paper-only / backtest-only state field is identified: if the asymmetry has no quant justification, it is a deletion candidate.

---

## Historical reference — what each rework stage clarified

| Stage | Cleared ambiguity in ADR-0002 via | Clause |
|---|---|---|
| 1a | Dispatch wiring must actually invoke `strategy.generate()` — not via module-reflection + no-args-construction when the strategy carries ctor args; the orchestrator's side-channel `spec.strategy_instance` is authoritative | (Clarifies move #1 mechanics) |
| 1b | Per-engine state persistence fields must be symmetric across transports, or deleted | B6 |
| 2a | Indicator data flows through `MarketDataCache` + `MetricsManifest`; strategy computes derived features inline in `generate()` from raw metric subs | Move #2 + B1 (declarative contract) |
| 3 | Cache layer is time authority in replay; no parallel clock counters may substitute | B4 |
| 4 | Data staleness vs signal absence distinction; Protocol method tables are read-only from orchestrator | B1 + B3 |
| 5 | Every timestamp emitted in replay paths derives from sim clock | B4 |
