"""M5 — ContingencyType capital aggregation per F3.

Covers:
  - T-M5-23: per-contingency capital rule.
    * NONE/OTO/OUO: sum(leg margin_usd)
    * OCO:         max(leg margin_usd)
    Parametrized over all 4 contingencies.

Design note (R13/R14): per-leg margin is stored in ``Leg.sizing_ctx["margin_usd"]``
consistent with PendingEntry's sizing_ctx pattern; the Leg dataclass does NOT
get a new top-level margin field. ``compute_reserved_capital(order)`` reads
each leg's ``sizing_ctx["margin_usd"]`` and aggregates per
``Order.contingency``.

All tests MUST FAIL today — v5.orders does not exist.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _mk_leg(ref: str, margin_usd: float) -> object:
    """Leg factory with per-leg margin_usd stored in Leg.sizing_ctx (R13/R14).

    The Leg does NOT expose a top-level margin_usd field; per-leg margin
    lives inside the leg's sizing_ctx dict, consistent with PendingEntry's
    sizing_ctx pattern. compute_reserved_capital(order) reads each leg via
    leg.sizing_ctx["margin_usd"].
    """
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        venue="binance",
        direction=1,
        target_qty=1.0,
        cum_qty=0.0,
        size_share=0.5,
        order_type="market",
        status=LegStatus.ARMED,
        trigger_price=None,
        limit_price=None,
        currency="USDT",
        sizing_ctx={"margin_usd": margin_usd},
    )


class TestTM523PerContingencyCapitalRule:
    """T-M5-23: capital aggregation varies per ContingencyType (F3)."""

    @pytest.mark.parametrize("contingency_name,legs_margin,expected_reserve", [
        ("NONE", [100.0, 200.0], 300.0),     # sum
        ("OTO",  [100.0, 200.0], 300.0),     # sum
        ("OUO",  [100.0, 200.0], 300.0),     # sum
        ("OCO",  [100.0, 200.0], 200.0),     # max
        ("OCO",  [50.0, 50.0, 300.0], 300.0), # max of 3
        ("NONE", [50.0, 50.0, 300.0], 400.0), # sum of 3
    ])
    def test_capital_aggregation(self, contingency_name, legs_margin, expected_reserve):
        """T-M5-23: compute_reserved_capital(order) returns expected aggregation."""
        from v5.orders import (
            Order, TriggerType, LegFillPolicy, ContingencyType,
            compute_reserved_capital,
        )
        ctype = getattr(ContingencyType, contingency_name)
        # R13/R14: margin_usd lives in each Leg's sizing_ctx (not in the
        # order-level sizing_ctx via a leg_margins dict).
        legs = tuple(_mk_leg(f"L{i}", m) for i, m in enumerate(legs_margin))
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=legs, fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ctype,
        )
        # Sanity: leg-level sizing_ctx is what the aggregator reads.
        for leg, expected_margin in zip(legs, legs_margin):
            assert leg.sizing_ctx["margin_usd"] == expected_margin
        reserve = compute_reserved_capital(order)
        assert reserve == pytest.approx(expected_reserve), (
            f"{contingency_name}: expected {expected_reserve}, got {reserve}"
        )


class TestTM523ContingencyEnumValues:
    """T-M5-23 sanity: ContingencyType enum values map to FIX 1385."""

    def test_fix_1385_values(self):
        """T-M5-23: NONE=0, OCO=1, OTO=2, OUO=3 per FIX 1385."""
        from v5.orders import ContingencyType
        assert int(ContingencyType.NONE) == 0
        assert int(ContingencyType.OCO) == 1
        assert int(ContingencyType.OTO) == 2
        assert int(ContingencyType.OUO) == 3
