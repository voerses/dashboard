"""Acceptance tests for deprecation notices (AC16, AC17).

Tests verify:
  - AC16: backtest_portfolio.py prints deprecation warning when invoked
  - AC17: run_oos_monthly.py prints deprecation warning when invoked
  - AC16/AC17: Deprecation messages mention the correct replacement commands

Uses subprocess to actually run the scripts — tests the __main__ block behavior.
These tests MUST FAIL until deprecation notices are added (RED phase).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

import pytest

PYTHON = sys.executable


# ===================================================================
# AC16: backtest_portfolio.py deprecation
# ===================================================================

class TestBacktestPortfolioDeprecation:
    """AC16: backtest_portfolio.py prints deprecation warning."""

    def test_backtest_portfolio_shows_deprecation_warning(self):
        """AC16: Running backtest_portfolio.py outputs a deprecation warning.
        Uses subprocess to test the actual __main__ block behavior."""
        result = subprocess.run(
            [PYTHON, str(_project_root / "backtest_portfolio.py"), "--help"],
            capture_output=True, text=True, cwd=str(_project_root),
            timeout=30,
        )
        output = result.stdout + result.stderr
        assert "deprecat" in output.lower(), (
            "backtest_portfolio.py should print a deprecation warning. "
            f"Got stdout: {result.stdout[:200]}, stderr: {result.stderr[:200]}"
        )

    def test_deprecation_points_to_correct_replacement_backtest_portfolio(self):
        """AC16: Deprecation message mentions 'v4/portfolio_backtest.py'."""
        result = subprocess.run(
            [PYTHON, str(_project_root / "backtest_portfolio.py"), "--help"],
            capture_output=True, text=True, cwd=str(_project_root),
            timeout=30,
        )
        output = result.stdout + result.stderr
        assert "v4/portfolio_backtest" in output or "portfolio_backtest" in output, (
            "Deprecation warning should mention 'v4/portfolio_backtest.py' as replacement. "
            f"Got: {output[:300]}"
        )


# ===================================================================
# AC17: run_oos_monthly.py deprecation
# ===================================================================

class TestRunOOSMonthlyDeprecation:
    """AC17: run_oos_monthly.py prints deprecation warning."""

    def test_run_oos_monthly_shows_deprecation_warning(self):
        """AC17: Running run_oos_monthly.py outputs a deprecation warning.
        Uses subprocess to test the actual __main__ block behavior."""
        result = subprocess.run(
            [PYTHON, str(_project_root / "run_oos_monthly.py"), "--help"],
            capture_output=True, text=True, cwd=str(_project_root),
            timeout=30,
        )
        output = result.stdout + result.stderr
        assert "deprecat" in output.lower(), (
            "run_oos_monthly.py should print a deprecation warning. "
            f"Got stdout: {result.stdout[:200]}, stderr: {result.stderr[:200]}"
        )

    def test_deprecation_points_to_correct_replacement_oos_monthly(self):
        """AC17: Deprecation message mentions '--oos-monthly' flag."""
        result = subprocess.run(
            [PYTHON, str(_project_root / "run_oos_monthly.py"), "--help"],
            capture_output=True, text=True, cwd=str(_project_root),
            timeout=30,
        )
        output = result.stdout + result.stderr
        assert "--oos-monthly" in output, (
            "Deprecation warning should mention '--oos-monthly' as replacement. "
            f"Got: {output[:300]}"
        )


# ===================================================================
# AC18: Regression smoke test
# ===================================================================

class TestRegressionSmokeTest:
    """AC18: New code doesn't break existing imports."""

    def test_key_modules_importable(self):
        """AC18: Key modules can still be imported without errors."""
        from v4.portfolio_backtest import run_backtest, main, parse_args
        from v4.live_fetcher import LiveFetcher
        from v4.run_paper_multi import main as runner_main

        # Verify run_backtest signature hasn't lost required params
        import inspect
        sig = inspect.signature(run_backtest)
        assert "strategy_ids" in sig.parameters
        assert "market" in sig.parameters
        assert "config" in sig.parameters
