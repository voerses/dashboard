"""M6 — Gap detection with 3-mode GapPolicy (AC-D5).

Upstream of MultiInstrumentCache: guarantees monotonicity before cache write.

FIX mapping note (AC-D16 honesty): GapPolicy → approximate. FIX MDUpdateType(265)
has no direct gap-handling analog; Bar.ts_event → TransactTime(60) remains the
canonical per-bar timestamp. STRICT/NAN_FILL/SKIP are engine semantics, not FIX
semantics (per brief FIX vocabulary block line 143).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from v5.data.bus import MessageBus
from v5.data.exceptions import DataGapError
from v5.data.streams import DataStream, GapPolicy

_log = logging.getLogger(__name__)

# AC-D9 backpressure threshold: handler taking >100ms for a 1m bar logs a WARN.
_SLOW_HANDLER_THRESHOLD_NS = 100_000_000


@dataclass(frozen=True, slots=True)
class GapDetected:
    """Event published to MessageBus when a gap is observed (any policy)."""
    stream: DataStream
    expected_ts: int
    got_ts: int
    missing_count: int


@dataclass(slots=True)
class _NanBar:
    """NAN_FILL-emitted bar at a missing ts_event slot."""
    instrument_id: Any
    bar_spec: Any
    ts_event: int
    ts_init: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class GapDetector:
    """Per-stream gap detector dispatching by GapPolicy.

    - STRICT: raise DataGapError after N REST backfill retries exhausted.
    - NAN_FILL: emit NaN-OHLCV bars at missing ts_event slots.
    - SKIP: silently drop gap (WARN log).

    Monotonicity invariant (policy-independent): bars with ts_event <= last_ts
    are rejected under ALL modes. This guards RollingCache's append() before
    it can see stale/duplicate data.
    """

    def __init__(
        self,
        stream: DataStream,
        policy: GapPolicy,
        rest_client,
        max_retries: int = 3,
        handler: Optional[Callable[[Any], None]] = None,
        bus: Optional[MessageBus] = None,
    ):
        self.stream = stream
        self.policy = policy
        self.rest = rest_client
        self.max_retries = max_retries
        self.handler = handler
        self.bus = bus
        self._last_ts: Optional[int] = None

    def _deliver(self, bar) -> None:
        """Call handler + measure backpressure (AC-D9)."""
        if self.handler is None:
            return
        t0 = time.monotonic_ns()
        self.handler(bar)
        elapsed = time.monotonic_ns() - t0
        if elapsed > _SLOW_HANDLER_THRESHOLD_NS:
            _log.warning(
                "MessageBus handler slow: elapsed_ms=%d threshold_ms=100 stream=%r",
                elapsed // 1_000_000, self.stream,
            )

    def _expected_next_ts(self) -> int:
        period = self.stream.bar_spec.period_ns
        return self._last_ts + period

    def _nan_bar(self, ts: int, template_bar) -> _NanBar:
        return _NanBar(
            instrument_id=template_bar.instrument_id,
            bar_spec=template_bar.bar_spec,
            ts_event=ts,
            ts_init=ts,
            open=math.nan,
            high=math.nan,
            low=math.nan,
            close=math.nan,
            volume=0.0,
        )

    def _publish_gap(self, expected: int, got: int, missing: int) -> None:
        if self.bus is None:
            return
        self.bus.publish(
            self.stream,
            GapDetected(
                stream=self.stream,
                expected_ts=expected,
                got_ts=got,
                missing_count=missing,
            ),
        )

    def on_bar(self, bar) -> None:
        """Ingest a bar. Dispatches by policy.

        Monotonicity invariant (AC-D5 "regardless of mode"): bars with
        ts_event <= last_ts are rejected under ALL policies — "rejected"
        meaning "not forwarded to handler/cache", not "raise exception".
        - STRICT: loudly raise ValueError (matches 'fail-fast' policy intent)
        - NAN_FILL / SKIP: silently drop + WARN log (matches 'tolerate upstream
          hiccups' intent; Binance WS reconnect routinely delivers duplicates)

        Industry precedent (Nautilus, Lean, Backtrader) all silently drop
        non-monotonic bars. Brief AC-D5 line 807 says "Cache rejects... logs
        the gap" — no mention of raising. Design §2.5 SM: "reject, log, no
        forward" — only STRICT elevates to exception.
        """
        ts = int(bar.ts_event)

        # Monotonicity invariant — rejected across all policies; STRICT raises.
        if self._last_ts is not None and ts <= self._last_ts:
            _log.warning(
                "GapDetector reject stale/duplicate: stream=%r got_ts=%d last_ts=%d",
                self.stream, ts, self._last_ts,
            )
            # Publish observability event regardless of policy
            self._publish_gap(expected=self._last_ts + 1, got=ts, missing=0)
            if self.policy == GapPolicy.STRICT:
                raise ValueError(
                    f"non-monotonic bar on {self.stream!r}: got ts_event={ts}, "
                    f"last_ts={self._last_ts}"
                )
            return  # NAN_FILL / SKIP: silent drop, no raise, no forward

        # First bar — accept and record.
        if self._last_ts is None:
            self._last_ts = ts
            self._deliver(bar)
            return

        expected = self._expected_next_ts()
        if ts == expected:
            # Clean forward progression — no gap.
            self._last_ts = ts
            self._deliver(bar)
            return

        # Gap detected (ts > expected).
        period = self.stream.bar_spec.period_ns
        missing_count = (ts - expected) // period
        self._publish_gap(expected=expected, got=ts, missing=int(missing_count))

        if self.policy == GapPolicy.STRICT:
            # Try REST backfill N times; raise DataGapError if still unfilled.
            filled = False
            for _ in range(self.max_retries):
                refill = list(self.rest.request(self.stream, expected, ts))
                if refill:
                    # Deliver each refilled bar in order, asserting monotonicity
                    for rb in refill:
                        rb_ts = int(rb.ts_event)
                        if self._last_ts is not None and rb_ts <= self._last_ts:
                            continue  # skip dupes/stales inside REST response
                        self._last_ts = rb_ts
                        self._deliver(rb)
                    if self._last_ts == ts - period:
                        filled = True
                        break
            if not filled:
                raise DataGapError(
                    f"STRICT: gap unfilled after {self.max_retries} retries — "
                    f"stream={self.stream!r} expected_ts={expected} got_ts={ts}"
                )
            # Gap bridged; now deliver the arriving bar.
            self._last_ts = ts
            self._deliver(bar)
            return

        if self.policy == GapPolicy.NAN_FILL:
            # Emit NaN bars at every missing slot, then the real bar.
            cursor = expected
            while cursor < ts:
                nan_bar = self._nan_bar(cursor, template_bar=bar)
                self._last_ts = cursor
                self._deliver(nan_bar)
                cursor += period
            self._last_ts = ts
            self._deliver(bar)
            return

        if self.policy == GapPolicy.SKIP:
            _log.warning(
                "SKIP: dropped %d bar(s) in gap stream=%r expected_ts=%d got_ts=%d",
                int(missing_count), self.stream, expected, ts,
            )
            self._last_ts = ts
            self._deliver(bar)
            return

        raise ValueError(f"unknown GapPolicy: {self.policy}")
