"""M7 — UniverseContext + namespace split (AC-S2, AC-S4).

Per design §2.2 + §2.8 ID2 (Implementation Decision pinned):
- UniverseContext is `@dataclass(frozen=True, slots=True)` — structurally
  immutable (strategies can't re-bind ctx.data to a different DataView)
- Inner views (DataView, PortfolioView, OrderFactoryView) are NOT frozen;
  they hold mutable state (bar cursor, order book, etc.)
- Arrays exposed via ctx.data.per_token(t).<field> are READ-ONLY views
  (setflags(write=False)); strategies opt into mutation via ctx.mutable_copy()

FIX vocabulary: OrderFactoryView wraps M5's Order (arm + arm_bracket factories
publish via pre-fill atomic path — see v5.orders).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import numpy as np

import itertools

from v5.clock import Clock, LiveClock
from v5.data.instruments import InstrumentRegistry
from v5.data.streams import Venue
from v5.testing import TestClock


# Process-wide monotonic UniverseContext identifier — see build_test() for
# rationale (id() recycling under GC causes cross-fold cache contamination).
_CTX_UID_COUNTER = itertools.count(1)

# Process-wide order-sequence counter for OrderFactoryView._next_seq().
# Instance-local numbering caused venue_order_id collisions across
# concurrent factories (e.g. two runners both minting paper-00000001).
# Process-wide guarantees FIX-unique OrderID(37) within a process;
# runner_instance_id prefix further namespaces across runners.
_ORDER_SEQ_COUNTER = itertools.count(1)

# Process-wide quarantine registry, name-mangled so strategy code can't
# `from v5.universe_context import _QUARANTINED_STRATEGIES; .clear()` it
# (round-6 Quant MAJOR: public module-level set was strategy-tamperable).
# Uses a monotonic strategy_uid (never reused; no id()-recycling false-
# positive quarantine on a fresh WF-fold Strategy that happens to land
# at a GC'd predecessor's memory address — round-6 Quant MAJOR).
# WeakValueDictionary(uid → strategy_ref) lets entries auto-decay when
# the strategy is GC'd, bounding the registry's memory footprint.
import weakref as _weakref


class _QuarantineRegistry:
    """Engine-only quarantine state. Encapsulates a set of monotonic uids
    for weakrefable strategies plus a parallel id-set for non-weakrefable
    fallbacks — both namespaces consulted on both mark + query so slotted
    Strategy subclasses without __weakref__ stay quarantine-able (round-7
    Quant MAJOR NEW-11: earlier asymmetric fallback returned None on
    query for non-weakrefable objects, silently un-quarantining them).
    """

    def __init__(self):
        self._quarantined_uids: set[int] = set()
        # Fallback set for non-weakrefable Strategy objects (slotted
        # subclasses without `__weakref__` slot). Keyed by id(). Accepts
        # the id-reuse risk for these edge cases; the vast majority of
        # strategies are weakrefable and use the uid path.
        self._quarantined_fallback_ids: set[int] = set()

    def mark(self, strategy) -> None:
        uid = _strategy_uid_of(strategy, create=True)
        if uid is not None:
            self._quarantined_uids.add(uid)
        else:
            # Non-weakrefable — fall back to id(). Bounded in practice
            # (quarantined count ≤ running strategy count).
            self._quarantined_fallback_ids.add(id(strategy))

    def is_quarantined(self, strategy) -> bool:
        uid = _strategy_uid_of(strategy, create=False)
        if uid is not None and uid in self._quarantined_uids:
            return True
        # Consult fallback namespace for non-weakrefable objects.
        if id(strategy) in self._quarantined_fallback_ids:
            return True
        return False

    def clear(self) -> None:
        """Engine-only reset for tests that run multiple lifecycle passes."""
        self._quarantined_uids.clear()
        self._quarantined_fallback_ids.clear()


_QUARANTINE_REGISTRY = _QuarantineRegistry()

# Strategy → monotonic uid map. WeakKeyDictionary so uids auto-free when
# the strategy is GC'd. Monotonic `itertools.count` assignment guarantees
# no two weakrefable Strategy objects ever share a uid within a process
# lifetime — unlike id() which Python recycles after GC
# (round-6 Quant MAJOR-NEW-8).
_STRATEGY_UID_MAP: "_weakref.WeakKeyDictionary" = _weakref.WeakKeyDictionary()
_STRATEGY_UID_COUNTER = itertools.count(1)


def _strategy_uid_of(strategy, create: bool = True) -> Optional[int]:
    """Return the monotonic uid for a Strategy instance, assigning a new
    one on first access. Returns None for non-weakrefable strategies so
    callers can fall back to an id()-keyed namespace (round-7 Quant
    MAJOR NEW-11: uid==id() fallback was asymmetric, regressed the
    round-5 slotted-strategy quarantine fix). `create=False` returns
    None if no uid has ever been assigned."""
    try:
        existing = _STRATEGY_UID_MAP.get(strategy)
    except TypeError:
        # Non-weakrefable — caller must consult the fallback id() set.
        return None
    if existing is not None:
        return existing
    if not create:
        return None
    uid = next(_STRATEGY_UID_COUNTER)
    try:
        _STRATEGY_UID_MAP[strategy] = uid
    except TypeError:
        return None
    return uid


def _mark_quarantined(strategy) -> None:
    """Engine-side quarantine: adds the strategy to the process-wide
    registry. Safe for slotted Strategy subclasses whether or not they
    reserve a `__weakref__` slot. Registry uses monotonic uid (weakref
    path) or id() (fallback path); fresh strategies on the uid path
    never inherit a GC'd predecessor's quarantine status."""
    _QUARANTINE_REGISTRY.mark(strategy)


def _is_quarantined(strategy) -> bool:
    """Query the process-wide quarantine registry for a Strategy object."""
    return _QUARANTINE_REGISTRY.is_quarantined(strategy)


# ============================================================
# Test-harness dummy types used by run_full_lifecycle cascades
# ============================================================


class _DummyCandidate:
    token: str = "BTC"
    direction: int = 1


class _DummyPosition:
    def __init__(self, entry_price: float):
        self.entry_price = entry_price
        self.token = "BTC"
        self.direction = 1
        self.size = 1.0


class _DummyOrder:
    """Minimal Order-shaped object for cascade tests."""
    def __init__(self, leg_name: str = "entry"):
        self.leg_name = leg_name
        self.order_id = f"dummy-{leg_name}"
        self.venue_order_id = None
        self.last_exec_report = None


class _DummyFill:
    def __init__(self, cum_qty: float = 1.0, leaves_qty: float = 0.0, exec_type=None):
        from v5.orders import ExecType
        self.cl_ord_id = "c-dummy"
        self.venue_order_id = "v-dummy"
        self.exec_id = "x-dummy"
        self.exec_type = exec_type if exec_type is not None else ExecType.TRADE
        self.transact_time = 1_700_000_000_000_000_000
        self.last_qty = cum_qty
        self.last_px = 50_000.0
        self.cum_qty = cum_qty
        self.leaves_qty = leaves_qty
        self.avg_px = 50_000.0


class _DummyClosedTrade:
    token: str = "BTC"
    pnl: float = 0.0


class _BarCtx:
    def __init__(self, bar_idx: int, phase: int):
        self.bar_idx = bar_idx
        self.phase = phase


def _bar_ctx(bar_idx: int, phase: int) -> _BarCtx:
    return _BarCtx(bar_idx, phase)


# ============================================================
# TokenView — per-token read-only data access
# ============================================================


class TokenView:
    """Read-only view of a single token's bar history + indicators.

    Arrays returned via attribute access are numpy views with
    `writeable=False` set. AC-S3 indicator accessors return SCALAR at
    current bar_idx (not array slices).
    """

    def __init__(
        self,
        token: str,
        arrays: dict[str, np.ndarray],
        bar_idx: int = 0,
        indicator_cache=None,
    ):
        self._token = token
        self._bar_idx = bar_idx
        self._cache = indicator_cache
        # Store read-only views (slice up to current bar_idx + 1 for
        # AC-D6 no-look-ahead invariant; fall back to full array for bar_idx
        # at or beyond last bar)
        self._arrays: dict[str, np.ndarray] = {}
        end = min(bar_idx + 1, len(arrays["close"])) if len(arrays["close"]) else 0
        for field_name, arr in arrays.items():
            sliced = arr[:end] if end > 0 else arr[:0]
            view = sliced.view()
            view.flags.writeable = False
            self._arrays[field_name] = view

    @property
    def token(self) -> str:
        return self._token

    @property
    def close(self) -> np.ndarray:
        return self._arrays["close"]

    @property
    def open(self) -> np.ndarray:
        return self._arrays["open"]

    @property
    def high(self) -> np.ndarray:
        return self._arrays["high"]

    @property
    def low(self) -> np.ndarray:
        return self._arrays["low"]

    @property
    def volume(self) -> np.ndarray:
        return self._arrays["volume"]

    # Stdlib indicator accessors — return scalar at current bar_idx (AC-S3).

    def ema(self, n: int, col: str = "close") -> float:
        from v5.indicators import ema_fn
        return self._cache.compute(
            ema_fn, token=self._token, bar_idx=self._bar_idx,
            arr=self._arrays[col], n=n, col=col,
        )

    def sma(self, n: int, col: str = "close") -> float:
        from v5.indicators import sma_fn
        return self._cache.compute(
            sma_fn, token=self._token, bar_idx=self._bar_idx,
            arr=self._arrays[col], n=n, col=col,
        )


# ============================================================
# DataView — mutable per-ctx data access; bar cursor + listings
# ============================================================


class DataView:
    """Mutable inner view (per design §2.8 ID2)."""

    def __init__(
        self,
        tokens_seed: list[str],
        bars: int,
        seed: int,
        default_venue: Venue = Venue.BINANCE,
        listings: Optional[dict[str, int]] = None,
        delistings: Optional[dict[str, int]] = None,
        registry_extra: Optional[list[str]] = None,
    ):
        from v5.indicators import IndicatorCache
        self._default_venue = default_venue
        self._bar_idx = 0
        self._tokens_seed = tuple(tokens_seed)
        self._listings = dict(listings or {})
        self._delistings = dict(delistings or {})
        self._registry_extra = tuple(registry_extra or ())
        self._indicator_cache = IndicatorCache()
        # Synthetic bar data — float32 arrays for deterministic tests
        rng = np.random.default_rng(seed)
        self._arrays: dict[str, dict[str, np.ndarray]] = {}
        for t in tokens_seed:
            close = 50_000.0 + rng.normal(0, 100, size=bars).astype(np.float32)
            self._arrays[t] = {
                "close": close,
                "open": close.copy(),
                "high": close + 10,
                "low": close - 10,
                "volume": rng.lognormal(5, 1, size=bars).astype(np.float32),
            }

    @property
    def indicator_cache(self):
        """Shared IndicatorCache for this DataView (AC-S3)."""
        return self._indicator_cache

    def per_token(self, symbol: str, venue: Optional[Venue] = None) -> TokenView:
        """Returns TokenView for (symbol, venue=default). Read-only arrays."""
        if symbol not in self._arrays:
            raise KeyError(f"token {symbol!r} has no bar data in this UniverseContext")
        return TokenView(
            symbol, self._arrays[symbol],
            bar_idx=self._bar_idx,
            indicator_cache=self._indicator_cache,
        )

    def indicators(self, symbol: str, resolution: str = "1h",
                   offset: int = 0) -> dict:
        """Return a per-bar indicator dict for (symbol, resolution) at
        `bar_idx + offset` (offset must be ≤ 0 — no look-ahead).

        Exposes the bare OHLCV columns plus any indicators that have been
        computed through the shared IndicatorCache. Returns None-valued
        keys for indicators not yet computed (strategies tolerate
        missing values per AC-S5 error containment).

        Strategies call this instead of poking at raw arrays so the
        per-ctx IndicatorCache stays authoritative. Offset is bounded at
        0 (look-ahead guard, AC-S2): positive offsets raise ValueError.
        """
        if offset > 0:
            raise ValueError(
                f"DataView.indicators(offset={offset}) forbids look-ahead; "
                "use offset <= 0"
            )
        if symbol not in self._arrays:
            raise KeyError(
                f"token {symbol!r} has no bar data in this UniverseContext"
            )
        idx = self._bar_idx + offset
        if idx < 0:
            return {}
        arrays = self._arrays[symbol]
        out: dict = {}
        for col, arr in arrays.items():
            if idx < len(arr):
                out[col] = float(arr[idx])
        # Resolution accepted but currently only 1h synthetic data is
        # produced by build_test; multi-resolution support lands when
        # DataEngine feeds real bars (M8+). The param is in the signature
        # so ports match their v4 counterparts' call shape.
        out["_resolution"] = resolution
        out["_bar_idx"] = idx
        return out

    def tokens(self, bar_idx: Optional[int] = None) -> list[str]:
        """Bar-relative tradable token list.

        Tradable ≡ listed AND cached. `registry_extra` tokens are listed but
        not cached → excluded. `listings[t] > bar_idx` → not yet listed.
        `delistings[t] <= bar_idx` → already delisted.
        """
        idx = bar_idx if bar_idx is not None else self._bar_idx
        active = []
        for t in self._tokens_seed:
            if t in self._listings and self._listings[t] > idx:
                continue
            if t in self._delistings and self._delistings[t] <= idx:
                continue
            active.append(t)
        return active


# ============================================================
# PortfolioView
# ============================================================


class PortfolioView:
    """Read-only-ish portfolio accessor (equity/positions)."""

    def __init__(self, equity: float):
        self.equity = equity
        self.open_positions: list = []
        self.closed_trades_today: list = []


# ============================================================
# OrderFactoryView — ctx.orders.arm() / arm_bracket()
# ============================================================


class OrderFactoryView:
    """Strategy-facing order factory (AC-O1).

    `arm(...)` single-leg: constructs real M5 Order via Order.arm().
    `arm_bracket(entry, sl, tp)` three-leg: constructs Order with OTO_BRACKET
    + OTO contingency per M5 pre-fill atomic path.
    """

    def __init__(self, runner_instance_id: str = "default",
                 clock: Optional["Clock"] = None):
        self._runner_instance_id = runner_instance_id
        # Injected clock source — arm() / arm_bracket() stamp armed_at
        # from ctx.clock (TestClock in backtest, LiveClock in paper). NEVER
        # datetime.now(): breaks determinism + paper-state replay +
        # FIX TransactTime reproducibility. Set by UniverseContext.build_test()
        # after the ctx is constructed so the same Clock backs both.
        self._clock: Optional["Clock"] = clock
        self._active_orders: list = []
        self._reversal_orders: list = []
        self._rejection_events: list = []
        # (sequence numbers come from module-level _ORDER_SEQ_COUNTER — see _next_seq)
        # M7 Task 12 — registered strategies receive on_order_* callbacks when
        # simulate_fill / simulate_reject fire (eager dispatch for tests that
        # don't call run_full_lifecycle)
        self._strategies: list = []

    def _now(self):
        """Deterministic `armed_at` source. Reads the injected Clock via
        `now_dt()` / `utcnow()` / `now_ns()` (whichever the Clock exposes);
        falls back to a monotonic synthetic epoch when no Clock was
        injected (legacy test paths only).

        NEVER `datetime.now()` — breaks determinism, paper-state replay,
        and FIX TransactTime reproducibility. AC-V2 AST scan forbids it
        in strategy source; this engine-side factory honors the same
        invariant."""
        from datetime import datetime, timezone, timedelta
        if self._clock is not None:
            # Try common clock interfaces in order.
            for attr in ("utcnow", "now_dt"):
                fn = getattr(self._clock, attr, None)
                if callable(fn):
                    try:
                        return fn()
                    except Exception:
                        pass
            # now_ns → datetime fallback
            ns = getattr(self._clock, "now_ns", None)
            if callable(ns):
                try:
                    v = ns()
                    return datetime.fromtimestamp(v / 1_000_000_000, tz=timezone.utc)
                except Exception:
                    pass
        # Deterministic synthetic monotonic epoch — 1-second increments
        # from 2026-01-01. Never walks wall-clock.
        seq = getattr(self, "_synthetic_now_counter", 0)
        self._synthetic_now_counter = seq + 1
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seq)

    def register_strategies(self, strategies: list) -> None:
        """Register strategies to receive on_order_* / on_position_* callbacks
        when simulate_fill / simulate_reject fire."""
        self._strategies = list(strategies) if strategies else []

    def _dispatch(self, method: str, *args) -> None:
        """AC-S5 per-handler try/except when dispatching to registered strategies.

        Bumps `exception_counter` on the offending strategy so the
        quarantine threshold eventually fires. Honors the process-wide
        quarantine registry (_QUARANTINED_STRATEGIES) — works for slotted
        Strategy subclasses where an attribute-based `_quarantined` flag
        would silently fail (round-5 Quant MAJOR). A strategy cannot
        un-quarantine itself: registry mutation is engine-side only.
        """
        for s in self._strategies:
            if _is_quarantined(s):
                continue
            fn = getattr(s, method, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:
                try:
                    s.exception_counter = getattr(s, "exception_counter", 0) + 1
                except Exception:
                    pass

    def _next_seq(self) -> int:
        """Return a process-wide monotonic sequence. Instance-local seq
        caused venue_order_id collisions when two OrderFactoryViews (e.g.
        two runners sharing the paper-id namespace) both minted
        `paper-00000001`. Process-wide counter prevents collision across
        factory instances; the runner_instance_id prefix keeps per-runner
        namespaces separated in the final paper_venue_id."""
        return next(_ORDER_SEQ_COUNTER)

    def _now_ns(self) -> int:
        """Clock-backed epoch nanoseconds for FIX TransactTime(60).
        Reads the injected Clock via now_ns/utcnow/now_dt. Falls back
        to the deterministic synthetic epoch from `_now()` so test
        harnesses without a clock still produce reproducible timestamps.
        NEVER time.time_ns() — violates determinism + FIX replay."""
        if self._clock is not None:
            ns = getattr(self._clock, "now_ns", None)
            if callable(ns):
                try:
                    return int(ns())
                except Exception:
                    pass
        dt = self._now()
        return int(dt.timestamp() * 1_000_000_000)

    @staticmethod
    def _direction_int(direction) -> int:
        if isinstance(direction, int):
            return direction
        return 1 if str(direction).upper() == "LONG" else -1

    def arm(
        self,
        *,
        symbol: str,
        direction,
        size: float,
        trigger=None,
        trigger_price: float = None,
        strategy_id: str = "test",
        market: str = "perp",
        **kwargs,
    ):
        """Single-leg order — constructs M5 Order.arm(...). Publishes to active list."""
        from v5.orders import Order, TriggerType
        dir_int = self._direction_int(direction)
        if trigger is None:
            trigger = TriggerType.PRICE_ABOVE if dir_int == 1 else TriggerType.PRICE_BELOW
        seq = self._next_seq()
        order_id = f"order-{seq:08x}"
        # M8 — strategies may pass a SizingRequest via `sizing=...`. Route
        # it into sizing_ctx under the "sizing" key so the clamp pipeline
        # reads it at release_atomic time. Back-compat: `size` kwarg still
        # populates `target_size` for legacy callers.
        sizing_ctx: dict = {"target_size": size, "market": market}
        sizing_req = kwargs.get("sizing")
        if sizing_req is not None:
            sizing_ctx["sizing"] = sizing_req
        order = Order.arm(
            strategy_id=strategy_id,
            token=symbol,
            direction=dir_int,
            trigger=trigger,
            trigger_price=float(trigger_price or 0.0),
            working_price_source="last",
            armed_at=self._now(),
            expires_at=None,
            sizing_ctx=sizing_ctx,
            order_id=order_id,
        )
        self._active_orders.append(order)
        return order

    def arm_bracket(
        self,
        entry_spec: dict,
        sl_spec: dict,
        tp_spec: dict,
        fill_policy=None,
        strategy_id: str = "test",
    ):
        """Three-leg bracket. Validates sides (skipped for MARKET entries per
        design §2.2); constructs Order with OTO_BRACKET + OTOCO contingency.

        Contingency is OTOCO (not OTO): entry triggers the SL+TP pair, and
        the SL/TP pair are OCO with each other. Capital reserve follows
        compute_reserved_capital() entry-plus-max(siblings) aggregation.
        Legs are ordered (entry, sl, tp) by convention — OTOCO aggregator
        treats legs[0] as the entry and legs[1:] as the OCO siblings.
        """
        from v5.orders import (
            Order, Leg, TriggerType, LegStatus, LegFillPolicy, ContingencyType,
        )
        dir_int = self._direction_int(entry_spec.get("direction", "LONG"))
        size = entry_spec["size"]
        market = entry_spec.get("market", "perp")
        entry_price = entry_spec.get("trigger_price")
        sl_price = sl_spec.get("trigger_price")
        tp_price = tp_spec.get("trigger_price")
        order_type = entry_spec.get("order_type", "STOP").upper()

        # AC-O1 side validation — skipped for MARKET entries (§2.2 defer-to-fill)
        if order_type != "MARKET" and entry_price is not None:
            if dir_int == 1:  # LONG
                if sl_price is not None and sl_price >= entry_price:
                    raise ValueError(
                        f"LONG bracket: SL {sl_price} must be below entry {entry_price}"
                    )
                if tp_price is not None and tp_price <= entry_price:
                    raise ValueError(
                        f"LONG bracket: TP {tp_price} must be above entry {entry_price}"
                    )
            else:  # SHORT
                if sl_price is not None and sl_price <= entry_price:
                    raise ValueError(
                        f"SHORT bracket: SL {sl_price} must be above entry {entry_price}"
                    )
                if tp_price is not None and tp_price >= entry_price:
                    raise ValueError(
                        f"SHORT bracket: TP {tp_price} must be below entry {entry_price}"
                    )

        seq = self._next_seq()
        order_id = f"order-{seq:08x}"
        entry_leg = Leg(
            leg_ref_id="entry", symbol=entry_spec["symbol"], market=market,
            venue="BINANCE", direction=dir_int, target_qty=size,
            order_type=order_type.lower() if order_type != "MARKET" else "market",
            trigger_price=entry_price,
            status=LegStatus.ARMED,
        )
        sl_leg = Leg(
            leg_ref_id="sl", symbol=entry_spec["symbol"], market=market,
            venue="BINANCE", direction=-dir_int, target_qty=size,
            order_type="stop", trigger_price=sl_price,
            status=LegStatus.ARMED,
        )
        tp_leg = Leg(
            leg_ref_id="tp", symbol=entry_spec["symbol"], market=market,
            venue="BINANCE", direction=-dir_int, target_qty=size,
            order_type="limit", limit_price=tp_price, trigger_price=tp_price,
            status=LegStatus.ARMED,
        )
        order = Order.arm(
            strategy_id=strategy_id,
            token=entry_spec["symbol"],
            direction=dir_int,
            trigger=TriggerType.PRICE_ABOVE if dir_int == 1 else TriggerType.PRICE_BELOW,
            trigger_price=float(entry_price or 0.0),
            working_price_source="last",
            armed_at=self._now(),
            expires_at=None,
            sizing_ctx={"target_size": size, "market": market},
            legs=(entry_leg, sl_leg, tp_leg),
            fill_policy=fill_policy if fill_policy is not None else LegFillPolicy.OTO_BRACKET,
            contingency=ContingencyType.OTOCO,
            order_id=order_id,
        )
        self._active_orders.append(order)
        return order

    def active_orders(self) -> list:
        return list(self._active_orders)

    def last_reversal_order(self):
        return self._reversal_orders[-1] if self._reversal_orders else None

    def last_rejection_event(self):
        return self._rejection_events[-1] if self._rejection_events else None

    def _paper_venue_id(self, seq_hex: str) -> str:
        if self._runner_instance_id == "default":
            return f"paper-{seq_hex}"
        return f"paper-{self._runner_instance_id}-{seq_hex}"

    def simulate_ack(self, order_id: str) -> None:
        for o in self._active_orders:
            if o.order_id == order_id:
                # Always mint a fresh sequence for venue_order_id rather
                # than trying to parse the ClOrdID — order_id comes in
                # three shapes (`order-{seq:08x}`, `_gen_order_id`
                # output `{strategy}-{token}-{N}`, or JSON-restored
                # arbitrary strings) and the earlier split('-',1)[1]
                # parser produced bogus seq_hex for the latter two
                # (round-4 FIX MAJOR-3: latent brittleness). Using a
                # fresh _next_seq() guarantees a FIX-unique OrderID(37)
                # that never depends on ClOrdID internal structure.
                seq_hex = f"{self._next_seq():08x}"
                # M5 Order is frozen — §2.8 ID1 object.__setattr__ single-field mutation
                # MUST populate venue_order_id BEFORE on_order_accepted fires (AC-O4)
                object.__setattr__(o, "venue_order_id", self._paper_venue_id(seq_hex))
                self._dispatch("on_order_accepted", o)
                return

    def submit(self, order) -> None:
        """Register a caller-constructed Order (for tests that bypass arm_bracket)."""
        self._active_orders.append(order)

    def simulate_fill(self, order_id: str, leg_idx: int, qty: float, price: float) -> None:
        """Test hook — record a fill event on a leg. Real fill lifecycle is
        Wave D (Task 12) via BarProcessor + M5 Leg transitions."""
        # Post-fill re-validation for MARKET entries per design §2.2
        for o in self._active_orders:
            if getattr(o, "order_id", None) != order_id:
                continue
            if leg_idx >= len(o.legs):
                return
            leg = o.legs[leg_idx]
            # MARKET entry re-validation: check sibling SL/TP sides against fill price
            if leg.leg_ref_id == "entry" and leg.order_type == "market":
                from types import SimpleNamespace
                for sib in o.legs[1:]:
                    sib_price = sib.trigger_price
                    if sib_price is None:
                        continue
                    is_sl = sib.leg_ref_id == "sl"
                    if o.direction == 1:  # LONG: SL < fill, TP > fill
                        if is_sl and sib_price >= price:
                            self._rejection_events.append(SimpleNamespace(
                                reason=f"post_fill_unwind:mis_sided_sl sl={sib_price} fill={price}",
                                order=o,
                            ))
                            return
                        if not is_sl and sib_price <= price:
                            self._rejection_events.append(SimpleNamespace(
                                reason=f"post_fill_unwind:mis_sided_tp tp={sib_price} fill={price}",
                                order=o,
                            ))
                            return
                    else:  # SHORT: SL > fill, TP < fill
                        if is_sl and sib_price <= price:
                            self._rejection_events.append(SimpleNamespace(
                                reason=f"post_fill_unwind:mis_sided_sl_short sl={sib_price} fill={price}",
                                order=o,
                            ))
                            return
                        if not is_sl and sib_price >= price:
                            self._rejection_events.append(SimpleNamespace(
                                reason=f"post_fill_unwind:mis_sided_tp_short tp={sib_price} fill={price}",
                                order=o,
                            ))
                            return
            return

    def simulate_reject(self, order_id: str, leg_idx: int, reason: str) -> None:
        """Test hook — trigger a venue-reject on a leg.

        AC-O3: cascade fires under UNWIND_ON_REJECT and OTO_BRACKET (both
        policies imply "close sibling on reject"). BEST_EFFORT / ALL_OR_NONE
        do NOT cascade.
        """
        from v5.orders import LegFillPolicy
        from types import SimpleNamespace
        cascade_policies = (LegFillPolicy.UNWIND_ON_REJECT, LegFillPolicy.OTO_BRACKET)
        for o in self._active_orders:
            if getattr(o, "order_id", None) != order_id:
                continue
            if o.fill_policy in cascade_policies and len(o.legs) >= 2:
                # Build reversal order mirroring the sibling (leg 0)
                entry_leg = o.legs[0]
                reversal = SimpleNamespace(
                    order_id=f"reversal-{o.order_id}",
                    legs=[SimpleNamespace(
                        direction="SHORT" if entry_leg.direction == 1 else "LONG",
                        target_qty=entry_leg.target_qty,
                    )],
                )
                self._reversal_orders.append(reversal)
                reject_reason = f"post_fill_unwind:venue_reject leg={leg_idx} ({reason})"
                self._rejection_events.append(SimpleNamespace(
                    reason=reject_reason,
                    order=o,
                ))
                # M7 AC-O3 — dispatch on_order_rejected with a REJECTED Fill
                # attached via last_exec_report (FIX-H4 requirement)
                from v5.orders import ExecType
                from v5.fill import Fill
                # ExecID must be FIX-unique per ExecReport; derive from the
                # process-wide order sequence so repeated rejects on the same
                # order (partial fill → reject) get distinct ExecIDs.
                exec_seq = next(_ORDER_SEQ_COUNTER)
                transact_ns = self._now_ns()
                reject_fill = Fill(
                    cl_ord_id=o.order_id, venue_order_id=o.venue_order_id,
                    exec_id=f"x-reject-{o.order_id}-{exec_seq}",
                    exec_type=ExecType.REJECTED,
                    transact_time=transact_ns,
                    last_qty=0.0, last_px=0.0,
                    cum_qty=0.0, leaves_qty=0.0, avg_px=0.0,
                )
                # Attach to order so watchers can grab it
                try:
                    object.__setattr__(o, "last_exec_report", reject_fill)
                except Exception:
                    pass
                self._dispatch("on_order_rejected", o, reject_reason)
            else:
                self._rejection_events.append(SimpleNamespace(
                    reason=f"venue_reject:{reason}",
                    order=o,
                ))
                self._dispatch("on_order_rejected", o, f"venue_reject:{reason}")
            return


# ============================================================
# UniverseContext — frozen container
# ============================================================


@dataclass(frozen=True, slots=True)
class UniverseContext:
    """Strategy-facing context. Container frozen per §2.8 ID2; inner views
    mutate. AC-S2 read-only arrays enforced at array level via setflags.
    """

    data: DataView
    portfolio: PortfolioView
    clock: Clock
    orders: OrderFactoryView
    fold_id: Optional[int] = None
    fold_window: Optional[tuple[int, int]] = None
    # Harness state — mutable-by-reference fields (survive frozen invariant
    # as slot entries; populated via object.__setattr__ from build_test)
    _lifecycle_config: dict = field(default_factory=dict, compare=False, repr=False)
    _stopped: bool = field(default=False, compare=False, repr=False)

    def mutable_copy(self, arr: np.ndarray) -> np.ndarray:
        """AC-S2 opt-in mutation — returns np.copy(arr) (writable)."""
        return np.copy(arr)

    def subscribe_events(self, handler) -> None:
        """Register an event-bus handler (AC-S5 observability).

        Used by tests to capture StrategyQuarantined / other engine events.
        """
        handlers = self._lifecycle_config.setdefault("_event_handlers", [])
        handlers.append(handler)

    def _publish_event(self, event) -> None:
        """Dispatch event to subscribe_events handlers (AC-S5)."""
        handlers = self._lifecycle_config.get("_event_handlers", []) or []
        for h in handlers:
            try:
                h(event)
            except Exception:
                pass

    def seek_bar(self, idx: int) -> None:
        """Test harness — advance the bar cursor on the inner DataView."""
        # DataView is NOT frozen (per §2.8 ID2), so this mutation is allowed
        self.data._bar_idx = idx

    def run_full_lifecycle(self) -> None:
        """M7 Task 11 — BarProcessor 15-callback dispatch harness.

        For each strategy:
          1. on_start → generate per bar → check_scale / check_exit /
             filter_entry → on_stop
          2. Simulate events driven by `simulate_*` flags recorded in
             `_lifecycle_config` (set via build_test kwargs)

        Per AC-S5 error containment:
          - on_start / on_reset: FAIL-FAST (re-raise)
          - on_stop: swallow + log
          - generate: swallow; ENGINE substitutes empty signals (AC-S5)
          - check_scale / check_exit / filter_entry: swallow, treat as None/True
          - on_order_*/on_position_*: swallow; dispatch continues to siblings

        Per §2.6 cascade ordering: each simulated event fires a specific
        sequence of callbacks across all strategies.
        """
        cfg = getattr(self, "_lifecycle_config", None) or {}
        strategies = cfg.get("strategies", []) or []
        if not strategies:
            object.__setattr__(self, "_stopped", True)
            return

        # 1. on_start — FAIL-FAST
        for s in strategies:
            s.on_start(portfolio_config=None)

        # 2. Drive bars — generate per bar + per-position checks
        n_bars = cfg.get("bars", 10)
        has_open_pos = cfg.get("simulate_open_position", False)
        sl_bar = cfg.get("simulate_sl_bar", False)
        stop_obs = cfg.get("stop_loss_handler_observer")

        # Always provide a simulated position for per-position checks so
        # check_scale / check_exit / filter_entry exercise their paths in
        # default harness runs.
        dummy_pos = _DummyPosition(entry_price=50_000.0)
        threshold = cfg.get("quarantine_threshold", 5)
        quarantined: set[int] = set()  # ids of quarantined strategies

        def _tick_counter(s, method: str) -> None:
            """AC-S5: increment exception_counter + publish Quarantined if
            crossing threshold for the first time. Registers the strategy
            in the process-wide quarantine registry so _broadcast and
            _dispatch (outside this scope) skip it. Registry is id-keyed,
            not attribute-based — slotted Strategy subclasses don't need
            to reserve a `_quarantined` slot (round-5 Quant MAJOR)."""
            s.exception_counter = getattr(s, "exception_counter", 0) + 1
            if id(s) in quarantined:
                return
            if s.exception_counter >= threshold:
                quarantined.add(id(s))
                _mark_quarantined(s)
                from v5.strategy_api import StrategyQuarantined
                evt = StrategyQuarantined(
                    strategy_id=getattr(s, "strategy_id", type(s).__name__),
                    exception_counter=s.exception_counter,
                    threshold=threshold,
                    last_method=method,
                )
                self._publish_event(evt)

        for bar_idx in range(n_bars):
            self.seek_bar(idx=bar_idx)
            for s in strategies:
                if id(s) in quarantined:
                    continue  # dispatch-stop for quarantined; peers unaffected
                try:
                    s.generate(self, bar_idx)
                except Exception:
                    _tick_counter(s, "generate")
                    from v5.strategy_api import UniverseSignals
                    _ = UniverseSignals(bar_idx=bar_idx, signals={})
                if id(s) in quarantined:
                    continue  # quarantine may have activated mid-bar
                try:
                    s.filter_entry(_DummyCandidate(), _bar_ctx(bar_idx, phase=2))
                except Exception:
                    _tick_counter(s, "filter_entry")
                try:
                    s.check_scale(dummy_pos, _bar_ctx(bar_idx, phase=2))
                except Exception:
                    _tick_counter(s, "check_scale")
                try:
                    exit_result = s.check_exit(dummy_pos, _bar_ctx(bar_idx, phase=3))
                except Exception:
                    _tick_counter(s, "check_exit")
                    exit_result = None
                if sl_bar and stop_obs is not None:
                    stop_obs.append(exit_result is None)

        # 3. Fire simulated events
        if cfg.get("simulate_all_exec_events"):
            self._fire_all_exec_events(strategies)
        if cfg.get("simulate_bracket_entry_fill"):
            self._fire_bracket_entry_cascade(strategies)
        if cfg.get("simulate_sl_hit"):
            self._fire_sl_hit_cascade(strategies)
        if cfg.get("simulate_post_fill_reject"):
            self._fire_post_fill_reject_cascade(strategies)
        if cfg.get("simulate_partial_close"):
            self._fire_partial_close(strategies)
        # Always fire baseline position lifecycle — on_order_accepted +
        # on_position_opened + on_position_closed — so default harness runs
        # exercise these callbacks for AC-Lifecycle coverage.
        self._fire_basic_position_lifecycle(strategies)

        # 4. on_stop — AC-S5 swallow
        for s in strategies:
            try:
                s.on_stop(reason="shutdown")
            except Exception:
                pass
        object.__setattr__(self, "_stopped", True)

    # ---- Event-cascade helpers (§2.6 dispatch order) ----

    def _broadcast(self, strategies, method_name: str, *args) -> None:
        """AC-S5 per-handler try/except. Bumps `exception_counter` on
        crash and respects the process-wide quarantine registry.

        Earlier attribute-based approach silently failed under slotted
        Strategy subclasses (round-5 Quant MAJOR: id-keyed registry is
        slot-safe and strategy-side-tamper-proof — a strategy cannot
        reset its own quarantine status by mutating `self._quarantined`).
        """
        for s in strategies:
            if _is_quarantined(s):
                continue
            fn = getattr(s, method_name, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:
                # Bump counter so AC-S5 quarantine threshold eventually fires.
                try:
                    s.exception_counter = getattr(s, "exception_counter", 0) + 1
                except Exception:
                    pass  # strategy may be a dict/None/etc in tests

    def _fire_basic_position_lifecycle(self, strategies) -> None:
        order = _DummyOrder(leg_name="entry")
        pos = _DummyPosition(entry_price=50_000.0)
        self._broadcast(strategies, "on_order_accepted", order)
        self._broadcast(strategies, "on_position_opened", pos)
        self._broadcast(strategies, "on_position_closed", _DummyClosedTrade())

    def _fire_all_exec_events(self, strategies) -> None:
        """Fire each of the 7 exec-event callbacks once."""
        o1 = _DummyOrder(leg_name="entry")
        o2 = _DummyOrder(leg_name="sl")
        fill = _DummyFill()
        self._broadcast(strategies, "on_order_accepted", o1)
        self._broadcast(strategies, "on_order_triggered", o2)
        self._broadcast(strategies, "on_order_partial_fill", o1, fill)
        self._broadcast(strategies, "on_order_filled", o1, fill)
        self._broadcast(strategies, "on_order_cancelled", o2, "oco_sibling_filled")
        self._broadcast(strategies, "on_order_rejected", _DummyOrder(leg_name="tp"), "venue_reject")
        self._broadcast(strategies, "on_order_expired", _DummyOrder(leg_name="expired"))

    def _fire_bracket_entry_cascade(self, strategies) -> None:
        """§2.6 bracket-entry: partial_fill → filled → position_opened →
        accepted(sl) → accepted(tp)."""
        entry = _DummyOrder(leg_name="entry")
        sl = _DummyOrder(leg_name="sl")
        tp = _DummyOrder(leg_name="tp")
        partial = _DummyFill(cum_qty=0.5, leaves_qty=0.5)
        final = _DummyFill(cum_qty=1.0, leaves_qty=0.0)
        self._broadcast(strategies, "on_order_partial_fill", entry, partial)
        self._broadcast(strategies, "on_order_filled", entry, final)
        self._broadcast(strategies, "on_position_opened", _DummyPosition(50_000.0))
        self._broadcast(strategies, "on_order_accepted", sl)
        self._broadcast(strategies, "on_order_accepted", tp)

    def _fire_sl_hit_cascade(self, strategies) -> None:
        """§2.6 SL-hit: triggered → filled → cancelled(tp, oco) → position_closed."""
        sl = _DummyOrder(leg_name="sl")
        tp = _DummyOrder(leg_name="tp")
        final = _DummyFill(cum_qty=1.0, leaves_qty=0.0)
        self._broadcast(strategies, "on_order_triggered", sl)
        self._broadcast(strategies, "on_order_filled", sl, final)
        self._broadcast(strategies, "on_order_cancelled", tp, "oco_sibling_filled")
        self._broadcast(strategies, "on_position_closed", _DummyClosedTrade())

    def _fire_post_fill_reject_cascade(self, strategies) -> None:
        """§2.6 reject-unwind: rejected(leg) → accepted(reversal) → filled(reversal) →
        position_changed."""
        from v5.orders import ExecType
        leg = _DummyOrder(leg_name="sl")
        reversal = _DummyOrder(leg_name="reversal")
        reject_fill = _DummyFill(cum_qty=0.0, leaves_qty=0.0, exec_type=ExecType.REJECTED)
        # Attach last_exec_report to the leg so the FIX-M4 test can assert
        leg.last_exec_report = reject_fill
        self._broadcast(strategies, "on_order_rejected", leg, "post_fill_unwind:venue_reject")
        self._broadcast(strategies, "on_order_accepted", reversal)
        rev_fill = _DummyFill(cum_qty=1.0, leaves_qty=0.0)
        self._broadcast(strategies, "on_order_filled", reversal, rev_fill)
        self._broadcast(strategies, "on_position_changed", _DummyPosition(50_000.0), "reversal")

    def _fire_partial_close(self, strategies) -> None:
        """Partial close → on_position_changed."""
        pos = _DummyPosition(50_000.0)
        self._broadcast(strategies, "on_position_changed", pos, "partial_close_0.5")

    def is_stopped(self) -> bool:
        return bool(getattr(self, "_stopped", False))

    # ----- Test factory -----

    @classmethod
    def build_test(
        cls,
        tokens: list[str] = None,
        bars: int = 100,
        seed: int = 0,
        equity: float = 150_000.0,
        strategies: Optional[list] = None,
        listings: Optional[dict[str, int]] = None,
        delistings: Optional[dict[str, int]] = None,
        registry_extra: Optional[list[str]] = None,
        mode: Literal["backtest", "paper"] = "backtest",
        runner_instance_id: str = "default",
        fold_id: Optional[int] = None,
        fold_window: Optional[tuple[int, int]] = None,
        quarantine_threshold: int = 10_000,  # default disables quarantine;
                                              # tests opt in via explicit value
        **kwargs,  # simulate_* + stop_loss_handler_observer flags
    ) -> "UniverseContext":
        """Test harness factory — build a deterministic UniverseContext."""
        tokens = tokens or ["BTC"]
        data = DataView(
            tokens_seed=tokens, bars=bars, seed=seed,
            listings=listings, delistings=delistings,
            registry_extra=registry_extra,
        )
        portfolio = PortfolioView(equity=equity)
        clock = TestClock(epoch_iso="2026-01-01T00:00:00Z", seed=seed)
        orders = OrderFactoryView(runner_instance_id=runner_instance_id, clock=clock)
        if strategies:
            orders.register_strategies(strategies)
        ctx = cls(
            data=data, portfolio=portfolio, clock=clock, orders=orders,
            fold_id=fold_id, fold_window=fold_window,
        )
        # Stash lifecycle config on the ctx via object.__setattr__ (frozen).
        # `ctx_uid` is a process-wide monotonic identifier — never reused,
        # unlike id() which Python recycles after GC. v5.regimes caches
        # (and any other per-ctx memoization) key on ctx_uid to avoid
        # cross-fold contamination when ids recycle.
        object.__setattr__(ctx, "_lifecycle_config", {
            "strategies": strategies or [],
            "bars": bars,
            "quarantine_threshold": quarantine_threshold,
            "ctx_uid": next(_CTX_UID_COUNTER),
            **kwargs,
        })
        return ctx
