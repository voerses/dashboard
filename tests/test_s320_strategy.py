"""Acceptance tests for s320_v3_momentum_overlays strategy (AC8-AC17).

These tests verify the strategy file, its signal logic, position sizing,
rebalance schedule, regime filtering, and performance. Uses mock
StrategyContext objects with controlled data for deterministic testing.
"""

import os
import sys
import time
import importlib
import importlib.util
import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock

# Ensure v4/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'v4'))
from engine import (
    StrategyContext, StrategyResult, MarketType,
    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
STRATEGY_FILE = os.path.join(PROJECT_ROOT, 'strategies', 's320_v3_momentum_overlays.py')


def _load_strategy_module():
    """Load the s320 strategy module directly."""
    v4_dir = os.path.join(PROJECT_ROOT, 'v4')
    if v4_dir not in sys.path:
        sys.path.insert(0, v4_dir)
    spec = importlib.util.spec_from_file_location(
        's320_v3_momentum_overlays', STRATEGY_FILE,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_mock_ctx(n=5000, ticker='BTC'):
    """Build a mock StrategyContext with controlled data for unit testing."""
    ctx = MagicMock(spec=StrategyContext)
    ctx.ticker = ticker

    close_1h = np.random.RandomState(42).uniform(30000, 70000, n).astype(np.float64)
    ctx.ind_1h = {'close': close_1h}

    # Daily bars (n // 24)
    n_d = n // 24
    close_d = close_1h[::24][:n_d].astype(np.float64)
    ema_20 = pd.Series(close_d).ewm(span=20, adjust=False).mean().values
    ema_50 = pd.Series(close_d).ewm(span=50, adjust=False).mean().values
    ctx.ind_d = {'close': close_d, 'ema_20': ema_20, 'ema_50': ema_50}
    ctx.idx_d = pd.date_range('2020-01-01', periods=n_d, freq='D')
    ctx.idx_1h = pd.date_range('2020-01-01', periods=n, freq='h')
    ctx.regime_1h = np.full(n, RANGE, dtype=np.int8)  # All non-crisis
    ctx.liquidity_mask = np.ones(n, dtype=bool)

    ctx.custom = {
        'pos_mult': np.ones(n, dtype=np.float64),
        'vrp_mult': np.ones(n, dtype=np.float64),
        'pos_z': np.zeros(n, dtype=np.float64),
        'vrp_z': np.zeros(n, dtype=np.float64),
    }

    # Mock align_daily_to_1h
    def align_daily_to_1h(daily_vals):
        s = pd.Series(daily_vals, index=ctx.idx_d[:len(daily_vals)])
        return s.reindex(ctx.idx_1h, method='ffill').values
    ctx.align_daily_to_1h = align_daily_to_1h

    return ctx


# ===========================================================================
# AC8: Strategy exists and is callable
# ===========================================================================

class TestStrategyExists:
    """AC8: s320 strategy file exists, exports a strategy function, and
    returns a StrategyResult with the correct name."""

    def test_strategy_file_exists(self):
        assert os.path.isfile(STRATEGY_FILE), (
            f"Strategy file not found: {STRATEGY_FILE}"
        )

    def test_strategy_function_exported(self):
        mod = _load_strategy_module()
        assert hasattr(mod, 'strategy'), "Module must export a 'strategy' function"
        assert callable(mod.strategy), "'strategy' must be callable"

    def test_strategy_returns_strategy_result(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx()
        result = mod.strategy(ctx)
        assert isinstance(result, StrategyResult), (
            f"Expected StrategyResult, got {type(result)}"
        )

    def test_strategy_result_name(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx()
        result = mod.strategy(ctx)
        assert result.name == 'v3_momentum_overlays', (
            f"Expected name 'v3_momentum_overlays', got '{result.name}'"
        )


# ===========================================================================
# AC9: Non-BTC returns all-False entry_mask
# ===========================================================================

class TestNonBTCFilter:
    """AC9: Non-BTC tokens get an all-False entry mask."""

    def test_eth_entry_mask_all_false(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx(ticker='ETH')
        result = mod.strategy(ctx)
        n = len(ctx.ind_1h['close'])
        assert result.entry_mask.dtype == bool
        assert np.all(~result.entry_mask), "entry_mask should be all False for ETH"
        assert len(result.entry_mask) == n

    def test_sol_entry_mask_all_false(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx(ticker='SOL')
        result = mod.strategy(ctx)
        assert np.all(~result.entry_mask), "entry_mask should be all False for SOL"

    def test_unknown_token_entry_mask_all_false(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx(ticker='DOGE')
        result = mod.strategy(ctx)
        assert np.all(~result.entry_mask), "entry_mask should be all False for DOGE"


# ===========================================================================
# AC10: Base signal uses pre-computed EMAs
# ===========================================================================

class TestBaseSignal:
    """AC10: Base signal is 1.0 when ema_20 > ema_50 on daily bars,
    0.0 otherwise."""

    def test_base_signal_long(self):
        """When ema_20 > ema_50 everywhere, base signal should be 1.0."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        # Force ema_20 > ema_50 for all daily bars
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        result = mod.strategy(ctx)
        # The base signal should be bullish; with default mults=1.0,
        # the final position should be > 0 at rebalance points after warmup
        # Check that at least some entries exist
        assert result.entry_mask.sum() > 0, (
            "With bullish EMA cross, there should be entries"
        )

    def test_base_signal_flat(self):
        """When ema_20 <= ema_50 everywhere, base signal should be 0.0."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 40000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        result = mod.strategy(ctx)
        # With bearish EMA, base signal is 0, so no entries
        assert result.entry_mask.sum() == 0, (
            "With bearish EMA cross, there should be no entries"
        )


# ===========================================================================
# AC11: Final position computation
# ===========================================================================

class TestFinalPositionComputation:
    """AC11: final = clip(base * pos_mult * vrp_mult, 0.0, 1.5)"""

    def test_clip_upper_bound(self):
        """base=1.0, pos_mult=1.3, vrp_mult=1.3 -> clip(1.69, 0, 1.5) = 1.5"""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        # Force bullish EMA (base=1.0)
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        # Set multipliers to 1.3 each
        ctx.custom['pos_mult'] = np.full(n, 1.3, dtype=np.float64)
        ctx.custom['vrp_mult'] = np.full(n, 1.3, dtype=np.float64)
        result = mod.strategy(ctx)
        # size_multiplier at entry points should be 1.5 (clipped from 1.69)
        if result.size_multiplier is not None and isinstance(result.size_multiplier, np.ndarray):
            entry_sizes = result.size_multiplier[result.entry_mask]
            if len(entry_sizes) > 0:
                assert np.allclose(entry_sizes, 1.5, atol=0.01), (
                    f"Expected clipped size_multiplier = 1.5, got {entry_sizes[:5]}"
                )

    def test_base_zero_means_no_position(self):
        """When base signal is 0 (bearish), final position is 0 regardless of mults."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 40000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        ctx.custom['pos_mult'] = np.full(n, 1.5, dtype=np.float64)
        ctx.custom['vrp_mult'] = np.full(n, 1.3, dtype=np.float64)
        result = mod.strategy(ctx)
        assert result.entry_mask.sum() == 0, (
            "No entries when base signal is 0 (bearish EMA)"
        )


# ===========================================================================
# AC12: Weekly rebalance
# ===========================================================================

class TestWeeklyRebalance:
    """AC12: Entry_mask is only True at 168-bar intervals (after warmup at
    2160 bars). Entry_mask[:2160] is all False."""

    def test_warmup_period_no_entries(self):
        """No entries during the first 2160 bars (warmup)."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        result = mod.strategy(ctx)
        warmup = 2160  # 90 days * 24 hours
        assert np.all(~result.entry_mask[:warmup]), (
            "entry_mask[:2160] should be all False (warmup period)"
        )

    def test_entries_at_168_bar_intervals(self):
        """After warmup, rebalance windows START at 168-bar intervals.

        AC12 says entry_mask is True for the *duration* of each rebalance
        window (up to 168 consecutive bars). This test verifies the block
        start positions are exactly 168 bars apart.
        """
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        result = mod.strategy(ctx)
        entry_indices = np.where(result.entry_mask)[0]
        assert len(entry_indices) > 0, "Expected some entries with bullish EMA"
        # Find the start of each contiguous block of True entries
        block_starts = [entry_indices[0]]
        for i in range(1, len(entry_indices)):
            if entry_indices[i] != entry_indices[i - 1] + 1:
                block_starts.append(entry_indices[i])
        block_starts = np.array(block_starts)
        if len(block_starts) >= 2:
            diffs = np.diff(block_starts)
            assert np.all(diffs == 168), (
                f"Rebalance window starts should be 168 bars apart, "
                f"got unique diffs: {np.unique(diffs)}"
            )


# ===========================================================================
# AC13: CRISIS regime filter
# ===========================================================================

class TestCrisisRegimeFilter:
    """AC13: When ctx.regime_1h == CRISIS at a bar, the final position is 0."""

    def test_crisis_bars_no_entry(self):
        """Bars in CRISIS regime should have entry_mask = False."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        # Set all bars to CRISIS regime
        ctx.regime_1h = np.full(n, CRISIS, dtype=np.int8)
        result = mod.strategy(ctx)
        assert result.entry_mask.sum() == 0, (
            "No entries should occur during CRISIS regime"
        )

    def test_mixed_regime_crisis_bars_filtered(self):
        """Specific CRISIS bars should be filtered even if neighbors are non-CRISIS."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        # Make the expected rebalance bars CRISIS
        warmup = 2160
        for i in range(warmup, n, 168):
            ctx.regime_1h[i] = CRISIS
        result = mod.strategy(ctx)
        # All rebalance bars are crisis -> no entries
        assert result.entry_mask.sum() == 0, (
            "Rebalance bars in CRISIS should have entry_mask = False"
        )


# ===========================================================================
# AC14: Liquidity mask
# ===========================================================================

class TestLiquidityMask:
    """AC14: When liquidity_mask is provided, entry_mask is AND'd with it."""

    def test_liquidity_mask_filters_entries(self):
        """Setting liquidity_mask to False at entry bars should suppress entries."""
        mod = _load_strategy_module()
        n = 5000
        ctx = make_mock_ctx(n=n)
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        # First run with all-True liquidity to find entry bars
        ctx.liquidity_mask = np.ones(n, dtype=bool)
        result_all_liquid = mod.strategy(ctx)
        n_entries_all = result_all_liquid.entry_mask.sum()

        # Now set liquidity_mask to False everywhere
        ctx.liquidity_mask = np.zeros(n, dtype=bool)
        result_no_liquid = mod.strategy(ctx)
        assert result_no_liquid.entry_mask.sum() == 0, (
            "All entries should be filtered when liquidity_mask is all False"
        )
        # Partial filter: only allow first entry
        if n_entries_all > 0:
            ctx.liquidity_mask = np.zeros(n, dtype=bool)
            first_entry = np.where(result_all_liquid.entry_mask)[0][0]
            ctx.liquidity_mask[first_entry] = True
            result_partial = mod.strategy(ctx)
            assert result_partial.entry_mask.sum() <= 1


# ===========================================================================
# AC15: StrategyResult fields
# ===========================================================================

class TestStrategyResultFields:
    """AC15: Verify specific field values on the StrategyResult."""

    @pytest.fixture(autouse=True)
    def run_strategy(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx()
        self.result = mod.strategy(ctx)

    def test_stop_mult(self):
        assert self.result.stop_mult == 99.0

    def test_trail_mult(self):
        assert self.result.trail_mult == 99.0

    def test_target_mult(self):
        assert self.result.target_mult == 999.0

    def test_max_hold(self):
        assert self.result.max_hold == 168

    def test_min_hold(self):
        assert self.result.min_hold == 24

    def test_exit_regimes_contains_crisis(self):
        assert CRISIS in self.result.exit_regimes, (
            f"exit_regimes should contain CRISIS, got {self.result.exit_regimes}"
        )

    def test_market_type_spot(self):
        assert self.result.market_type == MarketType.SPOT

    def test_leverage(self):
        assert self.result.leverage == 1.0

    def test_breakeven_atr_disabled(self):
        """breakeven_atr must be 0.0 — default of 0.5 causes premature stop-outs."""
        assert self.result.breakeven_atr == 0.0, (
            f"breakeven_atr should be 0.0 (disabled), got {self.result.breakeven_atr}. "
            "A non-zero value triggers the breakeven ratchet which moves stops to entry "
            "price within 1-2 bars, destroying the weekly-hold design."
        )

    def test_breakeven_atr_disabled_non_btc(self):
        """Non-BTC early return must also set breakeven_atr=0.0."""
        mod = _load_strategy_module()
        ctx = make_mock_ctx()
        ctx.ticker = 'ETH'
        result = mod.strategy(ctx)
        assert result.breakeven_atr == 0.0, (
            f"Non-BTC breakeven_atr should be 0.0, got {result.breakeven_atr}"
        )


# ===========================================================================
# AC16: Size multiplier and conviction
# ===========================================================================

class TestSizeMultiplierAndConviction:
    """AC16: size_multiplier is a float array in [0, 1.5],
    conviction_score is a float array in [0, 1],
    conviction = size_multiplier / 1.5."""

    @pytest.fixture(autouse=True)
    def run_strategy(self):
        mod = _load_strategy_module()
        ctx = make_mock_ctx()
        # Set bullish EMA for non-trivial output
        n = 5000
        n_d = n // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)
        self.result = mod.strategy(ctx)

    def test_size_multiplier_is_array(self):
        sm = self.result.size_multiplier
        assert isinstance(sm, np.ndarray), (
            f"size_multiplier should be np.ndarray, got {type(sm)}"
        )

    def test_size_multiplier_range(self):
        sm = self.result.size_multiplier
        assert np.all(sm >= 0.0), "size_multiplier values must be >= 0"
        assert np.all(sm <= 1.5), "size_multiplier values must be <= 1.5"

    def test_conviction_score_is_array(self):
        cs = self.result.conviction_score
        assert cs is not None, "conviction_score should not be None"
        assert isinstance(cs, np.ndarray), (
            f"conviction_score should be np.ndarray, got {type(cs)}"
        )

    def test_conviction_score_range(self):
        cs = self.result.conviction_score
        assert np.all(cs >= 0.0), "conviction_score values must be >= 0"
        assert np.all(cs <= 1.0), "conviction_score values must be <= 1"

    def test_conviction_equals_size_div_1_5(self):
        """conviction_score = size_multiplier / 1.5"""
        sm = self.result.size_multiplier
        cs = self.result.conviction_score
        expected = sm / 1.5
        assert np.allclose(cs, expected, atol=1e-10), (
            "conviction_score should equal size_multiplier / 1.5"
        )


# ===========================================================================
# AC17: Performance
# ===========================================================================

class TestPerformance:
    """AC17: Strategy call (with pre-populated ctx.custom) takes < 1ms."""

    def test_strategy_call_under_1ms(self):
        """Strategy execution should average under 1ms over 100 iterations."""
        mod = _load_strategy_module()
        ctx = make_mock_ctx(n=5000)
        n_d = 5000 // 24
        ctx.ind_d['ema_20'] = np.full(n_d, 60000.0)
        ctx.ind_d['ema_50'] = np.full(n_d, 50000.0)

        # Warm up
        mod.strategy(ctx)

        # Benchmark
        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            mod.strategy(ctx)
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / iterations) * 1000

        assert avg_ms < 1.0, (
            f"Strategy call took {avg_ms:.3f}ms average — must be < 1ms"
        )
