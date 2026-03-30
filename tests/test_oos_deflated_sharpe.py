"""Tests for Deflated Sharpe Ratio — AC14, AC15.

Tests the correct implementation of deflated_sharpe_ratio() in v4/metrics.py
per Bailey & Lopez de Prado (2014), and the deprecation/redirect of the
broken deflated_sharpe() in v4/cpcv.py.
"""
import math
import warnings

import numpy as np
import pytest

from v4.metrics import deflated_sharpe_ratio


class TestDeflatedSharpeRatioSignature:
    """AC14: deflated_sharpe_ratio() exists with correct signature."""

    def test_ac14_function_exists_in_metrics(self):
        """AC14: deflated_sharpe_ratio is importable from v4.metrics."""
        assert callable(deflated_sharpe_ratio)

    def test_ac14_accepts_required_parameters(self):
        """AC14: Function accepts (sharpe, n_obs, skewness, kurtosis, n_trials)."""
        result = deflated_sharpe_ratio(
            sharpe=1.5,
            n_obs=252,
            skewness=0.0,
            kurtosis=3.0,
            n_trials=10,
        )
        assert isinstance(result, float)


class TestDeflatedSharpeRatioValues:
    """AC14: deflated_sharpe_ratio() produces correct values per Bailey & Lopez de Prado."""

    def test_ac14_n_trials_1_returns_high_pvalue(self):
        """AC14: With n_trials=1, DSR should return ~1.0 (no deflation)."""
        dsr = deflated_sharpe_ratio(
            sharpe=2.0,
            n_obs=252,
            skewness=0.0,
            kurtosis=3.0,
            n_trials=1,
        )
        assert dsr > 0.95, f"DSR with n_trials=1 and sharpe=2.0 should be near 1.0, got {dsr}"

    def test_ac14_high_n_trials_lowers_dsr(self):
        """AC14: With high n_trials, DSR is lower than with n_trials=1."""
        dsr_1 = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=1,
        )
        dsr_100 = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=100,
        )
        assert dsr_100 < dsr_1, (
            f"DSR with 100 trials ({dsr_100}) should be lower than "
            f"DSR with 1 trial ({dsr_1})"
        )

    def test_ac14_known_inputs_hand_computed(self):
        """AC14: Verify against hand-computed Bailey & Lopez de Prado formula.

        Inputs: sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=10
        Hand computation:
          Var(SR) = (1 - 0*1.5 + (3-1)/4 * 1.5^2) / (252-1)
                  = (1 + 0 + 0.5*2.25) / 251
                  = (1 + 1.125) / 251
                  = 2.125 / 251
                  = 0.008466
          E[max SR] = sqrt(2*ln(10)) * (1 - ln(ln(10)) / (2*ln(10)))
                    = sqrt(4.6052) * (1 - ln(2.3026) / 4.6052)
                    = 2.1460 * (1 - 0.8340 / 4.6052)
                    = 2.1460 * (1 - 0.1811)
                    = 2.1460 * 0.8189
                    = 1.7574
          z = (1.5 - 1.7574) / sqrt(0.008466)
            = -0.2574 / 0.09201
            = -2.797
          DSR = Phi(-2.797) ≈ 0.0026
        """
        dsr = deflated_sharpe_ratio(
            sharpe=1.5,
            n_obs=252,
            skewness=0.0,
            kurtosis=3.0,
            n_trials=10,
        )
        assert 0.0 <= dsr <= 1.0, f"DSR should be a probability in [0, 1], got {dsr}"
        # DSR should be very low — Sharpe of 1.5 does not survive 10-trial correction
        assert dsr < 0.10, (
            f"AC14: With sharpe=1.5, n_trials=10, n_obs=252, DSR should be very low "
            f"(~0.003 per hand computation), got {dsr}"
        )

    def test_ac14_strong_sharpe_survives_few_trials(self):
        """AC14: A very strong Sharpe (3.0) with few trials should have high DSR.

        Inputs: sharpe=3.0, n_obs=500, skewness=0.0, kurtosis=3.0, n_trials=5
        Hand computation:
          Var(SR) = (1 + (2/4)*9) / 499 = (1+4.5)/499 = 5.5/499 = 0.01102
          E[max SR] = sqrt(2*ln(5)) * (1 - ln(ln(5))/(2*ln(5)))
                    = sqrt(3.2189) * (1 - ln(1.6094)/3.2189)
                    = 1.7941 * (1 - 0.4764/3.2189)
                    = 1.7941 * 0.8520
                    = 1.5282
          z = (3.0 - 1.5282) / sqrt(0.01102) = 1.4718 / 0.10498 = 14.02
          DSR = Phi(14.02) ≈ 1.0
        """
        dsr = deflated_sharpe_ratio(
            sharpe=3.0, n_obs=500, skewness=0.0, kurtosis=3.0, n_trials=5,
        )
        assert dsr > 0.99, (
            f"AC14: Sharpe 3.0 with 5 trials should have DSR near 1.0, got {dsr}"
        )

    def test_ac14_skewness_affects_result(self):
        """AC14: Non-zero skewness changes the variance of SR, hence DSR."""
        dsr_no_skew = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=10,
        )
        dsr_neg_skew = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=-1.0, kurtosis=3.0, n_trials=10,
        )
        assert dsr_no_skew != dsr_neg_skew, (
            "Non-zero skewness should affect DSR (variance of SR includes skewness term)"
        )

    def test_ac14_kurtosis_affects_result(self):
        """AC14: Excess kurtosis changes the variance of SR, hence DSR."""
        dsr_normal = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=10,
        )
        dsr_fat_tails = deflated_sharpe_ratio(
            sharpe=1.5, n_obs=252, skewness=0.0, kurtosis=6.0, n_trials=10,
        )
        assert dsr_normal != dsr_fat_tails, (
            "Different kurtosis should produce different DSR values"
        )

    def test_ac14_returns_value_between_0_and_1(self):
        """AC14: DSR is a probability, always in [0, 1]."""
        for n_trials in [1, 5, 50, 200]:
            dsr = deflated_sharpe_ratio(
                sharpe=1.0, n_obs=500, skewness=-0.5, kurtosis=4.0, n_trials=n_trials,
            )
            assert 0.0 <= dsr <= 1.0, f"DSR={dsr} out of [0,1] for n_trials={n_trials}"

    def test_ac14_negative_sharpe_gives_very_low_dsr(self):
        """AC14: Negative Sharpe should produce near-zero DSR with any n_trials."""
        dsr = deflated_sharpe_ratio(
            sharpe=-0.5, n_obs=252, skewness=0.0, kurtosis=3.0, n_trials=5,
        )
        assert dsr < 0.05, (
            f"AC14: Negative Sharpe should give very low DSR, got {dsr}"
        )


class TestBrokenDeflatedSharpeRedirect:
    """AC15: The old deflated_sharpe() in v4/cpcv.py redirects or shows deprecation warning."""

    def test_ac15_old_function_still_importable(self):
        """AC15: v4/cpcv.py still exports deflated_sharpe (for backward compat)."""
        from v4.cpcv import deflated_sharpe
        assert callable(deflated_sharpe)

    def test_ac15_old_function_shows_deprecation(self):
        """AC15: Calling the old deflated_sharpe() issues a deprecation warning."""
        from v4.cpcv import deflated_sharpe

        np.random.seed(42)
        returns = list(np.random.normal(0.001, 0.02, 252))

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = deflated_sharpe(returns)

            deprecation_warnings = [
                x for x in w if issubclass(x.category, (DeprecationWarning, FutureWarning))
            ]
            assert len(deprecation_warnings) > 0, (
                "AC15: Old deflated_sharpe() should issue a deprecation warning "
                "directing users to v4.metrics.deflated_sharpe_ratio()"
            )

        assert isinstance(result, float)
