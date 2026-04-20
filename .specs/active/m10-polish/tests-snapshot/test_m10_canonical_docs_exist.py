"""M10 F2 — Canonical documentation files exist and are substantive.

Covers ACs #3-5:
  * ARCHITECTURE.md — Trade Identity Model + TickCadencePolicy + 5 M8→M9
    test-dispute spec changes tally.
  * MIGRATION.md — v4→v5 runbook including concrete SIGTERM shutdown via
    ``stop_all_services.sh`` and step 6.5 alert-endpoint verification.
  * ROLLBACK.md — rehearsed rollback procedure.
  * DASHBOARD_V5.md — ``/v5`` URL routing + state_v5.json schema + cutover
    drill.
  * V5_SIZING_SYSTEM.md — M8 clamp pipeline + M9 SignalArbitrationPolicy +
    CapitalAllocationPolicy doc.

MUST FAIL TODAY — none of these docs currently exist in ``knowledge/``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_CANONICAL_DOCS = (
    "ARCHITECTURE.md",
    "MIGRATION.md",
    "ROLLBACK.md",
    "DASHBOARD_V5.md",
    "V5_SIZING_SYSTEM.md",
)


@pytest.mark.parametrize("doc_name", _CANONICAL_DOCS)
def test_canonical_doc_exists(doc_name):
    """Each of 5 canonical docs present in ``knowledge/``."""
    doc_path = _project_root / "knowledge" / doc_name
    assert doc_path.exists(), (
        f"knowledge/{doc_name} must exist as an M10 canonical doc "
        f"(AC #3-5). Missing: {doc_path}"
    )


@pytest.mark.parametrize("doc_name", _CANONICAL_DOCS)
def test_canonical_doc_substantive(doc_name):
    """Reject stubs — each doc > 500 chars."""
    doc_path = _project_root / "knowledge" / doc_name
    text = doc_path.read_text()
    assert len(text) > 500, (
        f"knowledge/{doc_name} too short ({len(text)} chars); must be a "
        f"real canonical doc, not a stub (AC #3-5)."
    )


class TestArchitectureMdContent:
    """AC #3 — ARCHITECTURE.md covers Trade Identity Model + TickCadence +
    test-dispute tally."""

    @pytest.fixture
    def arch_text(self):
        return (_project_root / "knowledge" / "ARCHITECTURE.md").read_text()

    def test_mentions_parent_position_id(self, arch_text):
        assert "parent_position_id" in arch_text, (
            "ARCHITECTURE.md must document parent_position_id (FIX "
            "OrderID(37) analog) per AC #3."
        )

    def test_mentions_exec_seq(self, arch_text):
        assert "exec_seq" in arch_text, (
            "ARCHITECTURE.md must document exec_seq (FIX ExecID(17) "
            "analog) per AC #3."
        )

    def test_mentions_tick_cadence_policy(self, arch_text):
        assert "TickCadencePolicy" in arch_text, (
            "ARCHITECTURE.md must document TickCadencePolicy wiring (M9 "
            "deliverable not yet documented) per AC #3."
        )

    def test_mentions_test_dispute_tally(self, arch_text):
        assert "test-dispute" in arch_text, (
            "ARCHITECTURE.md must include 5 M8→M9 test-dispute spec "
            "changes tally from .specs/telemetry.jsonl per AC #3."
        )


class TestMigrationMdContent:
    """AC #4 — MIGRATION.md has concrete v4 shutdown procedure."""

    @pytest.fixture
    def mig_text(self):
        return (_project_root / "knowledge" / "MIGRATION.md").read_text()

    def test_mentions_sigterm(self, mig_text):
        assert "SIGTERM" in mig_text, (
            "MIGRATION.md must document concrete SIGTERM signal for v4 "
            "shutdown per AC #4."
        )

    def test_mentions_stop_all_services_script(self, mig_text):
        assert "stop_all_services.sh" in mig_text, (
            "MIGRATION.md must reference tools/stop_all_services.sh for "
            "v4 shutdown per AC #4."
        )

    def test_mentions_step_6_5(self, mig_text):
        assert "step 6.5" in mig_text, (
            "MIGRATION.md must include step 6.5 (alert-endpoint "
            "verification / WS rate-limit smoke) per AC #4 + AC #25b."
        )
