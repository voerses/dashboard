#!/bin/bash
# Workflow Nudge Hook (UserPromptSubmit)
# Mode-aware context injection (AIPIP-0013).
#
# - dev mode: Inject active feature + phase context
# - strategy mode: Inject strategy lifecycle context
# - freeflow / absent: No injection
#
# Output: JSON with additionalContext field

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
MODE_FILE="$PROJECT_DIR/.process-mode"

# No mode file = freeflow, no nudge
if [ ! -f "$MODE_FILE" ]; then
  exit 0
fi

MODE=$(cut -d: -f1 < "$MODE_FILE")

case "$MODE" in
  dev)
    cd "$PROJECT_DIR" || exit 0
    ACTIVE=$(find .specs/active -name PHASE 2>/dev/null | head -1)
    if [ -z "$ACTIVE" ]; then
      echo '{"additionalContext": "Process mode: dev, but no active spec found. Use /dev to start a feature workflow."}'
    else
      PHASE=$(cat "$ACTIVE")
      FEATURE=$(basename "$(dirname "$ACTIVE")")
      echo "{\"additionalContext\": \"Process mode: dev | Feature: $FEATURE | Phase: $PHASE — Stay within phase constraints. Check .claude/rules/development-workflow.md for allowed actions.\"}"
    fi
    ;;
  strategy)
    GATE_FILE="$PROJECT_DIR/.strategy-gate"
    if [ -f "$GATE_FILE" ]; then
      GATE=$(cat "$GATE_FILE" | tr -d '[:space:]')
      case "$GATE" in
        gate0) GATE_DESC="Gate 0: Idea Screening — READ knowledge/STRATEGY_QUICK_REFERENCE.md (Available Capabilities + strategy classes). Choose class (per-token/portfolio/overlay), write hypothesis, check graveyard. No code yet." ;;
        gate1) GATE_DESC="Gate 1: Signal Lab — run IC testing, check significance. READ: process/SIGNAL_DISCOVERY_METHODS.md, INDICATOR_ANALYSIS.md. Kill if IC < 0.02 or t-stat < 2.0. No strategy code yet." ;;
        gate2) GATE_DESC="Gate 2: Knowledge + Dedup — read ALL KB files, compare against existing strategies. READ: STRATEGY_CATALOG.md, SIGNAL_DEVELOPMENT.md. Kill if >80% overlap (per-token) or >0.7 corr (portfolio). No strategy code yet." ;;
        gate3) GATE_DESC="Gate 3: Prototype — write vectorized strategy from TEMPLATE.py (per-token) or extend v3/ module (portfolio/overlay). READ: SIGNAL_DEVELOPMENT.md, PERFORMANCE_PATTERNS.md. Must be < 1ms/call." ;;
        gate4) GATE_DESC="Gate 4: Quick Validate — run v3/validation.py --strategy sNN --tokens BTC. Kill if BTC fails after 3 attempts." ;;
        gate5) GATE_DESC="Gate 5: Full Validate — run v3/validation.py all 49 tokens. Check Calmar > 0.5, Sortino > 1.0, DD < 25%. Tier assign by rate. For portfolio strategies: use 5P metrics (portfolio Calmar, corr vs S11)." ;;
        gate6) GATE_DESC="Gate 6: Paper Trading — deploy on live data, track 50+ trades. Kill if returns < 60% of backtest." ;;
        gate7) GATE_DESC="Gate 7: Production — deploy with risk limits, monitor for decay. Pull if rolling Calmar < 0 or 6mo no new high." ;;
        *) GATE_DESC="Unknown gate: $GATE. Check .strategy-gate file." ;;
      esac

      # Inject gate-relevant findings
      FINDINGS_FILE="$PROJECT_DIR/findings/strategy-findings.jsonl"
      GATE_FINDINGS=""
      if [ -f "$FINDINGS_FILE" ]; then
        GATE_NUM=$(echo "$GATE" | sed 's/gate//')
        GATE_FINDINGS=$(/workspace/venv/bin/python3 -c "
import sys, json
findings = []
for line in open('$FINDINGS_FILE'):
    try:
        f = json.loads(line.strip())
        g = str(f.get('gate',''))
        if g == '$GATE_NUM' or g.startswith('${GATE_NUM}'):
            findings.append(f'{f[\"strategy\"]}: {f[\"finding\"][:80]}')
    except: pass
for f in findings[-3:]:
    print(f'  - {f}')
" 2>/dev/null)
      fi

      if [ -n "$GATE_FINDINGS" ]; then
        CONTEXT="Process mode: strategy | $GATE_DESC | Recent findings at this gate:\n$GATE_FINDINGS\n| Objective: maximize returns, don't lose big (Calmar > Sortino > Sharpe)."
      else
        CONTEXT="Process mode: strategy | $GATE_DESC | Objective: maximize returns, don't lose big (Calmar > Sortino > Sharpe). READ knowledge/STRATEGY_QUICK_REFERENCE.md for thresholds."
      fi
      echo "{\"additionalContext\": \"$CONTEXT\"}"
    else
      echo '{"additionalContext": "Process mode: strategy — No gate state found. Run: echo gate0 > .strategy-gate to start the 8-gate pipeline. See /strategy skill for details."}'
    fi
    ;;
  freeflow|*)
    # No injection for freeflow or unknown modes
    exit 0
    ;;
esac
