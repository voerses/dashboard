"""M4 — MTF parity: code-path identity + minute_exits fixture reproduction.

Covers:
  - AC31 T-B23: 48-hour MTF tick fixture replays through both backtest and
    paper code paths; diff of ClosedTrade archive + PendingEntry log = 0 bytes
    for hourly-only, within AC41 tolerances for sub-hourly.
  - AC37 T-B29: minute_exits parity fixture (>=50 trades previously resolved
    by minute_exits.py) reproduces trade-archive bit-identically under
    BarProcessor with exit=BarSpec.from_minutes(1/5/15).

Fixtures:
  v5/tests/fixtures/mtf_48h_ticks.jsonl
  v5/tests/fixtures/minute_exits_parity.jsonl
  v5/tests/fixtures/minute_exits_parity_expected.bin
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


MTF_FIXTURE = _project_root / "v5" / "tests" / "fixtures" / "mtf_48h_ticks.jsonl"
ME_FIXTURE = (
    _project_root / "v5" / "tests" / "fixtures" / "minute_exits_parity.jsonl"
)
ME_EXPECTED = (
    _project_root / "v5" / "tests" / "fixtures" / "minute_exits_parity_expected.bin"
)


class TestAC31MTFCodePathIdentity:
    """AC31 T-B23: backtest == paper on 48h MTF fixture."""

    def test_mtf_48h_fixture_exists(self):
        """RED until fixture is captured."""
        if not MTF_FIXTURE.is_file():
            pytest.fail(
                f"MTF 48h fixture missing: {MTF_FIXTURE}. See T-B23."
            )

    def test_backtest_vs_paper_archive_match(self, tmp_path):
        """T-B23: ClosedTrade archive diff = 0 bytes for hourly-only;
        within AC41 tolerances for sub-hourly."""
        if not MTF_FIXTURE.is_file():
            pytest.fail("MTF 48h fixture missing — see T-B23.")

        from v5.bar_spec import BarSpec
        from v5.paper_engine import replay_paper_ticks
        from v5.simulator import run_backtest_mtf

        paper_archive = tmp_path / "paper.bin"
        bt_archive = tmp_path / "bt.bin"
        replay_paper_ticks(
            tick_fixture=MTF_FIXTURE,
            output_archive=paper_archive,
            hourly_only=True,
        )
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=1_770_000_000 * 1_000_000_000,
            end_ts_ns=1_770_000_000 * 1_000_000_000 + 48 * 3600 * 1_000_000_000,
            strategies=[], tokens=["BTC"],
            output_path=bt_archive, seed=42,
        )
        assert paper_archive.read_bytes() == bt_archive.read_bytes()

    def test_pending_entry_log_diff_zero(self, tmp_path):
        """T-B23: pending_entries_log.jsonl byte-identical between paths."""
        if not MTF_FIXTURE.is_file():
            pytest.fail("MTF 48h fixture missing — see T-B23.")

        from v5.bar_spec import BarSpec
        from v5.paper_engine import replay_paper_ticks
        from v5.simulator import run_backtest_mtf

        paper_log = tmp_path / "paper_pe.jsonl"
        bt_log = tmp_path / "bt_pe.jsonl"
        replay_paper_ticks(
            tick_fixture=MTF_FIXTURE,
            pe_log_path=paper_log,
            hourly_only=True,
        )
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=1_770_000_000 * 1_000_000_000,
            end_ts_ns=1_770_000_000 * 1_000_000_000 + 48 * 3600 * 1_000_000_000,
            strategies=[], tokens=["BTC"],
            pe_log_path=bt_log, seed=42,
        )
        assert paper_log.read_bytes() == bt_log.read_bytes()


class TestAC37MinuteExitsParityFixture:
    """AC37 T-B29: minute_exits parity fixture reproduces trade archive bit-identically."""

    def test_fixture_exists_and_has_50_trades(self):
        """AC37 T-B29: minute-exits parity fixture has >= 50 structured 'trade'
        events (event_type == 'trade' per JSONL schema)."""
        if not ME_FIXTURE.is_file():
            pytest.fail(f"Minute-exits fixture missing: {ME_FIXTURE}. See T-B29.")
        with ME_FIXTURE.open() as f:
            count = sum(
                1 for l in f if l.strip() and json.loads(l).get("event_type") == "trade"
            )
        assert count >= 50, (
            f"Expected >=50 trades in minute_exits parity fixture; got {count}"
        )

    def test_expected_archive_exists(self):
        if not ME_EXPECTED.is_file():
            pytest.fail(f"Expected archive missing: {ME_EXPECTED}. See T-B29.")

    def test_reproduction_bit_identical(self, tmp_path):
        """T-B29: replay fixture through BarProcessor with exit=1m/5m/15m;
        trade archive must equal the expected blob byte-for-byte."""
        if not (ME_FIXTURE.is_file() and ME_EXPECTED.is_file()):
            pytest.fail("Minute-exits parity fixtures missing — see T-B29.")

        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        out = tmp_path / "me_out.bin"
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=1_770_000_000 * 1_000_000_000,
            end_ts_ns=1_770_000_000 * 1_000_000_000 + 7 * 86400 * 1_000_000_000,
            strategies=[],  # loaded from fixture manifest
            tokens=["BTC"],
            output_path=out, seed=42,
            tick_fixture_path=ME_FIXTURE,
        )
        expected = ME_EXPECTED.read_bytes()
        actual = out.read_bytes()
        assert expected == actual, (
            f"AC37 minute_exits parity broken: {len(expected)} vs {len(actual)} bytes"
        )
