# M10 Tasks — Decomposition

**Phase**: decompose → implement (next)
**Total tasks**: 38 (35 pytest test files + 3 operator/doc artifacts)
**Clusters**: A (capstone) / B (shims) / C (scenarios) / D (deferrals) / E (ops) / F (docs) / G (polish)

Legend: `[P]` = parallelizable. `(after: X, Y)` = blocked by predecessor tasks.

---

## Cluster A — AC-S10 Capstone (AC #10)

- [ ] **A1**: `test_m10_token_bar_arrays_field_inventory.py` [P] — per-field diff between `TokenBarArrays.__dataclass_fields__` and `ctx.data._arrays[tok].keys()`. Produces `reviews/field_inventory.md` gap table. Q2 gate — must land BEFORE bridge edits in Phase 4.
- [ ] **A2**: `test_m10_ac_s10_per_year_parity.py` [P] — 5 per-year parity runs (2022/2023/2024/2025/Q1-2026) × 6-metric tolerance classes. Uses regenerated fixture `v4_reference_metrics_per_year.json` (Phase-4 regenerates via v4 command template in brief AC #10).
- [ ] **A3**: `test_m10_ac_s10_positive_assertions.py` [P] — `closed_trades` non-empty, `equity_curve` length == n_bars, `cum_pnl_usd[-1]` != 0, `_bridge_signals` attr absent. Runs BEFORE tolerance check.

## Cluster B — Shim Deletion Cascade (ACs #13, #19 parts, Q4)

- [ ] **B1**: `test_m10_sizing_fixed_fraction_equivalence.py` [P] — 1000 seeded `(equity, adv, edge, lev)` tuples; clamp pipeline `FIXED_FRACTION` = `_LegacyKellySizing.compute_size()` byte-identical. **Q3 prereq**.
- [ ] **B2**: `test_m10_no_sizing_legacy.py` (after: B1) — assert `v5/sizing_legacy.py` absent + no `from v5 import sizing_legacy`.
- [ ] **B3**: `test_m10_no_position_leg_field.py` [P] — assert `"leg" not in Position.__dataclass_fields__`; `leg_ref_id` sole source.
- [ ] **B4**: `test_m10_no_trigger_combined_entry.py` [P] — assert `trigger_combined_entry` function absent from simulator.
- [ ] **B5**: `test_m10_no_armed_log_dual_write.py` [P] — assert `v5/logs/armed_log.jsonl` never opened in paper_engine source; only `orders_log.jsonl`.
- [ ] **B6**: `test_m10_no_armed_tokens_property.py` [P] — assert `paper_engine._armed_tokens` attribute absent.
- [ ] **B7**: `test_m10_no_leg_market_field.py` [P] — assert `Leg.market` absent; `settlement_type` sole field.
- [ ] **B8**: `test_m10_no_confirmation_tiers.py` [P] — assert `confirmation_tiers` absent from `PaperConfig`.
- [ ] **B9**: `test_m10_no_cpcv_deflated_sharpe.py` [P] — assert `cpcv.deflated_sharpe` callable absent.
- [ ] **B10**: `test_walkforward_runner_dispatch_table.py` [P] — 4-row parametrize: M8 shape / M9 shape / ambiguous / invalid. Q4 — locks dispatch semantics BEFORE `__new__` delete.
- [ ] **B11**: `test_m10_no_get_sizing_model_globals_hack.py` [P] — assert no `globals()["get_sizing_model"]` obfuscation; direct import works.

## Cluster C — Scenario Tests (ACs #14-17)

- [ ] **C0**: `v5/tests/fixtures/_replay_builder.py` [P] — shared primitive: `ReplayFixtureBuilder(tokens, bar_count, start_ts, seed, scenario_spec)`. Used by C1-C4 + other clusters.
- [ ] **C1**: `test_m10_pnl_path_invariants.py` (after: C0) — 1 token × 200 bars; 4 invariants (cumulative == Σ ClosedTrade.pnl, equity identity, watermark monotone, curve reconstruction).
- [ ] **C2**: `test_m10_daily_pnl_rollover.py` (after: C0) — 1 token × 48 × 1h bars straddling UTC midnight; 2 trades pre + 2 post.
- [ ] **C3**: `test_m10_funding_accrual_multi_window.py` (after: C0) — 1 perp × 24 × 1h bars; 3 funding snaps 00/08/16 UTC; mix ± rates.
- [ ] **C4**: `test_m10_liquidation_cascade.py` (after: C0) — 3 tokens × 48 × 1h bars; uniform −25% drop at bar 24.

## Cluster D — Deferral Closures (ACs #18, #19)

- [ ] **D1**: `test_m10_funding_backtest_paper_parity.py` [P] — TestClock-harness; position opens 03:17 UTC; both `simulator.run_backtest` + `paper_engine` emit `funding_accruals.jsonl`; byte-identical across 3 snaps. **M8 deferral.**
- [ ] **D2**: `test_m10_data_engine_default.py` [P] — assert `PortfolioConfig.use_data_engine` default == `True`; `use_data_engine=False` branch raises or missing. **M7 deferral.**

## Cluster E — Operational Guards (ACs #20-25)

- [ ] **E1**: `test_m10_rollback_drill.py` (after: C0) — sandbox `state/v5_paper_multi_rehearsal/`; migrate → inject 500 bars via replay → stop → restore `.v1.bak` → v4 loads → $0.01 equity continuity.
- [ ] **E2**: `test_m10_fee_classification.py` [P] — synthetic limit-rest-fill (maker 2bps) vs cross-book market (taker 4bps); ClosedTrade fees match `v5/data/instruments.py` schedule.
- [ ] **E3**: `test_m10_log_rotation.py` [P] — write >500MB synthetic to each JSONL sink; rotation + gzip + re-open.
- [ ] **E4**: `test_m10_clock_drift.py` [P] — monkeypatch `server_time_ns` to +600ms (WARN) + +6s (HALT); assert `clock_drift.jsonl` entries + TradingState flip.
- [ ] **E5**: `test_m10_state_corruption.py` [P] — corrupt state three ways (schema=2 / mutated checksum / dup position_id); assert `StateSchemaMismatchError` with actionable msg.
- [ ] **E6**: `test_m10_ws_backoff_retry.py` [P] — **AC #25a**: mocked 429 triggers `[30, 60, 120]` backoff sequence at `paper_engine.py:857`.
- [ ] **E7**: `tools/ws_ratelimit_parallel_smoke.sh` (script, NOT pytest) [P] — **AC #25b**: operator runs live at cutover. Documented in MIGRATION.md step 6.5.

## Cluster F — Documentation (ACs #2-6)

- [ ] **F1**: `test_m10_naming_conventions_audit.py` [P] — assert `knowledge/NAMING_CONVENTIONS.md` exists; `tools/audit_naming.sh` returns 0 on current v5/ source (AC #2).
- [ ] **F2**: `test_m10_canonical_docs_exist.py` [P] — assert `knowledge/{ARCHITECTURE,MIGRATION,ROLLBACK,DASHBOARD_V5,V5_SIZING_SYSTEM}.md` all exist and non-empty (AC #3-5).
- [ ] **F3**: `test_m10_claude_md_v4_freeze.py` [P] — grep `CLAUDE.md` for `v4/ is FROZEN` notice mirroring v3 language (AC #6).
- [ ] **F4**: `tools/audit_naming.sh` (script) [P] — bash one-liner; exits non-zero on `\btoken\b|\bcandle\b` hits (exceptions file allowed). Referenced by F1.

## Cluster G — Polish (ACs #1, #7-9, #11-12, #26)

- [ ] **G1**: `test_m10_grep_zero_shims.py` [P] — runs `tools/grep_zero_shims.sh`; exits 0. (AC #1)
- [ ] **G2**: `tools/grep_zero_shims.sh` (script) [P] — rg for `v4 compat|backward compat|# TODO: remove|DEPRECATED`.
- [ ] **G3**: `test_m10_memory_growth_replay.py` (after: C0) — 24h replay fixture; RSS_end − RSS_start < 100MB; tracemalloc top-10 bounded; checksum round-trip on persist. (AC #8)
- [ ] **G4**: `test_m10_replay_parity_m8_clamps.py` (after: G5) — runs `run_replay_parity_m8_clamps`; asserts non-binding bars byte-identical + binding bars have matching `sizing_fills.jsonl`. (AC #9)
- [ ] **G5**: `v5/tests/fixtures/generate_m9_replay_parity_7d.py` [P] — fixture generator per design §9 (6 clamp-binding windows; manifest status "ready"). Spec work; implementation lives in Phase 4.
- [ ] **G6**: `test_m10_replay_parity_multi_leg.py` (after: G5) — same as G4 but `use_multi_leg_orders` flag. (AC #9)
- [ ] **G7**: `test_m10_pre_existing_13_audit_closed.py` [P] — asserts `m7_pre_existing_13_audit.md` exists + table has 13 rows + no STILL-RED residue. (AC #11)
- [ ] **G8**: `test_m10_parallel_ops_smoke.py` [P] — starts both runners via orchestrator; dashboard `/` + `/v5` both respond 200; tears down. (AC #12)
- [ ] **G9**: `test_m10_zero_warnings.py` [P] — runs pytest v5/tests/ and asserts `warnings_total == 0`. (AC #26)
- [ ] **G10**: `pytest.ini` patch [P] — adds `filterwarnings = error` for v5/tests scope. Lands LAST of cluster G (after warnings actually fixed).

---

## Dependency summary

```
A1 [P]  ─┐
A2 [P]  ─┼─▶ Cluster A complete ─┐
A3 [P]  ─┘                        │
                                  │
B1 [P]  ─▶ B2                     │
B3-B11 [P] ─────────────────────── │ ─▶ Phase 4 Cluster B safe-to-start
                                  │
C0 [P]  ─┬─▶ C1, C2, C3, C4      │
         ├─▶ E1                   │
         └─▶ G3                   │
                                  │
D1 [P]                            │
D2 [P]                            │
                                  │
E2-E7 [P]                         │
                                  │
F1-F4 [P]                         │
                                  │
G1-G2 [P]                         │
G5 [P]  ─▶ G4, G6                 │
G7-G10 [P]                        │
                                  │
(test-snapshot + RED verify) ────┴─▶ Phase 3 review gate
```

**Parallelizable at test-write time**: 34 of 38 tasks.
**Serial dependencies**: B1→B2, C0→{C1-C4, E1, G3}, G5→{G4, G6}.

## Test-writing plan (Phase 3)

5 parallel subagents per `.claude/rules/subagent-patterns.md`:
- **Subagent 1** (Cluster A): A1, A2, A3
- **Subagent 2** (Cluster B): B1-B11
- **Subagent 3** (Clusters C, D): C0-C4, D1, D2
- **Subagent 4** (Cluster E): E1-E7
- **Subagent 5** (Clusters F, G): F1-F4, G1-G10

Each subagent receives: brief.md path, their assigned ACs, existing v5/tests/ patterns. NOT the Phase-2 design reasoning (prevents implementation leak per subagent-patterns.md).

After test writing:
1. Copy all new test files to `.specs/active/m10-polish/tests-snapshot/`
2. Write `subagent-attestation.json` (required by `review-gate.sh`)
3. Run `pytest v5/tests/test_m10_*.py` — verify ALL tests FAIL (RED check)
4. Review gate — present tests to user for approval before Phase 4.
