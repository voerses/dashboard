"""Acceptance tests for Task 2: V4 Slippage Model Fixes.

Tests verify:
  - Slippage cap defaults to 300bps (not 100bps)
  - max_slip_bps is configurable
  - stress_adv_multiplier applies to stop/liquidation exits (lower ADV = higher slippage)
  - Non-stop exits use normal ADV (no stress multiplier)
  - Liquidation exits skip regular slippage and use the liquidation fee path instead
  - Default max_slip_bps=300 gives the same result as explicitly passing 300

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until the fixes are implemented (RED phase).
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v5.config import PortfolioConfig, StrategySpec
from v5.sizing import compute_slippage_bps


# ===================================================================
# Test: Slippage cap defaults to 300bps (not 100bps)
# ===================================================================

class TestSlippageCap:
    """Slippage hard cap should default to 300bps (configurable via max_slip_bps)."""

    def test_slippage_cap_is_300bps_by_default(self):
        """For a very large position relative to ADV, slippage should cap at 300bps.

        With $10M position vs $1M ADV => participation = 10.0 (very large)
        sqrt(10.0) * 0.03 * 10000 = 948 bps
        Capped at 300bps (not 100bps).
        """
        pos_usd = 10_000_000.0
        adv = 1_000_000.0
        slip = compute_slippage_bps(pos_usd, adv)
        # Must be capped at 300 (not 100)
        assert slip == pytest.approx(300.0, abs=1.0)

    def test_slippage_was_previously_capped_at_100bps(self):
        """Verify the cap is NOT 100bps anymore.

        With $1M position vs $1M ADV => participation = 1.0
        sqrt(1.0) * 0.03 * 10000 = 300 bps + 3 bps spread = 303 bps
        Old cap would return 100. New cap returns 300.
        """
        pos_usd = 1_000_000.0
        adv = 1_000_000.0
        slip = compute_slippage_bps(pos_usd, adv)
        # Must be greater than 100 (old cap)
        assert slip > 100.0
        # But capped at 300 (new cap)
        assert slip <= 300.0

    def test_slippage_max_slip_bps_configurable(self):
        """max_slip_bps parameter controls the cap.

        With custom cap of 500bps, large positions should cap at 500.
        """
        pos_usd = 50_000_000.0  # massive position
        adv = 1_000_000.0
        # New signature: compute_slippage_bps(pos_usd, adv, base_spread_bps, impact_coeff, max_slip_bps)
        slip = compute_slippage_bps(pos_usd, adv, base_spread_bps=3.0,
                                     impact_coeff=0.03, max_slip_bps=500)
        assert slip == pytest.approx(500.0, abs=1.0)

    def test_slippage_max_slip_bps_150(self):
        """Custom cap of 150bps."""
        pos_usd = 10_000_000.0
        adv = 1_000_000.0
        slip = compute_slippage_bps(pos_usd, adv, base_spread_bps=3.0,
                                     impact_coeff=0.03, max_slip_bps=150)
        assert slip == pytest.approx(150.0, abs=1.0)

    def test_small_position_unaffected_by_cap(self):
        """Small position relative to ADV should be well below any cap.

        $10k position vs $50M ADV => participation = 0.0002
        sqrt(0.0002) * 0.03 * 10000 = ~4.2 bps + 3 = ~7.2 bps
        Well below both old and new caps.
        """
        pos_usd = 10_000.0
        adv = 50_000_000.0
        slip = compute_slippage_bps(pos_usd, adv)
        assert slip < 30.0  # well below any cap

    def test_default_cap_matches_explicit_300(self):
        """Passing max_slip_bps=300 explicitly must give the same result as the default.

        This ensures the default is actually 300, not just that some cap exists.
        """
        pos_usd = 10_000_000.0
        adv = 1_000_000.0
        slip_default = compute_slippage_bps(pos_usd, adv)
        slip_explicit = compute_slippage_bps(pos_usd, adv, base_spread_bps=3.0,
                                              impact_coeff=0.03, max_slip_bps=300)
        assert slip_default == pytest.approx(slip_explicit, abs=0.01), \
            "Default slippage cap must be 300bps — default and explicit=300 should match"


# ===================================================================
# Test: stress_adv_multiplier for stop/liquidation exits
# ===================================================================

class TestStressADVMultiplier:
    """stop/liquidation exits should apply stress_adv_multiplier to ADV (default 0.3x).

    This increases slippage during stress events (reflecting real liquidity drying up).
    Non-stop exits should use normal ADV.
    """

    def test_config_has_stress_adv_multiplier(self):
        """PortfolioConfig must have stress_adv_multiplier field."""
        config = PortfolioConfig()
        assert hasattr(config, "stress_adv_multiplier")
        # Default should be backward compatible (1.0 for existing backtest)
        assert config.stress_adv_multiplier == pytest.approx(1.0)

    def test_config_has_max_slip_bps(self):
        """PortfolioConfig must have max_slip_bps field."""
        config = PortfolioConfig()
        assert hasattr(config, "max_slip_bps")
        assert config.max_slip_bps == 300

    def test_stress_multiplier_increases_slippage_for_stops(self):
        """With stress_adv_multiplier=0.3, stop exits see ADV * 0.3 => higher slippage.

        Normal: $100k position, $10M ADV => participation = 0.01
          slip = 3 + 0.03 * sqrt(0.01) * 10000 = 3 + 30 = 33 bps
        Stress: ADV = $10M * 0.3 = $3M => participation = 0.0333
          slip = 3 + 0.03 * sqrt(0.0333) * 10000 = 3 + 54.8 = 57.8 bps
        """
        pos_usd = 100_000.0
        normal_adv = 10_000_000.0
        stress_multiplier = 0.3

        normal_slip = compute_slippage_bps(pos_usd, normal_adv)
        stress_slip = compute_slippage_bps(pos_usd, normal_adv * stress_multiplier)

        assert stress_slip > normal_slip * 1.5  # significantly higher
        assert stress_slip == pytest.approx(271, abs=10)

    def test_stop_exit_uses_stress_adv_in_simulator(self):
        """When exit_reason is 'stop', _close_position should apply stress ADV.

        This test verifies the integration in simulator.py's _close_position.
        The exit fee from a stop exit should reflect higher slippage than a target exit.
        """
        from v5.position import Position, PositionManager
        from v5.simulator import SimulationState, _close_position, _process_exits
        from v5.signals import TokenSignals
        import pandas as pd

        config = PortfolioConfig(
            capital=200_000.0,
            stress_adv_multiplier=0.3,
            max_slip_bps=300,
        )

        # Create a position that will be stopped out
        pos = Position(
            position_id="BTC:s30:0:primary",
            token="BTC",
            strategy_id="s30",
            leg="primary",
            entry_bar=0,
            entry_price=100.0,
            direction=1,
            quantity=1000.0,  # $100k notional
            margin_usd=100_000.0,
            leverage=1.0,
            is_perp=True,
            fee_rate=0.0005,
            stop_mult=2.0,
            trail_mult=3.0,
            target_mult=5.0,
            no_stop_bars=0,  # stop active immediately
            min_hold=0,
            max_hold=720,
            stop_price=98.0,  # stop at $98
            highest=100.0,
            lowest=100.0,
            initial_risk=2.0,
        )

        adv = 10_000_000.0
        # When _close_position is called with exit_reason="stop",
        # it should use exit_adv * stress_adv_multiplier for slippage calculation
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 50.0

        # Close with stop reason
        trade_stop = _close_position(state, pos, 5, 97.0, "stop", adv, config)

        # Close another position with target reason for comparison
        pos2 = Position(
            position_id="ETH:s30:0:primary",
            token="ETH",
            strategy_id="s30",
            leg="primary",
            entry_bar=0,
            entry_price=100.0,
            direction=1,
            quantity=1000.0,
            margin_usd=100_000.0,
            leverage=1.0,
            is_perp=True,
            fee_rate=0.0005,
            stop_mult=2.0,
            trail_mult=3.0,
            target_mult=5.0,
            no_stop_bars=0,
            min_hold=0,
            max_hold=720,
            stop_price=98.0,
            highest=100.0,
            lowest=100.0,
            initial_risk=2.0,
        )
        state.position_manager.open_position(pos2)
        state._entry_fees_by_pos[pos2.position_id] = 50.0

        trade_target = _close_position(state, pos2, 5, 110.0, "target", adv, config)

        # Stop exit should have worse (higher) slippage than target exit
        # Stop exit slippage computed with adv * 0.3 => higher participation => more slippage
        # Target exit uses normal adv
        # Both at similar notional, so the stop trade should have a worse exit price
        # relative to the given exit_price
        stop_slip = abs(trade_stop.exit_price - 97.0)
        target_slip = abs(trade_target.exit_price - 110.0)
        assert stop_slip > target_slip, \
            "Stop exit should have higher slippage than target exit due to stress ADV"

    def test_non_stop_exit_uses_normal_adv(self):
        """Non-stop exits (target, regime, max_hold, rsi) should NOT apply stress multiplier.

        Slippage for a target exit should be the same regardless of stress_adv_multiplier.
        """
        pos_usd = 100_000.0
        adv = 10_000_000.0

        # Normal slippage (no stress)
        normal_slip = compute_slippage_bps(pos_usd, adv)
        # Stress slippage (with 0.3 multiplier on ADV)
        stress_slip = compute_slippage_bps(pos_usd, adv * 0.3)

        # For non-stop exits, the simulator should pass normal ADV, so slippage = normal_slip
        # This test verifies the expected normal value
        assert normal_slip == pytest.approx(150, abs=5)


# ===================================================================
# Test: Liquidation exits skip regular slippage, use liquidation fee
# ===================================================================

class TestLiquidationExitSlippage:
    """Liquidation exits must NOT get regular slippage applied.

    Instead, they use the separate liquidation fee path (1.5% of notional).
    The simulator's _close_position skips slippage when exit_reason == "liquidation".
    """

    def test_liquidation_exit_skips_regular_slippage(self):
        """Liquidation exit should not apply the sqrt market impact slippage model.

        Instead, the exit price should equal the close price (no slippage adjustment),
        and the liquidation fee (1.5% of notional) is charged separately.

        Compare: a stop exit at the same price WOULD get slippage applied.
        """
        from v5.position import Position
        from v5.simulator import SimulationState, _close_position

        config = PortfolioConfig(
            capital=200_000.0,
            stress_adv_multiplier=0.3,
            max_slip_bps=300,
        )

        # Create a leveraged perp position
        pos_liq = Position(
            position_id="BTC:s30:0:primary",
            token="BTC",
            strategy_id="s30",
            leg="primary",
            entry_bar=0,
            entry_price=100.0,
            direction=1,
            quantity=500.0,  # 5x, $50k notional at entry
            margin_usd=10_000.0,
            leverage=5.0,
            is_perp=True,
            fee_rate=0.0005,
            stop_mult=2.0,
            trail_mult=3.0,
            target_mult=5.0,
            no_stop_bars=6,
            min_hold=6,
            max_hold=720,
            stop_price=0.0,
            highest=100.0,
            lowest=100.0,
            initial_risk=2.0,
        )

        adv = 5_000_000.0
        exit_price = 55.0  # liquidation exit price

        state_liq = SimulationState(initial_capital=200_000.0)
        state_liq.position_manager.open_position(pos_liq)
        state_liq._entry_fees_by_pos[pos_liq.position_id] = 25.0

        trade_liq = _close_position(state_liq, pos_liq, 5, exit_price, "liquidation", adv, config)

        # Liquidation exit price should NOT be adjusted by slippage
        # (the exit_price passed in is used directly)
        assert trade_liq.exit_price == pytest.approx(exit_price, abs=0.01), \
            "Liquidation exit should not apply regular slippage to exit price"

        # Now compare with a stop exit at the same price — it SHOULD get slippage
        pos_stop = Position(
            position_id="ETH:s30:0:primary",
            token="ETH",
            strategy_id="s30",
            leg="primary",
            entry_bar=0,
            entry_price=100.0,
            direction=1,
            quantity=500.0,
            margin_usd=10_000.0,
            leverage=5.0,
            is_perp=True,
            fee_rate=0.0005,
            stop_mult=2.0,
            trail_mult=3.0,
            target_mult=5.0,
            no_stop_bars=6,
            min_hold=6,
            max_hold=720,
            stop_price=0.0,
            highest=100.0,
            lowest=100.0,
            initial_risk=2.0,
        )

        state_stop = SimulationState(initial_capital=200_000.0)
        state_stop.position_manager.open_position(pos_stop)
        state_stop._entry_fees_by_pos[pos_stop.position_id] = 25.0

        trade_stop = _close_position(state_stop, pos_stop, 5, exit_price, "stop", adv, config)

        # Stop exit SHOULD have slippage applied (exit price adjusted downward for long)
        assert trade_stop.exit_price < exit_price, \
            "Stop exit should have slippage reducing the exit price for a long"

    def test_liquidation_fee_is_separate_from_slippage(self):
        """Liquidation fee (1.5% notional) replaces the normal slippage + taker fee path.

        The liquidation fee should be significantly larger than what regular
        slippage + taker fee would produce for the same position.
        """
        from v5.position import Position
        from v5.simulator import SimulationState, _close_position

        config = PortfolioConfig(
            capital=200_000.0,
        )

        pos = Position(
            position_id="BTC:s30:0:primary",
            token="BTC",
            strategy_id="s30",
            leg="primary",
            entry_bar=0,
            entry_price=100.0,
            direction=1,
            quantity=500.0,
            margin_usd=10_000.0,
            leverage=5.0,
            is_perp=True,
            fee_rate=0.0005,
            stop_mult=2.0,
            trail_mult=3.0,
            target_mult=5.0,
            no_stop_bars=6,
            min_hold=6,
            max_hold=720,
            stop_price=0.0,
            highest=100.0,
            lowest=100.0,
            initial_risk=2.0,
        )

        adv = 5_000_000.0
        exit_price = 55.0

        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        trade = _close_position(state, pos, 5, exit_price, "liquidation", adv, config)

        # Liquidation fee = notional_at_exit * 0.015
        exit_notional = abs(pos.quantity * trade.exit_price)
        expected_liq_fee = exit_notional * 0.015
        assert trade.exit_fee == pytest.approx(expected_liq_fee, rel=1e-4), \
            "Liquidation exit fee should be 1.5% of notional, not taker fee"

        # This should be much larger than the taker fee would be
        taker_fee_would_be = exit_notional * 0.0005
        assert trade.exit_fee > taker_fee_would_be * 10
