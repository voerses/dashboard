"""AC12: Parity monitoring.

Tests verify:
- Parity check computes signal divergence between engine and Freqtrade
- Divergence >10% triggers alert flag
- Divergence >20% triggers kill flag
- Per-token breakdown of where signals disagree
"""

import pytest

from freqtrade_bridge.parity_check import ParityCheck


class TestDivergenceComputation:
    """Compute signal divergence between engine and Freqtrade."""

    def test_identical_signals_zero_divergence(self):
        checker = ParityCheck()
        signals_a = [True, False, True, True, False]
        signals_b = [True, False, True, True, False]
        assert checker.compute_divergence(signals_a, signals_b) == pytest.approx(0.0)

    def test_completely_opposite_signals(self):
        checker = ParityCheck()
        signals_a = [True, True, True, True, True]
        signals_b = [False, False, False, False, False]
        assert checker.compute_divergence(signals_a, signals_b) == pytest.approx(1.0)

    def test_partial_divergence(self):
        checker = ParityCheck()
        signals_a = [True, True, True, True, True, True, True, True, True, True]
        signals_b = [True, True, True, True, True, True, True, False, False, False]
        assert checker.compute_divergence(signals_a, signals_b) == pytest.approx(0.30)

    def test_empty_signals_raises_or_zero(self):
        checker = ParityCheck()
        with pytest.raises(ValueError):
            checker.compute_divergence([], [])

    def test_mismatched_lengths_raises(self):
        checker = ParityCheck()
        with pytest.raises(ValueError):
            checker.compute_divergence([True, False], [True])


class TestAlertFlag:
    """Divergence >10% triggers alert flag."""

    def test_alert_at_12_percent(self):
        checker = ParityCheck()
        # 25 signals, 3 differ = 12%
        engine = [True] * 25
        ft = [True] * 22 + [False] * 3
        result = checker.check(engine_signals=engine, freqtrade_signals=ft)
        assert result["alert"] is True

    def test_no_alert_at_8_percent(self):
        checker = ParityCheck()
        # 25 signals, 2 differ = 8%
        engine = [True] * 25
        ft = [True] * 23 + [False] * 2
        result = checker.check(engine_signals=engine, freqtrade_signals=ft)
        assert result["alert"] is False

    def test_alert_at_exactly_10_percent(self):
        """At exactly 10%, check boundary behavior (>10% triggers)."""
        checker = ParityCheck()
        # 10 signals, 1 differs = exactly 10%
        engine = [True] * 10
        ft = [True] * 9 + [False]
        result = checker.check(engine_signals=engine, freqtrade_signals=ft)
        # >10% triggers, so exactly 10% should NOT trigger
        assert result["alert"] is False


class TestKillFlag:
    """Divergence >20% triggers kill flag."""

    def test_kill_at_25_percent(self):
        checker = ParityCheck()
        # 20 signals, 5 differ = 25%
        engine = [True] * 20
        ft = [True] * 15 + [False] * 5
        result = checker.check(engine_signals=engine, freqtrade_signals=ft)
        assert result["kill"] is True

    def test_no_kill_at_15_percent(self):
        checker = ParityCheck()
        # 20 signals, 3 differ = 15%
        engine = [True] * 20
        ft = [True] * 17 + [False] * 3
        result = checker.check(engine_signals=engine, freqtrade_signals=ft)
        assert result["kill"] is False

    def test_kill_at_exactly_20_percent(self):
        """At exactly 20%, check boundary behavior (>20% triggers)."""
        checker = ParityCheck()
        # 10 signals, 2 differ = exactly 20%
        engine = [True] * 10
        ft = [True] * 8 + [False] * 2
        result = checker.check(engine_signals=engine, freqtrade_signals=ft)
        # >20% triggers, so exactly 20% should NOT trigger
        assert result["kill"] is False


class TestCheckOutput:
    """Check result contains divergence, alert, kill keys."""

    def test_check_result_has_divergence(self):
        checker = ParityCheck()
        result = checker.check(
            engine_signals=[True, True, True],
            freqtrade_signals=[True, True, False],
        )
        assert "divergence" in result

    def test_check_result_has_alert(self):
        checker = ParityCheck()
        result = checker.check(
            engine_signals=[True, True, True],
            freqtrade_signals=[True, True, False],
        )
        assert "alert" in result

    def test_check_result_has_kill(self):
        checker = ParityCheck()
        result = checker.check(
            engine_signals=[True, True, True],
            freqtrade_signals=[True, True, False],
        )
        assert "kill" in result


class TestPerTokenBreakdown:
    """Per-token breakdown of where signals disagree."""

    def test_per_token_all_tokens_present(self, sample_engine_signals, sample_freqtrade_signals):
        checker = ParityCheck()
        breakdown = checker.per_token_breakdown(
            engine_signals=sample_engine_signals,
            freqtrade_signals=sample_freqtrade_signals,
        )
        for token in sample_engine_signals:
            assert token in breakdown

    def test_per_token_divergence_value(self, sample_engine_signals, sample_freqtrade_signals):
        checker = ParityCheck()
        breakdown = checker.per_token_breakdown(
            engine_signals=sample_engine_signals,
            freqtrade_signals=sample_freqtrade_signals,
        )
        # BTC/USDT signals are identical in fixtures
        assert breakdown["BTC/USDT"]["divergence"] == pytest.approx(0.0)
        # ETH/USDT signals differ
        assert breakdown["ETH/USDT"]["divergence"] > 0.0

    def test_per_token_disagreement_indices(self, sample_engine_signals, sample_freqtrade_signals):
        checker = ParityCheck()
        breakdown = checker.per_token_breakdown(
            engine_signals=sample_engine_signals,
            freqtrade_signals=sample_freqtrade_signals,
        )
        # BTC/USDT has no disagreements
        assert breakdown["BTC/USDT"]["disagreement_indices"] == []
        # ETH/USDT has disagreements
        assert len(breakdown["ETH/USDT"]["disagreement_indices"]) > 0

    def test_disagreement_indices_correct(self):
        """Verify exact disagreement positions."""
        checker = ParityCheck()
        engine = {"TOKEN": [True, False, True, False, True]}
        ft = {"TOKEN": [True, True, True, False, False]}
        breakdown = checker.per_token_breakdown(engine_signals=engine, freqtrade_signals=ft)
        # Positions 1 and 4 differ
        assert 1 in breakdown["TOKEN"]["disagreement_indices"]
        assert 4 in breakdown["TOKEN"]["disagreement_indices"]
