"""Tests for Reproducibility Logging — AC20, AC21.

Tests that backtest runs log reproducibility manifests with the required
fields, and that identical configurations produce identical config hashes.
"""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from v4.walk_forward import BacktestManifest, log_manifest, compute_config_hash


class TestManifestFields:
    """AC20: Manifest contains required fields."""

    def test_ac20_manifest_has_token_data_ranges(self):
        """AC20: BacktestManifest contains token data ranges."""
        manifest = BacktestManifest(
            token_data_ranges={
                "BTC": {"first_bar": "2025-01-01T00:00:00", "last_bar": "2026-02-28T00:00:00"},
                "ETH": {"first_bar": "2025-01-15T00:00:00", "last_bar": "2026-02-28T00:00:00"},
            },
            end_date_used="2026-02-28",
            config_hash="abc123def456",
            run_timestamp="2026-03-01T10:00:00",
        )
        assert "BTC" in manifest.token_data_ranges
        assert "ETH" in manifest.token_data_ranges
        assert manifest.token_data_ranges["BTC"]["first_bar"] == "2025-01-01T00:00:00"
        assert manifest.token_data_ranges["BTC"]["last_bar"] == "2026-02-28T00:00:00"

    def test_ac20_manifest_has_end_date_used(self):
        """AC20: BacktestManifest contains end_date_used."""
        manifest = BacktestManifest(
            token_data_ranges={},
            end_date_used="2026-02-28",
            config_hash="abc",
            run_timestamp="2026-03-01T10:00:00",
        )
        assert manifest.end_date_used == "2026-02-28"

    def test_ac20_manifest_has_config_hash(self):
        """AC20: BacktestManifest contains config_hash."""
        manifest = BacktestManifest(
            token_data_ranges={},
            end_date_used="2026-02-28",
            config_hash="sha256_of_config",
            run_timestamp="2026-03-01T10:00:00",
        )
        assert manifest.config_hash == "sha256_of_config"

    def test_ac20_manifest_has_run_timestamp(self):
        """AC20: BacktestManifest contains run_timestamp."""
        manifest = BacktestManifest(
            token_data_ranges={},
            end_date_used="2026-02-28",
            config_hash="abc",
            run_timestamp="2026-03-01T10:00:00",
        )
        assert manifest.run_timestamp == "2026-03-01T10:00:00"


class TestManifestWriteToJsonl:
    """AC20: Manifest is written to JSONL file."""

    def test_ac20_manifest_written_as_jsonl(self, tmp_path):
        """AC20: log_manifest() writes a single JSON line to the file."""
        manifest = BacktestManifest(
            token_data_ranges={
                "BTC": {"first_bar": "2025-01-01", "last_bar": "2026-02-28"},
            },
            end_date_used="2026-02-28",
            config_hash="deadbeef1234",
            run_timestamp="2026-03-01T10:00:00",
        )
        outfile = tmp_path / "backtest_manifest.jsonl"
        log_manifest(manifest, str(outfile))

        content = outfile.read_text().strip()
        data = json.loads(content)

        assert "token_data_ranges" in data
        assert "end_date_used" in data
        assert "config_hash" in data
        assert "run_timestamp" in data
        assert data["end_date_used"] == "2026-02-28"

    def test_ac20_manifest_all_fields_serialized(self, tmp_path):
        """AC20: All manifest fields are present in the serialized JSON."""
        manifest = BacktestManifest(
            token_data_ranges={
                "BTC": {"first_bar": "2025-01-01", "last_bar": "2026-02-28"},
                "ETH": {"first_bar": "2025-02-01", "last_bar": "2026-02-28"},
            },
            end_date_used="2026-02-28",
            config_hash="abc123",
            run_timestamp="2026-03-01T12:00:00",
        )
        outfile = tmp_path / "manifest.jsonl"
        log_manifest(manifest, str(outfile))

        data = json.loads(outfile.read_text().strip())
        assert len(data["token_data_ranges"]) == 2
        assert "BTC" in data["token_data_ranges"]
        assert "ETH" in data["token_data_ranges"]


class TestConfigHashDeterminism:
    """AC21: Identical configs produce identical config_hash."""

    def test_ac21_identical_configs_same_hash(self):
        """AC21: Two identical PortfolioConfig instances produce the same config_hash."""
        from v4.config import PortfolioConfig

        config1 = PortfolioConfig(
            capital=200_000,
            purge_bars=168,
            seed=42,
        )
        config2 = PortfolioConfig(
            capital=200_000,
            purge_bars=168,
            seed=42,
        )

        hash1 = compute_config_hash(config1)
        hash2 = compute_config_hash(config2)

        assert hash1 == hash2, (
            f"AC21: Identical configs should produce identical hashes: "
            f"{hash1} != {hash2}"
        )

    def test_ac21_different_configs_different_hash(self):
        """AC21: Different configs produce different config_hash values."""
        from v4.config import PortfolioConfig

        config1 = PortfolioConfig(capital=200_000, purge_bars=168)
        config2 = PortfolioConfig(capital=100_000, purge_bars=168)

        hash1 = compute_config_hash(config1)
        hash2 = compute_config_hash(config2)

        assert hash1 != hash2, (
            f"AC21: Different configs should produce different hashes: "
            f"{hash1} == {hash2}"
        )

    def test_ac21_hash_is_hex_string(self):
        """AC21: config_hash is a hex digest string (SHA-256 based)."""
        from v4.config import PortfolioConfig

        config = PortfolioConfig()
        h = compute_config_hash(config)

        # Should be a non-empty hex string (length depends on truncation choice)
        assert len(h) >= 12, f"Config hash should be at least 12 hex chars, got {len(h)}"
        assert all(c in "0123456789abcdef" for c in h), (
            f"Config hash should contain only hex characters, got {h}"
        )
