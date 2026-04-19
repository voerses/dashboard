"""M6 — MessageBus (AC-D19 FIFO determinism).

Pub/sub with insertion-order deterministic dispatch. Hot-path storage uses
ordered dict (Python 3.7+); no set/frozenset iteration in publish/subscribe/
unsubscribe per AC-D19.

FIX note: `SubscriptionHandle._id: int` is bus-internal; `MDReqID(262)` string
mapping is a future concern at the venue-gateway boundary (not this module).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict

from v5.data.streams import DataStream

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubscriptionHandle:
    """Bus-internal handle (INT) — NOT a FIX MDReqID(262).

    FIX mapping note: MDReqID is a string that survives venue round-trip
    (MarketDataRequestReject echo, cancel-by-ID). `_id: int` is intentionally
    bus-internal only — the venue-facing identifier (when/if surfaced to a
    real FIX gateway in M11+) will be a distinct string derived from this
    handle at the session boundary, not the handle itself.
    """

    _id: int


class MessageBus:
    """Pub/sub with FIFO deterministic dispatch (AC-D19).

    Internal storage: dict[DataStream, dict[SubscriptionHandle, handler]].
    Python 3.7+ dict preserves insertion order; iterations are therefore
    FIFO. No set/frozenset iteration on hot paths.

    Per-handler exceptions are caught and logged; dispatch continues to
    remaining handlers to preserve determinism.
    """

    def __init__(self):
        self._subs: Dict[DataStream, Dict[SubscriptionHandle, Callable[[Any], None]]] = {}
        self._handle_to_topic: Dict[SubscriptionHandle, DataStream] = {}
        self._next_id: int = 0

    def subscribe(
        self,
        topic: DataStream,
        handler: Callable[[Any], None],
    ) -> SubscriptionHandle:
        """Register handler for topic. Returns opaque handle for unsubscribe."""
        handle = SubscriptionHandle(_id=self._next_id)
        self._next_id += 1
        if topic not in self._subs:
            self._subs[topic] = {}
        self._subs[topic][handle] = handler
        self._handle_to_topic[handle] = topic
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        """Remove handler. Safe on unknown handle (no-op)."""
        topic = self._handle_to_topic.pop(handle, None)
        if topic is None:
            return
        topic_subs = self._subs.get(topic)
        if topic_subs is not None:
            topic_subs.pop(handle, None)
            if not topic_subs:
                del self._subs[topic]

    def publish(self, topic: DataStream, event: Any) -> None:
        """Dispatch to every handler subscribed to topic, in registration order.

        Snapshot the handler list before iterating so unsubscribe-in-handler is
        re-entrancy safe. Per-handler exceptions are caught + logged; they do
        NOT interrupt dispatch to remaining handlers.
        """
        topic_subs = self._subs.get(topic)
        if not topic_subs:
            return
        for handle, handler in list(topic_subs.items()):
            try:
                handler(event)
            except Exception as e:
                _log.error(
                    "MessageBus handler raised: handle_id=%d topic=%r err=%r",
                    handle._id, topic, e,
                )
