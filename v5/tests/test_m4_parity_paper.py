"""M4 — Paper 24h replay parity: hourly-only -> bit-identical position/trade/alert state.

Covers:
  - AC19 T-B11: 24-hour paper tick fixture replays for hourly-only strategies
    -> bit-identical position/trade/alert state. Sub-hourly subscriptions
    validated via AC41 shadow replay harness.

Fixture: v5/tests/fixtures/paper_24h_ticks.jsonl
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


PAPER_FIXTURE = (
    _project_root / "v5" / "tests" / "fixtures" / "paper_24h_ticks.jsonl"
)
PRE_M4_PAPER_STATE = (
    _project_root / "v5" / "tests" / "fixtures" / "paper_24h_pre_m4_state.json"
)


class TestAC19PaperParity:
    """AC19 T-B11: paper 24h replay bit-identical for hourly-only strategies."""

    def test_paper_24h_fixture_exists(self):
        """RED until fixture is captured."""
        if not PAPER_FIXTURE.is_file():
            pytest.fail(
                f"Paper 24h tick fixture missing: {PAPER_FIXTURE}. See T-B11."
            )

    def test_pre_m4_paper_state_exists(self):
        """RED until pre-M4 paper_state snapshot is captured."""
        if not PRE_M4_PAPER_STATE.is_file():
            pytest.fail(
                f"Pre-M4 paper state snapshot missing: {PRE_M4_PAPER_STATE}. "
                "Expected for AC19 parity gate."
            )

    def test_paper_replay_matches_pre_m4_state(self, tmp_path):
        """T-B11: replay paper fixture through post-M4 engine; position/trade/alert
        state must equal pre-M4 snapshot byte-for-byte."""
        if not (PAPER_FIXTURE.is_file() and PRE_M4_PAPER_STATE.is_file()):
            pytest.fail("Paper parity fixtures missing — see T-B11.")

        from v5.paper_engine import replay_paper_ticks

        out_state = tmp_path / "post_m4_state.json"
        replay_paper_ticks(
            tick_fixture=PAPER_FIXTURE,
            output_state=out_state,
            hourly_only=True,
        )
        pre = PRE_M4_PAPER_STATE.read_bytes()
        post = out_state.read_bytes()
        assert pre == post, (
            "Paper 24h replay diverged — hourly-only parity broken"
        )

    def test_paper_and_backtest_trade_archives_equal(self, tmp_path):
        """AC19 companion: for hourly-only strategies, paper replay + backtest
        produce identical trade archives."""
        if not PAPER_FIXTURE.is_file():
            pytest.fail("Paper 24h fixture missing — see T-B11.")

        from v5.bar_spec import BarSpec
        from v5.paper_engine import replay_paper_ticks
        from v5.simulator import run_backtest_mtf

        paper_out = tmp_path / "paper_archive.bin"
        bt_out = tmp_path / "bt_archive.bin"

        replay_paper_ticks(
            tick_fixture=PAPER_FIXTURE,
            output_archive=paper_out,
            hourly_only=True,
        )
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(60),
            start_ts_ns=1_770_000_000 * 1_000_000_000,
            end_ts_ns=1_770_000_000 * 1_000_000_000 + 86400 * 1_000_000_000,
            strategies=[], tokens=["BTC"],
            output_path=bt_out, seed=42,
        )
        assert paper_out.read_bytes() == bt_out.read_bytes()
