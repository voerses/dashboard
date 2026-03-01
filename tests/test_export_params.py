"""AC3: V3 validation -> parameter export.

Tests verify:
- export_params reads sweep_summary JSON and extracts validated tokens per strategy
- Only tokens passing V3 dual gate are whitelisted
- Output contains: strategy params, exchange-specific pairs, fee schedule, tier info
"""

import json
import pytest

from freqtrade_bridge.export_params import ExportParams


class TestReadSweepSummary:
    """export_params reads sweep_summary JSON."""

    def test_loads_sweep_summary_from_file(self, sample_sweep_summary_json):
        exporter = ExportParams()
        data = exporter.load_sweep_summary(sample_sweep_summary_json)
        assert "s11" in data
        assert "s09" in data

    def test_missing_file_raises(self):
        exporter = ExportParams()
        with pytest.raises(FileNotFoundError):
            exporter.load_sweep_summary("/nonexistent/sweep_summary.json")


class TestV3DualGateFilter:
    """Only tokens passing V3 dual gate are whitelisted."""

    def test_only_v3_pass_tokens_included(self, sample_sweep_summary):
        exporter = ExportParams()
        whitelist = exporter.filter_validated(sample_sweep_summary, strategy="s11")
        tokens = [entry["token"] for entry in whitelist]
        assert "BTC/USDT" in tokens
        assert "ETH/USDT" in tokens
        assert "SOL/USDT" not in tokens  # v3_pass=False

    def test_s09_filters_correctly(self, sample_sweep_summary):
        exporter = ExportParams()
        whitelist = exporter.filter_validated(sample_sweep_summary, strategy="s09")
        tokens = [entry["token"] for entry in whitelist]
        assert "BTC/USDT" in tokens
        assert "DOGE/USDT" not in tokens  # v3_pass=False

    def test_empty_strategy_returns_empty(self, sample_sweep_summary):
        exporter = ExportParams()
        whitelist = exporter.filter_validated(sample_sweep_summary, strategy="s99")
        assert whitelist == []


class TestOutputContents:
    """Output contains strategy params, exchange-specific pairs, fee schedule, tier info."""

    def test_output_contains_strategy_params(self, sample_sweep_summary):
        exporter = ExportParams()
        result = exporter.export(sample_sweep_summary, strategy="s11", exchange="binance")
        assert "params" in result
        # BTC/USDT should have fast_ema and slow_ema
        btc_params = None
        for entry in result["tokens"]:
            if entry["token"] == "BTC/USDT":
                btc_params = entry["params"]
        assert btc_params is not None
        assert "fast_ema" in btc_params

    def test_output_contains_exchange_pairs(self, sample_sweep_summary):
        exporter = ExportParams()
        result = exporter.export(sample_sweep_summary, strategy="s11", exchange="binance")
        assert "pairs" in result
        assert isinstance(result["pairs"], list)
        assert len(result["pairs"]) > 0

    def test_output_contains_fee_schedule(self, sample_sweep_summary):
        exporter = ExportParams()
        result = exporter.export(sample_sweep_summary, strategy="s11", exchange="binance")
        assert "fee_schedule" in result
        assert "maker" in result["fee_schedule"]
        assert "taker" in result["fee_schedule"]

    def test_output_contains_tier_info(self, sample_sweep_summary):
        exporter = ExportParams()
        result = exporter.export(sample_sweep_summary, strategy="s11", exchange="binance")
        # Each token entry should have tier info
        for entry in result["tokens"]:
            assert "tier" in entry

    def test_binance_fee_schedule_correct(self, sample_sweep_summary):
        exporter = ExportParams()
        result = exporter.export(sample_sweep_summary, strategy="s11", exchange="binance")
        assert result["fee_schedule"]["maker"] == pytest.approx(0.0010)
        assert result["fee_schedule"]["taker"] == pytest.approx(0.0010)

    def test_kraken_fee_schedule_correct(self, sample_sweep_summary):
        exporter = ExportParams()
        result = exporter.export(sample_sweep_summary, strategy="s09", exchange="kraken")
        assert result["fee_schedule"]["maker"] == pytest.approx(0.0025)
        assert result["fee_schedule"]["taker"] == pytest.approx(0.0040)


class TestExportToFile:
    """Export writes output to JSON file."""

    def test_export_to_file(self, sample_sweep_summary, tmp_path):
        exporter = ExportParams()
        out_path = str(tmp_path / "export_s11_binance.json")
        exporter.export_to_file(
            sample_sweep_summary,
            strategy="s11",
            exchange="binance",
            output_path=out_path,
        )
        with open(out_path, "r") as f:
            data = json.load(f)
        assert "tokens" in data
        assert "fee_schedule" in data
