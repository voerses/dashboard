"""V5 Portfolio Backtest — Composable scale-check helper DSL (tp_ladder_{atr,r,price}, breakeven_plus_runner)."""
from __future__ import annotations

from typing import Callable, Literal, Optional, Union

from .position import Position
from .strategy_api import ScaleAction


# ---------------------------------------------------------------------------
# Helper state namespacing (Q3)
# ---------------------------------------------------------------------------

def _state_key(prefix: str, rungs) -> str:
    """Build a namespaced `_helper_state` key encoding the rung config.

    Q3: Same position can host multiple ladders — key encodes rungs so they
    don't collide. State is ephemeral (not serialized) — rebuilt from
    `pos.scaling_events` after restart.
    """
    return f"{prefix}__{str(sorted(rungs))}"


def _ensure_state(pos: Position) -> dict:
    if not hasattr(pos, "_helper_state") or pos._helper_state is None:
        pos._helper_state = {}
    return pos._helper_state


# ---------------------------------------------------------------------------
# tp_ladder_atr
# ---------------------------------------------------------------------------

def tp_ladder_atr(
    rungs: list[tuple[float, float]],
) -> Callable[[Position, object], Union[ScaleAction, list[ScaleAction], None]]:
    """Take-profit ladder keyed on profit in ATR units.

    ``rungs = [(profit_atr_threshold, fraction), ...]``.  Each rung fires at
    most once per position. Restart-idempotent: if `_helper_state` is empty
    but `pos.scaling_events` already contains reduce events consistent with
    prior firings, those rungs are marked fired and not re-fired.
    """
    rungs_sorted = sorted(rungs, key=lambda r: r[0])
    state_key = _state_key("tp_ladder_atr", rungs_sorted)

    def _rebuild_fired_from_events(pos: Position) -> set:
        """Rebuild the fired-rung index set by matching prior reduce events
        against the rung thresholds (in ATR units)."""
        fired: set = set()
        # Copy events; consume one event per rung (in order) that satisfies
        # the rung threshold.
        consumed: set = set()
        for idx, (threshold, _frac) in enumerate(rungs_sorted):
            for ev_i, ev in enumerate(pos.scaling_events):
                if ev_i in consumed:
                    continue
                if getattr(ev, "kind", None) != "reduce":
                    continue
                if getattr(ev, "is_stop_like", False):
                    continue
                atr_at = getattr(ev, "atr_at_event", None)
                if atr_at is None or atr_at <= 0:
                    continue
                if pos.direction == 1:
                    profit_atr = (ev.fill_price - pos.entry_price) / atr_at
                else:
                    profit_atr = (pos.entry_price - ev.fill_price) / atr_at
                if profit_atr + 1e-9 >= threshold:
                    fired.add(idx)
                    consumed.add(ev_i)
                    break
        return fired

    def fn(pos: Position, ctx) -> Union[ScaleAction, list[ScaleAction], None]:
        state = _ensure_state(pos)
        fired: set = state.get(state_key)
        if fired is None:
            fired = _rebuild_fired_from_events(pos)
            state[state_key] = fired

        atr = getattr(ctx, "atr", 0.0) or 0.0
        if atr <= 0:
            return None
        close = ctx.close
        if pos.direction == 1:
            profit_atr = (close - pos.entry_price) / atr
        else:
            profit_atr = (pos.entry_price - close) / atr

        actions: list[ScaleAction] = []
        for idx, (threshold, fraction) in enumerate(rungs_sorted):
            if idx in fired:
                continue
            if profit_atr + 1e-12 >= threshold:
                actions.append(
                    ScaleAction(
                        qty_delta=-abs(pos.quantity) * fraction * pos.direction,
                        reason=f"tp_rung_{idx}_atr",
                        is_stop_like=False,
                    )
                )
                fired.add(idx)

        if not actions:
            return None
        return actions if len(actions) > 1 else actions[0]

    return fn


# ---------------------------------------------------------------------------
# tp_ladder_r
# ---------------------------------------------------------------------------

def tp_ladder_r(
    rungs: list[tuple[float, float]],
    anchor: Literal["original", "avg_px"] = "original",
) -> Callable[[Position, object], Union[ScaleAction, list[ScaleAction], None]]:
    """Take-profit ladder keyed on R-multiples of ``initial_risk``.

    ``anchor="original"`` (default, per Q5): targets use frozen
    ``pos.r_anchor_price``. ``anchor="avg_px"``: targets use current
    weighted-average entry (``pos.entry_price``).
    """
    if anchor not in ("original", "avg_px"):
        raise ValueError(f"anchor must be 'original' or 'avg_px', got {anchor!r}")

    rungs_sorted = sorted(rungs, key=lambda r: r[0])
    state_key = _state_key(f"tp_ladder_r_{anchor}", rungs_sorted)

    def _anchor_price(pos: Position) -> float:
        if anchor == "original":
            return pos.r_anchor_price
        return pos.entry_price

    def _rung_target(pos: Position, rung_r: float) -> float:
        base = _anchor_price(pos)
        if pos.direction == 1:
            return base + rung_r * pos.initial_risk
        return base - rung_r * pos.initial_risk

    def _rebuild_fired_from_events(pos: Position) -> set:
        fired: set = set()
        consumed: set = set()
        for idx, (rung_r, _frac) in enumerate(rungs_sorted):
            target = _rung_target(pos, rung_r)
            for ev_i, ev in enumerate(pos.scaling_events):
                if ev_i in consumed:
                    continue
                if getattr(ev, "kind", None) != "reduce":
                    continue
                if getattr(ev, "is_stop_like", False):
                    continue
                # Price-satisfies check: long needs fill >= target; short <=
                if pos.direction == 1:
                    hit = ev.fill_price + 1e-9 >= target
                else:
                    hit = ev.fill_price - 1e-9 <= target
                if hit:
                    fired.add(idx)
                    consumed.add(ev_i)
                    break
        return fired

    def fn(pos: Position, ctx) -> Union[ScaleAction, list[ScaleAction], None]:
        if pos.initial_risk <= 0:
            return None
        state = _ensure_state(pos)
        fired: set = state.get(state_key)
        if fired is None:
            fired = _rebuild_fired_from_events(pos)
            state[state_key] = fired

        close = ctx.close
        actions: list[ScaleAction] = []
        for idx, (rung_r, fraction) in enumerate(rungs_sorted):
            if idx in fired:
                continue
            target = _rung_target(pos, rung_r)
            if pos.direction == 1:
                hit = close + 1e-12 >= target
            else:
                hit = close - 1e-12 <= target
            if hit:
                actions.append(
                    ScaleAction(
                        qty_delta=-abs(pos.quantity) * fraction * pos.direction,
                        reason=f"tp_rung_{idx}_r",
                        is_stop_like=False,
                    )
                )
                fired.add(idx)

        if not actions:
            return None
        return actions if len(actions) > 1 else actions[0]

    return fn


# ---------------------------------------------------------------------------
# tp_ladder_price
# ---------------------------------------------------------------------------

def tp_ladder_price(
    rungs: list[tuple[float, float]],
) -> Callable[[Position, object], Union[ScaleAction, list[ScaleAction], None]]:
    """Take-profit ladder keyed on absolute price levels."""
    rungs_sorted = sorted(rungs, key=lambda r: r[0])
    state_key = _state_key("tp_ladder_price", rungs_sorted)

    def _rebuild_fired_from_events(pos: Position) -> set:
        fired: set = set()
        consumed: set = set()
        for idx, (price, _frac) in enumerate(rungs_sorted):
            for ev_i, ev in enumerate(pos.scaling_events):
                if ev_i in consumed:
                    continue
                if getattr(ev, "kind", None) != "reduce":
                    continue
                if getattr(ev, "is_stop_like", False):
                    continue
                if pos.direction == 1:
                    hit = ev.fill_price + 1e-9 >= price
                else:
                    hit = ev.fill_price - 1e-9 <= price
                if hit:
                    fired.add(idx)
                    consumed.add(ev_i)
                    break
        return fired

    def fn(pos: Position, ctx) -> Union[ScaleAction, list[ScaleAction], None]:
        state = _ensure_state(pos)
        fired: set = state.get(state_key)
        if fired is None:
            fired = _rebuild_fired_from_events(pos)
            state[state_key] = fired

        close = ctx.close
        actions: list[ScaleAction] = []
        for idx, (price, fraction) in enumerate(rungs_sorted):
            if idx in fired:
                continue
            if pos.direction == 1:
                hit = close + 1e-12 >= price
            else:
                hit = close - 1e-12 <= price
            if hit:
                actions.append(
                    ScaleAction(
                        qty_delta=-abs(pos.quantity) * fraction * pos.direction,
                        reason=f"tp_rung_{idx}_price",
                        is_stop_like=False,
                    )
                )
                fired.add(idx)

        if not actions:
            return None
        return actions if len(actions) > 1 else actions[0]

    return fn


# ---------------------------------------------------------------------------
# breakeven_plus_runner (one-shot)
# ---------------------------------------------------------------------------

def breakeven_plus_runner(
    partial_r: float = 1.0,
    fraction: float = 0.5,
) -> Callable[[Position, object], Optional[ScaleAction]]:
    """One-shot: reduce ``fraction`` at ``partial_r`` R profit; moves stop to
    entry to realize the breakeven promise. Never re-fires (not at same price,
    not at a higher price, not across restart)."""
    state_key = "breakeven_plus_runner__fired"

    def _already_fired(pos: Position) -> bool:
        # One-shot: ANY prior non-stop-like reduce at or beyond the partial_r
        # threshold counts as having fired. This recovers state after restart.
        if pos.initial_risk <= 0:
            return False
        if pos.direction == 1:
            target = pos.entry_price + partial_r * pos.initial_risk
        else:
            target = pos.entry_price - partial_r * pos.initial_risk
        for ev in pos.scaling_events:
            if getattr(ev, "kind", None) != "reduce":
                continue
            if getattr(ev, "is_stop_like", False):
                continue
            if pos.direction == 1 and ev.fill_price + 1e-9 >= target:
                return True
            if pos.direction == -1 and ev.fill_price - 1e-9 <= target:
                return True
        return False

    def fn(pos: Position, ctx) -> Optional[ScaleAction]:
        state = _ensure_state(pos)
        fired = state.get(state_key)
        if fired is None:
            fired = _already_fired(pos)
            state[state_key] = fired
        if fired:
            return None

        if pos.initial_risk <= 0:
            return None
        close = ctx.close
        if pos.direction == 1:
            profit_r = (close - pos.entry_price) / pos.initial_risk
        else:
            profit_r = (pos.entry_price - close) / pos.initial_risk
        if profit_r + 1e-12 < partial_r:
            return None

        state[state_key] = True
        return ScaleAction(
            qty_delta=-abs(pos.quantity) * fraction * pos.direction,
            reason="breakeven_plus_runner",
            stop_override=pos.entry_price,
            is_stop_like=False,
        )

    return fn


# ---------------------------------------------------------------------------
# combine
# ---------------------------------------------------------------------------

def combine(
    *fns: Callable[[Position, object], Union[ScaleAction, list[ScaleAction], None]],
) -> Callable[[Position, object], Union[ScaleAction, list[ScaleAction], None]]:
    """Compose scale-check helpers — first non-None wins."""

    def composed(pos: Position, ctx):
        for f in fns:
            result = f(pos, ctx)
            if result is None:
                continue
            if isinstance(result, list) and len(result) == 0:
                continue
            return result
        return None

    return composed


# ---------------------------------------------------------------------------
# Increase helpers: add_at_price, add_on_profit_atr, confirm_and_add
# ---------------------------------------------------------------------------

def add_at_price(
    rungs: list[tuple[float, float]],
) -> Callable[[Position, object], Optional[ScaleAction]]:
    """Add to position on pullback to specified prices.

    For a long: triggers when bar low <= rung price (pullback).
    For a short: triggers when bar high >= rung price.
    ``fraction`` is of the current absolute position quantity.
    """
    # For longs, process higher rungs first (closer-to-entry pullback first);
    # for shorts the reverse. Simpler: sort so first rung touched wins and
    # each rung fires once.
    rungs_sorted = sorted(rungs, key=lambda r: -r[0])  # high-to-low for longs
    state_key = _state_key("add_at_price", rungs_sorted)

    def _rebuild_fired_from_events(pos: Position) -> set:
        fired: set = set()
        consumed: set = set()
        for idx, (price, _frac) in enumerate(rungs_sorted):
            for ev_i, ev in enumerate(pos.scaling_events):
                if ev_i in consumed:
                    continue
                if getattr(ev, "kind", None) != "increase":
                    continue
                # match by price proximity
                if abs(ev.fill_price - price) < 1e-6:
                    fired.add(idx)
                    consumed.add(ev_i)
                    break
        return fired

    def fn(pos: Position, ctx) -> Optional[ScaleAction]:
        state = _ensure_state(pos)
        fired: set = state.get(state_key)
        if fired is None:
            fired = _rebuild_fired_from_events(pos)
            state[state_key] = fired

        low = getattr(ctx, "low", ctx.close)
        high = getattr(ctx, "high", ctx.close)
        for idx, (price, fraction) in enumerate(rungs_sorted):
            if idx in fired:
                continue
            if pos.direction == 1 and low <= price:
                fired.add(idx)
                return ScaleAction(
                    qty_delta=+abs(pos.quantity) * fraction,
                    reason=f"add_rung_{idx}_price",
                    is_stop_like=False,
                )
            if pos.direction == -1 and high >= price:
                fired.add(idx)
                return ScaleAction(
                    qty_delta=+abs(pos.quantity) * fraction,
                    reason=f"add_rung_{idx}_price",
                    is_stop_like=False,
                )
        return None

    return fn


def add_on_profit_atr(
    rungs: list[tuple[float, float]],
) -> Callable[[Position, object], Optional[ScaleAction]]:
    """Add to position once profit in ATR units reaches each rung threshold."""
    rungs_sorted = sorted(rungs, key=lambda r: r[0])
    state_key = _state_key("add_on_profit_atr", rungs_sorted)

    def _rebuild_fired_from_events(pos: Position) -> set:
        fired: set = set()
        consumed: set = set()
        for idx, (threshold, _frac) in enumerate(rungs_sorted):
            for ev_i, ev in enumerate(pos.scaling_events):
                if ev_i in consumed:
                    continue
                if getattr(ev, "kind", None) != "increase":
                    continue
                atr_at = getattr(ev, "atr_at_event", None)
                if atr_at is None or atr_at <= 0:
                    continue
                if pos.direction == 1:
                    profit_atr = (ev.fill_price - pos.entry_price) / atr_at
                else:
                    profit_atr = (pos.entry_price - ev.fill_price) / atr_at
                if profit_atr + 1e-9 >= threshold:
                    fired.add(idx)
                    consumed.add(ev_i)
                    break
        return fired

    def fn(pos: Position, ctx) -> Optional[ScaleAction]:
        state = _ensure_state(pos)
        fired: set = state.get(state_key)
        if fired is None:
            fired = _rebuild_fired_from_events(pos)
            state[state_key] = fired

        atr = getattr(ctx, "atr", 0.0) or 0.0
        if atr <= 0:
            return None
        close = ctx.close
        if pos.direction == 1:
            profit_atr = (close - pos.entry_price) / atr
        else:
            profit_atr = (pos.entry_price - close) / atr

        for idx, (threshold, fraction) in enumerate(rungs_sorted):
            if idx in fired:
                continue
            if profit_atr + 1e-12 >= threshold:
                fired.add(idx)
                return ScaleAction(
                    qty_delta=+abs(pos.quantity) * fraction,
                    reason=f"add_rung_{idx}_atr",
                    is_stop_like=False,
                )
        return None

    return fn


def confirm_and_add(
    confirm_bars: int,
    min_profit_atr: float,
    add_fraction: float,
) -> Callable[[Position, object], Optional[ScaleAction]]:
    """Add after ``confirm_bars`` of holding AND profit >= ``min_profit_atr``.

    One-shot. The ``bars_held`` attribute is expected on the context.
    """
    state_key = f"confirm_and_add__{confirm_bars}_{min_profit_atr}_{add_fraction}"

    def _already_fired(pos: Position) -> bool:
        # If any increase event exists past entry we assume already fired for
        # restart-idempotency purposes (one-shot).
        for ev in pos.scaling_events:
            if getattr(ev, "kind", None) == "increase":
                return True
        return False

    def fn(pos: Position, ctx) -> Optional[ScaleAction]:
        state = _ensure_state(pos)
        fired = state.get(state_key)
        if fired is None:
            fired = _already_fired(pos)
            state[state_key] = fired
        if fired:
            return None

        bars_held = getattr(ctx, "bars_held", None)
        if bars_held is None or bars_held < confirm_bars:
            return None

        atr = getattr(ctx, "atr", 0.0) or 0.0
        if atr <= 0:
            return None
        close = ctx.close
        if pos.direction == 1:
            profit_atr = (close - pos.entry_price) / atr
        else:
            profit_atr = (pos.entry_price - close) / atr
        if profit_atr + 1e-12 < min_profit_atr:
            return None

        state[state_key] = True
        return ScaleAction(
            qty_delta=+abs(pos.quantity) * add_fraction,
            reason="confirm_and_add",
            is_stop_like=False,
        )

    return fn
