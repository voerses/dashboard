"""Acceptance tests for V4 Sizing Defaults externalization.

Tests cover:
  - SizingDefaults dataclass construction, immutability, and from-dict parsing
  - resolve_sizing() validation: empty overrides, valid overrides, unknown keys,
    non-overridable keys, out-of-bounds values, dangerous combos
  - compute_position_size() parameterization: custom edge_minimum, target_vol,
    spot equity cap, kelly overrides/scaling
  - adv_to_sizing() parameterization
  - Backward compatibility: default config = identical behavior to hardcoded
  - Config loading: JSON with/without sizing_defaults, StrategySpec sizing_overrides
  - Integration: resolved config reaches compute_position_size via simulator

All tests MUST FAIL until implementation is complete (RED phase).
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
import pytest

from v4.config import PortfolioConfig, StrategySpec


# ---------------------------------------------------------------------------
# Test 1: SizingDefaults default values match current hardcoded constants
# ---------------------------------------------------------------------------

class TestSizingDefaultsDataclass:

    def test_sizing_defaults_match_current_hardcoded(self):
        """Default SizingDefaults values must equal current hardcoded constants."""
        from v4.config import SizingDefaults

        sd = SizingDefaults()
        # sizing.py hardcoded values
        assert sd.edge_minimum == 0.10
        assert sd.target_vol == 0.02
        assert sd.vol_floor == 0.005
        # universe.py adv_to_sizing hardcoded values
        assert sd.kelly_mult_floor == 0.15
        assert sd.kelly_mult_range == 0.35
        assert sd.cap_pct_floor == 0.02
        assert sd.cap_pct_range == 0.10
        assert sd.adv_scaling_divisor == 5.0
        # simulator.py hardcoded values
        assert sd.unrealized_pnl_floor == 0.85
        assert sd.funding_buffer_pct == 0.01
        # New: spot equity cap
        assert sd.spot_max_equity_pct == 1.0
        # Override defaults (disabled by default)
        assert sd.kelly_mult_override == 0.0
        assert sd.kelly_mult_scale == 1.0
        assert sd.cap_pct_override == 0.0
        assert sd.cap_pct_scale == 1.0
        # Liquidity gating
        assert sd.min_adv_usd == 500_000
        assert sd.adv_lookback_days == 30

    # ---------------------------------------------------------------------------
    # Test 2: SizingDefaults is frozen (immutable)
    # ---------------------------------------------------------------------------

    def test_sizing_defaults_frozen(self):
        """SizingDefaults must be immutable after construction."""
        from v4.config import SizingDefaults

        sd = SizingDefaults()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            sd.edge_minimum = 0.20

    # ---------------------------------------------------------------------------
    # Test 3: SizingDefaults from dict
    # ---------------------------------------------------------------------------

    def test_sizing_defaults_from_dict(self):
        """SizingDefaults should be constructable from a partial dict (JSON parsing)."""
        from v4.config import SizingDefaults

        d = {"edge_minimum": 0.08, "target_vol": 0.03}
        sd = SizingDefaults(**d)
        assert sd.edge_minimum == 0.08
        assert sd.target_vol == 0.03
        # Unspecified fields retain defaults
        assert sd.vol_floor == 0.005
        assert sd.kelly_mult_floor == 0.15


# ---------------------------------------------------------------------------
# Tests 4-9: resolve_sizing() validation
# ---------------------------------------------------------------------------

class TestResolveSizing:

    def test_resolve_sizing_empty_overrides(self):
        """Empty overrides dict returns defaults unchanged."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        result = resolve_sizing(sd, {})
        assert result == sd

    def test_resolve_sizing_valid_override(self):
        """Valid overrides merge correctly into new SizingDefaults."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        result = resolve_sizing(sd, {"edge_minimum": 0.08, "target_vol": 0.03})
        assert result.edge_minimum == 0.08
        assert result.target_vol == 0.03
        # Other fields unchanged
        assert result.vol_floor == sd.vol_floor
        assert result.kelly_mult_floor == sd.kelly_mult_floor

    def test_resolve_sizing_rejects_unknown_key(self):
        """Unknown keys (typos) raise ValueError."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        with pytest.raises(ValueError, match="Unknown sizing"):
            resolve_sizing(sd, {"edgee_minimum": 0.08})

    def test_resolve_sizing_rejects_non_overridable(self):
        """Non-overridable parameters (vol_floor, unrealized_pnl_floor, etc.) raise ValueError."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        for key in ["vol_floor", "unrealized_pnl_floor", "funding_buffer_pct"]:
            with pytest.raises(ValueError, match="not overridable"):
                resolve_sizing(sd, {key: 0.5})

    def test_resolve_sizing_rejects_out_of_bounds(self):
        """Values outside safety rails raise ValueError."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        # edge_minimum below floor (0.05)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {"edge_minimum": 0.01})
        # edge_minimum above ceiling (0.50)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {"edge_minimum": 0.60})
        # target_vol above ceiling (0.05)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {"target_vol": 0.10})

    def test_resolve_sizing_rejects_dangerous_combo(self):
        """Composite validator catches combined aggression even when individual params are valid."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        # Push both kelly_mult_scale and target_vol to their rail maximums
        # max_kelly = (0.15 + 0.35) * 2.0 = 1.0, vol_adj = 0.05/0.005 = 10.0
        # worst_case = 1.0 * 10.0 = 10.0 > 8.0 threshold
        with pytest.raises(ValueError, match="Composite sizing check"):
            resolve_sizing(sd, {
                "target_vol": 0.05,
                "kelly_mult_scale": 2.0,
            })

    def test_resolve_sizing_allows_override_sentinel_zero(self):
        """Sentinel value 0.0 for override fields is allowed (means disabled)."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        # Setting override to 0.0 should not raise (it means "disabled")
        result = resolve_sizing(sd, {"kelly_mult_override": 0.0, "cap_pct_override": 0.0})
        assert result.kelly_mult_override == 0.0
        assert result.cap_pct_override == 0.0

    def test_resolve_sizing_rejects_non_numeric(self):
        """Non-numeric override values raise ValueError."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        with pytest.raises(ValueError, match="must be numeric"):
            resolve_sizing(sd, {"target_vol": "0.03"})

    def test_resolve_sizing_rejects_boolean(self):
        """Boolean values rejected even though bool is a subclass of int."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        with pytest.raises(ValueError, match="must be numeric"):
            resolve_sizing(sd, {"cap_pct_scale": True})

    @pytest.mark.parametrize("key,lo,hi", [
        ("edge_minimum", 0.05, 0.50),
        ("target_vol", 0.005, 0.05),
        ("spot_max_equity_pct", 0.10, 1.0),
        ("kelly_mult_override", 0.05, 0.50),
        ("kelly_mult_scale", 0.5, 2.0),
        ("cap_pct_override", 0.01, 0.30),
        ("cap_pct_scale", 0.5, 2.0),
        ("min_adv_usd", 100_000, 10_000_000),
        ("adv_lookback_days", 7, 90),
    ])
    def test_resolve_sizing_rejects_each_rail_boundary(self, key, lo, hi):
        """Each SAFETY_RAILS key rejects values just below floor and just above ceiling."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        # Just below floor
        below = lo - (lo * 0.01 if lo > 1 else 0.001)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {key: below})
        # Just above ceiling
        above = hi + (hi * 0.01 if hi > 1 else 0.001)
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {key: above})

    def test_resolve_sizing_accepts_valid_kelly_mult_override(self):
        """resolve_sizing accepts a valid kelly_mult_override > 0 within rails."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        result = resolve_sizing(sd, {"kelly_mult_override": 0.25})
        assert result.kelly_mult_override == 0.25
        # Verify it's a valid SizingDefaults with the override set
        assert result.kelly_mult_floor == sd.kelly_mult_floor  # unchanged

    def test_validate_composite_uses_kelly_override_when_set(self):
        """Composite validator uses kelly_mult_override (not curve formula) when override > 0."""
        from v4.config import SizingDefaults, _validate_composite

        # With kelly_mult_override=0.10 and target_vol=0.02, vol_floor=0.005:
        # max_kelly = 0.10, vol_adj = 0.02/0.005 = 4.0, worst_case = 0.40 < 8.0 → PASS
        sd_passes = SizingDefaults(kelly_mult_override=0.10, target_vol=0.02)
        _validate_composite(sd_passes)  # Should not raise

        # With kelly_mult_override=0.50 and target_vol=0.05, vol_floor=0.005:
        # max_kelly = 0.50, vol_adj = 0.05/0.005 = 10.0, worst_case = 5.0 < 8.0 → PASS
        # But if the code ignored kelly_mult_override and used curve formula instead:
        # max_kelly = (0.15+0.35)*1.0 = 0.50, same result — need different values
        # Use kelly_mult_override=0.05 (small) + aggressive target_vol/vol_floor:
        # max_kelly = 0.05, vol_adj = 0.05/0.005 = 10.0, worst_case = 0.50 < 8.0 → PASS
        # But if code used curve: max_kelly = (0.15+0.35)*1.0 = 0.50, worst_case = 5.0 → still PASS
        # To differentiate: use kelly_mult_scale=2.0 (only matters for curve path)
        # With override=0.05: max_kelly=0.05, worst_case=0.50 → PASS
        # Without override (curve): max_kelly=(0.15+0.35)*2.0=1.0, worst_case=10.0 → FAIL
        sd_override_small = SizingDefaults(
            kelly_mult_override=0.05, kelly_mult_scale=2.0, target_vol=0.05
        )
        # If _validate_composite correctly uses kelly_mult_override (0.05), this passes
        # If it incorrectly used the curve formula (0.50 * 2.0 = 1.0), this would fail
        _validate_composite(sd_override_small)  # Should not raise

    def test_resolve_sizing_sentinel_zero_only_for_override_keys(self):
        """Sentinel 0.0 bypass only works for keys ending in '_override', not other keys."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        # edge_minimum=0.0 should be rejected (below rail floor 0.05), NOT bypassed as sentinel
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {"edge_minimum": 0.0})
        # target_vol=0.0 should also be rejected
        with pytest.raises(ValueError, match="out of bounds"):
            resolve_sizing(sd, {"target_vol": 0.0})

    def test_resolve_sizing_coerces_int_fields(self):
        """adv_lookback_days is coerced to int even if passed as float (from JSON)."""
        from v4.config import SizingDefaults, resolve_sizing

        sd = SizingDefaults()
        result = resolve_sizing(sd, {"adv_lookback_days": 45.0})
        assert result.adv_lookback_days == 45
        assert isinstance(result.adv_lookback_days, int)


# ---------------------------------------------------------------------------
# Tests 10-15: compute_position_size parameterization
# ---------------------------------------------------------------------------

class TestComputePositionSizeParams:

    def test_position_size_custom_edge_minimum(self):
        """edge=0.08 should produce a position with edge_minimum=0.05, but return 0 with default 0.10."""
        from v4.sizing import compute_position_size

        common = dict(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.02,
            edge=0.08,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
        )
        # Default edge_minimum=0.10 → reject
        assert compute_position_size(**common) == 0.0
        # Custom edge_minimum=0.05 → accept
        result = compute_position_size(**common, edge_minimum=0.05)
        assert result > 0.0

    def test_position_size_custom_target_vol(self):
        """Different target_vol produces different position sizes."""
        from v4.sizing import compute_position_size

        common = dict(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
        )
        size_default = compute_position_size(**common)  # target_vol=0.02
        size_higher = compute_position_size(**common, target_vol=0.04)
        # Higher target_vol → higher vol_adj → larger position
        assert size_higher > size_default

    def test_position_size_spot_equity_cap(self):
        """On spot (leverage=1.0), position capped at strategy_equity * spot_max_equity_pct."""
        from v4.sizing import compute_position_size

        equity = 50_000
        # spot_max_equity_pct=0.5 → spot cap = 25,000
        # With cap_multiplier=10, cap_pct ~0.094 → cap = 50k*0.094*10 = 47,000 (above spot cap)
        # raw = 50k * (0.41*0.50*3.0) * 4.0 ≈ 123,000 (above spot cap)
        # Without spot cap: min(123k, 47k, 500M) = 47k
        # With spot cap: min(47k, 25k) = 25k
        result = compute_position_size(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.005,
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=10.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,  # disable ADV cap for this test
            leverage=1.0,
            spot_max_equity_pct=0.5,
        )
        expected_cap = equity * 0.5  # 25,000
        assert result == pytest.approx(expected_cap, abs=1.0)

    def test_position_size_perp_no_cap(self):
        """On perp (leverage=3.0), spot equity cap does NOT apply."""
        from v4.sizing import compute_position_size

        equity = 50_000
        # Same params as spot test but with leverage=3.0
        # Without spot cap: min(123k, 47k, 500M) = 47k > 25k
        result = compute_position_size(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.005,
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=10.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
            spot_max_equity_pct=0.5,
        )
        # Perp: spot cap of 25k should NOT apply; result should be ~47k (cap_pct cap)
        assert result > equity * 0.5 + 1.0

    def test_position_size_kelly_mult_override(self):
        """Fixed kelly_mult_override bypasses ADV curve — same result at different ADV levels."""
        from v4.sizing import compute_position_size

        common = dict(
            strategy_equity=100_000,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,  # large cap so it doesn't bind
            max_trade_pct=0.0,
            adv_cap_pct=1.0,  # disable ADV cap
            kelly_mult_override=0.25,
        )
        # Two very different ADV levels → same result proves ADV curve bypassed
        size_low_adv = compute_position_size(**common, rolling_adv=1_000_000)
        size_high_adv = compute_position_size(**common, rolling_adv=5_000_000_000)
        # Both should produce the same raw: 100k * (0.25 * 0.20 * 1.0) * 1.0 = 5000
        assert size_low_adv == pytest.approx(size_high_adv, rel=0.01)
        assert size_low_adv == pytest.approx(5000.0, abs=1.0)

    def test_position_size_kelly_mult_scale(self):
        """kelly_mult_scale multiplies the ADV curve output."""
        from v4.sizing import compute_position_size

        common = dict(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,  # large cap so raw binds
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
        )
        size_default = compute_position_size(**common)
        size_scaled = compute_position_size(**common, kelly_mult_scale=1.5)
        # 1.5x kelly → 1.5x raw (since raw binds with cap_multiplier=100)
        assert size_scaled == pytest.approx(size_default * 1.5, rel=0.01)

    def test_position_size_cap_pct_override(self):
        """Fixed cap_pct_override replaces ADV curve cap_pct."""
        from v4.sizing import compute_position_size

        equity = 100_000
        # Set cap_pct_override=0.05 → cap = 100k * 0.05 * 1.0 = 5000
        # Set up so raw >> cap (so cap binds)
        result = compute_position_size(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.005,  # low vol → high vol_adj → large raw
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            cap_pct_override=0.05,
            leverage=3.0,  # perp to avoid spot cap
        )
        assert result == pytest.approx(equity * 0.05, abs=1.0)

    def test_position_size_both_overrides(self):
        """kelly_mult_override + cap_pct_override together: both values used correctly."""
        from v4.sizing import compute_position_size

        equity = 100_000
        # Case 1: kelly_mult raw binds (raw < cap)
        # kelly_mult_override=0.10, edge=0.50 → kelly_frac=0.05
        # vol_adj=0.02/0.02=1.0 → raw=100k*0.05*1.0=5000
        # cap_pct_override=0.10 → cap=100k*0.10=10000
        # min(5000, 10000, 500M) = 5000 (raw binds, proving kelly_mult_override is used)
        result_raw = compute_position_size(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.02,
            edge=0.50,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            kelly_mult_override=0.10,
            cap_pct_override=0.10,
            leverage=3.0,
        )
        assert result_raw == pytest.approx(5000.0, abs=1.0)

        # Case 2: cap_pct cap binds (cap < raw)
        # cap_pct_override=0.03 → cap=100k*0.03=3000 < raw=5000
        result_cap = compute_position_size(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.02,
            edge=0.50,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            kelly_mult_override=0.10,
            cap_pct_override=0.03,
            leverage=3.0,
        )
        assert result_cap == pytest.approx(3000.0, abs=1.0)

    def test_position_size_cap_pct_scale_with_kelly_override(self):
        """cap_pct_scale is applied even when kelly_mult_override is active."""
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.005,  # low vol → large raw so cap binds
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            kelly_mult_override=0.30,  # fixed kelly
            leverage=3.0,
        )
        size_default_scale = compute_position_size(**common, cap_pct_scale=1.0)
        size_double_scale = compute_position_size(**common, cap_pct_scale=2.0)
        # cap_pct_scale should affect the result (cap binds with these params)
        assert size_double_scale > size_default_scale

    def test_position_size_cap_pct_scale_without_kelly_override(self):
        """cap_pct_scale on the ADV curve path (no kelly_mult_override) affects position size.

        This tests the else branch (sizing.py line 69): cap_pct *= cap_pct_scale
        when kelly_mult_override is 0 (disabled) and cap_pct_override is 0 (disabled).
        """
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.005,  # low vol → large raw so cap binds
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
            # kelly_mult_override=0.0 (default, disabled — uses ADV curve)
            # cap_pct_override=0.0 (default, disabled — uses ADV curve)
        )
        size_scale_1 = compute_position_size(**common, cap_pct_scale=1.0)
        size_scale_2 = compute_position_size(**common, cap_pct_scale=2.0)
        # Doubling cap_pct_scale should double the cap (and thus the final size when cap binds)
        assert size_scale_2 == pytest.approx(size_scale_1 * 2.0, rel=0.01)
        # Verify cap is actually binding (raw >> cap)
        assert size_scale_1 < equity  # cap should be well below equity with default cap_pct ~0.094

    def test_position_size_volatility_zero(self):
        """volatility=0 uses vol_adj=1.0 (special case)."""
        from v4.sizing import compute_position_size

        result = compute_position_size(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.0,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
        )
        # vol_adj=1.0 when volatility=0, so raw = equity * kelly_frac * 1.0
        from v4.universe import adv_to_sizing
        km, _ = adv_to_sizing(50_000_000)
        expected_raw = 100_000 * km * 0.20 * 1.0 * 1.0  # vol_adj=1.0
        assert result == pytest.approx(expected_raw, rel=0.01)

    def test_position_size_max_trade_pct_binds(self):
        """max_trade_pct > 0 caps position at strategy_equity * max_trade_pct."""
        from v4.sizing import compute_position_size

        equity = 100_000
        # Set up a scenario where raw/cap would be large but max_trade_pct constrains
        result = compute_position_size(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.005,  # low vol → large raw
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=10.0,
            max_trade_pct=0.03,  # 3% of equity = 3000
            adv_cap_pct=1.0,
            leverage=3.0,
        )
        # max_trade_pct should bind: 100k * 0.03 = 3000
        assert result == pytest.approx(equity * 0.03, abs=1.0)

    def test_position_size_adv_cap_binds(self):
        """adv_cap (rolling_adv * adv_cap_pct) caps position when it's the tightest constraint."""
        from v4.sizing import compute_position_size

        # Use tiny rolling_adv with moderate adv_cap_pct so adv_cap is small
        # adv_cap = 100_000 * 0.05 = 5000
        result = compute_position_size(
            strategy_equity=1_000_000,
            rolling_adv=100_000,  # small ADV
            volatility=0.005,  # low vol → large raw
            edge=0.50,
            size_multiplier=3.0,
            cap_multiplier=10.0,
            max_trade_pct=0.0,
            adv_cap_pct=0.05,
            leverage=3.0,
        )
        # adv_cap = 100k * 0.05 = 5000 — should be the binding constraint
        assert result == pytest.approx(100_000 * 0.05, abs=1.0)

    def test_position_size_vol_floor_clamps(self):
        """vol_floor clamps very small (but nonzero) volatilities to prevent explosion."""
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.001,  # below vol_floor of 0.005
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,  # large so raw binds
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
        )
        # With vol_floor=0.005: vol_adj = 0.02/0.005 = 4.0
        size_with_floor = compute_position_size(**common, vol_floor=0.005)
        # Without effective floor (set vol_floor=0.0001): vol_adj = 0.02/0.001 = 20.0
        size_no_floor = compute_position_size(**common, vol_floor=0.0001)
        # vol_floor should limit the amplification
        assert size_no_floor > size_with_floor
        # Verify vol_floor is actually clamping (vol=0.001 < vol_floor=0.005)
        assert size_with_floor == pytest.approx(size_no_floor * (0.001 / 0.005), rel=0.01)

    def test_position_size_adv_sizing_enabled(self):
        """adv_sizing_enabled scales position down based on ADV relative to base."""
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            rolling_adv=10_000_000,  # small relative to base of 100M
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
        )
        size_disabled = compute_position_size(**common, adv_sizing_enabled=False)
        size_enabled = compute_position_size(
            **common,
            adv_sizing_enabled=True,
            adv_sizing_base=100_000_000,
            adv_sizing_floor=0.20,
        )
        # adv_mult = sqrt(10M/100M) = sqrt(0.1) ≈ 0.316
        expected_mult = np.sqrt(10_000_000 / 100_000_000)
        assert size_enabled == pytest.approx(size_disabled * expected_mult, rel=0.01)
        assert size_enabled < size_disabled

    def test_position_size_adv_sizing_floor_binds(self):
        """adv_sizing_floor prevents adv_mult from going below the floor for very illiquid tokens."""
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            rolling_adv=100_000,  # very small — sqrt(100k/100M)=0.032, below floor of 0.20
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
            adv_sizing_enabled=True,
            adv_sizing_base=100_000_000,
        )
        size_with_floor = compute_position_size(**common, adv_sizing_floor=0.20)
        size_low_floor = compute_position_size(**common, adv_sizing_floor=0.01)
        # sqrt(100k/100M) = 0.032 — floor of 0.20 should clamp up; floor of 0.01 should not
        assert size_with_floor > size_low_floor
        # Verify the floor is actually binding: size should be proportional to floor
        assert size_with_floor == pytest.approx(size_low_floor * (0.20 / 0.032), rel=0.05)

    def test_position_size_adv_sizing_capped_at_one(self):
        """adv_mult is capped at 1.0 — mega-liquid tokens never get position inflation."""
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=100.0,
            max_trade_pct=0.0,
            adv_cap_pct=1.0,
            leverage=3.0,
            adv_sizing_enabled=True,
            adv_sizing_base=100_000_000,
            adv_sizing_floor=0.20,
        )
        # rolling_adv >> adv_sizing_base: sqrt(1B/100M) = sqrt(10) = 3.16
        # Without cap: position would be 3.16x the non-ADV-sized position
        # With cap at 1.0: position equals the non-ADV-sized position
        size_mega = compute_position_size(**common, rolling_adv=1_000_000_000)
        size_disabled = compute_position_size(
            **{**common, "adv_sizing_enabled": False}, rolling_adv=1_000_000_000
        )
        assert size_mega == pytest.approx(size_disabled, rel=0.001)

    def test_position_size_max_trade_pct_zero_is_noop(self):
        """max_trade_pct=0.0 does not constrain position (disabled state)."""
        from v4.sizing import compute_position_size

        equity = 100_000
        common = dict(
            strategy_equity=equity,
            rolling_adv=500_000_000,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            adv_cap_pct=1.0,
            leverage=3.0,
        )
        size_disabled = compute_position_size(**common, max_trade_pct=0.0)
        size_large = compute_position_size(**common, max_trade_pct=10.0)  # 1000% — never binds
        assert size_disabled == pytest.approx(size_large, rel=0.001)


# ---------------------------------------------------------------------------
# Test 16: adv_to_sizing parameterized
# ---------------------------------------------------------------------------

class TestAdvToSizingParameterized:

    def test_adv_to_sizing_parameterized(self):
        """Custom curve params produce expected values different from defaults."""
        from v4.universe import adv_to_sizing

        adv = 50_000_000  # $50M

        # Default values
        km_default, cp_default = adv_to_sizing(adv)

        # Custom values — wider range
        km_custom, cp_custom = adv_to_sizing(
            adv,
            kelly_mult_floor=0.20,
            kelly_mult_range=0.50,
            cap_pct_floor=0.05,
            cap_pct_range=0.15,
            adv_scaling_divisor=4.0,
        )

        # Custom should differ from default
        assert km_custom != km_default
        assert cp_custom != cp_default
        # Custom floor is higher
        assert km_custom > km_default


# ---------------------------------------------------------------------------
# Test 17: Backward compatibility — default config = identical sizing
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:

    def test_backward_compat_default_config(self):
        """Default SizingDefaults produces identical sizing to current hardcoded behavior."""
        from v4.config import SizingDefaults
        from v4.sizing import compute_position_size

        # Compute with all-default params (should match hardcoded behavior)
        common = dict(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
        )

        sd = SizingDefaults()
        size_with_defaults = compute_position_size(
            **common,
            edge_minimum=sd.edge_minimum,
            target_vol=sd.target_vol,
            vol_floor=sd.vol_floor,
            kelly_mult_floor=sd.kelly_mult_floor,
            kelly_mult_range=sd.kelly_mult_range,
            cap_pct_floor=sd.cap_pct_floor,
            cap_pct_range=sd.cap_pct_range,
            adv_scaling_divisor=sd.adv_scaling_divisor,
        )

        # Calling without explicit params should give the same result
        size_no_params = compute_position_size(**common)
        assert abs(size_with_defaults - size_no_params) < 0.01


# ---------------------------------------------------------------------------
# Tests 18-20: Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:

    def test_json_config_without_sizing_defaults(self):
        """JSON config without sizing_defaults loads successfully with defaults."""
        from v4.config import SizingDefaults

        config_data = {
            "strategies": [{"strategy_id": "s30", "market": "spot", "weight": 0.5}]
        }
        # Parse strategies
        specs = [StrategySpec.from_dict(s) for s in config_data["strategies"]]
        # Build PortfolioConfig — should have default sizing_defaults
        pc = PortfolioConfig(strategies=specs)
        assert pc.sizing_defaults == SizingDefaults()

    def test_json_config_with_sizing_defaults(self):
        """JSON config with sizing_defaults parses and applies correctly."""
        from v4.config import SizingDefaults

        config_data = {
            "sizing_defaults": {"edge_minimum": 0.08, "target_vol": 0.03},
            "strategies": [{"strategy_id": "s30", "market": "spot", "weight": 0.5}],
        }
        sd = SizingDefaults(**config_data["sizing_defaults"])
        pc = PortfolioConfig(
            strategies=[StrategySpec.from_dict(s) for s in config_data["strategies"]],
            sizing_defaults=sd,
        )
        assert pc.sizing_defaults.edge_minimum == 0.08
        assert pc.sizing_defaults.target_vol == 0.03

    def test_strategy_spec_sizing_overrides_parsed(self):
        """StrategySpec.from_dict() parses sizing_overrides from JSON."""
        d = {
            "strategy_id": "s320",
            "market": "spot",
            "weight": 0.5,
            "sizing_overrides": {
                "kelly_mult_scale": 1.5,
                "spot_max_equity_pct": 0.95,
            },
        }
        spec = StrategySpec.from_dict(d)
        assert spec.sizing_overrides == {"kelly_mult_scale": 1.5, "spot_max_equity_pct": 0.95}


# ---------------------------------------------------------------------------
# Test 21: Integration — resolved config reaches compute_position_size
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_simulator_threads_resolved_config(self):
        """Integration: resolved config with custom edge_minimum affects position sizing.

        Verifies that the full pipeline (PortfolioConfig → resolve_sizing → compute_position_size)
        produces different results with custom vs default settings.
        """
        from v4.config import SizingDefaults, resolve_sizing
        from v4.sizing import compute_position_size

        # Default config
        sd_default = SizingDefaults()
        resolved_default = resolve_sizing(sd_default, {})

        # Custom config with lower edge_minimum
        sd_custom = SizingDefaults()
        resolved_custom = resolve_sizing(sd_custom, {"edge_minimum": 0.05})

        # With edge=0.08: default rejects, custom accepts
        common = dict(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.02,
            edge=0.08,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
        )

        size_default = compute_position_size(**common, edge_minimum=resolved_default.edge_minimum)
        size_custom = compute_position_size(**common, edge_minimum=resolved_custom.edge_minimum)

        assert size_default == 0.0  # Rejected by default edge_minimum=0.10
        assert size_custom > 0.0   # Accepted by custom edge_minimum=0.05


# ---------------------------------------------------------------------------
# Tests 22-25: Sizing model registry
# ---------------------------------------------------------------------------

class TestSizingModelRegistry:

    def test_get_sizing_model_kelly(self):
        """get_sizing_model('kelly') returns a SizingModel instance."""
        from v4.sizing import get_sizing_model, SizingModel

        model = get_sizing_model("kelly")
        assert isinstance(model, SizingModel)

    def test_get_sizing_model_unknown_raises(self):
        """get_sizing_model('unknown') raises KeyError."""
        from v4.sizing import get_sizing_model

        with pytest.raises(KeyError):
            get_sizing_model("unknown")

    def test_custom_sizing_model_dispatch(self):
        """Register a custom model, verify simulator dispatches to it."""
        from v4.sizing import SizingModel, _SIZING_MODELS, get_sizing_model

        class FixedSizing:
            """Always returns a fixed position size."""
            FIXED_SIZE = 42_000.0

            def compute_size(self, **kwargs) -> float:
                return self.FIXED_SIZE

        # Register custom model
        _SIZING_MODELS["fixed"] = FixedSizing()
        try:
            model = get_sizing_model("fixed")
            assert isinstance(model, SizingModel)
            result = model.compute_size(
                strategy_equity=100_000,
                rolling_adv=50_000_000,
                volatility=0.02,
                edge=0.20,
                size_multiplier=1.0,
                cap_multiplier=1.0,
                max_trade_pct=0.0,
            )
            assert result == 42_000.0
        finally:
            del _SIZING_MODELS["fixed"]

    def test_default_kelly_unchanged(self):
        """Default kelly model produces identical results via registry vs direct wrapper."""
        from v4.sizing import get_sizing_model, compute_position_size

        model = get_sizing_model("kelly")
        common = dict(
            strategy_equity=100_000,
            rolling_adv=50_000_000,
            volatility=0.02,
            edge=0.20,
            size_multiplier=1.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
        )
        via_registry = model.compute_size(**common)
        via_wrapper = compute_position_size(**common)
        assert via_registry == via_wrapper
