# M10 — Final Polish + Rename

**Summary**: Remove all remaining v4 compatibility shims, finalize API naming, write architecture documentation, ship the paper state migrator, and freeze v4 — completing the v5 engine transition.

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

- **API naming consistency pass**:
  - Audit all public APIs across v5/ modules for consistent terminology
  - Standardize: `symbol` (not `token` or `instrument` for the string identifier), `instrument` (for metadata object), `bar` (not `candle`), `position` (not `trade` for open), `closed_trade` (for completed)
  - Ensure all Protocol method signatures use consistent parameter names

- **Documentation**:
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

- **`state_schema_version` field**: Added to state.json for dashboard schema transition. Dashboard frontend tolerates both v4 and v5 schemas during transition period by checking this field.

- **Final test suite audit**: Verify all v5 tests are categorized (unit/integration/parity), no orphaned tests, no tests that only pass due to compat shims. Expected: v4 surviving tests (~1,250-1,300 functions after dead code deletion in M1) + new v5 tests added in M2-M9 (~200-300) = ~1,450-1,600 total test functions. Exact count validated during M10.

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

### Out of scope

- New features (this is polish only)
- Strategy migration (user-driven, per-strategy)
- Performance optimization (unless a regression is found)
- v4 deletion (v4 stays as frozen reference, like v3)
- CI/CD pipeline changes

---

## Key Acceptance Criteria

1. **Zero compat shims**: A grep for `v4 compat`, `backward compat`, `# deprecated`, `# TODO: remove` in v5/ returns zero results. All temporary bridges are deleted.

2. **Naming audit passes**: A script (or manual audit) confirms all public v5 APIs use the standardized terminology. No public method uses `token` where `symbol` is the standard, etc.

3. **ARCHITECTURE.md exists**: `knowledge/ARCHITECTURE.md` contains a Trade Identity Model section explaining parent_position_id, ScalingEvent, and ClosedTrade lineage with a diagram.

4. **Migration runbook exists**: `knowledge/MIGRATION.md` contains numbered steps for migrating one strategy from v4 to v5, including: code changes, config changes, paper state migration, verification steps, rollback steps.

5. **Paper state migrator works**: `v5/migrate_state_v1_to_v2.py` converts a v4 paper state file to v5 format. A test round-trips a real v4 state file through the migrator and verifies all positions and orders are preserved.

6. **CLAUDE.md updated**: CLAUDE.md contains the v4 freeze notice with the freeze date. The v3 freeze notice is unchanged.

7. **Full test suite green**: All v5 tests pass. `pytest v5/tests/` exits 0. No skipped tests, no xfail.

8. **48h paper soak**: Paper trader runs for 48 hours on v5 without errors, memory growth, or state corruption (manual verification, not automated test).

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **ALL (M1-M9)** | **Required** — This is the final milestone; all others must be complete |

---

## Time Estimate

**14-20 hours**

- ~3h: Compat shim removal + grep verification
- ~2h: Naming consistency audit + fixes
- ~4h: Documentation (ARCHITECTURE.md with Trade Identity Model detail, MIGRATION.md, ROLLBACK.md)
- ~3h: Paper state migrator + `--commit-migration` flag + `load_trade_log()` compat parser
- ~1h: CLAUDE.md update + `state_schema_version` field
- ~1h: Memory monitoring setup (RSS logging, 1.2GB alert, tracemalloc snapshots)
- ~2h: Final test audit + cleanup (~1,450-1,600 test functions expected)
- ~2h: 48h soak test monitoring (async)

---

## Parity Gate

- Full v5 test suite green (`pytest v5/tests/` exits 0)
- Paper trader runs 48 hours clean (no errors, no memory growth, no state corruption)
- CLAUDE.md updated with v4 freeze notice
- Zero grep hits for compat shims in v5/
- All documentation files exist and are non-empty
