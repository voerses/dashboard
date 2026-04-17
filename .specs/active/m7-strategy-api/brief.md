# M7 — Unified Strategy API (Option Y)

**Summary**: Define a single `Strategy` Protocol class that replaces the current split between per-token and portfolio signal generation, eliminates the `strategy_type` field, and provides a `UniverseContext` for lazy indicator access — unifying how strategies interact with the engine.

---

## Problem

v4 strategies interact with the engine through multiple inconsistent interfaces:

1. **Two signal paths**: `signals.py` (per-token `TokenSignals`) and `portfolio_signals.py` (cross-sectional `PortfolioSignals`) with separate dataclasses, separate dispatch logic, and a `strategy_type` field that switches between them at runtime.

2. **No standard lifecycle**: Strategies have no formal on_start, on_stop, on_position_closed hooks. Cleanup logic is scattered or missing.

3. **Walk-forward baked into signal generation**: The walk-forward/CPCV validation loop is interleaved with signal computation in signals.py, making it impossible to run signal generation without also running validation (or vice versa).

4. **Push-model indicators**: The engine pre-computes all indicators and pushes them to strategies via context dicts. Strategies can't request custom indicators, and unused indicators waste compute.

5. **No scaling/exit callbacks**: Strategies can't express "check if this position should scale" or "apply custom exit logic" — those are hardcoded in engine config.

---

## Scope

### In scope

- `v5/strategy_api.py` — `Strategy` Protocol class with methods:
  - `on_start(portfolio_config) -> None` — lifecycle: init state
  - `on_stop(reason: str) -> None` — lifecycle: cleanup
  - `required_data() -> list[Subscription]` — data needs declaration (AC-D2)
  - `generate(universe_ctx, bar_idx) -> UniverseSignals` — unified signal generation (replaces both per-token and portfolio paths)
  - `check_scale(pos, bar_ctx) -> ScaleAction | None` — per-position scaling check
  - `check_exit(pos, bar_ctx) -> ExitCheck | None` — custom exit logic
  - `filter_entry(candidate, bar_ctx) -> bool` — pre-entry filter
  - `on_position_closed(closed_trade) -> None` — post-close callback
  - `on_order_filled(order, fill) -> None` — post-fill callback
  - `view_state() -> dict` — introspection for dashboard/debugging
- `UniverseContext` class with:
  - `per_token(symbol) -> TokenView` — lazy per-token data access
  - `TokenView` with indicator access (e.g., `ctx.per_token("BTCUSDT").ema(20)`)
  - Universe-wide data (portfolio equity, open positions, regime state if opted in)
  - Clock access via `clock.now()` (AC-D6)
  - `tokens` property: bar-relative list of currently-tradable tokens (delisted tokens disappear, new listings appear) (AC-S4)
  - Cross-strategy isolation: UniverseContext returns DEEP-COPIED numpy arrays per strategy invocation. Strategy A mutating `ctx.per_token('BTC').close[50] = 999` does NOT affect Strategy B. Cost: one `np.copy()` per accessed array per strategy per bar. For 236 tokens x 6 arrays x 8760 bars x float32 = ~12GB total copies per backtest — acceptable given the alternative (silent cross-strategy corruption). In paper mode (single strategy per pool), deep-copy is skipped for performance (AC-S2)
- `v5/indicators.py` — pull-based memoized indicator cache. Memoization key: `(token, indicator_fn.__qualname__, frozen_params, bar_idx)`. Scoped per run; cleared at end. (AC-S3)
- `v5/regimes.py` — optional utility module. Per-bar regime memoized per `(bar_idx, universe_ctx_id)` for cross-strategy sharing (AC-Reg2)
- `v5/clock.py` — `TestClock` (virtual time, advances with bars) + `LiveClock` (wall clock). Strategies use `clock.now()`; direct `time.time()` forbidden. `UniverseContext` never exposes bars with `ts_event > clock.now()` — no look-ahead leakage in backtest (AC-D6)
- Unified `v5/signals.py` replacing both `signals.py` and `portfolio_signals.py`
- Walk-forward extraction to `v5/validation.py` — outer loop only, not interleaved with signal generation. `validation.py` slices `(train, oos)` windows, invokes engine N times with clean state. Signal generation NEVER sees walk-forward masks. Strategies don't know WF exists (AC-V1)
- `strategy_type` field removed — all strategies implement the same Protocol
- Reference strategy migration: s524m ported to v5 Strategy Protocol
- `v5/signals.py` produces `TokenSignal` (unified, replaces both `TokenSignals` and `PortfolioSignals`)

### Out of scope

- Indicator computation engine (M9 — pull-based memoized indicators)
- Sizing changes (M8)
- Data subscription declarations (M6 provides the hook; M7 uses it)
- Migration of all strategies (only s524m as reference; others migrate when user decides)
- New strategy capabilities not in v4 (this is unification, not expansion)

---

## Per-Bar to Per-Fill Sizing Handoff

Strategy's `generate()` returns `UniverseSignals` containing `TokenSignal` per token. `TokenSignal` carries `sizing: SizingRequest` (from M8). The engine evaluates `SizingRequest` at fill time (when the entry candidate becomes an actual position). For per-bar arrays (size_multiplier, leverage), the strategy pre-indexes at `bar_idx` inside `generate()` and bakes the scalar into `SizingRequest`. Example: `SizingRequest(FIXED_FRACTION, fraction=base_frac * size_mult[bar_idx], leverage=lev[bar_idx])`. The engine never sees arrays.

## SizingRequest Stub

M7 defines `TokenSignal.sizing: SizingRequest | None = None` as a field. The `SizingRequest` dataclass is a stub in M7 (just the type with `intent` and `fraction_of_equity` fields). M8 fleshes it out with full fields (leverage, reduce_only, margin_mode, notional_usd). M7 tests use the stub.

## Conviction to Priority Split

v4's `conviction_score: np.ndarray` served both ranking AND sizing. v5 splits this cleanly: `TokenSignal.priority: float | None` (ranking only — consumed by AllocationPolicy) + `SizingRequest.fraction_of_equity: float` (sizing only — strategy computes fraction itself, optionally using signal_strength). This is a semantic decomposition, not a rename. Migration of s524m requires rewriting the conviction logic into separate priority + fraction computations (~4-5h).

## Strategy Rewrite Scope

Rewriting production strategies (s513, s523c, s524m) for v5 is in scope. Estimated: s513 ~2h, s523c ~3h, s524m ~5h. These rewrites produce clean v5 strategies, not mechanical ports. No v4 parity requirement — strategies are validated against their own backtest results, not v4 results.

---

## Key Acceptance Criteria

1. **Single Protocol (AC-S1)**: All strategies implement the `Strategy` Protocol:
   ```python
   class Strategy(Protocol):
       def on_start(self, portfolio_config) -> None: ...
       def on_stop(self, reason: str) -> None: ...
       def required_data(self) -> list[Subscription]: ...
       def generate(self, universe_ctx, bar_idx) -> UniverseSignals: ...
       def check_scale(self, pos, bar_ctx) -> ScaleAction | None: ...
       def check_exit(self, pos, bar_ctx) -> ExitCheck | None: ...
       def filter_entry(self, candidate, bar_ctx) -> bool: ...
       def on_position_closed(self, closed_trade) -> None: ...
       def on_order_filled(self, order, fill) -> None: ...
       def view_state(self) -> dict: ...
   ```
   Default implementations: `on_start`/`on_stop`/`on_*` no-op; `check_scale`/`check_exit` return None; `filter_entry` returns True; `view_state` returns `{}`.
   There is no `strategy_type` field, no per-token vs portfolio branching in the engine. A type-check test verifies protocol conformance.

2. **UniverseContext lazy + isolated (AC-S2)**: Strategies receive a `UniverseContext` that provides lazy per-token data access. `ctx.per_token("BTCUSDT")` returns a `TokenView` with bar data and indicator access. A test verifies lazy evaluation (indicators not computed until accessed). Cross-strategy isolation enforced: UniverseContext returns DEEP-COPIED numpy arrays per strategy invocation. Strategy A mutating `ctx.per_token(t).close[50] = 999` does NOT affect Strategy B. Cost: one `np.copy()` per accessed array per strategy per bar. In paper mode (single strategy per pool), deep-copy is skipped for performance.

3. **Pull-based memoized indicators (AC-S3)**: `ctx.per_token("BTC").ema(n=20, col="close")` lazily computes + memoizes. Cache key: `(token, indicator_fn.__qualname__, frozen_params, bar_idx)`. Scoped per run; cleared at end. Engine computes once per (bar, key) across all strategies within a run.

4. **UniverseContext.tokens bar-relative (AC-S4)**: `ctx.tokens` returns currently-tradable tokens at `bar_idx`. Delisted tokens disappear; new listings appear.

5. **Error containment per method (AC-S5)**: Exception in `on_start` = fail-fast at startup. Exception in `generate`/`check_scale`/`check_exit`/`filter_entry` = logged and treated as no-op for that call (strategy skipped this bar, engine continues). Exception in `on_stop` = logged but doesn't block shutdown.

6. **Unified signals.py**: A single `v5/signals.py` module handles all signal generation dispatch. `portfolio_signals.py` is deleted. Signal generation produces `UniverseSignals` regardless of strategy style.

7. **Walk-forward extracted (AC-V1)**: `v5/validation.py` slices `(train, oos)` windows, invokes engine N times with clean state. Signal generation in `v5/signals.py` has no walk-forward logic. Strategies NEVER see walk-forward masks. A test runs signal generation without validation and gets results. **WF fold state reset**: `validation.py` instantiates a FRESH Strategy object per WF fold. Each fold gets a clean `on_start()` call. Strategy internal state (`_regime_cache`, `_helper_fired_levels`, etc.) is automatically clean because it's a new object. Strategies MUST NOT use module-level mutable state — all state lives on `self`.

8. **Clock abstraction (AC-D6)**: `v5/clock.py` provides `TestClock` (virtual time, advances with bars) + `LiveClock` (wall clock). Strategies use `clock.now()`; direct `time.time()` forbidden. `UniverseContext` never exposes bars with `ts_event > clock.now()` — no look-ahead leakage in backtest.

9. **Lifecycle hooks**: `on_start`, `on_stop`, `on_position_closed`, `on_order_filled` are called at the correct points. A test with a mock strategy verifies each hook is called with correct arguments.

10. **s524m parity**: Reference strategy s524m, ported to v5 Strategy Protocol, produces metrics within 0.5% of v4/s524m over the Q-DEC4 validation period.

11. **check_scale + check_exit**: BarProcessor calls `Strategy.check_scale` in Phase 2 and `Strategy.check_exit` in Phase 3 (alongside handler-based exits). A test verifies both callbacks fire at the correct phase.

12. **Per-bar regime memoization (AC-Reg2)**: `v5/regimes.py` optional utility. Memoized per `(bar_idx, universe_ctx_id)` for cross-strategy sharing.

13. **view_state**: `Strategy.view_state()` returns a dict suitable for dashboard display. The default implementation returns an empty dict (strategies opt in to introspection).

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M4** (BarProcessor) | **Required** — BarProcessor calls Strategy lifecycle hooks in Phase 1/2/3 |
| **M6** (Data Architecture) | **Required** — DataEngine provides the data that populates UniverseContext |
| **M1** (v5 Fork) | **Required** — v5 namespace |
| **M2** (Position Scaling) | **Benefits from** — ScaleAction already defined; check_scale integrates it |

---

## Migration Cost Estimates

Per-strategy v4 -> v5 migration cost (user-driven, not part of M7 scope — reference only):

- **s513** (simple per-token, 188 LOC): ~1-2h — remove module constants, wire `v5.regimes.detect_crisis`, rewrite `generate()` as universe iterator
- **s523c** (composite per-token): ~2-3h — add priority field, migrate conviction -> priority
- **s524m** (cross-sectional portfolio): ~3-4h — conviction everywhere; carefully migrate; preserves per-bar leverage + size_multiplier via strategy-local logic
- **s532, s540** (s524m-family): ~2h each after s524m pattern established

---

## Time Estimate

**40-60 hours**

- ~8h: Strategy Protocol + UniverseContext + TokenView design
- ~8h: Unified signals.py (merge per-token + portfolio paths)
- ~6h: Walk-forward extraction to validation.py
- ~6h: Lifecycle hook wiring in BarProcessor + paper_engine
- ~8h: s524m reference migration + parity testing
- ~5h: TokenSignal unification (replace TokenSignals + PortfolioSignals)
- ~4h: view_state + dashboard integration point
- ~5h: Tests (unit + integration + parity)
- ~3h: Documentation

---

## Parity Gate

- Reference strategy s524m produces metrics within 0.5% of v4/s524m (Q-DEC4 validation period)
- Unified signal path produces identical results for both per-token-style and portfolio-style strategies
- All v5 tests pass
- Walk-forward validation produces identical fold results when run through v5/validation.py
