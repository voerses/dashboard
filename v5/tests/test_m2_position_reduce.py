"""Acceptance tests for M2 — Position.reduce primitive.

Covers:
  - AC4: Position.reduce returns ReduceResult bundle; quantity/margin/funding pro-rating
  - AC5: entry_price unchanged on reduce
  - AC7: fee booking — pro-rata entry fee; exit fee on closed notional
  - AC9: funding pro-rating by close_fraction
  - AC14: dust threshold promotes to terminal; quantity = 0.0 by assignment
  - AC14a: dust-boundary equivalence with EPS_NOTIONAL_USD
  - AC17: over-close clamp to full close; no direction flip
  - AC22: sign-of-product invariant after reduce

All tests MUST FAIL until Position.reduce / ReduceResult exist.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.position import Position, ReduceResult, ScalingEvent  # noqa: E402


EPS_NOTIONAL_USD = 0.10  # Per AC14a test design constant


def _make_long_position(
    entry_price: float = 100.0,
    quantity: float = 10.0,
    margin_usd: float = 1_000.0,
    direction: int = 1,
    cumulative_funding: float = -5.0,
    entry_bar: int = 5,
) -> Position:
    signed_qty = direction * abs(quantity)
    return Position(
        position_id="BTC:s30:5:primary",
        token="BTC",
        strategy_id="s30",
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=signed_qty,
        margin_usd=margin_usd,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        stop_price=90.0,
        highest=entry_price,
        lowest=entry_price,
        initial_risk=10.0,
        cumulative_funding=cumulative_funding,
    )


# ===================================================================
# AC4 — Position.reduce returns ReduceResult bundle
# ===================================================================

class TestAC4ReduceResultBundle:
    """reduce() returns a ReduceResult dataclass with the fields defined in AC4."""

    def test_returns_reduce_result_instance(self):
        pos = _make_long_position()
        result = pos.reduce(
            qty_to_close=5.0, fill_price=120.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert isinstance(result, ReduceResult)

    def test_reduce_result_fields_exist(self):
        pos = _make_long_position()
        result = pos.reduce(
            qty_to_close=5.0, fill_price=120.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        for name in (
            "closed_qty_signed", "closed_qty_abs", "closed_margin",
            "closed_funding", "partial_entry_fee_to_book",
            "partial_entry_fee_remaining", "exit_fee", "slip_bps",
            "gross_pnl", "is_terminal", "suffix",
        ):
            assert hasattr(result, name), f"ReduceResult missing {name}"

    def test_closed_qty_signed_and_abs(self):
        pos = _make_long_position(quantity=10.0, direction=1)
        result = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        # Reducing a long => closed_qty_signed is +3 (pos-direction units)
        assert result.closed_qty_signed == pytest.approx(3.0)
        assert result.closed_qty_abs == pytest.approx(3.0)

    def test_closed_qty_signed_short(self):
        pos = _make_long_position(quantity=10.0, direction=-1)
        # For a short, direction is -1 and signed qty is -10.
        result = pos.reduce(
            qty_to_close=3.0, fill_price=90.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        # closed_qty_signed in pos-direction units: closing a short closes
        # -3 units of signed quantity. Per AC4 "signed in pos-direction units",
        # the magnitude is 3.0 either way. We check abs() is correct; sign
        # follows direction convention implemented by position.
        assert abs(result.closed_qty_signed) == pytest.approx(3.0)
        assert result.closed_qty_abs == pytest.approx(3.0)

    def test_quantity_shrinks_signed(self):
        pos = _make_long_position(quantity=10.0, direction=1)
        pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        # 10 * (1 - 3/10) = 7
        assert pos.quantity == pytest.approx(7.0)

    def test_margin_pro_rata(self):
        pos = _make_long_position(margin_usd=1_000.0, quantity=10.0)
        pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert pos.margin_usd == pytest.approx(700.0)

    def test_gross_pnl_sign(self):
        """gross_pnl = closed_qty_signed * (fill_price - entry_price)."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0)
        result = pos.reduce(
            qty_to_close=5.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        # long, closed 5 units, +10 per unit = +50
        assert result.gross_pnl == pytest.approx(50.0)

    def test_reduce_appends_scaling_event(self):
        pos = _make_long_position()
        pos.reduce(
            qty_to_close=5.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert len(pos.scaling_events) == 1
        assert pos.scaling_events[0].kind == "reduce"

    def test_suffix_is_scale_N_for_non_terminal(self):
        pos = _make_long_position()
        result = pos.reduce(
            qty_to_close=2.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert ":scale_" in result.suffix
        assert not result.suffix.endswith("_final")
        assert result.is_terminal is False

    def test_reduce_fraction_wrapper(self):
        """reduce_fraction is a thin convenience wrapper."""
        pos = _make_long_position(quantity=10.0)
        result = pos.reduce_fraction(
            fraction=0.3, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert isinstance(result, ReduceResult)
        assert pos.quantity == pytest.approx(7.0)


# ===================================================================
# AC5 — entry_price unchanged on reduce
# ===================================================================

class TestAC5EntryPriceUnchanged:
    """entry_price (cost basis) is unchanged on reduce (bit-exact)."""

    def test_entry_price_bit_exact_long(self):
        pos = _make_long_position(entry_price=100.0, quantity=5.0)
        pos.reduce(
            qty_to_close=1.5, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert pos.entry_price == 100.0  # exact equality, no drift


# ===================================================================
# AC7 — fee booking
# ===================================================================

class TestAC7FeeBooking:
    """Entry fee pro-rated by close_fraction; exit fee on closed notional."""

    def test_entry_fee_pro_rata_on_close_fraction(self):
        """$5 full entry fee, reduce 40% => partial_entry_fee_to_book = $2."""
        pos = _make_long_position(quantity=10.0)
        result = pos.reduce(
            qty_to_close=4.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert result.partial_entry_fee_to_book == pytest.approx(2.0)
        assert result.partial_entry_fee_remaining == pytest.approx(3.0)

    def test_entry_fee_sum_invariant_over_three_reduces(self):
        """sum of booked entry fees + final = $5."""
        pos = _make_long_position(quantity=10.0)
        full_entry_fee = 5.0
        r1 = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0,
            full_entry_fee=full_entry_fee, dust_usd=0.01,
        )
        r2 = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=11,
            fee_rate=0.0005, atr=5.0,
            full_entry_fee=r1.partial_entry_fee_remaining, dust_usd=0.01,
        )
        r3 = pos.reduce(
            qty_to_close=4.0, fill_price=110.0, bar_idx=12,
            fee_rate=0.0005, atr=5.0,
            full_entry_fee=r2.partial_entry_fee_remaining, dust_usd=0.01,
        )
        total_booked = (r1.partial_entry_fee_to_book
                        + r2.partial_entry_fee_to_book
                        + r3.partial_entry_fee_to_book)
        assert total_booked == pytest.approx(5.0)

    def test_exit_fee_on_closed_notional(self):
        """exit_fee = fee_rate * |closed_qty| * fill_price."""
        pos = _make_long_position(quantity=10.0)
        result = pos.reduce(
            qty_to_close=4.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.001, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        # 4 * 110 * 0.001 = 0.44
        assert result.exit_fee == pytest.approx(0.44)


# ===================================================================
# AC9 — funding pro-rating on reduce
# ===================================================================

class TestAC9FundingProRating:
    """cumulative_funding splits by close_fraction."""

    def test_funding_closed_pro_rata(self):
        pos = _make_long_position(quantity=10.0, cumulative_funding=-10.0)
        r = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        # 30% of -10 = -3 booked; pos keeps -7
        assert r.closed_funding == pytest.approx(-3.0)
        assert pos.cumulative_funding == pytest.approx(-7.0)

    def test_funding_invariant_across_multiple_reduces(self):
        """sum(closed_funding) + pos.cumulative_funding == starting funding."""
        pos = _make_long_position(quantity=10.0, cumulative_funding=-10.0)
        r1 = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=0.01,
        )
        r2 = pos.reduce(
            qty_to_close=4.0, fill_price=110.0, bar_idx=11,
            fee_rate=0.0005, atr=5.0, full_entry_fee=r1.partial_entry_fee_remaining, dust_usd=0.01,
        )
        total = r1.closed_funding + r2.closed_funding + pos.cumulative_funding
        assert total == pytest.approx(-10.0)


# ===================================================================
# AC14 — dust threshold: is_terminal=True + quantity assigned 0
# ===================================================================

class TestAC14DustThreshold:
    """When remaining notional < dust_usd, reduce promotes to terminal."""

    def test_dust_promotes_to_terminal(self):
        """Position notional 10 * 100 = 1000; reduce 999/1000 => 0.001 left.
        If dust_usd = 10 USD (notional), remaining < dust -> terminal."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0)
        result = pos.reduce(
            qty_to_close=9.99, fill_price=100.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=10.0,
        )
        assert result.is_terminal is True
        assert result.suffix.endswith("_final")

    def test_dust_terminal_quantity_assigned_zero(self):
        """On terminal via dust, pos.quantity is set to 0.0 by assignment."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0)
        pos.reduce(
            qty_to_close=9.99, fill_price=100.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=10.0,
        )
        assert pos.quantity == 0.0

    def test_non_dust_is_not_terminal(self):
        pos = _make_long_position(entry_price=100.0, quantity=10.0)
        result = pos.reduce(
            qty_to_close=4.0, fill_price=100.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=10.0,
        )
        assert result.is_terminal is False


# ===================================================================
# AC14a — dust-boundary equivalence
# ===================================================================

class TestAC14aDustBoundaryEquivalence:
    """Reduces that land just inside vs just outside the dust threshold
    must differ only by the `2 * EPS_NOTIONAL_USD`-sized slice."""

    def test_just_above_and_just_below_dust_differ_only_at_boundary(self):
        """
        Construct a position with total notional ~100*dust_usd (well above).
        - Reduce A: remaining notional = dust_usd + EPS_NOTIONAL_USD
          -> NON-terminal (is_terminal=False)
        - Reduce B: remaining notional = dust_usd - EPS_NOTIONAL_USD
          -> TERMINAL (is_terminal=True)
        The PnL/fee delta between A and B must be attributable to the
        boundary-crossing slice (~2 * EPS_NOTIONAL_USD of notional).
        """
        dust_usd = 5.0
        entry_price = 100.0

        def _make():
            return _make_long_position(entry_price=entry_price, quantity=5.0,
                                       margin_usd=500.0, cumulative_funding=0.0)

        # Total notional = 500. Target: remaining_notional = dust +/- eps.
        # remaining_notional = (total_qty - closed_qty) * fill_price
        # set fill_price = entry_price for determinism
        total_qty = 5.0
        # above: remaining_notional = dust + eps = 5.10 -> closed = (500 - 5.10)/100 = 4.949
        qty_close_above = (500.0 - (dust_usd + EPS_NOTIONAL_USD)) / entry_price
        # below: remaining_notional = dust - eps = 4.90 -> closed = (500 - 4.90)/100 = 4.951
        qty_close_below = (500.0 - (dust_usd - EPS_NOTIONAL_USD)) / entry_price

        pos_a = _make()
        r_a = pos_a.reduce(
            qty_to_close=qty_close_above, fill_price=entry_price, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=dust_usd,
        )
        pos_b = _make()
        r_b = pos_b.reduce(
            qty_to_close=qty_close_below, fill_price=entry_price, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=dust_usd,
        )

        assert r_a.is_terminal is False
        assert r_b.is_terminal is True
        # suffix differs per AC14a (non-terminal vs terminal)
        assert not r_a.suffix.endswith("_final")
        assert r_b.suffix.endswith("_final")

    def test_non_terminal_side_is_partial_reduce(self):
        """The non-terminal side must remain an ordinary partial reduce —
        exec_type would be 'reduce' and is_terminal=False when booked by
        the simulator wrapper. Here we just verify the domain result."""
        dust_usd = 5.0
        pos = _make_long_position(entry_price=100.0, quantity=5.0, margin_usd=500.0)
        qty_close = (500.0 - (dust_usd + EPS_NOTIONAL_USD)) / 100.0
        r = pos.reduce(
            qty_to_close=qty_close, fill_price=100.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=dust_usd,
        )
        assert r.is_terminal is False

    def test_pnl_equivalence_at_dust_boundary(self):
        """AC14a strengthening (peer-review round 1):
        Beyond `is_terminal` and the `_final` suffix, the realized PnL at
        the dust boundary must match to within a computable
        fees-+-slippage tolerance.

        Concretely: the "above-boundary" reduce leaves a thin sliver unclosed
        whose notional is ~2 * EPS_NOTIONAL_USD. The "below-boundary" reduce
        closes that sliver via dust-promotion. The difference in total realized
        cashflow between the two paths (A_gross_pnl - A_exit_fee) vs
        (B_gross_pnl - B_exit_fee) must equal the sliver's own gross_pnl -
        sliver's exit_fee, i.e. the slice cost.

        Use fill_price = entry_price so gross_pnl on the sliver is 0; any
        delta is attributable to exit fees and slippage on the sliver.
        """
        dust_usd = 5.0
        entry_price = 100.0
        fee_rate = 0.0005

        def _make():
            return _make_long_position(entry_price=entry_price, quantity=5.0,
                                       margin_usd=500.0, cumulative_funding=0.0)

        qty_close_above = (500.0 - (dust_usd + EPS_NOTIONAL_USD)) / entry_price
        qty_close_below = (500.0 - (dust_usd - EPS_NOTIONAL_USD)) / entry_price

        pos_a = _make()
        r_a = pos_a.reduce(
            qty_to_close=qty_close_above, fill_price=entry_price, bar_idx=10,
            fee_rate=fee_rate, atr=5.0, full_entry_fee=5.0, dust_usd=dust_usd,
        )
        pos_b = _make()
        r_b = pos_b.reduce(
            qty_to_close=qty_close_below, fill_price=entry_price, bar_idx=10,
            fee_rate=fee_rate, atr=5.0, full_entry_fee=5.0, dust_usd=dust_usd,
        )

        # PnL equivalence: cashflow delta at boundary matches the sliver.
        # boundary slice notional ~ 2 * EPS_NOTIONAL_USD of the original
        # position; slice gross_pnl is 0 at fill=entry, slice exit_fee is at
        # most fee_rate * 2 * EPS_NOTIONAL_USD. Slippage bps on a dust-sized
        # slice is bounded as well.
        cashflow_a = r_a.gross_pnl - r_a.exit_fee
        cashflow_b = r_b.gross_pnl - r_b.exit_fee
        # The sliver contributes at most (2*EPS * fee_rate) in fees plus a
        # tight slippage term (<= 10 bps of notional on a dust-sized slice).
        # 2 * EPS * fee_rate = 2 * 0.10 * 0.0005 = 1e-4 USD; slippage ~1e-3 USD.
        tolerance = (2.0 * EPS_NOTIONAL_USD) * (fee_rate + 0.01)
        assert abs(cashflow_a - cashflow_b) < tolerance + 1e-6


# ===================================================================
# AC17 — over-close clamp to full close, no direction flip
# ===================================================================

class TestAC17OverCloseClamp:
    """qty_to_close >= |pos.quantity| clamps to full close."""

    def test_over_close_clamps_to_full_close(self):
        pos = _make_long_position(quantity=5.0)
        r = pos.reduce(
            qty_to_close=999.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert r.is_terminal is True
        # no direction flip: quantity should be 0 (not -994)
        assert pos.quantity == 0.0

    def test_exact_full_close_is_terminal(self):
        pos = _make_long_position(quantity=5.0)
        r = pos.reduce(
            qty_to_close=5.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=0.01,
        )
        assert r.is_terminal is True
        assert pos.quantity == 0.0


# ===================================================================
# AC22 — sign-of-product invariant
# ===================================================================

class TestAC22QuantitySignInvariant:
    """After any reduce: is_closed or (quantity * direction > 0.0)."""

    def _is_closed(self, pos: Position) -> bool:
        return pos.quantity == 0.0

    def test_invariant_after_single_reduce(self):
        pos = _make_long_position(quantity=10.0, direction=1)
        pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert self._is_closed(pos) or (pos.quantity * pos.direction > 0.0)

    def test_invariant_after_over_close(self):
        pos = _make_long_position(quantity=5.0, direction=1)
        pos.reduce(
            qty_to_close=999.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=5.0, dust_usd=1.0,
        )
        assert self._is_closed(pos) or (pos.quantity * pos.direction > 0.0)

    def test_invariant_property_random_fractions(self):
        """With seed=42, apply random reductions and check invariant holds."""
        rng = random.Random(42)
        pos = _make_long_position(quantity=10.0)
        full_entry_fee = 5.0
        for i in range(5):
            if pos.quantity == 0.0:
                break
            frac = rng.uniform(0.05, 0.45)
            r = pos.reduce_fraction(
                fraction=frac, fill_price=110.0, bar_idx=10 + i,
                fee_rate=0.0005, atr=5.0,
                full_entry_fee=full_entry_fee, dust_usd=0.01,
            )
            full_entry_fee = r.partial_entry_fee_remaining
            assert (pos.quantity == 0.0) or (pos.quantity * pos.direction > 0.0)

    def test_negative_zero_quantity_is_closed(self):
        """math.copysign(0.0, -1.0) -> sign-of-product (0.0 * -1 = 0)
        is NOT > 0.0, so the invariant requires is_closed path."""
        pos = _make_long_position(quantity=5.0, direction=1)
        # Manually force negative-zero
        pos.quantity = math.copysign(0.0, -1.0)
        # Either is_closed or sign-product > 0 -- is_closed path is satisfied.
        assert (pos.quantity == 0.0) or (pos.quantity * pos.direction > 0.0)
