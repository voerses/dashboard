"""M4 — M2 invariants preserved (AC8, AC10, AC11).

AC8 T-B4b: breakeven reads OLD entry_price, not post-scale VWAP.
AC10 T-B4: r_anchor_price frozen; Stage 3 reads r_anchor, not entry.
AC11: liquidation consumes post-scale state after increase/reduce.

All tests MUST FAIL RED — M4 handler/helper imports do not exist yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Position is already present from M2 — used to set up invariant fixtures.
from v5.position import Position  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _long_100(breakeven_atr: float = 1.0) -> Position:
    """Long 10 @ $100, margin $1000, stop $95, initial_risk $5."""
    return Position(
        position_id="BTC:s1:0:primary", token="BTC", strategy_id="s1",
        leg="primary", entry_bar=0, entry_price=100.0, direction=1,
        quantity=10.0, margin_usd=1_000.0, leverage=1.0, is_perp=True,
        fee_rate=0.0005, stop_mult=1.0, trail_mult=3.0, target_mult=5.0,
        no_stop_bars=0, min_hold=0, max_hold=720,
        stop_price=95.0, highest=100.0, lowest=100.0, initial_risk=5.0,
        breakeven_atr=breakeven_atr, r_anchor_price=100.0,
    )


def _scale_down(pos: Position) -> None:
    """Shared helper: increase 10 @ $95 -> VWAP $97.5."""
    pos.increase(qty_to_add=10.0, fill_price=95.0, margin_delta=950.0,
                 bar_idx=5, fee_rate=0.0005, atr=5.0)


# ---------------------------------------------------------------------------
# AC8 — breakeven-after-scale
# ---------------------------------------------------------------------------


class TestAC8BreakevenAfterScale:
    """AC8 T-B4b: Stage 1 breakeven reads OLD entry_price, not new VWAP."""

    def test_breakeven_handler_uses_old_entry_price_after_increase(self):
        """AC8 T-B4b: breakeven anchor remains at $100 after VWAP moves to $97.5."""
        from v5.exit_handlers import breakeven_handler_factory
        pos = _long_100(breakeven_atr=1.0)
        _scale_down(pos)
        assert pos.entry_price == pytest.approx(97.5)
        handler = breakeven_handler_factory(pos)
        assert handler.entry_anchor_price == pytest.approx(100.0)
        assert handler.entry_anchor_price != pytest.approx(97.5)

    def test_breakeven_not_triggered_at_vwap_price(self):
        """AC8: price == VWAP 97.5 must NOT arm (gate is 100 + 1*5 = 105)."""
        from v5.bar_processor import BarContext
        from v5.bar_spec import BarSpec
        from v5.exit_handlers import breakeven_handler_factory
        pos = _long_100(breakeven_atr=1.0)
        _scale_down(pos)
        bar_ctx = BarContext(
            bar_spec=BarSpec.from_minutes(60), ts_ns=1_700_000_000_000_000_000,
            close=97.5, high=97.5, low=97.5, volume=0.0, hourly_bar_index=6,
        )
        assert breakeven_handler_factory(pos).check(pos, bar_ctx) is False


# ---------------------------------------------------------------------------
# AC10 — R-anchor invariant
# ---------------------------------------------------------------------------


class TestAC10RAnchorInvariant:
    """AC10 T-B4: r_anchor frozen across scale; Stage 3 reads r_anchor."""

    def test_r_anchor_unchanged_after_increase_default(self):
        """AC10: default freeze_initial_risk=True keeps r_anchor at $100."""
        from v5.exit_handlers import r_multiple_gate_price  # noqa: F401 — RED gate
        pos = _long_100()
        _scale_down(pos)
        assert pos.entry_price == pytest.approx(97.5)
        assert pos.r_anchor_price == pytest.approx(100.0)

    def test_stage3_reads_r_anchor_not_entry_price(self):
        """AC10: 2R gate = r_anchor + 2*initial_risk = 110 (not 107.5 via VWAP)."""
        from v5.exit_handlers import r_multiple_gate_price
        pos = _long_100()
        _scale_down(pos)
        level = r_multiple_gate_price(pos, r_multiple=2.0)
        assert level == pytest.approx(110.0)
        assert level != pytest.approx(107.5)


# ---------------------------------------------------------------------------
# AC11 — liquidation uses post-scale state
# ---------------------------------------------------------------------------


class TestAC11LiquidationAfterIncrease:
    """AC11(a): after increase: entry_price = new VWAP, margin += delta, qty grew."""

    def test_entry_price_is_new_vwap(self):
        """AC11(a): VWAP (10*100+10*95)/20 = 97.5."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); _scale_down(pos)
        assert pos.entry_price == pytest.approx(97.5)

    def test_margin_usd_grew_by_delta(self):
        """AC11(a): margin_usd accumulates the delta (1000 + 950)."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); _scale_down(pos)
        assert pos.margin_usd == pytest.approx(1_950.0)

    def test_quantity_grew_exactly_by_qty_added(self):
        """AC11(a): |quantity| grows by qty_to_add (10 + 10 = 20)."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); _scale_down(pos)
        assert pos.quantity == pytest.approx(20.0)

    def test_liquidation_engine_reads_post_scale_state(self):
        """AC11(a): liquidation helper result reflects mutated state."""
        from v5.bar_processor import liquidation_equity_required
        pos = _long_100(); _scale_down(pos)
        eq_before = liquidation_equity_required(margin_usd=1_000.0,
                                                quantity=10.0, entry_price=100.0)
        eq_after = liquidation_equity_required(margin_usd=pos.margin_usd,
                                               quantity=abs(pos.quantity),
                                               entry_price=pos.entry_price)
        assert eq_after != pytest.approx(eq_before)


def _reduce_half(pos: Position):
    """close_fraction = 5/10 = 0.5."""
    return pos.reduce(qty_to_close=5.0, fill_price=105.0, bar_idx=7,
                      fee_rate=0.0005, atr=5.0, full_entry_fee=0.0, dust_usd=1.0)


class TestAC11LiquidationAfterReduce:
    """AC11(b): reduce leaves entry_price UNCHANGED; margin/qty/funding * (1-frac)."""

    def test_entry_price_unchanged_after_reduce(self):
        """AC11(b): entry_price must be identical pre- and post-reduce."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); pos.cumulative_funding = 20.0
        entry_before = pos.entry_price
        _reduce_half(pos)
        assert pos.entry_price == pytest.approx(entry_before)

    def test_margin_scales_by_close_fraction(self):
        """AC11(b): margin * 0.5 = 500."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); pos.cumulative_funding = 20.0
        _reduce_half(pos)
        assert pos.margin_usd == pytest.approx(500.0)

    def test_quantity_scales_by_close_fraction(self):
        """AC11(b): |quantity| * 0.5 = 5."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100()
        _reduce_half(pos)
        assert abs(pos.quantity) == pytest.approx(5.0)

    def test_cumulative_funding_scales_by_close_fraction(self):
        """AC11(b): cumulative_funding * 0.5 = 10."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); pos.cumulative_funding = 20.0
        _reduce_half(pos)
        assert pos.cumulative_funding == pytest.approx(10.0)

    def test_reduce_result_closed_funding_is_fractional(self):
        """AC11(b): booked funding = close_fraction * pre-reduce funding."""
        from v5.bar_processor import liquidation_equity_required  # noqa: F401 — RED
        pos = _long_100(); pos.cumulative_funding = 20.0
        rr = _reduce_half(pos)
        assert rr.closed_funding == pytest.approx(10.0)
