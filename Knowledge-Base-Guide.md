# Knowledge Base Guide

How the AI's knowledge system is organized, what each layer contains, and how it all fits together.

---

## Overview

The knowledge system has **3 core layers**, with optional extension layers for project-specific knowledge:

```
Layer 1: CLAUDE.md + Rules        ← Always in context. Process + conventions.
Layer 2: Skills                   ← Reusable workflows triggered by slash commands.
Layer 3: Research + AIPIPs        ← Decision history and background research.

Optional extension layers (add for your project):
Layer 4: Memory Files             ← Deep technical knowledge, loaded on demand.
Layer 5: Structured Data + Query  ← Machine-queryable cross-cutting data.
```

---

## Layer 1: Always-Loaded Context

These files are injected into every conversation automatically.

### `CLAUDE.md` (project root)
The master instruction file. Contains:
- Meta-rules for AI behavior
- Slash command reference
- Enforcement hooks table
- Customization guide

### `.claude/rules/*.md`
Behavioral rules the AI must follow:

| File | What it enforces |
|------|-----------------|
| `development-workflow.md` | 5-phase process, review gates, stop triggers |
| `tdd.md` | Test-first rules, never weaken tests |
| `review-process.md` | Independent subagent review, context isolation |
| `conventions.md` | Project-specific naming, error handling, imports, testing patterns |
| `javascript.md` | JS/TS-specific patterns |
| `golang.md` | Go-specific patterns |
| `blast-radius.md` | Shared package caution levels |
| `subagent-patterns.md` | When/how to use subagents |
| `process-governance.md` | AIPIP required for process changes |

---

## Layer 2: Skills (Slash Commands)

Reusable workflows defined in `.claude/skills/`. Each skill is a `SKILL.md` file with structured instructions.

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `dev` | `/dev` | Full 5-phase development workflow (specify → complete) |
| `impact` | `/impact <pkg>` | Blast radius analysis for shared packages |
| `review` | `/review` | Independent code review by subagent |
| `discover-repo` | `/discover-repo` | Systematic repo exploration (structure, patterns, conventions) |

Skills are git-committable and team-shareable.

---

## Layer 3: Research + AIPIPs

Background research and process evolution history.

### Research Reports (`research/`)
Investigation documents created for specific technical or process questions.

### AIPIPs (`AIPIP/`)
Versioned process improvement proposals. Each has problem statement, proposal, alternatives, and impact analysis. See the [Process Guide](./AI-Dev-Process-Guide.md) for details.

---

## Optional Extension Layers

### Layer 4: Memory Files
Create an `aidev/memory/` directory with deep technical knowledge files organized by topic. Add a routing table in `CLAUDE.md` so the AI knows when to load each file.

Example structure:
```
memory/
├── tech/           # Technology deep dives
├── flows/          # End-to-end system traces
└── architecture/   # Structural knowledge
```

### Layer 5: Structured Data + Query Tool
Create scanner scripts to generate machine-readable JSON from your codebase, and a query tool for cross-cutting analysis.

Example structure:
```
data/               # Scanner output (JSON)
scripts/            # Scanner scripts
tools/query.py      # Cross-cutting query tool
```

---

## How It All Connects

```
Developer says: "Add encryption to the upload flow"

1. CLAUDE.md rules         → AI follows project conventions
2. /dev skill kicks off    → Phase 1: Specify (feature brief)
3. Phase 2: Design         → AI explores codebase, reads existing patterns
4. Phase 3: Decompose      → Subagent writes tests (context-isolated)
5. Phase 4: Implement      → AI follows conventions from .claude/rules/
6. /review                 → Independent subagent reviews against conventions
7. Phase 5: Complete       → Draft PR, human reviews on GitHub
```

---

## Maintenance

| What | How | When |
|------|-----|------|
| Rules | Requires an accepted AIPIP | When process needs improvement |
| Skills | Requires an accepted AIPIP | When workflows need adjustment |
| Research | Created for specific investigations | As needed |
