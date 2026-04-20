#!/usr/bin/env bash
# M10 G2 / AC #1 — Grep-zero invariant for v4 compat shim markers.
#
# After writing this file:
#   chmod +x /workspace/crypto_backtest/tools/grep_zero_shims.sh
#
# Fails (non-zero exit) if any of the four marker strings appear in
# v5/*.py outside v5/tests/**:
#   - "v4 compat"
#   - "backward compat"
#   - "# TODO: remove"
#   - "DEPRECATED"
#
# Allowlisted sentinel (per AC #1): the renamed v4-log loader banner at
# `v5/paper_state.py` line 1178. That line is filtered out post-match
# via a grep -v pattern.
#
# Uses `grep -rE` (not ripgrep) for portability — ripgrep is sometimes
# shell-aliased rather than on PATH, which silently breaks scripts.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# Walk v5/ non-test .py files, excluding tests and pycache.
# grep -E for extended regex; -rn for recursive + line numbers;
# --include limits to .py; --exclude-dir skips tests + __pycache__.
RAW_HITS="$(grep -rnE 'v4 compat|backward compat|# TODO: remove|DEPRECATED' v5/ \
    --include='*.py' \
    --exclude-dir='tests' \
    --exclude-dir='__pycache__' \
    2>/dev/null || true)"

if [ -z "${RAW_HITS}" ]; then
    exit 0
fi

# Allowlist: v5/paper_state.py line 1178 renamed banner.
FILTERED="$(printf '%s\n' "${RAW_HITS}" \
    | grep -v 'v5/paper_state.py:1178:' 2>/dev/null || true)"

if [ -z "${FILTERED}" ]; then
    exit 0
fi

echo "v4 compat shim markers still present in v5/ non-test source (AC #1):"
echo "${FILTERED}"
echo ""
echo "Remove shims (or add 1 line to the allowlist IF genuinely required)."
exit 1
