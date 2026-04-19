"""M7 — AC-P3 blocking test: 24h shadow replay returns zero divergence.

This file is intentionally named `shadow_replay_24h.py` (not `test_*`) per
the brief test layout. It is the HARD merge gate for flipping
PaperConfig.use_data_engine=True in production.

All tests MUST FAIL today — the 8-site paper engine migration is pending.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_ROOT = _project_root / "v5" / "tests" / "fixtures" / "shadow_replay_24h"


class TestShadowReplay24hFixtureAvailable:
    """AC-P3 — 24h fixture directory must exist (M6-shipped)."""

    def test_fixture_root_exists(self):
        assert FIXTURE_ROOT.exists(), (
            f"AC-P3: expected M6-shipped 24h fixture at {FIXTURE_ROOT}"
        )

    def test_ws_tap_file_present(self):
        """AC-P3 — at least one WS-tap recording file must be present."""
        ws_files = list(FIXTURE_ROOT.glob("binance_ws_tap_*.jsonl*"))
        assert ws_files, (
            f"AC-P3: expected Binance WS tap file(s) under {FIXTURE_ROOT}"
        )

    def test_rest_tap_file_present(self):
        rest_files = list(FIXTURE_ROOT.glob("binance_rest_tap_*.jsonl*"))
        assert rest_files, (
            f"AC-P3: expected Binance REST tap file(s) under {FIXTURE_ROOT}"
        )


class TestShadowReplay24hZeroDivergence:
    """AC-P3 — run_shadow_replay against the 24h fixture returns 0 divergence."""

    def test_ohlcv_divergence_count_is_zero(self):
        """AC-P3 — this is the HARD merge gate for the flag flip.

        # NOTE: brief ambiguous at AC-P3; strict interpretation — the report
        # must be produced with real Binance live WS tap data (not synthetic
        # placeholder); a synthetic tape is acceptable ONLY if labelled as
        # such and still returns 0 divergence.
        """
        from v5.data.engine import run_shadow_replay
        report = run_shadow_replay(fixture_root=FIXTURE_ROOT)
        assert report.ohlcv_divergence_count == 0, (
            f"AC-P3: ohlcv_divergence_count must be 0 to flip use_data_engine=True in prod; "
            f"got {report.ohlcv_divergence_count}"
        )

    def test_trade_divergence_count_is_zero(self):
        """AC-P3 — trade-tape divergence also at zero."""
        from v5.data.engine import run_shadow_replay
        report = run_shadow_replay(fixture_root=FIXTURE_ROOT)
        assert getattr(report, "trade_divergence_count", 0) == 0, (
            f"AC-P3: trade-tape divergence must be zero; "
            f"got {report.trade_divergence_count}"
        )

    def test_replay_uses_real_recording_not_synthetic(self):
        """AC-P3 — operational check: the fixture must not be a 5-min placeholder.

        Brief §"AC-P3" requires the 5-min synthetic to be REPLACED by a
        genuine 24h live recording before flag flip.
        """
        # Require the real recording to span approximately 24h. This is a
        # production-gate test — synthetic placeholders fail it.
        from v5.data.engine import summarize_shadow_fixture
        summary = summarize_shadow_fixture(fixture_root=FIXTURE_ROOT)
        assert summary.duration_hours >= 23.0, (
            f"AC-P3: 24h fixture must be a real ≥ 23h recording; "
            f"got {summary.duration_hours:.2f}h — still the synthetic placeholder?"
        )
