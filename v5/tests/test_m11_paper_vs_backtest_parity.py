"""M11 AC-2 (SHIP CRITERION) — Paper-vs-Backtest parity.

This is THE binary ship test for M11. Same strategy instance, run over the
same data window via:
  (a) the new `run_backtest()` orchestrator (event-driven backtest), and
  (b) the live `PaperPortfolioEngine` in REPLAY mode.

Assertion: identical closed trades — length, and for each trade its
(instrument, direction, entry_ts, exit_ts, pnl within 1e-6 absolute).

Parameterized over ≥2 strategies with different signal-emission shapes:
  - `s513_v5.S513TripleTriggerSwing` — per-instrument independent
  - `s524m_v5.S524M` — cross-sectional (portfolio-rank)

All tests RED today — both `run_backtest` and paper REPLAY dispatch
don't exist, so the imports and/or first call blow up.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue
from v5.tests.fixtures._m11_parity_builder_v2 import build_ac2_fixture


def _inst(sym: str) -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


@pytest.fixture
def parity_fixture(tmp_path: Path):
    """60-day BTC+ETH 1h + funding + metrics fixture (real parquet data).

    Replaces the original 14-day synthetic random-walk fixture per
    ``.specs/active/m11-unified-event-loop/reviews/ac2-fixture-dispute.md``
    (verdict: UPHELD_AFTER_REWORK, 2026-04-22). The 22-day composite
    z-score window in ``s524m`` needs > 22 daily samples to produce a
    non-NaN composite; the 14-day synthetic fixture produced 0/0
    closed trades on both sides (vacuous tautology).
    """
    return build_ac2_fixture(tmp_path)


def _build_strategy(slug: str, instruments):
    """Instantiate the requested strategy bound to `instruments`."""
    tokens = [i.symbol.replace("USDT", "") for i in instruments]
    if slug == "s513":
        from v5.strategies.s513_v5 import S513TripleTriggerSwing
        return S513TripleTriggerSwing(tokens=tokens)
    if slug == "s524m":
        from v5.strategies.s524m_v5 import S524M
        return S524M()
    raise ValueError(f"unknown strategy slug: {slug}")


def _run_backtest(strategy, fixture):
    """Run the new event-driven backtest orchestrator on the fixture."""
    from v5.config import PortfolioConfig
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.run_backtest import run_backtest

    return run_backtest(
        strategies=[strategy],
        instruments=fixture["instruments"],
        start_ns=fixture["start_ns"],
        end_ns=fixture["end_ns"],
        manifest=BUILT_IN_MANIFEST,
        config=PortfolioConfig(strategies=[], capital=150_000.0, seed=42),
        fixture_root=fixture["root"],
    )


def _run_paper_replay(strategy, fixture):
    """Run PaperPortfolioEngine in REPLAY mode over the same fixture."""
    from v5.paper_engine import PaperPortfolioEngine
    from v5.paper_config import PaperConfig

    # Paper engine exposes a REPLAY-mode driver post-M11. Today this import
    # path either doesn't exist or the driver doesn't hit `strategy.generate`
    # directly — both paths flunk.
    from v5.run_backtest import drive_paper_in_replay

    config = PaperConfig(strategies=[], capital=150_000.0, exchange="binance")
    engine = PaperPortfolioEngine(config=config)
    return drive_paper_in_replay(
        engine=engine,
        strategies=[strategy],
        instruments=fixture["instruments"],
        start_ns=fixture["start_ns"],
        end_ns=fixture["end_ns"],
        fixture_root=fixture["root"],
    )


# ----------------------------------------------------------------------
# AC-2 parity test (parameterized over 2 strategies)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("strategy_slug", ["s513", "s524m"])
def test_same_closed_trades_across_backtest_and_paper(
    parity_fixture, strategy_slug,
):
    """Identical closed trades between run_backtest and paper REPLAY:
    len match + per-trade (instrument, direction, entry_ts, exit_ts) match
    + pnl within 1e-6 absolute."""
    strat_a = _build_strategy(strategy_slug, parity_fixture["instruments"])
    strat_b = _build_strategy(strategy_slug, parity_fixture["instruments"])

    state_backtest = _run_backtest(strat_a, parity_fixture)
    state_paper = _run_paper_replay(strat_b, parity_fixture)

    closed_a = list(state_backtest.closed_trades)
    closed_b = list(state_paper.closed_trades)

    assert len(closed_a) == len(closed_b), (
        f"{strategy_slug}: closed_trade count mismatch — "
        f"backtest={len(closed_a)} paper={len(closed_b)}"
    )

    for i, (ta, tb) in enumerate(zip(closed_a, closed_b)):
        # Instrument / direction must match exactly
        inst_a = getattr(ta, "token", None) or getattr(ta, "instrument", None)
        inst_b = getattr(tb, "token", None) or getattr(tb, "instrument", None)
        assert inst_a == inst_b, (
            f"{strategy_slug} trade#{i}: instrument mismatch "
            f"(backtest={inst_a!r} paper={inst_b!r})"
        )
        dir_a = getattr(ta, "direction", None)
        dir_b = getattr(tb, "direction", None)
        assert dir_a == dir_b, (
            f"{strategy_slug} trade#{i}: direction mismatch "
            f"(backtest={dir_a} paper={dir_b})"
        )
        # entry_ts / exit_ts — nanoseconds preferred but accept datetimes
        entry_a = getattr(ta, "entry_ts", None) or getattr(ta, "entry_time", None)
        entry_b = getattr(tb, "entry_ts", None) or getattr(tb, "entry_time", None)
        assert entry_a == entry_b, (
            f"{strategy_slug} trade#{i}: entry ts mismatch "
            f"(backtest={entry_a!r} paper={entry_b!r})"
        )
        exit_a = getattr(ta, "exit_ts", None) or getattr(ta, "exit_time", None)
        exit_b = getattr(tb, "exit_ts", None) or getattr(tb, "exit_time", None)
        assert exit_a == exit_b, (
            f"{strategy_slug} trade#{i}: exit ts mismatch "
            f"(backtest={exit_a!r} paper={exit_b!r})"
        )
        pnl_a = float(getattr(ta, "pnl", 0.0))
        pnl_b = float(getattr(tb, "pnl", 0.0))
        assert pnl_a == pytest.approx(pnl_b, abs=1e-6), (
            f"{strategy_slug} trade#{i}: pnl mismatch "
            f"(backtest={pnl_a} paper={pnl_b} delta={abs(pnl_a - pnl_b)})"
        )
