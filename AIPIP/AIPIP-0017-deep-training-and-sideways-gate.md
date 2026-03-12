# AIPIP-0017: Deep Training Window + Strategy Mission Briefs

**Status:** accepted
**Author:** Claude
**Created:** 2026-03-10

## Problem

1. V4-Gate 3 trains on only 12 months of data. This misses multiple full market cycles — the 2020-2021 bull, 2022 bear, 2023 recovery, 2024 ETF rally, and 2025-2026 consolidation. Strategies optimized on 12 months may overfit to the most recent regime.

2. Users set specific goals for a strategy search ("beat s58 in sideways markets") that aren't permanent gate criteria but need to persist across sessions and compactions. Currently there's no mechanism to capture these — they get lost.

## Changes

### Part 1: Deep Training Window (V4-Gate 3) — Permanent

Change V4-Gate 3 default from `--months 12` to max available data (`--months 72`).

**Before:** `python v4/portfolio_backtest.py --strategy sNN --months 12 --capital 200000`
**After:** `python v4/portfolio_backtest.py --strategy sNN --months 72 --capital 200000`

This ensures strategies train on all available regimes:
- COVID crash (Mar 2020) — CRISIS
- Bull run (2020-2021) — UPTREND
- Bear market (2022) — DOWNTREND
- Recovery (2023) — RANGE/QUIET
- ETF rally (Jan 2024) — structural shift
- Consolidation (2025-2026) — current RANGE/QUIET

Gate 3 thresholds unchanged — they just must hold over the full window.

### Part 2: Strategy Mission Brief — Mechanism

Add a **mission brief** file that captures the current strategy search goal.

**File:** `.claude/.strategy-mission`

**Format:**
```
goal: <one-line objective>
baseline: <strategy to beat, with metrics>
kill_if: <specific conditions that kill candidates>
done_when: <success criteria — when to delete this file>
```

**Lifecycle:**
1. User sets a goal → write `.strategy-mission`
2. `/strategy` Gate 0 reads mission brief → uses as additional screening criteria
3. All subsequent gates check mission-specific kill criteria alongside standard gates
4. When a strategy passes all gates AND meets the mission goal → mark mission done
5. Delete `.strategy-mission` (or archive to `findings/`)

**Process integration:**
- Gate 0: Read mission brief. Kill ideas that can't plausibly meet the goal.
- V4-Gate 4: Check mission-specific OOS criteria (e.g., "beat s58 in March").
- V4-Gate 5: Check mission-specific regime criteria (e.g., "outperform in RANGE/QUIET").
- On mission completion: append finding to `findings/strategy-findings.jsonl`, delete mission file.

This is NOT a permanent gate change — it's a mechanism for temporary, session-spanning goals.

## Files Modified

- `.claude/skills/strategy/SKILL.md` — V4-Gate 3 months default, Gate 0 mission brief loading
- `knowledge/STRATEGY_QUICK_REFERENCE.md` — V4-Gate 3 months default

## Rationale

- **Deep training** is always better — 72 months covers 3+ full cycles. No downside.
- **Mission briefs** solve the "lost context" problem without polluting permanent gate criteria. The user's current goal ("beat s58 in March") is important NOW but irrelevant once achieved. Permanent gates should stay clean and general.
