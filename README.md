# aidev — AI Development Process Framework

A structured AI development process for Claude Code. Enforces phased workflows (specify, design, decompose, implement, complete), test-driven development, independent code review, and human approval gates.

Clone this repo into your project directory, run `/setup` inside Claude, and you're ready to go.

## Quick Start

```bash
cd ~/my-project                # your project directory (create it if new)
git clone <aidev-repo-url> aidev
claude                         # start Claude Code from project root
```

Inside Claude, run:
```
/setup                         # creates symlinks, process is immediately active
```

## Requirements

- macOS or Linux
- bash 3.1+ (macOS default works)
- git 2.x+
- jq 1.6+ (`brew install jq` / `apt install jq`)
- gh (GitHub CLI, optional — for repo cloning and PR workflows)

## How It Works

Run `claude` from the project root. Three symlinks wire Claude Code to aidev's process files:

| Symlink | Target | Purpose |
|---------|--------|---------|
| `.claude` | `aidev/.claude` | Hooks, rules, skills, settings |
| `CLAUDE.md` | `aidev/CLAUDE.md` | Root instructions |
| `.specs` | `aidev/.specs` | Feature workflow templates + state |

Working repos are siblings of `aidev/` — clone them directly into the project root.

## Directory Layout

```
~/my-project/                        ← project root, run `claude` here
├── .claude → aidev/.claude          ← symlink
├── CLAUDE.md → aidev/CLAUDE.md      ← symlink
├── .specs → aidev/.specs            ← symlink
├── aidev/                           ← this repo
│   ├── .claude/                     # Hooks, rules, skills, settings
│   ├── CLAUDE.md                    # Root instructions
│   ├── AIPIP/                       # Process improvement proposals
│   ├── research/                    # Research reports
│   └── .specs/                      # Templates + workspace state
├── your-repo/                       ← working repo
└── ...
```

## Knowledge System

| Layer | What | Where | Loaded |
|-------|------|-------|--------|
| 1 | Root instructions + rules | `CLAUDE.md`, `.claude/rules/` | Always |
| 2 | Skills — reusable workflows | `.claude/skills/` | On invocation |
| 3 | Research + AIPIPs — decision history | `research/`, `AIPIP/` | On demand |

## Customization

To adapt this framework for your project, see the "Customization" section in `CLAUDE.md`. You can add:
- Project-specific knowledge files (`memory/`)
- Per-repo guides (`repo-guides/`)
- Scanner data and query tools (`data/`, `tools/`)
- Additional skills (`.claude/skills/`)

## Updating

```bash
cd aidev && git pull
# Process changes are active immediately — symlinks still point to the right place
```

## Process Governance (AIPIPs)

Changes to the AI development process (rules, hooks, skills) require an AIPIP (AI Process Improvement Proposal). See [AIPIP/README.md](AIPIP/README.md) for the format and team PR flow.
