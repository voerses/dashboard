"""Breach detection and confirmation for the Real-Time Exit Sentinel.

BreachDetector monitors mark prices against stop levels.  When a stop is
breached, a confirmation timer starts.  If the price stays below (longs)
or above (shorts) the stop for the tier-specific duration, the breach is
confirmed and an exit event is emitted.  If price recovers, the timer is
cancelled (wick filter).

One BreachDetector instance per portfolio — state is never shared.
Thread safety: All public methods acquire self._lock (R2-1 fix).
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from v4.stop_store import StopLevel, StopStore, ExitEvent

logger = logging.getLogger(__name__)

# Tokens in the btc_eth and top-10 liquidity tiers
_BTC_ETH_TOKENS = {"BTC", "ETH"}
_TOP10_TOKENS = {
    "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "MATIC", "SHIB", "TRX", "UNI", "LTC",
}

RING_BUFFER_MAX = 50


class BreachDetector:
    """Per-portfolio breach detection with tier-based confirmation delays."""

    def __init__(
        self,
        portfolio_name: str,
        stop_store: StopStore,
        state_dir: str | Path,
        sentinel_mode: str = "shadow",
        confirmation_tiers: dict | None = None,
    ) -> None:
        self.portfolio_name = portfolio_name
        self.stop_store = stop_store
        self._state_dir = Path(state_dir)
        self.sentinel_mode = sentinel_mode
        self._confirmation_tiers = confirmation_tiers or {
            "btc_eth": 30, "top10": 60, "other": 90,
        }

        # R2-1 fix: Thread-safety lock for all mutable state
        self._lock = threading.Lock()

        # Current stops indexed by token
        self._stops_by_token: dict[str, list[StopLevel]] = {}

        # Active confirmation timers: position_id -> timer dict
        self._pending: dict[str, dict] = {}

        # Wick-filtered events log
        self._wick_filtered: list[dict] = []

        # Cascade tracking: timestamps of recent confirmed exits
        self._exit_timestamps: list[float] = []
        self._cascade_events: list[dict] = []
        self._cascade_window_s = 300  # 5 minutes
        self._cascade_threshold = 6   # >5 means >=6
        self._last_cascade_ts: float = 0.0  # M-4: dedup cascade events

        # Sentinel tracking per position (trail tightening state)
        self._sentinel_highest: dict[str, float] = {}  # position_id -> highest price
        self._sentinel_lowest: dict[str, float] = {}   # position_id -> lowest price
        self._sentinel_stop: dict[str, float] = {}     # position_id -> tightened stop
        self._liq_warning_fired: set[str] = set()      # position_ids that got 90% warning
        self._immediate_exit_fired: set[str] = set()  # R5-1: dedup immediate exits

        # R5-3 fix: Deferred I/O list — built under lock, persisted outside lock
        self._deferred_io: list[dict] = []

        # R2-F3 fix: All events from the last check_price call (for metrics)
        self._last_check_events: list[dict] = []

        # R3-F3 fix: Counter for wick-filtered events (for metrics propagation)
        self._wick_filtered_count: int = 0

        # R6-2 fix: Lock for sentinel_recent.json read-modify-write
        self._recent_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Stop management
    # ------------------------------------------------------------------

    def update_stops(self, stops: list[StopLevel]) -> None:
        """Replace current stops with a fresh snapshot.

        H-1 fix: Preserves tighter sentinel_stop/highest/lowest if the position
        still exists.  Only resets state for new positions.
        H-2 fix: Clears pending confirmations for positions no longer in stops.
        R2-1 fix: Acquires lock for thread safety.
        """
        with self._lock:
            self._update_stops_unlocked(stops)

    def _update_stops_unlocked(self, stops: list[StopLevel]) -> None:
        """Internal update_stops without lock (caller must hold self._lock)."""
        self._stops_by_token.clear()

        new_position_ids = {s.position_id for s in stops}

        # H-2 fix: clear pending timers for positions no longer in stops
        stale_pids = [pid for pid in self._pending if pid not in new_position_ids]
        for pid in stale_pids:
            del self._pending[pid]

        # Clean up trail state for positions no longer in stops
        for d in (self._sentinel_highest, self._sentinel_lowest, self._sentinel_stop):
            for pid in list(d.keys()):
                if pid not in new_position_ids:
                    del d[pid]

        # Clean up liq warnings and immediate exit dedup for removed positions
        self._liq_warning_fired = self._liq_warning_fired & new_position_ids
        self._immediate_exit_fired = self._immediate_exit_fired & new_position_ids

        for s in stops:
            self._stops_by_token.setdefault(s.token, []).append(s)
            pid = s.position_id

            # F8 fix: Preserve intra-hour trail state across stops.json refreshes.
            # Use max/min so we never lose a sentinel-detected high/low between
            # hourly engine ticks.  New positions (not yet in dict) get stops.json value.
            self._sentinel_highest[pid] = max(
                self._sentinel_highest.get(pid, s.highest), s.highest,
            )
            self._sentinel_lowest[pid] = min(
                self._sentinel_lowest.get(pid, s.lowest), s.lowest,
            )

            # H-1 fix: Preserve tighter sentinel_stop across refreshes
            if pid not in self._sentinel_stop:
                self._sentinel_stop[pid] = s.stop_price
            else:
                # Keep whichever is tighter (closer to current price)
                if s.direction == 1:
                    # Long: tighter = higher stop
                    self._sentinel_stop[pid] = max(self._sentinel_stop[pid], s.stop_price)
                else:
                    # Short: tighter = lower stop
                    self._sentinel_stop[pid] = min(self._sentinel_stop[pid], s.stop_price)

    # ------------------------------------------------------------------
    # Price checking
    # ------------------------------------------------------------------

    def check_price(
        self, token: str, price: float, timestamp_ms: int,
        is_perp: bool | None = None,
    ) -> tuple[Optional[dict], list[dict]]:
        """Check a price update against all stops for *token*.

        Parameters
        ----------
        is_perp : bool | None
            When ``True``, only check perp positions.  When ``False``, only
            check spot positions.  When ``None`` (default), check all —
            preserving backward compatibility.

        Returns
        -------
        (last_event, all_events)
            ``last_event``: the last breach event dict, or None.
            ``all_events``: list of ALL events from this check (R3-F1 fix).

        R2-1 fix: Acquires lock for thread safety.
        R3-F1 fix: Returns all_events atomically under the same lock
        acquisition as the check, preventing TOCTOU races between
        concurrent perp/spot callbacks.
        """
        with self._lock:
            result = self._check_price_unlocked(token, price, timestamp_ms, is_perp=is_perp)
            all_events = list(self._last_check_events)
            # R5-3 fix: Collect deferred I/O records
            deferred = list(self._deferred_io)
            self._deferred_io.clear()
        # Persist outside lock to avoid blocking concurrent check_price calls
        for record in deferred:
            self._persist_exit_record(record)
        return result, all_events

    def _check_price_unlocked(
        self, token: str, price: float, timestamp_ms: int,
        is_perp: bool | None = None,
    ) -> Optional[dict]:
        """Internal check_price without lock (caller must hold self._lock).

        R3-12: Returns the last breach event. All events are processed internally
        (timers started, exits emitted). Multiple events are logged individually.
        """
        stops = self._stops_by_token.get(token, [])
        if is_perp is not None:
            stops = [s for s in stops if s.is_perp == is_perp]
        if not stops:
            self._last_check_events = []
            return None

        result = None
        all_events: list[dict] = []  # R2-F3 fix: accumulate all events
        event_count = 0
        for stop in stops:
            pid = stop.position_id

            # R4-F1 fix: Skip positions that already received an immediate exit
            # this cycle.  Without this guard, a deduped liq/target exit falls
            # through to stop/CB checks, creating a spurious confirmation timer.
            if pid in self._immediate_exit_fired:
                self._update_trail(stop, price)  # still track highs/lows
                continue

            # --- Trail tightening (AC12) ---
            self._update_trail(stop, price)

            # --- Liquidation safety net (AC13) — fires regardless of stop_active ---
            if stop.estimated_liq_price > 0:
                liq_result = self._check_liquidation(stop, price, timestamp_ms)
                if liq_result is not None:
                    if result is not None:
                        event_count += 1
                    all_events.append(liq_result)
                    result = liq_result
                    continue

            # --- Target breach (AC12b) — immediate, no confirmation ---
            # Validate target is in the profitable direction:
            #   Long: target > entry (take profit above)
            #   Short: target < entry (take profit below)
            target_valid = (stop.target_price > 0 and stop.stop_active and
                            ((stop.direction == 1 and stop.target_price > stop.entry_price) or
                             (stop.direction == -1 and stop.target_price < stop.entry_price)))
            if target_valid:
                if self._is_target_breached(stop, price):
                    event = self._emit_immediate_exit(
                        stop, price, timestamp_ms, exit_reason="sentinel_target",
                        event_type="target_breach",
                    )
                    if event is not None:
                        if result is not None:
                            event_count += 1
                        all_events.append(event)
                        result = event
                    continue

            # Check for recovery first (wick filter)
            if pid in self._pending:
                if self._is_recovered(stop, price):
                    pending = self._pending.pop(pid)
                    duration_s = (timestamp_ms / 1000.0) - pending["breach_time_s"]
                    # L-4 fix: cap wick_filtered list to prevent unbounded growth
                    if len(self._wick_filtered) > 1000:
                        self._wick_filtered = self._wick_filtered[-500:]
                    self._wick_filtered.append({
                        "position_id": pid,
                        "token": token,
                        "breach_price": pending["breach_price"],
                        "recovery_price": price,
                        "duration_s": duration_s,
                        "liquidity_tier": self._get_tier(token),
                    })
                    self._wick_filtered_count += 1  # R3-F3 fix
                continue  # Already pending — don't re-trigger

            # Check circuit breaker (fires regardless of stop_active)
            # CB must be in the adverse direction relative to stop:
            #   Long: cb_price < stop_price (deeper loss)
            #   Short: cb_price > stop_price (higher price = deeper loss)
            cur_stop = self._sentinel_stop.get(pid, stop.stop_price)
            cb_valid = (stop.cb_price > 0 and
                        ((stop.direction == 1 and stop.cb_price < cur_stop) or
                         (stop.direction == -1 and stop.cb_price > cur_stop)))
            if cb_valid:
                if self._is_cb_breached(stop, price):
                    event = self._start_confirmation(
                        stop, price, timestamp_ms, exit_reason="sentinel_cb",
                    )
                    if result is not None:
                        event_count += 1
                    all_events.append(event)
                    result = event
                    continue

            # Check normal stop (only when stop_active) — use tightened sentinel_stop
            if stop.stop_active:
                cur_stop = self._sentinel_stop.get(pid, stop.stop_price)
                if self._is_stop_breached_at(stop.direction, cur_stop, price):
                    event = self._start_confirmation(
                        stop, price, timestamp_ms, exit_reason="sentinel_stop",
                    )
                    if result is not None:
                        event_count += 1
                    all_events.append(event)
                    result = event

        # R2-F3 fix: Store all events for callers that need complete metrics
        self._last_check_events = all_events

        # R3-12: Log when multiple breaches detected for same token in one tick
        if event_count > 0:
            logger.info(
                "Multiple breach events (%d) for %s in single tick at price %.2f",
                event_count + 1, token, price,
            )

        return result

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    def confirm_pending_exits(
        self,
        current_time_s: float | None = None,
        latest_prices: dict[str, float] | None = None,
        perp_prices: dict[str, float] | None = None,
        spot_prices: dict[str, float] | None = None,
    ) -> list[dict]:
        """Check pending confirmations and emit exits for those past their delay.

        C-4 fix: Uses latest_prices (current mark prices) as exit_price instead
        of the stale breach_price from timer start.
        R2-1 fix: Acquires lock for thread safety.

        Parameters
        ----------
        latest_prices : dict | None
            Backward-compat merged prices (used when perp_prices/spot_prices
            are not provided).
        perp_prices : dict | None
            Perp mark prices keyed by token.
        spot_prices : dict | None
            Spot last prices keyed by token.

        Returns list of confirmed exit event dicts.
        """
        with self._lock:
            result = self._confirm_pending_exits_unlocked(
                current_time_s, latest_prices,
                perp_prices=perp_prices, spot_prices=spot_prices,
            )
            # R5-3 fix: Collect deferred I/O records
            deferred = list(self._deferred_io)
            self._deferred_io.clear()
        # Persist outside lock
        for record in deferred:
            self._persist_exit_record(record)
        return result

    def _confirm_pending_exits_unlocked(
        self,
        current_time_s: float | None = None,
        latest_prices: dict[str, float] | None = None,
        perp_prices: dict[str, float] | None = None,
        spot_prices: dict[str, float] | None = None,
    ) -> list[dict]:
        """Internal confirm_pending_exits without lock."""
        now = current_time_s if current_time_s is not None else time.time()
        prices = latest_prices or {}
        perp_p = perp_prices or {}
        spot_p = spot_prices or {}
        confirmed = []
        to_remove = []

        for pid, pending in self._pending.items():
            delay = pending["confirmation_delay_s"]
            elapsed = now - pending["breach_time_s"]
            if elapsed >= delay:
                to_remove.append(pid)
                # Use venue-appropriate latest price as exit_price if available
                token = pending["token"]
                is_perp = pending.get("is_perp", True)
                venue_prices = perp_p if is_perp else spot_p
                if token in venue_prices:
                    pending["exit_price_override"] = venue_prices[token]
                elif token in prices:
                    # Backward-compat fallback: use merged prices
                    pending["exit_price_override"] = prices[token]
                event = self._emit_confirmed_exit(pending)
                confirmed.append(event)

        for pid in to_remove:
            del self._pending[pid]

        return confirmed

    def get_sentinel_stop(self, position_id: str) -> float:
        """Return the current (possibly tightened) sentinel stop for a position."""
        with self._lock:
            return self._sentinel_stop.get(position_id, 0.0)

    def get_sentinel_highest(self, position_id: str) -> float:
        """Return the sentinel-tracked highest price for a position."""
        with self._lock:
            return self._sentinel_highest.get(position_id, 0.0)

    def get_sentinel_lowest(self, position_id: str) -> float:
        """Return the sentinel-tracked lowest price for a position."""
        with self._lock:
            return self._sentinel_lowest.get(position_id, 0.0)

    def get_and_reset_wick_filtered_count(self) -> int:
        """Return wick-filtered count since last call and reset (R3-F3 fix)."""
        with self._lock:
            count = self._wick_filtered_count
            self._wick_filtered_count = 0
            return count

    def has_pending_confirmations(self) -> bool:
        """Return True if there are any pending confirmation timers (R4-6 fix)."""
        with self._lock:
            return bool(self._pending)

    def get_active_confirmations(self) -> list[dict]:
        """Return list of active (pending) confirmation timers."""
        with self._lock:
            return [
                {"position_id": pid, **info}
                for pid, info in self._pending.items()
            ]

    def get_wick_filtered_events(self) -> list[dict]:
        """Return list of wick-filtered events (breaches that recovered)."""
        with self._lock:
            return list(self._wick_filtered)

    def get_cached_position_ids(self) -> set[str]:
        """Return set of position IDs in the sentinel stop cache (R3-5 fix)."""
        with self._lock:
            return set(self._sentinel_stop.keys())

    # ------------------------------------------------------------------
    # Price validation (AC14)
    # ------------------------------------------------------------------

    def validate_exit_price(
        self,
        ws_price: float,
        rest_price: float | None,
        max_divergence_bps: int = 150,
        ws_consistent: bool = True,
    ) -> dict:
        """Validate WS price against REST before confirming exit.

        Returns dict with 'valid' bool and optional 'override', 'divergence_bps'.
        """
        if rest_price is None:
            # REST failed — override only if WS is consistent
            if ws_consistent:
                return {"valid": True, "override": True}
            return {"valid": False, "override": False}

        # H-4 fix: Guard against zero ws_price (avoid division by zero)
        if ws_price <= 0:
            return {"valid": False, "divergence_bps": float("inf")}

        divergence_bps = abs(ws_price - rest_price) / ws_price * 10_000
        if divergence_bps <= max_divergence_bps:
            return {"valid": True, "divergence_bps": divergence_bps}
        return {"valid": False, "divergence_bps": divergence_bps}

    # ------------------------------------------------------------------
    # Cascade logging (AC15)
    # ------------------------------------------------------------------

    def record_exit_timestamp(self, timestamp_s: float) -> None:
        """Record an exit timestamp for cascade detection.

        M-4 fix: Only emit cascade event if we haven't already fired one
        in this window (dedup). L-4 fix: Cap exit_timestamps list.
        R2-1 fix: Acquires lock for thread safety.
        """
        with self._lock:
            self._record_exit_timestamp_unlocked(timestamp_s)

    def _record_exit_timestamp_unlocked(self, timestamp_s: float) -> None:
        """Internal record_exit_timestamp without lock."""
        self._exit_timestamps.append(timestamp_s)
        # Prune old timestamps outside the window
        cutoff = timestamp_s - self._cascade_window_s
        self._exit_timestamps = [
            t for t in self._exit_timestamps if t >= cutoff
        ]
        # Check for cascade — M-4: only fire if no cascade in this window
        if (len(self._exit_timestamps) >= self._cascade_threshold
                and timestamp_s - self._last_cascade_ts >= self._cascade_window_s):
            self._last_cascade_ts = timestamp_s
            # L-5 fix: cap cascade_events list to prevent unbounded growth
            if len(self._cascade_events) > 1000:
                self._cascade_events = self._cascade_events[-500:]
            self._cascade_events.append({
                "event_type": "cascade_event",
                "exit_count": len(self._exit_timestamps),
                "window_s": self._cascade_window_s,
                "timestamp": timestamp_s,
            })

    def get_cascade_events(self) -> list[dict]:
        """Return list of cascade events. R4-1 fix: Acquires lock."""
        with self._lock:
            return list(self._cascade_events)

    # ------------------------------------------------------------------
    # Liquidity tier
    # ------------------------------------------------------------------

    def _get_tier(self, token: str) -> str:
        """Classify token into liquidity tier."""
        if token in _BTC_ETH_TOKENS:
            return "btc_eth"
        if token in _TOP10_TOKENS:
            return "top10"
        return "other"

    def _get_confirmation_delay(self, token: str) -> int:
        """Get confirmation delay in seconds for a token's tier."""
        tier = self._get_tier(token)
        return self._confirmation_tiers.get(tier, self._confirmation_tiers.get("other", 90))

    # ------------------------------------------------------------------
    # Breach logic
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Trail tightening (AC12)
    # ------------------------------------------------------------------

    def _update_trail(self, stop: StopLevel, price: float) -> None:
        """Update sentinel_highest/lowest and tighten sentinel_stop.

        Guards: skip if convex_exit, has_trail_schedule, or chandelier_lookback > 0.
        R5-2 fix: math imported at module level (was per-call on hot path).
        """
        pid = stop.position_id

        # R3-8 fix: NaN guard — corrupted data would permanently poison trail state
        if math.isnan(price) or math.isnan(stop.highest) or math.isnan(stop.lowest):
            return
        if math.isnan(stop.cur_atr) or math.isnan(stop.trail_mult):
            return

        # Guard conditions — don't tighten
        if stop.convex_exit or stop.has_trail_schedule or stop.chandelier_lookback > 0:
            return

        # M-1/M-2 fix: Skip if ATR or trail_mult is zero/negative (no meaningful trail)
        if stop.cur_atr <= 0 or stop.trail_mult <= 0:
            return

        if stop.direction == 1:
            # Long: track highest, tighten stop upward only on genuine new highs
            cur_highest = self._sentinel_highest.get(pid, stop.highest)
            new_highest = max(cur_highest, price)
            self._sentinel_highest[pid] = new_highest

            if new_highest > cur_highest:
                trail_stop = new_highest - stop.trail_mult * stop.cur_atr
                cur_stop = self._sentinel_stop.get(pid, stop.stop_price)
                self._sentinel_stop[pid] = max(cur_stop, trail_stop)
        else:
            # Short: track lowest, tighten stop downward only on genuine new lows
            cur_lowest = self._sentinel_lowest.get(pid, stop.lowest)
            new_lowest = min(cur_lowest, price)
            self._sentinel_lowest[pid] = new_lowest

            if new_lowest < cur_lowest:
                trail_stop = new_lowest + stop.trail_mult * stop.cur_atr
                cur_stop = self._sentinel_stop.get(pid, stop.stop_price)
                self._sentinel_stop[pid] = min(cur_stop, trail_stop)

    # ------------------------------------------------------------------
    # Target breach (AC12b)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_target_breached(stop: StopLevel, price: float) -> bool:
        """Long: price >= target.  Short: price <= target."""
        if stop.direction == 1:
            return price >= stop.target_price
        return price <= stop.target_price

    def _emit_immediate_exit(
        self,
        stop: StopLevel,
        price: float,
        timestamp_ms: int,
        exit_reason: str,
        event_type: str = "breach",
    ) -> dict | None:
        """Emit an exit event immediately (no confirmation delay).

        R5-1 fix: Dedup — only fires once per position per stops refresh cycle.
        Also clears any pending confirmation for this position.
        """
        pid = stop.position_id
        if pid in self._immediate_exit_fired:
            return None

        self._immediate_exit_fired.add(pid)
        # Clear any pending confirmation for this position (avoid double exit)
        self._pending.pop(pid, None)

        # R3-7 fix: Use tightened sentinel_stop, not original stop_price
        effective_stop = self._sentinel_stop.get(pid, stop.stop_price)
        pending = {
            "position_id": stop.position_id,
            "token": stop.token,
            "direction": stop.direction,
            "stop_price": effective_stop,
            "breach_price": price,
            "breach_time_s": timestamp_ms / 1000.0,
            "breach_timestamp_ms": timestamp_ms,
            "confirmation_delay_s": 0,
            "exit_reason": exit_reason,
            "entry_price": stop.entry_price,
            "margin_usd": stop.margin_usd,
            "quantity": stop.quantity,
            "fee_rate": stop.fee_rate,
            "liquidity_tier": self._get_tier(stop.token),
            "cur_atr": stop.cur_atr,
            "is_perp": stop.is_perp,
        }
        # R2-F8 fix: Defensive try/except so record-building errors don't crash callback
        try:
            self._emit_confirmed_exit(pending)
        except Exception as exc:
            logger.error("Failed to build exit record for %s: %s", pid, exc)
            return None
        return {
            "event_type": event_type,
            "position_id": stop.position_id,
            "token": stop.token,
            "breach_price": price,
            "exit_reason": exit_reason,
        }

    # ------------------------------------------------------------------
    # Liquidation safety net (AC13)
    # ------------------------------------------------------------------

    def _check_liquidation(
        self, stop: StopLevel, price: float, timestamp_ms: int,
    ) -> Optional[dict]:
        """Check liquidation safety net (fires regardless of stop_active).

        90% zone: warning event.
        100% (price at or past liq): immediate emergency exit.
        """
        pid = stop.position_id
        liq_price = stop.estimated_liq_price
        entry_price = stop.entry_price

        # Validate liq price is in the adverse direction for this position:
        #   Long: liq_price < entry_price (danger is price falling)
        #   Short: liq_price > entry_price (danger is price rising)
        if stop.direction == 1 and liq_price >= entry_price:
            return None
        if stop.direction == -1 and liq_price <= entry_price:
            return None

        # Distance from entry to liq
        distance = abs(entry_price - liq_price)
        if distance <= 0:
            return None

        # How far price has moved toward liq (as fraction of total distance)
        if stop.direction == 1:
            # Long: danger when price drops toward liq
            moved = entry_price - price
            at_or_past_liq = price <= liq_price
        else:
            # Short: danger when price rises toward liq
            moved = price - entry_price
            at_or_past_liq = price >= liq_price

        pct_toward_liq = moved / distance if distance > 0 else 0

        # 100% — emergency exit (no confirmation)
        if at_or_past_liq:
            return self._emit_immediate_exit(
                stop, price, timestamp_ms,
                exit_reason="sentinel_liq",
                event_type="liq_emergency",
            )

        # 90% — warning (only fire once per position per stops refresh)
        if pct_toward_liq >= 0.90 and pid not in self._liq_warning_fired:
            self._liq_warning_fired.add(pid)
            return {
                "event_type": "liq_warning",
                "position_id": pid,
                "token": stop.token,
                "breach_price": price,
                "exit_reason": "sentinel_liq",
                "pct_toward_liq": pct_toward_liq,
            }

        return None

    # ------------------------------------------------------------------
    # Breach logic
    # ------------------------------------------------------------------

    @staticmethod
    def _is_cb_breached(stop: StopLevel, price: float) -> bool:
        """Long: price < cb_price.  Short: price > cb_price."""
        if stop.direction == 1:
            return price < stop.cb_price
        return price > stop.cb_price

    @staticmethod
    def _is_stop_breached_at(direction: int, stop_price: float, price: float) -> bool:
        """Check if price breached a given stop level."""
        if direction == 1:
            return price < stop_price
        return price > stop_price

    def _is_recovered(self, stop: StopLevel, price: float) -> bool:
        """Long: recovered if price >= sentinel_stop.  Short: price <= sentinel_stop.

        Uses the tightened sentinel_stop (C-2 fix), not the original stop_price.
        """
        cur_stop = self._sentinel_stop.get(stop.position_id, stop.stop_price)
        if stop.direction == 1:
            return price >= cur_stop
        return price <= cur_stop

    # ------------------------------------------------------------------
    # Timer management
    # ------------------------------------------------------------------

    def _start_confirmation(
        self,
        stop: StopLevel,
        breach_price: float,
        timestamp_ms: int,
        exit_reason: str,
    ) -> dict:
        """Start a confirmation timer for a breached stop."""
        breach_time_s = timestamp_ms / 1000.0
        delay = self._get_confirmation_delay(stop.token)

        # M-8 fix: Store the tightened sentinel_stop, not the original stop_price
        effective_stop = self._sentinel_stop.get(stop.position_id, stop.stop_price)

        self._pending[stop.position_id] = {
            "position_id": stop.position_id,
            "token": stop.token,
            "direction": stop.direction,
            "stop_price": effective_stop,
            "breach_price": breach_price,
            "breach_time_s": breach_time_s,
            "breach_timestamp_ms": timestamp_ms,
            "confirmation_delay_s": delay,
            "exit_reason": exit_reason,
            "entry_price": stop.entry_price,
            "margin_usd": stop.margin_usd,
            "quantity": stop.quantity,
            "fee_rate": stop.fee_rate,
            "liquidity_tier": self._get_tier(stop.token),
            "cur_atr": stop.cur_atr,
            "is_perp": stop.is_perp,
        }

        return {
            "event_type": "breach",
            "position_id": stop.position_id,
            "token": stop.token,
            "breach_price": breach_price,
            "exit_reason": exit_reason,
        }

    # ------------------------------------------------------------------
    # Exit emission
    # ------------------------------------------------------------------

    def _build_exit_record(self, pending: dict) -> dict:
        """Build exit event record dict from pending confirmation data.

        Pure computation — no I/O, safe to call under lock.
        """
        now = time.time()
        breach_ts = datetime.fromtimestamp(
            pending["breach_time_s"], tz=timezone.utc,
        ).isoformat()
        confirm_ts = datetime.now(timezone.utc).isoformat()

        # R2-F5 fix: Record actual elapsed confirmation time, not just tier delay
        actual_confirmation_time_s = now - pending["breach_time_s"]

        # Slippage estimate from ATR
        slippage_bps = 0.0
        if pending["cur_atr"] > 0 and pending["entry_price"] > 0:
            slippage_bps = (pending["cur_atr"] / pending["entry_price"]) * 10000 * 0.01

        # Use latest price at confirmation time, not stale breach price
        exit_price = pending.get("exit_price_override", pending["breach_price"])

        return {
            "position_id": pending["position_id"],
            "portfolio": self.portfolio_name,
            "token": pending["token"],
            "direction": pending["direction"],
            "event_type": "breach_confirmed",
            "exit_reason": pending["exit_reason"],
            "entry_price": pending["entry_price"],
            "stop_price": pending["stop_price"],
            "breach_price": pending["breach_price"],
            "exit_price": exit_price,
            "confirmation_delay_s": pending["confirmation_delay_s"],
            "actual_confirmation_time_s": round(actual_confirmation_time_s, 3),
            "liquidity_tier": pending["liquidity_tier"],
            "slippage_bps": slippage_bps,
            "margin_usd": pending["margin_usd"],
            "quantity": pending["quantity"],
            "timestamp": confirm_ts,
            "_breach_ts": breach_ts,  # Internal: used for ExitEvent construction
        }

    def _persist_exit_record(self, event_record: dict) -> None:
        """Write exit record to shadow log, sentinel_recent, and (live) exit_events.

        R5-3 fix: Separated from _build_exit_record so I/O happens outside lock.
        R4-2 fix: Shadow/recent failures don't block live exit.
        """
        # Always log to shadow JSONL (both shadow and live modes)
        try:
            self._append_shadow_log(event_record)
        except Exception as exc:
            logger.error("Shadow log write failed: %s", exc)
        try:
            self._update_sentinel_recent(event_record)
        except Exception as exc:
            logger.error("Sentinel recent update failed: %s", exc)

        # Live mode: also write exit event for the hourly engine
        # R6-1 fix: Wrap in try/except so one failed write doesn't abort the batch
        if self.sentinel_mode == "live":
            try:
                breach_ts = event_record.get("_breach_ts", "")
                confirm_ts = event_record.get("timestamp", "")
                exit_event = ExitEvent(
                    position_id=event_record["position_id"],
                    token=event_record["token"],
                    direction=event_record["direction"],
                    exit_price=event_record["exit_price"],
                    stop_price=event_record["stop_price"],
                    breach_price=event_record["breach_price"],
                    breach_timestamp=breach_ts,
                    confirm_timestamp=confirm_ts,
                    slippage_bps=event_record["slippage_bps"],
                    sentinel_timestamp=confirm_ts,
                    margin_usd=event_record["margin_usd"],
                    quantity=event_record["quantity"],
                    entry_price=event_record["entry_price"],
                    exit_reason=event_record["exit_reason"],
                )
                self.stop_store.append_exit_event(exit_event)
            except Exception as exc:
                logger.error(
                    "CRITICAL: Failed to write exit event for %s: %s",
                    event_record.get("position_id", "?"), exc,
                )

    def _emit_confirmed_exit(self, pending: dict) -> dict:
        """Build exit record and queue for deferred I/O (R5-3 fix).

        Returns the event_record dict. I/O happens when the calling public
        method drains _deferred_io outside the lock.
        """
        record = self._build_exit_record(pending)
        self._deferred_io.append(record)
        return record

    def _append_shadow_log(self, event: dict) -> None:
        """Append event to sentinel_shadow.jsonl.

        R3-13 fix: fsync for crash safety.
        """
        path = self._state_dir / "sentinel_shadow.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _update_sentinel_recent(self, event: dict) -> None:
        """Update sentinel_recent.json ring buffer (max 50 entries).

        H-7 fix: Atomic write-tmp-rename to prevent partial reads by dashboard.
        R6-2 fix: Serialized with _recent_lock to prevent concurrent read-modify-write races.
        """
        import tempfile

        with self._recent_lock:
            path = self._state_dir / "sentinel_recent.json"
            existing: list[dict] = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    existing = []

            # Shadow mode dedup: skip if position_id already in buffer
            pid = event.get("position_id", "")
            if pid and any(e.get("position_id") == pid for e in existing):
                return  # Already logged first trigger for this position

            existing.append(event)
            # Cap at ring buffer max
            if len(existing) > RING_BUFFER_MAX:
                existing = existing[-RING_BUFFER_MAX:]

            # Atomic write via tmp file + rename
            fd, tmp_path = tempfile.mkstemp(
                dir=self._state_dir, prefix=".recent_", suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(existing, f, indent=2)
                os.replace(tmp_path, path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
