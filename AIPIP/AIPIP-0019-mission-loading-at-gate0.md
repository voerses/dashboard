---
id: AIPIP-0019
title: Mandatory Mission Loading at Gate 0 + Mission Lifecycle
status: accepted
author: @claude
created: 2026-03-11
---

# AIPIP-0019: Mandatory Mission Loading at Gate 0 + Mission Lifecycle

## Problem

The `/strategy` skill does not instruct loading `.claude/.strategy-mission` at Gate 0.
The mission file defines:
- The search goal (what to beat)
- Baselines (performance numbers to exceed)
- Kill criteria (mission-specific, layered on top of gate thresholds)
- Previous attempts (what's already been tried and failed)
- Promising directions (curated from sweep results)

Without loading the mission at Gate 0, the agent:
1. Proposes ideas without knowing mission-specific kill criteria
2. May duplicate previous failed attempts (s59, s60)
3. Doesn't apply mission baselines at V4-Gate 3/4/5
4. Wastes time on directions the mission explicitly excludes

**Observed failure:** On 2026-03-11, `/strategy` was launched. The agent loaded
`PROJECT_STATUS.md` and `STRATEGY_QUICK_REFERENCE.md` per Gate 0 instructions, but
did NOT load `.claude/.strategy-mission` because the skill doesn't mention it. The
mission file had been updated minutes earlier with baselines, kill criteria, and
excluded directions. The agent proceeded without this context.

Additionally, there is no defined lifecycle for missions. When a strategy passes Gate 6
(paper trading deployed), the mission should be evaluated: close it (goal achieved or
abandoned), or update it with learnings to drive the next iteration of strategy
improvement.

## Solution

### Change 1: Add mission loading to Gate 0 Step 0 (conditional)

In `SKILL.md` Gate 0 Step 0 ("Load Project State"), add the mission file as a
conditional read — only if it exists and has an active mission (not closed):

```
READ: .claude/.strategy-mission — IF file exists AND has active goal (no `status: closed`),
      load search goals, baselines, kill criteria, previous attempts.
      If file doesn't exist or mission is closed, proceed without mission context.
```

### Change 2: Apply mission kill criteria at every V4 gate

Add to V4-Gate 3, V4-Gate 4, and V4-Gate 5 a reminder:

```
**Mission criteria:** If an active mission is loaded, apply its kill_if rules
in addition to gate thresholds. Mission criteria are ADDITIVE — a strategy must pass
both gate thresholds AND mission criteria.
```

This already exists partially at V4-Gate 3 ("Check mission brief") but should be
explicit at all three V4 gates.

### Change 3: Mission-aware Gate 0 screening

Add to Gate 0 Step 3 (Check Graveyard + Tier C):

```
Also check `.claude/.strategy-mission` notes.previous_attempts — kill if already
tried with same approach and no new evidence.
```

### Change 4: Mission lifecycle review at Gate 6 completion

After a strategy is deployed to paper trading (Gate 6 entry), add a mandatory
mission review step. Ask the user:

```
**Mission Review:** Strategy sNN deployed to paper trading.

1. **Close mission** — goal achieved, no further search needed
2. **Update mission** — incorporate learnings from this strategy development cycle
   to refine goals (e.g., further improve annual return, reduce risk, add
   diversification). Update baselines, kill criteria, previous attempts, and
   promising directions for the next iteration.
3. **Keep mission as-is** — strategy deployed but mission goal not yet fully met,
   continue searching with current criteria
```

This ensures the mission evolves with each development cycle rather than going stale.

### Change 5: Mission file format — add status field

The mission file should support a `status` field at the top:

```yaml
status: active    # active | closed
goal: ...
```

When status is `closed`, Gate 0 skips mission loading. Closing a mission appends
a summary entry to `findings/strategy-findings.jsonl` with category `mission`.

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Gate 0 Step 0: conditional mission read. Gate 0 Step 3: check previous attempts. V4-Gate 3/4/5: mission criteria reminder. Gate 6: mission lifecycle review step. |
| `.claude/.strategy-mission` | Add `status: active` field to current mission. |

## Risks

- None. Additive change — adds conditional reads and a user prompt, doesn't remove
  or change existing gate logic.
- Mission file remains optional — if it doesn't exist or is closed, gates proceed normally.
- Mission review at Gate 6 is a user prompt (AskUserQuestion), not an autonomous action.
