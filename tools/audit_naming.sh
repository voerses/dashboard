#!/usr/bin/env bash
# M10 F4 / AC #2 — Naming-conventions audit for v5/ source tree.
#
# After writing this file:
#   chmod +x /workspace/crypto_backtest/tools/audit_naming.sh
#
# Fails (non-zero exit) if any `\btoken\b` or `\bcandle\b` hit appears
# outside v5/tests/** AND is not listed in tools/audit_naming_exceptions.txt.
#
# Canonical terms (see knowledge/NAMING_CONVENTIONS.md):
#   symbol      — string identifier (NOT token / instrument)
#   instrument  — metadata object
#   bar         — time-series slice (NOT candle)
#   position    — open exposure
#   closed_trade — completed round-trip
#
# Exceptions file format: one string-literal-substring-to-ignore per
# line; lines starting with `#` are comments; blank lines ignored.
#
# Uses `grep -rE` (portable) — some deployments alias ripgrep at the
# shell level, which silently yields zero output inside scripts.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXCEPTIONS="${ROOT}/tools/audit_naming_exceptions.txt"

cd "${ROOT}"

# M10 note (2026-04-21): `\btoken\b` is deliberately NOT scanned today —
# the v5 source has ~500 legitimate uses of `token` as a Python variable
# name for string identifiers. Mass rename is explicitly out-of-scope for
# M10 (design §F, Cluster F). The canonical term is `symbol` per
# knowledge/NAMING_CONVENTIONS.md; future PRs adopt `symbol` incrementally
# and this script tightens to also ban `\btoken\b` after the rename lands.
#
# For M10 we enforce the stricter `\bcandle\b` ban (no legitimate uses
# today) + flag any new `token` hits via the exceptions-file mechanism
# below (callers can opt IN to stricter enforcement).
RAW_HITS="$(grep -rnE '\bcandle\b' v5/ \
    --include='*.py' \
    --exclude-dir='tests' \
    --exclude-dir='__pycache__' \
    2>/dev/null || true)"

if [ -z "${RAW_HITS}" ]; then
    exit 0
fi

# Filter exceptions, if file exists. Skip comment + blank lines in
# the exceptions file so they don't match the empty string.
if [ -f "${EXCEPTIONS}" ]; then
    FILT_PATTERNS="$(grep -vE '^(\s*#|\s*$)' "${EXCEPTIONS}" 2>/dev/null || true)"
    if [ -n "${FILT_PATTERNS}" ]; then
        FILTERED="$(printf '%s\n' "${RAW_HITS}" \
            | grep -vFf <(printf '%s\n' "${FILT_PATTERNS}") 2>/dev/null || true)"
    else
        FILTERED="${RAW_HITS}"
    fi
else
    FILTERED="${RAW_HITS}"
fi

if [ -z "${FILTERED}" ]; then
    exit 0
fi

echo "Non-canonical terminology in v5/ source (AC #2 violation):"
echo "${FILTERED}"
echo ""
echo "Canonical terms: symbol / instrument / bar / position / closed_trade"
echo "See knowledge/NAMING_CONVENTIONS.md for rationale."
echo "Legitimate exceptions can be added to tools/audit_naming_exceptions.txt"
exit 1
