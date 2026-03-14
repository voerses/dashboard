# AI Development Process — Claude Code Instructions

## Meta-Rules

1. **Code over specs.** When implementation and spec disagree, follow the implementation. Flag the divergence but don't "fix" toward the spec without explicit instruction.
2. **Pattern over principle.** Don't invent new patterns. Find how the codebase already does it and follow that.
3. **Check blast radius.** Before changing a shared package, check downstream dependents. Document high-impact packages in your project's blast radius list.
4. **Trace before coding.** For cross-service changes, understand the end-to-end flow before modifying code.
5. **Test like we test.** Follow existing test patterns in the codebase.
6. **v3/ is FROZEN.** Never modify files in `v3/`. All engine, validation, simulation, and universe code lives in `v4/`. The `v3/` directory is legacy reference only — all imports have been migrated to `v4/`.

## Slash Commands

| Command | Purpose |
|---------|---------|
| `/dev` | Start the full 5-phase development workflow for a feature |
| `/impact <package>` | Assess blast radius of a change to a shared package |
| `/review` | Run independent code review (subagent) |
| `/discover-repo` | Systematically explore a repo's structure and conventions |
| `/strategy` | Switch to 8-gate strategy development pipeline (return-first, kill losers fast) |

## Enforcement Hooks

Process rules in `.claude/rules/` are enforced by hooks in `.claude/hooks/`, wired via `.claude/settings.json`. These provide Tier 2 enforcement (~90%+ compliance) on top of Tier 1 rules (~60-80%).

| Hook | Event | Blocks? | What it enforces |
|------|-------|---------|------------------|
| `session-resume.sh` | SessionStart | No | Re-injects active feature state (phase, tasks, git branch) on startup/resume/compact |
| `branch-protection.sh` | PreToolUse (Bash) | Yes | Blocks git commit/push on main when a feature is active |
| `pre-commit-checks.sh` | PreToolUse (Bash) | Yes | Verifies phase is implement/complete before commits |
| `phase-gate.sh` | PreToolUse (Edit/Write) | Yes | Blocks source files in specify/design, non-test files in decompose |
| `tdd-guard.sh` | PreToolUse (Edit/Write) | Yes | Blocks source files if no test snapshot exists |
| `test-mod-detector.sh` | PreToolUse (Edit/Write) | No (warns) | Flags acceptance test modifications during implement phase |
| `review-gate.sh` | PreToolUse (Edit/Write) | Yes | Blocks PHASE transitions without required review artifacts |
| `strategy-gate-guard.sh` | PreToolUse (Edit/Write) | Yes | Blocks strategy file writes before Gate 3 (Prototype) in strategy mode |
| `workflow-nudge.sh` | UserPromptSubmit | No | Injects phase/gate context into every prompt (mode-aware) |
| `stop-decompose-verify.sh` | Stop | Yes | Verifies all tests fail (RED) during decompose phase |

See `aidev/AIPIP/AIPIP-0005-session-continuity-enforcement.md` for the design rationale.

## Data Safety (Non-Negotiable)

The `data/` directory contains ~500MB+ of historical market data (gitignored, re-fetchable but takes 60+ minutes). These rules prevent accidental data loss:

1. **NEVER use `git checkout --orphan` in the main worktree.** Use `git worktree add /tmp/<name>` instead.
2. **NEVER use `git checkout -f` or `git clean -f`** without first running `bash tools/check_data_integrity.sh`.
3. **NEVER use `git add -A` or `git add .`** after any orphan/detached HEAD operation — it will stage gitignored data files.
4. **For gh-pages or other isolated branches:** Always use a separate worktree:
   ```bash
   git worktree add /tmp/gh-pages gh-pages
   # work in /tmp/gh-pages
   git worktree remove /tmp/gh-pages
   ```
5. **If data is lost:** Regenerate with `bash tools/fetch_all_perp_data.sh --force && /workspace/venv/bin/python tools/fetch_binance_spot.py --force && /workspace/venv/bin/python tools/build_parquet_cache.py`

## Customization

This is a generic AI development process framework. To adapt it for your project:

1. **Add project-specific knowledge** — Create `memory/` files with your architecture, conventions, and technology patterns.
2. **Define blast radius packages** — Update `.claude/rules/blast-radius.md` with your high-impact shared packages.
3. **Set conventions** — Update `.claude/rules/conventions.md`, `golang.md`, and `javascript.md` with your project's naming, imports, and patterns.
4. **Add repo guides** — Create `repo-guides/<repo>.md` files for per-repo context.
5. **Add scanner data** — Create `data/` files and `tools/query.py` for cross-cutting queries over your codebase.
