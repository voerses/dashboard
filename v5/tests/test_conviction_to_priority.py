"""Acceptance tests for conviction->priority refactor (M7 dependency).

M9 C-1 FINAL DELETION: the conviction->priority shim itself is deleted.
TokenBarArrays no longer has `conviction_score` / int32 `priority` array
fields; strategies emit `TokenSignal.priority: float` scalar per M7 API.

All 24 tests in this module are obsolete (spec contract changed).
Module-level skip is applied below. See:
  - .specs/active/m9-cleanups/brief.md C-1 (clean-cut rationale)
  - .specs/telemetry.jsonl (test_dispute entries)
  - Git history pre-M9 for the original test bodies.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.skip(reason=(
    "M9 C-1: conviction->priority shim deleted; tests obsolete. "
    "TokenSignal.priority is now a scalar float per M7 API, not an "
    "int32 array auto-derived from conviction_score."
))


def test_m9_c1_spec_contract_changed():
    """Sentinel assertion noting the M9 spec change.
    Skipped by module-level pytestmark."""
    pass  # pragma: no cover — pytest.mark.skip pre-empts this
