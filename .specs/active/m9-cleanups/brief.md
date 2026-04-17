# M9 — Cleanups (Conviction, Walk-Forward, Indicators, Regime, Risk)

**Summary**: Bundle of five independent cleanup items that depend on M7's Strategy API: replace conviction with priority + AllocationPolicy, finalize walk-forward extraction, build pull-based memoized indicators, remove regime from engine, and add formal risk components.

---

## Problem

After M7 ships the unified Strategy API, several v4 legacy patterns remain that are awkward, misleading, or incomplete:

1. **conviction_score misnomer**: The 0-1 float called "conviction" is actually a priority/allocation weight. Real conviction would affect sizing (M8). The name misleads strategy developers into thinking it affects position size when it only affects selection order.

2. **Walk-forward residue**: If M7 doesn't fully extract walk-forward from signal generation, residual coupling remains. Either way, the validation.py outer loop needs hardening.

3. **Push-model indicators**: v4 pre-computes all indicators and pushes them via context dicts. Strategies pay for indicators they don't use. There's no caching — the same EMA(20) computed twice for two strategies on the same token wastes cycles.

4. **Regime in engine**: Regime detection (BTC gate, TOTAL2 boost, bear/bull classification) is baked into the engine via `regime_params` config and exit_regime_* parameters. This couples the engine to a specific market-structure theory. Strategies should own their regime logic.

5. **No formal risk layer**: Risk checks (drawdown throttle, gross exposure limit, daily loss limit, funding safety) are scattered across config flags and ad-hoc if-statements. No structured risk component model.

6. **Dashboard regime display**: The dashboard shows regime state that will be removed from the engine. It also lacks scaling timeline and parent_position_id grouping from M2/M5.

---

## Scope

### In scope

**C-1: Conviction -> Priority + AllocationPolicy**
- Rename `conviction_score` to `priority` in TokenSignal
- `AllocationPolicy` Protocol with implementations:
  ```python
  class AllocationPolicy(Protocol):
      def rank(self, candidates: list[EntryCandidate], state: SimulationState) -> list[EntryCandidate]: ...

  # rank() returns a reordered list, not indices — returning indices couples the policy to the caller's list ordering.
  class RandomShuffle(AllocationPolicy): ...   # default
  class PriorityDesc(AllocationPolicy): ...    # by TokenSignal.priority
  class TieredPriority(AllocationPolicy):       # 3-tier with shuffle within tier
      def __init__(self, thresholds: list[float] = [0.66, 0.33]): ...  # configurable boundaries; default matches v4 (simulator.py:761-769)
  ```
  - `RandomShuffle` — current default behavior (random selection from eligible)
  - `PriorityDesc` — highest priority first
  - `TieredPriority` — 3-tier buckets with shuffle within tier (strategies assign tier 1/2/3; within each tier, random order prevents systematic bias toward alphabetical symbols)
  - `PortfolioConfig.allocation_policy: AllocationPolicy = RandomShuffle()` — pluggable
- Strategies return priority in TokenSignal; engine uses AllocationPolicy to order entries
- **conviction array->scalar reshape**: v4's `conviction_score: np.ndarray` is a per-bar array. v5's `priority: float | None` is a per-signal scalar. This is a reshape, not a rename. The strategy must pre-index at the current bar in `generate()`: `priority = conviction_array[bar_idx]`. The per-bar array no longer exists on the signal object.
- `min_conviction_threshold` REMOVED — strategies self-filter in `generate()` or don't emit low-quality signals

**C-2: Walk-Forward Finalization**
- If M7 left residual walk-forward logic in signals.py, extract it
- `v5/validation.py` hardening: configurable fold parameters, CPCV support, clear separation from signal generation
- Walk-forward params move from PortfolioConfig to a new `ValidationConfig` dataclass:
  ```python
  @dataclass
  class ValidationConfig:
      train_bars: int = 2000
      recal_bars: int = 500
      purge_bars: int = 100
      n_folds: int = 5
      cpcv_enabled: bool = True
  ```
  Passed to `validation.py` functions, NOT to the engine or strategies.
- Walk-forward isolation contract: Signal generation NEVER sees walk-forward masks. Strategies don't know WF exists. `v5/validation.py` slices `(train, oos)` windows and invokes the engine N times with clean state.
- Walk-forward is an outer loop that calls strategy.generate() repeatedly, never the reverse

**C-3: Pull-Based Memoized Indicators**
- `v5/indicators.py` — memoized indicator cache keyed by (symbol, indicator_name, params)
- `ctx.per_token(t).ema(20)` computes on first access, caches for the bar
- Memoization cache key: `(token, id(indicator_fn), frozen_params, bar_idx)` — uses `id(indicator_fn)` (stable within process) or a user-supplied string name instead of `__qualname__` (which can collide across modules). Cache scope: per backtest run (cleared between runs), per WF fold (fresh Strategy = fresh cache), per paper tick cycle (cleared after all strategies processed for the tick).
- Cache invalidates on new bar arrival
- Common indicators: EMA, SMA, ATR, RSI, Bollinger, VWAP, Donchian, Z-score
- Strategies define only what they use — no pre-computation of unused indicators

**C-4: Regime Removal from Engine**
- Delete `regime_params` from PortfolioConfig
- Delete `exit_regime_*` parameters
- Delete regime computation from BarProcessor / simulator
- `v5/regimes.py` — optional utility module that strategies can import and call. Utility function signatures:
  - `detect_crisis(universe_ctx, bar_idx)` — returns True during BTC-gated crisis conditions (needs full UniverseContext for BTC price history + TOTAL2 lookback, not just a single bar's context)
  - `detect_uptrend(universe_ctx, bar_idx)` — returns True during confirmed bull regime
  - `detect_dispersion(universe_ctx, bar_idx)` — returns True when alt dispersion is elevated
  - Memoized per `(bar_idx, universe_ctx_id)` — safe to call from multiple strategies without recomputation. `universe_ctx_id` is generated as `id(universe_ctx)` — unique per UniverseContext instance. Changes every bar (new instance per bar in backtest) or every tick cycle (paper mode).
- Strategies that use regime (s524 family) call `v5.regimes.detect_crisis(universe_ctx, bar_idx)` / `v5.regimes.detect_uptrend(universe_ctx, bar_idx)` in their `generate()` or `check_exit()` method

**C-5: Risk Components**
- `v5/risk.py` with formal risk component model:
  ```python
  class RiskComponent(Protocol):
      def check(self, candidate, state) -> RiskDecision: ...

  class RiskDecision(Enum):
      ACCEPT = "ACCEPT"
      REJECT = "REJECT"
      REDUCE = "REDUCE"
      HALT = "HALT"
  ```
  - `DrawdownThrottle` — reduces position count or size during drawdown. `__init__(thresholds: list[tuple[float, float]], scope: Literal["portfolio", "strategy"] = "portfolio")`. Portfolio-scope is default.
  - `MaxGrossExposure` — caps total portfolio exposure
  - `MaxNetExposure` — caps net long/short imbalance
  - `MaxConcurrentOrders` — limits total open orders across strategies
  - `DailyLossLimit` — halts trading after daily loss threshold (`max_daily_loss_usd`)
  - `FundingSafetyCheck` — warns/blocks when funding rate is extreme
  - `PortfolioConfig.risk_components: list[RiskComponent] = [DrawdownThrottle([...])]` — pluggable
  - `TradingState`: `state.trading_state: Literal["ACTIVE", "REDUCING", "HALTED"]` derived from risk components. HALTED blocks all new entries. ACTIVE is normal operation. In `TradingState.REDUCING`: (a) new entries BLOCKED, (b) exits via stop/trail/exit-chain ALLOWED (risk-reducing), (c) `Strategy.check_scale` returning negative qty_delta (reduce) ALLOWED, (d) `Strategy.check_scale` returning positive qty_delta (increase) BLOCKED, (e) linked-leg auto-propagation of REDUCES allowed, auto-propagation of INCREASES blocked.
- Risk components registered with BarProcessor, checked before Phase 3 (entries)

**C-6: Dashboard Rework**
- Remove regime display panels
- No `consolidate_partial_trades` band-aid — Dashboard provides BOTH views: (a) position-level grouping by `parent_position_id` (scaling history per position), (b) order-level grouping by `order_id` (multi-leg order status with all legs). Toggle in dashboard UI.
- New trade table columns: `scale_count`, `leaves_qty`, `has_scaling`, `r_anchor_price`
- Add scaling_events timeline in position detail view (from M2's ScalingEvent data)
- Update trade table to show binding constraint from M8

**C-6 FIX-aligned counters** (in `v5/report.py` / `SimulationState`):
- `partial_fills: int` — non-terminal `Position.reduce` events only (reduce-only, not exits)
- `increase_fills: int` — scale-up events
- `contingent_fills: int` — auto-propagated linked-leg executions
- `entry_scale_downs: int` — entries clamped by concentration/ADV/capital constraints

### Out of scope

- New indicator algorithms not in v4
- Machine learning indicator models
- External data-based risk (e.g., VIX-based throttle — that's strategy logic)
- Dashboard infrastructure changes (stays Streamlit)
- Portfolio optimization / rebalancing

---

## Key Acceptance Criteria

1. **conviction_score deleted**: No references to `conviction_score` remain in v5/. All replaced with `priority`. TokenSignal uses `priority: float`. A grep test confirms zero hits for "conviction_score" in v5/.

2. **AllocationPolicy works**: PriorityDesc selects the highest-priority signals first. A test with 10 signals and max_positions=3 verifies the top 3 by priority are selected.

3. **Memoized indicators**: `ctx.per_token("BTCUSDT").ema(20)` called twice in the same bar returns the same object (identity check). Called on a new bar, it recomputes. A test verifies both behaviors.

4. **Regime out of engine**: BarProcessor and simulator have zero references to regime. `v5/regimes.py` exists as an optional import. s524m calls `v5.regimes.classify()` in its generate() method. Engine tests pass without any regime config.

5. **Risk components**: DrawdownThrottle reduces max_positions when portfolio drawdown exceeds threshold. A test simulates 15% drawdown and verifies position count is reduced.

6. **TradingState**: Risk components collectively determine TradingState. HALTED blocks all new entries. REDUCING blocks entries but allows scale-out. ACTIVE is normal. A test transitions through all three states (ACTIVE -> REDUCING -> HALTED). RiskDecision enum `{ACCEPT, REJECT, REDUCE, HALT}` is returned by each component's `check()` method.

7. **Dashboard renders**: Dashboard loads without errors after regime removal. Scaling timeline renders for a strategy with ScalingEvents. Parent position grouping shows linked trades together.

8. **Walk-forward clean**: `v5/validation.py` can run walk-forward with configurable folds independently of signal generation. A test runs 3-fold walk-forward and verifies fold boundaries.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M7** (Strategy API) | **Required** — AllocationPolicy plugs into signal dispatch; indicators are part of UniverseContext; regime removal requires Strategy.generate() to own regime logic |
| **M4** (BarProcessor) | **Required** — Risk components integrate with BarProcessor Phase 3 gating |
| **M2** (Position Scaling) | **Benefits from** — Dashboard scaling timeline uses ScalingEvent data |
| **M5** (Multi-leg Orders) | **Benefits from** — Dashboard parent_position_id grouping uses Order.legs data |
| **M8** (Sizing) | **Benefits from** — Dashboard binding constraint display uses M8's per-fill logging |

---

## Time Estimate

**30-40 hours**

- ~5h: C-1 conviction -> priority + AllocationPolicy
- ~4h: C-2 walk-forward finalization
- ~6h: C-3 pull-based memoized indicators
- ~4h: C-4 regime removal + v5/regimes.py utility
- ~6h: C-5 risk components (DrawdownThrottle, MaxGrossExposure, DailyLossLimit, FundingSafetyCheck, TradingState)
- ~5h: C-6 dashboard rework
- ~5h: Tests (unit + integration)
- ~3h: Documentation + migration notes

---

## Parity Gate

- All v5 tests pass with new conviction->priority naming
- Dashboard renders correctly without regime panels
- s524m strategy works with regime logic in strategy.generate() (not engine)
- Risk components can be disabled (no-op) for parity with v4 behavior
- Memoized indicators produce identical values to v4's pre-computed indicators
