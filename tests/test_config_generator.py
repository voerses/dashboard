"""AC5: Exchange configs generated per instance.

Tests verify:
- Config generator produces valid JSON with dry_run=true, correct exchange, correct pairs
- Unique DB path per instance
- Unique API port per instance (deterministic: s09_kraken=8091, s11_binance=8112, etc.)
- Instance cap: refuses to generate if 3 instances already running on same exchange
- Fee rates match cost_model (Kraken 0.25%/0.40%, Binance 0.10%/0.10%)
"""

import json
import pytest

from freqtrade_bridge.config_generator import ConfigGenerator


class TestConfigStructure:
    """Generated config is valid JSON with required fields."""

    def test_config_has_dry_run_true(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["dry_run"] is True

    def test_config_has_correct_exchange(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["exchange"]["name"] == "binance"

    def test_config_has_correct_pairs(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s09", exchange="kraken", pairs=sample_exchange_pairs["kraken"]
        )
        assert set(config["exchange"]["pair_whitelist"]) == set(sample_exchange_pairs["kraken"])

    def test_config_is_serializable_json(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        serialized = json.dumps(config)
        assert isinstance(serialized, str)
        roundtrip = json.loads(serialized)
        assert roundtrip == config


class TestUniquePaths:
    """Each instance gets a unique DB path."""

    def test_unique_db_path_per_instance(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config_a = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        config_b = gen.generate(
            strategy="s09", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config_a["db_url"] != config_b["db_url"]

    def test_db_path_contains_instance_id(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert "s11_binance" in config["db_url"]


class TestDeterministicApiPort:
    """Unique API port per instance, deterministic mapping."""

    def test_s09_kraken_port_is_8091(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s09", exchange="kraken", pairs=sample_exchange_pairs["kraken"]
        )
        assert config["api_server"]["listen_port"] == 8091

    def test_s11_binance_port_is_8112(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["api_server"]["listen_port"] == 8112

    def test_different_instances_get_different_ports(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config_a = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        config_b = gen.generate(
            strategy="s09", exchange="kraken", pairs=sample_exchange_pairs["kraken"]
        )
        assert config_a["api_server"]["listen_port"] != config_b["api_server"]["listen_port"]


class TestInstanceCap:
    """Refuses to generate if 3 instances already running on same exchange."""

    def test_refuses_fourth_instance_on_same_exchange(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        # Register 3 existing running instances on binance
        gen.register_running_instances([
            {"instance_id": "s11_binance", "exchange": "binance", "status": "running"},
            {"instance_id": "s09_binance", "exchange": "binance", "status": "running"},
            {"instance_id": "s13_binance", "exchange": "binance", "status": "running"},
        ])
        with pytest.raises(Exception) as exc_info:
            gen.generate(
                strategy="s21", exchange="binance", pairs=sample_exchange_pairs["binance"]
            )
        assert "instance" in str(exc_info.value).lower() or "cap" in str(exc_info.value).lower()

    def test_allows_instance_on_different_exchange(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        gen.register_running_instances([
            {"instance_id": "s11_binance", "exchange": "binance", "status": "running"},
            {"instance_id": "s09_binance", "exchange": "binance", "status": "running"},
            {"instance_id": "s13_binance", "exchange": "binance", "status": "running"},
        ])
        # Kraken has 0 running -- should be fine
        config = gen.generate(
            strategy="s09", exchange="kraken", pairs=sample_exchange_pairs["kraken"]
        )
        assert config["exchange"]["name"] == "kraken"

    def test_stopped_instances_do_not_count_toward_cap(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        gen.register_running_instances([
            {"instance_id": "s11_binance", "exchange": "binance", "status": "running"},
            {"instance_id": "s09_binance", "exchange": "binance", "status": "stopped"},
            {"instance_id": "s13_binance", "exchange": "binance", "status": "running"},
        ])
        # Only 2 running -- should be allowed
        config = gen.generate(
            strategy="s21", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["dry_run"] is True


class TestFeeRatesInConfig:
    """Fee rates in config match cost_model defaults."""

    def test_kraken_fees_in_config(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s09", exchange="kraken", pairs=sample_exchange_pairs["kraken"]
        )
        assert config["exchange"]["fee"]["maker"] == pytest.approx(0.0025)
        assert config["exchange"]["fee"]["taker"] == pytest.approx(0.0040)

    def test_binance_fees_in_config(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["exchange"]["fee"]["maker"] == pytest.approx(0.0010)
        assert config["exchange"]["fee"]["taker"] == pytest.approx(0.0010)


class TestConfigDefaults:
    """AC5: Config must have correct wallet size, trade cap, and API server."""

    def test_config_has_200k_wallet(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["dry_run_wallet"] == 200000

    def test_config_has_max_14_trades(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["max_open_trades"] == 14

    def test_config_has_api_server_enabled(self, sample_exchange_pairs):
        gen = ConfigGenerator()
        config = gen.generate(
            strategy="s11", exchange="binance", pairs=sample_exchange_pairs["binance"]
        )
        assert config["api_server"]["enabled"] is True
