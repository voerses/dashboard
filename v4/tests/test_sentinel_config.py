"""Acceptance tests for Task 1: PaperConfig sentinel fields (AC7).

Tests verify:
  - PaperConfig has sentinel_mode field with default "off"
  - PaperConfig has confirmation_tiers field with default tier delays
  - PaperConfig has carry_strategies field with default empty list
  - Validation rejects invalid sentinel_mode values
  - JSON loading round-trips sentinel fields correctly

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until sentinel config fields are implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest
from unittest.mock import patch

from v4.paper_config import PaperConfig, load_paper_config, validate_paper_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(data: dict, tmp_path: Path) -> str:
    """Write a config dict to a temp JSON file and return the path."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))
    return str(config_path)


def _valid_config_dict(**overrides) -> dict:
    """Return a valid paper config dictionary with sensible defaults."""
    base = {
        "initial_capital": 200_000.0,
        "mode": "pool",
        "pool_name": "test_pool",
        "strategies": [
            {"strategy_id": "s56", "weight": 0.5, "market": "combined", "max_positions": 10},
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
        "adv_cap_pct": 0.10,
        "min_position_usd": 200.0,
        "exchange": "binance",
        "seed": 42,
        "lookback_months": 12,
        "enable_purge_windows": False,
        "drawdown_alert_pct": 5.0,
        "stress_adv_multiplier": 0.3,
        "max_slip_bps": 300,
    }
    base.update(overrides)
    return base


# ===================================================================
# Test: sentinel_mode defaults and validation
# ===================================================================

class TestSentinelModeDefaults:
    """PaperConfig sentinel_mode defaults to 'off'."""

    def test_default_sentinel_mode_is_off(self):
        """Default sentinel_mode should be 'off'."""
        cfg = PaperConfig()
        assert cfg.sentinel_mode == "off"

    def test_sentinel_mode_shadow(self, tmp_path):
        """sentinel_mode='shadow' is accepted."""
        data = _valid_config_dict(sentinel_mode="shadow")
        config = load_paper_config(_write_config(data, tmp_path))
        assert config.sentinel_mode == "shadow"

    def test_sentinel_mode_live(self, tmp_path):
        """sentinel_mode='live' is accepted."""
        data = _valid_config_dict(sentinel_mode="live")
        config = load_paper_config(_write_config(data, tmp_path))
        assert config.sentinel_mode == "live"

    def test_sentinel_mode_off_explicit(self, tmp_path):
        """sentinel_mode='off' explicitly set is accepted."""
        data = _valid_config_dict(sentinel_mode="off")
        config = load_paper_config(_write_config(data, tmp_path))
        assert config.sentinel_mode == "off"

    @patch("v4.engine._load_strategy_fn", return_value=lambda ctx: None)
    def test_invalid_sentinel_mode_raises(self, _mock_load, tmp_path):
        """sentinel_mode with invalid value should raise during validation.

        Patches _load_strategy_fn to prevent false-pass from strategy-not-found error.
        The test must fail specifically because of invalid sentinel_mode, not strategy loading.
        """
        data = _valid_config_dict(sentinel_mode="turbo")
        config_path = _write_config(data, tmp_path)
        config = load_paper_config(config_path)
        with pytest.raises((ValueError, RuntimeError), match=r"(?i)sentinel"):
            validate_paper_config(config)

    @patch("v4.engine._load_strategy_fn", return_value=lambda ctx: None)
    def test_invalid_sentinel_mode_empty_string_raises(self, _mock_load, tmp_path):
        """sentinel_mode='' should raise during validation.

        Patches _load_strategy_fn to prevent false-pass from strategy-not-found error.
        """
        data = _valid_config_dict(sentinel_mode="")
        config_path = _write_config(data, tmp_path)
        config = load_paper_config(config_path)
        with pytest.raises((ValueError, RuntimeError), match=r"(?i)sentinel"):
            validate_paper_config(config)


# ===================================================================
# Test: confirmation_tiers defaults and loading
# ===================================================================

class TestConfirmationTiers:
    """PaperConfig confirmation_tiers has correct defaults and loads from JSON."""

    def test_default_confirmation_tiers(self):
        """Default confirmation_tiers: btc_eth=30, top10=60, other=90."""
        cfg = PaperConfig()
        assert cfg.confirmation_tiers == {"btc_eth": 30, "top10": 60, "other": 90}

    def test_confirmation_tiers_from_json(self, tmp_path):
        """confirmation_tiers loaded from JSON config overrides defaults."""
        custom_tiers = {"btc_eth": 15, "top10": 30, "other": 45}
        data = _valid_config_dict(confirmation_tiers=custom_tiers)
        config = load_paper_config(_write_config(data, tmp_path))
        assert config.confirmation_tiers["btc_eth"] == 15
        assert config.confirmation_tiers["top10"] == 30
        assert config.confirmation_tiers["other"] == 45

    def test_confirmation_tiers_partial_override(self, tmp_path):
        """Partial override of confirmation_tiers still contains all keys."""
        custom_tiers = {"btc_eth": 10, "top10": 60, "other": 90}
        data = _valid_config_dict(confirmation_tiers=custom_tiers)
        config = load_paper_config(_write_config(data, tmp_path))
        assert config.confirmation_tiers["btc_eth"] == 10


# ===================================================================
# Test: carry_strategies defaults and loading
# ===================================================================

class TestCarryStrategies:
    """PaperConfig carry_strategies field."""

    def test_default_carry_strategies_empty(self):
        """Default carry_strategies should be an empty list."""
        cfg = PaperConfig()
        assert cfg.carry_strategies == []

    def test_carry_strategies_from_json(self, tmp_path):
        """carry_strategies loaded from JSON config."""
        data = _valid_config_dict(carry_strategies=["s57", "s99"])
        config = load_paper_config(_write_config(data, tmp_path))
        assert config.carry_strategies == ["s57", "s99"]

    def test_carry_strategies_single_entry(self, tmp_path):
        """carry_strategies with a single strategy."""
        data = _valid_config_dict(carry_strategies=["s57"])
        config = load_paper_config(_write_config(data, tmp_path))
        assert len(config.carry_strategies) == 1
        assert "s57" in config.carry_strategies


# ===================================================================
# Test: JSON round-trip with all sentinel fields
# ===================================================================

class TestSentinelFieldsRoundTrip:
    """All sentinel fields survive JSON config loading."""

    def test_all_sentinel_fields_present_after_load(self, tmp_path):
        """Loading a config with all sentinel fields produces correct values."""
        data = _valid_config_dict(
            sentinel_mode="shadow",
            confirmation_tiers={"btc_eth": 20, "top10": 40, "other": 60},
            carry_strategies=["s57"],
        )
        config = load_paper_config(_write_config(data, tmp_path))

        assert config.sentinel_mode == "shadow"
        assert config.confirmation_tiers == {"btc_eth": 20, "top10": 40, "other": 60}
        assert config.carry_strategies == ["s57"]

    def test_missing_sentinel_fields_use_defaults(self, tmp_path):
        """Config JSON without sentinel fields uses defaults."""
        data = _valid_config_dict()
        # Explicitly remove sentinel fields if present
        data.pop("sentinel_mode", None)
        data.pop("confirmation_tiers", None)
        data.pop("carry_strategies", None)
        config = load_paper_config(_write_config(data, tmp_path))

        assert config.sentinel_mode == "off"
        assert config.confirmation_tiers == {"btc_eth": 30, "top10": 60, "other": 90}
        assert config.carry_strategies == []
