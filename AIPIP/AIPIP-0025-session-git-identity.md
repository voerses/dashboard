---
id: AIPIP-0025
title: Set Git Identity on Session Start for Dashboard Pushes
status: accepted
author: @voerses
created: 2026-03-20
---

# AIPIP-0025: Set Git Identity on Session Start for Dashboard Pushes

## Problem

The paper trading runner's dashboard push (`push_to_ghpages`) clones the dashboard repo into a temp directory and commits there. This temp clone does not inherit the main repo's local `.git/config` identity. When `~/.gitconfig` is absent (which happens after every session restart), the commit fails silently with "Author identity unknown", and the dashboard goes stale.

This was observed on 2026-03-20: the runner logged "Dashboard pushed to gh-pages (17 tabs)" but the actual push failed because git couldn't determine the committer identity in the temp clone. The dashboard was stuck at the previous session's last successful push.

## Proposal

Add two lines to `session-resume.sh` to set the global git identity on every session start:

```bash
# Ensure git identity is set globally (needed for dashboard push to temp clones)
git config --global user.name "voerses" 2>/dev/null || true
git config --global user.email "voerses@users.noreply.github.com" 2>/dev/null || true
```

Place these before the workspace directory creation block.

## Alternatives Considered

1. **Fix `push_to_ghpages` directly** — Set `git config user.name/email` in the temp clone inside the function. This works but is a point fix; any other tool that clones to a temp dir would hit the same issue.
2. **Shell profile (`~/.bashrc`)** — Add git config commands to the shell profile. Less reliable since profile sourcing is environment-dependent.
3. **Do nothing, rely on `~/.gitconfig`** — Requires manually running `git config --global` after every session restart. Error-prone.

## Impact

| File | Change |
|------|--------|
| `.claude/hooks/session-resume.sh` | Add 2 lines after line 23 (before workspace dir creation) |

No other files affected. The change is purely additive and non-breaking.

## Change Log

- 2026-03-20: Proposed after diagnosing silent dashboard push failure.
