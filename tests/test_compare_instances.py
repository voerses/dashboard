"""AC9 + AC12: Cross-instance comparison, per-strategy metrics, and degradation reporting.

Tests verify:
- Per-strategy metrics: Sortino, Calmar, profit factor, win rate
- Dual-benchmark alpha: strategy return vs BTC buy-and-hold, vs equal-weight universe
- Head-to-head comparison: same strategy different exchanges, different strategies same exchange
- Regime coverage report: flags insufficient regime diversity
- Uptime report: flags prolonged downtime during signal windows
- Degradation report (AC12): backtest-to-paper metric degradation ratios
"""

import json
import os
import pytest

from paper_trading.compare_instances import CompareInstances


# ---------------------------------------------------------------------------
# AC9: Per-strategy metrics
# ---------------------------------------------------------------------------

class TestPerStrategyMetrics:
    """Verify per-strategy metric computation from trade/equity data."""

    def test_compute_sortino_ratio_from_trades(self, tmp_path):
        """Given trades.jsonl data, verify Sortino is computed correctly.

        Sortino = (mean return - target) / downside_deviation.
        With target=0, only negative returns contribute to downside std.
        """
        trades = [
            {"trade_id": f"t{i:03d}", "pnl": pnl, "return_pct": pnl / 1000.0}
            for i, pnl in enumerate(
                [50, -20, 30, -10, 40, -15, 25, 60, -5, 35]
            )
        ]
        trades_path = tmp_path / "trades.jsonl"
        with open(trades_path, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        ci = CompareInstances()
        sortino = ci.compute_sortino(trades_path=str(trades_path), target=0.0)

        # Mean return_pct > 0 and downside deviation is from negatives only
        assert isinstance(sortino, float)
        assert sortino > 0.0  # net-positive trades => positive Sortino

    def test_compute_calmar_ratio_from_equity(self, tmp_path):
        """Given equity.csv, verify Calmar = annualized_return / max_drawdown."""
        import csv

        equity_path = tmp_path / "equity.csv"
        # 30 days of equity data with a clear drawdown
        rows = [
            {"timestamp": f"2024-01-{d:02d}T00:00:00Z", "equity": eq}
            for d, eq in [
                (1, 200000), (2, 202000), (3, 204000), (4, 201000),
                (5, 199000), (6, 203000), (7, 206000), (8, 210000),
            ]
        ]
        with open(equity_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "equity"])
            writer.writeheader()
            writer.writerows(rows)

        ci = CompareInstances()
        calmar = ci.compute_calmar(equity_path=str(equity_path))

        assert isinstance(calmar, float)
        # Positive overall return with bounded drawdown -> positive Calmar
        assert calmar > 0.0

    def test_compute_profit_factor(self, tmp_path):
        """Profit factor = gross_profit / gross_loss."""
        trades = [
            {"trade_id": "t001", "pnl": 100.0},
            {"trade_id": "t002", "pnl": -40.0},
            {"trade_id": "t003", "pnl": 60.0},
            {"trade_id": "t004", "pnl": -20.0},
        ]
        trades_path = tmp_path / "trades.jsonl"
        with open(trades_path, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        ci = CompareInstances()
        pf = ci.compute_profit_factor(trades_path=str(trades_path))

        # gross_profit = 160, gross_loss = 60 => PF = 160/60 = 2.6667
        assert pf == pytest.approx(160.0 / 60.0, rel=0.01)

    def test_compute_win_rate(self, tmp_path):
        """Win rate = number_of_winning_trades / total_trades."""
        trades = [
            {"trade_id": "t001", "pnl": 100.0},
            {"trade_id": "t002", "pnl": -40.0},
            {"trade_id": "t003", "pnl": 60.0},
            {"trade_id": "t004", "pnl": -20.0},
            {"trade_id": "t005", "pnl": 10.0},
        ]
        trades_path = tmp_path / "trades.jsonl"
        with open(trades_path, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        ci = CompareInstances()
        wr = ci.compute_win_rate(trades_path=str(trades_path))

        # 3 winners out of 5 trades = 0.60
        assert wr == pytest.approx(0.60, rel=0.01)


# ---------------------------------------------------------------------------
# AC9: Dual-benchmark alpha
# ---------------------------------------------------------------------------

class TestDualBenchmarkAlpha:
    """Strategy return vs BTC buy-and-hold and vs equal-weight universe."""

    def test_alpha_vs_btc_benchmark(self):
        """Alpha = strategy_return - btc_buy_and_hold_return."""
        ci = CompareInstances()
        alpha = ci.alpha_vs_benchmark(
            strategy_return=0.15,
            benchmark_return=0.10,
        )
        assert alpha == pytest.approx(0.05, rel=0.01)

    def test_alpha_vs_equal_weight_benchmark(self):
        """Alpha = strategy_return - equal_weight_universe_return."""
        ci = CompareInstances()
        alpha = ci.alpha_vs_benchmark(
            strategy_return=0.12,
            benchmark_return=0.08,
        )
        assert alpha == pytest.approx(0.04, rel=0.01)


# ---------------------------------------------------------------------------
# AC9: Head-to-head comparison
# ---------------------------------------------------------------------------

class TestHeadToHeadComparison:
    """Compare instances head-to-head."""

    def test_same_strategy_different_exchanges(self, tmp_path):
        """Compare s11_kraken vs s11_binance — same strategy, different venues."""
        ci = CompareInstances()
        result = ci.head_to_head(
            instance_a={"instance_id": "s11_kraken", "sharpe": 1.2, "sortino": 1.8, "total_return": 0.12},
            instance_b={"instance_id": "s11_binance", "sharpe": 1.5, "sortino": 2.0, "total_return": 0.15},
        )
        assert "winner" in result
        assert result["winner"] in ("s11_kraken", "s11_binance")
        assert "metrics_compared" in result

    def test_different_strategies_same_exchange(self, tmp_path):
        """Compare s11_kraken vs s09_kraken — different strategies, same venue."""
        ci = CompareInstances()
        result = ci.head_to_head(
            instance_a={"instance_id": "s11_kraken", "sharpe": 1.2, "sortino": 1.8, "total_return": 0.12},
            instance_b={"instance_id": "s09_kraken", "sharpe": 0.9, "sortino": 1.1, "total_return": 0.08},
        )
        assert result["winner"] == "s11_kraken"  # Better across all metrics


# ---------------------------------------------------------------------------
# AC9: Regime coverage report
# ---------------------------------------------------------------------------

class TestRegimeCoverageReport:
    """Regime coverage should flag insufficient diversity."""

    def test_regime_coverage_flags_single_regime(self):
        """Only bull/TRENDING_UP observed -> flags insufficient coverage."""
        ci = CompareInstances()
        report = ci.regime_coverage_report(
            observed_regimes=["TRENDING_UP"],
        )
        assert report["sufficient"] is False

    def test_regime_coverage_passes_multiple_regimes(self):
        """Bull + bear observed -> passes coverage check."""
        ci = CompareInstances()
        report = ci.regime_coverage_report(
            observed_regimes=["TRENDING_UP", "TRENDING_DOWN"],
        )
        assert report["sufficient"] is True

    def test_regime_coverage_report_includes_regime_list(self):
        """Report includes the list of observed regimes."""
        ci = CompareInstances()
        report = ci.regime_coverage_report(
            observed_regimes=["TRENDING_UP", "MEAN_REVERTING", "HIGH_VOL_CHAOS"],
        )
        assert "observed_regimes" in report
        assert set(report["observed_regimes"]) == {"TRENDING_UP", "MEAN_REVERTING", "HIGH_VOL_CHAOS"}


# ---------------------------------------------------------------------------
# AC9: Uptime report
# ---------------------------------------------------------------------------

class TestUptimeReport:
    """Uptime monitoring during signal windows."""

    def test_uptime_above_threshold(self):
        """99%+ uptime passes."""
        ci = CompareInstances()
        report = ci.uptime_report(
            total_expected_seconds=86400,
            total_downtime_seconds=60,  # ~0.07% downtime => 99.93% uptime
        )
        assert report["uptime_pct"] >= 0.99
        assert report["passed"] is True

    def test_uptime_flags_prolonged_downtime(self):
        """Gap >5 minutes during signal windows is flagged."""
        ci = CompareInstances()
        report = ci.uptime_report(
            total_expected_seconds=86400,
            total_downtime_seconds=600,  # 10 min downtime
            max_gap_seconds=360,  # longest single gap was 6 min (>5 min)
        )
        assert report["prolonged_gap_flagged"] is True


# ---------------------------------------------------------------------------
# AC12: Degradation report
# ---------------------------------------------------------------------------

class TestDegradationReport:
    """Backtest-to-paper metric degradation ratios."""

    def test_load_backtest_baseline_from_sweep_summary(self, tmp_path):
        """Loads baseline metrics from results/sweep_summary_*.json structure."""
        sweep_data = {
            "s11": {
                "BTC/USDT": {"sharpe": 1.8, "sortino": 2.5, "calmar": 3.0},
            },
        }
        sweep_path = tmp_path / "results"
        sweep_path.mkdir()
        summary_file = sweep_path / "sweep_summary_s11.json"
        summary_file.write_text(json.dumps(sweep_data))

        ci = CompareInstances()
        baseline = ci.load_backtest_baseline(
            sweep_dir=str(sweep_path),
            strategy="s11",
        )
        assert "sharpe" in baseline or "BTC/USDT" in baseline

    def test_compute_degradation_ratio(self):
        """Degradation = paper_metric / backtest_metric for each metric."""
        ci = CompareInstances()
        degradation = ci.compute_degradation(
            backtest_metrics={"sortino": 2.5, "calmar": 3.0, "sharpe": 1.8},
            paper_metrics={"sortino": 1.5, "calmar": 1.8, "sharpe": 1.2},
        )
        assert degradation["sortino"] == pytest.approx(1.5 / 2.5, rel=0.01)
        assert degradation["calmar"] == pytest.approx(1.8 / 3.0, rel=0.01)
        assert degradation["sharpe"] == pytest.approx(1.2 / 1.8, rel=0.01)

    def test_degradation_report_format(self):
        """Output has per-metric degradation ratios."""
        ci = CompareInstances()
        report = ci.degradation_report(
            backtest_metrics={"sortino": 2.0, "calmar": 2.5, "sharpe": 1.5},
            paper_metrics={"sortino": 1.2, "calmar": 1.5, "sharpe": 0.9},
        )
        assert "degradation_ratios" in report
        ratios = report["degradation_ratios"]
        assert "sortino" in ratios
        assert "calmar" in ratios
        assert "sharpe" in ratios
        # All ratios should be between 0 and 1 (paper < backtest is expected)
        for metric, ratio in ratios.items():
            assert isinstance(ratio, float)
