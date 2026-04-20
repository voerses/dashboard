# Milestone 2: Position Scaling

**Add `Position.increase` / `Position.reduce` primitives with weighted-average entry, composable helpers, and FIX-aligned trade identity to v5.**

## Problem

The v4 engine has a rigid three-state position lifecycle: open at fixed size, optional one-shot partial TP, full exit. Analysis shows:

- Winners establish direction by 48-72h and want 50% entry + 50% on confirmation
- Fat-tail winners need multi-level partial-out to lock profit while keeping a runner
- Losers are identifiable by day 2-3 and want early cut to 25% size
- Current one-shot `partial_tp` + fixed entry cannot express any of this

Exchange reality: one (strategy, token, market) = ONE position. Exchanges track NET quantity. Scaling (add-to / reduce) is the correct model, not parallel positions.

## Dependencies

- **M1 (v5 fork)**: v5/ directory exists as a clean copy of v4 with dead code removed. All M2 work targets `v5/` files exclusively.

## Scope

### In scope (this milestone)

- `Position.increase`, `Position.reduce`, `Position.reduce_fraction` methods on `v5/position.py`
- `ReduceResult` bundle (full field list in AC4) and `ScalingEvent` dataclass (full 11-field definition in AC13)
- New Position fields: `scale_count`, `scaling_events`, `r_anchor_price`, `_helper_state`, `_scale_action_bar`
- `ScaleAction` dataclass in `v5/strategy_api.py` (new file; will also host `Strategy` Protocol in M7 — creating the module now is the right namespace, not a premature split)
- `scale_check_fn` field on StrategySpec (or `Strategy.check_scale` method if M7 Strategy Protocol lands first)
- Wiring into `v5/simulator.py` AND `v5/minute_exits.py` (both dispatch paths)
- Per-bar invocation cap: one scale action per (position, hourly bar)
- `LinkedScalePolicy` enum (INDEPENDENT default; PROPORTIONAL/ABSOLUTE with defensive NotImplementedError in paper engine)
- ClosedTrade identity fields with backward-compatible defaults
- Portfolio constraints on increase (ADV cap, concentration, margin, min size)
- Composable helper DSL in `v5/helpers.py` (canonical location; `scaling.py` mentioned in early draft is retired)
- `load_trade_log()` compat parser for v4 `analysis/*.json`
- Diagnostic counters: `partial_fills`, `increase_fills`, `contingent_fills`, `entry_scale_downs`
- Paper engine wiring for `linked_scale_policy=INDEPENDENT`

### Deferred (later milestones)

- Unified BarProcessor (single dispatcher replacing 3 dispatch paths) -- separate milestone
- Unified Strategy Protocol with `Strategy.check_scale` method -- M7
- Paper-side PROPORTIONAL/ABSOLUTE auto-propagation (async broker timing)
- Multiple scale actions per bar (cap enforces one)
- Strategy migrations from v4 to v5
- Sizing redesign (2-intent engine)
- Data architecture overhaul

## Acceptance Criteria

AC numbering matches the full brief (`/workspace/crypto_backtest/.specs/active/position-scaling/brief.md`) where applicable.

### Add side

**AC1 -- Position.increase**: `Position.increase(qty_to_add, fill_price, margin_delta, bar_idx, stop_override=None, fee_rate=None, atr=None, freeze_initial_risk=True)`:
- `qty_to_add` is unsigned absolute base units
- Weighted-average entry price (WACB / FIX `AvgPx`): `(old_qty_abs * old_entry + qty_to_add * fill_price) / (old_qty_abs + qty_to_add)`. This is position cost-basis tracking, not execution VWAP (which is a benchmark metric, separately captured via `slippage_bps` per AC8).
- `pos.margin_usd += margin_delta`
- `pos.quantity += pos.direction * qty_to_add`
- Appends ScalingEvent with `kind="increase"` to `pos.scaling_events`
- Does NOT book a ClosedTrade

**AC2 -- stop_price on increase**:
- If `stop_override` provided: `pos.stop_price = stop_override`
- If None: never-loosen default (long: max of old stop and new_avg_px - stop_mult * atr; short: min). `new_avg_px` = weighted-average entry after the fill.

**AC3 -- initial_risk default-frozen, configurable via increase flag, earned state preserved**:
- `Position.increase(..., freeze_initial_risk: bool = True)` — default matches v4-like semantics (R levels stay anchored to the original entry's risk).
- When `freeze_initial_risk=False`, increase recomputes `initial_risk = stop_mult * atr` against the new weighted-average entry price so R-based ladders/TP helpers compute off current-risk rather than original-risk.
- `breakeven_triggered` stays True if already earned (not reset by increase regardless of freeze flag).
- `highest` / `lowest` continue tracking.
- `r_anchor_price` is tied to the frozen-vs-refresh decision: frozen when `freeze_initial_risk=True`, refreshed to new weighted-average entry when `False`.
- `tp_ladder` helper in `v5/helpers.py` MUST expose `risk_mode: Literal["frozen", "current"]` so ladder consumers make the choice explicit at helper-construction time, not at increase time.

**AC5 -- entry_price unchanged on reduce**: weighted-average entry (cost basis) stays the same; only quantity/margin/cumulative_funding shrink.

### Reduce side

**AC4 -- Position.reduce**: `Position.reduce(qty_to_close, fill_price, bar_idx, fee_rate, atr, full_entry_fee, dust_usd, triggered_by="", is_stop_like=False) -> ReduceResult`:
- `is_stop_like` is copied verbatim onto the appended `ScalingEvent.is_stop_like` (AC13) so post-hoc analysis can distinguish stop/trail reduces from voluntary reduces. The `book_reduce` wrapper reads this flag to pick the slippage model (stressed vs. normal) per AC28b BEFORE calling `Position.reduce`, so the `fill_price` the position receives is already-adjusted for the correct slippage regime. `Position.reduce` itself stays pure-accounting.
- `qty_to_close` is unsigned absolute base units
- Returns `ReduceResult` bundle:
  ```python
  @dataclass
  class ReduceResult:
      closed_qty_signed: float             # signed in pos-direction units
      closed_qty_abs: float                # absolute |closed_qty_signed|
      closed_margin: float                 # margin pro-rata removed from position
      closed_funding: float                # cumulative_funding pro-rata removed
      partial_entry_fee_to_book: float     # -> ClosedTrade.entry_fee
      partial_entry_fee_remaining: float   # -> state._entry_fees_by_pos[pos_id] new value
      exit_fee: float                      # -> ClosedTrade.exit_fee (on closed notional, taker rate)
      slip_bps: float                      # -> ScalingEvent.slippage_bps
      gross_pnl: float                     # closed_qty_signed * (fill_price - entry_price)
      is_terminal: bool                    # True if reduce promoted to full close (AC14)
      suffix: str                          # ":scale_N" or ":scale_N_final"
  ```
  Field rationale: `gross_pnl` and `exit_fee` needed by simulator wrapper to update `state.realized_pnl` and `state.total_fees`. `slip_bps` populates `ScalingEvent.slippage_bps`. `closed_qty_signed` avoids the wrapper recomputing sign after mutation.
- `pos.quantity *= (1 - close_fraction)` (sign-safe)
- `pos.margin_usd *= (1 - close_fraction)`
- `pos.cumulative_funding *= (1 - close_fraction)` (pro-rated share returned in ReduceResult)
- Appends ScalingEvent with `kind="reduce"`
- `Position.reduce_fraction(fraction, ...)` is a thin convenience wrapper
- Simulator wrapper `book_reduce()` handles ClosedTrade booking (see code sketch below)
- Terminal reduces delegate to `_close_position` per Q-DEC5 (one math path)

**Simulator `book_reduce()` wrapper** (~30 lines, MOST error-prone integration point):
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
        # Q-DEC5: Delegate to existing terminal-close path with overrides --
        # ONE terminal-close math path (FIX one-ExecutionReport canon).
        # Increment scale_count BEFORE delegating so exec_seq is correct
        # on the terminal ClosedTrade.
        pos.scale_count += 1
        try:
            _close_position(state, pos, fill_price,
                            exit_reason="dust_promoted_reduce",
                            exit_bar=bar_idx,
                            exit_adv=sig.adv[bar_idx], config=config,
                            position_id_override=f"{pos.position_id}{result.suffix}",
                            exec_type="reduce",
                            triggered_by=triggered_by)
        except Exception:
            pos.scale_count -= 1  # rollback (symmetric with non-terminal path)
            raise
    else:
        # Non-terminal reduce -- book partial ClosedTrade directly.
        # Full field list (no `...` — exhaustive to prevent field-drift bugs):
        pos.scale_count += 1
        try:
            closed = ClosedTrade(
                # Identity (AC29)
                position_id=f"{pos.position_id}{result.suffix}",
                parent_position_id=pos.position_id,
                exec_seq=pos.scale_count,
                exec_type="reduce",
                is_terminal=False,
                triggered_by=triggered_by,
                has_scaling=True,  # reduce itself appended a ScalingEvent
                scaling_events=list(pos.scaling_events),  # snapshot at booking
                # Core trade (copied/derived from Position)
                token=pos.token,
                strategy_id=pos.strategy_id,
                leg=pos.leg,
                entry_bar=pos.entry_bar,
                exit_bar=bar_idx,
                entry_price=pos.entry_price,
                exit_price=fill_price,
                direction=pos.direction,
                margin_usd=result.closed_margin,
                pnl=result.gross_pnl - result.exit_fee - result.closed_funding,
                funding_cost=result.closed_funding,
                entry_fee=result.partial_entry_fee_to_book,
                exit_fee=result.exit_fee,
                hold_bars=bar_idx - pos.entry_bar,
                exit_reason="partial_reduce",  # orthogonal to exec_type per AC30
                is_perp=pos.is_perp,
                entry_timestamp=pos.entry_timestamp,
                exit_timestamp="",  # filled by caller in paper mode
                # Limit-order metadata (inherit from Position, per v4 convention)
                limit_price=pos.limit_price,
                limit_placed_at=pos.limit_placed_at,
                stop_limit_price=pos.stop_limit_price,
                fill_source=pos.fill_source,
            )
            state.position_manager.closed_trades.append(closed)
            state._entry_fees_by_pos[pos.position_id] = result.partial_entry_fee_remaining
            state.total_fees += result.exit_fee
            state.realized_pnl += closed.pnl
            state.partial_fills += 1  # reduce-only semantic
        except Exception:
            pos.scale_count -= 1  # rollback
            raise
```

**`_close_position` API extension (Q-DEC5)**: add optional parameters `position_id_override: str | None = None`, `exec_type: str = "exit"`, `triggered_by: str = ""`. Default behavior unchanged (non-scaling closures use `pos.position_id` and `exec_type="exit"`). Dust-promoted reduces pass overrides for the suffix scheme per AC4.

**AC4 suffix scheme**: ClosedTrade.position_id uses `parent:scale_N` for non-terminal, `parent:scale_N_final` for terminal reduce. `scale_count` incremented before booking with rollback on failure.

### Hook wiring

**AC6 -- scale_check_fn**: `scale_check_fn(pos: Position, ctx: BarContext) -> ScaleAction | None | list[ScaleAction]` (or `Strategy.check_scale` method):
- Signature matches `exit_check_fn` — `BarContext` exposes atr/regime/volume/funding_zscore/vol_20/ret_1h so scale decisions are data-driven.
- Wired into both hourly and sub-hourly dispatch paths.
- Runs in try/except (exceptions logged, not crash).
- `ScaleAction(qty_delta, reason, stop_override=None, is_stop_like=False)` -- positive = increase, negative = reduce. `is_stop_like` (typed bool, default False) marks stop/trail-like reduces so the `book_reduce` wrapper applies `stress_adv_multiplier` per AC28b. Strategies/helpers set `True` for stop-emulating reduces; TP ladders and voluntary profit-taking leave it `False`.
- Return type is `None` (no action), a single `ScaleAction`, or `list[ScaleAction]` (TP-ladder case — multiple rungs fired simultaneously by the same bar's price movement).
- Returning `list[ScaleAction]`: actions execute in list order. Per-bar cap (AC18) still enforces ONE strategy invocation per hourly bar, but a single invocation may dispatch multiple rungs — this prevents the "3-rung TP ladder loses rungs when price rips through all levels in one bar" drop-bug.
- Returns None for no action.

### Accounting correctness

**AC7 -- fee booking**:
- Each fill incurs entry/exit fees via existing fee schedule
- Increase: `state._entry_fees_by_pos[pos_id] += new_entry_fee`
- Reduce: pro-rate entry fee by close_fraction; exit fee on closed notional

**AC8 -- slippage per fill**: ADV-based slippage computed on fill notional (not total position). Adjusts fill_price: longs pay up on `increase`, down on `reduce`; shorts opposite. Recorded in ScalingEvent. **Slippage adjustment point**: `fill_price` passed to `Position.reduce` (and `Position.increase`) is PRE-ADJUSTED for slippage by the engine wrapper. The engine computes `adj_price = bar.close +/- slippage_model(notional, adv)`, then calls `pos.reduce(adj_price, ...)`. Position is pure accounting -- it never knows about slippage. Matches FIX `LastPx(31)` = actual execution price. Same pattern as v4 (`simulator.py:212-216`).

**AC9 -- funding pro-rating**:
- Reduce splits cumulative_funding by close_fraction
- Increase leaves past cumulative_funding unchanged
- **Invariant (QC1 -- revised for interleaved increase/reduce)**: at any instant,
  ```
  sum(booked_funding in ClosedTrades for this parent_position_id) + pos.cumulative_funding
     == sum(all per-bar funding accruals to date on this position)
  ```
  This holds REGARDLESS of whether increases interleave with reduces (the pre-reduce funding gets its pro-rata share booked; post-increase accruals add to `pos.cumulative_funding` on the new larger size). The original "sum of all closed + final = total paid" invariant only held for pure-reduce-sequence; this one is correct for ALL sequences.
  T1 (pure reduces) and T1b (interleaved increase+reduce) both must verify this.

### Observability

**AC13 -- ScalingEvent dataclass** (full 12-field definition):
```python
@dataclass
class ScalingEvent:
    bar: int
    kind: str                           # "increase" or "reduce" (FIX-aligned vocabulary)
    fill_price: float
    qty_delta: float                    # signed actual: + on increase, - on reduce
    requested_qty_delta: float          # signed requested by strategy BEFORE AC10 constraint clamping (I3)
    margin_delta: float                 # signed actual: + on increase, - on reduce
    fill_notional: float               # absolute |qty_delta| * fill_price
    entry_fee_delta: float             # > 0 on increase
    exit_fee: float                    # > 0 on reduce
    slippage_bps: float                # actual slippage paid (already reflects stress multiplier if is_stop_like)
    atr_at_event: float
    is_stop_like: bool                 # copied from ScaleAction.is_stop_like; False for increases, varies for reduces (AC28b)
```
- For un-clamped events: `requested_qty_delta == qty_delta`. For AC10-clamped increases: `abs(requested_qty_delta) > abs(qty_delta)`. For all `reduce` events (no AC10 constraints): always equal.
- **`requested_qty_delta` vs `qty_delta`**: when AC10's concentration scale-down clamps an `increase`, `requested_qty_delta` records the strategy's original intent and `qty_delta` records what actually executed. Critical for post-hoc analysis ("did my strategy under-add because it WANTED to, or because concentration choked it?").
- Final `ClosedTrade.scaling_events` = copy of position's events at close time

### Portfolio constraints (increase only)

**AC10 -- constraints on increase**:
- ADV cap: per-fill check (not cumulative)
- Concentration: cumulative check (subtracts existing position margin)
- Margin availability: standard check
- Min increase size: skip if below min_position_usd
- All skips are silent (increment rejection counters)
- Each constraint logs which was binding

### New config fields

**AC10a -- PortfolioConfig defaults for dust/min-close** (added to `v5/config.py` PortfolioConfig dataclass):
- `min_close_notional_usd: float = 1.0` -- minimum notional value for a reduce to be worth booking as a ClosedTrade
- `dust_fraction_of_min_position: float = 0.05` -- fraction of `min_position_usd` used in dust threshold calculation

These are used in `book_reduce()`: `dust_usd = max(min_close_notional_usd, dust_fraction_of_min_position * min_position_usd)`.

### Edge cases

**AC14 -- dust threshold**: `dust_usd = max(min_close_notional_usd, dust_fraction * min_position_usd)`. If remaining notional < dust_usd after reduce, promote to full close. Set `pos.quantity = 0.0` by assignment (not arithmetic).

**AC14a -- dust-promoted reduce is equity/attribution-equivalent to a non-dust reduce**: The ClosedTrade booked via the `_close_position` override path (when `result.is_terminal=True`) must be indistinguishable from a reduce that happened to fully-close on its own, for all downstream consumers:
- Equity curve deltas match to the cent
- Per-strategy attribution (`strategy_stats[sid]["pnl"]`) matches
- Dashboard trade log display shows `exec_type="reduce"` (NOT `"exit"`), `is_terminal=True`, `exit_reason="dust_promoted_reduce"`
- `position_id` uses the `:scale_N_final` suffix (per AC4)
- **Test design (boundary-correct)**: use explicit notional epsilon `EPS_NOTIONAL_USD = 0.10` (constant in the test). Construct a position where remaining notional after the reduce is deliberately within ±`EPS_NOTIONAL_USD` of `dust_usd` (both sides). Example: `pos.quantity * close ≈ 100 * dust_usd`; issue one reduce that leaves `remaining_notional = dust_usd + EPS_NOTIONAL_USD` (NON-terminal, books a partial ClosedTrade with `exec_type="reduce"`, `is_terminal=False`) and compare to a reduce that leaves `remaining_notional = dust_usd - EPS_NOTIONAL_USD` (terminal via promotion, books a ClosedTrade with `exec_type="reduce"`, `is_terminal=True`, `exit_reason="dust_promoted_reduce"`). Assert: the realized PnL deltas for the two reduces differ ONLY by the last (`2 * EPS_NOTIONAL_USD`-notional * fees + slippage) slice — i.e., equity curve, attribution, and identity invariants cross the dust boundary without a discontinuity. NOTE the flawed earlier phrasing ("0.999 vs 1.0 of pos.quantity") is removed — `dust_usd` is a notional threshold, not a fraction of position.

**AC15 -- no force-close after increase**: Even if new stop is already breached, StopLossHandler fires naturally in Phase 3.

**AC16 -- trailing stop state preserved**: `pos.highest`/`pos.lowest` continue tracking across increase.

**AC17 -- qty_delta sign convention**:
- Positive = grow position (routed to increase)
- Negative = shrink position (routed to reduce)
- Over-close clamp: if abs(qty_delta) >= abs(pos.quantity), clamp to full close
- No direction flip via scaling

**AC18 -- order of operations per bar (multi-action semantics)**:
0. **Phase 0 (pre-existing, unchanged): funding accrual** — per-bar funding is added to `pos.cumulative_funding` BEFORE any Phase 1/2/3 handling. This is the invariant anchor for QC1: funding accrues to the pre-scale position; reduces in Phase 2 pro-rata that accrued amount; increases in Phase 2 do NOT retroactively change already-accrued funding (new size accrues on the NEXT bar). Spec this explicitly because QC1 correctness is order-sensitive — if funding were to accrue AFTER Phase 2, a Phase 2 reduce would lose the current bar's funding share, and a Phase 2 increase would gain the current bar's funding share on pre-increase size. Placing funding at Phase 0 makes the invariant hold trivially.
1. Phase 1: handler.update_state (uses OLD entry_price/initial_risk)
2. Phase 2: scale_check_fn (only side-effect mechanism)
3. Phase 3: check_exit chain (sees updated stop_price from Phase 2)
- One `scale_check_fn` **invocation** per (position, hourly bar) regardless of sub-hourly cadence. A single invocation may return `list[ScaleAction]` (AC6) for multi-rung TP ladders — ALL rungs in that list execute in one bar.
- **Per-hourly-bar invocation cap (C2)**: enforced via `pos._scale_action_bar: int` (hourly bar index of last fired invocation; -1 initially). If a sub-hourly tick fires a scale invocation on a position, NO further invocation fires on that position until the next hourly bar.
  - Rationale: prevents pathological cascades (a strategy returning `ScaleAction(qty_delta=-0.05*qty)` every 5-min tick would fire 12 reduces/hour, eating through dust threshold, creating ~100k spurious ClosedTrades across a portfolio backtest).
  - Mirrors the `partial_closed: bool` one-shot semantic of the retired PartialTPHandler -- without this cap, we'd get a regression worse than today.
  - Cap is on **invocations**, not actions: `list[ScaleAction]` from one invocation is permitted (critical for TP ladders); repeated invocations per bar is not.

**AC20 -- one position per (strategy, token, market)**: `max_concurrent_per_token > 1` + `scale_check_fn` raises ValueError.

**AC21 -- time counters never reset on increase**: `entry_bar`, `entry_timestamp` unchanged. All `bars_held` calculations count from original entry.

**AC22 -- quantity-sign invariant**: After any increase/reduce: `pos.is_closed or (pos.quantity * pos.direction > 0.0)`. Sign-of-product check handles negative-zero.

**AC24 -- one scale invocation per (position, bar), may return multiple actions**: First non-None return from `scale_check_fn` is executed. If the return is `list[ScaleAction]`, ALL actions execute in list order (sequentially, each mutating position state before the next is processed). `scale_check_fn` is NOT re-invoked that bar.

**AC24a -- list[ScaleAction] execution rules (order-sensitive)**:
- Actions execute strictly in list order; each sees the state mutated by all predecessors.
- **AC10 (portfolio constraints) re-evaluates per action**, not once for the batch — e.g., an increase in position 3 of the list must re-check ADV/concentration/margin against the position size AFTER reductions 1 and 2. Any clamped action records `requested_qty_delta` vs actual `qty_delta` per AC13.
- **Terminal action halts remainder**: if action N triggers `is_terminal=True` (e.g., dust-promoted reduce via AC14, or explicit full close), actions N+1..end of list are silently DROPPED (position no longer exists). Record dropped actions as a single diagnostic counter `scale_actions_dropped_after_terminal` and emit a single log message at **WARNING** level per drop event (logged via `logger.warning`; rate-limited per-strategy-per-bar via the standard warning cache pattern). Do NOT raise; strategies legitimately queue fallback actions. A WARNING (vs INFO) is justified because dropped actions usually indicate either a helper-level bug (e.g., TP ladder with a full-close rung followed by more rungs) or a misconfigured strategy — both are recoverable but deserve attention.
- **Mixed-direction lists are allowed but unusual**: a list containing both increase and reduce (e.g., `[ScaleAction(-0.3*qty, "tp_rung_1"), ScaleAction(+0.1*qty, "add_on_dip")]`) is not rejected. Execution order = list order; each action mutates state for the next.
- `scaling_events` append ordering matches execution ordering.

**AC25 -- error handling**: scale_check_fn exceptions logged, treated as no-action.

**AC27 -- increase does not count against max_concurrent_per_token**: Mutates existing Position; no new Position created.

**AC28 -- liquidation uses post-scale state**: Liquidation check reads mutated entry_price, margin_usd, quantity, cumulative_funding after increase/reduce.

**AC28a -- entry_filter_fn does NOT gate increases**: `entry_filter_fn` fires only on NEW position entries (mutating `SimulationState.position_manager`). Scale-ins via `scale_check_fn` bypass `entry_filter_fn` entirely — rationale: entry filters are for "should this trade open?" semantics; scale-in is "how should the open position grow?". Strategies that want to gate scale-ins on trade-history metrics should embed that logic inside `scale_check_fn` itself (it has access to `BarContext`).

**AC28b -- stress_adv_multiplier applies to stop-like scale reduces only, via typed `is_stop_like` flag on ScaleAction**: In v4, `config.stress_adv_multiplier` widens slippage on `stop`/`margin_call` exits (simulating liquidity drying up during stop-outs). For M2: add `is_stop_like: bool = False` as a field on `ScaleAction` — strategies/helpers set `True` when the reduce is intended as a stop/trail (replicating v4 stop slippage semantics), `False` (default) otherwise. The `book_reduce` wrapper reads this typed flag and applies `stress_adv_multiplier` only when `True`. No string-parsing of `reason` — `reason` stays free-form for diagnostics only.

### Trade identity (FIX/Nautilus-aligned)

**AC29 -- ClosedTrade identity fields** (all with backward-compat defaults):
- `parent_position_id: str = ""` (FIX OrderID(37))
- `exec_seq: int = 0` (0=full close, 1..N=scale executions)
- `exec_type: str = "exit"` ("reduce" | "exit" | "linked_reduce")
- `is_terminal: bool = True` (FIX OrdStatus=Filled(2))
- `triggered_by: str = ""` (FIX ClOrdLinkID(583))
- `has_scaling: bool = False` (convenience flag -- see trigger definition below)
- `scaling_events: list[ScalingEvent] = field(default_factory=list)` (snapshot of parent Position's scaling_events at close time)

**`has_scaling` trigger definition**: Set `True` on any ClosedTrade where the parent Position had >=1 ScalingEvent at close time. Both the terminal ClosedTrade AND all prior reduce ClosedTrades for that parent get `has_scaling=True`. For non-terminal reduces booked mid-life, `has_scaling` is set at booking time based on current `len(pos.scaling_events) >= 1` (which is always True since the reduce itself appends a ScalingEvent before booking).

**AC30 -- field semantics**: `exec_type` is orthogonal to `exit_reason`. `exit_reason` stays as granular cause ("stop", "take_profit", etc.). Exactly one ClosedTrade per parent has `is_terminal=True`.

**AC31 -- paper_state serialization**: New fields roundtrip through paper_state. Old state files load with derived defaults.
- **AC31a -- live-state compat fixture**: Test MUST include at least one realistic v4 paper-state snapshot. Since v5 paper-state doesn't exist live yet, the fixture captures the **v4 format** that v5 must read-compat-migrate from (the interesting compat direction is v4→v5, not v5→v5 roundtrip).
  - Fixture source: copy `state/v4_paper_s501/*.json` at test-authoring time into `.specs/active/m2-position-scaling/tests-snapshot/fixtures/v4_paper_state_s501.json`. Anonymize if sensitive (scrub exchange credentials; position/trade data is fine).
  - Test path 1 (v4→v5 migration): load v4 fixture via v5's compat path, assert it parses without errors, assert new fields get derived defaults (`parent_position_id=""`, `exec_seq=0`, `is_terminal=True`, etc. per AC29).
  - Test path 2 (v5 roundtrip, once implemented): serialize v5-state with scaling_events populated, deserialize, assert equality including list-order for `scaling_events`.
  - Test path 1 is required for M2; test path 2 is a natural extension but may ship in M3 (paper memory fix) if scope-creep concerns arise.

**AC33 -- load_trade_log() compat parser**: Reads v4 `analysis/*.json` with `:partial` suffix; synthesizes identity fields. Fails loud on mixed-format logs (`:scale_` suffix without identity fields).

**AC34 -- cutover**: Legacy `position_id` retained. `:partial` suffix retired in v5.

### Linked positions

**AC36 -- LinkedScalePolicy**:
- `INDEPENDENT` (default): no propagation; runtime warning on reduce of linked position
- `PROPORTIONAL`: primary fraction propagates to secondary; `exec_type="linked_reduce"`
- `ABSOLUTE`: primary qty propagates to secondary (clamped if exceeds)
- **Dust-cascade invariant (C4)**: after PROPORTIONAL or ABSOLUTE auto-propagation, if EITHER leg was promoted to terminal (via AC14 dust threshold), the engine MUST force-close the OTHER leg with `exit_reason="linked_exit"`. Implementation: after `book_reduce(secondary, ...)` returns, check `if primary.is_closed or secondary.is_closed: close the other via _close_position(reason="linked_exit")`. End-state invariant: `primary.is_closed == secondary.is_closed` (both or neither).
- Paper engine: INDEPENDENT wired; PROPORTIONAL/ABSOLUTE raise NotImplementedError at startup

### Counters

**AC26 -- diagnostic counters**:
- `entry_scale_downs` (incremented when AC10 concentration/ADV/capital clamps reduce the requested increase size below the strategy's intent -- i.e., when `abs(ScalingEvent.qty_delta) < abs(ScalingEvent.requested_qty_delta)` on an increase. Reference sites in v4: `simulator.py:1174`, `simulator.py:1225`, `simulator.py:1472`, `simulator.py:1506`)
- `partial_fills` (repurposed: reduce-only)
- `increase_fills` (new)
- `contingent_fills` (new: linked-leg auto-propagations)

## FIX Vocabulary Mapping

**Note:** `exec_type` values (`"reduce"`, `"exit"`, `"linked_reduce"`) are project-specific enumerations inspired by FIX protocol vocabulary, NOT actual FIX enum values. The mapping below shows conceptual alignment, not wire-format equivalence.

| Project type | FIX/Nautilus equivalent | Notes |
|---|---|---|
| `qty_to_add` (unsigned) | FIX `LastQty(32)` | increase parameter |
| `qty_to_close` (unsigned) | FIX `LastQty(32)` | reduce parameter |
| `qty_delta` (signed) | FIX NewOrderSingle direction | ScaleAction field |
| `pos.leaves_qty` | FIX `LeavesQty(151)` | `abs(pos.quantity)` |
| `pos.is_closed` | Nautilus `position_side == FLAT` | `pos.quantity == 0.0` |
| `parent_position_id` | FIX `OrderID(37)` | immutable parent join key |
| `exec_seq` | FIX `ExecID(17)` derived | monotonic per parent |
| `exec_type` | FIX `ExecType(150)` | lifecycle phase |
| `is_terminal` | FIX `OrdStatus=Filled(2)` | terminal-fill marker |
| `triggered_by` | FIX `ClOrdLinkID(583)` | auto-propagation ref |
| `LinkedScalePolicy` | FIX `ContingencyType(1385)` | INDEPENDENT/PROPORTIONAL/ABSOLUTE |
| `ReduceResult` | Inspired by domain-driven design separation | state-free domain bundle (pure accounting result, no side effects) |

## Position State Mutation Summary

| Field | After increase | After reduce |
|---|---|---|
| `entry_price` | Weighted-average entry updated (FIX `AvgPx`) | Unchanged |
| `quantity` | `+= direction * qty_to_add` | `*= (1 - close_fraction)` |
| `margin_usd` | `+= margin_delta` | `*= (1 - close_fraction)` |
| `cumulative_funding` | Unchanged | `*= (1 - close_fraction)` |
| `initial_risk` | FROZEN when `freeze_initial_risk=True` (default, v4-like); REFRESHED to `stop_mult * atr` at new weighted-average entry when `freeze_initial_risk=False` (AC3) | Unchanged |
| `entry_bar` / `entry_timestamp` | Unchanged | Unchanged |
| `breakeven_triggered` | Preserved (independent of `freeze_initial_risk`) | Preserved |
| `highest` / `lowest` | Continue tracking | Continue tracking |
| `stop_price` | stop_override or never-loosen | Unchanged |
| `scale_count` | Unchanged | +1 before booking |
| `r_anchor_price` | FROZEN when `freeze_initial_risk=True`; REFRESHED to new weighted-average entry when `False` (AC3) | Unchanged |
| `_helper_state` | Helpers may mutate | Helpers may mutate |
| `_scale_action_bar` | Set to current hourly bar | Set to current hourly bar |

## Helper API (`v5/helpers.py`)

Composable closures that translate strategy intent to absolute-unit primitives. Each returns `(pos, bar) -> ScaleAction | None`.

### Reduce helpers

| Helper | Behavior |
|---|---|
| `tp_ladder_atr([(2.0, 0.3), (4.0, 0.3)])` | Reduce fraction at each ATR-profit threshold |
| `tp_ladder_price([(78000, 0.3), ...])` | Reduce fraction at each absolute price level |
| `tp_ladder_r([(1.0, 0.33), ...], anchor="original")` | Reduce fraction at each R-multiple of initial_risk. Default anchor=original (frozen r_anchor_price per Q-DEC1); opt-in anchor=avg_px (weighted-average entry) |
| `breakeven_plus_runner(partial_r=1.0, fraction=0.5)` | One-shot reduce at breakeven, keep runner (see Q5 interaction below) |

### Increase helpers

| Helper | Behavior |
|---|---|
| `add_at_price([(71000, 0.3), ...])` | Add fraction at price pullback triggers |
| `add_on_profit_atr([(1.5, 0.5), ...])` | Pyramid: add when profit crosses ATR thresholds |
| `confirm_and_add(confirm_bars=48, min_profit_atr=0.5, add_fraction=1.0)` | Enter small, confirm thesis, add rest |

### Composition

| Helper | Behavior |
|---|---|
| `combine(*fns)` | Run helpers in order; return first non-None ScaleAction |

All helpers track fire-state via `pos._helper_state` (Q-DEC3). Strategies have full escape hatch: write raw `scale_check_fn` returning `ScaleAction(qty_delta=...)`.

**Breakeven_plus_runner interaction with BreakevenRatchetHandler (Q5)**:
If `config.break_even_atr` is also set, BOTH the helper AND the handler may fire on the same bar in different phases of AC18:
1. Phase 1: BreakevenRatchetHandler moves `pos.stop_price` to entry (state mutation)
2. Phase 2: `breakeven_plus_runner` helper triggers `Position.reduce(fraction)` (side-effect)
3. Phase 3: StopLossHandler sees BOTH the new stop AND the now-reduced position; if `bar.low`/`bar.high` already breached the moved stop, the runner closes IMMEDIATELY this same bar with `reason="stop"`

This is the desired "lock-in breakeven AND get out if instant whip" behavior. Use `breakeven_plus_runner` WITHOUT `break_even_atr` if you want the runner to keep its original stop after the partial close.

## Key Design Decisions (Locked)

- **Q-DEC1**: R-multiple anchor defaults to `r_anchor_price` (frozen at first entry), not weighted-average entry (avg_px)
- **Q-DEC2**: Paper engine wires INDEPENDENT only; PROPORTIONAL/ABSOLUTE raise NotImplementedError
- **Q-DEC3**: Helper fire-state stored on `pos._helper_state: dict` (per-position, not module globals)
- **Q-DEC5**: Terminal reduces delegate to `_close_position` (one terminal-close math path)

## Paper State Migration (v1 -> v2)

v5 paper_state schema changes are too large to roundtrip from v4. `STATE_VERSION` bumps to 2. Deserializing a v1 state file in v5 raises `ValueError` by default -- migration is explicit.

**`v5/migrate_state_v1_to_v2.py`** -- one-shot converter CLI:
```bash
python -m v5.migrate_state_v1_to_v2 \
    --input state/v4_paper_s513/state.json \
    --output state/v5_paper_s513/state.json
```

Transformations applied:
- Strip removed fields: `exit_regimes`, `exit_regimes_long`, `exit_regimes_short`, `regime_exit_min_bars`, `last_known_regimes`, `partial_tp_atr/pct/trail`, `partial_closed`
- Rename: `conviction` -> `priority` (on any pending/armed entries)
- Translate `open_positions[]` + `armed_tokens{}` -> unified `open_orders[]` with nested `legs[]`:
  - Active open position -> `Order(legs=[Leg(status=WORKING)])`
  - Armed entry -> `Order(legs=[Leg(status=ARMED, order_type="stop", trigger_price=...)])`
- Synthesize ClosedTrade identity fields for historical trades in `trades.jsonl`:
  - `parent_position_id = position_id.split(":")[0]`
  - `exec_seq = 1 if ":partial" in position_id else 0`
  - `exec_type = "reduce" if partial else "exit"`
  - `is_terminal = ":partial" not in position_id`
  - `triggered_by = ""`
- Split `partial_fills` counter: old value -> `state.partial_fills + state.entry_scale_downs` estimated split via audit log replay if available, else dump total to `partial_fills` (documented imprecision)
- `version: 2`

**Backup**: `.v1.bak` file created alongside output. **`--commit-migration`** flag required for destructive write (without it, dry-run only). **`--reset-state`** alternative flag = clean start, loses active positions + armed history (acceptable for paper, but avoid if possible).

## Dashboard Rework

v5 changes ripple into dashboards. Changes needed:

- **Per-token regime display REMOVED entirely** -- drop `regime` column from position table, drop `regime_names` dict, drop `_last_known_regimes` tracking, drop `exit_regimes` display, delete `regime`/`exit_regimes` fields from `to_dashboard_sim` output
- **`consolidate_partial_trades` deleted** -- the `position_id:partial` suffix-parsing band-aid becomes trivial: group by `parent_position_id`, emit one row per parent with `legs[]=exec_seq-ordered`. Net code REMOVAL.
- **New scaling counters surfaced** -- per-strategy breakdown shows `increase_fills`, `partial_fills` (reduce-only), `contingent_fills`, `entry_scale_downs` instead of the v4 single conflated `partial_fills`
- **Scaling events timeline** -- new per-position detail view shows `pos.scaling_events` as a timeline (size ups/downs, pyramiding rungs)
- **Position table columns** -- DROP: `regime`, `exit_regimes`. ADD: `scale_count`, `leaves_qty`, `r_anchor_price`, `has_scaling`

## Time Estimate

**30-40 hours**

| Work package | Hours |
|---|---|
| Position primitives (increase/reduce/reduce_fraction + ReduceResult + ScalingEvent) | 6-8 |
| ClosedTrade identity fields + suffix scheme | 2-3 |
| ScaleAction + LinkedScalePolicy + config wiring | 2-3 |
| Simulator/minute_exits wiring (both dispatch paths) + book_reduce wrapper | 4-6 |
| Portfolio constraints on increase (AC10) | 2-3 |
| Helpers (tp_ladder_*, add_*, breakeven, combine) | 4-6 |
| Paper engine wiring (INDEPENDENT + defensive checks) | 2-3 |
| Diagnostic counters + report.py + load_trade_log compat | 2-3 |
| Test suite (~40 tests covering all ACs, including QC1 interleaved funding + dust-boundary + multi-action list coverage) | 8-10 |
| Bug buffer | 2-3 |

## Risks

1. **Blast radius contained**: `v5/position.py` is the primary target. In v4, Position has 21 importers -- but in v5 after M1 copy, blast is contained to v5/ only. No v4 files are modified.
2. **Fee/slippage/funding bookkeeping**: Three subsystems must stay in sync. ReduceResult bundle pattern (Q-DEC5) isolates domain math from booking for testability.
3. **Float arithmetic**: AC22 sign invariant and AC14 dust promotion use assignment (not arithmetic) for terminal closes. Sign-of-product check handles negative-zero.
4. **Paper state serialization**: New fields must roundtrip. Old state files load with derived defaults (AC31).
5. **Backward compat for analysis logs**: `load_trade_log()` handles v4 `:partial` suffix transparently (AC33).

## Parity Gate

**Strategies without scaling MUST produce identical results.** This is the non-negotiable regression gate:

1. Run any v5 strategy that does NOT set `scale_check_fn` (or override `check_scale`)
2. Results must be bit-exact with M1 baseline (same v5 code without M2 changes)
3. All new scaling acceptance tests pass
4. Existing v5 tests continue to pass unchanged

The parity gate ensures M2 is purely additive -- no behavioral changes to the non-scaling path.

## Phase 3 Test Plan (~40 tests)

These tests are inferred during specification -- actual test files are written in Phase 3 (decompose). Listing them here prevents loss between phases.

### Critical accounting tests (must-have)

- **T-P1. Funding sum invariant (AC9, pure-reduce)**: Open position, accrue funding for 3 funding cycles, perform 3 reduces of varying fractions, then full close. Assert: `sum(ClosedTrade.funding_cost for all reduces + final) == total_funding_paid_during_position_life`.
- **T-P1a. Funding invariant QC1 (interleaved increase+reduce)**: Open → accrue 1 funding cycle → reduce 30% (booked funding pro-rata) → increase 50% (new cumulative_funding accrues on new larger size) → accrue 2 more funding cycles → reduce 50% → full close. Assert the invariant from AC9: at every state, `sum(booked ClosedTrade.funding_cost) + pos.cumulative_funding == total_funding_paid_to_date`.
- **T-P1b. Funding invariant, reduce-then-increase (inverse ordering)**: Same as T-P1a but starting with reduce-before-first-funding-cycle.
- **T-P1c. Funding invariant, dust-promoted reduce**: Open → accrue 2 funding cycles → reduce such that remaining notional < dust_usd (AC14 triggers) → verify total booked funding equals total paid; no funding lost in the `_close_position` override path.
- **T-P2. Entry fee sum invariant (AC7)**: Open position with `entry_fee = $5`, perform 3 reduces, then full close. Assert: `sum(ClosedTrade.entry_fee) == $5` (no double-count, no loss).
- **T-P3. Margin sum invariant**: Open with `margin_usd = $200`, perform 3 reduces, full close. Assert: `sum(ClosedTrade.margin_usd) == $200`.
- **T-P4. AC22 negative-zero edge case**: Force `pos.quantity = math.copysign(0.0, -1.0)` for a long -> `is_closed=True` AND defensive assert passes (sign-of-product, not copysign).
- **T-P5. AC22 random-fraction property test**: With seed=42, perform 5 reduces with `rng.uniform(0.05, 0.45)` fractions totalling close to 1.0 -> sign invariant holds throughout, final dust promotes to terminal.
- **T-P6. Over-close clamp**: `reduce(qty_to_close=999)` on a 5-unit position -> clamp to full close, `is_terminal=True`.
- **T-P7. r_anchor_price frozen**: `increase` at different price -> `r_anchor_price` unchanged from first entry.
- **T-P8. entry_bar unchanged on increase**: `pos.entry_bar` and `pos.entry_timestamp` unchanged after increase.
- **T-P9. scale_count rollback on failure**: Simulate ClosedTrade booking failure -> `pos.scale_count` decremented back.
- **T-P10. Identity uniqueness (I1)**: 100 simulated positions with mixed exit paths (terminal scale-out + terminal exit chain) -> all `ClosedTrade.position_id` values unique within the run.
- **T-P11. Terminal invariant**: exactly one ClosedTrade per parent has `is_terminal=True`.
- **T-P12. Sign invariant after interleaved ops**: After arbitrary increase/reduce sequences, `pos.is_closed or (pos.quantity * pos.direction > 0.0)`.

### Linked propagation tests (AC36)

- **T-L1. INDEPENDENT policy**: primary `Position.reduce(50%)` on position with `linked_position_id` set -> secondary unchanged, runtime warning logged once per (strategy, token).
- **T-L2. PROPORTIONAL hedge ratio**: primary `quantity=10`, secondary `quantity=5` (ratio 2:1). Primary reduces 30% -> secondary auto-reduces 30%. Assert post-state: primary=7, secondary=3.5, ratio still 2:1.
- **T-L3. ABSOLUTE asymmetric clamp**: primary `quantity=100`, secondary `quantity=50`. Primary `reduce(qty_to_close=60)` -> secondary `reduce(qty_to_close=60)` clamps to `qty_to_close=50` (full close per AC22).
- **T-L4. Dust-cascade invariant (C4)**: after PROPORTIONAL propagation where one leg promotes to terminal, verify the OTHER leg is force-closed with `exit_reason="linked_exit"`. End-state: `primary.is_closed == secondary.is_closed`.
- **T-L5. Full-close propagation unaffected by policy**: closing a primary fully always closes the linked secondary regardless of LinkedScalePolicy.

### Sizing constraint tests (AC10)

- **T-Sz1. Concentration scale-down**: Strategy returns `ScaleAction(qty_delta=+10)`, but concentration cap allows only `+6`. Assert `ScalingEvent.qty_delta == +6`, `ScalingEvent.requested_qty_delta == +10`.
- **T-Sz2. ADV cap skip**: Strategy returns `ScaleAction(qty_delta=+10)` exceeding per-fill ADV cap -> action SKIPPED, `state.rejections.adv_cap += 1`, no ScalingEvent recorded.
- **T-Sz3. Min increase size skip**: increase below `min_position_usd` -> silently skipped.
- **T-Sz4. Margin availability check**: increase exceeding available margin -> silently skipped.

### Order-of-operations tests (AC18)

- **T-B1. Stop-after-increase same-bar**: open at bar 0, `Position.increase` at bar 5 with `stop_override` placing stop at a price already breached by `bar.low` -> Phase 2 mutates stop, Phase 3 StopLossHandler fires same bar, ClosedTrade has `exit_reason="stop"`.
- **T-B2. Breakeven_plus_runner + break_even_atr same-bar interaction (Q5)**: helper triggers reduce 50% in Phase 2, BreakevenRatchetHandler moved stop in Phase 1, runner closes Phase 3 if instant whip -- assert exact sequence in `pos.scaling_events` + ClosedTrades.
- **T-B3. Phase ordering**: verify Phase 1 (update_state) uses OLD entry_price/initial_risk BEFORE Phase 2 (scale_check_fn) modifies them.
- **T-B4. Single scale action per bar**: strategy returning two ScaleActions -> only first executes.

### Helper unit tests

- **T18. tp_ladder_atr fires once per level**: 3-level ladder, advance bars through each level -> exactly 3 reduces, each at its trigger.
- **T19-revised. tp_ladder_r with default "original" anchor (Q-DEC1)**: Open long at $100 with `initial_risk=$5`, `r_anchor_price=$100` (frozen). `Position.increase` at $95 -> avg_px=$97.5 but `r_anchor_price` STAYS $100. tp_ladder_r([(1.0, 0.5)]) fires when price reaches $105 ($100 + 1.0x$5 = original anchor + 1R), NOT $102.50 (avg_px + 1R).
- **T19b. tp_ladder_r with opt-in "avg_px" anchor**: Same setup, but `tp_ladder_r([(1.0, 0.5)], anchor="avg_px")`. After increase to avg_px=$97.5, fires at $102.50. Documents the non-standard opt-in behavior.

### Bar-resolution invariant tests

- **T20a. Position math is bar-resolution-agnostic**: Direct unit test. Construct two identical Position objects + identical pre-state inputs. Call `pos1.reduce(qty_to_close=X, fill_price=Y, ...)` and `pos2.reduce(qty_to_close=X, fill_price=Y, ...)` -- assert ReduceResult fields are bit-exact equal. Isolates the "Position.reduce math is independent of dispatcher" claim.
- **T20b. Per-hourly-bar invocation cap (C2)**: sub-hourly strategy returning `ScaleAction(qty_delta=-0.05*qty)` every 5-min tick; run over 10 hourly bars (120 sub-hourly ticks at 5-min resolution) -> assert `state.partial_fills <= 10` (cap enforces once per hourly bar, not per tick).

### Missing coverage tests (TG1-TG6)

- **TG1. AC5 -- entry_price unchanged on reduce**: Open long at $100 with quantity=5. `reduce(qty_to_close=1.5, fill_price=$110)` -> assert `pos.entry_price == 100.0` (bit-exact, no drift).
- **TG2. AC8 -- slippage recorded per fill**: `increase(qty_to_add=2, fill_price=$100)` with ADV slippage model producing 3.5 bps -> assert `ScalingEvent.slippage_bps == 3.5`. Same for reduce.
- **TG3. AC15 -- no force-close after increase that breaches new stop**: Open long at $100, quantity=1, stop=$95. `increase(qty_to_add=1, fill_price=$90, stop_override=$92)` on a bar with `bar.low=$91` -> Phase 2 does NOT close; Phase 3 StopLossHandler fires and closes with `exit_reason="stop"` same bar.
- **TG4. AC16 -- trailing peaks preserved**: Open long, `pos.highest=$110` from prior bars. `increase(qty_to_add=1, fill_price=$105)` on a bar with `bar.high=$108` -> assert `pos.highest == 110.0` (not reset to $108, not recalculated from $105).
- **TG5. AC20 -- spec validation**: `StrategySpec(..., max_concurrent_per_token=2, scale_check_fn=<some fn>)` -> raises `ValueError` at construction time.
- **TG6. T1b -- funding invariant with interleaved increase (QC1 revised)**: Open, accrue funding 2 periods, reduce 50%, increase 100% (back to original size), accrue 2 more periods, full close. Assert `sum(closed_funding) + 0 == sum(all per-bar funding accruals)`. Non-trivial invariant that pure-reduce T1 doesn't cover.

### Paper state roundtrip test

- **T13b. ScalingEvent paper_state roundtrip**: Position with 3 ScalingEvents (mix of increase + reduce), plus populated `_helper_state` dict (e.g., `{"tp_ladder_fired": {0, 1}}`). Serialize to paper_state JSON, deserialize, assert round-tripped Position has identical `scaling_events` (all 11 ScalingEvent fields), `scale_count`, `_helper_state`, `r_anchor_price`.

### Deleted config test

- **T17. Verify `raw_mode` and `dd_scaling` do NOT exist in v5**: importing `v5.config.PortfolioConfig` and instantiating it should not accept `raw_mode`, `raw_max_positions`, `dd_scaling`, `dd_scaling_enabled`, `pump_filter_*`, or `unrealized_pnl_floor` kwargs -- construction raises `TypeError`. Prevents accidental porting of v4 dead config.

## Reference

Full design rationale, quant review discussion, and deep FIX/Nautilus vocabulary mapping:
`/workspace/crypto_backtest/.specs/active/position-scaling/brief.md` (1,864 lines)
