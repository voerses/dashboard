# M8 Sizing Redesign — Task Decomposition

**Phase 3a artifact.** 33 tasks, organized in 13 waves (A-M). Each task ≤ 1-2 files, ≤ 4h AI work. `[P]` = parallelizable. Dependencies as `(after: N, M)`.

---

## Wave A — SizingIntent enum + port updates

### Task 1 [P] ✓ — SizingIntent enum + SizingRequest extend
- **Files**: `v5/sizing/intents.py` (new), `v5/strategy_api.py` (modify re-export)
- **Delivers**: `class SizingIntent(str, Enum)` with FIXED_FRACTION, FIXED_NOTIONAL values; extend existing `SizingRequest` with `notional_usd`, `reduce_only`, `margin_mode` fields; preserve JSON string roundtrip for paper-state compat. Add mutual-exclusion validator (`fraction_of_equity XOR notional_usd`).
- **Est.**: 3h. **ACs**: AC-Sz1, AC-Sz2.

### Task 2 [P] ✓ — Strategy port intent constant updates
- **Files**: `v5/strategies/s513_v5.py`, `v5/strategies/s523c_v5.py`, `v5/strategies/s524m_v5.py` (1 line each)
- **Delivers**: `intent="FIXED_FRACTION"` → `intent=SizingIntent.FIXED_FRACTION`. No behavior change; type-hygiene migration.
- **Est.**: 1h. **After**: 1. **ACs**: AC-Sz1 compat.

---

## Wave B — MarketState Protocol + adapters + CapitalAllocationPolicy

### Task 3 [P] ✓ — MarketState Protocol + AllocationState TypedDict
- **Files**: `v5/sizing/market_state.py` (new)
- **Delivers**: `MarketState` Protocol with methods `adv(token) -> float`, `mark_price(token) -> float`, `free_margin(strategy_id, policy) -> float`, `liquidation_distance(position, leverage) -> float`, `rolling_adv(token, window) -> float`, `equity(strategy_id) -> float`. `AllocationState` TypedDict with 4 fields (see design §8.2).
- **Est.**: 2h. **ACs**: AC-Sz3 data-source foundation.

### Task 4 [P] ✓ — SimulatorMarketState adapter
- **Files**: `v5/sizing/market_state.py` (extend)
- **Delivers**: Implementation backed by `SimulationState` + signal arrays. Reads ADV from `sig.adv_1h[bar_idx]`, mark from `sig.close_1h[bar_idx]`, free margin from `state.available_margin`.
- **Est.**: 2h. **After**: 3. **ACs**: AC-Sz3 data-source (backtest).

### Task 5 [P] ✓ — PriceMonitorMarketState adapter
- **Files**: `v5/sizing/market_state.py` (extend)
- **Delivers**: Implementation backed by `PriceMonitor` + legacy paper state. Reads ADV + mark + free margin via existing PriceMonitor facade. Flag=OFF path.
- **Est.**: 2h. **After**: 3. **ACs**: AC-Sz3 data-source (legacy paper).

### Task 6 [P] ✓ — DataEngineMarketState adapter
- **Files**: `v5/sizing/market_state.py` (extend)
- **Delivers**: Implementation backed by M6 `DataEngine.venue(...)`. Flag=ON path. Must return byte-identical values to PriceMonitor for identical inputs (AC-Sz9 parity precondition).
- **Est.**: 2h. **After**: 3. **ACs**: AC-Sz3 data-source (M6 paper).

### Task 7 ✓ — CapitalAllocationPolicy Protocol + SharedPoolPolicy
- **Files**: `v5/sizing/allocation.py` (new)
- **Delivers**: `CapitalAllocationPolicy` Protocol with `sampling_cadence` class attr + `available_capital(strategy_id, state, clock_now_ns)` method. `SharedPoolPolicy` implementation returning `state.available_margin` (identity). Paper-state `to_config()/from_config()` factory.
- **Est.**: 2h. **After**: 3. **ACs**: Design §8 hook; forward-compat for M9.

---

## Wave C — 6 clamps + pipeline

### Task 8 [P] ✓ — ADV cap clamp (clamp #1)
- **Files**: `v5/sizing/clamps.py` (new)
- **Delivers**: `adv_cap_clamp(order, market_state, config) -> ClampResult`. Computes `max_fill_notional = rolling_adv × adv_cap_pct`. Binding-log records current/clamped notional.
- **Est.**: 2h. **After**: 3, 4. **ACs**: AC-Sz3 clause 1.

### Task 9 [P] ✓ — Concentration clamp (clamp #2)
- **Files**: `v5/sizing/clamps.py` (extend)
- **Delivers**: `concentration_clamp` — `max_per_symbol = strategy_equity × concentration_limit`. Respects per-strategy (isolated) vs portfolio-wide (SharedPool) mode.
- **Est.**: 2h. **After**: 3, 4. **ACs**: AC-Sz3 clause 2.

### Task 10 ✓ — Free-capital clamp (clamp #3) + CapitalAllocationPolicy integration
- **Files**: `v5/sizing/clamps.py` (extend)
- **Delivers**: `free_capital_clamp` that calls `policy.available_capital(...)`. Funding-buffer subtracted BEFORE policy call (§7.1). Under `margin_mode="cross"` reads portfolio-level unrealized PnL via `MarketState`.
- **Est.**: 3h. **After**: 3, 4, 7. **ACs**: AC-Sz3 clause 3 (cross-margin tested explicitly).

### Task 11 [P] ✓ — Min-size clamp (clamp #4)
- **Files**: `v5/sizing/clamps.py` (extend)
- **Delivers**: Floor check — if notional < `min_position_usd`, REJECT with `binding="min_size"`.
- **Est.**: 1h. **After**: 3, 4. **ACs**: AC-Sz3 clause 4.

### Task 12 [P] ✓ — Liquidation-distance clamp (clamp #5, Binance tiered MMR)
- **Files**: `v5/sizing/clamps.py` (extend)
- **Delivers**: `liquidation_distance_clamp` using Binance's tiered maintenance-margin schedule (notional-bucketed). Reject if `notional × leverage` pushes liq price inside current stop distance. Test with position spanning two tiers.
- **Est.**: 3h. **After**: 3, 4. **ACs**: AC-Sz3 clause 5. **Risk-reviewer focus target (round 3).**

### Task 13 [P] ✓ — Slippage clamp (clamp #6, move SqrtImpact)
- **Files**: `v5/sizing/slippage.py` (new; content copied verbatim from `v5/sizing.py::SqrtImpactSlippage`)
- **Delivers**: `SqrtImpactSlippage` relocated under sizing package. Clamp #6 uses it to adjust `fill_price` (not a reject).
- **Est.**: 2h. **After**: 3, 4. **ACs**: AC-Sz3 clause 6.

### Task 14 ✓ — Clamp pipeline + ordering
- **Files**: `v5/sizing/clamps.py` (extend)
- **Delivers**: `run_clamp_pipeline(order, available_capital_usd, market_state, policy, config) -> (new_order, binding_log)`. Fixed 6-clamp order. Each clamp either adjusts `sizing_ctx["margin_usd"]` or sets `binding_constraint`.
- **Est.**: 2h. **After**: 8, 9, 10, 11, 12, 13. **ACs**: AC-Sz3.

---

## Wave D — Order.release_atomic integration + binding log

### Task 15 ✓ — Binding-log writer + JSONL schema
- **Files**: `v5/sizing/binding_log.py` (new)
- **Delivers**: `write_sizing_fill_entry(order, binding_log, binding, error=None)`. Schema per design §3e (timestamp, order_id, symbol, strategy_id, intent, clamp_values, binding_constraint, fill_*, error). Buffered file handle; flush on engine shutdown. Uses FIX StrategyID(1098) vocabulary (not Party 448).
- **Est.**: 3h. **After**: 14. **ACs**: AC-Sz5.

### Task 16 ✓ — Order.release_atomic integration
- **Files**: `v5/orders.py` (modify existing `release_atomic`)
- **Delivers**: Extend signature to `release_atomic(*, available_capital_usd, market_state, policy, config)`. Invoke `run_clamp_pipeline` → emit binding-log → `_transit(RELEASED)` or `_transit(REJECTED)`. Default `policy=SharedPoolPolicy()` preserves backward compat.
- **Est.**: 3h. **After**: 14, 15. **ACs**: AC-Sz3 entry point; design B1.

### Task 17 ✓ — Clamp error containment
- **Files**: `v5/orders.py` (extend release_atomic try/except)
- **Delivers**: Catch ClampError inside `release_atomic` → `state=REJECTED, reject_reason=f"clamp_error_{name}"`. Emit binding-log entry with `error` field populated. BarProcessor never crashed. AC-S5 pattern.
- **Est.**: 2h. **After**: 16. **ACs**: AC-Sz7.

---

## Wave E — Helper library

### Task 18 ✓ — vol_target_fraction + kelly_fraction helpers
- **Files**: `v5/sizing/helpers.py` (new)
- **Delivers**: Two pure functions with documented formulas. `vol_target_fraction(target_vol_annual, realized_vol, vol_cap=4.0)`. `kelly_fraction(edge, variance, kelly_mult=0.25)` (textbook formula, NOT v4's `kelly_mult * edge`).
- **Est.**: 2h. **ACs**: AC-Sz4.

### Task 19 ✓ — risk_budget_fraction + composite_scaled_fraction helpers
- **Files**: `v5/sizing/helpers.py` (extend)
- **Delivers**: `risk_budget_fraction(risk_usd, stop_distance_bps, equity, leverage)`. `composite_scaled_fraction(base_fraction, composite_score, adv, config=V4_DEFAULTS)` using `ADVScalingConfig` frozen dataclass (mirrors v4 curve for optional migration).
- **Est.**: 3h. **ACs**: AC-Sz4.

---

## Wave F — Delete v4 pipeline + config cascade

### Task 20 ✓ — Delete v4 sizing pipeline
- **Files**: `v5/sizing.py` (DELETE entire module; `SqrtImpactSlippage` preserved in Task 13)
- **Delivers**: Remove `KellySizing`, `SizingModel` Protocol, `compute_position_size`, `get_sizing_model`, `compute_slippage_bps`, `_DEFAULT_KELLY`, `_SIZING_MODELS`, `_DEFAULT_SLIPPAGE`, `_SLIPPAGE_MODELS`, `get_slippage_model`. Leaves `v5/sizing/` as the only sizing package.
- **Est.**: 2h. **After**: 13. **ACs**: AC-Sz6.

### Task 21 ✓ — Config cascade cleanup (~28 files)
- **Files**: `v5/config.py` + ~27 test/config files that reference deleted fields
- **Delivers**: Delete `SizingDefaults` dataclass entirely. Delete 9 v4 sizing fields from `PortfolioConfig` (adv_to_sizing, kelly_mult_* cluster, cap_pct_* cluster, size_multiplier, cap_multiplier, max_trade_pct, dd_scaling, pump_filter_*, unrealized_pnl_floor, adv_sizing_enabled, vol_adj). Add new `ClampsConfig` dataclass to `PortfolioConfig` (adv_cap_pct, concentration_limit, min_position_usd, liquidation_buffer_pct, funding_buffer_pct, max_sizing_equity). Update `SAFETY_RAILS` + `NON_OVERRIDABLE`. Sweep callers.
- **Est.**: 4h. **After**: 20. **ACs**: AC-Sz6.

---

## Wave G — Callsite migration (simulator + paper_engine)

### Task 22 (G, partial) ✓ — simulator.py callsite migration
- **Files**: `v5/simulator.py` (lines ~1546, ~1818 — 2 callsites)
- **Delivers**: Replace `sizing_model.compute_size(...)` with `order = build_order(...); released = order.release_atomic(available_capital_usd, market_state=SimulatorMarketState(sig), policy=cfg.capital_allocation_policy, config=cfg.clamps)`. Read `released.sizing_ctx["margin_usd"]` for filled notional.
- **Est.**: 3h. **After**: 16, 20. **ACs**: Wire-up; integration-tested via AC-Sz3 tests.

### Task 23 (G, partial) ✓ — paper_engine.py callsite migration
- **Files**: `v5/paper_engine.py` (line ~1696 — 1 callsite)
- **Delivers**: Same migration as Task 22 but with `PriceMonitorMarketState` (flag=OFF) or `DataEngineMarketState` (flag=ON) routed via existing `use_data_engine` flag. M7 AC-P1 wiring preserved.
- **Est.**: 2h. **After**: 16, 20. **ACs**: Wire-up; AC-P1 compat.

---

## Wave H — Multi-leg OTOCO aggregation

### Task 24 ✓ — Multi-leg OTOCO clamp aggregation
- **Files**: `v5/sizing/clamps.py` (extend)
- **Delivers**: Free-capital + concentration clamps respect `ContingencyType` aggregation rules from `v5/orders.py::compute_reserved_capital`. OTOCO reserves `entry + max(siblings)`; OCO reserves `max`; OTO/NONE/OUO sum. ADV + slippage remain per-leg.
- **Est.**: 3h. **After**: 16. **ACs**: Design B2.

---

## Wave I — reduce_only overfill semantics

### Task 25 ✓ — reduce_only overfill handling
- **Files**: `v5/orders.py` (extend `release_atomic` or add pre-clamp check)
- **Delivers**: Check `reduce_only=True` + order qty vs current position. If would open or flip direction → REJECT `reject_reason="reduce_only_overfill"`. Partial-fill: emit `ExecType.TRADE` for reducing portion + `ExecType.REJECTED` for overage. 3 test scenarios (long_5x_reduce_7x, short_3_reduce_5, flip_attempt).
- **Est.**: 4h. **After**: 16. **ACs**: AC-Sz8.

---

## Wave J — AC-S10 backtest-vs-backtest parity (path I)

### Task 26 ✓ — Real OHLCV loader in validation.py
- **Files**: `v5/validation.py` (add `load_oos_window` function)
- **Delivers**: `load_oos_window(tokens, window="Q-DEC4-2025") -> dict[token, pd.DataFrame]`. Reads `data/perp/1h_cache/{token}_1h.parquet` (verified fresh 2026-04-20; 236 tokens cached). Returns 206-token OHLCV + funding_1h arrays for Q-DEC4 2025 window.
- **Est.**: 4h. **ACs**: AC-S10 data loader.

### Task 27 ✓ — WalkForwardRunner → simulate_portfolio wiring
- **Files**: `v5/validation.py` (rewrite `WalkForwardRunner.run`)
- **Delivers**: Drive real `v5.portfolio_backtest.run_backtest(strategy_ids=[strategy.name], months=3, capital=100_000, ...)`. Collect `PerformanceMetrics` from `v5/report.py::compute_portfolio_metrics` (reuse — no reimpl). Surface via `WalkForwardResult.metrics: dict`.
- **Est.**: 4h. **After**: 26. **ACs**: AC-S10 runner.

### Task 28 (M9 trip-wire) ✓ — Fixture regen + test_m7_s524m_parity.py alignment
- **Files**: `v5/tests/fixtures/m7_s524m_parity/v4_reference_metrics.json` (regen), `v5/tests/test_m7_s524m_parity.py` (unwrap xfails)
- **Delivers**: Regenerate v4 fixture on same 206-token × Q-DEC4 × seed=42 window via `v4/portfolio_backtest.py --strategy s524m_nofilter --months 3 --capital 100000 --market perp --conviction-mode ranked --max-portfolio-positions 30 --end-date 2025-12-31T23:00:00`. Remove `@pytest.mark.xfail(strict=True)` from `TestS524MMetricParityWithinHalfPercent`. Update test to load 206-token universe via Task 26 loader.
- **Est.**: 4h. **After**: 27. **ACs**: AC-S10. **Critical gate**: xfails flip to real PASS within 0.5%.

---

## Wave K — AC-Sz9 paper-vs-backtest parity

### Task 29 ✓ — AC-Sz9 parity fixture construction
- **Files**: `v5/tests/fixtures/m8_sizing_parity/` (new dir)
- **Delivers**: 1-week hourly fixture with 1 token + deliberate clamp-hit strategy (scenarios: ADV-saturating, concentration-exceeding, liq-distance-violating, min-size-failing). Generated synthetically with deterministic TestClock seed.
- **Est.**: 4h. **After**: 16. **ACs**: AC-Sz9 fixture.

### Task 29a ✓ — AC-Sz9 parity fixture/runner module
- **Files**: `v5/sizing/parity_fixture.py` (new)
- **Delivers**: Three public callables referenced by `test_m8_paper_backtest_parity.py`:
  - `build_m8_parity_fixture(seed=42, bars=168, token="BTC") -> dict` — builds the deterministic 1-week × 1-token synthetic bar fixture + clamp-hit scenario schedule (ADV-sat at bar 40, concentration-exceed at bar 80, etc.)
  - `run_backtest_parity(fixture, tmp_path) -> Path` — drives `v5.simulator.simulate_portfolio` (NOT `run_backtest` — that's a CLI wrapper) against the fixture with `SharedPoolPolicy()`; writes `sizing_fills.jsonl` to tmp_path; returns path
  - `run_paper_parity(fixture, tmp_path) -> Path` — drives `v5.paper_engine` under deterministic TestClock with a replayable data feed against the same fixture; writes `sizing_fills.jsonl`; returns path
  - Both runners must produce byte-identical non-float columns + M3-tolerance float columns for AC-Sz9 to pass (quant reviewer round-2 found tests reference this module but no task creates it).
- **Est.**: 4h. **After**: 16, 27. **ACs**: AC-Sz9 runner (prerequisite for Task 30).

### Task 30 ✓ — AC-Sz9 paper-vs-backtest parity test
- **Files**: `v5/tests/test_m8_paper_backtest_parity.py` (already exists from Phase 3 — Task delivers IMPLEMENTATION of the 3 runners from Task 29a that make this test pass)
- **Delivers**: Test file calls `build_m8_parity_fixture`, `run_backtest_parity`, `run_paper_parity` from `v5.sizing.parity_fixture` (Task 29a). Runs fixture through `v5.simulator.simulate_portfolio` (NOT `run_backtest`; that's a CLI wrapper) → emit backtest `sizing_fills.jsonl`. Runs same fixture through `v5.paper_engine` under deterministic TestClock → emit paper `sizing_fills.jsonl`. Assert byte-identical for non-float columns; M3 tolerances (4 ULP / 5 bps / 10 bps) per float-field. Uses `v5/tests/shadow_replay_tolerances.py` constants (NOT hardcoded `2**-20`).
- **Est.**: 4h. **After**: 29, 29a. **ACs**: AC-Sz9.

---

## Wave L — Reviewer loop

### Task 31 — Final reviewer loop (FIX + Quant primary + focused Risk round 3)
- **Delivers**: Iterate with FIX architect + Quant architect subagent reviewers until both return PASS. At round ~3, spawn dedicated Risk architect subagent scoped to AC-Sz3 clauses 3+5 (free capital under cross-margin, liquidation distance) + AC-Sz8 (reduce_only) + margin_mode semantics. Fold Risk findings into round 4. Continue rounds until both primary reviewers SHIP (M7 pattern: 10 rounds).
- **Est.**: 8-15h. **After**: ALL implementation tasks (1-33).

---

## Wave M — Additional Phase-3 review gap closures

### Task 32 ✓ — Aggregate `fraction × leverage ≤ 1.0` portfolio pre-clamp
- **Files**: `v5/sizing/clamps.py` (extend), `v5/orders.py` (pre-clamp hook)
- **Delivers**: Pre-clamp check run BEFORE the 6-clamp pipeline in `Order.release_atomic`: `sum(open_positions.fraction_of_equity × leverage) + incoming.fraction × incoming.leverage ≤ 1.0`. On violation: `state=REJECTED, reject_reason="over_leveraged"` unless `SizingRequest.allow_over_leveraged=True` escape-hatch is set. Default: reject. Binding-log entry records `aggregate_leverage_ratio`.
- **Est.**: 3h. **After**: 16. **ACs**: brief §M7 Impact item 2 (`TokenSignal fraction_of_equity` bound).

### Task 33 ✓ — OTO contingency aggregation test + tiered MMR cases
- **Files**: `v5/tests/test_m8_multi_leg_aggregation.py` (extend), `v5/tests/test_m8_clamps_individual.py` (extend TestLiquidationDistanceClamp)
- **Delivers**:
  - OTO case: order with 3 legs under `ContingencyType.OTO` aggregates as `sum(margins)` (sequential triggering doesn't reduce aggregate capital) — matches `compute_reserved_capital` rule.
  - Binance tiered MMR: positions sized inside tier-1 (<$50k notional) vs straddling tier-2 (>$50k) → different maintenance-margin rates → different liquidation distances. Test both; assert correct tier lookup.
- **Est.**: 2h. **After**: 14. **ACs**: AC-Sz3 clauses 2+5 coverage gap from Phase-3 review.

---

## Additional stop-and-redecompose triggers (Phase-3 review output)

- **Task 16 `frozen` semantics**: if extending `Order.release_atomic` breaks the M7-round-4 `fix_audit_log` append-on-replace invariant (audit log must persist across `replace()` calls), STOP, escalate — frozen dataclass mutation via `replace(..., state=NEW)` must preserve the shared-list-by-reference audit trail.
- **Task 30 AC-Sz9 adapter drift**: if `PriceMonitorMarketState` and `DataEngineMarketState` produce non-identical values for any read method, STOP — this is an M6 regression, not M8 scope. File incident ticket against M6 before proceeding.
- **Task 26 OHLCV loader**: if `data/perp/1h_cache/*.parquet` is stale (not updated in >24h) or token count drops below 200, re-run `tools/fetch_all_perp_data.sh` before proceeding with fixture regen.
- **Task 10 cross-margin formula**: if the cross-margin free-capital formula under test_m8_cross_margin_free_capital.py differs from Binance's documented cross-margin engine, STOP — historical bug site; fold into Risk-reviewer round-3 scope.

---

## Dependency graph (summary)

```
Wave A (Tasks 1-2)       →  Wave B (Tasks 3-7)
Wave B                    →  Wave C (Tasks 8-14)
Wave C (Task 14)          →  Wave D (Tasks 15-17)
Wave D (Task 16)          →  Waves G, H, I
Wave F (Task 20) after 13 →  Wave G (Tasks 22-23) after 16, 20
Waves E, J, K, H, I independent of each other (after their immediate prereqs)
Wave L (Task 31) after ALL
```

**Critical path**: 1 → 3 → 10 → 14 → 15 → 16 → 22/23 → 28 → 31 (~30h + reviewer).
**Parallelizable**: 3 MarketState adapters (4, 5, 6), 5 independent clamps (8, 9, 11, 12, 13), 2 helpers (18, 19), AC-S10 loader (26), AC-Sz9 fixture (29) can all run concurrently.

## Stop-and-redecompose triggers

- Task 21 (config cascade) exceeds ~30 files touched → STOP, file AIPIP, split into per-file batches.
- Task 28 (fixture regen) produces metrics > 5% drift from M7 fixture → STOP, investigate (structural divergence not a clamp bug).
- Task 30 (AC-Sz9 parity) shows non-float-column drift → STOP, MarketState adapter bug; do not merge.
- Any task requires modifying M2/M4/M5 shipped contracts → STOP, escalate to user before proceeding.

---

**Total estimated hours**: ~90h implementation + 8-15h reviewer loop = 98-105h. Aligns with design estimate (91-106h); +5h for Tasks 32 (aggregate leverage bound) and 33 (OTO+tiered MMR test additions) per Phase-3 review round 1.
**Task count**: 33 (28 implementation + 2 AC-S10 + 2 AC-Sz9 + 1 reviewer loop ongoing as Task 31).
