# ADR-0001 — Event-Driven Execution as the Single Simulation Loop

**Status:** accepted (2026-04-21, via M11 brief review gate)
**Author:** Claude Opus 4.7 (research + synthesis)
**Acceptance:** approved by kinstler.alexander@gmail.com on 2026-04-21 via the M11 brief review gate. Future sessions must cite this ADR before re-opening the dispatch decision; any reversal requires a superseding ADR.
**Supersedes:** the implicit dual-dispatch model that emerged across earlier milestones without explicit architectural review

## Context

### The quant question

A signal-generating strategy produces, at each time step, **per-instrument trading intent** — a target position, direction, weight, or a score that a sizer converts into a position. The architectural question is:

> **How does the engine obtain the strategy's trading intent at each time step, and when does it ask?**

Two canonical patterns in the literature:

- **Event-driven.** At each bar/tick the engine invokes the strategy's callback with current observable state. The strategy returns trading intent per instrument. The engine converts intent into orders, routes fills, updates positions. Paper and backtest differ only in (a) how time advances — wall-clock vs programmatic — and (b) where data comes from — live feed vs historical replay. Strategy code is identical across modes.
- **Batch precompute.** For pure cross-sectional factor models with no state dependence, the full-length per-instrument alpha array is computed once up front from historical data and then walked in the simulation. Valid for offline factor research. Cannot represent trailing stops, position-aware sizing, cross-instrument rebalancing, or any decision that depends on intra-simulation state.

### Current state of our engine

The engine currently mixes both patterns, accidentally:

- **Backtest path** calls the strategy's per-bar callback *upfront in a setup loop* — all bars, before simulation starts — bakes the output into per-instrument arrays, then walks the arrays in the simulation inner loop. This is batch precompute dressed as event-driven.
- **Paper path** never calls the strategy's per-bar callback. It walks pre-baked arrays populated by a separate precompute pass; per-tick logic only inspects those arrays.
- The per-bar callback exists on the Strategy Protocol and is *defined* as per-bar, but **nothing calls it per-bar** today.

### How we got here

Forensic audit of the engine's brief history (2026-04-21) found the dual model is **accidental, not deliberate**:

- The Strategy Protocol's per-bar callback was introduced in an earlier milestone without documenting dispatch semantics.
- The precompute fallback appears in the simulator with no brief sanction — it emerged as an implementation detail during a subsequent milestone's bridge wiring.
- An earlier brief claimed paper and backtest share a unified dispatcher, but the paper engine never calls the per-bar callback. That parity claim is violated.
- No brief ever stated "backtest = paper in replay mode."
- The rebuild was conducted under a "design over code" meta-rule that explicitly allowed AI-authored architectural trade-offs without intermediate human sign-off.

### Industry evidence (2026)

Survey of mature systematic trading engines and the academic literature (López de Prado, Hilpisch, Chan, Carver):

| Engine | Dispatch | Research-production parity claim |
|---|---|---|
| NautilusTrader | Event-driven, single Rust loop | Explicit: "same architecture, execution semantics, time model across both environments" |
| QuantConnect LEAN | Event-driven `OnData` | Explicit: "seamlessly works in backtests and live trading with no code changes" |
| Zipline | Event-driven `handle_data`; Pipeline API is vectorized factor compute **inside** the event loop | Explicit |
| Backtrader | Event-driven | Explicit |
| pysystemtrade (Carver) | Event-driven | Same engine for research and live book |
| vectorbt PRO | Added Numba-compiled sequential callbacks specifically because trailing stops / path-dependence cannot be vectorized | Documents the limit |

Consensus: **event-driven is the canonical architecture; vectorized precompute is a factor-screening layer inside the event loop, never replacing it.** The pattern of "paper looks up pre-baked values at the live bar index" is a documented code smell — any cache gap, late tick, warmup-window mismatch silently desyncs the pre-baked array from what live invocation would have produced. This is the research-vs-paper drift class that NautilusTrader and LEAN were explicitly built to eliminate.

## Decision

**The engine adopts event-driven dispatch as the single simulation loop, shared by backtest and paper (and live).**

Concretely:

1. **The strategy's per-bar callback is the canonical dispatch primitive.** The engine invokes it inline, per bar in backtest and per tick/bar in paper, with the current observable state. What the callback returns — per-instrument trading intent in whatever shape the Strategy Protocol defines — is unchanged by this decision.
2. **One simulation loop, parameterized by transport only.** The same loop runs in backtest and paper. The only differences are:
   - *Clock* — programmatic iteration through a historical window vs wall-clock tick events
   - *Data transport* — historical replay client vs live streaming client
   - Both are already first-class concepts in the data layer; no new abstractions required.
3. **Vectorized precompute survives as an opt-in optimization** for strategies whose signals are pure cross-sectional factors with no path-dependence — executed inside the event loop on a rolling-window-update cadence (Pipeline-style). Portfolio state, stops, sizing, rebalancing always see per-bar state.
4. **The precompute-fallback dispatch path is deleted** as the primary mechanism. The batch-shape simulation entrypoint that consumes externally-precomputed signals is retained for backwards compatibility with strategies authored against the legacy contract; sunset as those strategies migrate to the Protocol.
5. **Data reaches strategies through one contract.** The strategy declares its data dependencies; the engine delivers them through registered clients appropriate to the transport. Strategies do not read files, nor reach into transport-specific cache internals.

## Consequences

**Positive:**
- Research-production parity becomes a structural property, not an after-the-fact tested claim. Paper and backtest invoke literally the same callback on literally the same state.
- Path-dependent strategies — trailing stops, position-aware sizing, cross-instrument rebalancing — become naturally expressible. Today they cannot be represented in the precompute model without hacks.
- Adding a new data source is a registry/manifest operation, not a bespoke integration.
- The architecture aligns with the 2026 industry consensus. Future contributors (human or AI) have a named reference model and a decision document to point at.

**Costs:**
- The live execution runtime is refactored: its tick handler calls the strategy's per-bar callback inline instead of walking pre-baked arrays. High blast radius.
- Tests that depend on the pre-baked-array dispatch break; they migrate to the event-driven loop.
- The simulation entrypoint loses its "bundle" shape kwargs (the bridge); callers are migrated to the new orchestrator.
- Per-bar dispatch is slower than array-walking for pure cross-sectional factor strategies. Mitigation: vectorized precompute survives as a Pipeline-style optimization inside the event loop.

**Explicitly out of scope for this decision:**
- Migration of strategies authored against the pre-Protocol contract off the legacy batch-shape entrypoint. That happens as individual strategies are rewritten.
- Promotion of specific metric kinds to FIX-standard `MDEntryType` values. Adding a generic vendor-extension kind with a `metric_id` discriminator is sufficient.

## Rollback plan

The change is introduced as a single atomic commit. If regressions emerge, the commit is reverted; the precompute-fallback dispatch is restored from git history; this ADR is updated to record the reversion reason. Paper and backtest return to their pre-decision dispatch paths.

## References

**Industry:**
- NautilusTrader architecture: https://nautilustrader.io/docs/latest/concepts/architecture/
- QuantConnect LEAN algorithm engine: https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine
- Zipline (ML4T workflow): https://stefan-jansen.github.io/machine-learning-for-trading/08_ml4t_workflow/04_ml4t_workflow_with_zipline/
- vectorbt PRO portfolio callbacks: https://vectorbt.pro/features/portfolio/
- QuantStart event-driven backtesting: https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-I/
- López de Prado — Backtesting (SSRN): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2606462
- QuantConnect forum — backtest-vs-live indicator drift: https://www.quantconnect.com/forum/discussion/20251/

**Concrete references in this codebase (for implementers, not reasoning):**
- `v5/simulator.py::_engine_precompute_fallback` — the undocumented backtest dispatch to be removed
- `v5/paper_engine.py::PaperPortfolioEngine._tick_internal_async` — the paper dispatch path that currently does not invoke the strategy callback
- `v5/strategy_api.py::Strategy.generate` — the Protocol method defined as per-bar but not currently invoked that way
- `v5/data/engine.py::DataEngine.subscribe_all` — the existing data-subscription entry point that the single data-delivery contract builds on

---

## Addendum (2026-04-22) — Bar-Close Convention & Time-Authority Rules

Added after M11 Commit 8 Stage-3 clock-unification rework. These rules are binding for any path that participates in the unified event loop (backtest or paper-replay).

### Rule A1 — Bar-index ↔ timestamp invariant

For a fixed `start_ns` and cadence `cadence_ns` (the GCD of all subscribed cadences):

> **`bar_idx = N` ⇔ `clock.now_ns() == start_ns + N × cadence_ns` ⇔ bar N has just closed and its data is in the cache.**

Strategy code invoked via `generate(ctx, bar_idx)` sees the cache populated through bar N inclusive. The decision is made *at bar close*, not at bar open. This matches close-labeled bar convention used by the overwhelming majority of professional trading systems and avoids look-ahead via an open-labeled convention.

### Rule A2 — Single clock authority in replay

In any replay-mode loop (`run_backtest`, `drive_paper_in_replay`):

> **`SimulationClock` is the ONLY source of truth for time.** No `time.time()` / `time.time_ns()` / `datetime.now()` / `datetime.utcnow()` reads after initialization. No parallel bar-index counters (`tick_counter`, `_last_tick_ts`, etc.) may substitute for `clock.current_bar_idx` when dispatching strategies or resolving bar boundaries.

Paper engine's internal `self.tick_counter` remains legitimate for state-persistence bookkeeping (counting state.json commits), but MUST NOT be read as bar_idx when `self._clock` (the shared `SimulationClock`) is attached. Only `engine.advance_one_tick()` mutates simulation time; no other code may assign to `clock._now_ns` or `clock.current_bar_idx`.

AC-10 invariants enforce: (a) no wall-clock reads in `_tick_internal_body` / `_build_ctx_for_tick` / `_dispatch_strategies_at_tick`; (b) `arg_bar_idx == clock.current_bar_idx` on every `generate()` invocation in replay.

### Rule A3 — Protocol read-only from the orchestrator

The orchestrator (`run_backtest`, `drive_paper_in_replay`) MUST NOT mutate a strategy's method table or attributes. Monkey-patching `strat.required_data = ...` (historic pattern in `_subscribe_strategies_with_default`) is forbidden; augmenting subscriptions is done via a parallel `effective_subs_by_sid` dict threaded through `_collect_subscribed_streams` / `register_strategy_cadences` / `_hydrate_cache_up_to_now`.

If a strategy needs per-instrument parameterization, it declares it via constructor args or class-level declarative attributes (e.g., `METRIC_IDS`, `EXTRA_BAR_RESOLUTIONS_MIN`). The orchestrator reads those, it does not write them.

AC-10 invariant enforces: no assignment to `strat.required_data` / `strategy.required_data` in `run_backtest.py`.

### Rule A4 — Validators must propagate

Fail-fast validators (e.g., `_validate_all_strategies_are_protocol`) exist to surface contract violations synchronously. Wrapping a dispatch call in `try: tick_fn() except Exception: log.warning(...)` silently converts validator errors into warnings that hide parity bugs (M11 Stage 1 incident: paper's strategy dispatch was silently failing for weeks; AC-2 parity was vacuously green because the swallow hid it).

> **Exceptions from the dispatch path propagate verbatim in replay.** Defensive catches are permitted only for specific, narrow exception classes with explicit rationale, never bare `except Exception:`.

### Rule A5 — Data staleness is a cache concern

Under ADR-0002's polymorphic `Data` hierarchy, data freshness is a property of the `MarketDataCache` (last bar timestamp vs clock time per instrument), not of strategy signal emission. "Drive-via-absence" logic — force-closing positions because the strategy did not emit a signal this tick — is banned. A strategy that doesn't signal is normal behavior (e.g., a 30-day-hold momentum strategy emits once every 30 days).

If staleness protection is required (paper-live only), it belongs in a cache-level gap detector firing a `DataStalenessEvent` that an opt-in observer converts to `force_close`. The dispatch loop must not contain such sweeps.

AC-10 invariant enforces: no `_handle_disappeared_tokens*` or equivalent sweep in the paper dispatch body.

### Rule A6 — Same-loop parity is structural, not statistical

"Same loop, parameterized by transport" means: given identical strategy instance, instruments, data, and clock, both `run_backtest` and `drive_paper_in_replay` MUST produce byte-identical `SimulationState.closed_trades` (pnl within `1e-6` per AC-2). Any asymmetric code path — a handler called on only one side, a sweep only one path runs, a state field only one path maintains — is a parity bug and a candidate for deletion or symmetrization.

---

## Historical reference — rework sequence that produced these rules

1. **Stage 1a (wiring)** — fixed `_resolve_protocol_strategy` to honor `spec.strategy_instance`; removed bare-except swallow in `drive_paper_in_replay`. (Rule A4)
2. **Stage 1b (kwarg cleanup)** — deleted `last_known_regimes`/`armed_tokens`/`filled_4h_windows`/`last_known_prices` persistence kwargs; these were pre-M11 scaffolding for legacy paper state, asymmetric with the backtest orchestrator which has no per-engine persistence. (Rule A6)
3. **Stage 2a (s524m metrics port)** — s524m `required_data()` now declares `MetricData` subscriptions via M11's `MetricsManifest` infra end-to-end; composite_zscore computed inline from raw metrics in `generate()`.
4. **Stage 3 (clock unification)** — `_tick_internal_body` reads `bar_idx` from the shared `SimulationClock.current_bar_idx`, not from `self.tick_counter`; `_PaperTickClock` wall-clock fallback deleted. (Rules A1, A2)
5. **Stage 4 (legacy cleanup)** — deleted `_handle_disappeared_tokens*` (Rule A5); gated `_purge_expired_armed_orders` wall-clock read on `self._clock is None` (Rule A2); removed `strat.required_data` monkey-patch, added `effective_subs_by_sid` threading through `_subscribe_strategies_with_default` / `register_strategy_cadences` / `_hydrate_cache_up_to_now` (Rule A3); added 3 AC-10 invariants (wall-clock absence, Protocol immutability, disappeared-tokens deletion).

Per-trade parity on the 60-day BTC/ETH AC-2 fixture: **12/12 match with 0 mismatches** after Stage 4 + hydration plumbing fix.
