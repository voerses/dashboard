---
id: AIPIP-0032
title: Fix AutoResearcher Gate Progression — Don't Reset, Drive Through Gates
status: accepted
author: @claude
created: 2026-03-29
---

## Problem

The `/strategy` skill's Step 1 unconditionally resets the gate to `gate0` on every session start:

```bash
echo "gate0" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

This destroys progress every time the skill is invoked. Additionally, Step 4 ("Begin autonomous research") is too vague — it tells the researcher to "start research" but never instructs it to actually progress ideas through Gate 0 → 1 → 2 → 3 → etc.

### Result

The autoResearcher stays stuck at Gate 0 forever, running IC tests and signal discovery but never advancing promising signals through the pipeline to prototype and validation.

## Proposal

### 1. Fix Step 1: Preserve existing gate state

```bash
echo "strategy" > "$CLAUDE_PROJECT_DIR/.process-mode"
# Only set gate0 if no gate file exists — don't reset progress
[ ! -f "$CLAUDE_PROJECT_DIR/.strategy-gate" ] && echo "gate0" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### 2. Rewrite Step 4: Explicit autonomous gate progression

Replace the vague "begin autonomous research" with clear instructions:

- The researcher autonomously drives ideas through the gate pipeline
- At each gate, run the gate checks described in the SKILL, use raw backtest (AIPIP-0031), and make a kill/pass decision
- On PASS: advance the gate and continue to the next gate's checks
- On KILL: log to graveyard, pick up the next idea, start over at Gate 0
- The researcher does NOT wait for user approval between gates (the user can redirect at any time)
- The gate file tracks the current active idea's highest gate

### 3. Make the research loop explicit

After the briefing, the researcher should:
1. Check current gate state
2. If at Gate 0: screen the highest-priority idea from RESEARCH_STATUS.md
3. If at Gate 1: run IC tests + raw backtest on the current idea
4. If at Gate 2: run dedup checks
5. If at Gate 3+: prototype and validate
6. On PASS at any gate: advance to next gate immediately
7. On KILL: log, reset gate to 0, pick next idea

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Fix Step 1 gate reset, rewrite Step 4 with autonomous progression loop |

## Risks

- **Too autonomous:** Researcher might advance bad ideas. Mitigation: raw backtest (AIPIP-0031) kills most bad ideas automatically. User can redirect at any time.

## Changelog

| Date | Change |
|------|--------|
| 2026-03-29 | Proposed |
| 2026-03-29 | Accepted by user |
| 2026-03-29 | Implemented: SKILL.md Step 1 gate-reset fix + Step 4 autonomous progression loop |
