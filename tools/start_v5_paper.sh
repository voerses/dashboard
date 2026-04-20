#!/bin/bash
# ============================================================
# M9 C-8 — Start v5 Paper Trading Runner (feature-flagged)
#
# Separate from v4's tools/start_all_services.sh. v4 runner is never
# touched by this script. v5 writes to its own state dirs, own PID
# file, own log, own state.json output path.
#
# USAGE:
#   V5_PAPER_ENABLED=1 bash tools/start_v5_paper.sh
#
# STOP:
#   bash tools/stop_v5_paper.sh
#
# v4 untouched: /tmp/paper_runner.pid, state/v4_paper_multi/,
#   /srv/data/state.json, configs/runner_pool_config.json.
# ============================================================

cd /workspace/crypto_backtest

if [ "${V5_PAPER_ENABLED:-0}" != "1" ]; then
  echo "V5 paper disabled (set V5_PAPER_ENABLED=1 to enable)"
  exit 0
fi

PID_FILE="/tmp/paper_runner_v5.pid"
LOG_FILE="/tmp/paper_runner_v5.log"
STATE_FILE="/srv/data/state_v5.json"
CONFIG_FILE="configs/runner_pool_config_v5.json"

# Idempotent: don't start if already running
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "V5 paper runner already running: PID $(cat "$PID_FILE")"
  exit 0
fi

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: $CONFIG_FILE not found"
  exit 1
fi

echo "[v5] Starting paper trader..."
nohup /workspace/venv/bin/python -u -m v5.run_paper_multi \
    --config "$CONFIG_FILE" \
    > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "  PID: $PID"
echo "  Log: $LOG_FILE"
echo "  State: $STATE_FILE"
echo ""
echo "=== V5 paper trader started ==="
echo "Dashboard: <host>/v5 (requires symlink /srv/dashboard/current/v5 -> .)"
echo "Kill-switch: bash tools/stop_v5_paper.sh"
