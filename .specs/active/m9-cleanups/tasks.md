# M9 — Tasks (Wave-Ordered Decomposition)

**Total tasks**: 48. Wave-ordered A → F. Dependencies explicit via `(after: N)`. Parallel-safe tasks marked `[P]`. All tests in `v5/tests/test_m9_*.py` (88 tests, 84 RED + 4 invariant-green guardrails).

---

## Wave A — Pre-deletion upgrades (non-breaking)

Foundation layer. No deletions. Adds new modules that subsequent waves depend on.

- [ ] **T1 [P]** — C-3: Add `timeframe: BarSpec` to `IndicatorCache` cache key (`v5/indicators.py:60+`). Test: `test_m9_indicators_mtf.py` passes.
- [ ] **T2 [P]** — C-4: Implement `v5/regimes.py` utility module: `detect_crisis()`, `detect_uptrend()`, `detect_dispersion()`, memoized on `ctx_uid + bar_idx`; move `detect_daily_regime()` from `v5/engine.py:781-829`; export `CRISIS=0, QUIET=1, UPTREND=2, RANGE=3, DOWNTREND=4` constants.
- [ ] **T3 (after: 2)** — C-4: Add `ctx.market_indices` namespace to `UniverseContext`. Populate from `regime_signals.parquet` + `total2_total3.parquet`. Canonical keys: `BTC_CLOSE_1D, BTC_CLOSE_1H, TOTAL2, TOTAL3, DXY, BTC_DOMINANCE, REGIME_FLAG_1D`. Test: `test_m9_regime_deletion.py::test_market_indices_canonical_keys`.
- [ ] **T4 [P]** — C-1: New file `v5/arbitration.py` with `SignalArbitrationPolicy` Protocol + 4 impls (`RandomShuffle`, `PriorityDesc`, `TieredPriority`, `RoundRobin`) + `EntryCandidate` dataclass. Test: `test_m9_signal_arbitration.py::TestRandomShuffleDefault` et al.
- [ ] **T5 (after: 4)** — C-1: Add `PortfolioConfig.arbitration_policy: SignalArbitrationPolicy = RandomShuffle()` to `v5/config.py`. Add `StrategySpec.min_slot_guarantee: int = 0`. Validate `sum(guarantees) ≤ max_portfolio_positions` in `PortfolioConfig.__post_init__`.
- [ ] **T6 (after: 5)** — C-1: Phase 3.05 de-duplication step in `v5/simulator.py` before arbitration. Dedup by `max_positions_per_symbol` per strategy. Test: `test_m9_signal_arbitration.py::TestPhaseOrdering`.
- [ ] **T7 (after: 5)** — C-1: Replace `_sort_key` at `v5/simulator.py:1391-1398` with `config.arbitration_policy.rank(candidates, state, scope)`. Engine passes `scope="portfolio"` under SharedPool; per-strategy `scope="strategy"` loop under future FixedBudget. Test: `test_m9_signal_arbitration.py::TestScopeParameter`.
- [ ] **T8 (after: 7)** — C-1: Arbitration telemetry `v5/logs/arbitration.jsonl` sink. Schema: `{bar_idx, strategy_id, token, rank_in, rank_out, tier, admitted, displaced_by}`. 500MB rotate + gzip. Test: `test_m9_arbitration_analyzer_cli.py::test_arbitration_jsonl_rotates_at_500mb`.
- [ ] **T9 [P]** — C-5: New file `v5/risk.py` with `RiskDecision` enum + `RiskVerdict` dataclass + `TradingState` dataclass + `RiskComponent` Protocol (with `sampling_cadence`).
- [ ] **T10a (after: 9)** — C-5: Implement 6 simple risk components: `DrawdownThrottle`, `MaxGrossExposure`, `MaxNetExposure`, `MaxConcurrentOrders`, `DailyLossLimit`, `FundingSafetyCheck` in `v5/risk.py`. Tests: subset of `test_m9_risk_components.py`.
- [ ] **T10b (after: T10a)** — C-5: Implement `MaxCorrelatedExposure` (with 168-bar log-return memoization per `(bar_idx, frozenset(token_set))`, throttle not hard-reject) + `PerSymbolStrategyLimit`. Tests: remainder of `test_m9_risk_components.py`.
- [ ] **T11 (after: 9)** — C-5: Add `SimulationState.trading_state: dict[str, TradingState]` field (backwards-compat via `default_factory`). Keys: `_global` + per-strategy.
- [ ] **T12 (after: T10a, T10b, T11)** — C-5: Phase 3.0 hook in `v5/simulator.py` at line ~1305 (before `_sort_key`). Runs risk components on aggregate candidates pre-arbitration. Test: `test_m9_risk_components.py::TestTradingStateTransitions`.
- [ ] **T13 (after: 9)** — C-5: Add `PortfolioConfig.risk_components: list[RiskComponent] = field(default_factory=list)` to `v5/config.py`.
- [ ] **T14 [P]** — BarContext enrichment: add `ctx: UniverseContext`, `state_view: StateView` with `{equity, portfolio_dd_pct, open_positions_count, total_notional_usd, per_strategy_equity, per_symbol_exposure, bars_since_last_fill}` to `v5/exit_handlers.py:36+`. Test: `test_m9_bar_context_enrichment.py` (new).
- [ ] **T14b [P]** — C-2: Implement `ValidationConfig` + `CPCVSpec` + `WalkForwardResult` (with `pbo`, `deflated_sharpe` fields) in `v5/validation.py`. Delete duplicate dataclasses at lines 69-99. Add `PortfolioConfig.validation_config: ValidationConfig = field(default_factory=ValidationConfig)`. Test: `test_m9_validation_cpcv.py` (5 tests).

**Wave A gate**: all Wave-A tests green; no deletions yet; v4 paper runner unaffected.

---

## Wave B — Engine decoupling (regime + conviction deletion + strategy re-ports)

Destructive wave. Deletes shims after strategies are re-ported.

- [ ] **T15 (after: T2, T3)** — Re-port `v5/strategies/s524m_v5.py:336-339`: replace `bar_ctx.regime == CRISIS` with `v5.regimes.detect_crisis(bar_ctx.ctx, bar_ctx.bar_idx)`. Test: suite still green.
- [ ] **T16 (after: T2)** — Re-port `v5/strategies/s523c_v5.py:37`: `CRISIS = 0` → `from v5.regimes import CRISIS`. One-line.
- [ ] **T17 (after: T15, T16)** — Delete `RegimeConfig` from `v5/config.py:49-61`. Delete `detect_daily_regime()` call in `v5/engine.py:1719-1725`. Delete `bear_target_mult`, `bear_max_hold` from `StrategyContext` at `v5/engine.py:959-962`.
- [ ] **T18 (after: T17)** — Delete regime columns from `TokenBarArrays` (`v5/signals.py:73, 96-98, 410, 424, 546-547, 657, 675-676`). Delete `bear_target_mult`/`bear_max_hold` fields. Delete `TakeProfitHandler` regime branch at `v5/exit_handlers.py:476-488`.
- [ ] **T19 (after: T18)** — Delete `BarContext.regime` field at `v5/exit_handlers.py:54`. Test: `test_m9_regime_deletion.py::test_grep_regime_returns_zero`.
- [ ] **T20 [P]** — Implement `VectorizedStrategy` sub-Protocol in `v5/strategy_api.py` (adds `to_token_bar_arrays(ctx) -> dict[str, TokenBarArrays]` optional method).
- [ ] **T21 (after: T20)** — Implement `to_token_bar_arrays()` adapter on `s524m_v5` (~20 lines wrapping `_evaluate_token`) and `s523c_v5` (similar). s513_v5 does NOT implement `VectorizedStrategy`. Test: `test_m9_c7_bridge_interface.py::test_isinstance_vectorized`.
- [ ] **T22 (after: T21)** — Delete `TokenBarArrays.conviction_score` + `.priority` int32 array fields at `v5/signals.py:84-85`. Delete conviction→priority shim at `v5/signals.py:156-158, 164-173`. Delete `min_conviction_threshold` config knob (if present). Test: `test_m9_regime_deletion.py::test_grep_conviction_returns_zero`.
- [ ] **T23 (after: T22)** — Re-port s524m + s523c + s513: `TokenSignal.priority` emission directly (replace any leftover conviction-score-produces-priority-via-shim code). Strategy parity: full test suite still green.

**Wave B gate**: greps for `conviction_score` and `regime` return 0 in v5/ (excluding regimes.py); all strategy tests green; v4 paper runner untouched.

---

## Wave C — AC-S10 bridge + WalkForwardRunner wiring

- [ ] **T24 (after: T20)** — Implement `_engine_precompute_fallback(strategy, n_bars, ctx)` in `v5/simulator.py`. Python setup loop calls `strategy.generate(ctx, bar_idx)` for each bar, assembles `dict[str, TokenBarArrays]`.
- [ ] **T25 (after: T24)** — Add `__setattr__`-guarded proxy wrapper for fallback path. Any `self.` mutation during `generate()` call raises `StrategyStateMutationError`. Test: `test_m9_c7_bridge_interface.py::test_fallback_state_mutation_guard`.
- [ ] **T26 (after: T24, T21)** — Extend `simulate_portfolio()` signature at `v5/simulator.py:2207`: `strategies: dict[str, Strategy] | None = None`, `ctx_provider: Callable[[], UniverseContext] = None`. Dispatch: `if isinstance(s, VectorizedStrategy): use to_token_bar_arrays(); else: fallback`. Test: `test_m9_c7_bridge_interface.py::test_simulate_portfolio_strategies_kwarg`.
- [ ] **T27 (after: T26)** — Wire bridge into `WalkForwardRunner.run()` at `v5/validation.py:974-984`. Replace `_stub_metrics()` with real `compute_portfolio_metrics()`. Test: `test_m8_ac_s10_s524m_parity.py` xfails flip to PASS.
- [ ] **T28 (after: T21)** — Parity test: s524m + s523c `to_token_bar_arrays()` output byte-identical to `_engine_precompute_fallback()` output on Q-DEC4 2025 fold. `np.testing.assert_array_equal` on int fields; `np.array_equal(equal_nan=True)` on float fields. Test: `test_m9_c7_bridge_interface.py::test_parity_vectorized_vs_fallback`.
- [ ] **T29 (after: T27, T28, T15, T23)** — AC-S10 xfails flip to PASS within 0.5% relative tolerance on Q-DEC4 2025 206-token fold. Transitive on Wave B re-ports ensures s524m's crisis exit uses `v5.regimes.detect_crisis` (not deleted `bar_ctx.regime`).

**Wave C gate**: 6 xfails in test_m8_ac_s10_s524m_parity.py PASS; parity test green.

---

## Wave D — CapitalAllocationPolicy pluggable + replay-parity + flag flips + shim deletions

- [ ] **T30 [P]** — C-9: Extend `AllocationState` TypedDict in `v5/sizing/allocation.py` with `market_snapshot: dict[str, float]`.
- [ ] **T31 (after: T30)** — C-9: Add `PortfolioConfig.capital_allocation_policy: CapitalAllocationPolicy = SharedPoolPolicy()` to `v5/config.py`. Add `__setattr__` hot-swap guard raising `ConfigError` when policy changes with non-empty position book. Test: `test_m9_capital_allocation_pluggable.py`.
- [ ] **T32 (after: T31)** — C-9: Update free-capital clamp callsite in `v5/simulator.py:~1571` to read `config.capital_allocation_policy` instead of hardcoded `SharedPoolPolicy()` instance. Populate `market_snapshot` at bar-close from `ctx.market_indices`.
- [ ] **T33 (after: T31, T32)** — C-9: Schema update `v5/sizing/binding_log.py`: add `fix_1098: str` + `fix_1099: str | None = None` fields to per-fill JSONL record. Test: `test_m9_capital_allocation_pluggable.py::test_sizing_fills_fix_dual_stamp`.
- [ ] **T34 [P]** — Wave D: Build `v5/tests/fixtures/generate_m9_replay_parity_7d.py` fixture builder. Pick 7-day historical window from `data/perp/1m_cache/` + `1h_cache/` forcing each of 6 M8 clamps to bind at least once. Persist manifest to `v5/tests/fixtures/m9_replay_parity_7d/manifest.json`.
- [ ] **T35 (after: T34)** — Wave D: Run `use_m8_clamps` replay-parity test. paper_engine flag=False → archive A; flag=True → archive B. Assert non-binding bars byte-identical; binding bars have matching `sizing_fills.jsonl` row. Test: `test_m9_replay_parity.py::test_use_m8_clamps_nonbinding_bars_byte_identical`.
- [ ] **T35b (after: T34)** — Wave D: Run `use_multi_leg_orders` replay-parity on s513_v5 OTOCO bracket fixture. flag=False → archive A; flag=True → archive B. Assert non-contingent single-leg orders byte-identical. Test: `test_m9_replay_parity.py::test_use_multi_leg_orders_nonbinding_bars_byte_identical`.
- [ ] **T36 (after: T35)** — Wave D: Flip `PortfolioConfig.use_m8_clamps` default to `True`. Delete the flag + both branches at `v5/simulator.py:~1571`.
- [ ] **T37 (after: T36)** — Wave D: Delete `v5/sizing_legacy.py`. Clean up cascade: `v5/simulator.py` imports, `globals()["get_sizing_model"]` alias, `v5/paper_engine.py` legacy import, AC-Sz6 grep carve-out.
- [ ] **T38 (after: T35b)** — Wave D: Flip `StrategySpec.use_multi_leg_orders` default to `True`. Delete flag + `trigger_combined_entry` legacy branch at `v5/simulator.py:2740-2877` (~140 lines). Gated on T35b (multi-leg replay-parity) passing.
- [ ] **T39 (after: T38)** — Wave D: Delete `Position.leg: str = "primary"` field at `v5/position.py:68`. Migrate 8 read sites in `v5/simulator.py:413, 541, 589, 1045, 1065, 1191, 2111, 2278` to `leg_ref_id` + venue/market lookup.
- [ ] **T40 [P]** — Wave D: Delete `Instrument.contract_type: str | None` free-string from `v5/data/instruments.py`. Update all callers to read `contract_subtype: Literal[...]`.
- [ ] **T41 [P]** — Wave D: Delete `armed_log.jsonl` dual-write path in `v5/orders_log.py`. `orders_log.jsonl` sole sink.
- [ ] **T42 [P]** — Wave D: Delete `paper_engine._armed_tokens` property + alias. All writers/readers use `_pending_entries` directly.
- [ ] **T43 [P]** — Wave D: Pick `Leg.settlement_type` as authoritative (maps to FIX `LegSettlType(587)`). Drop `Leg.market` at `v5/orders.py:397`.

**Wave D gate**: all 12 shim deletions verified; replay-parity green; full suite green.

---

## Wave E — Dashboard rework + v5 paper runner deployment (feature-flagged)

- [ ] **T44a [P]** — C-6: Rework `v5/dashboard_state.py` — delete regime panels; delete `consolidate_partial_trades` band-aid. Output path stays `/srv/data/state_v5.json`.
- [ ] **T44b (after: T44a, T45)** — C-6: Add parent_position_id grouping + scaling timeline + binding_constraint column (JOINs `sizing_fills.jsonl` by `order_id`) to `v5/dashboard_state.py`.
- [ ] **T45 (after: T44)** — C-6: Add FIX-aligned counters to `v5/report.py` + `SimulationState`: `partial_fills`, `increase_fills`, `contingent_fills`, `entry_scale_downs`.
- [ ] **T46 [P]** — C-8: Create `tools/start_v5_paper.sh` + `tools/stop_v5_paper.sh`. Env-gated: `V5_PAPER_ENABLED=0` default → no-op. Uses `/tmp/paper_runner_v5.pid`, `state/v5_paper_multi/`, `/srv/data/state_v5.json`. Does NOT touch v4 paths. Test: `test_m9_runner_kill_switch.py`.
- [ ] **T47 [P]** — C-8: Create `configs/runner_pool_config_v5.json` with ported strategies (s513_v5 + s523c_v5 + s524m_v5).
- [ ] **T48 [P]** — C-8: Add 8-line `view` detection to `/srv/dashboard/current/index.html`. Create symlink `/srv/dashboard/current/v5 -> .`. Preserve v4 `/` path unchanged (git diff empty on v4-facing paths). Test: `test_m9_dashboard_v5_rendering.py::test_v4_dashboard_unchanged`.
- [ ] **T49 (after: T46, T48)** — C-8: Kill-switch drill: stop v5 runner → `/v5` shows OFFLINE; v4 `/` continues. `start_all_services.sh` unchanged (hash stable). Test: `test_m9_dashboard_v5_rendering.py::test_start_all_services_hash_stable`.

**Wave E gate**: v4 dashboard at `/` unchanged throughout; v5 at `/v5` renders state_v5.json; kill-switch drill green.

---

## Wave F — Observability hardening + pulled-back M10 items

- [ ] **T50 [P]** — C-10: Wrap `ctx._lifecycle_config` in `MappingProxyType` in `UniverseContext.__post_init__`. Rename `_stopped` → name-mangled `__stopped`. Strategy loader rejects `ctx._lifecycle_config[...] = ...` and `object.__setattr__(ctx, ...)`.
- [ ] **T51 [P]** — C-10: Fix uid leak in quarantine registry: `WeakKeyDictionary` finalizer also removes uid from `_quarantined_uids`.
- [ ] **T52 (after: T51)** — C-10: Wire `_is_quarantined` into all 15 BarProcessor callback dispatch sites. Quarantined strategies skipped for `generate/check_*/filter_entry/on_*`.
- [ ] **T53 [P]** — C-10: Convert stale XPASS markers in v5 suite: `pytest --runxfail -v` → identify → convert to plain passes or `strict=True`.
- [ ] **T54 [P]** — C-10: `BaseStrategy.exception_counter`: class-attr → `__init__` self-attr.
- [ ] **T55 [P]** — C-10: Extend `DEFAULT_MMR_SCHEDULE` to 3-tuples `(upper, mmr, maint_amount)` in `v5/sizing/clamps.py:38-44`. Update `_liquidation_distance_clamp` with tier-blend formula. Verify Binance calculator match within 1 bp on $300k tier-spanning positions.
- [ ] **T56 [P]** — Pulled-M10: Delete `StrategySpec.scale_check_fn` function hook. Migrate any fixtures using it to `Strategy.check_scale(pos, bar_ctx)` Protocol method. Test: `test_m9_scale_hook_cleanup.py`.
- [ ] **T57 (after: T56, T19, T22)** — Pulled-M10: Full strategy signature audit via grep:
  - `grep -rn "bar_ctx.regime" v5/ --include="*.py"` → 0 hits
  - `grep -rn "scale_check_fn" v5/ --include="*.py"` → 0 hits
  - `grep -rn "conviction_score\|TokenBarArrays.priority" v5/ --exclude-dir=tests` → 0 hits
- [ ] **T58 [P]** — Pulled-M10: Implement `v5/sizing/tick_cadence.py::TickCadencePolicy` (real tick-level equity sampling; replaces synthetic nudge in `parity_fixture.py`). Test: `test_m9_tick_cadence_policy.py`.
- [ ] **T59 [P]** — Pulled-M10: Implement `v5/tools/arbitration_analyzer.py` CLI: `python -m v5.tools.arbitration_analyzer --log <path> --starvation-report`. Outputs per-strategy admission rate, displacement chains, tier-boundary analysis. Test: `test_m9_arbitration_analyzer_cli.py`.
- [ ] **T60 [P]** — Risk carry-over: Tighten `test_cross_deep_drawdown_would_force_liquidation` via test-dispute resolution protocol (reviewer subagent approves strengthening): `cross <= 10_000.0` + `iso > cross` → `cross < 0` + `iso > 0`.

**Wave F gate**: full v5 suite green; all 88 M9 tests PASS; observability audits green.

---

## Final Wave — Review + Ship

- [ ] **T61 (after: ALL)** — Run post-implementation FIX + Quant + Risk architect subagent reviews. Iterate until all verdicts PASS. Fix findings.
- [ ] **T62 (after: T61)** — Write `reviews/final.json` with consolidated verdict. Move spec to `.specs/done/m9-cleanups/`. Advance PHASE=complete.
- [ ] **T63 (after: T62)** — Final commit + push. Update memory with session summary.

---

## Dependency Graph (simplified)

```
Wave A (T1-T14): foundation, no deletions
  ├─ T1, T2, T4, T9, T14 — parallel
  ├─ T3 ← T2
  ├─ T5 ← T4
  ├─ T6,T7 ← T5
  ├─ T8 ← T7
  ├─ T10,T13 ← T9
  ├─ T11 ← T9
  └─ T12 ← T10, T11

Wave B (T15-T23): deletions, requires Wave A foundation
  ├─ T15 ← T2, T3
  ├─ T16 ← T2
  ├─ T17 ← T15, T16
  ├─ T18 ← T17
  ├─ T19 ← T18
  ├─ T20 — parallel within Wave B
  ├─ T21 ← T20
  ├─ T22 ← T21
  └─ T23 ← T22

Wave C (T24-T29): AC-S10 bridge
  ├─ T24 ← T20
  ├─ T25 ← T24
  ├─ T26 ← T24, T21
  ├─ T27 ← T26
  ├─ T28 ← T21
  └─ T29 ← T27

Wave D (T30-T43): replay-parity + flag flips + shim deletions
  ├─ T30 — parallel; T31 ← T30
  ├─ T32 ← T31; T33 ← T32
  ├─ T34 — parallel
  ├─ T35 ← T34
  ├─ T36 ← T35; T37 ← T36
  ├─ T38 ← T35
  ├─ T39 ← T38
  └─ T40, T41, T42, T43 — parallel within Wave D

Wave E (T44-T49): dashboard + runner
  ├─ T44, T46, T47, T48 — parallel
  ├─ T45 ← T44
  └─ T49 ← T46, T48

Wave F (T50-T60): observability + M10 pull-backs
  └─ most parallel; T52 ← T51; T57 ← T56, T19, T22

Final (T61-T63): review + ship
```

## Rules

- Work waves in order. Parallel `[P]` tasks within a wave can run in any order.
- Never start a task with unresolved dependencies.
- After each task: update `CURRENT_TASK` file + re-run target test.
- After each wave: commit. Wave-level commit message: `M9 Wave {A..F}: {summary}`.
- Test freeze per Phase 4 rules: test files frozen unless test-dispute resolution protocol fires.
- v4 paper runner (PID /tmp/paper_runner.pid) is LIVE — never touch, never kill.
