"""AC14: Gate 4 compatibility with paper engine output.

Tests verify:
- Gate 4 engine works on paper engine output format
- Vacuous checks (parity, slippage) accept trivially when no comparison data exists
- Vacuous acceptance reason is logged
- All standard Gate 4 checks still run on paper engine data
"""

import json
import os

import pytest

from v3.dashboard_paper import PaperDashboardAdapter
from paper_trading.gate4_engine import Gate4Engine


# ---------------------------------------------------------------------------
# Gate 4 on paper engine output
# ---------------------------------------------------------------------------


class TestGate4PaperCompatibility:
    """Gate 4 works on paper engine output format."""

    def test_adapter_converts_paper_output(self, tmp_path, sample_gate4_thresholds):
        """PaperDashboardAdapter converts paper engine output to Gate 4 format."""
        adapter = PaperDashboardAdapter()
        paper_output = {
            "strategy_id": "s11",
            "trade_returns": [0.01, -0.005, 0.02, 0.015, -0.01] * 12,
            "num_weeks": 6,
            "equity_curve": [200000 + i * 100 for i in range(60)],
        }
        gate4_input = adapter.to_gate4_format(paper_output)
        assert "trade_returns" in gate4_input
        assert "num_weeks" in gate4_input

    def test_gate4_accepts_paper_format(self, sample_gate4_thresholds):
        """Gate 4 engine can evaluate paper engine formatted data."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        trade_returns = [0.01] * 55
        result = engine.evaluate(
            trade_returns=trade_returns,
            num_weeks=6,
        )
        assert result["verdict"] in ("PASS", "FAIL", "CONTINUE", "INSUFFICIENT DATA")


# ---------------------------------------------------------------------------
# Vacuous checks — accept trivially with reason logged
# ---------------------------------------------------------------------------


class TestVacuousParityCheck:
    """Parity check accepts trivially when no Freqtrade comparison data exists."""

    def test_parity_check_vacuous_pass(self, sample_gate4_thresholds):
        """Without Freqtrade signals, parity check passes vacuously."""
        adapter = PaperDashboardAdapter()
        result = adapter.check_parity(
            paper_signals={"BTC/USDT": [True, False, True]},
            freqtrade_signals=None,  # No comparison data
        )
        assert result["passed"] is True
        assert "vacuous" in result.get("reason", "").lower()

    def test_parity_check_logs_reason(self, tmp_path, sample_gate4_thresholds):
        """Vacuous parity acceptance logs a reason."""
        adapter = PaperDashboardAdapter(log_dir=str(tmp_path))
        result = adapter.check_parity(
            paper_signals={"BTC/USDT": [True, False, True]},
            freqtrade_signals=None,
        )
        assert result["reason"] is not None
        assert len(result["reason"]) > 0

    def test_parity_check_with_signals_runs_normally(self, sample_gate4_thresholds):
        """When comparison data exists, parity check runs normally."""
        adapter = PaperDashboardAdapter()
        result = adapter.check_parity(
            paper_signals={"BTC/USDT": [True, False, True, True, False]},
            freqtrade_signals={"BTC/USDT": [True, False, True, True, False]},
        )
        # Identical signals → should pass
        assert result["passed"] is True


class TestVacuousSlippageCheck:
    """Slippage check accepts trivially when no live comparison data exists."""

    def test_slippage_check_vacuous_pass(self, sample_gate4_thresholds):
        """Without live slippage data, slippage check passes vacuously."""
        adapter = PaperDashboardAdapter()
        result = adapter.check_slippage(
            modeled_slippage_bps=5.0,
            actual_slippage_bps=None,  # No live data
        )
        assert result["passed"] is True
        assert "vacuous" in result.get("reason", "").lower()

    def test_slippage_check_logs_reason(self, tmp_path, sample_gate4_thresholds):
        """Vacuous slippage acceptance logs a reason."""
        adapter = PaperDashboardAdapter(log_dir=str(tmp_path))
        result = adapter.check_slippage(
            modeled_slippage_bps=5.0,
            actual_slippage_bps=None,
        )
        assert result["reason"] is not None
        assert len(result["reason"]) > 0

    def test_slippage_check_with_data_runs_normally(self, sample_gate4_thresholds):
        """When live slippage data exists, slippage check runs normally."""
        adapter = PaperDashboardAdapter()
        result = adapter.check_slippage(
            modeled_slippage_bps=5.0,
            actual_slippage_bps=4.0,  # Acceptable ratio
        )
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# Full Gate 4 pipeline on paper engine data
# ---------------------------------------------------------------------------


class TestFullGate4OnPaperData:
    """All standard Gate 4 checks run on paper engine data."""

    def test_full_evaluation_with_paper_data(self, sample_gate4_thresholds):
        """Full Gate 4 evaluation works with paper engine trade returns."""
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        # Simulated paper engine returns
        returns = [0.01, -0.005, 0.015, 0.008, -0.003] * 12
        result = engine.evaluate(
            trade_returns=returns,
            num_weeks=8,
        )
        assert "verdict" in result

    def test_go_live_check_with_vacuous_parity(self, sample_gate4_thresholds):
        """Go-live check works when parity is vacuously passed."""
        adapter = PaperDashboardAdapter()
        parity_result = adapter.check_parity(
            paper_signals={"BTC/USDT": [True, False]},
            freqtrade_signals=None,
        )
        assert parity_result["passed"] is True

        # Feed vacuous parity into go-live check
        engine = Gate4Engine(thresholds=sample_gate4_thresholds)
        result = engine.go_live_check(
            backtest_sortino=2.0,
            paper_sortino=1.5,
            backtest_max_dd=0.10,
            paper_max_dd=0.12,
            modeled_slippage_bps=5.0,
            actual_slippage_bps=8.0,
            fill_rate=0.97,
            in_sample_sharpe=1.8,
            paper_sharpe=1.2,
            consecutive_negative_days=10,
            observed_regimes=["TRENDING_UP", "TRENDING_DOWN"],
            parity_divergence=0.0,  # Vacuous pass → 0 divergence
        )
        assert result["verdict"] in ("PASS", "FAIL")

    def test_adapter_logs_vacuous_checks_to_file(self, tmp_path):
        """Adapter writes vacuous check reasons to a log file."""
        log_dir = str(tmp_path)
        adapter = PaperDashboardAdapter(log_dir=log_dir)
        adapter.check_parity(
            paper_signals={"BTC/USDT": [True]},
            freqtrade_signals=None,
        )
        adapter.check_slippage(
            modeled_slippage_bps=5.0,
            actual_slippage_bps=None,
        )

        # Check that a log file was created
        log_files = [f for f in os.listdir(log_dir) if f.endswith((".jsonl", ".log", ".json"))]
        assert len(log_files) > 0

    def test_adapter_log_contains_vacuous_entries(self, tmp_path):
        """Log entries for vacuous checks contain the word 'vacuous'."""
        log_dir = str(tmp_path)
        adapter = PaperDashboardAdapter(log_dir=log_dir)
        adapter.check_parity(
            paper_signals={"BTC/USDT": [True]},
            freqtrade_signals=None,
        )

        log_files = [f for f in os.listdir(log_dir) if f.endswith((".jsonl", ".log", ".json"))]
        assert len(log_files) > 0
        log_path = os.path.join(log_dir, log_files[0])
        with open(log_path, "r") as f:
            content = f.read().lower()
        assert "vacuous" in content
