# v5 Test Migration Map

Classification of all 1,899 v4 test functions across 70 files for v5 migration.

## Summary

| Verdict | Files | Tests | % of total |
|---------|-------|-------|------------|
| **KEEP** (copy, rewrite imports only) | 25 | 330 | 17% |
| **ADAPT** (behavior relevant, API changes needed) | 19 | 448 | 24% |
| **DELETE** (dead code removed in v5) | 15 | 420 | 22% |
| **PARTIAL** (mixed — some keep, some delete) | 11 | 701 (~590 keep, ~111 delete) | 37% |
| **TOTAL** | **70** | **1,899** | 100% |

**Net surviving tests**: ~1,368 (KEEP 330 + ADAPT 448 + PARTIAL-keep ~590)
**Net deleted tests**: ~531 (DELETE 420 + PARTIAL-delete ~111)

## DELETE — files NOT copied to v5 (15 files, 420 tests)

All sentinel stack, paper_shadow, minute_exits, v3, and standalone scripts:

| File | Tests | Reason |
|------|-------|--------|
| `v4/tests/test_breach_detector.py` | 26 | Sentinel stack deleted (M1) |
| `v4/tests/test_run_sentinel.py` | 24 | Sentinel stack deleted (M1) |
| `v4/tests/test_sentinel_config.py` | 14 | Sentinel stack deleted (M1) |
| `v4/tests/test_sentinel_exits.py` | 13 | Sentinel stack deleted (M1) |
| `v4/tests/test_sentinel_extraction.py` | 24 | Sentinel stack deleted (M1) |
| `v4/tests/test_sentinel_metrics.py` | 12 | Sentinel stack deleted (M1) |
| `v4/tests/test_sentinel_trail.py` | 19 | Sentinel stack deleted (M1) |
| `v4/tests/test_sentinel_validation.py` | 12 | Sentinel stack deleted (M1) |
| `v4/tests/test_stop_store.py` | 12 | Sentinel stack deleted (M1) |
| `v4/tests/test_shadow_report.py` | 18 | Sentinel shadow report deleted (M1) |
| `v4/tests/test_paper_shadow.py` | 19 | paper_shadow deleted (M1) |
| `v4/tests/test_minute_exits.py` | 20 | minute_exits absorbed into BarProcessor (M4) |
| `tests/test_v3_e2e.py` | 11 | v3 is frozen; v5 doesn't test v3 |
| `tests/test_v3_plugins.py` | 37 | v3 is frozen; v5 doesn't test v3 |
| `v4/test_breakeven_all_portfolios.py` | 0 | Script, not a test file |

**Total: 261 tests deleted (sentinel: 144, paper_shadow: 37, minute_exits: 20, v3: 48, script: 0)**

## KEEP — copy as-is with import rewrite only (25 files, 330 tests)

| File | Tests | Notes |
|------|-------|-------|
| `v4/tests/test_candle_aggregator.py` | 14 | Pure utility, no deleted features |
| `v4/tests/test_data_loader.py` | 29 | Pure data loading, no deleted features |
| `v4/tests/test_live_cli.py` | 3 | CLI utilities |
| `v4/tests/test_live_config.py` | 4 | PaperConfig defaults |
| `v4/tests/test_live_observability.py` | 5 | TickResult fields + heartbeat |
| `v4/tests/test_manifest.py` | 21 | Versioned promotion log |
| `v4/tests/test_measure_stop_breach.py` | 16 | Analysis tool (tools/ import) |
| `v4/tests/test_promote_live.py` | 17 | Promotion tool |
| `v4/tests/test_reconciliation.py` | 10 | Paper/backtest comparison |
| `v4/tests/test_run_paper.py` | 4 | CLI arg parsing |
| `v4/tests/test_slippage_fixes.py` | 13 | Slippage model (survives) |
| `tests/test_compare_instances.py` | 16 | Standalone paper util |
| `tests/test_equity_tracker.py` | 15 | Standalone paper util |
| `tests/test_gate4_engine.py` | 26 | Standalone gate engine |
| `tests/test_instance_manager.py` | 17 | Standalone paper util |
| `tests/test_monitor.py` | 14 | Standalone paper util |
| `tests/test_setup.py` | 10 | Standalone paper util |
| `tests/test_trade_persistence.py` | 15 | SQLite persistence (no v4 imports) |

Plus ~81 KEEP functions from PARTIAL files (dashboard_v2: 21, margin_calls: 14, sentinel_atr: 8, paper_config: 22, v4_filters: 6, v4_metrics: 7, paper_alerts: 3-from-partial).

## ADAPT — behavior relevant, needs API changes (19 files, 448 tests)

### Light adaptation (remove `exit_regimes=set()` from Position constructors)

| File | Tests | Changes needed |
|------|-------|----------------|
| `v4/tests/test_e2e_sub_hourly.py` | 16 | Remove exit_regimes param |
| `v4/tests/test_liquidation_fixes.py` | 20 | Remove exit_regimes param |
| `v4/tests/test_live_dashboard.py` | 6 | Remove exit_regimes param |
| `v4/tests/test_live_startup.py` | 8 | Remove exit_regimes param |
| `v4/tests/test_live_state.py` | 8 | Remove exit_regimes param |
| `v4/tests/test_paper_alerts.py` | 21 | Rewrite v4→v5 imports |
| `v4/tests/test_paper_determinism.py` | 9 | Remove exit_regimes from TokenSignals |
| `v4/tests/test_paper_live_integration.py` | 13 | Rewrite imports |
| `v4/tests/test_tick_internal.py` | 11 | Remove shadow pool test (1), rewrite imports |

### Medium adaptation (API changes from specific milestones)

| File | Tests | Milestone | Changes needed |
|------|-------|-----------|----------------|
| `tests/test_memory_decoupling.py` | 33 | M7 | Plugin registry → pull-based indicators |
| `tests/test_oos_config.py` | 9 | M9 | WalkForwardConfig location change |
| `tests/test_oos_data_cap.py` | 8 | M7/M9 | Signal precomputation path change |
| `tests/test_oos_deflated_sharpe.py` | 12 | — | Import rewrite only (metrics module) |
| `tests/test_oos_reproducibility.py` | 9 | M9 | walk_forward module location change |
| `tests/test_oos_true_wf.py` | 7 | M7/M9 | WF extraction changes |
| `tests/test_oos_walk_forward.py` | 14 | M9 | walk_forward module location change |
| `tests/test_oos_wfe.py` | 10 | — | Import rewrite only (metrics module) |
| `tests/test_s320_strategy.py` | 32 | M7 | Strategy Protocol, exit_regimes, conviction→priority |
| `tests/test_realistic_sizing.py` | 32 | M8 | Sizing API redesign |

### Heavy adaptation (significant rewrite for new v5 abstractions)

| File | Tests | Milestone | Changes needed |
|------|-------|-----------|----------------|
| `v4/tests/test_live_fetcher.py` | 14 | M6 | LiveFetcher → BinanceRESTClient |
| `v4/tests/test_price_monitor.py` | 35 | M6 | PriceMonitor → BinanceWSClient + DataEngine |
| `v4/tests/test_shared_pricemonitor.py` | 37 | M6 | Shared monitor → DataEngine fan-out |
| `v4/tests/test_sub_hourly_entries.py` | 48 | M5 | _armed_tokens → Order.legs |

## PARTIAL — mixed keep/delete within files (11 files, ~590 keep / ~111 delete)

| File | Total | Keep | Delete | What's deleted |
|------|-------|------|--------|----------------|
| `v4/tests/test_dashboard_v2.py` | 29 | 21 | 8 | ShadowRebalancer + pool aggregation tests |
| `v4/tests/test_margin_calls.py` | 15 | 14 | 1 | `partial_closed` attribute test |
| `v4/tests/test_sentinel_atr.py` | 11 | 8 | 3 | Cold-start fallback tests (sentinel-specific) |
| `v4/tests/test_paper_config.py` | 23 | 22 | 1 | `shadow_rebalance_threshold` default test |
| `v4/tests/test_paper_engine.py` | 26 | ~22 | ~4 | exit_regimes logic tests |
| `v4/tests/test_paper_state.py` | 23 | ~17 | ~6 | exit_regimes serialization + shadow pool tests |
| `v4/tests/test_paper_sub_hourly.py` | 45 | ~30 | ~15 | partial_tp tests + bear regime tightening |
| `tests/test_exit_handlers.py` | 53 | ~50 | 3 | RegimeExitHandler tests |
| `tests/test_sizing_defaults.py` | 45 | ~20 | ~25 | Kelly curve 9-layer pipeline + unrealized_pnl_floor |
| `tests/test_tier1_fixes.py` | 51 | ~25 | ~26 | raw_mode tests + unrealized_pnl_floor |
| `tests/test_v4_diagnostic_infra.py` | 55 | ~40 | ~15 | raw_mode config + simulator tests |
| `tests/test_v4_filters.py` | 8 | 6 | 2 | pump_filter tests |
| `tests/test_v4_metrics.py` | 7 | 7 | 0 | Keep all (skipped tests still valid) |
| `v4/test_v4_portfolio.py` | 103 | ~90 | ~13 | dd_scaling class + raw_mode tests |

## Adaptation by milestone

Which milestone triggers which test adaptations:

| Milestone | Files needing adaptation | Approx tests | Effort |
|-----------|------------------------|--------------|--------|
| **M1** (fork + purge) | ALL files: import rewrite `v4.`→`v5.` + remove exit_regimes/partial_tp from Position constructors | ~1,368 | 4-6h mechanical |
| **M4** (BarProcessor) | test_exit_handlers, test_e2e_sub_hourly, test_paper_sub_hourly, test_v4_portfolio, test_v4_diagnostic_infra, test_tier1_fixes | ~250 | 3-4h |
| **M5** (Order.legs) | test_sub_hourly_entries (48 tests — _armed_tokens → Order.legs) | 48 | 4-6h (heaviest per-file) |
| **M6** (Data arch) | test_live_fetcher, test_price_monitor, test_shared_pricemonitor | 86 | 6-8h (PriceMonitor→DataEngine) |
| **M7** (Strategy API) | test_memory_decoupling, test_s320_strategy, all test_oos_* files | ~130 | 4-6h |
| **M8** (Sizing) | test_realistic_sizing, test_sizing_defaults | ~52 | 3-4h |
| **M9** (Cleanups) | test_oos_* files (walk-forward extraction) | ~70 | 2-3h |

## M1 execution checklist for test migration

During M1 (fork + purge), the test migration is:

1. **Copy** all KEEP + ADAPT + PARTIAL files to `v5/tests/` (55 files)
2. **Don't copy** DELETE files (15 files)
3. **Mechanical fixes** across all copied files:
   - `from v4.` → `from v5.` (all files)
   - Remove `exit_regimes=set()` and `exit_regimes={N}` from all Position/TokenSignals constructors (~25 files)
   - Remove `partial_tp_atr=0`, `partial_tp_pct=0`, `partial_tp_trail=0`, `partial_closed=False` from Position constructors (~10 files)
   - Remove `regime_exit_min_bars=N` from Position constructors (~5 files)
4. **Delete functions** within PARTIAL files (see table above — ~111 functions)
5. **Verify**: `pytest v5/tests/` passes with ~1,250-1,370 test functions
6. **Flag for later**: ADAPT files tagged with their milestone (M4/M5/M6/M7/M8/M9) for future adaptation
