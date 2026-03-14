"""Acceptance tests for Task 11: Reconciliation Mode.

Tests verify:
  - Reconciliation over a known date range produces comparison report
  - Divergence detection: when paper and backtest disagree on an entry
  - Divergence detection: when paper and backtest disagree on exit timing
  - Divergence detection: P&L discrepancy between paper and backtest (H7)
  - Clean report when paper matches backtest exactly

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until reconciliation mode is implemented (RED phase).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_engine import PaperPortfolioEngine, ReconciliationReport
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import ClosedTrade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config() -> PaperConfig:
    return PaperConfig(
        strategies=[
            StrategySpec(strategy_id="s56", weight=1.0, market="perp", max_positions=10),
        ],
        capital=200_000.0,
        mode="pool",
        max_portfolio_positions=40,
        exchange="binance",
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
    )


def _make_trade(
    token: str = "BTC",
    entry_bar: int = 10,
    exit_bar: int = 20,
    pnl: float = 500.0,
    exit_reason: str = "target",
) -> ClosedTrade:
    return ClosedTrade(
        position_id=f"{token}:s56:{entry_bar}:primary",
        token=token,
        strategy_id="s56",
        leg="primary",
        entry_bar=entry_bar,
        exit_bar=exit_bar,
        entry_price=100.0,
        exit_price=105.0,
        direction=1,
        margin_usd=10_000.0,
        pnl=pnl,
        funding_cost=0.0,
        entry_fee=5.0,
        exit_fee=5.0,
        hold_bars=exit_bar - entry_bar,
        exit_reason=exit_reason,
        is_perp=True,
    )


# ===================================================================
# Test: Reconciliation produces comparison report
# ===================================================================

class TestReconciliationReport:
    """Reconciliation over a date range produces a structured comparison report (AC20)."""

    def test_reconciliation_returns_report(self):
        """reconcile() returns a ReconciliationReport object."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        # Provide matching paper and backtest trades
        paper_trades = [_make_trade(token="BTC", entry_bar=10, exit_bar=20, pnl=500.0)]
        backtest_trades = [_make_trade(token="BTC", entry_bar=10, exit_bar=20, pnl=500.0)]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert isinstance(report, ReconciliationReport)
        assert hasattr(report, "divergences")
        assert hasattr(report, "summary")

    def test_report_has_date_range(self):
        """Report includes the date range that was reconciled."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        report = engine.reconcile(
            paper_trades=[],
            backtest_trades=[],
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert report.start_date == "2026-02-01"
        assert report.end_date == "2026-03-01"


# ===================================================================
# Test: Divergence detection — entry disagreement
# ===================================================================

class TestEntryDivergence:
    """Detect when paper and backtest disagree on an entry."""

    def test_paper_has_entry_backtest_does_not(self):
        """Paper took a trade that backtest didn't => entry divergence."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        paper_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20),
            _make_trade(token="ETH", entry_bar=15, exit_bar=25),  # extra in paper
        ]
        backtest_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20),
        ]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) >= 1
        entry_divs = [d for d in report.divergences if d["type"] == "entry_mismatch"
                       or d["field"] == "entry"]
        assert len(entry_divs) >= 1
        assert any(d["token"] == "ETH" for d in entry_divs)

    def test_backtest_has_entry_paper_does_not(self):
        """Backtest took a trade that paper didn't => entry divergence."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        paper_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20),
        ]
        backtest_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20),
            _make_trade(token="SOL", entry_bar=12, exit_bar=22),  # extra in backtest
        ]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) >= 1
        assert any(d["token"] == "SOL" for d in report.divergences)


# ===================================================================
# Test: Divergence detection — exit timing disagreement
# ===================================================================

class TestExitDivergence:
    """Detect when paper and backtest disagree on exit timing."""

    def test_different_exit_bar(self):
        """Same token entered at same bar but exited at different bars."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        paper_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20),
        ]
        backtest_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=25),  # exits later
        ]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) >= 1
        exit_divs = [d for d in report.divergences
                     if d.get("type") == "exit_mismatch" or d.get("field") == "exit_bar"]
        assert len(exit_divs) >= 1
        assert exit_divs[0]["token"] == "BTC"
        assert exit_divs[0].get("paper_value") != exit_divs[0].get("backtest_value")

    def test_different_exit_reason(self):
        """Same entry/exit bar but different exit_reason (e.g., stop vs liquidation)."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        paper_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20, exit_reason="stop"),
        ]
        backtest_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20, exit_reason="liquidation"),
        ]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) >= 1
        reason_divs = [d for d in report.divergences
                       if d.get("field") == "exit_reason"]
        assert len(reason_divs) >= 1


# ===================================================================
# Test: Clean report when paper matches backtest exactly
# ===================================================================

class TestCleanReconciliation:
    """No divergences when paper matches backtest exactly."""

    def test_matching_trades_no_divergences(self):
        """Identical trade lists produce zero divergences."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20, pnl=500.0),
            _make_trade(token="ETH", entry_bar=15, exit_bar=25, pnl=200.0),
        ]

        report = engine.reconcile(
            paper_trades=trades,
            backtest_trades=trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) == 0
        assert report.summary["total_divergences"] == 0

    def test_empty_trade_lists_no_divergences(self):
        """Two empty trade lists produce zero divergences."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        report = engine.reconcile(
            paper_trades=[],
            backtest_trades=[],
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) == 0


# ===================================================================
# Test: P&L divergence detection (H7)
# ===================================================================

class TestPnLDivergence:
    """Detect when paper and backtest agree on entry/exit but disagree on P&L (AC20)."""

    def test_pnl_discrepancy_detected(self):
        """Same token, same entry_bar, same exit_bar, same exit_reason,
        but paper_pnl=500 vs backtest_pnl=450. Report should detect the
        P&L discrepancy and include it in divergences with the difference."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        paper_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20,
                        pnl=500.0, exit_reason="target"),
        ]
        backtest_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20,
                        pnl=450.0, exit_reason="target"),
        ]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        assert len(report.divergences) >= 1

        # Find the P&L divergence
        pnl_divs = [d for d in report.divergences
                     if d.get("field") == "pnl" or d.get("type") == "pnl_mismatch"]
        assert len(pnl_divs) >= 1

        pnl_div = pnl_divs[0]
        assert pnl_div["token"] == "BTC"
        assert pnl_div["paper_value"] == pytest.approx(500.0)
        assert pnl_div["backtest_value"] == pytest.approx(450.0)
        # Difference amount should be included
        assert abs(pnl_div.get("difference", pnl_div.get("diff", 0))) == pytest.approx(50.0)

    def test_matching_pnl_no_divergence(self):
        """Same P&L values should not produce a divergence."""
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        engine.config = _make_test_config()

        paper_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20,
                        pnl=500.0, exit_reason="target"),
        ]
        backtest_trades = [
            _make_trade(token="BTC", entry_bar=10, exit_bar=20,
                        pnl=500.0, exit_reason="target"),
        ]

        report = engine.reconcile(
            paper_trades=paper_trades,
            backtest_trades=backtest_trades,
            start_date="2026-02-01",
            end_date="2026-03-01",
        )

        pnl_divs = [d for d in report.divergences
                     if d.get("field") == "pnl" or d.get("type") == "pnl_mismatch"]
        assert len(pnl_divs) == 0
