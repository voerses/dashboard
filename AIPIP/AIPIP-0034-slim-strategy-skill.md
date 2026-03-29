---
id: AIPIP-0034
title: Slim Strategy SKILL.md — Extract Gate Details to Reference File
status: accepted
author: @claude
created: 2026-03-29
---

## Problem

SKILL.md is 795 lines. It loads entirely into context on every `/strategy` invocation.
~500 lines are gate detail sections (Gates 0-7, 3P, 3O, 5P, 5O, 5.5) — reference material
used one gate at a time, not all at once.

The skill already says "Read STRATEGY_QUICK_REFERENCE.md for your current gate" but then
duplicates all the gate details inline anyway.

## Proposal

1. Extract Gates 0-7 detail sections → `knowledge/process/STRATEGY_GATE_DETAILS.md`
2. Replace in SKILL.md with a compact summary table + instruction: "Read the gate details
   file for your current gate's section"
3. Target: SKILL.md under 300 lines

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Remove gate detail sections, add compact reference |
| `knowledge/process/STRATEGY_GATE_DETAILS.md` | New file with extracted gate details |

## Changelog

| Date | Change |
|------|--------|
| 2026-03-29 | Proposed and accepted |
| 2026-03-29 | Implemented: SKILL.md 795→251 lines, gate details moved to STRATEGY_PIPELINE_GATES.md |
