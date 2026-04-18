"""M4 — PendingEntry state machine primitives (AC13/AC32).

Wave A delivered the enums. T8 adds the frozen dataclass + the ``arm()``
factory so downstream T9 (state transitions) and T10 (persistence) can build
on a stable construction contract.

State-transition methods (``on_price``, ``release``, ``on_fill``, ``cancel``,
``check_expiry``) and the ``resolve_contention`` helper land in T9.
JSON persistence (``to_json`` / ``from_json``) lands in T10.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import IntEnum
from typing import Any, Iterable, Literal


_LOGGER = logging.getLogger(__name__)

# AC38 — de-dup set for the mark-trigger backtest audit log entry. Keyed by
# ``id(pe)`` so multiple ``on_backtest_bar`` calls against the same
# PendingEntry instance emit exactly one log line per position. Distinct
# PendingEntry instances (one per position) each emit their own entry.
_MARK_AUDIT_EMITTED: set[int] = set()


class TriggerKind(IntEnum):
    """Trigger predicate for a PendingEntry (AC13).

    Reserved for M8 scope (do not use in M4):
      STOP_LIMIT = 7
      TRAILING   = 8
    """
    PRICE_ABOVE = 1   # long entry / short cover — high >= level
    PRICE_BELOW = 2   # short entry / long cover — low <= level
    MARK_ABOVE  = 3   # long entry vs mark (perps); backtest fallback to close per AC38
    MARK_BELOW  = 4   # short entry vs mark
    TIME_AT     = 5   # fire at wall-clock timestamp
    BAR_CLOSE   = 6   # fire on next bar close of declared entry/exit resolution


class PendingState(IntEnum):
    """PendingEntry lifecycle state (AC32).

    Reserved for M5 scope (do not use in M4):
      PENDING_CANCEL = 9
      REPLACED       = 10
      SUSPENDED      = 11
    """
    ARMED            = 1   # active, watching market (FIX OrdStatus=0 New, locally emulated)
    TRIGGERED        = 2   # predicate hit; constraint check pending
    RELEASED         = 3   # constraint passed; handed to fill path (FIX ExecType=L Triggered)
    PARTIALLY_FILLED = 4   # partial fill received (FIX OrdStatus=1); leaves_qty > 0
    FILLED           = 5   # terminal fill (FIX OrdStatus=2)
    EXPIRED          = 6   # TIF elapsed without trigger
    REJECTED         = 7   # constraint failed at RELEASE or trigger-time validation
    CANCELLED        = 8   # explicitly cancelled (signal flip, etc.)


# For M4 SizingContext is a plain mapping; M7 formalises the typed structure.
SizingContext = Any


@dataclass(frozen=True, slots=True)
class PendingEntry:
    """Frozen state record for an armed entry (AC13, AC32).

    Transitions are implemented as methods returning a new ``PendingEntry``
    instance (since the dataclass is frozen). T8 ships construction + factory;
    T9 adds transition methods; T10 adds ``to_json`` / ``from_json``.

    Priority ordering for capital contention (AC39) is lexicographic on
    ``(armed_at, strategy_id, token)`` — oldest-armed wins.
    """

    strategy_id: str
    token: str
    direction: Literal[-1, 1]
    trigger: TriggerKind
    trigger_price: float
    working_price_source: Literal["last", "mark", "bar_hl"]
    armed_at: datetime                 # AC25 priority key
    expires_at: datetime | None
    sizing_ctx: SizingContext          # recomputed at RELEASE (dict in M4)
    state: PendingState
    filled_qty: float = 0.0            # PARTIALLY_FILLED accounting
    leaves_qty: float = 0.0
    reject_reason: str | None = None
    strategy_params: dict = field(default_factory=dict)
    window_end: float = 0.0            # 4H window epoch

    @classmethod
    def arm(
        cls,
        *,
        strategy_id: str,
        token: str,
        direction: Literal[-1, 1],
        trigger: TriggerKind,
        trigger_price: float,
        working_price_source: Literal["last", "mark", "bar_hl"],
        armed_at: datetime,
        expires_at: datetime | None,
        sizing_ctx: SizingContext,
        strategy_params: dict | None = None,
        window_end: float = 0.0,
    ) -> "PendingEntry":
        """Construct a new PendingEntry in the ARMED state.

        This is the single allowed entry point for creating a PendingEntry —
        callers must never construct ``PendingEntry(...)`` directly with a
        custom state. All subsequent state changes go through the transition
        methods (T9).
        """
        return cls(
            strategy_id=strategy_id,
            token=token,
            direction=direction,
            trigger=trigger,
            trigger_price=trigger_price,
            working_price_source=working_price_source,
            armed_at=armed_at,
            expires_at=expires_at,
            sizing_ctx=sizing_ctx,
            state=PendingState.ARMED,
            filled_qty=0.0,
            leaves_qty=0.0,
            reject_reason=None,
            strategy_params=dict(strategy_params) if strategy_params else {},
            window_end=window_end,
        )

    # ------------------------------------------------------------------ #
    # Transition methods (T9 — minimal set required by T10 persistence   #
    # tests; full T9 expansion lands later and is expected to extend,    #
    # not replace, these helpers).                                       #
    # ------------------------------------------------------------------ #

    def on_price(self, price: float) -> "PendingEntry":
        """ARMED -> TRIGGERED when the trigger predicate matches the price.

        Implements predicate evaluation for PRICE_ABOVE/PRICE_BELOW and
        MARK_ABOVE/MARK_BELOW (for M4, mark is treated identically to price
        per AC38 backtest fallback). Non-ARMED inputs are returned unchanged
        so downstream chains (``pe.on_price(...).release(...)``) remain safe.
        """
        if self.state != PendingState.ARMED:
            return self
        hit = False
        t = self.trigger
        if t in (TriggerKind.PRICE_ABOVE, TriggerKind.MARK_ABOVE):
            hit = float(price) >= float(self.trigger_price)
        elif t in (TriggerKind.PRICE_BELOW, TriggerKind.MARK_BELOW):
            hit = float(price) <= float(self.trigger_price)
        else:
            # TIME_AT / BAR_CLOSE — price alone does not trigger
            hit = False
        if not hit:
            return self
        return replace(self, state=PendingState.TRIGGERED)

    def release(self, *, capital_ok: bool) -> "PendingEntry":
        """TRIGGERED -> RELEASED (constraint pass) or REJECTED (fail).

        ``reject_reason`` is set to ``"risk_on_release"`` when the capital
        constraint check fails at RELEASE — this matches AC39's contention
        semantics (sizing-vs-available-capital failure).
        """
        if self.state != PendingState.TRIGGERED:
            return self
        if capital_ok:
            return replace(self, state=PendingState.RELEASED)
        return replace(
            self,
            state=PendingState.REJECTED,
            reject_reason="risk_on_release",
        )

    def on_fill(self, *, filled_qty: float, leaves_qty: float) -> "PendingEntry":
        """RELEASED/PARTIALLY_FILLED -> PARTIALLY_FILLED / FILLED.

        Terminal when ``leaves_qty == 0``; otherwise PARTIALLY_FILLED carries
        the outstanding quantity forward across restarts (AC32).
        """
        if self.state not in (PendingState.RELEASED, PendingState.PARTIALLY_FILLED):
            return self
        filled = float(filled_qty)
        leaves = float(leaves_qty)
        new_state = (
            PendingState.FILLED if leaves <= 0.0 else PendingState.PARTIALLY_FILLED
        )
        return replace(
            self, state=new_state, filled_qty=filled, leaves_qty=leaves,
        )

    def cancel(self) -> "PendingEntry":
        """Any non-terminal state -> CANCELLED (e.g. signal flip)."""
        terminal = {
            PendingState.FILLED, PendingState.EXPIRED,
            PendingState.REJECTED, PendingState.CANCELLED,
        }
        if self.state in terminal:
            return self
        return replace(self, state=PendingState.CANCELLED)

    # ------------------------------------------------------------------ #
    # AC38 — Mark-trigger backtest fallback                              #
    # ------------------------------------------------------------------ #

    def on_backtest_bar(self, *, close: float) -> "PendingEntry":
        """AC38 T-B30 — evaluate a MARK_* trigger against the bar close.

        In live trading mark-price triggers consume the venue mark (separate
        from the last trade price). Backtests do not carry a mark feed, so
        per AC38 we fall back to ``bar_ctx.close`` and emit a single audit
        log entry per (token, armed_at) tuple so downstream analyses can
        reconcile live vs backtest fills.

        The entry is idempotent — repeated calls on the same (token,
        armed_at) position only emit the audit log line once, matching the
        AC38 invariant "exactly one log entry per position". Non-mark
        working price sources fall straight through to :meth:`on_price`.
        """
        if self.working_price_source == "mark":
            key = id(self)
            if key not in _MARK_AUDIT_EMITTED:
                _MARK_AUDIT_EMITTED.add(key)
                _LOGGER.info(
                    "AC38 mark-trigger fallback: token=%s strategy=%s "
                    "using bar close=%.10g in lieu of mark (position=%s)",
                    self.token,
                    self.strategy_id,
                    float(close),
                    self.token,
                )
        return self.on_price(float(close))

    def check_expiry(self, *, now: datetime) -> "PendingEntry":
        """ARMED + now >= expires_at -> EXPIRED. Triggered PEs are immune."""
        if self.state != PendingState.ARMED:
            return self
        if self.expires_at is None:
            return self
        if now >= self.expires_at:
            return replace(self, state=PendingState.EXPIRED)
        return self

    # ------------------------------------------------------------------ #
    # JSON persistence (T10, AC32)                                       #
    # ------------------------------------------------------------------ #

    def to_json(self) -> dict:
        """Serialize to a JSON-compatible dict (AC32).

        Enum fields are stored as string names (stable across Python/enum-value
        changes). Datetime fields use ISO 8601 with timezone. ``None`` for
        ``expires_at`` round-trips correctly.
        """
        return {
            "strategy_id": self.strategy_id,
            "token": self.token,
            "direction": int(self.direction),
            "trigger": self.trigger.name,
            "trigger_price": float(self.trigger_price),
            "working_price_source": self.working_price_source,
            "armed_at": self.armed_at.isoformat() if self.armed_at is not None else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
            "sizing_ctx": _sizing_ctx_to_json(self.sizing_ctx),
            "state": self.state.name,
            "filled_qty": float(self.filled_qty),
            "leaves_qty": float(self.leaves_qty),
            "reject_reason": self.reject_reason,
            "strategy_params": dict(self.strategy_params)
                if self.strategy_params else {},
            "window_end": float(self.window_end),
        }

    @classmethod
    def from_json(cls, blob: dict) -> "PendingEntry":
        """Deserialize from a dict produced by :meth:`to_json`.

        ``from_json(to_json(pe)) == pe`` is idempotent; all 15 fields are
        restored including TriggerKind/PendingState enums (by name) and
        datetime fields (ISO 8601).
        """
        trigger_raw = blob["trigger"]
        if isinstance(trigger_raw, str):
            trigger = TriggerKind[trigger_raw]
        else:
            trigger = TriggerKind(int(trigger_raw))
        state_raw = blob.get("state", PendingState.ARMED.name)
        if isinstance(state_raw, str):
            state = PendingState[state_raw]
        else:
            state = PendingState(int(state_raw))
        armed_at_raw = blob.get("armed_at")
        armed_at = _parse_iso_dt(armed_at_raw)
        expires_at_raw = blob.get("expires_at")
        expires_at = _parse_iso_dt(expires_at_raw) if expires_at_raw else None
        return cls(
            strategy_id=blob["strategy_id"],
            token=blob["token"],
            direction=int(blob["direction"]),
            trigger=trigger,
            trigger_price=float(blob["trigger_price"]),
            working_price_source=blob["working_price_source"],
            armed_at=armed_at,
            expires_at=expires_at,
            sizing_ctx=blob.get("sizing_ctx"),
            state=state,
            filled_qty=float(blob.get("filled_qty", 0.0)),
            leaves_qty=float(blob.get("leaves_qty", 0.0)),
            reject_reason=blob.get("reject_reason"),
            strategy_params=dict(blob.get("strategy_params") or {}),
            window_end=float(blob.get("window_end", 0.0)),
        )


# ---------------------------------------------------------------------------
# Module-level helpers (T10)
# ---------------------------------------------------------------------------


def _parse_iso_dt(raw):
    """Parse an ISO 8601 string back into a timezone-aware datetime.

    Returns None for falsy input. Naive strings are coerced to UTC to match
    the ``arm()`` factory's convention (``datetime.fromisoformat`` preserves
    tzinfo when present).
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        dt = dt.replace(tzinfo=_tz.utc)
    return dt


def _sizing_ctx_to_json(ctx):
    """Best-effort conversion of the sizing context to a JSON-native structure.

    M4 uses plain dicts; M7 formalises a typed structure. Non-dict inputs
    (``object()`` in some unit tests) are preserved by returning ``None`` so
    downstream ``from_json`` rebuilds a neutral value rather than crashing.
    """
    if ctx is None:
        return None
    if isinstance(ctx, dict):
        return {k: ctx[k] for k in ctx}
    # Non-dict sizing_ctx (e.g. sentinel object()) is not meaningfully
    # serializable; drop to None so round-trip stays JSON-clean.
    return None


# ---------------------------------------------------------------------- #
# AC25 step 6 / AC39 — multi-PendingEntry capital contention priority
# ---------------------------------------------------------------------- #


def _priority_key(pe: "PendingEntry") -> tuple:
    """Lexicographic priority: (armed_at, strategy_id, token). Oldest wins."""
    return (pe.armed_at, pe.strategy_id, pe.token)


def _margin_usd(pe: "PendingEntry") -> float:
    """Extract margin_usd from sizing_ctx; 0.0 if unavailable (count-based mode)."""
    ctx = pe.sizing_ctx
    if isinstance(ctx, dict):
        try:
            return float(ctx.get("margin_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0
    getter = getattr(ctx, "get", None)
    if callable(getter):
        try:
            return float(getter("margin_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def resolve_contention(
    entries: Iterable["PendingEntry"],
    available_capital_usd: float | None = None,
    available_capital: int | None = None,
) -> list["PendingEntry"]:
    """Resolve capital contention among TRIGGERED PendingEntries (AC25, AC39).

    Priority: ``(armed_at, strategy_id, token)`` ascending — oldest-armed wins.

    Two modes:
      * ``available_capital_usd`` (float): greedy-fit by ``sizing_ctx["margin_usd"]``.
        Winners transition TRIGGERED -> RELEASED -> FILLED; losers transition
        TRIGGERED -> REJECTED with reason ``"risk_on_release"``.
      * ``available_capital`` (int): count-based cap. First N by priority win.

    Returns all entries in original input order with final states applied.
    """
    entries_list = list(entries)
    if not entries_list:
        return []

    # Sort by priority for allocation decisions, but preserve original order
    # in the return value.
    ordered = sorted(entries_list, key=_priority_key)

    winner_ids: set[int] = set()  # id() of PendingEntry winners
    if available_capital_usd is not None:
        remaining = float(available_capital_usd)
        for pe in ordered:
            cost = _margin_usd(pe)
            if cost <= remaining:
                winner_ids.add(id(pe))
                remaining -= cost
    elif available_capital is not None:
        cap = int(available_capital)
        for pe in ordered[:cap]:
            winner_ids.add(id(pe))
    else:
        raise ValueError(
            "resolve_contention requires available_capital_usd or available_capital"
        )

    results: list[PendingEntry] = []
    for pe in entries_list:
        if pe.state != PendingState.TRIGGERED:
            # Only TRIGGERED entries participate in contention; pass through.
            results.append(pe)
            continue
        if id(pe) in winner_ids:
            released = pe.release(capital_ok=True)
            # Winners treat margin_usd as fully filled (leaves_qty=0).
            filled = released.on_fill(
                filled_qty=float(_margin_usd(pe)),
                leaves_qty=0.0,
            )
            results.append(filled)
        else:
            # ``release(capital_ok=False)`` already sets
            # ``reject_reason="risk_on_release"`` per AC39 — pass only the
            # supported kwarg.
            results.append(pe.release(capital_ok=False))
    return results
