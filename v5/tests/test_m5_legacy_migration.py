"""M5 — Legacy simulator.PendingEntry deletion + field migration.

Covers:
  - T-M5-17: `simulator.PendingEntry` no longer exists;
    `grep simulator.PendingEntry v5/` returns zero hits; `trigger_fn`
    opaque callables all converted to `TriggerType` enum + params.
  - T-M5-24: legacy simulator.PendingEntry field migration — synthetic
    pre-M5 legacy-style record round-trips through the migration path
    with every field mapped to its new Order equivalent (no data loss).

All tests MUST FAIL today — v5.orders does not exist; v5.simulator still
has the legacy PendingEntry class until the rename sweep lands.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class TestTM517LegacySimulatorPendingEntryDeletion:
    """T-M5-17: class PendingEntry in v5/simulator.py must be deleted."""

    def test_simulator_pending_entry_class_not_defined(self):
        """T-M5-17: v5/simulator.py no longer defines `class PendingEntry`."""
        sim_path = _project_root / "v5" / "simulator.py"
        if not sim_path.is_file():
            pytest.fail("v5/simulator.py missing — cannot verify PendingEntry deletion")
        content = sim_path.read_text()
        # Must not contain a top-level class definition for PendingEntry.
        pattern = re.compile(r"^\s*class\s+PendingEntry\b", re.MULTILINE)
        assert not pattern.search(content), (
            "v5/simulator.py still defines `class PendingEntry` — "
            "M5 must delete it (the legacy local dataclass)."
        )

    def test_simulator_pending_entry_construction_absent(self):
        """T-M5-17: no `PendingEntry(` call sites remain in v5/simulator.py."""
        sim_path = _project_root / "v5" / "simulator.py"
        if not sim_path.is_file():
            pytest.fail("v5/simulator.py missing")
        content = sim_path.read_text()
        # Strip comments/strings isn't reliable; surface the count instead.
        # "simulator.PendingEntry(" construction / reference pattern:
        forbidden_refs = re.findall(r"simulator\.PendingEntry\b", content)
        assert not forbidden_refs, (
            f"{len(forbidden_refs)} reference(s) to simulator.PendingEntry remain"
        )

    def test_trigger_fn_callable_replaced(self):
        """T-M5-17: Order.arm() must NOT accept a trigger_fn kwarg.

        Behavioral check (R12): the legacy opaque-callable API is gone.
        Attempting to construct an Order via trigger_fn= must raise TypeError
        (unknown keyword argument). Constructing via TriggerType enum +
        trigger_price must succeed. Behavioral assertions replace the
        source-tree regex grep — tests behavior, not filesystem layout.
        """
        from v5.orders import Order, TriggerType

        # Legacy callable API — must be rejected.
        with pytest.raises(TypeError):
            Order.arm(
                strategy_id="s1", token="BTC", direction=1,
                trigger_fn=lambda bar: bar.close > 100,  # type: ignore[call-arg]
                working_price_source="last",
                armed_at=_dt("2026-04-01T00:00:00"),
                expires_at=None, sizing_ctx={},
            )

        # M5 canonical API — must succeed.
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        assert order.trigger == TriggerType.PRICE_ABOVE
        assert order.trigger_price == 100.0


class TestTM524LegacyFieldMigration:
    """T-M5-24: legacy simulator.PendingEntry fields → Order equivalents."""

    def test_legacy_fields_migrate_losslessly(self):
        """T-M5-24: construct an Order from legacy-shaped kwargs (per F4
        migration table) and verify every field round-trips semantically."""
        from v5.orders import Order, TriggerType, OrderStatus
        # Legacy simulator.PendingEntry shape (F4 table):
        #   strategy_id, symbol, type, direction, price, time, state,
        #   signal_bar, entry_bar, conviction, trigger_fn, pending_entries
        legacy = {
            "strategy_id": "s524",
            "symbol": "BTC",              # → token
            "type": "stop_buy",            # → dropped (replaced by trigger)
            "direction": 1,
            "price": 100.0,                # → trigger_price
            "time": _dt("2026-04-01T00:00:00").timestamp(),  # → armed_at
            "state": "armed",              # → OrderStatus.ARMED
            "signal_bar": 0,               # → dropped
            "entry_bar": 4,                # → expires_at = armed_at + delta
            "conviction": 0.8,             # → sizing_ctx["conviction"]
        }
        order = Order.from_legacy_simulator_pending_entry(
            legacy, bar_period_seconds=3600,
        )
        assert order.strategy_id == "s524"
        assert order.token == "BTC"
        assert order.direction == 1
        assert order.trigger_price == 100.0
        assert order.state == OrderStatus.ARMED
        # armed_at converted from epoch
        assert order.armed_at == _dt("2026-04-01T00:00:00")
        # expires_at derived (armed_at + (entry_bar - signal_bar) * bar_period)
        assert order.expires_at is not None
        assert (order.expires_at - order.armed_at).total_seconds() == 4 * 3600
        # conviction moved to sizing_ctx
        assert order.sizing_ctx.get("conviction") == 0.8
        # trigger enum assigned (not a callable)
        assert isinstance(order.trigger, TriggerType)

    def test_state_pending_entries_renamed_to_open_orders(self):
        """T-M5-24 / F4: SimulationState.pending_entries → state.open_orders.

        Post-M5 Task 14b: the SimulationState container exposes
        ``open_orders: list[Order]`` as the primary store for open/armed orders.
        The pre-M5 name ``pending_entries`` must be gone.

        Test-dispute resolution (2026-04-19): original placeholder was a
        bare pytest.fail() with no assertions. Replaced with real attribute
        checks per F4 rename semantics.
        """
        from v5.simulator import SimulationState
        state = SimulationState(initial_capital=100_000.0)
        # New API: open_orders exists and is a list
        assert hasattr(state, "open_orders"), (
            "SimulationState must expose `open_orders` after M5 Task 14b rename"
        )
        assert isinstance(state.open_orders, list), (
            "open_orders must be a list (list[Order])"
        )
        # Legacy API: pending_entries must be gone
        assert not hasattr(state, "pending_entries"), (
            "SimulationState.pending_entries must be removed (F4 rename)"
        )
