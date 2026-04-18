"""M4 — Look-ahead safety: bar_ctx.ts_ns < sim_clock assertion at callback emission.

Covers:
  - AC15 T-B7: signal=1h bar's close unreadable until sim clock >= 13:00.
    bar_ctx.ts_ns < sim_clock asserted at callback emission time (not at
    construction — eager materialization places all bars in memory from start).

All tests MUST FAIL today — BarProcessor look-ahead guard does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestAC15LookAheadSafety:
    """AC15 T-B7: callback emission asserts bar_ctx.ts_ns < sim_clock."""

    def test_bar_ctx_ts_less_than_sim_clock_at_emission(self):
        """T-B7: on_signal never fires for a bar whose ts_ns >= current sim clock."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        emissions: list[tuple[int, int]] = []  # (bar_ts, sim_clock_at_emission)

        class Strat:
            strategy_id = "s_la"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(60),
            }
            def on_signal(self, bar_ctx, sim_clock_ns):
                emissions.append((bar_ctx.ts_ns, sim_clock_ns))

        bp = BarProcessor()
        strat = Strat()
        bp.register_strategy(strat)

        # Tick the sim clock through a handful of hourly bars.
        start = 1_770_000_000 * 1_000_000_000
        hr = 3600 * 1_000_000_000
        for i in range(1, 6):
            bp.tick(sim_clock_ns=start + i * hr)

        for bar_ts, sim_clock in emissions:
            assert bar_ts < sim_clock, (
                f"Look-ahead violation: bar_ts={bar_ts} >= sim_clock={sim_clock}"
            )

    def test_signal_close_unreadable_before_bar_close(self):
        """T-B7: signal=1h bar 12:00 close is NOT visible at sim clock 12:30."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        emitted_bar_indices: list[int] = []

        class Strat:
            strategy_id = "s_la2"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            }
            def on_signal(self, bar_ctx, sim_clock_ns):
                # Store the bar index.
                emitted_bar_indices.append(bar_ctx.hourly_bar_index)

        bp = BarProcessor()
        bp.register_strategy(Strat())

        start = 1_770_000_000 * 1_000_000_000
        hr = 3600 * 1_000_000_000
        # Advance sim clock to 12:30 — bar [12:00, 13:00) has NOT closed.
        bp.tick(sim_clock_ns=start + 12 * hr + 30 * 60 * 1_000_000_000)
        # The 12:00 bar must NOT have been emitted yet.
        assert 12 not in emitted_bar_indices

        # Advance to 13:00 — bar [12:00, 13:00) has now closed; emission expected.
        bp.tick(sim_clock_ns=start + 13 * hr)
        assert 12 in emitted_bar_indices

    def test_look_ahead_assert_raises_when_violated(self):
        """AC15: an engine bug attempting to emit a future bar must raise."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        bp = BarProcessor()

        # Directly attempt to emit a bar that lies in the future relative to
        # the sim clock — engine must assert (not silently swallow).
        start = 1_770_000_000 * 1_000_000_000
        with pytest.raises((AssertionError, ValueError, RuntimeError)):
            bp.emit_callback(
                callback_kind="on_signal",
                bar_ctx_ts_ns=start + 3600 * 1_000_000_000,  # future bar
                sim_clock_ns=start,
            )
