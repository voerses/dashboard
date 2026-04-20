# M9 — Clean-Cut Milestone (Regime/Conviction Deletion, Portfolio Primitives, Shim Removal)

**Summary**: The capstone cleanup of the v5 rebuild. Fully deletes v4 shims (regime, conviction, flags, legacy sizing, dual-write logs), elevates `AllocationPolicy` / `RiskComponent` / `ValidationConfig` to first-class `PortfolioConfig` primitives, lands the AC-S10 simulator↔Strategy Protocol bridge so s524m can be measured end-to-end against v4, and deploys a parallel v5 paper runner to an isolated dashboard URL path behind a feature flag.

After M9 ships, v5 is a stand-alone quant-grade engine. No v4 compatibility code remains. v4 continues to run undisturbed in parallel until operator-driven retirement.

---

## Meta-Rule: design over code (M5-M10 v5 rebuild mode)

This milestone is the **final enforcement** of the v5 rebuild meta-rule. Legacy behaviors are deleted, not preserved. No parity gate shields v4 shims in M9. The only parity artifact is **AC-S10 s524m v5 ↔ v4** (which validates the new engine, not the shims).

**Consequences**:
- Bit-identical preservation: **not required** — every shim that existed for AC14-style parity is deleted in M9 per explicit user directive ("take a clean v5 cut here").
- Shadow replay parity (5 bps / 10 bps tolerances): used only for AC-S10 (s524m v4→v5) and the `use_m8_clamps` flag-flip gate.
- Determinism within v5 build: absolute.
- Post-M10: meta-rule sunsets, CLAUDE.md default (`code over specs`) resumes.

---

## Problem

After M5-M8 shipped, six v4-compatibility shims remain live in v5/ because each prior milestone deferred cleanup to "M9 after paper validation." M9 is that cleanup. In addition, four architectural items were intentionally held until the v5 rebuild reached a point where engine-internal semantics could change freely:

1. **Regime coupling in engine**: `RegimeConfig` + `exit_regime_*` behavior + regime columns in `TokenBarArrays` couple the engine to one market-structure theory. Strategies that want regime should own the logic; the engine should execute, period.

2. **Conviction as dual-purpose field**: v4's `conviction_score` (int32 array per bar) serves two roles — ranking AND sizing — baked into one field. M7 split the semantics (`TokenSignal.priority` scalar + `SizingRequest.fraction_of_equity`) but kept the v4 `TokenBarArrays.conviction_score` shim alive. M9 deletes the shim.

3. **`TokenSignal` vs `TokenBarArrays` dual representation**: M7 unified `PortfolioSignals` + per-token arrays into `TokenSignal`. But the per-bar `TokenBarArrays` (int32 direction/priority arrays) still shadow-persists for v4 legacy code paths. After M9, `TokenSignal` is the **only** signal object.

4. **Portfolio primitives scattered across config flags**: AllocationPolicy (signal ranking) doesn't exist as a concept in `PortfolioConfig`; risk checks are ad-hoc if-statements; walk-forward params are spread across multiple config paths. Quant-grade portfolio engines expose these as pluggable Protocol interfaces. M9 lands them.

5. **M8 rollback shim (`sizing_legacy.py` + `use_m8_clamps` flag)**: kept until M9 to allow a 7-day paper-validation window before default-on. Replaced with **replay-parity gate** — we don't wait 7 days wall-clock; we replay recorded market data through both branches and assert identical archives for non-binding bars + documented divergence for binding bars.

6. **M5 multi-leg order shim (`use_multi_leg_orders` flag + `Position.leg` string field)**: kept until M9 for the same reason. Also deleted via replay-parity gate.

7. **AC-S10 s524m v5 ↔ v4 parity unmeasured**: M8 shipped 6 strict-xfail tests in `test_m8_ac_s10_s524m_parity.py` as a forward-deferred trip-wire. They auto-flip to XPASS when the v5 simulator executes the Strategy Protocol end-to-end. Without this bridge, "does v5 reproduce v4's 1,094% s524m?" is unanswerable. M9 lands the bridge.

8. **Dashboard deployment**: v4 dashboard runs live at `/srv/dashboard/current/` serving `/data/state.json` from `v4.run_paper_multi`. v5 needs a parallel deployment at `/v5` URL path behind a feature flag — no disruption to v4 operator workflow.

---

## Scope

### In scope — C-1 through C-8

Each item is an independent deletable shim OR a new pluggable primitive. Tasks are sequenced but individual items are parallelizable.

### C-1 — Conviction → `TokenSignal.priority` + pluggable `SignalArbitrationPolicy`

**Delete** (clean cut):
- `TokenBarArrays.conviction_score: np.ndarray` field (`v5/signals.py:84`)
- `TokenBarArrays.priority: np.ndarray` int32 shim (`v5/signals.py:85`)
- The conviction→priority conversion shim (`v5/signals.py:164-173`)
- `min_conviction_threshold` config knob (wherever it survives)
- All 64 `conviction_score` references across v5/ (8 files per grep)
- Engine sort path at `v5/simulator.py:1396-1398` that indexes `sig.priority[lb]` — replaced by policy-based arbitration

**Land `TokenSignal.priority` as sole carrier**:
- `TokenSignal.priority: float | None` — already shipped by M7 at `v5/strategy_api.py:84`. M9 ensures it is the **only** priority carrier. Ranking within a strategy is strategy-owned via this field; engine does not re-rank.

**Land `SignalArbitrationPolicy` Protocol in new file `v5/arbitration.py`** (NOT in `v5/sizing/` — conceptual separation from M8 `CapitalAllocationPolicy`):

```python
@runtime_checkable
class SignalArbitrationPolicy(Protocol):
    """Resolves contention when emitted signals exceed available slots.
    Within-strategy ranking is strategy-owned via TokenSignal.priority;
    this Protocol is the engine's arbiter across strategies
    (scope="portfolio") or within a strategy's slot limit
    (scope="strategy"). Engine-internal; NOT FIX-serializable — priority
    has no FIX tag analog. No mainstream open-source backtester ships
    an explicit analog; prop-shop-internal pattern made explicit."""

    def rank(self, candidates: list[EntryCandidate],
             state: SimulationState,
             scope: Literal["portfolio", "strategy"] = "portfolio",
             ) -> list[EntryCandidate]: ...

class RandomShuffle(SignalArbitrationPolicy):
    """DEFAULT. Seeded via state.rng. Fairness-first — prevents
    systematic starvation when cross-strategy priorities are
    uncalibrated (e.g. momentum z-scores vs mean-rev normalized
    [0,1]). v4's priority-sort default is NOT the M9 default —
    starvation mode is silent for weeks under uncalibrated priorities."""

class PriorityDesc(SignalArbitrationPolicy):
    """Descending priority; ties broken by (strategy_id, token) lex.
    Opt-in after priority calibration confirmed across strategies."""

class TieredPriority(SignalArbitrationPolicy):
    """Rank-space quantile buckets with intra-tier shuffle. Default
    tier_quantiles=[0.33, 0.66] (bottom 33% / mid / top 33% by rank).
    Tier-membership logged per candidate in arbitration.jsonl."""
    def __init__(self, tier_quantiles: list[float] = [0.33, 0.66]): ...

class RoundRobin(SignalArbitrationPolicy):
    """Weighted round-robin across strategies — each strategy picks its
    next-best (by its own priority) in rotation, weights optional.
    Prevents starvation AND respects intra-strategy priority."""
    def __init__(self, weights: dict[str, float] | None = None): ...
```

- `PortfolioConfig.arbitration_policy: SignalArbitrationPolicy = field(default_factory=RandomShuffle)` — pluggable, composable.
- `StrategySpec.min_slot_guarantee: int = 0` — NEW. Arbitration pre-fills guaranteed slots per strategy BEFORE cross-strategy contention runs. Kills 2022 starvation incident class without requiring FixedBudget.
- `v5/logs/arbitration.jsonl` — NEW telemetry sink, schema per decision: `{bar_idx, strategy_id, token, rank_in, rank_out, tier: int | null, admitted: bool, displaced_by: (strategy_id, token) | null}`. Forensic-friendly; most shops debug starvation via SQL over fills, which is brutal.

**Scope parameter semantics**:
- `scope="portfolio"` — cross-strategy arbitration (one `.rank()` call over all candidates from all strategies). Runs under SharedPool.
- `scope="strategy"` — intra-strategy arbitration when strategy emits more signals than `max_positions` (one `.rank()` call per strategy). Runs under FixedBudget AND under SharedPool when strategy exceeds its own `max_positions`.
- Engine picks scope based on `capital_allocation_policy` mode + `max_portfolio_positions` / `max_positions` thresholds.

**Phase 3.05 de-duplication step** (new engine phase, between C-5 risk and C-1 arbitration):
- Intra-strategy `max_positions_per_symbol` de-dup runs BEFORE arbitration
- Prevents scarce portfolio slots from being consumed on duplicate-symbol candidates that clamp-reject post-arbitration
- AC: a strategy with `max_positions_per_symbol=1` and 2 signals on same token is de-duped to 1 before `.rank()`

**Semantics**:
- `priority: float` — **unbounded real**, sorted **descending** by strategy's own choice; ties broken by `(strategy_id, token)` lexicographic.
- `TieredPriority.tier_quantiles` — operates in **rank-space of emitted priorities** per bar, not absolute [0,1]. Within each tier: shuffled.
- Strategy `generate()` pre-indexes any per-bar series: `priority = float(my_conviction_array[bar_idx])`. Per-bar array never reaches engine.
- `SizingRequest.composite_score` (M8 sizing helper) continues to accept the same scalar; no strategy breakage.

**Canonical scale hook decision (from FIX/Quant reviewer round)**:
- `Strategy.check_scale(pos, bar_ctx)` Protocol method is CANONICAL — matches 19-method Protocol surface and class-based M7 direction.
- `StrategySpec.scale_check_fn` function hook is DEPRECATED in M9; removed in M10.
- Test fixtures using the function hook are migrated to Protocol method in Wave B.

**Enriched `BarContext` for reactive sizing** (NEW in M9):
- `BarContext.ctx: UniverseContext` — so `check_exit` / `check_scale` can call `ctx.per_token(t).atr(14)`, `v5.regimes.detect_crisis(ctx, bar_idx)` directly without stashing `self._ctx` in `generate()`
- `BarContext.state_view: StateView` — read-only snapshot:
  ```python
  @dataclass(frozen=True)
  class StateView:
      equity: float
      portfolio_dd_pct: float
      open_positions_count: int
      total_notional_usd: float
      per_strategy_equity: dict[str, float]
  ```
  Enables "scale down when portfolio dd > 5%", "reduce when I have 3+ correlated positions" patterns.

**DELETED proposal**: the `supports_precompute: bool = True` escape hatch from prior draft is dropped. Anti-pattern — a framework flag that changes execution model is a footgun. Strategies cache internally or they don't; engine doesn't need to know.

### C-2 — Walk-Forward Finalization + CPCV

**Delete**:
- Walk-forward params scattered in `PortfolioConfig`
- Duplicate `WalkForwardConfig` + `CPCVConfig` in `v5/validation.py:69-99`

**Land** (replaces both):

```python
@dataclass(frozen=True)
class CPCVSpec:
    """Combinatorial Purged CV (Lopez de Prado). Produces C(n_groups,
    n_test_groups) paths."""
    n_groups: int = 6
    n_test_groups: int = 2
    purge_bars: int = 100        # bars removed around each test fold
    embargo_bars: int = 50        # blackout after each test fold

@dataclass(frozen=True)
class ValidationConfig:
    train_bars: int = 2000       # rolling-WF window
    recal_bars: int = 500
    cpcv: CPCVSpec | None = None  # None = rolling WF; set = CPCV
    pbo_threshold: float = 0.40  # Probability of Backtest Overfitting cutoff
    deflated_sharpe: bool = True # compute Bailey/de Prado DSR
```

- `WalkForwardRunner.run()` returns `WalkForwardResult(per_fold_metrics, per_path_metrics, pbo, deflated_sharpe)` — path-level + fold-level surface.
- Walk-forward NEVER sees inside signal generation. `v5/validation.py` slices `(train, oos)` windows and invokes the engine N times with clean state.
- `PortfolioConfig.validation_config: ValidationConfig = field(default_factory=ValidationConfig)` — pluggable.

### C-3 — Pull-Based Memoized Indicators

**Delete**: push-model indicator precomputation in `v5/signals.py` for any indicator now served by `ctx.per_token(...)`.

**Land**: `v5/indicators.py` with a memoized registry. Cache key **MUST** include timeframe to avoid 1h-vs-1m collision in MTF mode:

```python
CacheKey = tuple[
    str,                # token
    "BarSpec",          # timeframe (1m / 5m / 1h / ...)
    str,                # indicator name (user-supplied, stable across modules)
    tuple,              # frozen params (via .cache_key() per M7 AC-S3)
    int,                # bar_idx
]
```

- `ctx.per_token("BTCUSDT").ema(20)` returns scalar at current bar (contract inherited from M7 AC-S3, not re-opened)
- Cache scope: **per UniverseContext instance** — new instance per bar in backtest → cache auto-evicts per bar; per tick cycle in paper → cache auto-evicts per tick
- Reconciled against M7 AC-S3: M7 is authoritative; M9 adds registry surface + `timeframe` key only
- Stdlib indicators: EMA, SMA, ATR, RSI, Bollinger, VWAP, Donchian, Z-score

### C-4 — Regime Fully Out of Engine

**Delete (clean cut)**:
- `RegimeConfig` dataclass at `v5/config.py:49-61`
- `StrategySpec.regime_params` override pipeline (if present)
- Regime columns in `TokenBarArrays` (`regime`, `bear_*`, `quiet_*`, `crisis_*` — 5+ columns per signals.py:73)
- Regime computation in `BarProcessor` / `simulator.py`
- `bear_target_mult`, `bear_max_hold`, and other regime-behavioral config knobs in `StrategySpec`

**Land**: `v5/regimes.py` as **optional utility module**:

```python
def detect_crisis(ctx: UniverseContext, bar_idx: int) -> bool: ...
def detect_uptrend(ctx: UniverseContext, bar_idx: int) -> bool: ...
def detect_dispersion(ctx: UniverseContext, bar_idx: int) -> bool: ...
```

- Memoized on the monotonic `ctx_uid` stamped in `_lifecycle_config` (matches shipped pattern at `v5/regimes.py:57`, not `id(ctx)`)
- Strategies import and call these themselves: `if v5.regimes.detect_crisis(ctx, i): ...`
- Engine has **zero** references to regime
- **New requirement — `ctx.market_indices` namespace**: Cross-sectional series (BTC price history, TOTAL2, TOTAL3, DXY) that regime detection needs are NOT tokens in the tradeable `InstrumentRegistry`. Added as a first-class namespace on `UniverseContext`:
  ```python
  ctx.market_indices["BTC_CLOSE"][bar_idx]
  ctx.market_indices["TOTAL2"][bar_idx]
  ```
  Populated from the same parquet cache pipeline as tokens. Strategies that don't need regime never read it.

**Porting — explicit M9 task list** (~7h, mandatory for AC-S10 + C-4):
1. **`s524m_v5.py:336-339`** — replace `bar_ctx.regime == CRISIS` with `v5.regimes.detect_crisis(bar_ctx.ctx, bar_ctx.bar_idx)` (uses enriched BarContext.ctx from C-1). Mandatory — regime columns deleted in Wave B3; current code crashes otherwise.
2. **`s523c_v5.py:37`** — re-point `CRISIS = 0` const to `from v5.regimes import CRISIS`. One line.
3. **s524m + s523c**: implement `VectorizedStrategy` Protocol with `to_token_bar_arrays(ctx)` method (~20 lines each; thin wrapper around existing `_evaluate_token` precompute). Opt-in vectorized fast path (Zipline/Pipeline institutional pattern). **s513** does NOT implement `VectorizedStrategy` — its trigger logic is bar-reactive; uses engine fallback (`_engine_precompute_fallback` calls `generate()` in setup loop). Correct but slower; acceptable at current scale.
4. **Bit-identity parity test** for s524m + s523c (mandatory discipline): assert byte-identity between `to_token_bar_arrays()` output and `_engine_precompute_fallback(strategy)` output on Q-DEC4 2025 fold. Prevents silent research-vs-paper drift — institutional #1 discipline.
5. **No `check_exit` / `check_scale` signature changes** — `ctx` lives on `BarContext` (C-1 enrichment), NOT as third arg. Signatures stay `check_scale(pos, bar_ctx)` / `check_exit(pos, bar_ctx)` as shipped.

**M10 deferrable** (cleanup, non-blocking): `scale_check_fn` → `Strategy.check_scale` Protocol migration; full strategy signature audit (grep `bar_ctx.regime` → 0 hits verification); any archived strategies. Tracked in M10 brief.

### C-5 — Risk Components + TradingState

**Land** `v5/risk.py`:

```python
class RiskDecision(Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"   # cites FIX OrdRejReason(103) when serialized
    REDUCE = "REDUCE"   # engine-internal; no FIX analog
    HALT = "HALT"       # cites FIX TradingSessionStatus(340)=3 Halted

@runtime_checkable
class RiskComponent(Protocol):
    sampling_cadence: Literal["bar_close", "tick", "release"]
    def check(self, candidate: EntryCandidate,
              state: SimulationState, clock_now_ns: int) -> RiskDecision: ...
```

- `sampling_cadence` required — matches M8 `CapitalAllocationPolicy` pattern. Prevents the 2023 crypto-prop 80bps drift class of incident (cited in `v5/sizing/allocation.py:12-14`).
- Components (ALL shipped in M9 — user directive "don't defer"):
  - `DrawdownThrottle(thresholds: list[tuple[float, float]], scope: Literal["portfolio","strategy"])` — `(dd_trigger, position_scale)` tuples; at `equity_dd >= dd_trigger`, multiplies `SizingRequest.fraction_of_equity` by `position_scale` (scale=0.0 = full halt for that scope). Evaluated in order, highest-matching wins.
  - `MaxGrossExposure(limit_pct: float, denominator: Literal["notional_usd","initial_margin","equity"])` — denominator explicit; no ambiguity vs M8 concentration clamp.
  - `MaxNetExposure(limit_pct: float)` — net long/short imbalance cap.
  - `MaxConcurrentOrders(limit: int)` — total open orders across strategies.
  - `DailyLossLimit(max_daily_loss_usd: float, day_boundary: Literal["utc_midnight","session_open"])` — day boundary explicit (no wall-clock ambiguity; `time.time()` banned by M7 AC-V2).
  - `FundingSafetyCheck(max_funding_bps: float, action: Literal["warn","block"])` — extreme funding rate gate.
  - **`MaxCorrelatedExposure(max_strategies_per_symbol: int = 2, max_correlated_notional_pct: float | None = None, corr_threshold: float = 0.85, corr_lookback_bars: int = 168)`** — NEW. Per-symbol cross-strategy limit + anti-correlated-pair check. Blocks when N+ strategies attempt the same token OR when summed notional on tokens with ρ > corr_threshold exceeds portfolio equity fraction. **Correlation source**: 168-bar log-return series from `ctx.per_token(t).close_array[bar_idx-168:bar_idx]`, computed at `sampling_cadence="bar_close"` only, memoized per `(bar_idx, frozenset(token_set))` hash (avoids O(N²) recompute every bar). **Throttles** `SizingRequest.fraction_of_equity` rather than hard-rejecting (prevents double-throttle with M8 concentration clamp). Closes the 2022 "3 strats all long BTC" scenario (cited allocation-review.md M6-i).
  - **`PerSymbolStrategyLimit(limits: dict[str, int])`** — NEW. Hard per-token cap on concurrent strategies (e.g., `{"BTCUSDT": 2}`). Complement to MaxCorrelatedExposure — one is correlation-based, one is explicit-symbol-based.

- **Ordering contract vs M8 clamps** (resolves Quant Section A #16):
  - Risk components run in **Phase 3 pre-allocation** on **aggregate multi-strategy state**.
  - M8 clamps run at `Order.release_atomic` **post-allocation** on **per-order state**.
  - Complementary, not redundant. An AC verifies a throttled candidate reaches M8 with already-reduced size (no double-counting).

- `TradingState`: derived from risk-component verdicts on each bar:
  - `ACTIVE`: normal operation
  - `REDUCING`: new entries BLOCKED; exits via stop/trail/exit-chain ALLOWED; `Strategy.check_scale` returning negative qty_delta ALLOWED; positive qty_delta BLOCKED; linked-leg auto-propagation of REDUCES allowed, INCREASES blocked
  - `HALTED`: all new entries blocked; in-flight `TRIGGERED` orders cancelled (explicit transition AC)
- `TradingState` is **global** (not per-strategy) — `DailyLossLimit` and `DrawdownThrottle(scope="portfolio")` flip it; `DrawdownThrottle(scope="strategy")` scopes the `REDUCING` effect to that strategy only via a per-strategy override mask.

- `PortfolioConfig.risk_components: list[RiskComponent] = field(default_factory=list)` — empty default (no-op); opt-in.

### C-6 — Dashboard Rework (v5-only)

**Delete from v5 dashboard path only** (v4 dashboard at `/` unchanged):
- Regime display panels
- `consolidate_partial_trades` band-aid

**Land** (in `v5/dashboard_state.py`, writing to `state_v5.json`):
- Dual grouping view (toggle in UI):
  - **Position-level** by `parent_position_id` — scaling history per position (M2 ScalingEvent integration)
  - **Order-level** by `order_id` — multi-leg order status with all legs (M5 Order.legs)
- New trade-table columns: `scale_count`, `leaves_qty`, `has_scaling`, `r_anchor_price`, `binding_constraint`
- Scaling timeline in position detail view (M2 ScalingEvent data)
- `binding_constraint` column **JOINS** `v5/logs/sizing_fills.jsonl` by `order_id`; missing join entries render `-` not error
- FIX-aligned counters in state_v5.json:
  - `partial_fills` — FIX OrdStatus(39)=1 PartiallyFilled count (non-terminal `Position.reduce` events only)
  - `increase_fills` — scale-up events
  - `contingent_fills` — FIX ContingencyType(1385)≠0 executions (auto-propagated linked-leg)
  - `entry_scale_downs` — engine-internal; entries clamped by M8 concentration/ADV/capital

### C-7 — AC-S10 Capstone: v5 Simulator ↔ Strategy Protocol Bridge

**The heaviest item (20-30h). Unblocks real v5 metrics end-to-end.**

Current state: `WalkForwardRunner.run()` returns `_stub_metrics()` all-zero (`v5/validation.py:974-984`). `v5/simulator.py` `simulate_portfolio` still consumes pre-M7 v4-style array signals.

Needed:
- Bridge `strategy.generate(ctx, bar_idx) → UniverseSignals` into `simulate_portfolio`'s internal arrays per-bar
- `on_start` / `on_stop` lifecycle + per-bar `generate` loop + exit-check dispatch
- Reuse `v5/report.py::compute_portfolio_metrics` for final metric dict
- **Implementation approach**: Python-per-bar callback (option C from Quant Section D #2). Reason: simplest, deterministic, one-fold WF runtime is ~seconds not minutes — the speed gain from numba/vectorized-upfront is not worth the complexity in this rebuild. Decision documented here and in design doc to close the reviewer question.

**Trip-wire flip**: 6 `strict=True` xfail tests in `v5/tests/test_m8_ac_s10_s524m_parity.py` auto-flip to XPASS when the bridge lands. They MUST pass within **0.5% relative tolerance** on the Q-DEC4 2025 206-token fold. If they flip but assertions fail → STOP: structural divergence, not a clamp bug.

s524m v4 baseline: 1,094% annual sum (memory). v5 target: within 5.5% absolute on that figure.

### C-8 — v5 Paper Runner Deployment (parallel to v4, feature-flagged)

**Deployment plan (from session discussion — Option D)**:

1. **Dashboard HTML — single file, path-aware fetch** (~8 lines of JS in `/srv/dashboard/current/index.html`):
   ```js
   const view = location.pathname.startsWith('/v5') ? 'v5' : 'v4';
   const STATE_URL = view === 'v5' ? '/data/state_v5.json' : '/data/state.json';
   ```
   Serve `/v5/*` → same file via symlink `/srv/dashboard/current/v5 -> .`.

2. **Isolated v5 runner state** (zero shared state with v4):

   | v4 (unchanged) | v5 (new) |
   |---|---|
   | `/tmp/paper_runner.pid` | `/tmp/paper_runner_v5.pid` |
   | `/tmp/paper_runner.log` | `/tmp/paper_runner_v5.log` |
   | `state/v4_paper_multi/` | `state/v5_paper_multi/` |
   | `/srv/data/state.json` | `/srv/data/state_v5.json` |
   | `v4.run_paper_multi` | `v5.run_paper_multi` |
   | `configs/runner_pool_config.json` | `configs/runner_pool_config_v5.json` |

3. **Feature flag** — `tools/start_v5_paper.sh` (new) and `tools/stop_v5_paper.sh` (new); env-gated:
   ```bash
   if [ "${V5_PAPER_ENABLED:-0}" != "1" ]; then
     echo "V5 paper disabled (set V5_PAPER_ENABLED=1 to enable)"; exit 0
   fi
   ```
   Default **OFF**. `start_all_services.sh` NOT modified.

4. **v5 runner config**: `configs/runner_pool_config_v5.json` with ported strategies (s513_v5, s523c_v5, s524m_v5). v4 config untouched.

5. **Kill-switch drills**:
   - Stop v5: stops `state_v5.json` writes → `/v5` URL shows OFFLINE banner (same logic as v4 offline); v4 unaffected
   - Delete v5 entirely: delete `tools/start_v5_paper.sh` + `stop_v5_paper.sh`, remove the 8-line JS `view` detection, delete `/srv/dashboard/current/v5` symlink, `rm configs/runner_pool_config_v5.json`. v4 path never touched.

### C-9 — `CapitalAllocationPolicy` Pluggable + `AllocationState` Extension

**Context**: M8 shipped the `CapitalAllocationPolicy` Protocol and `SharedPoolPolicy` in `v5/sizing/allocation.py`, but the policy instance is **hardcoded** inside `release_atomic` free-capital clamp. M9 makes it pluggable.

**User directive**: FixedBudgetPolicy is NOT shipped in M9 (user call: "we dont have fixed budget policy"). Only `SharedPoolPolicy` ships as the default; users write their own `CapitalAllocationPolicy` subclasses if they need non-shared allocation.

**Land**:
1. **`PortfolioConfig.capital_allocation_policy: CapitalAllocationPolicy = field(default_factory=SharedPoolPolicy)`** — first-class field; consumed by free-capital clamp via config hand-off.
2. **Extend `AllocationState` TypedDict** with `market_snapshot: dict[str, float]` — enables user-written dynamic allocators (e.g., `DynamicRegimeAllocator`) to read BTC/TOTAL2/regime flag at sampling time without needing a direct `UniverseContext` reference. Keeps the narrow-read-surface discipline while enabling dynamic patterns.
3. **Pattern catalog in design.md** — 3 worked examples for user reference:
   - `SharedPoolPolicy` (shipped M8, default)
   - `DynamicRegimeAllocator` (user-code example — 60% bull / 30% bear via `market_snapshot["regime_flag"]`)
   - `EqualRiskContribution` (user-code example — Kelly-like per-strategy vol-weighted allocation via `rolling_pnl_24h` + `per_strategy_equity`)

**FIX StrategyID(1098) dual-stamp in `sizing_fills.jsonl`** (from allocation-review.md §Q4 — prevents Binance/Deribit M10 wire-format migration pain):
- `strategy_id: "s524m"` — canonical internal
- `fix_1098: "s524m"` — FIX-native persistence key
- `fix_1099: str | None = None` — FIX 1099 StrategyParameters group placeholder
- Document all in sizing_fills.jsonl schema at `v5/sizing/binding_log.py`
- **DO NOT** use Party(448)/PartyRole(452)=53 — that's prime-broker give-up routing, NOT intra-firm strategy identity

**`funding_buffer_pct` ordering clarification** (from allocation-review.md §Q2): subtract `equity × funding_buffer_pct` **BEFORE** the policy's `available_capital()` call. Funding buffer is portfolio-level safety, not policy concern. Documented in M8 design §7.1; M9 re-verifies the ordering in the free-capital clamp path.

### C-10 — Hook Cleanup + TickCadencePolicy + Arbitration Analyzer (pulled back from M10 per user directive)

**Land**:
1. **`StrategySpec.scale_check_fn` function hook REMOVAL** (was M9 deprecation + M10 removal; now M9 removal in Wave F). Protocol method `Strategy.check_scale(pos, bar_ctx)` is canonical. Function hook deleted; any fixture using it migrated to Protocol method.
2. **Full strategy signature audit** (grep verification in Wave F):
   - `grep -rn "bar_ctx.regime" v5/ --include="*.py"` → 0 hits (regime removal complete)
   - `grep -rn "scale_check_fn" v5/ --include="*.py"` → 0 hits (function hook gone)
   - `grep -rn "conviction_score\|TokenBarArrays.priority" v5/ --exclude-dir=tests` → 0 hits (shims gone)
3. **`TickCadencePolicy` production scaffolding** (M8 AC-Sz9 carry-over): with real paper-tick MarketState, replace synthetic "5% available_margin nudge" in `parity_fixture.py` with genuine tick-level equity sampling. Empirical not injected. Lands in `v5/sizing/tick_cadence.py`.
4. **Arbitration telemetry analyzer CLI**:
   - `python -m v5.tools.arbitration_analyzer --log v5/logs/arbitration.jsonl --starvation-report`
   - Outputs: per-strategy admission rate, displacement chains, tier-boundary analysis
   - Replaces SQL-over-fills debug pattern most shops use
   - Lands in `v5/tools/arbitration_analyzer.py`

---

## Shim Deletion Table

All shims below were explicit "keep until M9" commitments from prior milestones. All are deleted in M9.

| # | Shim | Location | Kept for | Deletion mechanism |
|---|------|----------|----------|-------------------|
| 1 | `TokenBarArrays.conviction_score` int32 array | `v5/signals.py:84-85, 164-173` | M7 v4-parity | C-1 deletion; strategies re-ported to emit `TokenSignal.priority` scalar |
| 2 | `TokenBarArrays.priority` int32 array | `v5/signals.py:85` | M7 v4-parity | C-1 deletion |
| 3 | `RegimeConfig` + regime columns in `TokenBarArrays` | `v5/config.py:49-61`, `v5/signals.py:73` | engine regime coupling | C-4 deletion |
| 4 | `bear_*` / `quiet_*` / `exit_regime_*` behavior knobs | `StrategySpec`, exit handlers | v4 regime overrides | C-4 deletion; strategies call `v5.regimes.*` themselves |
| 5 | `use_m8_clamps: bool = False` flag + both branches | `v5/config.py:219`, `v5/simulator.py:~1571` | M8 rollback protocol | **Replay-parity gate** (see below), then flip default → True, then delete flag + both branches |
| 6 | `v5/sizing_legacy.py` rollback shim | whole file | M8 rollback | Delete after flag gate passes; cascade: `v5/simulator.py` import + `globals()["get_sizing_model"]` alias, `v5/paper_engine.py` import, `v5/test_v5_portfolio.py` legacy path, AC-Sz6 grep carve-out |
| 7 | `StrategySpec.use_multi_leg_orders: bool = False` flag | `v5/orders.py` / `StrategySpec` | M5 rollback | Replay-parity gate, flip default → True, delete flag + `v5/simulator.py:2740-2877` legacy `trigger_combined_entry` branch (~140 lines) |
| 8 | `Position.leg: str = "primary"` field | `v5/position.py:68` | M5 backcompat | Delete; migrate 8 read sites in `v5/simulator.py:413,541,589,1045,1065,1191,2111,2278` to `leg_ref_id` + venue/market lookup |
| 9 | `Instrument.contract_type: str \| None` free-string | `v5/data/instruments.py` | M6 migration alias | Delete; all callers read typed `contract_subtype: Literal[...]` |
| 10 | `armed_log.jsonl` dual-write | `v5/orders_log.py` | M5 compat | Delete dual-write path; `orders_log.jsonl` sole sink |
| 11 | `paper_engine._armed_tokens` property + alias | `v5/paper_engine.py` | M4 T16b compat | Delete; all writers/readers use `_pending_entries` directly |

**Bonus shim #12 — `Leg.market` OR `Leg.settlement_type` redundancy** (`v5/orders.py:397,414`): M5 kept both; M9 picks **`settlement_type`** as authoritative (maps to FIX `LegSettlType(587)`) and drops `market`. Coin-flip settled by FIX-tag precedence.

**AC-Sz6 grep carve-out**: with shim #6 deleted, the `globals()["get_sizing_model"]` alias at `v5/simulator.py` goes away; AC-Sz6 grep reverts to its strict form (no module-attribute assignment tricks needed).

---

## FIX Carry-Over Corrections (from FIX architect review)

- **`Fill` dataclass FIX tags 55/54/38/40 on Fill**: Reviewer correctly flagged that M7's design intentionally put these on `Order` and backfills via ClOrdID(11) linkage. M9 does **NOT** re-open this — the decision at `v5/fill.py:1-18` stands. Original M7 carry-over item #1 is **removed from M9 scope**.
- **`Leg.to_fix_leg_side()` / `Leg.to_fix_leg_ord_type()` serializers**: still needed for M10 FIX gateway. One-line methods. Kept as M9 item.
- **`RiskDecision` FIX citations**: `REJECT` cites `OrdRejReason(103)`; `HALT` cites `TradingSessionStatus(340)=3`; `REDUCE` has no FIX analog (note added to docstring). Prevents M10 gateway from inventing ad-hoc mappings.
- **`AllocationPolicy` non-FIX-serializable note**: priority is engine-internal; no FIX wire analog; documented in C-1 Protocol docstring.
- **M4 deferred 10-test gate (original brief lines 244-264)**: **stale — deleted**. `pytest.fail("blocked on...")` guards no longer exist in `v5/tests/`; verified 11/11 passing in `test_m4_parity_{hourly,mtf,look_ahead}.py`.

---

## Quant Carry-Over Items (from M8)

- **Tier-blend MMR `maintenance_amount` term** (Risk reviewer): `DEFAULT_MMR_SCHEDULE` extended to `(upper, mmr, maint_amount)` tuples; `_liquidation_distance_clamp` uses `Σ(tier_notional_i × mmr_i − maint_amount_i) / notional`. Tier-spanning positions at $300k (tiers 1+2+3) match Binance calculator output within 1 bp.
- **`test_cross_deep_drawdown_would_force_liquidation` boundary tighten**: via test-dispute resolution protocol (reviewer subagent approves strengthening), change `cross <= 10_000.0` + `iso > cross` → `cross < 0` + `iso > 0`.
- **AC-Sz9 TickCadencePolicy production scaffold**: with real paper-tick MarketState, replace the synthetic "5% available_margin nudge" with genuine tick-level equity sampling.

---

## Observability / Threat-Model Hardening (from M7 carry-overs)

- **AC-S4 `ctx` runtime-mutation** (M7 Quant round-8): wrap `_lifecycle_config` in `MappingProxyType` in `UniverseContext.__post_init__`; rename `_stopped` → name-mangled `__stopped` OR move to engine-owned registry. Strategy loader rejects `object.__setattr__(ctx, ...)` and `ctx._lifecycle_config[...] = ...` forms.
- **Uid leak in quarantine registry** (M7 Quant round-7): on `WeakKeyDictionary` finalizer, also remove uid from `_quarantined_uids`. Fixes docstring "auto-decays" claim.
- **Quarantine registry production wiring** (M7 Quant round-7): `BarProcessor` 15-callback dispatch consults `_is_quarantined` before invoking generate/check_*/filter_entry/on_*.
- **Stale XPASS markers** (M7 Quant round-3/4/6): identify via `pytest --runxfail -v`; convert to plain passes or `strict=True`.
- **Synthetic-clock injection contract**: all production paths inject a `Clock`; document the contract in `UniverseContext` docstring.
- **`BaseStrategy.exception_counter` style**: class-attr → `__init__` self-attr (M7 Quant round-1 NIT).

---

## Key Acceptance Criteria

1. **Regime deleted from engine**: `grep -rn "RegimeConfig\|regime_params\|exit_regime\|\.regime\[" v5/ --exclude=regimes.py --exclude-dir=tests | wc -l == 0`. Engine tests pass without any regime config. s523c_v5 and s524m_v5 re-ported: they call `v5.regimes.detect_*` in their own `generate()` methods.

2. **Conviction deleted**: `grep -rn "conviction_score\|conviction_array\|min_conviction_threshold" v5/ --exclude-dir=tests | wc -l == 0`. `TokenBarArrays` no longer has `conviction_score` or int32 `priority` array fields. `TokenSignal.priority: float | None` is the only priority carrier.

3. **SignalArbitrationPolicy pluggable (4 impls)**: `RandomShuffle` (DEFAULT, seed-deterministic, prevents starvation) + `PriorityDesc` (opt-in, sorts DESC by strategy-provided priority, ties broken by strategy_id/token lex) + `TieredPriority(tier_quantiles=[0.33,0.66])` (3-bucket rank with shuffled ties, tier-membership logged) + `RoundRobin(weights=None)` (each strategy picks its next-best in rotation). Tests: (a) `RandomShuffle` default over 100 bars with uncalibrated priorities has min/max strategy admission rates within 2σ of equal; (b) `PriorityDesc` returns top-3-by-priority for 10 signals + max_positions=3; (c) `TieredPriority` returns one per tier deterministically; (d) `RoundRobin` rotates equally with `weights=None`; (e) `StrategySpec.min_slot_guarantee=2` reserves 2 slots before cross-strategy contention runs; (f) scope parameter: `scope="strategy"` runs per-strategy under FixedBudget, `scope="portfolio"` runs globally under SharedPool.
   **`min_slot_guarantee` edge cases** (4 sub-ACs): (g) `sum(min_slot_guarantee) > max_portfolio_positions` → raises `ValueError` at `PortfolioConfig.__post_init__` (matches FixedBudgetPolicy sum-validation precedent); (h) strategy with 0 signals + `min_slot_guarantee=2` → slot unused that bar, NOT released to cross-strategy pool (deterministic over-allocation acceptable); (i) `min_slot_guarantee=5` + `StrategySpec.max_positions=3` → effective guarantee = `min(5, 3) = 3`; (j) guarantee count is **post-dedup** (Phase 3.05 runs first), not raw candidate count.
   **Phase ordering (pinned)**: Phase 3.05 dedup → `min_slot_guarantee` reservation → SignalArbitration → per-order M8 clamps. Dedup MUST run first so guaranteed slots can't be consumed by duplicate-symbol candidates.

4. **Memoized indicators with MTF safety**: `ctx.per_token("BTCUSDT").ema(20)` in 1h context and same call in 1m context return **different** cached values (timeframe in key). Called twice in same bar → identity check. Called on new bar → recomputes.

5. **Risk components + TradingState + ordering (8 components shipped)**:
   - `DrawdownThrottle([(0.10, 0.5), (0.20, 0.0)])` reduces sizing 50% at 10% dd, halts at 20% dd.
   - `MaxGrossExposure(0.80, denominator="notional_usd")` blocks when |notional| sum > 80% equity.
   - `MaxNetExposure(0.30)` caps long/short imbalance.
   - `MaxConcurrentOrders(40)` caps total open orders across strategies.
   - `DailyLossLimit(5000.0, "utc_midnight")` halts at $5k/day loss.
   - `FundingSafetyCheck(50.0, "block")` blocks entries when funding > 50bps.
   - **`MaxCorrelatedExposure(max_strategies_per_symbol=2, corr_threshold=0.85)`** — blocks when 3+ strategies attempt same token OR tokens with ρ>0.85 exceed summed notional cap. **No-double-throttling AC**: with `max_correlated_notional_pct=0.50` and 3 BTC-correlated candidates, component adjusts `SizingRequest.fraction_of_equity` such that post-M8-concentration sum ≤ 50%. No double-throttle with concentration clamp.
   - **`PerSymbolStrategyLimit({"BTCUSDT": 2})`** — hard per-token strategy cap.
   - Test verifies throttled candidate's `SizingRequest.fraction_of_equity` is already-reduced when it reaches M8 clamps (no double-counting).
   - TradingState transitions: ACTIVE → REDUCING (new entries blocked; `check_scale(qty_delta<0)` ALLOWED; `check_scale(qty_delta>0)` BLOCKED) → HALTED (new + in-flight TRIGGERED cancelled) → ACTIVE (recovery via `allow_auto_resume: bool = False`).
   - `RiskDecision.REJECT/HALT` docstrings cite FIX OrdRejReason(103) / TradingSessionStatus(340)=3.

6. **CPCV + PBO + DSR**: `ValidationConfig(cpcv=CPCVSpec(n_groups=6, n_test_groups=2))` produces C(6,2)=15 paths. `WalkForwardResult.pbo < 0.40` and `deflated_sharpe > 0` asserted on a positive-alpha synthetic fixture. Embargo + purge work: bars in embargo window don't appear in either train or test across adjacent folds.

7. **AC-S10 bridge (opt-in vectorized + engine fallback + parity test)**:
   - All 6 `test_m8_ac_s10_s524m_parity.py` strict-xfails flip to PASS within **0.5% relative tolerance** on Q-DEC4 2025 206-token fold.
   - s524m v5 Sharpe/return/max_dd/turnover/win_rate all within tolerance of v4 baseline.
   - `VectorizedStrategy` Protocol (sub-Protocol of `Strategy`) adds optional `to_token_bar_arrays(ctx)` method. Strategies that implement it get fast-path; strategies that don't get engine fallback via `_engine_precompute_fallback()` calling `generate()` in setup loop.
   - **Parity test tolerance (institutional #1 discipline)**: for every strategy implementing `VectorizedStrategy`, assert equality between `to_token_bar_arrays()` output and engine-fallback output on Q-DEC4 2025 fold using **`np.testing.assert_array_equal`** on integer fields (`direction`, `priority` when castable) and **`np.array_equal(equal_nan=True)`** on float fields (`stop_mult`, `r_anchor_price`, etc.) — **zero tolerance**. Both paths compute from the same source arithmetic; any drift is a Protocol contract violation. Currently applies to s524m + s523c. s513 doesn't implement `VectorizedStrategy`; fallback-only.
   - Prevents silent research-vs-paper drift — #1 way top shops catch adapter divergence.

8. **Shim deletion verified**: all 12 shim table rows show zero hits in v5/ (except `v5/regimes.py` for regime utility and vendored tests that assert the deletion). `use_m8_clamps`, `use_multi_leg_orders`, `Position.leg`, `contract_type`, `armed_log.jsonl`, `_armed_tokens`, `sizing_legacy.py` all gone.

9. **TokenSignal sole signal object**: `grep -rn "TokenBarArrays\|PortfolioSignals" v5/ --exclude-dir=tests` returns zero hits. `TokenSignal` + `UniverseSignals` from `v5/strategy_api.py` are the only signal carriers.

10. **Replay-parity gate for flag flips** (fixture source: historical parquet cache `data/perp/1m_cache/` + `data/perp/1h_cache/`, NOT live WS recording):
    - **Fixture construction**: pick a 7-day historical window engineered to force each of the 6 M8 clamps to bind at least once (ADV clamp via low-liquidity token; concentration via 3+ concurrent signals on same token; free-capital via deep drawdown period; min-size via small equity; liq-distance via a -15% intraday day; slippage via extreme order size). Persist the fixture token-list + window bounds to `v5/tests/fixtures/m9_replay_parity_7d/manifest.json`. Runs deterministically; same input → same output every invocation. Live WS `shadow_replay_24h/` (35-min capture) retained as secondary smoke fixture only.
    - **`use_m8_clamps` gate**: run paper_engine on the parquet-windowed fixture with flag=False → archive A; flag=True → archive B. Non-binding bars byte-identical between A and B; every divergence row on binding bars has a matching `sizing_fills.jsonl` entry with the expected clamp name. Runs in seconds. Replaces 7-day wall-clock wait.
    - **`use_multi_leg_orders` gate**: same mechanism on a multi-leg fixture (s513_v5 OTOCO bracket orders on the same 7-day window); non-contingent single-leg orders byte-identical across flag states.
    - **Side-task (not M9 scope)**: the live `v5/tools/record_ws_tap.py` silently died at 35 minutes during the Apr 19-20 run. Not M9-blocking since replay uses parquet cache, but harden the recorder with a keepalive/supervisor in a follow-up milestone.

11. **Dashboard deployment, v4 untouched**:
    - `/` URL unchanged — v4 operator workflow intact.
    - `/v5` URL renders from `state_v5.json`; shows OFFLINE banner when v5 runner stopped.
    - `start_all_services.sh` never modified (git diff empty).
    - `V5_PAPER_ENABLED=0` default → `start_v5_paper.sh` no-ops.
    - Kill-switch test: stop v5 runner, v4 paper state continues ticking, `/` URL shows v4 live, `/v5` URL shows OFFLINE.

12. **PortfolioConfig as pluggable portfolio primitive**:
    ```python
    @dataclass
    class PortfolioConfig:
        strategies: list[StrategySpec]
        capital: float
        arbitration_policy: SignalArbitrationPolicy = RandomShuffle()     # C-1 NEW
        capital_allocation_policy: CapitalAllocationPolicy = SharedPoolPolicy()  # C-9 NEW (M8 Protocol, M9 pluggable)
        validation_config: ValidationConfig = ValidationConfig()           # C-2 NEW
        risk_components: list[RiskComponent] = []                          # C-5 NEW
        clamps_config: ClampsConfig = ClampsConfig()                       # shipped M8
    ```
    No `use_m8_clamps`, no `regime_params`, no scattered walk-forward knobs.

13. **CapitalAllocationPolicy pluggable** (C-9): `SharedPoolPolicy` default (M8-shipped); `PortfolioConfig.capital_allocation_policy` field pluggable. **Hot-swap protection**: changing `PortfolioConfig.capital_allocation_policy` with non-empty position book raises `ConfigError` (paper-mode reload footgun; closes allocation-review.md §Q3 concern). `AllocationState` extended with `market_snapshot: dict[str, float]` enabling user-written dynamic allocators (`DynamicRegimeAllocator`, `EqualRiskContribution`) without direct UniverseContext access. FIX StrategyID(1098) dual-stamped in `sizing_fills.jsonl`: `strategy_id` + `fix_1098` + optional `fix_1099: str | None = None` (FIX 1099 StrategyParameters group placeholder, reserved for M10 routing payload, forestalls schema migration). FixedBudgetPolicy not shipped per user directive.

14. **BarContext reactive sizing surface** (from per-bar-reactivity review): `BarContext.ctx: UniverseContext` present (so `check_exit`/`check_scale` can call `ctx.per_token().atr()`, `v5.regimes.detect_crisis()` directly); `BarContext.state_view: StateView` present with read-only snapshot `{equity, portfolio_dd_pct, open_positions_count, total_notional_usd, per_strategy_equity, per_symbol_exposure: dict[str, float], bars_since_last_fill: dict[str, int]}`. `per_symbol_exposure` enables "scale down when my BTC exposure > X%"; `bars_since_last_fill` enables "cool-down after recent fill" — both common multi-strategy crypto patterns. `ctx` is **on BarContext ONLY**, NOT also a third arg to `check_scale` / `check_exit` — signatures are `check_scale(pos, bar_ctx)` / `check_exit(pos, bar_ctx)`, strategies read `bar_ctx.ctx`. No dual-surface redundancy. Test: "scale down when portfolio dd > 5%" pattern expressible in `check_scale` with ≤5 lines.

15. **Canonical scale hook + deprecation**: `Strategy.check_scale(pos, bar_ctx)` Protocol method is canonical. `StrategySpec.scale_check_fn` function hook emits DeprecationWarning in M9; removed in M10. Test: both hooks dispatching simultaneously raises ValueError.

16. **Pattern catalog documentation**: design.md appendix documents 3 `CapitalAllocationPolicy` patterns (SharedPool shipped + DynamicRegime + EqualRiskContribution user-code templates) and 4 reactive-sizing patterns (scale-up-on-1R, scale-down-on-ATR-spike, Kelly-on-drawdown, pyramid-vs-layered-entry) with worked examples.

17. **Observability hardening verified**: `ctx._lifecycle_config[...] = ...` and `object.__setattr__(ctx, '_stopped', True)` rejected by strategy loader. Quarantine uid GC: after WeakKeyDictionary finalizer, `_quarantined_uids` entry removed. BarProcessor skips quarantined strategies across all 15 callbacks.

18. **Arbitration telemetry**: `v5/logs/arbitration.jsonl` emits 1 row per candidate per bar with `{bar_idx, strategy_id, token, rank_in, rank_out, tier, admitted, displaced_by}`. Forensics on starvation / tier-boundary questions become SQL-free. **Size cap**: rotate at 500MB with gzip compression; 1-year 200-token × 3-strategy × hourly backtest produces <2GB total (~21M rows at ~50 bytes/row compressed).

19. **Phase 3.05 de-duplication step**: engine phase between C-5 risk and C-1 arbitration runs `max_positions_per_symbol` de-dup per strategy. AC: strategy with `max_positions_per_symbol=1` and 2 signals on same token is de-duped to 1 before `.rank()` is called.

20. **Paper-tick vs backtest-bar bit-identity**: `s524m_v5.generate()` returns identical `UniverseSignals` for identical input state regardless of path (paper BarProcessor tick-dispatch vs backtest simulate_portfolio bar-dispatch). Prevents silent mode drift.

21. **Fallback state-mutation guard**: during `_engine_precompute_fallback`, strategy `generate()` calls are made via a `__setattr__`-guarded proxy. Any attribute write to `self` during the setup loop raises `StrategyStateMutationError`. Test: a strategy that sets `self._last_bar = bar_idx` in `generate()` causes the fallback to raise, forcing the author to move state into explicit precompute or to implement `VectorizedStrategy`. Closes the silent research-vs-paper drift hole where fallback and tick-dispatch would desync on stateful-self strategies.

22. **Engine/strategy isolation (audit grep, makes §11.1 future-rewrite auditable)**:
    - `grep -rn 'from v5.simulator\|from v5.engine\|from v5.bar_processor' v5/strategies/ --exclude-dir=tests` returns 0 hits
    - `grep -rn 'strategy\._[a-z]' v5/simulator.py v5/engine.py v5/bar_processor.py` returns 0 hits
    - Ensures future engine rewrite (§11.1 two-engines split if ever needed) is not blocked by hidden cross-layer coupling.

23. **Paper-vs-vectorized parity**: for s524m_v5 and s523c_v5 (both implement `VectorizedStrategy`), fixture replays one day of paper ticks through the M4 BarProcessor path, collects per-bar `UniverseSignals`, assembles into `dict[str, TokenBarArrays]`, and asserts equality (same tolerance as AC #7) with `strategy.to_token_bar_arrays(ctx)` output on the same day's data. Catches the silent drift class where adapter-vs-tick-dispatch diverge.

24. **Sampling-cadence coordination** (prevents M8 Sz9-class incident at the risk layer): a `RiskComponent(sampling_cadence="bar_close")` and a `CapitalAllocationPolicy(sampling_cadence="bar_close")` configured on the same bar are invoked with identical `clock_now_ns` values. Test mocks both `check()` and `available_capital()`, asserts captured `clock_now_ns` equal. Prevents phase-ordering drift between risk and allocation layers.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M4** (BarProcessor) | Required — Risk components integrate with BarProcessor Phase 3; quarantine dispatch consults `_is_quarantined` |
| **M5** (Multi-leg Orders) | Required — shim #7, #8 deletions; dashboard `parent_position_id` grouping via Order.legs |
| **M6** (Data Architecture) | Required — `ctx.market_indices` namespace uses M6 parquet pipeline; shim #9 deletion |
| **M7** (Strategy API) | Required — AllocationPolicy plugs into signal dispatch; TokenSignal unification; observability carry-overs |
| **M8** (Sizing) | Required — shim #5, #6 deletions via replay-parity gate; AC-S10 bridge; dashboard binding-constraint column |

---

## Time Estimate

**150-200 hours** (honest; matches prior-milestone overrun pattern — M5 scoped 100h shipped 110h, M6 scoped 120h shipped 135h. Prior 135-175h estimate defended per-line but had zero slack for three known-unknowns: correlation-matrix memoization bugs, s513 fallback parity surprises, replay-parity fixture engineering for 6-clamp window.)

- C-1 Conviction → Priority + `SignalArbitrationPolicy` (4 impls) + `min_slot_guarantee` + arbitration.jsonl + Phase 3.05 de-dup: ~14h
- C-2 Walk-forward + CPCV finalization (PBO + DSR): ~8h
- C-3 Pull-based memoized indicators (MTF-safe cache key): ~6h
- C-4 Regime removal + `v5/regimes.py` + `ctx.market_indices` (canonical keys): ~10h
- C-5 Risk components (8 components incl. **MaxCorrelatedExposure** with 168-bar log-return correlation + memoization surface): ~20-24h
- C-6 Dashboard rework (v5-only): ~6h
- C-7 AC-S10 bridge (precompute-at-on_start adapter + `to_token_bar_arrays` on 3 strategies + 0.5% tolerance debug): ~8-12h
- C-8 v5 paper runner deployment + feature flag + kill-switch drill: ~4h
- C-9 `CapitalAllocationPolicy` pluggable + hot-swap protection + `AllocationState.market_snapshot` + FIX 1098/1099 dual-stamp + pattern catalog: ~5h (FixedBudgetPolicy removed per user directive)
- C-10 scale_check_fn removal + full signature audit + TickCadencePolicy scaffold + arbitration analyzer CLI: ~8h (pulled back from M10)
- Shim deletions (12 rows): ~10h
- Strategy re-ports for M9 scope (s524m crisis exit + s523c CRISIS import + 3 strategies `to_token_bar_arrays` adapter + signature extensions): ~6-8h
- BarContext enrichment (`ctx` + `state_view` with `per_symbol_exposure` + `bars_since_last_fill`): ~3h
- FIX/Quant/Risk carry-over corrections (incl. MMR tier-blend `maintenance_amount`): ~6h
- Observability hardening (MappingProxyType, uid GC, BarProcessor quarantine wiring, exception_counter init): ~6h
- Replay-parity fixture construction (engineering 6-clamp window): ~6-10h
- Tests (65 new tests × ~20min avg + integration + correlation fixtures): ~18-22h
- Documentation + migration notes + pattern catalog: ~6h

**Note**: `SimulationState.trading_state` field addition is backwards-compatible via `dataclass default_factory`; blast radius limited to engine internals despite SimulationState's 31 importers (no API signature changes).

---

## Parity Gate (final sign-off criteria)

- ✅ All v5 tests pass (expect ~2000+ with M9 additions)
- ✅ AC-S10 6 xfails flip to PASS within 0.5% tolerance (s524m v5 matches v4)
- ✅ Replay-parity on 7-day parquet-windowed fixture (constructed to force each M8 clamp to bind at least once): flag-on vs flag-off byte-identical on non-binding bars for both `use_m8_clamps` and `use_multi_leg_orders`; every binding divergence traced to a matching `sizing_fills.jsonl` entry
- ✅ Zero hits for: `conviction_score`, `RegimeConfig`, `TokenBarArrays`, `PortfolioSignals`, `use_m8_clamps`, `use_multi_leg_orders`, `Position.leg` (as string field), `sizing_legacy`, `contract_type` (free-string), `_armed_tokens`, `armed_log.jsonl` (as dual-write target)
- ✅ v4 dashboard at `/` renders unchanged throughout M9 implementation (`git diff /srv/dashboard/current/index.html` shows ONLY the 8-line `view` detection addition; no UI change visible to v4 operator at `/`)
- ✅ v5 dashboard at `/v5` renders with `V5_PAPER_ENABLED=1` + v5 runner active
- ✅ Risk component ordering: throttled candidate reaches M8 with already-reduced size (aggregate → per-order pipeline verified)

---

## Sunset Notes

Once M9 ships:
- v5 is stand-alone. No v4-compat code in v5/.
- M5-M10 meta-rule sunsets at M10 completion; CLAUDE.md default (`code over specs`) resumes.
- v4 paper runner continues at `/` until operator-initiated retirement; no engineering dependency on it.
- M10+ can add FIX gateway, on-exchange execution, additional `AllocationPolicy` / `RiskComponent` implementations without re-opening M9 scope.
