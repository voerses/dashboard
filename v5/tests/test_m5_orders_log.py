"""M5 — orders_log.jsonl rename + new event types + dual-write.

Covers:
  - T-M5-15: JSONL audit log renamed pending_entries_log.jsonl → orders_log.jsonl.
  - New event types present: leg_filled, leg_rejected, sibling_unwound,
    bracket_activated.
  - Dual-write: legacy events (arm, trigger, fire, expire, cancel) mirror to
    armed_log.jsonl (preserved for M5, dropped in M10). New M5 events
    (leg_filled etc.) are orders_log.jsonl ONLY per design F11.

All tests MUST FAIL today — v5.orders + OrdersLog writer do not exist.
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


def _read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


class TestTM515OrdersLogRenameAndEvents:
    """T-M5-15: new path + new event types + dual-write legacy events."""

    def test_orders_log_file_name(self, tmp_path):
        """T-M5-15: audit log writes to orders_log.jsonl (not pending_entries_log.jsonl)."""
        from v5.orders_log import OrdersLog
        log = OrdersLog(root=tmp_path)
        log.append({"event": "armed", "order_id": "ord-001"})
        log.flush()
        assert (tmp_path / "orders_log.jsonl").is_file(), (
            f"orders_log.jsonl missing; dir={list(tmp_path.iterdir())}"
        )

    def test_legacy_events_dual_write_to_armed_log(self, tmp_path):
        """T-M5-15 / F11: legacy events (arm/trigger/fire/expire/cancel)
        dual-write to orders_log.jsonl AND armed_log.jsonl."""
        from v5.orders_log import OrdersLog
        log = OrdersLog(root=tmp_path)
        log.append({"event": "armed", "order_id": "ord-001"})
        log.append({"event": "fire", "order_id": "ord-001"})
        log.flush()
        orders = _read_jsonl(tmp_path / "orders_log.jsonl")
        armed = _read_jsonl(tmp_path / "armed_log.jsonl")
        # Both log files must contain armed + fire events.
        orders_events = [e["event"] for e in orders]
        armed_events = [e["event"] for e in armed]
        assert "armed" in orders_events
        assert "fire" in orders_events
        assert "armed" in armed_events
        assert "fire" in armed_events

    @pytest.mark.parametrize("event_name", [
        "leg_filled", "leg_rejected", "sibling_unwound", "bracket_activated",
    ])
    def test_new_m5_events_only_in_orders_log(self, tmp_path, event_name):
        """T-M5-15 / F11: new M5 events write to orders_log.jsonl ONLY,
        NOT dual-written to armed_log.jsonl (old readers don't understand)."""
        from v5.orders_log import OrdersLog
        log = OrdersLog(root=tmp_path)
        log.append({"event": event_name, "order_id": "ord-001",
                    "leg_ref_id": "A"})
        log.flush()
        orders = _read_jsonl(tmp_path / "orders_log.jsonl")
        armed = _read_jsonl(tmp_path / "armed_log.jsonl")
        assert any(e["event"] == event_name for e in orders), (
            f"New M5 event {event_name} missing from orders_log.jsonl"
        )
        assert not any(e["event"] == event_name for e in armed), (
            f"New M5 event {event_name} leaked into armed_log.jsonl — must be "
            f"orders_log.jsonl only per F11"
        )

    def test_log_entries_are_valid_json(self, tmp_path):
        """T-M5-15: every line in orders_log.jsonl is a complete JSON object."""
        from v5.orders_log import OrdersLog
        log = OrdersLog(root=tmp_path)
        log.append({"event": "armed", "order_id": "ord-001"})
        log.append({"event": "leg_filled", "order_id": "ord-001",
                    "leg_ref_id": "A"})
        log.flush()
        text = (tmp_path / "orders_log.jsonl").read_text()
        for line in text.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)  # raises if malformed
            assert "event" in obj
