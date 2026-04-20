#!/usr/bin/env bash
# M10 E7 / AC #25b — operator-run parallel-ops WS rate-limit smoke.
#
# Rationale (brief §AC25 / quant expert review 2026-04-20):
# Binance WS rate limits are wall-clock phenomena enforced at the exchange
# edge. TestClock-accelerated pytest would only exercise our own mock, not
# the exchange's rate-limit policy. This script is the live-half that
# complements ``v5/tests/test_m10_ws_backoff_retry.py`` (AC #25a).
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
# Pass/fail asserts (Phase 4 implements):
#   * count(429) == 0 over the full duration, OR
#   * backoff path exercised AND the [30, 60, 120] sleep trace observed.
#
# Install: chmod +x tools/ws_ratelimit_parallel_smoke.sh
#
# Documented in knowledge/MIGRATION.md step 6.5 (connection verification).

set -euo pipefail

DURATION_MIN="${1:-30}"
WS_LOG="${WS_ERRORS_LOG:-/srv/data/ws_errors.jsonl}"

echo "[m10-e7-stub] would monitor ${WS_LOG} for ${DURATION_MIN} min"
echo "[m10-e7-stub] would assert: count(429) == 0 OR backoff [30,60,120] observed"
echo "[m10-e7-stub] Phase 4 implements the live monitor loop + assertions."

# Deliberately fail until Phase 4 fills this in. RED invariant per
# Phase-3 acceptance-test contract.
exit 1
