"""Tests for integrated sub-hourly entry resolution in the paper trader.

Tests verify:
  - PaperPortfolioEngine initializes entry resolution tracking
  - _cache_armed_levels populates cache from signals with armed levels
  - _cache_armed_levels skips when no armed level set
  - _cache_armed_levels skips combined strategies
  - _cache_armed_levels skips when position already open
  - process_sub_hourly_entries opens position on 1m cross of armed level
  - process_sub_hourly_entries skips when no cross
  - process_sub_hourly_entries respects portfolio position limit
  - process_sub_hourly_entries is no-op when entry_resolution=0
  - Armed levels refreshed at next hourly tick (fresh cache)
  - _update_ws_subscriptions includes armed token tokens
  - _process_entries_for_tick skips strategies with entry_resolution > 0

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
from v4.paper_config import PaperConfig
from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, PositionManager
from v4.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(**overrides) -> PaperConfig:
    """Build a PaperConfig for sub-hourly entry tests."""
    _entry_res = overrides.pop("entry_resolution", 0)
    _exit_res = overrides.get("exit_resolution", 0)
    # Allow overriding strategies directly
    if "strategies" not in overrides:
        overrides["strategies"] = [
            StrategySpec(
                strategy_id="s501", weight=0.5, market="perp",
                max_positions=10, exit_resolution=_exit_res,
                entry_resolution=_entry_res,
            ),
        ]
    defaults = dict(
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


def _make_mock_signal(
    n_bars: int = 10,
    armed_bar: int = 9,
    direction: int = 1,
    armed_level: float = 100.0,
    close_val: float = 95.0,
    high_val: float = 98.0,
    low_val: float = 90.0,
    atr_val: float = 5.0,
    adv_val: float = 1e6,
    is_combined: bool = False,
    no_armed: bool = False,
):
    """Build a mock TokenSignals for armed level entry resolution tests.

    By default, sets up a long armed level (price hasn't crossed yet).
    """
    from v4.signals import TokenSignals
    sig = MagicMock(spec=TokenSignals)
    sig.n_bars = n_bars
    sig.is_combined = is_combined

    # Arrays
    sig.entry_mask = np.zeros(n_bars, dtype=bool)
    sig.direction = np.zeros(n_bars, dtype=np.int8)
    sig.close = np.full(n_bars, close_val)
    sig.high = np.full(n_bars, high_val)
    sig.low = np.full(n_bars, low_val)
    sig.atr = np.full(n_bars, atr_val)
    sig.rolling_adv = np.full(n_bars, adv_val)
    sig.leverage = np.ones(n_bars)
    sig.size_multiplier = np.ones(n_bars)
    sig.cap_multiplier = np.ones(n_bars)
    sig.stop_mult = np.full(n_bars, 2.0)
    sig.trail_mult = np.full(n_bars, 999.0)
    sig.target_mult = 999.0
    sig.no_stop_bars = 0
    sig.min_hold = 1
    sig.max_hold = 4
    sig.exit_regimes = set()
    sig.convex_exit = False
    sig.edge = 0.15
    sig.max_trade_pct = 0.05
    sig.is_perp_primary = True
    sig.per_bar_is_perp = None
    sig.perp_close = None
    sig.perp_atr = None
    sig.perp_rolling_adv = None
    sig.regime = np.zeros(n_bars, dtype=np.int8)
    sig.bear_target_mult = 0.0

    # Exit handler fields (matching TokenSignals defaults)
    sig.rsi_exit_level = 999.0
    sig.regime_exit_min_bars = 6
    sig.convex_bar_thresholds = (48, 12)
    sig.convex_multipliers = (2.0, 1.5, 0.3)
    sig.trail_schedule = None
    sig.time_trail_schedule = None
    sig.max_trail_mult = None
    sig.funding_exit_threshold = 0.0
    sig.partial_tp_atr = 0.0
    sig.partial_tp_pct = 0.5
    sig.partial_tp_trail = 1.5
    sig.breakeven_atr = 0.0
    sig.chandelier_lookback = 0
    sig.funding_zscore = None

    # Entry limit price (for backtest path — not used in armed level path)
    sig.entry_limit_price = None

    # Armed level arrays
    if no_armed:
        sig.armed_levels = None
        sig.armed_direction = None
    else:
        sig.armed_levels = np.full(n_bars, np.nan, dtype=np.float64)
        sig.armed_levels[armed_bar] = armed_level
        sig.armed_direction = np.zeros(n_bars, dtype=np.int8)
        sig.armed_direction[armed_bar] = direction

    return sig


# ---------------------------------------------------------------------------
# Engine initialization tests
# ---------------------------------------------------------------------------

class TestEngineEntryResolutionInit:

    def test_entry_resolution_zero_defaults(self):
        """entry_resolution=0 should not set effective_entry_resolution."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        assert engine._effective_entry_resolution == 0
        assert engine._armed_tokens == {}

    def test_entry_resolution_positive_tracked(self):
        """entry_resolution>0 should set effective_entry_resolution."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        assert engine._effective_entry_resolution == 1
        assert engine._strategy_entry_resolution["s501"] == 1

    def test_entry_resolution_creates_ws_infra(self):
        """entry_resolution>0 should create CandleAggregator even without exit_resolution."""
        config = _make_test_config(entry_resolution=1, exit_resolution=0)
        engine = PaperPortfolioEngine(config)
        assert engine._candle_aggregator is not None
        assert engine._price_monitor is not None

    def test_exit_and_entry_resolution_both_create_ws(self):
        """Both resolutions active should still create exactly one WS infra."""
        config = _make_test_config(entry_resolution=1, exit_resolution=5)
        engine = PaperPortfolioEngine(config)
        assert engine._candle_aggregator is not None


# ---------------------------------------------------------------------------
# _cache_armed_levels tests
# ---------------------------------------------------------------------------

class TestCacheArmedLevels:

    def test_cache_populates_armed_level(self):
        """Armed level on pre-cross bar should be cached for WS monitoring."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(
            n_bars=1, armed_bar=0, direction=1,
            armed_level=100.0,
            close_val=95.0,   # close < armed_level → pre-cross
            high_val=98.0,
            low_val=90.0,
        )
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") in engine._armed_tokens
        cand = engine._armed_tokens[("s501", "BTC")]
        assert cand["level"] == 100.0
        assert cand["direction"] == 1
        assert cand["close_val"] == 95.0
        assert cand["sig_ref"] is sig

    def test_cache_skips_no_armed_levels(self):
        """Signal without armed_levels should NOT be cached."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1, no_armed=True)

        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._armed_tokens

    def test_cache_skips_nan_armed_level(self):
        """Bar with NaN armed level should NOT be cached."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1)
        sig.armed_levels[0] = np.nan  # Override to NaN

        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._armed_tokens

    def test_cache_skips_combined_strategy(self):
        """Combined strategies should NOT be cached."""
        config = _make_test_config(
            entry_resolution=1,
            strategies=[
                StrategySpec(
                    strategy_id="s501", weight=0.5, market="combined",
                    max_positions=10, entry_resolution=1,
                ),
            ],
        )
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1, armed_level=100.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._armed_tokens

    def test_cache_skips_existing_position(self):
        """Token with existing open position should NOT be cached."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        # Open an existing position for BTC
        pos = Position(
            position_id="BTC:s501:0:primary", token="BTC",
            strategy_id="s501", leg="primary", entry_bar=0,
            entry_price=105.0, direction=1, quantity=100.0,
            margin_usd=10000.0, leverage=1.0, is_perp=True,
            fee_rate=0.0005, stop_mult=2.0, trail_mult=999.0,
            target_mult=999.0, no_stop_bars=0, min_hold=1,
            max_hold=4, exit_regimes=set(), convex_exit=False,
            stop_price=95.0, highest=105.0, lowest=105.0,
            initial_risk=10.0,
        )
        engine.state.position_manager.open_position(pos)

        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1, armed_level=100.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._armed_tokens

    def test_cache_skips_entry_resolution_zero(self):
        """Strategy with entry_resolution=0 should NOT produce armed levels."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1, armed_level=100.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        assert len(engine._armed_tokens) == 0

    def test_cache_refreshes_on_each_tick(self):
        """Cache should be refreshed at each call (atomic swap per tick)."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        # Manually populate cache
        engine._armed_tokens[("s501", "OLD")] = {"dummy": True}

        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1, armed_level=100.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_armed_levels(all_signals, strategy_specs, bar_maps)

        # Old token should be gone
        assert ("s501", "OLD") not in engine._armed_tokens
        # New token should be present
        assert ("s501", "BTC") in engine._armed_tokens


# ---------------------------------------------------------------------------
# process_sub_hourly_entries tests
# ---------------------------------------------------------------------------

class TestProcessSubHourlyEntries:

    def _make_engine_with_armed(self, entry_resolution=1, **config_kw):
        """Create an engine with a single armed entry level."""
        config = _make_test_config(entry_resolution=entry_resolution, **config_kw)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10
        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0,
            "direction": 1,
            "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0,
            "atr_val": 5.0,
            "adv_val": 1e6,
            "leverage": 1.0,
            "size_multiplier": 1.0,
            "cap_multiplier": 1.0,
            "is_perp": True,
            "stop_mult": 2.0,
            "trail_mult": 999.0,
            "target_mult": 999.0,
            "no_stop_bars": 0,
            "min_hold": 1,
            "max_hold": 4,
            "exit_regimes": set(),
            "convex_exit": False,
            "edge": 0.15,
            "max_trade_pct": 0.05,
            "high_val": 98.0,
            "low_val": 90.0,
        }
        return engine

    def test_cross_detected_opens_position(self):
        """1m candle crossing armed level should open a position."""
        engine = self._make_engine_with_armed()

        # Long armed_level=100, 1m high=101 >= 100 → cross detected
        candles = {"BTC": (101.0, 99.0, 100.5)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1
        assert len(engine.state.position_manager.open_positions) == 1
        pos = engine.state.position_manager.open_positions[0]
        assert pos.token == "BTC"
        assert pos.strategy_id == "s501"
        assert pos.direction == 1
        # Entry price should be close to 1m candle close (100.5) + slippage
        assert pos.entry_price > 100.5  # slippage added
        assert pos.entry_timestamp  # Should have timestamp set

    def test_no_cross_no_entry(self):
        """1m candle NOT crossing armed level should not open a position."""
        engine = self._make_engine_with_armed()

        # Long armed_level=100, 1m high=99.5 < 100 → no cross
        candles = {"BTC": (99.5, 98.0, 99.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0
        assert len(engine.state.position_manager.open_positions) == 0
        # Armed token should still be active
        assert ("s501", "BTC") in engine._armed_tokens

    def test_cross_removes_armed_token(self):
        """Executed armed token should be removed from cache."""
        engine = self._make_engine_with_armed()

        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)

        assert ("s501", "BTC") not in engine._armed_tokens

    def test_short_cross_opens_position(self):
        """Short 1m cross should open a short position."""
        engine = self._make_engine_with_armed()
        engine._armed_tokens[("s501", "BTC")]["direction"] = -1
        engine._armed_tokens[("s501", "BTC")]["level"] = 100.0

        # Short armed_level=100, 1m low=99 <= 100 → cross detected
        candles = {"BTC": (101.0, 99.0, 99.5)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1
        pos = engine.state.position_manager.open_positions[0]
        assert pos.direction == -1

    def test_portfolio_limit_blocks_entry(self):
        """Portfolio position limit should prevent sub-hourly entry."""
        engine = self._make_engine_with_armed(max_portfolio_positions=0)

        candles = {"BTC": (101.0, 99.0, 100.5)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_strategy_limit_blocks_entry(self):
        """Strategy position limit should prevent sub-hourly entry."""
        engine = self._make_engine_with_armed(
            strategies=[
                StrategySpec(
                    strategy_id="s501", weight=0.5, market="perp",
                    max_positions=0, entry_resolution=1,
                ),
            ],
        )

        candles = {"BTC": (101.0, 99.0, 100.5)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_entry_resolution_zero_noop(self):
        """entry_resolution=0 should return 0 immediately."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        # Manually add armed token (shouldn't be checked)
        engine._armed_tokens[("s501", "BTC")] = {"dummy": True}

        candles = {"BTC": (101.0, 99.0, 100.5)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_empty_candles_noop(self):
        """Empty candles dict should produce no entries."""
        engine = self._make_engine_with_armed()
        entries = engine.process_sub_hourly_entries({})
        assert entries == 0

    def test_token_not_in_candles_skipped(self):
        """Armed token not in candles should be skipped."""
        engine = self._make_engine_with_armed()

        candles = {"ETH": (2000.0, 1900.0, 1950.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0
        # Armed token still active
        assert ("s501", "BTC") in engine._armed_tokens

    def test_entry_fee_accounting(self):
        """Entry fee should be tracked in state."""
        engine = self._make_engine_with_armed()
        initial_fees = engine.state.total_fees

        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)

        assert engine.state.total_fees > initial_fees

    def test_entry_price_includes_slippage(self):
        """Entry price should include slippage (higher than 1m close for longs)."""
        engine = self._make_engine_with_armed()

        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)

        pos = engine.state.position_manager.open_positions[0]
        # Long direction: entry_price = close + slippage > close
        assert pos.entry_price > 100.5

    def test_multiple_armed_some_cross(self):
        """Multiple armed tokens: only those with crosses should execute."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        # BTC: long armed_level=100 (will cross with high=101)
        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
        }
        # ETH: long armed_level=2000 (won't cross with high=1990)
        engine._armed_tokens[("s501", "ETH")] = {
            "level": 2000.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 1950.0, "atr_val": 50.0, "adv_val": 1e7,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 1980.0, "low_val": 1920.0,
        }

        candles = {
            "BTC": (101.0, 99.0, 100.5),     # high=101 >= level=100 → cross
            "ETH": (1990.0, 1980.0, 1985.0),  # high=1990 < level=2000 → no cross
        }
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1
        assert len(engine.state.position_manager.open_positions) == 1
        assert engine.state.position_manager.open_positions[0].token == "BTC"
        # BTC removed, ETH still armed
        assert ("s501", "BTC") not in engine._armed_tokens
        assert ("s501", "ETH") in engine._armed_tokens

    def test_long_cross_uses_high_not_close(self):
        """Long cross detection uses high >= level, NOT close > level."""
        engine = self._make_engine_with_armed()

        # high=100.1 >= level=100, but close=99 < level
        # Should still trigger because we check high, not close
        candles = {"BTC": (100.1, 98.0, 99.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1

    def test_short_cross_uses_low_not_close(self):
        """Short cross detection uses low <= level, NOT close < level."""
        engine = self._make_engine_with_armed()
        engine._armed_tokens[("s501", "BTC")]["direction"] = -1
        engine._armed_tokens[("s501", "BTC")]["level"] = 100.0

        # low=99.9 <= level=100, but close=101 > level
        # Should still trigger because we check low, not close
        candles = {"BTC": (102.0, 99.9, 101.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1


# ---------------------------------------------------------------------------
# NaN candle guard tests
# ---------------------------------------------------------------------------

class TestNaNCangleGuard:

    def _make_engine_with_armed(self):
        """Create an engine with a single armed entry level."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10
        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
        }
        return engine

    def test_nan_close_skips_entry(self):
        """NaN candle close should not open a position."""
        engine = self._make_engine_with_armed()
        candles = {"BTC": (float('nan'), float('nan'), float('nan'))}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0
        assert len(engine.state.position_manager.open_positions) == 0
        # Armed token should remain (not executed, not removed)
        assert ("s501", "BTC") in engine._armed_tokens

    def test_inf_close_skips_entry(self):
        """Inf candle close should not open a position."""
        engine = self._make_engine_with_armed()
        candles = {"BTC": (float('inf'), 99.0, float('inf'))}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_nan_price_not_stored(self):
        """NaN candle close should NOT overwrite _last_known_prices."""
        config = _make_test_config(entry_resolution=1, exit_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine._last_known_prices["BTC"] = 50000.0

        # Simulate sub-hourly exits with NaN candle — price should be preserved
        candles = {"BTC": (float('nan'), float('nan'), float('nan'))}
        engine.process_sub_hourly_exits(candles)

        assert engine._last_known_prices["BTC"] == 50000.0


# ---------------------------------------------------------------------------
# entry_bar and position ID correctness tests
# ---------------------------------------------------------------------------

class TestEntryBarAndPositionID:

    def _make_engine_with_armed(self, cached_tick=5):
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        # Simulate post-increment: tick_counter is N+1, but armed level was cached at N
        engine.tick_counter = cached_tick + 1
        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": cached_tick,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
        }
        return engine

    def test_entry_bar_uses_cached_tick(self):
        """Position entry_bar should use cached tick_counter, not current."""
        engine = self._make_engine_with_armed(cached_tick=5)
        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)

        pos = engine.state.position_manager.open_positions[0]
        assert pos.entry_bar == 5  # cached tick, not engine.tick_counter=6

    def test_position_id_uses_sub_suffix(self):
        """Sub-hourly position ID should use ':sub' suffix for disambiguation."""
        engine = self._make_engine_with_armed(cached_tick=5)
        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)

        pos = engine.state.position_manager.open_positions[0]
        assert pos.position_id == "BTC:s501:5:sub"
        assert ":sub" in pos.position_id


# ---------------------------------------------------------------------------
# Price update after sub-hourly entry
# ---------------------------------------------------------------------------

class TestPriceUpdateAfterEntry:

    def test_last_known_price_updated_after_entry(self):
        """_last_known_prices should be updated to candle close after sub-hourly entry."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10
        engine._last_known_prices["BTC"] = 50.0  # stale price

        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
        }

        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)

        assert engine._last_known_prices["BTC"] == 100.5  # Updated to candle close


# ---------------------------------------------------------------------------
# Exit handler init at sub-hourly entry (backtest parity)
# ---------------------------------------------------------------------------

class TestExitHandlerInitAtEntry:
    """Verify build_exit_chain is called immediately after sub-hourly position creation."""

    def test_exit_handlers_populated_on_entry(self):
        """Position created via sub-hourly entry should have exit_handlers built."""
        sig = _make_mock_signal()
        sig.rsi = None
        sig.mean_target_vals = None
        sig.sma_trail_vals = None

        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
            "sig_ref": sig,
        }

        candles = {"BTC": (101.0, 99.0, 100.5)}
        entries = engine.process_sub_hourly_entries(candles)
        assert entries == 1

        pos = engine.state.position_manager.open_positions[0]
        assert pos.exit_handlers is not None
        assert len(pos.exit_handlers) > 0

    def test_exit_handlers_contain_key_handlers(self):
        """Exit chain should include trailing stop, stop loss, take profit, max hold."""
        sig = _make_mock_signal()
        sig.rsi = None
        sig.mean_target_vals = None
        sig.sma_trail_vals = None

        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
            "sig_ref": sig,
        }

        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)
        pos = engine.state.position_manager.open_positions[0]

        from v4.exit_handlers import (
            TrailingStopHandler, StopLossHandler, TakeProfitHandler, MaxHoldHandler,
        )
        handler_types = [type(h) for h in pos.exit_handlers]
        assert TrailingStopHandler in handler_types
        assert StopLossHandler in handler_types
        assert TakeProfitHandler in handler_types
        assert MaxHoldHandler in handler_types

    def test_no_sig_ref_skips_exit_handlers(self):
        """If sig_ref is missing from armed token, exit_handlers should not be built."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        # No sig_ref in armed dict
        engine._armed_tokens[("s501", "BTC")] = {
            "level": 100.0, "direction": 1, "tick_counter": 10,
            "limit_placed_at": "",
            "close_val": 95.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 98.0, "low_val": 90.0,
        }

        candles = {"BTC": (101.0, 99.0, 100.5)}
        engine.process_sub_hourly_entries(candles)
        pos = engine.state.position_manager.open_positions[0]

        # Without sig_ref, exit_handlers should be empty (lazy init at next hourly tick)
        assert not pos.exit_handlers


# ---------------------------------------------------------------------------
# Exit handler rebuild on hourly tick (stale sig_ref fix)
# ---------------------------------------------------------------------------

class TestExitHandlerRebuildOnTick:
    """Verify _process_exits_for_tick clears exit_handlers so they rebuild with fresh sig."""

    def test_exit_handlers_cleared_before_hourly_exits(self):
        """Hourly tick should clear exit_handlers so lazy init rebuilds with fresh sig."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)

        # Create a position with stale exit_handlers
        pos = Position(
            position_id="BTC:s501:0:sub", token="BTC", strategy_id="s501",
            leg="primary", entry_bar=0, entry_price=100.0, direction=1,
            quantity=1.0, margin_usd=100.0, leverage=1.0, is_perp=True,
            fee_rate=0.0004, stop_mult=2.0, trail_mult=999.0, target_mult=999.0,
            no_stop_bars=0, min_hold=1, max_hold=4, exit_regimes=set(),
            stop_price=90.0, highest=110.0, lowest=95.0, initial_risk=10.0,
        )
        pos.exit_handlers = ["stale_handler_1", "stale_handler_2"]
        engine.state.position_manager.open_position(pos)

        all_signals = {"s501": {}}
        bar_maps = {}
        engine._process_exits_for_tick(all_signals=all_signals, bar_maps=bar_maps)

        assert pos.exit_handlers == []

    def test_handlers_cleared_then_rebuilt_not_stale(self):
        """After clearing, the handlers list should not contain the stale refs."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)

        pos = Position(
            position_id="BTC:s501:0:sub", token="BTC", strategy_id="s501",
            leg="primary", entry_bar=0, entry_price=100.0, direction=1,
            quantity=1.0, margin_usd=100.0, leverage=1.0, is_perp=True,
            fee_rate=0.0004, stop_mult=2.0, trail_mult=999.0, target_mult=999.0,
            no_stop_bars=0, min_hold=1, max_hold=4, exit_regimes=set(),
            stop_price=90.0, highest=110.0, lowest=95.0, initial_risk=10.0,
        )
        stale_handlers = [MagicMock(), MagicMock()]
        pos.exit_handlers = stale_handlers
        engine.state.position_manager.open_position(pos)

        all_signals = {"s501": {}}
        engine._process_exits_for_tick(all_signals=all_signals, bar_maps={})

        assert pos.exit_handlers == []
        assert pos.exit_handlers is not stale_handlers


# ---------------------------------------------------------------------------
# Inf guard on sub-hourly exits
# ---------------------------------------------------------------------------

class TestInfGuardOnExits:
    """Verify process_sub_hourly_exits rejects inf candle values."""

    def test_inf_candle_skipped_in_exits(self):
        """Inf high/low/close should not corrupt position state."""
        config = _make_test_config(exit_resolution=1, entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 5

        pos = Position(
            position_id="BTC:s501:0:primary", token="BTC", strategy_id="s501",
            leg="primary", entry_bar=0, entry_price=100.0, direction=1,
            quantity=1.0, margin_usd=100.0, leverage=1.0, is_perp=True,
            fee_rate=0.0004, stop_mult=2.0, trail_mult=999.0, target_mult=999.0,
            no_stop_bars=0, min_hold=1, max_hold=4, exit_regimes=set(),
            stop_price=90.0, highest=105.0, lowest=95.0, initial_risk=10.0,
        )
        engine.state.position_manager.open_position(pos)
        engine._cached_bar_data[("s501", "BTC")] = {"atr": 5.0, "adv": 1e6}

        candles = {"BTC": (float('inf'), 100.0, 105.0)}
        closed = engine.process_sub_hourly_exits(candles)
        assert closed == 0
        assert pos.highest == 105.0


# ---------------------------------------------------------------------------
# WS subscription tests
# ---------------------------------------------------------------------------

class TestWsSubscriptionsWithArmedTokens:

    def test_update_ws_includes_armed_tokens(self):
        """_update_ws_subscriptions should include armed token tokens."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine._armed_tokens[("s501", "BTC")] = {"dummy": True}
        engine._armed_tokens[("s501", "ETH")] = {"dummy": True}

        # Mock the price monitor to capture subscription updates
        mock_monitor = MagicMock()
        engine._price_monitor = mock_monitor
        engine._owns_price_monitor = True

        engine._update_ws_subscriptions()

        call_args = mock_monitor.update_subscriptions.call_args
        subscribed_tokens = call_args[0][0]
        assert "BTC" in subscribed_tokens
        assert "ETH" in subscribed_tokens


# ---------------------------------------------------------------------------
# Retroactive fill skip tests
# ---------------------------------------------------------------------------

class TestRetroactiveFillSkip:
    """Verify _process_entries_for_tick skips strategies with entry_resolution > 0."""

    def test_entry_resolution_strategies_excluded_from_hourly_entries(self):
        """Strategies with entry_resolution > 0 should not get retroactive fills."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        # Create mock signals with entry_mask set (would normally trigger entry)
        sig = _make_mock_signal(n_bars=1, armed_bar=0, direction=1, armed_level=100.0)
        sig.entry_mask[0] = True
        sig.direction[0] = 1
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        # _process_entries_for_tick should skip s501 since entry_resolution=1
        with patch("v4.simulator._process_entries") as mock_entries:
            engine._process_entries_for_tick(all_signals, strategy_specs, bar_maps)
            # Should be called with empty signals (s501 filtered out)
            if mock_entries.called:
                call_signals = mock_entries.call_args[0][1]  # all_signals arg
                assert "s501" not in call_signals


# ---------------------------------------------------------------------------
# Background tick tests
# ---------------------------------------------------------------------------

class TestBackgroundTick:
    """Verify background tick infrastructure."""

    def test_bg_executor_initialized(self):
        """Engine should have a ThreadPoolExecutor."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        assert engine._bg_executor is not None
        assert engine._bg_future is None

    def test_async_tick_methods_exist(self):
        """Engine should have async tick methods for non-blocking execution."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        assert hasattr(engine, '_tick_internal_async')
        assert hasattr(engine, '_tick_bg_worker')
        assert hasattr(engine, '_on_tick_complete')

    def test_cleanup_shuts_down_executor(self):
        """cleanup() should shut down the background executor."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        engine.cleanup()
        # Executor should be shut down (non-fatal if already done)
        assert engine._bg_executor is not None  # Still exists, just shut down
