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

- **Memory monitoring**: `v5/run_paper_multi.py` logs RSS every minute; alerts at 1.2GB threshold. Periodic `tracemalloc` snapshot every 6h for first week post-deploy. Memory soak test validates RSS growth < 100MB over 24h simulated paper run.

- **`state_schema_version` field** — reconciliation (not addition): M9 v5 paper_state already exposes `STATE_SCHEMA_VERSION = 3` at `v5/paper_state.py:45` + emits via `paper_state.py:1519`. DO NOT add a second field. M10 task: ensure dashboard frontend reads the existing field; delete any v4-only schema assumption.

- **v5 runner default-on cutover** (operational):
  - Post-48h-soak: `tools/start_all_services.sh` modified to launch `v5.run_paper_multi` instead of `v4.run_paper_multi` (or publish new `tools/start_v5_services.sh` as canonical + archive v4 launcher).
  - `/srv/data/state_v5.json` present + `state.json` symlinked/redirected OR dashboard defaults to v5 URL.
  - Feature flag `V5_PAPER_ENABLED=1` is the default in the v5 runner script after M10 ship.

- **v4 paper runner shutdown procedure** (missing from runbook step 1):
  - Concrete signal: `SIGTERM` via `bash tools/stop_all_services.sh` (existing script); wait 30s for drain; escalate to `SIGKILL` only if PID still alive.
  - Verify `state/v4_paper_multi/paper.pid` removed.
  - Confirm no partial writes to `state/v4_paper_multi/trades.csv` (tail the last row; must be complete trade record).

- **Final test suite audit**: Verify all v5 tests are categorized (unit/integration/parity), no orphaned tests, no tests that only pass due to compat shims.
  - **Actual baseline at M9 close** (verified 2026-04-20): 1671 passed, 7 skipped, 13 xfailed, 2 xpassed — NOT the old ~1,450-1,600 estimate.
  - M10 target: 1671+ passed, ≤2 documented skips (replay-parity fixture-gated until generator ships), 0 xfailed after AC-S10 bridge flips the 6 capstone xfails, 0 orphans.

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
   - 6 strict-xfail tests in `test_m8_ac_s10_s524m_parity.py` must flip to PASS within 0.5% relative tolerance on Q-DEC4 2025 206-token fold.
   - s524m v5 target: 1,094% annual sum v4 baseline match.

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
   - Once AC-S10 bridge wires, verify s524m, s523c, s513 `to_token_bar_arrays()` outputs byte-identical to `_engine_precompute_fallback` on the Q-DEC4 2025 fold (AC #7 parity).
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

8. **48h paper soak**: v5 paper trader runs 48 hours without errors; RSS growth < 100MB over the window; trade-rate within ±20% of v4 baseline; zero state-corruption events. Measured via `v5/run_paper_multi.py` memory monitor + trade-count diff.

9. **Replay-parity test + fixture ACTUALLY RUN (not scaffold)**:
    - `v5/tests/fixtures/generate_m9_replay_parity_7d.py` exists and produces a 7-day parquet-windowed fixture forcing each of 6 M8 clamps to bind at least once. Manifest at `v5/tests/fixtures/m9_replay_parity_7d/manifest.json` points to generated slices (status: "ready", not "scaffold").
    - `v5/tools/replay_parity.py::run_replay_parity_m8_clamps` and `run_replay_parity_multi_leg` are REAL implementations (not `pytest.skip` stubs) — they run paper_engine twice with flag=False / flag=True and diff archives.
    - **The 2 tests in `test_m9_replay_parity.py` PASS** (not skip): `test_use_m8_clamps_nonbinding_bars_byte_identical` + `test_use_multi_leg_orders_nonbinding_bars_byte_identical`. Non-binding bars byte-identical; binding bars have matching `sizing_fills.jsonl` entry with documented clamp name.

10. **AC-S10 backtest-vs-backtest parity CLOSED (the capstone)**:
    - 6 strict-xfail tests in `test_m8_ac_s10_s524m_parity.py` flip to PASS within **0.5% relative tolerance** on Q-DEC4 2025 206-token fold.
    - s524m v5 backtest total_return / sharpe / sortino / calmar / max_drawdown all match v4 backtest (1,094% annual sum baseline) within 0.5%.
    - `SimulationState._bridge_signals` private attribute DELETED after real wiring lands (not just "attached for inspection").
    - `WalkForwardRunner.__new__` dispatch shim at `v5/validation.py:1275-1298` CONSOLIDATED to a single canonical M9 runner (no more M8-vs-M9 kwarg routing).
    - Real engine call path exercises: precompute arrays → vectorized `_process_exits` / `_process_margin_calls` / `_process_orders` → `compute_portfolio_metrics`. Not an array-builder that discards output.

10b. **Backtest-vs-paper parity (v5 intra-mode consistency)**:
    - `test_m8_paper_backtest_parity.py` stays green (already passing 8/8 — keep it green through M10).
    - NEW AC: 1-day v5 paper-tick replay through `BarProcessor` vs v5 backtest over the same bar range produces byte-identical `TokenBarArrays` per strategy (verified for s524m + s523c via `test_m9_c7_bridge_interface.py::TestPaperVsVectorizedParity`, currently passing but paper path is synthetic — M10 wires real paper ticks).

11. **M9 regression skips closed**: `test_m2_scale_dispatch.py::test_scale_action_bar_updated_on_fire` unskipped + passing (debug `_dispatch_scale_action` write site). `test_m2_scale_dispatch.py::test_strategy_spec_rejects_scaling_with_multi_position` either formally retired (scale_check_fn deleted; contract gone) or re-implemented via Strategy Protocol presence check.

12. **v5/v4 parallel ops — dashboard side-by-side comparison (THE M10 operational AC)**:
    - **Both runners LIVE simultaneously**: v4 runner at PID `/tmp/paper_runner.pid` writes `/srv/data/state.json` → rendered at `/` (legacy). v5 runner at PID `/tmp/paper_runner_v5.pid` writes `/srv/data/state_v5.json` → rendered at `/v5` (URL path detection from M9 C-8).
    - **Zero cross-contamination**: `tools/start_all_services.sh` launches v4 (unchanged for M10 duration); `tools/start_v5_paper.sh` launches v5 (feature-flagged, M9-landed). Both run under separate PID files, state dirs, log files, dashboard URLs.
    - **Dashboard wiring verified**: `/srv/dashboard/current/index.html` path-aware JS routes requests correctly; both views load, refresh, and render without interfering. Dashboard headers label "V4 Live" and "V5 Live" distinctly.
    - **Side-by-side equity + trade-rate comparison**: the operator can open two browser tabs (`/` and `/v5`) and visually compare equity curves, open-position counts, MTM, trade-rate. At the end of 48h soak, v5 trade-rate within ±20% of v4 AND v5 equity drift within ±3% of v4 (documented in soak log).
    - **Full engine wiring**: every M9-shipped library piece actually executes in the v5 runner:
        - `apply_arbitration` + `ArbitrationLogWriter` → writing `v5/logs/arbitration.jsonl` during live bars
        - `RiskComponent` Phase 3.0 hook → firing on every entry candidate
        - `CapitalAllocationPolicy` → invoked at bar_close per sampling_cadence
        - `TickCadencePolicy` → wired if tick-cadence policy configured
        - Dashboard shows binding_constraint column (from M8 sizing_fills.jsonl JOIN)
        - FIX-aligned counters visible in state_v5.json (partial_fills, increase_fills, contingent_fills, entry_scale_downs)
    - **`tools/start_all_services.sh` default flip** (end of M10): modified to launch `v5.run_paper_multi` as canonical — AFTER 48h side-by-side soak verifies parity. v4 runner script moved to `tools/start_v4_legacy.sh` for reference.

13. **Remaining shim deletion cascade complete**: `sizing_legacy.py` deleted + `globals()["get_sizing_model"]` hack in `v5/simulator.py:31-41` removed; `Position.leg` string field deleted + 8 simulator read-sites migrated to `leg_ref_id`; `trigger_combined_entry` legacy branch (~140 lines) deleted; `armed_log.jsonl` dual-write deleted; `paper_engine._armed_tokens` alias deleted; `Leg.market` deleted (keep `settlement_type`).

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
- ~30min: re-audit `test_m7_pre_existing_13` meta-harness staleness
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
- ~2h: 48h soak test monitoring (async)

**Revised total: 60-82 hours** (up from 53-74h).

---

## Parity Gate

- **Full v5 test suite**: 1671+ passed, 0 failed, ≤2 documented skips (replay-parity fixture-gated is acceptable or should be closed), 0 xfailed, 0 xpassed (all trip-wires resolved)
- **AC-S10 backtest-vs-backtest parity CLOSED**: 6 strict-xfail tests in `test_m8_ac_s10_s524m_parity.py` flip to PASS within 0.5% on Q-DEC4 2025 206-token fold (v4 s524m baseline 1,094% ± 0.5%); `_bridge_signals` private attribute removed
- **Replay-parity tests ACTUALLY PASS** (not skip): both tests in `test_m9_replay_parity.py` green; fixture generator shipped; real diff-runner wiring complete
- **v5/v4 parallel-ops dashboard LIVE**: both runners alive simultaneously during 48h soak; `/` renders v4 state.json, `/v5` renders state_v5.json, both refresh with fresh data; operator visually confirms via the dashboard
- **Paper trader 48h soak**: v5 runner runs 48h clean; RSS growth < 100MB; trade-rate within ±20% of v4 baseline; zero state corruption
- **v5-runner default-on cutover**: `tools/start_all_services.sh` launches v5; `/srv/data/state_v5.json` populated; dashboard routes correctly
- **Documentation canonical**: ARCHITECTURE.md / MIGRATION.md / ROLLBACK.md / NAMING_CONVENTIONS.md all exist + non-empty
- **Grep-zero invariants**: `v4 compat`, `backward compat`, `# TODO: remove`, `DEPRECATED` (excl. 1 allowlisted banner per AC #1), `confirmation_tiers`, stale v4-active-engine docstrings → 0 hits in v5/ non-test
- **M9 regression skips closed**: both `test_m2_scale_dispatch.py` skips resolved (unskipped-passing OR formally retired per AC #11)
- **12 shim deletion cascade verified**: `sizing_legacy.py`, `Position.leg`, `trigger_combined_entry`, `armed_log.jsonl`, `_armed_tokens`, `Leg.market`, `confirmation_tiers`, `cpcv.deflated_sharpe` stub — all deleted; no grep hits
- **CLAUDE.md v4 freeze notice in place**; v5 paper runner is live; v4 is frozen reference
