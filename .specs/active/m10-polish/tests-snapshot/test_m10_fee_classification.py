"""M10 E2 — Maker/taker fee classification (AC #21).

Verifies that ``ClosedTrade.entry_fee`` / ``ClosedTrade.exit_fee`` math
discriminates between MAKER and TAKER fills per the Binance schedule
declared in ``v5/data/instruments.py`` (maker_fee_bps=2.0,
taker_fee_bps=4.0 at lines 169-170).

Three synthetic cases:

    * ``order_type="limit"`` that rests and fills at a mid price →
      MAKER fee.
    * ``order_type="market"`` that crosses the book on arrival →
      TAKER fee.
    * ``TriggerType.PRICE_ABOVE`` entry that crosses on trigger →
      TAKER fee (stop-market semantics).

MUST FAIL TODAY (RED):
    * ``v5.data.instruments.get_fee_schedule`` helper does not exist.
    * ``ClosedTrade.fill_type`` field does not exist on the dataclass.
    * Fee classification plumbing is Phase-4 work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FILL_SIZE = 0.5  # BTC
_FILL_PRICE = 68_000.0  # USD / BTC
_NOTIONAL = _FILL_SIZE * _FILL_PRICE


def _expected_fee(bps: float) -> float:
    """Fee = notional × bps / 10000."""
    return _NOTIONAL * bps / 10_000.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFeeScheduleLookup:
    """AC #21 — Binance perp schedule exposes maker/taker split."""

    def test_get_fee_schedule_returns_maker_taker_bps(self) -> None:
        from v5.data.instruments import get_fee_schedule

        schedule = get_fee_schedule("binance_perp")
        assert isinstance(schedule, dict), (
            "get_fee_schedule must return a dict with maker_bps + taker_bps."
        )
        assert schedule.get("maker_bps") == 2.0, (
            "Binance perp maker fee = 2.0 bps (instruments.py:169)."
        )
        assert schedule.get("taker_bps") == 4.0, (
            "Binance perp taker fee = 4.0 bps (instruments.py:170)."
        )


class TestLimitRestingFillIsMaker:
    """AC #21 — limit order that rests and fills at mid charges maker fee."""

    def test_limit_rest_fill_charges_maker_fee(self) -> None:
        from v5.data.instruments import get_fee_schedule
        from v5.orders import Order, TriggerType
        from v5.position import ClosedTrade  # noqa: F401 (ensures dataclass loaded)

        schedule = get_fee_schedule("binance_perp")
        maker_bps = schedule["maker_bps"]

        # Build a synthetic resting-limit Order that fills at the quoted price.
        # Phase 4 exposes a test-friendly `simulate_limit_rest_fill` helper on
        # paper_engine; this is the surface Phase-3 binds against (expect
        # ImportError today).
        from v5.paper_engine import simulate_limit_rest_fill

        closed_trade = simulate_limit_rest_fill(
            token="BTC",
            direction=1,
            fill_size=_FILL_SIZE,
            fill_price=_FILL_PRICE,
            limit_price=_FILL_PRICE,  # rests at the mid and fills at mid
        )
        assert hasattr(closed_trade, "fill_type"), (
            "ClosedTrade must expose `fill_type` field ('maker'|'taker') "
            "after Phase 4 lands AC #21."
        )
        assert closed_trade.fill_type == "maker", (
            f"Resting limit fill must be classified maker, got "
            f"{closed_trade.fill_type!r}."
        )
        expected = _expected_fee(maker_bps)
        assert closed_trade.entry_fee == pytest.approx(expected, abs=1e-6), (
            f"entry_fee = size × price × maker_bps/10000 "
            f"= {_FILL_SIZE}×{_FILL_PRICE}×{maker_bps}/10000 = {expected}, "
            f"got {closed_trade.entry_fee}."
        )


class TestMarketCrossBookFillIsTaker:
    """AC #21 — market order that crosses the book charges taker fee."""

    def test_market_cross_book_charges_taker_fee(self) -> None:
        from v5.data.instruments import get_fee_schedule
        from v5.paper_engine import simulate_market_cross_fill

        schedule = get_fee_schedule("binance_perp")
        taker_bps = schedule["taker_bps"]

        closed_trade = simulate_market_cross_fill(
            token="BTC",
            direction=1,
            fill_size=_FILL_SIZE,
            fill_price=_FILL_PRICE,
        )
        assert hasattr(closed_trade, "fill_type"), (
            "ClosedTrade must expose `fill_type` field after Phase 4."
        )
        assert closed_trade.fill_type == "taker", (
            f"Market-cross fill must be classified taker, got "
            f"{closed_trade.fill_type!r}."
        )
        expected = _expected_fee(taker_bps)
        assert closed_trade.entry_fee == pytest.approx(expected, abs=1e-6), (
            f"entry_fee = size × price × taker_bps/10000 = {expected}, "
            f"got {closed_trade.entry_fee}."
        )


class TestTriggeredEntryIsTaker:
    """AC #21 — stop-market triggered entry that crosses is taker."""

    def test_price_above_trigger_entry_is_taker(self) -> None:
        from v5.data.instruments import get_fee_schedule
        from v5.orders import TriggerType
        from v5.paper_engine import simulate_triggered_entry_fill

        schedule = get_fee_schedule("binance_perp")
        taker_bps = schedule["taker_bps"]

        # PRICE_ABOVE on a long: price rises through trigger, market order
        # crosses ask -> taker semantics.
        closed_trade = simulate_triggered_entry_fill(
            token="BTC",
            direction=1,
            trigger=TriggerType.PRICE_ABOVE,
            trigger_price=_FILL_PRICE - 10.0,
            fill_size=_FILL_SIZE,
            fill_price=_FILL_PRICE,
        )
        assert hasattr(closed_trade, "fill_type")
        assert closed_trade.fill_type == "taker", (
            "Triggered entry that crosses the book must be taker."
        )
        expected = _expected_fee(taker_bps)
        assert closed_trade.entry_fee == pytest.approx(expected, abs=1e-6)


class TestClosedTradeFillTypeFieldPresent:
    """AC #21 RED guard — ClosedTrade must carry fill_type."""

    def test_closed_trade_dataclass_exposes_fill_type_field(self) -> None:
        from v5.position import ClosedTrade

        fields = ClosedTrade.__dataclass_fields__
        assert "fill_type" in fields, (
            "ClosedTrade.fill_type field required for AC #21 classification. "
            "Phase 4 must add this dataclass field with values in "
            "('maker', 'taker')."
        )
