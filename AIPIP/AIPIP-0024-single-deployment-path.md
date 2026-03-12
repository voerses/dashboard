# AIPIP-0024: Single Paper Trading Deployment Path

**Status:** accepted
**Author:** Claude
**Date:** 2026-03-12

## Problem

Multiple paper trading deployment paths kept emerging over time:

1. **Multiple runner scripts:** `run_paper_live.py` (V3), `v4/run_paper.py` (V4 single),
   `v4/run_paper_multi.py` (V4 multi) — all coexisted, causing confusion about which to
   use and leading to multiple runner processes competing for the same state files.

2. **Multiple dashboard deployment paths:** The runner pushed dashboards using config
   order, but the CLI (`generate_dashboard_v2.py --all-pools`) discovered pools via
   filesystem glob (alphabetical order). This caused pool ordering inconsistencies every
   time the dashboard was regenerated manually.

3. **Stale runner accumulation:** Restarting the runner without properly killing old
   processes led to 2-3 simultaneous runners writing to the same state directories.

These problems were fixed in the 2026-03-12 session (old runners deleted, shared utils
extracted to `paper_utils.py`, dashboard CLI unified to use runner config), but the
strategy process docs still reference obsolete paths and don't codify the single-path
rule.

## Proposal

### Rule: One Runner, One Config, One Dashboard Path

All paper trading goes through:

| Component | Single Path | Forbidden |
|-----------|-------------|-----------|
| Runner | `v4/run_paper_multi.py` | Creating new `run_paper*.py` files |
| Config | `configs/multi_v4_paper.json` | Per-strategy standalone configs for running |
| Dashboard | Runner pushes after each tick; CLI reads same config | `--all-pools`, filesystem glob discovery, `--once` |
| State | `state/v4_paper_*/` directories | Running engines outside multi-runner |

### Changes to SKILL.md Gate 6 Deployment Checklist

Replace the current AIPIP-0022 checklist with a simplified version that:

1. Removes references to deleted files (`run_paper.py`, `run_paper.py --once`)
2. Removes step to create standalone portfolio config (no longer needed — pool config
   lives directly in `configs/multi_v4_paper.json`)
3. Adds explicit "NEVER create a new runner script" rule
4. Adds runner process verification (check only ONE runner PID exists)
5. Adds dashboard consistency check (CLI produces same tab order as runner)

### Changes to Quick Reference

Add a "Paper Trading Architecture" one-liner in the Gate 6 section:

> Single deployment path: `v4/run_paper_multi.py` + `configs/multi_v4_paper.json`.
> NEVER create new runner scripts or alternative dashboard paths.

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Rewrite Gate 6 deployment checklist |
| `knowledge/STRATEGY_QUICK_REFERENCE.md` | Add single-path note to Gate 6 section |

## Change Log

| Date | Change |
|------|--------|
| 2026-03-12 | Proposed |
| 2026-03-12 | Accepted and implemented |
