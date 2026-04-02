# Decouple Paper Trader Memory from Backtest + Plugin Opt-In

## Problem

Memory optimizations made for the 4GB paper trading container accidentally restrict the backtest engine:
1. `max_rows=22000` hardcoded in `portfolio_signals.py:80-81` silently truncates data for `lookback_months > 12`
2. `hist_cache.clear()` in `portfolio_signals.py:149-154` provides zero RSS benefit (glibc malloc fragmentation) but costs ~30s disk I/O per tick
3. All 11 indicator plugins run for every strategy — the existing opt-in mechanism in `engine.py:1155-1172` is dead code (`_NAMED_PLUGINS` is always empty)
4. Redundant plugin computation across strategies: each strategy creates fresh Engine instances, so shared tokens recompute `_build_context` + all plugins

## Success

Paper trader RSS drops from ~2GB to ~1GB for s501. Backtest works correctly with `lookback_months > 12`. Paper tick completes ~30s faster. Multi-strategy portfolios don't redundantly compute plugins for shared tokens.

## Appetite

1 day. All changes are additive fields/params with backward-compatible defaults.

## Solution

1. **Remove `hist_cache.clear()`** — confirmed via `/proc/PID/smaps` that RSS is identical before/after clearing. The cache was already in memory during context building; clearing forces a 30s re-read from disk on next tick.
2. **Make `max_rows` config-driven** — add `cache_max_rows` field to `PortfolioConfig` (default 0 = unlimited for backtest) and override to 22000 in `PaperConfig`. Replace the hardcoded value in `portfolio_signals.py`.
3. **Activate the existing plugin opt-in mechanism** — `engine.py` already has `_build_context` checking `self._required_plugins` (line 1155-1172) but `_NAMED_PLUGINS` is empty because no `@register_indicator` uses `name=`. Add `name=` to all 11 decorators and wire `REQUIRED_PLUGINS` from strategy modules through to Engine instances.
4. **Declare `REQUIRED_PLUGINS = []` on s501** — s501 uses zero `ctx.custom` fields.
5. **Share Engine instances across strategies (union approach)** — in `paper_engine._tick_internal()`, compute the UNION of all strategies' `REQUIRED_PLUGINS`. Create one `eng_spot` + `eng_perp` per tick with `_required_plugins = union`, pass them to all `precompute_strategy_signals()` calls. Enable `_context_cache` so `_build_context` results are reused across strategies.

## No-gos

- Will NOT change any strategy logic or signal computation
- Will NOT refactor the plugin system beyond adding names and wiring opt-in
- Will NOT touch `v3/` (frozen)
- Will NOT change how `_precompute_true_walk_forward` loads data (it already uses full parquets, no max_rows)
- Will NOT remove the separate `engine._hist_cache.clear()` in `run_paper_multi.py:1134-1136` — that's correct cache invalidation after 4h data promotion

## Acceptance Criteria

- [ ] AC1: `hist_cache.clear()` block in `_load_all_contexts` (portfolio_signals.py:149-154) is removed. The separate invalidation clear in `run_paper_multi.py:1134-1136` (after 4h promotion) remains.
- [ ] AC2: `PortfolioConfig` has `cache_max_rows: int` field defaulting to 0 (unlimited)
- [ ] AC3: `PaperConfig` overrides `cache_max_rows` to 22000
- [ ] AC4: `_load_all_contexts` uses `config.cache_max_rows` instead of hardcoded 22000 (passes 0 when unlimited — `load_token_data_cached` loads all rows)
- [ ] AC5: Backtest with `lookback_months=18` loads more than 22000 rows (no silent truncation)
- [ ] AC6: Paper trading with default config still limits to 22000 rows
- [ ] AC7: All 11 `@register_indicator` decorators have `name=` parameter
- [ ] AC8: `_load_strategy_fn` stores loaded modules in `_STRATEGY_MODULE_CACHE`
- [ ] AC9: `_load_strategy_required_plugins(strategy_id)` returns the module's `REQUIRED_PLUGINS` attribute (or None if missing)
- [ ] AC10: Engine instances receive `_required_plugins` from strategy module in `_load_all_contexts`, `_precompute_true_walk_forward`, and `precompute_strategy_signals`
- [ ] AC11: Strategy with `REQUIRED_PLUGINS = []` produces empty `ctx.custom` dict
- [ ] AC12: Strategy without `REQUIRED_PLUGINS` attribute runs all 11 plugins (backward compatible)
- [ ] AC13: Strategy with `REQUIRED_PLUGINS = ['obv', 'vwap']` runs only those 2 plugins
- [ ] AC14: s501 declares `REQUIRED_PLUGINS = []`
- [ ] AC15: All existing tests pass (774+ in v4/tests, integration tests in tests/)
- [ ] AC16: Plugin dependency order is documented: `obv_divergence` requires `obv`, `momentum_accel` requires `momentum`. Strategies using selective opt-in must include dependencies.
- [ ] AC17: Strategy module cache (`_STRATEGY_MODULE_CACHE`) is documented: strategy file changes require paper trader restart to take effect.
- [ ] AC18: `precompute_strategy_signals` accepts optional `eng_spot`/`eng_perp` Engine instances (backward compatible — creates fresh if not passed)
- [ ] AC19: `paper_engine._tick_internal()` creates shared Engine instances per tick. If ALL strategies declare `REQUIRED_PLUGINS`, engines get `_required_plugins=union(all)`. If ANY strategy lacks `REQUIRED_PLUGINS`, engines get `_required_plugins=None` (run all plugins, backward compat).
- [ ] AC20: When two strategies request the same token+market with shared engines, `_build_context` is called once (context cache hit on second call).
- [ ] AC21: `precompute_strategy_signals` (Class A path in `signals.py`) passes `config.cache_max_rows` to `load_token_data_cached`
- [ ] AC22: `load_paper_config()` wires `cache_max_rows` from JSON (defaults to 22000). `run_paper_multi.py` config constructor likewise.
- [ ] AC23: `_load_strategy_required_plugins` logs a warning when `REQUIRED_PLUGINS` contains a name not in `_NAMED_PLUGINS`
- [ ] AC24: `_build_context` with `_required_plugins=[]` produces empty `ctx.custom` dict (integration-level, not just registry check)
- [ ] AC25: `_build_context` with `_required_plugins=None` runs all 11 plugins (integration-level)
- [ ] AC26: `_load_strategy_fn` returns from cache on second call (no re-execution of module)

## Tasks

- [P] Task 1: Remove `hist_cache.clear()` in `_load_all_contexts` → `v4/portfolio_signals.py` (AC1)
- [P] Task 2: Config-driven `cache_max_rows` → `v4/config.py`, `v4/paper_config.py`, `v4/run_paper_multi.py`, `v4/portfolio_signals.py`, `v4/signals.py` (AC2-AC6, AC21-AC22)
- [ ] Task 3: Activate plugin opt-in + document dependencies → `v4/engine.py`, `v4/portfolio_signals.py`, `v4/signals.py` (AC7-AC13, AC16-AC17, AC23-AC26) (after: 2)
- [ ] Task 4: Share Engine instances across strategies → `v4/signals.py`, `v4/paper_engine.py` (AC18-AC20) (after: 3)
- [ ] Task 5: Declare s501 REQUIRED_PLUGINS → `strategies/s501_r172_v4_portfolio.py` (AC14) (after: 3)
- [ ] Task 6: Full regression → run all tests (AC15) (after: 1, 2, 3, 4, 5)

## Risks

- **Blast radius of `v4/config.py`** — EXTREME caution (39+ importers). Adding a new field with a default value only — verified safe (no `asdict()` comparisons, no field count assertions, dict unpacking is safe).
- **`load_token_data_cached` behavior with `max_rows=0`** — VERIFIED SAFE. `data_loader.py:211-214` already branches: `if max_rows > 0` trims, else stores full dataframe.
- **Plugin name collisions** — unlikely since we're assigning unique names matching function names, but verify `_NAMED_PLUGINS` dict has no key conflicts.
- **hist_cache memory trade-off** — Removing `hist_cache.clear()` keeps ~290MB in persistent `engine._hist_cache`. Acceptable: +290MB cache vs -910MB plugins vs -30s I/O. Container has 4GB.
- **Plugin dependencies** — `obv_divergence` depends on `obv`; `momentum_accel` depends on `momentum`. Both use `.get()` with None fallback (safe no-op if dependency missing).
- **Strategy module caching** — `_STRATEGY_MODULE_CACHE` means strategy file changes on disk aren't picked up until restart. Acceptable for paper trading.
- **Thread safety of _STRATEGY_MODULE_CACHE (Bug J)** — `_load_strategy_fn` can be called from `_bg_executor`. Protect with `threading.Lock`.
- **Union with undeclared strategies (Bug F)** — If any paper strategy lacks `REQUIRED_PLUGINS`, log warning and fall back to `_required_plugins=None`.
- **Plugin name typos** — If `REQUIRED_PLUGINS` contains a name not in `_NAMED_PLUGINS`, it is silently skipped. Mitigated by AC23: `_load_strategy_required_plugins` logs a warning for unknown plugin names.
- **Plugin dependency ordering** — `obv_divergence` must run AFTER `obv`; `momentum_accel` must run AFTER `momentum` (which produces `ret_6h`). Mitigated in Task 3: `_build_context` iterates `_INDICATOR_PLUGINS` in registration order (which is dependency-correct) and filters by `set(required_plugins)`. Callers can specify plugins in any order without risk.
- **Module-level side effects in cached strategies** — `_STRATEGY_MODULE_CACHE` means `exec_module` runs once per strategy. If a strategy module has side effects (global state, callbacks), they execute exactly once. Acceptable: all current strategies are pure functions.
- **Shared engines scope (Class A only)** — Task 4 shared engines only apply to Class A (per_token) strategies. Class B (portfolio) strategies like s501 dispatch to `portfolio_signals.py` which creates its own engines inside `_load_all_contexts`. s501 RSS savings come from Task 3 (plugin opt-in with `REQUIRED_PLUGINS=[]`), not Task 4. Task 4 benefits multi-Class-A-strategy runners.

## Design Notes

### Task 1: Remove hist_cache.clear() (1 file, 6 lines)
Delete `portfolio_signals.py:149-154` (the `if hist_cache is not None: hist_cache.clear()` block). The function then returns after `del eng_spot, eng_perp` on line 147.

### Task 2: Config-driven cache_max_rows (5 files)
- `config.py`: Add `cache_max_rows: int = 0` after line 296 (`raw_max_positions`) in PortfolioConfig
- `paper_config.py`: Override `cache_max_rows: int = 22000` in PaperConfig dataclass (after line 42); add `cache_max_rows=data.get("cache_max_rows", 22000)` in `load_paper_config()` constructor (~line 141)
- `run_paper_multi.py`: Add `cache_max_rows=merged.get("cache_max_rows", 22000)` in PaperConfig constructor (~line 122)
- `portfolio_signals.py:80-81`: Replace `max_rows=22000` → `max_rows=config.cache_max_rows`
- `signals.py:249-250`: Add `max_rows=config.cache_max_rows` to Class A strategy data loading

### Task 3: Plugin opt-in (3 files)
**engine.py:**
- Add `name=` to 11 decorators at lines: 545(obv), 558(vwap), 570(momentum), 584(enriched), 596(obv_divergence), 619(momentum_accel), 631(funding_zscore), 640(squeeze), 650(multi_tf), 749(positioning), 800(vrp)
- Add `_STRATEGY_MODULE_CACHE: Dict[str, object] = {}` and `_STRATEGY_MODULE_LOCK = threading.Lock()` after `_load_strategy_fn` (~line 1011)
- Modify `_load_strategy_fn`: on entry, check `_STRATEGY_MODULE_CACHE` under lock — return `cached_mod.strategy` on hit (AC26). On miss, proceed with existing `exec_module` logic, store `_STRATEGY_MODULE_CACHE[strategy_id] = mod` under lock BEFORE returning `mod.strategy` (AC8)
- Add `_load_strategy_required_plugins(strategy_id)` that reads from `_STRATEGY_MODULE_CACHE` under lock only. Logs a warning for any plugin name not in `_NAMED_PLUGINS` (AC23). Docstring documents: (a) plugin dependencies — `obv_divergence` requires `obv`, `momentum_accel` requires `momentum`/`ret_6h` (AC16), (b) cache means strategy file changes require restart (AC17)
- **Modify `_build_context` opt-in dispatch (lines 1157-1165):** Instead of iterating `required_plugins` and looking up in `_NAMED_PLUGINS`, iterate `_INDICATOR_PLUGINS` in registration order and check membership in `set(required_plugins)`. This guarantees dependency-correct execution order regardless of how callers specify the list. Implementation:
  ```python
  required_set = set(required_plugins)
  for fn in _INDICATOR_PLUGINS:
      name = _PLUGIN_NAMES.get(id(fn))  # reverse lookup
      if name is not None and name in required_set:
          try: fn(ctx)
          except Exception: pass
  ```
  Add `_PLUGIN_NAMES: Dict[int, str] = {}` reverse map, populated by `register_indicator` alongside `_NAMED_PLUGINS`.

**portfolio_signals.py:** After Engine creation at `_load_all_contexts` (lines 60-61) and `_precompute_true_walk_forward` (lines 700-701), call `_load_strategy_required_plugins(strategy_spec.strategy_id)` and set `eng_spot._required_plugins = eng_perp._required_plugins = result`. The `strategy_spec` parameter is already available in both functions. Note: `_load_strategy_fn` is always called by the parent function (`precompute_portfolio_signals` at line 552) BEFORE these functions, so the cache is populated.

**signals.py:** After Engine creation at lines 241-242, call `_load_strategy_required_plugins(strategy_spec.strategy_id)` and set `eng_spot._required_plugins = eng_perp._required_plugins = result`.

### Task 4: Share Engines across strategies (2 files)
**signals.py:** Add optional `eng_spot`/`eng_perp` params to `precompute_strategy_signals`. When engines are provided, skip creation + `_required_plugins` assignment. Pass `use_cache=True` to `_build_context` calls when using shared engines.

**paper_engine.py:** In `_tick_internal()` (~line 2027-2038):
1. Pre-load loop: call `_load_strategy_fn` for each strategy to populate cache
2. Collect REQUIRED_PLUGINS via `_load_strategy_required_plugins`
3. If ANY returns None: log warning, set `_required_plugins=None`
4. If all declare: compute union, create shared engines with `_required_plugins=sorted(union)`
5. Pass to each `precompute_strategy_signals()` call
6. Clear `_context_cache` after loop

### Task 5: s501 REQUIRED_PLUGINS (1 file, 1 line)
Add `REQUIRED_PLUGINS = []` to `strategies/s501_r172_v4_portfolio.py` after line 41.

### Critical Files

| File | Role | Blast Radius |
|------|------|-------------|
| `v4/config.py:265-304` | `PortfolioConfig` dataclass | EXTREME (39+) |
| `v4/paper_config.py:22-42` | `PaperConfig(PortfolioConfig)` | HIGH (21) |
| `v4/portfolio_signals.py:42-156` | `_load_all_contexts` | Medium |
| `v4/engine.py:510-542` | Plugin registry | Medium |
| `v4/engine.py:545-847` | 11 `@register_indicator` functions | Medium |
| `v4/engine.py:981-1010` | `_load_strategy_fn` | Medium |
| `v4/engine.py:1155-1172` | `_build_context` opt-in check | Medium |
| `v4/signals.py:211-260` | `precompute_strategy_signals` | HIGH (19) |
| `v4/run_paper_multi.py:89-123` | PaperConfig constructor | Medium |
| `strategies/s501_r172_v4_portfolio.py` | Strategy module | None |

### Adversarial Review Summary

Three independent review agents (quant, memory, blast radius) verified this plan. All issues resolved — see Risks section above.
