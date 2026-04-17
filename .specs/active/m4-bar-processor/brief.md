# M4 — Unified BarProcessor

**Summary**: Extract the duplicated Stage 1/2/3 per-bar dispatch logic from three separate locations into a single `v5/bar_processor.py` module that all dispatchers call, eliminating the class of bugs caused by logic divergence.

> **Naming note**: Stage 1/2/3 is the BarProcessor's per-position processing order. This is distinct from `exit_handlers.py`'s internal Phase 1/2/3 naming within `run_exit_handlers`. The "Stage" terminology avoids collision with that existing Phase naming.

---

## Problem

v4 has three independent implementations of per-bar processing:

1. `simulator._process_exits()` — backtest path (hourly bars)
2. `minute_exits.py` — sub-hourly exit checking (1m/5m bars)
3. `paper_engine.py` — live paper trading path

All three implement the same Stage 1 (exit checks) / Stage 2 (scaling checks) / Stage 3 (new entries) ordering, but they diverged over time. The 2026-04-15 StopLossHandler-deletion incident (-328pp metrics regression) was caused directly by this divergence: a fix applied to one path was not propagated to the others.

Every future feature (position scaling, multi-leg orders, new exit handlers) must be wired into all three paths — tripling implementation cost and bug surface.

---

## Scope

### In scope

- New `v5/bar_processor.py` module with a `BarProcessor` class
- Single entry point: `BarProcessor.process_bar(bar, positions, pending_orders, ...)` that executes Stage 1/2/3 in order
- Stage 1: Exit checks (stop-loss, take-profit, breakeven, circuit-breaker, max-hold, funding, strategy exit callback)
- Stage 2: Scaling checks (scale_check_fn / Strategy.check_scale if M2 landed)
- Stage 3: New entry processing (pending orders, armed entries, signal-driven entries)
- Resolution-agnostic: same BarProcessor handles hourly, 5m, 1m, or any future bar resolution
- Refactor `v5/simulator.py` to delegate to BarProcessor
- Refactor `v5/paper_engine.py` to delegate to BarProcessor
- Delete `v5/minute_exits.py` (logic absorbed into BarProcessor)
- BarProcessor unit tests covering Stage ordering guarantees

### Out of scope

- Data fetching / subscription changes (M6)
- Strategy API changes (M7)
- New exit handler types not already in v4
- Multi-leg order lifecycle (M5 — but BarProcessor provides the hook point)
- Performance optimization of bar processing (unless regression detected)

---

## Key Acceptance Criteria

1. **Single dispatcher**: All bar processing flows through `BarProcessor.process_bar()`. No direct Stage 1/2/3 logic remains in simulator.py or paper_engine.py.

2. **Stage ordering guarantee**: Stage 1 (exits) completes for ALL positions before Stage 2 (scaling) begins. Stage 2 completes before Stage 3 (entries). A unit test enforces this with a mock that records call order.

3. **minute_exits.py deleted**: The file no longer exists in v5/. Its functionality is covered by BarProcessor with sub-hourly bar resolution.

4. **Resolution-agnostic**: BarProcessor accepts a `BarSpec` (or resolution identifier) and processes bars identically regardless of timeframe. A test passes both hourly and 1m bars through the same BarProcessor instance.

5. **Backtest parity**: Running the full v5 test suite produces identical results before and after the BarProcessor extraction. No metric changes.

6. **Paper parity**: Paper engine delegates to BarProcessor and produces the same position/trade outcomes as the pre-refactor paper engine (verified by replaying a recorded bar sequence).

7. **Scale hook point**: BarProcessor's Stage 2 calls `scale_check_fn` (or `Strategy.check_scale`) if provided. If M2 has not yet landed, Stage 2 is a documented no-op placeholder.

8. **Exit handler dispatch**: All v4 exit handlers (StopLoss, TakeProfit, Breakeven, CircuitBreaker, MaxHold, Funding) are dispatched through a single registered-handler pattern, not if/elif chains.

9. **Stage 3 exit handler chain order (AC18)**: The check_exit chain in Stage 3 is ordered as follows (first match wins):
   CustomExitHandler -> CircuitBreakerHandler -> StopLossHandler -> TakeProfitHandler -> RSIExitHandler -> MeanTargetHandler -> SMATrailExitHandler -> MaxHoldHandler -> FundingCeilingHandler

10. **`process_bar()` signature**: `process_bar(pos, bar_ctx, global_bar) -> ProcessResult` — takes a position, a bar context (resolution-agnostic), and the global bar index, returning a ProcessResult bundle.

11. **Breakeven-after-scale nuance (AC18)**: If breakeven triggered in Stage 1 and scale changed VWAP in Stage 2, the breakeven stop stays at OLD entry_price (no longer equal to VWAP). This is intentional — earned protection from original risk budget is preserved.

12. **Per-hourly-bar invocation cap (AC-B3)**: `check_scale` is invoked at most ONCE per (position, hourly bar) regardless of sub-hourly cadence. If a sub-hourly tick fires a scale action on a position, NO further scale action fires on that position until the next hourly bar. Enforced via `BarProcessor._last_scale_bar: dict[str, int]` keyed by position_id (hourly bar index of last fired action; absent initially).
    - Rationale (dispatcher ownership): Dispatcher throttling state belongs on the dispatcher (BarProcessor), not the Position. Storing it on Position causes serialization bugs on paper restart — the bar index counter resets but Position state persists from the saved snapshot, leading to stale throttle state that blocks legitimate scale actions.
    - Rationale (cascade prevention): prevents pathological cascades (a strategy returning `ScaleAction(qty_delta=-0.05*qty)` every 5-min tick would fire 12 reduces/hour, eating through dust threshold, creating ~100k spurious ClosedTrades across a portfolio backtest).
    - Mirrors the `partial_closed: bool` one-shot semantic of the retired PartialTPHandler.

13. **Stage 2 -> Stage 3 data-flow invariant**: Stage 3 reads `r_anchor_price`, NOT `entry_price`, for R-based exits. The `r_anchor_price` field is frozen at first entry (Q-DEC1). This ensures R-multiple exit calculations use the original entry basis even after `Position.increase` changes the VWAP `entry_price`.

14. **Liquidation uses post-scale state (AC28)**: Liquidation math reads `pos.entry_price`, `pos.margin_usd`, `pos.quantity`, `pos.leverage`, `pos.cumulative_funding` — all of which are mutated by `Position.increase`/`Position.reduce`. After increase: `entry_price` = new VWAP, `margin_usd += margin_delta`, `quantity` grows. After reduce: `entry_price` UNCHANGED, `margin_usd`/`quantity`/`cumulative_funding` scaled by `(1 - close_fraction)`. Liquidation invariant falls out of existing math acting on mutated fields — no special engine code needed.

15. **Paper engine zero-duplication invariant**: Paper engine delegates to BarProcessor via the SAME `process_bar()` path used by the backtester. No separate Stage 1/2/3 logic remains in paper_engine.py. The paper engine builds a BarContext from live tick data and calls `bar_processor.process_bar(...)` — identical code path, zero duplication.

---

## ProcessResult Definition

```python
@dataclass
class ProcessResult:
    should_close: bool
    exit_reason: str | None
    exit_price: float | None
    scale_action_taken: bool  # True if Stage 2 fired a scale action this bar
```

---

## MinuteExitCache Note

If M4 ships before M6 (data architecture), `MinuteExitCache` from `v4/minute_exits.py` is preserved as a standalone cache module. BarProcessor delegates sub-hourly bar retrieval to it. After M6 ships, MinuteExitCache is superseded by DataEngine's RollingCache (ring buffer).

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M1** (v5 Fork) | **Required** — BarProcessor lives in v5/ namespace |
| **M2** (Position Scaling) | **Ideally before** — if M2 lands first, scale_check_fn is already wired and gets moved into BarProcessor Stage 2. If not, Stage 2 is a placeholder. |

---

## Time Estimate

**15-25 hours**

- ~5h: Design BarProcessor interface + Stage ordering protocol
- ~5h: Extract Stage 1/2/3 from simulator._process_exits
- ~3h: Refactor paper_engine to delegate
- ~3h: Absorb minute_exits.py logic
- ~4h: Tests (unit + parity)
- ~2h: Edge cases, cleanup, review

---

## Parity Gate

All v5 tests pass with no behavior change. New BarProcessor unit tests cover:
- Stage 1/2/3 ordering enforcement
- Each exit handler dispatched correctly
- Sub-hourly bar resolution handled identically to hourly
- Empty bar (no positions, no pending) is a no-op
- Multiple positions processed in deterministic order

### Specific Tests (from source brief)

- **T-B1 (Stage ordering)**: Mock that records call order verifies: all Stage 1 `update_state` calls complete for ALL positions before any Stage 2 `scale_check_fn` calls, and all Stage 2 calls complete before any Stage 3 `check_exit` chain calls. Uses a mock recorder injected into each handler and strategy hook.

- **T-B2 (Resolution-agnostic bit-exact ReduceResult)**: Construct two identical Position objects with identical pre-state inputs. Call `pos1.reduce(qty_to_close=X, fill_price=Y, ...)` and `pos2.reduce(qty_to_close=X, fill_price=Y, ...)` from different dispatcher contexts (simulating hourly vs sub-hourly). Assert ReduceResult fields are bit-exact equal. Isolates the "Position.reduce math is independent of dispatcher" claim.

- **T-B3 (Per-hourly-bar invocation cap)**: Sub-hourly strategy returning `ScaleAction(qty_delta=-0.05*qty)` every 5-min tick; run over 10 hourly bars (120 sub-hourly ticks at 5-min resolution). Assert `state.partial_fills <= 10` (cap enforces once per hourly bar, not per tick). Proves the cap prevents pathological cascade.

- **T-B4 (R-anchor in exits)**: Open long at $100 with `initial_risk=$5`, `r_anchor_price=$100` (frozen). `Position.increase` at $95 changes VWAP to $97.5 but `r_anchor_price` STAYS $100. Stage 3 exit handler using R-multiple targets reads `r_anchor_price` ($100), NOT `entry_price` ($97.5). Verify exit fires at the correct R-anchored price level.
