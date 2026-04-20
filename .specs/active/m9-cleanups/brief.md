# M9 — Cleanups (Conviction, Walk-Forward, Indicators, Regime, Risk)

**Summary**: Bundle of five independent cleanup items that depend on M7's Strategy API: replace conviction with priority + AllocationPolicy, finalize walk-forward extraction, build pull-based memoized indicators, remove regime from engine, and add formal risk components.

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

After M7 ships the unified Strategy API, several v4 legacy patterns remain that are awkward, misleading, or incomplete:

1. **conviction_score misnomer**: The 0-1 float called "conviction" is actually a priority/allocation weight. Real conviction would affect sizing (M8). The name misleads strategy developers into thinking it affects position size when it only affects selection order.

2. **Walk-forward residue**: If M7 doesn't fully extract walk-forward from signal generation, residual coupling remains. Either way, the validation.py outer loop needs hardening.

3. **Push-model indicators**: v4 pre-computes all indicators and pushes them via context dicts. Strategies pay for indicators they don't use. There's no caching — the same EMA(20) computed twice for two strategies on the same token wastes cycles.

4. **Regime in engine**: Regime detection (BTC gate, TOTAL2 boost, bear/bull classification) is baked into the engine via `regime_params` config and exit_regime_* parameters. This couples the engine to a specific market-structure theory. Strategies should own their regime logic.

5. **No formal risk layer**: Risk checks (drawdown throttle, gross exposure limit, daily loss limit, funding safety) are scattered across config flags and ad-hoc if-statements. No structured risk component model.

6. **Dashboard regime display**: The dashboard shows regime state that will be removed from the engine. It also lacks scaling timeline and parent_position_id grouping from M2/M5.

---

## Scope

### In scope

**C-1: Conviction -> Priority + AllocationPolicy**
- Rename `conviction_score` to `priority` in TokenSignal
- `AllocationPolicy` Protocol with implementations:
  ```python
  class AllocationPolicy(Protocol):
      def rank(self, candidates: list[EntryCandidate], state: SimulationState) -> list[EntryCandidate]: ...

  # rank() returns a reordered list, not indices — returning indices couples the policy to the caller's list ordering.
  class RandomShuffle(AllocationPolicy): ...   # default
  class PriorityDesc(AllocationPolicy): ...    # by TokenSignal.priority
  class TieredPriority(AllocationPolicy):       # 3-tier with shuffle within tier
      def __init__(self, thresholds: list[float] = [0.66, 0.33]): ...  # configurable boundaries; default matches v4 (simulator.py:761-769)
  ```
  - `RandomShuffle` — current default behavior (random selection from eligible)
  - `PriorityDesc` — highest priority first
  - `TieredPriority` — 3-tier buckets with shuffle within tier (strategies assign tier 1/2/3; within each tier, random order prevents systematic bias toward alphabetical symbols)
  - `PortfolioConfig.allocation_policy: AllocationPolicy = RandomShuffle()` — pluggable
- Strategies return priority in TokenSignal; engine uses AllocationPolicy to order entries
- **conviction array->scalar reshape**: v4's `conviction_score: np.ndarray` is a per-bar array. v5's `priority: float | None` is a per-signal scalar. This is a reshape, not a rename. The strategy must pre-index at the current bar in `generate()`: `priority = conviction_array[bar_idx]`. The per-bar array no longer exists on the signal object.
- `min_conviction_threshold` REMOVED — strategies self-filter in `generate()` or don't emit low-quality signals

**C-2: Walk-Forward Finalization**
- If M7 left residual walk-forward logic in signals.py, extract it
- `v5/validation.py` hardening: configurable fold parameters, CPCV support, clear separation from signal generation
- Walk-forward params move from PortfolioConfig to a new `ValidationConfig` dataclass:
  ```python
  @dataclass
  class ValidationConfig:
      train_bars: int = 2000
      recal_bars: int = 500
      purge_bars: int = 100
      n_folds: int = 5
      cpcv_enabled: bool = True
  ```
  Passed to `validation.py` functions, NOT to the engine or strategies.
- Walk-forward isolation contract: Signal generation NEVER sees walk-forward masks. Strategies don't know WF exists. `v5/validation.py` slices `(train, oos)` windows and invokes the engine N times with clean state.
- Walk-forward is an outer loop that calls strategy.generate() repeatedly, never the reverse

**C-3: Pull-Based Memoized Indicators**
- `v5/indicators.py` — memoized indicator cache keyed by (symbol, indicator_name, params)
- `ctx.per_token(t).ema(20)` computes on first access, caches for the bar
- Memoization cache key: `(token, id(indicator_fn), frozen_params, bar_idx)` — uses `id(indicator_fn)` (stable within process) or a user-supplied string name instead of `__qualname__` (which can collide across modules). Cache scope: per backtest run (cleared between runs), per WF fold (fresh Strategy = fresh cache), per paper tick cycle (cleared after all strategies processed for the tick).
- Cache invalidates on new bar arrival
- Common indicators: EMA, SMA, ATR, RSI, Bollinger, VWAP, Donchian, Z-score
- Strategies define only what they use — no pre-computation of unused indicators

**C-4: Regime Removal from Engine**
- Delete `regime_params` from PortfolioConfig
- Delete `exit_regime_*` parameters
- Delete regime computation from BarProcessor / simulator
- `v5/regimes.py` — optional utility module that strategies can import and call. Utility function signatures:
  - `detect_crisis(universe_ctx, bar_idx)` — returns True during BTC-gated crisis conditions (needs full UniverseContext for BTC price history + TOTAL2 lookback, not just a single bar's context)
  - `detect_uptrend(universe_ctx, bar_idx)` — returns True during confirmed bull regime
  - `detect_dispersion(universe_ctx, bar_idx)` — returns True when alt dispersion is elevated
  - Memoized per `(bar_idx, universe_ctx_id)` — safe to call from multiple strategies without recomputation. `universe_ctx_id` is generated as `id(universe_ctx)` — unique per UniverseContext instance. Changes every bar (new instance per bar in backtest) or every tick cycle (paper mode).
- Strategies that use regime (s524 family) call `v5.regimes.detect_crisis(universe_ctx, bar_idx)` / `v5.regimes.detect_uptrend(universe_ctx, bar_idx)` in their `generate()` or `check_exit()` method

**C-5: Risk Components**
- `v5/risk.py` with formal risk component model:
  ```python
  class RiskComponent(Protocol):
      def check(self, candidate, state) -> RiskDecision: ...

  class RiskDecision(Enum):
      ACCEPT = "ACCEPT"
      REJECT = "REJECT"
      REDUCE = "REDUCE"
      HALT = "HALT"
  ```
  - `DrawdownThrottle` — reduces position count or size during drawdown. `__init__(thresholds: list[tuple[float, float]], scope: Literal["portfolio", "strategy"] = "portfolio")`. Portfolio-scope is default.
  - `MaxGrossExposure` — caps total portfolio exposure
  - `MaxNetExposure` — caps net long/short imbalance
  - `MaxConcurrentOrders` — limits total open orders across strategies
  - `DailyLossLimit` — halts trading after daily loss threshold (`max_daily_loss_usd`)
  - `FundingSafetyCheck` — warns/blocks when funding rate is extreme
  - `PortfolioConfig.risk_components: list[RiskComponent] = [DrawdownThrottle([...])]` — pluggable
  - `TradingState`: `state.trading_state: Literal["ACTIVE", "REDUCING", "HALTED"]` derived from risk components. HALTED blocks all new entries. ACTIVE is normal operation. In `TradingState.REDUCING`: (a) new entries BLOCKED, (b) exits via stop/trail/exit-chain ALLOWED (risk-reducing), (c) `Strategy.check_scale` returning negative qty_delta (reduce) ALLOWED, (d) `Strategy.check_scale` returning positive qty_delta (increase) BLOCKED, (e) linked-leg auto-propagation of REDUCES allowed, auto-propagation of INCREASES blocked.
- Risk components registered with BarProcessor, checked before Phase 3 (entries)

**C-6: Dashboard Rework**
- Remove regime display panels
- No `consolidate_partial_trades` band-aid — Dashboard provides BOTH views: (a) position-level grouping by `parent_position_id` (scaling history per position), (b) order-level grouping by `order_id` (multi-leg order status with all legs). Toggle in dashboard UI.
- New trade table columns: `scale_count`, `leaves_qty`, `has_scaling`, `r_anchor_price`
- Add scaling_events timeline in position detail view (from M2's ScalingEvent data)
- Update trade table to show binding constraint from M8

**C-6 FIX-aligned counters** (in `v5/report.py` / `SimulationState`):
- `partial_fills: int` — non-terminal `Position.reduce` events only (reduce-only, not exits)
- `increase_fills: int` — scale-up events
- `contingent_fills: int` — auto-propagated linked-leg executions
- `entry_scale_downs: int` — entries clamped by concentration/ADV/capital constraints

### Out of scope

- New indicator algorithms not in v4
- Machine learning indicator models
- External data-based risk (e.g., VIX-based throttle — that's strategy logic)
- Dashboard infrastructure changes (stays Streamlit)
- Portfolio optimization / rebalancing

---

## Key Acceptance Criteria

1. **conviction_score deleted**: No references to `conviction_score` remain in v5/. All replaced with `priority`. TokenSignal uses `priority: float`. A grep test confirms zero hits for "conviction_score" in v5/.

2. **AllocationPolicy works**: PriorityDesc selects the highest-priority signals first. A test with 10 signals and max_positions=3 verifies the top 3 by priority are selected.

3. **Memoized indicators**: `ctx.per_token("BTCUSDT").ema(20)` called twice in the same bar returns the same object (identity check). Called on a new bar, it recomputes. A test verifies both behaviors.

4. **Regime out of engine**: BarProcessor and simulator have zero references to regime. `v5/regimes.py` exists as an optional import. s524m calls `v5.regimes.classify()` in its generate() method. Engine tests pass without any regime config.

5. **Risk components**: DrawdownThrottle reduces max_positions when portfolio drawdown exceeds threshold. A test simulates 15% drawdown and verifies position count is reduced.

6. **TradingState**: Risk components collectively determine TradingState. HALTED blocks all new entries. REDUCING blocks entries but allows scale-out. ACTIVE is normal. A test transitions through all three states (ACTIVE -> REDUCING -> HALTED). RiskDecision enum `{ACCEPT, REJECT, REDUCE, HALT}` is returned by each component's `check()` method.

7. **Dashboard renders**: Dashboard loads without errors after regime removal. Scaling timeline renders for a strategy with ScalingEvents. Parent position grouping shows linked trades together.

8. **Walk-forward clean**: `v5/validation.py` can run walk-forward with configurable folds independently of signal generation. A test runs 3-fold walk-forward and verifies fold boundaries.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M7** (Strategy API) | **Required** — AllocationPolicy plugs into signal dispatch; indicators are part of UniverseContext; regime removal requires Strategy.generate() to own regime logic |
| **M4** (BarProcessor) | **Required** — Risk components integrate with BarProcessor Phase 3 gating |
| **M2** (Position Scaling) | **Benefits from** — Dashboard scaling timeline uses ScalingEvent data |
| **M5** (Multi-leg Orders) | **Benefits from** — Dashboard parent_position_id grouping uses Order.legs data |
| **M8** (Sizing) | **Benefits from** — Dashboard binding constraint display uses M8's per-fill logging |

---

## Time Estimate

**30-40 hours**

- ~5h: C-1 conviction -> priority + AllocationPolicy
- ~4h: C-2 walk-forward finalization
- ~6h: C-3 pull-based memoized indicators
- ~4h: C-4 regime removal + v5/regimes.py utility
- ~6h: C-5 risk components (DrawdownThrottle, MaxGrossExposure, DailyLossLimit, FundingSafetyCheck, TradingState)
- ~5h: C-6 dashboard rework
- ~5h: Tests (unit + integration)
- ~3h: Documentation + migration notes

---

## Parity Gate

- All v5 tests pass with new conviction->priority naming
- Dashboard renders correctly without regime panels
- s524m strategy works with regime logic in strategy.generate() (not engine)
- Risk components can be disabled (no-op) for parity with v4 behavior
- Memoized indicators produce identical values to v4's pre-computed indicators

---

## M4 Impact (status — completed in Phase 4 Round 2)

**T16b and `paper_candle_exits.py` absorption were originally deferred to M9 but were completed during M4 Phase 4 Round 2 (post-review)**. Status retained here for traceability:

- ✅ **T16b (`_armed_tokens` → `_pending_entries`)** — COMPLETE. `_pending_entries: dict[(sid,token), PendingEntry]` is now the primary write storage (0 remaining write sites on `_armed_tokens`). `_armed_tokens` is a back-compat `@property` returning a derived dict view. Structural invariants asserted by `v5/tests/test_m4_pending_entries_primary_storage.py` (5 tests).
- ✅ **`paper_candle_exits.py` absorbed** — COMPLETE. File deleted; helpers inlined into `v5/paper_engine.py` at module scope. AC3 still green, AC4 (dispatcher unification) still green.

**Remaining M4 follow-up in M9 scope**:

None of the above carry over. M9's M4 cleanup is now limited to the original housekeeping:

Original cleanup scope:
- **Remove legacy armed-entry shims**: any `paper_engine._armed_tokens` property or compatibility alias pointing to the new `_pending_entries` attribute.
- **Rename artifacts**: drop `armed_log.jsonl` alias; only `pending_entries_log.jsonl` remains.
- **Rename stale memory entries**: grep for `ArmedEntry|armed_entry|armed_tokens` in memory/, docs/, and internal notes.

---

## M6 Impact — Cleanup M6 leaves for M9

M6 ships `v5/data/instruments.py::Instrument` with a narrowed `contract_subtype: Literal["perpetual","quarterly","dated","european","american"] | None` field (C2 from M6 Round 2 brief review). The legacy free-string `contract_type: str | None` field is kept in M6 **as a deprecated alias** during migration, so callers can be updated incrementally without breaking. M9 removes it:

- **Drop `Instrument.contract_type` field entirely** — the deprecated alias field introduced during M6 migration. All callers must read `instrument.contract_subtype` (typed `Literal`) instead. Rename audit: grep `contract_type` across `v5/`, `tests/`, and strategies; update every read site to `contract_subtype`.
- **Verify semantics mapping** is still honored after the deletion:
  - `asset_class="spot"` → `contract_subtype=None`
  - `asset_class="perp"` → `contract_subtype="perpetual"`
  - `asset_class="future"` → `contract_subtype="quarterly"` | `"dated"`
  - `asset_class="option"` → `contract_subtype="european"` | `"american"`
- **Grep test**: zero hits for `contract_type` in `v5/` after M9 ships (analogous to the `conviction_score` zero-hit AC in C-1).

---

## M5 Impact — Additional cleanup M5 leaves for M9

M5 ships `v5/orders.py` with FIX-aligned types + feature-flagged multi-leg migration behind `StrategySpec.use_multi_leg_orders: bool = False`. M5 preserves both codepaths to avoid AC14 parity break on the current hourly-only fleet. M9 drops the legacy path:

- **Flip `use_multi_leg_orders` default to `True` permanently** — parity-validated via shadow replay during M5 is the prerequisite. Delete the flag entirely once all strategies have migrated.
- **Delete legacy `Position.linked_position_id`-based path** — the combined primary/secondary pair logic that relied on `linked_position_id` + `pos.leg: str ("primary"|"secondary")` gets removed. M5 Order.legs with `ContingencyType.NONE + sum()` capital aggregation (design.md F3) becomes the sole combined-strategy path.
- **Remove `Position.leg: str` field** in favor of `leg_ref_id` exclusively. Touches 8 read sites in exit_handlers.py + simulator.py (design.md F6). Price-routing logic that currently checks `pos.leg == "secondary"` must migrate to venue/market-based lookup.
- **Consolidate `Leg.market` + `Leg.settlement_type` redundancy** — M5 keeps both during migration (design.md F10). M9 picks one as authoritative and drops the other.
- **Drop `armed_log.jsonl` dual-write** — M5 dual-writes legacy events to both `orders_log.jsonl` (new) and `armed_log.jsonl` (compat) per design.md F11. M9 drops the old file; `orders_log.jsonl` is sole sink.

### M4 deferred tests — M9 completion gate

10 M4 acceptance tests currently fail RED with `pytest.fail("blocked on ...")` because they exercise APIs deferred to M9. M9 must land these APIs AND verify all 10 go green before shipping.

**Blocked on T16b (_armed_tokens → _pending_entries migration + `replay_paper_ticks`)** — 6 tests:
- `v5/tests/test_m4_parity_paper.py::TestAC19PaperParity::test_paper_replay_matches_pre_m4_state`
- `v5/tests/test_m4_parity_paper.py::TestAC19PaperParity::test_paper_and_backtest_trade_archives_equal`
- `v5/tests/test_m4_parity_mtf.py::TestAC31MTFCodePathIdentity::test_backtest_vs_paper_archive_match`
- `v5/tests/test_m4_parity_mtf.py::TestAC31MTFCodePathIdentity::test_pending_entry_log_diff_zero`
- `v5/tests/test_m4_parity_mtf.py::TestAC37MinuteExitsParityFixture::test_reproduction_bit_identical`
- `v5/tests/test_m4_parity_mtf.py::TestAC37MinuteExitsParityFixture::test_expected_archive_bit_identical`

**Blocked on T15c/T17 plumbing (`run_backtest_mtf(output_path=, seed=, tick_fixture_path=)`)** — 3 tests:
- `v5/tests/test_m4_parity_hourly.py::TestAC18HourlyParity::test_hourly_only_archive_bit_identical`
- `v5/tests/test_m4_parity_mtf.py::TestAC31MTFCodePathIdentity::test_48h_mtf_fixture_generates_archive`
- `v5/tests/test_m4_parity_paper.py::TestAC19PaperParity::test_paper_24h_deterministic`

**Blocked on T11 look-ahead anchor semantics (implicit `start_ts_ns` anchor)** — 1 test:
- `v5/tests/test_m4_look_ahead.py::TestAC15LookAheadSafety::test_signal_close_unreadable_before_bar_close`

**M9 completion gate**: `pytest v5/tests/test_m4_parity_hourly.py v5/tests/test_m4_parity_paper.py v5/tests/test_m4_parity_mtf.py v5/tests/test_m4_look_ahead.py -v` must return 10/10 green before M9 ships. If any of these reveal a design issue requiring spec change, trigger test-dispute resolution protocol.

---

## M7 Impact — Carry-over cleanups from M7 ship reviews

M7 shipped after 10 review rounds (FIX architect + Quant architect). Final verdicts: both SHIP. A handful of non-blocking items were explicitly deferred here. All 10 review artifacts live under `.specs/active/m7-strategy-api/reviews/{fix,quant}-review-round{1..10}.json` — use them as the reference when scheduling these.

### FIX wire-encoder completeness (flagged 5+ rounds, non-blocking)

1. **Fill dataclass missing FIX ExecReport tags** (`v5/fill.py:27-46`) — reviewer: 4 rounds
   - Add fields: `symbol: str` (FIX 55), `side: Literal[-1, 1]` (FIX 54), `order_qty: float` (FIX 38), `ord_type: OrderType` (FIX 40).
   - Backfill via Order↔Fill `cl_ord_id` linkage is currently relied on at reconciliation time; M9 closes this by embedding the tags directly in Fill for wire-round-trip independence.

2. **Leg FIX serializers missing** (`v5/orders.py:~384-414`)
   - Add `Leg.to_fix_leg_side()` → LegSide(624) `'1'` / `'2'` from `direction`.
   - Add `Leg.to_fix_leg_ord_type()` → OrdType(40) from `Leg.order_type`.
   - `Leg.to_json` already emits `.name` values so persistence is safe; these methods are wire-encoder sugar for the FIX gateway M10+.

### Observability / threat-model

3. **AC-S4 ctx runtime-mutation** (Quant round-8 NEW-17, threat-model call deferred)
   - Current state: strategies can mutate `ctx._lifecycle_config` dict contents and call `object.__setattr__(ctx, '_stopped', True)`. AST scan cannot track taint on method parameters.
   - Proposal: wrap `_lifecycle_config` in `types.MappingProxyType` in `UniverseContext.__post_init__` (or via a custom `_private_mapping` class if MappingProxyType is too strict for engine writes). `_stopped` gets renamed to `__stopped` (name-mangled) OR moved to an engine-owned side-channel registry (same pattern as `_QUARANTINE_REGISTRY`).
   - Acceptance: strategy loader rejects any `ctx._lifecycle_config[...] = ...` / `.clear()` / `object.__setattr__(ctx, ...)` form.

4. **Uid leak in quarantine registry** (Quant round-7 MINOR, round-6 NEW-12)
   - `_quarantined_uids` keeps dead uids after GC (WeakKeyDictionary correctly drops the strategy → uid mapping, but the uid entry in `_quarantined_uids` stays). Bounded (≤ running strategy count) but docstring claim "auto-decays" is false.
   - Fix: on `WeakKeyDictionary` finalizer, also remove the uid from `_quarantined_uids`. Or switch to `WeakValueDictionary` semantics + `weakref.finalize` callback.

5. **Production integration of quarantine API** (Quant round-7 MINOR, round-7 NEW-13)
   - Currently `_mark_quarantined` / `_is_quarantined` are only exercised by tests. BarProcessor's 15-callback dispatch at `v5/bar_processor.py` should consult `_is_quarantined` before invoking generate/check_*/filter_entry/on_*.
   - Acceptance: run a paper session with a strategy forced to exception_counter ≥ threshold — BarProcessor skips it on subsequent bars.

6. **Stale XPASS markers** (Quant round-3/4/6 MINOR)
   - Two `@pytest.mark.xfail` markers in the v5 suite produce XPASS (test passes but stays marked xfail). Identify via `pytest --runxfail -v` and convert to plain passes or `strict=True` per intent.

7. **Synthetic-clock fallback non-determinism** (Quant round-3 MINOR)
   - `OrderFactoryView._now()` fallback path produces deterministic epoch monotonically, BUT `_now_ns` falls back to `_now()` which picks wall-clock-ish synthetic timestamps when no Clock injected. Tighten or document the injection contract: all production paths MUST inject a Clock.

### Other

8. **exception_counter class-attr style** (Quant round-1 NIT)
   - `BaseStrategy.exception_counter: int = 0` at class level → mutated via `self.exception_counter += 1`. Works, but clearer as an `__init__` `self.exception_counter = 0` to avoid the shared-class-attribute confusion for readers.

9. **Integer overflow in `_STRATEGY_UID_COUNTER`** (Quant round-7 MINOR context)
   - `itertools.count(1)` returns Python ints — no overflow risk in practice, but document that `_strategy_uid_of` is monotonic only up to process restart. Fine for intra-run; paper-state persistence does NOT rely on uid equality across restarts.

### Acceptance

- FIX items 1-2: add 4 fields to Fill + 2 methods to Leg with FIX tag docstrings. No behavior change for existing callers.
- Observability items 3-7: structural hardening; acceptance is empirical (run the round-9/10 test scripts from the review artifacts).
- Items 8-9: single-line cleanup + one-line docstring.

**Total estimated effort**: 4-6h (field additions + MappingProxyType wrapper + BarProcessor quarantine wiring).

### Not carry-over (resolved during M7)

- closed_trades deque/list AC-H1 row #13 — resolved via union-type test acceptance (commit 0959875 era); zero downstream cascade.
- WalkForwardRunner per-fold synthetic data regeneration (Quant round-3 MAJOR-4) — already acknowledged as Wave-B scaffold; real data wiring happens naturally when M8 brings up the simulator + bar loader.
