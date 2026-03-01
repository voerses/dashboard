---
name: free
description: "Switch to freeflow mode — no process enforcement, you guide directly"
user_invocable: true
---

# Freeflow Mode

Disable all process enforcement. No phase gates, no workflow nudges, no commit restrictions. You guide the work directly.

## Instructions

1. Write `freeflow` to the process mode file:

```bash
echo "freeflow" > "$CLAUDE_PROJECT_DIR/.process-mode"
```

2. Confirm to the user:

> **Switched to freeflow mode.** All process hooks are paused. You're in control — no phase gates or workflow constraints.
>
> Use `/dev` to start a structured feature workflow, `/strategy` for strategy development mode.
