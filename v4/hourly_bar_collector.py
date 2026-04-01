"""HourlyBarCollector — Collects 1H bars from WebSocket and tracks per-hour readiness.

State machine: COLLECTING -> READY -> CONSUMED -> COLLECTING.

Bars arriving for the next hour while state is READY are buffered.
Bars for already-consumed hours are silently dropped from tracking
(but still persisted via the write queue).

A dedicated writer thread drains the write queue and calls
``LiveFetcher.append_to_parquet()`` with per-token write locks
to prevent concurrent read-modify-write corruption.
"""
from __future__ import annotations

import enum
import logging
import math
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)

HOUR_MS = 3_600_000


class HourlyBarCollector:
    """Collects 1H bars from WebSocket and tracks per-hour readiness."""

    class State(enum.Enum):
        COLLECTING = "collecting"
        READY = "ready"
        CONSUMED = "consumed"

    def __init__(
        self,
        expected_tokens: dict[str, set[str]],
        fetcher: Any,
        readiness_pct: float = 0.90,
        timeout_s: float = 60.0,
    ) -> None:
        self._expected = expected_tokens
        self._fetcher = fetcher
        self._readiness_pct = readiness_pct
        self._timeout_s = timeout_s

        self._state = self.State.COLLECTING
        self._current_hour: int = 0
        self._received: dict[str, set[str]] = {m: set() for m in expected_tokens}
        self._next_hour_buffer: list[tuple[str, dict, str]] = []
        self._consumed_hours: set[int] = set()

        self._ready = threading.Event()
        self._lock = threading.Lock()

        # Total expected count across all markets
        self._total_expected = sum(len(v) for v in expected_tokens.values())

        # Handle edge case: 0 expected tokens => always ready
        if self._total_expected == 0:
            self._ready.set()
            self._state = self.State.READY

        # Write queue + per-token locks
        self._write_queue: queue.Queue[tuple[str | None, str | None, dict | None]] = queue.Queue()
        self._1h_write_locks: dict[str, threading.Lock] = {}
        self._shutdown_event = threading.Event()

        # Start writer thread
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="hourly-bar-writer",
            daemon=True,
        )
        self._writer_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """Whether the collector has enough data for the current hour."""
        return self._ready.is_set()

    def on_bar(self, token: str, bar: dict, market: str) -> None:
        """Called from WS thread on 1H candle close.

        Always enqueues for persistence. Tracks readiness unless the
        bar's hour has already been consumed.
        """
        # Always enqueue for persistence (even late bars)
        if not self._shutdown_event.is_set():
            self._write_queue.put((token, market, bar))

        # Track readiness
        with self._lock:
            hour = (bar["timestamp"] // HOUR_MS) * HOUR_MS

            # Drop bars for already-consumed hours from tracking
            if hour in self._consumed_hours:
                return

            if self._state == self.State.READY and hour != self._current_hour:
                # Next hour's bar arriving while previous not consumed — buffer it
                self._next_hour_buffer.append((token, bar, market))
                return

            if hour != self._current_hour:
                self._reset_hour(hour)

            # Only count toward readiness if token is in expected set
            expected_for_market = self._expected.get(market, set())
            if token in expected_for_market:
                self._received.setdefault(market, set()).add(token)
            else:
                return  # Unexpected token — persist but don't count

            if self._check_readiness():
                self._state = self.State.READY
                self._ready.set()

    def wait_for_ready(self, timeout: float | None = None) -> bool:
        """Block until data is ready or timeout.

        Returns True if readiness was achieved, False on timeout.
        """
        t = timeout if timeout is not None else self._timeout_s
        return self._ready.wait(timeout=t)

    def consume(self) -> dict[str, set[str]]:
        """Transition READY -> CONSUMED -> COLLECTING.

        Returns the received tokens for the completed hour.
        Replays any buffered next-hour bars into the new collecting period.
        """
        with self._lock:
            self._state = self.State.CONSUMED
            received = {m: set(s) for m, s in self._received.items()}
            self._consumed_hours.add(self._current_hour)
            self._ready.clear()

            # Capture buffered bars before resetting
            buffered = self._next_hour_buffer[:]
            self._next_hour_buffer.clear()

            # Reset to COLLECTING
            self._state = self.State.COLLECTING

        # Replay buffered next-hour bars outside the lock
        for token, bar, market in buffered:
            self.on_bar(token, bar, market)

        return received

    def get_missing_tokens(self) -> dict[str, set[str]]:
        """Return tokens that didn't report via WS for the current hour."""
        with self._lock:
            missing = {}
            for market, expected in self._expected.items():
                received = self._received.get(market, set())
                missing[market] = expected - received
            return missing

    def shutdown(self) -> None:
        """Stop writer thread and drain remaining queue items."""
        if self._shutdown_event.is_set():
            return  # idempotent

        self._shutdown_event.set()

        # Poison pill to unblock the writer thread
        self._write_queue.put((None, None, None))

        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=30)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_hour(self, hour: int) -> None:
        """Reset tracking for a new hour. Caller must hold self._lock."""
        self._current_hour = hour
        self._received = {m: set() for m in self._expected}
        self._ready.clear()

    def _check_readiness(self) -> bool:
        """Check if enough tokens have reported. Caller must hold self._lock."""
        if self._total_expected == 0:
            return True
        total_received = sum(len(s) for s in self._received.values())
        threshold = math.ceil(self._total_expected * self._readiness_pct)
        return total_received >= threshold

    def _writer_loop(self) -> None:
        """Background thread: drain write queue and persist via fetcher."""
        while True:
            try:
                item = self._write_queue.get(timeout=1.0)
            except queue.Empty:
                if self._shutdown_event.is_set():
                    break
                continue

            token, market, bar = item

            # Poison pill — shutdown
            if token is None:
                self._write_queue.task_done()
                break

            # Acquire per-token lock for write safety
            lock = self._1h_write_locks.setdefault(token, threading.Lock())
            try:
                with lock:
                    self._fetcher.append_to_parquet(token, market, [bar])
            except Exception as exc:
                logger.error(
                    "Error persisting 1h bar for %s/%s: %s", token, market, exc,
                    exc_info=True,
                )
            finally:
                self._write_queue.task_done()
