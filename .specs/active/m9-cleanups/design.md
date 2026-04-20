# M9 — Design Document (Tier 3)

**Status**: Phase 2 (design, read-only exploration complete; per-bar reactivity + arbitration naming review folded in)
**Inputs**: brief.md v3 (approved 2026-04-20), FIX + Quant + Per-Bar-Reactivity reviewers, Signal-Arbitration review, allocation-review.md (M8 shipped), 3 parallel explorer subagents (C-7 bridge, C-4 regime blast radius, C-5 risk integration)

---

## 1. Architecture Overview

### 1.1 Data flow — post-M9 engine

```
              ┌──────────────────────────┐
              │   UniverseContext (M7)   │
              │   • per_token(t).ema(n)  │  ← ctx.data.indicators (M7 AC-S3)
              │   • market_indices[key]  │  ← NEW: BTC, TOTAL2, DXY (C-4)
              │   • seek_bar(bar_idx)    │
              └────────────┬─────────────┘
                           │
                  ┌────────▼────────┐
                  │ Strategy.generate(ctx, bar_idx)    │  ← per-bar callback (C-7)
                  │   returns UniverseSignals{         │
                  │     bar_idx,                       │
                  │     signals: dict[tok,TokenSignal] │
                  │   }                                │
                  │   • strategy internally calls      │
                  │     v5.regimes.detect_crisis(...)  │  ← no engine regime (C-4)
                  │     if it needs regime state       │
                  └────────┬────────┘
                           │
              ┌────────────▼────────────────┐
              │  BarProcessor dispatch      │
              │  Phase 1: Exits              │  ← stop/trail/exit_check_fn
              │  Phase 2: Margin calls       │  ← liquidations
              │  Phase 3.0: Risk Components  │  ← NEW (C-5): pre-allocation
              │      └─ aggregate state      │     • DrawdownThrottle
              │      └─ RiskDecision         │     • MaxGrossExposure
              │      └─ TradingState         │     • DailyLossLimit
              │      └─ throttle fraction    │     • MaxCorrelatedExposure
              │                               │     • PerSymbolStrategyLimit
              │  Phase 3.05: de-dup          │  ← NEW: max_positions_per_symbol dedup
              │                               │     BEFORE arbitration (saves scarce slots)
              │  Phase 3.1: SignalArbitration│  ← NEW (C-1): arbitrate contention
              │      └─ RandomShuffle DEFAULT │     • PriorityDesc (opt-in calibrated)
              │      └─ scope: portfolio|strat│     • TieredPriority (rank buckets)
              │      └─ min_slot_guarantee   │     • RoundRobin (weighted)
              │      └─ → arbitration.jsonl  │     → telemetry sink (forensics)
              │  Phase 3.2: per-order loop   │
              │      └─ Order.release_atomic │  ← M8 clamps (post-allocation)
              │          ├─ adv_cap          │
              │          ├─ concentration    │
              │          ├─ free_capital     │
              │          ├─ min_size         │
              │          ├─ liq_distance     │
              │          └─ slippage         │
              │  Phase 4: Equity snapshot    │
              └──────────────┬──────────────┘
                             │
                   ┌─────────▼────────────┐
                   │  SimulationState     │
                   │   • positions        │
                   │   • trading_state    │  ← NEW (C-5): ACTIVE/REDUCING/HALTED
                   │   • equity history   │
                   │   • sizing_fills.jsonl (M8)
                   └──────────────────────┘
```

### 1.2 PortfolioConfig surface — post-M9

```python
@dataclass
class PortfolioConfig:
    # Core (unchanged)
    strategies: list[StrategySpec]
    capital: float
    concentration_limit: float
    adv_cap_pct: float
    min_position_usd: float
    seed: int
    # ...

    # NEW — first-class pluggable portfolio primitives
    arbitration_policy: SignalArbitrationPolicy = field(default_factory=RandomShuffle)   # C-1 NEW
    capital_allocation_policy: CapitalAllocationPolicy = field(default_factory=SharedPoolPolicy)  # C-9 (M8 Protocol, M9 pluggable)
    validation_config: ValidationConfig = field(default_factory=ValidationConfig)         # C-2 NEW
    risk_components: list[RiskComponent] = field(default_factory=list)                    # C-5 NEW
    clamps_config: ClampsConfig = field(default_factory=ClampsConfig)                     # M8

    # DELETED in M9 (no longer exist on the dataclass):
    # - use_m8_clamps: bool                  (flag flipped + deleted via replay-parity gate)
    # - use_multi_leg_orders                 (StrategySpec field — flag flipped + deleted)
    # - regime_params / RegimeConfig refs   (regime fully out of engine per C-4)
```

**Two distinct Protocol families** (deliberately separate, not unified — allocation-review.md §Q9):
- `SignalArbitrationPolicy` (new in `v5/arbitration.py`) — **slot contention** ("who fills when candidates > slots")
- `CapitalAllocationPolicy` (M8 shipped in `v5/sizing/allocation.py`) — **dollar partitioning** ("how much $ each strategy gets")

These are orthogonal. A `$500M` shop in 2019 unified them into a single `UnifiedAllocator`; took 18 months to untangle when capital-rule changes kept breaking arbitration invariants. Do NOT unify.

---

## 2. Migration Strategy — Deletion Order Matters

Some shims depend on each other; deleting in the wrong order breaks tests mid-milestone. Order below is dependency-safe.

### Wave A: Pre-deletion upgrades (non-breaking)
Order | Item | Why first
---|---|---
A1 | C-3: timeframe added to IndicatorCache key | MTF-safe indicators land before regime consumers migrate (C-4 strategies call `ctx.per_token(...).ema(20)` internally)
A2 | C-4: `ctx.market_indices` namespace populated | Strategies need BTC/TOTAL2 access before engine regime computation is deleted
A3 | C-5: RiskComponent Protocol + TradingState + hook in BarProcessor Phase 3.0 | Must exist before any risk-scattered check is deleted
A4 | C-1: AllocationPolicy Protocol + 3 impls (RandomShuffle/PriorityDesc/TieredPriority) registered | Must exist before signal sort path is replaced

### Wave B: Engine decoupling (regime + conviction)
B1 | s524m_v5 + s523c_v5 re-ported to call `v5.regimes.*` internally | must land before Wave B2 or strategies break
B2 | Delete `RegimeConfig` from config.py; delete regime computation at engine.py:1719-1725 | engine stops computing regime
B3 | Delete regime columns from `TokenBarArrays` (regime, bear_target_mult, bear_max_hold) | signals stop carrying regime
B4 | Delete `TakeProfitHandler` regime branch; simplify to plain target_mult | exit handlers regime-free
B5 | Delete `conviction_score` + int32 `priority` shim from TokenBarArrays | conviction gone from signals
B6 | Delete conviction→priority auto-derivation at signals.py:164-173 | shim warning + derivation gone

### Wave C: AC-S10 capstone (s524m v5 parity)
C1 | Land C-7 simulator↔Strategy Protocol bridge (Python-per-bar callback) | unblocks real v5 metrics
C2 | 6 xfail tests flip to PASS within 0.5% tolerance on Q-DEC4 2025 206-token fold | gates C3
C3 | Investigate any AC-S10 gaps (edge cases, non-convergence) | may require bridge iteration

### Wave D: Flag-flip via replay-parity
D1 | Build `m9_replay_parity_7d` parquet-windowed fixture (forces each of 6 M8 clamps to bind once) | prerequisite for both flag flips
D2 | Run `use_m8_clamps` replay-parity gate; verify byte-identity on non-binding bars + binding-log coverage | validates M8 default-on
D3 | Flip `use_m8_clamps` default to True; delete flag + both branches + `sizing_legacy.py` + `globals()` alias + AC-Sz6 carve-out | clean cut #1
D4 | Run `use_multi_leg_orders` replay-parity gate on multi-leg fixture | validates M5 default-on
D5 | Flip `use_multi_leg_orders` default to True; delete flag + legacy `trigger_combined_entry` (~140 lines) | clean cut #2
D6 | Delete `Position.leg: str` field; migrate 8 simulator.py read sites to `leg_ref_id` | clean cut #3
D7 | Delete `Instrument.contract_type` free-string; all callers read `contract_subtype: Literal[...]` | clean cut #4
D8 | Delete `armed_log.jsonl` dual-write; `orders_log.jsonl` sole sink | clean cut #5
D9 | Delete `paper_engine._armed_tokens` alias + M4 T16b memory-note references | clean cut #6
D10 | Pick one of `Leg.market` vs `Leg.settlement_type`; drop the other | clean cut #7

### Wave E: Dashboard + runner deployment (C-6, C-8)
E1 | Add 8-line `view` detection to `/srv/dashboard/current/index.html` | dashboard dual-path
E2 | Create `/srv/dashboard/current/v5` symlink | URL routing
E3 | Write `tools/start_v5_paper.sh` + `stop_v5_paper.sh` with `V5_PAPER_ENABLED` flag-gate | runner isolation
E4 | Write `configs/runner_pool_config_v5.json` with ported strategies | v5 strategy pool
E5 | Rework `v5/dashboard_state.py`: remove regime panels, add parent_position_id grouping, add binding-constraint column (JOIN sizing_fills.jsonl), add scaling timeline | state schema
E6 | Add FIX-aligned counters (partial_fills, increase_fills, contingent_fills, entry_scale_downs) | report.py + SimulationState

### Wave F: Observability hardening + carry-overs
F1 | Wrap `ctx._lifecycle_config` in `MappingProxyType`; rename `_stopped` → `__stopped` | AC-S4 Quant round-8
F2 | Add finalizer to `WeakKeyDictionary` that removes uid from `_quarantined_uids` | M7 Quant round-7
F3 | Wire `_is_quarantined` into all 15 BarProcessor callback dispatch sites | M7 production integration
F4 | Convert XPASS markers to plain passes or `strict=True` | M7 Quant round-3/4/6
F5 | `BaseStrategy.exception_counter`: class-attr → `__init__` self-attr | M7 Quant round-1 NIT
F6 | Extend `DEFAULT_MMR_SCHEDULE` to (upper, mmr, maint_amount) 3-tuples; update `_liquidation_distance_clamp` with tier-blend formula | M8 Risk tier-blend
F7 | Tighten `test_cross_deep_drawdown_would_force_liquidation` via test-dispute resolution protocol | M8 Risk MINOR
F8 | Add `Leg.to_fix_leg_side()` / `Leg.to_fix_leg_ord_type()` methods | M7 FIX wire-encoder

---

## 3. Per-item Design Notes

### 3.1 C-1 — Conviction → Priority + AllocationPolicy

**Current state (from signals.py:84-85, 164-173 + simulator.py:1396-1398)**:
- `TokenBarArrays.conviction_score: Optional[np.ndarray]` (float32, range [0,1])
- `TokenBarArrays.priority: Optional[np.ndarray]` (int32)
- Shim in `TokenBarArrays.__post_init__`: if `priority is None and conviction_score is not None` → derive via `round(clip(x,0,1) × 1_000_000).astype(int32)`, emit one-per-strategy warning
- simulator.py ranks via `_sort_key(candidate) = (-int(sig.priority[lb]), sid, tok)` at line 1396-1398

**Target state**:
- `TokenSignal.priority: float | None` (M7-shipped, `v5/strategy_api.py:84`) is sole carrier
- `TokenBarArrays.conviction_score` + `priority` array fields **deleted**
- `signals.py:164-173` shim block **deleted**
- simulator.py:1396-1398 sort replaced by:
  ```python
  ordered = config.allocation_policy.rank(candidates, state)
  ```

**AllocationPolicy Protocol** (new file `v5/allocation_policy.py` — NOT `v5/sizing/allocation.py` which is M8's CapitalAllocationPolicy):
```python
@dataclass(frozen=True)
class EntryCandidate:
    strategy_id: str
    token: str
    signal: TokenSignal
    direction: int

@runtime_checkable
class AllocationPolicy(Protocol):
    """Reorder entry candidates by signal-ranking strategy. Engine-internal;
    NOT FIX-serializable. Priority has no FIX tag analog."""
    def rank(self, candidates: list[EntryCandidate],
             state: SimulationState) -> list[EntryCandidate]: ...

class RandomShuffle(AllocationPolicy):
    """Seeded via state.rng; default policy, matches v4 behavior."""
    def rank(self, candidates, state):
        out = list(candidates)
        state.rng.shuffle(out)  # deterministic via PortfolioConfig.seed
        return out

class PriorityDesc(AllocationPolicy):
    """Descending priority; ties broken by (strategy_id, token) lex."""
    def rank(self, candidates, state):
        def key(c): return (-(c.signal.priority or 0.0), c.strategy_id, c.token)
        return sorted(candidates, key=key)

class TieredPriority(AllocationPolicy):
    """Rank-space tiers; within-tier shuffled. tier_quantiles in (0,1)."""
    tier_quantiles: list[float] = field(default_factory=lambda: [0.33, 0.66])
    # ...partition by quantile-rank, shuffle within tier...
```

**Strategy re-port pattern** (for s523c_v5, s524m_v5):
```python
# OLD: per-bar array indexed by engine
signal.conviction_score = my_array  # triggers shim

# NEW: strategy pre-indexes in generate(ctx, bar_idx)
priority_scalar = float(my_conviction_array[bar_idx])
universe_signals.signals[token] = TokenSignal(
    token=token, direction=d, priority=priority_scalar, ...
)
```

**Files to change**:
- `v5/signals.py:84-85` — delete fields
- `v5/signals.py:156-158, 164-173` — delete shim block + warning helper
- `v5/simulator.py:1307-1399` — rank via `config.allocation_policy.rank(...)` call instead of `_sort_key`
- `v5/allocation_policy.py` — NEW file with Protocol + 3 impls
- `v5/config.py:198+` — add `allocation_policy: AllocationPolicy = field(default_factory=RandomShuffle)`
- `v5/strategies/s523c_v5.py`, `v5/strategies/s524m_v5.py`, `v5/strategies/s513_v5.py` — emit `TokenSignal.priority: float` directly

**Verification grep**: `grep -rn "conviction_score\|min_conviction_threshold" v5/ --exclude-dir=tests | wc -l == 0`

### 3.2 C-2 — Walk-Forward + CPCV Finalization

**Current state (from validation.py:69-99)**:
- `WalkForwardConfig(train_days=365, recalibrate_every=90, purge_days=7)` — uses **days** (wrong unit; engine operates on bars)
- `CPCVConfig(n_groups=6, n_test_groups=2, purge_pct=0.01, warmup_bars=200)` — uses fraction for purge (LdP uses bar counts)
- `ValidationConfig` wraps both + `pbo_threshold=0.40`

**Target state**:
```python
@dataclass(frozen=True)
class CPCVSpec:
    """Combinatorial Purged CV — LdP canonical form. Produces C(n_groups,
    n_test_groups) paths; PBO computed across all."""
    n_groups: int = 6
    n_test_groups: int = 2
    purge_bars: int = 100     # remove bars around each test fold
    embargo_bars: int = 50    # blackout period after each test fold
    warmup_bars: int = 200

@dataclass(frozen=True)
class WalkForwardSpec:
    """Rolling WF with explicit bar counts."""
    train_bars: int = 2000      # ~83 days of hourly bars
    recal_bars: int = 500        # ~21 days
    purge_bars: int = 100

@dataclass(frozen=True)
class ValidationConfig:
    wf: WalkForwardSpec = field(default_factory=WalkForwardSpec)
    cpcv: CPCVSpec | None = None   # None = rolling WF only; set = CPCV paths
    capital: float = 200_000
    pbo_threshold: float = 0.40
    deflated_sharpe: bool = True
    workers: int = 4

@dataclass
class WalkForwardResult:
    per_fold_metrics: list[PerformanceMetrics]      # one per fold
    per_path_metrics: list[PerformanceMetrics]      # CPCV-reconstructed paths
    pbo: float                                       # Bailey/LdP PBO
    deflated_sharpe: float                           # Bailey/LdP DSR
```

**Files to change**:
- `v5/validation.py:69-99` — replace dataclasses
- `v5/validation.py:840-984` — `WalkForwardRunner.run()` — return `WalkForwardResult` with path-level + fold-level metrics; PBO + DSR computed from path returns
- `v5/config.py` — add `validation_config: ValidationConfig` to PortfolioConfig

**AC test shape**:
```python
def test_cpcv_produces_C_n_k_paths():
    spec = CPCVSpec(n_groups=6, n_test_groups=2)
    result = run(config=ValidationConfig(cpcv=spec))
    assert len(result.per_path_metrics) == math.comb(6, 2)  # 15

def test_embargo_prevents_leakage():
    # Build fixture with known leakage signal in embargo window;
    # assert PBO accounts for it
```

### 3.3 C-3 — Pull-Based Memoized Indicators (MTF-safe)

**Current state (from indicators.py:1-60)**: M7 already shipped `IndicatorCache` with cache key `(token, fn.__qualname__, frozen_params_tuple, bar_idx)`. **Missing `timeframe`** → 1h EMA(20) and 1m EMA(20) at same `bar_idx` collide.

**Target state**:
```python
# New cache key:
CacheKey = tuple[
    str,          # token
    BarSpec,      # timeframe (e.g. BarSpec.from_minutes(60) or (1))
    str,          # indicator __qualname__
    tuple,        # frozen_params_tuple
    int,          # bar_idx
]
```

**Files to change**:
- `v5/indicators.py:60+` — `IndicatorCache.compute()` signature gains `timeframe: BarSpec`; cache key tuple extended
- `v5/universe_context.py` — `ctx.per_token(t).ema(n)` resolves `timeframe` from current BarProcessor clock
- `v5/tests/test_m9_indicators_mtf.py` — new test: 1h vs 1m at same bar_idx return different cached values

### 3.4 C-4 — Regime Fully Out of Engine

**(Compiled from explorer output — blast radius map)**

**Engine deletions (file:line)**:
- `v5/config.py:50-62` — delete `RegimeConfig` (no active consumers found)
- `v5/engine.py:781-829` — delete `detect_daily_regime()` (function body moves to `v5/regimes.py` as utility)
- `v5/engine.py:1719-1725` — delete regime computation in `_build_context()`
- `v5/engine.py:959-962` — delete `bear_target_mult` / `bear_max_hold` StrategyContext fields
- `v5/engine.py:70` — move `CRISIS=0, QUIET=1, UPTREND=2, RANGE=3, DOWNTREND=4` constants to `v5/regimes.py` for strategy import
- `v5/signals.py:73` — delete `TokenBarArrays.regime` field
- `v5/signals.py:96-98` — delete `bear_target_mult`, `bear_max_hold` fields
- `v5/signals.py:410, 424, 546-547, 657, 675-676` — delete `regime` extraction + passthrough
- `v5/simulator.py:1128` — delete `regime=int(sig.regime[local_bar])` from BarContext
- `v5/exit_handlers.py:54` — delete `BarContext.regime` field (or keep and let strategies populate if needed)
- `v5/exit_handlers.py:476-488` — delete `TakeProfitHandler` regime branch; simplify to plain target_mult (strategies use `exit_check_fn` for custom TP)

**Strategy re-ports**:

*s524m_v5.py:336-339* (crisis exit) — **re-port required**:
```python
# BEFORE (reads engine-populated BarContext.regime)
def check_exit(self, pos, bar_ctx):
    if bar_ctx.bars_held > 6 and bar_ctx.regime == CRISIS:
        return ExitCheck(reason="crisis")

# AFTER (strategy calls utility directly)
import v5.regimes as regimes
def check_exit(self, pos, bar_ctx):
    if bar_ctx.bars_held > 6 and regimes.detect_crisis(self._ctx, bar_ctx.bar_idx):
        return ExitCheck(reason="crisis")
```

*s523c_v5.py:37* — only references `CRISIS=0` constant; re-point import to `v5.regimes.CRISIS`

**`v5/regimes.py` utility surface**:
```python
# Constants
CRISIS = 0; QUIET = 1; UPTREND = 2; RANGE = 3; DOWNTREND = 4

# Main detectors (memoized on ctx_uid + bar_idx)
def detect_regime(ctx: UniverseContext, bar_idx: int) -> int: ...  # returns 0-4
def detect_crisis(ctx: UniverseContext, bar_idx: int) -> bool: ...
def detect_uptrend(ctx: UniverseContext, bar_idx: int) -> bool: ...
def detect_dispersion(ctx: UniverseContext, bar_idx: int) -> bool: ...

# Utility (moved from engine.py)
def detect_daily_regime(ind_d, adx_threshold=25, crisis_mult=2.0,
                        quiet_mult=0.7, ema_pair=(20,50),
                        min_periods=60) -> np.ndarray: ...
```

**`ctx.market_indices` namespace**:
Populated from existing external parquets (`regime_signals.parquet`, `total2_total3.parquet`). Not a new data pipeline — just a namespaced accessor.

**Canonical keys** (prevent strategy-level namespace drift):
```python
ctx.market_indices["BTC_CLOSE_1D"][bar_idx]    # daily BTC close
ctx.market_indices["BTC_CLOSE_1H"][bar_idx]    # hourly BTC close
ctx.market_indices["TOTAL2"][bar_idx]          # market-cap excl. BTC
ctx.market_indices["TOTAL3"][bar_idx]          # market-cap excl. BTC + ETH
ctx.market_indices["DXY"][bar_idx]             # US Dollar Index
ctx.market_indices["BTC_DOMINANCE"][bar_idx]   # BTC share (%)
ctx.market_indices["REGIME_FLAG_1D"][bar_idx]  # precomputed 0-4 if v5.regimes.detect_daily_regime
```
Strategies needing additional keys file a design change; prevents silent namespace pollution. Also available via `AllocationState.market_snapshot` (C-9) for CapitalAllocationPolicy.

**Verification grep** (post-M9, should return 0):
```bash
grep -rn "detect_daily_regime\|RegimeConfig\|regime_1h\|bear_target_mult\|bear_max_hold" \
    v5/ --include="*.py" --exclude-dir=tests --exclude="regimes.py"
```

### 3.5 C-5 — Risk Components + TradingState

**(Compiled from explorer output — integration points)**

**BarProcessor Phase sequence (current)**:
- Phase 1: `_process_exits()` at simulator.py:1006
- Phase 2: `_process_margin_calls()` at simulator.py:2067
- Phase 3: `_process_orders()` → `_stage2_process_new_signals()` at simulator.py:2127 / 1307
- Phase 4: `_record_equity_snapshot()` at simulator.py:2182

**New Phase 3.0 hook** — risk components fire on aggregate candidates BEFORE ranking:
```python
# v5/simulator.py, inserted at line ~1305 (before candidate sort)
if candidates and config.risk_components:
    decision = _apply_risk_components(candidates, state, config, clock_now_ns)
    # decision.throttle_fraction applied to each candidate.signal.sizing.fraction_of_equity
    # decision.trading_state flips SimulationState.trading_state
    # decision.rejected_ids removed from candidates
    candidates = _scale_candidates(candidates, decision)
```

**RiskComponent Protocol** (`v5/risk.py` NEW):
```python
class RiskDecision(Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"   # cites FIX OrdRejReason(103)
    REDUCE = "REDUCE"   # engine-internal; no FIX analog
    HALT = "HALT"       # cites FIX TradingSessionStatus(340)=3

@runtime_checkable
class RiskComponent(Protocol):
    sampling_cadence: Literal["bar_close", "tick", "release"]
    def check(self, candidates: list[EntryCandidate],
              state: SimulationState,
              clock_now_ns: int) -> RiskVerdict: ...

@dataclass
class RiskVerdict:
    decision: RiskDecision
    throttle_fraction: float = 1.0  # multiplies SizingRequest.fraction_of_equity
    rejected_ids: list[tuple[str, str]] = field(default_factory=list)  # (strategy_id, token)
    reason: str = ""

@dataclass
class TradingState:
    state: Literal["ACTIVE", "REDUCING", "HALTED"] = "ACTIVE"
    reason: str = ""
    throttle_fraction: float = 1.0
    last_transition_ts: int = 0

# SimulationState.trading_state: dict[str, TradingState]
#   "_global" key for portfolio-wide; strategy_id keys for scope="strategy"
```

**Components** (concrete classes):
- `DrawdownThrottle(thresholds: list[tuple[float, float]], scope: Literal["portfolio","strategy"])`
- `MaxGrossExposure(limit_pct: float, denominator: Literal["notional_usd","initial_margin","equity"])`
- `MaxNetExposure(limit_pct: float)`
- `MaxConcurrentOrders(limit: int)`
- `DailyLossLimit(max_daily_loss_usd: float, day_boundary: Literal["utc_midnight","session_open"])`
- `FundingSafetyCheck(max_funding_bps: float, action: Literal["warn","block"])`

**No-double-counting contract**:
- Risk components reduce `SizingRequest.fraction_of_equity` **before** Order.release_atomic clamps see it
- M8 clamps then apply orthogonal constraint classes (ADV / concentration / liq_distance)
- AC-Risk-4 test verifies: throttled fraction reaches clamp already-reduced; `binding_constraint="none"` is fine as long as clamp doesn't re-reduce below throttled level

**Scattered risk check inventory** (deleted in Wave B2):
- simulator.py:1715 per-bar funding buffer → `DailyLossLimit` + `FundingSafetyCheck`
- simulator.py:2195 margin deficiency warning → `DrawdownThrottle` at higher threshold
- config.py:107 `StrategySpec.weight` equity allocation → `MaxGrossExposure(scope="strategy")`

**TradingState state machine**:
- `ACTIVE` → `REDUCING` when `DrawdownThrottle` or `MaxGrossExposure` triggers
- `ACTIVE` → `HALTED` when `DailyLossLimit` triggers
- `REDUCING`: new entries blocked (reduce_only auto-injected); exits allowed; linked-leg REDUCES allowed, INCREASES blocked
- `HALTED`: in-flight `TRIGGERED` orders cancelled (new engine transition AC); all new entries blocked
- `REDUCING` → `ACTIVE` on equity recovery OR daily reset (`day_boundary`)
- `HALTED` → `ACTIVE` requires manual reset via `PortfolioConfig.allow_auto_resume: bool = False`

**Files to change**:
- `v5/risk.py` — NEW (Protocol + 8 components: DrawdownThrottle, MaxGrossExposure, MaxNetExposure, MaxConcurrentOrders, DailyLossLimit, FundingSafetyCheck, **MaxCorrelatedExposure**, **PerSymbolStrategyLimit** + TradingState dataclass)
- `v5/simulator.py:1305` — insert Phase 3.0 hook
- `v5/simulator.py:160-180` — add `trading_state` field to SimulationState (backwards-compat via default_factory)
- `v5/config.py` — add `risk_components` to PortfolioConfig
- `v5/tests/test_m9_risk_components.py` — ACs per brief (10 variants)

#### 3.5.1 `MaxCorrelatedExposure` Implementation Detail

**Correlation source**: 168-bar (7-day hourly) log-return window over `ctx.per_token(t).close_array[bar_idx-168:bar_idx]`. Log-returns used for stationarity (not price levels).

**Memoization**: cache key `(bar_idx, frozenset(active_token_set))`; correlation matrix recomputed ONLY when new bar arrives. Within-bar repeated calls return cached matrix. O(N²) recompute happens once per bar, not per candidate.

**Update cadence**: `sampling_cadence = "bar_close"` — matches M8 CapitalAllocationPolicy pattern; ensures backtest-paper parity (AC-Sz9-style).

**Scope**: portfolio-level. Operates on `state.current_positions_notional` + pending-candidate notionals. Returns `RiskVerdict` with throttled `fraction_of_equity` rather than hard-reject (prevents double-throttle with M8 concentration clamp).

**Fallback**: when correlation window has insufficient data (first 168 bars of backtest), component returns `ACCEPT` with `throttle_fraction=1.0` — no-op until warmup completes. AC test verifies warmup behavior.

**Cost profile** (200 tokens): correlation matrix O(200² × 168) ≈ 6.7M float ops per bar. At ~100M float ops/sec NumPy baseline: ~70ms per bar. Over 2500 bars per fold: ~3min per fold. Acceptable for walk-forward; profiled and noted.

### 3.6 C-6 — Dashboard Rework (v5-only, state_v5.json)

**Current state (`v5/dashboard_state.py` mirrored from v4)**:
- Writes to `/srv/data/state.json` (target of same v4 HTML) — will be changed to `state_v5.json`
- Has `consolidate_partial_trades` band-aid (lines 20-100) — deleted, replaced by parent_position_id grouping
- Has regime panels (TBD via grep) — deleted

**Target (state_v5.json schema additions)**:
```json
{
  "trading_state": "ACTIVE",     // C-5: ACTIVE/REDUCING/HALTED
  "risk_verdicts": [              // C-5: active risk component states
    {"name": "DrawdownThrottle", "decision": "ACCEPT", "throttle_fraction": 1.0}
  ],
  "counters": {                   // C-6 FIX-aligned
    "partial_fills": 12,           // FIX OrdStatus(39)=1
    "increase_fills": 3,
    "contingent_fills": 5,         // FIX ContingencyType(1385)≠0
    "entry_scale_downs": 7          // engine-internal
  },
  "positions": [
    {
      "position_id": "...",
      "parent_position_id": "...", // C-6 grouping key
      "scaling_events": [...],     // M2 ScalingEvent timeline
      "scale_count": 2,
      "leaves_qty": 0.5,
      "has_scaling": true,
      "r_anchor_price": 45_000.0
    }
  ],
  "trades": [
    {
      "trade_id": "...",
      "order_id": "...",              // C-6 JOIN key
      "binding_constraint": "adv_cap"  // C-6: from sizing_fills.jsonl JOIN
    }
  ]
}
```

**Files to change**:
- `v5/dashboard_state.py` — delete regime/consolidate_partial_trades code; add parent_position_id grouping, scaling timeline, binding_constraint JOIN
- `v5/report.py` — add 4 counters to portfolio-metric output
- `/srv/dashboard/current/index.html` — 8-line `view` detection + `state_v5.json` fetch path (C-8 deploy item)

### 3.7 C-7 — AC-S10 Simulator ↔ Strategy Protocol Bridge (CAPSTONE)

**(Compiled from C-7 explorer output)**

**Current state**:
- `simulate_portfolio()` consumes `all_signals: dict[str, dict[str, TokenBarArrays]]` — precomputed array shape (simulator.py:2207)
- Bar loop at simulator.py:2232: `for global_bar in range(n_bars): _process_exits/...()`
- Exit handlers read `BarContext` fields populated from `sig.*[local_bar]` arrays (exit_handlers.py:54)
- `WalkForwardRunner.run()` at validation.py:974-984 returns `_stub_metrics()` all-zero
- 6 `strict=True` xfail tests in `test_m8_ac_s10_s524m_parity.py` expecting real metrics

**Target — Three-layer bridge: opt-in vectorized + engine fallback + parity test** (Zipline/Pipeline institutional pattern — industry-reviewed):

```python
# Layer 1: Sub-Protocol for strategies that opt into vectorized fast path
@runtime_checkable
class VectorizedStrategy(Strategy, Protocol):
    """Strategies that can produce signal arrays in one call — opt-in fast path.
    Strategies without this method get the engine fallback (Layer 2)."""
    def to_token_bar_arrays(self, ctx: UniverseContext) -> dict[str, TokenBarArrays]:
        ...

# Layer 2: Engine fallback — calls generate() in setup loop (setup Python, inner loop vectorized)
def _engine_precompute_fallback(strategy, n_bars, ctx):
    """Used when strategy does NOT implement VectorizedStrategy. Python setup loop
    materializes arrays from the per-bar generate() API. NOT a production fast path
    for 100+ strategies or 1M+ bars — use VectorizedStrategy for scale."""
    per_token_arrays = {}
    for bar_idx in range(n_bars):
        ctx.seek_bar(bar_idx)
        univ_sigs = strategy.generate(ctx, bar_idx)
        for tok, sig in univ_sigs.signals.items():
            arr = per_token_arrays.setdefault(tok, _empty_arrays(n_bars))
            arr.direction[bar_idx] = sig.direction
            arr.priority[bar_idx] = sig.priority or 0.0
            arr.stop_mult[bar_idx] = sig.stop_mult
            # ... flatten TokenSignal fields
    return per_token_arrays

def simulate_portfolio(
    *,
    strategies: dict[str, Strategy] | None = None,      # NEW (M9)
    all_signals: dict | None = None,                    # LEGACY (kept during Wave B, deleted Wave F)
    config: PortfolioConfig,
    ctx_provider: Callable[[], UniverseContext],
) -> SimulationState:
    state = SimulationState(initial_capital=config.capital)
    
    if strategies:
        ctx = ctx_provider()
        all_signals = {}
        for sid, strategy in strategies.items():
            strategy.on_start(portfolio_config=config, ctx=ctx)
            # Dispatch: opt-in vectorized OR engine fallback
            if isinstance(strategy, VectorizedStrategy):
                all_signals[sid] = strategy.to_token_bar_arrays(ctx)
            else:
                all_signals[sid] = _engine_precompute_fallback(strategy, n_bars, ctx)
    
    # EXISTING VECTORIZED SIMULATOR LOOP — UNCHANGED
    # Inner loop consumes all_signals exactly as today (numpy-vectorized inside
    # _process_exits / _process_margin_calls / _process_orders).
    n_bars = compute_bar_count(all_signals)
    for global_bar in range(n_bars):
        _process_exits(state, all_signals, global_bar, config, ...)
        _process_margin_calls(state, all_signals, global_bar, config)
        _process_orders(state, all_signals, global_bar, config, ...)
        _record_equity_snapshot(state, all_signals, global_bar, ...)
        # Per-bar reactivity: the 4 hooks (filter_entry_fn / check_exit / check_scale /
        # built-in stops) fire inside _process_orders / _process_exits as today.
    
    if strategies:
        for strategy in strategies.values():
            strategy.on_stop(reason="backtest_complete")
    
    return state
```

**Why this shape (institutional review verdict)**:
- **Strategies that opt into `VectorizedStrategy`** get the Zipline/Pipeline-style fast path. No Python loop overhead in setup. Scales to 100+ strategies × 5y × 500 tokens.
- **Strategies that don't opt in** fall back to engine-side precompute. Correctness-preserving. Acceptable for 3-10 strategies × 2500 bars × 200 tokens (our current scale).
- **Mandatory parity test** (AC): when a strategy implements BOTH `generate()` and `to_token_bar_arrays()`, assert byte-identity between the fallback output (engine loop over `generate()`) and the vectorized output (`to_token_bar_arrays()`). This is the #1 institutional discipline for preventing research-vs-production silent drift.
- **Paper trading unaffected**: BarProcessor (M4) dispatches per-tick via `generate()` always. `to_token_bar_arrays()` is a backtest-only fast path; paper never calls it (no future data available).

**Live-state reactivity** via the 4 per-bar hooks (`entry_filter_fn`, `check_exit`, `check_scale`, built-in stop/trail updates). These run per-bar with access to the enriched `BarContext` (`ctx` + `state_view` per C-1). Strategies split their logic:
- "What do I think about the future given history?" → `generate()` (M7 Protocol, always) + optional `to_token_bar_arrays()` fast path → vectorizable
- "What do I want to do given live portfolio state?" → 4 per-bar hooks → per-bar

**Strategies needing mid-simulation state in signal generation**: put the state-dependent veto in `entry_filter_fn` (returns 0.0 to block). This is the Nautilus/LEAN pattern.

**Legacy path survives M9**: the precomputed `all_signals` path stays for Wave B regime/conviction deletion tests. Deleted in Wave F post-paper validation.

**Strategy-level M9 decisions**:
- **s524m_v5**: IMPLEMENT `VectorizedStrategy` — it already precomputes arrays in `_evaluate_token`; thin adapter (~20 lines) exposes them.
- **s523c_v5**: IMPLEMENT `VectorizedStrategy` — similar precompute pattern.
- **s513_v5**: DOES NOT implement `VectorizedStrategy` — its trigger logic is bar-reactive, not precomputable. Gets engine fallback. Slower per-bar but correct.

**Parity test AC** (load-bearing for top-firm discipline): for s524m and s523c, run both paths on Q-DEC4 2025 fold, assert `TokenBarArrays` byte-identity field-by-field. Any drift is a BLOCKER — prevents silent research-vs-paper divergence.

**Risks** (revised):
1. Schema drift: strategy changes `generate()` but not `to_token_bar_arrays()` (or vice versa). **Mitigation**: mandatory parity test (institutional #1 discipline).
2. `TokenBarArrays` field coverage: strategy adapter / fallback must populate all fields the exit handlers expect (regime deleted in C-4, so fewer fields than today).
3. Equity-curve divergence beyond 0.5%: run both paths on fixture, correlate >0.99. If >0.5%, investigate indicator cache coherency.
4. Engine fallback performance ceiling: at >100 strategies or >1m-bar × 500-token fixtures, Python setup loop becomes minutes. **Known and acceptable** — all 3 shipped strategies are well within this envelope.
5. `generate()` in fallback loop mustn't mutate `self` state — if it does, backtest drifts from paper. **Mitigation**: AC audit for stateful-self-mutation in `generate()`.

**Files to change**:
- `v5/simulator.py:2207` — `simulate_portfolio(strategies=dict, ctx_provider=callable)` signature extension
- `v5/simulator.py:+2500-2550` — NEW `_precompute_strategy_signals(strategies, config, ctx)` helper
- `v5/strategy_api.py` — add optional `Strategy.to_token_bar_arrays(ctx) → dict[str, TokenBarArrays]` to Protocol surface. Default impl (via base class) calls `self._evaluate_token(ctx, tok)` per token, assembles arrays.
- `v5/validation.py:1021` — wire bridge into `_m8_run()`; return real `compute_portfolio_metrics()` instead of `_stub_metrics()`
- `v5/strategies/s524m_v5.py`, `v5/strategies/s523c_v5.py`, `v5/strategies/s513_v5.py` — implement `to_token_bar_arrays()` (mostly thin wrappers around existing precompute)
- `v5/tests/test_m9_c7_bridge_interface.py` — NEW unit tests (adapter shape coverage, all 4 per-bar hooks reach enriched BarContext, paper-vs-backtest bit-identity)
- `v5/tests/test_m8_ac_s10_s524m_parity.py` — xfails auto-flip on bridge activation

**Numba verdict** (from second quant review):
- Explicitly ruled out for M9 and likely forever in the Python-Protocol engine. Real speedup math: ~2% of runtime saved (125k clamp evals × ~10μs = 1.25s on a 30s backtest) for ~2 weeks of refactor work + loss of per-candidate traceability + determinism risk from numba version drift.
- **Where numba would pay**: 10^4+ symbols or full WF grid search as bottleneck. Neither applies.
- **Better parallelism path**: parallel-process CPCV folds (not numba inner loops).

### 3.8 C-8 — v5 Paper Runner Deployment (feature-flagged)

**Already-present scaffolding**:
- `v5/run_paper_multi.py` — exists (mirrored from v4)
- `v5/paper_engine.py` — exists
- `v5/dashboard_state.py` — exists (has v4 drift; will be reworked in C-6)

**New artifacts**:

1. `tools/start_v5_paper.sh` (mirrors `start_all_services.sh` section 1 only):
   ```bash
   #!/bin/bash
   cd /workspace/crypto_backtest
   if [ "${V5_PAPER_ENABLED:-0}" != "1" ]; then
     echo "V5 paper disabled (set V5_PAPER_ENABLED=1)"; exit 0
   fi
   if [ -f /tmp/paper_runner_v5.pid ] && kill -0 $(cat /tmp/paper_runner_v5.pid) 2>/dev/null; then
     echo "V5 paper runner already running: PID $(cat /tmp/paper_runner_v5.pid)"; exit 0
   fi
   nohup /workspace/venv/bin/python -u -m v5.run_paper_multi \
       --config configs/runner_pool_config_v5.json \
       --state-path /srv/data/state_v5.json \
       --pid-file /tmp/paper_runner_v5.pid \
       > /tmp/paper_runner_v5.log 2>&1 &
   echo $! > /tmp/paper_runner_v5.pid
   echo "V5 paper runner started: PID $(cat /tmp/paper_runner_v5.pid)"
   ```

2. `tools/stop_v5_paper.sh` — mirror pattern; kill PID at `/tmp/paper_runner_v5.pid` only, leave v4 untouched

3. `configs/runner_pool_config_v5.json`:
   ```json
   {
     "total_capital": 450000,
     "strategies": [
       {"strategy_id": "s513_v5", "weight": 0.333, "market": "combined"},
       {"strategy_id": "s523c_v5", "weight": 0.333, "market": "combined"},
       {"strategy_id": "s524m_v5", "weight": 0.333, "market": "combined"}
     ],
     "use_m8_clamps": true,
     "allocation_policy": {"class": "RandomShuffle"},
     "risk_components": [{"class": "DrawdownThrottle", "thresholds": [[0.15, 0.5], [0.25, 0.0]]}]
   }
   ```

4. `v5/run_paper_multi.py` — extend CLI to accept `--state-path`, `--pid-file` (override defaults)

5. `/srv/dashboard/current/index.html` — insert 8-line `view` detection at top of `<script>` block:
   ```js
   const view = location.pathname.startsWith('/v5') ? 'v5' : 'v4';
   const STATE_URL = view === 'v5' ? '/data/state_v5.json' : '/data/state.json';
   // (replace hardcoded '/data/state.json' fetches with STATE_URL)
   ```

6. `/srv/dashboard/current/v5` → symlink to `.` (same HTML file, different URL path)

**Kill-switch drill AC** (AC #11 from brief):
- Stop v5 runner → `state_v5.json` becomes stale (>30s) → `/v5` shows OFFLINE banner
- v4 `/srv/data/state.json` continues to tick from live v4 runner → `/` shows ACTIVE
- Full delete: remove `tools/start_v5_paper.sh`, remove 8-line `view` detection, remove symlink → v4 path git-diff empty

---

## 4. Test Strategy

### 4.1 Per-wave test plan

| Wave | Test files | Count |
|---|---|---|
| A1 C-3 MTF indicator | `test_m9_indicators_mtf.py` | ~5 |
| A2 market_indices | `test_m9_ctx_market_indices.py` | ~3 |
| A3 C-5 risk components | `test_m9_risk_components.py` | ~10 (6 ACs × variations) |
| A4 C-1 AllocationPolicy | `test_m9_allocation_policy.py` | ~8 |
| B C-1/C-4 deletion parity | `test_m9_conviction_deletion.py`, `test_m9_regime_deletion.py` | ~6 each |
| C C-7 AC-S10 | `test_m8_ac_s10_s524m_parity.py` (xflips), `test_m9_c7_bridge.py` | 6 xflips + 5 new |
| D replay-parity | `test_m9_replay_parity_m8_clamps.py`, `test_m9_replay_parity_m5_multileg.py` | ~4 |
| E C-6/C-8 deployment | `test_m9_dashboard_v5_rendering.py`, `test_m9_feature_flag_kill.py` | ~4 |
| F observability | `test_m9_ctx_mapping_proxy.py`, `test_m9_quarantine_uid_gc.py`, etc. | ~5 |
| **Total** | | **~65 new tests** |

### 4.2 Replay-parity fixture (critical)

Build `v5/tests/fixtures/m9_replay_parity_7d/` via **engineered window selection**:

```python
# v5/tests/fixtures/generate_m9_replay_parity_7d.py
def build_fixture():
    # Pick 7-day windows from parquet cache that force each clamp:
    windows = [
        {"clamp": "adv_cap", "token": "<low-liquidity-token>",
         "start": "2025-09-01", "end": "2025-09-08"},
        {"clamp": "concentration", "strategies": ["s513","s523c","s524m"],
         "window": "same-day-signal-overlap"},
        {"clamp": "free_capital", "start": "2026-02-15",  # deep-drawdown period
         "end": "2026-02-22"},
        {"clamp": "min_size", "equity_scale": 0.01},  # tiny equity forces min_size
        {"clamp": "liq_distance", "start": "2025-10-15",  # -15% intraday day
         "end": "2025-10-18"},
        {"clamp": "slippage", "order_size_mult": 100},  # oversize
    ]
    # Concatenate into one 7-day fixture, persist manifest + parquet slices
```

**Non-binding bars** (no clamp fired): flag-on and flag-off archives must be **byte-identical**. **Binding bars**: every divergence row in archive-B has a matching `sizing_fills.jsonl` entry.

### 4.3 Test Freeze / Dispute tolerance

- Phase 3 tests written by isolated subagent per workflow rules
- During Phase 4, any test that fails due to suspected test bug → test-dispute resolution protocol (reviewer subagent verdict)
- Applies especially to AC-S10 tolerance (0.5%) — if it's too tight or too loose, dispute path resolves

---

## 5. Rollback Plan

### 5.1 Per-wave rollback

- **Wave A (upgrades)**: non-breaking additions; no rollback needed (just revert the commit if needed)
- **Wave B (deletion)**: **NOT ROLLBACKABLE without restoring shims** — per M9 meta-rule, this is intentional. Rollback requires git-revert of the full wave + re-adding shims.
- **Wave C (C-7 bridge)**: legacy precomputed-array path kept until post-M9 → partial rollback possible (set `strategies=None`, revert to `all_signals=...`)
- **Wave D (flag flips)**: **Rollback = flip flags back to False**. Legacy branches still exist until the flag-delete step. If replay-parity fails, flip back and investigate.
- **Wave E (deployment)**: rollback = stop v5 runner + remove 8-line JS detection + remove symlink. v4 path unaffected.
- **Wave F (observability)**: MappingProxyType wrap is rollbackable via git-revert with no behavior change.

### 5.2 Emergency brake

If AC-S10 reveals structural divergence between v5 and v4 (>5% metric drift unfixable):
1. STOP — do NOT flip flags
2. Keep `use_m8_clamps` flag alive; keep legacy precomputed path alive
3. Produce divergence report; classify as (a) v5 bug, (b) v4 baseline drift, (c) meta-rule design-cut accepted drift
4. If (c): user explicitly sign off on the drift; document in `v5/docs/v5_vs_v4_intended_drift.md`; proceed with other M9 waves; AC-S10 tolerance adjusted upward (e.g., 5%)

---

## 6. Performance Considerations

- **C-7 bridge Python-per-bar**: ~515k dict allocations (206 tok × 2500 bars). Est. runtime ~15-30s per fold at Python overhead. Acceptable for walk-forward; unacceptable for live paper.
  - Paper uses M4 BarProcessor path already (not `simulate_portfolio`) — bridge only affects backtest path
- **C-3 IndicatorCache MTF key**: adding `BarSpec` to tuple is O(1) tuple construction; ~1% overhead
- **C-5 Phase 3.0 risk check**: ~6 components × ~50 candidates = 300 Python-level checks per bar. Bounded. Compile-time opportunity deferred to M10.
- **Replay-parity fixture build**: one-time 10-15 min parquet scan; persisted; never re-runs except on fixture regen

---

## 7. Files Summary (estimated)

| Category | Files to create | Files to modify | Files to delete |
|---|---|---|---|
| New modules | `v5/allocation_policy.py`, `v5/risk.py` | — | — |
| Config | — | `v5/config.py` (+allocation_policy, +risk_components, +validation_config; −RegimeConfig, −use_m8_clamps) | — |
| Engine | — | `v5/simulator.py`, `v5/bar_processor.py`, `v5/engine.py` | — |
| Signals | — | `v5/signals.py` (−conviction_score, −priority int32, −regime fields, −shim block) | — |
| Strategies | — | `v5/strategies/s523c_v5.py`, `v5/strategies/s524m_v5.py`, `v5/strategies/s513_v5.py` | — |
| Dashboard | — | `v5/dashboard_state.py`, `v5/report.py`, `/srv/dashboard/current/index.html` | — |
| Runner | — | `v5/run_paper_multi.py` | — |
| Tools | `tools/start_v5_paper.sh`, `tools/stop_v5_paper.sh` | — | — |
| Configs | `configs/runner_pool_config_v5.json` | — | — |
| Validation | — | `v5/validation.py` | — |
| Indicators | — | `v5/indicators.py` (+timeframe in key) | — |
| Rollback shims | — | — | `v5/sizing_legacy.py`, flag branches in simulator/paper_engine, `trigger_combined_entry` legacy branch |
| Regime | — | `v5/regimes.py` (move `detect_daily_regime` in + add new `detect_crisis/uptrend/dispersion` wrappers) | — |
| Tests | ~12 new `test_m9_*.py` files | `test_m8_ac_s10_s524m_parity.py` (xfails auto-flip) | — |

---

## 8. Open Questions (for Phase 3 test-author subagent)

1. Should `AllocationPolicy.rank()` mutate candidates or return new list? → **Return new list** (immutability / functional style) — committed
2. Should `TradingState.HALTED` auto-recover at UTC midnight or require manual reset? → **Config flag `allow_auto_resume: bool = False`** — committed
3. C-7 multi-strategy support in M9 or deferred? → **Single-strategy for AC-S10; multi-strategy AC as secondary** — committed
4. Replay-parity tolerance for non-binding bars — byte-identical OR within 1e-9? → **Byte-identical via JSON-canonicalization** — committed
5. Should we keep `detect_daily_regime()` or rewrite as `detect_regime()` with different signature? → **Keep existing signature; re-export from `v5/regimes.py`** — committed
6. `DEFAULT_MMR_SCHEDULE_WITH_AMOUNTS` source of truth — Binance docs snapshot date? → Flag as design question for reviewer round

---

## 9. C-9 — CapitalAllocationPolicy Pluggable (NEW section)

**Context**: M8 shipped `CapitalAllocationPolicy` Protocol + `SharedPoolPolicy` at `v5/sizing/allocation.py:35-92`. The Protocol instance is **hardcoded** inside `Order.release_atomic` free-capital clamp. M9 makes it pluggable.

**User directive**: FixedBudgetPolicy is NOT shipped in M9. Only `SharedPoolPolicy` (M8 default) is shipped. Users write their own `CapitalAllocationPolicy` subclasses via the pattern catalog in §10.1.

### 9.1 `AllocationState` extension

```python
class AllocationState(TypedDict):
    available_margin: float
    per_strategy_equity: Dict[str, float]
    rolling_pnl_24h: Dict[str, float]
    current_positions_notional: Dict[str, float]
    market_snapshot: Dict[str, float]  # NEW in M9: BTC_close, TOTAL2, TOTAL3, regime_flag, etc.
```

`market_snapshot` populated at bar-close by the engine, keyed by canonical symbols. Enables user-written dynamic allocators to read market-wide state without needing direct `UniverseContext` access. Preserves the "narrow read surface" discipline.

### 9.2 Files to change

- `v5/sizing/allocation.py` — extend `AllocationState` TypedDict with `market_snapshot: dict[str, float]`
- `v5/config.py` — add `PortfolioConfig.capital_allocation_policy: CapitalAllocationPolicy = field(default_factory=SharedPoolPolicy)` + hot-swap protection via `__setattr__` guard raising `ConfigError` if book non-empty
- `v5/simulator.py` — free-capital clamp callsite reads `config.capital_allocation_policy` instead of hardcoded instance; populate `market_snapshot` at bar-close
- `v5/sizing/binding_log.py` — schema: add `fix_1098: str` + `fix_1099: str | None = None` fields alongside `strategy_id` (FIX StrategyID dual-stamp)
- `v5/tests/test_m9_capital_allocation_pluggable.py` — NEW: pluggability, hot-swap protection, market_snapshot population

---

## 10. Pattern Catalog (Appendix)

### 10.1 `CapitalAllocationPolicy` patterns

**Shipped**:
- `SharedPoolPolicy` — identity, matches v4. Default (shipped in M8).

**Not shipped per user directive** (FixedBudgetPolicy removed from M9 scope). Users who want fixed per-strategy caps write their own `CapitalAllocationPolicy` subclass following the pattern below.

**User-code example: DynamicRegimeAllocator** (not engine code; docs-only template):
```python
class DynamicRegimeAllocator:
    sampling_cadence = "bar_close"
    def __init__(self, bull_weights: dict, bear_weights: dict, regime_key: str = "regime_flag"):
        self.bull_weights = bull_weights
        self.bear_weights = bear_weights
        self.regime_key = regime_key
    def available_capital(self, sid, state, clock_now_ns):
        is_bull = state["market_snapshot"].get(self.regime_key, 0) >= 2  # UPTREND
        weights = self.bull_weights if is_bull else self.bear_weights
        return state["available_margin"] * weights.get(sid, 0.0)
```

**User-code example: EqualRiskContribution**:
```python
class EqualRiskContribution:
    sampling_cadence = "bar_close"
    def __init__(self, target_vol_pct: float = 0.10):
        self.target_vol_pct = target_vol_pct
    def available_capital(self, sid, state, clock_now_ns):
        # inverse-vol weighting across strategies using rolling_pnl_24h as proxy
        vols = {s: max(abs(state["rolling_pnl_24h"].get(s, 0)) / e, 1e-6)
                for s, e in state["per_strategy_equity"].items()}
        total_inv = sum(1/v for v in vols.values())
        weight_sid = (1 / vols[sid]) / total_inv
        return state["available_margin"] * weight_sid
```

### 10.2 Reactive-sizing patterns (4 worked examples)

**Scale-up on favorable 1R move** — `Strategy.check_scale(pos, bar_ctx)` (ctx accessed via `bar_ctx.ctx`):
```python
def check_scale(self, pos, bar_ctx):
    r = (bar_ctx.close - pos.entry_price) / (pos.entry_price - pos.stop_price) * pos.direction
    if r >= 1.0 and not pos.has_scaled:
        return ScaleAction(qty_delta=+0.5 * pos.quantity, reason="1R_continuation")
    return None
```

**Scale-down on ATR volatility spike** — uses `bar_ctx.ctx` (M9 BarContext enrichment):
```python
def check_scale(self, pos, bar_ctx):
    atr_now = bar_ctx.atr
    atr_20 = bar_ctx.ctx.per_token(pos.token).atr(20)
    if atr_now > atr_20 * 2.0:
        return ScaleAction(qty_delta=-0.3 * pos.quantity, reason="vol_spike")
    return None
```

**Kelly-on-drawdown reduction** — uses `bar_ctx.state_view`:
```python
def check_scale(self, pos, bar_ctx):
    if bar_ctx.state_view.portfolio_dd_pct > 0.05:
        return ScaleAction(qty_delta=-0.5 * pos.quantity, reason="dd_kelly_reduce")
    return None
```

**Pyramid vs layered entry** — divergent patterns:
- **Pyramiding** = scale-up existing position: `max_positions_per_symbol=1`, logic in `check_scale`. ONE position with increasing qty.
- **Layered entries** = independent positions: `max_positions_per_symbol>1`, logic in `generate()` emitting multiple signals over time. N positions, each with own stop/R-anchor.
- DO NOT mix these patterns — they have different position lifecycle semantics.

---

## 11. Architectural Limitations — Known Trade-offs (Post-M10 Reference)

This section documents architectural decisions that were deliberately not made, so future maintainers understand the trade-offs in place. No pending work — the v5 rebuild completes at M10 and reverts to CLAUDE.md `code over specs` mode thereafter.

### 11.1 Single-engine (vs two-engine institutional pattern)

**What top quant firms do (Citadel, Two Sigma, Millennium — public talks, ex-employee write-ups)**: split research (vectorized pandas / kdb+ / Slang) from production (C++ event-driven). Two separate engines share a signal-schema contract (token, timestamp, score, horizon). Parity enforced by **replay tests**, not shared code.

**What we do**: single Python engine serving both backtest (vectorized precompute path) and paper/live (M4 BarProcessor per-tick path). The Strategy Protocol spans both modes. Parity enforced internally via (a) M8 `sampling_cadence` discipline, (b) M9 `VectorizedStrategy` parity test, (c) AC-Sz9 paper-vs-backtest byte-identity tests.

**Trade-off accepted**: single-engine reduces maintenance cost and keeps strategy code in one place, at the cost of performance ceiling. At current scale (3 strategies × 5y × 200 tokens) the ceiling is comfortable. **Cliff**: 100+ strategies or 1m-bar × 500-token × 5y fixtures — engine fallback's Python setup loop becomes minutes-to-hours. If scale grows past that, the engine will need to be re-architected (split into two, or JIT via numba/cython). That is a new rebuild project, not an M9-M10 concern.

**Signals that a rewrite is needed** (for future reference):
- Strategy count > 20 with full WF grid search taking > 1h
- Symbol universe > 500 at 1m resolution
- Per-bar Python overhead visibly dominates in profiles
- Silent parity divergence between vectorized and fallback paths despite green parity test (indicates Protocol surface is no longer sufficient as a contract)

**Signals that this architecture is still fine**:
- Research iteration time dominated by data loading / exploratory analysis, not sim runtime
- Fewer than 20 concurrent strategies
- No complaints about backtest latency from users

At M10 ship, we're well inside the "still fine" envelope. No action needed.

### 11.2 Pattern catalog — what we chose and why

| Pattern | Industry examples | Our choice | Why |
|---|---|---|---|
| **Per-bar callback** (Zipline `handle_data`, LEAN `OnData`, Nautilus `on_bar`) | paper/live mode via M4 BarProcessor | Required for paper (live ticks, no future data) |
| **Vectorized precompute** (vectorbt, Moonshot) | optional fast-path via `VectorizedStrategy` | Scales to research-heavy backtests without loss of paper/live correctness |
| **Declarative DAG** (Zipline `Pipeline`, LEAN Alpha Model) | not adopted | Requires strategies to express logic as DAG nodes — unwieldy at our scale; revisit if strategy count >20 |
| **JIT numba/cython inner loop** | not adopted | Incompatible with Protocol dispatch + dataclass state; 2% speedup for 2 weeks work + determinism risk |
| **Two engines with signal contract** (Citadel, Two Sigma) | documented as limitation (§11.1) | Single-engine trade-off explicitly accepted |

---

## 12. Design Review Checklist

- [x] Architecture diagram text-based included
- [x] Per-C-item design notes with file:line refs (C-1 through C-9)
- [x] Shim-deletion order (Wave A-F) explicit
- [x] AC-S10 bridge approach committed: **opt-in `VectorizedStrategy` (Zipline/Pipeline pattern) + engine fallback for non-opt-in + mandatory parity test between them** (institutional #1 discipline — corrected from per-strategy-required adapter and from Python-per-bar-callback)
- [x] Single-engine trade-off explicitly acknowledged as §11.1 architectural limitation (top firms run 2 engines; we're single-engine by design for M9-M10 scope; cliff at 20+ strategies or 500+ tokens flagged for future reference)
- [x] Replay-parity fixture design committed (parquet-windowed)
- [x] Risk-component ordering contract (no double-counting) — 8 components shipped (6 original + MaxCorrelatedExposure + PerSymbolStrategyLimit)
- [x] Regime blast-radius map (22 sites)
- [x] Dashboard dual-path (v4 `/` + v5 `/v5`) committed
- [x] Rollback plan per wave
- [x] Performance considerations (numba explicitly ruled out with reasoning)
- [x] Open questions surfaced
- [x] All 18 reviewer findings + 10 per-bar-reactivity edits + 10 signal-arbitration edits folded in
- [x] C-9 CapitalAllocationPolicy pluggable + AllocationState.market_snapshot (FixedBudgetPolicy NOT shipped per user directive)
- [x] Pattern catalog appendix (4 capital-policy patterns + 4 reactive-sizing patterns)
- [x] PortfolioAllocator ghost-Protocol deleted (was reinventing CapitalAllocationPolicy)
- [x] SignalArbitrationPolicy named distinctly from CapitalAllocationPolicy (Q9 — keep separate, not unified)
