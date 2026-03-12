---
id: AIPIP-0023
title: Mandatory knowledge base update after deployment + open exploration mandate
status: accepted
author: @claude
created: 2026-03-11
---

## Problem

Two related issues:

### A. Knowledge base goes stale after deployment

After completing strategy development (passing Gate 5) and deploying to paper trading
(Gate 6), the knowledge base files are not updated. This means:

1. **Mission drift** — The `.strategy-mission` file still reflects the old goal after
   it has been achieved or superseded by new results.
2. **Stale quick reference** — `STRATEGY_QUICK_REFERENCE.md` doesn't list new strategies,
   new paper trading pools, or updated edge family coverage. The next session's Gate 0/Gate 2
   dedup checks work from outdated information.
3. **Lost institutional memory** — Key findings from development (e.g., "funding carry
   improves Sharpe when combined with momentum") only exist in `findings/strategy-findings.jsonl`,
   not in the structured knowledge files where they inform future decisions.
4. **PROJECT_STATUS.md goes stale** — New strategies, tier changes, and findings aren't
   reflected until someone manually remembers to update.

In the s63/s65 development cycle, all knowledge updates were done ad-hoc after the user
reminded us. This should be automatic.

### B. Process anchored to single baseline strategy

The current process assumes all new strategies are **complements to s58**. This is
visible in:

1. **Class D hypothesis template** — reads "[Strategy] complements s58 in [regime]"
   which frames every new idea as an s58 addon.
2. **V4-Gate 5 evaluation** — Mode A/B/C all compare against "production portfolio"
   but the framing biases toward complementing rather than replacing.
3. **Mission anchoring** — `.strategy-mission` baselines are s58 metrics, and kill
   criteria are defined relative to s58 performance.
4. **Missing exploration paths** — There's no explicit encouragement to explore entirely
   new multi-strategy portfolios that could supersede s58 entirely.

The user is open to ANY strategy that performs better — including new standalone strategies
that become their own multi-portfolio baselines. The process should encourage broad
exploration, not restrict it to s58 complements.

## Proposal

Two changes:

### Change 1: Gate 6.5 — Mandatory Knowledge Update

Add a mandatory **Gate 6.5: Knowledge Update** step between deployment (Gate 6) and
ongoing monitoring (Gate 7). This step runs immediately after paper trading deployment
is confirmed working (first tick successful).

### Gate 6.5: Knowledge Update (< 10 min)

After confirming paper trading deployment (first tick logged), update ALL of the following:

1. **`memory/PROJECT_STATUS.md`**:
   - Add strategy to V4 Validated or V4 Production table
   - Add key findings to Key Findings section (numbered, continue sequence)
   - Update "Active" line in header
   - Add any killed strategies to Graveyard table

2. **`knowledge/STRATEGY_QUICK_REFERENCE.md`**:
   - Add strategy to appropriate dedup table (Per-Token Perp, Combined, etc.)
   - Update Paper Trading table with new pool
   - Update Tier C list if any strategies were killed during this cycle
   - Update Key Constraints if new edge families were validated

3. **`.claude/.strategy-mission`**:
   - Update baselines with new portfolio metrics
   - Add strategy to Previous Attempts section
   - Update kill criteria if new baselines are higher
   - Update edge family coverage
   - If mission is achieved, change status to `paper-trading-monitoring`
   - Add concrete Next Actions

4. **`findings/strategy-findings.jsonl`**:
   - Verify all gate findings were logged (should already be done at each gate)
   - Curate if >50 entries since last curation

5. **`strategies/GRAVEYARD.md`**:
   - Add any strategies killed during this development cycle

### Gate Report Format

```
## Gate 6.5: Knowledge Update — DONE

| File | Updated | Changes |
|------|---------|---------|
| PROJECT_STATUS.md | Yes | Added sNN to V4 Validated, findings 25-28, graveyard entries |
| STRATEGY_QUICK_REFERENCE.md | Yes | Added sNN to Perp table, updated Paper Trading table |
| .strategy-mission | Yes | Updated baselines, added attempts, status → monitoring |
| findings/strategy-findings.jsonl | Yes | N entries logged, no curation needed |
| GRAVEYARD.md | Yes | Added sNN_killed_strategy |

**All knowledge files current. Ready for next development cycle.**
```

### Gate State

```bash
echo "gate6.5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
# Then after completion:
echo "gate7" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### Change 2: Open Exploration Mandate

Remove the s58-anchoring from the strategy process. Strategies should be evaluated on
absolute merit, not just as complements to the current production portfolio.

**Specific changes to SKILL.md:**

1. **Class D hypothesis template** — Change from:
   > "[Strategy] complements s58 in [regime] because [mechanism]"

   To:
   > "[Strategy] generates alpha in [regime/market] because [mechanism]"

2. **Class D description** — Change from "Portfolio components for s58" to
   "V4 portfolio components, standalone or complement" and similar updates to the
   when-to-use table.

3. **V4-Gate 5 Mode descriptions** — Mode A becomes "Portfolio Complement" (generic,
   not s58-specific). Mode B becomes "Standalone" (can the strategy stand on its own
   or form a new portfolio). Mode C becomes "Best Multi-Portfolio" (find the best
   combination of ALL available strategies, including potentially dropping s58 components).

4. **V4 subtypes** — Add "standalone alpha" as a valid subtype alongside sideways
   complement, funding harvester, etc.

5. **Mission file guidelines** — When a Mode B or Mode C configuration beats the
   current production baseline, the mission should evolve to use the new best
   configuration as the baseline — not stay anchored to s58.

**Philosophy:** The best portfolio is the one that makes the most money with acceptable
risk. If a new strategy or combination beats s58, it becomes the new standard. s58 is
the current champion, not a permanent fixture.

## Alternatives Considered

1. **Update at each gate individually** — Already done for findings, but PROJECT_STATUS
   and quick reference updates during development would be premature (strategy might get
   killed at next gate). Better to batch after deployment confirms.

2. **Make it part of Gate 6** — Gate 6 is already complex (deployment checklist from
   AIPIP-0022). Separating knowledge update keeps concerns clean and makes it harder to skip.

3. **Automated hook** — Could write a hook that blocks Gate 7 without knowledge updates.
   Overkill for now — the gate report format with checklist is sufficient enforcement.

## Impact

- **`.claude/skills/strategy/SKILL.md`** — Add Gate 6.5 section between Gate 6 and Gate 7.
  Update gate table in the intro to show Gate 6.5. Remove s58-specific anchoring from
  Class D hypothesis template, when-to-use table, V4 subtypes, and Gate 5 mode descriptions.
- **`knowledge/STRATEGY_QUICK_REFERENCE.md`** — No structural change, just referenced as
  a target of Gate 6.5 updates.
- **No hook changes** — Enforcement is via the gate report checklist, not a blocking hook.

## Change Log

- 2026-03-11: Created.
- 2026-03-11: Accepted and implemented. Gate 6.5 added to SKILL.md. s58-anchoring removed from Class D template, when-to-use table, V4 subtypes, and V4-Gate 5 mode descriptions.
