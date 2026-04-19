"""M6 — Clock Protocol + LiveClock (AC-D6, AC-D20).

Infrastructure wall-clock reads:
- time.time_ns() — used by LiveClock.now_ns() / now() for wall-clock reads
  during paper/live trading. Backtest uses TestClock (deterministic, no wall
  reads) via v5.testing.TestClock which satisfies this Protocol structurally.

AC-D6 prohibition applies to STRATEGY code only — direct time.time() /
datetime.now() / time.monotonic() / time.perf_counter() in strategy source
raises at strategy __init__ (enforcement AST scan deferred to M7 per design
§4.3). This module's wall-clock read is infrastructure, not strategy.
"""
from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Time source Protocol.

    now_ns() returns current time as epoch nanoseconds. Matches the shipped
    v5/testing.py::TestClock.now_ns() signature so TestClock satisfies this
    Protocol structurally (runtime_checkable lets isinstance() work for tests).
    """

    def now_ns(self) -> int: ...


class LiveClock:
    """Wall clock for paper/live trading.

    Exposes both .now_ns() (Clock Protocol conformance) and .now() (legacy
    alias returning epoch nanoseconds).
    """

    def now_ns(self) -> int:
        """Return current wall-clock time as epoch nanoseconds."""
        return time.time_ns()

    def now(self) -> int:
        """Legacy alias for now_ns(). Returns epoch nanoseconds."""
        return time.time_ns()
