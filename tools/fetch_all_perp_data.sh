#!/bin/bash
# Fetch perpetual futures data from all 3 exchanges in parallel.
# Each exchange fetcher discovers ALL liquid tokens automatically.
#
# Usage:
#   ./tools/fetch_all_perp_data.sh              # all exchanges, all data
#   ./tools/fetch_all_perp_data.sh --force       # re-fetch everything
#   ./tools/fetch_all_perp_data.sh binance       # single exchange
#   ./tools/fetch_all_perp_data.sh hyperliquid-s3  # just S3 archive

set -e
cd "$(dirname "$0")/.."
PYTHON="/workspace/venv/bin/python"
FORCE_FLAG=""
EXCHANGE=""

for arg in "$@"; do
    case "$arg" in
        --force) FORCE_FLAG="--force" ;;
        binance|kraken|hyperliquid|hyperliquid-s3) EXCHANGE="$arg" ;;
    esac
done

run_fetcher() {
    local name="$1"
    local script="$2"
    local logfile="data/perp/${name}_fetch.log"
    echo "[$name] Starting... (log: $logfile)"
    $PYTHON "$script" $FORCE_FLAG > "$logfile" 2>&1 &
    echo $!
}

mkdir -p data/perp

PIDS=()

if [ -z "$EXCHANGE" ] || [ "$EXCHANGE" = "binance" ]; then
    PID=$(run_fetcher "binance" "tools/fetch_binance_perp.py")
    PIDS+=("binance:$PID")
fi

if [ -z "$EXCHANGE" ] || [ "$EXCHANGE" = "kraken" ]; then
    PID=$(run_fetcher "kraken" "tools/fetch_kraken_perp.py")
    PIDS+=("kraken:$PID")
fi

if [ -z "$EXCHANGE" ] || [ "$EXCHANGE" = "hyperliquid" ]; then
    PID=$(run_fetcher "hyperliquid" "tools/fetch_hyperliquid_perp.py")
    PIDS+=("hyperliquid:$PID")
fi

if [ -z "$EXCHANGE" ] || [ "$EXCHANGE" = "hyperliquid-s3" ]; then
    PID=$(run_fetcher "hyperliquid-s3" "tools/fetch_hyperliquid_s3.py")
    PIDS+=("hyperliquid-s3:$PID")
fi

echo ""
echo "All fetchers launched. Waiting for completion..."
echo ""

FAILED=0
for entry in "${PIDS[@]}"; do
    NAME="${entry%%:*}"
    PID="${entry##*:}"
    if wait "$PID"; then
        echo "[$NAME] DONE (exit 0)"
    else
        echo "[$NAME] FAILED (exit $?)"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "=== Summary ==="
echo "Fetchers run: ${#PIDS[@]}"
echo "Failed: $FAILED"
echo ""
echo "Data stored in:"
find data/perp -name "*.csv" -type f | wc -l
echo " CSV files total"
du -sh data/perp/ 2>/dev/null || true
echo ""
echo "Logs:"
ls -la data/perp/*_fetch.log 2>/dev/null || echo "  (none)"
