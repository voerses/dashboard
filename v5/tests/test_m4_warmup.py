"""M4 — Warm-up semantics (AC30).

on_signal blocked until warmup completes; Stage 3 blocked before first
on_signal; Stage 1 exits fire immediately once a position exists.
All tests MUST FAIL RED — v5.bar_processor does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v5.testing import TestClock  # noqa: E402


def _make_bar_ctx(ts_ns: int, hourly_bar_index: int, close: float = 100.0):
    from v5.bar_processor import BarContext
    from v5.bar_spec import BarSpec
    return BarContext(
        bar_spec=BarSpec.from_minutes(60), ts_ns=ts_ns,
        close=close, high=close + 0.5, low=close - 0.5, volume=1.0,
        hourly_bar_index=hourly_bar_index,
    )


def _strategy_needing_warmup(n_bars: int = 50):
    from v5.strategy_api import StrategySpec
    strategy = MagicMock(spec=StrategySpec)
    strategy.warmup_bars = n_bars
    return strategy


# ---------------------------------------------------------------------------
# AC30 — on_signal blocked during warmup
# ---------------------------------------------------------------------------


def _drive_bars(bp, n: int, positions=None, pending_entries=None):
    clock = TestClock("2026-01-01T00:00:00Z")
    for i in range(n):
        bp.process_bar(
            bar_ctx=_make_bar_ctx(clock.now_ns(), i),
            positions=positions or [], pending_entries=pending_entries or [],
        )
        clock.tick(3600)


class TestAC30WarmupBlocksOnSignal:
    """AC30: on_signal does not fire before indicator warmup completes."""

    def test_no_on_signal_during_warmup(self):
        """AC30: 49 warmup bars produce 0 on_signal invocations."""
        from v5.bar_processor import BarProcessor
        calls: list[int] = []
        strategy = _strategy_needing_warmup(50)
        strategy.on_signal = lambda *a, **kw: calls.append(1)
        _drive_bars(BarProcessor(strategies=[strategy]), 49)
        assert len(calls) == 0

    def test_on_signal_fires_exactly_once_at_warmup_boundary(self):
        """AC30: bar 49 (the 50th) fires on_signal exactly once."""
        from v5.bar_processor import BarProcessor
        calls: list[int] = []
        strategy = _strategy_needing_warmup(50)
        strategy.on_signal = lambda *a, **kw: calls.append(1)
        _drive_bars(BarProcessor(strategies=[strategy]), 50)
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# AC30 — Stage 3 blocked before first on_signal
# ---------------------------------------------------------------------------


class TestAC30Stage3BlockedDuringWarmup:
    """AC30: no Order can transition ARMED -> TRIGGERED before on_signal fires."""

    def test_no_pending_entries_armed_during_warmup(self):
        """AC30: Order list remains empty during warmup."""
        from v5.bar_processor import BarProcessor
        pending_entries: list = []
        _drive_bars(BarProcessor(strategies=[_strategy_needing_warmup(50)]),
                    49, pending_entries=pending_entries)
        assert len(pending_entries) == 0

    def test_stage3_no_triggered_transitions_during_warmup(self):
        """AC30: 0 ARMED->TRIGGERED transitions recorded in stats during warmup."""
        from v5.bar_processor import BarProcessor
        bp = BarProcessor(strategies=[_strategy_needing_warmup(50)], stats_enabled=True)
        _drive_bars(bp, 49)
        transitions = getattr(bp.stats, "pending_transitions", {})
        assert transitions.get(("ARMED", "TRIGGERED"), 0) == 0


# ---------------------------------------------------------------------------
# AC30 — Stage 1 fires immediately on existing positions (no warmup wait)
# ---------------------------------------------------------------------------


class TestAC30Stage1DuringWarmup:
    """AC30: Stage 1 (exits) fires immediately once a position exists."""

    def test_stop_loss_fires_mid_warmup(self):
        """AC30: bar 8 close=$94 triggers $95 stop — even though warmup=50."""
        from v5.bar_processor import BarProcessor
        from v5.position import Position

        pos = Position(
            position_id="BTC:s1:5:primary", token="BTC", strategy_id="s1",
            leg_ref_id="leg_primary", entry_bar=5, entry_price=100.0, direction=1,
            quantity=10.0, margin_usd=1_000.0, leverage=1.0, is_perp=True,
            fee_rate=0.0005, stop_mult=1.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=0, min_hold=0, max_hold=720,
            stop_price=95.0, highest=100.0, lowest=100.0, initial_risk=5.0,
        )
        bp = BarProcessor(strategies=[_strategy_needing_warmup(50)])
        exit_bars: list[int] = []
        bp.on_position_exit = lambda pos, bar_ctx, result: (
            exit_bars.append(bar_ctx.hourly_bar_index)
            if getattr(result, "should_close", False) else None
        )

        clock = TestClock("2026-01-01T00:00:00Z")
        for _ in range(6):
            clock.tick(3600)
        closes = {6: 99.0, 7: 98.0, 8: 94.0, 9: 94.0, 10: 94.0}
        for i in range(6, 11):
            bp.process_bar(
                bar_ctx=_make_bar_ctx(clock.now_ns(), i, close=closes[i]),
                positions=[pos], pending_entries=[],
            )
            clock.tick(3600)

        assert 8 in exit_bars, (
            f"AC30: stop-loss must fire at bar 8 during warmup, got {exit_bars}"
        )
        assert all(b <= 10 for b in exit_bars)
