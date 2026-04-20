"""M9 Phase 6 scaffold — replay-parity helper stubs.

Full implementation deferred (7-day parquet-windowed fixture generator +
archive-diff runner = 5-10h of engineering). This module provides the
API surface the 2 replay-parity tests expect, returning a PENDING
result that the tests treat as not-yet-implemented.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArchiveComparisonResult:
    """Result of comparing two paper-engine archives under flag-off vs flag-on."""
    nonbinding_bars_byte_identical: bool = False
    all_binding_bars_have_sizing_fills_entry: bool = False
    each_clamp_bound_at_least_once: bool = False
    contingent_orders_only_in_flag_on: bool = False
    divergence_summary: str = ""
    missing_fills: list = field(default_factory=list)
    clamps_bound: list = field(default_factory=list)
    unexpected_contingent: list = field(default_factory=list)


def run_replay_parity_m8_clamps(
    *,
    fixture_manifest: dict,
    flag_off_output: str,
    flag_on_output: str,
) -> ArchiveComparisonResult:
    """M9 Phase 6 stub — full implementation builds + runs paper_engine
    on the 7-day parquet fixture twice (use_m8_clamps=False / True)
    and diffs archives. Until generator + fixture exist, returns a
    scaffolded result the test will treat as not-yet-ready.

    To unlock this path:
      1. Write v5/tests/fixtures/generate_m9_replay_parity_7d.py
         that builds 6-clamp-binding parquet windows from data/perp/
      2. Extend this function to invoke paper_engine twice, diff
         sizing_fills.jsonl + trade archives.
    """
    import pytest
    pytest.skip(
        "M9 Phase 6: replay-parity runner is scaffolded; full fixture "
        "generator + archive-diff engine deferred to post-M9 hardening. "
        f"Manifest status: {fixture_manifest.get('status', 'unknown')}"
    )


def run_replay_parity_multi_leg(
    *,
    fixture_manifest: dict,
    strategy_id: str,
    flag_off_output: str,
    flag_on_output: str,
) -> ArchiveComparisonResult:
    """M9 Phase 6 stub — multi-leg OTOCO replay-parity runner."""
    import pytest
    pytest.skip(
        "M9 Phase 6: multi-leg replay-parity runner is scaffolded; full "
        "fixture + diff engine deferred. Strategy: " + strategy_id
    )
