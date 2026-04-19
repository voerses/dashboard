"""M5 — Paper state schema v3 roundtrip + v2 migration.

Covers:
  - T-M5-09: Paper state schema v3 roundtrip — 3-leg Order with mixed leg
    states serializes via Order.to_json and deserializes losslessly via
    Order.from_json; legs, fill_policy, contingency, order_id all preserved.
  - T-M5-10: v2 → v3 migration shim — legacy v2 state file loads with
    default legs=(), fill_policy=UNWIND_ON_REJECT, contingency=NONE,
    order_id populated, linked_order_id=None.

All tests MUST FAIL today — v5.orders + paper_state v3 schema do not exist.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _mk_leg(ref: str, status_name: str, market: str = "perp") -> object:
    from v5.orders import Leg, LegStatus
    return Leg(
        leg_ref_id=ref,
        symbol="BTCUSDT",
        market=market,
        venue="binance",
        direction=1,
        target_qty=1.0,
        cum_qty=0.5,
        size_share=0.5,
        order_type="market",
        status=getattr(LegStatus, status_name),
        trigger_price=100.0,
        limit_price=None,
        currency="USDT",
        dust_usd=1.0,
    )


class TestTM509SchemaV3Roundtrip:
    """T-M5-09: 3-leg mixed-state Order roundtrips via to_json/from_json."""

    def test_three_leg_mixed_state_roundtrip(self):
        """T-M5-09: 3 legs (FILLED, WORKING, ARMED) → JSON → back; identity."""
        from v5.orders import (
            Order, OrderStatus, LegStatus, LegFillPolicy,
            TriggerType, ContingencyType,
        )
        legs = (
            _mk_leg("E", "FILLED", market="perp"),
            _mk_leg("SL", "WORKING", market="perp"),
            _mk_leg("TP", "ARMED", market="perp"),
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx={"margin_usd": 100.0},
            legs=legs,
            fill_policy=LegFillPolicy.OTO_BRACKET,
            contingency=ContingencyType.OTO,
            order_id="ord-042",
        )
        dumped = order.to_json()
        # dumped must be a dict/JSON-encodable with schema_version >= 3.
        raw = json.dumps(dumped)
        loaded = json.loads(raw)
        restored = Order.from_json(loaded)

        assert restored.order_id == "ord-042"
        assert restored.fill_policy == LegFillPolicy.OTO_BRACKET
        assert restored.contingency == ContingencyType.OTO
        assert len(restored.legs) == 3
        restored_by_ref = {lg.leg_ref_id: lg for lg in restored.legs}
        assert restored_by_ref["E"].status == LegStatus.FILLED
        assert restored_by_ref["SL"].status == LegStatus.WORKING
        assert restored_by_ref["TP"].status == LegStatus.ARMED
        # Field-by-field identity for primitive fields.
        assert restored_by_ref["E"].cum_qty == 0.5
        assert restored_by_ref["E"].target_qty == 1.0
        assert restored_by_ref["E"].dust_usd == 1.0

    def test_roundtrip_schema_version_is_3(self):
        """T-M5-09: Order JSON includes schema_version >= 3 marker."""
        from v5.orders import (
            Order, TriggerType, LegFillPolicy, ContingencyType,
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
            legs=(),
            fill_policy=LegFillPolicy.UNWIND_ON_REJECT,
            contingency=ContingencyType.NONE,
        )
        dumped = order.to_json()
        assert dumped.get("schema_version", 0) >= 3


class TestTM510V2MigrationShim:
    """T-M5-10: legacy v2 state deserializes with M5 defaults."""

    def test_v2_state_loads_with_default_legs_empty(self):
        """T-M5-10: v2 Order JSON (no legs, no fill_policy, no contingency)
        loads with legs=(), fill_policy=UNWIND_ON_REJECT, contingency=NONE."""
        from v5.orders import (
            Order, LegFillPolicy, ContingencyType, TriggerType,
        )
        # Synthetic pre-M5 (v2) representation: no M5 fields.
        v2_raw = {
            "schema_version": 2,
            "strategy_id": "s1",
            "token": "BTC",
            "direction": 1,
            "trigger": int(TriggerType.PRICE_ABOVE),
            "trigger_price": 100.0,
            "working_price_source": "last",
            "armed_at": "2026-04-01T00:00:00+00:00",
            "expires_at": None,
            "sizing_ctx": {},
            "state": 1,  # ARMED
            "filled_qty": 0.0,
            "leaves_qty": 0.0,
            "reject_reason": None,
            "strategy_params": {},
            "window_end": 0.0,
            # M5 fields intentionally absent.
        }
        restored = Order.from_json(v2_raw)
        assert restored.legs == ()
        assert restored.fill_policy == LegFillPolicy.UNWIND_ON_REJECT
        assert restored.contingency == ContingencyType.NONE
        # order_id must be populated (generated if absent).
        assert restored.order_id

    def test_v2_to_v3_paper_state_migration(self, tmp_path):
        """T-M5-10: full paper state v2 file loads via read_paper_state and
        gets migrated to v3 (Position.order_id + Position.leg_ref_id default
        None; Order legs/fill_policy/contingency default)."""
        from v5.paper_state import read_paper_state
        v2_state = {
            "schema_version": 2,
            "positions": [
                {
                    "token": "BTC", "quantity": 1.0, "entry_price": 100.0,
                    # No order_id, no leg_ref_id in v2.
                },
            ],
            "pending_entries": [
                {
                    "strategy_id": "s1", "token": "BTC", "direction": 1,
                    "trigger": 1, "trigger_price": 100.0,
                    "working_price_source": "last",
                    "armed_at": "2026-04-01T00:00:00+00:00",
                    "expires_at": None, "sizing_ctx": {},
                    "state": 1, "filled_qty": 0.0, "leaves_qty": 0.0,
                    "reject_reason": None, "strategy_params": {},
                    "window_end": 0.0,
                },
            ],
        }
        p = tmp_path / "state.json"
        p.write_text(json.dumps(v2_state))
        migrated = read_paper_state(p)
        # R10: deterministic assertions — no disjunctive fallbacks.
        assert migrated.get("schema_version") == 3
        # Positions get default order_id=None + leg_ref_id=None
        pos0 = migrated["positions"][0]
        assert pos0.get("order_id") is None
        assert pos0.get("leg_ref_id") is None
        # Orders get default M5 fields (populated on the canonical 'open_orders'
        # list per design F4 rename).
        ord_list = migrated.get("open_orders", [])
        assert ord_list, "v3 migration must populate open_orders"
        for o in ord_list:
            assert o["fill_policy"] == "unwind_on_reject"
            assert o["contingency"] == 0
            # legs default-empty; serialized as [] (tuple → list in JSON).
            assert o.get("legs") == []
