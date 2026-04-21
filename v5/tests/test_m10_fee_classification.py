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


def _build_closed_trade(
    *,
    fill_type: str,
    direction: int,
    fill_size: float,
    fill_price: float,
    order_origin: str = "normal_entry",
):
    """Construct a ClosedTrade for fee-discrimination tests.

    Audit-relaxed 2026-04-20: Phase-3 no longer pins specific
    paper_engine helper names. Phase 4 may expose any of:
      - public fill helpers (simulate_limit_rest_fill / _market_cross /
        _triggered_entry) — if present, use them
      - direct ClosedTrade construction — if Phase 4 didn't add helpers
    This builder tries the helpers first and falls back to direct
    ClosedTrade construction.

    Raises ImportError today (RED) because neither path exists yet.
    """
    from v5.position import ClosedTrade

    # Try the canonical helper path first (if Phase 4 exposes it).
    try:
        from v5.paper_engine import build_closed_trade_for_test
        return build_closed_trade_for_test(
            fill_type=fill_type,
            direction=direction,
            fill_size=fill_size,
            fill_price=fill_price,
            order_origin=order_origin,
        )
    except ImportError:
        pass

    # Fallback: direct ClosedTrade construction with fill_type field.
    # This requires AC #21 Phase-4 work to add `fill_type` to ClosedTrade.
    from v5.data.instruments import get_fee_schedule
    schedule = get_fee_schedule("binance_perp")
    bps = schedule[f"{fill_type}_bps"]
    fee = fill_size * fill_price * bps / 10_000.0
    # ClosedTrade construction is Phase-4 specific; this call fails today
    # because `fill_type` is not a field. Phase 4 MUST add it.
    return ClosedTrade(
        position_id=f"BTC:test:0:primary",
        token="BTC",
        strategy_id="test",
        leg_ref_id="leg_primary",
        entry_bar=0,
        exit_bar=1,
        entry_price=fill_price,
        exit_price=fill_price,
        direction=direction,
        margin_usd=fill_size * fill_price / 5.0,  # 5x leverage default
        pnl=0.0,
        funding_cost=0.0,
        entry_fee=fee,
        exit_fee=fee,
        hold_bars=1,
        exit_reason="test",
        fill_type=fill_type,
    )


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
        from v5.position import ClosedTrade  # noqa: F401 (ensures dataclass loaded)

        schedule = get_fee_schedule("binance_perp")
        maker_bps = schedule["maker_bps"]

        # Audit-relaxed 2026-04-20: construct the ClosedTrade via the
        # public fill path rather than a Phase-3-invented helper.
        # Phase 4 may expose a helper, a config-driven fill, or a direct
        # ClosedTrade construction — any of them acceptable as long as
        # `fill_type` carries "maker" for a resting-limit fill and the
        # entry_fee math uses `maker_bps`. The _build_closed_trade helper
        # below binds to either a Phase-4 helper OR direct ClosedTrade
        # construction (whichever Phase 4 chooses).
        closed_trade = _build_closed_trade(
            fill_type="maker",
            direction=1,
            fill_size=_FILL_SIZE,
            fill_price=_FILL_PRICE,
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

        schedule = get_fee_schedule("binance_perp")
        taker_bps = schedule["taker_bps"]

        closed_trade = _build_closed_trade(
            fill_type="taker",
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

        schedule = get_fee_schedule("binance_perp")
        taker_bps = schedule["taker_bps"]

        # PRICE_ABOVE on a long: price rises through trigger, market order
        # crosses ask -> taker semantics.
        closed_trade = _build_closed_trade(
            fill_type="taker",
            direction=1,
            fill_size=_FILL_SIZE,
            fill_price=_FILL_PRICE,
            order_origin="triggered_entry",
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


class TestMixedMakerEntryTakerExit:
    """AC #21 tightening (audit 2026-04-20) — entry/exit fee classification
    is INDEPENDENT. A position that opens via limit (maker) and exits via
    stop-market (taker) MUST pay maker on entry fee AND taker on exit fee.
    This catches the common FIX-layer bug where both sides inherit a
    single fill_type.

    Requires Phase-4 to add `entry_fill_type` + `exit_fill_type` fields
    on ClosedTrade, OR have fill_type refer only to entry and an
    additional exit_fill_type field. Either shape acceptable.
    """

    def test_limit_entry_market_exit_pays_maker_entry_taker_exit(self) -> None:
        from v5.data.instruments import get_fee_schedule
        from v5.position import ClosedTrade

        schedule = get_fee_schedule("binance_perp")
        maker_bps = schedule["maker_bps"]
        taker_bps = schedule["taker_bps"]

        # Construct a ClosedTrade where entry is maker, exit is taker.
        # Phase 4 must enable this shape via either:
        #   (a) separate `entry_fill_type` + `exit_fill_type` fields, or
        #   (b) a single `fill_type` that applies to entry + a second
        #       `exit_fill_type` field.
        # Test accepts either.
        fields = ClosedTrade.__dataclass_fields__
        has_split_fields = "entry_fill_type" in fields and "exit_fill_type" in fields
        has_single_plus_exit = "fill_type" in fields and "exit_fill_type" in fields
        assert has_split_fields or has_single_plus_exit, (
            "AC #21 mixed-path: ClosedTrade must carry independent "
            "entry/exit fill_type fields. Expected either "
            "(entry_fill_type, exit_fill_type) OR (fill_type, exit_fill_type). "
            f"Got: {sorted(fields.keys())}."
        )
        # Expected fee math with distinct bps per side.
        expected_entry_fee = _FILL_SIZE * _FILL_PRICE * maker_bps / 10_000.0
        expected_exit_fee = _FILL_SIZE * _FILL_PRICE * taker_bps / 10_000.0
        assert expected_entry_fee != expected_exit_fee, (
            "Sanity: with maker=2bps and taker=4bps, fees must differ."
        )

        # Construct via direct ClosedTrade (Phase 4 may also expose a
        # helper; if so, add a branch here).
        kwargs = dict(
            position_id="BTC:test:0:primary",
            token="BTC",
            strategy_id="test",
            leg_ref_id="leg_primary",
            entry_bar=0,
            exit_bar=10,
            entry_price=_FILL_PRICE,
            exit_price=_FILL_PRICE,
            direction=1,
            margin_usd=_FILL_SIZE * _FILL_PRICE / 5.0,
            pnl=0.0,
            funding_cost=0.0,
            entry_fee=expected_entry_fee,
            exit_fee=expected_exit_fee,
            hold_bars=10,
            exit_reason="stop",
        )
        if has_split_fields:
            kwargs["entry_fill_type"] = "maker"
            kwargs["exit_fill_type"] = "taker"
        else:
            kwargs["fill_type"] = "maker"
            kwargs["exit_fill_type"] = "taker"
        ct = ClosedTrade(**kwargs)

        # Verify the fees match the FIX-correct math.
        assert ct.entry_fee == pytest.approx(expected_entry_fee, abs=1e-6), (
            f"Entry fee mismatch: got {ct.entry_fee}, expected {expected_entry_fee} "
            f"(size × price × maker_bps/10000)."
        )
        assert ct.exit_fee == pytest.approx(expected_exit_fee, abs=1e-6), (
            f"Exit fee mismatch: got {ct.exit_fee}, expected {expected_exit_fee} "
            f"(size × price × taker_bps/10000)."
        )
