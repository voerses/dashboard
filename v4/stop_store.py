"""Stop-level storage and exit-event messaging for the Real-Time Exit Sentinel.

StopStore mediates between the hourly PaperPortfolioEngine (writer) and the
real-time sentinel process (reader).  Two files are managed:

  stops.json        — current stop levels for all open positions (atomic write)
  exit_events.jsonl — sentinel → engine exit requests (append + rename-clear)

C-5 fix: File locking on exit_events.jsonl to prevent race between append
(sentinel) and rename-clear (engine).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StopLevel:
    """Snapshot of one position's stop/target/liquidation levels."""
    position_id: str
    token: str
    strategy_id: str
    direction: int          # +1 long, -1 short
    stop_price: float
    cb_price: float         # circuit-breaker price
    target_price: float
    estimated_liq_price: float
    entry_price: float
    margin_usd: float
    quantity: float
    leverage: float
    is_perp: bool
    no_stop_bars: int
    bars_held: int
    stop_active: bool
    convex_exit: bool
    trail_mult: float
    cur_atr: float
    highest: float
    lowest: float
    has_trail_schedule: bool
    chandelier_lookback: int
    cumulative_funding: float
    fee_rate: float


@dataclass
class ExitEvent:
    """A sentinel-initiated exit request passed to the hourly engine."""
    position_id: str
    token: str
    direction: int
    exit_price: float
    stop_price: float
    breach_price: float
    breach_timestamp: str
    confirm_timestamp: str
    slippage_bps: float
    sentinel_timestamp: str
    margin_usd: float
    quantity: float
    entry_price: float
    exit_reason: str


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class StopStore:
    """File-based store for stop levels and exit events."""

    STOPS_FILENAME = "stops.json"
    EXIT_EVENTS_FILENAME = "exit_events.jsonl"
    EXIT_EVENTS_LOCK = "exit_events.lock"
    CONSUMED_SUFFIX = ".consumed"

    def __init__(self, state_dir: str | Path) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def _acquire_exit_lock(self):
        """Acquire exclusive lock on exit_events (C-5 fix)."""
        lock_path = self._state_dir / self.EXIT_EVENTS_LOCK
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release_exit_lock(self, fd: int) -> None:
        """Release exclusive lock on exit_events."""
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # -- stops.json ----------------------------------------------------------

    def write_stops(self, stops: list[StopLevel]) -> None:
        """Atomically write stop levels to stops.json (write-tmp-rename)."""
        target = self._state_dir / self.STOPS_FILENAME
        data = [asdict(s) for s in stops]
        fd, tmp_path = tempfile.mkstemp(
            dir=self._state_dir, prefix=".stops_", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, target)
        except BaseException:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def read_stops(self) -> list[StopLevel]:
        """Read stop levels from stops.json.  Returns [] if file missing.

        R3-9 fix: Skips malformed entries instead of failing the entire read.
        """
        target = self._state_dir / self.STOPS_FILENAME
        if not target.exists():
            return []
        try:
            data = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read stops.json: %s", exc)
            return []
        results = []
        for d in data:
            try:
                results.append(StopLevel(**d))
            except (TypeError, KeyError) as exc:
                logger.warning("Skipping malformed stop entry: %s", exc)
        return results

    # -- exit_events.jsonl ---------------------------------------------------

    def append_exit_event(self, event: ExitEvent) -> None:
        """Append an exit event to exit_events.jsonl.

        C-5 fix: Acquires exclusive lock before writing.
        H-6 fix: fsync after write.
        """
        lock_fd = self._acquire_exit_lock()
        try:
            target = self._state_dir / self.EXIT_EVENTS_FILENAME
            with open(target, "a") as f:
                f.write(json.dumps(asdict(event)) + "\n")
                f.flush()
                os.fsync(f.fileno())
        finally:
            self._release_exit_lock(lock_fd)

    def read_and_clear_exit_events(self) -> list[ExitEvent]:
        """Read all exit events and atomically clear the file.

        C-5 fix: Acquires exclusive lock during rename+read to prevent
        sentinel from appending to a file being consumed.
        Uses rename-based clearing: rename to .consumed, read, delete.
        R8-4 fix: Recovers orphaned .consumed file from a previous crash.
        Returns [] if no file exists.
        """
        source = self._state_dir / self.EXIT_EVENTS_FILENAME
        consumed = self._state_dir / (self.EXIT_EVENTS_FILENAME + self.CONSUMED_SUFFIX)

        # R9-1/R8-4 fix: Recover orphaned .consumed file first (from previous crash)
        orphan_events: list[ExitEvent] = []
        if consumed.exists():
            logger.warning("Recovering orphaned .consumed exit events file")
            orphan_events = self._read_consumed_file(consumed)

        if not source.exists():
            return orphan_events

        lock_fd = self._acquire_exit_lock()
        try:
            # Re-check existence under lock
            if not source.exists():
                return orphan_events

            # Atomic rename — prevents new appends from mixing with our read
            try:
                os.replace(source, consumed)
            except OSError:
                return orphan_events
        finally:
            self._release_exit_lock(lock_fd)

        return orphan_events + self._read_consumed_file(consumed)

    def _read_consumed_file(self, consumed: Path) -> list[ExitEvent]:
        """Read and delete a consumed exit events file.

        R8-4 fix: Extracted helper for reuse in orphan recovery.
        """
        events: list[ExitEvent] = []
        try:
            with open(consumed) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        events.append(ExitEvent(**data))
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning("Skipping malformed exit event line: %s", exc)
        finally:
            try:
                os.unlink(consumed)
            except OSError:
                pass
        return events
