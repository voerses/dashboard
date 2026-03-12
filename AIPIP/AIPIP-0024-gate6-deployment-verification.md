# AIPIP-0024: Gate 6 Deployment Verification

**Status:** proposed
**Author:** Claude
**Date:** 2026-03-12

## Problem

Strategies that pass Gate 5 (validation) get `gate6` written to the strategy-gate state
file, but the actual deployment to the paper trading runner is a manual follow-through
step that can be forgotten. This happened with s59 (funding_mean_rev_v4) which passed
V4-Gate 5 conditional but was never added to the multi-runner config
(`configs/multi_v4_paper.json`).

The existing Gate 6 Deployment Checklist (AIPIP-0022) has the right steps, but there is
no verification that those steps were actually completed before Gate 6 monitoring begins.

## Root Cause

The Gate 5→6 transition writes `gate6` to the state file and outputs a gate report, but
the deployment itself is deferred ("go do this checklist"). If the session ends or context
resets between Gate 5 pass and deployment, the strategy sits validated but undeployed
indefinitely.

## Proposal

### 1. Make deployment atomic with the Gate 5→6 transition

Add a **mandatory deployment verification step** between the Gate 5 pass report and the
`gate6` state write. The gate file must NOT be set to `gate6` until deployment is
confirmed.

New flow:
```
Gate 5 PASS → output gate report → run deployment checklist → verify in runner log → THEN set gate6
```

### 2. Add verification command to SKILL.md Gate 6 section

After the deployment checklist steps, add a mandatory verification block:

```bash
# Verify strategy is actually running in the multi-runner
grep "pool_name.*sNN" configs/multi_v4_paper.json || echo "FAIL: not in config"
grep "sNN" /tmp/paper_multi.log | head -1 || echo "FAIL: not in runner log"
# ONLY set gate6 after both checks pass
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### 3. Add deployment status to Gate 6 report template

The Gate 6 report must include:

```
**Deployment verified:**
- [ ] Added to `configs/multi_v4_paper.json` (pool: <name>)
- [ ] Runner restarted (PID: <pid>)
- [ ] First tick logged (tick N, equity $X)
- [ ] Dashboard shows new pool tab
```

### 4. Update Quick Reference Gate 6 section

Add one-liner: "Deployment is part of Gate 5→6 transition, not a separate step. Do NOT
write gate6 until the strategy is confirmed running."

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Gate 6 section: move gate6 write AFTER deployment verification |
| `knowledge/STRATEGY_QUICK_REFERENCE.md` | Gate 6 section: add deployment verification note |

## Backward Compatibility

No breaking changes. Existing strategies already in the runner are unaffected. This only
changes the process for future Gate 5→6 transitions.

## Change Log

| Date | Change |
|------|--------|
| 2026-03-12 | Proposed |
