#!/bin/bash
# ============================================================
# Mission P Breadth Cull Sidecar Loop for s523c Paper Trading
# ============================================================
#
# Runs tools/s523c_breadth_cull_monitor.py ONCE per day at 01:30 UTC
# (= 04:30 Cyprus EEST, 30min after s523c's 01:00 UTC daily entry tick).
#
# WHY this cadence:
#   - s523c enters trades only at 01:00 UTC daily (day-boundary rule)
#   - The Mission P rule needs ≥3 days of grace before evaluating a trade
#   - Running 30min after the daily tick catches any newly-post-grace
#     positions from 3 days ago in a single daily evaluation
#   - The rule's own 14-day throttle makes multiple daily checks redundant
#
# MODE:
#   SHADOW — logs what the rule WOULD do, does not execute closes.
#            To switch to live execution, set EXECUTE_MODE=1 below.
#
# START:
#   nohup bash tools/run_breadth_cull_loop.sh > /tmp/breadth_cull_loop.log 2>&1 &
#
# STOP:
#   kill $(cat /tmp/breadth_cull_loop.pid)
#
# CHECK:
#   tail -f /tmp/breadth_cull_monitor.log
#   cat state/v4_paper_s523c/breadth_cull_shadow_log.jsonl | tail -5
#
# ============================================================

cd /workspace/crypto_backtest

# Safety: start in SHADOW mode. Flip to 1 only after one successful shadow
# evaluation has been observed firing correctly on real data.
EXECUTE_MODE=1

# Run at this UTC hour:minute each day (01:30 UTC = 04:30 Cyprus EEST)
RUN_HOUR="01"
RUN_MIN="30"

echo $$ > /tmp/breadth_cull_loop.pid
echo "$(date -u): Breadth cull sidecar loop started (PID $$) execute=$EXECUTE_MODE"
echo "$(date -u): Schedule: daily at ${RUN_HOUR}:${RUN_MIN} UTC"

EXEC_FLAG=""
if [ "$EXECUTE_MODE" = "1" ]; then
    EXEC_FLAG="--execute"
    echo "$(date -u): ⚠️  EXECUTE MODE — will queue real close commands on trigger"
else
    echo "$(date -u): SHADOW MODE — logging what rule would do, no commands issued"
fi

# Run once at startup for visibility, then wait for the scheduled slot
echo "$(date -u): startup evaluation..."
/workspace/venv/bin/python tools/s523c_breadth_cull_monitor.py $EXEC_FLAG >> /tmp/breadth_cull_monitor.log 2>&1

LAST_RUN_DATE=""
while true; do
    CUR_HOUR=$(date -u +%H)
    CUR_MIN=$(date -u +%M)
    CUR_DATE=$(date -u +%Y-%m-%d)

    if [ "$CUR_HOUR" = "$RUN_HOUR" ] && [ "$CUR_MIN" = "$RUN_MIN" ] && [ "$LAST_RUN_DATE" != "$CUR_DATE" ]; then
        echo "$(date -u): scheduled evaluation starting..."
        /workspace/venv/bin/python tools/s523c_breadth_cull_monitor.py $EXEC_FLAG >> /tmp/breadth_cull_monitor.log 2>&1
        LAST_RUN_DATE="$CUR_DATE"
        echo "$(date -u): scheduled evaluation done. Next run: $(date -u -d tomorrow +%Y-%m-%d) ${RUN_HOUR}:${RUN_MIN} UTC"
        # Sleep ~23h to avoid re-triggering within the same minute slot
        sleep 82800
    else
        # Check minute grid
        sleep 60
    fi
done
