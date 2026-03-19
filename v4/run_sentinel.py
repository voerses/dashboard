"""Real-Time Exit Sentinel process entry point (AC6).

CLI: python -m v4.run_sentinel --config <path>

Manages a shared PriceMonitor and per-portfolio BreachDetectors.
Reads stops.json every 60s, writes sentinel_heartbeat.json every 60s.
Writes sentinel_metrics.json every 5 minutes.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def parse_sentinel_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse sentinel CLI arguments."""
    parser = argparse.ArgumentParser(description="Real-Time Exit Sentinel")
    parser.add_argument("--config", required=True, help="Path to multi_v4_paper config JSON")
    return parser.parse_args(argv)


# ------------------------------------------------------------------
# PID lock
# ------------------------------------------------------------------

def acquire_pid_lock(pid_path: str) -> int:
    """Acquire an exclusive PID lock file (L-1 fix).

    Returns the file descriptor (must be kept open for the lock to hold).
    Raises RuntimeError if another sentinel is already running.
    """
    import fcntl

    path = Path(pid_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise RuntimeError(f"Another sentinel process holds the lock: {pid_path}")
    # Write our PID
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, str(os.getpid()).encode())
    return fd


def release_pid_lock(lock_fd: int, pid_path: str) -> None:
    """Release a PID lock file."""
    import fcntl

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    except OSError:
        pass
    path = Path(pid_path)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------

def setup_logging(state_dir: Path) -> None:
    """Set up sentinel logging with file rotation."""
    state_dir.mkdir(parents=True, exist_ok=True)

    # Main sentinel log — rotating file handler
    log_path = state_dir / "sentinel.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10_000_000, backupCount=5,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
    ))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Also log to stderr for systemd/docker
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
    ))
    root.addHandler(stderr_handler)


# ------------------------------------------------------------------
# Sentinel process
# ------------------------------------------------------------------

class SentinelProcess:
    """Main sentinel process managing PriceMonitor and BreachDetectors."""

    STOPS_REFRESH_S = 60
    HEARTBEAT_S = 60
    METRICS_S = 300  # 5 minutes
    DASHBOARD_PUSH_S = 600  # 10 minutes

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._shutdown_requested = False
        self._state_dir = Path("/tmp/sentinel_state")
        self._detectors: dict[str, "BreachDetector"] = {}
        self._metrics = None
        self._perp_monitor = None
        self._spot_monitor = None
        # Backward-compat alias used in tests / heartbeat
        self._monitor = None

    def setup_signal_handlers(self) -> None:
        """Register SIGTERM and SIGINT handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum, frame) -> None:
        """Set shutdown flag on signal receipt."""
        self._shutdown_requested = True

    @staticmethod
    def _filter_active_portfolios(portfolios: list[dict]) -> list[dict]:
        """Return portfolios where sentinel_mode is not 'off'."""
        return [p for p in portfolios if p.get("sentinel_mode", "off") != "off"]

    def write_heartbeat(
        self,
        ws_connected: bool = False,
        active_tokens: int = 0,
        spot_ws_connected: bool = False,
    ) -> None:
        """Write sentinel_heartbeat.json to state_dir.

        R3-10 fix: Atomic write via tmp+rename.
        """
        import tempfile

        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / "sentinel_heartbeat.json"
        data = {
            "timestamp": time.time(),
            "pid": os.getpid(),
            "ws_connected": ws_connected,
            "spot_ws_connected": spot_ws_connected,
            "active_tokens": active_tokens,
        }
        fd, tmp_path = tempfile.mkstemp(
            dir=self._state_dir, prefix=".heartbeat_", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _push_dashboard(self) -> None:
        """Regenerate and push the dashboard to gh-pages."""
        try:
            import subprocess
            result = subprocess.run(
                ["/workspace/venv/bin/python", "tools/generate_dashboard_v2.py", "--push"],
                capture_output=True, text=True, timeout=120,
                cwd="/workspace/crypto_backtest",
            )
            if result.returncode == 0:
                logger.info("Dashboard pushed to gh-pages")
            else:
                logger.warning("Dashboard push failed: %s", result.stderr[-200:] if result.stderr else "no stderr")
        except Exception as exc:
            logger.warning("Dashboard push error: %s", exc)

    def _load_config(self) -> dict:
        """Load multi-portfolio config JSON."""
        with open(self._config_path) as f:
            return json.load(f)

    def _on_perp_price(self, token: str, price: float, timestamp_ms: int) -> None:
        """Callback for perp PriceMonitor."""
        self._on_price_update(token, price, timestamp_ms, is_perp=True)

    def _on_spot_price(self, token: str, price: float, timestamp_ms: int) -> None:
        """Callback for spot PriceMonitor."""
        self._on_price_update(token, price, timestamp_ms, is_perp=False)

    def _on_price_update(
        self, token: str, price: float, timestamp_ms: int,
        is_perp: bool | None = None,
    ) -> None:
        """Callback for PriceMonitor — dispatches to all BreachDetectors."""
        if self._metrics:
            self._metrics.record_message()

        for name, detector in self._detectors.items():
            # R3-F1 fix: Atomically get both last event and all events
            result, all_events = detector.check_price(token, price, timestamp_ms, is_perp=is_perp)
            if result:
                # R2-F3 fix: Process ALL events from this check for metrics
                for evt in all_events:
                    event_type = evt.get("event_type", "")
                    pid = evt.get("position_id", "")
                    logger.info(
                        "[%s] %s on %s: %s (price=%.2f)",
                        name, event_type, token, pid, price,
                    )
                    # R9-3 fix: Only count actual breaches, not warnings or immediate exits
                    # R4-F2 fix: Immediate exits (target_breach, liq_emergency) are exits,
                    # not separate breach events — don't inflate breach count.
                    if self._metrics and event_type not in ("liq_warning", "target_breach", "liq_emergency"):
                        self._metrics.record_breach(pid)

                    # R8-3 fix: Track immediate exits (target_breach, liq_emergency)
                    # in metrics and cascade detection — not just timer-confirmed exits
                    if event_type in ("target_breach", "liq_emergency"):
                        if self._metrics:
                            self._metrics.record_exit_triggered(pid)
                            self._metrics.record_confirmation_time(0.0)
                        detector.record_exit_timestamp(timestamp_ms / 1000.0)

            # R3-14/R4-6 fix: Use public API to check pending (thread-safe)
            if not detector.has_pending_confirmations():
                continue

            # Check pending confirmations with venue-separated prices
            perp_prices = self._perp_monitor.get_latest_prices() if self._perp_monitor else {}
            spot_prices = self._spot_monitor.get_latest_prices() if self._spot_monitor else {}
            confirmed = detector.confirm_pending_exits(
                current_time_s=timestamp_ms / 1000.0,
                perp_prices=perp_prices,
                spot_prices=spot_prices,
            )
            for event in confirmed:
                pid = event.get("position_id", "")
                logger.info("[%s] EXIT CONFIRMED: %s", name, pid)
                if self._metrics:
                    self._metrics.record_exit_triggered(pid)
                    # R2-F5 fix: Record actual elapsed time, not tier delay
                    actual_time = event.get("actual_confirmation_time_s",
                                             event.get("confirmation_delay_s", 0))
                    self._metrics.record_confirmation_time(actual_time)
                # Record for cascade detection
                detector.record_exit_timestamp(timestamp_ms / 1000.0)

    def _refresh_stops(self) -> tuple[set[str], set[str]]:
        """Re-read stops.json for each portfolio and update detectors.

        Returns (perp_tokens, spot_tokens) — tokens partitioned by venue.
        """
        perp_tokens: set[str] = set()
        spot_tokens: set[str] = set()

        for name, detector in self._detectors.items():
            stops = detector.stop_store.read_stops()
            detector.update_stops(stops)

            for s in stops:
                if s.is_perp:
                    perp_tokens.add(s.token)
                else:
                    spot_tokens.add(s.token)

            # State consistency check (R3-5 fix: use locked getter)
            if self._metrics:
                cached_ids = detector.get_cached_position_ids()
                stops_ids = {s.position_id for s in stops}
                stale = self._metrics.check_consistency(cached_ids, stops_ids)
                if stale:
                    logger.warning(
                        "[%s] Stale position IDs in cache: %s", name, stale,
                    )

        return perp_tokens, spot_tokens

    def run(self) -> None:
        """Main sentinel loop."""
        from v4.breach_detector import BreachDetector
        from v4.price_monitor import PriceMonitor
        from v4.sentinel_metrics import SentinelMetrics
        from v4.stop_store import StopStore

        self.setup_signal_handlers()

        # Load config
        config = self._load_config()
        shared = config.get("shared", {})
        portfolios = config.get("portfolios", [])

        # Merge shared fields into portfolios
        for p in portfolios:
            for k, v in shared.items():
                if k not in p:
                    p[k] = v

        active = self._filter_active_portfolios(portfolios)
        if not active:
            logger.info("No portfolios with sentinel_mode enabled. Exiting.")
            return

        # Use first portfolio's state_dir as sentinel state dir,
        # or fall back to /tmp/sentinel_state
        first_state_dir = active[0].get("state_dir", "/tmp/sentinel_state")
        self._state_dir = Path(first_state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)

        setup_logging(self._state_dir)
        logger.info("Starting sentinel with %d active portfolios", len(active))

        # Initialize metrics
        self._metrics = SentinelMetrics(state_dir=self._state_dir)

        # Initialize per-portfolio breach detectors
        for p in active:
            name = p.get("name", p.get("pool_name", p.get("portfolio_name", "unnamed")))
            state_dir = Path(p.get("state_dir", str(self._state_dir / name)))
            state_dir.mkdir(parents=True, exist_ok=True)

            store = StopStore(state_dir=state_dir)
            mode = p.get("sentinel_mode", "shadow")
            tiers = p.get("confirmation_tiers", None)

            detector = BreachDetector(
                portfolio_name=name,
                stop_store=store,
                state_dir=state_dir,
                sentinel_mode=mode,
                confirmation_tiers=tiers,
            )
            self._detectors[name] = detector
            logger.info("Initialized detector for %s (mode=%s)", name, mode)

        # R5-5 fix: Acquire PID lock before starting any threads
        pid_path = str(self._state_dir / "sentinel.pid")
        lock_fd = acquire_pid_lock(pid_path)

        # Initial stops load
        perp_tokens, spot_tokens = self._refresh_stops()
        all_tokens = perp_tokens | spot_tokens
        logger.info(
            "Monitoring %d tokens (%d perp, %d spot): %s",
            len(all_tokens), len(perp_tokens), len(spot_tokens),
            sorted(all_tokens),
        )

        # Initialize perp price monitor with metrics callbacks (R2-8 fix)
        self._perp_monitor = PriceMonitor(
            callback=self._on_perp_price,
            rest_poll_interval_s=10,
            venue="perp",
        )
        # R2-F1 fix: Pass venue to metrics callbacks for per-venue uptime tracking
        self._perp_monitor._on_ws_connect_cb = lambda: self._metrics.record_ws_connect("perp")
        self._perp_monitor._on_ws_disconnect_cb = lambda: self._metrics.record_ws_disconnect("perp")
        self._perp_monitor._on_ws_reconnect_cb = self._metrics.record_ws_reconnection
        # Backward-compat alias
        self._monitor = self._perp_monitor

        # Initialize spot price monitor
        self._spot_monitor = PriceMonitor(
            callback=self._on_spot_price,
            rest_poll_interval_s=10,
            venue="spot",
        )
        self._spot_monitor._on_ws_connect_cb = lambda: self._metrics.record_ws_connect("spot")
        self._spot_monitor._on_ws_disconnect_cb = lambda: self._metrics.record_ws_disconnect("spot")
        self._spot_monitor._on_ws_reconnect_cb = self._metrics.record_ws_reconnection

        # Start WebSocket connections (if we have tokens to monitor)
        perp_ws_started = False
        spot_ws_started = False
        if perp_tokens:
            self._perp_monitor.connect(list(perp_tokens))
            perp_ws_started = True
        if spot_tokens:
            self._spot_monitor.connect(list(spot_tokens))
            spot_ws_started = True

        # Timing for periodic tasks
        last_stops_refresh = time.time()
        last_heartbeat = time.time()
        last_metrics = time.time()
        last_dashboard_push = time.time()

        try:
            while not self._shutdown_requested:
                now = time.time()

                # Refresh stops every 60s
                if now - last_stops_refresh >= self.STOPS_REFRESH_S:
                    new_perp, new_spot = self._refresh_stops()

                    # Update perp subscriptions if token set changed
                    if new_perp != perp_tokens:
                        if not perp_ws_started and new_perp:
                            self._perp_monitor.connect(list(new_perp))
                            perp_ws_started = True
                        elif perp_ws_started:
                            self._perp_monitor.update_subscriptions(new_perp)
                        perp_tokens = new_perp

                    # Update spot subscriptions if token set changed
                    if new_spot != spot_tokens:
                        if not spot_ws_started and new_spot:
                            self._spot_monitor.connect(list(new_spot))
                            spot_ws_started = True
                        elif spot_ws_started:
                            self._spot_monitor.update_subscriptions(new_spot)
                        spot_tokens = new_spot

                    all_tokens = perp_tokens | spot_tokens
                    logger.info(
                        "Updated subscriptions: %d perp, %d spot tokens",
                        len(perp_tokens), len(spot_tokens),
                    )
                    last_stops_refresh = now

                # Heartbeat every 60s
                if now - last_heartbeat >= self.HEARTBEAT_S:
                    self.write_heartbeat(
                        ws_connected=self._perp_monitor.ws_connected,
                        spot_ws_connected=self._spot_monitor.ws_connected,
                        active_tokens=len(all_tokens),
                    )
                    last_heartbeat = now

                # Metrics every 5 minutes
                if now - last_metrics >= self.METRICS_S:
                    # R3-F3 fix: Propagate wick-filtered counts to metrics
                    for det_name, detector in self._detectors.items():
                        wick_count = detector.get_and_reset_wick_filtered_count()
                        for _ in range(wick_count):
                            self._metrics.record_wick_filtered("")
                    self._metrics.write_metrics()
                    last_metrics = now

                # Dashboard push every 10 minutes
                if now - last_dashboard_push >= self.DASHBOARD_PUSH_S:
                    self._push_dashboard()
                    last_dashboard_push = now

                # R3-15/T5-2 fix: REST fallback is handled by the WS reconnect thread.
                # No duplicate REST polling from the main loop (avoids rate limit violations).

                # F6 fix: Sweep pending confirmations on main loop tick.
                # Ensures time-expired confirmations fire even during price drought.
                # R2-F2 fix: Pass current_time_s explicitly so breach_time comparison
                # uses the same clock source (time.time()) as the main loop.
                perp_prices = self._perp_monitor.get_latest_prices() if self._perp_monitor else {}
                spot_prices = self._spot_monitor.get_latest_prices() if self._spot_monitor else {}
                for det_name, detector in self._detectors.items():
                    if not detector.has_pending_confirmations():
                        continue
                    confirmed = detector.confirm_pending_exits(
                        current_time_s=now,
                        perp_prices=perp_prices,
                        spot_prices=spot_prices,
                    )
                    for event in confirmed:
                        eid = event.get("position_id", "")
                        logger.info("[%s] EXIT CONFIRMED (main loop sweep): %s", det_name, eid)
                        if self._metrics:
                            self._metrics.record_exit_triggered(eid)
                            # R2-F5 fix: Record actual elapsed time, not tier delay
                            actual_time = event.get("actual_confirmation_time_s",
                                                     event.get("confirmation_delay_s", 0))
                            self._metrics.record_confirmation_time(actual_time)
                        detector.record_exit_timestamp(now)

                time.sleep(1.0)  # Main loop tick

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            logger.info("Shutting down sentinel...")
            if self._perp_monitor:
                self._perp_monitor.disconnect()
            if self._spot_monitor:
                self._spot_monitor.disconnect()
            if self._metrics:
                self._metrics.write_metrics()
            self.write_heartbeat(ws_connected=False, spot_ws_connected=False, active_tokens=0)
            release_pid_lock(lock_fd, pid_path)
            logger.info("Sentinel stopped.")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    """CLI entry point: python -m v4.run_sentinel --config <path>."""
    args = parse_sentinel_args()
    sentinel = SentinelProcess(config_path=args.config)
    sentinel.run()


if __name__ == "__main__":
    main()
