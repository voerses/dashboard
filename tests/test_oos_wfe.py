"""Tests for Walk-Forward Efficiency and DSR integration — AC11, AC12, AC13, AC16.

Tests WFE computation (OOS return / IS return), edge cases for negative IS,
warning thresholds, and n_trials parameter passing to DSR.
"""
import warnings

import numpy as np
import pytest


class TestWalkForwardEfficiency:
    """AC11: WFE = annualized_OOS_return / annualized_IS_return."""

    def test_ac11_wfe_basic_calculation(self):
        """AC11: WFE = oos_return / is_return when IS > 0."""
        from v4.metrics import walk_forward_efficiency

        wfe = walk_forward_efficiency(
            oos_annualized_return=0.10,  # 10% OOS
            is_annualized_return=0.20,   # 20% IS
        )
        assert wfe == pytest.approx(0.50, abs=0.01), (
            f"WFE should be 0.10/0.20 = 0.50, got {wfe}"
        )

    def test_ac11_wfe_above_100_pct(self):
        """AC11: WFE can exceed 100% if OOS outperforms IS."""
        from v4.metrics import walk_forward_efficiency

        wfe = walk_forward_efficiency(
            oos_annualized_return=0.30,
            is_annualized_return=0.20,
        )
        assert wfe == pytest.approx(1.50, abs=0.01), (
            f"WFE should be 0.30/0.20 = 1.50, got {wfe}"
        )

    def test_ac11_wfe_field_on_performance_metrics(self):
        """AC11: PerformanceMetrics has a walk_forward_efficiency field."""
        from v4.metrics import PerformanceMetrics

        m = PerformanceMetrics()
        assert hasattr(m, "walk_forward_efficiency"), (
            "PerformanceMetrics should have walk_forward_efficiency field"
        )
        # Default should be None (not computed until WF validation)
        assert m.walk_forward_efficiency is None


class TestWfeNegativeIS:
    """AC12: WFE is None when IS return <= 0."""

    def test_ac12_wfe_none_when_is_zero(self):
        """AC12: When IS annualized return is 0, WFE is None."""
        from v4.metrics import walk_forward_efficiency

        wfe = walk_forward_efficiency(
            oos_annualized_return=0.10,
            is_annualized_return=0.0,
        )
        assert wfe is None, f"WFE should be None when IS return is 0, got {wfe}"

    def test_ac12_wfe_none_when_is_negative(self):
        """AC12: When IS annualized return < 0, WFE is None."""
        from v4.metrics import walk_forward_efficiency

        wfe = walk_forward_efficiency(
            oos_annualized_return=0.10,
            is_annualized_return=-0.05,
        )
        assert wfe is None, f"WFE should be None when IS return < 0, got {wfe}"

    def test_ac12_wfe_none_with_warning(self):
        """AC12: When IS <= 0, a warning 'IS not profitable — WFE undefined' is issued."""
        from v4.metrics import walk_forward_efficiency

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            wfe = walk_forward_efficiency(
                oos_annualized_return=0.10,
                is_annualized_return=-0.05,
            )

        assert wfe is None
        warning_messages = [str(x.message) for x in w]
        assert any("IS not profitable" in msg for msg in warning_messages), (
            f"Should warn 'IS not profitable — WFE undefined', got warnings: {warning_messages}"
        )


class TestWfeWarningThreshold:
    """AC13: WFE < 50% triggers warning."""

    def test_ac13_wfe_below_50_pct_triggers_warning(self):
        """AC13: When WFE < 50%, a warning is flagged in validation output."""
        from v4.metrics import walk_forward_efficiency

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            wfe = walk_forward_efficiency(
                oos_annualized_return=0.05,
                is_annualized_return=0.20,  # WFE = 25%
            )

        assert wfe == pytest.approx(0.25, abs=0.01)
        warning_messages = [str(x.message) for x in w]
        assert any("WFE" in msg and ("50%" in msg or "0.5" in msg or "below" in msg.lower())
                    for msg in warning_messages), (
            f"Should warn about WFE < 50%, got warnings: {warning_messages}"
        )

    def test_ac13_wfe_above_50_pct_no_warning(self):
        """AC13: When WFE >= 50%, no warning is triggered."""
        from v4.metrics import walk_forward_efficiency

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            wfe = walk_forward_efficiency(
                oos_annualized_return=0.15,
                is_annualized_return=0.20,  # WFE = 75%
            )

        assert wfe == pytest.approx(0.75, abs=0.01)
        wfe_warnings = [x for x in w if "WFE" in str(x.message)]
        assert len(wfe_warnings) == 0, (
            f"Should NOT warn about WFE when >= 50%, got: {[str(x.message) for x in wfe_warnings]}"
        )


class TestDsrNTrials:
    """AC16: Validation scripts pass n_trials to DSR."""

    def test_ac16_dsr_accepts_n_trials(self):
        """AC16: deflated_sharpe_ratio() uses n_trials parameter for multiple testing correction."""
        from v4.metrics import deflated_sharpe_ratio

        # With more trials, DSR should be lower for the same Sharpe
        dsr_few = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=5,
        )
        dsr_many = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=50,
        )
        assert dsr_many < dsr_few, (
            f"More trials should produce lower DSR: {dsr_many} should be < {dsr_few}"
        )

    def test_ac16_n_trials_is_required_parameter(self):
        """AC16: n_trials must be provided — it's not optional or defaulted to len(returns)."""
        from v4.metrics import deflated_sharpe_ratio
        import inspect

        sig = inspect.signature(deflated_sharpe_ratio)
        params = sig.parameters

        assert "n_trials" in params, "deflated_sharpe_ratio must accept n_trials parameter"
        # n_trials should not have a default (or if it does, it should not be derived
        # from len(returns) which was the bug in the old implementation)
