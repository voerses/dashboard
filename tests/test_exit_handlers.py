"""Unit tests for v4 exit handler chain.

Tests each handler in isolation with synthetic Position + BarContext,
plus integration tests for the full chain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pytest

from v4.exit_handlers import (
    BarContext,
    ExitCheck,
    BreakevenRatchetHandler,
    CircuitBreakerHandler,
    ConvexTrailingHandler,
    FundingCeilingHandler,
    MaxHoldHandler,
    MeanTargetHandler,
    RegimeExitHandler,
    RSIExitHandler,
    StopLossHandler,
    TakeProfitHandler,
    TrailingStopHandler,
    build_exit_chain,
    run_exit_handlers,
)
from v4.position import Position


# ---------------------------------------------------------------------------
# Helpers: minimal TokenSignals stub and Position factory
# ---------------------------------------------------------------------------

@dataclass
class _StubSignals:
    """Minimal TokenSignals substitute for handler tests."""
    bear_target_mult: float = 0.0
    bear_max_hold: int = 0
    rsi: Optional[np.ndarray] = None
    mean_target_vals: Optional[np.ndarray] = None
    regime: np.ndarray = field(default_factory=lambda: np.full(200, 3, dtype=np.int8))
    high: np.ndarray = field(default_factory=lambda: np.full(200, 105.0, dtype=np.float32))
    low: np.ndarray = field(default_factory=lambda: np.full(200, 95.0, dtype=np.float32))
    sma_trail_vals: Optional[np.ndarray] = None
    perp_high: Optional[np.ndarray] = None
    perp_low: Optional[np.ndarray] = None
    per_bar_is_perp: Optional[np.ndarray] = None


@dataclass
class _StubSpec:
    """Minimal StrategySpec substitute."""
    circuit_breaker_r: float = 0.0


def _make_position(
    direction=1,
    entry_price=100.0,
    stop_price=95.0,
    highest=105.0,
    lowest=95.0,
    initial_risk=5.0,
    no_stop_bars=6,
    min_hold=6,
    max_hold=720,
    trail_mult=3.0,
    target_mult=6.0,
    convex_exit=False,
    rsi_exit_level=999.0,
    breakeven_atr=0.0,
    breakeven_triggered=False,
    exit_regimes=None,
    is_perp=False,
    funding_exit_threshold=0.0,
    margin_usd=1000.0,
    cumulative_funding=0.0,
    partial_tp_atr=0.0,
    partial_closed=False,
    chandelier_lookback=0,
    trail_schedule=None,
    time_trail_schedule=None,
    max_trail_mult_arr=None,
    convex_bar_thresholds=(48, 12),
    convex_multipliers=(2.0, 1.5, 0.3),
    regime_exit_min_bars=6,
    entry_bar=0,
    leg="primary",
) -> Position:
    return Position(
        position_id="TEST:s30:0:primary",
        token="TEST",
        strategy_id="s30",
        leg=leg,
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=10.0 * direction,
        margin_usd=margin_usd,
        leverage=1.0,
        is_perp=is_perp,
        fee_rate=0.001,
        stop_mult=5.0,
        trail_mult=trail_mult,
        target_mult=target_mult,
        no_stop_bars=no_stop_bars,
        min_hold=min_hold,
        max_hold=max_hold,
        exit_regimes=exit_regimes or {0},
        convex_exit=convex_exit,
        rsi_exit_level=rsi_exit_level,
        regime_exit_min_bars=regime_exit_min_bars,
        convex_bar_thresholds=convex_bar_thresholds,
        convex_multipliers=convex_multipliers,
        trail_schedule=trail_schedule,
        time_trail_schedule=time_trail_schedule,
        max_trail_mult_arr=max_trail_mult_arr,
        funding_exit_threshold=funding_exit_threshold,
        partial_tp_atr=partial_tp_atr,
        breakeven_atr=breakeven_atr,
        breakeven_triggered=breakeven_triggered,
        chandelier_lookback=chandelier_lookback,
        stop_price=stop_price,
        highest=highest,
        lowest=lowest,
        initial_risk=initial_risk,
        cumulative_funding=cumulative_funding,
        partial_closed=partial_closed,
    )


def _make_bar(
    close=100.0, high=105.0, low=95.0, atr=5.0,
    rsi=50.0, regime=3, bars_held=10, local_bar=10, funding_val=0.0,
) -> BarContext:
    return BarContext(
        close=close, high=high, low=low, atr=atr,
        rsi=rsi, regime=regime, bars_held=bars_held,
        local_bar=local_bar, funding_val=funding_val,
    )


# ===========================================================================
# BreakevenRatchetHandler
# ===========================================================================

class TestBreakevenRatchetHandler:

    def test_no_trigger_when_disabled(self):
        handler = BreakevenRatchetHandler()
        pos = _make_position(breakeven_atr=0.0)
        bar = _make_bar()
        handler.update_state(pos, bar)
        assert pos.stop_price == 95.0  # unchanged
        assert not pos.breakeven_triggered

    def test_no_trigger_when_already_triggered(self):
        handler = BreakevenRatchetHandler()
        pos = _make_position(breakeven_atr=0.5, breakeven_triggered=True, stop_price=100.0)
        bar = _make_bar()
        handler.update_state(pos, bar)
        assert pos.stop_price == 100.0  # unchanged

    def test_trigger_long_moves_stop_to_entry(self):
        handler = BreakevenRatchetHandler()
        # highest=102.5, entry=100, atr=5 → profit_atr = 2.5/5 = 0.5
        pos = _make_position(
            direction=1, entry_price=100.0, highest=102.5,
            breakeven_atr=0.5, stop_price=95.0,
        )
        bar = _make_bar(atr=5.0)
        handler.update_state(pos, bar)
        assert pos.breakeven_triggered
        assert pos.stop_price == 100.0  # moved to entry

    def test_trigger_short_moves_stop_to_entry(self):
        handler = BreakevenRatchetHandler()
        pos = _make_position(
            direction=-1, entry_price=100.0, lowest=97.5,
            breakeven_atr=0.5, stop_price=105.0,
        )
        bar = _make_bar(atr=5.0)
        handler.update_state(pos, bar)
        assert pos.breakeven_triggered
        assert pos.stop_price == 100.0

    def test_no_trigger_insufficient_profit(self):
        handler = BreakevenRatchetHandler()
        pos = _make_position(
            direction=1, entry_price=100.0, highest=101.0,
            breakeven_atr=0.5, stop_price=95.0,
        )
        bar = _make_bar(atr=5.0)
        handler.update_state(pos, bar)
        assert not pos.breakeven_triggered
        assert pos.stop_price == 95.0

    def test_check_exit_always_no(self):
        handler = BreakevenRatchetHandler()
        pos = _make_position()
        bar = _make_bar()
        result = handler.check_exit(pos, bar)
        assert not result.should_exit


# ===========================================================================
# StopLossHandler
# ===========================================================================

class TestStopLossHandler:

    def test_long_stop_triggered(self):
        handler = StopLossHandler()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        bar = _make_bar(low=94.0, bars_held=10)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "stop"

    def test_long_stop_not_triggered(self):
        handler = StopLossHandler()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        bar = _make_bar(low=96.0, bars_held=10)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_short_stop_triggered(self):
        handler = StopLossHandler()
        pos = _make_position(direction=-1, stop_price=105.0, no_stop_bars=6)
        bar = _make_bar(high=106.0, bars_held=10)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "stop"

    def test_stop_inactive_during_no_stop_bars(self):
        handler = StopLossHandler()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        bar = _make_bar(low=90.0, bars_held=3)  # within no_stop_bars
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_stop_active_for_convex_even_during_no_stop_bars(self):
        handler = StopLossHandler()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6, convex_exit=True)
        bar = _make_bar(low=94.0, bars_held=2)
        result = handler.check_exit(pos, bar)
        assert result.should_exit


# ===========================================================================
# TakeProfitHandler
# ===========================================================================

class TestTakeProfitHandler:

    def test_long_target_hit_non_convex(self):
        sig = _StubSignals()
        handler = TakeProfitHandler(sig)
        pos = _make_position(direction=1, entry_price=100.0, target_mult=6.0)
        # target = 100 + 6*5 = 130, high=131
        bar = _make_bar(high=131.0, atr=5.0)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "target"

    def test_long_target_not_hit(self):
        sig = _StubSignals()
        handler = TakeProfitHandler(sig)
        pos = _make_position(direction=1, entry_price=100.0, target_mult=6.0)
        bar = _make_bar(high=125.0, atr=5.0)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_short_target_hit_non_convex(self):
        sig = _StubSignals()
        handler = TakeProfitHandler(sig)
        pos = _make_position(direction=-1, entry_price=100.0, target_mult=6.0)
        bar = _make_bar(low=69.0, atr=5.0)
        result = handler.check_exit(pos, bar)
        assert result.should_exit

    def test_convex_target_uses_initial_risk(self):
        sig = _StubSignals()
        handler = TakeProfitHandler(sig)
        pos = _make_position(
            direction=1, entry_price=100.0, target_mult=6.0,
            convex_exit=True, initial_risk=5.0,
        )
        # target = 100 + 6*5 = 130, close=131 triggers convex
        bar = _make_bar(close=131.0)
        result = handler.check_exit(pos, bar)
        assert result.should_exit

    def test_bear_target_mult_in_downtrend(self):
        sig = _StubSignals(bear_target_mult=3.0)
        handler = TakeProfitHandler(sig)
        pos = _make_position(direction=1, entry_price=100.0, target_mult=6.0)
        # In downtrend (regime=4), eff_target=3.0 → target = 100 + 3*5 = 115
        bar = _make_bar(high=116.0, atr=5.0, regime=4)
        result = handler.check_exit(pos, bar)
        assert result.should_exit


# ===========================================================================
# RegimeExitHandler
# ===========================================================================

class TestRegimeExitHandler:

    def test_regime_exit_triggered(self):
        handler = RegimeExitHandler()
        pos = _make_position(exit_regimes={0}, regime_exit_min_bars=6)
        bar = _make_bar(regime=0, bars_held=10)  # CRISIS
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "regime"

    def test_regime_not_in_exit_set(self):
        handler = RegimeExitHandler()
        pos = _make_position(exit_regimes={0})
        bar = _make_bar(regime=2, bars_held=10)  # UPTREND
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_regime_exit_blocked_by_min_bars(self):
        handler = RegimeExitHandler()
        pos = _make_position(exit_regimes={0}, regime_exit_min_bars=6)
        bar = _make_bar(regime=0, bars_held=5)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit


# ===========================================================================
# RSIExitHandler
# ===========================================================================

class TestRSIExitHandler:

    def test_long_rsi_exit_triggered(self):
        rsi_arr = np.full(200, 50.0, dtype=np.float32)
        rsi_arr[10] = 75.0
        sig = _StubSignals(rsi=rsi_arr)
        handler = RSIExitHandler(sig)
        pos = _make_position(direction=1, rsi_exit_level=70.0, min_hold=6)
        bar = _make_bar(bars_held=10, local_bar=10)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "rsi"

    def test_short_rsi_exit_triggered(self):
        rsi_arr = np.full(200, 50.0, dtype=np.float32)
        rsi_arr[10] = 25.0
        sig = _StubSignals(rsi=rsi_arr)
        handler = RSIExitHandler(sig)
        pos = _make_position(direction=-1, rsi_exit_level=70.0, min_hold=6)
        bar = _make_bar(bars_held=10, local_bar=10)
        result = handler.check_exit(pos, bar)
        assert result.should_exit  # 25 < 100 - 70 = 30

    def test_rsi_exit_blocked_by_min_hold(self):
        rsi_arr = np.full(200, 80.0, dtype=np.float32)
        sig = _StubSignals(rsi=rsi_arr)
        handler = RSIExitHandler(sig)
        pos = _make_position(direction=1, rsi_exit_level=70.0, min_hold=12)
        bar = _make_bar(bars_held=5, local_bar=5)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit


# ===========================================================================
# MeanTargetHandler
# ===========================================================================

class TestMeanTargetHandler:

    def test_mean_target_exit(self):
        mt_arr = np.full(200, 108.0, dtype=np.float32)
        sig = _StubSignals(mean_target_vals=mt_arr)
        handler = MeanTargetHandler(sig)
        pos = _make_position(
            direction=1, entry_price=100.0, initial_risk=5.0,
            convex_exit=True, min_hold=6,
        )
        # close=109 >= mt=108, but 109 < 100 + 2*5 = 110 → exit
        bar = _make_bar(close=109.0, bars_held=10, local_bar=10)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "mean_target"

    def test_mean_target_blocked_when_close_too_high(self):
        mt_arr = np.full(200, 108.0, dtype=np.float32)
        sig = _StubSignals(mean_target_vals=mt_arr)
        handler = MeanTargetHandler(sig)
        pos = _make_position(
            direction=1, entry_price=100.0, initial_risk=5.0,
            convex_exit=True, min_hold=6,
        )
        # close=112 >= mt=108, but 112 >= 100 + 2*5 = 110 → no exit
        bar = _make_bar(close=112.0, bars_held=10, local_bar=10)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_not_convex_returns_no_exit(self):
        mt_arr = np.full(200, 108.0, dtype=np.float32)
        sig = _StubSignals(mean_target_vals=mt_arr)
        handler = MeanTargetHandler(sig)
        pos = _make_position(convex_exit=False)
        bar = _make_bar(close=109.0, bars_held=10, local_bar=10)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit


# ===========================================================================
# MaxHoldHandler
# ===========================================================================

class TestMaxHoldHandler:

    def test_max_hold_exit(self):
        sig = _StubSignals()
        handler = MaxHoldHandler(sig)
        pos = _make_position(max_hold=720)
        bar = _make_bar(bars_held=720)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "max_hold"

    def test_max_hold_not_reached(self):
        sig = _StubSignals()
        handler = MaxHoldHandler(sig)
        pos = _make_position(max_hold=720)
        bar = _make_bar(bars_held=500)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_bear_max_hold_in_downtrend(self):
        sig = _StubSignals(bear_max_hold=360)
        handler = MaxHoldHandler(sig)
        pos = _make_position(max_hold=720)
        bar = _make_bar(bars_held=400, regime=4)
        result = handler.check_exit(pos, bar)
        assert result.should_exit


# ===========================================================================
# FundingCeilingHandler
# ===========================================================================

class TestFundingCeilingHandler:

    def test_funding_exit_triggered(self):
        handler = FundingCeilingHandler()
        pos = _make_position(
            is_perp=True, funding_exit_threshold=0.05,
            margin_usd=1000.0, cumulative_funding=60.0,
        )
        bar = _make_bar()
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "funding"

    def test_funding_below_threshold(self):
        handler = FundingCeilingHandler()
        pos = _make_position(
            is_perp=True, funding_exit_threshold=0.05,
            margin_usd=1000.0, cumulative_funding=30.0,
        )
        bar = _make_bar()
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_funding_disabled_for_spot(self):
        handler = FundingCeilingHandler()
        pos = _make_position(
            is_perp=False, funding_exit_threshold=0.05,
            margin_usd=1000.0, cumulative_funding=100.0,
        )
        bar = _make_bar()
        result = handler.check_exit(pos, bar)
        assert not result.should_exit


# ===========================================================================
# CircuitBreakerHandler
# ===========================================================================

class TestCircuitBreakerHandler:

    def test_long_circuit_breaker_triggered(self):
        handler = CircuitBreakerHandler(circuit_breaker_r=4.0)
        pos = _make_position(direction=1, entry_price=100.0, initial_risk=5.0)
        # cb_dist = 4 * 5 = 20, low <= 100 - 20 = 80
        bar = _make_bar(low=79.0)
        result = handler.check_exit(pos, bar)
        assert result.should_exit
        assert result.reason == "circuit_breaker"

    def test_short_circuit_breaker_triggered(self):
        handler = CircuitBreakerHandler(circuit_breaker_r=4.0)
        pos = _make_position(direction=-1, entry_price=100.0, initial_risk=5.0)
        bar = _make_bar(high=121.0)
        result = handler.check_exit(pos, bar)
        assert result.should_exit

    def test_circuit_breaker_not_triggered(self):
        handler = CircuitBreakerHandler(circuit_breaker_r=4.0)
        pos = _make_position(direction=1, entry_price=100.0, initial_risk=5.0)
        bar = _make_bar(low=85.0)  # 85 > 80 → no trigger
        result = handler.check_exit(pos, bar)
        assert not result.should_exit

    def test_disabled_when_zero(self):
        handler = CircuitBreakerHandler(circuit_breaker_r=0.0)
        pos = _make_position(direction=1, entry_price=100.0, initial_risk=5.0)
        bar = _make_bar(low=50.0)
        result = handler.check_exit(pos, bar)
        assert not result.should_exit


# ===========================================================================
# ConvexTrailingHandler
# ===========================================================================

class TestConvexTrailingHandler:

    def test_mature_trail_update(self):
        handler = ConvexTrailingHandler()
        pos = _make_position(
            direction=1, convex_exit=True,
            highest=120.0, stop_price=90.0,
            convex_bar_thresholds=(48, 12),
            convex_multipliers=(2.0, 1.5, 0.3),
        )
        bar = _make_bar(atr=5.0, bars_held=50)
        handler.update_state(pos, bar)
        # trail = 120 - 2.0 * 5 = 110
        assert pos.stop_price == 110.0

    def test_early_breakeven_trail(self):
        handler = ConvexTrailingHandler()
        pos = _make_position(
            direction=1, convex_exit=True,
            entry_price=100.0, highest=110.0, initial_risk=5.0,
            stop_price=90.0,
            convex_bar_thresholds=(48, 12),
            convex_multipliers=(2.0, 1.5, 0.3),
        )
        bar = _make_bar(atr=5.0, bars_held=15)
        handler.update_state(pos, bar)
        # highest=110 > entry + 1.5*5=107.5 → be_trail = 100 + 0.3*5 = 101.5
        assert pos.stop_price == 101.5

    def test_no_update_when_not_convex(self):
        handler = ConvexTrailingHandler()
        pos = _make_position(convex_exit=False, stop_price=90.0)
        bar = _make_bar(bars_held=50)
        handler.update_state(pos, bar)
        assert pos.stop_price == 90.0


# ===========================================================================
# TrailingStopHandler
# ===========================================================================

class TestTrailingStopHandler:

    def test_basic_trail_update_long(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        pos = _make_position(
            direction=1, trail_mult=3.0, highest=115.0,
            stop_price=90.0, no_stop_bars=6,
        )
        bar = _make_bar(atr=5.0, bars_held=10)
        handler.update_state(pos, bar)
        # trail = 115 - 3*5 = 100
        assert pos.stop_price == 100.0

    def test_basic_trail_update_short(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        pos = _make_position(
            direction=-1, trail_mult=3.0, lowest=85.0,
            stop_price=110.0, no_stop_bars=6,
        )
        bar = _make_bar(atr=5.0, bars_held=10)
        handler.update_state(pos, bar)
        # trail = 85 + 3*5 = 100
        assert pos.stop_price == 100.0

    def test_trail_only_tightens_long(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        pos = _make_position(
            direction=1, trail_mult=3.0, highest=115.0,
            stop_price=105.0, no_stop_bars=6,
        )
        bar = _make_bar(atr=5.0, bars_held=10)
        handler.update_state(pos, bar)
        # trail = 115 - 15 = 100 < current 105 → keep 105
        assert pos.stop_price == 105.0

    def test_no_update_before_no_stop_bars(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        pos = _make_position(trail_mult=3.0, stop_price=90.0, no_stop_bars=10)
        bar = _make_bar(bars_held=5)
        handler.update_state(pos, bar)
        assert pos.stop_price == 90.0

    def test_profit_schedule(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        # Schedule: at 2 ATR profit → trail_mult=2.0, at 4 → trail_mult=1.5
        schedule = np.array([[2.0, 2.0], [4.0, 1.5]])
        pos = _make_position(
            direction=1, entry_price=100.0, trail_mult=3.0,
            highest=120.0, stop_price=90.0, no_stop_bars=6,
            trail_schedule=schedule,
        )
        # close=115, atr=5 → profit_atr = |115-100|/5 = 3.0 → matches 2.0 threshold → eff_tm=2.0
        bar = _make_bar(close=115.0, atr=5.0, bars_held=10)
        handler.update_state(pos, bar)
        # trail = 120 - 2.0*5 = 110
        assert pos.stop_price == 110.0

    def test_time_trail_schedule(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        # Time schedule: at 100 bars → trail=2.0, at 200 → trail=1.5
        time_schedule = np.array([[100.0, 2.0], [200.0, 1.5]])
        pos = _make_position(
            direction=1, trail_mult=3.0, highest=120.0,
            stop_price=90.0, no_stop_bars=6,
            time_trail_schedule=time_schedule,
        )
        # bars_held=150 → matches 100 threshold → time_tm=2.0 < eff_tm=3.0
        bar = _make_bar(atr=5.0, bars_held=150)
        handler.update_state(pos, bar)
        # trail = 120 - 2.0*5 = 110
        assert pos.stop_price == 110.0

    def test_max_trail_mult_ceiling(self):
        sig = _StubSignals()
        handler = TrailingStopHandler(sig)
        max_arr = np.full(200, 1.5, dtype=np.float32)
        pos = _make_position(
            direction=1, trail_mult=3.0, highest=120.0,
            stop_price=90.0, no_stop_bars=6,
            max_trail_mult_arr=max_arr,
        )
        bar = _make_bar(atr=5.0, bars_held=10, local_bar=10)
        handler.update_state(pos, bar)
        # eff_tm capped to 1.5: trail = 120 - 1.5*5 = 112.5
        assert pos.stop_price == 112.5


# ===========================================================================
# build_exit_chain + run_exit_handlers integration
# ===========================================================================

class TestExitChainIntegration:

    def test_build_chain_default(self):
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position()
        chain = build_exit_chain(pos, sig, spec)
        # Should have: Breakeven, Trailing, Stop, TP, Regime, MaxHold
        assert len(chain) >= 5

    def test_build_chain_with_circuit_breaker(self):
        sig = _StubSignals()
        spec = _StubSpec(circuit_breaker_r=4.0)
        pos = _make_position()
        chain = build_exit_chain(pos, sig, spec)
        has_cb = any(isinstance(h, CircuitBreakerHandler) for h in chain)
        assert has_cb

    def test_build_chain_with_rsi_handler(self):
        rsi_arr = np.full(200, 50.0, dtype=np.float32)
        sig = _StubSignals(rsi=rsi_arr)
        spec = _StubSpec()
        pos = _make_position(rsi_exit_level=70.0)
        chain = build_exit_chain(pos, sig, spec)
        has_rsi = any(isinstance(h, RSIExitHandler) for h in chain)
        assert has_rsi

    def test_build_chain_convex_vs_trailing(self):
        sig = _StubSignals()
        spec = _StubSpec()
        pos_convex = _make_position(convex_exit=True)
        chain_convex = build_exit_chain(pos_convex, sig, spec)
        has_convex = any(isinstance(h, ConvexTrailingHandler) for h in chain_convex)
        has_trailing = any(isinstance(h, TrailingStopHandler) for h in chain_convex)
        assert has_convex
        assert not has_trailing

        pos_normal = _make_position(convex_exit=False)
        chain_normal = build_exit_chain(pos_normal, sig, spec)
        has_convex2 = any(isinstance(h, ConvexTrailingHandler) for h in chain_normal)
        has_trailing2 = any(isinstance(h, TrailingStopHandler) for h in chain_normal)
        assert not has_convex2
        assert has_trailing2

    def test_run_handlers_stop_exit(self):
        """Full chain returns stop exit when stop is hit."""
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position(direction=1, stop_price=95.0, no_stop_bars=6)
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        bar = _make_bar(low=94.0, bars_held=10, local_bar=10)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert result.should_exit
        assert result.reason == "stop"

    def test_run_handlers_no_exit(self):
        """Full chain returns no exit when nothing triggers."""
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position(direction=1, stop_price=90.0, no_stop_bars=6, max_hold=720)
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        bar = _make_bar(low=96.0, high=104.0, bars_held=10, local_bar=10)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert not result.should_exit

    def test_circuit_breaker_has_priority_over_stop(self):
        """Circuit breaker fires before stop-loss."""
        sig = _StubSignals()
        spec = _StubSpec(circuit_breaker_r=4.0)
        pos = _make_position(
            direction=1, entry_price=100.0, initial_risk=5.0,
            stop_price=95.0, no_stop_bars=6,
        )
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        # Both CB (80) and stop (95) triggered, CB should win
        bar = _make_bar(low=75.0, bars_held=10, local_bar=10)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert result.should_exit
        assert result.reason == "circuit_breaker"

    def test_breakeven_then_stop_interaction(self):
        """Breakeven ratchet moves stop, then stop triggers on same chain run."""
        sig = _StubSignals()
        spec = _StubSpec()
        pos = _make_position(
            direction=1, entry_price=100.0,
            highest=103.0,  # profit = 3/5 = 0.6 ATR
            stop_price=95.0,
            breakeven_atr=0.5,
            no_stop_bars=6,
        )
        pos.exit_handlers = build_exit_chain(pos, sig, spec)
        # Bar: low=99.5 (above original stop 95, below entry 100)
        # Breakeven triggers → stop moves to 100
        # Then stop check: low=99.5 <= 100 → exit
        bar = _make_bar(low=99.5, atr=5.0, bars_held=10, local_bar=10)
        result = run_exit_handlers(pos, bar, global_bar=10, adv_val=1e6)
        assert pos.breakeven_triggered
        assert pos.stop_price == 100.0
        assert result.should_exit
        assert result.reason == "stop"
