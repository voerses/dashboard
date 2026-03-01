# AI Development Process — Claude Code Instructions

## Meta-Rules

1. **Code over specs.** When implementation and spec disagree, follow the implementation. Flag the divergence but don't "fix" toward the spec without explicit instruction.
2. **Pattern over principle.** Don't invent new patterns. Find how the codebase already does it and follow that.
3. **Check blast radius.** Before changing a shared package, check downstream dependents. Document high-impact packages in your project's blast radius list.
4. **Trace before coding.** For cross-service changes, understand the end-to-end flow before modifying code.
5. **Test like we test.** Follow existing test patterns in the codebase.

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

## Customization

This is a generic AI development process framework. To adapt it for your project:

1. **Add project-specific knowledge** — Create `memory/` files with your architecture, conventions, and technology patterns.
2. **Define blast radius packages** — Update `.claude/rules/blast-radius.md` with your high-impact shared packages.
3. **Set conventions** — Update `.claude/rules/conventions.md`, `golang.md`, and `javascript.md` with your project's naming, imports, and patterns.
4. **Add repo guides** — Create `repo-guides/<repo>.md` files for per-repo context.
5. **Add scanner data** — Create `data/` files and `tools/query.py` for cross-cutting queries over your codebase.
