"""Acceptance tests for GitHub Pages layout (AC29-30).

Tests verify:
  - AC29: v1 dashboard moved to docs/v1/index.html
  - AC30: v2 dashboard written to docs/index.html (root)

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until GitHub Pages layout changes are made (RED phase).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import pytest


# ===================================================================
# Test: AC29 — v1 dashboard moved to docs/v1/index.html
# ===================================================================

class TestV1DashboardMoved:
    """AC29: v1 dashboard moved to docs/v1/index.html."""

    def test_v1_dashboard_at_v1_path(self):
        """The v1 dashboard HTML exists at docs/v1/index.html."""
        v1_path = _project_root / "docs" / "v1" / "index.html"
        assert v1_path.exists(), (
            f"Expected v1 dashboard at {v1_path}. "
            "AC29 requires moving docs/index.html to docs/v1/index.html."
        )

    def test_v1_dashboard_has_content(self):
        """docs/v1/index.html has non-empty content (not a placeholder)."""
        v1_path = _project_root / "docs" / "v1" / "index.html"
        if not v1_path.exists():
            pytest.skip("v1 dashboard not yet moved")

        content = v1_path.read_text()
        assert len(content) > 100, "v1 dashboard should have substantial HTML content"
        assert "<html" in content.lower(), "v1 dashboard should be valid HTML"


# ===================================================================
# Test: AC30 — v2 dashboard at docs/index.html (root)
# ===================================================================

class TestV2DashboardAtRoot:
    """AC30: v2 dashboard written to docs/index.html (root)."""

    def test_v2_dashboard_at_root_path(self):
        """The v2 paper trading dashboard exists at docs/index.html,
        AND the v1 dashboard has been moved to docs/v1/index.html.
        Both conditions must be true for AC29+AC30 to be satisfied."""
        v2_path = _project_root / "docs" / "index.html"
        v1_path = _project_root / "docs" / "v1" / "index.html"

        # AC29 prerequisite: v1 must be moved first
        assert v1_path.exists(), (
            f"Expected v1 dashboard at {v1_path}. "
            "AC29 requires moving the old dashboard to docs/v1/index.html "
            "before the v2 dashboard can take the root path."
        )

        assert v2_path.exists(), (
            f"Expected v2 dashboard at {v2_path}. "
            "AC30 requires the v2 dashboard to be at docs/index.html."
        )

        # Verify it is the v2 dashboard (not v1)
        content = v2_path.read_text()
        # v2 dashboard should contain indicators that distinguish it from v1
        # (e.g., "paper trading", "v2", fund allocation panel, etc.)
        is_v2 = (
            "paper" in content.lower()
            or "v2" in content.lower()
            or "fund allocation" in content.lower()
            or "mark_to_market" in content.lower()
            or "rebalance" in content.lower()
        )
        assert is_v2, (
            "docs/index.html should be the v2 paper trading dashboard, "
            "not the v1 backtest dashboard."
        )
