"""M10 AC #18 — funding cross-path parity (closes M8 deferral).

Same 24h fixture is driven through BOTH code paths:

    * ``simulator.run_backtest(...)`` → emits
      ``v5/logs/funding_accruals.jsonl``
    * ``paper_engine.PaperEngine(clock=TestClock(...)).run(...)`` →
      emits its own ``v5/logs/funding_accruals.jsonl``

A position opens at a non-window-aligned bar (representing
``03:17 UTC``) and runs through 3 funding snaps (00/08/16 UTC).

The acceptance criterion is that both funding-archive files are
BYTE-IDENTICAL after their JSON lines are sorted by ``ts_ns``. This
kills any lump-sum-vs-time-weighted asymmetry between the two paths,
which was the M8 deferral.

All tests MUST FAIL today — (a) ``simulator.run_backtest`` does not
yet exist as a direct fixture entrypoint, (b) ``paper_engine.PaperEngine``
does not expose a ``clock=TestClock(...)`` constructor, and (c) neither
path emits a ``funding_accruals.jsonl`` sink.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v5.testing import TestClock  # noqa: E402
from v5.tests.fixtures._replay_builder import (  # noqa: E402
    ReplayFixtureBuilder,
    ScenarioSpec,
)


N_BARS = 24
INITIAL_CAPITAL = 100_000.0
START_TS_UTC = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z in ns
OPEN_AT_BAR = 3  # 03:00 UTC — but the forced open happens intra-bar @ 03:17

# Snaps at 00/08/16 UTC (per AC #16 + AC #18 cross-path parity)
FUNDING_SNAPS = (
    (0, 0.0001),
    (8, -0.0002),
    (16, 0.00015),
)


def _build_fixture():
    return ReplayFixtureBuilder(
        tokens=["BTC"],
        n_bars=N_BARS,
        start_ts_utc=START_TS_UTC,
        seed=17,
        scenario=ScenarioSpec(
            random_walk_stddev=0.0,
            forced_trades=1,
            funding_snaps=FUNDING_SNAPS,
        ),
    )


def _run_backtest_path(log_dir: Path) -> Path:
    """Run simulator-backed backtest; return path to funding_accruals.jsonl."""
    from v5.simulator import run_backtest

    builder = _build_fixture()
    all_signals = builder.build()
    archive = log_dir / "backtest_funding_accruals.jsonl"
    run_backtest(
        all_signals=all_signals,
        initial_capital=INITIAL_CAPITAL,
        funding_archive=archive,
        open_at_second_of_day=3 * 3600 + 17 * 60,  # 03:17 UTC intra-bar open
    )
    return archive


def _run_paper_path(log_dir: Path) -> Path:
    """Run PaperEngine under TestClock over the same synthetic bars."""
    from v5.paper_engine import PaperEngine

    builder = _build_fixture()
    ctx = builder.ctx_stub()
    clock = TestClock(epoch_iso="2026-01-01T00:00:00Z", seed=17)
    archive = log_dir / "paper_funding_accruals.jsonl"
    engine = PaperEngine(clock=clock, funding_archive=archive)
    engine.run(ctx=ctx, n_bars=N_BARS, open_at_second_of_day=3 * 3600 + 17 * 60)
    return archive


class TestAC18FundingCrossPathParity:
    """AC #18 — backtest + paper funding archives byte-identical after sort."""

    def test_both_archives_emitted(self, tmp_path):
        bt_path = _run_backtest_path(tmp_path)
        pe_path = _run_paper_path(tmp_path)
        assert bt_path.exists(), f"Missing backtest archive at {bt_path}"
        assert pe_path.exists(), f"Missing paper archive at {pe_path}"

    def test_funding_archives_byte_identical_after_sort(self, tmp_path):
        bt_path = _run_backtest_path(tmp_path)
        pe_path = _run_paper_path(tmp_path)

        def _sorted_lines(p: Path) -> list[str]:
            lines = [l for l in p.read_text().splitlines() if l]
            parsed = [json.loads(l) for l in lines]
            parsed.sort(key=lambda d: int(d["ts_ns"]))
            return [json.dumps(d, sort_keys=True, separators=(",", ":")) for d in parsed]

        bt_lines = _sorted_lines(bt_path)
        pe_lines = _sorted_lines(pe_path)

        assert len(bt_lines) == 3, (
            f"Expected 3 funding snaps in backtest archive; got {len(bt_lines)}"
        )
        assert len(pe_lines) == len(bt_lines), (
            f"Archive line-count mismatch: backtest={len(bt_lines)}, "
            f"paper={len(pe_lines)}"
        )
        for i, (bt, pe) in enumerate(zip(bt_lines, pe_lines)):
            assert bt == pe, (
                f"Snap {i} diverged between paths:\n"
                f"  backtest: {bt}\n"
                f"  paper   : {pe}"
            )

    def test_0800_charge_matches_between_paths(self, tmp_path):
        """The 08:00 UTC debit on a 03:17-opened position is identical
        across backtest/paper — no lump-sum vs time-weighted asymmetry."""
        bt_path = _run_backtest_path(tmp_path)
        pe_path = _run_paper_path(tmp_path)

        def _snap_at_hour(p: Path, hour: int) -> dict:
            want_ns = START_TS_UTC + hour * 3600 * 1_000_000_000
            for line in p.read_text().splitlines():
                if not line:
                    continue
                entry = json.loads(line)
                if int(entry["ts_ns"]) == want_ns:
                    return entry
            pytest.fail(f"No funding snap at hour {hour} UTC in {p}")

        bt_snap = _snap_at_hour(bt_path, 8)
        pe_snap = _snap_at_hour(pe_path, 8)
        assert bt_snap == pe_snap, (
            f"08:00 funding snap diverges between paths:\n"
            f"  backtest: {bt_snap}\n"
            f"  paper   : {pe_snap}"
        )
