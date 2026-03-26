"""V4 Portfolio Backtest — Pluggable exit handler chain.

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

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import numpy as np

from .position import Position
from .signals import TokenSignals


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BarContext:
    """Immutable per-bar market data passed to exit handlers."""
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

    Reads all trail parameters from the Position (frozen at entry).
    """

    def __init__(self, sig: TokenSignals):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
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


class PartialTPHandler:
    """Partial profit-taking: close a fraction when profit exceeds threshold."""

    def __init__(self, state, config):
        self._state = state
        self._config = config

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass  # no state mutation needed

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        # Partial TP is handled as a side-effect (not a full exit)
        return _NO_EXIT

    def process_partial(self, pos: Position, bar: BarContext, global_bar: int, adv_val: float) -> bool:
        """Check and execute partial profit-taking. Returns True if partial close happened."""
        if pos.partial_tp_atr <= 0.0 or pos.partial_closed:
            return False
        if bar.bars_held < pos.no_stop_bars:
            return False

        atr = max(bar.atr, 1e-10)
        if pos.direction == 1:
            profit_atr = (bar.high - pos.entry_price) / atr
        else:
            profit_atr = (pos.entry_price - bar.low) / atr

        if profit_atr >= pos.partial_tp_atr:
            # Import here to avoid circular dependency
            from .simulator import _partial_close_position
            _partial_close_position(self._state, pos, global_bar, bar.close, adv_val, self._config)
            return True
        return False


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

    def __init__(self, sig: TokenSignals):
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


class RegimeExitHandler:
    """Exit when regime enters exit_regimes set."""

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        if bar.regime in pos.exit_regimes and bar.bars_held > pos.regime_exit_min_bars:
            return ExitCheck(should_exit=True, reason="regime")
        return _NO_EXIT


class RSIExitHandler:
    """RSI-based exit (symmetric: longs exit on high RSI, shorts on low)."""

    def __init__(self, sig: TokenSignals):
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

    def __init__(self, sig: TokenSignals):
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


class MaxHoldHandler:
    """Max hold exit, regime-conditional (shorter hold in DOWNTREND)."""

    def __init__(self, sig: TokenSignals):
        self._sig = sig

    def update_state(self, pos: Position, bar: BarContext) -> None:
        pass

    def check_exit(self, pos: Position, bar: BarContext) -> ExitCheck:
        eff_max_hold = pos.max_hold
        if self._sig.bear_max_hold > 0:
            if bar.regime == 4:  # DOWNTREND
                eff_max_hold = self._sig.bear_max_hold
        if bar.bars_held >= eff_max_hold:
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


# ---------------------------------------------------------------------------
# Factory: build the handler chain for a position
# ---------------------------------------------------------------------------

def build_exit_chain(
    pos: Position,
    sig: TokenSignals,
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
    PartialTPHandler runs as a side-effect between update_state and check_exit.
    """
    handlers: list = []

    # --- State-mutating handlers (run update_state only) ---
    handlers.append(BreakevenRatchetHandler())

    if pos.convex_exit:
        handlers.append(ConvexTrailingHandler())
    else:
        handlers.append(TrailingStopHandler(sig))

    # --- Partial TP (side-effect handler) ---
    if pos.partial_tp_atr > 0.0 and state is not None and config is not None:
        handlers.append(PartialTPHandler(state, config))

    # --- Exit check handlers (order = priority) ---
    cb_r = spec.circuit_breaker_r if spec else 0.0
    if cb_r > 0:
        handlers.append(CircuitBreakerHandler(cb_r))

    handlers.append(StopLossHandler())
    handlers.append(TakeProfitHandler(sig))
    handlers.append(RegimeExitHandler())

    if pos.rsi_exit_level < 999.0 and sig.rsi is not None:
        handlers.append(RSIExitHandler(sig))

    if pos.convex_exit and sig.mean_target_vals is not None:
        handlers.append(MeanTargetHandler(sig))

    handlers.append(MaxHoldHandler(sig))

    if pos.funding_exit_threshold > 0.0 and pos.is_perp:
        handlers.append(FundingCeilingHandler())

    return handlers


def run_exit_handlers(
    pos: Position,
    bar: BarContext,
    global_bar: int,
    adv_val: float,
) -> ExitCheck:
    """Run all handlers: update_state, partial TP, then check_exit until first match.

    Returns the first ExitCheck with should_exit=True, or _NO_EXIT.
    """
    # Phase 1: update state on all handlers
    for handler in pos.exit_handlers:
        handler.update_state(pos, bar)

    # Phase 2: partial profit-taking (side-effect, not a full exit)
    for handler in pos.exit_handlers:
        if isinstance(handler, PartialTPHandler):
            handler.process_partial(pos, bar, global_bar, adv_val)

    # Phase 3: check exit conditions (first match wins)
    for handler in pos.exit_handlers:
        result = handler.check_exit(pos, bar)
        if result.should_exit:
            return result

    return _NO_EXIT
