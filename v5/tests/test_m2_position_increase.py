"""Acceptance tests for M2 — Position.increase primitive.

Covers:
  - AC1: Position.increase API, WACB entry, margin/quantity updates, ScalingEvent append, no ClosedTrade
  - AC2: stop_override / never-loosen default
  - AC3: initial_risk frozen vs refreshed; breakeven_triggered preserved; r_anchor_price behavior
  - AC21: time counters (entry_bar, entry_timestamp) never reset on increase
  - Q2:   ValueError raised on qty_to_add <= 0

All tests MUST FAIL until Position.increase / ScalingEvent exist in v5/position.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.position import Position, ScalingEvent  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_long_position(
    entry_price: float = 100.0,
    quantity: float = 10.0,
    margin_usd: float = 1_000.0,
    direction: int = 1,
    stop_price: float = 90.0,
    initial_risk: float = 10.0,
    entry_bar: int = 5,
    entry_timestamp: str = "2026-01-01T00:00:00Z",
    cumulative_funding: float = 0.0,
    breakeven_triggered: bool = False,
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
        stop_price=stop_price,
        highest=entry_price,
        lowest=entry_price,
        initial_risk=initial_risk,
        cumulative_funding=cumulative_funding,
        entry_timestamp=entry_timestamp,
        breakeven_triggered=breakeven_triggered,
    )


# ===================================================================
# AC1 — WACB, quantity, margin, ScalingEvent, no ClosedTrade
# ===================================================================

class TestAC1IncreaseCore:
    """Position.increase updates entry (WACB), margin, quantity, appends ScalingEvent."""

    def test_weighted_average_entry_price(self):
        """WACB: (10*100 + 10*120)/20 = 110."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.entry_price == pytest.approx(110.0)

    def test_margin_accumulates(self):
        pos = _make_long_position(margin_usd=1_000.0)
        pos.increase(
            qty_to_add=5.0, fill_price=100.0, margin_delta=500.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.margin_usd == pytest.approx(1_500.0)

    def test_quantity_grows_signed_with_direction(self):
        """Long +direction grows qty positive; short grows negatively."""
        pos = _make_long_position(quantity=10.0, direction=1)
        pos.increase(
            qty_to_add=5.0, fill_price=100.0, margin_delta=500.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.quantity == pytest.approx(15.0)

    def test_quantity_grows_signed_for_short(self):
        pos = _make_long_position(quantity=10.0, direction=-1)
        assert pos.quantity == pytest.approx(-10.0)
        pos.increase(
            qty_to_add=5.0, fill_price=100.0, margin_delta=500.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.quantity == pytest.approx(-15.0)

    def test_appends_scaling_event_kind_increase(self):
        pos = _make_long_position()
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert len(pos.scaling_events) == 1
        ev = pos.scaling_events[0]
        assert isinstance(ev, ScalingEvent)
        assert ev.kind == "increase"
        assert ev.fill_price == pytest.approx(110.0)

    def test_does_not_book_closed_trade(self):
        """Increase only mutates position; it does not produce a ClosedTrade."""
        pos = _make_long_position()
        # The method returns nothing or a non-ClosedTrade; the method itself
        # never books. We can only assert indirectly: Position has no
        # closed_trades attribute, and no exception is raised when no
        # position_manager is wired.
        result = pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        # Must not be a ClosedTrade dataclass
        from v5.position import ClosedTrade
        assert not isinstance(result, ClosedTrade)


# ===================================================================
# AC2 — stop_price on increase
# ===================================================================

class TestAC2StopOnIncrease:
    """stop_override used as-is; else never-loosen default."""

    def test_stop_override_is_used_verbatim(self):
        pos = _make_long_position(stop_price=90.0)
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
            stop_override=95.0,
        )
        assert pos.stop_price == pytest.approx(95.0)

    def test_never_loosen_long_keeps_tighter_old_stop(self):
        """Long: never-loosen = max(old_stop, new_avg_px - stop_mult*atr)."""
        # old entry 100, qty 10; new fill 120 qty 10 => avg_px 110
        # new-stop candidate: 110 - 2*5 = 100; old stop 90
        # max(90, 100) == 100 -> tightens (not a "loosen" for long)
        pos = _make_long_position(entry_price=100.0, quantity=10.0,
                                  stop_price=90.0)
        pos.stop_mult = 2.0
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        # long -> max(90, 110 - 10) = 100
        assert pos.stop_price == pytest.approx(100.0)

    def test_never_loosen_long_preserves_old_if_tighter(self):
        """Long: if old stop higher than new-candidate, keep old."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0,
                                  stop_price=105.0)  # already above entry
        pos.stop_mult = 2.0
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        # max(105, 110 - 10 = 100) = 105
        assert pos.stop_price == pytest.approx(105.0)

    def test_never_loosen_short_uses_min(self):
        """Short: never-loosen = min(old_stop, new_avg_px + stop_mult*atr)."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0, direction=-1,
                                  stop_price=110.0)
        pos.stop_mult = 2.0
        # short add at 80 -> avg_px = (10*100 + 10*80)/20 = 90
        # new-stop candidate: 90 + 10 = 100; old 110
        # min(110, 100) = 100
        pos.increase(
            qty_to_add=10.0, fill_price=80.0, margin_delta=800.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.stop_price == pytest.approx(100.0)


# ===================================================================
# AC3 — initial_risk frozen vs refreshed; breakeven; r_anchor_price
# ===================================================================

class TestAC3RiskAndAnchorBehavior:
    """freeze_initial_risk flag controls whether risk/r_anchor refreshes."""

    def test_default_freezes_initial_risk(self):
        pos = _make_long_position(initial_risk=10.0)
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=8.0,
            # freeze_initial_risk not passed => default True
        )
        assert pos.initial_risk == pytest.approx(10.0)

    def test_refresh_when_freeze_false(self):
        """When freeze_initial_risk=False, initial_risk = stop_mult * atr on new avg_px."""
        pos = _make_long_position(initial_risk=10.0)
        pos.stop_mult = 2.0
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=8.0,
            freeze_initial_risk=False,
        )
        # new risk = stop_mult * atr = 2 * 8 = 16
        assert pos.initial_risk == pytest.approx(16.0)

    def test_r_anchor_frozen_by_default(self):
        pos = _make_long_position(entry_price=100.0, initial_risk=10.0)
        # r_anchor_price should be set to original entry_price on init
        original_anchor = pos.r_anchor_price
        assert original_anchor == pytest.approx(100.0)
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.r_anchor_price == pytest.approx(100.0)

    def test_r_anchor_refreshed_on_freeze_false(self):
        pos = _make_long_position(entry_price=100.0, initial_risk=10.0)
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
            freeze_initial_risk=False,
        )
        # new avg_px = 110 -> r_anchor_price = 110
        assert pos.r_anchor_price == pytest.approx(110.0)

    def test_breakeven_triggered_preserved_regardless_of_freeze(self):
        """breakeven_triggered must NOT reset on increase, freeze_initial_risk unrelated."""
        pos = _make_long_position(breakeven_triggered=True)
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
            freeze_initial_risk=False,
        )
        assert pos.breakeven_triggered is True


# ===================================================================
# AC21 — time counters never reset on increase
# ===================================================================

class TestAC21TimeCountersPreserved:
    """entry_bar and entry_timestamp never change on increase."""

    def test_entry_bar_unchanged(self):
        pos = _make_long_position(entry_bar=5)
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=42, fee_rate=0.0005, atr=5.0,
        )
        assert pos.entry_bar == 5

    def test_entry_timestamp_unchanged(self):
        pos = _make_long_position(entry_timestamp="2026-01-01T00:00:00Z")
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=42, fee_rate=0.0005, atr=5.0,
        )
        assert pos.entry_timestamp == "2026-01-01T00:00:00Z"


# ===================================================================
# Q2 — qty_to_add <= 0 raises ValueError
# ===================================================================

class TestQ2InvalidQtyToAdd:
    """Per Q2 resolution, qty_to_add must be strictly positive."""

    def test_zero_raises(self):
        pos = _make_long_position()
        with pytest.raises(ValueError):
            pos.increase(
                qty_to_add=0.0, fill_price=110.0, margin_delta=550.0,
                bar_idx=7, fee_rate=0.0005, atr=5.0,
            )

    def test_negative_raises(self):
        pos = _make_long_position()
        with pytest.raises(ValueError):
            pos.increase(
                qty_to_add=-1.0, fill_price=110.0, margin_delta=550.0,
                bar_idx=7, fee_rate=0.0005, atr=5.0,
            )


# ===================================================================
# AC15 — no force-close after increase: Position.increase must NOT raise
# an exit if the new stop is breached. Phase 3 StopLossHandler fires it
# naturally on the NEXT bar, not inside increase().
# ===================================================================

class TestAC15NoForceCloseAfterIncrease:
    """AC15 + TG3 — increase() must return normally even if stop_override is
    already "breached" by the current price. The StopLossHandler fires later
    in Phase 3, not inside the increase primitive."""

    def test_stop_already_breached_via_override_does_not_raise(self):
        """Simulate a long where stop_override sits ABOVE entry_price — which
        would trigger Phase 3 stop-out — but Position.increase itself does NOT
        raise, does NOT close, and returns normally."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0, stop_price=90.0)
        # Force a "breached-at-creation" state via stop_override (the AC20
        # normal path wouldn't allow this via never-loosen logic, but the
        # test must use stop_override to simulate it).
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
            stop_override=115.0,  # ABOVE both old entry and current fill
        )
        # Assert that increase() did NOT force-close
        assert pos.quantity != 0.0
        assert pos.quantity == pytest.approx(15.0)
        # The stop_override must be applied verbatim (AC2)
        assert pos.stop_price == pytest.approx(115.0)
        # The position is still open — no terminal booking done in increase()
        # (a ClosedTrade-like return is forbidden by AC1)
        from v5.position import ClosedTrade
        # Re-probe the method contract: increase does not emit ClosedTrade


# ===================================================================
# AC16 — trailing-stop state preserved across increase
# ===================================================================

class TestAC16TrailingStatePreserved:
    """AC16 + TG4 — pos.highest / pos.lowest continue tracking across
    an increase; the helper must NOT reset them."""

    def test_highest_preserved_on_long_increase(self):
        """Long at $100, price rises to $110 (highest=$110), increase at $108.
        After increase, pos.highest must still be 110 — NOT reset."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0, direction=1)
        # Strategy ran: price moved up to $110, trail bookkeeping set highest
        pos.highest = 110.0
        pos.increase(
            qty_to_add=5.0, fill_price=108.0, margin_delta=540.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.highest == pytest.approx(110.0)

    def test_lowest_preserved_on_short_increase(self):
        """Short at $100, price dropped to $90 (lowest=$90), increase at $92.
        After increase, pos.lowest must still be 90 — NOT reset."""
        pos = _make_long_position(entry_price=100.0, quantity=10.0, direction=-1)
        pos.lowest = 90.0
        pos.increase(
            qty_to_add=5.0, fill_price=92.0, margin_delta=460.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        assert pos.lowest == pytest.approx(90.0)
