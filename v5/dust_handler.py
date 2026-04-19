"""M5 Task 9 — Dust cascade handler (AC-P10a/b/c, design §F5).

Cross-leg dust-promotion cascade for linked Orders. Implements the
AC-P10a/b/c invariants:

  * AC-P10a (idempotency): when both linked legs dust-promote in the same
    bar, the ``linked_exit`` force-close fires AT MOST ONCE per position.
    ``_fired_order_ids: set[str]`` records cascaded order_ids; re-entry is
    a no-op.

  * AC-P10b (ARMED-sibling cascade): when a WORKING leg dust-promotes, an
    ARMED sibling (no Position yet) transitions ARMED -> CANCELLED with
    ``cancel_reason="linked_exit"``. NOT force_closed — there is no
    Position to close at the ARMED stage.

  * AC-P10c (per-leg dust thresholds): ``Leg.dust_usd`` is per-leg. Spot
    and perp may use different venue min-notional. The PROMOTING leg's
    own threshold is used to identify promotion; the CASCADED sibling
    leg uses its OWN threshold to evaluate post-close residual handling
    (zero_out vs retain).

The handler is stateful only in the ``_fired_order_ids`` set — all other
state lives on the Order / Leg / Position it is passed. No module-level
mutable state is introduced (unlike the one-shot AC38 audit flag in
v5.orders).

M5 boundary: this module MUST NOT depend on v5.simulator or v5.engine —
those are downstream consumers. It only imports data shapes from
v5.orders + v5.position.

See:
  * brief AC8 = AC-P10a/b/c
  * design §F5 — two-threshold mechanism
  * task 9 in tasks.md
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Literal, Mapping

from v5.orders import Leg, LegStatus, Order
from v5.position import ClosedTrade, Position


# --------------------------------------------------------------------------- #
# Internal helper — Order subclass that surfaces per-leg cascade events       #
# via ``emitted_events()``. Needed because Order is frozen + slots=True, so   #
# we cannot add an ``_extra_events`` field without subclassing.               #
# --------------------------------------------------------------------------- #


class _OrderWithCascadeEvents(Order):
    """Order subclass that appends cascade events to ``emitted_events()``.

    We subclass because ``Order`` is ``frozen=True, slots=True`` and the
    dust cascade needs to surface per-leg ``cancelled`` events (with
    ``leg_ref_id`` + ``cancel_reason="linked_exit"``) without mutating
    ``orders.py`` (Wave D.1 owns that file in parallel).

    ``isinstance(x, Order)`` is preserved so downstream consumers that
    do type checks continue to accept the returned value.
    """

    __slots__ = ("_extra_events",)

    def __init__(self, base_order: Order, extra_events: Iterable[dict]) -> None:
        # Copy every dataclass field from the base order onto this instance.
        for fname in base_order.__dataclass_fields__:
            object.__setattr__(self, fname, getattr(base_order, fname))
        object.__setattr__(self, "_extra_events", tuple(extra_events))

    def emitted_events(self) -> list[dict]:
        events = super().emitted_events()
        events.extend(dict(e) for e in self._extra_events)
        return events


def _wrap_with_events(order: Order, events: Iterable[dict]) -> Order:
    """Return an Order whose ``emitted_events()`` includes ``events``.

    If ``events`` is empty the original order is returned unchanged.
    """
    evs = tuple(events)
    if not evs:
        return order
    return _OrderWithCascadeEvents(order, evs)


# --------------------------------------------------------------------------- #
# DustHandler                                                                  #
# --------------------------------------------------------------------------- #


class DustHandler:
    """Cross-leg dust-promotion cascade handler (AC-P10a/b/c).

    Holds the small amount of state required for idempotency (the set of
    order_ids that have already cascaded) and accumulates the
    ``ClosedTrade`` emissions from WORKING-sibling force-closes so the
    caller can flush them once per bar.

    Usage::

        handler = DustHandler()
        new_order = handler.on_dust_promotion(
            order=order, leg_ref_id="A", positions=open_positions,
        )
        for closed in handler.emitted_closed_trades():
            state.closed_trades.append(closed)

    The handler is single-bar reusable but long-lived: ``_fired_order_ids``
    persists across bars since a cascaded Order is terminal and cannot
    re-fire.
    """

    def __init__(self) -> None:
        self._fired_order_ids: set[str] = set()
        self._closed_trades: list[ClosedTrade] = []

    # ------------------------------------------------------------------ #
    # Public entry points                                                #
    # ------------------------------------------------------------------ #

    def on_dust_promotion(
        self,
        *,
        order: Order,
        leg_ref_id: str,
        positions: Iterable[Position] | None = None,
    ) -> Order:
        """Evaluate AC-P10 cascade for ``leg_ref_id`` dust-promoting on
        ``order``.

        Returns a (possibly new) Order with sibling legs updated per
        AC-P10b/c and cascade events attached. Re-invocation for the same
        ``order.order_id`` is a no-op (AC-P10a idempotency).

        Parameters
        ----------
        order :
            The Order whose ``leg_ref_id`` leg has just dust-promoted.
        leg_ref_id :
            Identifier of the dust-promoting leg.
        positions :
            Open Positions linked to this Order. Used to locate sibling
            positions for WORKING-leg force-close (AC-P10c). If None, no
            ClosedTrades are emitted — useful for unit tests that exercise
            only the idempotency / ARMED-sibling paths.
        """
        # AC-P10a idempotency — cascade fires at most once per order_id.
        if order.order_id in self._fired_order_ids:
            return order
        self._fired_order_ids.add(order.order_id)

        pos_by_leg_ref = _index_positions_by_leg_ref(positions or ())

        new_legs: list[Leg] = []
        cascade_events: list[dict] = []

        for leg in order.legs:
            if leg.leg_ref_id == leg_ref_id:
                # The promoting leg — no status change here; caller has
                # already handled its reduce/close prior to invoking us.
                # Still emit a ClosedTrade for it if we have a matching
                # Position (AC-P10a exactly-one invariant applies equally
                # to the promoting leg).
                new_legs.append(leg)
                promoting_pos = pos_by_leg_ref.get(leg.leg_ref_id)
                if promoting_pos is not None:
                    self._closed_trades.append(
                        _build_linked_exit_trade(
                            leg=leg,
                            position=promoting_pos,
                            residual_usd=0.0,
                        )
                    )
                continue

            # Sibling leg — route per AC-P10b / AC-P10c.
            if leg.status == LegStatus.ARMED:
                # AC-P10b: no Position yet — transition ARMED -> CANCELLED.
                new_legs.append(replace(leg, status=LegStatus.CANCELLED))
                cascade_events.append({
                    "event": "cancelled",
                    "leg_ref_id": leg.leg_ref_id,
                    "cancel_reason": "linked_exit",
                })
            elif leg.status in _WORKING_LIKE:
                # AC-P10c: WORKING (or PARTIALLY_FILLED) sibling — force-close
                # the linked Position, emit one ClosedTrade, mark leg CANCELLED
                # (sibling's working order is cancelled; Position is closed).
                sib_pos = pos_by_leg_ref.get(leg.leg_ref_id)
                if sib_pos is not None:
                    # AC-P10c per-leg threshold: use SIBLING's own dust_usd
                    # to evaluate post-close residual handling.
                    residual_usd = _estimate_residual_usd(sib_pos)
                    post_action = self.evaluate_post_close(
                        leg=leg, residual_usd=residual_usd,
                    )
                    self._closed_trades.append(
                        _build_linked_exit_trade(
                            leg=leg,
                            position=sib_pos,
                            residual_usd=(
                                residual_usd if post_action == "retain" else 0.0
                            ),
                        )
                    )
                new_legs.append(replace(leg, status=LegStatus.CANCELLED))
                cascade_events.append({
                    "event": "cancelled",
                    "leg_ref_id": leg.leg_ref_id,
                    "cancel_reason": "linked_exit",
                })
            else:
                # Terminal sibling (FILLED / EXPIRED / REJECTED / CANCELLED) —
                # nothing to cascade.
                new_legs.append(leg)

        new_order = replace(order, legs=tuple(new_legs))
        return _wrap_with_events(new_order, cascade_events)

    def emitted_closed_trades(self) -> list[ClosedTrade]:
        """Return accumulated ClosedTrades from WORKING-sibling force-closes.

        The caller flushes these into the engine's closed-trade log. The
        list is a live handle — callers that call ``emitted_closed_trades``
        multiple times must copy it if they want a snapshot.
        """
        return self._closed_trades

    # ------------------------------------------------------------------ #
    # AC-P10c — per-leg threshold helpers (public for testing)           #
    # ------------------------------------------------------------------ #

    def identify_promotion(
        self,
        *,
        order: Order,
        leg_residuals_usd: Mapping[str, float],
    ) -> str | None:
        """AC-P10c identify-promotion: return the ``leg_ref_id`` whose
        residual USD has fallen below its OWN ``dust_usd`` threshold.

        Returns None if no leg has crossed its threshold. When multiple
        legs simultaneously cross, the first in ``order.legs`` order wins
        (deterministic ordering).
        """
        for leg in order.legs:
            residual = leg_residuals_usd.get(leg.leg_ref_id)
            if residual is None:
                continue
            if float(residual) < float(leg.dust_usd):
                return leg.leg_ref_id
        return None

    def evaluate_post_close(
        self,
        *,
        leg: Leg,
        residual_usd: float,
    ) -> Literal["zero_out", "retain"]:
        """AC-P10c post-close: evaluate a cascaded sibling's residual vs
        its OWN ``Leg.dust_usd`` threshold.

        Returns ``"zero_out"`` when the residual is at or below the leg's
        own dust threshold (full cleanup; zero the position's remaining
        qty). Returns ``"retain"`` when the residual exceeds its own
        threshold (keep the residual on the books).
        """
        if float(residual_usd) < float(leg.dust_usd):
            return "zero_out"
        return "retain"


# --------------------------------------------------------------------------- #
# Module-level helpers                                                        #
# --------------------------------------------------------------------------- #


_WORKING_LIKE: frozenset = frozenset({
    LegStatus.WORKING,
    LegStatus.PARTIALLY_FILLED,
})


def _index_positions_by_leg_ref(
    positions: Iterable[Position],
) -> dict[str, Position]:
    """Build a {leg_ref_id: Position} index from the supplied Positions.

    Silent on duplicates (last-writer-wins) because the engine guarantees
    ≤1 Position per Order-leg in normal operation.
    """
    idx: dict[str, Position] = {}
    for pos in positions:
        leg_ref = getattr(pos, "leg_ref_id", None)
        if leg_ref:
            idx[leg_ref] = pos
    return idx


def _estimate_residual_usd(position: Position) -> float:
    """Notional USD of the still-open Position at its entry price.

    Used as a proxy for "what remains to be closed" — the actual fill
    price would depend on market conditions at cascade-time, but the
    handler is invoked before the close leg executes, so entry_price is
    the best available estimate.
    """
    return abs(float(position.quantity)) * float(position.entry_price)


def _build_linked_exit_trade(
    *,
    leg: Leg,
    position: Position,
    residual_usd: float,
) -> ClosedTrade:
    """Construct a minimal linked-exit ClosedTrade for the force-closed
    sibling position.

    The handler emits the ClosedTrade record; the engine owns fee /
    slippage / funding adjustments at the caller layer. We fill in the
    identity fields (position_id, token, strategy_id, direction, leg) and
    carry ``exit_reason="linked_exit"`` per AC-P10b/c.
    """
    # The ClosedTrade requires many positional fields — most are dummy
    # here and will be set authoritatively by the engine's reduce path.
    # The handler's sole job is to produce a correctly-identified,
    # linked_exit-tagged trade record so downstream accounting can
    # distinguish the cascade-origin from primary exits.
    return ClosedTrade(
        position_id=position.position_id,
        token=position.token,
        strategy_id=position.strategy_id,
        leg=getattr(position, "leg", "primary"),
        entry_bar=int(getattr(position, "entry_bar", 0)),
        exit_bar=int(getattr(position, "entry_bar", 0)),
        entry_price=float(position.entry_price),
        exit_price=float(position.entry_price),
        direction=int(position.direction),
        margin_usd=float(position.margin_usd),
        pnl=0.0,
        funding_cost=float(getattr(position, "cumulative_funding", 0.0)),
        entry_fee=0.0,
        exit_fee=0.0,
        hold_bars=0,
        exit_reason="linked_exit",
        is_perp=bool(getattr(position, "is_perp", False)),
    )
