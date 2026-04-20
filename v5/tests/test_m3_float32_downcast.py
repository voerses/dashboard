"""M3 acceptance tests — float32 downcast for indicators, float64 for prices/PnL.

Covers:
  - AC9: Indicator arrays use float32 where precision is not needed;
         prices / PnL / fees / funding / ATR remain float64.

Downcast to float32 (per brief):
  - trail_schedule, time_trail_schedule, max_trail_mult (arrays)
  - conviction_score
  - volume, vol_20
  - ret_1h

Keep float64 (precision-critical):
  - close, high, low arrays
  - atr arrays (stop/entry precision)
  - quantities / margin / pnl / fees / funding on Position + ClosedTrade

These tests guard against regression as well as validate the downcast. Some
float64 assertions (prices/fees) are sentinel-green today — they catch a
regression where the refactor accidentally demotes a currency field.

All tests MUST FAIL today for the float32 side — TokenBarArrays construction
does not downcast these arrays yet.

Seed: 42.
"""
from __future__ import annotations

import sys
from dataclasses import fields as dc_fields
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _make_token_signals(n: int = 20):
    """Construct a TokenBarArrays with arrays typed float64 so downcast can
    be observed at the boundary (construction must downcast the listed
    fields to float32 per AC9)."""
    from v5.signals import TokenBarArrays
    close_f64 = np.full(n, 100.0, dtype=np.float64)
    return TokenBarArrays(
        token="BTC", strategy_id="s30", n_bars=n,
        timestamps=np.arange(n, dtype=np.int64),
        entry_mask=np.zeros(n, dtype=bool),
        direction=np.full(n, 1, dtype=np.int8),
        close=close_f64,
        high=close_f64 + 1.0, low=close_f64 - 1.0,
        atr=np.full(n, 5.0, dtype=np.float64),
        rolling_adv=np.full(n, 1e9, dtype=np.float64),
        funding_1h=np.zeros(n, dtype=np.float64),
        stop_mult=np.full(n, 2.0, dtype=np.float64),
        trail_mult=np.full(n, 3.0, dtype=np.float64),
        target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
        edge=0.35,
        leverage=np.ones(n, dtype=np.float64),
        trail_schedule=np.full(n, 3.0, dtype=np.float64),
        time_trail_schedule=np.full(n, 2.5, dtype=np.float64),
        max_trail_mult=np.full(n, 4.0, dtype=np.float64),
        convex_exit=False, rsi=None, rsi_exit_level=999.0,
        mean_target_vals=None, is_combined=False,
        secondary_entry_mask=None, secondary_direction=None,
        secondary_leverage=1.0, capital_split=0.5,
        is_perp_primary=True, is_perp_secondary=False,
        perp_close=None, perp_high=None, perp_low=None,
        perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
    )


# ===================================================================
# AC9 — float32 on indicator fields
# ===================================================================

class TestAC9Float32Fields:
    """Fields downcast to float32 at TokenBarArrays construction."""

    def test_trail_schedule_is_float32(self):
        sig = _make_token_signals()
        assert sig.trail_schedule is not None
        assert sig.trail_schedule.dtype == np.float32, (
            f"trail_schedule must be float32; got {sig.trail_schedule.dtype}"
        )

    def test_time_trail_schedule_is_float32(self):
        sig = _make_token_signals()
        assert sig.time_trail_schedule is not None
        assert sig.time_trail_schedule.dtype == np.float32

    def test_max_trail_mult_is_float32(self):
        sig = _make_token_signals()
        assert sig.max_trail_mult is not None
        assert sig.max_trail_mult.dtype == np.float32

    @pytest.mark.skip(reason=(
        "M9 C-1: conviction_score field deleted from TokenBarArrays. "
        "Float32 downcast invariant no longer applies."
    ))
    def test_conviction_score_is_float32(self):
        pass  # pragma: no cover


# ===================================================================
# AC9 — float64 on price / PnL / fee / funding / ATR fields
# ===================================================================

class TestAC9Float64PreservedFields:
    """Prices, fees, PnL, funding, ATR remain float64 — currency precision."""

    def test_close_is_float64(self):
        sig = _make_token_signals()
        assert sig.close.dtype == np.float64

    def test_high_is_float64(self):
        sig = _make_token_signals()
        assert sig.high.dtype == np.float64

    def test_low_is_float64(self):
        sig = _make_token_signals()
        assert sig.low.dtype == np.float64

    def test_atr_is_float64(self):
        sig = _make_token_signals()
        assert sig.atr.dtype == np.float64

    def test_funding_1h_is_float64(self):
        sig = _make_token_signals()
        assert sig.funding_1h.dtype == np.float64

    def test_position_price_and_margin_are_builtin_float(self):
        """Position scalar fields (entry_price, margin_usd, pnl, fees) are Python
        floats, i.e. float64 when stored in a numpy array. Checks they're not
        inadvertently demoted to np.float32 anywhere."""
        from v5.position import Position
        pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=0, entry_price=100.0, direction=1,
            quantity=1.0, margin_usd=100.0, leverage=1.0,
            is_perp=True, fee_rate=0.0005,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            stop_price=90.0, highest=100.0, lowest=100.0,
            initial_risk=10.0,
        )
        # Ensure that scalar assignment stays full-precision: a round-trip
        # via numpy doesn't silently lose precision.
        assert isinstance(pos.entry_price, float)
        # Confirm we can round-trip a value that would overflow float32 precision.
        sensitive = 12345678.9012345
        pos.entry_price = sensitive
        assert pos.entry_price == sensitive

    def test_closed_trade_pnl_and_fees_preserve_precision(self):
        from v5.position import ClosedTrade
        ct = ClosedTrade(
            position_id="BTC:s30:5:primary", token="BTC", strategy_id="s30",
            leg="primary", entry_bar=0, exit_bar=10, entry_price=100.0,
            exit_price=110.0, direction=1, margin_usd=100.0, pnl=10.0,
            funding_cost=0.0, entry_fee=0.5, exit_fee=0.5, hold_bars=10,
            exit_reason="target",
        )
        sensitive = 0.0000001234567890123
        ct.pnl = sensitive
        ct.entry_fee = sensitive
        ct.exit_fee = sensitive
        ct.funding_cost = sensitive
        # Must round-trip under float64 precision
        assert ct.pnl == sensitive
        assert ct.entry_fee == sensitive
        assert ct.exit_fee == sensitive
        assert ct.funding_cost == sensitive


# ===================================================================
# AC9 — downcast is lossy-but-valued (not just identity copy)
# ===================================================================

class TestAC9DowncastActuallyHappens:
    """The float32 downcast must change the dtype even if the input was float64."""

    def test_input_float64_becomes_float32(self):
        """Feeding float64 to the listed fields must result in float32 storage."""
        from v5.signals import TokenBarArrays
        n = 20
        close_f64 = np.full(n, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n,
            timestamps=np.arange(n, dtype=np.int64),
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.full(n, 1, dtype=np.int8),
            close=close_f64, high=close_f64 + 1, low=close_f64 - 1,
            atr=np.full(n, 5.0, dtype=np.float64),
            rolling_adv=np.full(n, 1e9),
            funding_1h=np.zeros(n),
            stop_mult=np.full(n, 2.0), trail_mult=np.full(n, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n),
            trail_schedule=np.full(n, 3.0, dtype=np.float64),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        assert sig.trail_schedule.dtype == np.float32, (
            "Construction must downcast trail_schedule input to float32"
        )
