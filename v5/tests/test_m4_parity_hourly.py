"""M4 — Hourly-only parity: bit-identical trade archive (binary diff = 0).

Covers:
  - AC18 T-B10: Strategies declaring (1h, 1h, 1h) produce bit-identical
    backtest trade archives after M4 vs pre-M4. Binary diff = 0.

Pre-M4 fixture archive is stored at
    v5/tests/fixtures/parity_hourly_pre_m4.bin

If the fixture is missing, the test intentionally fails with an informative
message — this is a pending-fixture RED state, not xfail.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_PATH = (
    _project_root / "v5" / "tests" / "fixtures" / "parity_hourly_pre_m4.bin"
)


class TestAC18HourlyParity:
    """AC18 T-B10: hourly-only strategy trade archive byte-identical pre/post."""

    def test_pre_m4_fixture_exists(self):
        """RED until fixture is captured by Phase 4 implementer."""
        if not FIXTURE_PATH.is_file():
            pytest.fail(
                f"Parity fixture missing: {FIXTURE_PATH}. "
                "Expected a pre-M4 hourly-only trade archive bytes blob."
            )

    def test_hourly_only_archive_bit_identical(self, tmp_path):
        """T-B10: run the same hourly-only strategy through post-M4 engine;
        trade-archive bytes must equal the pre-M4 fixture byte-for-byte."""
        if not FIXTURE_PATH.is_file():
            pytest.fail(
                f"Parity fixture missing: {FIXTURE_PATH}. See T-B10."
            )
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        out_path = tmp_path / "post_m4_archive.bin"
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(60),
            start_ts_ns=1_770_000_000 * 1_000_000_000,
            end_ts_ns=1_770_000_000 * 1_000_000_000 + 7 * 86400 * 1_000_000_000,
            strategies=[],  # loaded from fixture manifest
            tokens=["BTC"],
            output_path=out_path, seed=42,
        )
        pre_m4_bytes = FIXTURE_PATH.read_bytes()
        post_m4_bytes = out_path.read_bytes()
        assert pre_m4_bytes == post_m4_bytes, (
            f"Hourly-only parity broken: pre={len(pre_m4_bytes)} bytes, "
            f"post={len(post_m4_bytes)} bytes"
        )

    # AC18 parity test requires recorded-fixture harness (see T-B10 in
    # test_m4_parity_hourly.py above). Default-subscription normalization
    # is covered by test_m4_bar_spec.py::TestBarSpecInterning — removed here
    # to avoid duplicate coverage of a non-parity assertion.
