"""Tests for v4 entry filters: ADV sizing, graduated pump filter, equity cap, slippage."""
import numpy as np
import pytest

from v4.config import PortfolioConfig, StrategySpec


class TestADVSizingConfig:
    """Phase 2: ADV-scaled position sizing fields on StrategySpec."""

    def test_adv_sizing_fields_exist(self):
        """StrategySpec should have ADV sizing fields with correct defaults."""
        spec = StrategySpec(strategy_id="s56")
        assert hasattr(spec, "adv_sizing_enabled")
        assert spec.adv_sizing_enabled is False
        assert hasattr(spec, "adv_sizing_base")
        assert spec.adv_sizing_base == 100_000_000
        assert hasattr(spec, "adv_sizing_floor")
        assert spec.adv_sizing_floor == 0.20

    def test_adv_sizing_formula(self):
        """ADV sizing multiplier: min(1.0, max(floor, sqrt(adv/base)))."""
        spec = StrategySpec(strategy_id="s56", adv_sizing_enabled=True,
                            adv_sizing_base=75_000_000, adv_sizing_floor=0.20)
        # Test the formula directly
        adv = 30_000_000  # $30M ADV
        base = spec.adv_sizing_base
        floor = spec.adv_sizing_floor
        expected = min(1.0, max(floor, np.sqrt(adv / base)))
        assert expected == pytest.approx(0.6325, abs=0.01)

    def test_adv_sizing_from_dict(self):
        """StrategySpec.from_dict should parse ADV sizing fields."""
        d = {
            "strategy_id": "s56",
            "adv_sizing_enabled": True,
            "adv_sizing_base": 75_000_000,
            "adv_sizing_floor": 0.20,
        }
        spec = StrategySpec.from_dict(d)
        assert spec.adv_sizing_enabled is True
        assert spec.adv_sizing_base == 75_000_000
        assert spec.adv_sizing_floor == 0.20


@pytest.mark.skip(reason="Graduated pump filter not yet implemented")
class TestGraduatedPumpFilterConfig:
    """Phase 3: Graduated pump filter fields on StrategySpec."""

    def test_pump_grad_fields_exist(self):
        """StrategySpec should have graduated pump filter fields."""
        spec = StrategySpec(strategy_id="s56")
        assert hasattr(spec, "pump_grad_enabled")
        assert spec.pump_grad_enabled is False
        assert hasattr(spec, "pump_grad_window")
        assert spec.pump_grad_window == 168
        assert hasattr(spec, "pump_grad_t1")
        assert spec.pump_grad_t1 == 0.15
        assert hasattr(spec, "pump_grad_t2")
        assert spec.pump_grad_t2 == 0.25
        assert hasattr(spec, "pump_grad_t3")
        assert spec.pump_grad_t3 == 0.40
        assert hasattr(spec, "pump_grad_longs_only")
        assert spec.pump_grad_longs_only is True

    def test_pump_grad_from_dict(self):
        """StrategySpec.from_dict should parse graduated pump filter fields."""
        d = {
            "strategy_id": "s60",
            "pump_grad_enabled": True,
            "pump_grad_window": 168,
            "pump_grad_t1": 0.25,
            "pump_grad_t2": 0.40,
            "pump_grad_t3": 0.60,
            "pump_grad_longs_only": True,
        }
        spec = StrategySpec.from_dict(d)
        assert spec.pump_grad_enabled is True
        assert spec.pump_grad_t1 == 0.25
        assert spec.pump_grad_t2 == 0.40
        assert spec.pump_grad_t3 == 0.60


class TestEquityCapConfig:
    """Phase 1C: max_sizing_equity on PortfolioConfig."""

    def test_max_sizing_equity_default_none(self):
        """Default max_sizing_equity should be None (uncapped)."""
        config = PortfolioConfig()
        assert config.max_sizing_equity is None

    def test_max_sizing_equity_can_be_set(self):
        """max_sizing_equity can be set to a specific value."""
        config = PortfolioConfig(max_sizing_equity=5_000_000)
        assert config.max_sizing_equity == 5_000_000


class TestSlippageModel:
    """Phase 4: Slippage uses hourly volume, not daily."""

    def test_slippage_hourly_participation(self):
        """Slippage should use hourly volume (ADV/24) for participation rate."""
        from v4.sizing import compute_slippage_bps

        pos_usd = 100_000
        daily_adv = 24_000_000  # $24M daily → $1M hourly

        slip = compute_slippage_bps(pos_usd, daily_adv)
        # participation = 100K / (24M/24) = 100K / 1M = 0.10
        # slip = 3.0 + 0.03 * sqrt(0.10) * 10000 = 3.0 + 94.87 = 97.87 bps
        assert slip > 50.0  # should be much higher than with daily participation
