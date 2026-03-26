---
id: AIPIP-0026
title: Load research coordinator role when /strategy launches
status: accepted
author: @claude
created: 2026-03-26
---

## Problem

The research coordinator role (`memory/RESEARCH_COORDINATOR.md`) and its state
(`memory/RESEARCH_STATUS.md`) are isolated from the `/strategy` skill. When a
researcher launches `/strategy`, they have no awareness of:

1. **The auto-research pipeline** (`tools/auto_research_pipeline.py`) and its
   capabilities — 40+ signal variants, 3-stage screening, parallel execution
2. **The signal scoreboard** — 54 signals tested, which are GOLD/PASS/KILLED
3. **The research coordinator operating model** — autonomous iteration, subagent
   coordination, anti-patterns learned from 380+ failed candidates
4. **The V4 experimentation guide** (`knowledge/V4_EXPERIMENTATION_GUIDE.md`) —
   exit handlers, custom sizing models, regime customization, extension points

This means `/strategy` sessions waste time re-discovering signals that have already
been tested and killed, and miss opportunities to use the auto-research tools for
rapid screening.

Additionally, the research coordinator document predates the V4 engine refactoring
(exit handler extraction, sizing model registry) and should reference the
experimentation guide for engine-level experiments.

## Proposed Changes

### 1. Add research coordinator to SKILL.md Gate 0 loading

In the SKILL.md Gate 0 "Step 0: Load Project State" section, add:

```
READ: memory/RESEARCH_COORDINATOR.md — research coordination role, auto-research tools, anti-patterns
READ: memory/RESEARCH_STATUS.md — signal scoreboard, GOLD/PASS/KILLED verdicts (skim scoreboard only)
```

This makes the research coordinator role available at strategy launch without requiring
the researcher to know about it.

### 2. Add auto-research tools to SKILL.md capabilities

In the SKILL.md or Quick Reference "Available Capabilities" section, add an
"Auto-Research Tools" subsection listing:
- `tools/auto_research_pipeline.py` — 3-stage signal screening
- `tools/sweep_framework.py` — parameter sweep orchestration
- `tools/signal_discovery/` — 300+ features, walk-forward IC

### 3. Update RESEARCH_COORDINATOR.md to reference experimentation guide

Add a section pointing to `knowledge/V4_EXPERIMENTATION_GUIDE.md` for engine-level
experiments (exits, sizing models, regimes), so the coordinator role doesn't
contradict or duplicate the guide. The coordinator focuses on signal discovery
and research; the experimentation guide covers engine extension points.

### 4. Add research coordinator context to Quick Reference

Add a brief "Research Coordination" section to `STRATEGY_QUICK_REFERENCE.md`
that lists the auto-research tools and points to the scoreboard. This ensures
the information is available at every gate (not just Gate 0).

## Files Modified

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Add READ directives at Gate 0, add auto-research capabilities |
| `knowledge/STRATEGY_QUICK_REFERENCE.md` | Add Research Coordination section with tools and scoreboard reference |
| `memory/RESEARCH_COORDINATOR.md` | Add reference to V4 experimentation guide, clarify scope boundary |

## Backward Compatibility

Additive only. No existing behavior changes. The additional context loading at Gate 0
adds ~200 lines of context but this is within the "deep dive only when needed" principle
(the scoreboard is skimmed, not fully loaded).

## Risk

Low. The research coordinator role is read-only context — it informs decisions but
doesn't change the gate validation process or kill criteria.
