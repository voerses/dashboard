"""Comprehensive tests for Tier 1 fixes across v4 modules.

Tests cover four recent changes:
  1. Widened NON_OVERRIDABLE params (v4/config.py)
  2. SlippageModel protocol + registry (v4/sizing.py)
  3. BarContext expansion (v4/exit_handlers.py)
  4. Paper engine raw mode support (v4/paper_config.py, v4/paper_engine.py)

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v4.config import (
    NON_OVERRIDABLE,
    SAFETY_RAILS,
    PortfolioConfig,
    SizingDefaults,
    StrategySpec,
    resolve_sizing,
    _validate_composite,
)
from v4.sizing import (
    SlippageModel,
    SqrtImpactSlippage,
    _SLIPPAGE_MODELS,
    compute_slippage_bps,
    get_slippage_model,
)
from v4.exit_handlers import BarContext, ExitCheck, StopLossHandler, build_exit_chain, run_exit_handlers
from v4.signals import TokenSignals
from v4.paper_config import PaperConfig, load_paper_config


# ===========================================================================
# Helpers
# ===========================================================================

def _write_config(data: dict) -> str:
    """Write a config dict to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


@dataclass
class _StubSignals:
    """Minimal TokenSignals substitute for handler tests."""
    bear_target_mult: float = 0.0
    bear_max_hold: int = 0
    rsi: Optional[np.ndarray] = None
    mean_target_vals: Optional[np.ndarray] = None
    regime: np.ndarray = field(default_factory=lambda: np.full(200, 3, dtype=np.int8))
    high: np.ndarray = field(default_factory=lambda: np.full(200, 105.0, dtype=np.float32))
    low: np.ndarray = field(default_factory=lambda: np.full(200, 95.0, dtype=np.float32))
    perp_high: Optional[np.ndarray] = None
    perp_low: Optional[np.ndarray] = None
    per_bar_is_perp: Optional[np.ndarray] = None


@dataclass
class _StubSpec:
    """Minimal StrategySpec substitute."""
    circuit_breaker_r: float = 0.0


def _make_position(
    direction=1,
    entry_price=100.0,
    stop_price=95.0,
    highest=105.0,
    lowest=95.0,
    initial_risk=5.0,
    no_stop_bars=6,
    min_hold=6,
    max_hold=720,
    trail_mult=3.0,
    target_mult=6.0,
    convex_exit=False,
    rsi_exit_level=999.0,
    breakeven_atr=0.0,
    breakeven_triggered=False,
    exit_regimes=None,
    is_perp=False,
    funding_exit_threshold=0.0,
    margin_usd=1000.0,
    cumulative_funding=0.0,
    partial_tp_atr=0.0,
    partial_closed=False,
    chandelier_lookback=0,
    trail_schedule=None,
    time_trail_schedule=None,
    max_trail_mult_arr=None,
    convex_bar_thresholds=(48, 12),
    convex_multipliers=(2.0, 1.5, 0.3),
    regime_exit_min_bars=6,
    entry_bar=0,
    leg="primary",
):
    from v4.position import Position
    return Position(
        position_id="TEST:s30:0:primary",
        token="TEST",
        strategy_id="s30",
        leg=leg,
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=10.0 * direction,
        margin_usd=margin_usd,
        leverage=1.0,
        is_perp=is_perp,
        fee_rate=0.001,
        stop_mult=5.0,
        trail_mult=trail_mult,
        target_mult=target_mult,
        no_stop_bars=no_stop_bars,
        min_hold=min_hold,
        max_hold=max_hold,
        exit_regimes=exit_regimes or {0},
        convex_exit=convex_exit,
        rsi_exit_level=rsi_exit_level,
        regime_exit_min_bars=regime_exit_min_bars,
        convex_bar_thresholds=convex_bar_thresholds,
        convex_multipliers=convex_multipliers,
        trail_schedule=trail_schedule,
        time_trail_schedule=time_trail_schedule,
        max_trail_mult_arr=max_trail_mult_arr,
        funding_exit_threshold=funding_exit_threshold,
        partial_tp_atr=partial_tp_atr,
        breakeven_atr=breakeven_atr,
        breakeven_triggered=breakeven_triggered,
        chandelier_lookback=chandelier_lookback,
        stop_price=stop_price,
        highest=highest,
        lowest=lowest,
        initial_risk=initial_risk,
        cumulative_funding=cumulative_funding,
        partial_closed=partial_closed,
    )


def _make_bar(
    close=100.0, high=105.0, low=95.0, atr=5.0,
    rsi=50.0, regime=3, bars_held=10, local_bar=10, funding_val=0.0,
    volume=float('nan'), vol_20=float('nan'), ret_1h=float('nan'),
) -> BarContext:
    return BarContext(
        close=close, high=high, low=low, atr=atr,
        rsi=rsi, regime=regime, bars_held=bars_held,
        local_bar=local_bar, funding_val=funding_val,
        volume=volume, vol_20=vol_20, ret_1h=ret_1h,
    )


# ===========================================================================
# 1. Widened NON_OVERRIDABLE params (v4/config.py)
# ===========================================================================

class TestWidenedNonOverridable:
    """Verify the 5 ADV curve params moved from NON_OVERRIDABLE to SAFETY_RAILS."""

    # --- The 5 params ARE overridable via resolve_sizing() ---

    @pytest.mark.parametrize("param,value", [
        ("kelly_mult_floor", 0.10),
        ("kelly_mult_range", 0.20),
        ("cap_pct_floor", 0.01),
        ("cap_pct_range", 0.05),
        ("adv_scaling_divisor", 3.0),
    ])
    def test_adv_curve_params_are_overridable(self, param, value):
        """The 5 ADV curve params should be overridable via resolve_sizing()."""
        sd = SizingDefaults()
        result = resolve_sizing(sd, {param: value})
        assert getattr(result, param) == value

    def test_all_five_params_overridable_together(self):
        """All 5 ADV curve params can be overridden simultaneously."""
        sd = SizingDefaults()
        overrides = {
            "kelly_mult_floor": 0.10,
            "kelly_mult_range": 0.20,
            "cap_pct_floor": 0.01,
            "cap_pct_range": 0.05,
            "adv_scaling_divisor": 3.0,
        }
        result = resolve_sizing(sd, overrides)
        for key, val in overrides.items():
            assert getattr(result, key) == val

    # --- Safety rail bounds are respected ---

    @pytest.mark.parametrize("param,lo,hi", [
        ("kelly_mult_floor", 0.05, 0.40),
        ("kelly_mult_range", 0.10, 0.80),
        ("cap_pct_floor", 0.005, 0.08),
        ("cap_pct_range", 0.02, 0.30),
        ("adv_scaling_divisor", 1.0, 20.0),
    ])
    def test_adv_curve_params_respect_safety_rails(self, param, lo, hi):
        """Each ADV curve param should have the correct safety rail bounds."""
        assert param in SAFETY_RAILS
        actual_lo, actual_hi = SAFETY_RAILS[param]
        assert actual_lo == lo
        assert actual_hi == hi

    @pytest.mark.parametrize("param,lo,hi", [
        ("kelly_mult_floor", 0.05, 0.40),
        ("kelly_mult_range", 0.10, 0.80),
        ("cap_pct_floor", 0.005, 0.08),
        ("cap_pct_range", 0.02, 0.30),
        ("adv_scaling_divisor", 1.0, 20.0),
    ])
    def test_adv_curve_params_reject_below_floor(self, param, lo, hi):
        """Values below the safety rail floor are rejected."""
        sd = SizingDefaults()
        below = lo - (lo * 0.01 if lo > 1 else 0.001)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {param: below})

    @pytest.mark.parametrize("param,lo,hi", [
        ("kelly_mult_floor", 0.05, 0.40),
        ("kelly_mult_range", 0.10, 0.80),
        ("cap_pct_floor", 0.005, 0.08),
        ("cap_pct_range", 0.02, 0.30),
        ("adv_scaling_divisor", 1.0, 20.0),
    ])
    def test_adv_curve_params_reject_above_ceiling(self, param, lo, hi):
        """Values above the safety rail ceiling are rejected."""
        sd = SizingDefaults()
        above = hi + (hi * 0.01 if hi > 1 else 0.001)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {param: above})

    @pytest.mark.parametrize("param,lo,hi", [
        ("kelly_mult_floor", 0.05, 0.40),
        ("kelly_mult_range", 0.10, 0.80),
        ("cap_pct_floor", 0.005, 0.08),
        ("cap_pct_range", 0.02, 0.30),
        ("adv_scaling_divisor", 1.0, 20.0),
    ])
    def test_adv_curve_params_accept_at_boundaries(self, param, lo, hi):
        """Values exactly at the boundary should be accepted."""
        sd = SizingDefaults()
        # At floor
        result_lo = resolve_sizing(sd, {param: lo})
        assert getattr(result_lo, param) == lo
        # At ceiling
        result_hi = resolve_sizing(sd, {param: hi})
        assert getattr(result_hi, param) == hi

    # --- The 3 remaining params are still non-overridable ---

    def test_non_overridable_contains_exactly_three(self):
        """NON_OVERRIDABLE should contain exactly 3 safety-critical params."""
        assert NON_OVERRIDABLE == {"unrealized_pnl_floor", "funding_buffer_pct", "vol_floor"}

    @pytest.mark.parametrize("param", [
        "unrealized_pnl_floor",
        "funding_buffer_pct",
        "vol_floor",
    ])
    def test_remaining_params_still_non_overridable(self, param):
        """The 3 safety-critical params must still be rejected by resolve_sizing."""
        sd = SizingDefaults()
        with pytest.raises(ValueError, match="not overridable"):
            resolve_sizing(sd, {param: 0.5})

    def test_adv_curve_params_not_in_non_overridable(self):
        """The 5 moved params should NOT be in NON_OVERRIDABLE."""
        moved_params = {
            "kelly_mult_floor", "kelly_mult_range",
            "cap_pct_floor", "cap_pct_range",
            "adv_scaling_divisor",
        }
        for param in moved_params:
            assert param not in NON_OVERRIDABLE, (
                f"{param} should not be in NON_OVERRIDABLE anymore"
            )

    # --- Composite validation still catches dangerous combos ---

    def test_composite_validation_catches_dangerous_adv_curve_combo(self):
        """Widened params + aggressive target_vol can still be rejected by composite check."""
        sd = SizingDefaults()
        # kelly_mult_floor=0.30 + kelly_mult_range=0.70 => max_kelly = (0.30+0.70)*1.0 = 1.0
        # target_vol=0.05, vol_floor=0.005 => vol_adj = 10.0
        # worst_case = 1.0 * 10.0 = 10.0 > 8.0 threshold
        with pytest.raises(ValueError, match="Composite sizing check"):
            resolve_sizing(sd, {
                "kelly_mult_floor": 0.30,
                "kelly_mult_range": 0.70,
                "target_vol": 0.05,
            })

    def test_composite_validation_passes_safe_adv_curve_combo(self):
        """Conservative ADV curve overrides pass composite validation."""
        sd = SizingDefaults()
        # kelly_mult_floor=0.10 + kelly_mult_range=0.20 => max_kelly = (0.10+0.20)*1.0 = 0.30
        # default target_vol=0.02, vol_floor=0.005 => vol_adj = 4.0
        # worst_case = 0.30 * 4.0 = 1.2 < 8.0 threshold
        result = resolve_sizing(sd, {
            "kelly_mult_floor": 0.10,
            "kelly_mult_range": 0.20,
        })
        assert result.kelly_mult_floor == 0.10
        assert result.kelly_mult_range == 0.20


# ===========================================================================
# 2. SlippageModel protocol + registry (v4/sizing.py)
# ===========================================================================

class TestSlippageModelRegistry:
    """Tests for the SlippageModel protocol, SqrtImpactSlippage, and registry."""

    def test_get_slippage_model_sqrt_returns_instance(self):
        """get_slippage_model('sqrt') returns a SlippageModel instance."""
        model = get_slippage_model("sqrt")
        assert isinstance(model, SlippageModel)

    def test_get_slippage_model_default_is_sqrt(self):
        """Default get_slippage_model() returns the sqrt model."""
        model = get_slippage_model()
        assert isinstance(model, SqrtImpactSlippage)

    def test_get_slippage_model_unknown_raises_keyerror(self):
        """get_slippage_model('unknown') raises KeyError."""
        with pytest.raises(KeyError):
            get_slippage_model("unknown")

    def test_sqrt_impact_matches_compute_slippage_bps(self):
        """SqrtImpactSlippage.compute_slippage matches compute_slippage_bps output."""
        model = SqrtImpactSlippage()
        pos_usd = 500_000.0
        adv = 10_000_000.0
        base_spread = 3.0
        impact = 0.03
        max_slip = 300.0

        via_model = model.compute_slippage(pos_usd, adv, base_spread, impact, max_slip)
        via_wrapper = compute_slippage_bps(pos_usd, adv, base_spread, impact, max_slip)

        assert via_model == pytest.approx(via_wrapper, rel=1e-10)

    def test_sqrt_impact_matches_for_various_positions(self):
        """SqrtImpactSlippage matches compute_slippage_bps across a range of inputs."""
        model = SqrtImpactSlippage()
        test_cases = [
            (100_000, 50_000_000),
            (1_000_000, 5_000_000),
            (10_000_000, 1_000_000),
            (50_000, 100_000_000),
        ]
        for pos_usd, adv in test_cases:
            via_model = model.compute_slippage(pos_usd, adv)
            via_wrapper = compute_slippage_bps(pos_usd, adv)
            assert via_model == pytest.approx(via_wrapper, rel=1e-10), (
                f"Mismatch for pos_usd={pos_usd}, adv={adv}"
            )

    def test_sqrt_impact_formula_correctness(self):
        """SqrtImpactSlippage produces the expected value from the formula."""
        model = SqrtImpactSlippage()
        pos_usd = 1_000_000.0
        adv = 24_000_000.0  # adv/24 = 1M
        base_spread = 3.0
        impact = 0.03
        max_slip = 300.0

        # participation = 1M / 1M = 1.0
        # slip_bps = 3.0 + 0.03 * sqrt(1.0) * 10000 = 3.0 + 300.0 = 303.0
        # capped at 300.0
        expected = 300.0
        result = model.compute_slippage(pos_usd, adv, base_spread, impact, max_slip)
        assert result == pytest.approx(expected)

    def test_sqrt_impact_small_position(self):
        """Small position relative to ADV produces small slippage (below cap)."""
        model = SqrtImpactSlippage()
        pos_usd = 10_000.0
        adv = 100_000_000.0  # adv/24 ~ 4.17M
        # participation = 10000 / 4170000 ~ 0.0024
        # sqrt(0.0024) ~ 0.049, * 0.03 * 10000 = 14.7, + 3.0 = 17.7
        result = model.compute_slippage(pos_usd, adv)
        assert result < 300.0  # well below cap
        assert result > 3.0    # above base spread

    def test_custom_slippage_model_can_be_registered(self):
        """A custom slippage model can be registered and retrieved."""

        class FixedSlippage:
            """Always returns a fixed slippage for testing."""
            def compute_slippage(self, pos_usd, adv, base_spread_bps=3.0,
                                 impact_coeff=0.03, max_slip_bps=300.0):
                return 42.0

        _SLIPPAGE_MODELS["fixed_test"] = FixedSlippage()
        try:
            model = get_slippage_model("fixed_test")
            assert isinstance(model, SlippageModel)
            result = model.compute_slippage(1000, 1_000_000)
            assert result == 42.0
        finally:
            del _SLIPPAGE_MODELS["fixed_test"]

    def test_custom_slippage_model_used_via_registry(self):
        """Verify that a custom model can fully replace the default."""

        class ZeroSlippage:
            def compute_slippage(self, pos_usd, adv, base_spread_bps=3.0,
                                 impact_coeff=0.03, max_slip_bps=300.0):
                return 0.0

        _SLIPPAGE_MODELS["zero_test"] = ZeroSlippage()
        try:
            model = get_slippage_model("zero_test")
            assert model.compute_slippage(10_000_000, 1_000) == 0.0
        finally:
            del _SLIPPAGE_MODELS["zero_test"]


class TestStrategySpecSlippageModel:
    """Tests for StrategySpec.slippage_model field."""

    def test_strategy_spec_slippage_model_default(self):
        """StrategySpec.slippage_model defaults to 'sqrt'."""
        spec = StrategySpec(strategy_id="test")
        assert spec.slippage_model == "sqrt"

    def test_strategy_spec_slippage_model_custom(self):
        """StrategySpec can hold a custom slippage_model value."""
        spec = StrategySpec(strategy_id="test", slippage_model="custom")
        assert spec.slippage_model == "custom"

    def test_strategy_spec_from_dict_default_slippage(self):
        """StrategySpec.from_dict without slippage_model defaults to 'sqrt'."""
        d = {"strategy_id": "s30", "market": "spot"}
        spec = StrategySpec.from_dict(d)
        assert spec.slippage_model == "sqrt"

    def test_strategy_spec_from_dict_parses_slippage_model(self):
        """StrategySpec.from_dict correctly parses slippage_model field."""
        d = {"strategy_id": "s30", "market": "spot", "slippage_model": "custom_impact"}
        spec = StrategySpec.from_dict(d)
        assert spec.slippage_model == "custom_impact"

    def test_strategy_spec_from_dict_preserves_other_fields(self):
        """Parsing slippage_model does not affect other fields."""
        d = {
            "strategy_id": "s30",
            "market": "spot",
            "weight": 0.5,
            "max_positions": 20,
            "slippage_model": "my_model",
        }
        spec = StrategySpec.from_dict(d)
        assert spec.slippage_model == "my_model"
        assert spec.weight == 0.5
        assert spec.max_positions == 20


# ===========================================================================
# 3. BarContext expansion (v4/exit_handlers.py)
# ===========================================================================

class TestBarContextExpansion:
    """Tests for the 3 new BarContext fields: volume, vol_20, ret_1h."""

    def test_bar_context_new_fields_explicit(self):
        """BarContext can be constructed with explicit new fields."""
        bar = BarContext(
            close=100.0, high=105.0, low=95.0, atr=5.0,
            rsi=50.0, regime=3, bars_held=10, local_bar=10, funding_val=0.0,
            volume=1_000_000.0, vol_20=0.025, ret_1h=0.005,
        )
        assert bar.volume == 1_000_000.0
        assert bar.vol_20 == 0.025
        assert bar.ret_1h == 0.005

    def test_bar_context_new_fields_default_nan(self):
        """BarContext defaults new fields to NaN when not provided."""
        bar = BarContext(
            close=100.0, high=105.0, low=95.0, atr=5.0,
            rsi=50.0, regime=3, bars_held=10, local_bar=10, funding_val=0.0,
        )
        assert math.isnan(bar.volume)
        assert math.isnan(bar.vol_20)
        assert math.isnan(bar.ret_1h)

    def test_bar_context_frozen(self):
        """BarContext is frozen (immutable)."""
        bar = _make_bar(volume=100.0)
        with pytest.raises(AttributeError):
            bar.volume = 200.0

    def test_bar_context_backward_compat_with_make_bar(self):
        """The _make_bar helper creates valid BarContext with NaN defaults for new fields."""
        bar = _make_bar()
        assert math.isnan(bar.volume)
        assert math.isnan(bar.vol_20)
        assert math.isnan(bar.ret_1h)
        # Original fields still work
        assert bar.close == 100.0
        assert bar.atr == 5.0

    def test_bar_context_new_fields_with_specific_values(self):
        """_make_bar can pass specific values for new fields."""
        bar = _make_bar(volume=500_000.0, vol_20=0.03, ret_1h=-0.01)
        assert bar.volume == 500_000.0
        assert bar.vol_20 == 0.03
        assert bar.ret_1h == -0.01


class TestTokenSignalsNewFields:
    """Tests for the 3 new optional TokenSignals fields."""

    def test_token_signals_new_fields_default_none(self):
        """TokenSignals volume, vol_20, ret_1h default to None."""
        n = 100
        ts = TokenSignals(
            token="BTC",
            strategy_id="s30",
            n_bars=n,
            timestamps=np.arange(n),
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            close=np.full(n, 100.0),
            high=np.full(n, 105.0),
            low=np.full(n, 95.0),
            atr=np.full(n, 5.0),
            rolling_adv=np.full(n, 1e7),
            regime=np.full(n, 3, dtype=np.int8),
            funding_1h=np.zeros(n),
            exit_regimes={0},
            stop_mult=np.full(n, 3.0),
            trail_mult=np.full(n, 3.0),
            target_mult=6.0,
            no_stop_bars=6,
            min_hold=6,
            max_hold=720,
            edge=0.2,
            size_multiplier=np.ones(n),
            cap_multiplier=np.ones(n),
            leverage=np.ones(n),
            max_trade_pct=0.0,
        )
        assert ts.volume is None
        assert ts.vol_20 is None
        assert ts.ret_1h is None

    def test_token_signals_new_fields_explicit_arrays(self):
        """TokenSignals can be given explicit arrays for the new fields."""
        n = 100
        vol_arr = np.full(n, 1e6, dtype=np.float32)
        vol20_arr = np.full(n, 0.03, dtype=np.float32)
        ret1h_arr = np.full(n, 0.001, dtype=np.float32)

        ts = TokenSignals(
            token="ETH",
            strategy_id="s30",
            n_bars=n,
            timestamps=np.arange(n),
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            close=np.full(n, 100.0),
            high=np.full(n, 105.0),
            low=np.full(n, 95.0),
            atr=np.full(n, 5.0),
            rolling_adv=np.full(n, 1e7),
            regime=np.full(n, 3, dtype=np.int8),
            funding_1h=np.zeros(n),
            exit_regimes={0},
            stop_mult=np.full(n, 3.0),
            trail_mult=np.full(n, 3.0),
            target_mult=6.0,
            no_stop_bars=6,
            min_hold=6,
            max_hold=720,
            edge=0.2,
            size_multiplier=np.ones(n),
            cap_multiplier=np.ones(n),
            leverage=np.ones(n),
            max_trade_pct=0.0,
            volume=vol_arr,
            vol_20=vol20_arr,
            ret_1h=ret1h_arr,
        )
        assert ts.volume is not None
        np.testing.assert_array_equal(ts.volume, vol_arr)
        assert ts.vol_20 is not None
        np.testing.assert_array_equal(ts.vol_20, vol20_arr)
        assert ts.ret_1h is not None
        np.testing.assert_array_equal(ts.ret_1h, ret1h_arr)


class TestExitHandlersWithExpandedBarContext:
    """Regression: existing exit handlers still work with expanded BarContext."""

    def test_stop_loss_handler_with_new_fields(self):
        """StopLossHandler works correctly when BarContext has new fields."""
        handler = StopLossHandler()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        bar = _make_bar(low=94.0, bars_held=10, volume=1e6, vol_20=0.03, ret_1h=-0.01)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "stop"

    def test_full_exit_chain_with_expanded_context(self):
        """Full exit chain works with BarContext containing new fields."""
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        bar = _make_bar(low=94.0, bars_held=10, local_bar=10,
                        volume=500_000.0, vol_20=0.02, ret_1h=0.003)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert result.should_exit
        assert result.reason == "stop"

    def test_no_exit_with_expanded_context(self):
        """No exit triggered with expanded BarContext fields set."""
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position(direction=1, stop_price=90.0, no_stop_bars=6, max_hold=720)
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        bar = _make_bar(low=96.0, high=104.0, bars_held=10, local_bar=10,
                        volume=1e6, vol_20=0.025, ret_1h=0.001)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert not result.should_exit

    def test_bar_context_nan_defaults_dont_break_handlers(self):
        """NaN defaults for new fields do not break any exit handlers."""
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        # New fields default to NaN
        bar = _make_bar(low=94.0, bars_held=10, local_bar=10)
        assert math.isnan(bar.volume)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert result.should_exit
        assert result.reason == "stop"


# ===========================================================================
# 4. Paper engine raw mode support (v4/paper_config.py, v4/paper_engine.py)
# ===========================================================================

class TestPaperConfigRawMode:
    """Tests for raw_mode, raw_max_positions, skip_walk_forward in paper config."""

    def test_load_paper_config_raw_mode_true(self):
        """load_paper_config parses raw_mode=true from JSON."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "raw_mode": True,
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.raw_mode is True

    def test_load_paper_config_raw_mode_false_default(self):
        """load_paper_config defaults raw_mode to False when not in JSON."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.raw_mode is False

    def test_load_paper_config_raw_max_positions(self):
        """load_paper_config parses raw_max_positions from JSON."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "raw_mode": True,
            "raw_max_positions": 200,
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.raw_max_positions == 200

    def test_load_paper_config_raw_max_positions_default(self):
        """raw_max_positions defaults to 500."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.raw_max_positions == 500

    def test_load_paper_config_skip_walk_forward(self):
        """load_paper_config parses skip_walk_forward from JSON."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "skip_walk_forward": True,
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.skip_walk_forward is True

    def test_load_paper_config_skip_walk_forward_default(self):
        """skip_walk_forward defaults to False."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.skip_walk_forward is False

    def test_paper_config_inherits_raw_mode_from_portfolio_config(self):
        """PaperConfig (subclass of PortfolioConfig) exposes raw_mode field."""
        config = PaperConfig(raw_mode=True)
        assert config.raw_mode is True
        assert isinstance(config, PortfolioConfig)
        # PortfolioConfig defines raw_mode, PaperConfig inherits it
        assert hasattr(config, "raw_mode")

    def test_paper_config_inherits_raw_max_positions(self):
        """PaperConfig exposes raw_max_positions inherited from PortfolioConfig."""
        config = PaperConfig(raw_max_positions=250)
        assert config.raw_max_positions == 250

    def test_paper_config_inherits_skip_walk_forward(self):
        """PaperConfig exposes skip_walk_forward inherited from PortfolioConfig."""
        config = PaperConfig(skip_walk_forward=True)
        assert config.skip_walk_forward is True

    def test_load_paper_config_all_raw_fields_together(self):
        """All three raw mode fields can be set together."""
        data = {
            "strategies": [
                {"strategy_id": "s30", "market": "spot", "weight": 1.0, "max_positions": 15},
            ],
            "raw_mode": True,
            "raw_max_positions": 300,
            "skip_walk_forward": True,
            "max_portfolio_positions": 40,
        }
        config = load_paper_config(_write_config(data))
        assert config.raw_mode is True
        assert config.raw_max_positions == 300
        assert config.skip_walk_forward is True


class TestPaperEngineSlippageModels:
    """Tests for paper engine populating _slippage_models on state objects."""

    def test_pool_mode_state_has_slippage_models(self):
        """Paper engine pool mode populates _slippage_models on state."""
        from v4.paper_engine import PaperPortfolioEngine
        from v4.simulator import SimulationState

        config = PaperConfig(
            strategies=[
                StrategySpec(strategy_id="s30", weight=0.5, max_positions=10),
                StrategySpec(strategy_id="s31", weight=0.5, max_positions=10),
            ],
            capital=100_000,
            max_portfolio_positions=20,
        )
        engine = PaperPortfolioEngine(config)

        # Pool mode: state should have _slippage_models for each strategy
        assert engine.state is not None
        assert "s30" in engine.state._slippage_models
        assert "s31" in engine.state._slippage_models
        # Each should be a SlippageModel instance
        assert isinstance(engine.state._slippage_models["s30"], SlippageModel)
        assert isinstance(engine.state._slippage_models["s31"], SlippageModel)

    def test_pool_mode_slippage_model_is_sqrt_by_default(self):
        """Default slippage_model='sqrt' wires SqrtImpactSlippage to state."""
        from v4.paper_engine import PaperPortfolioEngine

        config = PaperConfig(
            strategies=[
                StrategySpec(strategy_id="s30", weight=1.0, max_positions=10),
            ],
            capital=100_000,
            max_portfolio_positions=20,
        )
        engine = PaperPortfolioEngine(config)

        model = engine.state._slippage_models["s30"]
        assert isinstance(model, SqrtImpactSlippage)

    def test_independent_mode_states_have_slippage_models(self):
        """Paper engine independent mode populates _slippage_models on each strategy state."""
        from v4.paper_engine import PaperPortfolioEngine

        config = PaperConfig(
            mode="independent",
            strategies=[
                StrategySpec(strategy_id="s30", weight=0.5, max_positions=10),
                StrategySpec(strategy_id="s31", weight=0.5, max_positions=10),
            ],
            capital=100_000,
            max_portfolio_positions=20,
        )
        engine = PaperPortfolioEngine(config)

        # Independent mode: each strategy_state should have _slippage_models
        assert "s30" in engine.strategy_states
        assert "s31" in engine.strategy_states
        assert "s30" in engine.strategy_states["s30"]._slippage_models
        assert "s31" in engine.strategy_states["s31"]._slippage_models
        assert isinstance(engine.strategy_states["s30"]._slippage_models["s30"], SlippageModel)
        assert isinstance(engine.strategy_states["s31"]._slippage_models["s31"], SlippageModel)

    def test_independent_mode_state_is_none(self):
        """In independent mode, the shared engine.state should be None."""
        from v4.paper_engine import PaperPortfolioEngine

        config = PaperConfig(
            mode="independent",
            strategies=[
                StrategySpec(strategy_id="s30", weight=1.0, max_positions=10),
            ],
            capital=100_000,
            max_portfolio_positions=20,
        )
        engine = PaperPortfolioEngine(config)
        assert engine.state is None

    def test_pool_mode_custom_slippage_model_name(self):
        """A strategy with a custom slippage_model name is wired in pool mode state.

        This test registers a temporary model, creates a strategy spec pointing to it,
        and verifies the engine wires the correct model instance.
        """

        class TestSlippage:
            def compute_slippage(self, pos_usd, adv, base_spread_bps=3.0,
                                 impact_coeff=0.03, max_slip_bps=300.0):
                return 99.0

        _SLIPPAGE_MODELS["test_custom"] = TestSlippage()
        try:
            from v4.paper_engine import PaperPortfolioEngine

            config = PaperConfig(
                strategies=[
                    StrategySpec(strategy_id="s30", weight=1.0, max_positions=10,
                                 slippage_model="test_custom"),
                ],
                capital=100_000,
                max_portfolio_positions=20,
            )
            engine = PaperPortfolioEngine(config)

            model = engine.state._slippage_models["s30"]
            assert model.compute_slippage(1000, 1_000_000) == 99.0
        finally:
            del _SLIPPAGE_MODELS["test_custom"]
