# Feature Brief: v5 Engine — Production-Hardened Evolution

**Target: `v5/` new directory. v4 stays running until strategies migrate.**

This spec builds v5 — the next hardened evolution of the backtest engine and paper trader. Not a prototype, not an MVP: v5 must ship with the same production-grade quality bar as v4 (which has backtested 5+ years across 200+ strategies and runs live paper trading on $450K capital across s513/s523c/s524m pools).

v5 corrects architectural debt accumulated in v4 and adds position-scaling as a first-class feature. v4 keeps running existing strategies until the user chooses to migrate each one. Eventually v4 freezes (the v3 → v4 pattern).

## Problem

The v4 engine has two categories of issues:

**Category 1 — Fragmented architecture (sources of recurring bugs)**:
- Three separate per-bar dispatchers (`simulator.py:_process_exits`, `minute_exits.py`, `paper_engine.py`) with their own copies of Phase 1/2/3 logic. Caused the 2026-04-15 StopLossHandler-deletion incident (-328pp in metrics).
- Two signal-generation paths (`signals.py` per-token, `portfolio_signals.py` cross-sectional) with different dataclasses (TokenSignals vs PortfolioSignals) and different sizing code.
- Dead/dormant code coupled into paper_engine (sentinel stack — never went live; paper_shadow — dormant, only needed for combined spot+perp).
- `engine.py` misnamed (it's the strategy-API layer, not the "engine").

**Category 2 — Missing position-scaling primitives**:
1. **Entry** — open at fixed size (inflexible)
2. **Partial TP** — one-shot, single-level reduction at fixed profit threshold. Blocks further partials.
3. **Full exit** — close entire position
4. Missing: `Position.increase`, multi-level `Position.reduce`, conditional scaling reacting to any signal

## Why v5, Not v4 Refactor

The original position-scaling spec targeted v4 modification. Review surfaced 34-44h of work touching 129 files (ClosedTrade has 129 importers), with real risk of breaking the paper trader running s513/s523c/s524m. Per the `v3 is FROZEN` pattern already codified in CLAUDE.md, major engine changes fork rather than modify.

v5 gives us a ONE-SHOT opportunity to fix Category 1 architectural issues from a clean slate, instead of layering new features on the fragmented base. Doing the architectural cleanup concurrent with position-scaling is cheaper than doing them sequentially because many of the position-scaling complications (dual-dispatcher wiring, per-hourly-bar cap, paper defensive check) collapse to triviality under a unified architecture.

## Why This Matters (trading value)

Analysis showed:
- Winners establish direction by 48-72h → want to enter 50%, add 50% on confirmation
- Fat-tail winners need multi-level partial-out to lock in profit while keeping runner
- Losers identifiable by day 2-3 → want to cut to 25% size early
- Current one-shot `partial_tp` + fixed entry = can't express any of this

**Exchange reality**: one (strategy, token, market) = ONE position. Exchanges track NET quantity. Scaling is the correct model for "add to winner" or "cut loser" — NOT opening parallel positions. v5 does not have the legacy `max_concurrent_per_token > 1` workaround; position is unitary.

## Vocabulary Mapping (CRITICAL — read before implementing)

This brief deliberately adopts FIX/NautilusTrader vocabulary for engine primitives. The implementer should reference this section for every old↔new term, FIX-source citation, and quantity-sign convention.

### Position primitives — old → new

| OLD (delete) | NEW (FIX/Nautilus-aligned) | FIX/Nautilus source |
|---|---|---|
| `Position.add_to(qty_delta, ...)` (proposed) | `Position.increase(qty_to_add, fill_price, margin_delta, bar_idx, stop_override=None)` | NautilusTrader `Position.apply()` semantics |
| `Position.scale_out(close_fraction, ...)` (proposed) | `Position.reduce(qty_to_close, fill_price, bar_idx, triggered_by="")` | FIX `LastQty(32)` / Nautilus `last_qty` |
| `_partial_close_position(state, pos, close_pct, ...)` (simulator.py:286, EXISTING) | DELETED — replaced by `Position.reduce` | — |
| (none) | `Position.reduce_fraction(fraction, ...)` — convenience wrapper | — |
| (none) | `Position.leaves_qty` (property) → `abs(self.quantity)` | FIX `LeavesQty(151)` |
| (none) | `Position.is_closed` (property) → `self.quantity == 0.0` (exact) | NautilusTrader `position_side == FLAT` |
| `pos.partial_closed: bool` (EXISTING) | DELETED — `Position.reduce` is multi-shot, no flag needed | — |

### Quantity vocabulary — what's signed, what's unsigned

| Term | Type | Where | Notes |
|---|---|---|---|
| `qty_to_add` | unsigned float, position-direction units | `Position.increase` parameter | FIX-aligned API primitive |
| `qty_to_close` | unsigned float, position-direction units | `Position.reduce` parameter | FIX-aligned API primitive |
| `qty_delta` | **signed** float, position-direction units | `ScaleAction.qty_delta` field | Engine routes positive→`increase`, negative→`reduce` |
| `close_fraction` | unsigned float in [0, 1] | INTERNAL only — derived inside `reduce` | For pro-rating margin/funding/fees |
| `fraction` | unsigned float in [0, 1] | `Position.reduce_fraction()` parameter | Convenience wrapper |
| `pos.quantity` | **signed** float | Position field (existing) | Sign matches `pos.direction`; invariant per AC22 |
| `pos.leaves_qty` | unsigned float, derived | Position property (NEW) | `abs(pos.quantity)` — FIX `LeavesQty(151)` analog |

### Position state — mutation behavior

| Field | After `Position.increase` | After `Position.reduce` |
|---|---|---|
| `entry_price` | Updated to new VWAP (per AC1) | Unchanged (per AC5) |
| `quantity` (signed) | += `pos.direction * qty_to_add` | *= `(1 - close_fraction)` |
| `margin_usd` | += `margin_delta` | *= `(1 - close_fraction)` |
| `cumulative_funding` | Unchanged (per AC9) | *= `(1 - close_fraction)` |
| `initial_risk` | **FROZEN** at entry-time value (per AC3) | Unchanged |
| `entry_bar` / `entry_timestamp` | Unchanged (per AC21) | Unchanged |
| `breakeven_triggered` | Preserved if True (per AC3) | Preserved |
| `highest` / `lowest` | Continue tracking (per AC16) | Continue tracking |
| `stop_price` | `stop_override` OR never-loosen default (per AC2) | Unchanged |
| `scale_count` (NEW int field) | Unchanged | += 1 BEFORE booking ClosedTrade (Q4 rollback on failure) |
| `scaling_events` (NEW list[ScalingEvent]) | Append `kind="increase"` event | Append `kind="reduce"` event |
| `r_anchor_price` (NEW float field, Q-DEC1) | **Unchanged** (frozen at first entry) | Unchanged |
| `_helper_state` (NEW dict field, Q-DEC3) | Helpers may mutate their own keys | Helpers may mutate their own keys |
| `_scale_action_bar` (NEW int field, C2) | Set to current hourly bar | Set to current hourly bar |

### ClosedTrade fields — old → new

| Field | Status | Notes |
|---|---|---|
| `position_id` (EXISTING) | KEPT | Suffix scheme: `parent_id` for normal exit, `parent_id:scale_{n}` for non-terminal reduce, `parent_id:scale_{n}_final` for terminal reduce (per AC4) |
| `exit_reason` (EXISTING) | KEPT | Granular cause ("stop", "take_profit", etc.) — orthogonal to `exec_type` (per AC30) |
| `parent_position_id: str = ""` | NEW (AC29) | Immutable — joins to `Position.position_id`. FIX `OrderID(37)` analog |
| `exec_seq: int = 0` | NEW (AC29) | 0 for full close (legacy default), 1..N for scale executions. FIX `ExecID(17)`-derived sequence |
| `exec_type: str = "exit"` | NEW (AC29) | Values: `"open"` / `"increase"` / `"reduce"` / `"exit"` / `"linked_reduce"`. FIX `ExecType(150)` analog |
| `is_terminal: bool = True` | NEW (AC29) | True for the final ClosedTrade that empties the position. FIX `OrdStatus=Filled (2)` / Nautilus `is_closed` analog |
| `triggered_by: str = ""` | NEW (AC29) | Parent exec_id when auto-propagated (linked-leg per AC36). FIX `ExecRestatementReason(378)` analog |
| `has_scaling: bool = False` | NEW (AC13) | Convenience flag for analysis filtering |

### SimulationState counters — FIX-aligned

| Counter | Status | Semantic |
|---|---|---|
| `partial_fills: int` (EXISTING) | KEPT, repurposed | Count of `Position.reduce` non-terminal events. FIX `OrdStatus=PartiallyFilled (1)` analog |
| `increase_fills: int = 0` | NEW (AC26) | Count of `Position.increase` events. FIX: fills that grow `CumQty(14)` in same direction |
| `contingent_fills: int = 0` | NEW (AC26) | Count of auto-propagated linked-leg executions. FIX `ContingencyType(1385)` triggered fills |

### Strategy hooks

| Hook | Status | Notes |
|---|---|---|
| `entry_filter_fn` (EXISTING) | UNCHANGED | — |
| `exit_check_fn` (EXISTING) | UNCHANGED | — |
| `scale_check_fn(pos, bar) -> ScaleAction \| None` | NEW (AC6) | Standard strategy↔engine callback pattern (analog: NautilusTrader `Strategy.on_bar()`, Backtrader `Strategy.next()`, QuantConnect `OnData()`). The returned `ScaleAction` is project-typed but conceptually equivalent to a FIX `NewOrderSingle(35=D)` (same-side for `qty_delta>0`, opposite-side for `qty_delta<0`). Engine translates: `qty_delta > 0` → `Position.increase`; `qty_delta < 0` → `Position.reduce`. Per-(position, bar) invocation cadence is project-specific (our engine is bar-based; live engines would invoke per-tick or per-event) |
| `linked_scale_policy: LinkedScalePolicy` | NEW (AC36) | `INDEPENDENT` (default) / `PROPORTIONAL` / `ABSOLUTE`. Maps to FIX `ContingencyType(1385)` enum values |

### Deleted (per AC12)

| Item | Replacement |
|---|---|
| `partial_tp_atr`, `partial_tp_pct`, `partial_tp_trail` (config fields) | `tp_ladder_atr()` / `tp_ladder_price()` / `tp_ladder_r()` helpers in `v4/scaling.py` |
| `partial_closed: bool` (Position field) | None — `reduce` is multi-shot |
| `PartialTPHandler` class (exit_handlers.py) | `scale_check_fn` + helpers |
| `_partial_close_position` (simulator.py:286) | `Position.reduce` method |
| `:partial` suffix on `ClosedTrade.position_id` | `:scale_{n}` and `:scale_{n}_final` (per AC4) |

### Helper API (`v4/scaling.py`) — translates strategy intent to absolute-unit primitives

| Helper | Translates to (per (pos, bar)) |
|---|---|
| `tp_ladder_atr([(2.0, 0.3), ...])` | `Position.reduce_fraction(0.3, ...)` at each ATR-profit threshold |
| `tp_ladder_price([(78000, 0.3), ...])` | `Position.reduce_fraction(0.3, ...)` at each absolute price level |
| `tp_ladder_r([(1.0, 0.33), ...])` | `Position.reduce_fraction(0.33, ...)` at each R-multiple of `initial_risk` |
| `breakeven_plus_runner(partial_r, fraction)` | One-shot `Position.reduce_fraction(fraction, ...)` at breakeven |
| `add_at_price([(71000, 0.3), ...])` | `Position.increase(qty_to_add=0.3 * abs(pos.quantity), ...)` at price triggers |
| `add_on_profit_atr([(1.5, 0.5), ...])` | `Position.increase(qty_to_add=0.5 * abs(pos.quantity), ...)` at ATR-profit triggers |
| `confirm_and_add(confirm_bars, min_profit_atr, add_fraction)` | Single `Position.increase(qty_to_add=add_fraction * abs(pos.quantity_at_entry), ...)` |
| `combine(*fns)` | Returns first non-None `ScaleAction` |

### FIX/Nautilus reference (sources for vocabulary)

- **FIX `OrderID(37)`** → maps to our `parent_position_id` (immutable parent identity)
- **FIX `ExecID(17)`** → conceptually our **`exec_id := position_id`** (the SUFFIXED form, e.g., `parent:scale_1`). We do NOT carry a separate `exec_id` field; the suffixed `position_id` IS our per-execution unique key (per AC4 invariant). When other ACs say "primary's exec_id" (e.g., AC36's `triggered_by`), they mean the primary execution's `position_id` (suffixed).
- **FIX `ExecID(17)` sequence** → maps to our `exec_seq` (per-execution monotonic sequence within a parent position)
- **FIX `ExecType(150)`** → maps to our `exec_type` (lifecycle phase enum)
- **FIX `OrdStatus=Filled(2)`** → maps to our `is_terminal=True` (terminal-fill marker)
- **FIX `LastQty(32)`** → maps to our `qty_to_close` parameter on `Position.reduce`
- **FIX `LeavesQty(151)`** → maps to our `Position.leaves_qty` property
- **FIX `CumQty(14)`** → maps to our `abs(Position.quantity)` (no separate field — derived)
- **FIX `ContingencyType(1385)`** → maps to our `LinkedScalePolicy` enum (INDEPENDENT/PROPORTIONAL/ABSOLUTE)
- **FIX `ExecRestatementReason(378)`** → maps to our `triggered_by` (auto-propagation parent reference)
- **FIX `MaintMargin(896)`** → updated each fill (per AC28 — liquidation uses post-scale state)
- **NautilusTrader `Position.apply(fill)`** → conceptual model for our `Position.increase` / `Position.reduce`
- **NautilusTrader `position_side == FLAT`** → maps to our `Position.is_closed` property

## Key Design Decision: UNIFIED mechanism (Option B)

**DELETE legacy `partial_tp` fields/handler entirely**, replace with `scale_check_fn` hook. Rationale:
- Multi-level `Position.reduce` is strictly a superset of `partial_tp` (can implement partial_tp as 5 lines in a scale_check_fn)
- Avoids ambiguity ("which do I use to downsize?")
- Single order-of-operations path (no interaction bugs)
- Matches industry best practice (Backtrader/QuantConnect/NautilusTrader use composable primitives + helpers)

**A helper builder** (`v4/scaling.py`) provides the common declarative case:
```python
from v4.scaling import tp_ladder
PORTFOLIO_CONFIG = {
    "scale_check_fn": tp_ladder([
        (2.0, 0.30),   # at +2 ATR profit: close 30%
        (4.0, 0.30),   # at +4 ATR: close 30%
        (6.0, 0.20),   # at +6 ATR: close 20%
    ], trail_mult_after_first=1.5),
}
```

## Scope

### v5 Fork Structure

**Create v5/ directory** with ~20 core files (stripped from v4's ~30+, plus new architectural modules):

| File | Action |
|------|--------|
| `v5/strategy_api.py` (renamed from engine.py) | Strategy Protocol, StrategyContext, UniverseContext, UniverseSignals, TokenSignal (with `priority: float \| None`, NO `conviction_score`), **ScaleAction**. Copy v4/engine.py base + rename + additions. |
| `v5/simulator.py` | Copy v4 with major cleanup: raw_mode REMOVED (single execution path); uses bar_processor for per-bar dispatch |
| `v5/bar_processor.py` | NEW — single per-bar dispatcher (replaces simulator._process_exits + minute_exits + paper_engine dispatch) |
| `v5/exit_handlers.py` | Copy v4 without PartialTPHandler, any regime-related handlers |
| `v5/position.py` | Position (with increase/reduce/reduce_fraction methods) + ClosedTrade identity fields + **ScalingEvent** + **ReduceResult** + PositionManager |
| `v5/orders.py` | NEW — `Leg` dataclass and `Order.legs: list[Leg]` abstraction for multi-leg atomic orders (FIX `LegGrp(555)` analog). Replaces v4's primary+secondary pattern. |
| `v5/allocation.py` | NEW — `AllocationPolicy` protocol + `RandomShuffle` (default), `PriorityDesc`, `TieredPriority` implementations. Replaces v4's `conviction_mode` enum + ad-hoc sort logic. |
| `v5/risk.py` | NEW — `DrawdownThrottle` and other portfolio-level risk components. Pluggable risk filters between ranking and sizing. Replaces v4's per-strategy `dd_scaling` config. |
| `v5/indicators.py` | NEW — pull-based memoized indicator cache. `UniverseContext.per_token(t).ema(20)` lazily computes + memoizes. Replaces v4's `REQUIRED_PLUGINS`/`REQUIRED_INDICATOR_GROUPS` push model. |
| `v5/sizing/` | NEW module (package) — `intents.py` (2-intent engine contract: FIXED_FRACTION, FIXED_NOTIONAL + SizingRequest dataclass) + `helpers.py` (strategy-side library: `vol_target_fraction`, `kelly_fraction`, `composite_scaled_fraction` for s524-family migration). Replaces v4's opaque 9-layer pipeline. |
| `v5/orders.py` | NEW — mentioned above; `Leg`, `Order`, `LegStatus` enum for unified multi-leg + armed entries (C-7) |
| `v5/data/` | NEW package — FIX/Nautilus-aligned data architecture. See "V5 Data Architecture" section. Replaces fragmented v4 data fetching (price_monitor.py, live_fetcher.py, candle_aggregator.py, hourly_bar_collector.py, minute_exits.py MinuteExitCache, data_loader.py, ~25 tools/fetch_*.py scripts). |
| `v5/helpers.py` | NEW — user-facing DSL: `tp_ladder_atr/price/r`, `breakeven_plus_runner`, `add_at_price`, `add_on_profit_atr`, `confirm_and_add`, `combine`. Pure closures over (pos, bar). |
| `v5/regimes.py` | NEW — **optional** utility module. `detect_crisis(bar_ctx)`, `detect_uptrend(bar_ctx)`, etc. Strategies import if they want regime logic. Engine has no knowledge of regime. |
| `v5/signals.py` | NEW unified universe-style API (Option Y) — replaces v4/signals.py + portfolio_signals.py. ONE signal-generation path. **NO walk-forward mask** (extracted to validation.py). |
| `v5/validation.py` | Copy v4 + absorb walk-forward orchestration: outer-loop wrapper that slices (train, oos) windows and invokes engine N times with clean state. Walk-forward out of signal generation. |
| `v5/data_loader.py` | Copy v4 with `hist_cache` internalized (LRU cache inside loader; removed from function signatures) |
| `v5/config.py` | PortfolioConfig, StrategySpec, **LinkedScalePolicy** enum, new knobs. REMOVED: `strategy_type`, `conviction_mode`, `min_conviction_threshold`, `train_bars`, `recal_bars`, `purge_bars`, `skip_walk_forward`, `true_walk_forward`, `live_bar`, `raw_mode`, `raw_max_positions`, `dd_scaling`, `pump_filter_funding_zscore`, `pump_filter_range_threshold`, `regime_params`. Per-strategy `entry_resolution` + `exit_resolution` REPLACED by single `PortfolioConfig.bar_resolution`. `max_concurrent_per_token` RENAMED to `max_positions_per_symbol` + fixed counting. |
| `v5/paper_engine.py` | Copy v4, strip sentinel imports, use bar_processor for per-tick scaling, single-resolution subscription |
| `v5/paper_state.py` | Copy v4 with new ClosedTrade identity fields |
| `v5/paper_config.py` | Copy v4, simplified (no strategy_type auto-detection, no conviction_mode) |
| `v5/paper_utils.py` | Copy v4 |
| `v5/report.py` | Copy v4 with new counters + load_trade_log compat parser |
| `v5/dashboard_state.py` | Copy v4 |
| `v5/position_commands.py` | Copy v4 |
| `v5/tests/` | NEW test suite (v5-internal) |

**NOT copied to v5** (cruft/dead code):
- `minute_exits.py` → eliminated by bar_processor (single dispatcher handles all resolutions)
- `portfolio_signals.py` → merged into unified `signals.py` (Option Y)
- `paper_shadow.py` → dormant; revive when a combined spot+perp strategy needs it
- `run_sentinel.py`, `sentinel_metrics.py`, `breach_detector.py`, `stop_store.py` → sentinel stack never went live, skip
- **Regime computation out of engine** → `bar.regime` field removed; `v5/regimes.py` is optional utility library strategies import if needed

### Module organization for scaling types (REORG applied)

Scaling-related types live with their conceptual homes (not lumped in one `scaling.py`):

| Type | Lives in | Rationale |
|------|----------|-----------|
| `ScaleAction` (strategy output) | `v5/strategy_api.py` | Alongside StrategyContext, UniverseSignals — strategy-side types |
| `ScalingEvent` (position state) | `v5/position.py` | Appended to `pos.scaling_events` — position-side state |
| `ReduceResult` (domain bundle) | `v5/position.py` | Returned by `Position.reduce` — domain-layer type |
| `LinkedScalePolicy` (config enum) | `v5/config.py` | Field on StrategySpec — config-side |
| Helpers (`tp_ladder_*`, `add_*`, `combine`) | `v5/helpers.py` | User-facing DSL for strategies |

No `v5/scaling.py` module — types live where they belong.

### v5 Architectural Cleanups (Quirks Audit)

v5 addresses 11 categories of v4 architectural debt surfaced by the quirks audit. Each is production-grade cleanup, not cosmetic:

#### C-1. Conviction system → `priority` + `AllocationPolicy`
- **v4**: `TokenSignals.conviction_score: Optional[np.ndarray]` conflates three concerns (signal strength, tie-break priority, entry filter). `PortfolioConfig.conviction_mode` enum (shuffle/ranked/hybrid). `min_conviction_threshold` filter.
- **v5**:
  - `TokenSignal.priority: float | None` — pure sort key for tie-break (no loaded semantic naming)
  - `v5/allocation.py` defines `AllocationPolicy` protocol:
    ```python
    class AllocationPolicy(Protocol):
        def rank(self, candidates: list[EntryCandidate], state: SimulationState) -> list[int]: ...

    class RandomShuffle(AllocationPolicy): ...   # default
    class PriorityDesc(AllocationPolicy): ...    # by TokenSignal.priority
    class TieredPriority(AllocationPolicy): ...  # 3-tier + shuffle within tier
    ```
  - `PortfolioConfig.allocation_policy: AllocationPolicy = RandomShuffle()` — pluggable
  - `min_conviction_threshold` REMOVED — strategies self-filter in `generate()` or don't emit low-quality signals
- **FIX alignment**: FIX has no "conviction" — priority is via `Price(44)` / `OrdType(40)`. Our AllocationPolicy is the portfolio-engine analog of venue-side matching logic.

#### C-2. Walk-forward extracted from signal generation → outer loop in validation.py
- **v4**: `_apply_walk_forward_mask()` in signals.py bakes WF inside signal generation. 5 config knobs (`train_bars`, `recal_bars`, `purge_bars`, `skip_walk_forward`, `true_walk_forward`), `live_bar` paper escape hatch, 1 diagnostic counter.
- **v5**: Walk-forward becomes validation.py's outer loop responsibility. It slices `(train, oos)` windows and invokes `run_backtest(v5_engine, oos_window)` N times with clean state. Engine knows nothing about WF.
- Signal generation in v5 always produces signals for the full window; validation decides which slices to use.
- **FIX alignment**: FIX/Nautilus have no WF concept — it's a validation wrapper, not an engine property.
- **Config knobs removed**: 5 fields + `live_bar` (paper mode uses last bar directly; no mask needed).

#### C-3. Plugin system → pull-based memoized indicators
- **v4**: Strategies declare `REQUIRED_PLUGINS = ['obv', 'vwap']` + `REQUIRED_INDICATOR_GROUPS = {'ema', 'macd'}` as module-level constants. Engine introspects via `getattr`, precomputes only declared subsets.
- **v5**: `v5/indicators.py` provides lazy memoized access:
    ```python
    class UniverseContext:
        def per_token(self, token: str) -> TokenContext: ...

    class TokenContext:
        def ema(self, n: int) -> np.ndarray:  # computed + memoized on first access
        def macd(self, fast=12, slow=26, signal=9) -> tuple[np.ndarray, ...]: ...
        # all indicators accessible as methods
    ```
- Strategies just call `ctx.per_token("BTC").ema(20)` inline. No module constants, no introspection.
- Memoization key: `(token, indicator_name, params_hash)`. Engine computes once per (bar, key) across all strategies within a run.
- **FIX alignment**: Nautilus uses the same pull pattern.

#### C-4. `raw_mode` REMOVED from v5 engine
- **v4**: `config.raw_mode=True` skips portfolio constraints, uses strategy's own sizing. Two parallel execution paths in simulator (~300 LOC of if-branches). Combined strategies explicitly unsupported in raw mode.
- **v5**: Removed entirely. Gate-1 signal research continues using `tools/raw_backtest.py` (a separate script with its own minimal execution, unchanged by this spec).
- One execution path in v5 simulator = ~300 LOC saved, dramatically simpler code.

#### C-5. `max_concurrent_per_token` → `max_positions_per_symbol` (renamed + fixed counting)
- **v4**: Name confusing (what "per token" actually means); combined strategies double-count (each entry = 2 Position objects).
- **v5**: Renamed to `max_positions_per_symbol` (matches FIX `MaxShow(210)` / risk-engine concepts). Counts LOGICAL entries, not Position row count; multi-leg orders count as 1.

#### C-6. `dd_scaling` → `DrawdownThrottle` risk component
- **v4**: Per-strategy `dd_scaling: tuple[tuple[float, float], ...]` applied at simulator.py:564-589. Odd architecture — drawdown is portfolio-level metric, config is per-strategy.
- **v5**: `v5/risk.py` provides pluggable risk components:
    ```python
    class RiskComponent(Protocol):
        def check(self, candidate, state) -> RiskDecision: ...

    class DrawdownThrottle(RiskComponent):
        def __init__(self, thresholds: list[tuple[float, float]], scope: Literal["portfolio", "strategy"] = "portfolio"): ...
    ```
- `PortfolioConfig.risk_components: list[RiskComponent] = [DrawdownThrottle([...])]` — pluggable
- Portfolio-scope is the default (most common case); per-strategy available as opt-in
- **FIX alignment**: Matches NautilusTrader's RiskEngine component model.

#### C-7. Multi-leg `Order.legs: list[Leg]` — unified multi-leg + armed entries + pending orders
- **v4 problems consolidated here** (three divergent patterns that share the same concept):
  - `secondary_*` / `sec_*` / `is_perp_primary` / `perp_close` — ~20 duplicated fields on TokenSignals for spot+perp combined strategies
  - `PendingEntry` dataclass + `SimulationState.pending_entries` list — backtest armed entries (simulator.py:87-106)
  - `_armed_tokens` dict + lock + persistence + recovery path — paper armed entries (paper_engine.py:165-171, 1264-1298)
  - Two divergent armed-entry implementations that drift over time (the same dispatcher-fragmentation pattern)
- **v5**: `v5/orders.py` — ONE abstraction that subsumes all three:
    ```python
    class LegStatus(Enum):
        ARMED = "armed"          # waiting for trigger price (FIX OrdType=Stop/StopLimit)
        WORKING = "working"      # active open position
        FILLED = "filled"        # terminal — fully closed
        EXPIRED = "expired"      # armed but trigger window passed
        CANCELLED = "cancelled"  # explicit cancel

    @dataclass
    class Leg:
        symbol: str              # token
        market: str              # "spot" | "perp"
        venue: str               # exchange
        direction: int           # +1 long, -1 short
        size_share: float        # fraction of the order's total allocation
        order_type: str          # "market" | "stop" | "stop_limit" | "limit"
        status: LegStatus
        trigger_price: float | None   # for STOP/STOP_LIMIT — FIX StopPx(99)
        entry_price: float | None     # set when trigger hits
        stop_price: float | None      # post-entry stop
        # ... other per-leg fields

    @dataclass
    class Order:
        order_id: str            # FIX OrderID(37) analog — unique per order
        legs: list[Leg]          # one-or-more; single-leg order is just [Leg(...)]
        priority: float | None   # order-level (from C-1)
        armed_at_bar: int | None # when arming fired (for audit)
        window_end: int | None   # TimeInForce=GTD deadline (FIX)
        # ...
    ```
- **Single-leg market entries** (common case): `Order(legs=[Leg(status=WORKING, order_type="market")])` — zero overhead
- **Multi-leg combined**: `Order(legs=[Leg(primary), Leg(secondary)])` — spot+perp pair
- **Armed entries** (waiting for price): `Order(legs=[Leg(status=ARMED, order_type="stop", trigger_price=71000)])` — same schema, different status
- **Multi-leg + armed** combines naturally: `Order(legs=[Leg(status=ARMED, ...), Leg(status=ARMED, ...)])` for paired arming
- `Position.order_id: str` links open positions back to their originating Order (FIX `OrderID(37)` / `LegGrp(555)` analog)
- **FIX alignment**:
  - Matches FIX's `LegGrp(555)` multi-leg order structure exactly
  - `LegStatus.ARMED` = FIX `OrdType=Stop(3)` / `StopLimit(4)` with `OrdStatus=New(0)` / `PendingNew(A)`
  - `LegStatus.WORKING` = FIX `OrdStatus=PartiallyFilled(1)` (once entry fills)
  - `LegStatus.FILLED` = FIX `OrdStatus=Filled(2)`
  - `LegStatus.EXPIRED` = FIX `OrdStatus=Expired(C)`
  - Nautilus BracketOrder uses the same list pattern
- **Replaces**:
  - `PendingEntry` dataclass + `SimulationState.pending_entries` → `SimulationState.open_orders: list[Order]` with `[o for o in open_orders if any(leg.status == ARMED for leg in o.legs)]` for armed-filtering
  - Paper `_armed_tokens` dict → same `open_orders` list; no separate dict, no separate lock
  - `_serialize_armed_tokens` → serialize `open_orders` normally
  - `conviction` frozen at arm time → replaced by `priority` frozen at arm time
- **Audit trail preserved**: `armed_log.jsonl` writes continue for arm/fire/expire/cancel events (dashboard depends on it)
- **Scope implication**: v5 supports multi-leg, armed entries, and pending orders from Day 1 via ONE clean abstraction. Eliminates the v4 three-way fork (combined fields / PendingEntry / _armed_tokens).

#### C-8. `exit_resolution`/`entry_resolution` → single `PortfolioConfig.bar_resolution`
- **v4**: Per-strategy `exit_resolution` and `entry_resolution`. Causes dual WS subscriptions in paper_engine (one per resolution).
- **v5**: Single `PortfolioConfig.bar_resolution: Timedelta = Timedelta(hours=1)` — engine-wide. All strategies in a portfolio run at the same resolution. Paper engine subscribes to one stream.
- Multiple resolutions becomes a separate portfolio with its own config (not within a single portfolio).

#### C-9. `hist_cache` internalized to data_loader
- **v4**: `hist_cache: dict` parameter threaded through 5+ function signatures (`precompute_signals`, `load_token_data_cached`, etc.).
- **v5**: `v5/data_loader.py` uses internal `@lru_cache(maxsize=N)`. Callers never see it.

#### C-10. `pump_filter_*` removed from engine config → strategy's `filter_entry()` method
- **v4**: `StrategySpec.pump_filter_funding_zscore` and `pump_filter_range_threshold` applied at simulator.py:1022-1039. 2 rejection counters.
- **v5**: Removed from config. Strategies that care implement in their `filter_entry()` method. Kills 2 fields, 2 rejection counters, 2 code paths.

#### C-11. `strategy_type: per_token | portfolio` dispatch — unified (already in Option Y)
- Removed via Option Y unified strategy API.

#### C-12. Sizing system → 2-intent engine + strategy-owned helpers (TOP-FIRM ALIGNED)

**v4 problem**: 9-layer opaque pipeline with implicit ADV-indexed Kelly curve, silent 4× vol_adj multiplier, ~9 overlapping size parameters, 3 different scale knobs (`size_multiplier`, `cap_multiplier`, `max_trade_pct`), `unrealized_pnl_floor=0.85` loss-masking floor. Strategy authors cannot predict their own position size. Known pain point flagged in `memory/V4_SIZING_PIPELINE.md`.

**Top-firm expert consensus (Renaissance/Two Sigma/AQR/Lopez de Prado pattern)**: Separate *signal* (alpha) from *portfolio construction* (sizing math) from *risk overlay* (engine clamps). Strategies emit a `target_weight` or `target_notional`; the engine accepts it and applies ONLY named clamps. Kelly/vol-target are **strategy-owned library functions, NOT engine primitives**.

**Our-strategies reality check**: None of s513/s523c/s524m/s532/s540 compute true Kelly. They compute VOL_TARGET with composite-weighted multipliers (ADV-sensitive kelly_mult × base_edge × vol_adj). What v4 calls "Kelly" is actually implicit vol-target sizing with a liquidity-indexed scale. v5 needs to preserve this SHAPE explicitly, not model it as an engine intent.

**v5 design**:

```python
# v5/sizing/intents.py — ENGINE contract (only 2 intents)
class SizingIntent(Enum):
    FIXED_FRACTION = "fixed_fraction"     # size = fraction × strategy_equity
    FIXED_NOTIONAL = "fixed_notional"     # size = explicit $

@dataclass
class SizingRequest:
    intent: SizingIntent
    fraction_of_equity: float | None = None
    notional_usd: float | None = None
    leverage: float = 1.0                  # ALWAYS explicit, never inferred

# v5/sizing/helpers.py — STRATEGY-side library (not engine)
def vol_target_fraction(target_vol_annual: float, realized_vol: float,
                        edge_weight: float = 1.0, vol_cap: float = 4.0) -> float:
    """Classic vol-target sizing. Strategy calls, passes result to SizingRequest."""

def kelly_fraction(edge: float, variance: float, kelly_mult: float = 0.25) -> float:
    """Fractional Kelly. Kelly_mult default 0.25 (industry: 0.25-0.50;
    full Kelly never used — see Thorp, MacLean-Thorp-Ziemba)."""

def composite_scaled_fraction(base_fraction: float, composite_score: float,
                              adv: float, adv_reference: float = 1e8) -> float:
    """Mirrors v4's composite-family sizing shape (s523c/s524m/s524r/s532/s540).
    Preserves ADV-sensitive scaling + composite-strength weighting.
    Direct migration path for the s524 strategy family."""
```

**Engine applies ONLY these named clamps** (each logs its binding constraint per fill for auditability):
1. **ADV cap**: `max_fill_notional = rolling_adv × config.adv_cap_pct` (market impact realism — preserves s524m pool-level `adv_cap_pct: 0.005`)
2. **Concentration limit**: `max_per_symbol = strategy_equity × config.concentration_limit`
3. **Free capital**: `max_margin_usd = state.available_margin`
4. **Min position size**: `config.min_position_usd` floor (skip below)
5. **Slippage**: `SqrtImpact(notional, adv)` on ADV participation (industry-correct, kept)

**Removed from v5 entirely** (both experts unanimous):
- `unrealized_pnl_floor=0.85` silent floor → **DELETE** (top-firm expert: "loss-masking device. Opposite of risk management. Looks like a bug that was codified."). Strategies wanting drawdown-aware sizing cuts write explicit logic: `frac *= max(0, 1 - dd/dd_limit)`.
- ADV-indexed Kelly curve (`adv_to_sizing()` function + its 9 shape params: `kelly_mult_floor/range/override/scale`, `cap_pct_floor/range/override/scale`, `adv_scaling_divisor`) → DELETE (strategies that want this call `composite_scaled_fraction()` helper explicitly)
- Silent `vol_adj` 4× multiplier → DELETE from engine; strategies that want vol scaling call `vol_target_fraction()` with explicit `vol_cap=4.0`
- `adv_sizing_enabled` sqrt secondary multiplier → DELETE (redundant with ADV cap)
- `size_multiplier`, `cap_multiplier`, `max_trade_pct` → DELETE all three (consolidate to strategy-computed `fraction` passed via `FIXED_FRACTION`)
- `dd_scaling` → already deleted per C-6 (moved to RiskEngine; but dd_scaling itself is unused so actually just DELETE)

**Per-fill transparency** — engine logs every fill with:
```
strategy=s524m token=BTC request=SizingRequest(FIXED_FRACTION, 0.05) leverage=3.0
  → equity=$100k → raw_margin=$5k → notional=$15k
  → adv_cap=$3k (BINDING) → concentration=$2.8k (BINDING)
  → filled_margin=$2.8k filled_notional=$8.4k slip=3.2bps
```

Strategy authors can predict sizing from their request and diagnose binding constraints.

**FIX alignment**: FIX client sends `OrderQty(38)` explicitly; venue does not silently mutate. v5's 2-intent contract mirrors this exactly — strategy declares quantity intent, engine applies named risk-engine clamps (FIX `MaxSize(210)`, `MaxNotional` analogs), no silent rewriting.

**Phase 4 parity gate (production risk control)**: When migrating s524m/s524r/s532/s540 from v4 → v5, each strategy must pass a parity test:
- Run v4/s524X vs v5/s524X (rewritten to use `composite_scaled_fraction()` helper) on same historical window
- Per-ClosedTrade margin_usd match within 0.1% (accounts for RNG reordering but not algorithmic drift)
- Metrics (Sharpe, Calmar, total return, MaxDD) within 0.5%
- Larger drift = investigate before migrating further. Especially load-bearing for s524m (1,094% backtest, live paper at $150K).

### V5 Data Architecture (NEW — addresses 2.3GB memory leak + fetcher fragmentation)

v4's data layer has two operational problems:
- **Memory growth to 2GB+**: documented `_context_cache` leak (2.3GB across 236 tokens × 2 markets × full-history indicator arrays), plus `TokenSignals` rebuilt from 12 months of history every paper tick (~2.4GB allocated per tick, GC pressure).
- **Fetcher fragmentation**: 831-line `PriceMonitor` with 3 parallel WebSocket streams (perp-1m/perp-1h/spot-miniTicker), separate `LiveFetcher` for REST, separate `MinuteExitCache` for backtest 1m parquet, ~25 `tools/fetch_*.py` cron scripts for alt data with NO live integration, hard-coded Binance URLs. No strategy-declared data needs (eagerly subscribes to every token). No live 1m→5m aggregation.

v5 replaces both with a FIX/Nautilus-aligned data engine. Module structure:

```
v5/data/
  __init__.py
  types.py           # BarType, BarSpec, InstrumentId, Tick, Bar, Subscription
  bus.py             # MessageBus (pub/sub, topic routing, wildcards)
  cache.py           # RollingCache — bounded deques per BarType (maxlen enforced)
  engine.py          # DataEngine — hub: strategies subscribe; clients register
  aggregators.py     # TimeBarAggregator, TickBarAggregator — internal BarType generation
  persistence.py     # HDB+RDB parquet merge (renamed v4/data_loader.py, now a DataClient)
  clients/
    base.py          # DataClient Protocol + LiveDataClient / HistoricalDataClient base classes
    binance_ws.py    # ONE class multiplexing 1m + 1h + spot miniTicker (replaces price_monitor.py)
    binance_rest.py  # ccxt-backed backfill + pagination (replaces live_fetcher.py)
    hyperliquid_ws.py  # example — extensibility demo
    parquet_replay.py  # BacktestDataClient — replays historical parquet as events
    tradingview.py     # example: external CSV/webhook adapter (same interface)
    alt_parquet.py     # ETF flows, TOTAL2/3 as periodic Bars (same interface)
```

#### Core abstractions

```python
# v5/data/types.py
@dataclass(frozen=True, slots=True)
class InstrumentId:
    symbol: str          # "BTCUSDT"
    venue: str           # "BINANCE"
    market: str          # "perp" | "spot"

@dataclass(frozen=True, slots=True)
class BarSpec:
    step: int                                   # 1, 5, 15, 60 ...
    unit: Literal["SECOND", "MINUTE", "HOUR", "DAY"]
    price: Literal["LAST", "MID", "MARK"] = "LAST"

@dataclass(frozen=True, slots=True)
class BarType:
    instrument: InstrumentId
    spec: BarSpec
    source: Literal["EXTERNAL", "INTERNAL"] = "EXTERNAL"  # INTERNAL = aggregator-produced

@dataclass(slots=True)
class Bar:
    bar_type: BarType
    ts_event: int        # epoch ns, close time
    open: float; high: float; low: float; close: float; volume: float

class Subscription(NamedTuple):
    bar_type: BarType
    handler: Callable[[Bar], None]
    warmup: int = 500   # min bars before handler starts firing
```

```python
# v5/data/clients/base.py
class DataClient(Protocol):
    name: str
    def supports(self, bar_type: BarType) -> bool: ...
    def subscribe(self, bar_type: BarType) -> None: ...
    def unsubscribe(self, bar_type: BarType) -> None: ...
    def request_bars(self, bar_type: BarType, start: int, end: int, limit: int) -> list[Bar]: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
```

#### Memory-bounded buffers (physically cannot leak)

```python
# v5/data/cache.py
class RollingCache:
    def __init__(self, max_bars_per_type: int = 2000):
        self._bars: dict[BarType, collections.deque[Bar]] = {}
        self._max = max_bars_per_type
    def on_bar(self, bar: Bar) -> None:
        dq = self._bars.get(bar.bar_type)
        if dq is None:
            dq = collections.deque(maxlen=self._max)   # CANNOT grow past maxlen
            self._bars[bar.bar_type] = dq
        dq.append(bar)
    def arrays(self, bar_type: BarType) -> BarArrays: ...  # views, not copies
```

`max_bars_per_type` is per-subscription; strategies needing longer history declare `warmup=N` explicitly. Unbounded growth is impossible by construction.

#### Strategy-declared subscriptions (replaces eager-subscribe-to-every-token)

```python
class Strategy:
    def required_data(self) -> list[Subscription]:
        return [
            Subscription(BarType(inst, BarSpec(1, "HOUR")), self.on_1h_bar, warmup=500),
            Subscription(BarType(inst, BarSpec(5, "MINUTE")), self.on_5m_bar, warmup=100),
        ]
```

Runner collects `required_data()` from every strategy in the portfolio, computes the UNION subscription set, hands it to `DataEngine.subscribe_all(...)`. Only the data actually needed is fetched.

#### Multi-source routing with fallback + aggregation

`DataEngine` holds a priority-ordered `list[DataClient]`. On `subscribe(bar_type)`:
1. Find clients where `client.supports(bar_type)` is True; pick highest priority (Binance-WS > Binance-REST > parquet-replay).
2. If no client supports `bar_type` natively, try INTERNAL aggregation: find a client with smaller `BarSpec` (e.g., 1m) and spawn a `TimeBarAggregator(source=1m, target=5m)` that subscribes upstream and publishes downstream as `source="INTERNAL"`.
3. If still unavailable, raise clear error at startup (not silent no-op).

#### WebSocket + REST + reconnect pattern

One `DataClient` per (venue, transport), sharing connection state:
```
subscribe(bar_type):
  1. REST.request_bars(end - warmup*step, end)    → seed cache
  2. WS.subscribe(bar_type)                        → live bars appended
  3. On WS disconnect: REST polling fallback      → same interface, strategies don't see disconnect
  4. On WS reconnect: REST.request_bars(last_seen, now) → gap fill
```

Strategies see one continuous stream; reconnect + backfill is entirely internal.

#### Memory fixes beyond the data architecture

- `@dataclass(slots=True)` on Position, ClosedTrade, TokenSignal, Bar — half the memory footprint
- `np.float32` for trailing multipliers, conviction scores, volume fields (precision not needed)
- `closed_trades` becomes `deque(maxlen=N)` in memory; older trades archived to parquet (dashboard reads from disk for history)
- Remove `dict(self._last_known_prices)` shallow-copy pattern in `PaperTickResult` — use immutable `MappingProxyType`
- Incremental signal computation: NO rebuild from 12 months of history per tick; indicators update as new bars arrive via RollingCache

#### Extensibility story (the win)

Adding a new data source = **one file** in `v5/data/clients/` implementing the `DataClient` protocol:
- TradingView CSV / webhook adapter → emits daily bars with `BarSpec(1, "DAY")` as `source="EXTERNAL"`
- Alt data (ETF flows, TOTAL2/3) → emits periodic Bars with custom fields
- New exchange (Hyperliquid, Kraken, Bybit) → own `xxx_ws.py` + `xxx_rest.py` files, registered in DataEngine

Strategies don't know or care which source the BarType came from. Same subscription model works for live, backtest, and alt data.

#### What v5 data architecture subsumes (consequential simplifications)

| Previously discussed item | Becomes free consequence of data arch |
|---|---|
| `bar_resolution` config (C-8) | Just a BarSpec on Subscription — no dedicated config field |
| Sub-hourly dispatch (Q4 wiring) | Just a 5-MINUTE Subscription; bar_processor handles any BarType |
| Paper vs backtest switch | Swap `LiveDataClient` for `ParquetReplayClient` — one config flag |
| `hist_cache` internalization (C-9) | `RollingCache` handles it natively; no parameter plumbing |
| ADV computation | Derived bar stream via `source="INTERNAL"` aggregator |
| TradingView / alt data integration | Client adapter — strategies subscribe same as any bar |

#### Operational priority

Memory leak fix is NOT a nice-to-have — v4 paper runs at $450K and currently grows to 2GB+ RAM requiring manual restarts. v5 MUST ship with bounded memory guarantees. This justifies the scope expansion.

### Paper State Migration (v1 → v2)

v5 paper_state schema changes are too large to roundtrip from v4. `STATE_VERSION` bumps to 2. Deserializing a v1 state file in v5 raises `ValueError` by default — migration is explicit.

**`v5/migrate_state_v1_to_v2.py`** — one-shot converter CLI:
```bash
python -m v5.migrate_state_v1_to_v2 \
    --input state/v4_paper_s513/state.json \
    --output state/v5_paper_s513/state.json
```

Transformations applied:
- Strip removed fields: `exit_regimes`, `exit_regimes_long`, `exit_regimes_short`, `regime_exit_min_bars`, `last_known_regimes`, `partial_tp_atr/pct/trail`, `partial_closed`
- Rename: `conviction` → `priority` (on any pending/armed entries)
- Translate `open_positions[]` + `armed_tokens{}` → unified `open_orders[]` with nested `legs[]`:
  - Active open position → `Order(legs=[Leg(status=WORKING)])`
  - Armed entry → `Order(legs=[Leg(status=ARMED, order_type="stop", trigger_price=...)])`
- Synthesize ClosedTrade identity fields for historical trades in `trades.jsonl`:
  - `parent_position_id = position_id.split(":")[0]`
  - `exec_seq = 1 if ":partial" in position_id else 0`
  - `exec_type = "reduce" if partial else "exit"`
  - `is_terminal = ":partial" not in position_id`
  - `triggered_by = ""`
- Split `partial_fills` counter: old value → `state.partial_fills + state.entry_scale_downs` estimated split via audit log replay if available, else dump total to `partial_fills` (documented imprecision)
- `version: 2`

**Use cases**:
- Live paper restart after v5 deploy → run migrator on state files, restart with `v5/run_paper_multi.py`
- Active positions preserved; armed entries preserved with carry-forward to v5's Order model
- ~60 min of armed_log.jsonl history survives (v5 reads v1 log format + writes v2 going forward)

**Alternative**: `--reset-state` flag on v5 paper runner = clean start, loses active positions + armed history. Acceptable for paper trading (rebuildable) but avoid if at all possible.

### Dashboard Rework

v5 changes ripple into dashboards (`v4/dashboard_state.py`, `v4/paper_engine.py:to_dashboard_sim`, `tools/generate_dashboard_v2.py`). Changes needed:

- **Per-token regime display REMOVED entirely** — not needed. Simplifies dashboard significantly: drop `regime` column from position table, drop `regime_names` dict, drop `_last_known_regimes` tracking, drop `exit_regimes` display, delete `regime`/`exit_regimes` fields from `to_dashboard_sim` output. No dashboard-side regime computation either.
- **Strategy-view hook** — strategies that carry their own internal state (e.g., halving-cycle gates, BTC-donchian signals, custom risk views) can optionally expose `Strategy.view_state() -> dict`. Dashboard renders the returned dict as key/value in the strategy tile. Default implementation returns `{}`. Use sparingly; dashboard shouldn't become a strategy-state dumping ground.
- **`consolidate_partial_trades` deleted** — the `position_id:partial` suffix-parsing band-aid (`dashboard_state.py:27-83`) becomes trivial: group by `parent_position_id`, emit one row per parent with `legs[]=exec_seq-ordered`. Net code REMOVAL.
- **New scaling counters surfaced** — per-strategy breakdown shows `increase_fills`, `partial_fills` (reduce-only), `contingent_fills`, `entry_scale_downs` instead of the v4 single conflated `partial_fills`.
- **Scaling events timeline** — new per-position detail view shows `pos.scaling_events` as a timeline (size ups/downs, pyramiding rungs) — valuable operator signal.
- **Armed orders view** — reads from unified `open_orders` list (previously `armed_tokens` dict). Same visual, cleaner source.
- **Exit reason display** — reads new `triggered_by` for short linkage display + `exit_reason` for human description.
- **Position table columns** — DROP: `regime`, `exit_regimes`. ADD: `scale_count`, `leaves_qty`, `r_anchor_price`, `has_scaling`.

### Regime & CRISIS exit migration (v5 architectural cleanup)

**v4 pattern** (engine-coupled):
```python
# Engine computes bar.regime; strategies check it
def _sharpe_exit(pos, bar):
    if pos.bars_held > 6 and bar.regime == 0:   # CRISIS
        return ExitCheck(should_exit=True, reason="crisis")
```

**v5 pattern** (strategy-owned):
```python
from v5.regimes import detect_crisis

class Strategy:
    def check_exit(self, pos, bar_ctx):
        if pos.bars_held > 6 and detect_crisis(bar_ctx):
            return ExitCheck(should_exit=True, reason="crisis")
```

- `v5` engine does not compute regime, does not expose `bar.regime`
- `v5/regimes.py` is an OPT-IN utility library (same detectors, just not forced)
- Strategies that want custom crisis definitions (e.g., BTC-price-based vs universe-dispersion-based) write their own
- Matches industry pattern (NautilusTrader, Backtrader, QuantConnect don't have engine-level regime)
- Migration touch-point: per strategy, replace `bar.regime == K` with `detect_X(bar_ctx)` call — mechanical

### Unified Strategy API (Option Y)

v5 replaces the `per_token` / `portfolio` distinction with a **single universe-style API** matching NautilusTrader/Backtrader/QuantConnect convention. A `Strategy` is a class with methods, not a set of callback hooks on a StrategySpec:

```python
class Strategy(Protocol):
    """v5 strategy protocol. Strategies implement at minimum generate();
    others are optional with sensible no-op defaults."""

    def generate(self, universe_ctx: UniverseContext, bar_idx: int) -> UniverseSignals:
        """Called per bar to generate ENTRY signals across the universe."""
        ...

    def check_scale(self, pos: Position, bar_ctx: BarContext) -> ScaleAction | None:
        """Called per (open position, bar) to check if position should be scaled.
        Default: None (no scaling). Override in strategies that use increase/reduce."""
        return None

    def check_exit(self, pos: Position, bar_ctx: BarContext) -> ExitCheck | None:
        """Called per (open position, bar) for strategy-custom exit logic.
        Default: None (rely on standard exit chain). Override for CRISIS exit, etc."""
        return None

    def filter_entry(self, candidate, bar_ctx: BarContext) -> bool:
        """Called per candidate entry for strategy-level filtering.
        Default: True (accept all). Override for custom entry gates."""
        return True
```

- `UniverseContext` — **lazy** accessor (Option b) for all tokens at the current bar. `ctx.per_token("BTC")` materializes a TokenContext on demand; unused indicators aren't computed.
- `UniverseSignals` — cross-sectional signal set; `{token: TokenSignal}` dict returned by the strategy.
- Per-symbol-style strategies iterate tokens in the universe, make independent decisions. Portfolio-style strategies rank across tokens and return weighted allocation. SAME API.
- `strategy_type` config field REMOVED — every strategy is just "a strategy".
- `scale_check_fn`, `exit_check_fn`, `entry_filter_fn` callback fields on StrategySpec are REMOVED — replaced by `check_scale`, `check_exit`, `filter_entry` methods on the Strategy class.

**Why methods instead of callback fields**:
- Strategy is ONE cohesive object — all its behavior lives in one class
- Default implementations (None, True) mean strategies that don't scale/customize-exit don't need to mention those methods
- Natural place for strategy-level state (`self._regime_cache`, `self._helper_fired_levels`)
- Matches NautilusTrader's `Strategy.on_event()` pattern
- Cleaner composition with helpers: `def check_scale(self, pos, bar): return tp_ladder_atr([(2,0.3)])(pos, bar)`

**Migration cost per v4 strategy**:
- Simple per_token (s513-like): ~30 min — wrap existing logic in `for token in universe_ctx.tokens:` loop, return dict
- Complex per_token (s523c): 1-2h
- Portfolio strategies (s524m): minimal — API-shape change only (they're already cross-sectional)
- Strategies using `exit_check_fn` (s524m, s523c, s513, s532, s540 for CRISIS): migrate callback → `check_exit` method; replace `bar.regime == 0` with `detect_crisis(bar_ctx)` — mechanical per-strategy

Migrations happen ONE AT A TIME when user decides; v4 keeps running existing strategies until then.

### Unified Dispatcher (Scope B — `v5/bar_processor.py`)

Single class encapsulates Phase 1/2/3 per-bar logic:

```python
class BarProcessor:
    def process_bar(self, pos, bar_ctx, global_bar) -> ProcessResult:
        # Phase 1: handler.update_state
        # Phase 2: scale_check_fn → Position.increase / Position.reduce
        # Phase 3: check_exit handler chain
```

- `v5/simulator.py` hourly loop: iterate hourly bars, build hourly BarContext, call `bar_processor.process_bar(...)`
- Sub-hourly: feed 5-min BarContexts to the SAME bar_processor (no separate dispatcher)
- `v5/paper_engine.py` per-tick: build BarContext from live tick, call `bar_processor.process_bar(...)`
- Adding 4h/daily resolution = additive (just feed 4h bars; no new code path)

### Position Primitives (New)

- `Position.increase(qty_to_add, fill_price, margin_delta, bar_idx, stop_override=None)` — absolute-base-units add, updates VWAP entry_price
- `Position.reduce(qty_to_close, fill_price, bar_idx, fee_rate, atr, full_entry_fee, dust_usd, triggered_by="") -> ReduceResult` — state-free domain method returning ReduceResult bundle for simulator wrapper to book
- `Position.reduce_fraction(fraction, ...)` — thin convenience wrapper
- `Position.leaves_qty` property — `abs(self.quantity)` (FIX `LeavesQty(151)` analog)
- `Position.is_closed` property — `self.quantity == 0.0` exactly (Nautilus `position_side == FLAT` analog)
- `Position.r_anchor_price` field — frozen at first entry (Q-DEC1 for tp_ladder_r)
- `Position._helper_state: dict` field — per-position helper fire-state storage (Q-DEC3)
- `Position._scale_action_bar: int` field — per-hourly-bar invocation cap (C2)
- Layered notional dust threshold: `dust_usd = max(min_close_notional_usd, dust_fraction_of_min_position × min_position_usd)` — FIX-aligned, no float epsilons

### Strategy-Facing API for scaling

- `Strategy.check_scale(pos, bar_ctx) -> ScaleAction | None` — method on Strategy Protocol (not a callback field)
- `ScaleAction(qty_delta, reason, stop_override=None)` — signed in position-direction units; lives in `v5/strategy_api.py`
- `LinkedScalePolicy` enum (INDEPENDENT default, PROPORTIONAL/ABSOLUTE opt-in) on StrategySpec; lives in `v5/config.py`
- Helpers in `v5/helpers.py`: `tp_ladder_atr`, `tp_ladder_price`, `tp_ladder_r` (with `anchor="original"` default per Q-DEC1), `breakeven_plus_runner`, `add_at_price`, `add_on_profit_atr`, `confirm_and_add`, `combine`

**Usage example**:
```python
from v5.strategy_api import Strategy
from v5.helpers import tp_ladder_atr

class MyStrategy(Strategy):
    def generate(self, universe_ctx, bar_idx):
        # entry signals...
        return UniverseSignals(token_signals=...)

    def check_scale(self, pos, bar_ctx):
        # Simple composition with helper
        return tp_ladder_atr([(2.0, 0.3), (4.0, 0.3), (6.0, 0.2)])(pos, bar_ctx)
```

### ClosedTrade Identity (v5 canonical)

New fields baked in from v5 start (not bolted on):
- `parent_position_id: str` (FIX `OrderID(37)` analog)
- `exec_seq: int` (FIX `ExecID`-derived sequence)
- `exec_type: str` ("reduce" | "exit" | "linked_reduce"; "open"/"increase" reserved)
- `is_terminal: bool` (FIX `OrdStatus=Filled(2)` analog)
- `triggered_by: str` (FIX `ExecRestatementReason(378)` analog; set only by linked-leg propagation)

### Paper Engine Parity

- v5 paper engine wires `scale_check_fn` via the same `bar_processor` used in backtest (single dispatcher, no special paper path)
- `linked_scale_policy=INDEPENDENT` fully supported
- Defensive runtime check at startup: raise NotImplementedError if any spec has `linked_scale_policy != INDEPENDENT` (deferred to follow-up spec; async broker timing for auto-propagation needs careful design)

### Backward Compat With v4 Artifacts

- Historical `analysis/*.json` trade logs (written by v4) remain loadable via `v5/report.py:load_trade_log()` compat parser — maps legacy `:partial` suffix to new identity fields
- v4 paper_state files are NOT directly loadable by v5 (different schema, different strategy API) — each strategy migration creates a fresh v5 paper_state
- v4 stays running; v4 paper_state continues to work in v4

### Deferred (separate specs later)

- Migrating v4 strategies to v5 (one-at-a-time; user decides timing per strategy)
- Paper-side `linked_scale_policy=PROPORTIONAL`/`ABSOLUTE` auto-propagation (async broker timing design)
- Multiple scale actions per bar (C2 cap enforces one per bar)
- Schedule-based declarative rules beyond the shipped helpers
- 4h/daily exit_resolution support (additive on bar_processor once a strategy needs it)
- Eventual deprecation of v4 after all strategies migrate

## Fill Price

Engine uses current bar's price for fills (same pattern as existing `_partial_close_position` we're replacing):
- Hourly: `bar.close`
- Sub-hourly (`exit_resolution > 0`): sub-hour's price
- Slippage applied to fill price via existing slippage model
- Strategy does NOT pass price — only specifies `qty_delta` (signed, position-direction units)
- Engine translates: `qty_delta > 0` → `Position.increase(qty_to_add=qty_delta)`; `qty_delta < 0` → `Position.reduce(qty_to_close=abs(qty_delta))`

## Acceptance Criteria

### Add side

1. **AC1 — Position.increase method** (FIX/Nautilus-aligned naming): `Position.increase(qty_to_add: float, fill_price: float, margin_delta: float, bar_idx: int, stop_override: float | None = None)`:
   - `qty_to_add` is **unsigned, in absolute base units** (matches FIX `LastQty(32)` and Nautilus `last_qty` semantics)
   - Engine translates `ScaleAction(qty_delta=+X)` (signed, position-direction) → `increase(qty_to_add=X)`
   - Direction inferred from `pos.direction`; signed quantity update: `pos.quantity += pos.direction * qty_to_add`
   - New `entry_price` = VWAP: `(abs(pos.quantity_before) × pos.entry_price + qty_to_add × fill_price) / (abs(pos.quantity_before) + qty_to_add)`
   - `pos.margin_usd += margin_delta`
   - Appends `ScalingEvent(bar=bar_idx, kind="increase", fill_price=..., qty_delta=+qty_to_add, margin_delta=margin_delta, ...)` to `pos.scaling_events`
   - Does NOT book a ClosedTrade (open position only — see AC35)

2. **AC2 — stop_price on increase is strategy-controlled with never-loosen default**:
   - `ScaleAction.stop_override: float | None` (passed through to `Position.increase(..., stop_override=...)`)
   - If provided: `pos.stop_price = stop_override` (strategy has full control)
   - If None: engine default = never-loosen:
     - Long: `pos.stop_price = max(pos.stop_price, new_VWAP - stop_mult × atr_at_event)`
     - Short: `pos.stop_price = min(pos.stop_price, new_VWAP + stop_mult × atr_at_event)`
   - Rationale: strategies have different risk philosophies (pyramiding vs averaging down); engine doesn't prescribe.

3. **AC3 — initial_risk FROZEN, entry_price = VWAP, earned state preserved**:
   - `entry_price` = new VWAP (standard cost-basis for PnL)
   - `initial_risk` = **NOT modified by increase** (frozen at entry-time value)
   - Rationale: institutional practice preserves original risk budget. Circuit breaker, breakeven ratchet, TakeProfit all use `initial_risk` — keeping it frozen preserves semantics.
   - Preserved across increase:
     - `pos.breakeven_triggered` — stays True if already earned
     - `pos.highest` / `pos.lowest` — trailing peaks continue tracking
     - `pos.initial_risk` — frozen
   - Handler behavior:
     - CircuitBreaker: `entry_price ± cb_r × initial_risk` = new VWAP ± original risk budget (sensible loss cap)
     - TakeProfit: new VWAP-relative target (natural)
     - Breakeven: `be_profit_atr = abs(bar.close - new_VWAP) / atr` from VWAP (correct)
   - Strategy override: `stop_override` lets strategy set any stop (dollar-risk preservation, tighter-on-pyramid, etc.)

### Reduce side

4. **AC4 — Position.reduce method** (FIX `LastQty(32)` / Nautilus `last_qty` aligned, ReduceResult-bundle pattern per Q2):

   **Signature** (state-free for testability — booking happens in simulator wrapper):
   ```python
   @dataclass
   class ReduceResult:
       closed_qty_signed: float             # signed in pos-direction units (sign matches pos.direction)
       closed_qty_abs: float                # absolute |closed_qty_signed|
       closed_margin: float                 # margin pro-rata removed from position
       closed_funding: float                # cumulative_funding pro-rata removed (becomes ClosedTrade.funding_cost)
       partial_entry_fee_to_book: float     # → ClosedTrade.entry_fee
       partial_entry_fee_remaining: float   # → state._entry_fees_by_pos[pos_id] new value
       exit_fee: float                      # → ClosedTrade.exit_fee (on closed notional, taker rate)
       slip_bps: float                      # → ScalingEvent.slippage_bps (computed against ADV slippage model)
       gross_pnl: float                     # closed_qty_signed × (fill_price - entry_price); for state.realized_pnl tracking
       is_terminal: bool                    # True if this reduce promoted to full close (AC14)
       suffix: str                          # ":scale_N" or ":scale_N_final"

   class Position:
       def reduce(self, qty_to_close: float, fill_price: float, bar_idx: int,
                  fee_rate: float, atr_at_event: float,
                  full_entry_fee: float, dust_usd: float,
                  triggered_by: str = "") -> ReduceResult: ...
   ```
   **Field rationale (C1)**: `gross_pnl` and `exit_fee` are needed by the simulator wrapper to update `state.realized_pnl` and `state.total_fees` (matches `_close_position` math at simulator.py:222-243). `slip_bps` populates `ScalingEvent.slippage_bps` (AC13). `closed_qty_signed` avoids the wrapper having to recompute sign from `pos.direction` after the mutation (`pos.quantity` already updated when wrapper sees ReduceResult).

   - `qty_to_close`: **unsigned, in absolute base units** (FIX-aligned primitive)
   - `triggered_by`: parent exec_id when auto-propagated (e.g., linked-leg per AC36); empty for strategy-driven
   - Internal close_fraction (for prorating margin/funding/fees): `close_fraction = qty_to_close / abs(pos.quantity)`
   - `pos.quantity = (1.0 - close_fraction) * pos.quantity` — sign-safe for longs and shorts (with AC22 assignment for terminal)
   - `pos.margin_usd *= (1.0 - close_fraction)`
   - `pos.cumulative_funding *= (1.0 - close_fraction)` (the BEFORE-multiplication funding amount × close_fraction is returned in `ReduceResult.closed_funding`)
   - **Returns** `ReduceResult` for the simulator wrapper to book a `ClosedTrade` with `exec_type="reduce"`
   - Pro-rated entry_fee logic (returned in `ReduceResult`, applied by wrapper):
     ```python
     partial_entry_fee = full_entry_fee * close_fraction
     # ReduceResult.partial_entry_fee_to_book = partial_entry_fee
     # ReduceResult.partial_entry_fee_remaining = full_entry_fee - partial_entry_fee
     ```
   - NOT one-shot — can be called multiple times
   - Append `ScalingEvent(kind="reduce", fill_price=..., qty_delta=-qty_to_close, ...)` to `pos.scaling_events`
   - ScaleAction translation (in simulator wrapper): `ScaleAction(qty_delta=-2.0)` on 5-unit position → `reduce(qty_to_close=2.0)`
   - **Convenience wrapper** also provided: `Position.reduce_fraction(fraction, fill_price, bar_idx, fee_rate, atr_at_event, full_entry_fee, dust_usd, triggered_by="") -> ReduceResult` — internally calls `reduce(qty_to_close=abs(self.quantity)*fraction, ...)`. For strategies that prefer percentage thinking; the absolute primitive is the canonical API.

   **Simulator wrapper** (in `simulator.py`, ~25 lines) handles the booking. **Dust-promoted terminal reduces delegate to `_close_position` for the actual close (Q-DEC5)** — ensures ONE terminal-close math path across dust-promotion and exit-chain closures:
   ```python
   def book_reduce(state, config, pos, qty_to_close, fill_price, bar_idx, sig, triggered_by=""):
       fee_rate = get_fee_rate(config.exchange, sig.market, "taker")
       atr = sig.atr[bar_idx]
       full_entry_fee = state._entry_fees_by_pos.get(pos.position_id, 0.0)
       dust_usd = max(config.min_close_notional_usd,
                      config.dust_fraction_of_min_position * config.min_position_usd)
       result = pos.reduce(qty_to_close, fill_price, bar_idx, fee_rate, atr,
                            full_entry_fee, dust_usd, triggered_by)

       if result.is_terminal:
           # Q-DEC5: Delegate to existing terminal-close path with overrides —
           # ONE terminal-close math path (FIX one-ExecutionReport canon).
           # _close_position is extended to accept position_id_override and exec_type.
           _close_position(state, pos, fill_price,
                           exit_reason="dust_promoted_reduce",
                           adv=sig.adv[bar_idx], config=config,
                           position_id_override=f"{pos.position_id}{result.suffix}",
                           exec_type="reduce",
                           triggered_by=triggered_by)
           # _close_position handles: fees/slippage/funding booking, pos removal from open_positions,
           # state.realized_pnl/total_fees updates, linked-leg full-close propagation per sim:522-529
       else:
           # Non-terminal reduce — book the partial ClosedTrade directly
           pos.scale_count += 1
           try:
               closed = ClosedTrade(
                   position_id=f"{pos.position_id}{result.suffix}",
                   parent_position_id=pos.position_id,
                   exec_seq=pos.scale_count, exec_type="reduce",
                   is_terminal=False, triggered_by=triggered_by,
                   entry_fee=result.partial_entry_fee_to_book,
                   exit_fee=result.exit_fee,
                   funding_cost=result.closed_funding,
                   pnl=result.gross_pnl - result.exit_fee - result.closed_funding,
                   ...)
               state.position_manager.closed_trades.append(closed)
               state._entry_fees_by_pos[pos.position_id] = result.partial_entry_fee_remaining
               state.total_fees += result.exit_fee
               state.realized_pnl += closed.pnl
               state.partial_fills += 1  # Q1: reduce-only semantic
           except Exception:
               pos.scale_count -= 1  # Q4 rollback
               raise
   ```

   **`_close_position` API extension (Q-DEC5)**: add optional parameters `position_id_override: str | None = None`, `exec_type: str = "exit"`, `triggered_by: str = ""`. Default behavior unchanged (non-scaling closures use `pos.position_id` and `exec_type="exit"`). Dust-promoted reduces pass overrides for the suffix scheme per AC4.

   **Why ReduceResult, not state-passing (Q2)**: matches NautilusTrader's `Position.apply(fill)` pattern (domain logic isolated from booking). Critical for testability — T1-T5 (accounting invariant tests) can be written at Position level WITHOUT constructing SimulationState/PortfolioConfig fixtures. Simulator wrapper is small enough that the seam is low-risk.
   - **ClosedTrade.position_id uses industry-standard parent:child scheme** (FIX OrderID/ExecID, vectorbt Position Id/Trade Id, Nautilus position_id/trade_id):
     - Pattern: `f"{pos.position_id}:scale_{n}"` where `n ∈ [1, 2, 3, ...]`
     - Counter stored on Position: `pos.scale_count: int` (default 0)
     - Increment atomically BEFORE booking the ClosedTrade (prevents race if scaling_events append happens later)
     - **Rollback on failure (Q4)**: if ClosedTrade booking raises (e.g., constraint violation surfaces late, or a downstream invariant fails), DECREMENT `pos.scale_count` in the except handler so subsequent successful reduces produce a contiguous sequence. Pattern:
       ```python
       pos.scale_count += 1
       try:
           closed = ClosedTrade(position_id=f"{pos.position_id}:scale_{pos.scale_count}", ...)
           state.position_manager.closed_trades.append(closed)
       except Exception:
           pos.scale_count -= 1
           raise
       ```
   - **Terminal close suffix** (mirrors FIX OrdStatus=2 — "last slice" queryable without groupby):
     - If reduce promotes to full close per AC14 dust threshold: `f"{pos.position_id}:scale_{n}_final"`
     - If close triggered by normal exit chain after prior reduces: `pos.position_id` (unchanged — current convention)
   - **Invariant (I1)**: all `ClosedTrade.position_id` values within a run are unique (assert at book time, Nautilus-style strict uniqueness). Specifically: each parent has exactly ONE ClosedTrade with `position_id == parent_position_id` (the unsuffixed terminal exit-chain close), plus N ClosedTrades with `position_id == f"{parent}:scale_{n}"` (one per non-terminal reduce), plus optionally ONE with `position_id == f"{parent}:scale_{n}_final"` (only if reduce promoted to full close per AC14, in which case there is NO unsuffixed terminal entry).
   - Legacy `":partial"` suffix from `_partial_close_position` is retired with `PartialTPHandler` deletion (AC12)

5. **AC5 — entry_price unchanged on reduce**: VWAP stays the same on reduction; only quantity/margin/cumulative_funding shrink.

### Hook

6. **AC6 — `Strategy.check_scale()` method** (Strategy Protocol in v5/strategy_api.py):
   - Signature: `def check_scale(self, pos: Position, bar_ctx: BarContext) -> ScaleAction | None`
   - Default implementation returns `None` (strategies that don't scale don't override)
   - `ScaleAction(qty_delta: float, reason: str, stop_override: float | None = None)` — positive qty_delta = add (routed to `Position.increase`), negative = reduce (routed to `Position.reduce`)
   - `bar_processor` calls it each bar per open position in Phase 2 (AC18)
   - Returning `None` means no action this bar
   - Runs in a `try/except` inside bar_processor — exceptions logged but don't crash simulation
   - Strategy-level state (helper fired sets, regime cache, etc.) lives on `self._helper_state` / strategy instance fields

### Accounting correctness

7. **AC7 — fees booking**:
   - Each fill incurs entry/exit fees via existing fee schedule (`get_fee_rate(exchange, market, "taker")`)
   - Added to `state.total_fees`
   - **`Position.increase`**: `state._entry_fees_by_pos[pos_id] += new_entry_fee`
   - **`Position.reduce`**: pro-rate and decrement (matches existing `_partial_close_position` lines 338-340):
     ```python
     close_fraction = qty_to_close / abs(pos.quantity_before)
     partial_entry_fee = full_entry_fee * close_fraction   # → ClosedTrade.entry_fee
     state._entry_fees_by_pos[pos_id] = full_entry_fee - partial_entry_fee
     ```
   - Exit fee on `reduce` goes to booked ClosedTrade's `exit_fee`
   - Final full-close uses remaining bucket balance (no double-counting)

8. **AC8 — slippage**: Each fill applies ADV-based slippage via `state._slippage_models[strategy_id]`:
   - Slippage computed on fill notional (not total position)
   - Adjusts fill_price: longs pay up on `increase`, down on `reduce`; shorts opposite
   - Slippage bps recorded in `ScalingEvent` for diagnostics

9. **AC9 — funding pro-rating** (mirror existing `_partial_close_position` at simulator.py:330, 368):
   - **`Position.reduce`** splits cumulative_funding by `close_fraction = qty_to_close / abs(pos.quantity_before)`:
     ```python
     closed_funding = pos.cumulative_funding * close_fraction      # → ClosedTrade.funding_cost, deducted from net_pnl
     pos.cumulative_funding = pos.cumulative_funding * (1.0 - close_fraction)  # remainder stays on position
     ```
     **Invariant (QC1 — revised for interleaved increase/reduce)**: at any instant,
     ```
     sum(booked_funding in ClosedTrades for this parent_position_id) + pos.cumulative_funding
        == sum(all per-bar funding accruals to date on this position)
     ```
     This holds REGARDLESS of whether increases interleave with reduces (the pre-reduce funding gets its pro-rata share booked; post-increase accruals add to `pos.cumulative_funding` on the new larger size). The original "sum of all closed + final = total paid" invariant only held for pure-reduce-sequence; this one is correct for ALL sequences.
     T1 (pure reduces) and T1b (interleaved increase+reduce) both must verify this.
   - **`Position.increase`**: past `cumulative_funding` UNCHANGED.
     - Don't retroactively dilute it across new quantity (the historical cost was on pre-add size)
     - Next funding settlement accrues on the **post-increase `pos.quantity` for the FULL settlement period** — matches Binance/Bybit perp funding semantics (funding is a snapshot at settlement time, NOT time-weighted within the period). So an `increase` at hour 7 of an 8h funding period → the new larger position pays/receives funding on the full size at hour 8 settlement (Q3 — explicit, prevents misreading "no immediate event" as "increase has no funding implication")
     - No immediate funding event booked on `increase` itself
   - **Final full close**: remaining `cumulative_funding` flows to final ClosedTrade's `funding_cost` (matches non-scaled behavior)

### Portfolio constraints (`increase` only — `reduce` always allowed)

10. **AC10 — constraints on `Position.increase`** (match existing entry semantics):
    - **ADV cap**: checked on **the increase's notional only** (per-fill), matching existing behavior at simulator.py:1447:
      ```python
      add_notional = qty_to_add * fill_price
      if add_notional > adv_val * config.adv_cap_pct:
          # SKIP the increase (silent skip, state.rejections.adv_cap += 1)
      ```
      Rationale: ADV cap is a market-impact constraint on each execution, not cumulative. A strategy that wants cumulative-cap discipline should check total size itself before requesting.
    - **Concentration**: cumulative — subtracts existing position margin from cap (matches simulator.py:1460):
      ```python
      existing_margin = state.position_manager.total_margin_for_token(token)
      max_for_token = config.concentration_limit * portfolio_eq - existing_margin
      if margin_delta > max_for_token:
          if max_for_token < config.min_position_usd:
              # SKIP (state.rejections.concentration += 1)
          else:
              # SCALE DOWN: margin_delta = max_for_token, qty_to_add recomputed from fill_price
      ```
      Match existing scale-down behavior. Strategy's requested qty may be reduced.
    - **Min increase size**: `qty_to_add * fill_price >= config.min_position_usd` — skip if too small
    - **Margin availability**: `margin_delta <= sizing_eq - state.total_margin_used` (standard portfolio margin check)
    - **All skips are silent** (increment `state.rejections.*` counters, not raise exceptions)
    - Constraints SKIPPED in raw mode per AC19

### Compatibility

11. **AC11 — v4 is unaffected** (trivially satisfied by fork model):
    - v4/ is untouched. All v4 strategies, v4 paper trading, v4 backtest results remain byte-identical.
    - v5 is independent. Migration of a strategy from v4 → v5 is a rewrite (per Option Y) and produces its OWN v5 results. Not expected to be bit-exact with v4 (different API, different dispatcher, different signal path).
    - **v5 internal consistency**: multiple runs of the same v5 strategy with the same seed must produce bit-exact results. FIX-grade replay equality (`PossDupFlag(43)` convention) applies within v5.
    - **Reference strategy validation**: after v5 ships, migrate s524m as reference. Run v4/s524m vs v5/s524m on the same historical window. Expect metrics within ≤0.5% Sharpe/Calmar/total-return (genuine API/dispatcher differences; not bit-exact). Any larger divergence = investigate before migrating more strategies.

12. **AC12 — partial_tp ABSENT from v5** (trivially satisfied by fork):
    - v5 starts WITHOUT `partial_tp_atr`, `partial_tp_pct`, `partial_tp_trail`, `partial_closed` fields on Position/StrategyResult/TokenSignals. Not "deleted" — never copied over.
    - v5 has no `PartialTPHandler` class.
    - v5 has no `_partial_close_position` function. `Position.reduce` + `scale_check_fn` + `scaling.py` helpers ARE the partial-close mechanism.
    - v4 strategies using `partial_tp_atr` (s502, s76_s56, s524j, s501, s503) stay in v4 and continue using v4's partial_tp. When/if they migrate to v5, the migration rewrites them to use `tp_ladder_atr()` helper.
    - No migration pressure from this spec; happens per-strategy later.

### Observability

13. **AC13 — trade log captures scaling events**:
    - `Position.scaling_events: list[ScalingEvent]` (dataclass with typed fields):
      ```python
      @dataclass
      class ScalingEvent:
          bar: int
          kind: str  # "increase" or "reduce" (FIX-aligned vocabulary)
          fill_price: float
          qty_delta: float                # signed actual: + on increase, - on reduce
          requested_qty_delta: float      # signed requested by strategy BEFORE AC10 constraint clamping (I3)
          margin_delta: float             # signed actual: + on increase, - on reduce
          fill_notional: float            # absolute |qty_delta| * fill_price
          entry_fee_delta: float          # > 0 on increase
          exit_fee: float                 # > 0 on reduce
          slippage_bps: float
          atr_at_event: float
      ```
    - For un-clamped events: `requested_qty_delta == qty_delta`. For AC10-clamped increases: `abs(requested_qty_delta) > abs(qty_delta)`. For all `reduce` events (no AC10 constraints): always equal.
    - **`requested_qty_delta` vs `qty_delta`**: when AC10's concentration scale-down clamps an `increase`, `requested_qty_delta` records the strategy's original intent and `qty_delta` records what actually executed. For un-clamped events, the two are equal. Critical for post-hoc analysis ("did my strategy under-add because it WANTED to, or because concentration choked it?").
    - Final `ClosedTrade.scaling_events` = copy of position's events at close time
    - Booked reduce ClosedTrades have `exec_type="reduce"` (per AC29/AC30 — `exit_reason` is orthogonal and stays as the granular cause)
    - `ClosedTrade` has new `has_scaling: bool` flag for analysis filtering (True if any prior reduce/increase happened on this position)

### Edge cases

14. **AC14 — reduce to zero with notional dust threshold** (FIX/Nautilus-aligned, no float epsilons):
    - **NEW config knobs** (added to `v4/config.py`, additive — safe per blast-radius rules):
      ```python
      min_close_notional_usd: float = 1.0           # absolute floor (~exchange MIN_NOTIONAL)
      dust_fraction_of_min_position: float = 0.05   # relative floor (5% of min_position_usd)
      ```
    - Effective dust threshold (computed at Position level): `dust_usd = max(config.min_close_notional_usd, config.dust_fraction_of_min_position * config.min_position_usd)`. At default `min_position_usd=200`: `dust_usd = max($1, $10) = $10`.
    - **Promotion to full close** (inside `Position.reduce`):
      ```python
      remaining_qty = abs(self.quantity) - qty_to_close
      remaining_usd = remaining_qty * fill_price
      if remaining_usd < dust_usd:
          qty_to_close = abs(self.quantity)   # promote
      ```
    - **After promotion, set `pos.quantity = 0.0` by ASSIGNMENT** (NOT `pos.quantity *= (1 - close_fraction)` arithmetic) — composes with AC22 sign invariant (C3). This applies to both the dust-promotion path and the normal terminal path. Rationale: float arithmetic from accumulated prior reduces can leave residual `1e-14`-scale dust even when the math should yield exact zero.
    - After assignment, exact-zero check determines terminal status: `is_terminal = (self.quantity == 0.0)` (Nautilus-style — no float eps).
    - Terminal close books ClosedTrade with `exec_type="reduce"`, `is_terminal=True`, suffix `:scale_{n}_final` (per AC4)
    - Remove position from `open_positions` when `is_closed` (i.e., `quantity == 0.0`)
    - No orphan zero-quantity positions in `open_positions`
    - **Defense-in-depth**: separate from reduce-promotion, after ANY scale event the engine asserts `pos.is_closed or pos.leaves_qty * fill_price >= dust_usd` (catches programming errors)

15. **AC15 — no force-close after increase**: `Position.increase` does NOT force-close even if new stop is already breached.
    - StopLossHandler in Phase 3 sees updated stop, fires naturally against `bar.low`/`bar.high` with `reason="stop"`
    - Correct fill semantics (slippage, fees, reason attribution) via normal exit path

16. **AC16 — trailing stop state preserved**: `pos.highest` / `pos.lowest` continue tracking across `Position.increase`. They represent "best excursion for the position's life", not per-add.

17. **AC17 — `qty_delta` sign convention and over-close clamp** (I5 — single rule, no opposing-direction framing):
    - **One rule, position-direction units**: `ScaleAction.qty_delta` is signed in **position-direction units** (NOT world frame):
      - `qty_delta > 0` → grow `|pos.quantity|` → routed to `Position.increase(qty_to_add=qty_delta)`
      - `qty_delta < 0` → shrink `|pos.quantity|` → routed to `Position.reduce(qty_to_close=abs(qty_delta))`
      - `qty_delta == 0` → no-op (engine logs warning; strategies should return `None` instead)
    - **Same rule for longs and shorts**: a long with `direction=+1` and `quantity=+5` calling `qty_delta=-2` reduces to `+3`. A short with `direction=-1` and `quantity=-5` calling `qty_delta=-2` reduces to `-3` (toward zero). The CALLER never deals with signed world-frame quantities; they always express intent as "grow my position by N" or "shrink my position by N".
    - **Over-close clamp**: if `qty_delta < 0` and `abs(qty_delta) >= abs(pos.quantity)`, internally clamp to `qty_to_close = abs(pos.quantity)` (full close — see AC14 dust promotion + AC22 sign invariant for terminal handling)
    - **Dust-promotion**: AC14's `dust_usd` threshold handles "almost-full" reduce inside `Position.reduce` itself — no separate eps needed
    - **No direction flip via scaling**: a strategy wanting to flip long→short must close fully (via `reduce` or exit chain), then open a new position via the normal entry path. The engine does NOT auto-flip direction. Per AC22.

18. **AC18 — order of operations per bar (CRITICAL)**:
    1. **Phase 1 — update_state** on all handlers (breakeven ratchet, trailing stop). Uses OLD `entry_price` / `initial_risk`.
    2. **Phase 2 — side-effects** (position-modifying):
       - **scale_check_fn** — strategy can `increase` or `reduce` the position. ONLY side-effect mechanism (PartialTPHandler is deleted per AC12).
    3. **Phase 3 — check_exit chain** (first match wins):
       CustomExitHandler → CircuitBreakerHandler → StopLossHandler → TakeProfitHandler → RSIExitHandler → MeanTargetHandler → SMATrailExitHandler → MaxHoldHandler → FundingCeilingHandler
    - Key invariant: Phase 2 completes BEFORE Phase 3 runs. StopLossHandler in Phase 3 sees updated `pos.stop_price` from scale actions and checks against this bar's `bar.low`/`bar.high`.
    - Within a bar, only ONE scale action per **single Position** (one `increase` OR one `reduce`, not both). **Scope clarification (I2)**: when `linked_scale_policy != INDEPENDENT` (AC36), the auto-propagated fill on the linked secondary leg occurs the SAME bar but is a SEPARATE Position — so across the linked PAIR there can be 2 fills/bar (one strategy-driven on primary + one auto-propagated on secondary). The "one per Position" rule is unchanged.
    - **Bar-resolution-agnostic invariant (Q4)**: AC18 Phase 1/2/3 ordering applies AT ANY BAR RESOLUTION:
      - Hourly path (`simulator.py:_process_exits`): per hourly bar
      - Sub-hourly path (`v4/minute_exits.py`): per sub-hourly tick when `exit_resolution > 0`
      - 4h / daily / future resolutions: per their respective bars
      - `scale_check_fn` is invoked at the strategy's configured `exit_resolution` cadence (default = hourly)
      - `Position.increase` / `Position.reduce` produce IDENTICAL results regardless of which dispatcher called them — the mutation math, VWAP, fees, funding pro-rating, dust threshold, and sign invariant are bar-resolution-AGNOSTIC.
      - Both `simulator.py` (hourly dispatch) AND `v4/minute_exits.py` (sub-hourly dispatch) call the SAME `Position.increase`/`Position.reduce` methods — neither reimplements the math. Forking the math by resolution caused the recent StopLossHandler-deletion bug; this spec must NOT deepen that fork.
      - `scale_check_fn` invocation wiring goes into BOTH dispatch paths so sub-hourly strategies get sub-hourly scale checks natively (no need for separate sub-hourly scaling API).
    - **Per-hourly-bar invocation cap (C2)**: `scale_check_fn` may be invoked at most ONCE per (position, hourly bar) regardless of sub-hourly cadence. If a sub-hourly tick fires a scale action on a position, NO further scale action fires on that position until the next hourly bar. Enforced by `pos._scale_action_bar: int` (hourly bar index of last fired action; -1 initially).
      - Rationale: prevents pathological cascades (a strategy returning `ScaleAction(qty_delta=-0.05*qty)` every 5-min tick would fire 12 reduces/hour, eating through dust threshold, creating ~100k spurious ClosedTrades across a portfolio backtest).
      - Mirrors the `partial_closed: bool` one-shot semantic of the retired PartialTPHandler — without this cap, we'd get a regression worse than today.
      - Test: T20 verifies cap is per HOURLY bar (not per sub-hourly tick).
    - **Breakeven-after-scale nuance**: if breakeven triggered in Phase 1 and scale changed VWAP in Phase 2, the breakeven stop stays at OLD entry_price (no longer equal to VWAP). Intentional — earned protection from original risk budget.

19. **AC19 — raw mode**: `scale_check_fn` runs regardless of `config.raw_mode`.
    - **Fees always apply**: entry/exit fees booked via `get_fee_rate(exchange, market, "taker")` — matches entry-side raw mode behavior at simulator.py:924-927 where `raw_mode` still computes `entry_fee = notional * fee_rate`.
    - **Slippage always applies**: ADV-based slippage via `state._slippage_models[strategy_id]` — matches simulator.py:209 where `raw_mode` still applies `_apply_entry_slippage`. Confirmed at simulator.py:809-991 (raw-mode entry path still routes through slippage model).
    - **Funding always applies**: `cumulative_funding` accrues on position and pro-rates on `Position.reduce` per AC9 regardless of `raw_mode`.
    - **Portfolio constraints (AC10) SKIPPED in raw mode**: margin check, ADV cap, concentration, min_position_usd — matches entry-side raw mode behavior at simulator.py:809 (raw mode bypasses `position_manager.can_open_position` checks).
    - **DD scaling still applies**: if `config.dd_scaling_enabled`, `Position.increase` margin is scaled by the current DD multiplier — same as entry sizing.
    - **Rationale**: `raw_mode` means "bypass portfolio-level gating for signal research". Fundamental execution costs (fees, slippage, funding) must always apply — without them, backtests would overstate edge and `raw_mode` results would be misleading for Gate-1 signal screening.

20. **AC20 — one position per (strategy, token, market)**:
    - Per_token perp: one Position per token
    - Combined (spot+perp): two Position objects per trade (primary/secondary legs); `scale_check_fn` called per Position
    - `max_concurrent_per_token > 1` + `scale_check_fn` → raise ValueError at spec construction
    - Strategies using scaling should set `max_concurrent_per_token = 1` (default)

21. **AC21 — time-since-entry counters never reset on `Position.increase`** (FIX/Nautilus/TradeStation convention):
    - `Position.increase` does NOT modify `pos.entry_bar` or `pos.entry_timestamp`
    - All `bars_held = global_bar - pos.entry_bar` calculations continue from ORIGINAL entry across any number of `increase` events
    - **Affected handlers** (none reset on increase):
      - `StopLossHandler` — `no_stop_bars` grace period
      - `MaxHoldHandler` — `max_hold_bars`
      - `BreakevenRatchetHandler` — `break_even_atr` activation
      - `ConvexTrailingHandler` — early-bars convex behavior
      - `RSIExitHandler`, `MeanTargetHandler`, `SMATrailExitHandler` — any handler reading `pos.bars_held` or equivalent
      - Any future handler using time-since-entry
    - **Rationale (industry-standard convention):**
      - Position is a unitary concept (FIX `OrderID(37)` + many `ExecID(17)` fills; NautilusTrader `Position.opened_time` set on first fill, never mutated; TradeStation `BarsSinceEntry` resets only on flat); adds are deliberate strategy decisions, not new entries.
      - Grace periods address ENTRY slippage / first-bar noise; that risk is consumed once and doesn't recur per add.
      - Per-add grace would create an exploit: a strategy that perpetually calls `increase` could silently disable its stop forever.
      - Pyramid trading literature (Faith *Way of the Turtle*, Tom Basso, Van Tharp R-multiples) is unanimous: stops ratchet UP, no fresh grace per add.
    - **Strategy override**: `stop_override` on `ScaleAction` (AC2) lets the strategy reset the stop on a per-`increase` basis if their thesis demands it. This is the one supported escape hatch — explicit, audited, and visible in `pos.scaling_events`.
    - **Tests**:
      - Open at bar 0 with `no_stop_bars=10`, `Position.increase` at bar 5: `no_stop_bars` gate at bar 8 uses `bars_held = 8 - 0 = 8` (still within grace), gate skips. At bar 11: gate uses `bars_held = 11`, gate fires normally. Increase did NOT reset.
      - `max_hold_bars=24`, open at bar 0, `Position.increase` at bar 12, position still open at bar 24: MaxHoldHandler fires (counts from bar 0, not bar 12).

22. **AC22 — quantity-sign invariant** (Nautilus `position_side` analog):
    - After any `Position.increase` or `Position.reduce`, exactly ONE of these must hold:
      - `pos.is_closed` (i.e., `pos.quantity == 0.0` exactly), OR
      - `sign(pos.quantity) == pos.direction` (sign matches original direction)
    - **No opposite-sign drift permitted**: if `Position.reduce` would cause `pos.quantity` to cross zero into the opposite sign due to float arithmetic error or caller passing `qty_to_close > abs(pos.quantity)`:
      - Clamp `qty_to_close = abs(pos.quantity)` (per AC17)
      - Set `pos.quantity = 0.0` exactly (assignment, not arithmetic — avoids residual)
      - Promote to terminal close (AC14)
    - **No direction flip via scaling**: a strategy wanting to flip long→short must fully close the position (via `reduce` or exit chain) and open a NEW position via the normal entry path. The engine does NOT auto-flip direction.
    - **Defensive assert** (engine-level, runs after every `increase`/`reduce`) — uses **sign-of-product** to handle negative-zero cleanly (Q2):
      ```python
      # Note: math.copysign(1.0, -0.0) returns -1.0, which would falsely fail
      # for a long with quantity = -0.0 from float arithmetic. Sign-of-product
      # avoids this — when pos.quantity == 0.0 (or -0.0), product is 0.0 and
      # the is_closed branch handles it.
      assert pos.is_closed or (pos.quantity * pos.direction > 0.0), \
          f"Sign invariant violated: pos.quantity={pos.quantity}, direction={pos.direction}"
      ```
    - This invariant is what makes downstream code (`PositionManager.total_locked_margin`, `effective_open`, liquidation, funding) safe to use signed `pos.quantity` directly.
    - Tests (all use `numpy.random.default_rng(seed=42)` for reproducibility):
      - Long position with `quantity=5.0`, `reduce(qty_to_close=5.0+1e-12)` → clamps to full close (`pos.quantity = 0.0` exactly, `is_closed=True`, terminal book).
      - Short position with `quantity=-3.0`, attempt `reduce(qty_to_close=4.0)` → clamps to `qty_to_close=3.0`, `pos.quantity=0.0`, terminal close.
      - Property test: after 5 reduce calls with fractions drawn from `rng.uniform(0.05, 0.45, 5)` totalling close to 1.0, sign invariant still holds and final dust promotes to terminal.
      - **Negative-zero edge case**: force `pos.quantity = -0.0` via `pos.quantity = math.copysign(0.0, -1.0)` for a long position → assert passes (sign-of-product is 0.0, `is_closed` branch handles).

23. **AC23 — breakeven ratchet interaction with `Position.increase`** (cross-ref):
    - Covered by AC3 ("`initial_risk` FROZEN, earned state preserved") + AC18 ("Breakeven-after-scale nuance").
    - Summary: `pos.breakeven_triggered` stays True if already earned; the breakeven stop level (computed against ORIGINAL entry_price) is preserved as a stop floor; new VWAP does NOT lower the breakeven stop.
    - Strategy can override via `stop_override` on `ScaleAction` (AC2). **Note**: `stop_override` HAS full power — including the ability to set a stop BELOW the breakeven level, effectively un-arming breakeven protection. This is by design (strategies have full risk control); auditable via `pos.scaling_events` showing the override.

24. **AC24 — at most one scale action per (position, bar)** (cross-ref):
    - Covered by AC18 ("Within a bar, only ONE scale action per position").
    - `scale_check_fn` is invoked at most once per (position, bar). The first non-None `ScaleAction` returned is executed; the engine does NOT call `scale_check_fn` again that bar even if the strategy returns multiple actions.
    - Prevents pathological callbacks that return ScaleActions in a loop.

25. **AC25 — `scale_check_fn` error handling** (cross-ref):
    - Covered by AC6 ("Runs in a try/except — exceptions logged but don't crash simulation").
    - Pattern matches existing `entry_filter_fn` and `CustomExitHandler` defensive wrapping (exit_handlers.py:417-423).
    - Test: a `scale_check_fn` that raises does not crash the sim; the exception is logged once and treated as "no action this bar".

26. **AC26 — diagnostic counters surface in StrategyResult** (FIX execution-event vocabulary, Q1 split):
    - **CORRECTION (Q1)**: the four `state.partial_fills += 1` sites at simulator.py:1174, 1225, 1472, 1506 are ENTRY-side scale-down increments (concentration cap, capital cap clamping requested entry size). They are NOT tied to the deleted `_partial_close_position`. They STAY but are renamed for clarity.

    Three distinct counters with separate semantics (preserves analytical signal — entry constraints vs exit ladders are diagnostically different):

    - **RENAMED**: `state.entry_scale_downs: int = 0` (was `partial_fills` at the 4 entry-side sites — simulator.py:1174, 1225, 1472, 1506):
      - Incremented when an entry-path constraint (concentration, ADV cap, capital) clamps the requested entry size below the strategy's intent
      - Diagnostic for "is portfolio capacity choking my entries?"
    - **REPURPOSED**: `state.partial_fills: int = 0` — now ONLY non-terminal `Position.reduce` events:
      - Incremented by every `Position.reduce` call that does NOT fully close (`is_terminal=False`)
      - Single increment site inside the `book_reduce` simulator wrapper (per AC4)
      - Diagnostic for "is my exit ladder firing as designed?"
      - Name matches FIX `OrdStatus=PartiallyFilled (1)` semantic for the reduce side
    - **NEW**: `state.increase_fills: int = 0` — count of `Position.increase` events (FIX: fills that grow `CumQty(14)` in the same direction)
    - **NEW**: `state.contingent_fills: int = 0` — count of auto-propagated linked-leg executions (FIX `ContingencyType(1385)` triggered fills per AC36; only non-zero when `linked_scale_policy != INDEPENDENT`)
    - **Reporting** (in `v4/report.py:summarize_state()`):
      - Add to `extra_info` dict:
        - `"entry_scale_downs"` (RENAMED — was conflated with partial_fills)
        - `"partial_fills"` (REPURPOSED — now reduce-only)
        - `"increase_fills"` (NEW)
        - `"contingent_fills"` (NEW)
      - Surfaces in StrategyResult and downstream consumers (run_conviction_backtest.py:104, dashboards, JSON dumps via the AC33 compat parser pattern)
    - **Migration note**: any tooling that reads `extra_info["partial_fills"]` and assumed it included entry scale-downs needs to also read `extra_info["entry_scale_downs"]`. Sum of both = old semantic.
    - **Test**: simulate strategy with 3 `Position.increase` + 5 non-terminal `Position.reduce` + 2 contingent auto-propagations + 4 entry scale-downs → counters end at `increase_fills=3, partial_fills=5, contingent_fills=2, entry_scale_downs=4`.

27. **AC27 — `Position.increase` does NOT count against `max_concurrent_per_token`**:
    - `Position.increase` mutates the existing Position; it does NOT create a new Position object.
    - Consequence: `max_concurrent_per_token` (and `max_concurrent_per_strategy`) capacity gates apply ONLY to `_open_position` calls (the entry path), NOT to `Position.increase`.
    - Equivalent FIX semantics: an execution that grows `CumQty(14)` against an existing parent `OrderID(37)` is the same parent order — it does not consume a new order slot at the venue or in our position-slot accounting.
    - **`PositionManager.effective_open(token)`** continues to return 1 for a token with one open position, regardless of how many `Position.increase` calls have been applied. (No change required — this falls out naturally since `increase` never appends to `open_positions`.)
    - **Free capital / margin checks** still apply per AC10 — a position that has been increased can fully consume `concentration_limit * portfolio_eq` even if `max_concurrent_per_token = 1`. Those are independent gates.
    - **Test**: open 1 position with `max_concurrent_per_token=1`, call `Position.increase` 3 times → `effective_open(token) == 1` throughout, no rejection counters fire on the increases.

28. **AC28 — liquidation math uses post-scale (VWAP'd) Position state**:
    - Existing liquidation check (simulator.py:458-466) reads `pos.entry_price`, `pos.margin_usd`, `pos.quantity`, `pos.leverage`, `pos.cumulative_funding`.
    - All of these are mutated by `Position.increase` / `Position.reduce`:
      - **After `increase`**: `entry_price` = new VWAP, `margin_usd += margin_delta`, `quantity` grows in direction units, `cumulative_funding` UNCHANGED (per AC9)
      - **After `reduce`**: `entry_price` UNCHANGED (per AC5), `margin_usd`/`quantity`/`cumulative_funding` scaled by `(1 - close_fraction)` (per AC4/AC9)
    - **Liquidation invariant** (no engine code change required — falls out of existing math acting on mutated fields): margin call triggers when
      ```
      pos.margin_usd + unrealized_vs_VWAP - pos.cumulative_funding < maintenance_margin
      where:
        unrealized_vs_VWAP = pos.quantity * (low_val - pos.entry_price)  # for longs
        maintenance_margin = (pos.margin_usd * pos.leverage) * mmr
      ```
    - **Matches FIX/exchange semantics**: real exchanges (Binance, Bybit) recompute maintenance margin against the **current** position state after each fill, not against original entry. FIX `MaintMargin(896)` updates with each ExecutionReport. Our backtest mirrors this.
    - **Edge case — averaging down to liquidation**: a long position underwater that gets `Position.increase`'d at a lower price has a LOWER VWAP entry → unrealized improves. But `margin_usd` grows by `margin_delta` → entry_notional and maintenance_margin grow proportionally. Net effect depends on whether the strategy is increasing risk faster than it's improving entry — exactly the tradeoff the strategy is making explicitly. Engine just executes the math.
    - **Tests**:
      - Long position at $100, `quantity=1.0`, `margin_usd=$10`, `leverage=10x`, `mmr=0.005`. Price drops to $92 → unrealized = -$8, maintenance = $0.50, equity = $10 - $8 = $2 > $0.50 → no liquidation. `Position.increase` at $92, `qty_to_add=1.0`, `margin_delta=$9.20`. New VWAP = $96, new `margin_usd=$19.20`, new `quantity=2.0`, new entry_notional=$192. Price drops to $90 → unrealized = 2.0 × ($90 - $96) = -$12, maintenance = $192 × 0.005 = $0.96, equity = $19.20 - $12 = $7.20 > $0.96 → no liquidation. Verifies VWAP-based liquidation math.
      - Same setup but `Position.increase` at $80 (much lower) → new VWAP = $90, large `margin_delta`. Verify liquidation triggers at the right post-scale price level, not at the pre-scale level.

### Trade-ID Refactor (FIX/Nautilus-style identity)

**Why this is in-scope** (not deferred): the `:partial` suffix-mangling pattern is the root cause of the AC4 collision problem. Industry standard (FIX OrderID/ExecID, vectorbt Position Id/Trade Id, NautilusTrader position_id/trade_id) uses **explicit fields**, not parsed strings. Adding the fields now — once — prevents downstream tools from baking in workarounds for the suffix scheme.

29. **AC29 — ClosedTrade gets identity fields** (added to `v4/position.py:ClosedTrade`):
    ```python
    parent_position_id: str = ""    # immutable parent (joins to Position.position_id) — FIX OrderID analog
    exec_seq: int = 0               # 0 = full close (legacy), 1..N = scale executions — FIX ExecID-derived sequence
    exec_type: str = "exit"         # "open" | "increase" | "reduce" | "exit" | "linked_reduce"
    is_terminal: bool = True        # True for the final close that empties the position (FIX OrdStatus=2 / Nautilus is_closed)
    triggered_by: str = ""          # parent exec_id (= primary's suffixed position_id) when this execution is auto-propagated (linked-leg per AC36) — FIX ExecRestatementReason(378) analog
    ```
    All defaults preserve backward compat — existing JSON trade logs and existing test code that constructs `ClosedTrade(...)` without these fields still works.

    **`triggered_by` lifecycle (C2)**:
    - Set ONLY by the linked-leg auto-propagation path (AC36 PROPORTIONAL/ABSOLUTE policies)
    - Empty string `""` for ALL strategy-driven executions, helpers (`tp_ladder_*`, `add_*`), and exit-chain closures
    - Value = primary's suffixed `position_id` (e.g., `"BTC_2026-01-15T10_strategy:scale_2"`) — joinable to the parent ClosedTrade row
    - Helpers MUST NOT set this field; only the engine's auto-propagation logic sets it

30. **AC30 — Field semantics** (FIX/Nautilus vocabulary):
    - `parent_position_id`: copy of the original `Position.position_id` at entry. Never mutated. Use this (NOT the suffixed `position_id`) for joins/groupbys. Maps to FIX `OrderID(37)`.
    - `exec_type` is **orthogonal to** `exit_reason`. `exit_reason` stays as the granular cause ("stop", "take_profit", "liquidation", etc.). `exec_type` describes the lifecycle phase. Maps to FIX `ExecType(150)`.
    - `exec_type` value mapping (V1 — the values that ACTUALLY appear vs reserved):
      - `"reduce"` → `Position.reduce` event that did NOT fully close the position (`is_terminal=False`) — **LIVE**
      - `"exit"` → terminal close via the exit chain (StopLossHandler, TakeProfit, etc.) — **LIVE** (also covers terminal close when reduce promotes to full close per AC14)
      - `"linked_reduce"` → auto-propagated `reduce` on linked secondary leg per AC36 (PROPORTIONAL/ABSOLUTE policy) — **LIVE** (only when `linked_scale_policy != INDEPENDENT`)
      - `"open"` → **RESERVED** for future use (we don't book a ClosedTrade on entry today; reserved if we ever add per-fill open executions)
      - `"increase"` → **RESERVED** (`Position.increase` mutates the open position; no ClosedTrade booked per AC1/AC35)
    - For full close (no prior reduces): `exec_seq=0, exec_type="exit", is_terminal=True, position_id=parent_position_id` (unchanged from today).
    - For reduce N (not terminal): `exec_seq=N, exec_type="reduce", is_terminal=False, position_id=f"{parent}:scale_{N}"`.
    - For reduce that promotes to full close per AC14 dust threshold: `exec_seq=N, exec_type="reduce", is_terminal=True, position_id=f"{parent}:scale_{N}_final"`.
    - For final close via exit chain after prior reduces: `exec_seq=last_seq+1, exec_type="exit", is_terminal=True, position_id=parent_position_id`.
    - Invariant: exactly one ClosedTrade per `parent_position_id` has `is_terminal=True` (assert in tests — Nautilus-style strict uniqueness).

31. **AC31 — paper_state.py serialization**:
    - `paper_state.py` serializes ClosedTrade field-by-field (see lines 76-80 for chandelier-style pattern).
    - Add the 5 new fields (`parent_position_id`, `exec_seq`, `exec_type`, `is_terminal`, `triggered_by`) to the serialization roundtrip.
    - On LOAD: if any new field is missing (old state file), apply defaults — `parent_position_id = position_id.split(":")[0]`, `exec_seq=0`, `exec_type="exit"`, `is_terminal=True`, `triggered_by=""`. Same compat shim used in AC33.
    - Test: roundtrip an old paper_state file with no new fields → loads with correct defaults.

32. **AC32 — ClosedTrade dataclass alignment** (Q3 — keep separate, no unification):
    - `v4/position.py:ClosedTrade` is canonical for production engine (full schema + new identity fields).
    - `tools/raw_backtest.py:ClosedTrade` (line 104): **KEEP SEPARATE** with a clarifying comment: `# NOTE: subset schema for Gate-1 raw signal validation; not the canonical v4.position.ClosedTrade`. Schema is intentionally disjoint (`gross_pnl`/`fees`/`funding`/`slippage`/`net_pnl`/`metadata` vs canonical's `pnl`/`funding_cost`/`entry_fee`/`exit_fee`) — `raw_backtest` is a deliberately-bypassable validation layer for signal screening, not a production booking system. Forcing schema alignment couples two layers that should evolve independently.
    - `research/R159_adaptive_trend.py:ClosedTrade` (line 197): research-frozen, leave as-is — add `# NOTE: not the canonical ClosedTrade — see v4/position.py` comment.
    - Rationale (Q3): industry quant practice keeps validation harnesses simpler than production booking. NautilusTrader, vectorbt, Backtrader all separate "research record" from "production trade record" schemas. Saves ~1h vs unification + removes coupling risk between two layers serving different audiences.

33. **AC33 — Compat-parser for analysis/*.json**:
    - 60+ historical trade-log JSON files in `analysis/` were written without the new fields.
    - New helper in `v4/report.py`: `load_trade_log(path) -> list[ClosedTrade]` — handles both old and new schemas:
      ```python
      def load_trade_log(path):
          for record in json.load(open(path)):
              if "parent_position_id" not in record:
                  # Compat shim — maps legacy ":partial" suffix into FIX/Nautilus identity model
                  # FAIL LOUD if a post-migration log is mis-classified (C4 defense)
                  assert ":scale_" not in record["position_id"], \
                      f"Post-migration log entry without identity fields: {record['position_id']}. " \
                      f"This indicates a mixed-format log (e.g., paper run interrupted mid-rollout). " \
                      f"Re-run the migration write path or hand-fix the log."
                  record["parent_position_id"] = record["position_id"].split(":")[0]
                  record["exec_seq"] = 1 if ":partial" in record["position_id"] else 0
                  record["exec_type"] = "reduce" if ":partial" in record["position_id"] else "exit"
                  record["is_terminal"] = ":partial" not in record["position_id"]
                  record["triggered_by"] = ""
              yield ClosedTrade(**record)
      ```
    - Old logs are NOT regenerated — they remain as historical artifacts. The compat-parser handles loads.
    - Newly-written logs include the new fields natively.
    - **Mixed-format logs are NOT supported** — the assertion fails loud rather than silently mis-classifying. If you encounter one, hand-edit or re-run the migration write path.
    - Test: load one old log (no identity fields) + one new log (has identity fields) → both produce correctly-populated ClosedTrade objects.
    - Test: load a hand-crafted mixed log (one row with `:scale_1` suffix but missing `parent_position_id`) → AssertionError raised.

34. **AC34 — Cutover behavior**:
    - The legacy `position_id` field on ClosedTrade is RETAINED (still the suffixed form: `parent:scale_N`) — backward compat for any downstream tool that hasn't migrated.
    - New analysis tooling SHOULD use `parent_position_id` for joins/groupbys; the suffixed `position_id` remains a stable per-row unique key.
    - The `:partial` suffix scheme used by `_partial_close_position` is fully retired (replaced by `:scale_{n}` per AC4).
    - Documented in `knowledge/ARCHITECTURE.md` under a new "Trade Identity Model" section.

35. **AC35 — Test coverage for identity fields**:
    - Existing tests pass unchanged (defaults preserve behavior).
    - New tests in `v4/tests/test_position_scaling.py`:
      - Single open + full close → `exec_seq=0, exec_type="exit", is_terminal=True`
      - Open + 2 reduces + full exit → 3 ClosedTrades with `exec_seq` ∈ {1, 2, 3}, only the last has `is_terminal=True`
      - Open + 1 reduce that promotes to full close (dust threshold per AC14) → 1 ClosedTrade with `exec_type="reduce"`, `is_terminal=True`, `position_id` ends in `:scale_1_final`
      - Open + `Position.increase` (no reduce) + full exit → 1 ClosedTrade `exec_seq=0` (`increase` does NOT book a ClosedTrade — it modifies the open position only)
      - Old paper_state file roundtrip → defaults applied correctly
      - Old analysis/*.json roundtrip via `load_trade_log()` → `:partial` suffix → `exec_type="reduce"`, `is_terminal=False`

36. **AC36 — linked position scale policy** (FIX ContingencyType, tag 1385):
    New field on StrategySpec: `linked_scale_policy: LinkedScalePolicy = LinkedScalePolicy.INDEPENDENT`
    ```python
    class LinkedScalePolicy(Enum):
        INDEPENDENT = "independent"     # FIX: no contingency on partial — strategy handles each leg
        PROPORTIONAL = "proportional"   # FIX: OneUpdatesOtherProportional — primary 30% → secondary 30%
        ABSOLUTE = "absolute"           # FIX: OneUpdatesOtherAbsolute — primary -Q → secondary -Q (signed)
    ```

    - **INDEPENDENT (default)**: `Position.reduce` / `Position.increase` does NOT propagate to linked. Each linked Position gets its own `scale_check_fn` invocation per AC20. Matches existing `_partial_close_position` behavior. **Runtime warning** when a `reduce` fires on a position with `linked_position_id` set (logged once per (strategy, token) pair) — surfaces legging risk that crypto basis-arb literature explicitly flags.
    - **PROPORTIONAL**: primary `reduce(qty_to_close=Q)` (or `increase(qty_to_add=Q)`) → secondary auto-applies the same fraction `F = Q / abs(primary.quantity_before)`. Auto-emitted secondary ClosedTrade has:
      - `exec_type = "linked_reduce"`
      - `exec_seq = next_seq` on secondary
      - `triggered_by = primary's exec_id` (per AC29 new field)
      Strategy's `scale_check_fn` is NOT called on the secondary for the auto-propagated bar (prevents double-fire).
    - **ABSOLUTE**: primary `reduce(qty_to_close=Q)` → secondary `reduce(qty_to_close=Q)` (same absolute units). Same auto-emit pattern. Used for delta-hedged / basis-mismatch positions where absolute unit count matters more than percentage.
    - **`Position.increase`** with PROPORTIONAL: primary +50% of size → secondary +50% of its size. With ABSOLUTE: primary +Q units → secondary +Q units.
    - **stop_override on primary's ScaleAction does NOT propagate** — each leg has its own risk profile. Strategies wanting paired stops set them via separate `scale_check_fn` calls per leg.
    - **Full-close propagation (simulator.py:522-529) stays UNCONDITIONAL** regardless of policy. Closing a primary fully always closes the linked secondary with `reason="linked_exit"`. Rationale: no current strategy expects an orphan secondary on full primary close — separate decision.
    - **Dust-cascade invariant (C4)**: after PROPORTIONAL or ABSOLUTE auto-propagation, if EITHER leg was promoted to terminal (via AC14 dust threshold), the engine MUST force-close the OTHER leg with `exit_reason="linked_exit"`. Prevents the hazard where primary 5×99%-reduce cascades + ABSOLUTE propagation leaves secondary at sub-dust but still "open".
      - Implementation: after `book_reduce(secondary, ...)` returns, check `if primary.is_closed or secondary.is_closed: close the other via _close_position(reason="linked_exit")`
      - Invariant: at the end of any auto-propagated tick, `primary.is_closed == secondary.is_closed` (both or neither).
    - Validation: `linked_scale_policy != INDEPENDENT` requires `max_concurrent_per_token == 1` (raises ValueError at spec construction). Mixing per-token-multi-position with auto-propagated linked scales would create ordering ambiguity.
    - Test coverage:
      - INDEPENDENT scale on linked position emits warning, secondary unchanged
      - PROPORTIONAL: primary scale 30% → secondary auto-scales 30%, both ClosedTrades have correct `triggered_by` linkage
      - ABSOLUTE on asymmetric leg sizes (primary 100, secondary 50) → primary -30 → secondary -30 (clamped to full close if `|Q| > |secondary.quantity|`)
      - Full close propagation unaffected by policy
      - Hedge ratio invariant: PROPORTIONAL preserves `|primary.quantity| / |secondary.quantity|` ratio across scales

## Helper API (new file: `v4/scaling.py`)

```python
from dataclasses import dataclass

@dataclass
class ScaleAction:
    """Strategy-side directive returned by scale_check_fn.
    qty_delta is signed in position-direction units:
      - positive → engine routes to Position.increase(qty_to_add=qty_delta)
      - negative → engine routes to Position.reduce(qty_to_close=abs(qty_delta))
    """
    qty_delta: float
    reason: str
    stop_override: float | None = None


@dataclass
class ScalingEvent:
    bar: int
    kind: str  # "increase" or "reduce" (FIX/Nautilus-aligned vocabulary)
    fill_price: float
    qty_delta: float            # signed actual: + on increase, - on reduce
    requested_qty_delta: float  # signed requested by strategy BEFORE constraint clamping (per AC10/I3)
    margin_delta: float         # signed actual: + on increase, - on reduce
    fill_notional: float        # absolute |qty_delta| * fill_price
    entry_fee_delta: float      # > 0 on increase
    exit_fee: float             # > 0 on reduce
    slippage_bps: float
    atr_at_event: float


# === REDUCE HELPERS (helpers internally call Position.reduce_fraction or compute absolute qty_to_close) ===
# Helper "fraction" parameter is unsigned [0, 1], same semantic as Position.reduce_fraction.

def tp_ladder_atr(levels: list[tuple[float, float]]):
    """Multi-level TP triggered by profit in ATR units.
    levels: [(atr_threshold, fraction), ...] ordered ascending.
    Example: tp_ladder_atr([(2, 0.3), (4, 0.3), (6, 0.2)])
    """

def tp_ladder_price(levels: list[tuple[float, float]]):
    """Multi-level TP triggered by absolute price levels.
    For longs: levels are prices above entry (high >= price triggers fire).
    For shorts: levels are prices below entry (low <= price triggers fire).
    levels: [(target_price, fraction), ...].
    Example: tp_ladder_price([(78000, 0.3), (82000, 0.3), (88000, 0.2)])
    """

def tp_ladder_r(levels: list[tuple[float, float]],
                anchor: Literal["original", "vwap"] = "original"):
    """R-multiple TP ladder (THE quant standard).
    R = initial_risk (FROZEN at entry per AC3).

    ANCHOR SEMANTICS (Q-DEC1 — explicit param because increase changes VWAP but freezes initial_risk):

      "original" (DEFAULT, FIX/Tharp/Faith industry-standard):
        Profit measured against pos.r_anchor_price (NEW Position field frozen at first entry).
        After Position.increase, r_anchor_price is UNCHANGED — matches FIX Stop/TP "set-once"
        semantics (a FIX TP limit order has fixed Price(44); doesn't follow AvgPx).
        Matches Tharp R-multiple framework, Faith "Way of the Turtle" pyramid semantics,
        Tom Basso interviews. 1R = original risk budget.
        Example: open long at $100 with initial_risk=$5. r_anchor_price=$100. 1R target = $105.
                 Position.increase at $95 → VWAP=$97.5 but r_anchor_price STILL $100. 1R target STILL $105.

      "vwap" (opt-in, non-standard):
        Profit measured against current pos.entry_price (= VWAP after any increase).
        Matches CircuitBreaker/TakeProfit handler convention (entry_price ± k × initial_risk).
        Use when the strategy's intent is "every time I scale up, my TP targets should also scale up".
        FIX-equivalent: would require OrderCancelReplaceRequest per fill to modify TP Price — heavy.
        Example: open long at $100 with initial_risk=$5. 1R target = $105.
                 Position.increase at $95 → VWAP=$97.5. 1R target RECALCULATES to $102.50.
        WARNING: after averaging UP, VWAP-anchored 1R can become already-true at next bar —
        helper would fire immediately at what was intended as a +2R level. Use with care.

    levels: [(r_multiple, fraction), ...] ordered ascending. fraction is unsigned [0,1].
    Example: tp_ladder_r([(1.0, 0.33), (2.0, 0.33), (3.0, 0.34)])
    — close 33% at 1R profit, 33% at 2R, last 34% at 3R. Default uses original-entry anchor.

    Directly maps to risk:reward thinking used by professional traders.
    """

def breakeven_plus_runner(partial_r: float = 1.0, fraction: float = 0.5):
    """Close N% at breakeven (when profit = partial_r × initial_risk), keep runner.
    Most common quant pattern (per research): locks in breakeven, leaves runner.

    INTERACTION WITH BreakevenRatchetHandler (Q5):
      If config.break_even_atr is also set, BOTH the helper AND the handler may fire
      on the same bar in different phases of AC18:
        Phase 1: BreakevenRatchetHandler moves pos.stop_price to entry (state mutation)
        Phase 2: this helper triggers Position.reduce(fraction) (side-effect)
        Phase 3: StopLossHandler sees the new stop AND the now-reduced position;
                 if bar.low/high already breached the moved stop, the runner closes
                 IMMEDIATELY this same bar with reason="stop"
      This is the desired "lock-in breakeven AND get out if instant whip" behavior.
      Use breakeven_plus_runner WITHOUT break_even_atr if you want the runner
      to keep its original stop after the partial close.

    Example: breakeven_plus_runner(partial_r=1.0, fraction=0.5)
    """

# === INCREASE HELPERS (helpers compute absolute qty_to_add internally and emit ScaleAction(qty_delta=+X)) ===

def add_at_price(triggers: list[tuple[float, float]]):
    """Add to position when price crosses specified levels.
    For longs: triggers at pullback prices below current (low <= price → add).
    For shorts: triggers at rally prices above current (high >= price → add).
    triggers: [(target_price, qty_fraction_of_current), ...]  each fires once.
    Example: add_at_price([(71000, 0.3), (68000, 0.5)])
    """

def add_on_profit_atr(triggers: list[tuple[float, float]]):
    """Pyramid: add to position when profit crosses ATR thresholds (never add to losers).
    triggers: [(atr_threshold, qty_fraction), ...] ordered ascending.
    Example: add_on_profit_atr([(1.5, 0.5), (3.0, 0.3)])
    """

def confirm_and_add(confirm_bars: int = 48, min_profit_atr: float = 0.5,
                    add_fraction: float = 1.0):
    """Enter small, confirm the thesis, add the rest.
    If position is profitable by ≥ min_profit_atr at bar=confirm_bars,
    add add_fraction of the ORIGINAL quantity. Only fires once.
    Example: confirm_and_add(confirm_bars=48, min_profit_atr=0.5, add_fraction=1.0)
    — add 100% of original size at 48h if +0.5 ATR in profit.
    Matches the "50% now, 50% on confirmation" pattern from MAE/MFE analysis.
    """

# === COMPOSITION ===

def combine(*fns):
    """Run helper fns in order; return first non-None ScaleAction.
    Example: combine(add_at_price([(71000, 0.3)]), tp_ladder_atr([(3, 0.5)]))
    """
```

**Helper design principles:**
- Helpers are syntactic sugar over the FIX/Nautilus-aligned engine primitives (`Position.increase` / `Position.reduce`). They use trading-domain terms (`tp_ladder`, `add_at_price`) at the user surface because that's how strategy writers think; they translate to absolute `qty_delta` internally.
- All helpers are pure closures over `(pos, bar)` — no external state
- Each tracks its fire state via `pos.scaling_events` (inspect history to check if level i already fired)
- Helpers can be composed with `combine()`
- Strategy writer always has escape hatch: write their own `scale_check_fn(pos, bar)` returning raw `ScaleAction(qty_delta=...)` in absolute units

**Strategy writer has THREE layers of expressiveness:**
1. Full imperative control: write your own `scale_check_fn(pos, bar) -> ScaleAction | None`
2. Declarative ATR-based: `tp_ladder_atr([(2, 0.3), (4, 0.3), (6, 0.2)])`
3. Declarative price-based: `tp_ladder_price([(78000, 0.3), (82000, 0.3), (88000, 0.2)])`
4. Declarative add-in: `add_at_price([(71000, 0.3), (68000, 0.3)])`
5. Combined: `combine(add_at_price(...), tp_ladder_price(...))`

## Time Appetite

**150-205 hours** (big-bang v5 ship). Production-grade engine work replacing a hardened v4 engine — estimate reflects the full hardening effort.

**Scope at a glance**:
- v5 fork with full self-contained module set (~22 files)
- Unified dispatcher (Scope B) — eliminates simulator + minute_exits + paper_engine dispatch fork
- Unified strategy API (Option Y) — `Strategy.generate/check_scale/check_exit/filter_entry` methods, UniverseContext lazy indicators
- Position scaling (increase/reduce/reduce_fraction + helpers + FIX-aligned identity)
- Trade-ID refactor (ClosedTrade identity fields, compat parser)
- Linked-leg policy (AC36)
- Regime removed from engine
- 12 architectural cleanups (C-1 through C-12 — conviction, walk-forward, plugin system, raw_mode, max_concurrent, dd_scaling DELETE, multi-leg Order.legs unifying spot+perp+armed entries, single bar_resolution, hist_cache, pump_filter DELETE, strategy_type dispatch, sizing redesign)
- Sizing: 2-intent engine (FIXED_FRACTION, FIXED_NOTIONAL) + v5/sizing/helpers.py library (vol_target, kelly, composite_scaled for s524 migration)
- **v5/data/ package** — FIX/Nautilus-aligned data engine fixing 2.3GB memory leak + fragmented fetchers; pluggable DataClient adapters for WS/REST/alt-data
- Paper state migrator v1 → v2
- Dashboard rework (regime display removed, scaling_events timeline, unified armed_orders view)
- Phase 4 parity gates (s524 family v4 vs v5 bit-exact + metric equivalence)

**Breakdown**:
- v5 fork mechanics (copy, imports, test structure): 6-8h
- `v5/data/` package (bus, cache, engine, aggregators, 4 initial clients): 25-35h
- Memory fixes (slotted dataclasses, float32, incremental signals, bounded closed_trades): 10-15h
- Unified BarProcessor (Scope B): 6-8h
- Unified strategy API + UniverseContext + pull-based indicators (Option Y + C-3): 10-14h
- Position primitives + ClosedTrade identity fields + scaling helpers: 12-16h
- Multi-leg Order.legs (C-7, unifies spot+perp + armed entries + pending orders): 16-20h
- Trade-ID refactor + load_trade_log compat: 3-4h
- Linked propagation (AC36): 3-4h
- Paper engine wiring (scale_check, single-resolution subscription): 4-5h
- Regime removal + v5/regimes.py utility: 2-3h
- Sizing redesign (2-intent engine + helper library): 8-10h
- Conviction → priority + AllocationPolicy (C-1): 6-8h
- Walk-forward extraction (C-2): 6-8h
- raw_mode removal (C-4): 3-4h
- max_positions rename + fix (C-5): 2h
- DrawdownThrottle risk component (C-6): 3-4h
- Single bar_resolution config (C-8): 2h
- pump_filter + dd_scaling DELETE: 1h
- Paper state migrator v1 → v2: 4-6h
- Dashboard rework (regime display removal, new counters, scaling timeline): 4-6h
- Test suite (T1-T20 + TG1-TG6 + T13b + multi-leg + data engine tests): 16-22h
- Phase 4 parity gates (s524 family v4 vs v5 verification): 4-6h
- Bug buffer: 10-15h

**Breakdown**:
- v5 fork mechanics (copy, imports, test structure): 6-8h
- Unified BarProcessor (Scope B): 6-8h
- Unified strategy API with UniverseContext lazy indicators (Option Y + C-3): 8-10h
- Position primitives + ClosedTrade identity fields + helpers: 12-16h
- Trade-ID refactor + load_trade_log compat: 3-4h
- Linked propagation (AC36): 3-4h
- Paper engine wiring (Q-DEC2): 3h
- Regime removal + v5/regimes.py: 2-3h
- Conviction → priority + AllocationPolicy (C-1): 6-8h
- Walk-forward extraction (C-2): 6-8h
- raw_mode removal (C-4): 3-4h
- max_positions rename + fix (C-5): 2h
- DrawdownThrottle risk component (C-6): 3-4h
- Multi-leg Order.legs clean implementation (C-7): 16-20h
- Single bar_resolution (C-8): 2h
- hist_cache internalize (C-9): 2h
- pump_filter removal (C-10): 1h
- Test suite for v5 (T1-T20 + TG1-TG6 + T13b + multi-leg tests): 12-16h
- Bug buffer: 6-8h

Position scaling core (12-16h):
- Core Position methods (`increase`, `reduce`, `reduce_fraction`, `leaves_qty`, `is_closed`): 2h
- ScaleAction/ScalingEvent dataclasses: 0.5h
- Hook wiring in simulator (Phase 2 processor): 1h
- Fees/slippage/funding accounting: 2h
- Portfolio constraint checks: 1h
- Helper module (`v4/scaling.py` + `tp_ladder()`): 1h
- Migrate 5 legacy strategies: 2h
- Remove `PartialTPHandler` + cleanup: 1h
- Unit tests (per AC): 3-4h
- Integration test with migrated strategy: 1h
- Bug buffer: 2-3h

Trade-ID refactor add-on (~6h):
- ClosedTrade new fields + populate everywhere: 1.5h
- paper_state.py serialization + back-compat shim: 1h
- Unify ClosedTrade across raw_backtest.py: 1h
- `load_trade_log()` compat parser in v4/report.py: 1h
- Identity-field test coverage: 1h
- ARCHITECTURE.md "Trade Identity Model" docs: 0.5h

Quant-pass additions (~4h):
- AC22 sign-of-product + negative-zero handling: 0.5h
- AC4 scale_count rollback + try/except: 0.5h
- AC10 requested_qty_delta plumbing through ScalingEvent: 1h
- AC36 ABSOLUTE asymmetric clamp test (T12): 0.5h
- Phase 3 test plan (T1-T19) authoring time during decompose: 1.5h

Second-pass review additions (~7h):
- C1 ReduceResult field extension (gross_pnl, exit_fee, slip_bps, closed_qty_signed): 0.5h
- C2 per-hourly-bar invocation cap + pos._scale_action_bar field: 0.5h
- C4 AC36 sibling force-close on dust cascade: 0.5h
- C5 T20 redesign to direct Position-level unit test: 0.5h
- Q-DEC1 r_anchor_price field + tp_ladder_r anchor param + T19-revised + T19b tests: 1h
- Q-DEC2 paper_engine wiring (INDEPENDENT-only): 3h
- Q-DEC3 pos._helper_state field + paper_state roundtrip: 0.5h
- Q-DEC5 _close_position API extension (position_id_override, exec_type, triggered_by): 0.5h
- QC1 AC9 funding invariant revision + T1b interleaved test: 0.5h
- TG1-TG6 six missing acceptance tests: 3h
- A4 ScalingEvent paper roundtrip test: 0.5h
- ClosedTrade positional-arg cascade audit (R2): 1h (grep, fix any breaking call sites)
- Bug buffer revision (upgrade from 2-3h to 4-6h for this size spec): +2h

## Phase 3 Test Plan (capture before tests are written)

These tests are inferred during specification — actual test files are written in Phase 3 (decompose). Listing them here prevents loss between phases.

### Critical accounting tests (must-have)

- **T1. Funding sum invariant (AC9)**: Open position, accrue funding for 3 funding cycles, perform 3 reduces of varying fractions, then full close. Assert: `sum(ClosedTrade.funding_cost for all reduces + final) == total_funding_paid_during_position_life`. Single most error-prone path.
- **T2. Entry fee sum invariant (AC7)**: Open position with `entry_fee = $5`, perform 3 reduces, then full close. Assert: `sum(ClosedTrade.entry_fee) == $5` (no double-count, no loss).
- **T3. Margin sum invariant**: Open with `margin_usd = $200`, perform 3 reduces, full close. Assert: `sum(ClosedTrade.margin_usd) == $200`.
- **T4. AC22 negative-zero edge case**: Force `pos.quantity = math.copysign(0.0, -1.0)` for a long → `is_closed=True` AND defensive assert passes (sign-of-product, not copysign).
- **T5. AC22 random-fraction property test**: With seed=42, perform 5 reduces with `rng.uniform(0.05, 0.45)` fractions totalling close to 1.0 → sign invariant holds throughout, final dust promotes to terminal.

### Order-of-operations tests (AC18)

- **T6. Stop-after-increase same-bar**: open at bar 0, `Position.increase` at bar 5 with `stop_override` placing stop at a price already breached by `bar.low` → Phase 2 mutates stop, Phase 3 StopLossHandler fires same bar, ClosedTrade has `exit_reason="stop"`.
- **T7. Breakeven_plus_runner + break_even_atr same-bar interaction (Q5)**: helper triggers reduce 50% in Phase 2, BreakevenRatchetHandler moved stop in Phase 1, runner closes Phase 3 if instant whip — assert exact sequence in `pos.scaling_events` + ClosedTrades.

### Constraint clamping tests (AC10)

- **T8. AC10 concentration scale-down**: Strategy returns `ScaleAction(qty_delta=+10)`, but concentration cap allows only `+6`. Assert `ScalingEvent.qty_delta == +6`, `ScalingEvent.requested_qty_delta == +10` (I3 evidence preservation).
- **T9. AC10 ADV cap skip**: Strategy returns `ScaleAction(qty_delta=+10)` exceeding per-fill ADV cap → action SKIPPED, `state.rejections.adv_cap += 1`, no ScalingEvent recorded.

### Linked-leg tests (AC36)

- **T10. INDEPENDENT policy**: primary `Position.reduce(50%)` on a position with `linked_position_id` set → secondary unchanged, runtime warning logged once per (strategy, token).
- **T11. PROPORTIONAL policy hedge ratio**: primary `quantity=10`, secondary `quantity=5` (ratio 2:1). Primary reduces 30% → secondary auto-reduces 30%. Assert post-state: primary=7, secondary=3.5, ratio still 2:1.
- **T12. ABSOLUTE policy asymmetric clamp**: primary `quantity=100`, secondary `quantity=50`. Primary `reduce(qty_to_close=60)` → secondary `reduce(qty_to_close=60)` clamps to `qty_to_close=50` (full close per AC22).

### Identity field tests (AC29-AC35)

- **T13. Old paper_state roundtrip (AC31)**: load a state file with no identity fields → defaults applied (`parent_position_id` derived from `position_id` split, `exec_seq=0`, etc.).
- **T14. Old analysis JSON roundtrip (AC33)**: load `analysis/trades_baseline_2024.json` (legacy `:partial` suffix) via `load_trade_log()` → ClosedTrade objects have correct `parent_position_id`, `exec_type="reduce"`, `is_terminal=False` for partial rows.
- **T15. Mixed-format log assertion (C4)**: hand-craft a log with `:scale_1` suffix but missing `parent_position_id` → `load_trade_log()` raises AssertionError.
- **T16. position_id uniqueness (I1)**: 100 simulated positions with mixed exit paths (terminal scale-out + terminal exit chain) → all `ClosedTrade.position_id` values unique within the run.

### Raw mode + DD scaling (T7 from reviewer)

- **T17. raw_mode + scale_check_fn + DD multiplier (AC19)**: with `config.raw_mode=True` and `config.dd_scaling_enabled=True` at 50% drawdown, `Position.increase` margin should be scaled by 0.5 (DD multiplier) AND skip portfolio constraints — verify both behaviors compose correctly.

### Helper unit tests

- **T18. tp_ladder_atr fires once per level**: 3-level ladder, advance bars through each level → exactly 3 reduces, each at its trigger.
- **T19. tp_ladder_r post-increase semantics (Q1)**: Open long at $100 with `initial_risk=$5`, `Position.increase` at $90 → new VWAP=$95. tp_ladder_r([(1.0, 0.5)]) → fires when price reaches $95 + 1.0*$5 = $100 (against new VWAP), NOT $100 + $5 = $105 (original entry).

### Bar-resolution invariant tests (Q4)

- **T20a. Position math is bar-resolution-agnostic (C5 redesigned — falsifiable)**: Direct unit test. Construct two identical Position objects + identical pre-state inputs. Call `pos1.reduce(qty_to_close=X, fill_price=Y, ...)` and `pos2.reduce(qty_to_close=X, fill_price=Y, ...)` — assert ReduceResult fields are bit-exact equal. This isolates the "Position.reduce math is independent of dispatcher" claim, testable without full sim harness.
- **T20b. Per-hourly-bar invocation cap (C2)**: sub-hourly strategy returning `ScaleAction(qty_delta=-0.05*qty)` every 5-min tick; run over 10 hourly bars (120 sub-hourly ticks at 5-min resolution) → assert `state.partial_fills <= 10` (cap enforces once per hourly bar, not per tick). Proves the cap prevents pathological cascade.

### Missing coverage tests (TG1-TG6)

- **TG1. AC5 — entry_price unchanged on reduce**: Open long at $100 with quantity=5. `reduce(qty_to_close=1.5, fill_price=$110)` → assert `pos.entry_price == 100.0` (bit-exact, no drift).
- **TG2. AC8 — slippage recorded per fill**: `increase(qty_to_add=2, fill_price=$100)` with ADV slippage model producing 3.5 bps → assert `ScalingEvent.slippage_bps == 3.5`. Same for reduce.
- **TG3. AC15 — no force-close after increase that breaches new stop**: Open long at $100, quantity=1, stop=$95. `increase(qty_to_add=1, fill_price=$90, stop_override=$92)` on a bar with `bar.low=$91` → Phase 2 does NOT close; Phase 3 StopLossHandler fires and closes with `exit_reason="stop"` same bar.
- **TG4. AC16 — trailing peaks preserved**: Open long, `pos.highest=$110` from prior bars. `increase(qty_to_add=1, fill_price=$105)` on a bar with `bar.high=$108` → assert `pos.highest == 110.0` (not reset to $108, not recalculated from $105).
- **TG5. AC20 — spec validation**: `StrategySpec(..., max_concurrent_per_token=2, scale_check_fn=<some fn>)` → raises `ValueError` at construction time.
- **TG6. T1b — funding invariant with interleaved increase (QC1 revised)**: Open, accrue funding 2 periods, reduce 50%, increase 100% (back to original size), accrue 2 more periods, full close. Assert `sum(closed_funding) + 0 == sum(all per-bar funding accruals)`. Non-trivial invariant that pure-reduce T1 doesn't cover.

### Paper state roundtrip test (A4)

- **T13b. ScalingEvent paper_state roundtrip (A4)**: Position with 3 ScalingEvents (mix of increase + reduce), plus populated `_helper_state` dict (e.g., `{"tp_ladder_fired": {0, 1}}`). Serialize to paper_state JSON, deserialize, assert round-tripped Position has identical `scaling_events` (all 10 ScalingEvent fields), `scale_count`, `_helper_state`, `r_anchor_price`.

### R-anchor test (Q-DEC1)

- **T19-revised. tp_ladder_r with default "original" anchor**: Open long at $100 with `initial_risk=$5`, `r_anchor_price=$100` (frozen). `Position.increase` at $95 → VWAP=$97.5 but `r_anchor_price` STAYS $100. tp_ladder_r([(1.0, 0.5)]) fires when price reaches $105 ($100 + 1.0×$5 = original anchor + 1R), NOT $102.50 (VWAP + 1R).
- **T19b. tp_ladder_r with opt-in "vwap" anchor**: Same setup, but `tp_ladder_r([(1.0, 0.5)], anchor="vwap")`. After increase to VWAP=$97.5, fires at $102.50. Documents the non-standard opt-in behavior.

## Risks

- **Position is high-blast-radius (21 importers)** — changes must be additive except for the removal of partial_tp fields
- **ClosedTrade is even higher blast — 129 files reference position_id** — mitigated because all new fields have defaults; only writers (simulator.py, paper_state.py) need updating, readers continue to work
- **Removing partial_tp fields breaks strategies not on the migration list** — run full test suite on all strategies, not just active ones
- **Fee/slippage/funding bookkeeping** — 3 subsystems must stay in sync
- **Paper engine state serialization for new identity fields** — covered by AC31 with back-compat shim for old state files
- **Old analysis/*.json trade logs** — NOT regenerated; covered by `load_trade_log()` compat parser (AC33). Tools that read JSON directly via `json.load()` without the helper will silently lose new field information until migrated.
- **Three ClosedTrade dataclasses** — research/R159 left frozen; raw_backtest.py kept separate per Q3 (intentional schema disjoint, comment annotation prevents future confusion); only canonical v4/position.py gets new identity fields.
- **Architectural debt RESOLVED**: v5 ships with unified BarProcessor (Scope B) — no dispatcher fork.
- **Operational priority: memory leak fix**. v4 paper trader grows to 2GB+ RAM and requires manual restarts. Live paper runs at $450K across s513/s523c/s524m. v5 MUST ship with bounded memory guarantees (RollingCache with maxlen + slotted dataclasses + incremental signal computation). Any slip here directly impacts live operations. This justifies the v5/data/ package expansion.
- **Big-bang ship risk**: v5 ships as one unit (not phased). Mitigation: Phase 4 verification gates (s524 family v4 vs v5 parity) must pass before any production migration. Plus v4 stays running during the full v5 development and validation period — zero pressure on the ship deadline.
- **Strategy migration complexity**: Option Y + sizing redesign + multi-leg Order.legs means each strategy migration is a full rewrite (not an import swap). First reference migration (s524m) establishes the pattern; subsequent migrations are faster. Budget ~2-4h per strategy migration post-ship.

## Design Notes

(Produced in Phase 2 by read-only exploration of the codebase. Defines file-by-file changes, hook wiring, test infrastructure, implementation order, and risks. Verified against actual code at the cited line numbers.)

### File-by-File Change Map

| File | Change Type | Lines (approx) | What changes |
|------|-------------|----------------|--------------|
| `v4/position.py` | MODIFY | 10-69 (Position), 46-48,55 (DELETE partial_tp), 72-98 (ClosedTrade), 101-173 (PositionManager) | Add `Position.increase`, `reduce`, `reduce_fraction`, `leaves_qty`, `is_closed`; add fields `scale_count`, `scaling_events`, `r_anchor_price` (frozen at first entry per Q-DEC1), `_helper_state: dict` (Q-DEC3 closure state), `_scale_action_bar: int = -1` (C2 per-hourly-bar cap); DELETE `partial_tp_atr/pct/trail` and `partial_closed`. Add 5 ClosedTrade identity fields per AC29 + `has_scaling: bool = False`. PositionManager.close_position populates identity fields from `pos`. |
| `v4/scaling.py` | NEW | — | `ScaleAction`, `ScalingEvent`, `LinkedScalePolicy` enum; helpers `tp_ladder_atr/price/r`, `breakeven_plus_runner`, `add_at_price`, `add_on_profit_atr`, `confirm_and_add`, `combine`. |
| `v4/simulator.py` | MODIFY | 119, 189-256 (`_close_position` API extension per Q-DEC5), 286-379 (DELETE `_partial_close_position`), 510-535, 1287-89, 1373-75, 1569-71 (DELETE partial_tp in Position(...) calls), 1447 (ADV-cap reuse), 1460 (concentration reuse) | Add `increase_fills`, `contingent_fills`, `entry_scale_downs` to `SimulationState`. Rename partial_fills increments at 1174/1225/1472/1506 → `entry_scale_downs`. **Extend `_close_position` with `position_id_override`, `exec_type`, `triggered_by` params** (Q-DEC5). DELETE `_partial_close_position`. Insert Phase 2 `scale_check_fn` invocation. Add `book_reduce` wrapper for non-terminal reduces (terminal reduces delegate to `_close_position`). Implement AC36 linked auto-propagation with C4 dust-cascade sibling force-close. |
| `v4/exit_handlers.py` | MODIFY | 193-225 (DELETE PartialTPHandler), 222 (cycle-import), 463-465, 504-523 (run_exit_handlers Phase ordering) | DELETE `PartialTPHandler` entirely + in-method `from .simulator import _partial_close_position`. Modify `run_exit_handlers` to support Phase 2 = `scale_check_fn`. Drop `if isinstance(handler, PartialTPHandler):` block. |
| `v4/config.py` | MODIFY (additive) | 220-269 (StrategySpec), 284-298 (PortfolioConfig) | Add to StrategySpec: `scale_check_fn: object = None`, `linked_scale_policy: LinkedScalePolicy = INDEPENDENT`. Cross-field ValueError in `__post_init__` per AC36 + AC20. Add to PortfolioConfig: `min_close_notional_usd: float = 1.0`, `dust_fraction_of_min_position: float = 0.05`. |
| `v4/paper_state.py` | MODIFY | 36-95 (`_serialize_position`), 71-73,77 (DELETE partial_tp), 97-163 (`_deserialize_position`), `_serialize/_deserialize_closed_trade` | Add Position roundtrip for `scale_count`/`scaling_events`. Add ClosedTrade identity-field roundtrip with backward-compat defaults per AC31. |
| `v4/report.py` | MODIFY | 101-111 (extra_info), end-of-file (NEW helper) | Add `"increase_fills"`, `"contingent_fills"` keys. Add `load_trade_log(path)` per AC33 with `:scale_` mixed-format AssertionError. |
| `v4/engine.py` | MODIFY | 405-514 (StrategyResult), 444-447 | DELETE `partial_tp_atr/pct/trail` from StrategyResult per AC12. |
| `v4/signals.py` | MODIFY | 67-71, 565-569, 720-725 | DELETE partial_tp from TokenSignals dataclass + builders. |
| `v4/portfolio_signals.py` | MODIFY | 313-317, 444-448 | DELETE partial_tp getattr/setter pairs. |
| `v4/validation.py` | MODIFY | 368-372 | DELETE partial_tp in TokenSignals construction. |
| `v5/minute_exits.py` | NOT COPIED | — | Eliminated in v5 — bar_processor handles all resolutions natively. No separate sub-hourly dispatcher exists. |
| `v4/paper_engine.py` | MODIFY | 446, 506-517, 993-995, 1544-1546 + new per-tick hook | DELETE `_partial_close_position` import. REPLACE partial_tp block (506-517) with per-tick `scale_check_fn` invocation calling `Position.increase`/`Position.reduce` (Q-DEC2 revised — paper parity). Add startup defensive check raising NotImplementedError for `linked_scale_policy != INDEPENDENT`. DELETE partial_tp in `cand.get(...)` Position construction + `bar_data`. |
| `tools/raw_backtest.py` | MODIFY (or alias) | 103-120 | UNIFY ClosedTrade per AC32. **Schema currently disjoint** (raw uses `gross_pnl/fees/funding/slippage/net_pnl/metadata`) — see Open Question #3. |
| `v4/tests/test_position_scaling.py` | NEW | — | All Phase 3 acceptance tests T1-T19. seed=42. |
| `strategies/{s502,s76_s56,s524j,s501,s503}*.py` | MIGRATE | — | Replace `partial_tp_atr/pct/trail` with `scale_check_fn=tp_ladder_atr([...])`. None in production per AC12 verification. |
| `v4/tests/test_minute_exits.py`, `v4/tests/test_paper_sub_hourly.py`, `v4/tests/test_margin_calls.py`, `tests/test_realistic_sizing.py` | MODIFY | various | Cleanup partial_tp test references. |
| `knowledge/ARCHITECTURE.md` | APPEND | — | Add "Trade Identity Model" section per AC34. |

### New Files

- `v4/scaling.py` — helpers + `ScaleAction` + `ScalingEvent` + `LinkedScalePolicy` enum
- `v4/tests/test_position_scaling.py` — all Phase 3 tests T1-T19

### Hook/Wiring Points (AC6, AC18, AC36)

**Phase 2 invocation site for `scale_check_fn`**: prefer inserting in `simulator.py:_process_exits` (lines 382-535) right after `for handler in pos.exit_handlers: handler.update_state(...)` and BEFORE `exit_result = run_exit_handlers(...)`. This avoids changing `run_exit_handlers` signature (smaller blast).

**Pattern reference**: `entry_filter_fn` invocation at `simulator.py:998-1003` shows the existing strategy-callback try/except style. `CustomExitHandler.check_exit` (exit_handlers.py:417-423) shows the protocol-typed wrapper version.

**AC36 auto-propagation hook**: after primary's `Position.reduce` books a ClosedTrade, call same path on linked position with `triggered_by=primary's suffixed position_id` and `exec_type="linked_reduce"`. Full-close propagation already exists at simulator.py:522-529 — that path stays unconditional per AC36.

### Circular Import Risk (Reviewer B2 — RESOLVED)

`exit_handlers.py:222` currently does `from .simulator import _partial_close_position` (in-method import). After AC12 deletion: `Position.reduce` lives on `Position` (in `position.py`, which imports nothing from simulator). Cycle broken naturally. `scaling.py` should import only from `position.py` (use `TYPE_CHECKING` for type hints if needed).

### Test Infrastructure

- **No `conftest.py` exists in `v4/tests/`** (verified via Glob). Tests do `sys.path.insert(0, ...)` at top — see `test_minute_exits.py:24-26`. New test file MUST follow this pattern.
- **Position factory**: each test file defines local `_make_position(...)` (e.g. `test_minute_exits.py:67-127`). Reuse this style for `test_position_scaling.py`.
- **Config factory**: `_default_config(**overrides) -> PortfolioConfig` (e.g. `test_minute_exits.py:47-64`) with `base_spread_bps=0, impact_coeff=0, max_slip_bps=0` for deterministic accounting tests.
- **Fee/slippage**: real config; for accounting tests T1-T3, zero out slippage at the config level. Fees still apply via `get_fee_rate()`.
- **Funding**: directly assign `pos.cumulative_funding = X` after construction.

### Implementation Order (Phase 4 task ordering)

1. **T-IM-1: ClosedTrade identity fields + has_scaling** (AC29, AC30) — `v4/position.py`. ~1.5h. **[P]**
2. **T-IM-2a: Position dataclass field additions + properties** (AC1 fields, AC22 setup) — `v4/position.py`. ~1h. **[after: T-IM-1]**
3. **T-IM-2b: DELETE partial_tp from all consumers** (AC12 mechanical sweep) — signals.py, validation.py, portfolio_signals.py, engine.py:StrategyResult, simulator.py 4 sites, paper_state.py, paper_engine.py, minute_exits.py + tests. ~2h mechanical. **[after: T-IM-2a]**
4. **T-IM-3: scaling.py module** (`ScaleAction`, `ScalingEvent`, `LinkedScalePolicy` enum) — pure dataclasses. ~1h. **[P with T-IM-1]**
5. **T-IM-4: Position.increase / reduce / reduce_fraction methods** (AC1, AC2, AC3, AC4, AC5, AC9 reduce-side, AC14 dust, AC17 clamp, AC22 sign invariant) — `v4/position.py`. ~3h. **[after: T-IM-1, T-IM-2a, T-IM-3]**
6. **T-IM-5: DELETE PartialTPHandler + DELETE _partial_close_position + wire Position.reduce into BOTH dispatch paths (Q4)** (AC12, AC18 bar-resolution-agnostic) — `exit_handlers.py`, `simulator.py:286-379`, `minute_exits.py:19-24,300-336`, `paper_engine.py:446,506-517`. Sub-hourly path (`minute_exits.py`) calls SAME `Position.reduce` method as hourly path; `scale_check_fn` invocation wired into BOTH dispatchers (NOT just hourly). ~2.5h (was 1.5h — sub-hourly wiring adds ~1h per Q4). **[after: T-IM-4]**
7. **T-IM-6: Wire scale_check_fn Phase 2 + AC10 constraints + AC18 ordering** (AC6, AC10, AC18, AC19) — `simulator.py:_process_exits`. ~2.5h. **[after: T-IM-4, T-IM-5]**
8. **T-IM-7: AC36 LinkedScalePolicy auto-propagation** (AC36, AC26 contingent_fills) — `simulator.py` + `config.py` validation. ~2h. **[after: T-IM-6]**
9. **T-IM-8: Helpers in scaling.py** (`tp_ladder_*`, `breakeven_plus_runner`, `add_*`, `combine`) — pure closures with per-position fired-state dict. ~2h. **[after: T-IM-3, T-IM-4]**
10. **T-IM-9: paper_state.py serialize/deserialize** (AC31) — `paper_state.py`. ~1h. **[after: T-IM-1, T-IM-2a]**
11. **T-IM-10: report.py extra_info + load_trade_log compat parser** (AC26 reporting, AC33) — `report.py`. ~1h. **[after: T-IM-1]**
12. **T-IM-11: tools/raw_backtest.py ClosedTrade unification** (AC32) — see Open Question #3. ~1h. **[after: T-IM-1]** **[P with T-IM-7..10]**
13. **T-IM-12: Migrate 5 legacy strategies + DELETE partial_tp_atr/pct/trail in callers** (AC11, AC12 migration) — strategies/*.py + remaining test cleanup. ~2h. **[after: T-IM-8]**
14. **T-IM-13: ARCHITECTURE.md "Trade Identity Model"** (AC34) — docs. ~0.5h. **[P after: all]**
15. **T-IM-14: Paper engine wiring (Q-DEC2)** — wire `scale_check_fn` into `paper_engine.py` per-tick invocation for `linked_scale_policy=INDEPENDENT`; add defensive startup check rejecting PROPORTIONAL/ABSOLUTE with NotImplementedError (FIX session-establishment capability-declaration pattern). ~3h. **[after: T-IM-4, T-IM-6]**
16. **T-IM-15: ClosedTrade positional-arg audit (R2)** — grep for positional `ClosedTrade(` call sites across the codebase BEFORE Phase 4 starts; fix any that would break when 5 new fields are added. Keyword-arg call sites are safe. ~1h. **[BEFORE T-IM-1 — prerequisite task]**
17. **T-IM-16: Phase 4 verification gate (AC11 Q-DEC4)** — run full backtest of 5 production strategies (s513, s523c, s524m, s532, s540) + 5 migration strategies (s502, s76_s56, s524j, s501, s503) pre/post refactor; assert bit-exact pnl on ClosedTrade level. Must pass before `/review --final`. ~1h. **[after: ALL other T-IM tasks]**

### Implementation Risks

1. **`Position.reduce` needs `state` + `config` to book a ClosedTrade.** Position currently knows nothing about simulator. Two design choices:
   - (a) Pass `state, config` as method args (matches `_partial_close_position(state, pos, ...)` precedent) — simpler, but breaks pure-Position abstraction
   - (b) `Position.reduce` returns a `ReduceResult(closed_qty, closed_margin, closed_funding, exit_fee_to_book, partial_entry_fee, ...)` and the simulator-layer wrapper does the booking — cleaner separation, more code
   - **See Open Question #2** — needs user decision.

2. **`tools/raw_backtest.py` ClosedTrade schema is disjoint** from canonical (`gross_pnl/fees/funding/slippage/net_pnl/metadata` vs `pnl/funding_cost/entry_fee/exit_fee`). AC32 says "import canonical" but field surfaces don't match. **See Open Question #3** — needs user decision.

3. **paper_engine.py is "out of scope" but partial_tp deletion breaks imports** at module-load time. Mitigation: minimal cleanup (DELETE import + partial_tp block only); do NOT add scale_check_fn paper support. Make-it-compile change only. Same for `minute_exits.py:23, 326-335`.

4. **`tp_ladder_*` helper performance**: tracking fired levels via `pos.scaling_events` history scan is O(N_bars × N_levels). For 211 strategies × 8000 bars × 5 levels ≈ 8M scans. **Mitigation**: helper closures maintain per-position `_fired_set` keyed on `(pos.position_id, level_idx)` in a closure-scoped dict, cleared on `pos.is_closed`. Brief says "pure closures" but performance demands closure-state.

5. **AC22 sign invariant assertion edge cases**: `pos.is_closed or (pos.quantity * pos.direction > 0.0)` MUST be implemented exactly as spec'd (not `math.copysign`). Property test T5 at seed=42 to flush out edge cases before production deploy.

### Open Questions (NEED USER DECISION before Phase 3)

1. **AC26 misattribution corrected** (resolved during design): `state.partial_fills += 1` at simulator.py:1174, 1225, 1472, 1506 are ENTRY-side scale-down increments — they STAY. Brief AC26 incorrectly said they'd be removed. Need to update AC26 to clarify: `partial_fills` semantic = "any fill that didn't get full requested size" (FIX-aligned), covering BOTH entry scale-down AND non-terminal `Position.reduce`.
2. **Position.reduce signature** — option (a) state-passing or (b) result-bundle? See Risk #1.
3. **raw_backtest.py ClosedTrade unification** — adapter, schema-extension, or keep separate? See Risk #2.
4. **RESOLVED** — Sub-hourly dispatcher is unified into bar_processor in v5 (Scope B). No behavior break: sub-hourly bars are fed to bar_processor the same way hourly bars are. Strategies migrated from v4 to v5 get sub-hourly `check_scale()` invocation via the unified dispatcher at whatever resolution they declare in `exit_resolution`.
5. **Brief AC36 stop_override on auto-propagation** — confirmed: secondary's existing `stop_price` is unchanged on auto-propagated reduce (consistent with primary's reduce per AC5).

## Out of Scope for this v5 Release

v5 is the next hardened evolution — not a preview. Everything shipped with v5 must be production-ready. The list below is items that legitimately belong to FUTURE specs, not "Phase 2 of this work":

- **Migrating v4 strategies to v5** — separate per-strategy decisions; user-driven timing
- **Paper-side `linked_scale_policy=PROPORTIONAL`/`ABSOLUTE` auto-propagation** — async broker timing (primary fill confirmation → secondary reduce trigger) needs careful design; not required for any production strategy today
- **Adding 4h / daily `exit_resolution`** — additive on bar_processor; no strategy currently requests it
- **Multiple scale actions per bar on the same position** — enforced to one per bar by C2 cap; if a future strategy needs more, reopen the cap
- **Schedule-based declarative rules beyond the shipped helpers** — strategies needing complex scheduling override `check_scale()` explicitly
- **Integration tests running full strategy portfolios with scaling enabled** — unit tests (T1-T20 + TG1-TG6 + T13b) cover correctness; full-portfolio stress tests happen at Phase 4 verification gate against v5 itself
- **paper_shadow.py port** — observability layer for dual-wallet spot+perp tracking; port when production paper needs it. Multi-leg Order.legs (C-7) is the PRIMITIVE; paper_shadow is the OBSERVABILITY on top.
- **Sentinel stack port** — never went live; port only if a strategy needs real-time stop-breach detection
- **Eventually freezing v4** — happens after all production strategies migrate; separate decision
