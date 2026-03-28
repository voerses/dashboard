---
id: AIPIP-0027
title: Strategy startup presents as Research Coordinator with state summary
status: accepted
author: @claude
created: 2026-03-28
---

## Problem

When `/strategy` launches, it only:
1. Sets the process mode file
2. Shows the gate table
3. Says "Ready for Gate 0 — what's your strategy idea?"

This is a cold, stateless start. The user has to manually ask "what did we try last?" or "what's next?" before any work begins. The research coordinator role (`memory/RESEARCH_COORDINATOR.md`) defines an autonomous agent that should:
- Never ask "what next?" — it decides based on data
- Resume from where it left off — no re-explaining
- Launch background research immediately

But the current startup sequence doesn't load state or present the coordinator identity until Gate 0 Step 0, which only runs after the user proposes a specific idea. This creates a disconnect: the coordinator role says "iterate autonomously" but the startup says "what's your idea?"

## Proposal

Replace the SKILL.md "Instructions" section (steps 1-3) with a startup sequence that:

### Step 1: Set mode files (unchanged)
```bash
echo "strategy" > "$CLAUDE_PROJECT_DIR/.process-mode"
echo "gate0" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### Step 2: Load state (NEW)
Read these files and extract key information:
- `memory/RESEARCH_COORDINATOR.md` — role identity
- `memory/RESEARCH_STATUS.md` — recent experiments, signal scoreboard
- `memory/PROJECT_STATUS.md` — capability inventory, strategy tiers, open items
- `.claude/.strategy-mission` — active mission (if any)
- `findings/strategy-findings.jsonl` — recent findings (last 10)

### Step 3: Present startup briefing (REPLACES gate table)

Output a briefing in this format:

```
**Research Coordinator online.** I coordinate parallel signal research,
decide kill/pass/double-down based on data, and iterate autonomously.

## Current State
- **Strategies:** [N Tier A, N Tier B, N killed]
- **Active mission:** [mission summary or "none"]
- **Last session:** [1-2 line summary of last session's work from RESEARCH_STATUS.md]
- **Key finding:** [most recent actionable finding]

## What's Next
[2-3 bullet points of highest-priority research items from RESEARCH_STATUS.md]

## Gate Pipeline (for reference)
| Gate | Name | Kill Rate |
|------|------|-----------|
[abbreviated table — just gate names and kill rates, no time/criteria columns]

Starting research now. I'll report findings as they come in.
```

### Step 4: Begin autonomous research (NEW)
If RESEARCH_STATUS.md has prioritized next actions, begin executing them immediately (launch background agents, start Gate 0 screening, etc.) without waiting for user input. If no clear next action exists, present 2-3 candidate research directions and ask which to pursue.

## Key Behavior Change

**Before:** `/strategy` → gate table → waits for user idea → loads state at Gate 0
**After:** `/strategy` → loads state → presents coordinator briefing + history → begins research autonomously

The gate table is still shown but abbreviated — it's reference material, not the primary output.

## Files Modified

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Replace Instructions steps 1-3 with coordinator startup sequence |

## Backward Compatibility

The gate process itself is unchanged. Only the startup presentation changes. All gate thresholds, kill criteria, and validation logic remain the same. The state files being loaded (RESEARCH_STATUS.md, etc.) already exist and are already referenced in Gate 0 Step 0 — this just moves the loading earlier.

## Risk

Low. This is a presentation/UX change to the startup sequence. No gate logic, validation, or kill criteria are modified. The coordinator role document already exists and defines this behavior — this AIPIP just makes the startup sequence match the role definition.
