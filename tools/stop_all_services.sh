#!/bin/bash
# Stop all paper trading services gracefully
cd /workspace/crypto_backtest

echo "=== Stopping all services ==="

for svc in paper_runner midnight_fetch_loop daily_metrics_loop breadth_cull_loop; do
    pid_file="/tmp/${svc}.pid"
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping $svc (PID $pid)..."
            kill "$pid"
            sleep 1
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                echo "  Force killing $pid..."
                kill -9 "$pid"
            fi
            echo "  Stopped."
        else
            echo "$svc already stopped (stale PID $pid)"
        fi
        rm -f "$pid_file"
    else
        echo "$svc: no PID file"
    fi
done

echo "=== All services stopped ==="
