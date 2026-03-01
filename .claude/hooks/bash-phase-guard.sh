#!/bin/bash
# Bash Phase Guard Hook (PreToolUse, matcher: Bash)
# AIPIP-0013:
#   1. Block writes to .process-mode (only user can switch modes via /dev /strategy /free)
#   2. Block action commands during specify/design phases in dev mode
#
# Exit codes:
#   0 = allow
#   2 = block

set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"

# Read the command first (stdin can only be read once)
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$COMMAND" ] && exit 0

# --- Guard 1: Block .process-mode writes (mode protection) ---
# Only user can switch modes via /dev, /strategy, /free skill commands.
# The skills tell the AI to run `echo "mode" > .process-mode` — the user approves that tool call.
# This guard catches UNAUTHORIZED writes (AI deciding to change mode on its own).
# Since skills and manual both use Bash, we warn rather than block — the user sees the tool call
# and can deny it if they didn't request a mode switch.
if echo "$COMMAND" | grep -qE '\.process-mode'; then
  echo "WARNING: Modifying .process-mode. Only the user should switch process modes (via /dev, /strategy, /free)." >&2
  # Allow but warn — user approves/denies the tool call
fi

# --- Guard 2: Phase-based Bash restrictions (dev mode only) ---
MODE_FILE="$PROJECT_DIR/.process-mode"
if [ ! -f "$MODE_FILE" ]; then
  exit 0
fi
MODE=$(cut -d: -f1 < "$MODE_FILE")
if [ "$MODE" != "dev" ]; then
  exit 0
fi

# Check phase — only enforce during specify and design
cd "$PROJECT_DIR" || exit 0
PHASE_FILE=$(find .specs/active -name PHASE 2>/dev/null | head -1)
[ -z "$PHASE_FILE" ] && exit 0

PHASE=$(cat "$PHASE_FILE")
if [[ "$PHASE" != "specify" && "$PHASE" != "design" ]]; then
  exit 0
fi

# Block action patterns — commands that modify the environment
# These are inappropriate during specify/design (read-only phases)
if echo "$COMMAND" | grep -qE '\b(pip|pip3)\s+install\b'; then
  echo "BLOCKED: 'pip install' not allowed during '$PHASE' phase. This is a read-only phase — no environment modifications." >&2
  echo "Move to implement phase first, or switch to freeflow mode." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\bnpm\s+(install|i|ci)\b'; then
  echo "BLOCKED: 'npm install' not allowed during '$PHASE' phase. This is a read-only phase." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\b(apt-get|apt|brew|yum|dnf|pacman)\s+(install|update|upgrade)\b'; then
  echo "BLOCKED: Package manager commands not allowed during '$PHASE' phase. This is a read-only phase." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\bcargo\s+install\b'; then
  echo "BLOCKED: 'cargo install' not allowed during '$PHASE' phase. This is a read-only phase." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\bdocker\s+(run|build|compose)\b'; then
  echo "BLOCKED: Docker commands not allowed during '$PHASE' phase. This is a read-only phase." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\bmake\b' && ! echo "$COMMAND" | grep -qE '\bmake\s+(help|info|print|check)\b'; then
  echo "BLOCKED: 'make' not allowed during '$PHASE' phase (except make help/info/check). This is a read-only phase." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qE '\bwget\b|\bcurl\s+.*-[oO]\b'; then
  echo "BLOCKED: Download commands not allowed during '$PHASE' phase. This is a read-only phase." >&2
  exit 2
fi

# Allow everything else (ls, cat, grep, git log, python --version, etc.)
exit 0
