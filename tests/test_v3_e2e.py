"""End-to-end acceptance test for V3 Momentum Strategy (AC18).

Builds a real Engine context from BTC spot data, verifies plugins populated
ctx.custom, imports and runs the s320 strategy, and validates the full
output pipeline. Also tests non-BTC (ETH) returns zero entries.
"""

import os
import sys
import numpy as np
import pandas as pd
import pytest

# Ensure v4/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'v4'))
from engine import Engine, StrategyContext, StrategyResult, MarketType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
BTC_PARQUET = os.path.join(DATA_DIR, 'spot', '1h_cache', 'BTC_1h.parquet')
ETH_PARQUET = os.path.join(DATA_DIR, 'spot', '1h_cache', 'ETH_1h.parquet')


# ===========================================================================
# AC18: Full E2E test — BTC
# ===========================================================================

class TestE2EBTC:
    """AC18: Full end-to-end test with real BTC data."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Build real BTC context and run strategy."""
        self.eng = Engine(data_dir=DATA_DIR, market='spot')
        df = pd.read_parquet(BTC_PARQUET)
        self.ctx = self.eng._build_context('BTC', df)
        assert self.ctx is not None, "Failed to build BTC context"

        # Import and run strategy
        from strategies.s320_v3_momentum_overlays import strategy
        self.result = strategy(self.ctx)

    def test_pos_mult_in_custom(self):
        """Step 4: pos_mult is in ctx.custom."""
        assert 'pos_mult' in self.ctx.custom

    def test_vrp_mult_in_custom(self):
        """Step 5: vrp_mult is in ctx.custom."""
        assert 'vrp_mult' in self.ctx.custom

    def test_result_is_strategy_result(self):
        """Step 7: Result is a StrategyResult."""
        assert isinstance(self.result, StrategyResult)

    def test_has_trades(self):
        """Step 8: entry_mask.sum() > 0 (has trades)."""
        assert self.result.entry_mask.sum() > 0, (
            "BTC E2E should produce at least one trade entry"
        )

    def test_result_name(self):
        """Step 9: result.name == 'v3_momentum_overlays'."""
        assert self.result.name == 'v3_momentum_overlays'

    def test_market_type_spot(self):
        """Step 10: result.market_type == MarketType.SPOT."""
        assert self.result.market_type == MarketType.SPOT

    def test_size_multiplier_not_none(self):
        """Step 11: result.size_multiplier is not None."""
        assert self.result.size_multiplier is not None

    def test_size_multiplier_range(self):
        """Step 12: size_multiplier values in [0, 1.5]."""
        sm = self.result.size_multiplier
        assert np.all(sm >= 0), "size_multiplier must be >= 0"
        assert np.all(sm <= 1.5), "size_multiplier must be <= 1.5"


# ===========================================================================
# AC18: Full E2E test — Non-BTC (ETH)
# ===========================================================================

class TestE2EETH:
    """AC18: E2E test with non-BTC token — entry_mask should be all False."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Build real ETH context and run strategy."""
        self.eng = Engine(data_dir=DATA_DIR, market='spot')
        df = pd.read_parquet(ETH_PARQUET)
        self.ctx = self.eng._build_context('ETH', df)
        assert self.ctx is not None, "Failed to build ETH context"

        from strategies.s320_v3_momentum_overlays import strategy
        self.result = strategy(self.ctx)

    def test_eth_entry_mask_all_false(self):
        """Non-BTC strategy should produce zero entries."""
        assert self.result.entry_mask.sum() == 0, (
            "ETH should have zero entries for BTC-only strategy"
        )

    def test_eth_result_is_strategy_result(self):
        """ETH result should still be a StrategyResult."""
        assert isinstance(self.result, StrategyResult)

    def test_eth_result_name(self):
        """Name should be consistent regardless of token."""
        assert self.result.name == 'v3_momentum_overlays'
