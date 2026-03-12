---
id: AIPIP-0020
title: Autonomous Mission-Driven Gate Traversal
status: accepted
author: @claude
created: 2026-03-11
depends: AIPIP-0019
---

# AIPIP-0020: Autonomous Mission-Driven Gate Traversal

## Problem

When a mission is active, the agent stops at every gate transition to ask the user for
approval. This creates unnecessary friction — the mission already defines what success
looks like (baselines, kill criteria). The agent has enough context to make pass/kill
decisions autonomously and should only stop when it reaches paper trading deployment.

Previous sessions show the agent already exercises judgment on gate criteria (e.g.,
"conditional pass" on V4-Gate 5 when marginal Sharpe was positive despite Sharpe delta
being slightly negative due to near-riskless baseline). This judgment should be formalized.

## Solution

### Autonomous Mode Rules (when active mission exists)

When `.claude/.strategy-mission` has `status: active`, the agent operates autonomously
through the gate pipeline:

1. **Mission criteria are HARD kills.** If any mission `kill_if` fails, the strategy is
   killed immediately. No conditional passes, no judgment calls. Document and move to
   next candidate.

2. **Gate criteria are SOFT gates.** If a gate metric is closely met (within ~10-20% of
   threshold), the agent may grant a conditional pass with documented reasoning. Examples:
   - Calmar 0.45 vs threshold 0.5 → conditional pass if other metrics strong
   - Validation rate 18% vs threshold 20% → conditional pass if top tokens are high-quality
   - Sharpe delta -0.1 vs threshold >0 → conditional pass if marginal Sharpe positive

3. **Document every decision.** Each gate report must include:
   - All metrics vs thresholds (PASS/SOFT PASS/FAIL)
   - For SOFT PASS: explicit reasoning why the shortfall is acceptable
   - Mission criteria check: all kill_if rules evaluated with values

4. **Iterate autonomously.** If a strategy is killed, immediately proceed to the next
   candidate idea from Gate 0. Don't stop to ask the user. Screen multiple ideas,
   develop the best ones, kill fast.

5. **Stop at Gate 6 (paper trading).** Present all results to the user before deploying:
   - Full gate history (every gate report for every candidate attempted)
   - Final strategy metrics vs mission baselines
   - Any SOFT PASSes and their reasoning
   - Recommendation: deploy or kill

6. **Stop on ambiguity.** If the agent genuinely cannot decide (e.g., strategy is
   borderline on multiple mission criteria simultaneously), ask the user rather than
   guessing.

### What Changes in SKILL.md

Remove the implicit "pause and ask" at each gate transition. Add autonomous mode
instructions to the Gate Process section.

### Gate Report Format Update

Add mission criteria evaluation to the mandatory gate report:

```
## Gate N: [NAME] — [PASS / SOFT PASS / KILL]

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| [gate metric] | [value] | [threshold] | PASS/SOFT PASS/FAIL |

**Mission criteria:**
| Kill Rule | Value | Threshold | Status |
|-----------|-------|-----------|--------|
| [mission kill_if] | [value] | [threshold] | PASS/FAIL |

**Soft pass justification (if any):** [why shortfall is acceptable]
**Decision:** PROCEED / KILL / ITERATE (try next candidate)
```

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Gate Process section: add autonomous mode rules. Gate report format: add SOFT PASS and mission criteria. Remove implicit pause-and-ask between gates. |

## Risks

- Agent may soft-pass too aggressively, letting weak strategies through to Gate 6.
  Mitigated by: mission criteria remain hard kills, and user reviews everything at Gate 6.
- Agent may iterate through many candidates without user visibility.
  Mitigated by: all gate reports are documented, user sees full history at Gate 6.
