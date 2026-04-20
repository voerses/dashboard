#!/bin/bash
# ============================================================
# M9 C-8 — Stop v5 Paper Trading Runner (v4 untouched)
#
# Only kills the v5 runner PID at /tmp/paper_runner_v5.pid. Never
# touches v4's /tmp/paper_runner.pid.
# ============================================================

cd /workspace/crypto_backtest

PID_FILE="/tmp/paper_runner_v5.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "V5 paper runner not running (no PID file at $PID_FILE)"
  exit 0
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
  echo "V5 paper runner PID $PID not alive; clearing stale PID file"
  rm -f "$PID_FILE"
  exit 0
fi

echo "Stopping V5 paper runner (PID $PID)..."
kill -TERM "$PID"
sleep 2
if kill -0 "$PID" 2>/dev/null; then
  echo "  SIGTERM ignored; sending SIGKILL"
  kill -KILL "$PID"
fi
rm -f "$PID_FILE"
echo "V5 paper runner stopped. v4 paper runner untouched (PID $(cat /tmp/paper_runner.pid 2>/dev/null || echo '<not running>'))"
