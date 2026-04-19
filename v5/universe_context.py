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

    `arm(...)` single-leg, `arm_bracket(entry, sl, tp)` three-leg with
    OTO_BRACKET + OTO contingency. Both publish via M5 pre-fill atomic path.
    """

    def __init__(self, runner_instance_id: str = "default"):
        self._runner_instance_id = runner_instance_id
        self._active_orders: list = []
        self._reversal_orders: list = []
        self._rejection_events: list = []
        self._next_order_seq = 0

    def arm(self, **kwargs):
        """Stub — real implementation in Task 9. Returns a test-shaped Order."""
        from types import SimpleNamespace
        self._next_order_seq += 1
        order = SimpleNamespace(
            order_id=f"order-{self._next_order_seq:08x}",
            venue_order_id=None,
            legs=[SimpleNamespace(
                trigger_price=kwargs.get("trigger_price"),
                target_qty=kwargs.get("size", 0.0),
                direction=kwargs.get("direction", "LONG"),
            )],
            **{k: v for k, v in kwargs.items() if k not in ("trigger_price", "size", "direction")},
        )
        self._active_orders.append(order)
        return order

    def arm_bracket(self, entry_spec: dict, sl_spec: dict, tp_spec: dict, **kwargs):
        """Stub — real implementation in Task 9."""
        from types import SimpleNamespace
        self._next_order_seq += 1
        # Basic side validation for LIMIT/STOP entries (skip for MARKET)
        entry_price = entry_spec.get("trigger_price")
        direction = entry_spec.get("direction", "LONG")
        if entry_price is not None:
            sl = sl_spec.get("trigger_price")
            tp = tp_spec.get("trigger_price")
            if direction == "LONG":
                if sl is not None and sl >= entry_price:
                    raise ValueError(f"LONG bracket: SL {sl} must be below entry {entry_price}")
                if tp is not None and tp <= entry_price:
                    raise ValueError(f"LONG bracket: TP {tp} must be above entry {entry_price}")
            else:  # SHORT
                if sl is not None and sl <= entry_price:
                    raise ValueError(f"SHORT bracket: SL {sl} must be above entry {entry_price}")
                if tp is not None and tp >= entry_price:
                    raise ValueError(f"SHORT bracket: TP {tp} must be below entry {entry_price}")
        order = SimpleNamespace(
            order_id=f"order-{self._next_order_seq:08x}",
            venue_order_id=None,
            legs=[
                SimpleNamespace(trigger_price=entry_spec.get("trigger_price"),
                                target_qty=entry_spec["size"], direction=direction),
                SimpleNamespace(trigger_price=sl_spec.get("trigger_price"),
                                target_qty=entry_spec["size"],
                                direction="SHORT" if direction == "LONG" else "LONG"),
                SimpleNamespace(trigger_price=tp_spec.get("trigger_price"),
                                target_qty=entry_spec["size"],
                                direction="SHORT" if direction == "LONG" else "LONG"),
            ],
            contingency=None,
            fill_policy=kwargs.get("fill_policy"),
        )
        self._active_orders.append(order)
        return order

    def active_orders(self) -> list:
        return list(self._active_orders)

    def last_reversal_order(self):
        return self._reversal_orders[-1] if self._reversal_orders else None

    def last_rejection_event(self):
        return self._rejection_events[-1] if self._rejection_events else None

    def simulate_ack(self, order_id: str) -> None:
        for o in self._active_orders:
            if o.order_id == order_id:
                # AC-O4 paper ack — use design §2.8 ID1 object.__setattr__ contract;
                # SimpleNamespace allows direct attribute mutation so this is simpler
                seq_hex = order_id.split("-", 1)[1]  # 8-hex suffix from order_id
                if self._runner_instance_id == "default":
                    o.venue_order_id = f"paper-{seq_hex}"
                else:
                    o.venue_order_id = f"paper-{self._runner_instance_id}-{seq_hex}"
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
