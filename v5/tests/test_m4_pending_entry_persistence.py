"""M4 — PendingEntry persistence + consolidator rehydration.

Covers:
  - AC32 T-B21: PendingEntry full state roundtrips through paper_state.json
    across crash-restart (state, expires_at, sizing_ctx, trigger,
    working_price_source, filled_qty, leaves_qty).
  - AC27 T-B17: Paper consolidator state rehydrates from tick sidecar
    (v5_state/ticks.log) deterministically; tick retention cap =
    2 * max(exit_bar_period_ns, 1h); mid-replay crash is idempotent.

All tests MUST FAIL today — v5.pending_entry and v5.paper_state (M4 schema)
do not exist yet.
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


class TestTB21PendingEntryRoundtrip:
    """AC32 T-B21: full PendingEntry state roundtrips through serialization."""

    def test_armed_state_roundtrip(self, tmp_path):
        """ARMED PendingEntry serializes + deserializes byte-identically."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind

        pe = PendingEntry.arm(
            strategy_id="s524", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.5,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx={"notional": 10_000.0, "risk_usd": 500.0},
        )
        blob = pe.to_json()
        restored = PendingEntry.from_json(blob)
        assert restored.state == PendingState.ARMED
        assert restored.strategy_id == "s524"
        assert restored.token == "BTC"
        assert restored.direction == 1
        assert restored.trigger == TriggerKind.PRICE_ABOVE
        assert restored.trigger_price == 100.5
        assert restored.working_price_source == "last"
        assert restored.armed_at == _dt("2026-04-01T00:00:00")
        assert restored.expires_at == _dt("2026-04-01T04:00:00")
        assert restored.filled_qty == 0.0
        assert restored.leaves_qty == 0.0

    def test_partially_filled_roundtrip(self):
        """PARTIALLY_FILLED preserves filled_qty and leaves_qty across restart."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind

        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None,
            sizing_ctx={"qty": 1.0},
        )
        pe = pe.on_price(101.0).release(capital_ok=True)
        pe = pe.on_fill(filled_qty=0.4, leaves_qty=0.6)

        blob = pe.to_json()
        restored = PendingEntry.from_json(blob)
        assert restored.state == PendingState.PARTIALLY_FILLED
        assert restored.filled_qty == 0.4
        assert restored.leaves_qty == 0.6

    def test_rejected_reason_roundtrip(self):
        """REJECTED preserves reject_reason across restart."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind

        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        pe = pe.on_price(101.0).release(capital_ok=False)
        assert pe.state == PendingState.REJECTED
        restored = PendingEntry.from_json(pe.to_json())
        assert restored.state == PendingState.REJECTED
        assert restored.reject_reason == "risk_on_release"

    def test_paper_state_file_contains_pending_entries(self, tmp_path):
        """AC32: paper_state.json carries PendingEntry list as top-level key
        AND all enumerated fields (state, expires_at, sizing_ctx, trigger,
        working_price_source, filled_qty, leaves_qty) roundtrip with known
        non-default values."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        from v5.paper_state import write_paper_state, read_paper_state

        # Build a PendingEntry with known non-default values on each field
        # and progress through RELEASED -> PARTIALLY_FILLED so filled_qty /
        # leaves_qty are both non-zero.
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.MARK_BELOW, trigger_price=100.0,
            working_price_source="mark",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx={"qty": 1.0, "notional": 10000.0, "margin_usd": 3000.0},
        )
        pe = pe.on_price(99.0).release(capital_ok=True)
        pe = pe.on_fill(filled_qty=0.35, leaves_qty=0.65)

        state_path = tmp_path / "paper_state.json"
        write_paper_state(state_path, pending_entries=[pe])
        restored = read_paper_state(state_path)
        assert len(restored.pending_entries) == 1
        pe_r = restored.pending_entries[0]
        assert pe_r.token == "BTC"
        # AC32 enumerated fields:
        assert pe_r.state == PendingState.PARTIALLY_FILLED
        assert pe_r.expires_at == _dt("2026-04-01T04:00:00")
        assert pe_r.sizing_ctx == {
            "qty": 1.0, "notional": 10000.0, "margin_usd": 3000.0,
        }
        assert pe_r.trigger == TriggerKind.MARK_BELOW
        assert pe_r.working_price_source == "mark"
        assert pe_r.filled_qty == 0.35
        assert pe_r.leaves_qty == 0.65


class TestTB17ConsolidatorRehydration:
    """AC27 T-B17: consolidator rehydrates from tick sidecar."""

    def test_rehydrate_from_tick_sidecar(self, tmp_path):
        """AC27: given a ticks.log sidecar, consolidator produces the same
        partial-bar state as the original session."""
        from v5.bar_spec import BarSpec
        from v5.streaming_consolidator import StreamingConsolidator

        sidecar = tmp_path / "ticks.log"
        # Write 5 synthetic ticks within a 1h bar window.
        base_ts = 1_770_000_000 * 1_000_000_000
        ticks = [
            {"ts_ns": base_ts + i * 60_000_000_000,
             "token": "BTC", "price": 100.0 + i, "volume": 10.0}
            for i in range(5)
        ]
        with sidecar.open("w") as f:
            for t in ticks:
                f.write(json.dumps(t) + "\n")

        spec = BarSpec.from_minutes(60)
        c1 = StreamingConsolidator(bar_spec=spec)
        for t in ticks:
            c1.on_tick(ts_ns=t["ts_ns"], price=t["price"], volume=t["volume"])
        snapshot_live = c1.snapshot()

        c2 = StreamingConsolidator(bar_spec=spec)
        c2.rehydrate_from_sidecar(sidecar)
        snapshot_rehydrated = c2.snapshot()
        assert snapshot_rehydrated == snapshot_live

    def test_tick_sidecar_retention_two_bar_periods(self, tmp_path):
        """AC27 retention: tick sidecar capped at 2 * max(exit_bar_period, 1h)."""
        from v5.bar_spec import BarSpec
        from v5.streaming_consolidator import StreamingConsolidator

        sidecar = tmp_path / "ticks.log"
        spec = BarSpec.from_minutes(60)
        c = StreamingConsolidator(bar_spec=spec, sidecar_path=sidecar)
        base_ts = 1_770_000_000 * 1_000_000_000
        # Push 4 hours of 1-minute ticks.
        for i in range(240):
            c.on_tick(
                ts_ns=base_ts + i * 60_000_000_000,
                price=100.0, volume=1.0,
            )
        # After multiple bar closes, the sidecar should retain AT MOST
        # 2 * max(period, 1h) of history.
        retained_lines = sidecar.read_text().strip().splitlines()
        assert len(retained_lines) <= 2 * 60, (
            f"Sidecar retention cap violated: {len(retained_lines)} lines"
        )

    def test_idempotent_replay(self, tmp_path):
        """AC27: crash-during-replay restarts cleanly — replaying twice yields
        same final state as replaying once."""
        from v5.bar_spec import BarSpec
        from v5.streaming_consolidator import StreamingConsolidator

        sidecar = tmp_path / "ticks.log"
        base_ts = 1_770_000_000 * 1_000_000_000
        ticks = [
            {"ts_ns": base_ts + i * 60_000_000_000,
             "token": "BTC", "price": 100.0 + i * 0.1, "volume": 1.0}
            for i in range(30)
        ]
        with sidecar.open("w") as f:
            for t in ticks:
                f.write(json.dumps(t) + "\n")

        spec = BarSpec.from_minutes(60)
        c1 = StreamingConsolidator(bar_spec=spec)
        c1.rehydrate_from_sidecar(sidecar)
        snap1 = c1.snapshot()
        # Replay second time — snapshot must not change.
        c1.rehydrate_from_sidecar(sidecar)
        snap2 = c1.snapshot()
        assert snap1 == snap2, "Replay is not idempotent"

    def test_partial_bar_forfeit_if_beyond_retention(self, tmp_path, caplog):
        """AC27: if paper was down longer than retention, partial-bar window is
        forfeited and an audit log entry is emitted. Exactly one message
        mentions 'forfeit', exactly one mentions 'retention', and zero
        messages mention 'partial' (this is the forfeit case, NOT a partial
        fill)."""
        from v5.bar_spec import BarSpec
        from v5.streaming_consolidator import StreamingConsolidator
        import logging

        sidecar = tmp_path / "ticks.log"
        sidecar.write_text("")  # empty — retention exhausted
        spec = BarSpec.from_minutes(60)
        caplog.set_level(logging.INFO)
        c = StreamingConsolidator(bar_spec=spec, sidecar_path=sidecar)
        c.rehydrate_from_sidecar(sidecar, now_ns=999 * 10**18)
        messages = [r.getMessage().lower() for r in caplog.records]
        forfeit_count = sum(1 for m in messages if "forfeit" in m)
        retention_count = sum(1 for m in messages if "retention" in m)
        partial_count = sum(1 for m in messages if "partial" in m)
        assert forfeit_count == 1, (
            f"Expected exactly 1 'forfeit' audit message; got {forfeit_count} "
            f"(messages={messages})"
        )
        assert retention_count == 1, (
            f"Expected exactly 1 'retention' audit message; got {retention_count} "
            f"(messages={messages})"
        )
        assert partial_count == 0, (
            f"Expected 0 'partial' audit messages (this is the forfeit path, "
            f"not a partial fill); got {partial_count} (messages={messages})"
        )
