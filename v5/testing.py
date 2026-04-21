"""V5 Testing Utilities — TestClock + parity tolerance constants (AC11, AC15, AC15a)."""
from __future__ import annotations

import random
from datetime import datetime, timezone
import numpy as np


# AC15 parity tolerance constants — per-field taxonomy from the brief.
# Float64 indicator outputs (EMA, ATR, MACD, etc. internal running state):
F64_ATOL: float = 1e-10
F64_RTOL: float = 1e-8
# Float32 downcast output fields (conviction, volume, vol_20, trail_schedule):
F32_ATOL: float = 1e-5
F32_RTOL: float = 1e-4

class TestClock:
    """Deterministic fixed-epoch clock. Used by soak tests (AC11), decision-level
    parity tests (AC15a), and any test needing reproducible tick timing."""

    # M10 AC #26: pytest collects classes starting with ``Test``; this helper
    # has ``__init__`` so collection would warn PytestCollectionWarning.
    __test__ = False

    def __init__(self, epoch_iso: str, seed: int = 42):
        """epoch_iso: ISO 8601 UTC timestamp (e.g. '2026-03-01T00:00:00Z'). seed: RNG seed."""
        self._current_dt = datetime.fromisoformat(epoch_iso.replace("Z", "+00:00"))
        if self._current_dt.tzinfo is None:
            self._current_dt = self._current_dt.replace(tzinfo=timezone.utc)
        self._rng = np.random.default_rng(seed)
        # Also seed stdlib random for deterministic shuffle et al.
        random.seed(seed)

    def now_ns(self) -> int:
        """Return current clock as epoch nanoseconds (UTC)."""
        return int(self._current_dt.timestamp() * 1_000_000_000)

    def now_dt(self) -> datetime:
        return self._current_dt

    def tick(self, bar_duration_sec: int) -> None:
        """Advance clock by exact duration (no drift, no wall clock)."""
        from datetime import timedelta
        self._current_dt = self._current_dt + timedelta(seconds=bar_duration_sec)

    @property
    def rng(self) -> np.random.Generator:
        """Seeded numpy RNG."""
        return self._rng
