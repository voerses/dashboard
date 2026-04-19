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

from v5.clock import Clock, LiveClock
from v5.data.instruments import InstrumentRegistry
from v5.data.streams import Venue
from v5.testing import TestClock


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

    def __init__(self, runner_instance_id: str = "default"):
        self._runner_instance_id = runner_instance_id
        self._active_orders: list = []
        self._reversal_orders: list = []
        self._rejection_events: list = []
        self._next_order_seq = 0

    def _next_seq(self) -> int:
        self._next_order_seq += 1
        return self._next_order_seq

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
        from datetime import datetime, timezone
        from v5.orders import Order, TriggerType
        dir_int = self._direction_int(direction)
        if trigger is None:
            trigger = TriggerType.PRICE_ABOVE if dir_int == 1 else TriggerType.PRICE_BELOW
        seq = self._next_seq()
        order_id = f"order-{seq:08x}"
        order = Order.arm(
            strategy_id=strategy_id,
            token=symbol,
            direction=dir_int,
            trigger=trigger,
            trigger_price=float(trigger_price or 0.0),
            working_price_source="last",
            armed_at=datetime.now(timezone.utc),
            expires_at=None,
            sizing_ctx={"target_size": size, "market": market},
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
        design §2.2); constructs Order with OTO_BRACKET + OTO contingency."""
        from datetime import datetime, timezone
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
            armed_at=datetime.now(timezone.utc),
            expires_at=None,
            sizing_ctx={"target_size": size, "market": market},
            legs=(entry_leg, sl_leg, tp_leg),
            fill_policy=fill_policy if fill_policy is not None else LegFillPolicy.OTO_BRACKET,
            contingency=ContingencyType.OTO,
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
                seq_hex = order_id.split("-", 1)[1] if "-" in order_id else "00000001"
                # Truncate/pad to 8 hex chars
                seq_hex = (seq_hex + "0" * 8)[:8]
                # M5 Order is frozen — §2.8 ID1 object.__setattr__ single-field mutation
                object.__setattr__(o, "venue_order_id", self._paper_venue_id(seq_hex))
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
                self._rejection_events.append(SimpleNamespace(
                    reason=f"post_fill_unwind:venue_reject leg={leg_idx} ({reason})",
                    order=o,
                ))
            else:
                self._rejection_events.append(SimpleNamespace(
                    reason=f"venue_reject:{reason}",
                    order=o,
                ))
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

    def mutable_copy(self, arr: np.ndarray) -> np.ndarray:
        """AC-S2 opt-in mutation — returns np.copy(arr) (writable)."""
        return np.copy(arr)

    def seek_bar(self, idx: int) -> None:
        """Test harness — advance the bar cursor on the inner DataView."""
        # DataView is NOT frozen (per §2.8 ID2), so this mutation is allowed
        self.data._bar_idx = idx

    def run_full_lifecycle(self) -> None:
        """Test harness stub — Phase-4 impl calls the BarProcessor Wave D
        dispatch. This scaffold is a no-op so lifecycle tests can exercise
        the Protocol shape without requiring BarProcessor wiring.
        """
        # BarProcessor 15-callback dispatch is Task 11; this is Wave-A scaffold
        pass

    def is_stopped(self) -> bool:
        return True  # Wave-A scaffold

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
        **kwargs,  # swallow extra test-harness kwargs for forward-compat
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
        orders = OrderFactoryView(runner_instance_id=runner_instance_id)
        return cls(
            data=data, portfolio=portfolio, clock=clock, orders=orders,
            fold_id=fold_id, fold_window=fold_window,
        )
