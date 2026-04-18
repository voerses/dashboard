"""M4 — Intra-bar fill realism: MTF gap + touch cases, single-res fallback, mark trigger.

Covers:
  - AC16 T-B8: MTF fill rules with both gap and touch cases for long/short stops
    and long/short take-profits and limit entries.
  - AC17 T-B9: single-resolution fallback — exit_price = override if not None else
    close_val (pinned to v5/simulator.py:1049 semantics).
  - AC38 T-B30: working_price_source="mark" in backtest evaluates against
    bar_ctx.close (same as "last") with audit log entry per position.

All tests MUST FAIL today — v5.bar_processor / intra-bar fill API does not
exist yet.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestAC16MTFStopLossLong:
    """AC16 T-B8: long stop-loss — gap-down fills at fine_bar.open; touch at stop."""

    def test_gap_down_fills_at_open(self):
        """Fine bar opens BELOW stop_price — long stop fills at open, not stop."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 95.0, "high": 98.0, "low": 93.0, "close": 97.0}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="long", kind="stop_loss",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 95.0, f"Expected gap-down fill at open (95.0); got {fill}"

    def test_touch_fills_at_stop(self):
        """Fine bar low dips to stop but does not gap — fill at stop_price exactly."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 101.0, "high": 102.0, "low": 99.0, "close": 100.5}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="long", kind="stop_loss",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 100.0, f"Expected touch fill at stop (100.0); got {fill}"


class TestAC16MTFStopLossShort:
    """AC16 T-B8: short stop-loss — gap-up fills at open; touch at stop."""

    def test_gap_up_fills_at_open(self):
        """Short stop_price=100, fine bar opens at 105 (gap-up) — fill at 105."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 105.0, "high": 107.0, "low": 102.0, "close": 104.0}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="short", kind="stop_loss",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 105.0

    def test_touch_fills_at_stop(self):
        """Short stop_price=100, fine high reaches 101 — fill at stop (100)."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 98.0, "high": 101.0, "low": 97.0, "close": 99.5}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="short", kind="stop_loss",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 100.0


class TestAC16MTFTakeProfitLong:
    """AC16 T-B8: long take-profit — favorable gap fills at open; touch at tp."""

    def test_favorable_gap_fills_at_open(self):
        """Long tp=100, fine opens at 105 (favorable gap) — fill at 105."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 105.0, "high": 107.0, "low": 102.0, "close": 104.0}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="long", kind="take_profit",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 105.0

    def test_touch_fills_at_tp(self):
        """Long tp=100, fine high reaches 101 — fill at tp (100)."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 99.0, "high": 101.0, "low": 98.5, "close": 99.8}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="long", kind="take_profit",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 100.0


class TestAC16MTFTakeProfitShort:
    """AC16 T-B8: short take-profit mirror of long."""

    def test_favorable_gap_fills_at_open(self):
        """Short tp=100, fine opens at 95 (favorable gap) — fill at 95."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 95.0, "high": 98.0, "low": 93.0, "close": 96.0}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="short", kind="take_profit",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 95.0

    def test_touch_fills_at_tp(self):
        """Short tp=100, fine low reaches 99 — fill at tp (100)."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 101.0, "high": 102.0, "low": 99.0, "close": 100.5}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="short", kind="take_profit",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 100.0


class TestAC16MTFLimitEntry:
    """AC16 T-B8: limit entry fills if low <= limit_price <= high; else no fill."""

    def test_limit_in_range_fills(self):
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 101.0, "high": 103.0, "low": 99.0, "close": 102.0}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="long", kind="limit_entry",
            trigger_price=100.0, spec=BarSpec.from_minutes(1),
        )
        assert fill == 100.0

    def test_limit_out_of_range_no_fill(self):
        """Limit price above the fine bar's range — no fill."""
        from v5.bar_processor import compute_intra_bar_fill
        from v5.bar_spec import BarSpec

        fine_bar = {"open": 101.0, "high": 103.0, "low": 99.5, "close": 102.0}
        fill = compute_intra_bar_fill(
            bar=fine_bar, side="long", kind="limit_entry",
            trigger_price=95.0, spec=BarSpec.from_minutes(1),
        )
        assert fill is None


class TestAC17SingleResolutionFallback:
    """AC17 T-B9: exit_price = override if not None else close (simulator.py:1049)."""

    def test_override_takes_precedence(self):
        """Handler-provided override (stop_price) is used."""
        from v5.bar_processor import compute_single_resolution_fill

        exit_price = compute_single_resolution_fill(
            close=105.0, exit_price_override=100.0,
        )
        assert exit_price == 100.0

    def test_no_override_uses_close(self):
        """No override — fall back to bar close."""
        from v5.bar_processor import compute_single_resolution_fill

        exit_price = compute_single_resolution_fill(
            close=105.0, exit_price_override=None,
        )
        assert exit_price == 105.0


class TestAC38MarkTriggerBacktestFallback:
    """AC38 T-B30: mark trigger in backtest falls back to close + audit log."""

    def test_mark_predicate_evaluates_against_close(self):
        """working_price_source='mark' in backtest evaluates vs bar_ctx.close."""
        from v5.pending_entry import PendingEntry, PendingState, TriggerKind
        from datetime import datetime, timezone

        pe = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.MARK_ABOVE, trigger_price=100.0,
            working_price_source="mark",
            armed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            expires_at=None, sizing_ctx={},
        )
        # In backtest, mark == close per AC38.
        pe2 = pe.on_backtest_bar(close=101.0)
        assert pe2.state == PendingState.TRIGGERED

    def test_audit_log_entry_emitted_in_backtest(self, caplog):
        """AC38: each backtest evaluation against a mark trigger emits exactly
        one audit log entry per position (keyed by position/pending_id)."""
        from v5.pending_entry import PendingEntry, TriggerKind
        from datetime import datetime, timezone

        pe_btc = PendingEntry.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerKind.MARK_ABOVE, trigger_price=100.0,
            working_price_source="mark",
            armed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            expires_at=None, sizing_ctx={},
        )
        pe_eth = PendingEntry.arm(
            strategy_id="s1", token="ETH", direction=1,
            trigger=TriggerKind.MARK_ABOVE, trigger_price=100.0,
            working_price_source="mark",
            armed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            expires_at=None, sizing_ctx={},
        )
        caplog.set_level(logging.INFO)
        pe_btc.on_backtest_bar(close=101.0)
        pe_eth.on_backtest_bar(close=101.0)
        audit_msgs = [r.getMessage().lower() for r in caplog.records]
        # At minimum, the fallback reason must be named.
        mark_msgs = [
            m for m in audit_msgs
            if "mark" in m and ("fallback" in m or "close" in m)
        ]
        assert mark_msgs, (
            f"Expected mark-fallback audit log entry; got {audit_msgs}"
        )
        # AC38: exactly one log entry PER position (position_id / token-keyed).
        counts_by_position: dict[str, int] = {}
        for m in mark_msgs:
            for tok in ("btc", "eth"):
                if tok in m:
                    counts_by_position[tok] = counts_by_position.get(tok, 0) + 1
        assert counts_by_position.get("btc", 0) == 1, (
            f"BTC position expected exactly 1 audit entry, got {counts_by_position}"
        )
        assert counts_by_position.get("eth", 0) == 1, (
            f"ETH position expected exactly 1 audit entry, got {counts_by_position}"
        )
