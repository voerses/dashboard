"""Sentinel metrics collection and state consistency checking (AC8, AC9).

R3-6 fix: All mutable state protected by threading.Lock.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path


class SentinelMetrics:
    """Collects operational metrics for the Exit Sentinel.

    Tracks breach counts, wick filters, exits, WS reconnections,
    message throughput, and uptime.  Writes sentinel_metrics.json
    on demand (called every 5 minutes by the sentinel process).

    Thread-safe: all public methods acquire self._lock.

    R2-F1 fix: Supports dual-venue (perp + spot) monitors with per-venue
    uptime tracking.  connect/disconnect callbacks accept an optional ``venue``
    parameter so each monitor's uptime is tracked independently.
    """

    def __init__(self, state_dir: str | Path) -> None:
        self._state_dir = Path(state_dir)
        self._start_time = time.time()
        self._lock = threading.Lock()

        # Counters
        self._total_breaches: int = 0
        self._wick_filtered: int = 0
        self._exits_triggered: int = 0
        self._ws_reconnections: int = 0
        self._message_count: int = 0
        self._interval_message_count: int = 0
        self._last_metrics_time: float = self._start_time

        # R2-F1 fix: Per-venue uptime tracking (supports dual monitors)
        self._ws_connected_venues: dict[str, bool] = {}
        self._ws_connected_time_venues: dict[str, float] = {}
        self._ws_last_connect_ts_venues: dict[str, float] = {}

        # Backward-compat single-venue aliases (used by existing tests)
        self._ws_connected: bool = False
        self._ws_connected_time: float = 0.0
        self._ws_last_connect_ts: float = 0.0

        # Confirmation time tracking
        self._confirmation_times: list[float] = []

    # ------------------------------------------------------------------
    # Recording (all acquire lock)
    # ------------------------------------------------------------------

    def record_breach(self, position_id: str) -> None:
        with self._lock:
            self._total_breaches += 1

    def record_wick_filtered(self, position_id: str) -> None:
        with self._lock:
            self._wick_filtered += 1

    def record_exit_triggered(self, position_id: str) -> None:
        with self._lock:
            self._exits_triggered += 1

    def record_ws_reconnection(self) -> None:
        with self._lock:
            self._ws_reconnections += 1

    def record_message(self) -> None:
        with self._lock:
            self._message_count += 1
            self._interval_message_count += 1

    def record_ws_connect(self, venue: str = "default") -> None:
        """Record WS connection established for a specific venue.

        R2-F1 fix: Per-venue tracking so dual monitors don't corrupt
        each other's uptime.
        """
        with self._lock:
            now = time.time()
            self._ws_connected_venues[venue] = True
            self._ws_last_connect_ts_venues[venue] = now
            self._ws_connected_time_venues.setdefault(venue, 0.0)
            # R3-F4 fix: Only set backward-compat timestamp on first connect
            if not self._ws_connected:
                self._ws_last_connect_ts = now
            self._ws_connected = True

    def record_ws_disconnect(self, venue: str = "default") -> None:
        """Record WS disconnection for a specific venue.

        R2-F1 fix: Per-venue tracking.
        """
        with self._lock:
            now = time.time()
            if self._ws_connected_venues.get(venue, False):
                connect_ts = self._ws_last_connect_ts_venues.get(venue, now)
                self._ws_connected_time_venues[venue] = (
                    self._ws_connected_time_venues.get(venue, 0.0)
                    + (now - connect_ts)
                )
                self._ws_connected_venues[venue] = False
            # Backward-compat: mark disconnected only if ALL venues are down
            any_connected = any(self._ws_connected_venues.values())
            if not any_connected and self._ws_connected:
                self._ws_connected_time += now - self._ws_last_connect_ts
                self._ws_connected = False

    def record_confirmation_time(self, duration_s: float) -> None:
        with self._lock:
            if len(self._confirmation_times) > 1000:
                self._confirmation_times = self._confirmation_times[-500:]
            self._confirmation_times.append(duration_s)

    # ------------------------------------------------------------------
    # Metrics output
    # ------------------------------------------------------------------

    def write_metrics(self) -> None:
        """Write sentinel_metrics.json with all required fields.

        R3-10 fix: Atomic write via tmp+rename.
        """
        now = time.time()

        with self._lock:
            elapsed = now - self._start_time
            interval = now - self._last_metrics_time

            avg_conf = 0.0
            if self._confirmation_times:
                avg_conf = sum(self._confirmation_times) / len(self._confirmation_times)

            mps = self._interval_message_count / interval if interval > 0 else 0.0

            # R3-F2 fix: Average per-venue uptime to prevent >100%
            # with dual monitors both connected simultaneously.
            connected_time = sum(self._ws_connected_time_venues.values())
            for venue, is_connected in self._ws_connected_venues.items():
                if is_connected:
                    connect_ts = self._ws_last_connect_ts_venues.get(venue, now)
                    connected_time += now - connect_ts
            num_venues = max(len(self._ws_connected_time_venues), 1)
            # Fallback to single-venue tracking if no per-venue data
            if not self._ws_connected_time_venues:
                connected_time = self._ws_connected_time
                if self._ws_connected:
                    connected_time += now - self._ws_last_connect_ts
                num_venues = 1
            uptime_pct = (connected_time / (elapsed * num_venues) * 100.0) if elapsed > 0 else 0.0

            metrics = {
                "total_breaches": self._total_breaches,
                "wick_filtered": self._wick_filtered,
                "exits_triggered": self._exits_triggered,
                "ws_reconnections": self._ws_reconnections,
                "avg_confirmation_time_s": round(avg_conf, 3),
                "messages_per_second": round(mps, 3),
                "uptime_pct": round(uptime_pct, 2),
                "timestamp": now,
            }

            self._last_metrics_time = now
            self._interval_message_count = 0

        # Write atomically outside lock (R3-10 fix)
        path = self._state_dir / "sentinel_metrics.json"
        fd, tmp_path = tempfile.mkstemp(
            dir=self._state_dir, prefix=".metrics_", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(metrics, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # State consistency (AC9)
    # ------------------------------------------------------------------

    @staticmethod
    def check_consistency(
        cached_ids: set[str], stops_ids: set[str],
    ) -> set[str]:
        """Return position IDs in cache but not in stops.json (stale)."""
        return cached_ids - stops_ids
