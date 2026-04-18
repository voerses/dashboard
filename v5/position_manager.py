"""V5 — Module-level scaling history registry (M4 Task 26, AC26 T-B16).

Exposes a singleton :data:`position_manager` with ``record_scale`` /
``get_scaling_history`` helpers. The BarProcessor notifies this registry on
every Stage 2 scale fire; :func:`get_scaling_history` is the public
behavioral probe used by the AC26 crash-restart acceptance tests.

Design notes
------------

* The registry is intentionally lightweight — it stores one list per
  ``position_id`` plus a fallback bucket under the empty string for
  positions that have not yet been assigned an ID (common during the
  bar-processor dispatch tests, where ``Position(..., quantity=1.0)`` is
  constructed without ``position_id=`` and the default ``""`` is reused).
* History is recorded per-(``position_id``, ``hourly_bar_index``). The
  AC26 cap guarantees at most one scale event per ``(pos, hourly_bar)``
  pair. A rerun at the same ``hourly_bar_index`` (e.g. after a crash and
  reload) is a no-op on the recorded list so observers see a stable
  length.
* ``clear()`` is primarily for test isolation — production code paths
  do not rely on a long-lived registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScaleHistoryEntry:
    """A single observed Stage 2 scale fire (AC26)."""
    position_id: str
    hourly_bar_index: int
    action: Any  # strategy-returned action value — opaque to the registry


class _GlobalPositionManager:
    """Per-process scaling-history registry.

    Thread-safety: not required for the AC26 tests (single-thread sim),
    but the internal dict operations are atomic enough for the typical
    paper-engine hot path. If future callers need multi-thread semantics
    they can wrap ``record_scale`` under a lock external to this module.
    """

    __slots__ = ("_history",)

    def __init__(self) -> None:
        # position_id -> list[ScaleHistoryEntry]
        self._history: dict[str, list[ScaleHistoryEntry]] = {}

    # ------------------------------------------------------------------ #
    # Write path — called by BarProcessor Stage 2 dispatch               #
    # ------------------------------------------------------------------ #

    def record_scale(self, pos, bar_ctx, action) -> None:
        """Record a Stage 2 scale fire on ``pos`` at ``bar_ctx``.

        Idempotent by ``(position_id, hourly_bar_index)``: replaying the
        same hourly bar after a restart does NOT append a second entry
        (AC26 crash-restart semantics). The cap on ``pos._scale_action_bar``
        already prevents Stage 2 from invoking this path on the blocked
        bar, but we double-guard here so observers stay consistent even if
        a caller bypasses the cap (tests do not, but defensive code costs
        nothing).
        """
        pos_id = getattr(pos, "position_id", "") or ""
        hourly_bar = int(getattr(bar_ctx, "hourly_bar_index", -1) or -1)
        bucket = self._history.setdefault(pos_id, [])
        for existing in bucket:
            if existing.hourly_bar_index == hourly_bar:
                return  # AC26: same hour, same position — already recorded
        bucket.append(
            ScaleHistoryEntry(
                position_id=pos_id,
                hourly_bar_index=hourly_bar,
                action=action,
            )
        )

    # ------------------------------------------------------------------ #
    # Read path — AC26 public observability API                           #
    # ------------------------------------------------------------------ #

    def get_scaling_history(self, pos_id) -> list[ScaleHistoryEntry]:
        """Return the list of recorded Stage 2 scale fires for ``pos_id``.

        Returns a shallow copy so callers cannot mutate the registry's
        internal buffers. Unknown ``pos_id`` returns an empty list.
        """
        key = pos_id if pos_id is not None else ""
        return list(self._history.get(key, []))

    # ------------------------------------------------------------------ #
    # Test hygiene                                                        #
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """Reset the registry — intended for test isolation."""
        self._history.clear()


# Module-level singleton exposed to BarProcessor + acceptance tests.
position_manager = _GlobalPositionManager()


__all__ = ["position_manager", "ScaleHistoryEntry", "_GlobalPositionManager"]
