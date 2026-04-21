#!/usr/bin/env bash
# M10 E7 / AC #25b — operator-run parallel-ops WS rate-limit smoke.
#
# Rationale (brief §AC25 / quant expert review 2026-04-20):
# Binance WS rate limits are wall-clock phenomena enforced at the exchange
# edge. TestClock-accelerated pytest would only exercise our own mock, not
# the exchange's rate-limit policy. This script is the live-half that
# complements v5/tests/test_m10_ws_backoff_retry.py (AC #25a).
#
# Usage:
#   bash tools/ws_ratelimit_parallel_smoke.sh [duration_minutes]
#
# Defaults:
#   duration_minutes: 30
#
# Prerequisites:
#   * Both v4 + v5 runners alive:
#       bash tools/start_all_services.sh
#       bash tools/start_v5_paper.sh
#   * WS error log being written at /srv/data/ws_errors.jsonl
#
# Pass/fail asserts:
#   * count(429) == 0 over the full duration  → PASS (green line)
#   * count(429) > 0 but backoff sleep trace observed → PASS (yellow;
#     retry path works)
#   * count(429) > 0 and NO backoff trace → FAIL (red; the retry path
#     is broken)
#
# Install: chmod +x tools/ws_ratelimit_parallel_smoke.sh
# Documented in knowledge/MIGRATION.md step 6.5 (connection verification).

set -euo pipefail

DURATION_MIN="${1:-30}"
WS_LOG="${WS_ERRORS_LOG:-/srv/data/ws_errors.jsonl}"
V5_LOG="${V5_PAPER_LOG:-/srv/data/paper_runner_v5.log}"
DURATION_SEC=$((DURATION_MIN * 60))

echo "=== M10 AC #25b — WS rate-limit parallel smoke ==="
echo "Duration:   ${DURATION_MIN} minutes (${DURATION_SEC} s)"
echo "WS log:     ${WS_LOG}"
echo "v5 log:     ${V5_LOG}"
echo ""

# Verify both runners are alive.
if [ ! -f /tmp/paper_runner.pid ]; then
    echo "ERROR: v4 runner PID file missing (/tmp/paper_runner.pid)"
    exit 2
fi
if [ ! -f /tmp/paper_runner_v5.pid ]; then
    echo "ERROR: v5 runner PID file missing (/tmp/paper_runner_v5.pid)"
    exit 2
fi
V4_PID="$(cat /tmp/paper_runner.pid)"
V5_PID="$(cat /tmp/paper_runner_v5.pid)"
if ! kill -0 "${V4_PID}" 2>/dev/null; then
    echo "ERROR: v4 PID ${V4_PID} not alive"
    exit 2
fi
if ! kill -0 "${V5_PID}" 2>/dev/null; then
    echo "ERROR: v5 PID ${V5_PID} not alive"
    exit 2
fi
echo "Both runners alive (v4 PID=${V4_PID}, v5 PID=${V5_PID})"
echo ""

# Snapshot baseline 429 count + backoff-trace count.
count_429_at() {
    local path="$1"
    if [ ! -f "${path}" ]; then
        echo 0
        return
    fi
    grep -c '"status_code":429\|429 rate' "${path}" 2>/dev/null || echo 0
}

count_backoff_at() {
    local path="$1"
    if [ ! -f "${path}" ]; then
        echo 0
        return
    fi
    grep -c 'BACKOFF.*30\|sleep.*30\|backoff.*30' "${path}" 2>/dev/null || echo 0
}

baseline_429=$(count_429_at "${WS_LOG}")
baseline_backoff=$(count_backoff_at "${V5_LOG}")
echo "Baseline 429 count:      ${baseline_429}"
echo "Baseline backoff count:  ${baseline_backoff}"

# Monitor loop: sample every 60s.
start_ts=$(date +%s)
end_ts=$((start_ts + DURATION_SEC))
while [ "$(date +%s)" -lt "${end_ts}" ]; do
    elapsed=$(($(date +%s) - start_ts))
    remaining=$((DURATION_SEC - elapsed))
    cur_429=$(count_429_at "${WS_LOG}")
    cur_backoff=$(count_backoff_at "${V5_LOG}")
    printf "[%5ds elapsed, %4ds remaining] 429=%s backoff=%s\n" \
        "${elapsed}" "${remaining}" \
        "$((cur_429 - baseline_429))" \
        "$((cur_backoff - baseline_backoff))"
    sleep 60
done

# Final snapshot.
final_429=$(count_429_at "${WS_LOG}")
final_backoff=$(count_backoff_at "${V5_LOG}")
delta_429=$((final_429 - baseline_429))
delta_backoff=$((final_backoff - baseline_backoff))

echo ""
echo "=== Final ==="
echo "429 events observed:      ${delta_429}"
echo "backoff traces observed:  ${delta_backoff}"

# Verdict per AC #25b pass/fail matrix.
if [ "${delta_429}" -eq 0 ]; then
    echo "RESULT: PASS (GREEN) — no 429s during smoke window"
    exit 0
elif [ "${delta_backoff}" -gt 0 ]; then
    echo "RESULT: PASS (YELLOW) — 429s observed but backoff retry works"
    exit 0
else
    echo "RESULT: FAIL (RED) — 429s observed but no backoff trace"
    echo "Investigate v5 WS client retry logic at v5/paper_engine.py:857"
    exit 1
fi
