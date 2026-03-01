#!/bin/bash
# Strategy Gate Guard Hook (PreToolUse: Write|Edit)
# Enforces that strategy files can only be written at Gate 3+
# and that the gate state file exists when in strategy mode.
#
# AIPIP-0014: Gate-based strategy validation pipeline enforcement.
#
# Exit codes:
#   0 = allow
#   2 = block

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
MODE_FILE="$PROJECT_DIR/.process-mode"
GATE_FILE="$PROJECT_DIR/.strategy-gate"

# Only enforce in strategy mode
if [ ! -f "$MODE_FILE" ]; then
  exit 0
fi
MODE=$(cut -d: -f1 < "$MODE_FILE")
if [ "$MODE" != "strategy" ]; then
  exit 0
fi

# Read the file being written
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.file // empty')

# No file path = not a file write
[ -z "$FILE_PATH" ] && exit 0

# Determine what kind of file is being written
IS_STRATEGY=0
IS_GATE_FILE=0
IS_GRAVEYARD=0
IS_KNOWLEDGE=0
IS_SPEC=0

# Strategy source files: strategies/sNN_*.py
if echo "$FILE_PATH" | grep -qE 'strategies/s[0-9]+.*\.py$'; then
  IS_STRATEGY=1
fi

# Gate state file and process mode file — always allowed
if echo "$FILE_PATH" | grep -qE '\.(strategy-gate|process-mode)$'; then
  IS_GATE_FILE=1
fi

# Graveyard log — always allowed
if echo "$FILE_PATH" | grep -qE 'GRAVEYARD\.md$'; then
  IS_GRAVEYARD=1
fi

# Knowledge files — always allowed (research output)
if echo "$FILE_PATH" | grep -qE 'knowledge/'; then
  IS_KNOWLEDGE=1
fi

# Spec files — always allowed
if echo "$FILE_PATH" | grep -qE '\.specs/'; then
  IS_SPEC=1
fi

# Non-strategy files are always allowed
if [ $IS_STRATEGY -eq 0 ]; then
  exit 0
fi

# Strategy files require gate state
if [ ! -f "$GATE_FILE" ]; then
  echo "BLOCKED: Strategy mode is active but no gate state file found." >&2
  echo "Run: echo \"gate0\" > \"$GATE_FILE\"" >&2
  echo "Then follow the 8-gate pipeline. See /strategy skill for details." >&2
  exit 2
fi

GATE=$(cat "$GATE_FILE" | tr -d '[:space:]')

# Strategy files can only be written at Gate 3 (Prototype) or later
case "$GATE" in
  gate0|gate1|gate2)
    echo "BLOCKED: Cannot write strategy files at $GATE." >&2
    echo "You are in the $GATE phase. Strategy code is only allowed at Gate 3 (Prototype) or later." >&2
    echo "" >&2
    case "$GATE" in
      gate0) echo "Complete Gate 0 (Idea Screening) first — write hypothesis, check graveyard." >&2 ;;
      gate1) echo "Complete Gate 1 (Signal Lab) first — run IC testing, check significance." >&2 ;;
      gate2) echo "Complete Gate 2 (Knowledge + Dedup) first — read all KB files, check overlaps." >&2 ;;
    esac
    echo "See: knowledge/process/STRATEGY_PIPELINE_GATES.md for gate details." >&2
    exit 2
    ;;
  gate3|gate4|gate5|gate6|gate7)
    # Allowed — proceed
    exit 0
    ;;
  *)
    # Unknown gate state — warn but allow
    echo "{\"additionalContext\": \"WARNING: Unknown gate state '$GATE'. Expected gate0-gate7. Proceeding but verify your gate state.\"}"
    exit 0
    ;;
esac
