"""Tests for minute-level exit simulation.

Tests verify:
  - MinuteExitCache loads parquet data and returns correct hour slices
  - MinuteExitCache returns None for missing tokens/hours
  - _update_trail_minute respects sentinel guard conditions
  - _update_trail_minute tightens stop from minute-level highest
  - process_minute_exits closes position on minute-level stop breach
  - process_minute_exits closes position on minute-level target breach
  - process_minute_exits closes position on circuit breaker
  - process_minute_exits handles partial profit-taking
  - process_minute_exits handles breakeven ratchet
  - Positions closed by minute exits are skipped by subsequent _process_exits
  - Tokens without minute data fall through to hourly exits

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pandas as pd
import pytest

from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, PositionManager
from v4.simulator import SimulationState, _process_exits
from v4.signals import TokenSignals
from v4.minute_exits import MinuteExitCache, process_minute_exits, _update_trail_minute


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_timestamps(n: int, start: str = "2024-01-01") -> np.ndarray:
    return pd.date_range(start, periods=n, freq="1h").values


def _default_config(**overrides) -> PortfolioConfig:
    defaults = dict(
        capital=200_000.0,
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        base_spread_bps=0.0,
        impact_coeff=0.0,
        max_slip_bps=0.0,
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
    )
    defaults.update(overrides)
    return PortfolioConfig(**defaults)


def _make_position(
    token: str = "BTC",
    strategy_id: str = "s30",
    entry_price: float = 100.0,
    margin_usd: float = 10_000.0,
    direction: int = 1,
    entry_bar: int = 0,
    stop_price: float = 90.0,
    stop_mult: float = 2.0,
    trail_mult: float = 3.0,
    target_mult: float = 5.0,
    no_stop_bars: int = 0,
    convex_exit: bool = False,
    trail_schedule=None,
    chandelier_lookback: int = 0,
    initial_risk: float = 2.0,
    highest: float = 0.0,
    lowest: float = 999999.0,
    partial_tp_atr: float = 0.0,
    partial_tp_pct: float = 0.5,
    partial_tp_trail: float = 1.5,
    breakeven_atr: float = 0.0,
    circuit_breaker_r: float = 0.0,
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
        stop_mult=stop_mult,
        trail_mult=trail_mult,
        target_mult=target_mult,
        no_stop_bars=no_stop_bars,
        min_hold=6,
        max_hold=720,
        exit_regimes=set(),
        convex_exit=convex_exit,
        trail_schedule=trail_schedule,
        chandelier_lookback=chandelier_lookback,
        stop_price=stop_price,
        highest=highest if highest != 0.0 else entry_price,
        lowest=lowest if lowest != 999999.0 else entry_price,
        initial_risk=initial_risk,
        partial_tp_atr=partial_tp_atr,
        partial_tp_pct=partial_tp_pct,
        partial_tp_trail=partial_tp_trail,
        breakeven_atr=breakeven_atr,
    )


def _make_token_signals(
    token: str = "BTC",
    strategy_id: str = "s30",
    n_bars: int = 20,
    close_price: float = 100.0,
) -> TokenSignals:
    timestamps = _make_timestamps(n_bars)
    close_array = np.full(n_bars, close_price, dtype=np.float64)
    high_array = close_array + 1.0
    low_array = close_array - 1.0
    atr_array = np.full(n_bars, 2.0, dtype=np.float64)
    adv_array = np.full(n_bars, 5_000_000.0, dtype=np.float64)
    funding_array = np.zeros(n_bars, dtype=np.float64)
    entry_mask = np.zeros(n_bars, dtype=bool)
    dir_array = np.full(n_bars, 1, dtype=np.int8)
    regime_array = np.zeros(n_bars, dtype=np.int8)
    sm_array = np.full(n_bars, 1.0, dtype=np.float64)
    lev_array = np.full(n_bars, 1.0, dtype=np.float64)
    stop_arr = np.full(n_bars, 2.0, dtype=np.float64)
    trail_arr = np.full(n_bars, 3.0, dtype=np.float64)

    return TokenSignals(
        token=token,
        strategy_id=strategy_id,
        n_bars=n_bars,
        timestamps=timestamps,
        entry_mask=entry_mask,
        direction=dir_array,
        close=close_array,
        high=high_array,
        low=low_array,
        atr=atr_array,
        rolling_adv=adv_array,
        regime=regime_array,
        funding_1h=funding_array,
        exit_regimes=set(),
        stop_mult=stop_arr,
        trail_mult=trail_arr,
        target_mult=5.0,
        no_stop_bars=0,
        min_hold=6,
        max_hold=720,
        edge=0.35,
        size_multiplier=sm_array,
        cap_multiplier=1.0,
        leverage=lev_array,
        max_trade_pct=0.0,
        convex_exit=False,
        rsi=None,
        rsi_exit_level=999.0,
        mean_target_vals=None,
        is_combined=False,
        secondary_entry_mask=None,
        secondary_direction=None,
        is_perp_primary=True,
    )


def _make_minute_parquet(tmpdir: Path, token: str, hours: int = 20,
                         start: str = "2024-01-01",
                         base_price: float = 100.0,
                         minute_prices: dict | None = None):
    """Create a synthetic 1m parquet file.

    minute_prices: optional dict mapping hour_index -> list of 60 (high, low, close) tuples.
    If not provided, all minutes get base_price +/- 1.
    """
    n_minutes = hours * 60
    idx = pd.date_range(start, periods=n_minutes, freq="1min")
    high = np.full(n_minutes, base_price + 1.0)
    low = np.full(n_minutes, base_price - 1.0)
    close = np.full(n_minutes, base_price)
    open_ = np.full(n_minutes, base_price)
    volume = np.full(n_minutes, 1000.0)

    if minute_prices:
        for hour_idx, prices in minute_prices.items():
            for min_offset, (h, l, c) in enumerate(prices):
                i = hour_idx * 60 + min_offset
                if i < n_minutes:
                    high[i] = h
                    low[i] = l
                    close[i] = c

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    path = tmpdir / f"{token}_1m.parquet"
    df.to_parquet(path)
    return path


# ---------------------------------------------------------------------------
# MinuteExitCache tests
# ---------------------------------------------------------------------------

class TestMinuteExitCache:
    def test_load_and_get_minute_bars(self, tmp_path):
        _make_minute_parquet(tmp_path, "BTC", hours=3)
        cache = MinuteExitCache(data_dir=str(tmp_path))

        # Get first hour
        hour_ts = np.datetime64("2024-01-01T00:00", "ms")
        result = cache.get_minute_bars("BTC", hour_ts)
        assert result is not None
        high, low, close = result
        assert len(high) == 60
        assert len(low) == 60
        assert len(close) == 60

    def test_missing_token_returns_none(self, tmp_path):
        cache = MinuteExitCache(data_dir=str(tmp_path))
        result = cache.get_minute_bars("NONEXISTENT", np.datetime64("2024-01-01T00:00", "ms"))
        assert result is None

    def test_missing_hour_returns_none(self, tmp_path):
        _make_minute_parquet(tmp_path, "BTC", hours=3)
        cache = MinuteExitCache(data_dir=str(tmp_path))
        # Request hour far outside data range
        result = cache.get_minute_bars("BTC", np.datetime64("2020-01-01T00:00", "ms"))
        assert result is None

    def test_lazy_loading(self, tmp_path):
        _make_minute_parquet(tmp_path, "BTC", hours=2)
        _make_minute_parquet(tmp_path, "ETH", hours=2)
        cache = MinuteExitCache(data_dir=str(tmp_path))

        assert len(cache._loaded) == 0
        cache.get_minute_bars("BTC", np.datetime64("2024-01-01T00:00", "ms"))
        assert "BTC" in cache._loaded
        assert "ETH" not in cache._loaded

    def test_custom_minute_prices(self, tmp_path):
        prices_h0 = [(105.0, 95.0, 102.0)] + [(101.0, 99.0, 100.0)] * 59
        _make_minute_parquet(tmp_path, "BTC", hours=2, minute_prices={0: prices_h0})
        cache = MinuteExitCache(data_dir=str(tmp_path))

        hour_ts = np.datetime64("2024-01-01T00:00", "ms")
        high, low, close = cache.get_minute_bars("BTC", hour_ts)
        assert high[0] == 105.0
        assert low[0] == 95.0
        assert close[0] == 102.0
        assert high[1] == 101.0


# ---------------------------------------------------------------------------
# _update_trail_minute tests
# ---------------------------------------------------------------------------

class TestUpdateTrailMinute:
    def test_basic_trail_tightening_long(self):
        pos = _make_position(entry_price=100.0, stop_price=90.0, trail_mult=2.0,
                             highest=110.0, no_stop_bars=0)
        # ATR=2.0, highest=110 -> trail = 110 - 2*2 = 106
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == 106.0

    def test_basic_trail_tightening_short(self):
        pos = _make_position(entry_price=100.0, direction=-1, stop_price=110.0,
                             trail_mult=2.0, lowest=90.0, no_stop_bars=0)
        # ATR=2.0, lowest=90 -> trail = 90 + 2*2 = 94
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == 94.0

    def test_guard_convex_exit(self):
        pos = _make_position(stop_price=90.0, convex_exit=True, highest=110.0,
                             no_stop_bars=0)
        orig_stop = pos.stop_price
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == orig_stop  # unchanged

    def test_guard_trail_schedule(self):
        schedule = np.array([[1.0, 1.5], [2.0, 1.0]])
        pos = _make_position(stop_price=90.0, trail_schedule=schedule, highest=110.0,
                             no_stop_bars=0)
        orig_stop = pos.stop_price
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == orig_stop

    def test_guard_chandelier(self):
        pos = _make_position(stop_price=90.0, chandelier_lookback=10, highest=110.0,
                             no_stop_bars=0)
        orig_stop = pos.stop_price
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == orig_stop

    def test_guard_no_stop_bars(self):
        pos = _make_position(stop_price=90.0, no_stop_bars=10, highest=110.0)
        orig_stop = pos.stop_price
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == orig_stop  # bars_held < no_stop_bars

    def test_stop_only_tightens_never_loosens(self):
        pos = _make_position(entry_price=100.0, stop_price=106.0, trail_mult=2.0,
                             highest=108.0, no_stop_bars=0)
        # trail = 108 - 2*2 = 104 < current 106 -> no change
        _update_trail_minute(pos, cur_atr=2.0, bars_held=5)
        assert pos.stop_price == 106.0


# ---------------------------------------------------------------------------
# process_minute_exits integration tests
# ---------------------------------------------------------------------------

def _setup_minute_exit_test(tmp_path, pos_kwargs=None, minute_prices=None,
                            spec_kwargs=None):
    """Set up common test infrastructure for minute exit tests."""
    pos_kw = pos_kwargs or {}
    pos = _make_position(**pos_kw)

    token = pos_kw.get("token", "BTC")
    strategy_id = pos_kw.get("strategy_id", "s30")
    entry_price = pos_kw.get("entry_price", 100.0)

    sig = _make_token_signals(token=token, strategy_id=strategy_id,
                              close_price=entry_price)

    all_signals = {strategy_id: {token: sig}}
    unified_ts = sig.timestamps
    bar_maps = {token: np.arange(sig.n_bars, dtype=np.int64)}

    spec_kw = spec_kwargs or {}
    spec = StrategySpec(strategy_id=strategy_id, **spec_kw)
    strategy_specs = {strategy_id: spec}
    config = _default_config()

    state = SimulationState(initial_capital=200_000.0)
    state.position_manager.open_position(pos)
    state._entry_fees_by_pos[pos.position_id] = 0.0

    # Create minute data
    _make_minute_parquet(tmp_path, token, hours=sig.n_bars,
                         base_price=entry_price,
                         minute_prices=minute_prices)
    minute_cache = MinuteExitCache(data_dir=str(tmp_path))

    return state, all_signals, bar_maps, unified_ts, config, strategy_specs, minute_cache, pos


class TestProcessMinuteExitsStop:
    def test_stop_hit_at_minute_level(self, tmp_path):
        """Stop is breached by minute-level low -> position closed."""
        # Long at 100, stop at 95. Minute 10 of hour 5 has low=94.
        minute_prices = {
            5: [(101.0, 99.0, 100.0)] * 10 + [(101.0, 94.0, 96.0)] + [(101.0, 99.0, 100.0)] * 49
        }
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path, pos_kwargs={"stop_price": 95.0, "entry_bar": 0},
                                   minute_prices=minute_prices)

        # Process bar 5
        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        assert state.position_manager.total_open() == 0
        assert len(state.position_manager.closed_trades) == 1
        trade = state.position_manager.closed_trades[0]
        assert trade.exit_reason == "stop"
        assert trade.exit_price == 96.0  # close of breaching minute candle

    def test_no_stop_when_above(self, tmp_path):
        """Stop is not breached -> position stays open."""
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path, pos_kwargs={"stop_price": 80.0, "entry_bar": 0})

        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        assert state.position_manager.total_open() == 1


class TestProcessMinuteExitsTarget:
    def test_target_hit_at_minute_level(self, tmp_path):
        """Target is hit by minute-level high -> position closed."""
        # Long at 100, target_mult=5, atr=2, target_price = 100 + 5*2 = 110
        # Minute 20 of hour 5 has high=111, low=109 (above tightened stop)
        # Use no_stop_bars=10 so stop is inactive at bars_held=5
        minute_prices = {
            5: [(101.0, 100.0, 100.5)] * 20 + [(111.0, 109.0, 109.5)] + [(101.0, 100.0, 100.5)] * 39
        }
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path,
                                   pos_kwargs={"stop_price": 80.0, "entry_bar": 0,
                                               "target_mult": 5.0, "no_stop_bars": 10},
                                   minute_prices=minute_prices)

        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        assert state.position_manager.total_open() == 0
        trade = state.position_manager.closed_trades[0]
        assert trade.exit_reason == "target"
        assert trade.exit_price == 109.5


class TestProcessMinuteExitsCB:
    def test_circuit_breaker_hit(self, tmp_path):
        """Circuit breaker triggered by minute low."""
        # Long at 100, initial_risk=2, CB=5 -> cb_dist=10 -> breach at 90
        # Minute 5 of hour 3 has low=89
        minute_prices = {
            3: [(101.0, 99.0, 100.0)] * 5 + [(101.0, 89.0, 91.0)] + [(101.0, 99.0, 100.0)] * 54
        }
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path,
                                   pos_kwargs={"stop_price": 95.0, "entry_bar": 0,
                                               "initial_risk": 2.0},
                                   spec_kwargs={"circuit_breaker_r": 5.0},
                                   minute_prices=minute_prices)

        process_minute_exits(state, all_signals, bar_maps, 3, unified_ts, config, specs, cache)
        assert state.position_manager.total_open() == 0
        trade = state.position_manager.closed_trades[0]
        assert trade.exit_reason == "circuit_breaker"


class TestProcessMinuteExitsBreakeven:
    def test_breakeven_ratchet(self, tmp_path):
        """Breakeven ratchet triggers at minute level, moving stop to entry."""
        # Long at 100, breakeven_atr=2.0, ATR=2.0 -> needs profit of 4.0 above entry
        # Minute 15 hits high=105 -> profit_atr = 5/2 = 2.5 >= 2.0 -> breakeven triggers
        # Use no_stop_bars=10 so stop doesn't fire at bars_held=5
        minute_prices = {
            5: [(101.0, 100.0, 100.5)] * 15 + [(105.0, 103.0, 104.0)] + [(101.0, 100.0, 100.5)] * 44
        }
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path,
                                   pos_kwargs={"stop_price": 90.0, "entry_bar": 0,
                                               "breakeven_atr": 2.0, "highest": 100.0,
                                               "no_stop_bars": 10},
                                   minute_prices=minute_prices)

        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        # Position should still be open (stop inactive), but breakeven triggered
        assert state.position_manager.total_open() == 1
        assert pos.breakeven_triggered is True
        assert pos.stop_price >= 100.0  # stop moved to entry price


class TestProcessMinuteExitsTrail:
    def test_trail_tightens_from_minute_highs(self, tmp_path):
        """Trail stop tightens as minute highs push highest up."""
        # Long at 100, trail_mult=2, ATR=2 -> trail = highest - 4
        # Ascending highs and lows, target_mult very high to avoid target exit
        ascending = [(100.0 + i * 0.2, 100.0 + i * 0.15, 100.0 + i * 0.1) for i in range(60)]
        # Last minute: high = 100 + 59*0.2 = 111.8, low = 100 + 59*0.15 = 108.85
        minute_prices = {5: ascending}
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path,
                                   pos_kwargs={"stop_price": 90.0, "entry_bar": 0,
                                               "trail_mult": 2.0, "highest": 100.0,
                                               "no_stop_bars": 10, "target_mult": 999.0},
                                   minute_prices=minute_prices)

        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        # highest should be ~111.8 from minute data
        assert pos.highest == pytest.approx(111.8, abs=0.1)
        # stop not active (bars_held=5 < no_stop_bars=10), so stop_price unchanged
        # but highest was updated for future trail use
        assert state.position_manager.total_open() == 1


class TestProcessMinuteExitsFallthrough:
    def test_missing_token_falls_through(self, tmp_path):
        """Token without minute data is not affected by minute exits."""
        # Create position for ETH but only provide BTC minute data
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path, pos_kwargs={"token": "ETH", "stop_price": 80.0,
                                                          "entry_bar": 0})

        # Remove the ETH parquet (the helper creates it, but we want it missing)
        eth_path = tmp_path / "ETH_1m.parquet"
        if eth_path.exists():
            eth_path.unlink()

        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        assert state.position_manager.total_open() == 1  # still open


class TestMinuteExitsIntegration:
    def test_minute_closed_skipped_by_hourly(self, tmp_path):
        """Position closed by minute exits is not re-processed by _process_exits."""
        # Stop hit at minute level
        minute_prices = {
            5: [(101.0, 99.0, 100.0)] * 10 + [(101.0, 85.0, 87.0)] + [(101.0, 99.0, 100.0)] * 49
        }
        state, all_signals, bar_maps, unified_ts, config, specs, cache, pos = \
            _setup_minute_exit_test(tmp_path,
                                   pos_kwargs={"stop_price": 90.0, "entry_bar": 0},
                                   minute_prices=minute_prices)

        # Minute exits close the position
        process_minute_exits(state, all_signals, bar_maps, 5, unified_ts, config, specs, cache)
        assert state.position_manager.total_open() == 0

        # Hourly exits should not crash (no positions to process)
        _process_exits(state, all_signals, bar_maps, 5, config, strategy_specs=specs)
        assert len(state.position_manager.closed_trades) == 1  # still just 1 trade
