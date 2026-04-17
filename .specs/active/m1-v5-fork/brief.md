# M1: v5 Engine Fork — Clean Baseline

**One-line summary:** Create `v5/` as a clean copy of `v4/` with dead code removed and all imports rewritten, while v4 stays frozen and running live paper.

---

## Problem

All future engine work (position scaling, regime removal, signal refactoring) needs a safe workspace that won't break the live paper trading system ($450K on v4). Today, every engine change risks production. M1 creates a byte-identical-then-cleaned fork so v5 development can proceed independently. This is purely mechanical — no behavioral changes, no new features.

---

## Scope

### IN scope
- Copy 41 engine files from `v4/` to `v5/`
- Copy 31 test files from `v4/tests/` to `v5/tests/`
- Rewrite all `from v4.` imports to `from v5.` in copied files
- Delete dead code blocks within copied files (raw_mode, dd_scaling, pump_filter, unrealized_pnl_floor, exit_regimes on Position, sizing purge knobs, walk-forward knobs, conviction knobs, partial_tp fields, regime_params)
- Skip dead files entirely (sentinel stack, paper_shadow)
- Strip sentinel module imports from paper_engine.py (file is copied but its imports of sentinel modules must be removed since those modules are not copied)
- Verify all surviving tests pass under `v5/` namespace

### NOT in scope
- Any modification to `v4/` (FROZEN — running live)
- Strategy file changes (strategies still import v4; migration is a later milestone)
- New features or behavioral changes
- `v3/` (FROZEN forever)

---

## Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| AC1 | `v5/` directory exists with all 41 engine `.py` files + `__init__.py` | `ls v5/*.py \| wc -l` = 42 (41 + __init__) |
| AC2 | Zero occurrences of `from v4.` in any `v5/` file | `grep -r "from v4\." v5/` returns empty |
| AC3 | Dead code removed: no `raw_mode`, `dd_scaling`, `pump_filter_funding_zscore`, `pump_filter_range_threshold`, `unrealized_pnl_floor`, `exit_regimes`, `exit_regimes_long`, `exit_regimes_short`, `regime_exit_min_bars` definitions in v5 config/position/simulator | `grep -rn` for each term in v5/ returns empty |
| AC11 | C-12 sizing purge: no `kelly_mult_floor`, `kelly_mult_range`, `kelly_mult_override`, `kelly_mult_scale`, `cap_pct_floor`, `cap_pct_range`, `cap_pct_override`, `cap_pct_scale`, `adv_scaling_divisor`, `adv_sizing_enabled` config fields; `size_multiplier`, `cap_multiplier`, `max_trade_pct` per-signal array fields deleted from sizing.py/signals.py; `adv_to_sizing()` function deleted from universe.py; `vol_adj` computation (`target_vol / max(vol, vol_floor)`, evaluates to max 4x at default params 0.02/0.005) removed from sizing.py | `grep -rn` for config fields in v5/config.py returns empty; `grep -rn` for `size_multiplier`, `cap_multiplier`, `max_trade_pct` in v5/sizing.py and v5/signals.py returns empty; `grep -rn "adv_to_sizing" v5/universe.py` returns empty |
| AC12 | Partial TP fields deleted: no `partial_tp_atr`, `partial_tp_pct`, `partial_tp_trail` config fields; no `partial_closed` field on Position | `grep -rn` for each term in v5/ returns empty |
| AC13 | Walk-forward knobs deleted from config: no `train_bars`, `recal_bars`, `purge_bars`, `skip_walk_forward`, `true_walk_forward` config fields in v5; `live_bar` function parameter removed from signals.py (not a PortfolioConfig field) | `grep -rn` for config fields in v5/config.py returns empty; `grep -rn "live_bar" v5/signals.py` returns empty |
| AC14 | Conviction system knobs deleted: no `conviction_mode`, `min_conviction_threshold`, `strategy_type` fields in v5 config | `grep -rn` for each term in v5/config.py returns empty |
| AC15 | `max_concurrent_per_token` renamed to `max_positions_per_symbol` in v5 config | `grep -rn "max_concurrent_per_token" v5/` returns empty; `grep -rn "max_positions_per_symbol" v5/config.py` finds the field |
| AC16 | `exit_resolution` and `entry_resolution` replaced by single `bar_resolution` (or `bar_spec`) on PortfolioConfig | `grep -rn "exit_resolution\|entry_resolution" v5/config.py` returns empty |
| AC17 | `regime_params` config field removed | `grep -rn "regime_params" v5/` returns empty |
| AC18 | paper_engine.py sentinel IMPORT lines stripped (sentinel modules are not copied; any `import` or `from v5.run_sentinel`/`sentinel_metrics`/`breach_detector`/`stop_store` lines removed) | `grep -rn "sentinel\|breach_detector\|stop_store" v5/paper_engine.py` returns empty |
| AC19 | paper_state.py regime fields stripped from serialization: `exit_regimes`, `exit_regimes_long`, `exit_regimes_short`, `regime_exit_min_bars`, `last_known_regimes` removed | `grep -rn` for each term in v5/paper_state.py returns empty |
| AC4 | Dead files NOT present in v5: `run_sentinel.py`, `sentinel_metrics.py`, `breach_detector.py`, `stop_store.py`, `paper_shadow.py` | `ls v5/run_sentinel.py` etc. all fail |
| AC5 | `pytest v5/tests/ -x -q` passes all surviving tests | Expected: ~1,650-1,796 test functions pass (exact count TBD after dead-test deletion) |
| AC6 | v4/ directory is byte-identical to before M1 started | `git diff v4/` shows no changes |
| AC7 | Basic import smoke test: `python -c "from v5.config import PortfolioConfig; print('OK')"` succeeds | Exit code 0 |
| AC8 | Module boot check: `python -c "from v5.paper_engine import PaperEngine; print('OK')"` succeeds | Exit code 0 |
| AC9 | `git diff --stat` shows only additions in `v5/` — no v4 modifications | Inspect diff |
| AC10 | `pytest v5/tests/ --tb=short` runs clean with 0 failures, 0 errors | Exit code 0 |

---

## Files Inventory

### Engine files: COPY (41 files)

| File | Notes |
|------|-------|
| `__init__.py` | Copy as-is |
| `candle_aggregator.py` | Copy, rewrite imports |
| `config.py` | Copy, rewrite imports, **delete**: `raw_mode`, `raw_max_positions`, `dd_scaling`, `dd_scaling_enabled`, `pump_filter_funding_zscore`, `pump_filter_range_threshold`, `unrealized_pnl_floor` fields, **C-12 sizing purge (config fields only)**: `kelly_mult_floor`, `kelly_mult_range`, `kelly_mult_override`, `kelly_mult_scale`, `cap_pct_floor`, `cap_pct_range`, `cap_pct_override`, `cap_pct_scale`, `adv_scaling_divisor`, `adv_sizing_enabled`; **C-2 walk-forward**: `train_bars`, `recal_bars`, `purge_bars`, `skip_walk_forward`, `true_walk_forward`; **C-1 conviction**: `conviction_mode`, `min_conviction_threshold`, `strategy_type`; **C-8**: `exit_resolution` + `entry_resolution` replaced by single `bar_resolution`; **C-5**: `max_concurrent_per_token` renamed to `max_positions_per_symbol`; **regime_params** removed |
| `cpcv.py` | Copy, rewrite imports |
| `dashboard_state.py` | Copy, rewrite imports |
| `data_loader.py` | Copy, rewrite imports |
| `data_maintenance.py` | Copy, rewrite imports |
| `dynamic_weights.py` | Copy, rewrite imports |
| `engine.py` | Copy, rewrite imports |
| `exit_handlers.py` | Copy, rewrite imports |
| `hourly_bar_collector.py` | Copy, rewrite imports |
| `live_fetcher.py` | Copy, rewrite imports |
| `manifest.py` | Copy, rewrite imports |
| `metrics.py` | Copy, rewrite imports |
| `minute_exits.py` | Copy, rewrite imports |
| `paper_config.py` | Copy, rewrite imports |
| `paper_engine.py` | Copy, rewrite imports, **strip sentinel IMPORT lines** (file is copied but imports of `run_sentinel`, `sentinel_metrics`, `breach_detector`, `stop_store` modules must be removed since those modules are not copied to v5) |
| `paper_state.py` | Copy, rewrite imports, **strip regime fields from serialization**: `exit_regimes`, `exit_regimes_long`, `exit_regimes_short`, `regime_exit_min_bars`, `last_known_regimes` |
| `paper_utils.py` | Copy, rewrite imports |
| `portfolio_backtest.py` | Copy, rewrite imports |
| `portfolio_signals.py` | Copy, rewrite imports |
| `position.py` | Copy, rewrite imports, **delete**: `exit_regimes`, `exit_regimes_long`, `exit_regimes_short`, `regime_exit_min_bars` fields + serialization; **delete**: `partial_tp_atr`, `partial_tp_pct`, `partial_tp_trail`, `partial_closed` fields |
| `position_commands.py` | Copy, rewrite imports |
| `price_monitor.py` | Copy, rewrite imports |
| `rank_all_portfolios.py` | Copy, rewrite imports |
| `report.py` | Copy, rewrite imports |
| `run_all_portfolios_backtest.py` | Copy, rewrite imports |
| `run_annual.py` | Copy, rewrite imports |
| `run_conviction_all19.py` | Copy, rewrite imports |
| `run_conviction_backtest.py` | Copy, rewrite imports |
| `run_dynamic_backtest.py` | Copy, rewrite imports |
| `run_paper_multi.py` | Copy, rewrite imports |
| `run_v2_backtest.py` | Copy, rewrite imports |
| `signals.py` | Copy, rewrite imports, **delete**: `size_multiplier`, `cap_multiplier`, `max_trade_pct` per-signal array fields (C-12 sizing purge); `live_bar` function parameter (C-2 walk-forward) |
| `simulator.py` | Copy, rewrite imports, **delete**: `raw_mode` branches (~300 LOC), `_dd_size_mult()`, `dd_scaling` references, `pump_filter` enforcement, `unrealized_pnl_floor` sizing line |
| `sizing.py` | Copy, rewrite imports, **delete**: `size_multiplier`, `cap_multiplier`, `max_trade_pct` per-signal array fields; `vol_adj` computation (`target_vol / max(vol, vol_floor)`) (C-12 sizing purge) |
| `test_breakeven_all_portfolios.py` | Copy, rewrite imports |
| `test_v4_portfolio.py` | Copy, rewrite imports (rename to test_v5_portfolio.py) |
| `universe.py` | Copy, rewrite imports, **delete**: `adv_to_sizing()` function (C-12 sizing purge) |
| `validation.py` | Copy, rewrite imports, **delete**: validators for removed config fields |
| `walk_forward.py` | Copy, rewrite imports |

### Engine files: SKIP (5 files — dead/dormant)

| File | Reason |
|------|--------|
| `run_sentinel.py` | Sentinel stack — never went live |
| `sentinel_metrics.py` | Sentinel stack — never went live |
| `breach_detector.py` | Sentinel stack — never went live |
| `stop_store.py` | Sentinel stack — never went live |
| `paper_shadow.py` | Dormant — no production strategy uses combined spot+perp |

### Test files: COPY (31 files)

All `v4/tests/test_*.py` EXCEPT the 12 listed below, plus `__init__.py`.

### Test files: SKIP (12 files — dead)

| File | Reason |
|------|--------|
| `test_run_sentinel.py` | Tests sentinel stack |
| `test_sentinel_atr.py` | Tests sentinel stack |
| `test_sentinel_config.py` | Tests sentinel stack |
| `test_sentinel_exits.py` | Tests sentinel stack |
| `test_sentinel_extraction.py` | Tests sentinel stack |
| `test_sentinel_metrics.py` | Tests sentinel stack |
| `test_sentinel_trail.py` | Tests sentinel stack |
| `test_sentinel_validation.py` | Tests sentinel stack |
| `test_paper_shadow.py` | Tests paper_shadow (dormant) |
| `test_shadow_report.py` | Tests paper_shadow (dormant) |
| `test_breach_detector.py` | Tests breach_detector (sentinel) |
| `test_stop_store.py` | Tests stop_store (sentinel) |

### Test functions to delete within copied files

Individual test functions/classes in surviving test files that specifically test:
- `raw_mode` behavior
- `dd_scaling` / `_dd_size_mult` behavior
- `pump_filter_funding_zscore` / `pump_filter_range_threshold` behavior
- `unrealized_pnl_floor` behavior
- `exit_regimes` / `regime_exit_min_bars` on Position
- `conviction_mode` / `min_conviction_threshold` behavior
- Walk-forward knobs: `train_bars`, `recal_bars`, `purge_bars`, `skip_walk_forward`, `true_walk_forward`, `live_bar` behavior
- C-12 sizing knobs: `kelly_mult_floor/range/override/scale`, `cap_pct_floor/range/override/scale`, `adv_scaling_divisor`, `adv_to_sizing()`, `size_multiplier`, `cap_multiplier`, `max_trade_pct`, `adv_sizing_enabled`, `vol_adj` multiplier
- `partial_tp_atr` / `partial_tp_pct` / `partial_tp_trail` / `partial_closed` behavior
- `regime_params` config behavior

These are surgical deletions within files, not whole-file removals.

---

## Time Estimate

**10-16 hours** of implementation time. The work is mechanical (copy, find-replace, delete) but the test verification pass is the long pole.

---

## Dependencies

None. This is the first milestone of the v5 engine project.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Import rewriting misses a reference | Medium | `grep -r "from v4\." v5/` as gate check (AC2) |
| Dead code deletion breaks a test | Low | Run full test suite after each deletion category |
| Circular imports from namespace change | Low | v4 has none today; copy preserves structure |
| Accidentally modifying v4/ | Low | AC6 + AC9 verify v4 is untouched; git diff as gate |

---

## Test Infrastructure Notes

- **`v5/tests/conftest.py`**: Must be created/copied from `v4/tests/conftest.py` with all `from v4.` imports rewritten to `from v5.`.
- **`pytest.ini`**: The `testpaths` setting must include `v5` (add alongside existing `tests v4` entries) for `pytest v5/tests/` discovery to work without explicit path.
- **Relative imports are safe**: Python relative imports (`from .` / `from ..`) resolve against the package name, not the absolute directory name. Only `from v4.` absolute imports need rewriting to `from v5.`. (In practice, the codebase uses no relative imports per conventions, so this is a no-op -- but documenting to prevent confusion during implementation.)

## Parity Gate

All surviving v4 tests must pass under the v5/ namespace. The exact test count will be determined after dead-test deletion (starting point: 1,796 functions across 95 files; expected surviving: ~1,650+ functions across ~83 files). The gate is: `pytest v5/tests/` exits with code 0 and 0 failures.
