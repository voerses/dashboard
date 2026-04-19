"""M6 — MessageBus pub/sub (T-D20 / AC-D19).

FIFO dispatch, per-handler exception catching, and a grep that no set/frozenset
iteration appears inside v5/data/bus.py hot-path functions.

All tests MUST FAIL today — v5.data.bus does not exist.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _topic():
    from v5.bar_spec import BarSpec
    from v5.data.streams import DataKind, DataStream, InstrumentId, Venue
    inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=inst, data_kind=DataKind.BAR,
                      bar_spec=BarSpec.from_minutes(60))


class TestMessageBusFIFO:
    """T-D20 / AC-D19 — insertion-order deterministic dispatch."""

    def test_handlers_dispatched_in_registration_order(self):
        from v5.data.bus import MessageBus
        bus = MessageBus()
        order: list[str] = []
        t = _topic()
        bus.subscribe(t, lambda ev: order.append("first"))
        bus.subscribe(t, lambda ev: order.append("second"))
        bus.subscribe(t, lambda ev: order.append("third"))
        bus.publish(t, {"bar": 1})
        assert order == ["first", "second", "third"]

    def test_subscribe_returns_handle(self):
        from v5.data.bus import MessageBus, SubscriptionHandle
        bus = MessageBus()
        assert isinstance(bus.subscribe(_topic(), lambda _ev: None), SubscriptionHandle)

    def test_unsubscribe_prevents_future_dispatch(self):
        from v5.data.bus import MessageBus
        bus = MessageBus()
        t = _topic()
        calls = {"a": 0, "b": 0}
        ha = bus.subscribe(t, lambda ev: calls.__setitem__("a", calls["a"] + 1))
        _hb = bus.subscribe(t, lambda ev: calls.__setitem__("b", calls["b"] + 1))
        bus.publish(t, {})
        bus.unsubscribe(ha)
        bus.publish(t, {})
        assert calls == {"a": 1, "b": 2}

    def test_handler_exception_does_not_break_dispatch(self):
        """AC-D19: per-handler exceptions caught; remaining handlers still run."""
        from v5.data.bus import MessageBus
        bus = MessageBus()
        t = _topic()
        seen: list[int] = []
        bus.subscribe(t, lambda ev: seen.append(1))
        bus.subscribe(t, lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        bus.subscribe(t, lambda ev: seen.append(3))
        bus.publish(t, {})
        assert seen == [1, 3]


class TestMessageBusDeterminismGrep:
    """T-D20 / AC-D19 — no set/frozenset iteration on bus hot path."""

    HOT = ("publish", "subscribe", "unsubscribe")

    def test_no_set_iteration_in_hot_path(self):
        path = _project_root / "v5" / "data" / "bus.py"
        assert path.exists(), f"expected {path} to exist"
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in self.HOT:
                body = ast.get_source_segment(src, node) or ""
                assert "set(" not in body, f"bus.py::{node.name} has `set(` — breaks AC-D19"
                assert "frozenset(" not in body, (
                    f"bus.py::{node.name} has `frozenset(` — breaks AC-D19"
                )
