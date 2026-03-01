---
id: AIPIP-0013
title: Workspace-Aware Process Modes
status: accepted
author: @claude
created: 2026-03-01
---

# AIPIP-0013: Workspace-Aware Process Modes

## Problem

1. **No mode toggle.** The process hooks always enforce `/dev` workflow constraints. There's no way to switch between structured `/dev` work, strategy development, or freeform exploration. When working on strategy research or quick experiments, the phase gates still fire and nudge toward the 5-phase workflow.

2. **No Bash-level phase enforcement.** Phase-gate only blocks Write/Edit tool calls. During specify/design phases, `pip install`, `docker run`, and other action commands go unchecked via the Bash tool. This led to a process violation: installing packages during the specify phase while building the paper trading system.

3. **Inconsistent project root resolution.** Some hooks use `$CLAUDE_PROJECT_DIR` (correct), others use `$(dirname "$0")/../..` (works via symlinks but is fragile). Should be consistent.

## Proposal

### 1. Process mode file: `$CLAUDE_PROJECT_DIR/.process-mode`

A simple text file at workspace root declaring the active process mode.

**Format:** `MODE` or `MODE:CONTEXT`

```
freeflow                       # No enforcement (default if file absent)
dev:paper-trading-setup        # Full /dev workflow, feature slug for spec lookup
strategy                       # Strategy development lifecycle
```

### 2. Mode switching

- `/dev` command writes `dev:<feature-slug>` to `.process-mode` when starting
- `/dev` writes `freeflow` when feature completes (phase → done)
- A future `/strategy` command writes `strategy`
- Absent file = `freeflow` (no enforcement)
- Only one mode active at a time

### 3. Hook mode check (all enforcement hooks)

Every hook adds a mode check at the top:

```bash
# --- Process mode check ---
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
MODE_FILE="$PROJECT_DIR/.process-mode"
if [ ! -f "$MODE_FILE" ]; then
  exit 0  # No mode file = freeflow
fi
MODE=$(cut -d: -f1 < "$MODE_FILE")
if [ "$MODE" = "freeflow" ]; then
  exit 0
fi
# Only enforce in dev mode (strategy mode has its own lighter rules, future AIPIP)
if [ "$MODE" != "dev" ]; then
  exit 0
fi
```

Hooks affected: phase-gate, tdd-guard, test-mod-detector, review-gate, branch-protection, pre-commit-checks, stop-decompose-verify.

**Exception:** `process-guard.sh` always enforces (process files are protected regardless of mode). `session-resume.sh` reads mode for context injection but doesn't block.

### 4. Bash-level phase guard (NEW hook)

New hook: `bash-phase-guard.sh` — triggers on `Bash` tool use.

During `specify` and `design` phases (dev mode only):
- **Block** commands matching action patterns: `pip install`, `npm install`, `apt`, `brew`, `cargo install`, `make`, `docker run/build`, `wget`, `curl -o`, `chmod +x`
- **Allow** read-only commands: `ls`, `cat`, `grep`, `find`, `git log/status/diff`, `python --version`, etc.
- **Warn** on ambiguous commands (don't block, just flag)

This catches the specific violation that prompted this AIPIP.

### 5. Consistent project root resolution

Replace all `cd "$(cd "$(dirname "$0")/../.." && pwd)"` with:

```bash
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
cd "$PROJECT_DIR" || exit 0
```

This is already the pattern in `session-resume.sh`, `branch-protection.sh`, `pre-commit-checks.sh`, and `process-guard.sh`. The remaining hooks (`phase-gate.sh`, `tdd-guard.sh`, `test-mod-detector.sh`, `stop-decompose-verify.sh`) need updating.

### 6. Workflow nudge mode awareness

`workflow-nudge.sh` outputs different context per mode:
- `dev`: Current behavior (active feature + phase)
- `strategy`: "Strategy development mode. Follow knowledge/STRATEGY_LIFECYCLE.md"
- `freeflow` / absent: No injection (exit 0 with empty context)

## Alternatives Considered

### A. Per-repo `.claude/` with separate hooks
Each repo gets its own hooks. Rejected: duplicates config, hooks diverge over time.

### B. Environment variable for mode
`export PROCESS_MODE=dev`. Rejected: doesn't persist across sessions/terminals.

### C. Phase-gate catches Bash too
Extend phase-gate.sh to handle Bash commands. Rejected: phase-gate is already complex. Separate hook follows single-responsibility.

## Impact

### Files modified

| File | Change |
|------|--------|
| `.claude/hooks/phase-gate.sh` | Add mode check, use `$CLAUDE_PROJECT_DIR` |
| `.claude/hooks/workflow-nudge.sh` | Mode-aware context injection |
| `.claude/hooks/branch-protection.sh` | Add mode check |
| `.claude/hooks/pre-commit-checks.sh` | Add mode check |
| `.claude/hooks/tdd-guard.sh` | Add mode check, use `$CLAUDE_PROJECT_DIR` |
| `.claude/hooks/test-mod-detector.sh` | Add mode check, use `$CLAUDE_PROJECT_DIR` |
| `.claude/hooks/review-gate.sh` | Add mode check |
| `.claude/hooks/stop-decompose-verify.sh` | Add mode check, use `$CLAUDE_PROJECT_DIR` |
| `.claude/hooks/session-resume.sh` | Show mode in context |
| `.claude/settings.json` | Add bash-phase-guard.sh to Bash matchers |

### New files

| File | Purpose |
|------|---------|
| `.claude/hooks/bash-phase-guard.sh` | Bash command enforcement during specify/design |

### NOT modified

| File | Reason |
|------|--------|
| `.claude/hooks/process-guard.sh` | Always enforces regardless of mode |

## Change Log

- 2026-03-01: Initial proposal
- 2026-03-01: Updated problem statement (hooks work via symlinks but lack mode toggle and Bash enforcement). Added bash-phase-guard.sh. Status → accepted.
- 2026-03-01: Implemented. All 8 enforcement hooks + workflow-nudge + session-resume updated. bash-phase-guard.sh created and wired into settings.json. All tests passing.
