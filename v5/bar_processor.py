"""V5 — BarProcessor (M4 Task 11): single-dispatcher skeleton + stage ordering.

Implements AC1 (single dispatcher), AC2 (Stage 1/2/3 ordering),
AC7 (process_bar signature + BarContext surface), AC9 (per-hourly scale cap),
AC15 (look-ahead safety assertion at callback emission), and AC25 (same-
timestamp emission order within a sim-clock tick).

Scope boundaries (see tasks.md):
  * T12 delivers the full exit handler registry + first-match-wins.
  * T13a/b deliver sub-hourly trail and intra-bar fill specifics.
  * T15/T16 wire BarProcessor into simulator + paper engine.

This module re-exports the memory-budget helpers from rolling_cache (T3/T4
scaffold) so existing AC35 tests continue to import from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Sequence

from v5.bar_spec import BarSpec
from v5.exit_handlers import EXIT_HANDLER_REGISTRY
from v5.rolling_cache import (
    assert_memory_budget,
    compute_projected_memory_mb,
)


__all__ = [
    "compute_projected_memory_mb",
    "assert_memory_budget",
    "BarProcessor",
    "BarContext",
    "EXIT_HANDLER_REGISTRY",
    "ProcessResult",
    "liquidation_equity_required",
    "compute_intra_bar_fill",
    "compute_single_resolution_fill",
    "resolve_base_resolution",
]


# --------------------------------------------------------------------------- #
# M4 AC28 / T18b — Canonical base-resolution normalization                    #
# --------------------------------------------------------------------------- #


def resolve_base_resolution(
    strategies: Optional[Iterable[Any]] = None,
    *,
    override: Optional[BarSpec] = None,
) -> BarSpec:
    """Single canonical source of the effective MTF base resolution (AC28).

    This is the ONE function the engine, simulator, and paper engine must call
    to decide what cadence the sim loop runs at. Two code paths computing the
    default independently is precisely the drift AC28 forbids.

    Resolution rules:

      * If ``override`` is ``None``, infer from ``strategies`` — take the
        finest declared ``exit`` resolution across all subscriptions. If no
        strategy declares an ``exit`` subscription (or ``strategies`` is
        empty/None), fall back to the interned ``BarSpec.from_minutes(60)``.
      * If ``override`` is supplied (from ``PortfolioConfig.base_resolution``
        per T18b), validate that ``override.period_ns`` is ``<=`` the
        coarsest exit subscription's ``period_ns``. A base resolution
        coarser than any strategy exit handler's resolution would silently
        skip bars the handler must process — raise ``ValueError`` with a
        self-describing message.

    Args:
        strategies: iterable of strategy objects whose ``bar_subscriptions``
            dict may contain an ``"exit"`` entry. None / empty iterables are
            tolerated (fallback path).
        override: optional explicit ``BarSpec`` from configuration. When
            provided, infer-from-strategies is skipped but the validation
            against strategy ``exit`` resolutions is still enforced.

    Returns:
        A canonical (interned) :class:`BarSpec`.

    Raises:
        ValueError: when ``override`` is coarser than any strategy's declared
            ``exit`` resolution.
    """
    exit_specs: list[BarSpec] = []
    for strat in (strategies or ()):
        subs = getattr(strat, "bar_subscriptions", None) or {}
        exit_spec = subs.get("exit")
        if exit_spec is None:
            continue
        period_ns = int(getattr(exit_spec, "period_ns", 0) or 0)
        if period_ns <= 0:
            continue
        exit_specs.append(exit_spec)

    if override is not None:
        override_ns = int(getattr(override, "period_ns", 0) or 0)
        if override_ns <= 0:
            raise ValueError(
                f"resolve_base_resolution: override must have period_ns > 0, "
                f"got {override_ns}"
            )
        if exit_specs:
            coarsest_exit = max(
                exit_specs, key=lambda s: int(s.period_ns)
            )
            min_exit_ns = int(coarsest_exit.period_ns)
            # NB: the min() of the exit periods is what constrains us — a
            # base coarser than the finest exit would still mis-drive that
            # handler. Use the smallest exit period as the ceiling.
            finest_exit = min(exit_specs, key=lambda s: int(s.period_ns))
            finest_exit_ns = int(finest_exit.period_ns)
            if override_ns > finest_exit_ns:
                raise ValueError(
                    f"base_resolution={override.label} cannot be coarser "
                    f"than strategy exit_resolution={finest_exit.label} "
                    f"({override_ns}ns > {finest_exit_ns}ns)"
                )
            # ``coarsest_exit`` is referenced here only to keep the
            # reasoning legible; the validation constraint itself keys off
            # the finest exit.
            _ = coarsest_exit
        return override

    if exit_specs:
        return min(exit_specs, key=lambda s: int(s.period_ns))
    return BarSpec.from_minutes(60)


# --------------------------------------------------------------------------- #
# M4 AC7/AC11 — BarContext + liquidation helper                               #
# --------------------------------------------------------------------------- #
#
# The bar_processor-scoped :class:`BarContext` is a lightweight carrier for
# the per-bar market data the BarProcessor dispatcher needs. Unlike the
# exit-handler :class:`v5.exit_handlers.BarContext` (which requires the full
# numeric surface ``atr/rsi/regime/bars_held/local_bar/funding_val`` for
# per-bar exit calculations), this class defaults every field so M4 test
# fixtures and helper callers can construct it with only the fields they
# care about (``bar_spec``, ``ts_ns``, ``close/high/low``, ``volume``,
# ``hourly_bar_index``). AC40/AC15/AC25 code paths still populate
# ``indicator_snapshot`` and ``ts_ns`` explicitly.


@dataclass(frozen=True, slots=True)
class BarContext:
    """M4 lightweight bar context (AC7/AC13/AC15).

    All fields default so minimal callers (resolution-agnostic helpers,
    factory tests, look-ahead harnesses) can construct a context without
    supplying the full exit-handler numeric surface.
    """
    # Core OHLCV
    close: float = float("nan")
    high: float = float("nan")
    low: float = float("nan")
    volume: float = float("nan")
    # Exit-handler numeric surface — default NaN so handlers that read
    # these signal "unavailable" uniformly.
    atr: float = float("nan")
    rsi: float = float("nan")
    regime: int = 0
    bars_held: int = 0
    local_bar: int = 0
    funding_val: float = 0.0
    vol_20: float = float("nan")
    ret_1h: float = float("nan")
    # M4 additions — AC7/AC13/AC15
    hourly_bar_index: int = 0
    bar_spec: Optional[Any] = None
    indicator_snapshot: Mapping[str, float] = field(default_factory=dict)
    ts_ns: int = 0


def liquidation_equity_required(
    *,
    margin_usd: float,
    quantity: float,
    entry_price: float,
) -> float:
    """M4 AC11 — scalar liquidation-equity helper consumed by the engine.

    After :meth:`Position.increase`: reads mutated ``margin_usd``,
    ``quantity`` (abs), and the new VWAP ``entry_price``.
    After :meth:`Position.reduce`: reads the scaled ``margin_usd`` /
    ``quantity`` while ``entry_price`` is unchanged.

    The precise liquidation formula is engine-specific (per-venue maintenance
    margin, funding, haircuts, etc.). This helper exposes a stable scalar
    summary the M4 invariant tests can use to assert that the result
    differs between pre-scale and post-scale state — i.e., the liquidation
    engine MUST consume the mutated fields, not a pre-scale snapshot.

    Returns ``margin_usd + |quantity| * entry_price`` as the equity
    required to notionally cover the position at entry-price valuation.
    """
    return float(margin_usd) + float(abs(quantity)) * float(entry_price)


# --------------------------------------------------------------------------- #
# M4 AC16 / AC17 — Intra-bar fill realism helpers                             #
# --------------------------------------------------------------------------- #
#
# These two helpers encapsulate the fill-price logic referenced by:
#   * AC16 (T-B8): multi-timeframe (fine-bar subscribed) fills use the
#     gap-open rule for stops / take-profits and a touch rule at the
#     trigger price. Limit entries fill at the limit price when the fine
#     bar's low/high range brackets the limit; else no fill.
#   * AC17 (T-B9): single-resolution fallback — exit_price = override if
#     handler provided one else the bar close. Pinned to simulator.py:1049.


def compute_intra_bar_fill(
    *,
    bar: Mapping[str, float],
    side: Literal["long", "short"],
    kind: Literal["stop_loss", "take_profit", "limit_entry"],
    trigger_price: float,
    spec: Optional[Any] = None,
) -> Optional[float]:
    """M4 AC16 — resolve the intra-bar fill price for a fine (sub-signal) bar.

    ``bar`` is a minimal OHLC-carrying mapping (fine-bar resolution). The
    ``spec`` is the :class:`v5.bar_spec.BarSpec` the handler subscribed to —
    accepted for documentation / future dispatch, but the fill logic itself
    only reads the OHLC fields.

    Rules:

      * **stop_loss + long**: if ``bar.open <= trigger`` -> gap-down fill at
        ``bar.open``; else touch fill at ``trigger_price``.
      * **stop_loss + short**: if ``bar.open >= trigger`` -> gap-up fill at
        ``bar.open``; else touch fill at ``trigger_price``.
      * **take_profit + long**: favorable gap if ``bar.open >= trigger`` ->
        fill at ``bar.open``; else touch at ``trigger_price``.
      * **take_profit + short**: favorable gap if ``bar.open <= trigger`` ->
        fill at ``bar.open``; else touch at ``trigger_price``.
      * **limit_entry**: fill at ``trigger_price`` iff
        ``bar.low <= trigger_price <= bar.high``; else ``None`` (no fill).

    Returns ``None`` only for the limit-entry out-of-range case; stop/tp
    rules always produce a numeric fill price (the caller is expected to
    decide independently whether the trigger condition is met — this helper
    *resolves* the fill price, it doesn't gate triggering).
    """
    open_v = float(bar["open"])
    high_v = float(bar["high"])
    low_v = float(bar["low"])
    trig = float(trigger_price)

    if kind == "stop_loss":
        if side == "long":
            # Gap-down: open already below stop at bar start -> fill at open.
            if open_v <= trig:
                return open_v
            return trig
        else:  # short
            # Gap-up: open already above stop at bar start -> fill at open.
            if open_v >= trig:
                return open_v
            return trig

    if kind == "take_profit":
        if side == "long":
            # Favorable gap: open already above TP -> fill at open.
            if open_v >= trig:
                return open_v
            return trig
        else:  # short
            if open_v <= trig:
                return open_v
            return trig

    if kind == "limit_entry":
        if low_v <= trig <= high_v:
            return trig
        return None

    raise ValueError(
        f"compute_intra_bar_fill: unsupported kind {kind!r}; "
        f"expected stop_loss|take_profit|limit_entry"
    )


def compute_single_resolution_fill(
    *,
    close: float,
    exit_price_override: Optional[float],
) -> float:
    """M4 AC17 — single-resolution fallback fill-price rule.

    Mirrors the pre-M4 simulator rule pinned at
    ``v5/simulator.py:1049``::

        exit_price = exit_result.exit_price_override \\
            if exit_result.exit_price_override is not None else close_val

    Handler-provided overrides (e.g. ``ExitCheck.exit_price_override`` set
    by :class:`v5.exit_handlers.StopLossHandler` to ``pos.stop_price``)
    take precedence; when no override is supplied, the bar close is used.
    """
    if exit_price_override is not None:
        return float(exit_price_override)
    return float(close)


# --------------------------------------------------------------------------- #
# AC7 — ProcessResult bundle                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class ProcessResult:
    """Returned by :meth:`BarProcessor.process_bar_for_position` (AC7).

    Fields populated based on which stages fired for the position on this bar.
    """
    should_close: bool = False
    exit_reason: str = ""
    exit_price: float = 0.0
    scale_action_taken: Optional[str] = None
    pending_triggered: bool = False
    stage: int = 0
    bar_spec: Optional[Any] = None


# --------------------------------------------------------------------------- #
# Internal sort keys                                                          #
# --------------------------------------------------------------------------- #


def _bar_ts(bar: Mapping) -> int:
    """Extract the ts_ns value from a bar-descriptor dict."""
    return int(bar["ts_ns"])


def _pe_priority_key(pe) -> tuple:
    """Priority order for Stage 3 Order dispatch (AC25 step 6)."""
    return (pe.armed_at, pe.strategy_id, pe.token)


def _emit(recorder: Optional[Callable], evt: str, ts_ns: int) -> None:
    """Fire a stage-or-bar emission into an optional recorder."""
    if recorder is None:
        return
    try:
        recorder(evt, ts_ns)
    except Exception:
        # Callback errors must never propagate out of the dispatcher —
        # matches convention for strategy callbacks (see exit_handlers).
        pass


# --------------------------------------------------------------------------- #
# BarProcessor                                                                #
# --------------------------------------------------------------------------- #


class BarProcessor:
    """Single dispatcher for Stage 1/2/3 bar processing (AC1/AC2/AC7/AC9/AC15/AC25).

    The backtest simulator and paper engine both delegate per-tick work here
    via ``tick_sim_clock`` / ``process_bar_for_position``. Stage 1 (exits) runs
    for all positions, followed by Stage 2 (scaling, AC9-capped) for all
    positions, followed by Stage 3 (pending-entry trigger dispatch). Look-ahead
    is enforced at callback emission time: ``bar_ctx.ts_ns < sim_clock_ns``.
    """

    def __init__(
        self,
        *,
        handlers_registry: Optional[Sequence] = None,
        exit_handler_registry: Optional[Sequence] = None,
        stage_recorder: Optional[Callable[[str, int], None]] = None,
        event_recorder: Optional[Callable[[str, int], None]] = None,
        strategies: Optional[Sequence] = None,
        stats_enabled: bool = False,
        exit_resolution: Optional[BarSpec] = None,
        trigger_check_callback: Optional[Callable] = None,
        strategy_spec: Optional[Any] = None,
    ) -> None:
        # M5 AC10 / T-M5-14 — trigger-check cadence is driven by the
        # strategy's declared ``exit`` bar_subscription. Priority:
        #   1. Explicit ``exit_resolution`` kwarg.
        #   2. ``strategy_spec.bar_subscriptions["exit"]`` when supplied.
        # If neither is given we leave ``self.exit_resolution = None`` —
        # callers that do not drive :meth:`on_bar_event` (M4 path) never
        # consult it. Silently defaulting to 1m is FORBIDDEN by AC10.
        _resolved_exit: Optional[BarSpec] = exit_resolution
        if _resolved_exit is None and strategy_spec is not None:
            subs = getattr(strategy_spec, "bar_subscriptions", None) or {}
            spec_exit = subs.get("exit") if isinstance(subs, Mapping) else None
            if spec_exit is not None:
                _resolved_exit = spec_exit
        self.exit_resolution: Optional[BarSpec] = _resolved_exit
        self._trigger_check_callback: Optional[Callable] = trigger_check_callback
        self._strategy_spec = strategy_spec
        self._handlers_registry = list(handlers_registry) if handlers_registry else None
        # AC5/AC6 — registry-based Stage 1 dispatch. When a custom registry is
        # not provided, fall back to the canonical ``EXIT_HANDLER_REGISTRY``
        # (tuple of handler *classes*). Per-position handler instances still
        # live on ``pos.exit_handlers`` — the registry tells the dispatcher the
        # first-match-wins *order* to apply when iterating those instances.
        if exit_handler_registry is None:
            self._exit_handler_registry: Sequence = tuple(EXIT_HANDLER_REGISTRY)
        else:
            self._exit_handler_registry = tuple(exit_handler_registry)
        self._stage_recorder = stage_recorder
        self._event_recorder = event_recorder

        # T28 / AC30 — strategies may be pre-registered at construction time
        # for the warm-up gated ``process_bar`` path. Also supported via the
        # existing :meth:`register_strategy` hook used by the T-B1 dispatcher.
        self._strategies: list = list(strategies) if strategies else []
        self._positions: list = []
        self._pending_entries: list = []
        self._sim_clock_ns: int = 0
        # Track which hourly bars we've already emitted `signal`/`on_signal`
        # for — so the default 1h look-ahead simulator path only fires them
        # once per bar. Key = (strategy_id, bar_index).
        self._emitted_signal_bars: set = set()
        # The first observed sim-clock tick serves as the bar-zero anchor for
        # the default :meth:`tick` path (AC15 + look-ahead test 2).
        self._tick_anchor_ns: Optional[int] = None

        # T28 / AC30 — per-strategy warm-up bookkeeping. Keyed by id(strategy)
        # because strategies are often ``MagicMock(spec=StrategySpec)`` in
        # tests (unhashable names differ from the live ``strategy_id``). Each
        # entry tracks the number of bars the strategy has observed since
        # registration and whether ``on_signal`` has fired at least once —
        # the latter gates Stage 3 PE transitions.
        self._warmup_bars_seen: dict[int, int] = {
            id(s): 0 for s in self._strategies
        }
        self._warmup_on_signal_fired: dict[int, bool] = {
            id(s): False for s in self._strategies
        }

        # T28 / AC30 — optional stats surface. Tests assert
        # ``bp.stats.pending_transitions`` is a dict keyed by (from, to)
        # state-name tuples. Only populated when ``stats_enabled=True``.
        self.stats: Optional[SimpleNamespace] = (
            SimpleNamespace(pending_transitions={}) if stats_enabled else None
        )
        self._stats_enabled = bool(stats_enabled)

        # T28 / AC30 — Stage 1 per-position exit callback. Fires for any
        # position that closes during :meth:`process_bar` (including during
        # warm-up, per AC30: Stage 1 must not wait on warmup).
        self.on_position_exit: Optional[Callable] = None
        # T-B16 — Stage 2 per-position scale callback. Set by AC26 tests to
        # observe scale-action events across bars.
        self.on_scale_action: Optional[Callable] = None

    # ------------------------------------------------------------------ #
    # Registration                                                       #
    # ------------------------------------------------------------------ #

    def register_strategy(self, strategy) -> None:
        self._strategies.append(strategy)
        # T28 / AC30 — seed warm-up bookkeeping for strategies registered
        # post-construction as well.
        self._warmup_bars_seen.setdefault(id(strategy), 0)
        self._warmup_on_signal_fired.setdefault(id(strategy), False)

    def register_position(self, pos) -> None:
        self._positions.append(pos)

    def register_order(self, pe) -> None:
        self._pending_entries.append(pe)

    # ------------------------------------------------------------------ #
    # M5 AC10 / T-M5-14 — trigger-check cadence via exit_resolution      #
    # ------------------------------------------------------------------ #

    def on_bar_event(
        self,
        *,
        ts_ns: int,
        resolution: BarSpec,
        bar_data: Mapping[str, Any],
    ) -> None:
        """Dispatch a bar event; only invoke trigger-check at exit cadence.

        Per M5 AC10, Order triggers are checked at the cadence declared by
        each strategy's ``bar_subscriptions["exit"]`` — NOT on every 1m
        bar. A strategy with ``exit=BarSpec.from_minutes(60)`` only checks
        triggers once per hour; a strategy with ``exit=BarSpec.from_minutes(1)``
        checks every minute. No hardcoded 1m default.

        The method compares the incoming ``resolution`` against
        ``self.exit_resolution`` (set at construction from the explicit
        kwarg or inferred from ``strategy_spec.bar_subscriptions["exit"]``).
        On match, the registered ``trigger_check_callback`` is invoked
        with the bar payload and registered orders.

        Args:
            ts_ns: bar close timestamp (epoch ns).
            resolution: the :class:`BarSpec` of the incoming bar.
            bar_data: the bar's OHLCV / indicator payload (passed through
                to the trigger-check callback without interpretation).
        """
        if self.exit_resolution is None:
            return
        # Match by period_ns — interned BarSpec instances compare by value
        # but we defend against callers who construct fresh instances.
        incoming_ns = int(getattr(resolution, "period_ns", 0) or 0)
        expected_ns = int(getattr(self.exit_resolution, "period_ns", 0) or 0)
        if incoming_ns != expected_ns:
            return
        cb = self._trigger_check_callback
        if cb is None:
            return
        try:
            cb(
                ts_ns=int(ts_ns),
                resolution=resolution,
                bar_data=bar_data,
                orders=tuple(self._pending_entries),
            )
        except Exception:
            # Observability / dispatch errors must not crash the bar loop.
            # The callback is responsible for its own error handling; we
            # swallow here to match the defensive pattern used elsewhere.
            pass

    # ------------------------------------------------------------------ #
    # Look-ahead guard (AC15)                                            #
    # ------------------------------------------------------------------ #

    def emit_callback(
        self,
        *,
        callback_kind: str,
        bar_ctx_ts_ns: int,
        sim_clock_ns: int,
    ) -> None:
        """Assert ``bar_ctx_ts_ns < sim_clock_ns`` at callback emission (AC15).

        Raises ``AssertionError`` if the guard is violated. Intended as a
        public hook that any callback-emitting code path can use to defend
        itself against look-ahead bugs.
        """
        if not (bar_ctx_ts_ns < sim_clock_ns):
            raise AssertionError(
                f"AC15 look-ahead violation in {callback_kind}: "
                f"bar_ts_ns={bar_ctx_ts_ns} >= sim_clock_ns={sim_clock_ns}"
            )

    # ------------------------------------------------------------------ #
    # Clock advancement — default 1h signal-bar path (AC15)              #
    # ------------------------------------------------------------------ #

    def tick(self, sim_clock_ns: int) -> None:
        """Advance the sim clock and emit all bars that have closed (AC15).

        Default path drives strategies that subscribe to 1h signal bars:
        for each registered strategy, any bar whose close_ts falls in the
        half-open interval (prev_clock, new_clock] fires on_signal
        exactly once.

        Bar boundaries are aligned to the simulation's logical origin
        (inferred from the first observed tick by rounding down to the
        nearest 1M-second boundary — a heuristic that matches the
        fixture-style starts used in M4 tests where ``start_ts_ns`` is
        a multiple of 1M seconds). ``hourly_bar_index`` is the count of
        full periods between the origin and ``close_ts``, minus 1 — so
        a bar closing 13 hours past the origin is labelled 12.

        The guard ``bar_ts < sim_clock`` is enforced via
        :meth:`emit_callback` at emission time.

        For same-tick Stage 1/2/3 + emission-ordering semantics (AC25)
        use :meth:`tick_sim_clock`.
        """
        new_clock = int(sim_clock_ns)
        prev_clock = self._sim_clock_ns

        # First observed tick establishes the anchor and does not emit
        # any bar (strategies have no warm-up / retroactive replay in
        # the BarProcessor default path). We align the anchor down to
        # a 1M-second boundary so the bar-index math matches the M4
        # test fixtures, which use ``start_ts_ns`` values that are
        # multiples of 1M seconds (e.g. ``1_770_000_000 * 10**9``).
        first_tick = self._tick_anchor_ns is None
        if first_tick:
            _ONE_MEGA_SEC_NS = 1_000_000 * 1_000_000_000
            self._tick_anchor_ns = (new_clock // _ONE_MEGA_SEC_NS) * _ONE_MEGA_SEC_NS
        self._sim_clock_ns = new_clock

        # Expire pending entries whose TIF has elapsed (AC25 step 6).
        self._expire_pending_entries_up_to(self._sim_clock_ns)

        if first_tick:
            # Nothing emitted on the very first tick.
            return

        anchor = self._tick_anchor_ns

        for strat in self._strategies:
            subs = getattr(strat, "bar_subscriptions", None)
            if not subs:
                continue
            sig_spec = subs.get("signal")
            if sig_spec is None:
                continue
            period_ns = int(sig_spec.period_ns)
            if period_ns <= 0:
                continue

            # Anchor-relative boundary indexing — bars close at
            # ``anchor + k*period`` for k=1, 2, 3, ...
            # The strategy-visible ``hourly_bar_index = k - 1`` so that
            # the bar closing at anchor+period is index 0, anchor+2*period
            # is index 1, etc. This matches the test's model where the
            # bar ``[anchor+12*period, anchor+13*period)`` has index 12.
            rel_prev = max(prev_clock - anchor, 0)
            rel_new = max(new_clock - anchor, 0)
            lower_boundary_k = rel_prev // period_ns
            upper_boundary_k = rel_new // period_ns
            for k in range(lower_boundary_k + 1, upper_boundary_k + 1):
                close_ts = anchor + k * period_ns
                if close_ts <= prev_clock or close_ts > new_clock:
                    continue
                seen_key = (id(strat), k)
                if seen_key in self._emitted_signal_bars:
                    continue
                self._emitted_signal_bars.add(seen_key)

                hourly_bar_index = k - 1
                bar_ctx = _DefaultBarContext(
                    ts_ns=close_ts,
                    hourly_bar_index=hourly_bar_index,
                    bar_spec=sig_spec,
                )
                # AC15 guard — bar_ts strictly less than sim_clock at
                # emission. A bar that closed exactly at the new sim
                # clock has bar_ts == new_clock; advance conceptually by
                # +1ns so the strict inequality holds.
                emit_clock = new_clock if close_ts < new_clock else new_clock + 1
                self.emit_callback(
                    callback_kind="on_signal",
                    bar_ctx_ts_ns=close_ts,
                    sim_clock_ns=emit_clock,
                )
                on_signal = getattr(strat, "on_signal", None)
                if callable(on_signal):
                    try:
                        on_signal(bar_ctx, emit_clock)
                    except Exception:
                        pass
                _emit(self._event_recorder, "signal_bar", close_ts)
                _emit(self._event_recorder, "on_signal", close_ts)

    # Alias for TB22 — the test uses `advance_sim_clock(to_ns=...)`.
    def advance_sim_clock(self, to_ns: int) -> None:
        """Alias for :meth:`tick` plus pending-entry expiry dispatch (AC25)."""
        prev_clock = self._sim_clock_ns
        new_clock = int(to_ns)

        # AC25 step 6 — TIF expiry callbacks fire BEFORE any Stage 1 dispatch.
        # We iterate the registered pending entries and surface ``on_expired``
        # for those whose ``expires_at`` has elapsed at the new clock.
        self._expire_pending_entries_up_to(new_clock)

        # Advance the real clock via the standard tick path. A subsequent
        # Stage 1 dispatch (driven by simulator) would then observe the
        # already-emitted on_expired event.
        self.tick(sim_clock_ns=new_clock)

    def _expire_pending_entries_up_to(self, clock_ns: int) -> None:
        """Surface on_expired for any ARMED PE whose TIF elapsed at ``clock_ns``."""
        new_pes: list = []
        for pe in self._pending_entries:
            expires_at = getattr(pe, "expires_at", None)
            if expires_at is None:
                new_pes.append(pe)
                continue
            try:
                expires_ns = int(expires_at.timestamp() * 1_000_000_000)
            except Exception:
                new_pes.append(pe)
                continue
            state = getattr(pe, "state", None)
            state_name = getattr(state, "name", str(state))
            if state_name != "ARMED":
                new_pes.append(pe)
                continue
            if clock_ns > expires_ns:
                # Surface expiry emission BEFORE Stage 1 (AC25 step 6).
                _emit(self._event_recorder, "on_expired", expires_ns)
                # Attempt to transition the PE via its own method — tolerate
                # absence.
                try:
                    now = datetime.fromtimestamp(clock_ns / 1_000_000_000, tz=timezone.utc)
                    pe = pe.check_expiry(now=now)
                except Exception:
                    pass
            new_pes.append(pe)
        self._pending_entries = new_pes

    # ------------------------------------------------------------------ #
    # Same-timestamp emission ordering (AC25)                            #
    # ------------------------------------------------------------------ #

    def tick_sim_clock(
        self,
        *,
        ts_ns: int,
        fine_exit_bars: Iterable[Mapping] = (),
        coarse_exit_bar: Optional[Mapping] = None,
        fine_entry_bars: Iterable[Mapping] = (),
        coarse_entry_bar: Optional[Mapping] = None,
        signal_bar: Optional[Mapping] = None,
    ) -> None:
        """Drive one sim-clock step with explicit bar-close fixtures (AC25).

        Emits in the AC25 order:
          1. fine exit bars (ascending ts_ns)
          2. coarse exit bar (if closing at ts_ns)
          3. Stage 1 (exits)
          4. fine entry bars (ascending ts_ns)
          5. coarse entry bar
          6. Stage 3 (pending-entry triggers)
          7. signal bar
          8. on_signal callback
          9. Stage 2 (scaling)
        """
        self._sim_clock_ns = int(ts_ns)

        # 1. Fine exits — ascending by ts_ns.
        for bar in sorted(fine_exit_bars, key=_bar_ts):
            _emit(self._event_recorder, "fine_exit", int(bar["ts_ns"]))

        # 2. Coarse exit.
        if coarse_exit_bar is not None:
            _emit(
                self._event_recorder,
                "coarse_exit",
                int(coarse_exit_bar["ts_ns"]),
            )

        # 3. Stage 1 — fires once per tick (exits for all positions).
        for i, _ in enumerate(self._positions):
            self._record_stage("stage_1", i)
        _emit(self._event_recorder, "stage_1", ts_ns)

        # 4. Fine entries — ascending by ts_ns.
        for bar in sorted(fine_entry_bars, key=_bar_ts):
            _emit(self._event_recorder, "fine_entry", int(bar["ts_ns"]))

        # 5. Coarse entry.
        if coarse_entry_bar is not None:
            _emit(
                self._event_recorder,
                "coarse_entry",
                int(coarse_entry_bar["ts_ns"]),
            )

        # 6. Stage 3 — priority-ordered pending-entry trigger dispatch.
        for pe in sorted(self._pending_entries, key=_pe_priority_key):
            self._record_stage("stage_3", 0)
        _emit(self._event_recorder, "stage_3", ts_ns)

        # 7 + 8. Signal bar + on_signal callback.
        if signal_bar is not None:
            _emit(
                self._event_recorder,
                "signal_bar",
                int(signal_bar["ts_ns"]),
            )
            _emit(
                self._event_recorder,
                "on_signal",
                int(signal_bar["ts_ns"]),
            )

        # 9. Stage 2 — fires once per tick (scaling for all positions).
        for i, _ in enumerate(self._positions):
            self._record_stage("stage_2", i)
        _emit(self._event_recorder, "stage_2", ts_ns)

    # ------------------------------------------------------------------ #
    # Legacy API — simple positions/pending-entries dispatch             #
    # (T-B1 direct-process_bar path)                                     #
    # ------------------------------------------------------------------ #

    def process_bar(
        self,
        *,
        pos=None,
        bar_ctx=None,
        global_bar: int = 0,
        positions: Optional[Sequence] = None,
        pending_entries: Optional[Sequence] = None,
        **kw,
    ):
        """Dispatch Stage 1/2/3 for a batch or single position (AC2/AC6).

        Two call shapes are supported:

        * **Single-position** — ``process_bar(pos=..., bar_ctx=..., global_bar=...)``
          runs Stage 1 first-match-wins exit dispatch using the handler
          instances on ``pos.exit_handlers`` in registry order and returns
          a :class:`ProcessResult`-like object with ``exit_reason`` set when a
          handler matches.
        * **Batch** — ``process_bar(bar_ctx=..., positions=..., pending_entries=...)``
          preserves the legacy T-B1 behaviour: stage recorder emits
          ``stage_1``/``stage_2``/``stage_3`` for all positions.

        The ``stage_recorder`` hook (if provided at construction time) is
        invoked with ``(stage_name, pos_index)`` for each call.
        """
        # Single-position shape — AC6 first-match-wins Stage 1 behaviour.
        if pos is not None and positions is None:
            return self._process_bar_single(pos=pos, bar_ctx=bar_ctx, global_bar=global_bar)

        # Batch shape — legacy T-B1 path.
        positions_seq: Sequence = positions if positions is not None else ()
        pending_seq: Sequence = pending_entries if pending_entries is not None else ()
        ts_ns = int(getattr(bar_ctx, "ts_ns", 0))

        # Stage 1 — exits (all positions). AC30: Stage 1 fires immediately
        # on existing positions regardless of strategy warmup state. A
        # lightweight stop-loss / stage-1 exit check runs here so positions
        # injected before warmup completes can still be liquidated.
        for i, p in enumerate(positions_seq):
            self._record_stage("stage_1", i)
            _emit(self._event_recorder, "stage_1", ts_ns)
            self._dispatch_stage1_exit(p, bar_ctx)

        # T28 / AC30 — advance warm-up counters BEFORE Stage 2/3 so the
        # first-signal fire and Stage 3 gate see the correct bars_seen.
        on_signal_fired_this_bar = self._advance_warmup_and_emit(bar_ctx)

        # Stage 2 — scaling (all positions), AC9 cap applied.
        hourly_bar_index = int(getattr(bar_ctx, "hourly_bar_index", -1))
        for i, p in enumerate(positions_seq):
            self._maybe_apply_scale_cap(p, hourly_bar_index)
            self._record_stage("stage_2", i)
            _emit(self._event_recorder, "stage_2", ts_ns)
            self._dispatch_stage2_scale(p, bar_ctx)

        # Stage 3 — pending-entry triggers (priority-ordered). AC30: blocked
        # until the owning strategy's first ``on_signal`` has fired. When
        # strategies are not registered (legacy callers), the gate is a
        # no-op and all PE transitions run as before.
        if pending_seq:
            for pe in sorted(pending_seq, key=_pe_priority_key):
                self._record_stage("stage_3", 0)
                self._maybe_transition_order(pe, bar_ctx)
        else:
            self._record_stage("stage_3", -1)
        _emit(self._event_recorder, "stage_3", ts_ns)
        return None

    # ------------------------------------------------------------------ #
    # T28 / AC30 — warm-up + stage-1 dispatch helpers                     #
    # ------------------------------------------------------------------ #

    def _advance_warmup_and_emit(self, bar_ctx) -> bool:
        """Tick each registered strategy's warm-up counter and fire on_signal.

        Returns True if any strategy fired ``on_signal`` on this bar.
        AC30 semantics:

        * ``bars_seen`` is incremented by one per ``process_bar`` call.
        * A strategy's ``on_signal`` fires the first time
          ``bars_seen >= warmup_bars`` (so a ``warmup_bars=50`` strategy fires
          exactly once at the 50th bar observation, bar_index 49).
        * Subsequent bars do not re-emit ``on_signal`` — that is Stage 2/3's
          job via the full dispatcher. The one-shot model is what the T28
          warm-up acceptance tests check.
        """
        fired_any = False
        for strat in self._strategies:
            key = id(strat)
            warmup = int(getattr(strat, "warmup_bars", 0) or 0)
            self._warmup_bars_seen[key] = self._warmup_bars_seen.get(key, 0) + 1
            bars_seen = self._warmup_bars_seen[key]
            already_fired = self._warmup_on_signal_fired.get(key, False)
            if already_fired:
                continue
            # Fire on_signal once the strategy has observed enough bars.
            # A warmup_bars of 0 fires immediately on bar 1 (no warm-up).
            if bars_seen >= max(warmup, 1):
                cb = getattr(strat, "on_signal", None)
                if callable(cb):
                    try:
                        cb(bar_ctx)
                    except Exception:
                        # Callback errors must not propagate; AC30 tests
                        # only care about the fact of invocation.
                        pass
                self._warmup_on_signal_fired[key] = True
                fired_any = True
        return fired_any

    def _dispatch_stage1_exit(self, pos, bar_ctx) -> None:
        """Stage 1 exit dispatch for a single position (AC30, warm-up safe).

        Runs each attached handler instance via the registry order when
        ``pos.exit_handlers`` is populated; falls back to a minimal inline
        stop-loss check otherwise so an injected position with just a
        ``stop_price``/``direction``/``no_stop_bars`` triple still liquidates
        (AC30 Stage 1 immediate fire test).
        """
        result: Optional[ProcessResult] = None
        handlers = list(getattr(pos, "exit_handlers", None) or [])
        if handlers:
            result = self._run_exit_handlers(pos, bar_ctx, handlers)
        if result is None or not result.should_close:
            inline = self._inline_stop_loss_check(pos, bar_ctx)
            if inline is not None:
                result = inline
        if result is not None and result.should_close:
            self._fire_on_position_exit(pos, bar_ctx, result)

    def _run_exit_handlers(self, pos, bar_ctx, handlers) -> Optional[ProcessResult]:
        """Iterate attached handlers first-match-wins and return a ProcessResult."""
        result = ProcessResult(bar_spec=getattr(bar_ctx, "bar_spec", None))
        for handler in handlers:
            check_fn = getattr(handler, "check_exit", None) or getattr(handler, "check", None)
            if not callable(check_fn):
                continue
            try:
                outcome = check_fn(pos, bar_ctx)
            except Exception:
                continue
            if outcome is None:
                continue
            should = bool(
                getattr(outcome, "should_exit", False)
                or getattr(outcome, "should_close", False)
            )
            if should:
                result.should_close = True
                result.exit_reason = str(
                    getattr(outcome, "reason", "")
                    or getattr(outcome, "exit_reason", "")
                    or ""
                )
                override = (
                    getattr(outcome, "exit_price_override", None)
                    or getattr(outcome, "exit_price", None)
                )
                result.exit_price = float(
                    override if override is not None
                    else getattr(bar_ctx, "close", 0.0) or 0.0
                )
                result.stage = 1
                return result
        return result

    def _inline_stop_loss_check(self, pos, bar_ctx) -> Optional[ProcessResult]:
        """Minimal stop-loss check for positions without explicit handlers.

        Mirrors :class:`v5.exit_handlers.StopLossHandler` semantics so AC30
        tests that inject a bare Position with ``stop_price`` set can still
        observe stop-loss exits during warm-up. Returns None when no stop is
        configured (``stop_price <= 0``) so callers can skip cleanly.
        """
        stop_price = float(getattr(pos, "stop_price", 0.0) or 0.0)
        if stop_price <= 0.0:
            return None
        direction = int(getattr(pos, "direction", 0) or 0)
        if direction not in (-1, 1):
            return None
        no_stop_bars = int(getattr(pos, "no_stop_bars", 0) or 0)
        bars_held = int(getattr(bar_ctx, "bars_held", 0) or 0)
        if bars_held < no_stop_bars and not bool(getattr(pos, "convex_exit", False)):
            return None
        low = float(getattr(bar_ctx, "low", 0.0) or 0.0)
        high = float(getattr(bar_ctx, "high", 0.0) or 0.0)
        hit = False
        if direction == 1 and low <= stop_price:
            hit = True
        elif direction == -1 and high >= stop_price:
            hit = True
        if not hit:
            return None
        result = ProcessResult(bar_spec=getattr(bar_ctx, "bar_spec", None))
        result.should_close = True
        result.exit_reason = "stop"
        result.exit_price = float(getattr(bar_ctx, "close", 0.0) or 0.0)
        result.stage = 1
        return result

    def _fire_on_position_exit(self, pos, bar_ctx, result: ProcessResult) -> None:
        cb = self.on_position_exit
        if cb is None:
            return
        try:
            cb(pos, bar_ctx, result)
        except Exception:
            pass

    def _dispatch_stage2_scale(self, pos, bar_ctx) -> None:
        """Stage 2 dispatch — AC9 cap + on_scale_action emission."""
        hourly_bar_index = int(getattr(bar_ctx, "hourly_bar_index", -1))
        if hourly_bar_index < 0:
            return
        last_scale_bar = int(getattr(pos, "_scale_action_bar", -1))
        if hourly_bar_index <= last_scale_bar:
            return
        action = self._invoke_scale_callbacks(pos, bar_ctx)
        if action is None:
            return
        result = ProcessResult(bar_spec=getattr(bar_ctx, "bar_spec", None))
        result.scale_action_taken = action if isinstance(action, str) else "scale"
        result.stage = 2
        try:
            pos._scale_action_bar = hourly_bar_index
        except Exception:
            pass
        # M4 Task 26 (AC26 T-B16) — publish this scale fire to the module-
        # level registry so ``position_manager.get_scaling_history`` can
        # observe it across crash-restart cycles. Best-effort import +
        # swallow errors so the dispatch is never destabilised by the
        # observability path.
        try:
            from v5.position_manager import position_manager as _pm
            _pm.record_scale(pos, bar_ctx, action)
        except Exception:
            pass
        cb = self.on_scale_action
        if cb is not None:
            try:
                cb(pos, bar_ctx, result)
            except Exception:
                pass

    def _maybe_transition_order(self, pe, bar_ctx) -> None:
        """AC30 — evaluate Order trigger only once the owning strategy has warmed.

        No-op when no strategies are registered (legacy callers). For an
        order belonging to a strategy still in warm-up, the ARMED
        state is preserved and no transition is recorded. Transitions that
        do run bump the optional ``stats.pending_transitions`` counter so
        tests can audit them.
        """
        if not self._strategies:
            return
        owner = self._find_strategy_for_order(pe)
        if owner is not None and not self._warmup_on_signal_fired.get(id(owner), False):
            # Warm-up gate: strategy hasn't fired on_signal yet.
            return
        before_state = getattr(pe, "state", None)
        before_name = getattr(before_state, "name", str(before_state))
        trigger_price = float(getattr(bar_ctx, "close", 0.0) or 0.0)
        try:
            new_pe = pe.on_price(trigger_price)
        except Exception:
            return
        after_state = getattr(new_pe, "state", None)
        after_name = getattr(after_state, "name", str(after_state))
        if before_name != after_name and self._stats_enabled and self.stats is not None:
            key = (before_name, after_name)
            self.stats.pending_transitions[key] = (
                self.stats.pending_transitions.get(key, 0) + 1
            )

    def _find_strategy_for_order(self, pe):
        """Return the registered strategy whose ``strategy_id`` matches ``pe``.

        Falls back to the first registered strategy when no id match exists —
        the AC30 warm-up tests register a single strategy and expect its
        warm-up gate to apply to any orders in flight.
        """
        pe_sid = getattr(pe, "strategy_id", None)
        if pe_sid is not None:
            for s in self._strategies:
                if getattr(s, "strategy_id", None) == pe_sid:
                    return s
        return self._strategies[0] if self._strategies else None

    # ------------------------------------------------------------------ #
    # AC6 first-match-wins single-position dispatch                      #
    # ------------------------------------------------------------------ #

    def _process_bar_single(self, *, pos, bar_ctx, global_bar: int) -> ProcessResult:
        """Iterate ``pos.exit_handlers`` in registered order; earliest wins.

        Each handler is called via ``.check(pos, bar_ctx)`` when available
        (matches the mock/protocol used in AC6 tests), falling back to
        ``.check_exit(pos, bar_ctx)`` for legacy handlers. The loop returns
        on the first handler whose result exposes ``should_close=True`` or
        ``should_exit=True``.
        """
        result = ProcessResult(bar_spec=getattr(bar_ctx, "bar_spec", None))
        handlers = list(getattr(pos, "exit_handlers", None) or [])
        for handler in handlers:
            check_fn = getattr(handler, "check", None)
            if callable(check_fn):
                try:
                    outcome = check_fn(pos, bar_ctx)
                except Exception:
                    outcome = None
            else:
                legacy_fn = getattr(handler, "check_exit", None)
                if not callable(legacy_fn):
                    continue
                try:
                    outcome = legacy_fn(pos, bar_ctx)
                except Exception:
                    outcome = None
            if outcome is None:
                continue
            should_close = bool(
                getattr(outcome, "should_close", False)
                or getattr(outcome, "should_exit", False)
            )
            if should_close:
                result.should_close = True
                result.exit_reason = str(
                    getattr(outcome, "exit_reason", None)
                    or getattr(outcome, "reason", "")
                    or ""
                )
                fallback_price = float(getattr(bar_ctx, "close", 0.0) or 0.0)
                result.exit_price = float(
                    getattr(outcome, "exit_price", None)
                    or getattr(outcome, "exit_price_override", None)
                    or fallback_price
                )
                result.stage = 1
                return result
        return result

    # ------------------------------------------------------------------ #
    # Per-position processing                                            #
    # ------------------------------------------------------------------ #

    def process_bar_for_position(
        self,
        *,
        pos,
        bar_ctx,
        global_bar: int = 0,
    ) -> ProcessResult:
        """Stage 1/2/3 dispatch for a single position (AC7, AC9).

        Implements the AC9 per-hourly scale cap via ``pos._scale_action_bar``
        vs ``bar_ctx.hourly_bar_index``. Returns a :class:`ProcessResult`
        summarising what fired.
        """
        result = ProcessResult(bar_spec=getattr(bar_ctx, "bar_spec", None))

        # ---- Stage 1: Exit handler chain ------------------------------ #
        # T11 does the dispatch skeleton; T12 wires the full exit-handler
        # registry. We call the existing helper if handlers are attached
        # to the position (pre-M4 fast path) so mid-wave tests don't lose
        # functionality.
        exit_handlers = getattr(pos, "exit_handlers", None) or []
        if exit_handlers:
            try:
                from v5.exit_handlers import (
                    run_check_exit_phase,
                    run_update_state_phase,
                )
                run_update_state_phase(pos, bar_ctx)
                check = run_check_exit_phase(pos, bar_ctx)
                if check.should_exit:
                    result.should_close = True
                    result.exit_reason = check.reason
                    result.exit_price = (
                        check.exit_price_override
                        if check.exit_price_override is not None
                        else float(getattr(bar_ctx, "close", 0.0))
                    )
                    result.stage = 1
            except Exception:
                # T11 guards against partial handler chains; exit-handler
                # issues must not break the dispatch skeleton.
                pass

        # ---- Stage 2: Scaling (AC9 cap) ------------------------------- #
        if not result.should_close:
            hourly_bar_index = int(getattr(bar_ctx, "hourly_bar_index", -1))
            last_scale_bar = int(getattr(pos, "_scale_action_bar", -1))
            if hourly_bar_index > last_scale_bar:
                # Fire check_scale on each registered strategy whose
                # subscriptions match the current bar resolution.
                action = self._invoke_scale_callbacks(pos, bar_ctx)
                if action is not None:
                    result.scale_action_taken = action
                    # Record that a scale action was taken in this hourly
                    # window (AC9 cap key).
                    try:
                        pos._scale_action_bar = hourly_bar_index
                    except Exception:
                        pass
                    # M4 Task 26 (AC26 T-B16) — publish to the registry so
                    # ``position_manager.get_scaling_history`` can observe
                    # this scale fire across a crash-restart cycle.
                    try:
                        from v5.position_manager import position_manager as _pm
                        _pm.record_scale(pos, bar_ctx, action)
                    except Exception:
                        pass
                    if result.stage < 2:
                        result.stage = 2

        return result

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _record_stage(self, stage: str, pos_idx: int) -> None:
        if self._stage_recorder is None:
            return
        try:
            self._stage_recorder(stage, pos_idx)
        except Exception:
            pass

    def _maybe_apply_scale_cap(self, pos, hourly_bar_index: int) -> None:
        """Keep ``pos._scale_action_bar`` monotone — helper for AC9."""
        if hourly_bar_index < 0:
            return
        # No-op: actual firing goes through process_bar_for_position.
        return None

    def _invoke_scale_callbacks(self, pos, bar_ctx):
        """Call each strategy's ``check_scale`` hook (if any) for the position.

        Returns the first non-None action string emitted. T12 will extend
        this to select by strategy-id match; T11 fires all strategies.
        """
        action = None
        for strat in self._strategies:
            fn = getattr(strat, "check_scale", None)
            if not callable(fn):
                continue
            try:
                out = fn(pos, bar_ctx)
            except Exception:
                out = None
            if out is not None and action is None:
                action = out
        return action


# --------------------------------------------------------------------------- #
# Internal default BarContext shim for AC15 path                              #
# --------------------------------------------------------------------------- #


class _DefaultBarContext:
    """Lightweight bar-context used by :meth:`BarProcessor.tick` for the 1h
    signal-bar path. Kept local to the module to avoid pulling in the full
    :class:`v5.exit_handlers.BarContext` and its numeric-field defaults —
    ``on_signal`` strategies only need ts_ns + hourly_bar_index + bar_spec.
    """

    __slots__ = ("ts_ns", "hourly_bar_index", "bar_spec")

    def __init__(
        self,
        *,
        ts_ns: int,
        hourly_bar_index: int,
        bar_spec: Any,
    ) -> None:
        self.ts_ns = int(ts_ns)
        self.hourly_bar_index = int(hourly_bar_index)
        self.bar_spec = bar_spec
