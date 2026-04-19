# M7 — Unified Strategy API (Option Y) + Carryover Closeout

**Summary**: Define a single `Strategy` Protocol that replaces split per-token / portfolio signal paths, eliminates `strategy_type`, and provides a `UniverseContext` with pull-based memoized indicators. Folds in all M4/M5/M6 carryover items (callback surface formalization, FIX wire serializers + post-fill unwind hook + `arm_bracket` factory, AC-D6 AST scan + full PaperEngine 8-site migration + v4→v5 paper runner swap), plus cleanup of the 13 pre-existing test failures that predate M6 baseline f42705f.

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

v4 strategies interact with the engine through multiple inconsistent interfaces:

1. **Two signal paths**: `signals.py` (per-token `TokenSignals`) and `portfolio_signals.py` (cross-sectional `PortfolioSignals`) with separate dataclasses, separate dispatch logic, and a `strategy_type` field that switches between them at runtime.
2. **No standard lifecycle**: Strategies have no formal on_start, on_stop, on_position_closed hooks. Cleanup logic is scattered or missing.
3. **Walk-forward baked into signal generation**: The walk-forward/CPCV validation loop is interleaved with signal computation in signals.py, making it impossible to run signal generation without also running validation.
4. **Push-model indicators**: The engine pre-computes all indicators and pushes them to strategies via context dicts. Strategies can't request custom indicators, and unused indicators waste compute.
5. **No scaling/exit callbacks**: Strategies can't express "check if this position should scale" or "apply custom exit logic" — those are hardcoded in engine config.
6. **M5 order lifecycle has no strategy-facing surface**: M5 shipped Order/Leg/OrderStatus but the strategy-side `arm_bracket(entry, sl, tp)` factory and the FIX wire serializers + `_order_reject_event` post-fill unwind hook were deferred to M7.
7. **M6 DataEngine is gated but unwired**: M6 shipped `PaperConfig.use_data_engine` flag + `build_paper_engine_for_test` helper, but the real 8-site PaperEngine dispatch (PriceMonitor → DataEngine) and the v4→v5 paper runner swap are M7's responsibility. Until done, `use_data_engine=True` only exercises test scaffolding, not the production path.
8. **AC-D6 Clock abstraction unenforced**: M6 shipped `Clock` Protocol + `LiveClock` + docstring prohibition, but the AST scan that enforces no-direct-`time.time()` in strategy source is M7 scope (the enforcement lives in the strategy loader, which M7 rewrites anyway).
9. **13 pre-existing test failures** predate M6 (verified against f42705f baseline) — cascading through M1/M3 "full suite green" meta-tests. Low-effort hygiene to clear before v5 rebuild continues.

---

## Scope

### In scope — Strategy API core

- `v5/strategy_api.py` — `Strategy` Protocol class (full callback surface below)
- `UniverseContext` — `per_token(symbol, venue=None)`, `tokens` (bar-relative), `clock`, `portfolio` (equity/positions), `fold_id`/`fold_window` for ensemble strategies
- `v5/indicators.py` — pull-based memoized indicator cache, typed param-contract
- `v5/regimes.py` — optional utility, per-bar regime memoized per `(bar_idx, universe_ctx_id)`
- Unified `v5/signals.py` replacing both `signals.py` and `portfolio_signals.py` — `portfolio_signals.py` DELETED
- `v5/validation.py` — walk-forward outer loop only, fresh Strategy object per fold
- `strategy_type` field removed — all strategies implement the same Protocol
- Reference strategy migration: s524m + s513 + s523c ported to v5 Strategy Protocol

### In scope — M4 carryover (callback surface + subscription extension)

- **DECISION — subscriptions extended to generic N-role** (reviewer H1 recommendation): `subscribe(role, spec)` where role is any string; `{signal, entry, exit}` reserved canonical names, existing triple strategies still work. Jane Street / Two Sigma multi-horizon alpha stacks use 3-5 roles routinely.
- **Full callback surface formalized** — see AC-S1 below. All 12 execution/lifecycle callbacks are first-class.
- **PendingEntry `self.arm(...)` factory** — strategies emit `Order` (M5) / `PendingEntry` (M4 legacy alias) via a Strategy-facing factory, not by populating dicts directly.
- **Role-to-callback routing is engine's job** — strategy declares subscriptions; engine dispatches the right callback based on which bar's close fired.

### In scope — M5 carryover

- **`arm_bracket(entry, sl, tp)` factory** on Strategy base class — constructs `Order(legs=[entry, sl, tp], fill_policy=OTO_BRACKET, contingency=OTO)`. AC-O1.
- **FIX wire serializers** — fill the M5 stubs: `OrderStatus.to_fix_ordstatus()`, `TriggerType.to_fix_trigger_type()`, `to_fix_trigger_price_direction()`, `LegStatus.to_fix_ordstatus()`. **Spec decision**: engine-internal states ARMED/TRIGGERED/RELEASED map to FIX OrdStatus=A "Pending New" + custom tag 9001-9003 with audit-log annotation (local-only; not sent over the wire). AC-O2.
- **`_order_reject_event` hook** — post-fill unwind path (venue REJECTED exec reports arriving after sibling leg fill). AC-O3.
- **`Order.venue_order_id: str | None`** population — venue-ack maps FIX OrderID(37) string onto the Order instance. AC-O4.
- **`Fill` dataclass** — carries FIX triple (ClOrdID(11) + OrderID(37) + ExecID(17)) + TransactTime(60) + last_qty/last_px/cum_qty/leaves_qty. AC-O5.

### In scope — M6 carryover

- **AC-D6 AST scan in strategy loader** — raises at strategy `__init__` if source contains banned calls. **Full banned set**: `time.time`, `time.time_ns`, `time.monotonic`, `time.perf_counter`, `datetime.now`, `datetime.utcnow`, `datetime.today`, `pd.Timestamp.now`, `np.datetime64('now')`. Detects BOTH attribute-access (`time.time()`) AND bare-import (`from time import time; time()`) forms. AC-V2.
- **`PaperConfig.use_data_engine=True` production rollout** — Task 17 full 8-site PaperEngine dispatch (paper_engine.py lines 859-912 / 893-911 / 1245-1248 / 2046-2068 / 3213-3217 / 3225-3229 / 3237-3252 / run_paper_multi.py 360-363,381-401) with flag=OFF byte-identical to pre-M6. AC-P1.
- **v4→v5 paper runner swap** — paper_runner migrates from `v4.run_paper_multi` to `v5.run_paper_multi`. Lock file `state/v4_paper_multi/paper.pid` → `state/v5_paper_multi/paper.pid` via a one-shot startup migration script (SIGTERM old, copy state, start new). AC-P2.
- **24h shadow replay hard merge gate** — replace M6's 5-min synthetic fixture with a genuine 24h live recording before flipping the flag. AC-P3.
- **`VenueCapabilities.supported_transport_modes`** — additive field added when strategy API needs `venue.supports(TransportMode.PUSH)`. AC-D15.
- **Backtest `use_data_engine` default flip** — STILL deferred to M9+ (no regression risk in M7). Not in scope for M7.

### In scope — Hygiene

- **Clean up 13 pre-existing test failures** (verified present on f42705f, not M6-caused):
  - 4 `test_conviction_to_priority.py` tests (AC2/AC3 priority sort + AC6 full-suite-green meta)
  - 4 `test_m1_fork_verification.py` tests (AC01 file count, AC05 test suite pass, AC11 signal field absent, AC16 exit_resolution absent)
  - 1 `test_m3_slots.py::TestAC15bM2Regression::test_full_v5_suite_passes`
  - 1 `test_m4_intra_bar_fill.py::TestAC38MarkTriggerBacktestFallback::test_audit_log_entry_emitted_in_backtest`
  - 3 paper determinism / state tests (`test_paper_determinism.py` ×2, `test_paper_state.py::TestClosedTradesEmptyAfterDeserialize`)
  
  **Budget**: ~6h investigation + 4h fixes. AC-H1.

### Out of scope

- Indicator computation engine beyond pull-memoized cache (M9)
- Sizing field expansion beyond M7 stub (M8 — `SizingRequest` gets `leverage`, `reduce_only`, `margin_mode`, `notional_usd`)
- Migration of ALL strategies (only s513, s523c, s524m as reference; others when user decides)
- New strategy capabilities not in v4 (unification, not expansion)
- Backtest `use_data_engine=True` default flip (M9+)

---

## Resolved gates (decisions made during Phase-1 review)

| Gate | Options considered | Decision | Rationale |
|------|--------------------|----------|-----------|
| G1 — Triple vs N-role subscription | (a) freeze triple / (b) extend to generic | **(b) extend** with reserved canonical names | Jane Street / Two Sigma precedent; backward-compat preserved |
| G2 — Callback surface MVP vs FIX-complete | (a) 2 callbacks (current brief) / (b) 6 (M4 flagged) / (c) 12 (Nautilus-parity) | **(c) 12 callbacks** | M5 shipped 8 OrdStatus values; 2 callbacks throws away the discipline |
| G3 — `self.arm(...)` vs `self.arm_bracket(...)` | one or two factories | **Both** — `arm(single_leg)` + `arm_bracket(entry, sl, tp)` | Bracket wraps arm; different ergonomics |
| G4 — paper_engine v4→v5 swap in M7 or later | M7 / M8 | **M7** (explicit, AC-P1/P2/P3) | Completes the real wiring; ~12h realistic |
| G5 — Indicator memoization key for numpy/lambda params | (a) forbid non-scalar / (b) require `.cache_key()` / (c) hash via tobytes() | **(b) `.cache_key()` method** for custom indicators; (a) forbid non-scalar for stdlib indicators | Explicit contract, no hidden hashing surprises |
| G6 — Cross-strategy isolation strategy | (a) deep-copy default / (b) read-only view default | **(b) read-only view default** with opt-in mutation via `ctx.mutable_copy(arr)` | Zero-cost isolation + catches accidental mutation eagerly |
| G7 — UniverseContext monolith vs split | single obj / split facades | **Namespace split**: `ctx.data`, `ctx.portfolio`, `ctx.clock`, `ctx.orders` — UniverseContext is the container, not a god-object | Nautilus precedent + cleaner mocking |
| G8 — FIX ARMED/TRIGGERED/RELEASED wire mapping | custom Admin msg / OrdStatus=A + custom tag / local-only | **OrdStatus=A "Pending New" + custom tag 9001-9003 + audit log** | Preserves FIX compatibility; engine internals leak only via custom tag |

---

## Per-Bar to Per-Fill Sizing Handoff

Strategy's `generate()` returns `UniverseSignals` containing `TokenSignal` per token. `TokenSignal` carries `sizing: SizingRequest` (from M8). The engine evaluates `SizingRequest` at fill time (when the entry candidate becomes an actual position). For per-bar arrays (size_multiplier, leverage), the strategy pre-indexes at `bar_idx` inside `generate()` and bakes the scalar into `SizingRequest`. Example: `SizingRequest(FIXED_FRACTION, fraction=base_frac * size_mult[bar_idx], leverage=lev[bar_idx])`. The engine never sees arrays.

## SizingRequest Stub (M7 scope)

M7 defines the stub with ALL fields s524m parity needs (leverage explicitly included per reviewer Quant-M5):

```python
@dataclass
class SizingRequest:
    intent: Literal["FIXED_FRACTION", "FIXED_NOTIONAL", "RISK_PER_TRADE"]
    fraction_of_equity: float = 0.0
    leverage: float = 1.0               # M7 — needed for s524m parity
    # M8 adds: reduce_only, margin_mode, notional_usd, risk_budget
```

## Conviction to Priority Split

v4's `conviction_score: np.ndarray` served both ranking AND sizing. v5 splits: `TokenSignal.priority: float | None` (ranking only) + `SizingRequest.fraction_of_equity: float` (sizing only). This is a semantic decomposition, not a rename.

## Strategy Rewrite Scope

Rewriting production strategies (s513, s523c, s524m) for v5. Reconciled estimate (was 3-4h vs 5h for s524m): **s513 ~2h, s523c ~3h, s524m ~5h** (the 5h figure per §90-92 is authoritative; §158 was understated). No v4 parity requirement — strategies validated against their own backtest results.

---

## Key Acceptance Criteria

### AC-S: Strategy Protocol + UniverseContext

1. **AC-S1 — Single Protocol with full FIX-aligned callback surface**:
   ```python
   class Strategy(Protocol):
       # Lifecycle
       def on_start(self, portfolio_config) -> None: ...
       def on_stop(self, reason: str) -> None: ...
       def on_reset(self) -> None: ...  # WF fold boundary
       
       # Data declaration
       def required_data(self) -> list[Subscription]: ...
       
       # Signal generation (per bar)
       def generate(self, ctx: UniverseContext, bar_idx: int) -> UniverseSignals: ...
       
       # Position management (per position per bar)
       def check_scale(self, pos, bar_ctx) -> ScaleAction | None: ...
       def check_exit(self, pos, bar_ctx) -> ExitCheck | None: ...
       def filter_entry(self, candidate, bar_ctx) -> bool: ...
       
       # FIX-aligned execution event callbacks (M5 OrdStatus states)
       def on_order_accepted(self, order) -> None: ...        # venue ack
       def on_order_rejected(self, order, reason: str) -> None: ...  # venue reject
       def on_order_cancelled(self, order, reason: str) -> None: ...
       def on_order_triggered(self, order) -> None: ...       # ARMED → TRIGGERED
       def on_order_partial_fill(self, order, fill) -> None: ...
       def on_order_filled(self, order, fill) -> None: ...    # final fill
       def on_order_expired(self, order) -> None: ...
       
       # Position lifecycle
       def on_position_opened(self, position) -> None: ...
       def on_position_changed(self, position, delta) -> None: ...
       def on_position_closed(self, closed_trade) -> None: ...
       
       # Introspection
       def view_state(self) -> dict: ...
   ```
   Default impls: lifecycle/event hooks = no-op; `check_scale`/`check_exit` = None; `filter_entry` = True; `view_state` = {}. Protocol declared `@runtime_checkable`; test verifies `isinstance(strategy, Strategy)` AND `mypy --strict` passes.

2. **AC-S2 — UniverseContext lazy + read-only isolated**: namespace split — `ctx.data.per_token(symbol, venue=None)`, `ctx.data.tokens`, `ctx.portfolio.equity`, `ctx.portfolio.open_positions`, `ctx.clock`, `ctx.orders.arm(...)`, `ctx.orders.arm_bracket(...)`, `ctx.fold_id`, `ctx.fold_window`. Per-token arrays returned as READ-ONLY views (`arr.setflags(write=False)`). Strategy A mutating `ctx.data.per_token(t).close[50] = 999` raises `ValueError: read-only array`. Strategy can opt into a copy via `ctx.mutable_copy(arr)` when intent is explicit.

3. **AC-S3 — Pull-based memoized indicators with typed cache key**: `ctx.data.per_token("BTC").ema(n=20, col="close")` returns SCALAR at `bar_idx` (not array). Cache key: `(token, indicator_fn.__qualname__, frozen_params_tuple, bar_idx)`. Stdlib indicators use immutable scalar params only (int/float/str). Custom indicators with non-scalar params MUST implement `.cache_key()` method returning a hashable tuple. Lambdas/closures rejected at registration (`ValueError: indicator must be named function`). Scoped per run; cleared on `on_reset()`.

4. **AC-S4 — UniverseContext.tokens bar-relative from InstrumentRegistry**: `ctx.data.tokens` queries `InstrumentRegistry` (M6-shipped) at `bar_idx` — delisted tokens absent, new listings present. Cross-references `MultiInstrumentCache` availability (token is "tradable" only if both listed AND cached).

5. **AC-S5 — Error containment per method**: `on_start`/`on_reset` = fail-fast; `generate`/`check_scale`/`check_exit`/`filter_entry` = logged + treated as no-op for that call (strategy skipped, engine continues); `on_order_*`/`on_position_*` = logged but don't block dispatch to other strategies; `on_stop` = logged but doesn't block shutdown.

6. **AC-S6 — Unified signals.py**: single module handles all signal generation. `portfolio_signals.py` DELETED. `TokenSignals`/`PortfolioSignals` dataclasses replaced by unified `TokenSignal`. Engine doesn't branch on `strategy_type`.

### AC-V: Walk-forward extraction + Clock enforcement

7. **AC-V1 — Walk-forward extracted**: `v5/validation.py` slices `(train, oos)` windows, invokes engine N times with FRESH `Strategy` instance per fold. `on_reset()` called between folds on kept instances (when validation opts for reuse). Strategies NEVER see WF masks. Module-level mutable state in strategy source forbidden — AST scan flags `global` + top-level `list`/`dict`/`set` assignments.

8. **AC-V2 — AC-D6 AST scan in strategy loader**: strategy `__init__` raises if source contains any of the banned clock calls listed in §"In scope — M6 carryover". Detects attribute-access (`time.time()`) AND bare-import (`from time import time; time()`) forms via AST walk over `Call`/`Attribute`/`ImportFrom` nodes.

### AC-O: Order / bracket factory + FIX wire

9. **AC-O1 — `arm` + `arm_bracket` factories**: `ctx.orders.arm(symbol, direction, size, trigger, **kwargs) -> Order` for single-leg. `ctx.orders.arm_bracket(entry_spec, sl_spec, tp_spec) -> Order` constructs `Order(legs=[entry, sl, tp], fill_policy=LegFillPolicy.OTO_BRACKET, contingency=ContingencyType.OTO)`. Both publish to bus per M5 pre-fill atomic path. Invalid combinations (e.g., SL price wrong side of entry) rejected with `ValueError` at factory call.

10. **AC-O2 — FIX wire serializers**: `OrderStatus.to_fix_ordstatus()`, `TriggerType.to_fix_trigger_type()`, `to_fix_trigger_price_direction()`, `LegStatus.to_fix_ordstatus()` return FIX-compliant strings. Engine-internal states ARMED/TRIGGERED/RELEASED → OrdStatus=A "Pending New" + custom tag 9001-9003 (v5 venue-adapter convention; audit log records the local state).

11. **AC-O3 — `_order_reject_event` post-fill unwind**: venue REJECTED exec report on a LEG whose sibling already FILLED triggers `LegFillPolicy.UNWIND_ON_REJECT` cascade — engine publishes reversal order to close the sibling leg. `Strategy.on_order_rejected(order, reason)` fires with `reason.startswith("post_fill_unwind:")`.

12. **AC-O4 — `Order.venue_order_id` population in live mode**: `BinanceWSClient`/`BinanceRESTClient` venue-ack maps FIX OrderID(37) onto `order.venue_order_id` before `on_order_accepted` fires. Paper/backtest modes synthesize a deterministic `venue_order_id = f"paper-{order_id:08x}"`.

13. **AC-O5 — `Fill` dataclass carries FIX triple**:
    ```python
    @dataclass(frozen=True, slots=True)
    class Fill:
        cl_ord_id: str             # FIX ClOrdID(11)
        venue_order_id: str | None # FIX OrderID(37)
        exec_id: str | None        # FIX ExecID(17)
        transact_time: int         # FIX TransactTime(60), epoch ns
        last_qty: float
        last_px: float
        cum_qty: float
        leaves_qty: float
    ```

### AC-P: Paper engine migration + runner swap

14. **AC-P1 — Full 8-site PaperEngine dispatch (Task 17)**: each of the 8 legacy sites in `v5/paper_engine.py` + 2 in `v5/run_paper_multi.py` gains an `if self._use_data_engine:` branch. Flag=OFF remains byte-identical to pre-M6. Flag=ON routes through `DataEngine`/`BinanceWSClient`/`BinanceRESTClient`. This is real extraction work — no fallback shim, no stop-trigger. Budget is 14h nominal; the work ships complete.

15. **AC-P2 — v4→v5 paper runner swap**: `v5/run_paper_multi.py` becomes the production runner. Lock file path migrates from `state/v4_paper_multi/paper.pid` to `state/v5_paper_multi/paper.pid`. A one-shot startup migration script reads old state once and writes to new path. `tools/start_all_services.sh` updated.

16. **AC-P3 — 24h shadow replay hard merge gate**: genuine 24h live WS+REST recording (not 5-min synthetic) diffed via `run_shadow_replay` returns `ohlcv_divergence_count == 0`. Gates the production `use_data_engine=True` flip. Recording via `v5/tools/record_ws_tap.py --mode ws --duration 86400`.

### AC-D: DataEngine extensions

17. **AC-D15 — `VenueCapabilities.supported_transport_modes` field**: additive; M7 adds when strategy API needs `venue.supports(TransportMode.PUSH)` symmetry.

### AC-Lifecycle + dashboard

18. **Lifecycle hooks firing (AC-Lifecycle)**: test with mock strategy verifies each of the **19 Protocol methods** (15 event callbacks + 4 declaration/query methods: `required_data`, `generate`, `check_scale`, `check_exit`, `filter_entry`) fires at the correct phase with correct arguments. The 15 event callbacks: on_start, on_stop, on_reset, on_order_{accepted, rejected, cancelled, triggered, partial_fill, filled, expired}, on_position_{opened, changed, closed}, view_state. (Earlier brief draft referenced `on_bar_close` / `on_day_end` — those are BarProcessor-emitted events, NOT Protocol methods; dropped from AC.)

19. **s524m parity (AC-S10)**: reference strategy s524m ported to v5 Strategy Protocol produces metrics within 0.5% of v4/s524m over the Q-DEC4 validation period.

20. **check_scale + check_exit (AC-S11)**: BarProcessor calls `Strategy.check_scale` in Phase 2 and `Strategy.check_exit` in Phase 3 (alongside handler-based exits).

21. **Per-bar regime memoization (AC-Reg2)**: `v5/regimes.py` optional utility. Memoized per `(bar_idx, universe_ctx_id)` for cross-strategy sharing.

22. **view_state**: returns dict suitable for dashboard display. Default impl returns `{}`.

### AC-H: Hygiene

23. **AC-H1 — 13 pre-existing test failures cleared**:
    - `test_m1_fork_verification.py::test_ac01_v5_file_count` — update expected count to reflect M4+M5+M6 deliverables
    - `test_m1_fork_verification.py::test_ac05_v5_tests_pass` — meta-test, converges once peers fixed
    - `test_m1_fork_verification.py::test_ac11_signal_field_absent` — verify signal-field purge is complete or revise test expectation
    - `test_m1_fork_verification.py::test_ac16_exit_resolution_absent` — same
    - `test_m3_slots.py::test_full_v5_suite_passes` — meta-test, converges
    - `test_conviction_to_priority.py` (4 tests) — M7 ships conviction→priority split per §"Conviction to Priority Split"; tests either pass or get revised spec
    - `test_paper_determinism.py` (2 tests) — investigate per-bar seeding; likely fix in paper engine
    - `test_paper_state.py::test_closed_trades_empty_after_deserialize` — investigate state schema
    - `test_m4_intra_bar_fill.py::test_audit_log_entry_emitted_in_backtest` — AC38 mark-trigger path; investigate

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M4** (BarProcessor) | **Required** — BarProcessor calls Strategy lifecycle hooks in Phase 1/2/3 |
| **M5** (Orders) | **Required** — `v5.orders.Order` is canonical; M7 adds strategy-facing factory |
| **M6** (Data Architecture) | **Required** — DataEngine provides bars; AC-P1/P2/P3 flip production to M6 path |
| **M1** (v5 Fork) | **Required** — v5 namespace |
| **M2** (Position Scaling) | **Benefits from** — ScaleAction already defined |
| **M8** (Sizing) | **Stub coordinated** — M7 ships SizingRequest with intent/fraction/leverage fields; M8 adds reduce_only/margin_mode/notional_usd/risk_budget |

---

## Migration Cost Estimates

Per-strategy v4 → v5 migration (user-driven; reference only):

- **s513** (simple per-token, 188 LOC): ~2h — wire `v5.regimes.detect_crisis`, rewrite `generate()` as universe iterator
- **s523c** (composite per-token): ~3h — add priority field, migrate conviction → priority
- **s524m** (cross-sectional portfolio): ~5h — conviction migration, priority split, pre-index leverage/size_multiplier into SizingRequest stub
- **s532, s540** (s524m-family): ~2h each after s524m pattern established

---

## Time Estimate

**100-140 hours** (up from 40-60h original estimate — expanded scope includes full carryover closeout + pre-existing fixes)

| Item | Hours |
|---|---|
| Strategy Protocol + UniverseContext namespace split + TokenView design (AC-S1/S2/S7) | 10 |
| Pull-based memoized indicators + cache_key() contract (AC-S3) | 8 |
| Unified signals.py (merge per-token + portfolio) (AC-S6) | 8 |
| Walk-forward extraction to validation.py (AC-V1) | 6 |
| AC-D6 AST scan in strategy loader (AC-V2) | 4 |
| All 15 lifecycle hooks wired in BarProcessor + PaperEngine (AC-Lifecycle) | 10 |
| s524m + s513 + s523c reference migration + parity testing | 12 |
| TokenSignal unification (replace TokenSignals + PortfolioSignals) | 5 |
| view_state + dashboard integration | 4 |
| M5 carryover — FIX wire serializers + `_order_reject_event` + venue_order_id + Fill triple (AC-O2/O3/O4/O5) | 10 |
| M5 carryover — `arm` + `arm_bracket` factories (AC-O1) | 4 |
| M6 carryover — Task 17 full 8-site PaperEngine dispatch (AC-P1) | 14 |
| M6 carryover — v4→v5 paper runner swap (AC-P2) | 6 |
| M6 carryover — 24h live WS recording + hard merge gate verification (AC-P3) | 4 (plus wall-clock 24h for the recording itself) |
| M6 carryover — VenueCapabilities.supported_transport_modes (AC-D15) | 1 |
| 13 pre-existing test failures cleanup (AC-H1) | 10 |
| Tests (unit + integration + parity) | 10 |
| Documentation | 4 |
| **Raw total** | **130** |

**Banded estimate: 100-140h.** Includes wall-clock 24h recording (operational, not engineering time — recorder shipped in M6 via `v5/tools/record_ws_tap.py --mode ws`).

---

## Parity Gate

- Reference strategy s524m produces metrics within 0.5% of v4/s524m (Q-DEC4 validation period)
- Unified signal path produces identical results for both per-token-style and portfolio-style strategies
- All v5 tests pass (including the 13 cleared failures from AC-H1)
- Walk-forward validation produces identical fold results when run through v5/validation.py
- 24h shadow replay (AC-P3) shows `ohlcv_divergence_count == 0` before `use_data_engine=True` flip in prod
- Paper runner migration (AC-P2) leaves live paper fleet running uninterrupted via the one-shot migration script (copy-not-move + SIGTERM + restart)

---

## M4 Impact — scope refinement — RESOLVED in-scope

M4's `StrategySpec.bar_subscriptions: dict[str, BarSpec]` with triple `{signal, entry, exit}` is **extended to generic N-role** subscriptions per G1 decision. Existing triple strategies still work (reserved canonical names); new strategies can declare `regime`, `alpha`, `risk`, or any string role. Callback routing (M4 Impact flagged `on_signal`/`on_entry_bar`/`on_exit_bar` etc.) is folded into AC-S1's full 15-callback surface.

## M5 Impact — RESOLVED in-scope

All 4 M5 carryover items are in scope:
- AC-O1: `arm_bracket` factory
- AC-O2: FIX wire serializers (OrderStatus/TriggerType/LegStatus with documented ARMED/TRIGGERED/RELEASED → OrdStatus=A + custom tag mapping)
- AC-O3: `_order_reject_event` post-fill unwind hook
- AC-O4: `Order.venue_order_id` population
- AC-O5: `Fill` dataclass with FIX triple (ClOrdID + OrderID + ExecID)

## M6 Impact — RESOLVED in-scope (4 of 5 items)

- AC-V2: AST scan enforcement — in scope
- AC-P1/P2/P3: full PaperEngine migration + runner swap + 24h hard gate — in scope
- AC-D15: VenueCapabilities.supported_transport_modes additive — in scope
- AC-O1: `arm_bracket` (M5 + M6 both flagged) — in scope via AC-O1
- Backtest `use_data_engine` default flip — **still deferred to M9+** (no production risk; needs numpy-path rebuild work that's M9 scope)
