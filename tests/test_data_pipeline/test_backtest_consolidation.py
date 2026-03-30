"""Acceptance tests for backtest CLI consolidation (AC12-AC15).

Tests verify:
  - AC12: --refresh flag is parsed correctly
  - AC13: --oos-monthly flag is parsed correctly
  - AC12: --refresh calls ensure_data_fresh before backtest
  - AC15: --refresh failure continues with warning (backtest still runs)
  - AC13: run_backtest returns 5-tuple with eq_daily as 5th element
  - AC13: --oos-monthly runs per-month with different end_dates
  - AC14: Each month uses same initial capital (no carry-over)
  - AC14: --strategy works with --oos-monthly
  - AC14: --capital works with --oos-monthly
  - AC13: --oos-monthly prints per-month return/drawdown table

All tests use mocks -- no real backtest, exchange, or data required.
These tests MUST FAIL until backtest consolidation is implemented (RED phase).
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

from v4.portfolio_backtest import parse_args, run_backtest, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run_backtest_5tuple(capital=200_000):
    """Create a mock 5-tuple return value for run_backtest."""
    metrics = {"sharpe": 1.5, "total_return_pct": 25.0, "max_drawdown_pct": -8.0}
    extra_info = {"total_bars": 1000}
    trades = [{"token": "BTC", "pnl": 100.0}]
    precomputed_signals = {"s56": {"BTC": MagicMock()}}
    eq_daily = pd.Series(
        [capital, capital * 1.01, capital * 1.02, capital * 1.015],
        index=pd.date_range("2025-01-01", periods=4, freq="D"),
        name="equity",
    )
    return (metrics, extra_info, trades, precomputed_signals, eq_daily)


# ===================================================================
# AC12: --refresh flag
# ===================================================================

class TestParseArgsRefresh:
    """AC12: --refresh is parsed correctly."""

    def test_parse_args_refresh_flag(self):
        """AC12: --refresh flag is accepted by parse_args."""
        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s56",
            "--market", "perp",
            "--refresh",
        ]):
            args = parse_args()
        assert hasattr(args, "refresh"), "parse_args must recognize --refresh"
        assert args.refresh is True


# ===================================================================
# AC13: --oos-monthly flag
# ===================================================================

class TestParseArgsOOSMonthly:
    """AC13: --oos-monthly is parsed correctly."""

    def test_parse_args_oos_monthly_flag(self):
        """AC13: --oos-monthly flag is accepted by parse_args."""
        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s56",
            "--market", "perp",
            "--oos-monthly",
            "--months", "3",
        ]):
            args = parse_args()
        assert hasattr(args, "oos_monthly"), "parse_args must recognize --oos-monthly"
        assert args.oos_monthly is True


# ===================================================================
# AC12: --refresh calls ensure_data_fresh
# ===================================================================

class TestRefreshCallsEnsureDataFresh:
    """AC12: --refresh calls ensure_data_fresh before running the backtest."""

    @patch("v4.portfolio_backtest.ensure_data_fresh")
    @patch("v4.portfolio_backtest.run_backtest")
    def test_refresh_calls_ensure_data_fresh(self, mock_run_bt, mock_ensure):
        """AC12: main() with --refresh calls ensure_data_fresh."""
        mock_ensure.return_value = {
            "gaps_filled": 5, "bars_fetched": 100, "tokens_failed": 0,
            "tokens_promoted": 10, "timed_out": False, "tokens_skipped": 0,
        }
        mock_run_bt.return_value = _mock_run_backtest_5tuple()

        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s56",
            "--market", "perp",
            "--refresh",
        ]):
            try:
                main()
            except SystemExit:
                pass

        mock_ensure.assert_called_once()


# ===================================================================
# AC15: --refresh failure continues with warning
# ===================================================================

class TestRefreshFailureContinues:
    """AC15: If --refresh fails, backtest still runs with warning."""

    @patch("v4.portfolio_backtest.ensure_data_fresh")
    @patch("v4.portfolio_backtest.run_backtest")
    def test_refresh_failure_continues_with_warning(self, mock_run_bt, mock_ensure):
        """AC15: ensure_data_fresh raises but backtest still executes."""
        mock_ensure.side_effect = RuntimeError("Network error during refresh")
        mock_run_bt.return_value = _mock_run_backtest_5tuple()

        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s56",
            "--market", "perp",
            "--refresh",
        ]):
            try:
                main()
            except SystemExit:
                pass

        # Backtest should still be called despite refresh failure
        mock_run_bt.assert_called()


# ===================================================================
# AC13: run_backtest returns 5-tuple with eq_daily
# ===================================================================

class TestRunBacktest5Tuple:
    """AC13: run_backtest returns 5-tuple (metrics, extra, trades, signals, eq_daily)."""

    @patch("v4.portfolio_backtest.simulate_portfolio")
    @patch("v4.portfolio_backtest.precompute_strategy_signals")
    @patch("v4.portfolio_backtest.discover_tokens")
    @patch("v4.portfolio_backtest.compute_portfolio_metrics")
    def test_run_backtest_returns_5_tuple_with_eq_daily(
        self, mock_metrics, mock_discover, mock_signals, mock_sim,
    ):
        """AC13: run_backtest returns 5-tuple; 5th element is pd.Series."""
        # Setup mocks
        mock_discover.return_value = ["BTC", "ETH"]
        mock_signals.return_value = {"BTC": MagicMock(), "ETH": MagicMock()}

        mock_state = MagicMock()
        mock_state.position_manager.closed_trades = []
        mock_sim.return_value = mock_state

        eq_daily = pd.Series(
            [200000, 201000, 202000],
            index=pd.date_range("2025-01-01", periods=3, freq="D"),
        )
        mock_metrics.return_value = ({"sharpe": 1.0}, {"bars": 100}, eq_daily)

        from v4.config import PortfolioConfig
        config = PortfolioConfig(
            strategies=[],
            capital=200_000,
            max_portfolio_positions=40,
            concentration_limit=0.10,
            adv_cap_pct=0.05,
        )

        result = run_backtest(
            strategy_ids=["s56"],
            months=12,
            capital=200_000,
            config=config,
            market="perp",
        )

        assert isinstance(result, tuple), "run_backtest must return a tuple"
        assert len(result) == 5, f"Expected 5-tuple, got {len(result)}-tuple"
        assert isinstance(result[4], pd.Series), (
            f"5th element must be pd.Series, got {type(result[4])}"
        )


# ===================================================================
# AC13: --oos-monthly runs per-month
# ===================================================================

class TestOOSMonthlyPerMonth:
    """AC13: --oos-monthly runs month-by-month OOS."""

    @patch("v4.portfolio_backtest.run_backtest")
    @patch("v4.portfolio_backtest.infer_data_end_date")
    def test_oos_monthly_runs_per_month(self, mock_infer, mock_run_bt):
        """AC13: run_backtest called N times with different end_dates for N months."""
        mock_infer.return_value = pd.Timestamp("2025-03-31")

        def make_result(*args, **kwargs):
            return _mock_run_backtest_5tuple()

        mock_run_bt.side_effect = make_result

        # Import the OOS monthly function
        from v4.portfolio_backtest import run_oos_monthly

        from v4.config import PortfolioConfig
        config = PortfolioConfig(
            strategies=[],
            capital=200_000,
            max_portfolio_positions=40,
            concentration_limit=0.10,
            adv_cap_pct=0.05,
        )

        results = run_oos_monthly(
            strategy_ids=["s56"],
            oos_months=3,
            capital=200_000,
            config=config,
            market="perp",
        )

        # Should call run_backtest 3 times (one per month)
        assert mock_run_bt.call_count == 3, (
            f"Expected 3 calls for 3 months, got {mock_run_bt.call_count}"
        )

        # Each call should have a different end_date
        end_dates = set()
        for c in mock_run_bt.call_args_list:
            end_date = c.kwargs.get("end_date", None)
            if end_date is None and len(c.args) > 6:
                end_date = c.args[6]
            if end_date is not None:
                end_dates.add(str(end_date))
        assert len(end_dates) == 3, f"Expected 3 different end_dates, got {end_dates}"


# ===================================================================
# AC14: Independent capital per month
# ===================================================================

class TestOOSMonthlyIndependentCapital:
    """AC14: Each month uses same initial capital (no carry-over)."""

    @patch("v4.portfolio_backtest.run_backtest")
    @patch("v4.portfolio_backtest.infer_data_end_date")
    def test_oos_monthly_independent_capital(self, mock_infer, mock_run_bt):
        """AC14: Each month's call uses same initial capital."""
        mock_infer.return_value = pd.Timestamp("2025-03-31")
        mock_run_bt.side_effect = lambda *a, **kw: _mock_run_backtest_5tuple(
            capital=kw.get("capital", 200_000),
        )

        from v4.portfolio_backtest import run_oos_monthly
        from v4.config import PortfolioConfig
        config = PortfolioConfig(
            strategies=[],
            capital=500_000,
            max_portfolio_positions=40,
            concentration_limit=0.10,
            adv_cap_pct=0.05,
        )

        run_oos_monthly(
            strategy_ids=["s56"],
            oos_months=3,
            capital=500_000,
            config=config,
            market="perp",
        )

        # Verify each call uses the same capital (500k), no carry-over
        for c in mock_run_bt.call_args_list:
            cap = c.kwargs.get("capital", c.args[2] if len(c.args) > 2 else None)
            assert cap == 500_000, f"Each month should use capital=500000, got {cap}"


# ===================================================================
# AC14: --strategy works with --oos-monthly
# ===================================================================

class TestOOSMonthlyAcceptsFlags:
    """AC14: --strategy and --capital work with --oos-monthly."""

    @patch("v4.portfolio_backtest.run_oos_monthly")
    @patch("v4.portfolio_backtest.ensure_data_fresh", return_value={})
    def test_oos_monthly_accepts_strategy_flag(self, mock_ensure, mock_oos):
        """AC14: --strategy works with --oos-monthly (not hardcoded)."""
        mock_oos.return_value = []

        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s100",
            "--market", "perp",
            "--oos-monthly",
            "--months", "6",
        ]):
            try:
                main()
            except SystemExit:
                pass

        # run_oos_monthly should be called with strategy_ids=["s100"]
        assert mock_oos.called
        call_kwargs = mock_oos.call_args.kwargs if mock_oos.call_args.kwargs else {}
        call_args = mock_oos.call_args.args if mock_oos.call_args.args else ()
        # Strategy should be "s100", not hardcoded
        all_str_args = str(call_args) + str(call_kwargs)
        assert "s100" in all_str_args, "Strategy flag should be passed through to OOS monthly"

    @patch("v4.portfolio_backtest.run_oos_monthly")
    @patch("v4.portfolio_backtest.ensure_data_fresh", return_value={})
    def test_oos_monthly_accepts_capital_flag(self, mock_ensure, mock_oos):
        """AC14: --capital works with --oos-monthly."""
        mock_oos.return_value = []

        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s56",
            "--market", "perp",
            "--oos-monthly",
            "--months", "3",
            "--capital", "1000000",
        ]):
            try:
                main()
            except SystemExit:
                pass

        assert mock_oos.called
        call_kwargs = mock_oos.call_args.kwargs if mock_oos.call_args.kwargs else {}
        call_args = mock_oos.call_args.args if mock_oos.call_args.args else ()
        all_str_args = str(call_args) + str(call_kwargs)
        assert "1000000" in all_str_args, "Capital flag should be passed through to OOS monthly"

    @patch("v4.portfolio_backtest.run_oos_monthly")
    @patch("v4.portfolio_backtest.ensure_data_fresh", return_value={})
    def test_oos_monthly_accepts_market_flag(self, mock_ensure, mock_oos):
        """AC14: --market works with --oos-monthly."""
        mock_oos.return_value = []

        with patch.object(sys, "argv", [
            "portfolio_backtest",
            "--strategy", "s56",
            "--market", "spot",
            "--oos-monthly",
            "--months", "3",
        ]):
            try:
                main()
            except SystemExit:
                pass

        assert mock_oos.called
        call_kwargs = mock_oos.call_args.kwargs if mock_oos.call_args.kwargs else {}
        call_args = mock_oos.call_args.args if mock_oos.call_args.args else ()
        all_str_args = str(call_args) + str(call_kwargs)
        assert "spot" in all_str_args, "Market flag should be passed through to OOS monthly"


# ===================================================================
# AC13: Per-month table output
# ===================================================================

class TestOOSMonthlyOutput:
    """AC13: --oos-monthly prints per-month return/drawdown table."""

    @patch("v4.portfolio_backtest.run_backtest")
    @patch("v4.portfolio_backtest.infer_data_end_date")
    def test_oos_monthly_prints_per_month_table(self, mock_infer, mock_run_bt, capsys):
        """AC13: OOS monthly output includes per-month return and drawdown."""
        mock_infer.return_value = pd.Timestamp("2025-03-31")
        mock_run_bt.side_effect = lambda *a, **kw: _mock_run_backtest_5tuple()

        from v4.portfolio_backtest import run_oos_monthly
        from v4.config import PortfolioConfig
        config = PortfolioConfig(
            strategies=[],
            capital=200_000,
            max_portfolio_positions=40,
            concentration_limit=0.10,
            adv_cap_pct=0.05,
        )

        run_oos_monthly(
            strategy_ids=["s56"],
            oos_months=3,
            capital=200_000,
            config=config,
            market="perp",
        )

        captured = capsys.readouterr()
        output = captured.out + captured.err

        # Should contain per-month data (month labels like "2025-01", "2025-02", etc.)
        assert "2025" in output, "Output should include year-month labels"
        # Should include return or drawdown indicators
        has_return_info = any(word in output.lower() for word in ["return", "ret", "%", "dd", "drawdown"])
        assert has_return_info, "Output should include return/drawdown information"
