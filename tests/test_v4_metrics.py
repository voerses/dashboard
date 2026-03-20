"""Tests for v4/metrics.py — hourly MaxDD, training period, and capacity cap fixes."""
import numpy as np
import pandas as pd
import pytest

from v4.metrics import compute_metrics, PerformanceMetrics


class TestHourlyMaxDD:
    """Phase 1A: MaxDD computed on hourly equity, not daily resampled."""

    def _make_hourly_equity(self, values, start="2024-01-01"):
        """Helper: create hourly equity series."""
        idx = pd.date_range(start, periods=len(values), freq="1h")
        return pd.Series(values, index=idx)

    def _make_daily_equity(self, values, start="2024-01-01"):
        """Helper: create daily equity series."""
        idx = pd.date_range(start, periods=len(values), freq="1D")
        return pd.Series(values, index=idx)

    def test_max_dd_hourly_pct_field_exists(self):
        """PerformanceMetrics should have a max_dd_hourly_pct field."""
        m = PerformanceMetrics()
        assert hasattr(m, "max_dd_hourly_pct")
        assert m.max_dd_hourly_pct == 0.0

    def test_hourly_dd_deeper_than_daily(self):
        """When hourly equity has intraday dips, hourly MaxDD should be deeper than daily."""
        # Hourly: peak at 200K, dips to 160K intraday (20% DD), recovers to 190K by EOD
        # Daily sees: 200K, 190K (5% DD) — misses the 20% intraday drawdown
        hourly_vals = [200_000] * 12 + [160_000] + [190_000] * 11  # 24 hours = 1 day
        hourly_vals += [195_000] * 24  # day 2
        eq_hourly = self._make_hourly_equity(hourly_vals)
        eq_daily = eq_hourly.resample("1D").last().dropna()

        metrics = compute_metrics([], eq_daily, eq_hourly=eq_hourly, capital=200_000)

        # Hourly DD should be ~-20% (the intraday dip)
        assert metrics.max_dd_hourly_pct < -15.0
        # Regular (daily) DD should be much less
        assert metrics.max_drawdown_pct > metrics.max_dd_hourly_pct  # daily is less negative

    def test_calmar_uses_hourly_dd(self):
        """When eq_hourly is provided, Calmar should use hourly MaxDD (deeper)."""
        # Create equity that rises overall but has a deep hourly dip
        hourly_vals = list(np.linspace(200_000, 250_000, 24 * 30))  # 30 days growth
        # Insert a 25% dip at hour 200
        peak_at_200 = hourly_vals[200]
        hourly_vals[200] = peak_at_200 * 0.75  # 25% DD
        hourly_vals[201] = peak_at_200 * 0.90  # partial recovery
        eq_hourly = self._make_hourly_equity(hourly_vals)
        eq_daily = eq_hourly.resample("1D").last().dropna()

        metrics = compute_metrics([], eq_daily, eq_hourly=eq_hourly, capital=200_000)

        # Calmar denominator should be the hourly DD (~25%), not daily
        assert metrics.max_dd_hourly_pct < -20.0
        # Calmar should be computed using hourly DD when available
        if metrics.annualized_return_pct > 0:
            expected_calmar = metrics.annualized_return_pct / abs(metrics.max_dd_hourly_pct)
            assert abs(metrics.calmar_ratio - expected_calmar) < 0.1

    def test_no_hourly_equity_falls_back(self):
        """Without eq_hourly, MaxDD computed on daily (existing behavior)."""
        eq_daily = self._make_daily_equity([200_000, 195_000, 190_000, 200_000])
        metrics = compute_metrics([], eq_daily, capital=200_000)

        assert metrics.max_drawdown_pct == pytest.approx(-5.0, abs=0.1)
        assert metrics.max_dd_hourly_pct == 0.0  # not computed


class TestTrainingPeriodExclusion:
    """Phase 1B: Annualized return excludes walk-forward training period."""

    def test_trading_start_trims_flat_bars(self):
        """Flat equity at start (training period) should be excluded from annualization."""
        # 365 days flat (training) + 365 days of trading with 50% return
        flat = [200_000] * 365
        trading = list(np.linspace(200_000, 300_000, 365))
        all_vals = flat + trading
        idx = pd.date_range("2022-01-01", periods=len(all_vals), freq="1D")
        eq = pd.Series(all_vals, index=idx)

        # Without trimming: 730 days → annualized ~22.5%
        # With trimming: 365 days → annualized ~50%
        metrics = compute_metrics([], eq, capital=200_000, trading_start_idx=365)
        assert metrics.annualized_return_pct > 40.0  # should be ~50%, not ~22.5%

    def test_auto_detect_first_trade(self):
        """When trading_start_idx not provided, detect first non-flat bar."""
        flat = [200_000] * 100
        trading = list(np.linspace(200_000, 300_000, 265))
        all_vals = flat + trading
        idx = pd.date_range("2022-01-01", periods=len(all_vals), freq="1D")
        eq = pd.Series(all_vals, index=idx)

        metrics = compute_metrics([], eq, capital=200_000)
        # Should auto-detect that first 100 bars are flat and exclude them
        # 265 days of real trading → annualized ~67.4%
        assert metrics.annualized_return_pct > 50.0


class TestCapacityEquityCap:
    """Phase 1C: max_sizing_equity caps portfolio equity for position sizing."""

    def test_max_sizing_equity_field_exists(self):
        """PortfolioConfig should have max_sizing_equity field."""
        from v4.config import PortfolioConfig
        pc = PortfolioConfig()
        assert hasattr(pc, "max_sizing_equity")
        assert pc.max_sizing_equity is None  # default = uncapped
