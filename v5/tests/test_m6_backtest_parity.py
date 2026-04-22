"""M6 — Backtest parity with flag=ON against M5 reference run.

Covers:
  - AC-D12 / Parity Gate (backtest slice): with use_data_engine_flag=True, a
    short backtest run via ParquetReplayClient → DataEngine → strategy
    produces an M5-reference-compatible trade archive. This gate protects
    against regressions in the routing / cache / aggregation wiring.

Fixture expectation: a small M5 reference backtest output lives under
`v5/tests/fixtures/m6_backtest_parity/` (Wave-F fixture work — not Phase 3
scope). Tests fail RED today via import error on v5.data.engine.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_ROOT = _project_root / "v5" / "tests" / "fixtures" / "m6_backtest_parity"


def _require_fixture():
    if not FIXTURE_ROOT.exists():
        pytest.fail(
            f"Expected fixture at {FIXTURE_ROOT} (Wave-F fixture work). "
            "Until the fixture lands, this test is RED via fixture-missing."
        )


class TestBacktestParityFlagOn:
    """Parity gate — flag=ON backtest matches M5 reference archive."""

    def test_short_backtest_produces_reference_trade_archive(self, tmp_path):
        """End-to-end: ParquetReplayClient → DataEngine → strategy → orders → archive."""
        _require_fixture()
        from v5.bar_spec import BarSpec
        from v5.data.engine import DataEngine
        from v5.data.clients.parquet_replay import ParquetReplayClient
        from v5.data.registry import DataClientRegistry
        from v5.data.streams import BarData, Venue

        reg = DataClientRegistry()
        replay = ParquetReplayClient(fixture_root=FIXTURE_ROOT / "parquet")
        reg.register(Venue.BINANCE, BarData, lambda _c: replay)

        engine = DataEngine(registry=reg)
        engine.use_data_engine_flag = True

        out_path = tmp_path / "trade_archive.bin"
        engine.run_backtest(
            start_ns=1_770_000_000 * 1_000_000_000,
            end_ns=1_770_000_000 * 1_000_000_000 + 3600 * 1_000_000_000,
            base_resolution=BarSpec.from_minutes(1),
            strategies=[], tokens=["BTC"], output_path=out_path, seed=42,
        )

        got = hashlib.sha256(out_path.read_bytes()).hexdigest()
        ref_digest_path = FIXTURE_ROOT / "m5_reference_trade_archive.sha256"
        expected = ref_digest_path.read_text().strip().split()[0]
        assert got == expected, (
            f"flag=ON backtest trade archive diverged vs M5 reference: "
            f"got={got} expected={expected}"
        )
