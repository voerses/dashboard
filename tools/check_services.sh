#!/bin/bash
# Check status of all paper trading services
echo "=== Service Status ==="

for svc in paper_runner midnight_fetch_loop daily_metrics_loop breadth_cull_loop; do
    pid_file="/tmp/${svc}.pid"
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            rss=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')
            uptime=$(ps -o etime= -p "$pid" 2>/dev/null | xargs)
            printf "  %-25s %-8s PID %-8s RSS %-8s Uptime %s\n" "$svc" "RUNNING" "$pid" "$rss" "$uptime"
        else
            printf "  %-25s %-8s (stale PID %s)\n" "$svc" "DEAD" "$pid"
        fi
    else
        printf "  %-25s %-8s\n" "$svc" "NO PID"
    fi
done

echo ""
echo "=== Quick Health ==="
echo "Paper runner log (last line): $(tail -1 /tmp/paper_runner.log 2>/dev/null || echo 'no log')"
echo "Last midnight fetch: $(grep 'Pipeline complete' /tmp/midnight_metrics.log 2>/dev/null | tail -1 || echo 'never')"
echo "Last daily metrics: $(tail -1 /tmp/daily_metrics_update.log 2>/dev/null || echo 'no log')"
