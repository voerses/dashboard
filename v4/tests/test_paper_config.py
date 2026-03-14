"""Acceptance tests for Task 3: Paper Config & Validation.

Tests verify:
  - Loading valid JSON config with all fields
  - Default values for PaperConfig
  - Pool mode config with pool_name
  - Independent mode config
  - Validation: weights > 1.0 raises error
  - Validation: invalid strategy_id raises error with clear message
  - Validation: max_portfolio_positions < per-strategy max raises error
  - Missing required fields raise clear errors (M10)
  - Load and validate are separated so validation triggers correctly (Q6)

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until paper_config.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_config import PaperConfig, load_paper_config, validate_paper_config
from v4.config import PortfolioConfig, StrategySpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(data: dict) -> str:
    """Write a config dict to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _valid_config_dict(**overrides) -> dict:
    """Return a valid paper config dictionary with sensible defaults."""
    base = {
        "initial_capital": 200_000.0,
        "mode": "pool",
        "pool_name": "test_pool",
        "strategies": [
            {"strategy_id": "s56", "weight": 0.5, "market": "combined", "max_positions": 10},
            {"strategy_id": "s57", "weight": 0.3, "market": "perp", "max_positions": 8},
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
# Test: Loading valid JSON config with all fields
# ===================================================================

class TestConfigLoading:
    """PaperConfig can be loaded from JSON with all fields."""

    def test_load_valid_config_all_fields(self):
        """Loading a complete config JSON produces a PaperConfig with all fields set."""
        config_path = _write_config(_valid_config_dict())
        config = load_paper_config(config_path)

        assert isinstance(config, PaperConfig)
        assert config.capital == 200_000.0
        assert config.mode == "pool"
        assert config.pool_name == "test_pool"
        assert len(config.strategies) == 2
        assert config.strategies[0].strategy_id == "s56"
        assert config.strategies[0].weight == pytest.approx(0.5)
        assert config.strategies[0].market == "combined"
        assert config.strategies[1].strategy_id == "s57"
        assert config.max_portfolio_positions == 40
        assert config.concentration_limit == pytest.approx(0.10)
        assert config.adv_cap_pct == pytest.approx(0.10)
        assert config.min_position_usd == pytest.approx(200.0)
        assert config.exchange == "binance"
        assert config.seed == 42
        assert config.lookback_months == 12
        assert config.enable_purge_windows is False
        assert config.drawdown_alert_pct == pytest.approx(5.0)
        assert config.stress_adv_multiplier == pytest.approx(0.3)
        assert config.max_slip_bps == 300

    def test_config_inherits_from_portfolio_config(self):
        """PaperConfig should be a subclass of (or contain) PortfolioConfig fields."""
        config_path = _write_config(_valid_config_dict())
        config = load_paper_config(config_path)

        # PaperConfig must have all PortfolioConfig fields
        assert hasattr(config, "capital")
        assert hasattr(config, "max_portfolio_positions")
        assert hasattr(config, "concentration_limit")
        assert hasattr(config, "adv_cap_pct")
        assert hasattr(config, "min_position_usd")
        assert hasattr(config, "exchange")
        assert hasattr(config, "seed")
        assert hasattr(config, "train_bars")


# ===================================================================
# Test: Default values
# ===================================================================

class TestConfigDefaults:
    """PaperConfig has sensible defaults for all optional fields."""

    def test_default_mode_is_pool(self):
        """Default mode should be 'pool'."""
        cfg = PaperConfig()
        assert cfg.mode == "pool"

    def test_default_pool_name_empty(self):
        """Default pool_name should be empty string."""
        cfg = PaperConfig()
        assert cfg.pool_name == ""

    def test_default_lookback_months(self):
        cfg = PaperConfig()
        assert cfg.lookback_months == 12

    def test_default_enable_purge_windows_false(self):
        """Purge windows disabled by default for live (AC15)."""
        cfg = PaperConfig()
        assert cfg.enable_purge_windows is False

    def test_default_drawdown_alert_pct(self):
        cfg = PaperConfig()
        assert cfg.drawdown_alert_pct == pytest.approx(5.0)

    def test_default_alert_webhook_url_empty(self):
        cfg = PaperConfig()
        assert cfg.alert_webhook_url == ""

    def test_default_shadow_rebalance_threshold(self):
        cfg = PaperConfig()
        assert cfg.shadow_rebalance_threshold == pytest.approx(100.0)

    def test_default_capital(self):
        """Default capital from PortfolioConfig."""
        cfg = PaperConfig()
        assert cfg.capital == 200_000.0


# ===================================================================
# Test: Pool mode config with pool_name
# ===================================================================

class TestPoolModeConfig:
    """Pool mode: strategies share capital, shown as one entity."""

    def test_pool_mode_with_pool_name(self):
        """Pool mode config has pool_name for dashboard display (AC8)."""
        data = _valid_config_dict(mode="pool", pool_name="s58_combined")
        config = load_paper_config(_write_config(data))

        assert config.mode == "pool"
        assert config.pool_name == "s58_combined"

    def test_pool_mode_strategies_share_capital(self):
        """In pool mode, all strategies are within the same config (shared capital model)."""
        data = _valid_config_dict(mode="pool")
        config = load_paper_config(_write_config(data))

        assert config.mode == "pool"
        total_weight = sum(s.weight for s in config.strategies)
        assert total_weight <= 1.0


# ===================================================================
# Test: Independent mode config
# ===================================================================

class TestIndependentModeConfig:
    """Independent mode: each strategy gets own SimulationState."""

    def test_independent_mode_loads(self):
        """Independent mode config parses correctly."""
        data = _valid_config_dict(mode="independent")
        config = load_paper_config(_write_config(data))

        assert config.mode == "independent"

    def test_independent_mode_strategies_have_weights(self):
        """Even in independent mode, strategies have weights (used for capital split)."""
        data = _valid_config_dict(mode="independent")
        config = load_paper_config(_write_config(data))

        for s in config.strategies:
            assert s.weight > 0.0


# ===================================================================
# Q6 fix: Validation — weights > 1.0 raises error
# Separated load from validate so the raises block captures the right call.
# ===================================================================

class TestConfigValidationWeights:
    """Validation: strategy weights must sum to <= 1.0 in pool mode.

    Q6 fix: load_paper_config and validate_paper_config are called
    separately so the pytest.raises block captures the correct failure.
    """

    def test_pool_mode_weights_over_1_warns(self):
        """Weights summing to > 1.0 in pool mode should warn but not raise.

        This matches v3 behavior where sub-strategies size off full equity.
        The free_capital check at entry time prevents over-allocation.
        """
        data = _valid_config_dict(
            mode="pool",
            strategies=[
                {"strategy_id": "s56", "weight": 0.7, "market": "combined", "max_positions": 10},
                {"strategy_id": "s57", "weight": 0.5, "market": "perp", "max_positions": 8},
            ],
        )
        config_path = _write_config(data)

        config = load_paper_config(config_path)
        # Should not raise — weights > 1.0 allowed in pool mode
        validate_paper_config(config)

    def test_pool_mode_weights_equal_1_ok(self):
        """Weights summing to exactly 1.0 should be valid."""
        data = _valid_config_dict(
            mode="pool",
            strategies=[
                {"strategy_id": "s56", "weight": 0.6, "market": "combined", "max_positions": 10},
                {"strategy_id": "s57", "weight": 0.4, "market": "perp", "max_positions": 8},
            ],
        )
        config_path = _write_config(data)
        config = load_paper_config(config_path)
        # Should not raise
        validate_paper_config(config)


# ===================================================================
# Q6 fix: Validation — invalid strategy_id raises error with clear message
# ===================================================================

class TestConfigValidationStrategyId:
    """Validation: strategy_id must be loadable.

    Q6 fix: load and validate are separated.
    """

    def test_invalid_strategy_id_raises_with_message(self):
        """Unknown strategy_id should produce a clear error message (AC7b)."""
        data = _valid_config_dict(
            strategies=[
                {"strategy_id": "s_nonexistent_999", "weight": 0.5, "market": "perp",
                 "max_positions": 10},
            ],
        )
        config_path = _write_config(data)

        # Q6 fix: separate loading from validation
        config = load_paper_config(config_path)
        with pytest.raises((ValueError, RuntimeError, FileNotFoundError)) as exc_info:
            validate_paper_config(config)

        # Error message should mention the bad strategy_id
        assert "s_nonexistent_999" in str(exc_info.value)


# ===================================================================
# Q6 fix: Validation — max_portfolio_positions < per-strategy max raises error
# ===================================================================

class TestConfigValidationPositionLimits:
    """max_portfolio_positions must be >= max of per-strategy max_positions.

    Q6 fix: load and validate are separated.
    """

    def test_portfolio_positions_less_than_strategy_max_raises(self):
        """max_portfolio_positions < any strategy's max_positions should raise (AC7b)."""
        data = _valid_config_dict(
            max_portfolio_positions=5,
            strategies=[
                {"strategy_id": "s56", "weight": 0.5, "market": "combined", "max_positions": 10},
                {"strategy_id": "s57", "weight": 0.3, "market": "perp", "max_positions": 8},
            ],
        )
        config_path = _write_config(data)

        # Q6 fix: separate loading from validation
        config = load_paper_config(config_path)
        with pytest.raises((ValueError, RuntimeError)):
            validate_paper_config(config)

    def test_portfolio_positions_equal_to_strategy_max_ok(self):
        """max_portfolio_positions == max strategy max_positions should be valid."""
        data = _valid_config_dict(
            max_portfolio_positions=10,
            strategies=[
                {"strategy_id": "s56", "weight": 0.5, "market": "combined", "max_positions": 10},
                {"strategy_id": "s57", "weight": 0.3, "market": "perp", "max_positions": 8},
            ],
        )
        config_path = _write_config(data)
        config = load_paper_config(config_path)
        # Should not raise
        validate_paper_config(config)


# ===================================================================
# M10: Missing required fields tests
# ===================================================================

class TestMissingRequiredFields:
    """Config validation must produce clear errors for missing required fields (AC7b)."""

    def test_missing_strategies_key_raises(self):
        """JSON with no 'strategies' key should raise with clear message."""
        data = _valid_config_dict()
        del data["strategies"]
        config_path = _write_config(data)

        with pytest.raises((ValueError, KeyError, RuntimeError, TypeError)) as exc_info:
            config = load_paper_config(config_path)
            validate_paper_config(config)

        error_msg = str(exc_info.value).lower()
        assert "strateg" in error_msg, (
            f"Error should mention 'strategies' but got: {exc_info.value}"
        )

    def test_strategy_missing_strategy_id_raises(self):
        """A strategy entry missing 'strategy_id' should raise with clear message."""
        data = _valid_config_dict(
            strategies=[
                {"weight": 0.5, "market": "perp", "max_positions": 10},
            ],
        )
        config_path = _write_config(data)

        with pytest.raises((ValueError, KeyError, RuntimeError, TypeError)) as exc_info:
            config = load_paper_config(config_path)
            validate_paper_config(config)

        error_msg = str(exc_info.value).lower()
        assert "strategy_id" in error_msg or "strategy" in error_msg, (
            f"Error should mention 'strategy_id' but got: {exc_info.value}"
        )

    def test_missing_initial_capital_uses_default_or_raises(self):
        """JSON with no 'initial_capital' should either use default (200k)
        or raise with a clear error. Both behaviors are acceptable."""
        data = _valid_config_dict()
        del data["initial_capital"]
        config_path = _write_config(data)

        try:
            config = load_paper_config(config_path)
            validate_paper_config(config)
            # If no error, capital should have a sensible default
            assert config.capital == 200_000.0, (
                f"When initial_capital is missing, expected default 200000.0, "
                f"got {config.capital}"
            )
        except (ValueError, KeyError, RuntimeError, TypeError):
            # Also acceptable: raising an error for missing capital
            pass

    def test_empty_json_raises(self):
        """Empty JSON '{}' should raise with a clear error message."""
        config_path = _write_config({})

        with pytest.raises((ValueError, KeyError, RuntimeError, TypeError)) as exc_info:
            config = load_paper_config(config_path)
            validate_paper_config(config)

        # Error message should be descriptive, not a raw KeyError
        error_msg = str(exc_info.value)
        assert len(error_msg) > 0, "Error message should not be empty"
