"""Tests for integrated sub-hourly entry resolution in the paper trader.

Tests verify:
  - PaperPortfolioEngine initializes entry resolution tracking
  - _cache_entry_candidates populates cache from signals with unfilled limits
  - _cache_entry_candidates skips when hourly bar fills the limit
  - _cache_entry_candidates skips combined strategies
  - _cache_entry_candidates skips when position already open
  - process_sub_hourly_entries opens position on 1m cross
  - process_sub_hourly_entries skips when no cross
  - process_sub_hourly_entries respects portfolio position limit
  - process_sub_hourly_entries is no-op when entry_resolution=0
  - Candidate expires at next hourly tick (fresh cache)
  - _update_ws_subscriptions includes candidate tokens

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
    entry_bar: int = 9,
    direction: int = 1,
    limit_price: float = 100.0,
    close_val: float = 105.0,
    high_val: float = 110.0,
    low_val: float = 102.0,
    atr_val: float = 5.0,
    adv_val: float = 1e6,
    is_combined: bool = False,
):
    """Build a mock TokenSignals for entry resolution tests."""
    from v4.signals import TokenSignals
    sig = MagicMock(spec=TokenSignals)
    sig.n_bars = n_bars
    sig.is_combined = is_combined

    # Arrays
    sig.entry_mask = np.zeros(n_bars, dtype=bool)
    sig.entry_mask[entry_bar] = True
    sig.direction = np.zeros(n_bars, dtype=np.int8)
    sig.direction[entry_bar] = direction
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

    # Entry limit price array
    sig.entry_limit_price = np.full(n_bars, np.nan)
    sig.entry_limit_price[entry_bar] = limit_price

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
        assert engine._pending_entry_candidates == {}

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
# _cache_entry_candidates tests
# ---------------------------------------------------------------------------

class TestCacheEntryCandidates:

    def test_cache_populates_unfilled_candidate(self):
        """Unfilled limit should be cached for sub-hourly resolution."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(
            n_bars=1, entry_bar=0, direction=1,
            limit_price=100.0,  # BB level
            low_val=102.0,      # low=102 > limit=100 → didn't fill
            high_val=110.0,
            close_val=105.0,
        )
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") in engine._pending_entry_candidates
        cand = engine._pending_entry_candidates[("s501", "BTC")]
        assert cand["limit_price"] == 100.0
        assert cand["direction"] == 1
        assert cand["close_val"] == 105.0
        assert cand["sig_ref"] is sig  # TokenSignals ref for build_exit_chain

    def test_cache_skips_filled_limit_long(self):
        """Long limit that filled on hourly bar should NOT be cached."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(
            n_bars=1, entry_bar=0, direction=1,
            limit_price=100.0,
            low_val=99.0,  # low=99 <= limit=100 → filled
        )
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._pending_entry_candidates

    def test_cache_skips_filled_limit_short(self):
        """Short limit that filled on hourly bar should NOT be cached."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(
            n_bars=1, entry_bar=0, direction=-1,
            limit_price=110.0,
            high_val=111.0,  # high=111 >= limit=110 → filled
        )
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._pending_entry_candidates

    def test_cache_skips_no_limit_price(self):
        """Signal without entry_limit_price should NOT be cached."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(n_bars=1, entry_bar=0, direction=1)
        sig.entry_limit_price = None

        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._pending_entry_candidates

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

        sig = _make_mock_signal(n_bars=1, entry_bar=0, direction=1,
                                limit_price=100.0, low_val=102.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._pending_entry_candidates

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

        sig = _make_mock_signal(n_bars=1, entry_bar=0, direction=1,
                                limit_price=100.0, low_val=102.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert ("s501", "BTC") not in engine._pending_entry_candidates

    def test_cache_skips_entry_resolution_zero(self):
        """Strategy with entry_resolution=0 should NOT produce candidates."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        sig = _make_mock_signal(n_bars=1, entry_bar=0, direction=1,
                                limit_price=100.0, low_val=102.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        assert len(engine._pending_entry_candidates) == 0

    def test_cache_clears_on_each_tick(self):
        """Cache should be cleared at the start of each call (fresh per tick)."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 0

        # Manually populate cache
        engine._pending_entry_candidates[("s501", "OLD")] = {"dummy": True}

        sig = _make_mock_signal(n_bars=1, entry_bar=0, direction=1,
                                limit_price=100.0, low_val=102.0)
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.array([0])}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        # Old candidate should be gone
        assert ("s501", "OLD") not in engine._pending_entry_candidates
        # New candidate should be present
        assert ("s501", "BTC") in engine._pending_entry_candidates


# ---------------------------------------------------------------------------
# process_sub_hourly_entries tests
# ---------------------------------------------------------------------------

class TestProcessSubHourlyEntries:

    def _make_engine_with_candidate(self, entry_resolution=1, **config_kw):
        """Create an engine with a single pending entry candidate."""
        config = _make_test_config(entry_resolution=entry_resolution, **config_kw)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10
        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0,
            "direction": 1,
            "tick_counter": 10,
            "close_val": 105.0,
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
            "high_val": 110.0,
            "low_val": 102.0,
        }
        return engine

    def test_cross_detected_opens_position(self):
        """1m candle crossing limit should open a position."""
        engine = self._make_engine_with_candidate()

        # Long candidate with limit=100, 1m close=101 > 100 → cross
        candles = {"BTC": (102.0, 100.5, 101.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1
        assert len(engine.state.position_manager.open_positions) == 1
        pos = engine.state.position_manager.open_positions[0]
        assert pos.token == "BTC"
        assert pos.strategy_id == "s501"
        assert pos.direction == 1
        # Entry price should be close to 1m cross close (101) + slippage
        assert pos.entry_price > 101.0  # slippage added
        assert pos.entry_timestamp  # Should have timestamp set

    def test_no_cross_no_entry(self):
        """1m candle NOT crossing limit should not open a position."""
        engine = self._make_engine_with_candidate()

        # Long candidate with limit=100, 1m close=99.5 <= 100 → no cross
        candles = {"BTC": (100.0, 99.0, 99.5)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0
        assert len(engine.state.position_manager.open_positions) == 0
        # Candidate should still be pending
        assert ("s501", "BTC") in engine._pending_entry_candidates

    def test_cross_removes_candidate(self):
        """Executed candidate should be removed from pending cache."""
        engine = self._make_engine_with_candidate()

        candles = {"BTC": (102.0, 100.5, 101.0)}
        engine.process_sub_hourly_entries(candles)

        assert ("s501", "BTC") not in engine._pending_entry_candidates

    def test_short_cross_opens_position(self):
        """Short 1m cross should open a short position."""
        engine = self._make_engine_with_candidate()
        engine._pending_entry_candidates[("s501", "BTC")]["direction"] = -1
        engine._pending_entry_candidates[("s501", "BTC")]["limit_price"] = 100.0

        # Short candidate with limit=100, 1m close=99 < 100 → cross
        candles = {"BTC": (100.0, 98.0, 99.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1
        pos = engine.state.position_manager.open_positions[0]
        assert pos.direction == -1

    def test_portfolio_limit_blocks_entry(self):
        """Portfolio position limit should prevent sub-hourly entry."""
        engine = self._make_engine_with_candidate(max_portfolio_positions=0)

        candles = {"BTC": (102.0, 100.5, 101.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_strategy_limit_blocks_entry(self):
        """Strategy position limit should prevent sub-hourly entry."""
        engine = self._make_engine_with_candidate(
            strategies=[
                StrategySpec(
                    strategy_id="s501", weight=0.5, market="perp",
                    max_positions=0, entry_resolution=1,
                ),
            ],
        )

        candles = {"BTC": (102.0, 100.5, 101.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_entry_resolution_zero_noop(self):
        """entry_resolution=0 should return 0 immediately."""
        config = _make_test_config(entry_resolution=0)
        engine = PaperPortfolioEngine(config)
        # Manually add candidate (shouldn't be checked)
        engine._pending_entry_candidates[("s501", "BTC")] = {"dummy": True}

        candles = {"BTC": (102.0, 100.5, 101.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0

    def test_empty_candles_noop(self):
        """Empty candles dict should produce no entries."""
        engine = self._make_engine_with_candidate()
        entries = engine.process_sub_hourly_entries({})
        assert entries == 0

    def test_token_not_in_candles_skipped(self):
        """Candidate for token not in candles should be skipped."""
        engine = self._make_engine_with_candidate()

        candles = {"ETH": (2000.0, 1900.0, 1950.0)}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0
        # Candidate still pending
        assert ("s501", "BTC") in engine._pending_entry_candidates

    def test_entry_fee_accounting(self):
        """Entry fee should be tracked in state."""
        engine = self._make_engine_with_candidate()
        initial_fees = engine.state.total_fees

        candles = {"BTC": (102.0, 100.5, 101.0)}
        engine.process_sub_hourly_entries(candles)

        assert engine.state.total_fees > initial_fees

    def test_entry_price_includes_slippage(self):
        """Entry price should include slippage (higher than 1m close for longs)."""
        engine = self._make_engine_with_candidate()

        candles = {"BTC": (102.0, 100.5, 101.0)}
        engine.process_sub_hourly_entries(candles)

        pos = engine.state.position_manager.open_positions[0]
        # Long direction: entry_price = close + slippage > close
        assert pos.entry_price > 101.0

    def test_multiple_candidates_some_cross(self):
        """Multiple candidates: only those with crosses should execute."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        # BTC: long limit=100 (will cross with close=101)
        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": 10,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
        }
        # ETH: long limit=2000 (won't cross with close=1990)
        engine._pending_entry_candidates[("s501", "ETH")] = {
            "limit_price": 2000.0, "direction": 1, "tick_counter": 10,
            "close_val": 2050.0, "atr_val": 50.0, "adv_val": 1e7,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 2100.0, "low_val": 2020.0,
        }

        candles = {
            "BTC": (102.0, 100.5, 101.0),  # crosses limit=100
            "ETH": (2000.0, 1980.0, 1990.0),  # doesn't cross limit=2000
        }
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 1
        assert len(engine.state.position_manager.open_positions) == 1
        assert engine.state.position_manager.open_positions[0].token == "BTC"
        # BTC removed, ETH still pending
        assert ("s501", "BTC") not in engine._pending_entry_candidates
        assert ("s501", "ETH") in engine._pending_entry_candidates


# ---------------------------------------------------------------------------
# NaN candle guard tests
# ---------------------------------------------------------------------------

class TestNaNCangleGuard:

    def _make_engine_with_candidate(self):
        """Create an engine with a single pending entry candidate."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10
        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": 10,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
        }
        return engine

    def test_nan_close_skips_entry(self):
        """NaN candle close should not open a position."""
        engine = self._make_engine_with_candidate()
        candles = {"BTC": (float('nan'), float('nan'), float('nan'))}
        entries = engine.process_sub_hourly_entries(candles)

        assert entries == 0
        assert len(engine.state.position_manager.open_positions) == 0
        # Candidate should remain (not executed, not removed)
        assert ("s501", "BTC") in engine._pending_entry_candidates

    def test_inf_close_skips_entry(self):
        """Inf candle close should not open a position."""
        engine = self._make_engine_with_candidate()
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

    def _make_engine_with_candidate(self, cached_tick=5):
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        # Simulate post-increment: tick_counter is N+1, but candidate was cached at N
        engine.tick_counter = cached_tick + 1
        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": cached_tick,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
        }
        return engine

    def test_entry_bar_uses_cached_tick(self):
        """Position entry_bar should use cached tick_counter, not current."""
        engine = self._make_engine_with_candidate(cached_tick=5)
        candles = {"BTC": (102.0, 100.5, 101.0)}
        engine.process_sub_hourly_entries(candles)

        pos = engine.state.position_manager.open_positions[0]
        assert pos.entry_bar == 5  # cached tick, not engine.tick_counter=6

    def test_position_id_uses_sub_suffix(self):
        """Sub-hourly position ID should use ':sub' suffix for disambiguation."""
        engine = self._make_engine_with_candidate(cached_tick=5)
        candles = {"BTC": (102.0, 100.5, 101.0)}
        engine.process_sub_hourly_entries(candles)

        pos = engine.state.position_manager.open_positions[0]
        assert pos.position_id == "BTC:s501:5:sub"
        assert ":sub" in pos.position_id


# ---------------------------------------------------------------------------
# Double-entry guard tests
# ---------------------------------------------------------------------------

class TestDoubleEntryGuard:

    def test_cache_skips_token_entered_this_tick(self):
        """Should NOT cache a candidate if hourly path already entered this tick."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 5

        # Simulate hourly entry: position with entry_bar == tick_counter
        pos = Position(
            position_id="BTC:s501:5:primary", token="BTC",
            strategy_id="s501", leg="primary", entry_bar=5,
            entry_price=105.0, direction=1, quantity=100.0,
            margin_usd=10000.0, leverage=1.0, is_perp=True,
            fee_rate=0.0005, stop_mult=2.0, trail_mult=999.0,
            target_mult=999.0, no_stop_bars=0, min_hold=1,
            max_hold=4, exit_regimes=set(), convex_exit=False,
            stop_price=95.0, highest=105.0, lowest=105.0,
            initial_risk=10.0,
        )
        engine.state.position_manager.open_position(pos)

        # Configure max_concurrent_per_token=2 so existing position check alone wouldn't block
        config.strategies[0].max_concurrent_per_token = 2

        sig = _make_mock_signal(
            n_bars=10, entry_bar=5, direction=1,
            limit_price=100.0, low_val=102.0,
        )
        all_signals = {"s501": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(10)}
        strategy_specs = {"s501": config.strategies[0]}

        engine._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        # Should NOT cache because hourly already entered this tick
        assert ("s501", "BTC") not in engine._pending_entry_candidates


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

        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": 10,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
        }

        candles = {"BTC": (102.0, 100.5, 101.0)}
        engine.process_sub_hourly_entries(candles)

        assert engine._last_known_prices["BTC"] == 101.0  # Updated to candle close


# ---------------------------------------------------------------------------
# Exit handler init at sub-hourly entry (backtest parity)
# ---------------------------------------------------------------------------

class TestExitHandlerInitAtEntry:
    """Verify build_exit_chain is called immediately after sub-hourly position creation."""

    def test_exit_handlers_populated_on_entry(self):
        """Position created via sub-hourly entry should have exit_handlers built."""
        sig = _make_mock_signal()
        # Set these to None so build_exit_chain doesn't create handlers
        # for features this mock doesn't support
        sig.rsi = None
        sig.mean_target_vals = None
        sig.sma_trail_vals = None

        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": 10,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
            "sig_ref": sig,
        }

        candles = {"BTC": (102.0, 100.5, 101.0)}
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

        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": 10,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
            "sig_ref": sig,
        }

        candles = {"BTC": (102.0, 100.5, 101.0)}
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
        """If sig_ref is missing from candidate, exit_handlers should not be built."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine.tick_counter = 10

        # No sig_ref in candidate dict
        engine._pending_entry_candidates[("s501", "BTC")] = {
            "limit_price": 100.0, "direction": 1, "tick_counter": 10,
            "close_val": 105.0, "atr_val": 5.0, "adv_val": 1e6,
            "leverage": 1.0, "size_multiplier": 1.0, "cap_multiplier": 1.0,
            "is_perp": True, "stop_mult": 2.0, "trail_mult": 999.0,
            "target_mult": 999.0, "no_stop_bars": 0, "min_hold": 1,
            "max_hold": 4, "exit_regimes": set(), "convex_exit": False,
            "edge": 0.15, "max_trade_pct": 0.05,
            "high_val": 110.0, "low_val": 102.0,
        }

        candles = {"BTC": (102.0, 100.5, 101.0)}
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

        # Provide signals with strategy key so _process_exits doesn't KeyError
        # but no token data, so no exits fire. The position's handlers should
        # have been cleared before _process_exits processes it.
        all_signals = {"s501": {}}
        bar_maps = {}
        engine._process_exits_for_tick(all_signals=all_signals, bar_maps=bar_maps)

        # Handlers cleared (BTC has no sig, so _process_exits skips it after
        # the handler clear loop). Verify they're not the stale ones.
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

        # Handlers should be cleared (empty list), not the stale ones
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

        # Feed inf candle — should be skipped entirely
        candles = {"BTC": (float('inf'), 100.0, 105.0)}
        closed = engine.process_sub_hourly_exits(candles)
        assert closed == 0
        # Position highest should NOT be corrupted to inf
        assert pos.highest == 105.0


# ---------------------------------------------------------------------------
# WS subscription tests
# ---------------------------------------------------------------------------

class TestWsSubscriptionsWithCandidates:

    def test_update_ws_includes_candidate_tokens(self):
        """_update_ws_subscriptions should include pending candidate tokens."""
        config = _make_test_config(entry_resolution=1)
        engine = PaperPortfolioEngine(config)
        engine._pending_entry_candidates[("s501", "BTC")] = {"dummy": True}
        engine._pending_entry_candidates[("s501", "ETH")] = {"dummy": True}

        # Mock the price monitor to capture subscription updates
        mock_monitor = MagicMock()
        engine._price_monitor = mock_monitor
        engine._owns_price_monitor = True

        engine._update_ws_subscriptions()

        # Check that update_subscriptions was called with BTC and ETH
        call_args = mock_monitor.update_subscriptions.call_args
        subscribed_tokens = call_args[0][0]
        assert "BTC" in subscribed_tokens
        assert "ETH" in subscribed_tokens
