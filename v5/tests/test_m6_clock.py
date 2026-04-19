"""M6 — Clock Protocol + LiveClock (T-D6, AC-D6).

Covers:
  - AC-D6 T-D6: `v5/clock.py` exposes `Clock` Protocol with `now()` returning
    epoch nanoseconds. `LiveClock` implements wall-clock reads. `TestClock`
    stays in `v5.testing` (M4-shipped, uses `now_ns()`).

All tests MUST FAIL today — `v5.clock` does not exist. Import errors are
valid RED states per Phase 3.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestClockProtocol:
    """T-D6 / AC-D6 — Clock Protocol shape."""

    def test_clock_protocol_importable(self):
        """Clock Protocol is importable from v5.clock."""
        from v5.clock import Clock  # noqa: F401

    def test_live_clock_importable(self):
        """LiveClock is importable from v5.clock."""
        from v5.clock import LiveClock  # noqa: F401

    def test_live_clock_now_returns_int_ns(self):
        """LiveClock.now() returns an int epoch-nanosecond value."""
        from v5.clock import LiveClock
        clk = LiveClock()
        n = clk.now()
        assert isinstance(n, int)
        # Nanosecond epoch for any date after 2000-01-01 is > 10^18
        assert n > 946_684_800_000_000_000

    def test_live_clock_monotonic_in_practice(self):
        """Successive LiveClock.now() calls are non-decreasing."""
        from v5.clock import LiveClock
        clk = LiveClock()
        a = clk.now()
        b = clk.now()
        assert b >= a


class TestClockBacktestIsolation:
    """T-D6 — backtest uses M4 TestClock; never advances past current bar close."""

    def test_test_clock_is_a_clock_protocol_instance(self):
        """M4's TestClock is a structural Clock — runtime-checkable via v5.clock.Clock."""
        from v5.clock import Clock
        from v5.testing import TestClock
        clk = TestClock(epoch_iso="2026-03-01T00:00:00Z", seed=0)
        assert isinstance(clk, Clock), "TestClock must satisfy v5.clock.Clock protocol"

    def test_universe_context_never_yields_future_bar(self):
        """AC-D6: UniverseContext.bars() never exposes bars with ts_event > clock.now()."""
        from v5.testing import TestClock
        # UniverseContext lives in v5.data — module doesn't exist yet.
        from v5.data.engine import UniverseContext
        clk = TestClock(epoch_iso="2026-03-01T00:00:00Z", seed=0)
        ctx = UniverseContext(clock=clk)
        # Any bar the context surfaces must have ts_event <= clk.now_ns()
        for b in ctx.latest_bars():
            assert b.ts_event <= clk.now_ns()
