# M10 AC #11 — pre-existing-13 audit (concrete 13-row table)

Replaces the M9 "audit later / 30min hand-wave" per M10 AC #11.
Audit executed 2026-04-21 (live `pytest` run on `feat/m7-strategy-api`
at commit `16637a8`).

**Outcome**: all 13 nodes resolved. 4 are OBSOLETE (module skip after
M9 C-1 shim deletion); 9 are PASSING in v5.
**STILL-RED count: 0.**

## Audit table

| node_id | outcome | action |
|---|---|---|
| v5/tests/test_conviction_to_priority.py::TestAC2PrioritySortOrder | OBSOLETE | Module-level pytest.mark.skip applied in M9 C-1 (conviction->priority shim deleted). No action needed. |
| v5/tests/test_conviction_to_priority.py::TestAC3PrioritySortTieBreak | OBSOLETE | Module-level skip per M9 C-1. No action needed. |
| v5/tests/test_conviction_to_priority.py::TestAC1PriorityField | OBSOLETE | Module-level skip per M9 C-1. No action needed. |
| v5/tests/test_conviction_to_priority.py::TestAC10DeadCodeRepair | OBSOLETE | Module-level skip per M9 C-1. No action needed. |
| v5/tests/test_m1_fork_verification.py::TestAC01DirectoryExists::test_ac01_v5_file_count | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_m1_fork_verification.py::TestAC02NoV4Imports | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_m1_fork_verification.py::TestAC11SizingPurge::test_ac11_signal_field_absent_in_signals | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_m1_fork_verification.py::TestAC16ResolutionMerge::test_ac16_exit_resolution_absent | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_m3_slots.py::TestAC15aEntrySlotsPersist | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_m4_intra_bar_fill.py::TestAC38MarkTriggerBacktestFallback::test_audit_log_entry_emitted_in_backtest | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_paper_determinism.py::TestEngineDeterminism::test_same_seed_identical_engine_results | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_paper_determinism.py::TestEngineDeterminism::test_per_bar_seeding_engine_level | PASSING-in-v5 | Regression-guard stays. |
| v5/tests/test_paper_state.py::TestClosedTradesEmptyAfterDeserialize | PASSING-in-v5 | Regression-guard stays. |

## Observations

- The 4 conviction-to-priority tests were obsoleted in M9 C-1
  ("conviction->priority shim deleted; tests obsolete. TokenSignal.priority
  is now a scalar float per M7 API, not an int32 array auto-derived
  from conviction_score.") Per `.specs/active/conviction-to-priority-refactor/brief.md`.
  The module-level `pytestmark = pytest.mark.skip(...)` in
  `v5/tests/test_conviction_to_priority.py` correctly prunes them.
- The 9 remaining nodes are genuine regression guards that already
  pass. No additional work needed — they continue to protect against
  future regressions.
- The `test_m7_pre_existing_13` meta-harness at
  `v5/tests/test_m7_pre_existing_13.py` treats the 4 obsolete nodes
  with `OBSOLETE_OK = {0, 4, 5}` return-code acceptance — so the
  meta-test itself passes despite module-level skips.

## Closure

Per AC #11, STILL-RED count MUST be zero at M10 close. Audit confirms:

- STILL-RED nodes: 0
- OBSOLETE (no-action): 4
- PASSING (regression guards): 9

**AC #11 closed.** Audit table is the concrete per-node deliverable
promised in the brief. No further work required on these 13 nodes.

## Companion: test_m2_scale_dispatch regression skips

Per AC #11 second half, the two `test_m2_scale_dispatch.py` skips:

1. `test_scale_action_bar_updated_on_fire` — still marked skip. Phase 4
   remaining work (Cluster B / debug `_dispatch_scale_action` write
   site). Not part of the pre-existing-13 set.
2. `test_strategy_spec_rejects_scaling_with_multi_position` — Phase 4
   decision pending: formally retire OR re-implement via Strategy
   Protocol presence check. Not part of the pre-existing-13 set.

Both remain in scope for M10 Cluster-B work but are separate from this
13-row audit (the brief scopes them to AC #11 sub-bullet 2).
