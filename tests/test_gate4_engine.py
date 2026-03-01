"""AC11: Gate 4 decision framework.

Tests verify:
- SPRT: given H0=0, H1=target_sharpe, returns accept/reject/continue
- Minimum data checks: refuses verdict with <50 trades or <4 weeks
- Kill thresholds from gate4_thresholds.json are applied
- Outputs PASS, FAIL, or INSUFFICIENT DATA
- Degradation thresholds: sortino, max_dd_ratio, slippage, fill_rate
- Sharpe shortfall, rolling sortino, regime coverage, parity divergence
- Go-live gate: all conditions must pass simultaneously
"""

import json
import pytest

from paper_trading.gate4_engine import Gate4Engine


class TestSPRTDecision:
    """SPRT returns accept, reject, or continue."""

    def test_sprt_returns_valid_verdict(self, sample_trade_sharpes, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(
            trade_returns=sample_trade_sharpes,
            num_weeks=6,
        )
        assert result["verdict"] in ("PASS", "FAIL", "CONTINUE", "INSUFFICIENT DATA")

    def test_sprt_with_strong_positive_returns(self, sample_gate4_thresholds):
        """Strongly positive returns should tend toward PASS."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        strong_returns = [0.02] * 60  # Consistent positive returns
        result = engine.evaluate(trade_returns=strong_returns, num_weeks=8)
        # With very strong returns the SPRT should accept
        assert result["verdict"] in ("PASS", "CONTINUE")

    def test_sprt_with_strongly_negative_returns(self, sample_gate4_thresholds):
        """Strongly negative returns should produce FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        bad_returns = [-0.03] * 60
        result = engine.evaluate(trade_returns=bad_returns, num_weeks=8)
        assert result["verdict"] == "FAIL"


class TestMinimumDataChecks:
    """Refuses verdict with <50 trades or <4 weeks."""

    def test_insufficient_trades(self, sample_insufficient_trades, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(
            trade_returns=sample_insufficient_trades,
            num_weeks=6,
        )
        assert result["verdict"] == "INSUFFICIENT DATA"

    def test_insufficient_weeks(self, sample_trade_sharpes, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(
            trade_returns=sample_trade_sharpes,
            num_weeks=2,  # Less than 4 weeks
        )
        assert result["verdict"] == "INSUFFICIENT DATA"

    def test_exactly_50_trades_is_sufficient(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        returns = [0.01] * 50
        result = engine.evaluate(trade_returns=returns, num_weeks=5)
        # 50 trades and 5 weeks should NOT be INSUFFICIENT DATA
        assert result["verdict"] != "INSUFFICIENT DATA"

    def test_exactly_4_weeks_is_sufficient(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        returns = [0.01] * 55
        result = engine.evaluate(trade_returns=returns, num_weeks=4)
        assert result["verdict"] != "INSUFFICIENT DATA"

    def test_49_trades_is_insufficient(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        returns = [0.01] * 49
        result = engine.evaluate(trade_returns=returns, num_weeks=5)
        assert result["verdict"] == "INSUFFICIENT DATA"

    def test_3_weeks_is_insufficient(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        returns = [0.01] * 55
        result = engine.evaluate(trade_returns=returns, num_weeks=3)
        assert result["verdict"] == "INSUFFICIENT DATA"


class TestKillThresholds:
    """Kill thresholds from gate4_thresholds.json are applied."""

    def test_max_drawdown_kill(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(
            trade_returns=[0.01] * 55,
            num_weeks=6,
            current_drawdown=0.30,  # Above 0.25 kill threshold
        )
        assert result["verdict"] == "FAIL"
        assert "drawdown" in result.get("reason", "").lower()

    def test_min_sharpe_kill(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(
            trade_returns=[-0.005] * 55,
            num_weeks=6,
            realized_sharpe=-0.8,  # Below -0.5 kill threshold
        )
        assert result["verdict"] == "FAIL"

    def test_below_kill_thresholds_not_auto_fail(self, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(
            trade_returns=[0.01] * 55,
            num_weeks=6,
            current_drawdown=0.08,  # Below kill threshold
            realized_sharpe=0.5,     # Above kill threshold
        )
        # Should NOT be FAIL due to kill thresholds
        assert result["verdict"] != "FAIL" or "kill" not in result.get("reason", "").lower()


class TestThresholdsFromFile:
    """Engine loads thresholds from JSON file."""

    def test_load_thresholds_from_file(self, sample_gate4_thresholds_json):
        engine = Gate4Engine.from_file(sample_gate4_thresholds_json)
        assert engine.thresholds["min_trades"] == 50
        assert engine.thresholds["min_weeks"] == 4
        assert engine.thresholds["target_sharpe"] == pytest.approx(0.5)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            Gate4Engine.from_file("/nonexistent/gate4_thresholds.json")


class TestOutputFormat:
    """Output contains verdict and supporting data."""

    def test_output_has_verdict_key(self, sample_trade_sharpes, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(trade_returns=sample_trade_sharpes, num_weeks=6)
        assert "verdict" in result

    def test_output_verdict_is_string(self, sample_trade_sharpes, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(trade_returns=sample_trade_sharpes, num_weeks=6)
        assert isinstance(result["verdict"], str)

    def test_verdict_is_one_of_valid_values(self, sample_trade_sharpes, sample_gate4_thresholds):
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.evaluate(trade_returns=sample_trade_sharpes, num_weeks=6)
        assert result["verdict"] in ("PASS", "FAIL", "CONTINUE", "INSUFFICIENT DATA")


class TestDegradationThresholds:
    """Kill thresholds for backtest-to-paper degradation ratios."""

    def test_sortino_degradation_below_threshold_fails(self, sample_gate4_thresholds):
        """paper_sortino / backtest_sortino < 0.6 => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_degradation(
            backtest_sortino=2.5,
            paper_sortino=1.2,  # ratio = 0.48, below 0.6 threshold
        )
        assert result["passed"] is False
        assert "sortino" in result.get("reason", "").lower()

    def test_max_dd_ratio_exceeded_fails(self, sample_gate4_thresholds):
        """paper_dd / backtest_dd > 1.5x => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_degradation(
            backtest_max_dd=0.10,
            paper_max_dd=0.18,  # ratio = 1.8, above 1.5 threshold
        )
        assert result["passed"] is False
        assert "drawdown" in result.get("reason", "").lower()

    def test_slippage_ratio_exceeded_fails(self, sample_gate4_thresholds):
        """actual_slippage / modeled_slippage > 2.0x => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_slippage(
            modeled_slippage_bps=5.0,
            actual_slippage_bps=12.0,  # ratio = 2.4, above 2.0 threshold
        )
        assert result["passed"] is False
        assert "slippage" in result.get("reason", "").lower()

    def test_fill_rate_below_threshold_fails(self, sample_gate4_thresholds):
        """fill_rate < 95% => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_fill_rate(
            fill_rate=0.92,  # below 0.95 threshold
        )
        assert result["passed"] is False
        assert "fill" in result.get("reason", "").lower()


class TestSharpeAndRollingChecks:
    """Sharpe shortfall and rolling Sortino checks."""

    def test_sharpe_shortfall_exceeded_fails(self, sample_gate4_thresholds):
        """IS_sharpe - paper_sharpe > 1.0 => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_sharpe_shortfall(
            in_sample_sharpe=2.5,
            paper_sharpe=1.2,  # shortfall = 1.3, above 1.0 threshold
        )
        assert result["passed"] is False
        assert "sharpe" in result.get("reason", "").lower()

    def test_rolling_sortino_negative_too_long_fails(self, sample_gate4_thresholds):
        """Rolling Sortino < 0 for 30+ consecutive days => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_rolling_sortino(
            consecutive_negative_days=35,  # above 30-day threshold
        )
        assert result["passed"] is False
        assert "sortino" in result.get("reason", "").lower()


class TestRegimeAndParityChecks:
    """Regime coverage and parity divergence checks."""

    def test_insufficient_regime_coverage_fails(self, sample_gate4_thresholds):
        """Fewer than 2 distinct regimes observed => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_regime_coverage(
            observed_regimes=["TRENDING_UP"],  # only 1 regime
        )
        assert result["passed"] is False
        assert "regime" in result.get("reason", "").lower()

    def test_parity_divergence_exceeded_fails(self, sample_gate4_thresholds):
        """Parity divergence > 10% => FAIL."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.check_parity_divergence(
            divergence=0.15,  # above 0.10 threshold
        )
        assert result["passed"] is False
        assert "parity" in result.get("reason", "").lower() or "divergence" in result.get("reason", "").lower()


class TestGoLiveGate:
    """All go-live conditions must pass simultaneously."""

    def test_all_go_live_requirements_pass(self, sample_gate4_thresholds):
        """When all 6 degradation/operational conditions are met => PASS."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.go_live_check(
            backtest_sortino=2.0,
            paper_sortino=1.5,           # ratio 0.75 >= 0.6 threshold
            backtest_max_dd=0.10,
            paper_max_dd=0.12,           # ratio 1.2 <= 1.5 threshold
            modeled_slippage_bps=5.0,
            actual_slippage_bps=8.0,     # ratio 1.6 <= 2.0 threshold
            fill_rate=0.97,              # >= 0.95 threshold
            in_sample_sharpe=1.8,
            paper_sharpe=1.2,            # shortfall 0.6 <= 1.0 threshold
            consecutive_negative_days=10, # <= 30 threshold
            observed_regimes=["TRENDING_UP", "TRENDING_DOWN"],  # 2 >= 2 threshold
            parity_divergence=0.05,      # <= 0.10 threshold
        )
        assert result["verdict"] == "PASS"
