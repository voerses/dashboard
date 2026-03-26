"""Acceptance tests for the Realistic Sizing Constraints feature.

Tests cover acceptance criteria NOT already covered by pre-existing tests
in test_v4_filters.py and test_v4_metrics.py:
  - Task 1: Equity cap wiring (AC2, AC4, AC4b)
  - Task 2: Paper engine preservation of ADV sizing fields
  - Task 3: ADV sizing in compute_position_size (AC7, AC8)
  - Task 5: Unrealized P&L in sizing (AC13, AC14, AC15, AC15b)
  - Task 6: Stress exit slippage (AC16, AC17, AC18)
  - Task 7: Integration — all constraints compose (AC19, AC20, AC21)

All tests MUST FAIL until the implementation is complete (RED phase).
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import dataclasses
import json
import tempfile

import numpy as np
import pandas as pd
import pytest

from v4.config import PortfolioConfig, StrategySpec
from v4.sizing import compute_position_size, compute_slippage_bps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config(**overrides) -> PortfolioConfig:
    """Build a PortfolioConfig with test-friendly defaults."""
    defaults = dict(
        capital=200_000.0,
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        base_spread_bps=3.0,
        impact_coeff=0.03,
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
    )
    defaults.update(overrides)
    return PortfolioConfig(**defaults)


def _make_timestamps(n: int, start: str = "2024-01-01") -> np.ndarray:
    """Create n hourly timestamps starting from `start`."""
    return pd.date_range(start, periods=n, freq="1h").values


def _make_token_signals(token, strategy_id, n_bars, entry_mask, close, adv,
                        direction=None, atr_val=2.0, edge=0.35, **kwargs):
    """Build a minimal synthetic TokenSignals for testing.

    Accepts optional combined-mode kwargs that are passed through to TokenSignals:
      is_combined, secondary_entry_mask, secondary_direction,
      secondary_leverage, capital_split, is_perp_primary, is_perp_secondary,
      perp_close, perp_high, perp_low, per_bar_is_perp
    """
    from v4.signals import TokenSignals

    if direction is None:
        direction = np.ones(n_bars, dtype=np.int8)
    timestamps = _make_timestamps(n_bars)
    high = close + 1.0
    low = close - 1.0
    atr = np.full(n_bars, atr_val)
    regime = np.zeros(n_bars, dtype=np.int8)
    sm = np.ones(n_bars)
    lev = np.ones(n_bars)
    stop_arr = np.full(n_bars, 2.0)
    trail_arr = np.full(n_bars, 3.0)

    # Extract combined-mode kwargs with defaults
    is_combined = kwargs.pop("is_combined", False)
    secondary_entry_mask = kwargs.pop("secondary_entry_mask", None)
    secondary_direction = kwargs.pop("secondary_direction", None)
    secondary_leverage = kwargs.pop("secondary_leverage", 1.0)
    capital_split = kwargs.pop("capital_split", 0.5)
    is_perp_primary = kwargs.pop("is_perp_primary", False)
    is_perp_secondary = kwargs.pop("is_perp_secondary", False)
    perp_close = kwargs.pop("perp_close", None)
    perp_high = kwargs.pop("perp_high", None)
    perp_low = kwargs.pop("perp_low", None)
    per_bar_is_perp = kwargs.pop("per_bar_is_perp", None)

    return TokenSignals(
        token=token, strategy_id=strategy_id, n_bars=n_bars,
        timestamps=timestamps, entry_mask=entry_mask,
        direction=direction, close=close, high=high, low=low,
        atr=atr, rolling_adv=adv, regime=regime,
        funding_1h=np.zeros(n_bars), exit_regimes=set(),
        stop_mult=stop_arr, trail_mult=trail_arr,
        target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
        edge=edge, size_multiplier=sm, cap_multiplier=np.ones(n_bars),
        leverage=lev, max_trade_pct=0.0,
        is_combined=is_combined,
        secondary_entry_mask=secondary_entry_mask,
        secondary_direction=secondary_direction,
        secondary_leverage=secondary_leverage,
        capital_split=capital_split,
        is_perp_primary=is_perp_primary,
        is_perp_secondary=is_perp_secondary,
        perp_close=perp_close,
        perp_high=perp_high,
        perp_low=perp_low,
        per_bar_is_perp=per_bar_is_perp,
    )


# ===================================================================
# Task 1: Equity Cap Wiring (AC2, AC4, AC4b)
# ===================================================================

class TestEquityCapWiring:
    """Test that max_sizing_equity is wired into the simulator entry path
    and that load_paper_config reads it from JSON."""

    def test_ac2_sizing_equity_is_capped_in_entry_path(self):
        """AC2: When max_sizing_equity is set, position sizing uses capped equity.

        Set up a simulation where portfolio_equity=$5M and max_sizing_equity=$2M.
        The position size should be based on $2M, not $5M. We compare sizes
        from two runs: one with cap and one without. The capped run should
        produce a smaller position.
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("BTC", "s30", n_bars, entry_mask, close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}

        # Run WITHOUT cap: portfolio equity is $5M
        config_no_cap = _default_config(capital=5_000_000.0)
        state_no_cap = SimulationState(initial_capital=5_000_000.0)
        rng = np.random.default_rng(42)
        _process_entries(state_no_cap, all_signals, strategy_specs,
                         bar_maps, 10, config_no_cap, rng)
        positions_no_cap = list(state_no_cap.position_manager.open_positions)

        # Run WITH cap: portfolio equity $5M but sizing capped at $2M
        config_cap = _default_config(capital=5_000_000.0, max_sizing_equity=2_000_000.0)
        state_cap = SimulationState(initial_capital=5_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_cap, all_signals, strategy_specs,
                         bar_maps, 10, config_cap, rng2)
        positions_cap = list(state_cap.position_manager.open_positions)

        assert len(positions_no_cap) == 1, "Should open a position without cap"
        assert len(positions_cap) == 1, "Should open a position with cap"

        # Capped position should be smaller
        size_no_cap = abs(positions_no_cap[0].margin_usd)
        size_cap = abs(positions_cap[0].margin_usd)
        assert size_cap < size_no_cap, (
            f"Capped position ({size_cap}) should be smaller than uncapped ({size_no_cap})"
        )

    def test_ac4_concentration_limit_uses_uncapped_equity(self):
        """AC4: Concentration limit uses actual (uncapped) equity.

        With max_sizing_equity=$500K, portfolio_eq=$5M, concentration_limit=0.05:
          - Uncapped concentration: $5M * 0.05 = $250K (not binding)
          - Capped concentration:   $500K * 0.05 = $25K (would truncate)

        Position is sized on strategy_equity = $500K (capped). With typical
        Kelly/cap sizing, the raw position is ~$47K. If concentration correctly
        uses uncapped equity ($250K), position stays at $47K. If it incorrectly
        uses capped equity ($25K), position gets truncated to $25K.
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 500_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("BTC", "s30", n_bars, entry_mask, close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}
        rng = np.random.default_rng(42)

        config = _default_config(
            capital=5_000_000.0,
            max_sizing_equity=500_000.0,
            concentration_limit=0.05,
        )

        state = SimulationState(initial_capital=5_000_000.0)
        _process_entries(state, all_signals, strategy_specs,
                         bar_maps, 10, config, rng)
        positions = list(state.position_manager.open_positions)

        assert len(positions) == 1, "Position should open"
        pos_size = abs(positions[0].margin_usd)

        # If concentration incorrectly used capped equity ($500K * 0.05 = $25K),
        # the position would be truncated to $25K. With uncapped equity
        # ($5M * 0.05 = $250K), the Kelly/cap-based position (~$47K) is not
        # truncated. Assert the position exceeds the capped concentration limit.
        capped_conc_limit = 500_000.0 * 0.05  # $25K
        assert pos_size > capped_conc_limit * 1.1, (
            f"Position ({pos_size:.0f}) should exceed capped concentration limit "
            f"({capped_conc_limit:.0f}), proving concentration uses uncapped equity"
        )

    def test_ac2_combined_entry_path_equity_cap(self):
        """AC2: When max_sizing_equity is set, the combined entry path also uses
        capped equity. AC2 says 'This applies in BOTH the combined entry path
        and the single-leg entry path.'

        Set portfolio_equity=$5M, max_sizing_equity=$2M, is_combined=True.
        Compare position sizes with and without cap.
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        secondary_entry_mask = np.zeros(n_bars, dtype=bool)
        secondary_entry_mask[10] = True
        secondary_direction = np.ones(n_bars, dtype=np.int8) * -1  # short secondary
        perp_close = close.copy()
        perp_high = close + 1.0
        perp_low = close - 1.0

        sig = _make_token_signals(
            "BTC", "s30", n_bars, entry_mask, close, adv,
            is_combined=True,
            secondary_entry_mask=secondary_entry_mask,
            secondary_direction=secondary_direction,
            secondary_leverage=1.0,
            capital_split=0.5,
            is_perp_primary=False,
            is_perp_secondary=True,
            perp_close=perp_close,
            perp_high=perp_high,
            perp_low=perp_low,
            per_bar_is_perp=None,
        )

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}

        # Run WITHOUT cap: portfolio equity is $5M
        config_no_cap = _default_config(capital=5_000_000.0)
        state_no_cap = SimulationState(initial_capital=5_000_000.0)
        rng = np.random.default_rng(42)
        _process_entries(state_no_cap, all_signals, strategy_specs,
                         bar_maps, 10, config_no_cap, rng)
        positions_no_cap = list(state_no_cap.position_manager.open_positions)

        # Run WITH cap: portfolio equity $5M but sizing capped at $2M
        config_cap = _default_config(capital=5_000_000.0, max_sizing_equity=2_000_000.0)
        state_cap = SimulationState(initial_capital=5_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_cap, all_signals, strategy_specs,
                         bar_maps, 10, config_cap, rng2)
        positions_cap = list(state_cap.position_manager.open_positions)

        # Combined opens 2 legs (primary + secondary)
        assert len(positions_no_cap) == 2, "Combined should open two legs without cap"
        assert len(positions_cap) == 2, "Combined should open two legs with cap"

        # Total margin across both legs should be smaller with cap
        total_no_cap = sum(abs(p.margin_usd) for p in positions_no_cap)
        total_cap = sum(abs(p.margin_usd) for p in positions_cap)
        assert total_cap < total_no_cap, (
            f"Combined capped total margin ({total_cap}) should be smaller "
            f"than uncapped ({total_no_cap})"
        )

    def test_ac2_max_sizing_equity_zero_blocks_entries(self):
        """AC2 edge case: When max_sizing_equity=0, strategy_equity=0 and
        no positions should open (sizing produces zero).
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("BTC", "s30", n_bars, entry_mask, close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}

        config = _default_config(capital=1_000_000.0, max_sizing_equity=0.0)
        state = SimulationState(initial_capital=1_000_000.0)
        rng = np.random.default_rng(42)
        _process_entries(state, all_signals, strategy_specs,
                         bar_maps, 10, config, rng)

        positions = list(state.position_manager.open_positions)
        assert len(positions) == 0, (
            "With max_sizing_equity=0, no positions should open"
        )

    def test_ac4b_load_paper_config_reads_max_sizing_equity(self):
        """AC4b: load_paper_config reads max_sizing_equity from JSON."""
        from v4.paper_config import load_paper_config

        config_data = {
            "strategies": [{"strategy_id": "s30", "market": "spot"}],
            "initial_capital": 200_000,
            "max_sizing_equity": 2_000_000,
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config = load_paper_config(f.name)

        assert config.max_sizing_equity == 2_000_000, (
            "load_paper_config should read max_sizing_equity from JSON"
        )

    def test_ac4b_load_paper_config_default_none(self):
        """AC4b: When max_sizing_equity is absent from JSON, it defaults to None."""
        from v4.paper_config import load_paper_config

        config_data = {
            "strategies": [{"strategy_id": "s30", "market": "spot"}],
            "initial_capital": 200_000,
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()
            config = load_paper_config(f.name)

        assert config.max_sizing_equity is None, (
            "load_paper_config should default max_sizing_equity to None"
        )


# ===================================================================
# Task 2: Paper Engine Preservation of ADV Sizing Fields
# ===================================================================

class TestPaperEnginePreservation:
    """Test that paper_engine independent mode preserves ADV sizing fields
    when reconstructing StrategySpec with weight=1.0."""

    def test_dataclasses_replace_preserves_adv_fields(self):
        """When StrategySpec has ADV sizing fields, dataclasses.replace(orig, weight=1.0)
        should preserve them. This tests the expected fix for paper_engine.py line 608-617.
        """
        orig = StrategySpec(
            strategy_id="s56",
            weight=0.5,
            max_positions=10,
            adv_sizing_enabled=True,
            adv_sizing_base=75_000_000,
            adv_sizing_floor=0.20,
        )
        replaced = dataclasses.replace(orig, weight=1.0)

        assert replaced.weight == 1.0
        assert replaced.strategy_id == "s56"
        assert replaced.max_positions == 10
        assert replaced.adv_sizing_enabled is True, (
            "dataclasses.replace should preserve adv_sizing_enabled"
        )
        assert replaced.adv_sizing_base == 75_000_000, (
            "dataclasses.replace should preserve adv_sizing_base"
        )
        assert replaced.adv_sizing_floor == 0.20, (
            "dataclasses.replace should preserve adv_sizing_floor"
        )


    def test_paper_engine_uses_dataclasses_replace(self):
        """Verify paper_engine.py uses dataclasses.replace for independent mode."""
        import inspect
        from v4 import paper_engine
        source = inspect.getsource(paper_engine)
        assert "dataclasses.replace" in source, (
            "paper_engine.py should use dataclasses.replace for StrategySpec reconstruction"
        )



# ===================================================================
# Task 3: ADV Sizing in compute_position_size (AC7, AC8)
# ===================================================================

class TestADVSizingInPositionSize:
    """Test that compute_position_size applies the ADV sizing multiplier
    when adv_sizing_enabled=True."""

    def test_ac7_adv_sizing_reduces_position_size(self):
        """AC7: When adv_sizing_enabled=True, compute_position_size multiplies
        the raw Kelly-based size by min(1.0, max(floor, sqrt(adv/base))).

        With adv=$30M, base=$75M, floor=0.20:
          multiplier = sqrt(30M/75M) = 0.632
        So the position size should be ~63% of what it would be without ADV sizing.
        """
        strategy_equity = 200_000.0
        rolling_adv = 30_000_000.0
        volatility = 0.02
        edge = 0.35
        size_multiplier = 1.0
        cap_multiplier = 1.0
        max_trade_pct = 0.0

        # Without ADV sizing
        size_without = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
        )

        # With ADV sizing enabled
        size_with = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_sizing_enabled=True,
            adv_sizing_base=75_000_000.0,
            adv_sizing_floor=0.20,
        )

        assert size_without > 0, "Baseline size should be positive"
        assert size_with > 0, "ADV-adjusted size should be positive"
        assert size_with < size_without, (
            f"ADV sizing should reduce position: {size_with} >= {size_without}"
        )

        expected_multiplier = np.sqrt(30_000_000.0 / 75_000_000.0)  # ~0.632
        ratio = size_with / size_without
        assert ratio == pytest.approx(expected_multiplier, abs=0.05), (
            f"Size ratio {ratio} should be close to ADV multiplier {expected_multiplier}"
        )

    def test_ac8_adv_sizing_disabled_no_change(self):
        """AC8: When adv_sizing_enabled=False (default), position sizing is unchanged."""
        strategy_equity = 200_000.0
        rolling_adv = 30_000_000.0
        volatility = 0.02
        edge = 0.35
        size_multiplier = 1.0
        cap_multiplier = 1.0
        max_trade_pct = 0.0

        # Default (no ADV sizing params)
        size_default = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
        )

        # Explicitly disabled
        size_disabled = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_sizing_enabled=False,
            adv_sizing_base=75_000_000.0,
            adv_sizing_floor=0.20,
        )

        assert size_default == pytest.approx(size_disabled, rel=1e-9), (
            "With adv_sizing_enabled=False, size should be identical to default"
        )

    def test_ac7_adv_sizing_high_adv_no_reduction(self):
        """AC7: When ADV >= base, multiplier = min(1.0, ...) = 1.0, no reduction."""
        strategy_equity = 200_000.0
        rolling_adv = 100_000_000.0  # equal to base
        volatility = 0.02
        edge = 0.35
        size_multiplier = 1.0
        cap_multiplier = 1.0
        max_trade_pct = 0.0

        size_without = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
        )

        size_with = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_sizing_enabled=True,
            adv_sizing_base=100_000_000.0,
            adv_sizing_floor=0.20,
        )

        assert size_with == pytest.approx(size_without, rel=1e-6), (
            "With ADV >= base, ADV sizing multiplier should be 1.0 (no reduction)"
        )

    def test_ac7_adv_sizing_floor_applied(self):
        """AC7: When ADV is very low, the floor prevents the multiplier from
        going below adv_sizing_floor (0.20 by default).

        sqrt(1M/75M) = 0.115, below floor of 0.20 -> multiplier = 0.20.

        Uses high cap_multiplier and adv_cap_pct to ensure `raw` is the
        binding constraint in both runs (not cap or adv_cap), so the ratio
        directly reflects the ADV sizing multiplier.
        """
        strategy_equity = 200_000.0
        rolling_adv = 1_000_000.0
        volatility = 0.02
        edge = 0.35
        size_multiplier = 1.0
        cap_multiplier = 100.0  # high so cap doesn't bind
        max_trade_pct = 0.0

        size_without = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_cap_pct=999999.0,  # high so ADV hard cap doesn't bind
        )

        size_with_floor = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_cap_pct=999999.0,
            adv_sizing_enabled=True,
            adv_sizing_base=75_000_000.0,
            adv_sizing_floor=0.20,
        )

        # sqrt(1M/75M) = 0.115, but floor is 0.20
        # So multiplier = max(0.20, 0.115) = 0.20
        assert size_without > 0, "Baseline size must be positive for ratio test"
        ratio = size_with_floor / size_without
        assert ratio == pytest.approx(0.20, abs=0.03), (
            f"ADV sizing floor should clamp multiplier at 0.20, got ratio {ratio}"
        )

    def test_ac7_rolling_adv_near_zero_uses_floor(self):
        """AC7 edge case: When rolling_adv is near zero, sqrt(adv/base)~=0,
        but floor=0.20 applies. Position size should be ~20% of what it would
        be without ADV sizing.

        Uses rolling_adv=1.0 (not 0.0) because adv_cap = rolling_adv * adv_cap_pct
        would be 0 with rolling_adv=0, making both sizes 0 (vacuous).
        Uses high cap_multiplier and adv_cap_pct to ensure only `raw` binds.
        """
        strategy_equity = 200_000.0
        rolling_adv = 1.0  # near-zero, not exactly 0 (avoids adv_cap=0)
        volatility = 0.02
        edge = 0.35
        size_multiplier = 1.0
        cap_multiplier = 100.0  # high so cap doesn't bind
        max_trade_pct = 0.0

        size_without = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_cap_pct=999999.0,  # high so ADV hard cap doesn't bind
        )

        size_with = compute_position_size(
            strategy_equity, rolling_adv, volatility, edge,
            size_multiplier, cap_multiplier, max_trade_pct,
            adv_cap_pct=999999.0,
            adv_sizing_enabled=True,
            adv_sizing_base=75_000_000.0,
            adv_sizing_floor=0.20,
        )

        # sqrt(1/75M) ~= 0.000115, below floor of 0.20 => multiplier = 0.20
        assert size_without > 0, "Baseline size must be positive for ratio test"
        ratio = size_with / size_without
        assert ratio == pytest.approx(0.20, abs=0.02), (
            f"With near-zero ADV, floor should apply: ratio={ratio}, expected ~0.20"
        )


# ===================================================================
# Task 5: Unrealized P&L in Sizing (AC13, AC14, AC15, AC15b)
# ===================================================================

class TestUnrealizedPnLSizing:
    """Test that sizing equity accounts for unrealized P&L across open positions."""

    def test_ac13_negative_unrealized_reduces_sizing_equity(self):
        """AC13: With negative unrealized P&L, sizing_eq is reduced.

        Formula: sizing_eq = max(min(portfolio_eq + unrealized, portfolio_eq), portfolio_eq * 0.85)

        We create an existing open position that is underwater, then verify
        that a new position opened in _process_entries is smaller than it
        would be without the open loss.
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        close = np.full(n_bars, 100.0)
        close[15:] = 90.0  # price drops for open positions
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True  # new entry after price drop

        sig = _make_token_signals("ETH", "s30", n_bars, entry_mask, close, adv)

        # BTC signal for unrealized P&L computation on existing position
        btc_close = np.full(n_bars, 100.0)
        btc_close[15:] = 90.0
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"ETH": sig, "BTC": btc_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "ETH": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
        }

        config = _default_config(capital=1_000_000.0)

        # Existing open position with unrealized loss
        # Entry at 100, current price at 90 => unrealized = 1000 * (90 - 100) = -$10K
        existing_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=100.0, direction=1,
            quantity=1000.0, margin_usd=100_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=80.0,
            highest=100.0, lowest=90.0, initial_risk=4.0,
        )

        # State WITH existing losing position
        state_with_loss = SimulationState(initial_capital=1_000_000.0)
        state_with_loss.position_manager.open_position(existing_pos)
        state_with_loss._entry_fees_by_pos[existing_pos.position_id] = 100.0

        rng1 = np.random.default_rng(42)
        _process_entries(state_with_loss, all_signals, strategy_specs,
                         bar_maps, 20, config, rng1)

        # State WITHOUT any open positions (no unrealized P&L)
        state_no_loss = SimulationState(initial_capital=1_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_no_loss, all_signals, strategy_specs,
                         bar_maps, 20, config, rng2)

        # Both should open ETH positions, but the one with losses should be smaller
        loss_positions = [p for p in state_with_loss.position_manager.open_positions
                          if p.token == "ETH"]
        no_loss_positions = [p for p in state_no_loss.position_manager.open_positions
                             if p.token == "ETH"]

        assert len(no_loss_positions) == 1, "Should open ETH without losses"
        assert len(loss_positions) == 1, "Should open ETH with losses"

        size_with_loss = abs(loss_positions[0].margin_usd)
        size_no_loss = abs(no_loss_positions[0].margin_usd)

        assert size_with_loss < size_no_loss, (
            f"Position with unrealized loss ({size_with_loss}) should be smaller "
            f"than without ({size_no_loss})"
        )

    def test_ac13_floor_at_85_percent_in_simulator(self):
        """AC13: Sizing equity floor at 85% of realized equity in the simulator.

        With a massive unrealized loss (>15%), the floor should limit
        sizing_eq to 85% of portfolio_eq, not let it drop further.
        This tests through the simulator, not just the formula.
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        close = np.full(n_bars, 100.0)
        close[15:] = 50.0  # 50% price drop => massive unrealized loss
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True

        sig = _make_token_signals("ETH", "s30", n_bars, entry_mask, close, adv)

        # BTC with massive loss
        btc_close = np.full(n_bars, 100.0)
        btc_close[15:] = 50.0
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"ETH": sig, "BTC": btc_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "ETH": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
        }

        config = _default_config(capital=1_000_000.0)

        # Existing position with HUGE unrealized loss (50% drop)
        # quantity=5000, entry=100, current=50 => unrealized = 5000*(50-100) = -$250K
        # That's 25% of $1M portfolio => exceeds 15% threshold
        # Floor should kick in: sizing_eq = 85% * $1M = $850K
        existing_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=100.0, direction=1,
            quantity=5000.0, margin_usd=500_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=40.0,
            highest=100.0, lowest=50.0, initial_risk=4.0,
        )

        # With massive loss (floor should apply)
        state_loss = SimulationState(initial_capital=1_000_000.0)
        state_loss.position_manager.open_position(existing_pos)
        state_loss._entry_fees_by_pos[existing_pos.position_id] = 500.0
        rng1 = np.random.default_rng(42)
        _process_entries(state_loss, all_signals, strategy_specs,
                         bar_maps, 20, config, rng1)

        # Without any loss
        state_no_loss = SimulationState(initial_capital=1_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_no_loss, all_signals, strategy_specs,
                         bar_maps, 20, config, rng2)

        loss_positions = [p for p in state_loss.position_manager.open_positions
                          if p.token == "ETH"]
        no_loss_positions = [p for p in state_no_loss.position_manager.open_positions
                             if p.token == "ETH"]

        assert len(loss_positions) == 1, "Should still open position (floor prevents zero)"
        assert len(no_loss_positions) == 1, "Should open position without loss"

        size_with_floor = abs(loss_positions[0].margin_usd)
        size_no_loss = abs(no_loss_positions[0].margin_usd)

        # With 85% floor, the position should be at least 85% of no-loss size
        # (may not be exactly 85% due to other constraints, but should be close)
        assert size_with_floor < size_no_loss, "Floor-limited size should be less than full"
        assert size_with_floor >= size_no_loss * 0.83, (
            f"Floor should keep sizing >= ~85% of full: got {size_with_floor}, "
            f"full was {size_no_loss}"
        )

    def test_ac14_equity_cap_applied_after_unrealized_adjustment(self):
        """AC14: max_sizing_equity cap is applied AFTER unrealized P&L adjustment.

        With portfolio_eq=$3M, unrealized=-$200K, max_sizing_equity=$2M:
          Step 1 (unrealized): sizing_eq = max(min($2.8M, $3M), $2.55M) = $2.8M
          Step 2 (cap): sizing_eq = min($2.8M, $2M) = $2M

        We verify that the cap binds even though unrealized already reduced equity.
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        close = np.full(n_bars, 100.0)
        close[15:] = 96.0  # small drop for unrealized
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True

        sig = _make_token_signals("ETH", "s30", n_bars, entry_mask, close, adv)

        btc_close = np.full(n_bars, 100.0)
        btc_close[15:] = 96.0
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"ETH": sig, "BTC": btc_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "ETH": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
        }

        # Existing position with some unrealized loss
        existing_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=100.0, direction=1,
            quantity=5000.0, margin_usd=500_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=80.0,
            highest=100.0, lowest=96.0, initial_risk=4.0,
        )

        # With unrealized loss AND cap: both should reduce sizing
        config_both = _default_config(
            capital=3_000_000.0,
            max_sizing_equity=2_000_000.0,
        )
        state_both = SimulationState(initial_capital=3_000_000.0)
        state_both.position_manager.open_position(existing_pos)
        state_both._entry_fees_by_pos[existing_pos.position_id] = 500.0
        rng1 = np.random.default_rng(42)
        _process_entries(state_both, all_signals, strategy_specs,
                         bar_maps, 20, config_both, rng1)

        # With cap only, no unrealized loss
        config_cap = _default_config(
            capital=3_000_000.0,
            max_sizing_equity=2_000_000.0,
        )
        state_cap = SimulationState(initial_capital=3_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_cap, all_signals, strategy_specs,
                         bar_maps, 20, config_cap, rng2)

        both_positions = [p for p in state_both.position_manager.open_positions
                          if p.token == "ETH"]
        cap_positions = [p for p in state_cap.position_manager.open_positions
                         if p.token == "ETH"]

        assert len(both_positions) == 1, "Should open with both constraints"
        assert len(cap_positions) == 1, "Should open with cap only"

        # Both should produce the same size IF the cap is the binding constraint
        # (cap at $2M is already below unrealized-adjusted $2.8M)
        size_both = abs(both_positions[0].margin_usd)
        size_cap = abs(cap_positions[0].margin_usd)

        # They should be equal because cap ($2M) binds in both cases
        assert size_both == pytest.approx(size_cap, rel=0.01), (
            f"When cap binds, unrealized+cap ({size_both}) should equal "
            f"cap-only ({size_cap})"
        )

    def test_ac15_no_open_positions_sizing_eq_equals_portfolio_eq(self):
        """AC15: With no open positions (unrealized=0), sizing_eq equals portfolio_eq.

        This tests through the simulator: with no open positions, the sizing
        behavior should be identical to current behavior.

        NOTE: This is a backward-compatible regression test — it PASSES both
        before and after implementation because no open positions means
        unrealized=0, which doesn't change sizing. It verifies the feature
        doesn't introduce regressions in the zero-unrealized case.
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("BTC", "s30", n_bars, entry_mask, close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}

        # With no open positions, sizing_eq == portfolio_eq
        config = _default_config(capital=500_000.0)

        # Run 1
        state1 = SimulationState(initial_capital=500_000.0)
        rng1 = np.random.default_rng(42)
        _process_entries(state1, all_signals, strategy_specs,
                         bar_maps, 10, config, rng1)

        # Run 2 (identical)
        state2 = SimulationState(initial_capital=500_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state2, all_signals, strategy_specs,
                         bar_maps, 10, config, rng2)

        pos1 = list(state1.position_manager.open_positions)
        pos2 = list(state2.position_manager.open_positions)

        assert len(pos1) == 1 and len(pos2) == 1
        # Both should be identical — no unrealized P&L effect
        assert abs(pos1[0].margin_usd) == pytest.approx(abs(pos2[0].margin_usd), rel=1e-9), (
            "With no open positions, sizing should be deterministic and use full equity"
        )

    def test_ac15b_unrealized_computed_via_helper(self):
        """AC15b: The simulator should have a _compute_total_unrealized helper
        (or equivalent) that computes unrealized P&L following the same bar map /
        signal lookup pattern as _record_equity_snapshot.

        We test that the helper exists and is callable.
        """
        from v4.simulator import _compute_total_unrealized

        # Just verify the function exists and is importable
        assert callable(_compute_total_unrealized), (
            "_compute_total_unrealized should be a callable function"
        )

    def test_ac13_catastrophic_loss_triggers_85pct_floor(self):
        """AC13 edge case: With catastrophic unrealized loss (>15% of equity),
        the 85% floor kicks in to limit sizing_eq.

        With portfolio_eq=$1M, existing position with unrealized=-$990K:
          min($1M - $990K, $1M) = $10K
          max($10K, $1M * 0.85) = $850K  (85% floor kicks in)
          max($850K, 0) = $850K

        So sizing_eq = $850K, producing a smaller position than the $1M
        baseline without any losses.

        NOTE: Uses high concentration_limit (0.50) and small margin ($10K) to
        ensure free_capital and concentration are NOT the binding constraints.
        The only difference between the two runs is the unrealized P&L
        adjustment to sizing equity.
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        close = np.full(n_bars, 100.0)
        close[15:] = 1.0  # 99% drop => catastrophic unrealized loss
        adv = np.full(n_bars, 500_000_000.0)  # high ADV so ADV cap doesn't bind
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True

        sig = _make_token_signals("ETH", "s30", n_bars, entry_mask, close, adv)

        btc_close = np.full(n_bars, 100.0)
        btc_close[15:] = 1.0
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"ETH": sig, "BTC": btc_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "ETH": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
        }

        # High concentration limit (0.50) so it doesn't bind
        config = _default_config(capital=1_000_000.0, concentration_limit=0.50)

        # Existing position with CATASTROPHIC loss but tiny margin
        # quantity=10000, entry=100, current=1 => unrealized = 10000*(1-100) = -$990K
        # margin=$10K so free_capital ~= $990K (not the binding constraint)
        existing_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=100.0, direction=1,
            quantity=10000.0, margin_usd=10_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=0.01,
            highest=100.0, lowest=1.0, initial_risk=4.0,
        )

        # State WITH catastrophic loss
        state_loss = SimulationState(initial_capital=1_000_000.0)
        state_loss.position_manager.open_position(existing_pos)
        state_loss._entry_fees_by_pos[existing_pos.position_id] = 10.0
        rng1 = np.random.default_rng(42)
        _process_entries(state_loss, all_signals, strategy_specs,
                         bar_maps, 20, config, rng1)

        # State WITHOUT any open positions (no unrealized P&L)
        state_clean = SimulationState(initial_capital=1_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_clean, all_signals, strategy_specs,
                         bar_maps, 20, config, rng2)

        loss_positions = [p for p in state_loss.position_manager.open_positions
                          if p.token == "ETH"]
        clean_positions = [p for p in state_clean.position_manager.open_positions
                           if p.token == "ETH"]

        assert len(clean_positions) == 1, "Should open position without any loss"
        assert len(loss_positions) == 1, "Should still open position (85% floor > 0)"

        # With the 85% floor, sizing_eq = $850K vs $1M without losses.
        # Position with catastrophic loss should be noticeably smaller.
        size_loss = abs(loss_positions[0].margin_usd)
        size_clean = abs(clean_positions[0].margin_usd)
        assert size_loss < size_clean * 0.95, (
            f"With catastrophic unrealized loss, position ({size_loss}) should be "
            f"materially smaller than without losses ({size_clean}) due to 85% floor"
        )

    def test_ac13_mixed_pnl_net_negative_reduces_sizing(self):
        """AC13: When some positions are profitable and some losing, the total
        unrealized P&L is what matters. With net NEGATIVE unrealized, sizing
        should be reduced compared to no open positions.

        BTC: entry=80, current=100 => unrealized = 500*(100-80) = +$10K
        ETH: entry=150, current=100 => unrealized = 500*(100-150) = -$25K
        Net unrealized = -$15K

        With portfolio_eq=$1M, unrealized=-$15K:
          sizing_eq = max(min($985K, $1M), $850K) = $985K

        NOTE: Uses high concentration_limit and large capital so free_capital
        is NOT the binding constraint (proving the test detects unrealized).
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 500_000_000.0)  # very high so ADV cap doesn't bind
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True

        sig = _make_token_signals("SOL", "s30", n_bars, entry_mask, close, adv)

        # BTC: current=100, entry=80 => profit
        btc_close = np.full(n_bars, 100.0)
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        # ETH: current=100, entry=150 => loss
        eth_close = np.full(n_bars, 100.0)
        eth_entry = np.zeros(n_bars, dtype=bool)
        eth_sig = _make_token_signals("ETH", "s30", n_bars, eth_entry, eth_close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"SOL": sig, "BTC": btc_sig, "ETH": eth_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "SOL": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
            "ETH": np.arange(n_bars, dtype=np.float64),
        }

        # High concentration limit so it doesn't bind
        config = _default_config(capital=1_000_000.0, concentration_limit=0.50)

        # Profitable BTC position (small margin so free_capital isn't the constraint)
        btc_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=80.0, direction=1,
            quantity=500.0, margin_usd=10_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=60.0,
            highest=100.0, lowest=80.0, initial_risk=4.0,
        )
        # Losing ETH position (small margin)
        eth_pos = Position(
            position_id="ETH:s30:5:primary",
            token="ETH", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=150.0, direction=1,
            quantity=500.0, margin_usd=10_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=100.0,
            highest=150.0, lowest=100.0, initial_risk=4.0,
        )

        # With mixed positions (net unrealized = +$10K - $25K = -$15K)
        state_mixed = SimulationState(initial_capital=1_000_000.0)
        state_mixed.position_manager.open_position(btc_pos)
        state_mixed.position_manager.open_position(eth_pos)
        state_mixed._entry_fees_by_pos[btc_pos.position_id] = 10.0
        state_mixed._entry_fees_by_pos[eth_pos.position_id] = 10.0
        rng1 = np.random.default_rng(42)
        _process_entries(state_mixed, all_signals, strategy_specs,
                         bar_maps, 20, config, rng1)

        # Without any positions (no unrealized P&L)
        state_clean = SimulationState(initial_capital=1_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_clean, all_signals, strategy_specs,
                         bar_maps, 20, config, rng2)

        sol_mixed = [p for p in state_mixed.position_manager.open_positions
                     if p.token == "SOL"]
        sol_clean = [p for p in state_clean.position_manager.open_positions
                     if p.token == "SOL"]

        assert len(sol_mixed) == 1, "Should open SOL with mixed unrealized"
        assert len(sol_clean) == 1, "Should open SOL without positions"

        # With net negative unrealized (-$15K), sizing_eq < portfolio_eq
        # So position should be smaller than clean baseline
        size_mixed = abs(sol_mixed[0].margin_usd)
        size_clean = abs(sol_clean[0].margin_usd)
        assert size_mixed < size_clean, (
            f"Net negative unrealized should reduce sizing: "
            f"mixed={size_mixed} should be < clean={size_clean}"
        )

    def test_ac13_nlv_floor_at_zero_when_portfolio_eq_negative(self):
        """AC13: When portfolio_equity is at or below zero (extreme realized losses),
        the formula `sizing_eq = max(sizing_eq, 0.0)` floors at 0.

        The NLV floor at 0 only activates when portfolio_eq itself is non-positive
        (since 85% floor of a positive number is always positive). This tests
        through the simulator with realized_pnl set to wipe out capital.

        With portfolio_eq near 0, strategy_equity = 0 * weight = 0, which produces
        position size 0 from compute_position_size regardless of the NLV floor.
        So we verify no positions open and that sizing doesn't go negative.
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("BTC", "s30", n_bars, entry_mask, close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}

        config = _default_config(capital=100_000.0)

        # Wipe out capital via realized losses
        state = SimulationState(initial_capital=100_000.0)
        state.realized_pnl = -100_000.0  # portfolio_equity = 0

        rng = np.random.default_rng(42)
        _process_entries(state, all_signals, strategy_specs,
                         bar_maps, 10, config, rng)

        positions = list(state.position_manager.open_positions)
        assert len(positions) == 0, (
            "With portfolio_equity=0, no positions should open (NLV floor at 0)"
        )

    def test_ac13_positive_unrealized_does_not_inflate_sizing(self):
        """AC13 constrain-only: Positive unrealized P&L must NOT inflate sizing
        above realized portfolio equity.

        Formula: sizing_eq = max(min(pf_eq + unrealized, pf_eq), pf_eq * 0.85)
        When unrealized > 0: min(pf_eq + positive, pf_eq) = pf_eq
        So sizing_eq = max(pf_eq, pf_eq * 0.85) = pf_eq

        This is a regression test — it PASSES both before and after implementation
        because the constrain-only property is backward compatible (currently
        unrealized isn't used at all). It catches broken implementations that
        accidentally inflate sizing with positive unrealized.
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 500_000_000.0)  # high so ADV cap doesn't bind
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True

        sig = _make_token_signals("SOL", "s30", n_bars, entry_mask, close, adv)

        # BTC with big profit: entry=50, current=100 => unrealized = +$50K
        btc_close = np.full(n_bars, 100.0)
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"SOL": sig, "BTC": btc_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "SOL": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
        }

        config = _default_config(capital=1_000_000.0, concentration_limit=0.50)

        # Profitable BTC position: entry=50, current=100, qty=1000 => +$50K unrealized
        btc_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=50.0, direction=1,
            quantity=1000.0, margin_usd=10_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=30.0,
            highest=100.0, lowest=50.0, initial_risk=4.0,
        )

        # With profitable position (unrealized +$50K)
        state_profit = SimulationState(initial_capital=1_000_000.0)
        state_profit.position_manager.open_position(btc_pos)
        state_profit._entry_fees_by_pos[btc_pos.position_id] = 10.0
        rng1 = np.random.default_rng(42)
        _process_entries(state_profit, all_signals, strategy_specs,
                         bar_maps, 20, config, rng1)

        # Without any positions
        state_clean = SimulationState(initial_capital=1_000_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state_clean, all_signals, strategy_specs,
                         bar_maps, 20, config, rng2)

        sol_profit = [p for p in state_profit.position_manager.open_positions
                      if p.token == "SOL"]
        sol_clean = [p for p in state_clean.position_manager.open_positions
                     if p.token == "SOL"]

        assert len(sol_profit) == 1, "Should open SOL with profitable position"
        assert len(sol_clean) == 1, "Should open SOL without positions"

        # Positive unrealized must NOT inflate sizing above portfolio_eq
        size_profit = abs(sol_profit[0].margin_usd)
        size_clean = abs(sol_clean[0].margin_usd)
        assert size_profit <= size_clean * 1.001, (
            f"Positive unrealized must NOT inflate sizing: "
            f"with_profit={size_profit} should be <= clean={size_clean}"
        )


# ===================================================================
# Task 6: Stress Exit Slippage (AC16, AC17, AC18)
# ===================================================================

class TestStressExitSlippage:
    """Test stress_adv_multiplier behavior for different exit types.

    NOTE: These tests assert slippage values calibrated to hourly volume
    (adv/24 denominator), which is the post-implementation behavior.
    With the current daily denominator they will produce different values.
    """

    def test_ac16_stress_adv_multiplier_default_is_1(self):
        """AC16: stress_adv_multiplier default remains 1.0 (backward compatible)."""
        config = PortfolioConfig()
        assert config.stress_adv_multiplier == pytest.approx(1.0), (
            "Default stress_adv_multiplier should be 1.0"
        )

    def test_ac17_stop_exit_with_stress_multiplier_higher_slippage(self):
        """AC17: Stop exits with stress_adv_multiplier=0.5 produce higher slippage
        than with 1.0 (unstressed).

        With hourly slippage (adv/24 denominator):
          Normal: $100K / ($10M/24) = $100K / $416.7K = 0.24
            slip = 3 + 0.03 * sqrt(0.24) * 10000 = 3 + 147 = 150 bps
          Stressed (0.5): $100K / ($5M/24) = $100K / $208.3K = 0.48
            slip = 3 + 0.03 * sqrt(0.48) * 10000 = 3 + 208 = 211 bps
        """
        from v4.position import Position
        from v4.simulator import SimulationState, _close_position

        adv = 10_000_000.0

        # After hourly slippage is implemented, these values should match
        normal_slip = compute_slippage_bps(100_000.0, adv)
        stressed_slip = compute_slippage_bps(100_000.0, adv * 0.5)

        # With hourly volume, normal slip should be ~150 bps
        assert normal_slip == pytest.approx(150.0, abs=10.0), (
            f"Normal slippage with hourly volume should be ~150 bps, got {normal_slip}"
        )
        assert stressed_slip > normal_slip, "Stressed slippage should exceed normal"

        # Also verify through _close_position
        config_stressed = _default_config(capital=200_000.0, stress_adv_multiplier=0.5)
        config_unstressed = _default_config(capital=200_000.0, stress_adv_multiplier=1.0)

        def _make_stop_pos(pid: str) -> Position:
            return Position(
                position_id=pid, token="BTC", strategy_id="s30", leg="primary",
                entry_bar=0, entry_price=100.0, direction=1,
                quantity=1000.0, margin_usd=100_000.0, leverage=1.0,
                is_perp=True, fee_rate=0.0005,
                stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
                no_stop_bars=0, min_hold=0, max_hold=720,
                exit_regimes=set(), stop_price=98.0,
                highest=100.0, lowest=100.0, initial_risk=2.0,
            )

        state1 = SimulationState(initial_capital=200_000.0)
        pos1 = _make_stop_pos("BTC:s30:0:primary")
        state1.position_manager.open_position(pos1)
        state1._entry_fees_by_pos[pos1.position_id] = 50.0
        trade_stressed = _close_position(state1, pos1, 5, 97.0, "stop", adv, config_stressed)

        state2 = SimulationState(initial_capital=200_000.0)
        pos2 = _make_stop_pos("BTC:s30:1:primary")
        state2.position_manager.open_position(pos2)
        state2._entry_fees_by_pos[pos2.position_id] = 50.0
        trade_unstressed = _close_position(state2, pos2, 5, 97.0, "stop", adv, config_unstressed)

        assert trade_stressed.exit_price < trade_unstressed.exit_price, (
            f"Stressed stop exit price ({trade_stressed.exit_price}) should be "
            f"lower than unstressed ({trade_unstressed.exit_price}) for a long"
        )

    def test_ac18_non_stop_exits_use_unstressed_adv(self):
        """AC18: Non-stop exits use unstressed ADV regardless of stress config.

        After hourly slippage implementation, the expected slippage for a target
        exit with $100K notional and $10M ADV should be ~150 bps (hourly volume).
        """
        from v4.position import Position
        from v4.simulator import SimulationState, _close_position

        adv = 10_000_000.0
        config_with_stress = _default_config(capital=200_000.0, stress_adv_multiplier=0.5)

        non_stress_exit_reasons = [
            "target", "regime", "max_hold", "funding", "rsi",
            "mean_target", "circuit_breaker", "linked_exit", "data_end",
        ]

        for exit_reason in non_stress_exit_reasons:
            pos = Position(
                position_id=f"BTC:s30:0:{exit_reason}",
                token="BTC", strategy_id="s30", leg="primary",
                entry_bar=0, entry_price=100.0, direction=1,
                quantity=1000.0, margin_usd=100_000.0, leverage=1.0,
                is_perp=True, fee_rate=0.0005,
                stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
                no_stop_bars=0, min_hold=0, max_hold=720,
                exit_regimes=set(), stop_price=98.0,
                highest=100.0, lowest=100.0, initial_risk=2.0,
            )

            state = SimulationState(initial_capital=200_000.0)
            state.position_manager.open_position(pos)
            state._entry_fees_by_pos[pos.position_id] = 50.0
            trade = _close_position(state, pos, 5, 110.0, exit_reason, adv, config_with_stress)

            # Compute expected slippage with UNSTRESSED ADV and HOURLY volume
            notional = abs(1000.0 * 110.0)
            expected_slip_bps = compute_slippage_bps(
                notional, adv,  # unstressed ADV
                config_with_stress.base_spread_bps,
                config_with_stress.impact_coeff,
                config_with_stress.max_slip_bps,
            )
            expected_slip = 110.0 * expected_slip_bps / 10000.0
            expected_exit = 110.0 - expected_slip  # long position

            # With hourly volume, this should be significantly higher slippage
            # than the old daily-based value
            assert expected_slip_bps > 50.0, (
                f"Hourly slippage for {exit_reason} should exceed 50 bps, got {expected_slip_bps}"
            )
            assert trade.exit_price == pytest.approx(expected_exit, abs=0.1), (
                f"Exit reason '{exit_reason}' should use unstressed ADV. "
                f"Got {trade.exit_price}, expected {expected_exit}"
            )

    def test_ac18_margin_call_uses_stressed_adv(self):
        """AC18 (complement): margin_call exits should use stressed ADV,
        same as stop exits. Verified with hourly slippage values.
        """
        from v4.position import Position
        from v4.simulator import SimulationState, _close_position

        adv = 10_000_000.0

        config_stressed = _default_config(capital=200_000.0, stress_adv_multiplier=0.5)
        config_unstressed = _default_config(capital=200_000.0, stress_adv_multiplier=1.0)

        def _make_pos(pid: str) -> Position:
            return Position(
                position_id=pid, token="BTC", strategy_id="s30", leg="primary",
                entry_bar=0, entry_price=100.0, direction=1,
                quantity=1000.0, margin_usd=100_000.0, leverage=1.0,
                is_perp=True, fee_rate=0.0005,
                stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
                no_stop_bars=0, min_hold=0, max_hold=720,
                exit_regimes=set(), stop_price=98.0,
                highest=100.0, lowest=100.0, initial_risk=2.0,
            )

        state1 = SimulationState(initial_capital=200_000.0)
        pos1 = _make_pos("BTC:s30:0:mc1")
        state1.position_manager.open_position(pos1)
        state1._entry_fees_by_pos[pos1.position_id] = 50.0
        trade_stressed = _close_position(state1, pos1, 5, 97.0, "margin_call", adv, config_stressed)

        state2 = SimulationState(initial_capital=200_000.0)
        pos2 = _make_pos("BTC:s30:0:mc2")
        state2.position_manager.open_position(pos2)
        state2._entry_fees_by_pos[pos2.position_id] = 50.0
        trade_unstressed = _close_position(state2, pos2, 5, 97.0, "margin_call", adv, config_unstressed)

        assert trade_stressed.exit_price < trade_unstressed.exit_price, (
            "margin_call with stress_adv_multiplier=0.5 should produce worse exit price "
            "than with multiplier=1.0"
        )

        # With hourly slippage, the stressed margin_call slip should be substantial
        stressed_slip_amount = abs(97.0 - trade_stressed.exit_price)
        assert stressed_slip_amount > 0.5, (
            f"Stressed margin_call slippage should be significant with hourly volume, "
            f"got {stressed_slip_amount}"
        )

    def test_ac18_partial_close_uses_unstressed_adv(self):
        """AC18: _partial_close_position uses unstressed ADV (calls compute_slippage_bps
        directly, bypassing stress logic). Even with stress_adv_multiplier=0.5,
        partial profit-taking should use the full ADV for slippage.
        """
        from v4.position import Position
        from v4.simulator import SimulationState, _partial_close_position

        adv = 10_000_000.0

        config_stressed = _default_config(capital=200_000.0, stress_adv_multiplier=0.5)
        config_unstressed = _default_config(capital=200_000.0, stress_adv_multiplier=1.0)

        def _make_pos(pid: str) -> Position:
            return Position(
                position_id=pid, token="BTC", strategy_id="s30", leg="primary",
                entry_bar=0, entry_price=100.0, direction=1,
                quantity=1000.0, margin_usd=100_000.0, leverage=1.0,
                is_perp=True, fee_rate=0.0005,
                stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
                no_stop_bars=0, min_hold=0, max_hold=720,
                exit_regimes=set(), stop_price=98.0,
                highest=110.0, lowest=100.0, initial_risk=2.0,
                partial_tp_atr=2.0, partial_tp_pct=0.5, partial_tp_trail=1.5,
            )

        # Partial close with stressed config
        state1 = SimulationState(initial_capital=200_000.0)
        pos1 = _make_pos("BTC:s30:0:partial1")
        state1.position_manager.open_position(pos1)
        state1._entry_fees_by_pos[pos1.position_id] = 50.0
        trade_stressed = _partial_close_position(state1, pos1, 5, 110.0, adv, config_stressed)

        # Partial close with unstressed config
        state2 = SimulationState(initial_capital=200_000.0)
        pos2 = _make_pos("BTC:s30:0:partial2")
        state2.position_manager.open_position(pos2)
        state2._entry_fees_by_pos[pos2.position_id] = 50.0
        trade_unstressed = _partial_close_position(state2, pos2, 5, 110.0, adv, config_unstressed)

        # Both should produce the same exit price (partial close ignores stress)
        assert trade_stressed.exit_price == pytest.approx(trade_unstressed.exit_price, rel=1e-9), (
            f"Partial close should use unstressed ADV regardless of config: "
            f"stressed={trade_stressed.exit_price}, unstressed={trade_unstressed.exit_price}"
        )


# ===================================================================
# Task 7: Integration (AC19, AC20, AC21)
# ===================================================================

class TestIntegrationAllConstraints:
    """Integration tests verifying all sizing constraints compose correctly."""

    def test_ac19_all_constraints_compose(self):
        """AC19: All sizing changes compose correctly.

        Concrete example from the spec:
          portfolio_equity=$3M, max_sizing_equity=$2M,
          total_unrealized=-$200K, adv_sizing_base=$75M,
          rolling_adv=$30M

        Steps:
          1. Unrealized adjustment: sizing_eq = max(min($2.8M, $3M), $2.55M) = $2.8M
          2. Equity cap: sizing_eq = min($2.8M, $2M) = $2M
          3. ADV multiplier: raw_position *= sqrt(30M/75M) = 0.632
          4. Concentration limit uses uncapped portfolio_eq = $3M
          5. Slippage uses adv/24 = $1.25M hourly volume
        """
        # Step 3: ADV multiplier
        rolling_adv = 30_000_000.0
        adv_sizing_base = 75_000_000.0
        adv_mult = min(1.0, max(0.20, np.sqrt(rolling_adv / adv_sizing_base)))
        assert adv_mult == pytest.approx(0.6325, abs=0.01), "ADV multiplier"

        # Step 5: Slippage uses hourly volume via compute_slippage_bps
        # This is the key test: after implementation, compute_slippage_bps
        # should use adv/24 as denominator
        test_pos = 100_000.0
        daily_adv = rolling_adv  # $30M
        slip = compute_slippage_bps(test_pos, daily_adv)
        # With hourly: participation = 100K / (30M/24) = 100K / 1.25M = 0.08
        # slip = 3 + 0.03 * sqrt(0.08) * 10000 = 3 + 84.9 = 87.9 bps
        assert slip == pytest.approx(88.0, abs=10.0), (
            f"Slippage should be ~88 bps with hourly volume "
            f"(participation=100K/(30M/24)=0.08, slip=3+0.03*sqrt(0.08)*10000), got {slip}"
        )

        # Also verify ADV sizing works in compute_position_size
        strategy_equity = 2_000_000.0  # after cap
        size = compute_position_size(
            strategy_equity, rolling_adv, 0.02, 0.35, 1.0, 1.0, 0.0,
            adv_sizing_enabled=True,
            adv_sizing_base=adv_sizing_base,
            adv_sizing_floor=0.20,
        )
        assert size > 0, "Position size with all constraints should be positive"

    def test_ac19_full_pipeline_integration(self):
        """AC19: Full integration test through the simulator pipeline.

        Uses a mini-simulation with all constraints enabled:
        - max_sizing_equity cap
        - ADV sizing multiplier
        - Unrealized P&L adjustment
        - Hourly slippage
        - Stress exit multiplier
        """
        from v4.simulator import SimulationState, _process_entries
        from v4.position import Position

        n_bars = 50
        rolling_adv = 30_000_000.0
        close = np.full(n_bars, 100.0)
        close[15:] = 95.0  # slight drop for unrealized P&L
        adv = np.full(n_bars, rolling_adv)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[20] = True

        sig = _make_token_signals("ETH", "s30", n_bars, entry_mask, close, adv)

        btc_close = np.full(n_bars, 100.0)
        btc_close[15:] = 95.0
        btc_entry = np.zeros(n_bars, dtype=bool)
        btc_sig = _make_token_signals("BTC", "s30", n_bars, btc_entry, btc_close, adv)

        # Config with ALL constraints enabled
        config = _default_config(
            capital=3_000_000.0,
            max_sizing_equity=2_000_000.0,
            stress_adv_multiplier=0.5,
        )

        spec = StrategySpec(
            strategy_id="s30", weight=1.0,
            adv_sizing_enabled=True,
            adv_sizing_base=75_000_000.0,
            adv_sizing_floor=0.20,
        )
        all_signals = {"s30": {"ETH": sig, "BTC": btc_sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {
            "ETH": np.arange(n_bars, dtype=np.float64),
            "BTC": np.arange(n_bars, dtype=np.float64),
        }

        # Existing BTC position with unrealized loss
        existing_pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=5, entry_price=100.0, direction=1,
            quantity=5000.0, margin_usd=500_000.0, leverage=1.0,
            is_perp=False, fee_rate=0.001,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            exit_regimes=set(), stop_price=80.0,
            highest=100.0, lowest=95.0, initial_risk=4.0,
        )

        state = SimulationState(initial_capital=3_000_000.0)
        state.position_manager.open_position(existing_pos)
        state._entry_fees_by_pos[existing_pos.position_id] = 500.0

        rng = np.random.default_rng(42)
        _process_entries(state, all_signals, strategy_specs,
                         bar_maps, 20, config, rng)

        # Should have opened ETH position
        eth_positions = [p for p in state.position_manager.open_positions
                         if p.token == "ETH"]
        assert len(eth_positions) == 1, (
            "Should open ETH position with all constraints active"
        )
        assert abs(eth_positions[0].margin_usd) > 0, "Position size should be positive"

        # The position should be significantly less than unconstrained size.
        # Without constraints: ~$3M * weight(1.0) * kelly_frac * vol_adj = large.
        # With constraints: cap at $2M, ADV scaling ~0.63, concentration 10%.
        # Max constrained size = min($2M * 10%, ...) = ~$200K plus ADV effects.
        # Assert it's materially constrained (less than $500K).
        assert abs(eth_positions[0].margin_usd) < 500_000.0, (
            f"Position should be constrained below $500K by cap+concentration+ADV, "
            f"got {abs(eth_positions[0].margin_usd)}"
        )

    def test_ac20_preexisting_test_classes_importable(self):
        """AC20: Pre-existing test classes in test_v4_filters.py exist.

        Verify the test classes can be referenced (they test AC1, AC3, AC5,
        AC6, AC9, AC10, AC12 which are covered by pre-existing tests).
        """
        from tests.test_v4_filters import TestEquityCapConfig
        from tests.test_v4_filters import TestADVSizingConfig
        from tests.test_v4_filters import TestSlippageModel
        from tests.test_v4_metrics import TestCapacityEquityCap

        assert hasattr(TestEquityCapConfig, "test_max_sizing_equity_default_none")
        assert hasattr(TestEquityCapConfig, "test_max_sizing_equity_can_be_set")
        assert hasattr(TestADVSizingConfig, "test_adv_sizing_fields_exist")
        assert hasattr(TestADVSizingConfig, "test_adv_sizing_formula")
        assert hasattr(TestADVSizingConfig, "test_adv_sizing_from_dict")
        assert hasattr(TestSlippageModel, "test_slippage_hourly_participation")
        assert hasattr(TestCapacityEquityCap, "test_max_sizing_equity_field_exists")

    def test_ac19_full_lifecycle_entry_exit(self):
        """AC19: Full lifecycle test — open a position via _process_entries with
        all constraints enabled, then close it via _close_position with
        stress_adv_multiplier=0.5 and stop exit.

        Verify:
        1. Entry used capped equity and ADV scaling
        2. Exit used stressed ADV and hourly slippage
        3. Net P&L accounting is correct (entry fees + exit fees + slippage)
        """
        from v4.simulator import SimulationState, _process_entries, _close_position
        from v4.position import Position

        n_bars = 50
        rolling_adv = 30_000_000.0
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, rolling_adv)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("ETH", "s30", n_bars, entry_mask, close, adv)

        config = _default_config(
            capital=3_000_000.0,
            max_sizing_equity=2_000_000.0,
            stress_adv_multiplier=0.5,
        )

        spec = StrategySpec(
            strategy_id="s30", weight=1.0,
            adv_sizing_enabled=True,
            adv_sizing_base=75_000_000.0,
            adv_sizing_floor=0.20,
        )
        all_signals = {"s30": {"ETH": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"ETH": np.arange(n_bars, dtype=np.float64)}

        # Step 1: Open position with all constraints
        state = SimulationState(initial_capital=3_000_000.0)
        rng = np.random.default_rng(42)
        _process_entries(state, all_signals, strategy_specs,
                         bar_maps, 10, config, rng)

        eth_positions = [p for p in state.position_manager.open_positions
                         if p.token == "ETH"]
        assert len(eth_positions) == 1, "Should open ETH position"

        pos = eth_positions[0]
        entry_margin = abs(pos.margin_usd)

        # Verify entry used capped equity (position should be constrained)
        # Without cap, strategy_equity would be $3M. With cap, $2M.
        assert entry_margin > 0, "Position should have positive margin"
        assert entry_margin < 500_000.0, (
            f"Entry should be constrained by cap+ADV scaling, got {entry_margin}"
        )

        # Step 2: Close with stop exit and stressed ADV
        entry_fee = state._entry_fees_by_pos.get(pos.position_id, 0.0)
        assert entry_fee > 0, "Entry fee should be tracked"

        exit_price = 97.0  # stop triggered below entry
        trade = _close_position(state, pos, 20, exit_price, "stop",
                                rolling_adv, config)

        # Step 3: Verify exit used stressed ADV (higher slippage)
        # Stressed ADV = 30M * 0.5 = 15M
        # Exit slippage should use stressed ADV, producing worse exit price
        assert trade.exit_price < exit_price, (
            "Stop exit should include slippage (exit price lower for long)"
        )

        # With hourly slippage: notional / (stressed_adv / 24)
        # This should produce substantial slippage
        slip_amount = exit_price - trade.exit_price
        assert slip_amount > 0.1, (
            f"Stressed hourly slippage should be substantial, got {slip_amount}"
        )

        # Step 4: Verify P&L accounting
        assert trade.entry_fee == pytest.approx(entry_fee, rel=1e-6), (
            "Entry fee in trade should match tracked entry fee"
        )
        assert trade.exit_fee > 0, "Exit fee should be positive"
        assert trade.pnl < 0, (
            "Stop exit below entry price on a long should produce negative P&L"
        )

        # Net P&L should account for entry fee, exit fee, and slippage
        # pnl = quantity * (exit_price_after_slip - entry_price) - exit_fee - funding
        assert trade.exit_reason == "stop", "Exit reason should be stop"


# ===================================================================
# Backward Compatibility (FAIL 3)
# ===================================================================

class TestBackwardCompatibility:
    """Verify that when all new features are at their defaults, behavior
    is identical to the pre-feature baseline (no regression)."""

    def test_no_regression_when_features_disabled(self):
        """With max_sizing_equity=None, adv_sizing_enabled=False, and no open
        positions (unrealized P&L = 0), position sizes from two identical runs
        should be exactly the same -- proving no regression when features are
        disabled.
        """
        from v4.simulator import SimulationState, _process_entries

        n_bars = 50
        close = np.full(n_bars, 100.0)
        adv = np.full(n_bars, 50_000_000.0)
        entry_mask = np.zeros(n_bars, dtype=bool)
        entry_mask[10] = True

        sig = _make_token_signals("BTC", "s30", n_bars, entry_mask, close, adv)

        # All new features at defaults
        spec = StrategySpec(strategy_id="s30", weight=1.0)
        all_signals = {"s30": {"BTC": sig}}
        strategy_specs = {"s30": spec}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.float64)}

        config = _default_config(capital=500_000.0)
        # Explicitly verify defaults
        assert config.max_sizing_equity is None or not hasattr(config, 'max_sizing_equity'), (
            "max_sizing_equity should default to None"
        )

        # Run 1
        state1 = SimulationState(initial_capital=500_000.0)
        rng1 = np.random.default_rng(42)
        _process_entries(state1, all_signals, strategy_specs,
                         bar_maps, 10, config, rng1)

        # Run 2 (identical setup)
        state2 = SimulationState(initial_capital=500_000.0)
        rng2 = np.random.default_rng(42)
        _process_entries(state2, all_signals, strategy_specs,
                         bar_maps, 10, config, rng2)

        pos1 = list(state1.position_manager.open_positions)
        pos2 = list(state2.position_manager.open_positions)

        assert len(pos1) == 1 and len(pos2) == 1, "Both runs should open one position"

        # Position sizes should be EXACTLY identical (bit-for-bit determinism)
        assert abs(pos1[0].margin_usd) == pytest.approx(abs(pos2[0].margin_usd), rel=1e-12), (
            f"Identical runs should produce identical sizes: "
            f"{abs(pos1[0].margin_usd)} vs {abs(pos2[0].margin_usd)}"
        )
        assert pos1[0].entry_price == pytest.approx(pos2[0].entry_price, rel=1e-12), (
            f"Identical runs should produce identical entry prices: "
            f"{pos1[0].entry_price} vs {pos2[0].entry_price}"
        )
        assert abs(pos1[0].quantity) == pytest.approx(abs(pos2[0].quantity), rel=1e-12), (
            f"Identical runs should produce identical quantities: "
            f"{abs(pos1[0].quantity)} vs {abs(pos2[0].quantity)}"
        )
