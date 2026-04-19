"""M5 — Trigger-check cadence via M4 exit_resolution.

Covers:
  - T-M5-14: a strategy declaring bar_subscriptions={"exit": BarSpec.from_minutes(1)}
    gets its Order triggers checked at 1m cadence (every WS candle).
    A strategy declaring exit=1h gets hourly-only cadence. No hardcoded 1m
    default in M5 — cadence is driven by the strategy's exit_resolution
    subscription.

All tests MUST FAIL today — v5.orders + bar_processor trigger-check paths
do not exist.
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


class TestTM514TriggerCadenceHourly:
    """T-M5-14: hourly-only strategy triggers check at 1h cadence only."""

    def test_hourly_exit_resolution_checks_at_1h_only(self):
        """T-M5-14: strategy with exit_resolution=1h — triggers check only at
        hourly boundaries; sub-hour ticks do not invoke check_triggers."""
        from v5.orders import Order, TriggerType
        from v5.bar_spec import BarSpec
        from v5.bar_processor import BarProcessor

        check_count = {"n": 0}

        def record_check(*args, **kwargs):
            check_count["n"] += 1
            return None

        bp = BarProcessor(
            exit_resolution=BarSpec.from_minutes(60),
            trigger_check_callback=record_check,
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        bp.register_order(order)
        # Dispatch one hourly bar — trigger check fires once.
        bp.on_bar_event(
            ts_ns=int(_dt("2026-04-01T01:00:00").timestamp() * 1e9),
            resolution=BarSpec.from_minutes(60),
            bar_data={"close": 100.5},
        )
        # Dispatch 59 sub-hour (1m) bars — trigger check does NOT fire.
        for i in range(1, 60):
            bp.on_bar_event(
                ts_ns=int((_dt("2026-04-01T01:00:00")
                           + timedelta(minutes=i)).timestamp() * 1e9),
                resolution=BarSpec.from_minutes(1),
                bar_data={"close": 100.5},
            )
        assert check_count["n"] == 1, (
            f"Expected 1 trigger-check (at 1h boundary); got {check_count['n']}"
        )


class TestTM514TriggerCadenceMinute:
    """T-M5-14: 1m-exit strategy triggers at every 1m bar."""

    def test_minute_exit_resolution_checks_at_each_minute(self):
        """T-M5-14: strategy with exit_resolution=1m — every minute bar
        invokes check_triggers."""
        from v5.orders import Order, TriggerType
        from v5.bar_spec import BarSpec
        from v5.bar_processor import BarProcessor

        check_count = {"n": 0}

        def record_check(*args, **kwargs):
            check_count["n"] += 1

        bp = BarProcessor(
            exit_resolution=BarSpec.from_minutes(1),
            trigger_check_callback=record_check,
        )
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        bp.register_order(order)
        for i in range(0, 60):
            bp.on_bar_event(
                ts_ns=int((_dt("2026-04-01T01:00:00")
                           + timedelta(minutes=i)).timestamp() * 1e9),
                resolution=BarSpec.from_minutes(1),
                bar_data={"close": 100.5},
            )
        assert check_count["n"] == 60, (
            f"Expected 60 trigger-checks (1 per minute); got {check_count['n']}"
        )

    def test_no_hardcoded_1m_default(self):
        """T-M5-14: BarProcessor without explicit exit_resolution does NOT
        default to 1m cadence — this is a V5-native, spec-driven knob.

        Negative case: constructing BarProcessor WITHOUT passing
        exit_resolution must either raise ValueError (missing required arg)
        or, if a StrategySpec is supplied, use the strategy's declared
        exit_resolution. It must NOT silently default to 1m.
        """
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        # Case A: no exit_resolution + no strategy_spec → must raise.
        raised = False
        try:
            BarProcessor()
        except (ValueError, TypeError) as exc:
            raised = True
            msg = str(exc).lower()
            assert "exit_resolution" in msg or "missing" in msg, (
                f"Error message must reference missing exit_resolution; "
                f"got {exc!r}"
            )
        # Case B: if it did not raise, it must have read the declaration
        # from a strategy spec — NEVER default to 1m silently. Confirm by
        # passing a strategy with declared 1h exit and asserting the BP
        # adopts 1h.
        if not raised:
            spec = StrategySpec(
                strategy_id="s1",
                bar_subscriptions={"exit": BarSpec.from_minutes(60)},
            )
            bp = BarProcessor(strategy_spec=spec)
            assert bp.exit_resolution == BarSpec.from_minutes(60), (
                f"BarProcessor must adopt StrategySpec.bar_subscriptions"
                f"['exit']=1h, not a hardcoded 1m default; got "
                f"{bp.exit_resolution}"
            )
            assert bp.exit_resolution != BarSpec.from_minutes(1), (
                "BarProcessor silently defaulted to 1m — forbidden by M5 AC10"
            )
