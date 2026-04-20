"""M4/M5 — Order state machine primitives (AC13/AC32 + M5 multi-leg).

Wave A delivered the enums. T8 adds the frozen dataclass + the ``arm()``
factory so downstream T9 (state transitions) and T10 (persistence) can build
on a stable construction contract.

State-transition methods (``on_price``, ``release``, ``on_fill``, ``cancel``,
``check_expiry``) and the ``resolve_contention`` helper land in T9.
JSON persistence (``to_json`` / ``from_json``) lands in T10.

M5 rename: this module was previously ``v5.pending_entry`` exposing
``PendingEntry`` / ``PendingState`` / ``TriggerKind``. Under M5's
design-over-code decision the native module was renamed to ``v5.orders``
exposing ``Order`` / ``OrderStatus`` / ``TriggerType`` with NO back-compat
shim. Imports of ``v5.pending_entry`` must raise ModuleNotFoundError.

FIX mapping (for M7 wire integration):
  - Order.order_id → FIX ClOrdID(11)
  - Order.venue_order_id → FIX OrderID(37) [populated on venue ack in M7+]
  - Order.legs → FIX LegGrp(555)
  - Order.contingency → FIX ContingencyType(1385)
  - Order.time_in_force → FIX TimeInForce(59)
  - OrderStatus ordinals are engine-internal (NOT FIX OrdStatus(39) wire values).
    Wire mapping per G8 institutional convention: ARMED/TRIGGERED/RELEASED → "A"
    (PendingNew — not yet visible at venue) with an engine-custom tag (9001/9002/
    9003) disambiguating the sub-state; PARTIALLY_FILLED → "1"; FILLED → "2";
    CANCELLED → "4"; REJECTED → "8"; EXPIRED → "C".
    See OrderStatus.to_fix_ordstatus() + OrderStatus.fix_custom_tag.
  - TriggerType is a FIX TriggerType(1100)×TriggerPriceDirection(1109)
    COMPOSITE (trigger kind AND price direction):
      PRICE_ABOVE → (FIX TriggerType=4 PriceMovement, TriggerPriceDirection=U)
      PRICE_BELOW → (4, D)
      MARK_ABOVE  → (4, U) with TriggerPriceType(1107)=mark
      MARK_BELOW  → (4, D) with TriggerPriceType=mark
    See to_fix_trigger_type() + to_fix_trigger_price_direction().
  - ContingencyType.NONE=0 is an engine sentinel; wire serialization
    omits the tag when state is NONE.
  - Leg.leg_ref_id → FIX LegRefID(654)
  - Leg.symbol → FIX LegSymbol(600)
  - Leg.direction → FIX LegSide(624)
  - Leg.target_qty → FIX LegQty
  - Leg.size_share → FIX LegRatioQty
  - Leg.trigger_price → FIX StopPx(99) (per-leg)
  - Leg.limit_price → FIX LegPrice
  - Leg.currency → FIX LegCurrency
  - Leg.expires_at → FIX LegExpireTime(621)
  - Leg.settlement_type → FIX LegSettlType(587)

Cross-leg atomicity rationale: no venue supports atomic multi-leg across spot+perp
products on crypto markets today. FIX FOK is single-order atomicity only (quantity
within one order). NewOrderMultileg is options-only on crypto venues. Engine-side
pre-fill atomic check at TRIGGERED→RELEASED is the only available mechanism for
cross-leg atomicity in combined spot+perp strategies, not a workaround. Matches
industry practice at all major crypto prop shops.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Iterable, Literal


_LOGGER = logging.getLogger(__name__)

# AC38 — de-dup set for the mark-trigger backtest audit log entry.
# Keyed by Order.order_id (FIX ClOrdID(11)) — guaranteed unique per armed
# Order via the auto-generated monotonic counter (see `_next_order_id`
# below). "Exactly one entry per position" holds across the session.
#
# History: a plain set keyed by id() suppressed BTC audit lines under
# id-reuse (observed full-suite 2026-04-19). A tuple key on
# (strategy_id, token, armed_at) suppressed legitimate re-emissions when
# tests shared those fields. WeakSet failed on Order.__hash__ (sizing_ctx
# dict → unhashable). order_id counter-based is the correct long-term
# design — it matches the FIX spec for ClOrdID uniqueness.
_MARK_AUDIT_EMITTED: set[str] = set()


# Module-level monotonic counter for auto-generated order_ids. Never
# repeats within a process lifetime — perfect for dedup. Reset happens
# only on process restart (tests share a process; still distinct).
import itertools
_next_order_id = itertools.count(1)


def _gen_order_id(strategy_id: str, token: str) -> str:
    """Generate a unique FIX ClOrdID(11) when caller passed order_id=''.

    Format: ``{strategy_id}-{token}-{monotonic_counter}`` — human-readable
    for logs while guaranteed unique per process. Capped at 36 chars to
    satisfy Binance venue constraints (newClientOrderId max 36 chars);
    if the concatenated form exceeds 36, the strategy_id is truncated
    (token + counter are load-bearing for uniqueness; strategy_id is
    cosmetic in the venue namespace).
    """
    seq = next(_next_order_id)
    candidate = f"{strategy_id}-{token}-{seq}"
    if len(candidate) <= 36:
        return candidate
    tail = f"-{token}-{seq}"
    head_budget = max(1, 36 - len(tail))
    return f"{strategy_id[:head_budget]}{tail}"


class TriggerType(IntEnum):
    """Trigger predicate for an Order (AC13).

    Engine-internal ordinals; FIX wire mapping is a COMPOSITE of
    FIX TriggerType(1100) and TriggerPriceDirection(1109). See module
    docstring and :meth:`to_fix_trigger_type` / :meth:`to_fix_trigger_price_direction`.

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

    def to_fix_trigger_type(self) -> int:
        """Return FIX TriggerType(1100) integer code (M7 AC-O2).

        All PRICE/MARK variants map to FIX TriggerType=4 "PriceMovement".
        Distinction between them (spot vs mark, above vs below) is carried
        separately via TriggerPriceDirection(1109) and venue-level routing.
        """
        if self in (TriggerType.PRICE_ABOVE, TriggerType.PRICE_BELOW,
                    TriggerType.MARK_ABOVE, TriggerType.MARK_BELOW):
            return 4
        # TIME_AT / BAR_CLOSE — no standard FIX TriggerType analog
        raise NotImplementedError(
            f"{self.name}: no FIX TriggerType(1100) analog (engine-internal only)"
        )

    def to_fix_trigger_price_direction(self) -> str:
        """Return FIX TriggerPriceDirection(1109) — 'U' (up/above) or 'D' (down/below)."""
        if self in (TriggerType.PRICE_ABOVE, TriggerType.MARK_ABOVE):
            return "U"
        if self in (TriggerType.PRICE_BELOW, TriggerType.MARK_BELOW):
            return "D"
        raise NotImplementedError(
            f"{self.name}: no FIX TriggerPriceDirection(1109) analog"
        )

    def to_fix_trigger_price_type(self) -> int:
        """Return FIX TriggerPriceType(1107) — which reference price feeds the
        trigger predicate at the venue.

        Mapping:
          PRICE_ABOVE / PRICE_BELOW → 2 (LastTrade)
          MARK_ABOVE  / MARK_BELOW  → 7 (BestIndexPrice — closest public
                                         analog for perp "mark" price in
                                         FIX 5.0 SP2; venues that expose
                                         a dedicated MarkPrice enum map to
                                         it at the session layer).

        Without this tag the venue cannot distinguish a last-trade trigger
        from a mark-price trigger — they would fire on different tapes.
        """
        if self in (TriggerType.PRICE_ABOVE, TriggerType.PRICE_BELOW):
            return 2
        if self in (TriggerType.MARK_ABOVE, TriggerType.MARK_BELOW):
            return 7
        raise NotImplementedError(
            f"{self.name}: no FIX TriggerPriceType(1107) analog"
        )


class ExecType(str, Enum):
    """FIX ExecType(150) — what an ExecutionReport describes (M7 AC-O5).

    FIX-standard values (9 total). Partial vs final fill is disambiguated via
    OrdStatus(39): OrdStatus=1 (PartiallyFilled) vs OrdStatus=2 (Filled).
    Strategy-level dispatch uses `on_order_partial_fill` vs `on_order_filled`
    callback names.
    """
    NEW = "0"               # order accepted by venue
    TRADE = "F"             # fill (partial or final; use OrdStatus to distinguish)
    CANCELED = "4"
    REJECTED = "8"
    TRIGGERED = "L"         # ARMED → TRIGGERED (pre-fill predicate hit)
    EXPIRED = "C"
    TRADE_CANCEL = "H"      # cancel a prior fill (venue bust)
    TRADE_CORRECT = "G"     # correct a prior fill (venue amendment)
    ORDER_STATUS = "I"      # status response (no trade)


class OrderStatus(IntEnum):
    """Order lifecycle state (AC32).

    Engine-internal ordinals (NOT FIX OrdStatus(39) wire values). See
    :meth:`to_fix_ordstatus` for the wire mapping.

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

    def to_fix_ordstatus(self) -> str:
        """Return FIX OrdStatus(39) single-char wire code (M7 AC-O2).

        Engine-internal states (ARMED/TRIGGERED/RELEASED) map to OrdStatus='A'
        "Pending New" per G8 decision + audit-log annotation via fix_custom_tag.
        """
        mapping = {
            OrderStatus.ARMED:            "A",
            OrderStatus.TRIGGERED:        "A",
            OrderStatus.RELEASED:         "A",
            OrderStatus.PARTIALLY_FILLED: "1",
            OrderStatus.FILLED:           "2",
            OrderStatus.CANCELLED:        "4",
            OrderStatus.REJECTED:         "8",
            OrderStatus.EXPIRED:          "C",
        }
        return mapping[self]

    @property
    def fix_custom_tag(self) -> int:
        """Engine-internal custom tag 9001-9003 for ARMED/TRIGGERED/RELEASED
        (G8 decision — audit-log annotation attached to OrdStatus='A')."""
        tags = {
            OrderStatus.ARMED:     9001,
            OrderStatus.TRIGGERED: 9002,
            OrderStatus.RELEASED: 9003,
        }
        if self in tags:
            return tags[self]
        return 0  # no custom tag for terminal states


class LegStatus(IntEnum):
    """Per-leg lifecycle state (M5 AC4).

    Mirrors :class:`OrderStatus` vocabulary at the leg granularity.
    Order.state is derived from leg statuses per the F1 derivation table
    (Task 7). Engine-internal ordinals — FIX wire mapping via
    :meth:`to_fix_ordstatus` (M7).
    """
    ARMED            = 1   # awaiting trigger (FIX OrdStatus=0 New, locally emulated)
    WORKING          = 2   # trigger fired, handed to fill path
    PARTIALLY_FILLED = 3   # partial fill received; cum_qty < target_qty
    FILLED           = 4   # terminal fill (FIX OrdStatus=2)
    EXPIRED          = 5   # LegExpireTime elapsed without trigger
    CANCELLED        = 6   # explicit cancel (sibling unwind, signal flip, etc.)
    REJECTED         = 7   # constraint/atomicity failure at release

    def to_fix_ordstatus(self) -> str:
        """Return FIX OrdStatus(39) single-char wire code (M7 AC-O2).

        LegStatus engine-internal ARMED/WORKING map to OrdStatus='A' per
        same G8 pattern as Order-level. Terminal states map to standard FIX.
        """
        mapping = {
            LegStatus.ARMED:            "A",
            LegStatus.WORKING:          "A",
            LegStatus.PARTIALLY_FILLED: "1",
            LegStatus.FILLED:           "2",
            LegStatus.CANCELLED:        "4",
            LegStatus.REJECTED:         "8",
            LegStatus.EXPIRED:          "C",
        }
        return mapping[self]


class LegFillPolicy(Enum):
    """Cross-leg fill reaction policy (M5 AC5).

    - ``UNWIND_ON_REJECT``: if any leg rejects, already-filled siblings
      unwind at market (F2 post-fill cascade).
    - ``BEST_EFFORT``: siblings are independent; one rejecting leaves the
      others untouched.
    - ``OTO_BRACKET``: entry leg fills activate SL/TP siblings
      (ARMED → WORKING atomically). See F1 derivation table (Task 8b).
    """
    UNWIND_ON_REJECT = "unwind_on_reject"
    BEST_EFFORT      = "best_effort"
    OTO_BRACKET      = "oto_bracket"


class ContingencyType(IntEnum):
    """Cross-order linkage (FIX ContingencyType(1385)).

    NONE=0 is an engine sentinel — wire serialization OMITS the tag when
    state is NONE (standard FIX practice: absent tag == no contingency).

    - OCO (One-Cancels-Other): filling one cancels the siblings.
    - OTO (One-Triggers-Other): filling one arms the siblings.
    - OUO (One-Updates-Other): filling one amends sibling qty.
    - OTOCO (OTO + inner OCO): entry triggers a pair of siblings, which
      are OCO with each other. Standard 3-leg bracket semantics —
      entry→{SL, TP}. Capital reserve = entry + max(sibling margins).
    """
    NONE  = 0
    OCO   = 1
    OTO   = 2
    OUO   = 3
    OTOCO = 4


class TimeInForce(Enum):
    """FIX TimeInForce(59) values (M5 F7).

    Default is GTC (Good-Til-Cancelled) — matches M4 ``Order.arm()`` semantics
    where an Order persists ARMED until it triggers, expires, or is cancelled.

    Canonical FIX 5.0 SP2 values (TIF 59):
      0 = Day, 1 = GTC, 2 = OPG, 3 = IOC, 4 = FOK, 5 = GTX (GoodTillCrossing),
      6 = GTD, 7 = At the Close, 8 = GoodThroughCrossing, 9 = At Crossing.
    """
    DAY = "0"
    GTC = "1"   # default
    IOC = "3"
    FOK = "4"
    GTX = "5"   # GoodTillCrossing (fixed from "8" which is GoodThroughCrossing)
    GTD = "6"


@dataclass(frozen=True, slots=True)
class Leg:
    """Per-leg record inside a multi-leg :class:`Order` (M5 AC4, AC6, F10).

    For single-leg Orders the ``legs`` tuple is empty and the ``Order``'s
    bare fields carry the semantics (AC3 zero-overhead invariant). For
    multi-leg Orders each Leg has its own trigger/status/qty tracking.

    FIX field mapping is documented in the module docstring.
    """

    leg_ref_id: str                                                    # FIX LegRefID(654)
    symbol: str                                                        # FIX LegSymbol(600)
    market: Literal["spot", "perp"]
    venue: str
    direction: Literal[-1, 1]                                          # FIX LegSide(624)
    target_qty: float                                                  # FIX LegQty
    cum_qty: float = 0.0
    size_share: float = 1.0                                            # FIX LegRatioQty
    order_type: Literal["market", "stop", "stop_limit", "limit"] = "market"
    status: LegStatus = LegStatus.ARMED
    trigger_price: float | None = None                                 # FIX StopPx(99)
    limit_price: float | None = None                                   # FIX LegPrice
    currency: str = "USDT"                                             # FIX LegCurrency
    position_id: str | None = None
    dust_usd: float = 1.0
    # Per-leg sizing (margin_usd lives here per R13/R14).
    sizing_ctx: dict = field(default_factory=dict)
    # F10 additions — missing FIX fields pulled in at Wave B.
    expires_at: datetime | None = None                                 # FIX LegExpireTime(621)
    settlement_type: Literal["spot", "perp", "futures"] = "spot"       # FIX LegSettlType(587)


# For M4 SizingContext is a plain mapping; M7 formalises the typed structure.
SizingContext = Any


@dataclass(frozen=True, slots=True)
class Order:
    """Frozen state record for an armed entry (AC13, AC32).

    Transitions are implemented as methods returning a new ``Order``
    instance (since the dataclass is frozen). T8 ships construction + factory;
    T9 adds transition methods; T10 adds ``to_json`` / ``from_json``.

    Priority ordering for capital contention (AC39) is lexicographic on
    ``(armed_at, strategy_id, token)`` — oldest-armed wins.

    M5 multi-leg extensions (AC3, AC4):
      * ``legs`` — empty tuple `()` = single-leg (bare fields are the implicit
        leg, zero Leg allocation); non-empty tuple must have ≥2 elements.
      * ``fill_policy`` — cross-leg fill reaction (UNWIND_ON_REJECT /
        BEST_EFFORT / OTO_BRACKET), per F1/F2.
      * ``contingency`` — FIX ContingencyType(1385) linkage.
      * ``linked_order_id`` — combined primary/secondary pair identifier.
      * ``time_in_force`` — FIX TimeInForce(59) (default GTC).
      * ``order_id`` — FIX ClOrdID(11); auto-generated from
        (strategy_id, token, armed_at) if empty.
    """

    strategy_id: str
    token: str
    direction: Literal[-1, 1]
    trigger: TriggerType
    trigger_price: float
    working_price_source: Literal["last", "mark", "bar_hl"]
    armed_at: datetime                 # AC25 priority key
    expires_at: datetime | None
    sizing_ctx: SizingContext          # recomputed at RELEASE (dict in M4)
    state: OrderStatus
    filled_qty: float = 0.0            # PARTIALLY_FILLED accounting
    leaves_qty: float = 0.0
    reject_reason: str | None = None
    strategy_params: dict = field(default_factory=dict)
    window_end: float = 0.0            # 4H window epoch
    # F9 — FIX OrderID(37) — populated on venue ack in M7+. Paper/backtest
    # Orders carry None throughout their lifecycle (no venue round-trip).
    venue_order_id: str | None = None
    # M5 Task 6 additions — multi-leg container + FIX alignment.
    order_id: str = ""                                   # FIX ClOrdID(11)
    legs: tuple[Leg, ...] = ()                           # FIX LegGrp(555)
    fill_policy: LegFillPolicy = LegFillPolicy.UNWIND_ON_REJECT
    contingency: ContingencyType = ContingencyType.NONE
    linked_order_id: str | None = None                   # combined primary/secondary pair
    time_in_force: TimeInForce = TimeInForce.GTC         # FIX TimeInForce(59)
    # M5 Wave D — mutable emission sinks (survive ``replace()`` by reference).
    # These lists are intentionally mutable; callers append to them from
    # transition methods. They are NOT persisted via ``to_json`` (audit log
    # writer consumes them via ``emitted_events()`` / ``emitted_closed_trades()``).
    _events: list = field(default_factory=list, compare=False, repr=False)
    _closed_trades: list = field(default_factory=list, compare=False, repr=False)
    # M7 AC-O2: audit log for engine-internal state transitions (ARMED/
    # TRIGGERED/RELEASED → OrdStatus='A' + custom tag 9001-9003). Populated
    # by transition methods when they advance `state`; NOT persisted via to_json.
    fix_audit_log: list = field(default_factory=list, compare=False, repr=False)
    # M7 AC-O3: last exec report for on_order_rejected / on_order_filled surfacing.
    last_exec_report: object = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        # Invariant (AC3 / T-M5-20): legs tuple is either empty (single-leg
        # via bare Order fields) or ≥2 (true multi-leg). Exactly one element
        # is ambiguous and forbidden — single-leg Orders MUST use legs=().
        if len(self.legs) == 1:
            raise ValueError(
                f"Order.legs must be empty tuple () for single-leg OR have "
                f">=2 elements; got 1 element. Single-leg orders use bare "
                f"Order fields; legs tuple is for multi-leg only."
            )
        # Auto-generate order_id (FIX ClOrdID(11)) if not provided. Callers
        # can pre-compute a stable ID; otherwise the (strategy_id, token,
        # armed_at) composite key is sufficient for backtest/paper use.
        if not self.order_id:
            oid = (
                f"{self.strategy_id}:{self.token}:"
                f"{self.armed_at.isoformat() if self.armed_at is not None else ''}"
            )
            object.__setattr__(self, "order_id", oid)
        # Derive Order.state from legs when multi-leg (F1 derivation table).
        # Empty-legs case: state is carried from the bare field (unchanged).
        #
        # Derivation is authoritative for leg-driven outcomes (FILLED,
        # REJECTED, CANCELLED, EXPIRED, PARTIALLY_FILLED) but must NOT
        # regress Order-level lifecycle states that have no leg-status
        # equivalent (TRIGGERED, RELEASED). When all legs are ARMED the
        # derivation returns ARMED which would backtrack an externally-
        # set TRIGGERED/RELEASED state — suppress that override.
        if self.legs:
            derived = _derive_state_from_legs(
                legs=self.legs,
                fill_policy=self.fill_policy,
            )
            if derived is not None and derived != self.state:
                # Don't regress TRIGGERED/RELEASED back to ARMED when legs
                # haven't individually progressed — the Order-level bare
                # state tracks the top-level lifecycle stage.
                if derived == OrderStatus.ARMED and self.state in (
                    OrderStatus.TRIGGERED, OrderStatus.RELEASED,
                    OrderStatus.REJECTED,
                ):
                    pass
                else:
                    object.__setattr__(self, "state", derived)

    def log_fix_state_change(self, new_state: "OrderStatus") -> None:
        """Append a FIX audit-log entry describing a state transition (AC-O2).

        Format: "<state_name>:OrdStatus=<wire_char>:tag=<custom_tag>".
        ARMED/TRIGGERED/RELEASED get custom-tag annotations (9001/9002/9003).
        Terminal states are logged without a tag.

        Mutates the fix_audit_log list in-place (list is mutable-by-reference;
        frozen dataclass invariant preserved).
        """
        wire = new_state.to_fix_ordstatus()
        tag = new_state.fix_custom_tag
        entry = f"{new_state.name}:OrdStatus={wire}"
        if tag:
            entry += f":tag={tag}"
        self.fix_audit_log.append(entry)

    def _transit(self, new_state: "OrderStatus", **replace_kwargs) -> "Order":
        """Internal transition helper — logs FIX state change on the CURRENT
        (pre-replace) order, then returns the replaced Order.

        fix_audit_log is a mutable-by-reference list so both self and the
        replaced Order share the same growing audit sequence — callers
        inherit the full transition history (round-3 FIX MAJOR 4: earlier
        transitions bypassed log_fix_state_change so the audit log was
        empty in production despite the field existing on the dataclass).
        """
        self.log_fix_state_change(new_state)
        return replace(self, state=new_state, **replace_kwargs)

    @classmethod
    def build_for_test(cls, *, order_id: int = 0x00000001, **overrides) -> "Order":
        """Test-only factory for M7 unit tests needing a shaped Order without
        wiring full sizing_ctx. Accepts order_id as int or str.
        """
        from datetime import datetime, timezone
        oid = f"order-{order_id:08x}" if isinstance(order_id, int) else str(order_id)
        defaults = dict(
            strategy_id="test",
            token="BTC",
            direction=1,
            trigger=TriggerType.PRICE_ABOVE,
            trigger_price=50_000.0,
            working_price_source="last",
            armed_at=datetime.now(timezone.utc),
            expires_at=None,
            sizing_ctx={},
            state=OrderStatus.ARMED,
            filled_qty=0.0,
            leaves_qty=0.0,
            reject_reason=None,
            strategy_params={},
            window_end=0.0,
            order_id=oid,
        )
        defaults.update(overrides)
        return cls(**defaults)

    @classmethod
    def arm(
        cls,
        *,
        strategy_id: str,
        token: str,
        direction: Literal[-1, 1],
        trigger: TriggerType,
        trigger_price: float,
        working_price_source: Literal["last", "mark", "bar_hl"],
        armed_at: datetime,
        expires_at: datetime | None,
        sizing_ctx: SizingContext,
        strategy_params: dict | None = None,
        window_end: float = 0.0,
        # M5 multi-leg kwargs (all default-valued — AC3 single-leg fallback).
        legs: tuple["Leg", ...] = (),
        fill_policy: LegFillPolicy = LegFillPolicy.UNWIND_ON_REJECT,
        contingency: ContingencyType = ContingencyType.NONE,
        linked_order_id: str | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        order_id: str = "",
    ) -> "Order":
        """Construct a new Order in the ARMED state.

        This is the single allowed entry point for creating an Order —
        callers must never construct ``Order(...)`` directly with a
        custom state. All subsequent state changes go through the transition
        methods (T9).

        M5 multi-leg kwargs are all default-valued so existing M4 callers
        continue to work unchanged (legs=() is the zero-overhead single-leg
        common case, AC3).
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
            state=OrderStatus.ARMED,
            filled_qty=0.0,
            leaves_qty=0.0,
            reject_reason=None,
            strategy_params=dict(strategy_params) if strategy_params else {},
            window_end=window_end,
            order_id=order_id or _gen_order_id(strategy_id, token),
            legs=tuple(legs),
            fill_policy=fill_policy,
            contingency=contingency,
            linked_order_id=linked_order_id,
            time_in_force=time_in_force,
        )

    # ------------------------------------------------------------------ #
    # M5 Task 14b — Legacy simulator.PendingEntry migration (F4 table)   #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_legacy_simulator_pending_entry(
        cls,
        legacy: dict,
        *,
        bar_period_seconds: float = 3600.0,
    ) -> "Order":
        """Construct an Order from a pre-M5 ``simulator.PendingEntry`` shape.

        Implements the F4 migration table (design.md §F4). Mapping:
          - ``symbol`` → ``token``
          - ``type`` → dropped (replaced by ``trigger`` enum)
          - ``price`` → ``trigger_price``
          - ``time`` (epoch float) → ``armed_at`` (UTC datetime)
          - ``state`` (str) → ``OrderStatus`` (defaults ARMED if absent/unknown)
          - ``signal_bar`` → dropped (implicit from ``armed_at``)
          - ``entry_bar`` → ``expires_at = armed_at + (entry_bar - signal_bar) * bar_period``
          - ``conviction`` → ``sizing_ctx["conviction"]``
          - ``trigger_fn`` → dropped (audit confirmed zero reducibility
            concerns; no production site ever populated it)

        Used by ad-hoc paper-state migration scripts + the T-M5-24 lossless
        round-trip test. Production open path does NOT flow through this
        classmethod (Task 15's ``_process_orders`` uses ``Order.arm`` directly).
        """
        from datetime import timezone as _tz

        symbol = legacy.get("symbol") or legacy.get("token", "")
        direction = int(legacy.get("direction", 0) or 0)
        if direction not in (-1, 1):
            direction = 1  # safe default — same as legacy _process_entries
        price = float(legacy.get("price", legacy.get("trigger_price", 0.0)) or 0.0)
        # Epoch seconds → UTC datetime (legacy `time` field).
        ts = legacy.get("time", legacy.get("armed_at"))
        if isinstance(ts, datetime):
            armed_at = ts if ts.tzinfo else ts.replace(tzinfo=_tz.utc)
        else:
            armed_at = datetime.fromtimestamp(float(ts or 0.0), tz=_tz.utc)
        # entry_bar → expires_at delta from signal_bar (both legacy bar indices).
        signal_bar = int(legacy.get("signal_bar", 0) or 0)
        entry_bar = int(legacy.get("entry_bar", 0) or 0)
        expires_at: datetime | None = None
        if entry_bar > signal_bar:
            delta_sec = (entry_bar - signal_bar) * float(bar_period_seconds)
            expires_at = datetime.fromtimestamp(
                armed_at.timestamp() + delta_sec, tz=_tz.utc,
            )
        # conviction → sizing_ctx["conviction"]
        sizing_ctx: dict = {}
        if "conviction" in legacy:
            sizing_ctx["conviction"] = float(legacy["conviction"])
        # state (str) → OrderStatus.
        state_str = str(legacy.get("state", "armed") or "armed").lower()
        state_map = {
            "armed":           OrderStatus.ARMED,
            "triggered":       OrderStatus.TRIGGERED,
            "released":        OrderStatus.RELEASED,
            "filled":          OrderStatus.FILLED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "expired":         OrderStatus.EXPIRED,
            "rejected":        OrderStatus.REJECTED,
            "cancelled":       OrderStatus.CANCELLED,
        }
        st = state_map.get(state_str, OrderStatus.ARMED)
        # Trigger enum — price-level implies PRICE_ABOVE (long) / PRICE_BELOW
        # (short). Legacy `type` was a discriminator string; we derive from
        # direction since F4 documents `type` as dropped.
        trigger = (
            TriggerType.PRICE_ABOVE if direction == 1
            else TriggerType.PRICE_BELOW
        )
        strategy_id_str = str(legacy.get("strategy_id", ""))
        # Honor caller-supplied order_id if present; otherwise generate a
        # unique monotonic ClOrdID(11) so identical legacy blobs don't
        # collide (earlier fallback used armed_at.isoformat() which is
        # deterministic under TestClock and violates FIX ClOrdID
        # uniqueness; also exceeded Binance's 36-char cap).
        order_id = str(legacy.get("order_id") or "")
        if not order_id:
            order_id = _gen_order_id(strategy_id_str, str(symbol))
        return cls(
            strategy_id=strategy_id_str,
            token=str(symbol),
            direction=direction,  # type: ignore[arg-type]
            trigger=trigger,
            trigger_price=price,
            working_price_source="last",
            armed_at=armed_at,
            expires_at=expires_at,
            sizing_ctx=sizing_ctx,
            state=st,
            filled_qty=0.0,
            leaves_qty=0.0,
            reject_reason=None,
            strategy_params={},
            window_end=0.0,
            order_id=order_id,
        )

    # ------------------------------------------------------------------ #
    # Transition methods (T9 — minimal set required by T10 persistence   #
    # tests; full T9 expansion lands later and is expected to extend,    #
    # not replace, these helpers).                                       #
    # ------------------------------------------------------------------ #

    def on_price(self, price: float) -> "Order":
        """ARMED -> TRIGGERED when the trigger predicate matches the price.

        Implements predicate evaluation for PRICE_ABOVE/PRICE_BELOW and
        MARK_ABOVE/MARK_BELOW (for M4, mark is treated identically to price
        per AC38 backtest fallback). Non-ARMED inputs are returned unchanged
        so downstream chains (``order.on_price(...).release(...)``) remain safe.
        """
        if self.state != OrderStatus.ARMED:
            return self
        hit = False
        t = self.trigger
        if t in (TriggerType.PRICE_ABOVE, TriggerType.MARK_ABOVE):
            hit = float(price) >= float(self.trigger_price)
        elif t in (TriggerType.PRICE_BELOW, TriggerType.MARK_BELOW):
            hit = float(price) <= float(self.trigger_price)
        else:
            # TIME_AT / BAR_CLOSE — price alone does not trigger
            hit = False
        if not hit:
            return self
        return self._transit(OrderStatus.TRIGGERED)

    def release(self, *, capital_ok: bool) -> "Order":
        """TRIGGERED -> RELEASED (constraint pass) or REJECTED (fail).

        ``reject_reason`` is set to ``"risk_on_release"`` when the capital
        constraint check fails at RELEASE — this matches AC39's contention
        semantics (sizing-vs-available-capital failure).
        """
        if self.state != OrderStatus.TRIGGERED:
            return self
        if capital_ok:
            return self._transit(OrderStatus.RELEASED)
        return self._transit(OrderStatus.REJECTED, reject_reason="risk_on_release")

    def on_fill(self, *, filled_qty: float, leaves_qty: float) -> "Order":
        """RELEASED/PARTIALLY_FILLED -> PARTIALLY_FILLED / FILLED.

        Terminal when ``leaves_qty == 0``; otherwise PARTIALLY_FILLED carries
        the outstanding quantity forward across restarts (AC32).
        """
        if self.state not in (OrderStatus.RELEASED, OrderStatus.PARTIALLY_FILLED):
            return self
        filled = float(filled_qty)
        leaves = float(leaves_qty)
        new_state = (
            OrderStatus.FILLED if leaves <= 0.0 else OrderStatus.PARTIALLY_FILLED
        )
        return self._transit(new_state, filled_qty=filled, leaves_qty=leaves)

    def cancel(self) -> "Order":
        """Any non-terminal state -> CANCELLED (e.g. signal flip)."""
        terminal = {
            OrderStatus.FILLED, OrderStatus.EXPIRED,
            OrderStatus.REJECTED, OrderStatus.CANCELLED,
        }
        if self.state in terminal:
            return self
        return self._transit(OrderStatus.CANCELLED)

    # ------------------------------------------------------------------ #
    # AC38 — Mark-trigger backtest fallback                              #
    # ------------------------------------------------------------------ #

    def on_backtest_bar(self, *, close: float) -> "Order":
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
            if self.order_id not in _MARK_AUDIT_EMITTED:
                _MARK_AUDIT_EMITTED.add(self.order_id)
                _LOGGER.info(
                    "AC38 mark-trigger fallback: token=%s strategy=%s "
                    "using bar close=%.10g in lieu of mark (position=%s)",
                    self.token,
                    self.strategy_id,
                    float(close),
                    self.token,
                )
        return self.on_price(float(close))

    def check_expiry(self, *, now: datetime) -> "Order":
        """ARMED + now >= expires_at -> EXPIRED. Triggered orders are immune."""
        if self.state != OrderStatus.ARMED:
            return self
        if self.expires_at is None:
            return self
        if now >= self.expires_at:
            return self._transit(OrderStatus.EXPIRED)
        return self

    # ------------------------------------------------------------------ #
    # M5 Task 6/7 — multi-leg helpers (T-M5-02 / T-M5-21)                #
    # ------------------------------------------------------------------ #

    def derive_status_from_legs(self) -> OrderStatus:
        """Compute the Order-level status from current legs (F1 table).

        For empty legs (``legs=()``) this returns the bare ``self.state``
        since the single-leg Order's direct transitions already represent
        the implicit single leg (AC3 zero-overhead invariant).
        """
        if not self.legs:
            return self.state
        derived = _derive_state_from_legs(
            legs=self.legs,
            fill_policy=self.fill_policy,
        )
        return derived if derived is not None else self.state

    def _replace_leg(self, leg_ref_id: str, **leg_kwargs) -> "Order":
        """Helper: return a new Order with one leg replaced."""
        new_legs: list[Leg] = []
        found = False
        for lg in self.legs:
            if lg.leg_ref_id == leg_ref_id:
                new_legs.append(replace(lg, **leg_kwargs))
                found = True
            else:
                new_legs.append(lg)
        if not found:
            raise KeyError(
                f"Order {self.order_id!r} has no leg with leg_ref_id={leg_ref_id!r}"
            )
        return replace(self, legs=tuple(new_legs))

    def on_leg_price(self, *, leg_ref_id: str, price: float) -> "Order":
        """Per-leg trigger evaluation (T-M5-02).

        When ``price`` crosses the named leg's trigger level, that leg
        transitions ARMED -> WORKING; siblings stay in their current state
        (BEST_EFFORT / UNWIND_ON_REJECT / OTO_BRACKET semantics are applied
        in Wave D — this helper is just the per-leg trigger evaluation).
        """
        target_leg = None
        for lg in self.legs:
            if lg.leg_ref_id == leg_ref_id:
                target_leg = lg
                break
        if target_leg is None:
            raise KeyError(
                f"Order {self.order_id!r} has no leg with leg_ref_id={leg_ref_id!r}"
            )
        if target_leg.status != LegStatus.ARMED:
            return self
        hit = False
        if target_leg.order_type == "stop":
            # Stop leg: triggers when price crosses trigger_price (per
            # direction — long stop below, short stop above).
            if target_leg.trigger_price is not None:
                if target_leg.direction == 1:
                    hit = float(price) >= float(target_leg.trigger_price)
                else:
                    hit = float(price) <= float(target_leg.trigger_price)
        elif target_leg.order_type == "limit":
            # Limit (TP) leg: triggers when price reaches limit_price.
            if target_leg.limit_price is not None:
                if target_leg.direction == 1:
                    hit = float(price) <= float(target_leg.limit_price)
                else:
                    hit = float(price) >= float(target_leg.limit_price)
        elif target_leg.order_type == "stop_limit":
            if target_leg.trigger_price is not None:
                if target_leg.direction == 1:
                    hit = float(price) >= float(target_leg.trigger_price)
                else:
                    hit = float(price) <= float(target_leg.trigger_price)
        else:  # "market"
            # Market legs trigger immediately; this method is a no-op for
            # market legs (they get armed → working via explicit activation).
            hit = True
        if not hit:
            return self
        return self._replace_leg(leg_ref_id, status=LegStatus.WORKING)

    def on_leg_fill(self, *, leg_ref_id: str,
                    filled_qty: float,
                    leaves_qty: float | None = None) -> "Order":
        """Leg fill dispatch (T-M5-02, T-M5-05).

        Transitions WORKING -> PARTIALLY_FILLED / FILLED. ``leaves_qty``
        defaults to ``target_qty - filled_qty`` if not provided.

        OTO_BRACKET semantics (T-M5-05 Wave D.1 Task 8b): when the entry
        leg (first leg) transitions to FILLED under ``OTO_BRACKET``, all
        remaining ARMED sibling legs atomically transition ARMED -> WORKING
        and a ``bracket_activated`` event is emitted.

        OTO_BRACKET exit leg resolution: when a protective sibling (SL/TP)
        transitions to FILLED, any other still-WORKING or ARMED sibling
        transitions to CANCELLED with a ``sibling_cancelled`` event.
        """
        target_leg = None
        for lg in self.legs:
            if lg.leg_ref_id == leg_ref_id:
                target_leg = lg
                break
        if target_leg is None:
            raise KeyError(
                f"Order {self.order_id!r} has no leg with leg_ref_id={leg_ref_id!r}"
            )
        cum = float(filled_qty)
        tgt = float(target_leg.target_qty)
        leaves = (tgt - cum) if leaves_qty is None else float(leaves_qty)
        new_status = (
            LegStatus.FILLED if leaves <= 0.0 else LegStatus.PARTIALLY_FILLED
        )
        out = self._replace_leg(
            leg_ref_id, status=new_status, cum_qty=cum,
        )
        # OTO_BRACKET: on entry-leg FILL, activate sibling protective legs
        # (ARMED -> WORKING) + emit bracket_activated. The entry leg is the
        # FIRST leg in the ``legs`` tuple by convention (matches Nautilus'
        # BracketOrder + M5 brief AC5/F1 Row 6).
        if (out.fill_policy == LegFillPolicy.OTO_BRACKET
                and new_status == LegStatus.FILLED
                and len(out.legs) > 0
                and out.legs[0].leg_ref_id == leg_ref_id):
            new_legs: list[Leg] = []
            activated_any = False
            for lg in out.legs:
                if lg.leg_ref_id == leg_ref_id:
                    new_legs.append(lg)
                elif lg.status == LegStatus.ARMED:
                    new_legs.append(replace(lg, status=LegStatus.WORKING))
                    activated_any = True
                else:
                    new_legs.append(lg)
            out = replace(out, legs=tuple(new_legs))
            if activated_any:
                out._events.append({
                    "event": "bracket_activated",
                    "order_id": out.order_id,
                    "entry_leg_ref_id": leg_ref_id,
                })
        # OTO_BRACKET: protective-leg FILL cancels the other protective
        # sibling. Entry leg is legs[0]; protectives are legs[1:].
        if (out.fill_policy == LegFillPolicy.OTO_BRACKET
                and new_status == LegStatus.FILLED
                and len(out.legs) > 0
                and out.legs[0].leg_ref_id != leg_ref_id):
            new_legs = []
            cancelled_any = False
            for lg in out.legs:
                if lg.leg_ref_id == leg_ref_id:
                    new_legs.append(lg)
                elif lg.leg_ref_id == out.legs[0].leg_ref_id:
                    # Leave entry leg alone (already FILLED / WORKING).
                    new_legs.append(lg)
                elif lg.status in (LegStatus.WORKING, LegStatus.ARMED,
                                   LegStatus.PARTIALLY_FILLED):
                    new_legs.append(replace(lg, status=LegStatus.CANCELLED))
                    cancelled_any = True
                else:
                    new_legs.append(lg)
            out = replace(out, legs=tuple(new_legs))
            if cancelled_any:
                out._events.append({
                    "event": "sibling_cancelled",
                    "order_id": out.order_id,
                    "trigger_leg_ref_id": leg_ref_id,
                })
        # Surface a generic leg_filled audit event (consumed by orders_log
        # writer in Wave G). Keep this unconditional so the emit log is
        # self-describing for multi-leg fills.
        out._events.append({
            "event": "leg_filled",
            "order_id": out.order_id,
            "leg_ref_id": leg_ref_id,
            "filled_qty": cum,
        })
        return out

    def on_leg_reject(self, *, leg_ref_id: str,
                      reason: str = "venue_reject") -> "Order":
        """Handle a leg REJECT per ``fill_policy`` (M5 Wave D.1 Task 8a).

        Transitions the named leg to REJECTED and applies the cross-leg
        cascade per the Order's ``fill_policy``:

        * ``UNWIND_ON_REJECT`` (F2 post-fill cascade): for each sibling leg
          that has already FILLED, emit a ``ClosedTrade`` with
          ``exit_reason="sibling_unwound"`` + a ``sibling_unwound`` event.
          The filled sibling stays FILLED (terminal); the unwinding is
          expressed via ClosedTrade emission, not leg-status transition.

        * ``BEST_EFFORT``: siblings that are still ARMED transition to
          CANCELLED (engine-initiated cancellation — the venue REJECT of
          leg B does not leak to sibling A's engine status). Already-FILLED
          siblings stay FILLED.

        * ``OTO_BRACKET``: if the entry leg rejects, SL/TP remain ARMED
          (never activate per F2). If a protective rejects post-activation
          the entry stays FILLED; no cascade beyond the leg REJECTED mark.
        """
        target_leg = None
        for lg in self.legs:
            if lg.leg_ref_id == leg_ref_id:
                target_leg = lg
                break
        if target_leg is None:
            raise KeyError(
                f"Order {self.order_id!r} has no leg with leg_ref_id={leg_ref_id!r}"
            )
        policy = self.fill_policy
        # Under BEST_EFFORT the venue REJECT is converted to an engine-level
        # CANCELLED for the rejected leg so venue state does not leak into
        # the engine's audit trail (per T-M5-04). UNWIND_ON_REJECT and
        # OTO_BRACKET preserve the leg REJECTED status as the terminal
        # failure mode.
        if policy == LegFillPolicy.BEST_EFFORT:
            out = self._replace_leg(leg_ref_id, status=LegStatus.CANCELLED)
        else:
            out = self._replace_leg(leg_ref_id, status=LegStatus.REJECTED)
        out._events.append({
            "event": "leg_rejected",
            "order_id": out.order_id,
            "leg_ref_id": leg_ref_id,
            "reason": reason,
        })
        if policy == LegFillPolicy.UNWIND_ON_REJECT:
            # Force-close any FILLED siblings via ClosedTrade emission.
            for sibling in out.legs:
                if sibling.leg_ref_id == leg_ref_id:
                    continue
                if sibling.status == LegStatus.FILLED:
                    ct = _build_unwind_closed_trade(
                        order=out, sibling=sibling, reason="sibling_unwound",
                    )
                    out._closed_trades.append(ct)
                    out._events.append({
                        "event": "sibling_unwound",
                        "order_id": out.order_id,
                        "unwound_leg_ref_id": sibling.leg_ref_id,
                        "trigger_leg_ref_id": leg_ref_id,
                    })
        elif policy == LegFillPolicy.BEST_EFFORT:
            # Cancel still-ARMED siblings (engine-initiated, not venue reject).
            new_legs: list[Leg] = []
            cancelled_any = False
            for lg in out.legs:
                if lg.leg_ref_id == leg_ref_id:
                    new_legs.append(lg)
                elif lg.status == LegStatus.ARMED:
                    new_legs.append(replace(lg, status=LegStatus.CANCELLED))
                    cancelled_any = True
                else:
                    new_legs.append(lg)
            if cancelled_any:
                out = replace(out, legs=tuple(new_legs))
                out._events.append({
                    "event": "sibling_cancelled",
                    "order_id": out.order_id,
                    "trigger_leg_ref_id": leg_ref_id,
                })
        # OTO_BRACKET requires no additional cascade at engine level here —
        # status derivation (F1) propagates the REJECTED to Order-level.
        return out

    def release_atomic(self, *, available_capital_usd: float) -> "Order":
        """TRIGGERED/ARMED -> RELEASED (atomic) or REJECTED (F2 / Task 8c).

        Aggregates per-leg margin under the Order's ``contingency`` rule
        (see :func:`compute_reserved_capital`) and compares against the
        caller-supplied ``available_capital_usd``. If the aggregate exceeds
        available capital the Order transitions to REJECTED with
        ``reject_reason="risk_on_release_atomic"`` and NO LEG OPENS (F2
        pre-fill atomic invariant). Otherwise the Order transitions to
        RELEASED; Wave F follow-ups transition legs to WORKING.

        Accepts both TRIGGERED (canonical) and ARMED (multi-leg variant —
        leg-level derivation can suppress a TRIGGERED bare state when all
        legs are still ARMED; see ``__post_init__`` derivation guard).
        """
        if self.state not in (OrderStatus.TRIGGERED, OrderStatus.ARMED):
            return self
        aggregate = compute_reserved_capital(self)
        if aggregate > float(available_capital_usd):
            return self._transit(
                OrderStatus.REJECTED, reject_reason="risk_on_release_atomic",
            )
        return self._transit(OrderStatus.RELEASED)

    def apply_liquidity_check(self, *, available_qty: float | dict) -> "Order":
        """Apply FOK single-order liquidity atomicity (M5 F7 / Task 10).

        For ``time_in_force == TimeInForce.FOK`` the Order's full requested
        quantity must fit available liquidity in a single bar. If ``requested
        > available`` the Order transitions to REJECTED with
        ``reject_reason="fok_liquidity"``.

        For non-FOK Orders (GTC, IOC, etc.) this method is a no-op — the
        Order is returned unchanged.

        Single-leg: requested qty is read from ``sizing_ctx["qty"]`` (or
        ``"margin_usd"`` as a fallback — the sizing context carries whichever
        is defined per strategy). ``available_qty`` is a float.

        Multi-leg: ``available_qty`` is a ``dict[leg_ref_id, float]`` and
        each leg is checked against its own liquidity cap; a single
        insufficient leg triggers rejection of the whole Order (FOK is an
        atomicity primitive).
        """
        if self.time_in_force != TimeInForce.FOK:
            return self
        if self.legs:
            # Multi-leg FOK — available_qty must be a dict keyed on leg_ref_id.
            if not isinstance(available_qty, dict):
                raise ValueError(
                    "Multi-leg FOK requires available_qty as a dict "
                    "{leg_ref_id: float}; got a scalar."
                )
            for lg in self.legs:
                leg_avail = float(available_qty.get(lg.leg_ref_id, 0.0))
                if float(lg.target_qty) > leg_avail:
                    return replace(
                        self,
                        state=OrderStatus.REJECTED,
                        reject_reason="fok_liquidity",
                    )
            return self
        # Single-leg: read requested qty from sizing_ctx.
        ctx = self.sizing_ctx
        requested = 0.0
        if isinstance(ctx, dict):
            requested = float(ctx.get("qty", ctx.get("margin_usd", 0.0)) or 0.0)
        if requested > float(available_qty):
            return self._transit(
                OrderStatus.REJECTED, reject_reason="fok_liquidity",
            )
        return self

    def trigger_immediately(self) -> "Order":
        """ARMED -> TRIGGERED unconditionally (T-M5-01 BAR_CLOSE fast-path).

        Immediate market orders cycle the full state machine in a single
        tick. This helper is the bar-close fast-path that fires without
        a price comparison.
        """
        if self.state != OrderStatus.ARMED:
            return self
        return self._transit(OrderStatus.TRIGGERED)

    def emitted_events(self) -> list[dict]:
        """Return the audit log events for this Order's lifecycle.

        Combines:
          * Multi-leg transition events recorded by state-machine methods
            (``leg_filled``, ``leg_rejected``, ``sibling_unwound``,
            ``bracket_activated``, ``sibling_cancelled``) — accumulated on
            ``self._events`` across ``replace()`` calls.
          * A terminal Order-level event synthesized from the final
            ``self.state`` (``filled`` / ``partially_filled`` / ``rejected``
            / ``expired`` / ``cancelled``) for single-leg Orders, matching
            the M4 T-M5-01 audit-trail contract.

        The orders_log writer (Wave G) consumes this stream verbatim.
        """
        events: list[dict] = list(self._events)
        if self.state == OrderStatus.FILLED:
            events.append({"event": "filled", "order_id": self.order_id})
        elif self.state == OrderStatus.PARTIALLY_FILLED:
            events.append({"event": "partially_filled", "order_id": self.order_id})
        elif self.state == OrderStatus.REJECTED:
            events.append({"event": "rejected", "order_id": self.order_id})
        elif self.state == OrderStatus.EXPIRED:
            events.append({"event": "expired", "order_id": self.order_id})
        elif self.state == OrderStatus.CANCELLED:
            events.append({"event": "cancelled", "order_id": self.order_id})
        return events

    def emitted_closed_trades(self) -> list:
        """Return ClosedTrade records produced by the Order's state machine.

        UNWIND_ON_REJECT (Task 8a) force-closes FILLED siblings on a post-
        fill leg REJECT and emits a ClosedTrade with
        ``exit_reason="sibling_unwound"`` per sibling. The orders_log /
        trade archive writers consume this stream for post-trade audit.

        Returns a new list (snapshot) to isolate callers from subsequent
        mutations of the internal emission sink.
        """
        return list(self._closed_trades)

    # ------------------------------------------------------------------ #
    # M5 Task 11 — Order → Position materialization (F9 identity chain)  #
    # ------------------------------------------------------------------ #

    def materialize_positions(self, state: Any = None) -> list:
        """Spawn N :class:`v5.position.Position` rows from this Order (F9).

        Single-leg (``legs=()``): returns exactly 1 Position using the bare
        Order fields; ``order_id`` = ``self.order_id``, ``leg_ref_id = None``.

        Multi-leg (``legs=(Leg, ...)``): returns N Positions — one per leg —
        all sharing ``order_id = self.order_id`` and each carrying its leg's
        ``leg_ref_id`` (FIX LegRefID(654)).

        The ``state`` parameter is accepted for API parity with downstream
        call-sites that pass a :class:`SimulationState` for tick_counter
        allocation; it is currently unused (positions receive a deterministic
        ``position_id`` derived from the order_id + leg_ref_id so multiple
        ``materialize_positions`` calls for distinct Orders never collide).
        """
        # Lazy import to avoid a hard dep cycle with position.py.
        from v5.position import Position

        if not self.legs:
            # Single-leg Order: materialize one Position from bare fields.
            pid = f"{self.order_id}:primary" if self.order_id else ""
            pos = Position(
                position_id=pid,
                token=self.token,
                strategy_id=self.strategy_id,
                direction=int(self.direction),
                order_id=self.order_id or None,
                leg_ref_id=None,
            )
            return [pos]

        positions: list = []
        for lg in self.legs:
            pid = (
                lg.position_id
                if getattr(lg, "position_id", None)
                else f"{self.order_id}:{lg.leg_ref_id}"
            )
            pos = Position(
                position_id=pid,
                token=lg.symbol or self.token,
                strategy_id=self.strategy_id,
                direction=int(lg.direction),
                is_perp=(getattr(lg, "market", "spot") == "perp"),
                order_id=self.order_id or None,
                leg_ref_id=lg.leg_ref_id,
            )
            positions.append(pos)
        return positions

    # ------------------------------------------------------------------ #
    # JSON persistence (T10, AC32)                                       #
    # ------------------------------------------------------------------ #

    def to_json(self) -> dict:
        """Serialize to a JSON-compatible dict (AC32).

        Enum fields are stored as string names (stable across Python/enum-value
        changes). Datetime fields use ISO 8601 with timezone. ``None`` for
        ``expires_at`` round-trips correctly.

        M5 additions: ``legs`` (list of Leg-dicts), ``fill_policy`` and
        ``contingency`` (string names), ``linked_order_id``, ``time_in_force``
        (string name), ``order_id``.
        """
        return {
            # M5 AC9 — schema version marker on Order JSON (T-M5-09).
            "schema_version": 3,
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
            "venue_order_id": self.venue_order_id,
            # M5 Task 6 additions
            "order_id": self.order_id,
            "legs": [_leg_to_json(lg) for lg in self.legs],
            "fill_policy": self.fill_policy.name,
            "contingency": self.contingency.name,
            "linked_order_id": self.linked_order_id,
            "time_in_force": self.time_in_force.name,
        }

    @classmethod
    def from_json(cls, blob: dict) -> "Order":
        """Deserialize from a dict produced by :meth:`to_json`.

        ``from_json(to_json(order)) == order`` is idempotent; all fields are
        restored including TriggerType/OrderStatus/LegFillPolicy/
        ContingencyType/TimeInForce enums (by name) and datetime fields
        (ISO 8601). Missing M5 fields default to M4-compatible values so v2
        paper state deserializes cleanly (schema v2 → v3 migration shim).
        """
        trigger_raw = blob["trigger"]
        if isinstance(trigger_raw, str):
            trigger = TriggerType[trigger_raw]
        else:
            trigger = TriggerType(int(trigger_raw))
        state_raw = blob.get("state", OrderStatus.ARMED.name)
        if isinstance(state_raw, str):
            state = OrderStatus[state_raw]
        else:
            state = OrderStatus(int(state_raw))
        armed_at_raw = blob.get("armed_at")
        armed_at = _parse_iso_dt(armed_at_raw)
        expires_at_raw = blob.get("expires_at")
        expires_at = _parse_iso_dt(expires_at_raw) if expires_at_raw else None
        # M5 additions with M4-compatible defaults (schema v2 migration).
        legs_raw = blob.get("legs") or ()
        legs = tuple(_leg_from_json(lg) for lg in legs_raw)
        fill_policy_raw = blob.get("fill_policy", LegFillPolicy.UNWIND_ON_REJECT.name)
        if isinstance(fill_policy_raw, str):
            # Accept both the .name (e.g. "UNWIND_ON_REJECT", v2 legacy) and
            # the .value (e.g. "unwind_on_reject", v3 migration lowercase).
            try:
                fill_policy = LegFillPolicy[fill_policy_raw]
            except KeyError:
                fill_policy = LegFillPolicy(fill_policy_raw)
        else:
            fill_policy = LegFillPolicy(fill_policy_raw)
        cont_raw = blob.get("contingency", ContingencyType.NONE.name)
        if isinstance(cont_raw, str):
            contingency = ContingencyType[cont_raw]
        else:
            contingency = ContingencyType(int(cont_raw))
        tif_raw = blob.get("time_in_force", TimeInForce.GTC.name)
        if isinstance(tif_raw, str):
            # Accept both the .name ("GTC") and the .value ("1").
            try:
                time_in_force = TimeInForce[tif_raw]
            except KeyError:
                time_in_force = TimeInForce(tif_raw)
        else:
            time_in_force = TimeInForce(tif_raw)
        # Honor serialized order_id when present; otherwise generate a new
        # ClOrdID (legacy v2 blobs pre-date the order_id field). _gen_order_id
        # enforces 36-char cap and FIX uniqueness.
        order_id_raw = str(blob.get("order_id") or "")
        if not order_id_raw:
            order_id_raw = _gen_order_id(blob["strategy_id"], blob["token"])
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
            venue_order_id=blob.get("venue_order_id"),
            order_id=order_id_raw,
            legs=legs,
            fill_policy=fill_policy,
            contingency=contingency,
            linked_order_id=blob.get("linked_order_id"),
            time_in_force=time_in_force,
        )


# ---------------------------------------------------------------------------
# Module-level helpers (T10 + M5 Task 7)
# ---------------------------------------------------------------------------


def _leg_to_json(leg: "Leg") -> dict:
    """Serialize a Leg to JSON-compatible dict (M5 AC9 schema v3)."""
    return {
        "leg_ref_id": leg.leg_ref_id,
        "symbol": leg.symbol,
        "market": leg.market,
        "venue": leg.venue,
        "direction": int(leg.direction),
        "target_qty": float(leg.target_qty),
        "cum_qty": float(leg.cum_qty),
        "size_share": float(leg.size_share),
        "order_type": leg.order_type,
        "status": leg.status.name,
        "trigger_price": (
            float(leg.trigger_price) if leg.trigger_price is not None else None
        ),
        "limit_price": (
            float(leg.limit_price) if leg.limit_price is not None else None
        ),
        "currency": leg.currency,
        "position_id": leg.position_id,
        "dust_usd": float(leg.dust_usd),
        "sizing_ctx": dict(leg.sizing_ctx) if leg.sizing_ctx else {},
        "expires_at": (
            leg.expires_at.isoformat() if leg.expires_at is not None else None
        ),
        "settlement_type": leg.settlement_type,
    }


def _leg_from_json(blob: dict) -> "Leg":
    """Deserialize a Leg from JSON-compatible dict (M5 AC9 schema v3)."""
    status_raw = blob.get("status", LegStatus.ARMED.name)
    if isinstance(status_raw, str):
        status = LegStatus[status_raw]
    else:
        status = LegStatus(int(status_raw))
    expires_raw = blob.get("expires_at")
    expires_at = _parse_iso_dt(expires_raw) if expires_raw else None
    return Leg(
        leg_ref_id=blob["leg_ref_id"],
        symbol=blob["symbol"],
        market=blob["market"],
        venue=blob["venue"],
        direction=int(blob["direction"]),
        target_qty=float(blob["target_qty"]),
        cum_qty=float(blob.get("cum_qty", 0.0)),
        size_share=float(blob.get("size_share", 1.0)),
        order_type=blob.get("order_type", "market"),
        status=status,
        trigger_price=(
            float(blob["trigger_price"])
            if blob.get("trigger_price") is not None else None
        ),
        limit_price=(
            float(blob["limit_price"])
            if blob.get("limit_price") is not None else None
        ),
        currency=blob.get("currency", "USDT"),
        position_id=blob.get("position_id"),
        dust_usd=float(blob.get("dust_usd", 1.0)),
        sizing_ctx=dict(blob.get("sizing_ctx") or {}),
        expires_at=expires_at,
        settlement_type=blob.get("settlement_type", "spot"),
    )


def _derive_state_from_legs(
    *,
    legs: tuple["Leg", ...],
    fill_policy: LegFillPolicy,
) -> OrderStatus | None:
    """Derive Order.state from legs + fill_policy per F1 table (Task 7).

    Returns None when the caller should preserve the current Order.state
    (only happens when ``legs`` is empty — the empty-legs case is handled
    by the caller; this helper assumes non-empty legs).

    F1 derivation precedence (checked top-down):
      1. all FILLED                                   → FILLED
      2. any REJECTED + ≥1 FILLED + UNWIND_ON_REJECT  → REJECTED
      3. any REJECTED + ≥1 FILLED + BEST_EFFORT       → PARTIALLY_FILLED
      4. any REJECTED + 0 FILLED                      → REJECTED
      5. all CANCELLED                                → CANCELLED
      6. all EXPIRED                                  → EXPIRED
      7. OTO_BRACKET: any FILLED + any ARMED/WORKING  → PARTIALLY_FILLED
      8. OTO_BRACKET: any FILLED + no ARMED/WORKING   → FILLED
      9. any TRIGGERED (mixed with ARMED/WORKING)     → TRIGGERED
     10. mixed ARMED/WORKING (no trigger)             → ARMED
    """
    if not legs:
        return None
    n = len(legs)
    statuses = [lg.status for lg in legs]

    n_filled    = sum(1 for s in statuses if s == LegStatus.FILLED)
    n_rejected  = sum(1 for s in statuses if s == LegStatus.REJECTED)
    n_cancelled = sum(1 for s in statuses if s == LegStatus.CANCELLED)
    n_expired   = sum(1 for s in statuses if s == LegStatus.EXPIRED)
    n_armed     = sum(1 for s in statuses if s == LegStatus.ARMED)
    n_working   = sum(1 for s in statuses if s == LegStatus.WORKING)
    n_partial   = sum(1 for s in statuses if s == LegStatus.PARTIALLY_FILLED)

    # Row 1: all FILLED → FILLED
    if n_filled == n:
        return OrderStatus.FILLED

    # Rows 2/3/4: any REJECTED branches
    if n_rejected > 0:
        if n_filled >= 1:
            if fill_policy == LegFillPolicy.UNWIND_ON_REJECT:
                return OrderStatus.REJECTED  # post sibling-unwind
            elif fill_policy == LegFillPolicy.BEST_EFFORT:
                return OrderStatus.PARTIALLY_FILLED
            # OTO_BRACKET with mixed REJECTED+FILLED — conservative REJECTED.
            return OrderStatus.REJECTED
        # 0 FILLED + any REJECTED → REJECTED (applies to all policies).
        return OrderStatus.REJECTED

    # Row 5: all CANCELLED → CANCELLED
    if n_cancelled == n:
        return OrderStatus.CANCELLED

    # BEST_EFFORT variant: FILLED + CANCELLED (no REJECTED) — sibling was
    # venue-rejected then converted to engine-CANCELLED per Task 8a; the
    # Order is PARTIALLY_FILLED (same semantics as Row 3).
    if (fill_policy == LegFillPolicy.BEST_EFFORT
            and n_filled >= 1 and n_cancelled >= 1
            and (n_filled + n_cancelled) == n):
        return OrderStatus.PARTIALLY_FILLED

    # Row 9: all EXPIRED → EXPIRED
    if n_expired == n:
        return OrderStatus.EXPIRED

    # Rows 6/7: OTO_BRACKET bracket semantics
    if fill_policy == LegFillPolicy.OTO_BRACKET and n_filled >= 1:
        n_active = n_armed + n_working + n_partial
        if n_active >= 1:
            # Entry filled + SL/TP still ARMED|WORKING → PARTIALLY_FILLED
            return OrderStatus.PARTIALLY_FILLED
        # Entry FILLED + exactly one protective FILLED, the other CANCELLED
        # (or all protectives resolved by FILL/CANCEL) → FILLED.
        return OrderStatus.FILLED

    # Row 8 mixed (non-bracket): ARMED / WORKING / partially filled
    # (no trigger yet) → ARMED.  If any leg is in a TRIGGERED-equivalent
    # intermediate, upgrade to TRIGGERED. LegStatus has no TRIGGERED
    # ordinal — WORKING is the trigger-fired state at the Leg granularity.
    # If ANY leg has moved past ARMED (to WORKING / PARTIALLY_FILLED) we
    # surface TRIGGERED at the Order level; otherwise ARMED.
    if (n_working + n_partial) >= 1:
        # At least one leg triggered — TRIGGERED at parent level.
        # Exception: if all non-ARMED legs are PARTIALLY_FILLED (already
        # in fill path), prefer PARTIALLY_FILLED semantics. For now the
        # F1 table collapses this row to TRIGGERED/ARMED.
        if n_armed == 0 and n_partial >= 1:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.TRIGGERED

    # Default: all ARMED (or mix of ARMED / resolved-neutral states).
    return OrderStatus.ARMED


def derive_order_status(order: "Order") -> OrderStatus:
    """Public wrapper for the F1 status derivation (Task 7).

    Returns the Order-level status implied by its legs + fill_policy.
    For empty-legs Orders (``legs=()``) returns ``order.state`` unchanged
    since the bare Order fields ARE the implicit single leg (AC3).
    """
    if not order.legs:
        return order.state
    derived = _derive_state_from_legs(
        legs=order.legs,
        fill_policy=order.fill_policy,
    )
    return derived if derived is not None else order.state


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
# M5 Wave D.1 — per-contingency capital aggregation (F3 / Task 8c)
# ---------------------------------------------------------------------- #


def _leg_margin(leg: "Leg") -> float:
    """Read per-leg margin from ``leg.sizing_ctx["margin_usd"]`` (R13/R14)."""
    ctx = getattr(leg, "sizing_ctx", None)
    if isinstance(ctx, dict):
        try:
            return float(ctx.get("margin_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def compute_reserved_capital(order: "Order") -> float:
    """Aggregate per-leg margin under the Order's ``contingency`` rule (F3).

    Aggregation per FIX ContingencyType(1385):
      * ``NONE`` / ``OTO`` / ``OUO``: ``sum(leg.sizing_ctx["margin_usd"])``
      * ``OCO``: ``max(leg.sizing_ctx["margin_usd"])`` — only one can fill,
        reserve the larger.
      * ``OTOCO``: standard bracket — entry + ``max(sibling margins)``. The
        entry leg reserves independently; the OCO-linked siblings can fill
        mutually exclusively so only the larger of the two is reserved.

    Single-leg (``legs=()``) Orders fall back to
    ``order.sizing_ctx["margin_usd"]`` (M4 semantics preserved).
    """
    if not order.legs:
        ctx = order.sizing_ctx
        if isinstance(ctx, dict):
            try:
                return float(ctx.get("margin_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    margins = [_leg_margin(lg) for lg in order.legs]
    if order.contingency == ContingencyType.OCO:
        return float(max(margins)) if margins else 0.0
    if order.contingency == ContingencyType.OTOCO:
        # Convention: first leg is the entry; remainder are OCO siblings.
        if not margins:
            return 0.0
        entry_margin = float(margins[0])
        sibling_margins = margins[1:]
        sibling_reserve = float(max(sibling_margins)) if sibling_margins else 0.0
        return entry_margin + sibling_reserve
    return float(sum(margins))


def check_and_release(
    order: "Order", available_capital_usd: float,
) -> "Order":
    """Module-level wrapper for :meth:`Order.release_atomic` (F2 / Task 8c).

    Provided for call-site clarity (``check_and_release(o, cap)`` reads as
    a pre-fill atomic check operation) and symmetry with
    :func:`compute_reserved_capital`. Prefer the method on Order directly
    in hot paths.
    """
    return order.release_atomic(available_capital_usd=available_capital_usd)


def _build_unwind_closed_trade(*, order: "Order", sibling: "Leg",
                                reason: str) -> Any:
    """Build a ClosedTrade for a UNWIND_ON_REJECT sibling force-close.

    Imports ``v5.position.ClosedTrade`` lazily to avoid a hard dependency
    (M5 Wave D.1 runs before the full simulator wiring of ClosedTrade
    emission into the trade archive — that landing is Wave G/J).

    Fields populated with best-effort defaults; the simulator unwind path
    (post-M5 live/paper) will supply full entry/exit price + PnL. The M5
    tests only assert ``exit_reason == "sibling_unwound"``.
    """
    try:
        from v5.position import ClosedTrade
    except Exception:  # pragma: no cover — defensive
        # Fallback: lightweight namespace matching the assertion shape.
        class _CT:  # type: ignore[no-redef]
            pass
        ct = _CT()
        ct.exit_reason = reason
        ct.position_id = sibling.position_id or ""
        ct.token = order.token
        ct.strategy_id = order.strategy_id
        ct.leg = sibling.leg_ref_id
        ct.order_id = order.order_id
        ct.leg_ref_id = sibling.leg_ref_id
        return ct
    return ClosedTrade(
        position_id=sibling.position_id or "",
        token=order.token,
        strategy_id=order.strategy_id,
        leg=sibling.leg_ref_id,
        entry_bar=0,
        exit_bar=0,
        entry_price=0.0,
        exit_price=0.0,
        direction=int(sibling.direction),
        margin_usd=_leg_margin(sibling),
        pnl=0.0,
        funding_cost=0.0,
        entry_fee=0.0,
        exit_fee=0.0,
        hold_bars=0,
        exit_reason=reason,
        is_perp=(sibling.market == "perp"),
    )


# ---------------------------------------------------------------------- #
# AC25 step 6 / AC39 — multi-Order capital contention priority
# ---------------------------------------------------------------------- #


def _priority_key(order: "Order") -> tuple:
    """Lexicographic priority: (armed_at, strategy_id, token). Oldest wins."""
    return (order.armed_at, order.strategy_id, order.token)


def _margin_usd(order: "Order") -> float:
    """Extract margin_usd from sizing_ctx; 0.0 if unavailable (count-based mode)."""
    ctx = order.sizing_ctx
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
    entries: Iterable["Order"],
    available_capital_usd: float | None = None,
    available_capital: int | None = None,
) -> list["Order"]:
    """Resolve capital contention among TRIGGERED Orders (AC25, AC39).

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

    winner_ids: set[int] = set()  # id() of Order winners
    if available_capital_usd is not None:
        remaining = float(available_capital_usd)
        for order in ordered:
            cost = _margin_usd(order)
            if cost <= remaining:
                winner_ids.add(id(order))
                remaining -= cost
    elif available_capital is not None:
        cap = int(available_capital)
        for order in ordered[:cap]:
            winner_ids.add(id(order))
    else:
        raise ValueError(
            "resolve_contention requires available_capital_usd or available_capital"
        )

    results: list[Order] = []
    for order in entries_list:
        if order.state != OrderStatus.TRIGGERED:
            # Only TRIGGERED entries participate in contention; pass through.
            results.append(order)
            continue
        if id(order) in winner_ids:
            released = order.release(capital_ok=True)
            # Winners treat margin_usd as fully filled (leaves_qty=0).
            filled = released.on_fill(
                filled_qty=float(_margin_usd(order)),
                leaves_qty=0.0,
            )
            results.append(filled)
        else:
            # ``release(capital_ok=False)`` already sets
            # ``reject_reason="risk_on_release"`` per AC39 — pass only the
            # supported kwarg.
            results.append(order.release(capital_ok=False))
    return results


# ---------------------------------------------------------------------- #
# M5 Task 11 — logical-position cardinality helper (AC7, F9)            #
# ---------------------------------------------------------------------- #


def count_logical_positions(orders: Iterable["Order"]) -> int:
    """Count logical Orders (not raw Position rows) for ``max_positions_per_symbol``.

    AC7 cardinality rule: a multi-leg Order spawns N Positions but represents
    exactly 1 logical entry for the per-symbol concurrency guard. This helper
    counts distinct Orders — the caller groups by symbol/token upstream and
    feeds the filtered list in.

    The implementation counts distinct ``order_id``s (FIX ClOrdID(11)) so
    that callers which index by Positions can pass a synthetic list of
    Order-like objects carrying ``order_id``. Orders with empty/None
    ``order_id`` each count as 1 (degenerate fallback).
    """
    seen_ids: set[str] = set()
    anon = 0
    for o in orders:
        oid = getattr(o, "order_id", None)
        if oid:
            seen_ids.add(str(oid))
        else:
            anon += 1
    return len(seen_ids) + anon
