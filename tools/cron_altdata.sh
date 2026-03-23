#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# cron_altdata.sh — Hourly alternative data collection
#
# Runs all alternative data fetchers in sequence.
# Designed for crontab:
#   0 * * * * /workspace/crypto_backtest/tools/cron_altdata.sh >> /workspace/crypto_backtest/logs/cron_altdata.log 2>&1
#
# Each fetcher appends to its own parquet file (no overwrites).
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="/workspace/venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "========================================"
echo "Alternative data collection started: $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
echo "========================================"

# 1. Binance positioning data (built by another agent)
if [ -f "$SCRIPT_DIR/fetch_alternative_data.py" ]; then
    echo ""
    echo "[1/3] Binance positioning..."
    $PYTHON "$SCRIPT_DIR/fetch_alternative_data.py" || echo "[WARN] Binance positioning fetch failed"
else
    echo ""
    echo "[1/3] Binance positioning — script not found, skipping"
fi

# 2. Binance L/S history (paginated, merges with existing data)
echo ""
echo "[2/3] Binance L/S history (28-day window, incremental merge)..."
$PYTHON "$SCRIPT_DIR/fetch_binance_ls_history.py" || echo "[WARN] Binance L/S history fetch failed"

# 3. Deribit options snapshots
echo ""
echo "[3/3] Deribit options..."
$PYTHON "$SCRIPT_DIR/fetch_deribit_options.py" || echo "[WARN] Deribit options fetch failed"

echo ""
echo "========================================"
echo "Alternative data collection finished: $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
echo "========================================"
