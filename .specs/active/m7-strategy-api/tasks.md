# M7 Task Decomposition

**Phase**: 3 (Decompose + Tests)
**Waves**: A (primitives) → B (loader + WF) → C (signals + order factory) → D (execution wiring) → E (paper migration) → F (reference strategies) → G (hard merge gate)
**Task count**: 25 tasks (was 23; +Task 24 regimes.py per AC-Reg2 reviewer H6 gap; +Task 25 post-fill validation split from arm_bracket per user feedback). Each task touches 1-2 files. Independent tasks marked `[P]`.

Legend: `[P]` = parallel-safe; `(after: N, M)` = blocked by tasks N and M.

---

## Wave A — Primitives (foundation)

### Task 1 [P] — strategy_api.py
- **Files**: `v5/strategy_api.py` (new, ≤300 LOC)
- **Delivers**: `Strategy` Protocol `@runtime_checkable` with 15 callbacks (lifecycle, data decl, signal gen, per-position checks, 7 exec event hooks, 3 position lifecycle, view_state). `BaseStrategy` with no-op defaults. `UniverseSignals` / `TokenSignal` dataclasses. `ScaleAction` / `ExitCheck` types.
- **Acceptance**: AC-S1 (full Protocol), AC-S5 (error containment contract).
- **Tests**: `test_m7_strategy_protocol.py`.

### Task 2 [P] — fill.py + ExecType enum
- **Files**: `v5/fill.py` (new, ≤50 LOC), `v5/orders.py` (extend — add `ExecType` enum)
- **Delivers**: `ExecType` enum (10 FIX states: NEW, PARTIAL_FILL, FILL, CANCELED, REJECTED, TRIGGERED, EXPIRED, TRADE_CANCEL, TRADE_CORRECT, ORDER_STATUS). `Fill` dataclass with FIX triple (ClOrdID + OrderID + ExecID) + TransactTime + qty/px triple.
- **Acceptance**: AC-O5 (Fill carries FIX triple + ExecType).
- **Tests**: `test_m7_fill_triple.py`.

### Task 3 [P] — universe_context.py
- **Files**: `v5/universe_context.py` (new, ≤300 LOC)
- **Delivers**: `UniverseContext` frozen dataclass + `DataView`, `PortfolioView`, `OrderFactoryView` namespace views per G7 decision. Arrays returned via `ctx.data.per_token(...)` are read-only (`setflags(write=False)`). `ctx.mutable_copy(arr)` opt-in. `ctx.fold_id`, `ctx.fold_window` exposed.
- **Acceptance**: AC-S2 (UniverseContext + read-only isolation), AC-S4 (tokens bar-relative).
- **Tests**: `test_m7_universe_context.py`.

### Task 4 [P] — indicators.py
- **Files**: `v5/indicators.py` (new, ≤200 LOC)
- **Delivers**: `IndicatorCache` with pull-based memoization. Cache key `(token, fn.__qualname__, frozen_params_tuple, bar_idx)`. Rejects lambdas/closures at registration. Stdlib path: scalar-only params. Custom path: requires `.cache_key()` method + defensive `hash(result)` catches non-hashable returns. `clear()` on Strategy.on_reset().
- **Acceptance**: AC-S3.
- **Tests**: `test_m7_indicators.py`.

### Task 5 — AC-H1 quick wins (after: none — independent)
- **Files**: `v5/tests/test_m1_fork_verification.py` (modify constant + regex), `v5/simulator.py` or `v5/signals.py` (remove stale comments)
- **Delivers**: (a) update `test_ac01_v5_file_count` expected count from 41 → 56; (b) remove stale `size_multiplier` comment at `v5/signals.py:544`; (c) remove stale `exit_resolution` comment at config reference; (d) export `_process_entries` from simulator (decide rename to public or keep `_`-prefix with test-only import allowance — blast-radius-bounded).
- **Acceptance**: partial AC-H1 (rows #1-3, #5, #7, #8, #11, #12).
- **Tests**: 8 of 13 pre-existing failures flip GREEN.

---

## Wave B — Loader + Walk-Forward (after Wave A Task 1)

### Task 6 — strategy_loader.py (after: 1)
- **Files**: `v5/strategy_loader.py` (new, ≤500 LOC)
- **Delivers**: Replaces v4/engine.py::_load_strategy_fn. AST scan with `_BANNED_ATTRIBUTES` + `_BANNED_BARE_NAMES` + `_WARN_ATTRIBUTES` per §2.5. Detects attribute-access AND bare-import forms. Rejects at `__init__` (not first tick). Module-level mutable state WARN-only (preserves s523c_growth.TOKEN_BLACKLIST read-only).
- **Acceptance**: AC-V2 (AST scan).
- **Tests**: `test_m7_ast_scan.py` — parametrized over all banned calls, both forms.

### Task 7 [P] — validation.py WalkForwardRunner (after: 1, 3)
- **Files**: `v5/validation.py` (modify)
- **Delivers**: `WalkForwardRunner.run_splits(strategy_cls, splits, universe, config)` — fresh `Strategy` instance per fold. `ctx.fold_id` / `ctx.fold_window` populated. Signals NEVER see WF masks (already M1-clean; just preserves).
- **Acceptance**: AC-V1.
- **Tests**: `test_m7_validation_wf.py`.

---

## Wave C — Signals + Order Factory (after Wave A + B)

### Task 8 — Unified signals.py (after: 1, 6)
- **Files**: `v5/signals.py` (modify) + delete `v4/portfolio_signals.py`
- **Delivers**: Single signal-dispatch path. `strategy_type` field removed from `StrategySpec`. `TokenSignal` replaces `TokenSignals` + `PortfolioSignals`. Engine no longer branches on `strategy_type`. Legacy v4-shape strategies migrated to the unified Protocol as part of Wave F (Tasks 17-19).
- **Acceptance**: AC-S6 (unified signals.py).
- **Tests**: `test_m7_signals_unified.py` — asserts portfolio_signals.py is gone + no `strategy_type` branches.

### Task 9 [P] — OrderFactoryView arm + arm_bracket (after: 2, 3)
- **Files**: `v5/universe_context.py` (extend — implement `OrderFactoryView`)
- **Delivers**: `arm(symbol, direction, size, trigger, trigger_price, **kwargs) -> Order` single-leg. `arm_bracket(entry_spec, sl_spec, tp_spec) -> Order` three-leg with OTO_BRACKET + OTO contingency. Validation at factory time: SL/TP side (defers to fill for MARKET entries per FIX M1), size parity, trigger-type validity.
- **Acceptance**: AC-O1.
- **Tests**: `test_m7_arm_bracket.py`.

### Task 10 [P] — FIX wire serializers (after: 2)
- **Files**: `v5/orders.py` (extend)
- **Delivers**: `OrderStatus.to_fix_ordstatus()`, `TriggerType.to_fix_trigger_type()`, `TriggerType.to_fix_trigger_price_direction()`, `LegStatus.to_fix_ordstatus()`. ARMED/TRIGGERED/RELEASED → OrdStatus=A + custom tag 9001/9002/9003 per G8.
- **Acceptance**: AC-O2.
- **Tests**: `test_m7_fix_wire.py`.

---

## Wave D — Execution Event Wiring (after Wave C)

### Task 11 — BarProcessor 15-callback dispatch (after: 1, 9)
- **Files**: `v5/bar_processor.py` (modify — extend Phase 2/3 handlers)
- **Delivers**: All 15 callbacks fire at correct phase. Callback dispatch order per §2.6 (bracket-entry, SL-hit, reject-unwind cascades). Per-method error containment per §2.7 (fail-fast on_start/on_reset; EMPTY_SIGNALS on generate; None on check_*; True on filter_entry). `strategy.exception_counter`. `StrategyQuarantined` event on threshold. check_exit runs BEFORE StopLossHandler per §3a.
- **Acceptance**: AC-S5 (error containment), AC-S11 (check_scale + check_exit), AC-Lifecycle (all 15 fire).
- **Tests**: `test_m7_lifecycle_hooks.py`.

### Task 12 [P] — `_order_reject_event` post-fill unwind (after: 2, 10)
- **Files**: `v5/orders.py` or `v5/simulator.py` (extend)
- **Delivers**: Venue REJECTED exec on a LEG whose sibling already FILLED → `LegFillPolicy.UNWIND_ON_REJECT` cascade. Engine publishes reversal order. `Strategy.on_order_rejected(order, reason)` fires with `reason.startswith("post_fill_unwind:")`.
- **Acceptance**: AC-O3.
- **Tests**: `test_m7_reject_unwind.py`.

### Task 13 [P] — Order.venue_order_id population (after: 2, 10)
- **Files**: `v5/orders.py` (extend — make `Order.venue_order_id` mutable after arm) + `v5/data/clients/binance_ws.py` + `v5/data/clients/binance_rest.py` (extend with `on_venue_ack` public method)
- **Delivers**: Order gains `venue_order_id: str | None` attribute mutable via `order.venue_order_id = ...` (not via `replace()` — this is a post-arm ack-time mutation, not a state-machine transition). Venue-ack in live mode extracts FIX OrderID(37) via `on_venue_ack(event, order)` on the M6 client classes and writes to the order BEFORE `on_order_accepted` fires. Paper/backtest synthesizes deterministic `f"paper-{runner_instance_id}-{order_id:08x}"` (addresses FIX-L1 collision risk per reviewer H3). Hidden dep on Task 10 (FIX wire) since serializer methods consume venue_order_id.
- **Pinned implementation** (design §2.8 ID1): use `object.__setattr__(order, "venue_order_id", value)` at the ack-time write site. Do NOT break M5's `frozen=True`. Do NOT use a side-table. See design for example.
- **Acceptance**: AC-O4.
- **Tests**: `test_m7_venue_order_id.py`.

---

## Wave E — Paper Engine Migration (Task 17 equivalent, after Wave D)

### Task 14 — PaperEngine 8-site dispatch (after: 11)
- **Files**: `v5/paper_engine.py` (modify — 8 sites)
- **Delivers**: Each of 8 legacy sites gains `if self._use_data_engine:` branch. Flag=OFF byte-identical. Flag=ON routes through M6 DataEngine. Budget: 14h nominal. No shim fallback — the extraction ships complete.
- **Acceptance**: AC-P1.
- **Tests**: `test_m7_paper_8site.py`.

### Task 15 — Runner swap v4→v5 (after: 14)
- **Files**: `v5/run_paper_multi.py` (modify — one-shot startup migration script) + `tools/start_all_services.sh` (modify — invocation path) + `configs/runner_pool_config.json` (modify — state_dir)
- **Delivers**: Startup migration script SIGTERMs old PID (if alive), COPIES state from `state/v4_paper_multi/` to `state/v5_paper_multi/` (preserves v4 dir for revert). AC-P2 tests verify migration + revert paths.
- **Acceptance**: AC-P2.
- **Tests**: `test_m7_runner_swap.py`.

### Task 16 [P] — VenueCapabilities.supported_transport_modes (after: none — small)
- **Files**: `v5/data/instruments.py` (extend)
- **Delivers**: Additive field `supported_transport_modes: frozenset[TransportMode]` + `supports(TransportMode)` method for strategy-facing symmetry. Populated per-venue at connect().
- **Acceptance**: AC-D15.
- **Tests**: `test_m7_transport_modes.py` (new M7-owned file; does NOT modify M6-frozen test_m6_instruments.py per reviewer H5 fix).

---

## Wave F — Reference Strategy Migration (after Wave D; E can proceed in parallel)

### Task 17 [P] — s513 port (after: 1, 3, 8)
- **Files**: `strategies/s513_*.py` → `strategies/s513_v5.py` (new reference version)
- **Delivers**: s513 rewritten as v5 Strategy subclass. `required_data()` declares subscriptions. `generate()` uses ctx.data indicators. 2h estimated.
- **Acceptance**: reference migration (no v4 parity required).
- **Tests**: smoke test that strategy instantiates + generates signals without error.

### Task 18 [P] — s523c port (after: 1, 3, 8)
- **Files**: `strategies/s523c_v5.py` (new)
- **Delivers**: s523c rewritten. Conviction→priority split applied. 3h estimated.
- **Acceptance**: reference migration.
- **Tests**: smoke test.

### Task 19 — s524m port + parity (after: 1, 3, 7, 8)
- **Files**: `strategies/s524m_v5.py` (new)
- **Delivers**: s524m rewritten WITHOUT module-level mutable state (resolves the `_composite_cache`/`_aligned_cache`/`_daily_loaded`/`_last_load_date` violations). All state on self. Priority split. Pre-indexed leverage/size_multiplier into SizingRequest stub. 5h estimated.
- **Acceptance**: AC-S10 — produces metrics within 0.5% of v4/s524m over Q-DEC4 validation period.
- **Tests**: `test_m7_s524m_parity.py`.

---

## Wave G — Hard Merge Gate (after Wave E + F)

### Task 20 — WS --strict pre-flight (after: none — small early prep)
- **Files**: `v5/tools/record_ws_tap.py` (modify — add --strict flag)
- **Delivers**: `--strict` flag causes recorder to error-out on REST fallback. Short 60s pre-flight test runs `record_ws_tap.py --mode ws --duration 60 --strict` and asserts real WS frames captured. Addresses FIX reviewer M4.
- **Acceptance**: pre-flight for AC-P3.
- **Tests**: `test_m7_ws_strict_preflight.py`.

### Task 21 — 24h recording + AC-P3 shadow replay (after: 14, 15, 20)
- **Files**: operational (run `v5/tools/record_ws_tap.py --mode ws --duration 86400 --strict` → write fixture) + `v5/tests/shadow_replay_24h.py` (new test)
- **Delivers**: Real 24h live WS+REST recording. The recorder is SHIPPED — M6 already proved it with a real 5-min Binance WS recording (commit `c429149`, 8,494 live frames). Running at 86400s instead of 300s is just wall-clock time; no new code. `run_shadow_replay(fixture, duration_hours=24)` returns zero-divergence report.
- **Acceptance**: AC-P3.
- **Tests**: `shadow_replay_24h.py`.

---

## Wave A+ (extension, can start anytime)

### Task 22 — AC-H1 row #13 closed_trades deque/list decision (after: 5)
- **Files**: `v5/position.py` (modify) or `v5/tests/test_paper_state.py` (modify, per decision)
- **Delivers**: Resolve deque vs list. Check all downstream consumers (blast radius rule: 21 position.py importers). File AIPIP if downstream cascade exceeds 2h. 2-3h estimated.
- **Acceptance**: completes AC-H1.
- **Tests**: `test_paper_state::test_closed_trades_empty_after_deserialize` flips GREEN.

### Task 23 [P] — strategy.exception_counter + StrategyQuarantined event (after: 11)
- **Files**: `v5/strategy_api.py` (extend — BaseStrategy gains counter) + `v5/bar_processor.py` (extend — publishes event)
- **Delivers**: `strategy.exception_counter` int field. Threshold-based `StrategyQuarantined` event on MessageBus. `view_state()` default includes counter.
- **Acceptance**: AC-S5 observability per §2.7.
- **Tests**: `test_m7_strategy_quarantine.py`.

### Task 24 [P] — v5/regimes.py per-bar memoized utility (after: 3)
- **Files**: `v5/regimes.py` (new, ≤150 LOC)
- **Delivers**: `detect_regime(ctx, bar_idx)` canonical detector. Cache keyed by `(id(ctx), bar_idx)` → shared across strategies on same UniverseContext. Cache cleared on fresh context (WF fold boundary). `_regime_call_count(ctx)` helper for test observability.
- **Acceptance**: AC-Reg2 (previously had no task — reviewer H6 fix).
- **Tests**: `test_m7_regimes.py`.

### Task 25 [P] — Post-fill Order-level validation (after: 2, 9)
- **Files**: `v5/orders.py` or `v5/simulator.py` (extend — `_order_reject_event` post-fill re-validation hook)
- **Delivers**: When a MARKET entry fills on a multi-leg Order whose sibling SL/TP trigger_price is mis-sided vs fill price, engine publishes a reject event. Applies to ALL multi-leg Orders with MARKET entries — not just arm_bracket-constructed ones (per user feedback on bracket-factory scope).
- **Acceptance**: design §2.2 lines 207-213 (Order-level, not bracket-level).
- **Tests**: `test_m7_post_fill_validation.py` (new file, separated from test_m7_arm_bracket.py per user feedback).

---

## Dependency graph (critical path)

```
Wave A: [1] [2] [3] [4] [5]   (all parallel; no cross-blocks)
Wave B: [1] → [6]; [1, 3] → [7]
Wave C: [1, 6] → [8]; [2, 3] → [9]; [2] → [10]
Wave D: [1, 9] → [11]; [2, 10] → [12]; [2] → [13]
Wave E: [11] → [14] → [15]; [16] [P]
Wave F: [1, 3, 8] → [17] [18]; [1, 3, 7, 8] → [19]
Wave G: [14, 15, 20] → [21]; [20] [P]
Wave A+: [5] → [22]; [11] → [23] [P]
```

Critical path: 1 → 6 → 8 → 9 → 11 → 14 → 15 → 21. Raw sum = 51h (1 + 4 + 8 + 4 + 10 + 14 + 6 + 4); 60-75h band accounts for parallel-wait slack. Parallel tracks (Tasks 2/3/4/5/7/10/12/13/16/17/18/20/22/23/24/25) absorb remainder toward 100-140h total.

**Budget update post final review**: Task 24 (regimes.py, ~3h) + Task 25 (post-fill validation, ~4h) add 7h raw, pushing total to ~137h. Still inside the 100-140h brief band. No budget increase required.

---

## Stop-redecompose triggers (recap from design §10)

- Task 14 PaperEngine extraction — complete the real work; no shim fallback.
- Task 21 24h shadow replay divergence >0 → STOP; do NOT flip use_data_engine=True in prod. File root-cause investigation.
- Task 22 AC-H1 row #13 cascades beyond 2h → file AIPIP, defer to M8.
- Any task exceeds 2-file scope → STOP and re-decompose per development-workflow.md.

---

## Test files delivered (Phase 3 output)

Written in Phase 3 by an isolated subagent, per `.claude/rules/subagent-patterns.md`:

- `v5/tests/test_m7_strategy_protocol.py` — AC-S1
- `v5/tests/test_m7_universe_context.py` — AC-S2, AC-S4
- `v5/tests/test_m7_indicators.py` — AC-S3
- `v5/tests/test_m7_signals_unified.py` — AC-S6
- `v5/tests/test_m7_validation_wf.py` — AC-V1
- `v5/tests/test_m7_ast_scan.py` — AC-V2
- `v5/tests/test_m7_arm_bracket.py` — AC-O1
- `v5/tests/test_m7_fix_wire.py` — AC-O2
- `v5/tests/test_m7_reject_unwind.py` — AC-O3
- `v5/tests/test_m7_venue_order_id.py` — AC-O4
- `v5/tests/test_m7_fill_triple.py` — AC-O5
- `v5/tests/test_m7_paper_8site.py` — AC-P1
- `v5/tests/test_m7_runner_swap.py` — AC-P2
- `v5/tests/test_m7_lifecycle_hooks.py` — all 15 callbacks + AC-S5 + AC-S11
- `v5/tests/test_m7_s524m_parity.py` — AC-S10
- `v5/tests/test_m7_pre_existing_13.py` — AC-H1 (13 cleared)
- `v5/tests/test_m7_ws_strict_preflight.py` — Wave G step 20
- `v5/tests/test_m7_strategy_quarantine.py` — AC-S5 observability
- `v5/tests/shadow_replay_24h.py` — AC-P3 (run against real 24h recording)
- `v5/tests/test_m7_transport_modes.py` — AC-D15 (new per reviewer H5; M6-frozen file untouched)
- `v5/tests/test_m7_regimes.py` — AC-Reg2 (new per reviewer H6; was missing)
- `v5/tests/test_m7_post_fill_validation.py` — Order-level post-fill validation (split out of arm_bracket per user feedback)

**Total**: 22 new test files. Target ≥80 test functions in aggregate. All tests MUST FAIL on Phase 3 completion (RED state verified before Phase 4).
