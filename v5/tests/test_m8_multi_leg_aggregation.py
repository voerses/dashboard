"""M8 — Multi-leg clamp aggregation.

Clamps respect ContingencyType rules from v5/orders.py:compute_reserved_capital:
  - OTOCO: entry + max(sibling margins)
  - OCO: max(legs)
  - OTO: sum(all legs)
  - single-leg: sizing_ctx["margin_usd"] directly

Free-capital and concentration clamps aggregate across legs.
ADV and slippage remain per-leg.

All tests MUST FAIL today — multi-leg aggregation not wired to clamps.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=150_000.0)


class TestOtocoFreeCapitalAggregation:
    """Free-capital clamp reads entry + max(sl, tp) — NOT entry + sl + tp."""

    def test_otoco_reserved_capital_uses_max_of_siblings(self, ctx):
        from v5.orders import Order
        order = ctx.orders.arm_bracket(
            entry_spec={"symbol": "BTC", "direction": "LONG", "size": 1.0,
                        "trigger_price": 50_000.0},
            sl_spec={"trigger_price": 48_000.0},
            tp_spec={"trigger_price": 55_000.0},
        )
        assert isinstance(order, Order)
        from v5.sizing.aggregation import reserved_capital_for_clamp
        reserved = reserved_capital_for_clamp(order)
        # Expected: entry_margin + max(sl_margin, tp_margin); NOT sum of siblings
        legs_by_ref = {lg.leg_ref_id: lg for lg in order.legs}
        entry_m = legs_by_ref[list(legs_by_ref)[0]].sizing_ctx.get("margin_usd", 0.0)
        sibling_margins = [
            lg.sizing_ctx.get("margin_usd", 0.0)
            for lg in order.legs[1:]
        ]
        expected = entry_m + max(sibling_margins)
        sum_version = entry_m + sum(sibling_margins)
        assert reserved == pytest.approx(expected)
        assert reserved != pytest.approx(sum_version)


class TestOcoMaxAggregation:
    """OCO-contingency Order aggregates max(legs), not sum."""

    def test_oco_aggregation_uses_max(self, ctx):
        """Construct OCO 2-leg Order → reserved = max(leg_margins)."""
        from datetime import datetime, timezone
        from v5.orders import (
            Order, Leg, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )

        def mk(ref, margin):
            return Leg(
                leg_ref_id=ref, symbol="BTCUSDT", venue="binance", direction=1, target_qty=1.0, cum_qty=0.0,
                size_share=0.5, order_type="limit", status=LegStatus.ARMED,
                trigger_price=None, limit_price=50_000.0, currency="USDT",
                sizing_ctx={"margin_usd": margin},
            )

        leg_a = mk("A", 100.0)
        leg_b = mk("B", 200.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=datetime.now(tz=timezone.utc),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b),
            fill_policy=LegFillPolicy.BEST_EFFORT,
            contingency=ContingencyType.OCO,
        )
        from v5.sizing.aggregation import reserved_capital_for_clamp
        reserved = reserved_capital_for_clamp(order)
        assert reserved == pytest.approx(200.0)


class TestOtoSumAggregation:
    """OTO-contingency Order aggregates SUM of all leg margins (NOT max, NOT entry+max)."""

    def test_oto_contingency_sums_leg_margins(self, ctx):
        """3-leg OTO Order → reserved = sum(leg_margins)."""
        from datetime import datetime, timezone
        from v5.orders import (
            Order, Leg, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )

        def mk(ref, margin):
            return Leg(
                leg_ref_id=ref, symbol="BTCUSDT", venue="binance", direction=1, target_qty=1.0, cum_qty=0.0,
                size_share=1/3, order_type="limit", status=LegStatus.ARMED,
                trigger_price=None, limit_price=50_000.0, currency="USDT",
                sizing_ctx={"margin_usd": margin},
            )

        leg_a = mk("A", 100.0)
        leg_b = mk("B", 200.0)
        leg_c = mk("C", 300.0)
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=datetime.now(tz=timezone.utc),
            expires_at=None, sizing_ctx={},
            legs=(leg_a, leg_b, leg_c),
            fill_policy=LegFillPolicy.BEST_EFFORT,
            contingency=ContingencyType.OTO,
        )
        from v5.sizing.aggregation import reserved_capital_for_clamp
        reserved = reserved_capital_for_clamp(order)
        # OTO = sum(all legs)
        assert reserved == pytest.approx(600.0)
        # NOT max
        assert reserved != pytest.approx(300.0)
        # NOT entry + max(siblings) (= 100 + 300 = 400)
        assert reserved != pytest.approx(400.0)


class TestSingleLegUsesSizingCtx:
    """Single-leg Order uses sizing_ctx['margin_usd'] directly."""

    def test_single_leg_reads_sizing_ctx_margin(self, ctx):
        from v5.orders import TriggerType
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        # Ensure single-leg path: len(legs) == 0 per M5 convention
        assert len(order.legs) == 0
        from v5.sizing.aggregation import reserved_capital_for_clamp
        reserved = reserved_capital_for_clamp(order)
        expected = order.sizing_ctx.get("margin_usd", 0.0)
        assert reserved == pytest.approx(expected)
