# Feature Brief: Exit Dispatcher Unification

(Resolution-agnostic per-bar processing — eliminate the simulator/minute_exits/paper_engine fork that has caused multiple bugs)

## Problem

The engine has **THREE separate per-bar exit-loop implementations** that each maintain their own copy of the Phase 1 → Phase 2 → Phase 3 logic:

1. **`v4/simulator.py:_process_exits`** (lines ~382-535) — hourly bar loop
2. **`v4/minute_exits.py`** (entire file) — sub-hourly bar loop, called when `exit_resolution > 0`
3. **`v4/paper_engine.py`** (lines ~446-517) — paper/live trading loop

Each one independently:
- Iterates positions per bar
- Calls handler.update_state (Phase 1)
- Runs the side-effect path (Phase 2 — partial_tp historically; `scale_check_fn` after position-scaling lands)
- Runs the exit-handler chain (Phase 3 — stop, target, etc.)
- Closes positions and books ClosedTrades

**Concrete bug evidence**:
- 2026-04-15: `StopLossHandler` was accidentally deleted from `build_exit_chain` while migrating CRISIS exit. Baseline dropped from 1,121% → 793% (-328pp). Caught only by full backtest. Root cause: edit applied to one path, missed the other.
- The `_partial_close_position` function had THREE callers (simulator + minute_exits + paper_engine), each with slightly different surrounding logic.

**Why this matters now**: position-scaling (active spec) wires `scale_check_fn` into BOTH simulator and minute_exits paths to avoid deepening the fork. But until the dispatcher itself is unified, every future engine change risks introducing the same class of bug.

## Why This Matters

- **Bug surface**: every Phase 1/2/3 change must be applied to 3 files. Past incidents prove we routinely miss one.
- **Test surface**: invariants like "Phase 2 completes before Phase 3" must be tested 3 times.
- **Refactor velocity**: any future bar-resolution work (4h, daily, custom) requires editing 3 dispatchers.
- **Cognitive load**: new contributors need to learn 3 mental models for "what happens per bar".

## Scope

**Include:**
- Extract per-bar Phase 1/2/3 logic into a single `BarProcessor` class
- `simulator.py` hourly loop becomes a thin "iterate hourly bars and call BarProcessor" wrapper
- `minute_exits.py` sub-hourly loop becomes a thin "iterate sub-hourly bars and call BarProcessor" wrapper
- `paper_engine.py` per-tick loop becomes a thin "on tick, call BarProcessor" wrapper
- Bar resolution becomes a true parameter (default=hourly; sub-hourly via `exit_resolution`; future 4h/daily additive)
- All three call sites use the SAME `Position.increase` / `Position.reduce` methods (already true after position-scaling lands)

**Out of scope:**
- Adding new bar resolutions (4h, daily) beyond what's already supported
- Changing exit handler semantics (this is a structural refactor, not a behavior change)
- Refactoring the entry path (separate concern)
- Live execution semantics changes

## Acceptance Criteria

1. **AC1 — `BarProcessor` class exists** in `v4/bar_processor.py`:
   ```python
   class BarProcessor:
       def __init__(self, state, config, spec, sig): ...

       def process_bar(self, pos, bar_ctx, global_bar) -> ProcessResult:
           """Single source of truth for per-bar position processing.
           Phase 1: handler.update_state (breakeven ratchet, trailing stop)
           Phase 2: scale_check_fn invocation (Position.increase / reduce)
           Phase 3: exit handler chain (CircuitBreaker → StopLoss → ... → Funding)
           Resolution-agnostic: bar_ctx may be hourly, sub-hourly, 4h, daily.
           """
   ```

2. **AC2 — Hourly dispatch (simulator.py) calls BarProcessor**:
   - `_process_exits` becomes a thin wrapper: iterate `open_positions`, build hourly `BarContext`, call `bar_processor.process_bar(pos, bar_ctx, global_bar)`
   - All Phase 1/2/3 logic moves OUT of simulator.py
   - Behavior-equivalent to current state (full backtest produces identical metrics)

3. **AC3 — Sub-hourly dispatch (minute_exits.py) calls BarProcessor**:
   - `process_minute_exits` becomes a thin wrapper: iterate sub-hourly bars, build sub-hourly `BarContext`, call `bar_processor.process_bar(pos, bar_ctx, global_bar)`
   - All Phase 1/2/3 logic moves OUT of minute_exits.py
   - Sub-hourly tests (test_minute_exits.py) pass unchanged

4. **AC4 — Paper dispatch (paper_engine.py) calls BarProcessor**:
   - On each tick, paper engine builds a `BarContext`, calls `bar_processor.process_bar(...)`
   - All Phase 1/2/3 logic moves OUT of paper_engine.py
   - Paper engine tests pass unchanged

5. **AC5 — Behavior-equivalent invariant**: full backtest of all production strategies (s513, s523c, s524m, s532, s540) produces metrics within 0.01% Sharpe / Calmar / total return of pre-refactor baseline. This is a STRUCTURAL refactor, not a behavior change.

6. **AC6 — Single test suite for Phase 1/2/3 ordering**: `tests/test_bar_processor.py` tests Phase ordering ONCE, not three times. Existing simulator/minute_exits/paper-specific tests verify their dispatch wrapper plus shared BarProcessor.

7. **AC7 — Bar resolution as parameter**: BarProcessor doesn't care about resolution. `BarContext` carries the bar (close, high, low, atr, regime, etc.) regardless of timeframe. Adding 4h or daily becomes "feed 4h bars to BarProcessor" — no new code paths.

8. **AC8 — Exit handlers run identically across dispatchers**: `pos.exit_handlers` chain is built once (via `build_exit_chain`) and called by BarProcessor. No dispatcher reimplements handler logic.

9. **AC9 — `scale_check_fn` invocation lives in BarProcessor only**: position-scaling wires `scale_check_fn` into BOTH simulator and minute_exits as part of its MVP (Q4). After this refactor, the wiring lives in BarProcessor only — simulator and minute_exits become call sites, not implementations.

10. **AC10 — `_close_position` and `book_reduce` are called by BarProcessor**: closure booking remains a state-aware operation (needs SimulationState access). BarProcessor calls these during Phase 3 / dust-promotion paths.

## Why NOT in scope of position-scaling

Position-scaling needs to ship to unblock 2026 perf work. Doing this refactor concurrently would:
- Triple the touched files
- Make position-scaling testing harder (new BarProcessor abstraction + new feature simultaneously)
- Risk the production-strategy migration of position-scaling

The position-scaling brief explicitly defers this and ensures it doesn't make the fork worse (Q4 wiring into both paths, same `Position.reduce` API in both).

## Dependencies

- **Position-scaling MUST land first.** This refactor presumes the unified `Position.increase` / `Position.reduce` API exists.
- After position-scaling: `scale_check_fn` is wired into 2 dispatchers (simulator + minute_exits). After this refactor: wired in 1 (BarProcessor).

## Time Appetite

**12-18 hours**:
- Design `BarProcessor` interface + `BarContext` abstraction: 2h
- Implement BarProcessor by extracting from simulator._process_exits: 3h
- Refactor simulator.py to use BarProcessor: 2h
- Refactor minute_exits.py to use BarProcessor: 2h
- Refactor paper_engine.py to use BarProcessor: 2h
- Behavior-equivalence verification (run all production strategies pre/post): 2h
- Test refactoring (move Phase 1/2/3 ordering tests to test_bar_processor.py): 1-2h
- Bug buffer: 2-3h

## Risks

- **Behavior equivalence is the hard part**. Even small differences in field initialization order, BarContext construction, or handler-state setup can produce subtle metric drift. Mitigation: full pre/post backtest comparison, strategy-by-strategy.
- **paper_engine.py has live-trading-specific concerns** (idempotency, network, state persistence) that simulator/minute_exits don't. BarProcessor abstraction must NOT leak these into the shared interface. Mitigation: `BarProcessor.process_bar` must be pure (input → state mutation + ProcessResult), with all I/O in the dispatcher wrapper.
- **Test coverage during refactor**: existing tests are dispatcher-specific. Need to ensure equivalent coverage from Day 1 of the refactor, not after.

## Out of Scope

- Adding new bar resolutions (4h, daily) — additive once BarProcessor exists
- Changing exit handler interfaces (Handler protocol stays the same)
- Refactoring entry path (separate spec)
- Performance optimization (this is structural, not perf-focused)
- Live execution semantics changes (paper engine keeps its current concerns)

## Success Metric

After this refactor:
- Adding a new exit handler requires editing 1 file (`exit_handlers.py`) and registering in `build_exit_chain` — works automatically across hourly, sub-hourly, and paper dispatchers.
- Changing Phase ordering requires editing 1 file (`bar_processor.py`) — propagates automatically across all dispatchers.
- The class of bug from the 2026-04-15 incident becomes structurally impossible.
