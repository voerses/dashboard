# Knowledge Base Guidelines

How to write and maintain knowledge files efficiently. Based on Anthropic's
context engineering best practices.

## Core Principle

> Find the smallest set of high-signal tokens that maximize the likelihood
> of your desired outcome. — Anthropic, "Effective Context Engineering"

## Before Writing a Knowledge File

1. **Check if it already exists** — grep `knowledge/` for overlapping content
2. **Ask: will this be read every session?** If no, consider a task-local note instead
3. **Ask: can this be derived on the fly?** If a 2-line grep/glob gets the answer, don't persist it

## File Size Rules

| Type | Target | Hard Max | Notes |
|------|--------|----------|-------|
| CLAUDE.md / rules | <200 lines | 200 | Loaded every session |
| Quick reference | <300 lines | 400 | Scanned at gate entry |
| Deep-dive knowledge | <500 lines | 800 | Loaded on demand only |
| Catalog / registry | No limit | — | Never load fully; grep into it |

## Structure Rules

- **Headers are navigation** — use `##` sections so grep + head finds the right block
- **Tables over prose** — 5 rows of table replaces 20 lines of text
- **One file, one topic** — split rather than append
- **No duplication** — reference other files with "See `FILE.md` § Section"
- **Concrete over vague** — thresholds, commands, URLs; not "consider doing X"

## What to Store vs. What to Skip

### Store
- Thresholds, limits, magic numbers (hard to re-derive)
- API endpoints, auth patterns, rate limits (painful to re-discover)
- Architectural decisions and their rationale
- Failure modes and their fixes (postmortems)
- Cross-cutting patterns used by multiple strategies/scripts

### Skip
- Raw tool output or data dumps
- Anything still fresh in the active conversation
- One-off debug sessions (use git commit messages instead)
- Information available via `--help` or docstrings

## Knowledge Update Rules

### When to Update
Both `/strategy` and `/dev` have mandatory end-of-session knowledge capture steps.
See each skill's SKILL.md for the specific file-by-file checklist.

### Promote-on-Validation Lifecycle
1. **Session findings** → `findings/strategy-findings.jsonl` (raw, append-only)
2. **Validated findings** → specific knowledge file section (confirmed by multiple analyses or review)
3. **Domain knowledge** → generalized insight in a knowledge file (extracted from multiple validated findings)

Never promote raw session findings directly to knowledge files. Require confirmation first.

### Staleness Markers
When updating any knowledge file, add or update in the file header:
```
> Last updated: YYYY-MM-DD
```
Files not updated in 90 days should be reviewed for accuracy during Gate 0 Step 0.5 (findings curation).

### Append-Only for State Files
`RESEARCH_STATUS.md`, `PROJECT_STATUS.md` — add new entries, don't rewrite history.
When these files exceed 100K, archive older sections to `memory/archive/`.

### Size Discipline
If a knowledge file exceeds its target size (see table above), split or archive older
content rather than letting it grow unbounded. Check with `wc -l knowledge/*.md`.

## Maintenance

- After consolidation: delete or archive superseded files
- Quarterly: check line counts with `wc -l knowledge/*.md`
- If a file exceeds its target: split it or compress tables
- If two files overlap >50%: merge into one

## Context Loading Strategy

```
Session start:  CLAUDE.md + rules/ + STRATEGY_QUICK_REFERENCE.md (~600 lines)
On demand:      Read specific knowledge file section via grep/head
Never:          Load all knowledge files at once (~5000+ lines)
```

Sources:
- https://code.claude.com/docs/en/memory
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
