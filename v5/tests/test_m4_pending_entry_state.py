"""M4 — PendingEntry state machine: 8-state enum, transitions, TIF, contention priority.

Covers:
  - AC (state machine section) T-B20: 8-state PendingState enum + transitions
    including PARTIALLY_FILLED.
  - T-B22: TIF expiry — ARMED -> EXPIRED when expires_at elapses without trigger.
  - AC39 T-B31: multi-PendingEntry contention priority
    (armed_at ASC, strategy_id ASC, token ASC): oldest-armed wins.

All tests MUST FAIL today — v5.pending_entry does not exist.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class TestTB20StateEnumMembers:
    """T-B20: PendingState enum has all 8 documented states."""

    def test_eight_states_present(self):
        from v5.pending_entry import PendingState
        expected = {
            "ARMED", "TRIGGERED", "RELEASED", "PARTIALLY_FILLED",
            "FILLED", "EXPIRED", "REJECTED", "CANCELLED",
        }
        actual = {m.name for m in PendingState}
        missing = expected - actual
        assert not missing, f"PendingState missing: {missing}"

    def test_trigger_kind_members(self):
        from v5.pending_entry import TriggerKind
        expected = {
            "PRICE_ABOVE", "PRICE_BELOW", "MARK_ABOVE", "MARK_BELOW",
            "TIME_AT", "BAR_CLOSE",
        }
        actual = {m.name for m in TriggerKind}
        missing = expected - actual
        assert not missing, f"TriggerKind missing: {missing}"


class TestTB20StateTransitions:
    """T-B20: documented transitions fire in sequence."""

    def test_armed_to_triggered_on_predicate_hit(self):
        """ARMED -> TRIGGERED when trigger predicate matches (no constraint check)."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx=object(),
        )
        assert pe.state == PendingState.ARMED
        pe2 = pe.on_price(price=101.0)
        assert pe2.state == PendingState.TRIGGERED

    def test_triggered_to_released_on_constraint_pass(self):
        """AC39: TRIGGERED -> RELEASED when capital constraint check passes."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None,
            sizing_ctx=object(),
        )
        pe = pe.on_price(price=101.0)
        assert pe.state == PendingState.TRIGGERED
        pe = pe.release(capital_ok=True)
        assert pe.state == PendingState.RELEASED

    def test_triggered_to_rejected_on_constraint_fail(self):
        """AC39: TRIGGERED -> REJECTED when constraint fails at RELEASE."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None,
            sizing_ctx=object(),
        )
        pe = pe.on_price(price=101.0)
        pe = pe.release(capital_ok=False)
        assert pe.state == PendingState.REJECTED
        assert pe.reject_reason == "risk_on_release"

    def test_released_to_filled(self):
        """Terminal fill path: RELEASED -> FILLED (single fill)."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None,
            sizing_ctx=object(),
        )
        pe = pe.on_price(price=101.0).release(capital_ok=True)
        pe = pe.on_fill(filled_qty=1.0, leaves_qty=0.0)
        assert pe.state == PendingState.FILLED

    def test_partially_filled_state(self):
        """T-B20: RELEASED -> PARTIALLY_FILLED when filled_qty < qty, leaves_qty > 0."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None,
            sizing_ctx=object(),
        )
        pe = pe.on_price(price=101.0).release(capital_ok=True)
        pe = pe.on_fill(filled_qty=0.4, leaves_qty=0.6)
        assert pe.state == PendingState.PARTIALLY_FILLED
        assert pe.filled_qty == 0.4
        assert pe.leaves_qty == 0.6

    def test_cancelled_state(self):
        """ARMED -> CANCELLED via explicit cancel (e.g. signal flip)."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None,
            sizing_ctx=object(),
        )
        pe = pe.cancel()
        assert pe.state == PendingState.CANCELLED


class TestTB22TIFExpiry:
    """T-B22: ARMED -> EXPIRED when expires_at elapses without trigger."""

    def test_expires_after_tif_elapsed(self):
        """AC25 step 6: TIF expiry must be emission-ordered against bar events
        — when sim_clock advances to T+1ns past expires_at=T, on_expired fires
        BEFORE the next Stage 1 dispatch (observed via emission log)."""
        from v5.bar_processor import BarProcessor  # forward reference; M4 target
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind

        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx=object(),
        )
        # Baseline: clock advanced past expires_at without predicate hit
        # transitions ARMED -> EXPIRED.
        pe2 = pe.check_expiry(now=_dt("2026-04-01T04:00:01"))
        assert pe2.state == PendingState.EXPIRED

        # Emission-ordering assertion (AC25 step 6): at sim_clock T+1ns past
        # expires_at, the BarProcessor must fire on_expired BEFORE the next
        # Stage 1 dispatch.
        expires_at_ns = int(
            _dt("2026-04-01T04:00:00").timestamp() * 1_000_000_000
        )
        emission_log: list[str] = []
        bp = BarProcessor(
            event_recorder=lambda evt, ts_ns: emission_log.append(evt),
        )
        bp.register_pending_entry(pe)
        bp.advance_sim_clock(to_ns=expires_at_ns + 1)
        # on_expired must be logged before any stage_1 dispatch.
        if "on_expired" in emission_log and "stage_1" in emission_log:
            idx_expired = emission_log.index("on_expired")
            idx_stage_1 = emission_log.index("stage_1")
            assert idx_expired < idx_stage_1, (
                f"on_expired must fire before Stage 1 dispatch; log={emission_log}"
            )
        else:
            assert "on_expired" in emission_log, (
                f"on_expired callback missing from emission log: {emission_log}"
            )

    def test_no_expiry_before_tif(self):
        """Pre-TIF tick does not expire."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx=object(),
        )
        pe2 = pe.check_expiry(now=_dt("2026-04-01T03:59:59"))
        assert pe2.state == PendingState.ARMED

    def test_triggered_before_tif_does_not_expire(self):
        """Already-triggered PendingEntry is not subject to TIF expiry."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=_dt("2026-04-01T04:00:00"),
            sizing_ctx=object(),
        )
        pe = pe.on_price(price=101.0)
        assert pe.state == PendingState.TRIGGERED
        pe2 = pe.check_expiry(now=_dt("2026-04-01T05:00:00"))
        assert pe2.state == PendingState.TRIGGERED


class TestTB31MultiPendingContention:
    """AC39 T-B31: oldest-armed wins capital contention; lexicographic tiebreak."""

    def test_oldest_armed_first(self):
        """AC39 T-B31: Three PEs with margin_usd=3000 each; available capital
        5000 USD fits only 1. Earliest armed_at reaches RELEASED -> FILLED;
        the other two go TRIGGERED -> REJECTED at RELEASE-time constraint
        check (capital-vs-sizing in USD, not integer count)."""
        from v5.pending_entry import (
            PendingEntry, PendingState, TriggerKind,
            resolve_contention,
        )
        sizing = {"margin_usd": 3000.0}
        a = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx=dict(sizing),
        ).on_price(101.0)
        b = PendingEntry.arm(
            strategy_id="s1", token="ETH", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:01:00"),
            expires_at=None, sizing_ctx=dict(sizing),
        ).on_price(101.0)
        c = PendingEntry.arm(
            strategy_id="s1", token="SOL", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:02:00"),
            expires_at=None, sizing_ctx=dict(sizing),
        ).on_price(101.0)

        # $5000 USD — only one $3000-margin entry fits.
        results = resolve_contention([c, a, b], available_capital_usd=5000.0)
        by_token = {r.token: r.state for r in results}
        assert by_token["BTC"] == PendingState.FILLED
        assert by_token["ETH"] == PendingState.REJECTED
        assert by_token["SOL"] == PendingState.REJECTED

    def test_tiebreak_strategy_id_asc(self):
        """Same armed_at — earlier strategy_id lexicographic wins."""
        from v5.pending_entry import (
            PendingEntry, PendingState, TriggerKind,
            resolve_contention,
        )
        t = _dt("2026-04-01T00:00:00")
        a = PendingEntry.arm(
            strategy_id="s100_a", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last", armed_at=t,
            expires_at=None, sizing_ctx=object(),
        ).on_price(101.0)
        b = PendingEntry.arm(
            strategy_id="s200_b", token="BTC", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last", armed_at=t,
            expires_at=None, sizing_ctx=object(),
        ).on_price(101.0)
        results = resolve_contention([b, a], available_capital=1)
        by_strat = {r.strategy_id: r.state for r in results}
        assert by_strat["s100_a"] == PendingState.FILLED
        assert by_strat["s200_b"] == PendingState.REJECTED

    def test_tiebreak_token_asc(self):
        """Same (armed_at, strategy_id) — earlier token lexicographic wins."""
        from v5.pending_entry import (
            PendingEntry, PendingState, TriggerKind,
            resolve_contention,
        )
        t = _dt("2026-04-01T00:00:00")
        a = PendingEntry.arm(
            strategy_id="s1", token="AAA", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last", armed_at=t,
            expires_at=None, sizing_ctx=object(),
        ).on_price(101.0)
        b = PendingEntry.arm(
            strategy_id="s1", token="BBB", direction=1,
            trigger=TriggerKind.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last", armed_at=t,
            expires_at=None, sizing_ctx=object(),
        ).on_price(101.0)
        results = resolve_contention([b, a], available_capital=1)
        by_token = {r.token: r.state for r in results}
        assert by_token["AAA"] == PendingState.FILLED
        assert by_token["BBB"] == PendingState.REJECTED
