"""M8 AC-Sz2 — SizingRequest schema: 5 explicit fields + mutex + scalar-only.

All tests MUST FAIL today — v5.sizing package does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestSizingRequestFields:
    """AC-Sz2 — every sizing request includes 5 fields with correct defaults."""

    def test_fixed_fraction_request_constructs(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert req.intent == SizingIntent.FIXED_FRACTION
        assert req.fraction_of_equity == 0.02

    def test_fixed_notional_request_constructs(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_NOTIONAL,
            notional_usd=5000.0,
        )
        assert req.intent == SizingIntent.FIXED_NOTIONAL
        assert req.notional_usd == 5000.0

    def test_leverage_default_is_1(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert req.leverage == 1.0

    def test_reduce_only_default_false(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert req.reduce_only is False

    def test_margin_mode_default_isolated(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert req.margin_mode == "isolated"

    def test_leverage_explicit_override(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
            leverage=3.5,
        )
        assert req.leverage == 3.5

    def test_reduce_only_explicit_true(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
            reduce_only=True,
        )
        assert req.reduce_only is True

    def test_margin_mode_cross(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
            margin_mode="cross",
        )
        assert req.margin_mode == "cross"


class TestSizingRequestMutualExclusion:
    """AC-Sz2 — fraction_of_equity and notional_usd are mutually exclusive."""

    def test_both_fraction_and_notional_raises(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(ValueError):
            SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
                fraction_of_equity=0.02,
                notional_usd=5000.0,
            )

    def test_fixed_fraction_without_fraction_raises(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(ValueError):
            SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
                notional_usd=5000.0,
            )

    def test_fixed_notional_without_notional_raises(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(ValueError):
            SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                fraction_of_equity=0.02,
            )

    def test_neither_field_set_raises(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(ValueError):
            SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
            )


class TestMarginModeLiteralValidation:
    """AC-Sz2 — margin_mode must be 'isolated' or 'cross'."""

    def test_invalid_margin_mode_raises(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises((ValueError, TypeError)):
            SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
                fraction_of_equity=0.02,
                margin_mode="portfolio",
            )

    @pytest.mark.parametrize("mode", ["isolated", "cross"])
    def test_valid_margin_mode_accepted(self, mode):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
            margin_mode=mode,
        )
        assert req.margin_mode == mode


class TestScalarOnlyInvariant:
    """AC-Sz2 — fields are pure scalars; engine never reads bar-arrays."""

    def test_array_fraction_raises_type_error(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(TypeError):
            SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
                fraction_of_equity=np.array([0.02, 0.03]),
            )

    def test_array_notional_raises_type_error(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(TypeError):
            SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=np.array([5000.0, 6000.0]),
            )

    def test_array_leverage_raises_type_error(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        with pytest.raises(TypeError):
            SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
                fraction_of_equity=0.02,
                leverage=np.array([1.0, 2.0]),
            )


class TestAllowOverLeveragedField:
    """AC-Sz2 + brief §M7 Impact item 2 — allow_over_leveraged escape hatch."""

    def test_allow_over_leveraged_default_false(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert req.allow_over_leveraged is False

    def test_allow_over_leveraged_explicit_true(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
            allow_over_leveraged=True,
        )
        assert req.allow_over_leveraged is True
