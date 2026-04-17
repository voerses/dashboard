#!/bin/bash
# ============================================================
# Start All Services — Paper Trader + Data Fetchers + Overlays
# ============================================================
#
# USAGE:
#   bash tools/start_all_services.sh
#
# STOP ALL:
#   bash tools/stop_all_services.sh
#
# CHECK STATUS:
#   bash tools/check_services.sh
#
# ============================================================

cd /workspace/crypto_backtest

echo "=== Starting all services ==="
echo "$(date -u): Starting..."

# 1. Paper Trading Runner (s513 + s523c + s524m pools, $450K total)
echo "[1/4] Starting paper trader..."
nohup /workspace/venv/bin/python -u -m v4.run_paper_multi \
    --config configs/runner_pool_config.json \
    > /tmp/paper_runner.log 2>&1 &
echo $! > /tmp/paper_runner.pid
echo "  PID: $(cat /tmp/paper_runner.pid)"

# 2. Pre-midnight data fetcher (23:50 UTC — positioning + TOTAL2/TOTAL3)
echo "[2/4] Starting midnight fetch loop..."
nohup bash tools/run_midnight_fetch_loop.sh \
    > /tmp/midnight_fetch.log 2>&1 &
# PID written inside the script to /tmp/midnight_fetch_loop.pid
sleep 1
echo "  PID: $(cat /tmp/midnight_fetch_loop.pid 2>/dev/null || echo 'starting...')"

# 3. Daily metrics updater (08:00 UTC — 5min binance metrics)
echo "[3/4] Starting daily metrics loop..."
nohup bash tools/run_daily_metrics_loop.sh \
    > /tmp/daily_metrics_loop.log 2>&1 &
sleep 1
echo "  PID: $(cat /tmp/daily_metrics_loop.pid 2>/dev/null || echo 'starting...')"

# 4. Mission P breadth cull sidecar (01:30 UTC — s523c shadow monitor)
echo "[4/4] Starting breadth cull loop..."
nohup bash tools/run_breadth_cull_loop.sh \
    > /tmp/breadth_cull_loop.log 2>&1 &
sleep 1
echo "  PID: $(cat /tmp/breadth_cull_loop.pid 2>/dev/null || echo 'starting...')"

echo ""
echo "=== All services started ==="
echo "Check status: bash tools/check_services.sh"
echo "View logs:    tail -f /tmp/paper_runner.log"
