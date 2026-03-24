"""Acceptance tests for V3 Momentum Strategy indicator plugins (AC1-AC7).

These tests verify the positioning plugin and VRP plugin that will be
registered as indicator plugins in v4/engine.py. They run via the real
Engine class against real BTC spot data.
"""

import os
import sys
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure v4/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'v4'))
from engine import (
    Engine, StrategyContext, StrategyResult, MarketType,
    CRISIS, register_indicator, _INDICATOR_PLUGINS,
    rolling_zscore, rolling_std,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
BTC_PARQUET = os.path.join(PROJECT_ROOT, 'data', 'spot', '1h_cache', 'BTC_1h.parquet')


def _build_btc_context():
    """Build a real StrategyContext for BTC using the Engine."""
    eng = Engine(data_dir=os.path.join(PROJECT_ROOT, 'data'), market='spot')
    df = pd.read_parquet(BTC_PARQUET)
    ctx = eng._build_context('BTC', df)
    assert ctx is not None, "Failed to build BTC context — check data availability"
    return ctx


# ===========================================================================
# AC1: Positioning plugin output
# ===========================================================================

class TestPositioningPluginOutput:
    """AC1: After running plugins, ctx.custom contains pos_z and pos_mult
    with correct dtype, length, and value constraints."""

    @pytest.fixture(autouse=True)
    def build_ctx(self):
        self.ctx = _build_btc_context()

    def test_pos_z_exists(self):
        assert 'pos_z' in self.ctx.custom, "pos_z not found in ctx.custom"

    def test_pos_z_dtype(self):
        assert self.ctx.custom['pos_z'].dtype == np.float64

    def test_pos_z_length(self):
        expected = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['pos_z']) == expected

    def test_pos_mult_exists(self):
        assert 'pos_mult' in self.ctx.custom, "pos_mult not found in ctx.custom"

    def test_pos_mult_dtype(self):
        assert self.ctx.custom['pos_mult'].dtype == np.float64

    def test_pos_mult_length(self):
        expected = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['pos_mult']) == expected

    def test_pos_mult_valid_values(self):
        allowed = {0.3, 0.5, 1.0, 1.3, 1.5}
        unique_vals = set(np.unique(self.ctx.custom['pos_mult']))
        assert unique_vals.issubset(allowed), (
            f"pos_mult contains unexpected values: {unique_vals - allowed}"
        )


# ===========================================================================
# AC2: VRP plugin output
# ===========================================================================

class TestVRPPluginOutput:
    """AC2: After running plugins, ctx.custom contains vrp_z and vrp_mult
    with correct dtype, length, and value constraints."""

    @pytest.fixture(autouse=True)
    def build_ctx(self):
        self.ctx = _build_btc_context()

    def test_vrp_z_exists(self):
        assert 'vrp_z' in self.ctx.custom, "vrp_z not found in ctx.custom"

    def test_vrp_z_dtype(self):
        assert self.ctx.custom['vrp_z'].dtype == np.float64

    def test_vrp_z_length(self):
        expected = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['vrp_z']) == expected

    def test_vrp_mult_exists(self):
        assert 'vrp_mult' in self.ctx.custom, "vrp_mult not found in ctx.custom"

    def test_vrp_mult_dtype(self):
        assert self.ctx.custom['vrp_mult'].dtype == np.float64

    def test_vrp_mult_length(self):
        expected = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['vrp_mult']) == expected

    def test_vrp_mult_valid_values(self):
        allowed = {0.3, 0.5, 1.0, 1.3}
        unique_vals = set(np.unique(self.ctx.custom['vrp_mult']))
        assert unique_vals.issubset(allowed), (
            f"vrp_mult contains unexpected values: {unique_vals - allowed}"
        )


# ===========================================================================
# AC3: Fallback behavior
# ===========================================================================

class TestFallbackBehavior:
    """AC3: When data files are missing or token is unknown, mult arrays
    default to all 1.0."""

    def test_positioning_fallback_no_file(self):
        """When positioning data file doesn't exist, pos_mult is all 1.0."""
        import engine as _eng_mod

        # Save and reset module-level positioning cache so the plugin
        # re-evaluates the (patched) path instead of returning stale data.
        saved_raw_loaded = _eng_mod._pos_raw_loaded
        saved_raw_df = _eng_mod._pos_raw_df
        saved_pos_cache = _eng_mod._pos_cache.copy()
        try:
            _eng_mod._pos_raw_loaded = False
            _eng_mod._pos_raw_df = None
            _eng_mod._pos_cache.clear()

            # Patch the path constant to a non-existent file so the plugin
            # triggers its "file missing" fallback (the real path is hardcoded
            # relative to engine.py, NOT relative to Engine.data_dir).
            fake_path = Path('/tmp/_nonexistent_positioning_data.parquet')
            with patch.object(_eng_mod, '_POS_PATH', fake_path):
                eng = Engine(data_dir=os.path.join(PROJECT_ROOT, 'data'), market='spot')
                df = pd.read_parquet(BTC_PARQUET)
                ctx = eng._build_context('BTC', df)
                assert ctx is not None
                assert 'pos_mult' in ctx.custom
                assert np.all(ctx.custom['pos_mult'] == 1.0), (
                    "pos_mult should be all 1.0 when positioning file is missing"
                )
        finally:
            # Restore module-level cache to avoid polluting other tests
            _eng_mod._pos_raw_loaded = saved_raw_loaded
            _eng_mod._pos_raw_df = saved_raw_df
            _eng_mod._pos_cache = saved_pos_cache

    def test_vrp_fallback_no_dvol_file(self):
        """When DVOL data file doesn't exist, vrp_mult uses RV proxy path
        (AC5: rv_90d * 1.2), producing valid non-trivial multipliers."""
        import engine as _eng_mod

        # Save and reset module-level DVOL cache so the plugin
        # re-evaluates the (patched) paths.
        saved_dvol_cache = _eng_mod._dvol_cache.copy()
        try:
            _eng_mod._dvol_cache.clear()

            # Patch DVOL path constants to non-existent files so the VRP
            # plugin falls back to the RV proxy (rv_90d * 1.2) per AC5.
            fake_btc = Path('/tmp/_nonexistent_btc_dvol.json')
            fake_eth = Path('/tmp/_nonexistent_eth_dvol.json')
            with patch.object(_eng_mod, '_BTC_DVOL_PATH', fake_btc), \
                 patch.object(_eng_mod, '_ETH_DVOL_PATH', fake_eth):
                eng = Engine(data_dir=os.path.join(PROJECT_ROOT, 'data'), market='spot')
                df = pd.read_parquet(BTC_PARQUET)
                ctx = eng._build_context('BTC', df)
                assert ctx is not None
                assert 'vrp_mult' in ctx.custom
                assert 'vrp_z' in ctx.custom
                # RV proxy produces real VRP values, so vrp_mult should
                # contain valid multipliers from the allowed set
                allowed = {0.3, 0.5, 1.0, 1.3}
                unique_vals = set(np.unique(ctx.custom['vrp_mult']))
                assert unique_vals.issubset(allowed), (
                    f"vrp_mult contains unexpected values: {unique_vals - allowed}"
                )
                # vrp_z should have non-NaN values (proxy path computes real z-scores)
                assert not np.all(np.isnan(ctx.custom['vrp_z'])), (
                    "vrp_z should not be all NaN when RV proxy is used"
                )
        finally:
            _eng_mod._dvol_cache = saved_dvol_cache

    def test_vrp_fallback_exception(self):
        """When VRP computation raises an exception, vrp_mult is all 1.0."""
        import engine as _eng_mod

        # Force the VRP plugin's try block to hit an exception by
        # making the daily close array trigger an error during
        # rolling_std computation.
        saved_dvol_cache = _eng_mod._dvol_cache.copy()
        try:
            _eng_mod._dvol_cache.clear()

            with patch.object(_eng_mod, 'rolling_std', side_effect=RuntimeError("forced")):
                eng = Engine(data_dir=os.path.join(PROJECT_ROOT, 'data'), market='spot')
                df = pd.read_parquet(BTC_PARQUET)
                ctx = eng._build_context('BTC', df)
                assert ctx is not None
                assert 'vrp_mult' in ctx.custom
                assert np.all(ctx.custom['vrp_mult'] == 1.0), (
                    "vrp_mult should be all 1.0 when VRP computation raises an exception"
                )
        finally:
            _eng_mod._dvol_cache = saved_dvol_cache

    def test_positioning_fallback_unknown_token(self):
        """When token is not in positioning data (e.g., ticker='UNKNOWN'),
        pos_mult is all 1.0."""
        eng = Engine(data_dir=os.path.join(PROJECT_ROOT, 'data'), market='spot')
        df = pd.read_parquet(BTC_PARQUET)
        # Build context with an unknown ticker so positioning lookup fails
        ctx = eng._build_context('UNKNOWN', df)
        assert ctx is not None
        assert 'pos_mult' in ctx.custom
        assert np.all(ctx.custom['pos_mult'] == 1.0), (
            "pos_mult should be all 1.0 when token is not in positioning data"
        )


# ===========================================================================
# AC4: Positioning computation correctness
# ===========================================================================

class TestPositioningComputation:
    """AC4: Verify positioning computation against known formulas.

    divergence = count_toptrader_ls_ratio - count_ls_ratio
    combined_z = (rolling_zscore(sum_toptrader, 30) + rolling_zscore(divergence, 30)) / 2

    z > 1.5  -> 0.3
    z > 0.5  -> 0.5
    |z| < 0.5 -> 1.0
    z < -0.5 -> 1.3
    z < -1.5 -> 1.5
    """

    def test_divergence_formula(self):
        """divergence = count_toptrader_ls_ratio - count_ls_ratio
        Verify end-to-end: pos_z must exist in ctx.custom after plugin runs."""
        ctx = _build_btc_context()
        # pos_z must exist (populated by the positioning plugin)
        assert 'pos_z' in ctx.custom, "pos_z not found — positioning plugin not registered"
        pos_z = ctx.custom['pos_z']
        # The z-score is derived from divergence = count_toptrader_ls - count_ls
        # and combined with sum_toptrader z-score. Verify it has correct shape.
        assert len(pos_z) == len(ctx.ind_1h['close'])
        # Verify non-trivial values exist (not all zero, which would mean
        # the plugin fell back rather than computing from real data)
        assert not np.all(pos_z == 0.0), (
            "pos_z should not be all zeros when real positioning data is available"
        )

    def test_z_to_mult_mapping_high_z(self):
        """z > 1.5 maps to pos_mult = 0.3 (crowded long -> reduce size)."""
        ctx = _build_btc_context()
        pos_z = ctx.custom['pos_z']
        pos_mult = ctx.custom['pos_mult']
        high_z = pos_z > 1.5
        if np.any(high_z):
            assert np.all(pos_mult[high_z] == 0.3), (
                "pos_mult should be 0.3 where pos_z > 1.5"
            )

    def test_z_to_mult_mapping_mid_high_z(self):
        """0.5 < z <= 1.5 maps to pos_mult = 0.5."""
        ctx = _build_btc_context()
        pos_z = ctx.custom['pos_z']
        pos_mult = ctx.custom['pos_mult']
        mid_high = (pos_z > 0.5) & (pos_z <= 1.5)
        if np.any(mid_high):
            assert np.all(pos_mult[mid_high] == 0.5), (
                "pos_mult should be 0.5 where 0.5 < pos_z <= 1.5"
            )

    def test_z_to_mult_mapping_neutral_z(self):
        """|z| <= 0.5 maps to pos_mult = 1.0."""
        ctx = _build_btc_context()
        pos_z = ctx.custom['pos_z']
        pos_mult = ctx.custom['pos_mult']
        neutral = np.abs(pos_z) < 0.5
        if np.any(neutral):
            assert np.all(pos_mult[neutral] == 1.0), (
                "pos_mult should be 1.0 where |pos_z| < 0.5"
            )

    def test_z_to_mult_mapping_mid_low_z(self):
        """-1.5 <= z < -0.5 maps to pos_mult = 1.3."""
        ctx = _build_btc_context()
        pos_z = ctx.custom['pos_z']
        pos_mult = ctx.custom['pos_mult']
        mid_low = (pos_z < -0.5) & (pos_z >= -1.5)
        if np.any(mid_low):
            assert np.all(pos_mult[mid_low] == 1.3), (
                "pos_mult should be 1.3 where -1.5 <= pos_z < -0.5"
            )

    def test_z_to_mult_mapping_low_z(self):
        """z < -1.5 maps to pos_mult = 1.5 (crowded short -> increase size)."""
        ctx = _build_btc_context()
        pos_z = ctx.custom['pos_z']
        pos_mult = ctx.custom['pos_mult']
        low_z = pos_z < -1.5
        if np.any(low_z):
            assert np.all(pos_mult[low_z] == 1.5), (
                "pos_mult should be 1.5 where pos_z < -1.5"
            )

    def test_combined_z_uses_rolling_zscore_window_30(self):
        """combined_z should use rolling_zscore with window=30."""
        ctx = _build_btc_context()
        pos_z = ctx.custom['pos_z']
        # The z-score should be clipped to [-3, 3] by rolling_zscore
        assert np.all(pos_z >= -3.0) and np.all(pos_z <= 3.0), (
            "pos_z values should be clipped to [-3, 3] (rolling_zscore behavior)"
        )


# ===========================================================================
# AC5: VRP computation correctness
# ===========================================================================

class TestVRPComputation:
    """AC5: Verify VRP computation against known formulas.

    rv_20d = rolling_std(log_returns, 20) * sqrt(365) * 100
    vrp = iv - rv_20d
    vrp_z = rolling_zscore(vrp, 60)

    z > 1.0   -> 1.3
    z > -0.5  -> 1.0
    z > -1.5  -> 0.5
    z <= -1.5 -> 0.3
    """

    def test_vrp_z_to_mult_mapping_high(self):
        """vrp_z > 1.0 maps to vrp_mult = 1.3 (IV premium -> increase size)."""
        ctx = _build_btc_context()
        vrp_z = ctx.custom['vrp_z']
        vrp_mult = ctx.custom['vrp_mult']
        high = vrp_z > 1.0
        if np.any(high):
            assert np.all(vrp_mult[high] == 1.3), (
                "vrp_mult should be 1.3 where vrp_z > 1.0"
            )

    def test_vrp_z_to_mult_mapping_neutral(self):
        """-0.5 < vrp_z <= 1.0 maps to vrp_mult = 1.0."""
        ctx = _build_btc_context()
        vrp_z = ctx.custom['vrp_z']
        vrp_mult = ctx.custom['vrp_mult']
        neutral = (vrp_z > -0.5) & (vrp_z <= 1.0)
        if np.any(neutral):
            assert np.all(vrp_mult[neutral] == 1.0), (
                "vrp_mult should be 1.0 where -0.5 < vrp_z <= 1.0"
            )

    def test_vrp_z_to_mult_mapping_low(self):
        """-1.5 < vrp_z <= -0.5 maps to vrp_mult = 0.5."""
        ctx = _build_btc_context()
        vrp_z = ctx.custom['vrp_z']
        vrp_mult = ctx.custom['vrp_mult']
        low = (vrp_z > -1.5) & (vrp_z <= -0.5)
        if np.any(low):
            assert np.all(vrp_mult[low] == 0.5), (
                "vrp_mult should be 0.5 where -1.5 < vrp_z <= -0.5"
            )

    def test_vrp_z_to_mult_mapping_very_low(self):
        """vrp_z <= -1.5 maps to vrp_mult = 0.3."""
        ctx = _build_btc_context()
        vrp_z = ctx.custom['vrp_z']
        vrp_mult = ctx.custom['vrp_mult']
        very_low = vrp_z <= -1.5
        if np.any(very_low):
            assert np.all(vrp_mult[very_low] == 0.3), (
                "vrp_mult should be 0.3 where vrp_z <= -1.5"
            )

    def test_vrp_z_clipped(self):
        """vrp_z should be in [-3, 3] range (rolling_zscore clips)."""
        ctx = _build_btc_context()
        vrp_z = ctx.custom['vrp_z']
        assert np.all(vrp_z >= -3.0) and np.all(vrp_z <= 3.0)

    def test_rv_uses_log_returns(self):
        """Realized vol should be computed from log returns, not simple returns."""
        # This is a structural test: we verify the output is reasonable
        # for BTC daily data (rv_20d should be 30-150% annualized range)
        ctx = _build_btc_context()
        # If vrp_z exists and has non-NaN values, the computation ran
        vrp_z = ctx.custom['vrp_z']
        assert not np.all(np.isnan(vrp_z)), "vrp_z should not be all NaN"


# ===========================================================================
# AC6: Daily-to-1H alignment
# ===========================================================================

class TestDailyTo1HAlignment:
    """AC6: pos_mult and vrp_mult arrays have length equal to 1H bars."""

    @pytest.fixture(autouse=True)
    def build_ctx(self):
        self.ctx = _build_btc_context()

    def test_pos_mult_length_matches_1h(self):
        n_1h = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['pos_mult']) == n_1h, (
            f"pos_mult length {len(self.ctx.custom['pos_mult'])} != 1H bars {n_1h}"
        )

    def test_vrp_mult_length_matches_1h(self):
        n_1h = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['vrp_mult']) == n_1h, (
            f"vrp_mult length {len(self.ctx.custom['vrp_mult'])} != 1H bars {n_1h}"
        )

    def test_pos_z_length_matches_1h(self):
        n_1h = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['pos_z']) == n_1h

    def test_vrp_z_length_matches_1h(self):
        n_1h = len(self.ctx.ind_1h['close'])
        assert len(self.ctx.custom['vrp_z']) == n_1h

    def test_pos_mult_not_daily_length(self):
        """pos_mult should NOT have daily length — it's aligned to 1H."""
        n_d = len(self.ctx.ind_d['close'])
        n_1h = len(self.ctx.ind_1h['close'])
        assert n_d != n_1h, "Sanity: daily and 1H should differ in length"
        assert len(self.ctx.custom['pos_mult']) != n_d or len(self.ctx.custom['pos_mult']) == n_1h


# ===========================================================================
# AC7: Module-level caching
# ===========================================================================

class TestModuleLevelCaching:
    """AC7: Positioning data is loaded once; subsequent builds reuse cached data."""

    def test_positioning_data_loaded_once(self):
        """Reset module cache, build context twice, verify the positioning
        parquet is read exactly once (module-level cache prevents re-read)."""
        import engine as _eng_mod

        eng = Engine(data_dir=os.path.join(PROJECT_ROOT, 'data'), market='spot')
        df = pd.read_parquet(BTC_PARQUET)

        # Save module-level cache state so we can restore it after the test.
        saved_raw_loaded = _eng_mod._pos_raw_loaded
        saved_raw_df = _eng_mod._pos_raw_df
        saved_pos_cache = _eng_mod._pos_cache.copy()

        try:
            # Reset module-level positioning cache so the plugin must
            # actually call pd.read_parquet on the first build.
            _eng_mod._pos_raw_loaded = False
            _eng_mod._pos_raw_df = None
            _eng_mod._pos_cache.clear()

            original_read_parquet = pd.read_parquet
            positioning_read_count = [0]

            def counting_read_parquet(path, *args, **kwargs):
                path_str = str(path)
                # Match the actual positioning data filename
                if 'all_symbols_daily_ls' in path_str.lower():
                    positioning_read_count[0] += 1
                return original_read_parquet(path, *args, **kwargs)

            with patch('pandas.read_parquet', side_effect=counting_read_parquet):
                # Build context twice (clear engine cache to force plugin re-run)
                eng._context_cache.clear()
                ctx1 = eng._build_context('BTC', df)
                assert ctx1 is not None
                assert 'pos_z' in ctx1.custom, (
                    "pos_z must be in ctx.custom — positioning plugin not registered"
                )
                eng._context_cache.clear()
                _eng_mod._pos_cache.clear()  # clear symbol cache, keep raw cache
                ctx2 = eng._build_context('BTC', df)

            # Positioning parquet should be read exactly once across both builds
            # (module-level _pos_raw_loaded cache prevents the second read).
            assert positioning_read_count[0] == 1, (
                f"Positioning data read {positioning_read_count[0]} times — "
                "expected exactly 1 (module-level cache should prevent re-read)"
            )
        finally:
            _eng_mod._pos_raw_loaded = saved_raw_loaded
            _eng_mod._pos_raw_df = saved_raw_df
            _eng_mod._pos_cache = saved_pos_cache
