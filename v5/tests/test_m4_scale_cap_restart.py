"""M4 — Per-hourly-bar scale cap survives crash-restart (AC26, T-B16).

Mid-hour crash+restart must NOT produce a second scale in the same hour.
Fresh hour -> cap resets -> scale CAN fire. Backed by `_scale_action_bar`
serialization via paper_state.
All tests MUST FAIL RED — v5.bar_processor / paper_state schema bump absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v5.position import Position  # noqa: E402
from v5.testing import TestClock  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scaled_position_in_hour_10() -> Position:
    """Long position that already fired a scale in hourly bar 10."""
    pos = Position(
        position_id="BTC:s1:5:primary", token="BTC", strategy_id="s1",
        leg="primary", entry_bar=5, entry_price=100.0, direction=1,
        quantity=20.0, margin_usd=2_000.0, leverage=1.0, is_perp=True,
        fee_rate=0.0005, stop_mult=1.0, trail_mult=3.0, target_mult=5.0,
        no_stop_bars=0, min_hold=0, max_hold=720,
        stop_price=95.0, highest=105.0, lowest=95.0, initial_risk=5.0,
        r_anchor_price=100.0,
    )
    pos._scale_action_bar = 10
    pos.scale_count = 1
    return pos


def _make_bar_ctx(ts_ns: int, hourly_bar_index: int, close: float = 103.0):
    from v5.bar_processor import BarContext
    from v5.bar_spec import BarSpec
    return BarContext(
        bar_spec=BarSpec.from_minutes(1), ts_ns=ts_ns,
        close=close, high=close + 0.25, low=close - 0.25, volume=1.0,
        hourly_bar_index=hourly_bar_index,
    )


def _always_scale_strategy():
    from v5.strategy_api import StrategySpec
    strategy = MagicMock(spec=StrategySpec)
    strategy.warmup_bars = 0
    strategy.check_scale = lambda pos, bar_ctx, **kw: MagicMock(
        should_scale=True, qty=5.0, fill_price=103.0,
    )
    return strategy


# ---------------------------------------------------------------------------
# AC26 — scale cap persists across restart
# ---------------------------------------------------------------------------


class TestAC26ScaleCapCrashRestart:
    """AC26 T-B16: mid-hour restart does not produce a second scale in the same hour."""

    def test_scale_action_bar_serializes_to_paper_state(self, tmp_path: Path):
        """AC26: `_scale_action_bar` round-trips through paper_state.json."""
        from v5.paper_state import PaperState

        pos = _scaled_position_in_hour_10()
        state_file = tmp_path / "paper_state.json"

        state = PaperState(positions=[pos])
        state.save(state_file)

        reloaded = PaperState.load(state_file)
        assert len(reloaded.positions) == 1
        restored = reloaded.positions[0]
        assert restored._scale_action_bar == 10, (
            "AC26: `_scale_action_bar` must round-trip through paper_state"
        )
        assert restored.scale_count == 1

    def test_no_second_scale_in_same_hour_after_restart(self, tmp_path: Path):
        """T-B16 core: restart mid-hour -> no second scale fires in hour 10."""
        from v5.bar_processor import BarProcessor
        from v5.paper_state import PaperState

        state_file = tmp_path / "paper_state.json"
        PaperState(positions=[_scaled_position_in_hour_10()]).save(state_file)
        positions = PaperState.load(state_file).positions

        bp = BarProcessor(strategies=[_always_scale_strategy()])
        clock = TestClock("2026-01-01T10:30:00Z")
        for _ in range(30):  # minutes 30..59 of hour 10
            bp.process_bar(
                bar_ctx=_make_bar_ctx(clock.now_ns(), 10),
                positions=positions, pending_entries=[],
            )
            clock.tick(60)

        restored = positions[0]
        increase_events = [
            e for e in getattr(restored, "scaling_events", [])
            if getattr(e, "kind", "") == "increase"
        ]
        assert len(increase_events) == 0, (
            f"AC26 T-B16: second scale fired ({len(increase_events)} events)"
        )
        assert restored._scale_action_bar == 10

    def test_fresh_scale_in_hour_11_after_restart(self, tmp_path: Path):
        """T-B16: crossing into hour 11 resets the cap; scale fires again."""
        from v5.bar_processor import BarProcessor
        from v5.paper_state import PaperState

        state_file = tmp_path / "paper_state.json"
        PaperState(positions=[_scaled_position_in_hour_10()]).save(state_file)
        positions = PaperState.load(state_file).positions

        scale_events: list[int] = []
        bp = BarProcessor(strategies=[_always_scale_strategy()])
        bp.on_scale_action = lambda pos, bar_ctx, result: (
            scale_events.append(bar_ctx.hourly_bar_index)
            if getattr(result, "scale_action_taken", False) else None
        )

        clock = TestClock("2026-01-01T10:45:00Z")
        for _ in range(15):
            bp.process_bar(
                bar_ctx=_make_bar_ctx(clock.now_ns(), 10),
                positions=positions, pending_entries=[],
            )
            clock.tick(60)
        assert 10 not in scale_events

        bp.process_bar(
            bar_ctx=_make_bar_ctx(clock.now_ns(), 11),
            positions=positions, pending_entries=[],
        )
        assert 11 in scale_events, "AC26: cap must reset on new hour"
