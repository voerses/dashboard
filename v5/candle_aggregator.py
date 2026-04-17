"""Thread-safe candle aggregator for sub-hourly paper trading exits.

Buffers WebSocket price ticks into N-minute candles.  The WS thread calls
on_price() and the main thread calls flush_completed() — all access to
mutable state is protected by a single lock.
"""
from __future__ import annotations

import threading


class CandleAggregator:
    """Buffers WebSocket price ticks into N-minute candles.

    Thread-safe: on_price() called from WS thread,
    flush_completed() called from main thread.
    """

    def __init__(self, resolution_minutes: int):
        if resolution_minutes <= 0:
            raise ValueError(f"resolution_minutes must be > 0, got {resolution_minutes}")
        self._resolution = resolution_minutes
        self._lock = threading.Lock()
        # token -> {high, low, close, interval_key}
        self._current: dict[str, dict] = {}
        # list of (token, high, low, close) for completed candles
        self._completed: list[tuple[str, float, float, float]] = []

    def on_price(self, token: str, price: float, timestamp_ms: int) -> None:
        """Called from WS thread on each price update."""
        interval_key = (timestamp_ms // 1000) // (self._resolution * 60)
        with self._lock:
            cur = self._current.get(token)
            if cur is None or cur["interval_key"] != interval_key:
                # New interval — emit previous candle if exists
                if cur is not None:
                    self._completed.append(
                        (token, cur["high"], cur["low"], cur["close"])
                    )
                self._current[token] = {
                    "high": price,
                    "low": price,
                    "close": price,
                    "interval_key": interval_key,
                }
            else:
                cur["high"] = max(cur["high"], price)
                cur["low"] = min(cur["low"], price)
                cur["close"] = price

    def flush_completed(self) -> dict[str, tuple[float, float, float]]:
        """Called from main thread.  Returns {token: (high, low, close)} for completed candles.

        If multiple candles completed for the same token between flushes,
        merges conservatively: high=max, low=min, close=last.  This ensures
        stop-loss breaches in earlier candles are not silently dropped.
        """
        with self._lock:
            result: dict[str, tuple[float, float, float]] = {}
            for token, h, l, c in self._completed:
                if token in result:
                    prev_h, prev_l, _ = result[token]
                    result[token] = (max(prev_h, h), min(prev_l, l), c)
                else:
                    result[token] = (h, l, c)
            self._completed.clear()
            return result

    def update_tokens(self, active_tokens: set[str]) -> None:
        """Remove tokens no longer being tracked."""
        with self._lock:
            stale = set(self._current.keys()) - active_tokens
            for t in stale:
                self._current.pop(t, None)
