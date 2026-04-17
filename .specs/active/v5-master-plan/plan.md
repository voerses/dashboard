# v5 Engine — Master Delivery Plan

v5 replaces the v4 backtest engine + paper trader through **10 independently-shippable milestones**. v4 stays running live paper ($450K across s513/s523c/s524m) throughout; strategies migrate one at a time when user decides.

**Approach**: copy v4 → v5 verbatim first (M1), then iterate. v4's 1,796 tests become the parity bar — they keep passing at every step.

## Milestone Map

```
M1 ─────┬──> M2 (position scaling — original ask)
        ├──> M3 (memory fix — operational priority)
        ├──> M4 (BarProcessor) ──┬──> M5 (Order.legs)
        │                        ├──> M7 (Strategy API) ──> M8 (Sizing)
        │                        │                      └──> M9 (Cleanups)
        ├──> M6 (v5/data/ package) ──────────────────────────┘
        └──> M10 (Polish — after all others)
```

## Stop Points (ship value at each)

| After | You have | Cumulative hours |
|-------|----------|-----------------|
| **M1** | Clean v5 baseline with 1,796 tests passing | 10-16 |
| **M1+M2** | Position scaling shipped (original ask) | 40-56 |
| **M1+M2+M3** | + stable memory (no more 2GB restarts) | 70-106 |
| **M1-M4** | + unified dispatcher (no more 3-way fork bugs) | 85-131 |
| **M1-M6** | + clean data architecture + multi-leg orders | 155-231 |
| **M1-M10** | Full v5 — production-hardened clean engine | 255-376 |

## Milestones

### M1 — v5 Fork + Dead Code Purge
- **Scope**: Copy v4→v5 verbatim; rewrite `from v4.` → `from v5.`; delete sentinel stack, paper_shadow, pump_filter_*, dd_scaling, unrealized_pnl_floor, raw_mode; delete associated tests.
- **Depends on**: Nothing
- **Hours**: 10-16
- **Parity gate**: All surviving v4 tests pass under v5/ namespace
- **Brief**: `.specs/active/m1-v5-fork/brief.md`

### M2 — Position Scaling
- **Scope**: `Position.increase/reduce/reduce_fraction`, `ScaleAction`, `ScalingEvent`, `ReduceResult`, `scale_check_fn` hook (or `Strategy.check_scale` if M7 landed first), FIX-aligned ClosedTrade identity fields, `LinkedScalePolicy`, tp_ladder helpers, paper engine wiring for INDEPENDENT, per-fill logging.
- **Depends on**: M1
- **Hours**: 30-40
- **Parity gate**: New scaling tests pass; all v5 baseline tests still pass; no behavior change for strategies without scaling.
- **Brief**: `.specs/active/m2-position-scaling/brief.md`

### M3 — Memory Fix
- **Scope**: RollingCache (bounded deques replacing unbounded _hist_cache + _context_cache), slotted dataclasses (Position, ClosedTrade, Bar), float32 where safe, bounded closed_trades archive to parquet, bounded _alerts/_pending_alerts, 24h memory soak test.
- **Depends on**: M1 (independent of M2)
- **Hours**: 30-50
- **Parity gate**: Memory soak test: 24h simulated paper < 100MB RSS growth. All v5 tests pass.
- **Brief**: `.specs/active/m3-memory-fix/brief.md`

### M4 — Unified BarProcessor
- **Scope**: Extract Phase 1/2/3 logic from simulator._process_exits into `v5/bar_processor.py`. Refactor simulator.py, minute_exits.py, paper_engine.py to call BarProcessor. Delete minute_exits.py (absorbed). Resolution-agnostic: same BarProcessor handles hourly, sub-hourly, any future resolution.
- **Depends on**: M1; ideally after M2 (so scale_check_fn is already wired and gets moved into BarProcessor)
- **Hours**: 15-25
- **Parity gate**: All v5 tests pass; no behavior change. New BarProcessor unit tests cover Phase 1/2/3 ordering.
- **Brief**: `.specs/active/m4-bar-processor/brief.md`

### M5 — Multi-leg Order.legs
- **Scope**: `v5/orders.py` — `Leg`, `Order`, `LegStatus` enum. Unify PendingEntry + _armed_tokens + combined primary/secondary into one abstraction. FIX LegGrp(555). `leg_fill_policy` (atomic/unwind/best_effort). Paper state serialization of Order.legs. armed_log.jsonl compat.
- **Depends on**: M4 (BarProcessor handles Order lifecycle)
- **Hours**: 20-30
- **Parity gate**: Armed entry tests pass; combined-strategy tests pass; paper state roundtrip preserves armed entries.
- **Brief**: `.specs/active/m5-multi-leg-orders/brief.md`

### M6 — v5/data/ Package
- **Scope**: DataEngine + MessageBus + RollingCache (if not already from M3) + DataClient Protocol + BinanceWSClient (consolidates price_monitor.py) + BinanceRESTClient (consolidates live_fetcher.py) + ParquetReplayClient + TimeBarAggregator. Strategy-declared subscriptions via `required_data()`. Clock abstraction (TestClock/LiveClock). Non-bar events (FundingRate, OI, MarkPrice). Instrument metadata registry.
- **Depends on**: M1; benefits from M4 (BarProcessor feeds from DataEngine)
- **Hours**: 50-70
- **Parity gate**: Paper trader runs with v5/data/ clients instead of v4 price_monitor+live_fetcher. Same data, same results. Gap detection test. Memory bounded.
- **Brief**: `.specs/active/m6-data-architecture/brief.md`

### M7 — Unified Strategy API (Option Y)
- **Scope**: `Strategy` Protocol class with methods (generate, check_scale, check_exit, filter_entry, on_start, on_stop, on_position_closed, view_state). `UniverseContext` with lazy per-token indicator access. Unified signals.py replacing signals.py + portfolio_signals.py. Walk-forward extracted to validation.py outer loop. `strategy_type` field removed. Reference strategy migration (s524m).
- **Depends on**: M4 (BarProcessor), M6 (DataEngine provides UniverseContext data)
- **Hours**: 40-60
- **Parity gate**: Reference strategy s524m produces metrics within 0.5% of v4/s524m (Q-DEC4). Unified signal path produces identical results for per-token and portfolio strategies.
- **Brief**: `.specs/active/m7-strategy-api/brief.md`

### M8 — Sizing Redesign
- **Scope**: `v5/sizing/intents.py` (FIXED_FRACTION + FIXED_NOTIONAL) + `v5/sizing/helpers.py` (vol_target_fraction, kelly_fraction, composite_scaled_fraction). Delete v4 opaque 9-layer pipeline (adv_to_sizing curve + 9 shape params). 6 explicit engine clamps (ADV cap, concentration, free capital, min size, liquidation distance, slippage). Per-fill binding-constraint logging. SizingRequest with explicit leverage, reduce_only, margin_mode.
- **Depends on**: M7 (Strategy API — strategies declare SizingRequest in generate())
- **Hours**: 20-30
- **Parity gate**: s524m parity gate re-run with new sizing via composite_scaled_fraction() helper. Per-ClosedTrade margin within 0.1%.
- **Brief**: `.specs/active/m8-sizing-redesign/brief.md`

### M9 — Cleanups: Conviction, Walk-Forward, Indicators, Regime
- **Scope**: C-1 conviction→priority + AllocationPolicy; C-2 walk-forward extraction (if not done in M7); C-3 pull-based memoized indicators; regime removal from engine + v5/regimes.py utility; dashboard rework (drop regime display, add scaling timeline, parent_position_id grouping). RiskEngine components (DrawdownThrottle, MaxGrossExposure, DailyLossLimit, FundingSafetyCheck, TradingState enum).
- **Depends on**: M7 (Strategy API) — AllocationPolicy plugs into signal-dispatch; indicators are part of UniverseContext
- **Hours**: 30-40
- **Parity gate**: All v5 tests pass with new conviction→priority naming. Dashboard renders correctly without regime.
- **Brief**: `.specs/active/m9-cleanups/brief.md`

### M10 — Final Polish + Rename
- **Scope**: Remove any remaining v4 compatibility shims. Final API naming consistency. Documentation (knowledge/ARCHITECTURE.md "Trade Identity Model", migration runbook, rollback procedure). Paper state migrator v1→v2 if not shipped in M5. Clean up CLAUDE.md to add "v4 is FROZEN as of {date}".
- **Depends on**: ALL other milestones
- **Hours**: 10-15
- **Parity gate**: Full v5 test suite green. Paper trader runs 48h clean. CLAUDE.md updated.
- **Brief**: `.specs/active/m10-polish/brief.md`

## Delivery Model

Each milestone follows the `/dev` workflow:
1. Its own `brief.md` in `.specs/active/mN-slug/`
2. Phase 2 design (if needed — M1/M3/M10 may skip straight to decompose)
3. Phase 3 decompose + write tests (RED)
4. Phase 4 implement (TDD)
5. Phase 5 commit + PR + review

Each PR merges to a `feat/v5-mN-slug` branch. After M10, all branches converge.

## Test Migration Map

See `test-migration-map.md` in this directory for the full classification of all 1,899 v4 test functions:
- **1,368 tests survive** into v5 (330 KEEP + 448 ADAPT + ~590 from PARTIAL files)
- **531 tests deleted** (sentinel 144, shadow 37, minute_exits 20, v3 48, partial-deletes 111, dead script 1)
- M1 handles the mechanical migration (copy, import rewrite, field removal, function deletion)
- M4-M8 each adapt their relevant test files when they change the APIs

## Risk Management

- **v4 stays running throughout** — no deadline pressure on any milestone
- **Each milestone has its own parity gate** — behavior drift detected at every step
- **Stop at any milestone** — you always have a working, better-than-before engine
- **Strategy migration is SEPARATE** from milestones — user-driven timing per strategy after the relevant milestone ships
