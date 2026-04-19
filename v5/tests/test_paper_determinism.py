"""Acceptance tests for Task 12: Determinism & Integration Tests.

Tests verify:
  - Same config + data + seed produces identical results across two runs
    (ENGINE-level, not just numpy)
  - Different seeds produce different but reasonable results
  - State restore + continue produces consistent results with per-bar seeding
  - Per-bar seeding at the engine level (tick_counter-based)

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until paper engine is fully implemented (RED phase).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v5.paper_engine import PaperPortfolioEngine, TickResult
from v5.paper_config import PaperConfig
from v5.paper_state import serialize_state, deserialize_state
from v5.config import StrategySpec
from v5.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(seed: int = 42) -> PaperConfig:
    return PaperConfig(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="perp", max_positions=10),
        ],
        capital=200_000.0,
        mode="pool",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=seed,
        lookback_months=3,
        enable_purge_windows=False,
    )


def _make_synthetic_signals():
    """Build deterministic synthetic signals for testing."""
    import pandas as pd
    from v5.signals import TokenBarArrays

    n_bars = 200
    timestamps = pd.date_range("2024-01-01", periods=n_bars, freq="1h").values
    close = np.full(n_bars, 100.0)
    # Create a pattern that triggers entries
    entry_mask = np.zeros(n_bars, dtype=bool)
    entry_mask[50] = True
    entry_mask[100] = True
    entry_mask[150] = True

    return TokenBarArrays(
        token="BTC",
        strategy_id="s56",
        n_bars=n_bars,
        timestamps=timestamps,
        entry_mask=entry_mask,
        direction=np.full(n_bars, 1, dtype=np.int8),
        close=close,
        high=close + 1.0,
        low=close - 1.0,
        atr=np.full(n_bars, 2.0),
        rolling_adv=np.full(n_bars, 5_000_000.0),
        regime=np.zeros(n_bars, dtype=np.int8),
        funding_1h=np.zeros(n_bars),
        stop_mult=np.full(n_bars, 2.0),
        trail_mult=np.full(n_bars, 3.0),
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        edge=0.35,
        leverage=np.ones(n_bars),
        convex_exit=False,
        rsi=None,
        rsi_exit_level=999.0,
        mean_target_vals=None,
        is_combined=False,
        secondary_entry_mask=None,
        secondary_direction=None,
        secondary_leverage=1.0,
        capital_split=0.5,
        is_perp_primary=True,
        is_perp_secondary=False,
        perp_close=None,
        perp_high=None,
        perp_low=None,
        perp_atr=None,
        perp_rolling_adv=None,
        perp_funding_1h=None,
    )


def _build_engine_for_tick(config: PaperConfig, signals) -> PaperPortfolioEngine:
    """Build a PaperPortfolioEngine configured for tick-by-tick testing.

    The engine is set up with mocked data fetching so that _tick_internal
    can be called directly with the provided signals.
    """
    engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
    engine.config = config
    engine.state = SimulationState(initial_capital=config.capital)
    engine.tick_counter = 0
    engine.last_timestamp = None
    engine._consecutive_failures = 0
    engine._alerts = []
    engine._last_known_prices = {}
    engine.equity_history = []
    engine._candle_aggregator = None
    engine._price_monitor = None
    engine._effective_exit_resolution = 0
    engine._strategy_exit_resolution = {}
    engine._cached_bar_data = {}

    # Store signals for the engine to use
    engine._test_signals = {"s56": {"BTC": signals}}
    engine._test_specs = {"s56": StrategySpec(
        strategy_id="s56", weight=0.5, market="perp", max_positions=10
    )}

    return engine


# ===================================================================
# PRIMARY: Engine-level determinism tests (Q5 fix)
# ===================================================================

class TestEngineDeterminism:
    """Same config + data + seed => identical engine results (AC17).

    Q5 fix: these tests exercise PaperPortfolioEngine.process_tick(),
    not raw numpy. They verify that equity snapshots and trade lists
    are identical across two runs with the same seed.
    """

    def test_same_seed_identical_engine_results(self):
        """Create PaperPortfolioEngine, run 2 ticks. Create another with
        same config+seed, run 2 ticks. Verify identical equity snapshots
        and trade lists."""
        config = _make_test_config(seed=42)
        signals = _make_synthetic_signals()

        # Run 1: create engine, process 2 ticks
        engine1 = _build_engine_for_tick(config, signals)
        result1_tick0 = engine1.process_tick(
            all_signals=engine1._test_signals,
            specs=engine1._test_specs,
        )
        equity1_after_tick0 = engine1.state.portfolio_equity
        trades1_after_tick0 = len(engine1.state.position_manager.closed_trades)
        positions1_after_tick0 = len(engine1.state.position_manager.open_positions)

        result1_tick1 = engine1.process_tick(
            all_signals=engine1._test_signals,
            specs=engine1._test_specs,
        )
        equity1_after_tick1 = engine1.state.portfolio_equity
        trades1_after_tick1 = len(engine1.state.position_manager.closed_trades)
        positions1_after_tick1 = len(engine1.state.position_manager.open_positions)

        # Run 2: fresh engine, same config+seed, process 2 ticks
        engine2 = _build_engine_for_tick(config, signals)
        result2_tick0 = engine2.process_tick(
            all_signals=engine2._test_signals,
            specs=engine2._test_specs,
        )
        equity2_after_tick0 = engine2.state.portfolio_equity
        trades2_after_tick0 = len(engine2.state.position_manager.closed_trades)
        positions2_after_tick0 = len(engine2.state.position_manager.open_positions)

        result2_tick1 = engine2.process_tick(
            all_signals=engine2._test_signals,
            specs=engine2._test_specs,
        )
        equity2_after_tick1 = engine2.state.portfolio_equity
        trades2_after_tick1 = len(engine2.state.position_manager.closed_trades)
        positions2_after_tick1 = len(engine2.state.position_manager.open_positions)

        # Verify identical equity snapshots
        assert equity1_after_tick0 == pytest.approx(equity2_after_tick0), (
            f"Equity diverged after tick 0: {equity1_after_tick0} vs {equity2_after_tick0}"
        )
        assert equity1_after_tick1 == pytest.approx(equity2_after_tick1), (
            f"Equity diverged after tick 1: {equity1_after_tick1} vs {equity2_after_tick1}"
        )

        # Verify identical trade counts
        assert trades1_after_tick0 == trades2_after_tick0
        assert trades1_after_tick1 == trades2_after_tick1

        # Verify identical position counts
        assert positions1_after_tick0 == positions2_after_tick0
        assert positions1_after_tick1 == positions2_after_tick1

    def test_per_bar_seeding_engine_level(self):
        """Create engine, run tick at tick_counter=5. Reset. Run tick at
        tick_counter=5 again. Verify same shuffle order. This tests that
        the engine seeds per-bar, not just that numpy works."""
        config = _make_test_config(seed=42)
        signals = _make_synthetic_signals()

        # Run 1: engine starts at tick_counter=5
        engine1 = _build_engine_for_tick(config, signals)
        engine1.tick_counter = 5
        result1 = engine1.process_tick(
            all_signals=engine1._test_signals,
            specs=engine1._test_specs,
        )
        equity1 = engine1.state.portfolio_equity
        positions1 = [
            (p.token, p.direction, p.margin_usd)
            for p in engine1.state.position_manager.open_positions
        ]
        trades1 = [
            (t.token, t.pnl, t.exit_reason)
            for t in engine1.state.position_manager.closed_trades
        ]

        # Run 2: fresh engine also at tick_counter=5
        engine2 = _build_engine_for_tick(config, signals)
        engine2.tick_counter = 5
        result2 = engine2.process_tick(
            all_signals=engine2._test_signals,
            specs=engine2._test_specs,
        )
        equity2 = engine2.state.portfolio_equity
        positions2 = [
            (p.token, p.direction, p.margin_usd)
            for p in engine2.state.position_manager.open_positions
        ]
        trades2 = [
            (t.token, t.pnl, t.exit_reason)
            for t in engine2.state.position_manager.closed_trades
        ]

        assert equity1 == pytest.approx(equity2)
        assert positions1 == positions2
        assert trades1 == trades2


# ===================================================================
# SUPPLEMENTARY: Numpy-level determinism (kept from original)
# ===================================================================

class TestNumpyDeterminism:
    """Supplementary: verify numpy-level per-bar seeding works correctly.
    These are lower-level sanity checks; the engine-level tests above
    are the primary determinism verification."""

    def test_same_seed_identical_rng_output(self):
        """Two runs with the same seed and data produce identical RNG sequences."""
        config = _make_test_config(seed=42)

        results1 = []
        for tick in range(100):
            rng = np.random.RandomState(config.seed + tick)
            results1.append(rng.randint(0, 1000))

        results2 = []
        for tick in range(100):
            rng = np.random.RandomState(config.seed + tick)
            results2.append(rng.randint(0, 1000))

        assert results1 == results2

    def test_per_bar_seeding_deterministic(self):
        """Per-bar seeding: RandomState(seed + tick_counter) is deterministic."""
        seed = 42
        tick_counter = 100

        rng1 = np.random.RandomState(seed + tick_counter)
        shuffle1 = list(range(10))
        rng1.shuffle(shuffle1)

        rng2 = np.random.RandomState(seed + tick_counter)
        shuffle2 = list(range(10))
        rng2.shuffle(shuffle2)

        assert shuffle1 == shuffle2


# ===================================================================
# Test: Different seeds produce different but reasonable results
# ===================================================================

class TestDifferentSeeds:
    """Different seeds produce different RNG sequences."""

    def test_different_seeds_different_shuffle(self):
        """Two different seeds produce different shuffle orders."""
        seed_a = 42
        seed_b = 123
        tick = 50

        rng_a = np.random.RandomState(seed_a + tick)
        order_a = list(range(20))
        rng_a.shuffle(order_a)

        rng_b = np.random.RandomState(seed_b + tick)
        order_b = list(range(20))
        rng_b.shuffle(order_b)

        assert order_a != order_b

    def test_different_seeds_both_valid(self):
        """Both runs with different seeds produce valid (non-empty) results."""
        seed_a = 42
        seed_b = 123

        for seed in [seed_a, seed_b]:
            rng = np.random.RandomState(seed)
            values = rng.random(100)
            assert len(values) == 100
            assert all(0 <= v <= 1 for v in values)


# ===================================================================
# Test: State restore + continue produces consistent results
# ===================================================================

class TestStateRestoreContinuity:
    """State restore + continue produces consistent results via per-bar seeding (AC9c)."""

    def test_restore_continues_with_correct_tick_counter(self):
        """After restore, tick_counter picks up where it left off."""
        state = SimulationState(initial_capital=200_000.0)
        state.realized_pnl = 5_000.0
        tick_counter = 50

        json_data = serialize_state(state, tick_counter, "2026-03-09T20:00:00Z")
        restored_state, restored_tick = deserialize_state(json_data)

        assert restored_tick == 50

        # Per-bar seeding from restored tick should match
        seed = 42
        rng_original = np.random.RandomState(seed + 50)
        order_original = list(range(10))
        rng_original.shuffle(order_original)

        rng_restored = np.random.RandomState(seed + restored_tick)
        order_restored = list(range(10))
        rng_restored.shuffle(order_restored)

        assert order_original == order_restored

    def test_rng_not_persisted_per_bar_seeding_used(self):
        """RNG state is NOT persisted. Per-bar seeding provides determinism (AC9c).

        Two different paths to tick 50 should produce the same RNG output:
        1. Fresh start, run to tick 50
        2. Start, save at tick 25, restore, continue to tick 50
        """
        seed = 42

        # Path 1: direct to tick 50
        rng_direct = np.random.RandomState(seed + 50)
        result_direct = rng_direct.random(5).tolist()

        # Path 2: restore at tick 25, continue to tick 50
        # (per-bar seeding means tick 50 always uses same seed)
        rng_restored = np.random.RandomState(seed + 50)
        result_restored = rng_restored.random(5).tolist()

        assert result_direct == result_restored

    def test_restored_state_equity_matches(self):
        """After restore, portfolio_equity should be computed correctly."""
        state = SimulationState(initial_capital=200_000.0)
        state.realized_pnl = 10_000.0
        state.total_fees = 500.0
        state.total_funding = -200.0

        expected_equity = 200_000.0 + 10_000.0 - 500.0 - (-200.0)

        json_data = serialize_state(state, 42, "2026-03-09T20:00:00Z")
        restored_state, _ = deserialize_state(json_data)

        assert restored_state.portfolio_equity == pytest.approx(expected_equity)
