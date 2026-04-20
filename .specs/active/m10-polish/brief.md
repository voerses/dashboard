# M10 — Final Polish + Rename + v5/v4 Parallel Ops

**Summary**: The FINAL milestone — ships everything. Remove all remaining v4 compatibility shims, finalize API naming, complete AC-S10 capstone parity, ship paper state migrator + replay-parity fixture generator, rewire all M9 library code into live engine paths, and achieve **v5 engine running in parallel with v4** behind isolated dashboard/state/runner paths so the operator can compare results side-by-side via the `/v5` URL.

**Policy**: NO DEFERRALS. No carry-over to a hypothetical M11. No "scaffold only". Every item in this brief ships or M10 doesn't close.

---

## Meta-Rule: design over code (M5-M10 v5 rebuild mode — final milestone)

This milestone operates under the **design over code** meta-rule, inherited from the M5-M10 collective v5 rebuild policy. M10 is the LAST milestone under this mode. After M10 ships, the engine enters steady-state maintenance mode and reverts to the CLAUDE.md default (`code over specs`).

**Consequences**:
- Hourly-only strategies: bit-identical backtest preservation (AC14-style) — this is the final parity check before v4 is frozen.
- Non-trivial migrations: shadow-replay validation (AC41-style, 4 ULP / 5 bps / 10 bps tolerances) with documented per-strategy deltas in `fleet_behavior_delta.md`.
- Determinism (AC24-style within-build): absolute.
- Sign-off thresholds for documented behavior changes: ≤20 bps auto / 20-100 bps quant / >100 bps design review.
- **Post-M10**: any future milestone re-invoking design-over-code must declare it explicitly in its own brief + reference this precedent.

---

## Problem

After M1-M9, v5 is functionally complete but has accumulated:

1. **v4 compat shims**: Temporary compatibility layers added during incremental migration (import aliases, adapter functions, deprecated parameter handling) that should be removed now that v5 is the primary engine.

2. **Naming inconsistencies**: Different milestones may have introduced slightly different naming conventions (e.g., some modules use `symbol` vs `instrument`, `bar` vs `candle`). A final consistency pass is needed.

3. **Missing documentation**: The Trade Identity Model (parent_position_id, linked trades, FIX alignment) is a novel concept that needs an architecture document. Migration runbook and rollback procedure are needed for safe v4->v5 cutover.

4. **Paper state migration**: If M5 didn't ship the paper state migrator (v1 -> v2 schema), live paper traders can't transition to v5 without losing their state.

5. **CLAUDE.md stale**: The project instructions still reference v4 as the active engine. v4 needs to be formally frozen (like v3 before it).

---

## Scope

### In scope

- **Compat shim removal**: Find and delete all `# v4 compat` or `# backward compat` marked code in v5/. Remove any import aliases that map v4 names to v5 names.
  - **Specific targets** (from 2026-04-20 scope audit):
    * `v5/paper_config.py:39,128` — `confirmation_tiers: dict = ... # deprecated` field + reader → delete
    * `v5/cpcv.py:58-70` — `deflated_sharpe()` DeprecationWarning-raising stub → archive or delete
    * `v5/paper_state.py:1178` — `# v4 compatibility loaders (AC31a, AC33, AC34)` banner → rename comment; keep function bodies
    * `v5/universe.py:180`, `v5/metrics.py:326,336`, `v5/signals.py:434` — review 4 `warnings.warn` call sites; keep only user-facing warnings, delete code-smell ones
    * `v5/tests/test_m7_paper_8site.py:80` — dead `pytest.skip("v5/paper_engine.py not present")` (file exists) → delete

- **Stale v4-reference docstrings**: grep-blind compat rot (not caught by `v4 compat` string):
  - `v5/engine.py:1,9,1511,1544` — module + class docstrings refer to v4 as the "active engine"
  - `v5/validation.py:6,16,18` — same pattern
  - Rewrite to reflect v5-canonical status. No code change; docstrings only.

- **API naming consistency pass**:
  - Audit all public APIs across v5/ modules for consistent terminology
  - Standardize: `symbol` (not `token` or `instrument` for the string identifier), `instrument` (for metadata object), `bar` (not `candle`), `position` (not `trade` for open), `closed_trade` (for completed)
  - Ensure all Protocol method signatures use consistent parameter names

- **Documentation** (4 canonical docs + 1 optional):
  - `knowledge/NAMING_CONVENTIONS.md` — source of truth for AC #2 naming audit (symbol/instrument/bar/position/closed_trade canonical terms + rationale)
  - `knowledge/DASHBOARD_V5.md` — `/v5` URL routing, state_v5.json schema, cutover procedure, kill-switch drill
  - `knowledge/V5_SIZING_SYSTEM.md` — companion to archived V4_SIZING_SYSTEM.md; documents M8 clamp pipeline + M9 SignalArbitrationPolicy + CapitalAllocationPolicy after v4 freeze
  - (optional, nice-to-have) `knowledge/V5_RELEASE_NOTES.md` or `CHANGELOG.md` — 10-milestone summary
  - `knowledge/ARCHITECTURE.md` — Trade Identity Model section with full detail:
    - `parent_position_id: str` — immutable join key (FIX `OrderID(37)` analog)
    - `exec_seq: int` — monotonic per parent_position_id (FIX `ExecID(17)` analog)
    - `exec_type: str` — values: `"reduce"`, `"exit"`, `"linked_reduce"`; RESERVED: `"open"`, `"increase"` (FIX `ExecType(150)`)
    - `is_terminal: bool` — exactly one per parent_position_id has `True` (FIX `OrdStatus(39)=Filled`)
    - `triggered_by: str` — set ONLY by linked-leg auto-propagation (FIX `ExecRestatementReason(378)`)
    - `has_scaling: bool` — True on any ClosedTrade for a position with prior ScalingEvents
    - ScalingEvent schema, ClosedTrade lineage diagram, FIX field mapping table
  - `knowledge/MIGRATION.md` — Step-by-step runbook for migrating a strategy from v4 to v5
  - `knowledge/ROLLBACK.md` — Procedure to revert to v4 if v5 has issues in production

- **`load_trade_log()` compat parser** (in `v5/report.py`): Reads v4 `analysis/*.json` logs; synthesizes identity fields from `:partial` suffix. Fails loudly on mixed-format logs (`:scale_N` suffix without identity fields → `AssertionError`).

- **Paper state migrator**: `v5/migrate_state_v1_to_v2.py` — one-shot converter CLI:
  ```bash
  python -m v5.migrate_state_v1_to_v2 \
      --input state/v4_paper_s513/state.json \
      --output state/v5_paper_s513/state.json
  ```
  - Preserves active positions + armed entries
  - Backs up v1 state as `state.json.v1.bak`
  - Keeps backup until `--commit-migration` flag after N hours green
  - Strips removed fields (`regime`, `partial_tp_*`)
  - Translates `open_positions[] + armed_tokens{}` into unified `open_orders[]` with `legs[]`
  - Adds v5-only identity fields with defaults: `parent_position_id = position_id`, `exec_seq = 0`, `exec_type = "exit"`, `is_terminal = True`, `triggered_by = ""`
  - Splits `partial_fills` counter: old value estimated split via audit log replay if available, else dumps total to `partial_fills`
  - Sets `version: 2`
  - `--dry-run` flag for verification without writing
  - `--reset-state` flag as alternative: clean start, loses active positions (acceptable for paper, avoid if possible)

- **`--commit-migration` flag**: Specific M10 deliverable. After N hours/days running green on v5, user runs `--commit-migration` to move `.v1.bak` files to `backups/v4-archive/{timestamp}/` (retained 90 days, not deleted). Explicit `--purge-archive` required for permanent deletion. Until `--commit-migration`, rollback is instant.

- **CLAUDE.md update**: Add "v4/ is FROZEN as of {date}. All engine, validation, simulation, and universe code lives in v5/." Mirror the existing v3 freeze language exactly: `v4/ is FROZEN. Never modify files in v4/. All engine, validation, simulation, and universe code lives in v5/. The v4/ directory is legacy reference only — all imports have been migrated to v5/.`

- **Memory monitoring** (replay-based, NOT live soak): `v5/run_paper_multi.py` logs RSS every minute in live mode; alerts at 1.2GB threshold; `tracemalloc` snapshot hook available for operator debugging. **Test coverage is replay-based**: `v5/tests/test_m10_memory_growth_replay.py` drives `paper_engine` over a deterministic 24h replay fixture and asserts `RSS_end - RSS_start < 100MB` + tracemalloc top-10 allocators bounded. No test depends on live data collection.

- **`state_schema_version` field** — reconciliation (not addition): M9 v5 paper_state already exposes `STATE_SCHEMA_VERSION = 3` at `v5/paper_state.py:45` + emits via `paper_state.py:1519`. DO NOT add a second field. M10 task: ensure dashboard frontend reads the existing field; delete any v4-only schema assumption.

- **v5 runner default-on cutover** (operational):
  - After replay-parity + short parallel smoke passes (NOT after 48h live soak): `tools/start_all_services.sh` modified to launch `v5.run_paper_multi` instead of `v4.run_paper_multi` (or publish new `tools/start_v5_services.sh` as canonical + archive v4 launcher).
  - `/srv/data/state_v5.json` present + `state.json` symlinked/redirected OR dashboard defaults to v5 URL.
  - Feature flag `V5_PAPER_ENABLED=1` is the default in the v5 runner script after M10 ship.

- **v4 paper runner shutdown procedure** (missing from runbook step 1):
  - Concrete signal: `SIGTERM` via `bash tools/stop_all_services.sh` (existing script); wait 30s for drain; escalate to `SIGKILL` only if PID still alive.
  - Verify `state/v4_paper_multi/paper.pid` removed.
  - Confirm no partial writes to `state/v4_paper_multi/trades.csv` (tail the last row; must be complete trade record).

- **Final test suite audit**: Verify all v5 tests are categorized (unit/integration/parity), no orphaned tests, no tests that only pass due to compat shims.
  - **Actual baseline at M9 close** (verified 2026-04-20): 1671 passed, 7 skipped, 13 xfailed, 2 xpassed — NOT the old ~1,450-1,600 estimate.
  - M10 target: 1671+ passed, ≤2 documented skips (replay-parity fixture-gated until generator ships), 0 xfailed after AC-S10 bridge flips the 6 capstone xfails, 0 orphans.

- **Enhanced scenario coverage** (new test files per ACs #14-#17):
  - `v5/tests/test_m10_pnl_path_invariants.py` — cumulative PnL / equity-identity / watermark-monotonicity over 200-bar fixture
  - `v5/tests/test_m10_daily_pnl_rollover.py` — realized_pnl_today_usd reset at UTC midnight; DailyLossLimit interaction
  - `v5/tests/test_m10_funding_accrual_multi_window.py` — 3-window funding accrual (24h fixture), sign correctness, 00/08/16 UTC snap points
  - `v5/tests/test_m10_liquidation_cascade.py` — 3-position simultaneous liquidation; deterministic ordering; equity floor + HALTED transition

- **Audit-driven new tests (ACs #8, #18-#25)** — all replay-based, none depend on live data collection:
  - `v5/tests/test_m10_memory_growth_replay.py` — replaces 48h-live soak (AC #8)
  - `v5/tests/test_m10_funding_backtest_paper_parity.py` — closes M8 funding-cadence deferral (AC #18)
  - `v5/tests/test_m10_data_engine_default.py` — closes M7 `use_data_engine` deferral (AC #19)
  - `v5/tests/test_m10_rollback_drill.py` — executed rollback rehearsal (AC #20)
  - `v5/tests/test_m10_fee_classification.py` — maker/taker discrimination (AC #21)
  - `v5/tests/test_m10_log_rotation.py` — all JSONL sinks rotate at 500MB (AC #22)
  - `v5/tests/test_m10_clock_drift.py` — WARN/HALT thresholds for exchange-clock skew (AC #23)
  - `v5/tests/test_m10_state_corruption.py` — schema + checksum + uniqueness guards (AC #24)
  - `v5/tests/test_m10_ws_ratelimit_parallel.py` — short-smoke 429 guard (AC #25)

### Paper Migration Runbook (11 steps)

0. **Pre-flight verification**: Run v5 backtest on s524m reference strategy. Verify metrics are reasonable (no NaN, Sharpe > 0, drawdown < 50%). Do NOT stop v4 paper until this passes. Step 0 includes rewriting s513, s523c, s524m for v5 API. These are clean rewrites, not mechanical ports. Rewrite validates: s513 ~2h, s523c ~3h, s524m ~5h.
1. Graceful stop v4 paper at end of day (no mid-bar)
2. Backup `state/v4_paper_*/` to `backups/v4-pre-v5/{timestamp}/`
3. Run `bash tools/check_data_integrity.sh`
4. Per pool: `python -m v5.migrate_state_v1_to_v2 --dry-run --input ... --output ...`; verify output
5. Run real migration; diff open_positions equity sum (MUST match within $0.01)
5.5. **Connection verification**: Start v5 paper in dry-run mode. Verify BinanceWSClient connects, receives bars for all subscribed tokens, DataEngine populates RollingCache. Only proceed to live mode after data flow confirmed.
6. Start v5 paper; verify first-tick equity reconciles with v4 last-tick
6.5. **Alert endpoint verification**: Verify alert endpoints (drawdown_alert_pct threshold, any configured notification channels) fire correctly. Send test alert. Confirm receipt.
7. 24h monitoring: RSS curve, trade-rate vs v4 baseline, alerts smoke-tested
8. After 7 days green: `--commit-migration` moves `.v1.bak` to `backups/v4-archive/{timestamp}/` (retained 90 days, not deleted). Explicit `--purge-archive` required for permanent deletion.
9. If anything regresses: stop v5, restore `.v1.bak`, restart v4

### M9 carry-overs — post-Option-A finish (2026-04-20 final)

**STATUS**: M9 shipped at ~85% (10 commits on `feat/m7-strategy-api`, pushed as `ba1977d..01116ef`). Option A finish (Phases 1-6) landed after the first honest-close commit. Remaining deferrals are scoped + carry real surgical risk — M10 closes them.

#### ✅ COMPLETED in M9 Option A finish (do NOT redo in M10)

- **Actual field deletions**: `_legacy_conv`, `_legacy_regime`, `_legacy_min_conv`, `bear_target_mult`, `bear_max_hold` fields deleted from TokenBarArrays + `engine.py` StrategyContext. 24 call sites in `simulator.py` / `paper_engine.py` migrated with None-safe fallbacks (Phase 1, commit `20cc03a`).
- **`apply_arbitration` wired into engine**: `simulator.py:1391` now calls `_run_arbitration_dispatch()` which invokes `config.arbitration_policy.rank()`. Legacy `_sort_key` deleted. `ArbitrationLogWriter` lazily instantiated on state; writes to `v5/logs/arbitration.jsonl` with full AC #18 schema (Phase 2, commit `533e07a`).
- **Phase 3.0 risk hook wired**: `_apply_risk_components_phase_30()` inserted in `_stage2_process_new_signals` BEFORE arbitration. HALT/REJECT/REDUCE/ACCEPT verdicts honored; `TradingState` flips on halt (Phase 3, commit `533e07a`).
- **Flag flips**: `PortfolioConfig.use_m8_clamps` default → True (legacy branch inlined); `StrategySpec.use_multi_leg_orders` default → True; `Instrument.contract_type` free-string DELETED (Phase 5, commit `fdf197f`). Test-dispute #3 logged.
- **Replay-parity scaffold**: `v5/tools/replay_parity.py` stub + `v5/tests/fixtures/m9_replay_parity_7d/manifest.json` so test collection works (Phase 6, commit `01116ef`).

#### ⏳ REMAINING for M10 (surgical risk — careful)

1. **AC-S10 bridge inner-loop wiring** (~20-30h, the capstone):
   - `simulate_portfolio(strategies=, ctx=)` builds `bridge_signals` dict via `_build_token_bar_arrays_from_generate` but doesn't feed them into the vectorized `_process_orders` / `_process_exits` inner loop — returns `SimulationState` with `_bridge_signals` attached but metrics empty.
   - Wire bridge output into `all_signals` consumed by `simulate_portfolio` inner loop.
   - Thread through `compute_portfolio_metrics` so xfails produce real numbers.
   - 6 strict-xfail tests in `test_m8_ac_s10_s524m_parity.py` must flip to PASS within the per-metric tolerance classes below (AC #10). Parity comparison runs **year-by-year** (NOT full-period compounding) and the annual-sum is the headline number — see per-year baseline below.
   - **v4 baseline regenerated 2026-04-20, per-year** (s524m, capital=$100k, perp, ranked, --adv-cap 0.005, --max-portfolio-positions 50, --skip-wf, seed 42):

     | Year | total_return | sharpe | sortino | calmar | max_dd | trades | win_rate |
     |---|---|---|---|---|---|---|---|
     | 2022 (01-01 → 12-31) | +176.0% | 1.71 | — | — | −33.4% | 124 | 54.0% |
     | 2023 (01-01 → 12-31) | +263.5% | 2.02 | — | — | −45.4% | 275 | 37.8% |
     | 2024 (01-01 → 12-31) | +69.4% | 1.04 | — | — | −72.3% | 313 | 37.7% |
     | 2025 (01-01 → 12-31) | +491.0% | 2.67 | — | — | −37.9% | 541 | 42.7% |
     | 2026 Q1 (01-01 → 03-31) | +39.4% | 2.10 | — | — | −29.3% | 121 | 59.5% |
     | **Annual sum** | **+1039.3%** | — | — | — | — | **1374** | — |

   - v4 baseline command template: `python v4/portfolio_backtest.py --strategy s524m --capital 100000 --market perp --conviction-mode ranked --adv-cap 0.005 --max-portfolio-positions 50 --skip-wf --start-date {YYYY}-01-01 --end-date {YYYY}-12-31 --seed 42` (run once per year 2022-2025, plus Q1-2026 with `--end-date 2026-03-31`).
   - v5 AC-S10 parity test must match ALL 5 per-year runs within the per-metric tolerance classes below (AC #10) AND the annual sum must match v4's 1039.3% within 0.5%.

2. **Replay-parity fixture GENERATOR** (~5-10h):
   - Write `v5/tests/fixtures/generate_m9_replay_parity_7d.py` that builds parquet-windowed slices from `data/perp/1m_cache/` + `data/perp/1h_cache/` engineering each of 6 M8 clamps to bind at least once.
   - Extend `v5/tools/replay_parity.py::run_replay_parity_*` stubs to actually run paper_engine twice + diff archives.
   - Unlocks the 2 currently-skipped tests in `test_m9_replay_parity.py`.

3. **Remaining shim deletions** (replay-parity gate first, then delete):
   - `sizing_legacy.py` — 3 callers (`simulator.py:35`, `paper_engine.py:1629`, `test_v5_portfolio.py:29`). Deletion requires surgery on `simulator.py:1425` `get_sizing_model` resolver + removing `globals()["get_sizing_model"]` alias. Now that `use_m8_clamps` is unconditional, the legacy path is dead — but `sizing_model.compute_size()` is still invoked for initial sizing input to the clamp pipeline. Refactor.
   - `Position.leg: str = "primary"` field — 8 read sites in `simulator.py:413, 541, 589, 1045, 1065, 1191, 2111, 2278`. Migrate to `leg_ref_id` + venue/market lookup.
   - `trigger_combined_entry` legacy branch (~140 lines in `simulator.py:~2740-2877`) — M5 multi-leg OTOCO is now canonical (flag flipped in M9 Phase 5); delete once replay-parity passes on multi-leg fixture.
   - `armed_log.jsonl` dual-write — delete dual-write in `paper_engine.py`; `orders_log.jsonl` sole sink.
   - `paper_engine._armed_tokens` property + alias — T16b migration; delete the back-compat property.
   - `Leg.market` vs `Leg.settlement_type` — pick settlement_type (maps to FIX LegSettlType(587)); drop market.

4. **WalkForwardRunner dispatch cleanup** — currently dispatches M8 vs M9 via `__new__` kwarg detection (back-compat shim added in `1c42b4e`). M10 consolidates to a single canonical M9 runner once AC-S10 bridge is wired.

5. **Strategy re-port refinements**:
   - Once AC-S10 bridge wires, verify s524m, s523c, s513 `to_token_bar_arrays()` outputs byte-identical to `_engine_precompute_fallback` on the multi-year 2022-01-01 → 2026-03-31 fold (AC #7 parity).
   - Once bridge runs, verify `test_m9_c7_bridge_interface.py::TestPaperVsVectorizedParity` actually exercises the paper-vs-vectorized code path (currently exercises the precompute loop on both sides).

### M9 regression-sweep follow-ups (from 2026-04-20 post-audit, commit `92783bb`)

Post-M9 audit uncovered real regressions from the Phase 1 field-deletion
cascade. Sweep fixed 50+ stale-kwarg tests automatically. Two genuine
regressions were marked `pytest.mark.skip` with dispute rationale —
M10 must investigate + close:

1. **`test_m2_scale_dispatch.py::test_scale_action_bar_updated_on_fire`**:
   - Symptom: `pos._scale_action_bar` not updated to `bar_ctx.local_bar`
     during `_dispatch_scale_action`; expected 10, got 0.
   - Root cause unknown — likely collateral from Phase 2/3 simulator
     edits (arbitration + risk hook). Engine still advances `scaling_
     events[0].bar = 0` but Position field stays 0.
   - Pre-M9 state: PASSED (verified against `ba1977d`).
   - M10 task: debug `_dispatch_scale_action` call path; unskip once
     fixed. ~1-2h.

2. **`test_m2_scale_dispatch.py::test_strategy_spec_rejects_scaling_
   with_multi_position`**:
   - Symptom: test asserts `ValueError` when `StrategySpec(
     scale_check_fn=fn, max_positions_per_symbol=2)` is constructed;
     after M9 C-10 deleted the `scale_check_fn` field, the test
     construction fails silently (validate_scaling_compat is a no-op).
   - M10 task: either (a) formally remove the test as obsolete (the
     whole AC20 contract was "scale_check_fn requires
     max_positions_per_symbol==1"; that precondition no longer exists)
     or (b) re-implement the invariant via Strategy Protocol presence
     check (`if Strategy.check_scale implemented AND max_positions_per_
     symbol>1 → ValueError`). Recommend (a) unless there's a
     semantic parallel. ~30min.

3. **Audit test_m7_pre_existing_13 meta-harness for staleness** —
   M10 should reassess whether the 13 tracked nodes still represent
   "pre-existing M7 failures that need to be cleared". 4 of the 13
   are now module-skipped (test_conviction_to_priority). Another pass
   after AC-S10 bridge wires may reveal more that became obsolete
   or got genuinely fixed. ~30min.

4. **Test-dispute telemetry consolidation**: 5 test-dispute events
   landed in `.specs/telemetry.jsonl` during M9. M10's final
   documentation pass (ARCHITECTURE.md / MIGRATION.md) should
   summarize the running tally of M8→M9 spec changes so future
   maintainers can trace the breadcrumbs.

### Items that remain in M10 (genuinely final polish — unchanged)

- Paper state migrator v1→v2 (step 0 pre-flight of migration runbook)
- Documentation (ARCHITECTURE.md, MIGRATION.md, ROLLBACK.md)
- Compat shim removal grep audit + naming consistency pass
- CLAUDE.md v4 freeze notice
- Memory monitoring setup (RSS logging, 1.2GB alert, tracemalloc)
- 48h soak test monitoring
- Final test suite audit

Note on FixedBudgetPolicy: removed from M9 scope per user directive; not deferred to M10. Users who need per-strategy caps write their own `CapitalAllocationPolicy` subclass.

### Out of scope

- New features (this is polish only)
- Strategy migration (user-driven, per-strategy)
- Performance optimization (unless a regression is found)
- v4 deletion (v4 stays as frozen reference, like v3)
- CI/CD pipeline changes

---

## Key Acceptance Criteria

1. **Zero compat shims**: A grep for `v4 compat`, `backward compat`, `# TODO: remove`, `DEPRECATED` (excluding the rename-commented `# deprecated` banner in `v5/paper_state.py:1178` — the renamed banner is a legitimate v4-log loader for dashboard back-compat per AC #5) returns zero results in v5/ non-test code. All temporary bridges deleted.

2. **Naming audit passes**: `knowledge/NAMING_CONVENTIONS.md` exists as the source of truth + a grep script in `tools/audit_naming.sh` verifies zero violations across `v5/*.py` + `v5/strategies/*.py`. Standardized terms: `symbol` (string identifier), `instrument` (metadata object), `bar` (not `candle`), `position` / `closed_trade`.

3. **ARCHITECTURE.md exists**: `knowledge/ARCHITECTURE.md` contains (a) Trade Identity Model (parent_position_id / ScalingEvent / ClosedTrade lineage + diagram), (b) `TickCadencePolicy` wiring path (M9 deliverable not yet documented), (c) running tally of 5 M8→M9 test-dispute spec changes from `.specs/telemetry.jsonl`.

4. **Migration runbook exists**: `knowledge/MIGRATION.md` contains numbered steps for migrating one strategy from v4 to v5, including: code changes, config changes, paper state migration, verification steps, rollback steps. **Concrete v4 shutdown** (SIGTERM via `tools/stop_all_services.sh`, 30s drain, SIGKILL escalation). **Dashboard cutover** (URL, state.json redirect).

5. **Paper state migrator works**: `v5/migrate_state_v1_to_v2.py` converts a v4 paper state file to v5 format. A test round-trips a real v4 state file through the migrator and verifies all positions and orders are preserved.

6. **CLAUDE.md + stale docstrings updated**: (a) CLAUDE.md contains the v4 freeze notice with freeze date; v3 notice unchanged. (b) `v5/engine.py:1,9,1511,1544` + `v5/validation.py:6,16,18` module/class docstrings rewritten to reflect v5-canonical status (no more "v4 is active engine" prose inside v5/).

7. **Full test suite green**: `pytest v5/tests/` exits 0 with 1671+ passed. Allowed: ≤2 replay-parity tests skipped pending fixture-generator ship (AC #9). 0 xfailed after AC-S10 bridge flips the 6 capstone xfails (AC #10). 0 orphaned/dead-skip tests (delete `test_m7_paper_8site.py:80` stale skip).

8. **Replay-based stability test (NOT live soak)**: `test_m10_memory_growth_replay.py` drives `paper_engine` through a deterministic 24h replay fixture (built by the AC #9 fixture generator) and asserts:
    - `RSS_end - RSS_start < 100MB` over the full 24h simulated run
    - `tracemalloc.get_traced_memory()` top-10 allocators all bounded (no unbounded accumulators)
    - Zero state-corruption events (checksum after each `paper_state.persist()` matches on re-load)
    - Trade-rate within ±20% of v4 reference-backtest baseline over the same fixture window
    - **Rationale**: per user directive 2026-04-20, NO M10 test depends on wall-clock live data collection. All stability/memory/parity claims must be reproducible via fixtures + replay.

9. **Replay-parity test + fixture ACTUALLY RUN (not scaffold)**:
    - `v5/tests/fixtures/generate_m9_replay_parity_7d.py` exists and produces a 7-day parquet-windowed fixture forcing each of 6 M8 clamps to bind at least once. Manifest at `v5/tests/fixtures/m9_replay_parity_7d/manifest.json` points to generated slices (status: "ready", not "scaffold").
    - `v5/tools/replay_parity.py::run_replay_parity_m8_clamps` and `run_replay_parity_multi_leg` are REAL implementations (not `pytest.skip` stubs) — they run paper_engine twice with flag=False / flag=True and diff archives.
    - **The 2 tests in `test_m9_replay_parity.py` PASS** (not skip): `test_use_m8_clamps_nonbinding_bars_byte_identical` + `test_use_multi_leg_orders_nonbinding_bars_byte_identical`. Non-binding bars byte-identical; binding bars have matching `sizing_fills.jsonl` entry with documented clamp name.

10. **AC-S10 backtest-vs-backtest parity CLOSED (the capstone)** — audit-tightened:
    - 6 strict-xfail tests in `test_m8_ac_s10_s524m_parity.py` flip to PASS on the multi-year 2022-01-01 → 2026-03-31 fold (see below for v4 command + baseline). `@pytest.mark.xfail(strict=True)` decorators DELETED (not left as XPASS — the decorator itself is removed and the tests must affirmatively pass).
    - **Positive-assertion guards (mandatory, must run BEFORE the parity comparisons)**:
        - `assert len(state.closed_trades) > 0` — at least one trade materialized
        - `assert len(state.equity_curve) == n_bars` — equity curve populated across the full fold
        - `assert state.cum_pnl_usd[-1] != 0.0` — cumulative PnL is non-zero (rejects silent-zero scaffolding)
        - `assert not hasattr(state, "_bridge_signals")` — the private attribute has been deleted
    - **Per-metric tolerance classes (NOT uniform 0.5%)** — Phase 2 design must document provenance for each; default values below:
        - `total_return` (annual sum): rtol ≤ **0.5%**
        - `sharpe`, `sortino`: rtol ≤ **5%** (noise floor dominated by per-trade stop-path variance)
        - `max_drawdown`: rtol ≤ **10%** (path-dependent peak-trough; v4 per-year DD ranges −29% to −72% with 2024 structural drawdown dominating)
        - `calmar`: rtol ≤ **5%**
        - `turnover` (trades/year): rtol ≤ **10%**
        - `win_rate`: rtol ≤ **2%** absolute
    - s524m v5 backtest metrics match v4 backtest **per-year** (5 separate runs: 2022, 2023, 2024, 2025, Q1-2026) within the above class tolerances; the annual sum matches v4's 1039.3% within 0.5%.
    - `WalkForwardRunner.__new__` dispatch shim at `v5/validation.py:1275-1298` CONSOLIDATED to a single canonical M9 runner (no more M8-vs-M9 kwarg routing).
    - Real engine call path exercises: precompute arrays → vectorized `_process_exits` / `_process_margin_calls` / `_process_orders` → `compute_portfolio_metrics`. Not an array-builder that discards output.

10b. **Backtest-vs-paper parity (v5 intra-mode consistency)**:
    - `test_m8_paper_backtest_parity.py` stays green (already passing 8/8 — keep it green through M10).
    - NEW AC: 1-day v5 paper-tick replay through `BarProcessor` vs v5 backtest over the same bar range produces byte-identical `TokenBarArrays` per strategy (verified for s524m + s523c via `test_m9_c7_bridge_interface.py::TestPaperVsVectorizedParity`, currently passing but paper path is synthetic — M10 wires real paper ticks).

11. **M9 regression skips + test_m7_pre_existing_13 staleness closed (concrete, not hand-wave)**:
    - `test_m2_scale_dispatch.py::test_scale_action_bar_updated_on_fire` unskipped + passing (debug `_dispatch_scale_action` write site).
    - `test_m2_scale_dispatch.py::test_strategy_spec_rejects_scaling_with_multi_position` either formally retired (scale_check_fn deleted; contract gone) or re-implemented via Strategy Protocol presence check.
    - **`test_m7_pre_existing_13` concrete deliverable** (replaces the M9 "30min audit" hand-wave): produce `.specs/active/m10-polish/m7_pre_existing_13_audit.md` with a 13-row table (node_id × outcome × M10 action). Outcome per node: PASSING-in-v5 / OBSOLETE-delete-node / STILL-RED-debug-and-fix. STILL-RED rows must be resolved (fix or formally retire with telemetry entry) before AC #11 closes. No "audit later" residue.

12. **v5/v4 parallel-ops SHORT smoke test — dashboard side-by-side works (NOT a 48h soak)**:
    - **Scope per user directive 2026-04-20**: parallel-run is a wiring-verification smoke, not a parity gate. Parity evidence lives in AC #9 (replay) + AC #10 (backtest-to-backtest) + AC #8 (replay-based stability). Parallel-run confirms the dashboard + both runners come up cleanly for visual comparison; operator tears down when satisfied.
    - **Both runners START simultaneously**: v4 runner at PID `/tmp/paper_runner.pid` writes `/srv/data/state.json` → rendered at `/` (legacy). v5 runner at PID `/tmp/paper_runner_v5.pid` writes `/srv/data/state_v5.json` → rendered at `/v5` (URL path detection from M9 C-8).
    - **Zero cross-contamination**: `tools/start_all_services.sh` launches v4 (unchanged for M10 duration); `tools/start_v5_paper.sh` launches v5 (feature-flagged, M9-landed). Both run under separate PID files, state dirs, log files, dashboard URLs.
    - **Dashboard wiring verified**: `/srv/dashboard/current/index.html` path-aware JS routes requests correctly; both views load, refresh, and render without interfering. Dashboard headers label "V4 Live" and "V5 Live" distinctly.
    - **30-60 minute wall-clock smoke**: both runners alive simultaneously for a short window; operator visually confirms dashboard rendering both views; verifies WS rate-limit collision guard (AC #25) passes; tears down. No multi-day equity drift check — that is NOT the parallel-run's job.
    - **Full engine wiring** (during the smoke window, every M9-shipped library piece actually executes in the v5 runner):
        - `apply_arbitration` + `ArbitrationLogWriter` → writing `v5/logs/arbitration.jsonl` during live bars
        - `RiskComponent` Phase 3.0 hook → firing on every entry candidate
        - `CapitalAllocationPolicy` → invoked at bar_close per sampling_cadence
        - `TickCadencePolicy` → wired if tick-cadence policy configured
        - Dashboard shows binding_constraint column (from M8 sizing_fills.jsonl JOIN)
        - FIX-aligned counters visible in state_v5.json (partial_fills, increase_fills, contingent_fills, entry_scale_downs)
    - **`tools/start_all_services.sh` default flip** (end of M10): modified to launch `v5.run_paper_multi` as canonical — gated by AC #9 (replay-parity green) + AC #10 (backtest-vs-backtest PASS) + AC #8 (replay stability green) + this AC's smoke. v4 runner script moved to `tools/start_v4_legacy.sh` for reference.

13. **Remaining shim deletion cascade complete**: `sizing_legacy.py` deleted + `globals()["get_sizing_model"]` hack in `v5/simulator.py:31-41` removed; `Position.leg` string field deleted + 8 simulator read-sites migrated to `leg_ref_id`; `trigger_combined_entry` legacy branch (~140 lines) deleted; `armed_log.jsonl` dual-write deleted; `paper_engine._armed_tokens` alias deleted; `Leg.market` deleted (keep `settlement_type`).

14. **Multi-bar PnL accrual invariants**: new `test_m10_pnl_path_invariants.py` verifies over a 200-bar fixture:
    - cumulative `realized_pnl_today_usd` at bar N equals sum of all ClosedTrade pnls closed at bar ≤ N
    - `portfolio_equity` at bar N = `initial_capital + sum(realized_pnl) + sum(unrealized_pnl_at_N)` (fundamental identity)
    - `max_equity_watermark` is monotone non-decreasing
    - equity curve matches reconstructed-from-trades equity curve bit-identical

15. **Realized-PnL day-boundary rollover** (DailyLossLimit dependency):
    - `test_m10_daily_pnl_rollover.py` simulates 48h fixture crossing a UTC-midnight boundary
    - verifies `realized_pnl_today_usd` resets to 0 at 00:00:00 UTC
    - verifies DailyLossLimit halt state clears at the new day (or persists per config)
    - verifies PnL accrued pre-boundary is captured in lifetime cumulative stats but excluded from day counter

16. **Funding cost integration across 8h windows**:
    - `test_m10_funding_accrual_multi_window.py` runs a 24h (3-window) fixture with positive and negative funding rates
    - verifies Position.cumulative_funding accumulates correctly across 3 funding snaps
    - verifies equity MTM correctly incorporates funding cost at each window edge
    - verifies `sign(direction) × funding_rate × notional` math at each window
    - verifies funding snap happens at 00:00, 08:00, 16:00 UTC (no drift)

17. **Liquidation cascade** (multiple positions liquidate same bar):
    - `test_m10_liquidation_cascade.py` constructs a 3-position fixture where all 3 cross their liq threshold in the same bar (market-wide crash scenario)
    - verifies all 3 positions close in the same bar
    - verifies deterministic ordering (by liq_distance ascending, then strategy_id+token lex tie-break)
    - verifies `SimulationState.portfolio_equity` never goes negative even under simultaneous cascade
    - verifies `state.trading_state` flips to HALTED if drawdown threshold breached mid-cascade

18. **Funding cross-path parity (M8 deferral CLOSED)**:
    - New `test_m10_funding_backtest_paper_parity.py` opens a position at a non-window-aligned bar (e.g. 03:17 UTC) and runs the SAME fixture through:
        * `simulator.run_backtest` → produces `v5/logs/funding_accruals.jsonl`
        * `paper_engine` under `TestClock` with deterministic 1m bars → produces `v5/logs/funding_accruals.jsonl`
    - Asserts both archives are byte-identical across 3 funding snaps (00/08/16 UTC)
    - Asserts the 08:00 UTC debit on a position opened at 03:17 is charged identically on both paths (no lump-sum-vs-time-weighted asymmetry; M8 left this open, M10 closes it per user directive "everything deferred MUST be implemented")
    - Gates removal of the "funding cadence asymmetry" note from ARCHITECTURE.md

19. **Backtest `use_data_engine=True` default flip (M7 deferral CLOSED)**:
    - `DataEngine` becomes the default backtest data source; the old `_engine_precompute_fallback` path is removed or explicitly gated to `use_data_engine=False` for debug-only use (and that flag is removed entirely if no other consumers).
    - AC-S10 parity fold (5 per-year runs 2022-01-01 through 2026-03-31) produces byte-identical precomputed TokenBarArrays on both paths (DataEngine vs fallback) before the fallback is deleted.
    - M7 brief line 91 "still deferred to M9+" contract honored here — M10 is the last milestone so this cannot carry further.
    - Regression-guarded by `test_m10_data_engine_default.py` asserting `PortfolioConfig.use_data_engine` default == `True` and that the `use_data_engine=False` branch either raises `DeprecatedPathError` or is deleted.

20. **Rollback drill executed (not just documented)**:
    - `ROLLBACK.md` is REHEARSED — not just written — via a replay-based drill:
    - `test_m10_rollback_drill.py` runs the following sequence against a sandbox state dir (`state/v5_paper_multi_rehearsal/`):
        1. Seed a v4 state file; run migrator → v5 state
        2. Inject 500 bars of v5 paper activity via replay fixture (trades, fills, closed_trades written to state.json)
        3. Execute `ROLLBACK.md` steps programmatically (stop v5, restore `.v1.bak`, restart v4)
        4. Verify v4 loads the restored state, equity continuity within $0.01, no position loss
    - A rollback procedure that has never been executed is a design document, not a rollback. This AC removes that risk.

21. **Maker/taker fee discrimination**:
    - New `test_m10_fee_classification.py` verifies:
        * An `order_type='market'` entry (or triggered entry that crosses the book) charges TAKER fee
        * An `order_type='limit'` entry that rests and fills charges MAKER fee
        * ClosedTrade `entry_fee` / `exit_fee` math == `fill_size × fill_price × {maker|taker}_bps` from `v5/data/instruments.py` Binance schedule
    - No M9 test covers maker/taker discrimination today — this closes the gap before live-cutover.

22. **Log rotation policy (all JSONL sinks)**:
    - `v5/logs/LogRotator` class (or equivalent) applies to every JSONL sink under `v5/logs/`: `arbitration.jsonl` (already rotates per M9 AC #18), `sizing_fills.jsonl`, `orders_log.jsonl`, `funding_accruals.jsonl`, `paper_runner_v5.log`, `risk_decisions.jsonl` (new per AC #23 below if emitted).
    - Rotate at 500MB; gzip rotated file; keep last 10; configurable via env var.
    - `test_m10_log_rotation.py` writes >500MB synthetic payload to each sink and asserts rotation fires + gzip produced + active sink is newly opened.

23. **Clock-drift detection (paper runner)**:
    - Every 60s, `v5/run_paper_multi.py` compares `LiveClock.now_ns()` vs `BinanceRESTClient.server_time_ns()`:
        * `|delta| > 500ms` → WARN to `v5/logs/clock_drift.jsonl`
        * `|delta| > 5s` → HALT (flip TradingState to HALTED; no new entries; existing exits allowed)
    - `test_m10_clock_drift.py` uses a monkey-patched server-clock to verify WARN + HALT thresholds fire correctly.
    - `v5/clock.py` exists but has no drift check today — closes that gap.

24. **State-corruption guard on load**:
    - `paper_state.load()` validates:
        * `schema_version == 3` (else raise `StateSchemaMismatchError` pointing to `.v1.bak` restore procedure)
        * Checksum of `active_positions[]` + `open_orders[]` present and matches in-file checksum
        * Every `active_position.position_id` appears at most once; every `open_order.order_id` appears at most once
    - `test_m10_state_corruption.py` corrupts a state file (wrong schema, mutated checksum, duplicate position_id) and asserts load raises with a user-actionable message.

25. **WS rate-limit handling — split into pytest (retry logic) + operator smoke (live)**:
    - **Rationale (quant expert review 2026-04-20)**: Binance WS rate limits are wall-clock phenomena enforced on the exchange edge; TestClock-accelerated tests would only exercise your own mock. Split into two deliverables.
    - **AC #25a — pytest unit test (fast, deterministic)**: `test_m10_ws_backoff_retry.py` verifies the `[30, 60, 120]` backoff sequence at `paper_engine.py:857` fires correctly given a mocked 429 response. Covers YOUR retry code. In the normal pytest suite.
    - **AC #25b — operator smoke script (live, NOT pytest)**: `tools/ws_ratelimit_parallel_smoke.sh` — operator runs against Binance live endpoint for 30 min during cutover (both v4 + v5 runners alive). Monitors `/srv/data/ws_errors.jsonl`; asserts `count(429) == 0` OR backoff triggered. Documented in `MIGRATION.md` as step 6.5 (connection verification). NOT gated by wall-clock inside pytest.

26. **Zero test warnings** (743 → 0):
    - Baseline measured 2026-04-20: 1761 passed with **743 warnings** (UserWarning ×~739, PytestCollectionWarning ×3, Pandas4Warning ×1).
    - **~739 UserWarning** at `v5/signals.py:434` (s56 combined-mode fallback) — downgrade to `logger.debug()` OR emit once-per-strategy via a `_warned_set` sentinel (not once-per-token-per-test). Fallback behavior itself stays correct; only the emission policy changes.
    - **3 PytestCollectionWarning** at `v5/testing.py:17` — set `TestClock.__test__ = False` so pytest stops trying to collect the helper class.
    - **1 Pandas4Warning** at `v5/data_resampler.py:270` — drop the deprecated `copy=False` kwarg or switch to `.copy()`; pandas 3.0 Copy-on-Write makes it a no-op anyway.
    - **Hardening**: add `filterwarnings = error` to `pytest.ini` (v5 test scope) so any future warning hard-fails the suite. Test run at M10 close: `pytest v5/tests/` reports `0 warnings`.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **ALL (M1-M9)** | **Required** — This is the final milestone; all others must be complete |

---

## Time Estimate

**50-70 hours** (revised — M9 deferrals + original polish scope)

M9 Option-A carry-overs (surgical):
- ~20-30h: AC-S10 bridge inner-loop wiring — 6 xfails flip to PASS within 0.5% tolerance
- ~5-10h: Replay-parity fixture generator + 2 currently-skipped test unlocks
- ~6-10h: `sizing_legacy.py` + `get_sizing_model` resolver surgery
- ~3-4h: `Position.leg` field deletion + 8 simulator read-site migration
- ~2-3h: `trigger_combined_entry` legacy branch deletion (~140 lines)
- ~1-2h: `armed_log.jsonl` dual-write + `_armed_tokens` alias deletion
- ~1h: `Leg.market` drop (keep `settlement_type`)
- ~1h: WalkForwardRunner dispatch cleanup (single canonical M9 runner)

M9 regression-sweep follow-ups (from post-audit):
- ~1-2h: debug `_dispatch_scale_action` — fix `_scale_action_bar` write
- ~30min: resolve `test_strategy_spec_rejects_scaling_with_multi_position` (obsolete vs Protocol-based reimpl)
- ~1-2h: `test_m7_pre_existing_13` 13-row audit table + per-node resolution (was 30min hand-wave; promoted per AC #11 tightening)
- ~30min: test-dispute telemetry consolidation into ARCHITECTURE.md

Post-audit scope additions (from 2026-04-20 M10 brief review):
- ~30min: delete `confirmation_tiers` in `v5/paper_config.py:39,128` + dead `deflated_sharpe` stub in `v5/cpcv.py:58-70`
- ~30min: rewrite 6 stale v4-reference docstrings in `v5/engine.py` + `v5/validation.py`
- ~30min: delete stale `pytest.skip` at `v5/tests/test_m7_paper_8site.py:80` + audit 4 `warnings.warn` sites
- ~1h: v5-runner default-on cutover (modify `tools/start_all_services.sh` or replace with canonical v5 launcher; publish `/srv/data/state_v5.json`)
- ~1h: document concrete v4 shutdown procedure in MIGRATION.md step 1
- ~30min: reconcile `state_schema_version` field vs existing `STATE_SCHEMA_VERSION=3` at `v5/paper_state.py:45`
- ~1h: write `NAMING_CONVENTIONS.md` + `audit_naming.sh` script (source of truth for AC #2)
- ~1h: write `DASHBOARD_V5.md` + `V5_SIZING_SYSTEM.md` (or mark one-or-both as nice-to-have deferral)

Original M10 polish:
- ~3h: Compat shim removal + grep verification
- ~2h: Naming consistency audit + fixes
- ~4h: Documentation (ARCHITECTURE.md with Trade Identity Model detail, MIGRATION.md, ROLLBACK.md)
- ~3h: Paper state migrator + `--commit-migration` flag + `load_trade_log()` compat parser
- ~1h: CLAUDE.md update (stale v4-reference docstrings handled separately above)
- ~1h: Memory monitoring setup (RSS logging, 1.2GB alert, tracemalloc snapshots)
- ~2h: Final test audit + cleanup (baseline: 1671 passed, target: 1671+ passed with 0 xfail post-AC-S10)
- ~1h: Short parallel-ops smoke verification (30-60 min wall-clock; WS rate-limit collision check per AC #25)

Enhanced scenario coverage (ACs #14-#17):
- ~1-1.5h: `test_m10_pnl_path_invariants.py` — 200-bar fixture + 4 invariants (cumulative = ΣClosedTrade.pnl, equity identity, watermark monotone, equity-curve reconstruction)
- ~1-1.5h: `test_m10_daily_pnl_rollover.py` — 48h UTC-midnight fixture + DailyLossLimit interaction
- ~1-1.5h: `test_m10_funding_accrual_multi_window.py` — 24h 3-window fixture at 00/08/16 UTC; sign correctness + MTM integration
- ~1-1.5h: `test_m10_liquidation_cascade.py` — 3-position simultaneous-crash fixture; deterministic ordering + equity floor + HALTED flip

Audit-driven additions (ACs #8, #18-#25 — all replay-based, no live-wall-clock tests):
- ~2h: `test_m10_memory_growth_replay.py` — 24h replay fixture + RSS + tracemalloc asserts (replaces 48h live soak per user directive)
- ~3-4h: `test_m10_funding_backtest_paper_parity.py` + TestClock paper harness — closes M8 funding-cadence deferral (AC #18)
- ~4-6h: Backtest `use_data_engine=True` default flip + parity verification + fallback-path deletion (AC #19)
- ~2-3h: `test_m10_rollback_drill.py` — executed rollback drill against sandbox state dir (AC #20)
- ~1-1.5h: `test_m10_fee_classification.py` — maker/taker discrimination (AC #21)
- ~2h: `LogRotator` implementation + `test_m10_log_rotation.py` covering all JSONL sinks (AC #22)
- ~2h: Clock-drift detector in `run_paper_multi.py` + `test_m10_clock_drift.py` (AC #23)
- ~2h: State-corruption guard in `paper_state.load()` + `test_m10_state_corruption.py` (AC #24)
- ~1h: `test_m10_ws_ratelimit_parallel.py` short-smoke 429 guard (AC #25)
- ~30-60min: zero-warnings cleanup (AC #26) — s56 fallback emission policy, `TestClock.__test__`, pandas `copy=False`, `filterwarnings=error` in pytest.ini

Per-metric tolerance provenance work for AC #10 (replaces blanket 0.5%):
- ~2h: Phase-2 design note justifying per-metric rtol classes (return 50bp / Sharpe-Sortino-Calmar 5% / drawdown 10% / turnover 10% / win_rate 2% abs)

**Revised total: 82-111 hours** (up from 64-88h; +18-23h for 8 new audit-driven ACs + tolerance-provenance work + zero-warnings AC #26; AC-S10 wiring work unchanged).

**Realism note**: Given M5 (scoped 100h → 110h shipped), M6 (120h → 135h), M9 (150-200h), a 110h ceiling still carries downside risk — if AC-S10 wiring exposes a structural vectorized-vs-per-bar semantic divergence, add 30-50h. The "NO DEFERRALS" policy means that risk must be absorbed inside M10, not kicked.

---

## Parity Gate

- **Full v5 test suite**: 1671+ passed, 0 failed, 0 skipped (all prior skips resolved — replay-parity fixture-generator ships per AC #9; `test_m7_paper_8site.py:80` stale skip deleted; `test_m2_scale_dispatch` regressions closed per AC #11), 0 xfailed, 0 xpassed, **0 warnings** (AC #26 closes the 743-warning baseline). No residual skips allowed at M10 close per "NO DEFERRALS" policy.
- **AC-S10 backtest-vs-backtest parity CLOSED**: 6 `strict=True` xfails in `test_m8_ac_s10_s524m_parity.py` have their decorators DELETED + positive-assertion guards PASS (`closed_trades` non-empty, `equity_curve` populated, `cum_pnl_usd[-1]` non-zero, `_bridge_signals` attribute deleted) + per-metric tolerances met on each of 5 per-year runs (2022, 2023, 2024, 2025, Q1-2026): return ≤50bp, Sharpe/Sortino/Calmar ≤5%, drawdown ≤10%, turnover ≤10%, win_rate ≤2% absolute; annual sum matches v4 1039.3% ±0.5%.
- **Replay-parity tests ACTUALLY PASS** (not skip): both tests in `test_m9_replay_parity.py` green; fixture generator shipped; real diff-runner wiring complete.
- **v5/v4 parallel-ops SMOKE confirms dashboard wiring** (NOT a multi-day soak per user directive): both runners come up simultaneously, `/` renders v4 state.json, `/v5` renders state_v5.json, WS rate-limit 429 count == 0 over a 30-60 min window (AC #25); operator tears down after visual verification.
- **Replay-based stability**: `test_m10_memory_growth_replay.py` green — 24h replay fixture, RSS growth <100MB, tracemalloc bounded, zero state-corruption events. Replaces any live-wall-clock soak per user directive.
- **Deferred-items CLOSED** (M7 + M8 carry-overs): funding cross-path parity (AC #18) green; backtest `use_data_engine=True` default flipped (AC #19); fallback path deleted; rollback drill executed (AC #20) with $0.01 equity continuity.
- **v5-runner default-on cutover**: `tools/start_all_services.sh` launches v5; `/srv/data/state_v5.json` populated; dashboard routes correctly. Gated by replay-parity + AC-S10 + replay-stability + short parallel smoke — NOT by a live soak.
- **Documentation canonical**: ARCHITECTURE.md / MIGRATION.md / ROLLBACK.md / NAMING_CONVENTIONS.md / DASHBOARD_V5.md / V5_SIZING_SYSTEM.md all exist + non-empty.
- **Grep-zero invariants**: `v4 compat`, `backward compat`, `# TODO: remove`, `DEPRECATED` (excl. 1 allowlisted banner per AC #1), `confirmation_tiers`, stale v4-active-engine docstrings, `xfail(strict=True)` on AC-S10 nodes → 0 hits in v5/ non-test.
- **M9 regression skips closed**: both `test_m2_scale_dispatch.py` skips resolved (unskipped-passing OR formally retired per AC #11); `test_m7_pre_existing_13` 13-row audit produced with per-node resolution.
- **12+ shim deletion cascade verified**: `sizing_legacy.py`, `Position.leg`, `trigger_combined_entry`, `armed_log.jsonl`, `_armed_tokens`, `Leg.market`, `confirmation_tiers`, `cpcv.deflated_sharpe` stub, `use_data_engine=False` fallback branch, `WalkForwardRunner.__new__` dispatch shim — all deleted; no grep hits.
- **Operational guards live**: log rotation for all JSONL sinks (AC #22), clock-drift detector (AC #23), state-corruption guard (AC #24) all in place with passing tests.
- **CLAUDE.md v4 freeze notice in place**; v5 paper runner is live; v4 is frozen reference.
