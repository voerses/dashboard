"""M4 — BarSpec + DataResampler + canonical resolution set (AC12/AC22/AC28).

BarSpec is the immutable, hashable descriptor for a resampling resolution.
Interned at module load for canonical minutes so identity equality holds
(required for registry-key stability in RollingCache + default normalization).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


_CANONICAL_MINUTES = frozenset({1, 3, 5, 10, 15, 30, 60, 120, 240, 360, 480, 720, 1440})


@dataclass(frozen=True, slots=True)
class BarSpec:
    resolution_minutes: int
    label_side: Literal["left", "right"] = "left"
    closed_side: Literal["left", "right"] = "left"
    anchor_utc: str = "00:00"

    @classmethod
    def from_minutes(cls, n: int) -> "BarSpec":
        """Construct a canonical BarSpec (interned). Raises on non-canonical n."""
        if n not in _CANONICAL_MINUTES:
            raise ValueError(
                f"Non-canonical resolution: {n} minutes. "
                f"Allowed: {sorted(_CANONICAL_MINUTES)}"
            )
        return _interned(n, "left", "left", "00:00")

    @property
    def period_ns(self) -> int:
        return self.resolution_minutes * 60 * 1_000_000_000

    @property
    def label(self) -> str:
        """Sidecar/log label — matches pre-M4 RollingCache key format."""
        m = self.resolution_minutes
        if m == 1440:
            return "1d"
        if m % 60 == 0:
            return f"{m // 60}h"
        return f"{m}m"

    def __hash__(self) -> int:
        return hash((self.resolution_minutes, self.label_side, self.closed_side, self.anchor_utc))


_INTERN_CACHE: dict[tuple[int, str, str, str], BarSpec] = {}


def _interned(n: int, label_side: str, closed_side: str, anchor_utc: str) -> BarSpec:
    key = (n, label_side, closed_side, anchor_utc)
    if key not in _INTERN_CACHE:
        _INTERN_CACHE[key] = BarSpec(
            resolution_minutes=n,
            label_side=label_side,  # type: ignore[arg-type]
            closed_side=closed_side,  # type: ignore[arg-type]
            anchor_utc=anchor_utc,
        )
    return _INTERN_CACHE[key]
