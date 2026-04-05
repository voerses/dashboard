#!/bin/bash
# ============================================================
# Daily Metrics Updater Loop for s523 Paper Trading
# ============================================================
#
# Runs tools/update_daily_metrics.py once per day at 08:00 UTC.
# data.binance.vision uploads previous day's 5-min metrics at ~07:40 UTC.
#
# START:
#   nohup bash tools/run_daily_metrics_loop.sh > /tmp/daily_metrics_loop.log 2>&1 &
#
# STOP:
#   kill $(cat /tmp/daily_metrics_loop.pid)
#
# CHECK:
#   tail -f /tmp/daily_metrics_update.log
#
# ALTERNATIVE (if cron is available):
#   crontab -e
#   0 8 * * * cd /workspace/crypto_backtest && /workspace/venv/bin/python tools/update_daily_metrics.py >> /tmp/daily_metrics_update.log 2>&1
#
# ============================================================

cd /workspace/crypto_backtest
echo $$ > /tmp/daily_metrics_loop.pid
echo "$(date -u): Daily metrics loop started (PID $$)"

while true; do
    # Get current hour UTC
    HOUR=$(date -u +%H)

    # Run at 08:xx UTC
    if [ "$HOUR" = "08" ]; then
        echo "$(date -u): Running daily metrics update..."
        /workspace/venv/bin/python tools/update_daily_metrics.py >> /tmp/daily_metrics_update.log 2>&1
        echo "$(date -u): Update complete."
        # Sleep 23 hours to avoid running twice in the same hour
        sleep 82800
    else
        # Check every 30 minutes
        sleep 1800
    fi
done
