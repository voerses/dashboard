"""Tests for OOS config changes — AC10, AC17, AC18, AC19.

Tests PortfolioConfig defaults, purge_bars changes, true_walk_forward field,
and config validation rules.
"""
import pytest

from v4.config import PortfolioConfig, StrategySpec
from v4.validation import WalkForwardConfig


class TestPurgeBarsDefault:
    """AC17, AC18: purge_bars default is 168 and remains configurable."""

    def test_ac17_purge_bars_default_is_168(self):
        """AC17: Default purge_bars in PortfolioConfig is 168 (7 days), not 120."""
        cfg = PortfolioConfig()
        assert cfg.purge_bars == 168, (
            f"Default purge_bars should be 168 (7 days), got {cfg.purge_bars}"
        )

    def test_ac18_purge_bars_is_configurable(self):
        """AC18: purge_bars can be set to a custom value per strategy.

        Also confirms the new default is 168. Setting to 100 should work.
        """
        default_cfg = PortfolioConfig()
        assert default_cfg.purge_bars == 168, (
            f"AC18: Default should be 168, got {default_cfg.purge_bars}"
        )
        custom_cfg = PortfolioConfig(purge_bars=100)
        assert custom_cfg.purge_bars == 100

    def test_ac18_purge_bars_can_be_set_to_old_default(self):
        """AC18: purge_bars can be set back to old default (120) if needed.

        Also confirms the new default is 168, not 120.
        """
        default_cfg = PortfolioConfig()
        assert default_cfg.purge_bars != 120, (
            "AC18: Default purge_bars should no longer be 120"
        )
        cfg = PortfolioConfig(purge_bars=120)
        assert cfg.purge_bars == 120

    def test_ac19_purge_config_consistency(self):
        """AC19: WalkForwardConfig.purge_days * 24 == PortfolioConfig.purge_bars.

        The purge in WalkForwardConfig (days) should be consistent with
        PortfolioConfig (bars, where 1 bar = 1 hour). Both should use the
        new default of 7 days / 168 bars.
        """
        wf_cfg = WalkForwardConfig()
        pc_cfg = PortfolioConfig()
        expected_bars = wf_cfg.purge_days * 24
        assert expected_bars == pc_cfg.purge_bars, (
            f"WalkForwardConfig.purge_days={wf_cfg.purge_days} * 24 = {expected_bars} "
            f"should equal PortfolioConfig.purge_bars={pc_cfg.purge_bars}"
        )
        # Explicitly verify the new defaults (7 days / 168 bars)
        assert wf_cfg.purge_days == 7, (
            f"WalkForwardConfig.purge_days should be 7 (was 5), got {wf_cfg.purge_days}"
        )
        assert pc_cfg.purge_bars == 168, (
            f"PortfolioConfig.purge_bars should be 168 (was 120), got {pc_cfg.purge_bars}"
        )


class TestTrueWalkForwardConfig:
    """AC10: true_walk_forward config field and validation."""

    def test_ac10_true_walk_forward_default_is_false(self):
        """AC10: PortfolioConfig().true_walk_forward defaults to False."""
        cfg = PortfolioConfig()
        assert cfg.true_walk_forward is False

    def test_ac10_true_walk_forward_can_be_enabled(self):
        """AC10: true_walk_forward=True works when skip_walk_forward=False."""
        cfg = PortfolioConfig(true_walk_forward=True, skip_walk_forward=False)
        assert cfg.true_walk_forward is True
        assert cfg.skip_walk_forward is False

    def test_ac10_both_true_raises_value_error(self):
        """AC10: Setting both true_walk_forward=True and skip_walk_forward=True raises ValueError."""
        with pytest.raises(ValueError, match="true_walk_forward.*skip_walk_forward"):
            PortfolioConfig(true_walk_forward=True, skip_walk_forward=True)

    def test_ac10_skip_wf_alone_still_works(self):
        """AC10: skip_walk_forward=True alone is fine (backward compat)."""
        cfg = PortfolioConfig(skip_walk_forward=True)
        assert cfg.skip_walk_forward is True
        assert cfg.true_walk_forward is False

    def test_ac10_both_false_is_default(self):
        """AC10: Default state — both flags False — is the normal walk-forward path."""
        cfg = PortfolioConfig()
        assert cfg.true_walk_forward is False
        assert cfg.skip_walk_forward is False
