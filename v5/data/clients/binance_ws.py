"""M6 — BinanceWSClient (AC-D3, AC-D13, AC-D20).

Multiplexes 1m + 1h klines + markPriceUpdate + aggTrades on ONE class
(not 4 separate classes as v4/price_monitor.py did). Shards across WS
connections at 200 streams/connection per Binance's limit.

This Phase-4 scaffold implements the Protocol interface + trade-frame
parsing (T-D13). Full 831-LOC PriceMonitor extraction (multi-shard,
reconnect, merge window, partial-bar suppression) is deferred — see
AC-D13 stop-trigger at 18h for fallback behind DataClient shim.

Infrastructure wall-clock reads:
  - time.time_ns() — WS heartbeat timestamp, reconnect backoff
  - time.time_ns() — drift-sync local reference (delegated to BinanceRESTClient)
  These reads are engine-internals and do NOT affect AC24 determinism
  (simulator hot path reads no wall-clock).

FIX mapping: aggTrades → MDEntryType(269)=2 Trade; aggressor side via
Side(54). BinanceWS `m` field = "buyer-is-maker"; m=True → aggressor=SELL.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional

from v5.data.streams import DataKind, DataStream, TransportMode, Venue
from v5.data.types import Trade

_log = logging.getLogger(__name__)

# Binance WS limits — documented in Binance API docs
_MAX_STREAMS_PER_CONNECTION = 200


class BinanceWSClient:
    """WebSocket client for Binance spot + perp.

    Multiplexes across 1m klines, 1h klines, aggTrades, markPrice on one class.
    Shards across multiple WS connections when subscriber count exceeds 200.
    """

    venue: Venue = Venue.BINANCE
    supported_modes: FrozenSet[TransportMode] = frozenset({TransportMode.PUSH})

    def __init__(self):
        self._subscribed: List[DataStream] = []
        # Handlers keyed by stream identity
        self._handlers: Dict[DataStream, Callable[[Any], None]] = {}
        # Trade-id dedup state per stream
        self._seen_trade_ids: Dict[DataStream, set] = {}
        # Shard tracking — list of per-shard stream counts
        self._connections: List[int] = []

    # ------------------------------------------------------------
    # DataClient Protocol conformance
    # ------------------------------------------------------------

    def supports(self, stream: DataStream, mode: TransportMode) -> bool:
        # Binance WS supports BAR / TRADE / FUNDING_RATE / MARK_PRICE streams
        # via PUSH only. Other modes delegate to BinanceRESTClient.
        if mode != TransportMode.PUSH:
            return False
        return stream.data_kind in {
            DataKind.BAR, DataKind.TRADE,
            DataKind.FUNDING_RATE, DataKind.MARK_PRICE,
        }

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        self._subscribed.clear()
        self._handlers.clear()
        self._seen_trade_ids.clear()
        self._connections.clear()

    def request(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> List[Any]:
        # WS is push-only; historical requests delegate to REST.
        raise NotImplementedError(
            "BinanceWSClient does not support PULL_ONCE. Use BinanceRESTClient."
        )

    def replay(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[Any]:
        raise NotImplementedError(
            "BinanceWSClient does not support REPLAY. Use ParquetReplayClient."
        )

    # ------------------------------------------------------------
    # LiveDataClient Protocol conformance
    # ------------------------------------------------------------

    def subscribe(self, stream: DataStream) -> None:
        """PUSH subscription. Shards across connections at 200 streams/shard."""
        if stream in self._subscribed:
            return
        self._subscribed.append(stream)
        self._seen_trade_ids[stream] = set()
        # Shard assignment: find first shard with room, else open new
        for i, count in enumerate(self._connections):
            if count < _MAX_STREAMS_PER_CONNECTION:
                self._connections[i] += 1
                return
        # New shard needed
        self._connections.append(1)
        _log.info(
            "ws_shard_opened: shard_count=%d stream=%r",
            len(self._connections), stream,
        )

    def unsubscribe(self, stream: DataStream) -> None:
        if stream not in self._subscribed:
            return
        self._subscribed.remove(stream)
        self._handlers.pop(stream, None)
        self._seen_trade_ids.pop(stream, None)
        # Simple shard decrement (don't shuffle — FIFO stays stable)
        for i, count in enumerate(self._connections):
            if count > 0:
                self._connections[i] -= 1
                break

    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None:
        raise NotImplementedError(
            "BinanceWSClient does not support PULL_SCHEDULED; use BinanceRESTClient."
        )

    # ------------------------------------------------------------
    # Handler registration (M6-specific, not in Protocol)
    # ------------------------------------------------------------

    def set_handler(
        self,
        stream: DataStream,
        handler: Callable[[Any], None],
    ) -> None:
        """Register a handler for events on this stream."""
        self._handlers[stream] = handler

    # ------------------------------------------------------------
    # Frame ingestion (Wave-F wires real WS; this is the test hook)
    # ------------------------------------------------------------

    def _find_trade_stream(self, symbol: str) -> Optional[DataStream]:
        for s in self._subscribed:
            if s.data_kind == DataKind.TRADE and s.instrument.symbol == symbol:
                return s
        return None

    @classmethod
    def build_for_test(cls) -> "BinanceWSClient":
        """Test-only constructor with no-arg default state (M7 Task 13)."""
        return cls()

    def on_venue_ack(self, event: Dict[str, Any], order) -> None:
        """M7 AC-O4 — Binance venue-ack → Order.venue_order_id assignment.

        FIX OrderID(37) arrives on ExecutionReport events. Per design §2.8 ID1,
        we use object.__setattr__ to preserve M5's frozen=True invariant.

        Event shape (typical):
          {"msg_type": "ExecutionReport", "order_id": "12345", "cl_ord_id": "c-1", ...}
        """
        venue_id = event.get("order_id") or event.get("i")
        if venue_id is None:
            raise ValueError(f"BinanceWS ack missing OrderID(37): {event}")
        try:
            object.__setattr__(order, "venue_order_id", str(venue_id))
        except Exception:
            # SimpleNamespace or any non-slots object — direct assignment works
            order.venue_order_id = str(venue_id)

    def _inject_aggtrade_frame(self, frame: Dict[str, Any]) -> None:
        """Parse a Binance aggTrade frame and deliver Trade event to handler.

        Binance aggTrade frame shape:
          s: symbol, T: trade_time_ms, p: price (str), q: qty (str),
          m: is_buyer_maker (bool), a: agg_trade_id (int)

        Aggressor side: m=True → buyer is maker → aggressor is SELLER → SELL.
        m=False → aggressor is BUYER → BUY.
        """
        symbol = frame["s"]
        stream = self._find_trade_stream(symbol)
        if stream is None:
            return
        trade_id = int(frame["a"])
        seen = self._seen_trade_ids.setdefault(stream, set())
        if trade_id in seen:
            return  # dedup
        seen.add(trade_id)

        is_buyer_maker = bool(frame.get("m", False))
        side = "SELL" if is_buyer_maker else "BUY"

        trade = Trade(
            instrument_id=stream.instrument,
            ts_event=int(frame["T"]) * 1_000_000,  # ms → ns
            price=float(frame["p"]),
            qty=float(frame["q"]),
            side=side,
            trade_id=trade_id,
        )
        handler = self._handlers.get(stream)
        if handler is not None:
            handler(trade)
