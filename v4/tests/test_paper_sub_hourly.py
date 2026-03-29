"""Tests for integrated sub-hourly exits in the paper trader.

Tests verify:
  - PaperConfig exit_resolution field validation
  - PaperPortfolioEngine initializes candle_aggregator/price_monitor when exit_resolution > 0
  - PaperPortfolioEngine does NOT initialize them when exit_resolution == 0
  - process_sub_hourly_exits closes position on stop breach
  - process_sub_hourly_exits closes position on target breach
  - process_sub_hourly_exits closes position on circuit breaker
  - process_sub_hourly_exits skips tokens without cached bar data
  - process_sub_hourly_exits persists state after exits
  - process_sub_hourly_exits handles linked positions
  - _cache_bar_data populates cache from signals
  - _update_ws_subscriptions updates price monitor subscriptions
  - check_candle_exits shared helper works for stop/target/CB

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v4.paper_engine import PaperPortfolioEngine
from v4.paper_config import PaperConfig, validate_paper_config
from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, PositionManager
from v4.simulator import SimulationState
from v4.minute_exits import check_candle_exits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(**overrides) -> PaperConfig:
    """Build a PaperConfig for sub-hourly tests."""
    # Propagate exit_resolution to strategies if not explicitly providing strategies
    _exit_res = overrides.get("exit_resolution", 0)
    defaults = dict(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10, exit_resolution=_exit_res),
        ],
        capital=200_000.0,
        mode="pool",
        pool_name="test",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
        lookback_months=3,
        enable_purge_windows=False,
        drawdown_alert_pct=5.0,
        stress_adv_multiplier=0.3,
        max_slip_bps=300,
        exit_resolution=0,
    )
    defaults.update(overrides)
    return PaperConfig(**defaults)


def _make_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_price: float = 100.0,
    margin_usd: float = 10_000.0,
    direction: int = 1,
    entry_bar: int = 0,
    stop_price: float = 90.0,
    trail_mult: float = 3.0,
    target_mult: float = 5.0,
    no_stop_bars: int = 0,
    convex_exit: bool = False,
    initial_risk: float = 2.0,
    highest: float = 0.0,
    lowest: float = 999999.0,
    breakeven_atr: float = 0.0,
) -> Position:
    quantity = direction * margin_usd / entry_price
    return Position(
        position_id=f"{token}:{strategy_id}:0:primary",
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=quantity,
        margin_usd=margin_usd,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=trail_mult,
        target_mult=target_mult,
        no_stop_bars=no_stop_bars,
        min_hold=6,
        max_hold=720,
        exit_regimes=set(),
        convex_exit=convex_exit,
        stop_price=stop_price,
        highest=highest if highest != 0.0 else entry_price,
        lowest=lowest if lowest != 999999.0 else entry_price,
        initial_risk=initial_risk,
        breakeven_atr=breakeven_atr,
    )


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------

class TestPaperConfigExitResolution:

    def test_exit_resolution_default_zero(self):
        config = _make_test_config()
        assert config.exit_resolution == 0

    def test_exit_resolution_valid_values(self):
        for res in (0, 1, 5, 15, 30):
            config = _make_test_config(exit_resolution=res)
            validate_paper_config(config)  # Should not raise

    def test_exit_resolution_invalid_value(self):
        config = _make_test_config(exit_resolution=7)
        with pytest.raises(ValueError, match="exit_resolution"):
            validate_paper_config(config)

    def test_exit_resolution_invalid_negative(self):
        config = _make_test_config(exit_resolution=-1)
        with pytest.raises(ValueError, match="exit_resolution"):
            validate_paper_config(config)


# ---------------------------------------------------------------------------
# Engine initialization tests
# ---------------------------------------------------------------------------

class TestEngineInitialization:

    def test_exit_resolution_zero_no_ws(self):
        """exit_resolution=0 should NOT create aggregator or price monitor."""
        config = _make_test_config(exit_resolution=0)
        engine = PaperPortfolioEngine(config)
        assert engine._candle_aggregator is None
        assert engine._price_monitor is None

    def test_exit_resolution_positive_creates_aggregator(self):
        """exit_resolution>0 should create CandleAggregator."""
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)
        assert engine._candle_aggregator is not None
        assert engine._price_monitor is not None

    def test_cached_bar_data_initialized_empty(self):
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)
        assert engine._cached_bar_data == {}


# ---------------------------------------------------------------------------
# check_candle_exits shared helper tests
# ---------------------------------------------------------------------------

class TestCheckCandleExits:

    def test_stop_breach_long(self):
        """Long position: low breaches stop_price → exit."""
        pos = _make_position(entry_price=100.0, stop_price=90.0, direction=1)
        reason, price = check_candle_exits(
            pos, h=101.0, l=89.0, c=90.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=5.0, convex_initial_risk=2.0,
        )
        assert reason == "stop"
        assert price == 90.0

    def test_stop_breach_short(self):
        """Short position: high breaches stop_price → exit."""
        pos = _make_position(
            entry_price=100.0, stop_price=110.0, direction=-1,
            highest=100.0, lowest=100.0,
        )
        reason, price = check_candle_exits(
            pos, h=111.0, l=99.0, c=110.5,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=5.0, convex_initial_risk=2.0,
        )
        assert reason == "stop"
        assert price == 110.5

    def test_target_hit_long(self):
        """Long position: high reaches target → exit."""
        pos = _make_position(
            entry_price=100.0, stop_price=80.0, direction=1,
            target_mult=3.0, trail_mult=10.0,
        )
        # Target = entry + 3.0 * atr = 100 + 3*5 = 115
        # Trail stop = highest(116) - 10*5 = 66, well below low=105
        reason, price = check_candle_exits(
            pos, h=116.0, l=105.0, c=115.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=3.0, convex_initial_risk=2.0,
        )
        assert reason == "target"
        assert price == 115.0

    def test_circuit_breaker_long(self):
        """Long position: circuit breaker fires on large loss."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            initial_risk=5.0,
        )
        # CB triggers when loss >= cb_r * initial_risk = 3.0 * 5.0 = 15
        # So price <= 100 - 15 = 85
        reason, price = check_candle_exits(
            pos, h=100.0, l=84.0, c=85.0,
            cur_atr=5.0, bars_held=5, cb_r=3.0,
            eff_target=5.0, convex_initial_risk=5.0,
        )
        assert reason == "circuit_breaker"
        assert price == 85.0

    def test_no_exit_when_stop_inactive(self):
        """No stop exit before no_stop_bars elapsed."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            no_stop_bars=10,
        )
        reason, price = check_candle_exits(
            pos, h=101.0, l=89.0, c=90.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=5.0, convex_initial_risk=2.0,
        )
        assert reason is None
        assert price is None

    def test_nan_candle_skipped(self):
        """NaN candle values should return (None, None)."""
        pos = _make_position()
        reason, price = check_candle_exits(
            pos, h=float("nan"), l=float("nan"), c=float("nan"),
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=5.0, convex_initial_risk=2.0,
        )
        assert reason is None
        assert price is None

    def test_updates_highest_long(self):
        """check_candle_exits should update pos.highest for long positions."""
        pos = _make_position(entry_price=100.0, highest=100.0, direction=1, stop_price=80.0)
        check_candle_exits(
            pos, h=110.0, l=99.0, c=105.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=10.0, convex_initial_risk=2.0,
        )
        assert pos.highest == 110.0

    def test_updates_lowest_short(self):
        """check_candle_exits should update pos.lowest for short positions."""
        pos = _make_position(
            entry_price=100.0, lowest=100.0, direction=-1,
            stop_price=120.0,
        )
        check_candle_exits(
            pos, h=101.0, l=90.0, c=95.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=10.0, convex_initial_risk=2.0,
        )
        assert pos.lowest == 90.0

    def test_breakeven_ratchet(self):
        """Breakeven ratchet should move stop to entry when profit threshold met."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            breakeven_atr=2.0, highest=100.0,
        )
        # High=112 → highest becomes 112, profit_atr = (112-100)/5 = 2.4 >= 2.0
        check_candle_exits(
            pos, h=112.0, l=99.0, c=111.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=10.0, convex_initial_risk=2.0,
        )
        assert pos.breakeven_triggered is True
        assert pos.stop_price >= 100.0  # Moved to at least entry price

    def test_convex_target(self):
        """Convex exit uses initial_risk for target calculation."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            convex_exit=True, initial_risk=5.0, target_mult=3.0,
        )
        # Target = entry + eff_target * initial_risk = 100 + 3 * 5 = 115
        reason, price = check_candle_exits(
            pos, h=116.0, l=100.0, c=116.0,
            cur_atr=5.0, bars_held=5, cb_r=0.0,
            eff_target=3.0, convex_initial_risk=5.0,
        )
        assert reason == "target"


# ---------------------------------------------------------------------------
# process_sub_hourly_exits tests
# ---------------------------------------------------------------------------

class TestProcessSubHourlyExits:

    def _make_engine_with_position(self, pos, exit_resolution=5, **config_kw):
        """Create an engine with a single open position and cached bar data."""
        config = _make_test_config(exit_resolution=exit_resolution, **config_kw)
        engine = PaperPortfolioEngine(config)
        engine.state.position_manager.open_position(pos)
        engine.tick_counter = 10  # Simulate having run 10 ticks
        engine._cached_bar_data[(pos.strategy_id, pos.token)] = {
            "atr": 5.0,
            "adv": 1e6,
            "regime": 0,
            "bear_target_mult": 0.0,
        }
        return engine

    def test_stop_breach_closes_position(self):
        """Sub-hourly stop breach should close the position."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)

        candles = {"BTC": (101.0, 89.0, 90.0)}  # h, l, c — low breaches stop
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 1
        assert len(engine.state.position_manager.open_positions) == 0
        assert len(engine.state.position_manager.closed_trades) == 1
        assert engine.state.position_manager.closed_trades[0].exit_reason == "stop"

    def test_target_breach_closes_position(self):
        """Sub-hourly target breach should close the position."""
        pos = _make_position(
            entry_price=100.0, stop_price=80.0, direction=1,
            target_mult=3.0, trail_mult=10.0, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)

        # Target = 100 + 3.0 * 5.0 (ATR) = 115
        # Trail stop = highest(116) - 10*5 = 66, well below low=105
        candles = {"BTC": (116.0, 105.0, 115.0)}
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 1
        assert engine.state.position_manager.closed_trades[0].exit_reason == "target"

    def test_circuit_breaker_closes_position(self):
        """Sub-hourly circuit breaker should close the position."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            initial_risk=5.0, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)

        # CB @ cb_r=5.0 * initial_risk=5.0 = 25 → price <= 75
        # Default strategy has circuit_breaker_r=5.0
        # Actually need to check StrategySpec default
        spec = engine.config.strategies[0]
        cb_dist = spec.circuit_breaker_r * 5.0
        breach_price = 100.0 - cb_dist - 1.0

        candles = {"BTC": (100.0, breach_price, breach_price + 0.5)}
        closed = engine.process_sub_hourly_exits(candles)

        if spec.circuit_breaker_r > 0:
            assert closed == 1
            assert engine.state.position_manager.closed_trades[0].exit_reason == "circuit_breaker"
        else:
            # CB disabled — should not close
            assert closed == 0

    def test_no_exit_when_price_within_stops(self):
        """No exit when candle is within safe range."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            target_mult=10.0, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)

        candles = {"BTC": (102.0, 98.0, 101.0)}  # Safe range
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 0
        assert len(engine.state.position_manager.open_positions) == 1

    def test_skips_token_without_cached_bar_data(self):
        """Tokens without cached bar data should be skipped."""
        pos = _make_position(token="ETH", entry_price=3000.0, stop_price=2700.0, entry_bar=5)
        engine = self._make_engine_with_position(pos)
        # Remove cached data for ETH
        engine._cached_bar_data.pop(("s56", "ETH"), None)

        candles = {"ETH": (3001.0, 2600.0, 2650.0)}  # Would breach stop
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 0  # Skipped — no cached bar data

    def test_skips_token_not_in_candles(self):
        """Positions for tokens not in the candles dict should be skipped."""
        pos = _make_position(entry_price=100.0, stop_price=90.0, entry_bar=5)
        engine = self._make_engine_with_position(pos)

        candles = {"ETH": (3001.0, 2600.0, 2650.0)}  # BTC not in candles
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 0

    def test_empty_candles_no_exits(self):
        """Empty candles dict should produce no exits."""
        pos = _make_position(entry_price=100.0, stop_price=90.0, entry_bar=5)
        engine = self._make_engine_with_position(pos)

        closed = engine.process_sub_hourly_exits({})
        assert closed == 0

    def test_pnl_accounting_long(self):
        """PnL accounting for long position exit should be correct."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            margin_usd=10_000.0, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)
        initial_equity = engine.state.portfolio_equity

        candles = {"BTC": (101.0, 89.0, 90.0)}
        engine.process_sub_hourly_exits(candles)

        trade = engine.state.position_manager.closed_trades[0]
        # Long stop: exit_price = 90 - slippage (slippage reduces exit for longs)
        assert trade.exit_price < 90.0  # Slippage applied
        assert trade.exit_reason == "stop"
        # realized_pnl should be worse than -1000 (slippage widens loss)
        assert engine.state.realized_pnl < -1000.0

    def test_pnl_accounting_short(self):
        """PnL accounting for short position exit should be correct."""
        pos = _make_position(
            entry_price=100.0, stop_price=110.0, direction=-1,
            margin_usd=10_000.0, entry_bar=5,
            highest=100.0, lowest=100.0,
        )
        engine = self._make_engine_with_position(pos)

        candles = {"BTC": (111.0, 99.0, 110.5)}
        engine.process_sub_hourly_exits(candles)

        trade = engine.state.position_manager.closed_trades[0]
        # Short stop: exit_price = 110.5 + slippage (slippage raises exit for shorts)
        assert trade.exit_price > 110.5  # Slippage applied
        # realized_pnl should be worse than -1050 (slippage widens loss)
        assert engine.state.realized_pnl < -1050.0

    def test_exit_resolution_zero_no_sub_hourly(self):
        """exit_resolution=0 should not have aggregator — hourly only mode."""
        config = _make_test_config(exit_resolution=0)
        engine = PaperPortfolioEngine(config)
        assert engine._candle_aggregator is None
        assert engine._price_monitor is None

    def test_update_ws_subscriptions_no_monitor(self):
        """_update_ws_subscriptions should be no-op when price_monitor is None."""
        config = _make_test_config(exit_resolution=0)
        engine = PaperPortfolioEngine(config)
        engine._update_ws_subscriptions()  # Should not raise

    def test_partial_tp_triggers_sub_hourly(self):
        """Sub-hourly partial TP should close 50% and tighten trail."""
        pos = _make_position(
            entry_price=100.0, stop_price=0.0, direction=1,
            trail_mult=3.0, target_mult=999.0, entry_bar=5,
            no_stop_bars=0,
        )
        pos.partial_tp_atr = 1.5     # Take partial at 1.5 ATR
        pos.partial_tp_pct = 0.5     # Close 50%
        pos.partial_tp_trail = 2.0   # Tighten trail from 3.0 to 2.0 ATR
        original_qty = pos.quantity

        engine = self._make_engine_with_position(pos)
        # ATR = 5.0 from cached bar data, so 1.5 ATR = 7.5
        # Entry = 100, need high >= 107.5

        candles = {"BTC": (108.0, 99.0, 105.0)}  # High 108 > 107.5 threshold
        closed = engine.process_sub_hourly_exits(candles)

        # Position should still be open (partial, not full close)
        assert len(engine.state.position_manager.open_positions) == 1
        # But quantity should be halved
        remaining_pos = engine.state.position_manager.open_positions[0]
        assert abs(remaining_pos.quantity - original_qty * 0.5) < 0.01
        assert remaining_pos.partial_closed is True
        # Trail should be tightened from 3.0 to 2.0 by _partial_close_position
        assert remaining_pos.trail_mult == 2.0
        assert remaining_pos.trail_schedule is None
        # Should have one partial trade recorded
        assert len(engine.state.position_manager.closed_trades) == 1
        assert engine.state.position_manager.closed_trades[0].exit_reason == "partial_tp"
        assert closed == 0  # No full exits

    def test_partial_tp_does_not_retrigger(self):
        """Partial TP should only fire once — no re-trigger after partial_closed=True."""
        pos = _make_position(
            entry_price=100.0, stop_price=0.0, direction=1,
            trail_mult=999.0, target_mult=999.0, entry_bar=5,
        )
        pos.partial_tp_atr = 1.5
        pos.partial_tp_pct = 0.5
        pos.partial_tp_trail = 2.0
        pos.partial_closed = True  # Already partially closed
        original_qty = pos.quantity

        engine = self._make_engine_with_position(pos)

        candles = {"BTC": (120.0, 115.0, 118.0)}  # Above threshold, wide trail=999 won't fire
        engine.process_sub_hourly_exits(candles)

        # Quantity should not change — partial already happened
        remaining_pos = engine.state.position_manager.open_positions[0]
        assert remaining_pos.quantity == original_qty
        assert len(engine.state.position_manager.closed_trades) == 0

    def test_partial_tp_short_direction(self):
        """Sub-hourly partial TP should work for short positions too."""
        pos = _make_position(
            entry_price=100.0, stop_price=999.0, direction=-1,
            trail_mult=2.0, target_mult=999.0, entry_bar=5,
            highest=100.0, lowest=100.0,
        )
        pos.partial_tp_atr = 1.5
        pos.partial_tp_pct = 0.5
        pos.partial_tp_trail = 2.0
        original_qty = pos.quantity

        engine = self._make_engine_with_position(pos)
        # ATR = 5.0, so 1.5 ATR = 7.5. Short needs low <= 92.5

        candles = {"BTC": (101.0, 92.0, 95.0)}  # Low 92 < 92.5 threshold
        engine.process_sub_hourly_exits(candles)

        remaining_pos = engine.state.position_manager.open_positions[0]
        assert abs(remaining_pos.quantity - original_qty * 0.5) < 0.01
        assert remaining_pos.partial_closed is True
        assert engine.state.position_manager.closed_trades[0].exit_reason == "partial_tp"

    def test_partial_tp_below_threshold_no_trigger(self):
        """Partial TP should NOT trigger when profit is below threshold."""
        pos = _make_position(
            entry_price=100.0, stop_price=0.0, direction=1,
            trail_mult=2.0, target_mult=999.0, entry_bar=5,
        )
        pos.partial_tp_atr = 1.5
        pos.partial_tp_pct = 0.5
        pos.partial_tp_trail = 2.0
        original_qty = pos.quantity

        engine = self._make_engine_with_position(pos)
        # ATR = 5.0, so 1.5 ATR = 7.5. Need high >= 107.5

        candles = {"BTC": (106.0, 99.0, 103.0)}  # High 106 < 107.5
        engine.process_sub_hourly_exits(candles)

        remaining_pos = engine.state.position_manager.open_positions[0]
        assert remaining_pos.quantity == original_qty
        assert remaining_pos.partial_closed is False
        assert len(engine.state.position_manager.closed_trades) == 0

    def test_partial_tp_then_trail_stop_same_candle(self):
        """Partial TP fires, tightens trail, then trail stop closes remainder on same candle.

        This mirrors the backtest's per-minute iteration: partial TP on one minute,
        tightened trail triggers on a later minute within the same aggregated candle.
        """
        pos = _make_position(
            entry_price=100.0, stop_price=0.0, direction=1,
            trail_mult=10.0, target_mult=999.0, entry_bar=5,
            no_stop_bars=0, highest=100.0,
        )
        pos.partial_tp_atr = 1.5     # 1.5 * 5.0 ATR = 7.5 → need high >= 107.5
        pos.partial_tp_pct = 0.5
        pos.partial_tp_trail = 2.0   # After partial: trail = 2.0 ATR
        original_qty = pos.quantity

        engine = self._make_engine_with_position(pos)
        # ATR = 5.0 from cached bar data
        # Candle: high=110 triggers partial TP (profit=10 > 7.5 ATR threshold)
        # After partial TP: trail_mult=2.0, highest updates to 110
        # Trail stop = 110 - 2.0*5.0 = 100.  Low=99 breaches 100 → full exit
        candles = {"BTC": (110.0, 99.0, 101.0)}
        closed = engine.process_sub_hourly_exits(candles)

        # Should have 2 trades: partial_tp + stop (or trail)
        trades = engine.state.position_manager.closed_trades
        assert len(trades) == 2
        assert trades[0].exit_reason == "partial_tp"
        assert trades[1].exit_reason == "stop"
        # Position fully closed
        assert len(engine.state.position_manager.open_positions) == 0
        assert closed == 1  # Full exits count

    def test_partial_tp_prorates_entry_fees(self):
        """Partial TP should pro-rate entry fees from _entry_fees_by_pos."""
        pos = _make_position(
            entry_price=100.0, stop_price=0.0, direction=1,
            trail_mult=999.0, target_mult=999.0, entry_bar=5,
            no_stop_bars=0,
        )
        pos.partial_tp_atr = 1.5
        pos.partial_tp_pct = 0.5
        pos.partial_tp_trail = 2.0

        engine = self._make_engine_with_position(pos)
        st = engine.state

        # Populate entry fees (normally set during _process_entries_for_tick)
        st._entry_fees_by_pos[pos.position_id] = 10.0  # $10 entry fee

        candles = {"BTC": (108.0, 99.0, 105.0)}  # High 108 > 107.5
        engine.process_sub_hourly_exits(candles)

        # Partial TP trade should have pro-rated entry fee
        trade = st.position_manager.closed_trades[0]
        assert trade.exit_reason == "partial_tp"
        assert abs(trade.entry_fee - 5.0) < 0.01  # 50% of $10
        # Remaining entry fee should be reduced
        assert abs(st._entry_fees_by_pos.get(pos.position_id, 0.0) - 5.0) < 0.01

    def test_independent_mode_partial_tp(self):
        """Partial TP should work in independent mode (per-strategy states)."""
        config = _make_test_config(
            exit_resolution=5,
            mode="independent",
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="perp",
                             max_positions=10, exit_resolution=5),
                StrategySpec(strategy_id="s99", weight=0.5, market="perp",
                             max_positions=10, exit_resolution=5),
            ],
        )
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        # Add position only for s56
        pos = _make_position(
            token="BTC", strategy_id="s56", entry_price=100.0,
            stop_price=0.0, direction=1, trail_mult=3.0, target_mult=999.0,
            entry_bar=5, no_stop_bars=0,
        )
        pos.partial_tp_atr = 1.5
        pos.partial_tp_pct = 0.5
        pos.partial_tp_trail = 2.0
        original_qty = pos.quantity

        # In independent mode, strategy_states is keyed by strategy_id
        s56_state = engine.strategy_states["s56"]
        s56_state.position_manager.open_position(pos)

        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 5.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
        }

        candles = {"BTC": (108.0, 99.0, 105.0)}
        engine.process_sub_hourly_exits(candles)

        remaining_pos = s56_state.position_manager.open_positions[0]
        assert abs(remaining_pos.quantity - original_qty * 0.5) < 0.01
        assert remaining_pos.partial_closed is True
        assert len(s56_state.position_manager.closed_trades) == 1
        assert s56_state.position_manager.closed_trades[0].exit_reason == "partial_tp"

    def test_zero_atr_uses_fallback(self):
        """Zero ATR in cached bar data should fall back to entry_price * 0.02."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1,
            trail_mult=3.0, target_mult=999.0, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)
        # Override ATR to 0.0 — fallback should be 100 * 0.02 = 2.0
        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 0.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
        }
        # Trail stop with fallback ATR=2.0: highest(101) - 3.0*2.0 = 95
        # Low=94 breaches 95 → stop fires
        candles = {"BTC": (101.0, 94.0, 96.0)}
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 1
        assert engine.state.position_manager.closed_trades[0].exit_reason == "stop"

    def test_bear_regime_tightens_target(self):
        """Bear regime (regime=4) should use bear_target_mult for earlier exit."""
        pos = _make_position(
            entry_price=100.0, stop_price=80.0, direction=1,
            trail_mult=999.0, target_mult=10.0, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)
        # Set bear regime with tighter target
        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 5.0, "adv": 1e6, "regime": 4, "bear_target_mult": 2.0,
        }
        # Normal target = 100 + 10*5 = 150 (wouldn't fire at 112)
        # Bear target = 100 + 2.0*5 = 110 (fires at 112)
        candles = {"BTC": (112.0, 99.0, 111.0)}
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 1
        assert engine.state.position_manager.closed_trades[0].exit_reason == "target"

    def test_inf_candle_values_skipped(self):
        """Inf candle values from corrupted WS feeds should be skipped."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
        )
        engine = self._make_engine_with_position(pos)

        candles = {"BTC": (float("inf"), 89.0, 90.0)}
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 0
        assert len(engine.state.position_manager.open_positions) == 1


# ---------------------------------------------------------------------------
# _cache_bar_data tests
# ---------------------------------------------------------------------------

class TestCacheBarData:

    def test_cache_populates_from_signals(self):
        """_cache_bar_data should populate the cache dict."""
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0  # Current tick is 0

        # Create minimal mock signals
        from v4.signals import TokenSignals
        sig = MagicMock(spec=TokenSignals)
        sig.n_bars = 10
        sig.regime = np.array([0, 1, 2, 3, 4, 0, 1, 2, 3, 4])
        sig.bear_target_mult = 3.0

        all_signals = {"s56": {"BTC": sig}}
        # bar_maps[token][tick_counter] = local_bar
        bar_maps = {"BTC": np.array([5])}  # tick 0 maps to local bar 5

        with patch("v4.simulator._get_bar_data") as mock_gbd:
            mock_gbd.return_value = (100.0, 105.0, 95.0, 5.0, 1e6, 0.0)
            engine._cache_bar_data(all_signals, bar_maps)

        assert ("s56", "BTC") in engine._cached_bar_data
        assert engine._cached_bar_data[("s56", "BTC")]["atr"] == 5.0
        assert engine._cached_bar_data[("s56", "BTC")]["adv"] == 1e6

    def test_cache_per_strategy_keying(self):
        """Each strategy should get its own ATR cache entry for the same token."""
        config = _make_test_config(
            exit_resolution=5,
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10, exit_resolution=5),
                StrategySpec(strategy_id="s99", weight=0.5, market="combined", max_positions=10, exit_resolution=5),
            ],
        )
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        from v4.signals import TokenSignals
        sig_s56 = MagicMock(spec=TokenSignals)
        sig_s56.n_bars = 10
        sig_s56.regime = np.array([0, 1, 2, 3, 4, 0, 1, 2, 3, 4])
        sig_s56.bear_target_mult = 3.0

        sig_s99 = MagicMock(spec=TokenSignals)
        sig_s99.n_bars = 10
        sig_s99.regime = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        sig_s99.bear_target_mult = 2.0

        all_signals = {"s56": {"BTC": sig_s56}, "s99": {"BTC": sig_s99}}
        bar_maps = {"BTC": np.array([5])}

        sig_s56.per_bar_is_perp = None
        sig_s99.per_bar_is_perp = None

        call_count = [0]
        def _mock_get_bar_data(sig, local_bar, flag, use_perp=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return (100.0, 105.0, 95.0, 5.0, 1e6, 0.0)  # s56: ATR=5
            else:
                return (100.0, 108.0, 92.0, 8.0, 2e6, 0.0)  # s99: ATR=8

        with patch("v4.simulator._get_bar_data", side_effect=_mock_get_bar_data):
            engine._cache_bar_data(all_signals, bar_maps)

        # Both strategies should have their own entry
        assert ("s56", "BTC") in engine._cached_bar_data
        assert ("s99", "BTC") in engine._cached_bar_data
        assert engine._cached_bar_data[("s56", "BTC")]["atr"] == 5.0
        assert engine._cached_bar_data[("s99", "BTC")]["atr"] == 8.0


# ---------------------------------------------------------------------------
# Linked exit price tests
# ---------------------------------------------------------------------------

class TestLinkedExitPrice:

    def test_linked_exit_uses_computed_price_same_token(self):
        """Linked leg (same token) should run check_candle_exits for exit price."""
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)

        # Primary long position (will hit stop)
        primary = _make_position(
            token="BTC", strategy_id="s56", entry_price=100.0,
            stop_price=90.0, direction=1, entry_bar=5,
        )
        primary.linked_position_id = "BTC:s56:0:secondary"

        # Linked short position (hedge) — same token
        linked = _make_position(
            token="BTC", strategy_id="s56", entry_price=100.0,
            stop_price=110.0, direction=-1, entry_bar=5,
            highest=100.0, lowest=100.0,
        )
        linked.position_id = "BTC:s56:0:secondary"
        linked.linked_position_id = "BTC:s56:0:primary"

        engine.state.position_manager.open_position(primary)
        engine.state.position_manager.open_position(linked)
        engine.tick_counter = 10

        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 5.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
        }

        candles = {"BTC": (101.0, 89.0, 95.0)}
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 2
        trades = engine.state.position_manager.closed_trades
        assert len(trades) == 2

        linked_trade = [t for t in trades if t.exit_reason == "linked_exit"][0]
        # Linked short: exit_price = candle_close + slippage (short buys back higher)
        assert linked_trade.exit_price > 95.0  # Slippage applied to linked exit

    def test_linked_exit_cross_token_uses_own_candle(self):
        """Linked leg on different token should use its own candle data."""
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)

        primary = _make_position(
            token="BTC", strategy_id="s56", entry_price=100.0,
            stop_price=90.0, direction=1, entry_bar=5,
        )
        primary.linked_position_id = "ETH:s56:0:secondary"

        # Linked position on ETH (different token)
        linked = _make_position(
            token="ETH", strategy_id="s56", entry_price=3000.0,
            stop_price=3300.0, direction=-1, entry_bar=5,
            highest=3000.0, lowest=3000.0,
        )
        linked.position_id = "ETH:s56:0:secondary"
        linked.linked_position_id = "BTC:s56:0:primary"

        engine.state.position_manager.open_position(primary)
        engine.state.position_manager.open_position(linked)
        engine.tick_counter = 10

        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 5.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
        }
        engine._cached_bar_data[("s56", "ETH")] = {
            "atr": 50.0, "adv": 5e5, "regime": 0, "bear_target_mult": 0.0,
        }

        candles = {
            "BTC": (101.0, 89.0, 95.0),  # BTC stop breach
            "ETH": (3100.0, 2900.0, 3050.0),  # ETH no breach
        }
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 2
        trades = engine.state.position_manager.closed_trades
        linked_trade = [t for t in trades if t.exit_reason == "linked_exit"][0]
        # Should use ETH candle close + slippage (short buys back higher)
        assert linked_trade.exit_price > 3050.0  # ETH price with slippage, not BTC

    def test_linked_exit_no_candle_for_linked_token(self):
        """When linked token has no candle data, exit at primary's candle close."""
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)

        primary = _make_position(
            token="BTC", strategy_id="s56", entry_price=100.0,
            stop_price=90.0, direction=1, entry_bar=5,
        )
        primary.linked_position_id = "ETH:s56:0:secondary"

        linked = _make_position(
            token="ETH", strategy_id="s56", entry_price=3000.0,
            stop_price=3300.0, direction=-1, entry_bar=5,
            highest=3000.0, lowest=3000.0,
        )
        linked.position_id = "ETH:s56:0:secondary"
        linked.linked_position_id = "BTC:s56:0:primary"

        engine.state.position_manager.open_position(primary)
        engine.state.position_manager.open_position(linked)
        engine.tick_counter = 10

        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 5.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
        }

        # Only BTC candle, no ETH candle
        candles = {"BTC": (101.0, 89.0, 95.0)}
        closed = engine.process_sub_hourly_exits(candles)

        assert closed == 2
        trades = engine.state.position_manager.closed_trades
        linked_trade = [t for t in trades if t.exit_reason == "linked_exit"][0]
        # Falls back to primary's candle close + slippage (short buys back higher)
        assert linked_trade.exit_price > 95.0  # Slippage applied


# ---------------------------------------------------------------------------
# equity.csv sub-hourly persist tests
# ---------------------------------------------------------------------------

class TestEquityCsvSubHourly:

    def test_persist_writes_equity_csv(self):
        """_persist_sub_hourly_state should write an equity.csv row."""
        import tempfile, os
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            engine.config.state_dir = tmpdir
            engine.tick_counter = 5
            engine._flushed_position_ids = set()

            engine._persist_sub_hourly_state("2025-01-01T12:00:00Z")

            equity_path = os.path.join(tmpdir, "equity.csv")
            assert os.path.exists(equity_path)
            with open(equity_path) as f:
                lines = f.readlines()
            assert len(lines) >= 2  # header + at least 1 data row
            # Verify timestamp is in the data row
            assert "2025-01-01T12:00:00Z" in lines[1]

    def test_sub_hourly_exit_updates_last_known_prices(self):
        """process_sub_hourly_exits should update _last_known_prices from candle closes."""
        pos = _make_position(
            entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
        )
        config = _make_test_config(exit_resolution=5)
        engine = PaperPortfolioEngine(config)
        engine.state.position_manager.open_position(pos)
        engine.tick_counter = 10
        engine._cached_bar_data[("s56", "BTC")] = {
            "atr": 5.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
        }

        candles = {"BTC": (101.0, 89.0, 90.0)}
        engine.process_sub_hourly_exits(candles)

        # _last_known_prices should now contain BTC's candle close
        assert engine._last_known_prices.get("BTC") == 90.0
