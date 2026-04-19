"""M6 — 24h shadow replay harness (T-D19 / AC-D12).

Covers:
  - AC-D12 T-D19: given a 24h recorded-WS fixture, harness feeds the tape
    into the pre-M6 (legacy PriceMonitor) path and the post-M6 DataEngine
    path in parallel and reports any divergence as a blocking failure.
    Zero tolerance on OHLCV. Non-zero tolerance would mask real bugs.

Phase 3 scope: a 1-hour PROXY fixture exercises the harness's plumbing.
The full 24h recorded-WS fixture is Wave-F infra work — not Phase 3 scope.

All tests MUST FAIL today — v5.data.engine and the shadow replay CLI don't
exist yet. Import error is a valid RED state.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


PROXY_FIXTURE_ROOT = _project_root / "v5" / "tests" / "fixtures" / "shadow_replay_1h_proxy"
FULL_24H_FIXTURE_ROOT = _project_root / "v5" / "tests" / "fixtures" / "shadow_replay_24h"


class TestShadowReplayHarnessShape:
    """T-D19 — the harness exposes a callable entrypoint."""

    def test_harness_importable(self):
        """Harness entrypoint must be importable."""
        from v5.data.engine import run_shadow_replay  # noqa: F401


class TestShadowReplayProxyOneHour:
    """T-D19 Phase 3 proxy — 1-hour replay exercises the pipeline."""

    def test_proxy_run_reports_zero_divergence(self):
        if not PROXY_FIXTURE_ROOT.exists():
            pytest.fail(
                f"Expected proxy fixture at {PROXY_FIXTURE_ROOT} (Wave-F work). "
                "RED today via fixture-missing."
            )
        from v5.data.engine import run_shadow_replay

        report = run_shadow_replay(
            fixture_root=PROXY_FIXTURE_ROOT,
            duration_hours=1,
        )
        assert report.ohlcv_divergence_count == 0, (
            f"proxy replay detected {report.ohlcv_divergence_count} OHLCV "
            f"divergences — AC-D12 zero tolerance"
        )


class TestShadowReplay24h:
    """T-D19 full — full 24h run (fixture is Wave-F scope)."""

    def test_full_24h_replay_reports_zero_divergence(self):
        if not FULL_24H_FIXTURE_ROOT.exists():
            pytest.fail(
                f"Expected 24h fixture at {FULL_24H_FIXTURE_ROOT} (Wave-F work). "
                "RED today via fixture-missing."
            )
        from v5.data.engine import run_shadow_replay

        report = run_shadow_replay(
            fixture_root=FULL_24H_FIXTURE_ROOT,
            duration_hours=24,
        )
        assert report.ohlcv_divergence_count == 0
        # Funding / mark / trade tolerances from the brief's runbook.
        assert report.funding_rate_bps_divergence <= 1.0
        assert report.mark_price_bps_divergence <= 1.0
        assert report.trade_count_per_second_divergence <= 1
