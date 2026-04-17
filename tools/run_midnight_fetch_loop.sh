#!/bin/bash
# ============================================================
# Pre-Midnight Data Fetcher Loop
# ============================================================
#
# Runs at 23:50 UTC daily:
#   - tools/fetch_midnight_metrics.py   (positioning: OI, L/S ratio, taker)
#   - tools/fetch_total2_total3.py      (crypto market cap indices)
#
# Timed for 00:00 UTC bar close so strategy has same-day data.
#
# START:
#   nohup bash tools/run_midnight_fetch_loop.sh > /tmp/midnight_fetch.log 2>&1 &
#
# STOP:
#   kill $(cat /tmp/midnight_fetch_loop.pid)
#
# CHECK:
#   tail -f /tmp/midnight_metrics.log
#   tail -f /tmp/total2_update.log
#
# ============================================================

cd /workspace/crypto_backtest
echo $$ > /tmp/midnight_fetch_loop.pid
echo "$(date -u): Midnight fetch loop started (PID $$)"

while true; do
    HOUR=$(date -u +%H)
    MIN=$(date -u +%M)

    # Run at 23:50 UTC (before 00:00 bar close)
    if [ "$HOUR" = "23" ] && [ "$MIN" -ge 50 ]; then
        echo "$(date -u): Running midnight metrics fetch..."
        /workspace/venv/bin/python tools/fetch_midnight_metrics.py >> /tmp/midnight_metrics.log 2>&1
        echo "$(date -u): Running TOTAL2/TOTAL3 fetch..."
        /workspace/venv/bin/python tools/fetch_total2_total3.py >> /tmp/total2_update.log 2>&1
        echo "$(date -u): Midnight fetch complete."
        # Sleep 23 hours to avoid running twice in the same window
        sleep 82800
    else
        # Check every 5 minutes
        sleep 300
    fi
done
