"""V5 Portfolio Backtest — Pluggable exit handler chain.

Extracts strategy-specific exit logic from _process_exits() into individually
testable handler classes. The engine remains strategy-agnostic: new exit types
never require simulator changes.

Handler chain lifecycle:
  1. Built once at position entry via build_exit_chain(pos, sig, spec)
  2. Stored on Position.exit_handlers (not serialized to ClosedTrade)
  3. Per-bar: update_state() runs all handlers, then check_exit() runs until first match
  4. Order matches the original _process_exits() if/elif priority
"""
from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

import numpy as np

from .position import Position
from .signals import TokenBarArrays


# Empty immutable indicator snapshot — default for BarContext when no MTF
# indicators are subscribed. Using MappingProxyType guarantees the default is
# not mutable and is shared safely across instances.
_EMPTY_INDICATOR_SNAPSHOT: Mapping[str, float] = types.MappingProxyType({})


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BarContext:
    """Immutable per-bar market data passed to exit handlers.

    M4 additions (all optional, backward-compat for pre-M4 callers):
      - hourly_bar_index (AC7): exposed for the AC9 scale cap regardless of
        fine-bar cadence. When execution_resolution == 1h this equals local_bar.
      - bar_spec (AC13): which resolution drove this call. None for legacy
        hourly-only callers.
      - indicator_snapshot (AC13): resolution-appropriate indicator dict.
        Canonical key 'atr_hourly' carries hourly-frozen ATR per AC40.
      - ts_ns (AC15): sim clock timestamp; used by BarProcessor look-ahead
        assertion at callback emission time.
    """
    close: float
    high: float
    low: float
    atr: float
    rsi: float           # NaN if unavailable
    regime: int           # 0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND
    bars_held: int
    local_bar: int
    funding_val: float    # hourly funding rate (0 for spot)
    # Extended fields for custom exit handlers (NaN if unavailable)
    volume: float = float('nan')
    vol_20: float = float('nan')     # 20-period rolling volatility
    ret_1h: float = float('nan')     # 1-hour log return
    # M4 additions — optional, default-valued (AC7/AC13/AC15)
    hourly_bar_index: int = 0
    bar_spec: Optional[Any] = None   # typed as BarSpec; Any to avoid import cycle
    indicator_snapshot: Mapping[str, float] = field(
        default_factory=lambda: _EMPTY_INDICATOR_SNAPSHOT
    )
    ts_ns: int = 0


@dataclass
class ExitCheck:
    """Result of an exit handler check."""
    should_exit: bool
    reason: str = ""
    exit_price_override: Optional[float] = None  # None = use bar close


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ExitHandler(Protocol):
    """Interface every exit handler must satisfy."""

    def update_state(self, pos: Position, bar: BarContext) -> None:
        """Mutate position state (trailing stop, breakeven ratchet, etc.)."""
        ...

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        """Return ExitCheck indicating whether the position should close."""
        ...


_NO_EXIT = ExitCheck(should_exit=False)


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

class BreakevenRatchetHandler:
    """Move stop to entry price once profit reaches breakeven_atr * ATR."""

    def update_state(self, pos: Position, bar: BarContext) -> None:
        if pos.breakeven_atr <= 0.0 or pos.breakeven_triggered:
            return
        atr = max(bar.atr, 1e-10)
        if pos.direction == 1:
            be_profit_atr = (pos.highest - pos.entry_price) / atr
        else:
            be_profit_atr = (pos.entry_price - pos.lowest) / atr
        if be_profit_atr >= pos.breakeven_atr:
            if pos.direction == 1:
                pos.stop_price = max(pos.stop_price, pos.entry_price)
            else:
                pos.stop_price = min(pos.stop_price, pos.entry_price)
            pos.breakeven_triggered = True

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        return _NO_EXIT


class BreakevenHandler:
    """M4 AC8 — breakeven handler that reads the ORIGINAL entry price.

    Unlike :class:`BreakevenRatchetHandler` (which mutates stop on a moving
    high/low ratchet), this handler exposes an ``entry_anchor_price`` that
    is pinned to ``pos._breakeven_anchor_entry_price`` — the frozen
    pre-scale entry. After :meth:`Position.increase`, ``entry_price``
    becomes the VWAP but the anchor stays at the original value, so the
    arm threshold is not artificially lowered (longs) or raised (shorts)
    by averaging.

    ``check(pos, bar_ctx)`` returns True iff the bar close has crossed
    ``anchor + direction * breakeven_atr * initial_risk``. The handler
    consumes ``pos.initial_risk`` (frozen at entry) rather than
    ``bar.atr`` so it tolerates minimal :class:`v5.bar_processor.BarContext`
    instances that omit the full exit-handler numeric surface.
    """

    __slots__ = ("entry_anchor_price",)

    def __init__(self, pos: Position) -> None:
        self.entry_anchor_price = float(pos._breakeven_anchor_entry_price)

    def check(self, pos: Position, bar_ctx: Any) -> bool:
        """Return True iff the price has crossed the breakeven arm threshold."""
        if pos.breakeven_atr <= 0.0 or pos.initial_risk <= 0.0:
            return False
        gate_dist = pos.breakeven_atr * pos.initial_risk
        close = float(getattr(bar_ctx, "close", 0.0))
        if pos.direction == 1:
            return close >= self.entry_anchor_price + gate_dist
        return close <= self.entry_anchor_price - gate_dist


def breakeven_handler_factory(pos: Position) -> "BreakevenHandler":
    """M4 AC8 T-B4b — build a breakeven handler bound to ``pos``.

    The returned handler's ``entry_anchor_price`` is the frozen pre-scale
    entry. ``check(pos, bar_ctx)`` does NOT read post-scale VWAP — this
    preserves AC8: stop-to-breakeven arms against the OLD entry price
    regardless of intervening :meth:`Position.increase` VWAP adjustments.
    """
    return BreakevenHandler(pos)


def r_multiple_gate_price(pos: Position, r_multiple: float) -> float:
    """M4 AC10 T-B4 — R-multiple gate price measured off ``r_anchor_price``.

    Stage-3 gates (e.g. partial-TP at 2R) MUST consume
    ``pos.r_anchor_price`` (frozen at first entry by :meth:`Position.increase`
    with ``freeze_initial_risk=True``, the default) rather than the
    post-scale VWAP ``entry_price``. Otherwise a scale-down would lower
    the 2R gate for longs (raise it for shorts) and fire premature
    take-profits.

    Formula: ``r_anchor_price + direction * r_multiple * initial_risk``.
    """
    return (
        float(pos.r_anchor_price)
        + float(pos.direction) * float(r_multiple) * float(pos.initial_risk)
    )


class ConvexTrailingHandler:
    """Trailing stop for convex-exit strategies (mutually exclusive with TrailingStopHandler)."""

    def update_state(self, pos: Position, bar: BarContext) -> None:
        if not pos.convex_exit:
            return
        mature_bars, early_bars = pos.convex_bar_thresholds
        mature_trail_atr, early_profit_mult, early_be_offset = pos.convex_multipliers
        atr = max(bar.atr, 1e-10)
        if bar.bars_held >= mature_bars and pos.direction == 1:
            trail = pos.highest - mature_trail_atr * atr
            pos.stop_price = max(pos.stop_price, trail)
        elif bar.bars_held >= early_bars and pos.direction == 1:
            if pos.highest > pos.entry_price + early_profit_mult * pos.initial_risk:
                be_trail = pos.entry_price + early_be_offset * pos.initial_risk
                pos.stop_price = max(pos.stop_price, be_trail)

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        return _NO_EXIT


class TrailingStopHandler:
    """Standard trailing stop with profit schedule, time schedule, max ceiling, and chandelier.

    Two calling conventions:

      1. Engine integration (pre-M4): ``TrailingStopHandler(sig)`` where
         ``sig`` is a :class:`TokenBarArrays`. Reads trail parameters from the
         :class:`Position` (frozen at entry) and consumes per-bar indicators
         off ``BarContext``.

      2. AC40 T13a resolution-agnostic trail_schedule path:
         ``TrailingStopHandler(trail_schedule=[(R_hit, R_offset), ...])``.
         Evaluates on every bar of ``exit_resolution`` (fine-bar cadence when
         exit_resolution<1h). Requires only ``pos.entry_price``,
         ``pos.initial_risk``, ``pos.direction``, ``pos.stop_price`` and
         ``bar_ctx.close`` — NO ATR dependency. Stop only tightens
         (monotonicity preserved).
    """

    def __init__(
        self,
        sig: Optional[TokenBarArrays] = None,
        *,
        trail_schedule: Optional[list[tuple[float, float]]] = None,
    ):
        self._sig = sig
        # AC40 T13a: handler-level trail_schedule overrides any per-position
        # schedule and drives the resolution-agnostic profit-milestone
        # tightening. Stored as a tuple of (profit_R, stop_R_offset) pairs.
        self._handler_trail_schedule: Optional[tuple[tuple[float, float], ...]] = None
        if trail_schedule is not None:
            self._handler_trail_schedule = tuple(
                (float(r_hit), float(r_offset)) for r_hit, r_offset in trail_schedule
            )

    # ------------------------------------------------------------------
    # AC40 T13a helper — resolution-agnostic milestone tightening.
    # ------------------------------------------------------------------
    def _update_state_schedule(self, pos: Position, bar: BarContext) -> None:
        """Profit-milestone tightening that fires on every exit_resolution bar.

        NO ATR dependency. Reads only close / entry_price / initial_risk.
        Preserves monotonicity: stop only tightens.
        """
        if self._handler_trail_schedule is None:
            return
        if pos.initial_risk <= 0.0:
            return

        # profit_R measured in units of initial_risk (signed by direction).
        if pos.direction == 1:
            profit_r = (bar.close - pos.entry_price) / pos.initial_risk
        else:
            profit_r = (pos.entry_price - bar.close) / pos.initial_risk

        # Walk schedule and find the furthest milestone already crossed.
        # Schedule is assumed to be sorted ascending by profit_R.
        hit_offset: Optional[float] = None
        for r_hit, r_offset in self._handler_trail_schedule:
            if profit_r >= r_hit:
                hit_offset = r_offset
            else:
                break

        if hit_offset is None:
            return  # no milestone crossed on this bar — leave stop untouched

        if pos.direction == 1:
            candidate = pos.entry_price + hit_offset * pos.initial_risk
            # Monotonicity: long stop only moves up.
            if candidate > pos.stop_price:
                pos.stop_price = candidate
        else:
            candidate = pos.entry_price - hit_offset * pos.initial_risk
            # Monotonicity: short stop only moves down.
            if candidate < pos.stop_price or pos.stop_price == 0.0:
                pos.stop_price = candidate

    def update_state(self, pos: Position, bar: BarContext) -> None:
        # AC40 T13a: when a handler-level trail_schedule is set, drive the
        # resolution-agnostic profit-milestone path and return. This branch
        # intentionally skips all ATR/sig-dependent logic — the fine-bar
        # BC carries only close/high/low/indicator_snapshot.
        if self._handler_trail_schedule is not None:
            self._update_state_schedule(pos, bar)
            return

        if pos.convex_exit:
            return  # handled by ConvexTrailingHandler
        if bar.bars_held < pos.no_stop_bars:
            return

        atr = max(bar.atr, 1e-10)

        # Profit-based trail schedule
        if pos.trail_schedule is not None:
            profit_atr = abs(bar.close - pos.entry_price) / atr
            eff_tm = pos.trail_mult
            for si in range(pos.trail_schedule.shape[0]):
                if profit_atr >= pos.trail_schedule[si, 0]:
                    eff_tm = pos.trail_schedule[si, 1]
                else:
                    break
        else:
            eff_tm = pos.trail_mult

        # Time-based trail tightening
        if pos.time_trail_schedule is not None:
            time_tm = pos.trail_mult
            for si in range(pos.time_trail_schedule.shape[0]):
                if bar.bars_held >= pos.time_trail_schedule[si, 0]:
                    time_tm = pos.time_trail_schedule[si, 1]
                else:
                    break
            if time_tm < eff_tm:
                eff_tm = time_tm

        # Per-bar ceiling (defensive stop overlay)
        if pos.max_trail_mult_arr is not None and bar.local_bar < len(pos.max_trail_mult_arr):
            if pos.max_trail_mult_arr[bar.local_bar] < eff_tm:
                eff_tm = pos.max_trail_mult_arr[bar.local_bar]

        # Chandelier: use lookback-window high/low instead of all-time
        if pos.chandelier_lookback > 0 and bar.bars_held > pos.chandelier_lookback:
            lb = pos.chandelier_lookback
            start = max(0, bar.local_bar - lb)
            sig = self._sig
            want_perp = pos.is_perp if sig.per_bar_is_perp is not None else (pos.leg == "secondary")
            if want_perp and sig.perp_high is not None:
                h_arr, l_arr = sig.perp_high, sig.perp_low
            else:
                h_arr, l_arr = sig.high, sig.low
            ref_high = float(np.max(h_arr[start:bar.local_bar + 1]))
            ref_low = float(np.min(l_arr[start:bar.local_bar + 1]))
        else:
            ref_high = pos.highest
            ref_low = pos.lowest

        if pos.direction == 1:
            trail = ref_high - eff_tm * atr
            pos.stop_price = max(pos.stop_price, trail)
        else:
            trail = ref_low + eff_tm * atr
            pos.stop_price = min(pos.stop_price, trail)

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        return _NO_EXIT


class ChandelierStopHandler:
    """AC40 T13a — chandelier stop with explicit ATR-source cascade.

    The chandelier trails a stop N * ATR below (long) / above (short) the
    rolling max/min of the exit_resolution series. ATR source is declared
    on the :class:`v5.strategy_spec.StrategySpec`:

      - ``chandelier_atr_source='fine'`` → reads
        ``bar_ctx.indicator_snapshot['atr_fine']``. Requires
        ``declared_fine_atr=True`` on the spec (enforced at spec construction).
      - ``chandelier_atr_source='hourly'`` → reads
        ``bar_ctx.indicator_snapshot['atr_hourly']`` (the hourly-frozen ATR
        AC40 canonical key).

    The rolling max/min is supplied by the fine-bar :class:`RollingCache`
    via ``bar_ctx.rolling_max`` / ``bar_ctx.rolling_min``. The ATR-multiplier
    ``k`` is fixed at 3.0 to match the upstream chandelier contract used in
    the T-B32b expected-value assertions.
    """

    # ATR multiplier for chandelier formula. Held as a class-level constant
    # so both long and short stops pick the same k across tests.
    K: float = 3.0

    def __init__(self, spec):
        self._spec = spec
        source = getattr(spec, "chandelier_atr_source", None)
        if source not in ("fine", "hourly"):
            raise ValueError(
                "ChandelierStopHandler requires spec.chandelier_atr_source "
                "to be 'fine' or 'hourly'."
            )
        self._atr_source: str = source

    def _resolve_atr(self, bar) -> float:
        """Read the declared ATR source off ``bar_ctx.indicator_snapshot``."""
        snapshot = getattr(bar, "indicator_snapshot", None) or {}
        if self._atr_source == "fine":
            atr = snapshot.get("atr_fine")
        else:
            atr = snapshot.get("atr_hourly")
        if atr is None:
            raise KeyError(
                f"indicator_snapshot missing "
                f"{'atr_fine' if self._atr_source == 'fine' else 'atr_hourly'}"
            )
        return float(atr)

    def compute_stop(self, pos: Position, bar) -> float:
        """Compute the chandelier stop level for this bar.

        Does NOT mutate ``pos`` — returns the candidate stop so the caller
        (or ``update_state``) can apply monotonicity.
        """
        atr = self._resolve_atr(bar)
        if pos.direction == 1:
            ref = float(getattr(bar, "rolling_max"))
            return ref - self.K * atr
        else:
            ref = float(getattr(bar, "rolling_min"))
            return ref + self.K * atr

    def update_state(self, pos: Position, bar) -> None:
        """Tighten stop toward chandelier level; monotonicity preserved."""
        candidate = self.compute_stop(pos, bar)
        if pos.direction == 1:
            if candidate > pos.stop_price:
                pos.stop_price = candidate
        else:
            if candidate < pos.stop_price or pos.stop_price == 0.0:
                pos.stop_price = candidate

    def check_exit(self, pos: Position, bar) -> ExitCheck:
        return _NO_EXIT


class CircuitBreakerHandler:
    """Emergency exit regardless of no_stop_bars when loss exceeds Nx initial risk."""

    def __init__(self, circuit_breaker_r: float):
        self._cb_r = circuit_breaker_r

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if self._cb_r <= 0 or pos.initial_risk <= 0:
            return _NO_EXIT
        cb_dist = self._cb_r * pos.initial_risk
        d = pos.direction
        if d == 1 and bar.low <= pos.entry_price - cb_dist:
            return ExitCheck(should_exit=True, reason="circuit_breaker")
        elif d == -1 and bar.high >= pos.entry_price + cb_dist:
            return ExitCheck(should_exit=True, reason="circuit_breaker")
        return _NO_EXIT


class StopLossHandler:
    """Intra-bar stop-loss check (low/high triggers, fill at bar close)."""

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if pos.stop_price <= 0:
            return _NO_EXIT  # no stop set (stop_mult >= 999)
        stop_active = bar.bars_held >= pos.no_stop_bars or pos.convex_exit
        if not stop_active:
            return _NO_EXIT
        d = pos.direction
        if d == 1 and bar.low <= pos.stop_price:
            return ExitCheck(should_exit=True, reason="stop")
        elif d == -1 and bar.high >= pos.stop_price:
            return ExitCheck(should_exit=True, reason="stop")
        return _NO_EXIT


class TakeProfitHandler:
    """Take-profit check, regime-conditional (tighter target in bear)."""

    def __init__(self, sig: TokenBarArrays):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        eff_target = pos.target_mult
        if self._sig.bear_target_mult > 0.0:
            if bar.regime == 4:  # DOWNTREND
                eff_target = self._sig.bear_target_mult

        d = pos.direction
        atr = max(bar.atr, 1e-10)
        if pos.convex_exit:
            if d == 1 and bar.close > pos.entry_price + eff_target * pos.initial_risk:
                return ExitCheck(should_exit=True, reason="target")
            elif d == -1 and bar.close < pos.entry_price - eff_target * pos.initial_risk:
                return ExitCheck(should_exit=True, reason="target")
        else:
            if d == 1 and bar.high >= pos.entry_price + eff_target * atr:
                return ExitCheck(should_exit=True, reason="target")
            elif d == -1 and bar.low <= pos.entry_price - eff_target * atr:
                return ExitCheck(should_exit=True, reason="target")
        return _NO_EXIT


# RegimeExitHandler removed — strategies use exit_check_fn with bar.regime instead.
# See strategies/s538_strategy_crisis.py for migration pattern.


class RSIExitHandler:
    """RSI-based exit (symmetric: longs exit on high RSI, shorts on low)."""

    def __init__(self, sig: TokenBarArrays):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if pos.rsi_exit_level >= 999.0 or self._sig.rsi is None:
            return _NO_EXIT
        rsi_val = self._sig.rsi[bar.local_bar]
        if bar.bars_held < pos.min_hold:
            return _NO_EXIT
        d = pos.direction
        if d == 1 and rsi_val > pos.rsi_exit_level:
            return ExitCheck(should_exit=True, reason="rsi")
        elif d == -1 and rsi_val < (100.0 - pos.rsi_exit_level):
            return ExitCheck(should_exit=True, reason="rsi")
        return _NO_EXIT


class MeanTargetHandler:
    """Mean-target exit for convex strategies only."""

    def __init__(self, sig: TokenBarArrays):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if not pos.convex_exit or self._sig.mean_target_vals is None:
            return _NO_EXIT
        mt = self._sig.mean_target_vals[bar.local_bar]
        if np.isnan(mt):
            return _NO_EXIT
        d = pos.direction
        if d == 1 and bar.close >= mt and bar.bars_held >= pos.min_hold:
            if bar.close < pos.entry_price + 2.0 * pos.initial_risk:
                return ExitCheck(should_exit=True, reason="mean_target")
        return _NO_EXIT


class SMATrailExitHandler:
    """Exit when price crosses below (longs) or above (shorts) precomputed SMA level.

    The strategy precomputes SMA values (e.g., SMA(15) of close) and passes them
    via StrategyResult.sma_trail_vals.  This handler checks each bar after the
    no_stop_bars protection window.
    """

    def __init__(self, sig: TokenBarArrays):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass  # no state mutation

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if self._sig.sma_trail_vals is None:
            return _NO_EXIT
        if bar.bars_held < pos.no_stop_bars:
            return _NO_EXIT
        if bar.local_bar >= len(self._sig.sma_trail_vals):
            return _NO_EXIT
        sma_val = float(self._sig.sma_trail_vals[bar.local_bar])
        if np.isnan(sma_val) or sma_val <= 0.0:
            return _NO_EXIT
        if pos.direction == 1 and bar.close < sma_val:
            return ExitCheck(should_exit=True, reason="sma_trail")
        elif pos.direction == -1 and bar.close > sma_val:
            return ExitCheck(should_exit=True, reason="sma_trail")
        return _NO_EXIT


class MaxHoldHandler:
    """Max hold exit — forces exit after N bars held."""

    def __init__(self, sig: TokenBarArrays):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if bar.bars_held >= pos.max_hold:
            return ExitCheck(should_exit=True, reason="max_hold")
        return _NO_EXIT


class FundingCeilingHandler:
    """Exit perp positions when cumulative funding drag exceeds threshold."""

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if pos.funding_exit_threshold <= 0.0 or not pos.is_perp:
            return _NO_EXIT
        if pos.margin_usd > 0.0 and pos.cumulative_funding / pos.margin_usd > pos.funding_exit_threshold:
            return ExitCheck(should_exit=True, reason="funding")
        return _NO_EXIT


class CustomExitHandler:
    """Strategy-defined exit callback. Runs before built-in exit checks."""

    def __init__(self, exit_fn):
        self._exit_fn = exit_fn

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        try:
            result = self._exit_fn(pos, bar)
            if result is not None and getattr(result, 'should_exit', False):
                return result
        except Exception:
            pass  # callback error, skip
        return _NO_EXIT


# ---------------------------------------------------------------------------
# AC6 — canonical exit handler registry (first-match-wins order)
# ---------------------------------------------------------------------------
#
# The nine-element tuple below is the single source of truth for the Stage 1
# exit-handler chain ordering consumed by :class:`v5.bar_processor.BarProcessor`.
# Reordering this tuple changes production behaviour; treat it as a spec file.
EXIT_HANDLER_REGISTRY = (
    CustomExitHandler,
    CircuitBreakerHandler,
    StopLossHandler,
    TakeProfitHandler,
    RSIExitHandler,
    MeanTargetHandler,
    SMATrailExitHandler,
    MaxHoldHandler,
    FundingCeilingHandler,
)


# ---------------------------------------------------------------------------
# Factory: build the handler chain for a position
# ---------------------------------------------------------------------------

def build_exit_chain(
    pos: Position,
    sig: TokenBarArrays,
    spec,  # StrategySpec — avoid import cycle by duck typing
    state=None,
    config=None,
) -> list:
    """Build ordered exit handler chain for a newly opened position.

    Order matches the original _process_exits() if/elif priority:
      0. Circuit breaker (before stop_active gate)
      1. Stop-loss
      2. Take-profit (regime-conditional)
      3. Regime exit
      4. RSI exit
      5. Mean-target exit
      6. Max hold
      7. Funding ceiling

    State-mutating handlers (breakeven, trailing) run update_state() before checks.
    """
    handlers: list = []

    # --- State-mutating handlers (run update_state only) ---
    handlers.append(BreakevenRatchetHandler())

    if pos.convex_exit:
        handlers.append(ConvexTrailingHandler())
    else:
        handlers.append(TrailingStopHandler(sig))

    # --- Custom exit check (strategy-defined, runs before built-in checks) ---
    exit_fn = getattr(spec, 'exit_check_fn', None) if spec else None
    if exit_fn is not None:
        handlers.append(CustomExitHandler(exit_fn))

    # --- Exit check handlers (order = priority) ---
    cb_r = spec.circuit_breaker_r if spec else 0.0
    if cb_r > 0:
        handlers.append(CircuitBreakerHandler(cb_r))

    handlers.append(StopLossHandler())
    handlers.append(TakeProfitHandler(sig))
    # RegimeExitHandler removed — strategies use exit_check_fn with bar.regime instead

    if pos.rsi_exit_level < 999.0 and sig.rsi is not None:
        handlers.append(RSIExitHandler(sig))

    if pos.convex_exit and sig.mean_target_vals is not None:
        handlers.append(MeanTargetHandler(sig))

    if sig.sma_trail_vals is not None:
        handlers.append(SMATrailExitHandler(sig))

    handlers.append(MaxHoldHandler(sig))

    if pos.funding_exit_threshold > 0.0 and pos.is_perp:
        handlers.append(FundingCeilingHandler())

    return handlers


def run_update_state_phase(pos: Position, bar: BarContext) -> None:
    """Run Phase 1 (handler.update_state) on all handlers."""
    for h in pos.exit_handlers:
        h.update_state(pos, bar)


def run_check_exit_phase(pos: Position, bar: BarContext) -> ExitCheck:
    """Run Phase 3 (handler.check_exit) on all handlers; first should_exit wins."""
    for h in pos.exit_handlers:
        result = h.check_exit(pos, bar)
        if result.should_exit:
            return result
    return _NO_EXIT


def run_exit_handlers(
    pos: Position,
    bar: BarContext,
    global_bar: int,
    adv_val: float,
) -> ExitCheck:
    """Back-compat wrapper: runs Phase 1 then Phase 3 (no scale hook)."""
    run_update_state_phase(pos, bar)
    return run_check_exit_phase(pos, bar)
