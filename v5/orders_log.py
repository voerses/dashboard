"""V5 M5 — OrdersLog: JSONL audit log writer for Order lifecycle events.

Implements T-M5-15 / F11:

  * **Rename.** The audit log file is named ``orders_log.jsonl`` (renamed
    from the pre-M5 ``pending_entries_log.jsonl``).
  * **New M5 events.** Introduces four new event types that the pre-M5
    ``armed_log.jsonl`` readers cannot interpret:

      - ``leg_filled``           — an :class:`~v5.orders.Order` leg fills.
      - ``leg_rejected``         — an Order leg is rejected by the venue.
      - ``sibling_unwound``      — a one-cancels-other / bracket sibling
                                   leg is unwound after its peer fills.
      - ``bracket_activated``    — a bracket Order's exit legs activate
                                   after the parent entry fills.

  * **Dual-write scope.** Legacy events (``armed`` / ``arm`` / ``trigger``
    / ``fire`` / ``expire`` / ``cancel`` / ``expired`` / ``filled`` /
    ``skipped``) are dual-written to BOTH ``orders_log.jsonl`` (new,
    preferred) AND the legacy ``armed_log.jsonl`` (back-compat, dropped
    in M10). New M5 events are written to ``orders_log.jsonl`` ONLY —
    pre-M5 readers don't understand the schema and would crash or
    silently misinterpret entries.

The writer is thread-safe: callers that invoke :meth:`append` from
WebSocket + tick threads concurrently are serialized under a single
file lock. :meth:`flush` is a no-op for unbuffered writes (kept for
API symmetry with batching writers).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Mapping


# Legacy event names that must dual-write to armed_log.jsonl for back-compat
# with pre-M5 readers (dashboard, paper_utils expiry scanner). Dropped in M10.
_LEGACY_EVENTS: frozenset[str] = frozenset({
    "arm", "armed",
    "trigger",
    "fire", "filled",
    "expire", "expired",
    "cancel", "cancelled", "canceled",
    "skipped",
})

# M5-native event names — orders_log.jsonl ONLY (never dual-written).
_NEW_M5_EVENTS: frozenset[str] = frozenset({
    "leg_filled",
    "leg_rejected",
    "sibling_unwound",
    "bracket_activated",
})


class OrdersLog:
    """JSONL writer for Order lifecycle audit events.

    Writes each appended event as a single JSON object on its own line.
    Legacy event names (``armed``, ``fire``, etc.) are additionally
    mirrored to ``armed_log.jsonl`` in the same directory per F11; new
    M5 events (``leg_filled`` etc.) go to ``orders_log.jsonl`` only.

    Args:
        root: directory to write ``orders_log.jsonl`` (and optionally
            ``armed_log.jsonl``) into. Created if it does not exist.
    """

    __slots__ = ("_root", "_orders_path", "_armed_path", "_lock")

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._orders_path: Path = self._root / "orders_log.jsonl"
        self._armed_path: Path = self._root / "armed_log.jsonl"
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- #
    # Public API                                                       #
    # ---------------------------------------------------------------- #

    def append(self, event: Mapping[str, object]) -> None:
        """Append one event dict to the log(s).

        The ``event`` dict must include an ``event`` key naming the event
        type; legacy names dual-write, new M5 names are orders_log-only.
        Writes are fsync-free (cheap append) but flushed — readers polling
        the file will see the new line on next read.
        """
        if not isinstance(event, Mapping):
            raise TypeError(
                f"OrdersLog.append expects a Mapping, got {type(event).__name__}"
            )
        name = str(event.get("event", "") or "")
        line = json.dumps(dict(event), default=str, separators=(",", ":")) + "\n"

        with self._lock:
            # Always write to orders_log.jsonl.
            try:
                with open(self._orders_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                # Observability should never crash the engine.
                pass

            # Dual-write legacy events to armed_log.jsonl for back-compat.
            # New M5 events are orders_log-only per F11.
            if name in _LEGACY_EVENTS and name not in _NEW_M5_EVENTS:
                try:
                    with open(self._armed_path, "a", encoding="utf-8") as f:
                        f.write(line)
                except OSError:
                    pass

    def flush(self) -> None:
        """Flush any buffered writes. No-op — appends are unbuffered."""
        # File objects in :meth:`append` are opened/closed per-write,
        # so there is nothing to flush here. Retained for API symmetry
        # with batching writers that might replace this later.
        return

    # ---------------------------------------------------------------- #
    # Introspection                                                    #
    # ---------------------------------------------------------------- #

    @property
    def orders_log_path(self) -> Path:
        """Absolute path to ``orders_log.jsonl``."""
        return self._orders_path

    @property
    def armed_log_path(self) -> Path:
        """Absolute path to the legacy ``armed_log.jsonl`` (dual-write target)."""
        return self._armed_path


__all__ = ["OrdersLog"]
