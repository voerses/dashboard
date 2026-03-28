---
id: AIPIP-0029
title: Mandatory end-of-session knowledge capture for both /strategy and /dev
status: accepted
author: @claude
created: 2026-03-28
---

## Problem

Knowledge capture is asymmetric and incomplete:

1. **Strategy mode** has Gate 6.5 (knowledge update after paper deploy) but NO session-end
   capture. If you do 3 hours of research and kill at Gate 2, all learnings are lost unless
   manually written to RESEARCH_STATUS.md. The research coordinator doc says "update
   RESEARCH_STATUS.md before session ends" but this is a soft rule, not enforced.

2. **Dev mode** has zero knowledge updates. New architecture patterns, blast radius changes,
   convention discoveries, and implementation learnings are lost after PR merge. The code
   becomes the only record — but knowledge about WHY decisions were made, what patterns
   emerged, and what to avoid next time is not captured.

3. **INDEX.md and QUANT_METHODOLOGY.md** were just created but there's no mechanism to keep
   them current as the project evolves.

## Proposal

Add a **mandatory knowledge capture step** at the end of both workflows, with clear rules
about what to update and how.

### 1. Strategy: Add "Session Wrap-Up" step to Instructions

After Step 4 (Begin autonomous research), add a Step 5 that triggers before session ends
or when the user says "stop" / "done" / "wrap up":

**What to update:**
- `memory/RESEARCH_STATUS.md` — signal scoreboard, new GOLD/PASS/KILLED verdicts, next actions
- `memory/PROJECT_STATUS.md` — only if strategy tiers changed or new capabilities added
- `memory/INDEX.md` — only if new files were created or existing files significantly changed
- `findings/strategy-findings.jsonl` — append findings from this session
- `knowledge/QUANT_METHODOLOGY.md` — only if new statistical insights or threshold adjustments discovered

**What NOT to update (prevent bloat):**
- Don't add raw agent outputs or intermediate calculations
- Don't update deep-dive files unless a specific gap was identified and filled
- Don't rewrite files that haven't changed — append or update specific sections

### 2. Dev: Add "Knowledge Capture" phase after Phase 5

After PR merge and spec moved to done/, add a knowledge capture step:

**What to update:**
- `memory/PROJECT_STATUS.md` — new capabilities, changed architecture, deployment state
- `knowledge/ARCHITECTURE.md` — only if directory structure or system design changed
- `aidev/.claude/rules/blast-radius.md` — only if new shared modules created or import counts changed
- `aidev/.claude/rules/conventions.md` — only if new patterns established
- `memory/INDEX.md` — only if new knowledge files created

**What NOT to update:**
- Don't document the feature itself (that's in the PR)
- Don't add implementation details (that's in the code)
- Only capture cross-cutting learnings that affect future development

### 3. Update Rules (in KNOWLEDGE_BASE_GUIDELINES.md)

Add a "Knowledge Update Rules" section:

**Promote-on-validation lifecycle:**
1. Session notes (raw findings from this session's work)
2. Validated findings (confirmed by multiple analyses or explicit review)
3. Domain knowledge (generalized insights extracted from multiple findings)

**Staleness rule:** When updating a knowledge file, add/update a `Last updated: YYYY-MM-DD`
line in the file header. Files not updated in 90 days should be reviewed for accuracy.

**Append-only for state files:** RESEARCH_STATUS.md, PROJECT_STATUS.md — add new entries,
don't rewrite history. Archive old sections when files exceed 100K.

**Size discipline:** If a knowledge file exceeds its target size (see guidelines), split
or archive older content rather than letting it grow unbounded.

## Files Modified

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Add Session Wrap-Up step to Instructions |
| `.claude/skills/dev/SKILL.md` | Add Knowledge Capture phase after Phase 5 |
| `knowledge/KNOWLEDGE_BASE_GUIDELINES.md` | Add Knowledge Update Rules section |

## Risk

Low. Additive only — no existing behavior changes. The knowledge capture steps are
lightweight (update only what changed) and prevent the more costly problem of lost learnings.
