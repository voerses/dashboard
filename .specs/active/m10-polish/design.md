# M10 Design — Final Polish + AC-S10 Capstone + Operational Hardening

**Status:** Phase 2 (DESIGN) output. No code. No file writes.
**Inputs:** 26 ACs in `.specs/active/m10-polish/brief.md`. Verified against v5 source @ 2026-04-20.

## 1. Executive summary

M10 is the last v5-rebuild milestone: it closes the AC-S10 bridge (the 6 strict-xfails at the heart of v5 credibility), ships a replay-parity fixture generator, deletes 12+ compat shims, closes 2 M7/M8 deferrals, adds 6 operational guards (rotation/drift/corruption/fee/rollback/WS), writes 6 docs, and drives warnings from 743 to 0. The risk is asymmetric: **Cluster A (AC #10) carries ~80% of the project risk** because the current `simulate_portfolio` bridge branch at `v5/simulator.py:2585-2650` builds a 3-field `SimpleNamespace` dict and attaches it to `state._bridge_signals` without ever running `_process_exits`/`_process_orders`/`_process_margin_calls` — the inner loop expects 40-field `TokenBarArrays` objects with `close`/`high`/`low`/`atr`/`rolling_adv`/`entry_mask`/`stop_mult`/`trail_mult`/`leverage`/`funding_1h` and ~30 more. Bridging requires either (a) expanding `_build_token_bar_arrays_from_generate` to emit real `TokenBarArrays` (fed from `ctx.data._arrays`) or (b) merging bridge-signal fields onto `precompute_strategy_signals` output.

Key risk ordering: (1) AC #10 structural divergence — no certainty v5-bridged semantics match v4 precompute-exit semantics on a 206-token 3-month fold; (2) AC #19 `use_data_engine=True` default flip — touching the default data source can silently alter bar-level content; (3) AC #9 replay-parity generator — must engineer 6 clamp-binding windows using real parquet slices; (4) Cluster B shim cascade order-of-operations; (5) secondary risks bounded. **Fixture baseline discrepancy open question**: the v4 reference fixture at `v5/tests/fixtures/m7_s524m_parity/v4_reference_metrics.json` records `total_return=-10.1%` but the brief asserts `1,094% annual sum` — needs user clarification before tolerance gates close.

Sequencing: Cluster A starts first and runs alone — its outcome determines whether ~8 downstream clusters proceed as planned. Clusters C (scenario tests), F (docs), G (warnings/polish), and parts of E can parallelize once A has a green single-token smoke. Cluster B (shim deletion) must land AFTER A + the two replay-parity tests (AC #9) are green. D (deferral closures) depends on A for #19; independent for #18.

## 2. Work clusters & task-ordering

```
A (AC #10 capstone) ──┬─▶ B (shim deletes 13, 19 parts) ─┬─▶ [M10 ship gate]
                      ├─▶ D-19 (use_data_engine default) ┤
                      └─▶ WalkForwardRunner dispatch cleanup ┤

G-9 (replay-parity generator) ─▶ B-sizing_legacy delete ────┘
                              ├─▶ D-18 (funding parity) [P]
                              └─▶ E-20 (rollback drill) [P]

[P-parallel-from-day-1]:
  C (4 scenario tests #14-17)
  E-21 (fee classification), E-22 (log rotator), E-23 (clock drift),
    E-24 (state corruption), E-25 (WS smoke)
  F (6 docs + CLAUDE.md patch + audit_naming.sh)
  G-1  (grep-zero script)
  G-11 (13-row audit + 2 unskips)
  G-26 (zero-warnings: 3 targeted fixes + pytest.ini filter)

Hard gate before any cluster-B deletion:
  A smoke green + AC #9 replay tests green.
```

Legend: **A** AC #10, **B** shim cascade, **C** scenario tests, **D** deferral closures, **E** ops guards, **F** docs, **G** polish.

## 3. Cluster A — AC-S10 Bridge Wiring (capstone, AC #10)

**The deepest structural change in M10.**

### 3.1 Current state (verified file:line)

- `v5/simulator.py:2553-2650` — `simulate_portfolio(strategies=..., ctx=...)` bridge branch. Builds `bridge_signals` via `_build_token_bar_arrays_from_generate` (line 2593) and `_engine_precompute_fallback` (line 2527).
- `v5/simulator.py:2516-2524` — returns `SimpleNamespace(token, direction, priority, stop_mult)` — **only 3 signal fields**. Missing: `close`, `high`, `low`, `atr`, `rolling_adv`, `entry_mask`, `leverage`, `funding_1h`, `target_mult`, `max_hold`, `no_stop_bars`, `trail_mult`, 12 combined/secondary fields, 6 perp_* fields.
- `v5/simulator.py:2648-2650` — state returned with empty equity curve + `_bridge_signals` attached; inner loop NEVER runs.
- `v5/simulator.py:2661-2673` — real inner loop only triggers on legacy branch (line 2651 onward).
- `v5/simulator.py:1006-1096` — `_process_exits` reads `sig.n_bars`, `sig.per_bar_is_perp`, calls `_get_bar_data(sig, local_bar, ...)` which requires full `TokenBarArrays`.
- `v5/validation.py:1238-1256` — `_m8_run` always returns `_WalkForwardResult(metrics=_stub_metrics())` (all zeros) in M8-mode. This is the function the xfail tests call.
- `v5/validation.py:1274-1300` — `WalkForwardRunner.__new__` kwarg dispatch shim between M9 (`config=`) and M8 (`strategy=,data_bundle=`).

### 3.2 Wiring plan

**Step A1: Extend bridge-signal builder to produce real `TokenBarArrays`.** Modify `_build_token_bar_arrays_from_generate` at `v5/simulator.py:2440-2524` so for each token, the returned object includes price/volume/liquidity arrays pulled from `ctx.data._arrays[token]`:

```python
# pseudocode — replaces the SimpleNamespace block at 2515-2524
from v5.signals import TokenBarArrays
arr = ctx.data._arrays.get(tok, {})
return {
    tok: TokenBarArrays(
        token=tok, strategy_id=strategy.strategy_id, n_bars=n_bars,
        timestamps=arr["timestamps"],
        entry_mask=(per_token[tok]["direction"] != 0),
        direction=per_token[tok]["direction"],
        close=arr["close"], high=arr["high"], low=arr["low"],
        atr=arr["atr"], rolling_adv=arr["rolling_adv"],
        funding_1h=arr.get("funding_1h", np.zeros(n_bars)),
        stop_mult=per_token[tok]["stop_mult"],
        trail_mult=_scalar_or_array(strategy.spec.trail_mult, n_bars),
        target_mult=strategy.spec.target_mult,
        no_stop_bars=strategy.spec.no_stop_bars,
        min_hold=strategy.spec.min_hold, max_hold=strategy.spec.max_hold,
        edge=strategy.spec.edge,
        leverage=_scalar_or_array(strategy.spec.leverage, n_bars),
        priority=per_token[tok]["priority"],
    )
    for tok in per_token
}
```

Must align with `ctx.data` shapes — verify `v5/data/engine.py` array contents before finalizing.

**Step A2: Route bridge path through the vectorized inner loop.** Replace lines 2595-2650 with construction of `strategy_specs` and fall-through to the legacy inner-loop starting at 2651:

```python
if strategies is not None:
    all_signals = bridge_signals  # dict[sid, dict[token, TokenBarArrays]]
    strategy_specs = {sid: strat.spec for sid, strat in strategies.items()}
# fall through — SAME code as legacy branch 2651-2675
unified_ts, bar_maps = build_unified_index(all_signals)
...
```

**Step A3: Wire WalkForwardRunner → simulate_portfolio → compute_portfolio_metrics.** Rewrite `_m8_run` at `v5/validation.py:1238-1256` to replace `_stub_metrics()` with a real call:

```python
from v5.simulator import simulate_portfolio
from v5.report import compute_portfolio_metrics
ctx = _build_ctx_from_data_bundle(self._m8_data_bundle)
config = _default_m8_portfolio_config(seed=self._m8_seed, capital=100_000)
state = simulate_portfolio(strategies={strat.strategy_id: strat}, ctx=ctx, config=config)
metrics, extra, eq_daily = compute_portfolio_metrics(state, config.capital)
return _WalkForwardResult(metrics=metrics)
```

**Step A4: Consolidate `WalkForwardRunner` dispatch.** After A3 passes smoke, delete `__new__` dispatch at `v5/validation.py:1274-1300` and merge M8 + M9 runners. Single canonical M9 runner exposes both call shapes via a single `__init__` switch.

**Step A5: Delete xfails + add positive-assertion guards.** Remove both `@pytest.mark.xfail(strict=True)` decorators at `v5/tests/test_m8_ac_s10_s524m_parity.py:62` and `:113`. Add pre-tolerance assertions per AC #10.

### 3.3 Risks & early detection

**Vectorized vs per-bar semantic divergence.** The bridge precomputes arrays via a setup-loop calling `strategy.generate(ctx, bar_idx)` for every bar in advance, but the v4 precompute path (`precompute_strategy_signals` at `v5/signals.py:207`) uses a different code path. If a strategy mutates self across bars (despite `_MutationGuardProxy` at `v5/strategy_api.py`), the two paths diverge.

Pre-flight detection (MUST run before 206-token run):
1. **Single-token smoke**: s524m on BTC-only (1 token, 3 months, Q-DEC4 fold) via both paths; compare TokenBarArrays byte-for-byte.
2. **Array-hash probe**: in `compute_portfolio_metrics`, hash `direction`/`priority`/`stop_mult`/`entry_mask` per token and compare to `precompute_strategy_signals` output.

### 3.4 Test strategy — per-metric tolerances (AC #10)

| Metric | rtol | Provenance |
|---|---|---|
| total_return | 0.5% | Sum-of-annual-returns drift bound |
| sharpe, sortino | 5% | Per-trade stop-path noise floor; σ(returns) denominator variance |
| max_drawdown | 10% | Path-dependent peak-trough; ordering-ties can flip by ≤1 trade |
| calmar | 5% | Inherits from sharpe + drawdown coupling |
| turnover | 10% | Entry-filter-ordering can push trade count by ±2% |
| win_rate | 2% abs | Discrete 1/N_trades quantization on 113 trades = 0.9%/trade |

### 3.5 Fallback if divergence is structural

If single-token smoke shows >10% return divergence and cause is not identifiable in <8h of debugging:
1. Keep the bridge-signal builder as-is (produces TokenBarArrays).
2. Feed `bridge_signals` into `precompute_strategy_signals`' post-processing (walk-forward mask, liquidity mask) to recover v4-baseline semantics.
3. Document divergence class in `knowledge/ARCHITECTURE.md`.
4. Per the NO-DEFERRALS policy, fallback MUST close the xfails — with per-metric tolerance class up to next-rung-larger (e.g., sharpe 5% → 10%).

## 4. Cluster B — Shim Deletion Cascade (ACs #13, #19 parts)

**Gate:** Cluster A + AC #9 green before ANY deletion.

| # | Shim | Current site | Consumers | Migration target | Risk |
|---|---|---|---|---|---|
| 1 | `sizing_legacy.py` (whole) | Root of `v5/` | 3 (simulator.py:35, paper_engine.py:1629, test_v5_portfolio.py:29) | Remove; route via `v5/sizing/` clamp pipeline only | Medium — `sizing_model.compute_size()` still invoked BEFORE clamp pipeline as initial input. Verify replaceability first |
| 2 | `Position.leg: str` | `position.py:68` | 12 read sites across simulator.py + paper_engine.py:3261 writer | Map to existing `leg_ref_id: Optional[str]` at position.py:131 | Medium |
| 3 | `trigger_combined_entry` legacy branch | `simulator.py:3159-3319` (~140 lines) | Flag-gated | Delete once multi-leg OTOCO replay-parity green | Low if gate held |
| 4 | `armed_log.jsonl` dual-write | `paper_engine.py:957,959,2080-2120` | All paper entries | Delete dual-write; keep `orders_log.jsonl` | Low |
| 5 | `_armed_tokens` property | `paper_engine.py:99,1611,1865,1882,1936,2062` | Serialize/load paths | Replace with `_pending_entries` direct access | Medium — regenerate paper state fixture |
| 6 | `Leg.market` | `orders.py` | Sibling of `settlement_type` | Keep `settlement_type` (FIX LegSettlType(587)); drop market | Low |
| 7 | `confirmation_tiers` | `paper_config.py:39,128` | Dead | Delete field + reader | Low |
| 8 | `cpcv.deflated_sharpe` stub | `cpcv.py:58-70` | DeprecationWarning-raising | Delete | Low |
| 9 | `WalkForwardRunner.__new__` | `validation.py:1274-1300` | Dispatch shim | Merge M8+M9 runners (part of Cluster A) | Medium |
| 10 | `globals()["get_sizing_model"]` hack | `simulator.py:31-41, 1425` | Monkeypatch resolver | Real import; drop obfuscation | Low |
| 11 | `use_data_engine=False` fallback | `paper_engine.py:4585,4648,4659-4715` | ~8 sites | After D-19 parity verified, delete branch | High |
| 12 | `test_m7_paper_8site.py:80` skip | Test | None | Delete dead `pytest.skip` | Trivial |
| 13 | `# v4 compatibility loaders` banner | `paper_state.py:1178` | None | Rename comment | Trivial |

Sequencing: 7→8→13→12→4→6→5→3→2→1→10→9 (deletes independent of AC-S10 first; #11 blocked by D-19).

## 5. Cluster C — Scenario Test Fixtures (ACs #14-17)

All replay-based, seeded, deterministic. Shared primitive: `ReplayFixtureBuilder` helper (new `v5/tests/fixtures/_replay_builder.py`) accepts `(tokens, bar_count, start_ts, seed, scenario_spec)` → `dict[token, TokenBarArrays]` + synthetic trades.

| Test file | Fixture size | Scenario spec |
|---|---|---|
| `test_m10_pnl_path_invariants.py` | 1 token, 200 bars | random-walk 0.1% stddev; 5 forced open/close cycles via entry_mask |
| `test_m10_daily_pnl_rollover.py` | 1 token, 48 × 1h bars | straddle UTC midnight; 2 trades pre-boundary + 2 post |
| `test_m10_funding_accrual_multi_window.py` | 1 perp token, 24 × 1h bars | funding_1h with 3 snaps at 00/08/16 UTC; mix of + and − rates |
| `test_m10_liquidation_cascade.py` | 3 tokens, 48 × 1h bars | flat until bar 24; uniform −25% drop bar → triggers liq on all 3 |

Reuse primitives: `generate_minute_exits_parity.py` (TokenBarArrays assembly template), `generate_mtf_48h_fixture.py` (multi-bar pattern).

## 6. Cluster D — Deferral Closures (ACs #18, #19)

### D-18: Funding cross-path parity
- New `test_m10_funding_backtest_paper_parity.py`.
- `v5/testing.py::TestClock` for deterministic `now_ns()`.
- Open position at 03:17 UTC; run both `simulator.run_backtest` and `paper_engine` driven by TestClock.
- Assert byte-identity of `funding_accruals.jsonl` across 3 snaps.
- Risk: verify both paths use identical `sign × rate × notional` math at boundary.

### D-19: `use_data_engine=True` default flip
1. Inventory `use_data_engine=False` consumers: currently `paper_engine.py:4659-4715` (8 sites). Verify via Grep.
2. Before flip: run 206-token Q-DEC4 2025 fold on BOTH paths; assert byte-identical `TokenBarArrays` per strategy.
3. Flip `PortfolioConfig.use_data_engine` default → True.
4. `_engine_precompute_fallback` is called from `simulator.py:2593` under bridge path! **Verify before deleting.** Either keep (rename) or replace bridge-fallback with DataEngine-driven builder.
5. New regression test `test_m10_data_engine_default.py`.

## 7. Cluster E — Operational Guards (ACs #20-25)

### E-20 Rollback drill (`test_m10_rollback_drill.py`)
- Sandbox dir `state/v5_paper_multi_rehearsal/`.
- Fixture seq: seed v4-format state → migrator → inject 500 bars via replay → programmatic stop v5 → restore `.v1.bak` → restart v4 → assert $0.01 equity continuity.

### E-21 Fee classification (`test_m10_fee_classification.py`)
- Maker/taker schedule at `v5/data/instruments.py:39-40,169-170,184-185`. Gap: no test discriminates.
- Synthetic limit-rest-fill (maker 2bps) + synthetic cross-book market (taker 4bps). Assert ClosedTrade.entry_fee / exit_fee math.
- Verify if current `paper_engine` fill-path differentiates; may need `fill_type: Literal["maker","taker"]` on `v5/fill.py`.

### E-22 Log rotation (`LogRotator`)
- New `v5/logs/log_rotator.py::LogRotator`. Wraps any JSONL sink; rotate at 500MB (env-configurable); gzip; keep last 10.
- Sinks: `arbitration.jsonl` (reuse existing rotation at `v5/arbitration.py:316`), `sizing_fills.jsonl`, `orders_log.jsonl`, `funding_accruals.jsonl`, `paper_runner_v5.log`, `risk_decisions.jsonl`, `clock_drift.jsonl` (new).
- Test: `test_m10_log_rotation.py` writes >500MB to each; assert rotation + gzip + re-open.

### E-23 Clock drift (`test_m10_clock_drift.py`)
- Extend `v5/clock.py` with `DriftDetector` OR add function in `run_paper_multi.py`.
- Every 60s: `LiveClock.now_ns()` vs `BinanceRESTClient.server_time_ns()`; WARN at 500ms, HALT at 5s via TradingState flip.
- Test: monkeypatch `server_time_ns` → +600ms + +6s; assert WARN + HALTED.

### E-24 State corruption (`test_m10_state_corruption.py`)
- Extend `paper_state.load()` at `paper_state.py:1594` to validate:
  - `schema_version == 3` (raise `StateSchemaMismatchError` with restore-hint),
  - Checksum of `active_positions[] + open_orders[]` matches,
  - No duplicate position_id / order_id.
- Checksum: `sha256(json.dumps(sorted([...]), sort_keys=True)).hexdigest()[:16]` stored at `state["checksum"]`.
- Test: corrupt three ways; assert raise with actionable message.

### E-25 WS rate-limit smoke (`test_m10_ws_ratelimit_parallel.py`)
- Start both runners; monitor `/srv/data/ws_errors.jsonl` for 30-60 min window; assert `count(429) == 0`.
- Practicality: can use TestClock-accelerated synthetic WS traffic if full wall-clock impractical in CI.

## 8. Cluster F — Documentation (ACs #2-6)

| File | Contents summary |
|---|---|
| `knowledge/NAMING_CONVENTIONS.md` | Canonical terms: symbol/instrument/bar/position/closed_trade + rationale. Source-of-truth for AC #2 grep |
| `knowledge/ARCHITECTURE.md` | §1 Trade Identity Model (parent_position_id, exec_seq, exec_type, is_terminal, triggered_by, has_scaling, ScalingEvent schema, lineage diagram, FIX mapping). §2 TickCadencePolicy wiring. §3 M8→M9 test-dispute tally |
| `knowledge/MIGRATION.md` | 11-step runbook with SIGTERM cmd (`bash tools/stop_all_services.sh`; 30s drain; SIGKILL escalation), dashboard URL cutover, migrator CLI |
| `knowledge/ROLLBACK.md` | Revert procedure; referenced by `test_m10_rollback_drill.py` |
| `knowledge/DASHBOARD_V5.md` | `/v5` routing, state_v5.json schema, cutover |
| `knowledge/V5_SIZING_SYSTEM.md` | M8 clamp pipeline + M9 arbitration/allocation |
| `CLAUDE.md` patch | `v4/ is FROZEN as of {date}.` Mirror v3 language |
| `tools/audit_naming.sh` | `rg -n '\btoken\b|\bcandle\b' v5/**/*.py` + exceptions |

## 9. Cluster G — Polish (ACs #1, #7-9, #11-12, #26)

### G-1 Grep-zero script
`tools/grep_zero_shims.sh`:
```bash
rg -n 'v4 compat|backward compat|# TODO: remove|DEPRECATED' v5/ \
  --glob '!v5/tests/**' --iglob '!*.pyc'
```
Exit non-zero on hit. Allowlisted: renamed banner at `paper_state.py:1178`.

### G-9 Replay-parity fixture generator
`v5/tests/fixtures/generate_m9_replay_parity_7d.py`. Parquet slices from `data/perp/1m_cache/` + `data/perp/1h_cache/`. One window per clamp, all 6 binding:
- `adv_cap`: min-ADV token window.
- `concentration`: day where s513/s523c/s524m emit same-day same-token.
- `free_capital`: deep-drawdown window (eq < 50% peak).
- `min_size`: scale equity to 0.01.
- `liq_distance`: >15% intraday decline.
- `slippage`: synthetic order_size_mult=100 on 1 bar.

Manifest `"status": "ready"` + per-window parquet path + checksum. Implement `run_replay_parity_m8_clamps` + `run_replay_parity_multi_leg` at `v5/tools/replay_parity.py`: launch `paper_engine` twice (flag=False/True), diff archives.

### G-11 `test_m7_pre_existing_13` 13-row audit
`.specs/active/m10-polish/m7_pre_existing_13_audit.md` — 13 rows: `node_id × outcome ∈ {PASSING-in-v5, OBSOLETE-delete, STILL-RED-fix} × M10-action`. STILL-RED rows resolved before AC #11 closes. Plus 2 `test_m2_scale_dispatch` skips per AC #11.

### G-26 Zero warnings
1. `v5/signals.py:434` s56 fallback — downgrade to `logger.debug()` (preferred) OR `_warned_set` sentinel for once-per-(strategy_id, token).
2. `v5/testing.py:17` — `TestClock.__test__ = False`.
3. `v5/data_resampler.py:270` — remove `copy=False`.
4. `pytest.ini`: `filterwarnings = error` scoped to v5/tests.

Also audit: `cpcv.py:67`, `metrics.py:326,336`, `universe.py:180`, `signals.py:434`. Keep user-facing (metrics.py legit); delete code-smell.

## 10. Blast radius & migration strategy

| Cluster | Modules touched |
|---|---|
| A | `simulator.py` (2440-2650), `validation.py` (1238-1300), `strategy_api.py` (178), strategies' `to_token_bar_arrays` |
| B | `simulator.py`, `paper_engine.py`, `position.py`, `orders.py`, `paper_config.py`, `cpcv.py`, `test_v5_portfolio.py` |
| C | `tests/test_m10_*.py` (4 new), `tests/fixtures/_replay_builder.py` (new) |
| D | `paper_engine.py`, `simulator.py`, `tests/test_m10_*.py` |
| E | `logs/log_rotator.py` (new), `clock.py`, `paper_state.py`, `run_paper_multi.py`, `fill.py` (potentially) |
| F | `knowledge/*.md`, `CLAUDE.md`, `tools/audit_naming.sh` |
| G | `signals.py`, `testing.py`, `data_resampler.py`, `pytest.ini`, `tools/grep_zero_shims.sh`, audit .md |

Re-run test gate after: `simulator.py` (full suite), `paper_engine.py` (full), `validation.py` (full + AC-S10 specifically), `paper_state.py` (round-trip + migrator).

Rollback plan per cluster: A (revert wiring commit + reinstate xfails). B (each shim delete = separate commit; `git revert`). C-G independent.

## 11. Performance considerations

- AC-S10 wiring: v4 baseline for s524m Q-DEC4 2025 fold ~30-45s (from `portfolio_backtest.py`). Budget ≤60s. If >2× baseline after wiring, profile and cache `_build_token_bar_arrays_from_generate` output. Target: ≤10% regression vs M9 legacy path.
- Log rotation: off critical path. Mirror existing `ArbitrationLogWriter` background-thread + lock pattern at `v5/arbitration.py:316`.
- Clock drift: 1/min, <100ms per poll. Negligible.
- WS rate-limit smoke: short window only.

## 12. Test strategy summary

- Every new test is replay-based. Fixtures in → asserts out. No wall-clock, no live fetch. Per user directive 2026-04-20.
- `pytest.ini` adds `filterwarnings = error` for v5 scope. Lands LAST (after G-26) to avoid false-positive failures.
- Target: 1761+ passed, 0 skipped, 0 xfailed, 0 xpassed, 0 warnings.
- AC-S10 positive-assertion guards run BEFORE tolerance comparisons to reject silent-zero scaffolding.
- Memory-growth test (AC #8) = 24h replay fixture + RSS/tracemalloc asserts (NOT live soak).

## 13. Open questions — RESOLVED 2026-04-20 via quant-prop-shop expert review

### Q1 — s524m baseline [RESOLVED]
Per-year v4 runs: 2022=+176.0%, 2023=+263.5%, 2024=+69.4%, 2025=+491.0%, Q1-2026=+39.4%. Annual sum = +1039.3%. AC-S10 parity pattern: 5 per-year runs × per-metric tolerance classes; annual sum matches v4 ±0.5%. Fixture at `v5/tests/fixtures/m7_s524m_parity/v4_reference_metrics.json` becomes a 5-element array — Phase 3 regenerates it.

### Q2 — Bridge-signal builder field source [MODIFIED]
Start with (a) synthesize from `ctx.data._arrays[token]` directly (DataEngine as single market-data source of truth), but **add a hard per-field byte-hash diff gate** before touching Cluster B. `precompute_strategy_signals` outputs are strategy-layer overlays (analogous to FIX `StrategyParameters 957/958`), not authoritative market data — do NOT merge them into the bridge source.

**Phase-3 deliverable** (BEFORE bridge edit lands):
- Add fixture `test_token_bar_arrays_field_inventory.py` that diffs `set(TokenBarArrays.__dataclass_fields__)` vs `set(ctx.data._arrays[tok].keys())`. Land a MARKDOWN gap table in `reviews/` directory.
- Single-token smoke (design §3.3) byte-hashes EACH of 40 fields individually (not whole-object). Per-field hashing tells which field diverges when metrics drift.
- Fallback (b) `precompute_strategy_signals` post-processing permitted ONLY if the gap table shows ≥3 fields missing from ctx.data — document the split in `ARCHITECTURE.md`.

### Q3 — `sizing_legacy.py` delete [ACCEPTED — with prerequisites]
Clamp pipeline IS self-sufficient via `SizingIntent.FIXED_FRACTION` at `v5/sizing/intents.py:31,81`. Current `simulator.py:1562-1586` uses `FIXED_NOTIONAL` and feeds `compute_size()` output — that's legacy carryover, not a pipeline requirement. `_LegacyKellySizing.compute_size` at `sizing_legacy.py:42-57` is already duplicated by clamp #1 (adv_cap) + clamp #2 (concentration).

**Phase-3 deliverables** (test-FIRST):
- `test_sizing_fixed_fraction_equivalence.py`: 1000 seeded `(equity, adv, edge, lev)` tuples — clamp pipeline with `FIXED_FRACTION` produces byte-identical notional to `_LegacyKellySizing.compute_size()`.
- Migrate `simulator.py:1562,1912` + `paper_engine.py:1629` to `FIXED_FRACTION`.
- **Also delete** `except Exception: pass` at `simulator.py:1645-1648` — it currently MASKS clamp-pipeline failures (silent sizing bypass).
- Only THEN delete `sizing_legacy.py`.

### Q4 — WalkForwardRunner unification [DEFERRED to AFTER Cluster A]
Keep `__new__` dispatch through Cluster A. Unify only after AC-S10 bridge is green. Use **explicit mode-string** (`WalkForwardRunner(mode="cpcv", ...)` vs `mode="m8_legacy"`) or `isinstance(config, ValidationConfig)` check — NOT kwarg-sniffing.

**Risk**: M8 callers pass `config=portfolio_config` (a `PortfolioConfig`, not `ValidationConfig`). Current `__new__` dispatches by kwarg-name precision — losing that risks misrouting a PortfolioConfig-carrying M8 call into the M9 runner.

**Phase-3 deliverables**:
- Grep ALL `WalkForwardRunner(` callsites; include notebooks + analysis scripts.
- Lock dispatch semantics in `test_walkforward_runner_dispatch_table.py` with 4-row parametrize (M8 shape / M9 shape / ambiguous / invalid) BEFORE deleting `__new__`.

### Q5 — `use_data_engine=False` flag [ACCEPTED — delete outright + scrub stubs]
Internal plumbing flag with zero external consumers. NO DEFERRALS → delete flag + False branch entirely.

**Phase-3 deliverables** (beyond design §6 D-19):
- After delete, scrub 8 `_m7_siteN_*` stub functions at `paper_engine.py:4659-4719` — the `use_data_engine: bool` parameter becomes vestigial. Don't leave stubs accepting a flag they no longer honor.
- Delete assertion in `test_m6_paper_migration.py:30` that checks default is `False`.
- D-19 gate (206-token × 5-year byte-identical TokenBarArrays on BOTH paths) MUST pass before delete commit.

### Q6 — AC #25 WS smoke [PUSHED BACK — split into two deliverables]
**The TestClock-accelerated approach tests your own mock, not the Binance rate limit.** Rate limits are wall-clock phenomena enforced on Binance's edge servers; accelerating synthetic time doesn't exercise the token bucket. Solution: split AC #25 into two:

**AC #25a (pytest, fast, deterministic)** — BACKOFF RETRY LOGIC:
- `test_m10_ws_backoff_retry.py` verifies the `[30, 60, 120]` backoff sequence at `paper_engine.py:857` fires correctly given a mocked 429 response.
- Fast, hermetic, tests YOUR code.

**AC #25b (operator smoke script, NOT pytest)** — LIVE WS RATE-LIMIT SMOKE:
- New `tools/ws_ratelimit_parallel_smoke.sh` — operator runs against Binance live endpoint for 30 min at cutover time.
- Documented in `MIGRATION.md` as step 6.5.
- NOT part of the pytest suite. NOT gated by wall-clock.

**Brief AC #25 update required**: replace single 30-60 min wall-clock pytest test with AC #25a (fast pytest retry test) + AC #25b (operator runbook script). Update brief before Phase 3.
