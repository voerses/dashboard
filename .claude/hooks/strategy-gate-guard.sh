#!/bin/bash
# Strategy Gate Guard Hook (PreToolUse: Write|Edit)
# Enforces that strategy files can only be written at Gate 3+
# and that the gate state file exists when in strategy mode.
#
# AIPIP-0014: Gate-based strategy validation pipeline enforcement.
# AIPIP-0031: Mandatory raw backtest check before gate transitions past Gate 1.
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

# AIPIP-0031: When writing the gate state file, check for raw backtest before advancing past gate1
if [ $IS_GATE_FILE -eq 1 ] && echo "$FILE_PATH" | grep -q '\.strategy-gate$'; then
  # Read the new gate value being written (from tool input)
  NEW_GATE=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty' | tr -d '[:space:]')
  # If advancing to gate2+ from gate1, check for raw backtest output
  if echo "$NEW_GATE" | grep -qE '^gate[2-7]'; then
    BACKTEST_DIR="$(dirname "$PROJECT_DIR")/results/raw_backtest"
    if [ ! -d "$BACKTEST_DIR" ] || [ -z "$(ls "$BACKTEST_DIR" 2>/dev/null)" ]; then
      echo "{\"additionalContext\": \"WARNING (AIPIP-0031): Advancing to $NEW_GATE but no raw backtest results found in results/raw_backtest/. Every signal must pass raw backtest before advancing past Gate 1.\"}"
    fi
  fi
  exit 0
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
  gate3|gate3p|gate3o)
    # Gate 3 (Prototype) — strategy files allowed
    # AIPIP-0031: Check for raw backtest output before allowing strategy writes at Gate 3+
    STRAT_ID=$(echo "$FILE_PATH" | grep -oP 's\d+' | head -1)
    if [ -n "$STRAT_ID" ]; then
      BACKTEST_DIR="$(dirname "$PROJECT_DIR")/results/raw_backtest"
      if [ -d "$BACKTEST_DIR" ]; then
        BACKTEST_FILE=$(find "$BACKTEST_DIR" -name "${STRAT_ID}_*" -o -name "*${STRAT_ID}*" 2>/dev/null | head -1)
        # At Gate 3 we're writing the prototype — backtest comes after, so just warn
        if [ -z "$BACKTEST_FILE" ]; then
          echo "{\"additionalContext\": \"REMINDER (AIPIP-0031): Run raw backtest via tools/raw_backtest.py before advancing past Gate 3. No gate transition without a PASS verdict.\"}"
        fi
      fi
    fi
    exit 0
    ;;
  gate4|gate5|gate5p|gate5o|gate6|gate7)
    # Gate 4+ — strategy files allowed, warn if no sizing verification
    # Extract strategy ID from filename (e.g., s400 from strategies/s400_foo.py)
    STRAT_ID=$(echo "$FILE_PATH" | grep -oP 's\d+' | head -1)
    if [ -n "$STRAT_ID" ]; then
      # Check if strategy has SIZING_OVERRIDES (grep the file being written)
      STRAT_FILE=$(find "$(dirname "$PROJECT_DIR")/strategies" -name "${STRAT_ID}_*.py" 2>/dev/null | head -1)
      if [ -n "$STRAT_FILE" ] && grep -q "SIZING_OVERRIDES" "$STRAT_FILE" 2>/dev/null; then
        VERIFY_FILE="$PROJECT_DIR/../.specs/active/${STRAT_ID}/sizing_verification.json"
        if [ ! -f "$VERIFY_FILE" ]; then
          echo "{\"additionalContext\": \"WARNING: Strategy $STRAT_ID has SIZING_OVERRIDES but no sizing_verification.json. Run: python tools/verify_sizing.py $STRAT_ID --save .specs/active/$STRAT_ID/sizing_verification.json\"}"
        fi
      fi
    fi
    exit 0
    ;;
  *)
    # Unknown gate state — warn but allow
    echo "{\"additionalContext\": \"WARNING: Unknown gate state '$GATE'. Expected gate0-gate7. Proceeding but verify your gate state.\"}"
    exit 0
    ;;
esac
