---
id: AIPIP-0033
title: Enforce Coordinator Discipline Throughout Gate Pipeline
status: accepted
author: @claude
created: 2026-03-29
---

## Problem

The Research Coordinator role (`memory/RESEARCH_COORDINATOR.md`) clearly defines:
- You are the COORDINATOR, not the executor
- Launch subagents in BACKGROUND for all heavy work
- Stay responsive to the user at all times
- Never go silent while agents run

But the `/strategy` SKILL.md describes every gate as if the researcher should do the
work directly ("Run signal_lab.py", "Compute IC manually", "Run validation.py"). When
the skill takes over, the coordinator discipline is forgotten — the researcher runs
heavy computations inline, goes silent for minutes, and stops being responsive.

## Proposal

### 1. Add a coordinator discipline block at the top of SKILL.md Instructions

A clear, non-optional operating model section that applies at EVERY gate:

- All heavy work (IC tests, backtests, data fetching, validation) → background subagents
- The coordinator stays in the main thread, responsive to the user
- Between launching agents and getting results: brief status update to user
- When results return: evaluate, decide kill/pass, report to user, launch next wave
- NEVER go silent — always tell the user what's happening

### 2. Add coordinator reminders in gate descriptions

At each gate section, add a one-liner reinforcing delegation:
"**Delegate:** Launch this as a background subagent. Stay responsive."

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Add coordinator discipline section + per-gate delegation reminders |

## Changelog

| Date | Change |
|------|--------|
| 2026-03-29 | Proposed |
| 2026-03-29 | Accepted by user |
| 2026-03-29 | Implemented: coordinator discipline block + per-gate delegation reminders in SKILL.md |
