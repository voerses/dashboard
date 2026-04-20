"""M8 AC-Sz4 — Strategy-side helper library (pure functions).

4 helpers in v5/sizing/helpers.py:
  - vol_target_fraction(target_vol_annual, realized_vol, vol_cap=4.0) -> float
  - kelly_fraction(edge, variance, kelly_mult=0.25) -> float
      (textbook: edge / variance * kelly_mult — NOT v4's kelly_mult * edge)
  - risk_budget_fraction(risk_usd, stop_distance_bps, equity, leverage=1.0)
  - composite_scaled_fraction(base_fraction, composite_score, adv, config=V4_DEFAULTS)
      (v4 curve via ADVScalingConfig frozen dataclass; kelly_mult * edge formula)

All tests MUST FAIL today — v5.sizing.helpers does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestVolTargetFraction:
    """AC-Sz4 — vol_target_fraction = target_vol / realized_vol, capped."""

    @pytest.mark.parametrize(
        "target,realized,cap,expected",
        [
            (0.20, 0.40, 4.0, 0.50),     # target/realized
            (0.20, 0.20, 4.0, 1.00),     # equal
            (0.20, 0.10, 4.0, 2.00),     # realized lower → scale up
            (0.20, 0.01, 4.0, 4.00),     # hit cap
            (0.10, 0.40, 4.0, 0.25),
        ],
    )
    def test_known_values(self, target, realized, cap, expected):
        from v5.sizing.helpers import vol_target_fraction
        assert vol_target_fraction(target, realized, vol_cap=cap) == pytest.approx(expected)

    def test_zero_realized_caps_at_vol_cap(self):
        from v5.sizing.helpers import vol_target_fraction
        # Division-by-zero must not raise — cap applies
        result = vol_target_fraction(0.20, 0.0, vol_cap=4.0)
        assert result == pytest.approx(4.0)


class TestKellyFractionTextbook:
    """AC-Sz4 — kelly_fraction uses textbook edge/variance * kelly_mult."""

    @pytest.mark.parametrize(
        "edge,variance,kelly_mult,expected",
        [
            (0.02, 0.04, 0.25, 0.02 / 0.04 * 0.25),  # 0.125
            (0.01, 0.02, 0.25, 0.01 / 0.02 * 0.25),  # 0.125
            (0.05, 0.10, 0.50, 0.05 / 0.10 * 0.50),  # 0.25
            (0.04, 0.04, 1.00, 1.0),
        ],
    )
    def test_textbook_formula(self, edge, variance, kelly_mult, expected):
        from v5.sizing.helpers import kelly_fraction
        result = kelly_fraction(edge, variance, kelly_mult=kelly_mult)
        assert result == pytest.approx(expected)

    def test_zero_variance_returns_zero_not_inf(self):
        from v5.sizing.helpers import kelly_fraction
        result = kelly_fraction(0.02, 0.0, kelly_mult=0.25)
        assert result == pytest.approx(0.0)


class TestRiskBudgetFraction:
    """AC-Sz4 — risk_budget_fraction(risk_usd, stop_bps, equity, leverage)."""

    @pytest.mark.parametrize(
        "risk_usd,stop_bps,equity,leverage,expected",
        [
            # fraction = risk_usd / (equity * leverage * stop_bps / 10000)
            # (500) / (150000 * 2 * 0.02) = 500 / 6000 = 0.08333...
            (500, 200, 150_000, 2.0, 500 / (150_000 * 2.0 * 0.02)),
            (1000, 100, 100_000, 1.0, 1000 / (100_000 * 1.0 * 0.01)),
            (250, 150, 50_000, 3.0, 250 / (50_000 * 3.0 * 0.015)),
        ],
    )
    def test_known_values(self, risk_usd, stop_bps, equity, leverage, expected):
        from v5.sizing.helpers import risk_budget_fraction
        result = risk_budget_fraction(
            risk_usd=risk_usd,
            stop_distance_bps=stop_bps,
            equity=equity,
            leverage=leverage,
        )
        assert result == pytest.approx(expected)


class TestCompositeScaledFraction:
    """AC-Sz4 — composite_scaled_fraction uses v4 ADV curve.

    Formula: kelly_mult * edge (NOT textbook edge/variance).
    Configurable via ADVScalingConfig frozen dataclass.
    """

    def test_adv_scaling_config_is_frozen_dataclass(self):
        from v5.sizing.helpers import ADVScalingConfig
        cfg = ADVScalingConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.kelly_mult_floor = 99.0

    def test_v4_defaults_exposed(self):
        from v5.sizing.helpers import V4_DEFAULTS, ADVScalingConfig
        assert isinstance(V4_DEFAULTS, ADVScalingConfig)
        assert V4_DEFAULTS.kelly_mult_floor == 0.15
        assert V4_DEFAULTS.kelly_mult_range == 0.35
        assert V4_DEFAULTS.cap_pct_floor == 0.02
        assert V4_DEFAULTS.cap_pct_range == 0.10
        assert V4_DEFAULTS.adv_scaling_divisor == 5.0

    def test_returns_finite_float(self):
        from v5.sizing.helpers import composite_scaled_fraction, V4_DEFAULTS
        import math
        result = composite_scaled_fraction(
            base_fraction=0.02,
            composite_score=0.5,
            adv=1_000_000.0,
            config=V4_DEFAULTS,
        )
        assert isinstance(result, float)
        assert math.isfinite(result)

    @pytest.mark.parametrize(
        "base,score,adv",
        [
            (0.02, 0.0, 1_000_000.0),
            (0.02, 0.5, 1_000_000.0),
            (0.02, 1.0, 1_000_000.0),
            (0.02, 0.5, 100_000.0),
            (0.02, 0.5, 10_000_000.0),
        ],
    )
    def test_monotonic_and_bounded(self, base, score, adv):
        """Result must be non-negative and finite across realistic inputs."""
        from v5.sizing.helpers import composite_scaled_fraction, V4_DEFAULTS
        result = composite_scaled_fraction(
            base_fraction=base,
            composite_score=score,
            adv=adv,
            config=V4_DEFAULTS,
        )
        assert result >= 0.0
        assert result < 10.0  # sanity upper bound

    def test_configurable_via_custom_config(self):
        from v5.sizing.helpers import composite_scaled_fraction, ADVScalingConfig
        custom = ADVScalingConfig(
            kelly_mult_floor=0.30,
            kelly_mult_range=0.70,
            cap_pct_floor=0.05,
            cap_pct_range=0.20,
            adv_scaling_divisor=10.0,
        )
        result_custom = composite_scaled_fraction(0.02, 0.5, 1_000_000.0, config=custom)
        # With different config, result should differ from V4_DEFAULTS
        from v5.sizing.helpers import V4_DEFAULTS
        result_default = composite_scaled_fraction(0.02, 0.5, 1_000_000.0, config=V4_DEFAULTS)
        assert result_custom != pytest.approx(result_default)


class TestHelperPurity:
    """AC-Sz4 — helpers are pure: no engine dep, no file I/O, no time reads."""

    def test_helpers_do_not_import_engine(self):
        import v5.sizing.helpers as h
        # No engine imports allowed at module load time
        src = Path(h.__file__).read_text()
        banned = ["v5.orders", "v5.simulator", "v5.paper_engine", "v5.bar_processor"]
        for token in banned:
            assert token not in src, (
                f"helpers.py must not import {token!r} (pure-function contract)"
            )
