"""Acceptance tests for M2 — QC1 funding-sum invariant.

Covers:
  - T-P1:  Pure-reduce sequence: sum(booked funding) + pos.funding == total paid
  - T-P1a: Interleaved increase + reduce (starts with increase after funding)
  - T-P1b: Interleaved reduce + increase (reduce first, then increase)
  - T-P1c: Dust-promoted reduce path must NOT lose funding in _close_position

The invariant (AC9 revised):
    sum(booked funding in ClosedTrades for parent) + pos.cumulative_funding
       == total_funding_paid_to_date

Interleavings must preserve this regardless of ordering.

All tests MUST FAIL until Position.reduce / increase with funding pro-rating land.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.position import Position  # noqa: E402


def _make_pos(
    quantity: float = 10.0, entry_price: float = 100.0,
    margin_usd: float = 1_000.0, direction: int = 1,
    cumulative_funding: float = 0.0,
) -> Position:
    signed_qty = direction * abs(quantity)
    return Position(
        position_id="BTC:s30:5:primary", token="BTC", strategy_id="s30",
        leg="primary", entry_bar=5, entry_price=entry_price,
        direction=direction, quantity=signed_qty, margin_usd=margin_usd,
        leverage=1.0, is_perp=True, fee_rate=0.0005,
        stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
        no_stop_bars=6, min_hold=1, max_hold=720,
        stop_price=90.0, highest=entry_price, lowest=entry_price,
        initial_risk=10.0, cumulative_funding=cumulative_funding,
    )


class TestTP1FundingPureReduce:
    """T-P1 — pure-reduce sequence preserves the funding invariant.

    Strengthened (peer-review round 1): invariant is asserted AFTER EVERY
    mutation (accrual AND reduce), not just at final close. The invariant is:

        sum(ClosedTrade.funding_cost booked to date) + pos.cumulative_funding
          == total_funding_accrued_to_date_for_this_position

    at any instant — matches T-P1a/T-P1b pattern.
    """

    def test_pure_reduce_funding_invariant(self):
        """3 funding cycles, 3 reduces + final close — invariant holds at every
        checkpoint (after each accrual AND after each reduce)."""
        pos = _make_pos(quantity=10.0, cumulative_funding=0.0)
        total_paid = 0.0
        booked = 0.0
        full_entry_fee = 5.0

        # Cycle 1: accrue -$3
        pos.cumulative_funding += -3.0
        total_paid += -3.0
        # invariant immediately after accrual
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)
        # reduce 30%
        r1 = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=full_entry_fee,
            dust_usd=0.01,
        )
        booked += r1.closed_funding
        full_entry_fee = r1.partial_entry_fee_remaining
        # invariant after reduce 1
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)

        # Cycle 2: accrue -$2 (on remaining 7 units)
        pos.cumulative_funding += -2.0
        total_paid += -2.0
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)
        # reduce 40% of current (7 * 0.4 = 2.8)
        r2 = pos.reduce(
            qty_to_close=2.8, fill_price=112.0, bar_idx=20,
            fee_rate=0.0005, atr=5.0, full_entry_fee=full_entry_fee,
            dust_usd=0.01,
        )
        booked += r2.closed_funding
        full_entry_fee = r2.partial_entry_fee_remaining
        # invariant after reduce 2
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)

        # Cycle 3: accrue -$1
        pos.cumulative_funding += -1.0
        total_paid += -1.0
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)
        # Final close
        r3 = pos.reduce(
            qty_to_close=pos.quantity * pos.direction, fill_price=115.0,
            bar_idx=30, fee_rate=0.0005, atr=5.0,
            full_entry_fee=full_entry_fee, dust_usd=0.01,
        )
        booked += r3.closed_funding
        assert r3.is_terminal is True

        # final: cumulative_funding must be 0.0 after terminal close and
        # booked equals total_paid
        assert pos.cumulative_funding == pytest.approx(0.0, abs=1e-9)
        assert booked == pytest.approx(total_paid, abs=1e-9)
        # Full-invariant form (adds zero on the cumulative side)
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)


class TestTP1aFundingInterleavedIncrease:
    """T-P1a — interleaved increase after funding, then more reduces."""

    def test_interleaved_invariant(self):
        pos = _make_pos(quantity=10.0, cumulative_funding=0.0)
        total_paid = 0.0
        booked = 0.0
        full_entry_fee = 5.0

        # accrue 1 cycle funding of -$2 on 10 units
        pos.cumulative_funding += -2.0
        total_paid += -2.0

        # reduce 30%
        r1 = pos.reduce(
            qty_to_close=3.0, fill_price=110.0, bar_idx=10,
            fee_rate=0.0005, atr=5.0, full_entry_fee=full_entry_fee,
            dust_usd=0.01,
        )
        booked += r1.closed_funding
        full_entry_fee = r1.partial_entry_fee_remaining
        # invariant mid-way
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)

        # increase 50% (add 3.5 units on current 7 units)
        pos.increase(
            qty_to_add=3.5, fill_price=108.0, margin_delta=378.0,
            bar_idx=12, fee_rate=0.0005, atr=5.0,
        )
        # Increase does NOT change funding already booked or accrued
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)

        # Accrue 2 more cycles on the new larger size
        pos.cumulative_funding += -3.0
        total_paid += -3.0
        pos.cumulative_funding += -1.5
        total_paid += -1.5

        # reduce 50%
        q_now = pos.quantity * pos.direction  # 7 + 3.5 = 10.5
        r2 = pos.reduce(
            qty_to_close=q_now * 0.5, fill_price=112.0, bar_idx=30,
            fee_rate=0.0005, atr=5.0, full_entry_fee=full_entry_fee,
            dust_usd=0.01,
        )
        booked += r2.closed_funding
        full_entry_fee = r2.partial_entry_fee_remaining

        # Final close
        r3 = pos.reduce(
            qty_to_close=pos.quantity * pos.direction, fill_price=115.0,
            bar_idx=40, fee_rate=0.0005, atr=5.0,
            full_entry_fee=full_entry_fee, dust_usd=0.01,
        )
        booked += r3.closed_funding
        assert r3.is_terminal is True

        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)


class TestTP1bFundingReduceThenIncrease:
    """T-P1b — reduce-before-first-funding-cycle then increase path."""

    def test_reduce_first_invariant(self):
        pos = _make_pos(quantity=10.0, cumulative_funding=0.0)
        total_paid = 0.0
        booked = 0.0
        full_entry_fee = 5.0

        # Reduce 20% with zero funding accrued
        r1 = pos.reduce(
            qty_to_close=2.0, fill_price=108.0, bar_idx=5,
            fee_rate=0.0005, atr=5.0, full_entry_fee=full_entry_fee,
            dust_usd=0.01,
        )
        booked += r1.closed_funding  # should be 0
        full_entry_fee = r1.partial_entry_fee_remaining

        # Accrue 2 funding cycles on the 8 remaining units
        pos.cumulative_funding += -2.5
        total_paid += -2.5
        pos.cumulative_funding += -1.5
        total_paid += -1.5

        # Increase by 4 units
        pos.increase(
            qty_to_add=4.0, fill_price=106.0, margin_delta=424.0,
            bar_idx=20, fee_rate=0.0005, atr=5.0,
        )
        # Invariant holds (increase doesn't affect accruals)
        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)

        # Accrue 1 more cycle on the new larger size
        pos.cumulative_funding += -2.0
        total_paid += -2.0

        # Full close
        r2 = pos.reduce(
            qty_to_close=pos.quantity * pos.direction, fill_price=110.0,
            bar_idx=30, fee_rate=0.0005, atr=5.0,
            full_entry_fee=full_entry_fee, dust_usd=0.01,
        )
        booked += r2.closed_funding
        assert r2.is_terminal is True

        assert booked + pos.cumulative_funding == pytest.approx(total_paid, abs=1e-9)


class TestTP1cFundingDustPromoted:
    """T-P1c — dust-promoted reduce path must NOT lose any funding."""

    def test_dust_promoted_funding_invariant(self):
        pos = _make_pos(quantity=10.0, cumulative_funding=0.0)
        total_paid = 0.0
        booked = 0.0
        full_entry_fee = 5.0

        # Accrue 2 funding cycles on 10 units
        pos.cumulative_funding += -4.0
        total_paid += -4.0
        pos.cumulative_funding += -2.0
        total_paid += -2.0

        # Reduce such that remaining notional < dust_usd
        # notional = 10 * 100 = 1000; target remaining = 5
        dust_usd = 10.0
        # close fraction ~0.9999
        qty_close = 10.0 * 0.9999
        r = pos.reduce(
            qty_to_close=qty_close, fill_price=100.0, bar_idx=30,
            fee_rate=0.0005, atr=5.0,
            full_entry_fee=full_entry_fee, dust_usd=dust_usd,
        )
        assert r.is_terminal is True
        booked += r.closed_funding

        # After a terminal reduce, all funding should be booked; pos should be closed
        # with cumulative_funding = 0.0 (the booking took 100%).
        assert pos.quantity == 0.0
        assert pos.cumulative_funding == pytest.approx(0.0, abs=1e-9)
        assert booked == pytest.approx(total_paid, abs=1e-9)
