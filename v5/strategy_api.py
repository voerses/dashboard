"""V5 Portfolio Backtest — Strategy-facing API.

Legacy (pre-M7): `ScaleAction`, `LinkedScalePolicy`, and re-exports
`StrategySpec`. Preserved verbatim — high blast radius (31+ importers via
v5/simulator.py).

M7 additions (AC-S1): `Strategy` Protocol with 19 callbacks,
`BaseStrategy` default impls, `UniverseSignals` / `TokenSignal` /
`SizingRequest` / `ExitCheck` supporting types.

Per AC-S5 error containment contract (engine-level — enforced by BarProcessor):
  - `on_start` / `on_reset` → FAIL-FAST (engine aborts on exception)
  - `on_stop` → logged; shutdown continues
  - `generate` → logged; engine substitutes EMPTY_SIGNALS
  - `check_scale` / `check_exit` → logged; treated as `return None`
  - `filter_entry` → logged; treated as `return True` (fail-open)
  - `on_order_*` / `on_position_*` → logged; dispatch continues
  - `view_state` → logged; dashboard shows {"error": ...}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from v5.data.streams import Subscription
from v5.strategy_spec import StrategySpec  # noqa: F401 — re-export


# =============================================================
# Legacy types (pre-M7, preserved for simulator.py compatibility)
# =============================================================


@dataclass
class ScaleAction:
    """Scale action returned by strategy scale callbacks.

    Attributes:
        qty_delta: Signed quantity delta. Positive = increase position,
            negative = reduce position.
        reason: Free-form diagnostic string (e.g. "tp_rung_1", "stop_trail").
        stop_override: Optional explicit stop price to set on an increase
            (AC2). ``None`` means no stop change.
        is_stop_like: When ``True`` on a reduce action, dispatch applies a
            stressed ADV participation cap (AC28b).
    """

    qty_delta: float
    reason: str
    stop_override: float | None = None
    is_stop_like: bool = False


class LinkedScalePolicy(Enum):
    """Policy for propagating scale actions from a primary leg to its linked
    secondary leg in a linked (pair) position (AC36)."""

    INDEPENDENT = "independent"
    PROPORTIONAL = "proportional"
    ABSOLUTE = "absolute"


# =============================================================
# M7 Strategy API — Unified Protocol + supporting types (AC-S1)
# =============================================================


# M8 — SizingIntent + SizingRequest are now owned by v5.sizing.intents.
# Re-exported here so existing M7 imports keep working. Design-over-code:
# the enum promotion from Literal → (str, Enum) is a type-hygiene upgrade;
# runtime string values ("FIXED_FRACTION"/"FIXED_NOTIONAL") are preserved
# so paper-state JSON roundtrip remains byte-identical.
from v5.sizing.intents import SizingIntent, SizingRequest  # noqa: F401


@dataclass
class TokenSignal:
    """Per-token signal output from Strategy.generate(). Unifies v4's
    TokenBarArrays (per-token) + PortfolioSignals (portfolio) per AC-S6.
    """
    token: str
    direction: int = 0                  # 1 long / -1 short / 0 none
    priority: Optional[float] = None    # ranking only; NOT sizing
    sizing: Optional[SizingRequest] = None
    # Optional per-trade params
    stop_mult: float = 0.0
    trail_mult: float = 0.0
    target_mult: float = 0.0
    min_hold: int = 0
    max_hold: int = 0


@dataclass
class UniverseSignals:
    """Whole-universe signal output per bar."""
    bar_idx: int = 0
    signals: dict[str, TokenSignal] = field(default_factory=dict)


@dataclass
class ExitCheck:
    """Returned by Strategy.check_exit() to request a position exit."""
    reason: str
    price: Optional[float] = None


@dataclass
class StrategyQuarantined:
    """Event published when a strategy's exception_counter crosses the
    configured threshold. Engine stops dispatching to that strategy;
    peers unaffected. (AC-S5 observability per design §2.7.)
    """
    strategy_id: str
    exception_counter: int
    threshold: int
    last_method: str = ""


@runtime_checkable
class Strategy(Protocol):
    """Unified Strategy contract — 19 Protocol methods.

    15 event callbacks + 4 declaration/query methods (`required_data`,
    `generate`, `check_scale`, `check_exit`, `filter_entry`).

    Protocol is `@runtime_checkable`; `isinstance(obj, Strategy)` checks
    that all 19 methods are present (PEP 544 duck typing).
    """

    # Lifecycle (3) — FAIL-FAST on on_start/on_reset
    def on_start(self, portfolio_config) -> None: ...
    def on_stop(self, reason: str) -> None: ...
    def on_reset(self) -> None: ...

    # Data declaration (1)
    def required_data(self) -> list[Subscription]: ...

    # Signal generation (1)
    def generate(self, ctx, bar_idx: int) -> UniverseSignals: ...

    # Per-position per-bar checks (3)
    def check_scale(self, pos, bar_ctx) -> Optional[ScaleAction]: ...
    def check_exit(self, pos, bar_ctx) -> Optional[ExitCheck]: ...
    def filter_entry(self, candidate, bar_ctx) -> bool: ...

    # FIX-aligned execution event callbacks (7) — maps to ExecType(150) states
    def on_order_accepted(self, order) -> None: ...
    def on_order_rejected(self, order, reason: str) -> None: ...
    def on_order_cancelled(self, order, reason: str) -> None: ...
    def on_order_triggered(self, order) -> None: ...
    def on_order_partial_fill(self, order, fill) -> None: ...
    def on_order_filled(self, order, fill) -> None: ...
    def on_order_expired(self, order) -> None: ...

    # Position lifecycle (3)
    def on_position_opened(self, position) -> None: ...
    def on_position_changed(self, position, delta) -> None: ...
    def on_position_closed(self, closed_trade) -> None: ...

    # Introspection (1)
    def view_state(self) -> dict: ...


# M9 C-1 re-export — tests import BarContext from v5.strategy_api for
# the canonical Strategy Protocol surface. BarContext itself lives in
# v5.exit_handlers (historical placement from M4). Use a module-level
# __getattr__ for lazy resolution — avoids import-order failures in
# test-batch runs where exit_handlers may not be fully imported yet at
# module-load time of strategy_api but is available on first access.
def __getattr__(name):
    if name == "BarContext":
        from v5.exit_handlers import BarContext as _BC
        return _BC
    raise AttributeError(f"module 'v5.strategy_api' has no attribute {name!r}")


class StrategyStateMutationError(RuntimeError):
    """M9 C-7: raised when a strategy mutates `self` state during
    `_engine_precompute_fallback` (backtest setup loop calls
    generate() across all bars upfront). Stateful-self mutation
    during precompute silently diverges from paper per-tick dispatch.
    Prevents the silent research-vs-paper drift class."""


@runtime_checkable
class VectorizedStrategy(Strategy, Protocol):
    """M9 C-7 opt-in sub-Protocol for strategies that produce signal
    arrays up-front (Zipline/Pipeline institutional pattern).

    Strategies that can precompute cheaply (e.g. s524m's composite
    z-score loop) implement `to_token_bar_arrays(ctx)` for the fast
    path. Strategies that can't (s513 bar-reactive triggers) omit this
    method and fall back to `_engine_precompute_fallback` which calls
    `generate()` in a setup loop.

    Parity test (AC #7): for every strategy implementing
    VectorizedStrategy, `to_token_bar_arrays()` output must equal the
    fallback output on the Q-DEC4 2025 fold (np.testing.assert_array_equal
    on int fields + np.array_equal(equal_nan=True) on float fields —
    zero tolerance)."""

    def to_token_bar_arrays(self, ctx) -> dict:
        """Return dict[str, TokenBarArrays] — the v4-shape arrays the
        vectorized simulate_portfolio consumes. Typically a thin wrapper
        around the strategy's internal precompute (e.g. s524m's
        `_evaluate_token` loop)."""
        ...


class BaseStrategy:
    """Default no-op implementations for all 19 Protocol methods.

    Strategies inherit this to override only the methods they need.
    `generate()` MUST be overridden (raises NotImplementedError).
    """

    exception_counter: int = 0   # AC-S5 observability; BarProcessor increments

    # Lifecycle
    def on_start(self, portfolio_config) -> None:
        return None

    def on_stop(self, reason: str) -> None:
        return None

    def on_reset(self) -> None:
        return None

    # Data declaration
    def required_data(self) -> list[Subscription]:
        return []

    # Signal generation — MUST be overridden
    def generate(self, ctx, bar_idx: int) -> UniverseSignals:
        raise NotImplementedError(
            f"{type(self).__name__} must override generate() to produce UniverseSignals"
        )

    # Per-position checks
    def check_scale(self, pos, bar_ctx) -> Optional[ScaleAction]:
        return None

    def check_exit(self, pos, bar_ctx) -> Optional[ExitCheck]:
        return None

    def filter_entry(self, candidate, bar_ctx) -> bool:
        return True

    # Exec event callbacks — 7 no-op defaults
    def on_order_accepted(self, order) -> None:
        return None

    def on_order_rejected(self, order, reason: str = "") -> None:
        return None

    def on_order_cancelled(self, order, reason: str = "") -> None:
        return None

    def on_order_triggered(self, order) -> None:
        return None

    def on_order_partial_fill(self, order, fill=None) -> None:
        return None

    def on_order_filled(self, order, fill=None) -> None:
        return None

    def on_order_expired(self, order) -> None:
        return None

    # Position lifecycle — 3 no-op defaults
    def on_position_opened(self, position) -> None:
        return None

    def on_position_changed(self, position, delta=None) -> None:
        return None

    def on_position_closed(self, closed_trade) -> None:
        return None

    # Introspection
    def view_state(self) -> dict:
        return {}
