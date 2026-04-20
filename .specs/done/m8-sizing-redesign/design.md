# M8 — Sizing Redesign Design

**Phase 2 artifact.** Read-only codebase exploration complete (3 parallel subagent reports synthesized). This document locks architecture, files-to-change, data flow, test strategy, migration strategy, and rollback plan before Phase 3 test authoring.

---

## 1. Architecture overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  STRATEGY (v5/strategies/*.py)                                           │
│   generate(ctx, bar_idx) → UniverseSignals                               │
│   ├─ TokenSignal(token, direction, priority, sizing=SizingRequest(...))  │
│   │                                                                       │
│   │  SizingRequest is SCALAR — strategies pre-index per-bar arrays       │
│   │  into per-fill scalars at generate() time. Engine never sees arrays. │
│   └─ SizingRequest(intent: SizingIntent, fraction_of_equity|notional_usd, │
│                    leverage, reduce_only, margin_mode)                    │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  ORDER FACTORY (v5/universe_context.py::OrderFactoryView)                │
│   arm(...)         → Order(legs=(), sizing_ctx={...})                    │
│   arm_bracket(...) → Order(legs=(entry, sl, tp), contingency=OTOCO)      │
│                                                                          │
│   At arm time: sizing_ctx["margin_usd"] populated from SizingRequest     │
│   (FIXED_FRACTION × equity × leverage, OR FIXED_NOTIONAL, OR per-leg)    │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  BARPROCESSOR (v5/bar_processor.py) — Phase 3: new entries               │
│   Trigger-check each ARMED Order; predicates hit → ARMED → TRIGGERED     │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  ORDER RELEASE (v5/orders.py::release_atomic) — **M8 CLAMP ENTRY POINT** │
│                                                                          │
│   def release_atomic(self, *, available_capital_usd,                     │
│                     market_state: MarketState) -> Order:                 │
│      # 1. Start: reserved = compute_reserved_capital(self)              │
│      # 2. Run 6 clamps in order; each may REDUCE sizing_ctx["margin_usd"]│
│      #    or set state=REJECTED                                          │
│      # 3. If all pass: transit TRIGGERED → RELEASED with mutated legs   │
│      # 4. Emit per-fill binding-log entry → v5/logs/sizing_fills.jsonl   │
│                                                                          │
│   Clamp pipeline (AC-Sz3):                                               │
│     ADV cap ──► concentration ──► free capital ──► min size             │
│         ──► liquidation distance ──► slippage                            │
│                                                                          │
│   Each clamp:                                                            │
│     1. Reads market_state (ADV, mark price, free margin, etc.)          │
│     2. Computes max_allowed_notional for this clamp                     │
│     3. If currently requested > max_allowed: apply min() and record     │
│        "binding" if this clamp was the one that reduced                  │
│     4. On unrecoverable error: state=REJECTED, reject_reason=            │
│        f"clamp_error_{name}"  (AC-Sz7)                                   │
│                                                                          │
│   Multi-leg aggregation: respects ContingencyType via                    │
│   compute_reserved_capital() — OTOCO = entry + max(siblings)             │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  SIMULATOR (v5/simulator.py) / PAPER_ENGINE (v5/paper_engine.py)         │
│   Consume RELEASED Order → fill execution → Position update              │
└──────────────────────────────────────────────────────────────────────────┘
```

**Key architectural decisions**:
1. **Single clamp entry point**: `Order.release_atomic`. No clamp logic lives in `v5/simulator.py` or `v5/paper_engine.py`. Both modes converge to the same hook.
2. **MarketState adapter**: thin wrapper providing `adv(token) / mark_price(token) / free_margin(strategy_id) / liquidation_distance(position, leverage)`. Implementations: `PriceMonitorMarketState` (legacy paper), `DataEngineMarketState` (M6), `SimulatorMarketState` (backtest). Unified interface so clamps are mode-agnostic.
3. **SizingRequest is scalar-only**: strategies bake per-bar inputs at `generate()` time. Engine reads only scalars. Preserves M7 per-bar-to-per-fill invariant (survey §4).
4. **v4 sizing pipeline deleted**: `v5/sizing.py::KellySizing`, `compute_position_size`, `SizingModel` protocol — all removed. `SqrtImpactSlippage` preserved (moved into clamp 6).

---

## 2. Files to change

### New files

| File | Purpose |
|------|---------|
| `v5/sizing/__init__.py` | Package root, re-exports public API |
| `v5/sizing/intents.py` | `SizingIntent(str, Enum)` + `SizingRequest` (moved from `v5/strategy_api.py`; re-exported for compat) |
| `v5/sizing/clamps.py` | 6 clamps + dispatch pipeline. One `Clamp` Protocol; six concrete implementations |
| `v5/sizing/market_state.py` | `MarketState` Protocol + 3 impls (`SimulatorMarketState`, `PriceMonitorMarketState`, `DataEngineMarketState`) |
| `v5/sizing/helpers.py` | `vol_target_fraction`, `kelly_fraction`, `risk_budget_fraction`, `composite_scaled_fraction` (pure; no engine dep) |
| `v5/sizing/binding_log.py` | Per-fill JSONL writer + schema struct |
| `v5/sizing/slippage.py` | Move `SqrtImpactSlippage` here from `v5/sizing.py` (which gets deleted) |
| `v5/logs/sizing_fills.jsonl` | Runtime output (gitignored) |
| `v5/tests/fixtures/m8_sizing_parity/` | AC-Sz9 parity fixture (1-week hourly, deliberate clamp-hit scenarios) |

### Modified files

| File | Change | Lines touched (est.) |
|------|--------|---------------------|
| `v5/orders.py` | Extend `release_atomic(..., market_state)`; invoke clamp pipeline; emit binding log | ~60 |
| `v5/strategy_api.py` | Promote `SizingIntent` from `Literal` → `Enum`; re-export from `v5.sizing.intents`; keep `SizingRequest` dataclass shape | ~15 |
| `v5/simulator.py` | Delete 2 callsites of `sizing_model.compute_size` (lines 1546, 1818); replace with `SimulatorMarketState` injected into `release_atomic` | ~50 |
| `v5/paper_engine.py` | Delete 1 callsite of `sizing_model.compute_size` (~line 1696); wire `PriceMonitorMarketState` / `DataEngineMarketState` per flag | ~40 |
| `v5/config.py` | Delete 9 v4 sizing fields (per AC-Sz6); add `adv_cap_pct / concentration_limit / min_position_usd / liquidation_buffer_pct / funding_buffer_pct / max_sizing_equity` to a new `ClampsConfig` dataclass | ~50 |
| `v5/strategies/s513_v5.py` | 1 line: `intent="FIXED_FRACTION"` → `intent=SizingIntent.FIXED_FRACTION` | 1 |
| `v5/strategies/s523c_v5.py` | Same | 1 |
| `v5/strategies/s524m_v5.py` | Same | 1 |
| `v5/validation.py` | `WalkForwardRunner.run()` — drive real `v5.simulator.simulate_portfolio` via `v5.portfolio_backtest.run_backtest`; populate `WalkForwardResult.metrics` from `v5/report.py::compute_portfolio_metrics` | ~40 |
| `v5/tests/test_m7_s524m_parity.py` | Remove `xfail(strict=True)` on `TestS524MMetricParityWithinHalfPercent`; pass 206-token universe loaded from parquet | ~20 |

### Deleted files / sections

| Target | Reason |
|--------|--------|
| `v5/sizing.py` entire module | Superseded by `v5/sizing/` package |
| `SizingModel` Protocol + `KellySizing` class | Replaced by clamp pipeline |
| `compute_position_size` function | Replaced by clamp pipeline |
| `SAFETY_RAILS["edge_minimum"]`, `["target_vol"]`, `["spot_max_equity_pct"]`, `["min_adv_usd"]`, `["adv_lookback_days"]` | Per AC-Sz6 |
| `SizingDefaults` dataclass | Per AC-Sz6 |
| `v5/simulator.py` sizing paths around lines 1546/1818 | Replaced by clamp call |
| `v5/paper_engine.py` sizing path around line 1696 | Replaced by clamp call |

**Blast-radius cascade**: v5/config.py deletions touch **~28 files** (tests + configs) per grep. All are one-line references. Budget ~6h for the cascade cleanup.

---

## 3. Data flow details

### 3a. SizingRequest construction (strategy-side)

```python
# Example: s524m_v5.py::generate, one iteration per token
signals[token] = TokenSignal(
    token=token,
    direction=direction,
    priority=priority,
    sizing=SizingRequest(
        intent=SizingIntent.FIXED_FRACTION,
        fraction_of_equity=size_multiplier / self.MAX_POSITIONS_HINT,  # scalar
        leverage=leverage,                                              # scalar
        reduce_only=False,
        margin_mode="isolated",
    ),
)
```

All per-bar arrays (`size_multiplier[:]`, `leverage[:]`) are pre-indexed at `bar_idx` before baking into `SizingRequest`. This preserves M7 per-bar-to-per-fill invariant (survey §4).

### 3b. OrderFactoryView.arm_bracket → legs.sizing_ctx population

When `arm_bracket(entry, sl, tp)` is called, the factory:
1. Reads `SizingRequest` from the strategy's `TokenSignal`.
2. Computes per-leg `margin_usd`:
   - **Entry leg**: `(fraction_of_equity × equity) / leverage` (since margin = notional / leverage)
   - **SL/TP legs**: inherit entry's `margin_usd` (reservation is sibling-max under OTOCO aggregation)
3. Stamps `Leg.sizing_ctx = {"margin_usd": X, "target_qty": Y, "market": "perp"}` for each leg.
4. Sets `Order.contingency = ContingencyType.OTOCO` (round-2 FIX fix).

### 3c. Clamp pipeline in release_atomic

```python
def release_atomic(
    self,
    *,
    available_capital_usd: float,
    market_state: "MarketState",
) -> "Order":
    if self.state not in (OrderStatus.TRIGGERED, OrderStatus.ARMED):
        return self

    from v5.sizing.clamps import run_clamp_pipeline
    from v5.sizing.binding_log import write_sizing_fill_entry

    try:
        # Clamps read current sizing_ctx, adjust in-place (returning new Order
        # instance via _replace_leg / object.__setattr__), record binding-log
        # entry for each fill decision.
        new_order, binding_log = run_clamp_pipeline(
            self,
            available_capital_usd=available_capital_usd,
            market_state=market_state,
        )
    except Exception as e:
        # AC-Sz7: never crash the BarProcessor
        clamp_name = getattr(e, "_clamp_name", "unknown")
        write_sizing_fill_entry(self, {}, binding=f"clamp_error_{clamp_name}",
                                error=str(e))
        return self._transit(OrderStatus.REJECTED,
                             reject_reason=f"clamp_error_{clamp_name}")

    # Re-check aggregate capital post-clamp
    reserved = compute_reserved_capital(new_order)
    if reserved > available_capital_usd:
        write_sizing_fill_entry(new_order, binding_log, binding="free_capital")
        return self._transit(OrderStatus.REJECTED,
                             reject_reason="risk_on_release_atomic")

    write_sizing_fill_entry(new_order, binding_log, binding=binding_log.final)
    return new_order._transit(OrderStatus.RELEASED)
```

### 3d. Clamp ordering (AC-Sz3)

Fixed order (matters for binding-log determinism):

1. **ADV cap** — `max_fill_notional = rolling_adv × adv_cap_pct`
2. **Concentration** — `max_per_symbol = strategy_equity × concentration_limit`
3. **Free capital** — `max_margin = available_capital_usd` (post-funding-buffer)
4. **Min size** — if result below `min_position_usd`: REJECT with `binding="min_size"`
5. **Liquidation distance** — reject if `notional × leverage` would push liq price inside stop distance
6. **Slippage** — adjusts `fill_price` (not a reject); `SqrtImpact(notional, adv)` per `v5/sizing/slippage.py`

Each clamp returns either `(adjusted_notional, clamp_value)` or raises `ClampError` (caught by release_atomic try/except).

### 3e. Binding-log schema (AC-Sz5)

One JSONL entry per fill attempt written to `v5/logs/sizing_fills.jsonl`:

```jsonc
{
  "timestamp": "2026-04-20T10:00:00.123456Z",  // from ctx.clock.now_ns()
  "order_id": "s524m-BTC-abc123-1",            // FIX ClOrdID(11)
  "symbol": "BTC",
  "strategy_id": "s524m",
  "intent": "FIXED_FRACTION",
  "requested_fraction": 0.04,
  "requested_notional": 6000.0,
  "leverage": 2.6,
  "margin_mode": "isolated",
  "reduce_only": false,
  "clamp_values": {
    "adv_cap": 5000.0,
    "concentration": 15000.0,
    "free_capital": 20000.0,
    "min_size": 200.0,       // floor, not cap
    "liq_distance": Infinity,
    "slippage_bps": 12.3
  },
  "binding_constraint": "adv_cap",
  "filled_margin": 1923.08,
  "filled_notional": 5000.0,
  "fill_price": 50006.15,    // after slippage adjustment
  "slippage_bps": 12.3,
  "error": null              // populated on clamp_error path only
}
```

Log writer uses a buffered file handle (flushed on engine shutdown); in tests, tmp_path-scoped to avoid pollution.

### 3f. AC-S10 backtest-vs-backtest path

```python
# v5/validation.py::WalkForwardRunner.run (rewritten)
from v5.portfolio_backtest import run_backtest as _run_backtest_cli
from v5.report import compute_portfolio_metrics

def run(self, tokens: Sequence[str], seed: int = 0) -> WalkForwardResult:
    fold_results = []
    for fold_idx in range(self.n_folds):
        strategy = self.strategy_factory() if not self.reuse_instance else ...
        # Real simulator path
        result = _run_backtest_cli(
            strategy_ids=[strategy.name],
            months=3,  # Q-DEC4 window
            capital=100_000,
            config=PortfolioConfig(...),
            market="perp",
            end_date=pd.Timestamp("2025-12-31T23:00:00"),
            start_date=pd.Timestamp("2025-10-01T00:00:00"),
        )
        metrics, extra_info, trades, _, eq_daily = result
        fold_results.append({
            "fold_id": fold_idx,
            "metrics": metrics,  # PerformanceMetrics dataclass → dict
            "trades": trades,
            "equity": eq_daily,
        })

    # Aggregate: for single-fold AC-S10, just surface fold 0
    agg_metrics = fold_results[0]["metrics"] if fold_results else {}
    return WalkForwardResult(folds=fold_results, metrics=agg_metrics)
```

**Critical correction from subagent report**: subagent claimed `v5/strategies/s524m_v5.py` doesn't exist. **It does** — shipped in M7 Wave F (commit 9351245). No port work required; just wire the runner.

---

## 4. Test strategy

### Phase 3 acceptance tests (written by subagent, FROZEN post-approval)

| Test file | AC coverage | Scope |
|-----------|-------------|-------|
| `v5/tests/test_m8_sizing_intent_enum.py` | AC-Sz1 | Enum shape, unknown intent raises ValueError, JSON roundtrip preserves string |
| `v5/tests/test_m8_sizing_request_fields.py` | AC-Sz2 | Mutual exclusion (fraction vs notional), defaults, scalar-only invariant |
| `v5/tests/test_m8_clamps_individual.py` | AC-Sz3 | 6 tests, one per clamp, triggered in isolation |
| `v5/tests/test_m8_clamps_ordering.py` | AC-Sz3 | Multi-clamp scenarios, binding-constraint determinism |
| `v5/tests/test_m8_binding_log.py` | AC-Sz5 | JSONL schema, binding_constraint field, error-path entries |
| `v5/tests/test_m8_helpers.py` | AC-Sz4 | Parameterized known-inputs/outputs for all 4 helpers |
| `v5/tests/test_m8_v4_pipeline_deleted.py` | AC-Sz6 | Grep-based assertion that 9 banned fields + `compute_position_size` are absent |
| `v5/tests/test_m8_release_atomic_integration.py` | B1 (clamp hook) | Verify clamps fire inside `release_atomic`, not elsewhere |
| `v5/tests/test_m8_multi_leg_aggregation.py` | B2 (OTOCO) | OTOCO bracket capital reserve = entry + max(SL, TP) |
| `v5/tests/test_m8_clamp_error_containment.py` | AC-Sz7 | Deliberate exception in each clamp; assert `state=REJECTED, reason=clamp_error_X` |
| `v5/tests/test_m8_reduce_only_overfill.py` | AC-Sz8 | 3 scenarios (long_5x_reduce_7x, short_3_reduce_5, flip attempt) |
| `v5/tests/test_m8_s10_parity.py` | AC-S10 | Already exists (`test_m7_s524m_parity.py`) — unwrap xfails after runner wiring |
| `v5/tests/test_m8_paper_backtest_parity.py` | AC-Sz9 | Record paper under TestClock; replay backtest; diff `sizing_fills.jsonl` |
| `v5/tests/test_m8_market_state_adapter.py` | B3 | `PriceMonitorMarketState` vs `DataEngineMarketState` return identical values for same inputs |
| `v5/tests/test_m8_cross_margin_free_capital.py` | Risk scope | Portfolio unrealized PnL aggregation under `margin_mode="cross"` |

Target: 40-60 individual test cases across 15 files.

### Reviewer loop

- **FIX architect + Quant architect** primary review rounds (M7 pattern).
- **Focused Risk architect subagent pass at round 3** — scoped to AC-Sz3 clauses 3+5 (free capital under cross-margin, liquidation distance), AC-Sz8 (reduce_only), and margin_mode semantics. 4-6h budget.
- No scope cap per user directive — iterate until both reviewers return PASS.

---

## 5. Migration strategy

### Wave structure

| Wave | Scope | Est. hours |
|------|-------|------------|
| **A** | `SizingIntent` enum + `SizingRequest` schema extension + 3 port updates | 4 |
| **B** | `MarketState` Protocol + 3 impls (`SimulatorMarketState`, `PriceMonitorMarketState`, `DataEngineMarketState`) | 8 |
| **C** | 6 clamps (`v5/sizing/clamps.py`) + `run_clamp_pipeline` + unit tests | 12 |
| **D** | `Order.release_atomic` integration + clamp-error containment + binding-log writer | 8 |
| **E** | Helper library (4 functions) + unit tests | 6 |
| **F** | Delete v4 pipeline (`v5/sizing.py` → `v5/sizing/slippage.py` preserve, everything else DELETE) + 28-file config cascade | 8 |
| **G** | `simulator.py` + `paper_engine.py` callsite migration (3 callsites) | 6 |
| **H** | Multi-leg OTOCO clamp aggregation + tests | 4 |
| **I** | `reduce_only` overfill semantics + tests | 4 |
| **J** | AC-S10 path (I): `WalkForwardRunner` → `run_backtest` wiring + fixture regen + 206-token test alignment | 15 |
| **K** | AC-Sz9 paper-vs-backtest parity: fixture + test | 8 |
| **L** | Reviewer loop (FIX + Quant + focused Risk) | 8-15 |

**Total**: 91-106h (aligns with 60-85h estimate + reviewer buffer).

### Rollback plan

Feature flag `PortfolioConfig.use_m8_clamps: bool = True` (default ON when M8 ships). If a production regression surfaces during Wave G/J:

1. Set flag to `False` → `Order.release_atomic` uses pre-M8 `compute_reserved_capital`-only path (no clamps).
2. `v5/simulator.py` + `v5/paper_engine.py` fall back to pre-M8 behavior via a shim that reads the old `SizingModel.compute_size` signature from a preserved `v5/sizing_legacy.py` module (kept until M9 for rollback only).
3. All 6 clamp tests skip when flag=False.
4. AC-Sz9 paper-vs-backtest parity continues to hold under flag=False since the shim preserves existing behavior.

Shim stays in place until M9 "cleanups" verifies no fallback triggers in 7 days of paper runtime, then deleted.

### Data-safety protocol

- **No `git checkout -f` / `clean -f`** during Wave F config cascade — use `git mv` + `git rm` per file.
- **No parquet refetch**: AC-S10 reuses the existing 236-token `data/perp/1h_cache/*.parquet` (verified fresh 2026-04-20 per subagent report).
- **Paper state v3 schema**: unchanged (no new persisted fields for clamp state; binding log is ephemeral JSONL).

---

## 6. Performance considerations

- **Clamp pipeline**: 6 × O(1) lookups on `MarketState` per release. ADV lookup already cached per-token in `PriceMonitor` / `DataEngine`. Estimated overhead: <50μs per release. Paper releases are ~1/sec; backtest releases are ~100/bar peak; total budget ≤5ms/bar. Acceptable.
- **Binding log I/O**: buffered writer (default 4KB buffer), flushed on engine shutdown. Paper mode may call `.flush()` per release for observability (configurable). Backtest writes all entries at fold end in one batch.
- **`MarketState` adapter**: 3 implementations share Protocol interface; zero indirection cost (Python attribute lookup inline-optimized).
- **AC-S10 single-fold Q-DEC4**: 2160 hourly bars × 206 tokens × single strategy = ~445k signal computations per fold. v5 simulator benchmark (v5/tests/bench_m4.py) is ~30s per fold. Reviewer-loop tolerable.

---

## 7. Open issues / follow-ups

1. **`funding_buffer_pct` enforcement**: added as optional field in `ClampsConfig`; reduces `available_capital_usd` by `equity × funding_buffer_pct` **before** `CapitalAllocationPolicy.available_capital(...)` is called in the free-capital clamp. Funding buffer is a portfolio-level safety concern, not a policy concern — document this ordering explicitly (quant reviewer flagged as ordering-matters for AC-Sz9 parity).
2. **`max_sizing_equity` 7th optional clamp**: listed in scope but deferred to inside `concentration` clamp as a portfolio-wide ceiling rather than a separate clamp, to avoid re-ordering the fixed 6-clamp sequence.
3. **Strategy ports `priority` field integration**: M7 carry-over `TokenSignal.fraction_of_equity` bound across concurrent positions is enforced via a new portfolio-level pre-clamp (runs before the 6 per-order clamps). Doc note in tasks.md when decomposing.
4. **M4 AC-P3 shadow replay compatibility**: AC-Sz9's 1-week parity fixture is a subset of AC-P3's 24h shadow replay scope. Both MUST pass post-M8.
5. **M9 carry-over**: Fill missing FIX tags (Symbol 55, Side 54, OrderQty 38, OrdType 40) would be cheap to populate in the binding-log schema now; worth asking at Phase-3 review whether to pull this into M8 scope.

---

## 8. Multi-strategy capital allocation (Protocol hook ships M8; policies M9+)

Post-drift-review quant expert consult (`.specs/active/m8-sizing-redesign/allocation-review.md`) modified the original M8 plan. Summary: **Protocol surface lands in M8, policy library deferred**. Rationale: retrofitting the clamp pipeline in M9 touches 5+ newly-stabilized files (dangerous); shipping the Protocol hook with a hardcoded default costs ~30 lines and removes that risk entirely.

### 8.1 New file `v5/sizing/allocation.py` — ships in M8

```python
from typing import Protocol, Literal

class CapitalAllocationPolicy(Protocol):
    """Returns capital available to a strategy at release time.

    Named `CapitalAllocationPolicy` (NOT `AllocationPolicy`) to avoid name
    collision with M9 C-1's `AllocationPolicy` which handles signal-order
    ranking (RandomShuffle / PriorityDesc / TieredPriority). Different
    concerns, different Protocols.
    """
    sampling_cadence: Literal["bar_close", "tick", "release"]

    def available_capital(
        self, strategy_id: str,
        state: "AllocationState",
        clock_now_ns: int,
    ) -> float: ...


class SharedPoolPolicy:
    """Default — every strategy sees the same `state.available_margin`.
    Identity function over the shared pool; matches current v5 behavior
    exactly (backward-compat guarantee). `release` cadence = pure function
    of current state, so AC-Sz9 parity holds trivially."""
    sampling_cadence = "release"

    def available_capital(self, strategy_id, state, clock_now_ns):
        return state.available_margin
```

### 8.2 `AllocationState` TypedDict — narrow read surface

```python
class AllocationState(TypedDict):
    available_margin: float
    per_strategy_equity: dict[str, float]
    rolling_pnl_24h: dict[str, float]
    current_positions_notional: dict[str, float]
```

Narrow TypedDict instead of passing full `SimulationState`. Forward-compat: prevents user-written policies from peeking at individual position data. Free-capital clamp builds this dict at call time; cheap.

### 8.3 Free-capital clamp (AC-Sz3 clause 3) integration

```python
# Inside v5/sizing/clamps.py::free_capital_clamp
def free_capital_clamp(order, state, clock, policy=SharedPoolPolicy()):
    # Funding buffer subtracted BEFORE policy call — portfolio-level safety
    margin_after_buffer = state.available_margin - (
        state.equity * config.funding_buffer_pct
    )
    allocation_state = _build_allocation_state(state)
    strategy_budget = policy.available_capital(
        order.strategy_id,
        allocation_state,
        clock.now_ns(),
    )
    max_margin = min(margin_after_buffer, strategy_budget)
    ...
```

M8 ships with `policy=SharedPoolPolicy()` hardcoded in the clamp. M9 flips this to read from `PortfolioConfig.capital_allocation_policy` — one-line swap. No retrofit of clamp pipeline, no touching of binding-log schema.

### 8.4 FIX vocabulary — StrategyID(1098), not Party(448)

Per quant expert correction: **Party(448) + PartyRole(452)=53 is prime-broker give-up routing, not intra-firm strategy identity.** Correct FIX vocab is **StrategyID(1098) + StrategyType(1099)** (FIXT 1.1 / FIX 5.0 SP2 native).

M8 schema impact: add `strategy_id` field to `sizing_fills.jsonl` binding-log schema (§3e) now, NOT as `party_id` or `fix_448`. When M10 connects to Binance/Deribit FIX the persistence schema is already correct; dual-stamping with venue-specific custom tags (5000+ range for Binance FIX 4.4) happens at the wire layer and does not touch persisted JSON.

### 8.5 What M9 will add (for context only — not M8 scope)

- `PortfolioConfig.capital_allocation_policy: CapitalAllocationPolicy = SharedPoolPolicy()` — pluggable hook.
- **No FixedBudgetPolicy in M9**. Deferred to M10 with proper observability (quant expert flagged 40 lines + 8 tests + config-round-trip story, plus paper-state persistence questions for Protocol instances). M10 ships `FixedBudgetPolicy(budgets, missing_strategy)` with hard `sum(budgets)==1.0±1e-9` validation at construction + `to_config()/from_config()` for paper-restart safety.
- **No DrawdownThrottle in allocation layer**. M9 C-5's existing `RiskComponent` Protocol with `RiskDecision.REDUCE` is the correct architectural home. Two kill-switches in one Protocol is an incident-forensics footgun.
- **Nautilus-style "portfolio manager as Strategy actor" pattern rejected permanently**. Determinism requirements (AC-Sz9 paper-vs-backtest parity) + crash-restart correctness make in-engine enforcement mandatory for this codebase. Quant reviewer endorsed this call 100%.

### 8.6 Known traps documented

- **`sampling_cadence` field on Protocol is load-bearing for AC-Sz9 parity**. Future dynamic policies (Sharpe-weighted, vol-target) will drift between tick-cadence paper and bar-cadence backtest if they read rolling equity without cadence alignment. Crypto-prop incident 2023: 80bps drift on volatile day after 1-week clean shadow replay. `sampling_cadence="bar_close"` enforces snapshot-at-close reads.
- **Cross-strategy symbol correlation under SharedPool** (strategy A: 4% BTC + strategy B: 4% BTC = 8% portfolio exposure): NOT a capital-allocation concern but a risk-layer concern. M9's `MaxGrossExposure` / `MaxNetExposure` risk components hint at this; design note for M9 to add portfolio-level (not strategy-level) concentration when operating under SharedPool.
- **Scalping-pathology under FixedBudget**: tiny budgets × concentration_limit < min_position_usd → all signals rejected silently. M10 FixedBudget constructor will warn.
- **Protocol-instance serialization for paper crash-restart**: persist policy *config* (dict), not instance. `policy.to_config() + policy_from_config(d)` factory pattern. Trivial for SharedPool.

---

## 8. Approval gate (Phase 2 → Phase 3)

**Ready for Phase 3 (decompose + test authoring) once approved.**

**Key design decisions locked**:
- Single clamp entry point: `Order.release_atomic`.
- `MarketState` Protocol unifies 3 data-source impls.
- `SizingIntent` as `(str, Enum)`.
- 12-wave migration (A-L), 91-106h budget, no scope cap.
- Rollback via `use_m8_clamps` flag + `sizing_legacy.py` shim preserved until M9.
- Test authoring via isolated subagent per CLAUDE.md TDD protocol (writes 40-60 tests across 15 files, RED on creation).

Does this design close your value/scope questions, or any piece to tighten before I kick Phase 3?
