"""M6 — BinanceRESTClient (AC-D3, AC-D10, AC-D11, AC-D20).

Extracts Binance REST concerns from v4/live_fetcher.py:
  - REST weight tracker (AC-D10): trusts X-MBX-USED-WEIGHT-1M header
  - Time-drift sync (AC-D11): periodic /time endpoint check, severity-based
    WARN/ERROR + ClockDriftHigh event on bus

Infrastructure wall-clock reads:
  - time.time_ns() — rate-limit window reset timestamp calculation
  - time.time_ns() — local clock reference for drift sync
  These reads are engine-internals and do NOT affect AC24 determinism (the
  simulator hot path does not read wall-clock; this client runs outside sim).

FIX mapping: no direct analog for weight tracker (Binance-specific). Time
drift is informational telemetry; ClockDriftHigh event has no FIX mapping.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, FrozenSet, Iterator, List, Optional

from v5.data.bus import MessageBus
from v5.data.exceptions import ClockDriftHigh, RateLimitExceeded
from v5.data.streams import DataStream, TransportMode, Venue

_log = logging.getLogger(__name__)

_BINANCE_WEIGHT_CAP_PER_MIN = 1200
_WINDOW_NS = 60 * 1_000_000_000  # 1 minute in ns

_DRIFT_WARN_MS = 500
_DRIFT_ERROR_MS = 5000


class BinanceRESTClient:
    """REST client with weight tracker + time-drift sync.

    Attributes:
      venue:                    Venue.BINANCE
      supported_modes:          {PULL_ONCE, PULL_SCHEDULED}
      venue_clock_offset_ms:    signed drift between venue clock and local ns
    """

    venue: Venue = Venue.BINANCE
    supported_modes: FrozenSet[TransportMode] = frozenset({
        TransportMode.PULL_ONCE, TransportMode.PULL_SCHEDULED,
    })

    @classmethod
    def build_for_test(cls) -> "BinanceRESTClient":
        """Test-only constructor with no-arg default state (M7 Task 13)."""
        return cls()

    def on_venue_ack(self, response: dict, order) -> None:
        """M7 AC-O4 — REST venue-ack → Order.venue_order_id assignment.

        FIX OrderID(37) arrives on POST /fapi/v1/order response as `orderId`.
        Per design §2.8 ID1, uses object.__setattr__ to preserve M5 frozen invariant.
        """
        venue_id = response.get("orderId") or response.get("order_id")
        if venue_id is None:
            raise ValueError(f"BinanceREST ack missing orderId: {response}")
        try:
            object.__setattr__(order, "venue_order_id", str(venue_id))
        except Exception:
            order.venue_order_id = str(venue_id)

    def __init__(self, bus: Optional[MessageBus] = None):
        self._bus = bus
        self._weight_budget: int = _BINANCE_WEIGHT_CAP_PER_MIN
        self._weight_window_reset_ts: int = time.time_ns() + _WINDOW_NS
        self.venue_clock_offset_ms: int = 0
        self._drift_handlers: List[Callable[[Any], None]] = []

    # ------------------------------------------------------------
    # DataClient Protocol conformance
    # ------------------------------------------------------------

    def supports(self, stream: DataStream, mode: TransportMode) -> bool:
        return mode in self.supported_modes

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def request(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> List[Any]:
        """PULL_ONCE — stub returning empty list. Wave-F fills with real
        Binance REST calls. Weight check runs on every call."""
        self._check_budget(weight_cost=1)
        return []

    def replay(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[Any]:
        return iter([])

    # ------------------------------------------------------------
    # LiveDataClient ops — PUSH not supported
    # ------------------------------------------------------------

    def subscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "BinanceRESTClient does not support PUSH; use BinanceWSClient."
        )

    def unsubscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "BinanceRESTClient does not support PUSH."
        )

    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None:
        """PULL_SCHEDULED — cron REST polling. Wave-F wires the actual loop."""
        pass

    # ------------------------------------------------------------
    # Weight tracker (AC-D10)
    # ------------------------------------------------------------

    def _check_budget(self, weight_cost: int) -> None:
        """Deduct `weight_cost` from the current window. Raise RateLimitExceeded
        if budget would go negative; reset budget if window expired."""
        now_ns = time.time_ns()
        if now_ns >= self._weight_window_reset_ts:
            # Window rolled over — reset budget
            self._weight_budget = _BINANCE_WEIGHT_CAP_PER_MIN
            self._weight_window_reset_ts = now_ns + _WINDOW_NS

        if self._weight_budget < weight_cost:
            wait_ms = max(0, (self._weight_window_reset_ts - now_ns) // 1_000_000)
            raise RateLimitExceeded(retry_after_ms=int(wait_ms))
        self._weight_budget -= weight_cost

    def _on_response(self, headers: dict) -> None:
        """Called after every REST response. Trusts Binance's
        X-MBX-USED-WEIGHT-1M header as source of truth."""
        used = headers.get("X-MBX-USED-WEIGHT-1M")
        if used is None:
            # Missing header — conservatively reset to full cap (next response
            # will correct)
            self._weight_budget = _BINANCE_WEIGHT_CAP_PER_MIN
            return
        try:
            used_int = int(used)
        except (ValueError, TypeError):
            return
        self._weight_budget = max(0, _BINANCE_WEIGHT_CAP_PER_MIN - used_int)

    # ------------------------------------------------------------
    # Time-drift sync (AC-D11)
    # ------------------------------------------------------------

    def subscribe_drift_events(self, handler: Callable[[Any], None]) -> None:
        """Register a handler for ClockDriftHigh events."""
        self._drift_handlers.append(handler)

    def _apply_time_sync(self, venue_ms: int, local_ms: int) -> None:
        """Compute drift from a time-sync response.

        Called by the periodic sync loop (Wave-F: actual /time endpoint call).
        Severity:
          |drift| > 500ms → WARN log
          |drift| > 5000ms → ERROR log + publish ClockDriftHigh event
        """
        drift_ms = venue_ms - local_ms
        self.venue_clock_offset_ms = drift_ms
        abs_drift = abs(drift_ms)

        if abs_drift > _DRIFT_ERROR_MS:
            _log.error(
                "venue clock drift %dms > %dms — backtest/live parity suspect",
                drift_ms, _DRIFT_ERROR_MS,
            )
            event = ClockDriftHigh(drift_ms=drift_ms, venue=self.venue)
            for h in self._drift_handlers:
                try:
                    h(event)
                except Exception as e:
                    _log.error("drift handler raised: %r", e)
        elif abs_drift > _DRIFT_WARN_MS:
            _log.warning(
                "venue clock drift %dms > %dms — check NTP",
                drift_ms, _DRIFT_WARN_MS,
            )
