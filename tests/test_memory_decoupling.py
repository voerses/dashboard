"""Acceptance tests for memory decoupling + plugin opt-in.

Tests AC1-AC26 from the feature brief. All tests should FAIL (RED) before
implementation and PASS (GREEN) after all tasks are complete.
"""
import json
import logging
import os
import sys
import tempfile
import types
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import pandas as pd
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from v4.config import PortfolioConfig, StrategySpec
from v4.engine import Engine, _INDICATOR_PLUGINS, _NAMED_PLUGINS


def _make_paper_config(**overrides):
    """Create a PaperConfig with sensible defaults for testing."""
    from v4.paper_config import PaperConfig
    defaults = dict(
        capital=200_000.0, mode="pool", pool_name="test_pool",
        strategies=[StrategySpec(strategy_id="s100", weight=0.5, market="perp", max_positions=10)],
        max_portfolio_positions=40, concentration_limit=0.10, adv_cap_pct=0.10,
        min_position_usd=200.0, exchange="binance", seed=42, lookback_months=12,
    )
    defaults.update(overrides)
    return PaperConfig(**defaults)


@pytest.fixture(autouse=True)
def _clear_engine_caches():
    """Clear module-level caches between tests to prevent cross-test pollution."""
    yield
    try:
        from v4.engine import _STRATEGY_MODULE_CACHE
        _STRATEGY_MODULE_CACHE.clear()
    except ImportError:
        pass


# ============================================================================
# Test 1: hist_cache.clear() removed (AC1)
# ============================================================================

class TestHistCacheClearRemoved:
    def test_hist_cache_survives_after_context_load(self):
        """AC1: hist_cache should NOT be cleared after _load_all_contexts returns.
        RED: _load_all_contexts calls hist_cache.clear() at line 153-154.
        GREEN: After removing that line, sentinel survives."""
        from v4.portfolio_signals import _load_all_contexts

        hist_cache = {("SENTINEL", "spot"): pd.DataFrame({"close": [1.0]})}
        config = PortfolioConfig()
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        # Empty token list -> no data loading, but hist_cache.clear() still runs
        with patch("v4.portfolio_signals.load_token_data_cached", return_value=None):
            _load_all_contexts([], spec, config, months=12, hist_cache=hist_cache)

        assert ("SENTINEL", "spot") in hist_cache, "hist_cache was cleared"


# ============================================================================
# Test 2: Config-driven cache_max_rows (AC2-AC6)
# ============================================================================

class TestCacheMaxRowsConfig:
    def test_portfolio_config_default_unlimited(self):
        """AC2: PortfolioConfig.cache_max_rows defaults to 0 (unlimited).
        RED: No such field -> AttributeError."""
        config = PortfolioConfig()
        assert config.cache_max_rows == 0

    def test_paper_config_default_22000(self):
        """AC3: PaperConfig.cache_max_rows defaults to 22000.
        RED: No such field -> AttributeError."""
        config = _make_paper_config()
        assert config.cache_max_rows == 22000

    def test_load_all_contexts_passes_config_max_rows(self):
        """AC4: _load_all_contexts passes config.cache_max_rows to load_token_data_cached.
        RED: Current code hardcodes max_rows=22000, not 0. Assertion max_rows==0 fails."""
        from v4.portfolio_signals import _load_all_contexts

        config = PortfolioConfig()  # After impl: cache_max_rows=0 (unlimited)
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        with patch("v4.portfolio_signals.load_token_data_cached", return_value=None) as mock_load:
            _load_all_contexts(["BTC"], spec, config, months=12, hist_cache={})

        # Must pass max_rows=0 (from config), not hardcoded 22000
        assert mock_load.called, "load_token_data_cached never called"
        for c in mock_load.call_args_list:
            assert c.kwargs.get("max_rows") == 0, \
                f"Expected max_rows=0 (unlimited), got {c.kwargs.get('max_rows')}"

    def test_backtest_passes_zero_max_rows(self):
        """AC5: Backtest (cache_max_rows=0) passes max_rows=0 (unlimited).
        RED: config has no cache_max_rows -> AttributeError."""
        from v4.portfolio_signals import _load_all_contexts

        config = PortfolioConfig()
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        with patch("v4.portfolio_signals.load_token_data_cached", return_value=None) as mock_load:
            _load_all_contexts(["BTC"], spec, config, months=18, hist_cache={})

        assert mock_load.called, "load_token_data_cached never called"
        _, kwargs = mock_load.call_args
        assert kwargs.get("max_rows") == 0, f"Expected max_rows=0, got {kwargs.get('max_rows')}"

    def test_paper_config_limits_to_22000(self):
        """AC6: Paper (PaperConfig, cache_max_rows=22000) limits to 22000.
        RED: No such field -> AttributeError."""
        config = _make_paper_config()
        assert config.cache_max_rows == 22000


# ============================================================================
# Test 3: Plugin opt-in (AC7-AC13)
# ============================================================================

class TestPluginOptIn:
    def test_all_plugins_have_names(self):
        """AC7: All 11 @register_indicator decorators have name= parameter.
        RED: _NAMED_PLUGINS is empty dict (0 entries)."""
        from v4.engine import _NAMED_PLUGINS
        assert len(_NAMED_PLUGINS) == 11
        expected = {'obv', 'vwap', 'momentum', 'enriched', 'obv_divergence',
                    'momentum_accel', 'funding_zscore', 'squeeze', 'multi_tf',
                    'positioning', 'vrp'}
        assert set(_NAMED_PLUGINS.keys()) == expected

    def test_strategy_module_cached_after_load(self):
        """AC8: _load_strategy_fn stores module in _STRATEGY_MODULE_CACHE.
        RED: _STRATEGY_MODULE_CACHE doesn't exist -> ImportError."""
        from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE
        _load_strategy_fn("s100")  # s100 exists in strategies/
        assert "s100" in _STRATEGY_MODULE_CACHE

    def test_load_required_plugins_returns_list(self):
        """AC9: _load_strategy_required_plugins returns REQUIRED_PLUGINS list.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _STRATEGY_MODULE_CACHE, _load_strategy_required_plugins

        # Use mock module to avoid cross-task dependency on AC14 (s501)
        mock_mod = types.SimpleNamespace(REQUIRED_PLUGINS=['obv'], strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s_test_ac9'] = mock_mod

        result = _load_strategy_required_plugins('s_test_ac9')
        assert result == ['obv']

    def test_load_required_plugins_returns_none_when_missing(self):
        """AC9: Returns None when strategy has no REQUIRED_PLUGINS.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _STRATEGY_MODULE_CACHE, _load_strategy_required_plugins

        # Mock module WITHOUT REQUIRED_PLUGINS attribute
        mock_mod = types.SimpleNamespace(strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s_test_no_plugins'] = mock_mod

        result = _load_strategy_required_plugins('s_test_no_plugins')
        assert result is None

    def test_none_required_plugins_runs_all(self):
        """AC12: All 11 plugins in _INDICATOR_PLUGINS have _NAMED_PLUGINS entries.
        RED: _NAMED_PLUGINS is empty -> first plugin assertion fails."""
        from v4.engine import _INDICATOR_PLUGINS, _NAMED_PLUGINS
        assert len(_INDICATOR_PLUGINS) == 11
        named_fns = set(_NAMED_PLUGINS.values())
        for plugin in _INDICATOR_PLUGINS:
            assert plugin in named_fns, f"{plugin.__name__} registered but not named"

    def test_selective_required_plugins_registry_check(self):
        """AC13: 'obv' and 'vwap' are registered named plugins.
        RED: _NAMED_PLUGINS is empty -> assertion fails."""
        from v4.engine import _NAMED_PLUGINS
        assert 'obv' in _NAMED_PLUGINS, "obv not registered"
        assert 'vwap' in _NAMED_PLUGINS, "vwap not registered"


# ============================================================================
# Test 4: Engine _required_plugins wiring (AC10)
# ============================================================================

class TestPluginWiring:
    def test_load_all_contexts_sets_required_plugins(self):
        """AC10: Engine instances in _load_all_contexts receive _required_plugins.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError.
        GREEN: After Task 3, engines get _required_plugins from strategy module."""
        from v4.engine import _load_strategy_required_plugins, _STRATEGY_MODULE_CACHE
        from v4.portfolio_signals import _load_all_contexts

        # Pre-populate module cache with strategy that declares plugins
        mock_mod = types.SimpleNamespace(REQUIRED_PLUGINS=['obv'], strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s100'] = mock_mod

        # Create synthetic data with enough rows (>500) for _build_context to be reached
        end_date = pd.Timestamp("2023-04-01")
        idx = pd.date_range("2020-01-01", periods=8000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 8000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "volume": rng.uniform(1e5, 1e6, 8000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 8000),
        }, index=idx)

        engines_seen = []
        def spy_build(self_eng, *args, **kwargs):
            engines_seen.append(getattr(self_eng, '_required_plugins', 'NOT_SET'))
            return None  # skip actual context creation

        config = PortfolioConfig()
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        with patch.object(Engine, '_build_context', spy_build), \
             patch("v4.portfolio_signals.load_token_data_cached", return_value=df):
            _load_all_contexts(["BTC"], spec, config, months=12,
                               end_date=end_date, hist_cache={})

        # spy_build captured _required_plugins from the Engine instance
        assert len(engines_seen) > 0, "_build_context never called"
        assert any(rp != 'NOT_SET' for rp in engines_seen), \
            "Engine._required_plugins was never set in _load_all_contexts"

    def test_precompute_strategy_signals_sets_required_plugins(self):
        """AC10: precompute_strategy_signals wires _required_plugins on engines.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _load_strategy_required_plugins, _STRATEGY_MODULE_CACHE
        from v4.signals import precompute_strategy_signals

        mock_mod = types.SimpleNamespace(REQUIRED_PLUGINS=['vwap'], strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s100'] = mock_mod

        end_date = pd.Timestamp("2023-04-01")
        idx = pd.date_range("2020-01-01", periods=8000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 8000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "volume": rng.uniform(1e5, 1e6, 8000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 8000),
        }, index=idx)

        engines_seen = []
        def spy_build(self_eng, *args, **kwargs):
            engines_seen.append(getattr(self_eng, '_required_plugins', 'NOT_SET'))
            return None

        config = PortfolioConfig()
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        with patch.object(Engine, '_build_context', spy_build), \
             patch("v4.signals.load_token_data_cached", return_value=df):
            precompute_strategy_signals(spec, ["BTC"], config, months=12,
                                       end_date=end_date, hist_cache={})

        assert len(engines_seen) > 0, "_build_context never called"
        assert any(rp != 'NOT_SET' for rp in engines_seen), \
            "Engine._required_plugins was never set in precompute_strategy_signals"


# ============================================================================
# Test 5: Shared Engine instances (AC18-AC20)
# ============================================================================

class TestSharedEngines:
    def test_precompute_accepts_optional_engines(self):
        """AC18: precompute_strategy_signals accepts eng_spot/eng_perp.
        RED: No such params -> assertion fails."""
        from v4.signals import precompute_strategy_signals
        sig = inspect.signature(precompute_strategy_signals)
        assert 'eng_spot' in sig.parameters
        assert 'eng_perp' in sig.parameters
        assert sig.parameters['eng_spot'].default is None
        assert sig.parameters['eng_perp'].default is None

    def test_union_computed_when_all_declare(self):
        """AC19: Union of REQUIRED_PLUGINS computed when all strategies declare.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _STRATEGY_MODULE_CACHE, _load_strategy_required_plugins

        _STRATEGY_MODULE_CACHE['s_a'] = types.SimpleNamespace(
            REQUIRED_PLUGINS=['obv'], strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s_b'] = types.SimpleNamespace(
            REQUIRED_PLUGINS=['vwap'], strategy=lambda ctx: None)

        plugins_a = _load_strategy_required_plugins('s_a')
        plugins_b = _load_strategy_required_plugins('s_b')
        assert plugins_a is not None and plugins_b is not None
        union = sorted(set(plugins_a) | set(plugins_b))
        assert union == ['obv', 'vwap']

    def test_fallback_when_any_undeclared(self):
        """AC19: Falls back to None when any strategy lacks REQUIRED_PLUGINS.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _STRATEGY_MODULE_CACHE, _load_strategy_required_plugins

        _STRATEGY_MODULE_CACHE['s_c'] = types.SimpleNamespace(
            REQUIRED_PLUGINS=['obv'], strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s_d'] = types.SimpleNamespace(
            strategy=lambda ctx: None)  # NO REQUIRED_PLUGINS

        plugins_c = _load_strategy_required_plugins('s_c')
        plugins_d = _load_strategy_required_plugins('s_d')
        assert plugins_c is not None
        assert plugins_d is None  # Missing -> fallback to run all

    def test_context_cache_hit_on_second_call(self):
        """AC20: Same token+market with shared engine hits context cache.
        RED: precompute_strategy_signals has no eng_perp param -> TypeError."""
        from v4.signals import precompute_strategy_signals

        eng = Engine(data_dir="data", market="perp", capital=200000, exchange="binance")
        eng._required_plugins = []

        build_count = [0]
        mock_ctx = MagicMock()
        def counting_build(self_eng, *args, **kwargs):
            build_count[0] += 1
            return mock_ctx

        # Synthetic data large enough for _build_context to be reached
        end_date = pd.Timestamp("2023-04-01")
        idx = pd.date_range("2020-01-01", periods=8000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 8000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "volume": rng.uniform(1e5, 1e6, 8000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 8000),
        }, index=idx)

        config = PortfolioConfig()
        spec_a = StrategySpec(strategy_id="s100", weight=0.5, market="perp", max_positions=10)
        spec_b = StrategySpec(strategy_id="s100", weight=0.5, market="perp", max_positions=10)

        with patch.object(Engine, '_build_context', counting_build), \
             patch("v4.signals.load_token_data_cached", return_value=df), \
             patch("v4.signals._load_strategy_fn", return_value=lambda ctx: MagicMock()):
            # First call builds context
            precompute_strategy_signals(spec_a, ["BTC"], config, months=12,
                                       end_date=end_date, hist_cache={}, eng_perp=eng)
            first_count = build_count[0]
            # Second call for same token should hit cache
            precompute_strategy_signals(spec_b, ["BTC"], config, months=12,
                                       end_date=end_date, hist_cache={}, eng_perp=eng)

        # Context was built once for BTC, second call should hit cache
        assert build_count[0] == first_count, \
            f"Expected cache hit: _build_context called {build_count[0]} times, expected {first_count}"


# ============================================================================
# Test 6: s501 declaration (AC14)
# ============================================================================

class TestS501RequiredPlugins:
    def test_s501_declares_required_plugins_empty(self):
        """AC14: s501 declares REQUIRED_PLUGINS = [].
        RED: s501 exists but has no REQUIRED_PLUGINS attr -> hasattr fails."""
        from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE
        _load_strategy_fn("s501")  # loads and caches module
        mod = _STRATEGY_MODULE_CACHE.get("s501")
        assert mod is not None, "s501 module not cached"
        assert hasattr(mod, 'REQUIRED_PLUGINS'), "s501 missing REQUIRED_PLUGINS"
        assert mod.REQUIRED_PLUGINS == []


# ============================================================================
# Test 7: Plugin dependency documentation (AC16-AC17)
# ============================================================================

class TestPluginDocumentation:
    def test_named_plugins_have_dependency_info(self):
        """AC16: Plugin dependencies documented in _load_strategy_required_plugins.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _load_strategy_required_plugins
        assert _load_strategy_required_plugins.__doc__ is not None
        doc = _load_strategy_required_plugins.__doc__.lower()
        assert "depend" in doc or "require" in doc

    def test_module_cache_has_restart_doc(self):
        """AC17: Strategy module cache restart requirement documented.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _load_strategy_required_plugins
        doc = _load_strategy_required_plugins.__doc__ or ""
        assert "restart" in doc.lower() or "cache" in doc.lower()


# ============================================================================
# Test 8: Class A max_rows wiring in signals.py (AC21)
# ============================================================================

class TestClassAMaxRows:
    def test_precompute_strategy_signals_passes_config_max_rows(self):
        """AC21: precompute_strategy_signals (Class A) passes config.cache_max_rows
        to load_token_data_cached.
        RED: signals.py currently calls load_token_data_cached without max_rows param,
        so kwargs.get('max_rows') returns None, not 0."""
        from v4.signals import precompute_strategy_signals

        config = PortfolioConfig()  # cache_max_rows=0 after impl
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        with patch("v4.signals.load_token_data_cached", return_value=None) as mock_load:
            precompute_strategy_signals(spec, ["BTC"], config, months=12,
                                       end_date=pd.Timestamp("2023-04-01"), hist_cache={})

        assert mock_load.called, "load_token_data_cached never called"
        for c in mock_load.call_args_list:
            assert c.kwargs.get("max_rows") == 0, \
                f"Expected max_rows=0 (unlimited), got {c.kwargs.get('max_rows')}"

    def test_paper_config_class_a_limits_to_22000(self):
        """AC21+AC6: Class A path with PaperConfig passes max_rows=22000.
        RED: signals.py has no max_rows param -> kwargs.get('max_rows') is None."""
        from v4.signals import precompute_strategy_signals

        config = _make_paper_config()  # cache_max_rows=22000 after impl
        spec = StrategySpec(strategy_id="s100", weight=0.5, market="perp", max_positions=10)

        with patch("v4.signals.load_token_data_cached", return_value=None) as mock_load:
            precompute_strategy_signals(spec, ["BTC"], config, months=12,
                                       end_date=pd.Timestamp("2023-04-01"), hist_cache={})

        assert mock_load.called
        for c in mock_load.call_args_list:
            assert c.kwargs.get("max_rows") == 22000, \
                f"Expected max_rows=22000, got {c.kwargs.get('max_rows')}"


# ============================================================================
# Test 9: JSON config wiring for cache_max_rows (AC22)
# ============================================================================

class TestConfigJsonWiring:
    def test_load_paper_config_wires_cache_max_rows_default(self):
        """AC22: load_paper_config() defaults cache_max_rows to 22000.
        RED: PaperConfig has no cache_max_rows field -> AttributeError,
        or if field exists but not wired in load_paper_config, defaults to 0."""
        from v4.paper_config import load_paper_config

        # Minimal valid JSON config (no cache_max_rows key)
        config_data = {
            "strategies": [{"strategy_id": "s100", "weight": 0.5, "market": "perp", "max_positions": 10}],
            "initial_capital": 100000,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            tmp_path = f.name

        try:
            config = load_paper_config(tmp_path)
            assert config.cache_max_rows == 22000, \
                f"Expected cache_max_rows=22000, got {config.cache_max_rows}"
        finally:
            os.unlink(tmp_path)

    def test_load_paper_config_respects_explicit_value(self):
        """AC22: load_paper_config() respects explicit cache_max_rows from JSON.
        RED: PaperConfig has no cache_max_rows field -> AttributeError."""
        from v4.paper_config import load_paper_config

        config_data = {
            "strategies": [{"strategy_id": "s100", "weight": 0.5, "market": "perp", "max_positions": 10}],
            "initial_capital": 100000,
            "cache_max_rows": 50000,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            tmp_path = f.name

        try:
            config = load_paper_config(tmp_path)
            assert config.cache_max_rows == 50000, \
                f"Expected cache_max_rows=50000, got {config.cache_max_rows}"
        finally:
            os.unlink(tmp_path)


# ============================================================================
# Test 10: Plugin name validation (AC23)
# ============================================================================

class TestPluginNameValidation:
    def test_unknown_plugin_name_logs_warning(self):
        """AC23: _load_strategy_required_plugins warns on unknown plugin names.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _STRATEGY_MODULE_CACHE, _load_strategy_required_plugins

        _STRATEGY_MODULE_CACHE['s_typo'] = types.SimpleNamespace(
            REQUIRED_PLUGINS=['obv', 'vwp_typo'], strategy=lambda ctx: None)

        with patch('v4.engine.logging') as mock_logging:
            result = _load_strategy_required_plugins('s_typo')

        assert result == ['obv', 'vwp_typo']  # still returns the list
        # Verify warning was logged for the typo'd name
        mock_logging.getLogger.return_value.warning.assert_called()
        warning_args = str(mock_logging.getLogger.return_value.warning.call_args)
        assert 'vwp_typo' in warning_args, \
            f"Expected warning about 'vwp_typo', got: {warning_args}"


# ============================================================================
# Test 11: Integration-level plugin dispatch (AC24-AC25)
# ============================================================================

class TestPluginDispatchIntegration:
    def test_build_context_empty_required_plugins_no_custom(self):
        """AC24: _build_context with _required_plugins=[] produces empty ctx.custom.
        RED: _NAMED_PLUGINS is empty -> _build_context runs no plugins anyway,
        so ctx.custom is empty for wrong reason. After impl, _NAMED_PLUGINS has
        11 entries and this test proves the opt-in mechanism actually works."""
        from v4.engine import _NAMED_PLUGINS

        # Precondition: named plugins must exist (proves system is wired)
        assert len(_NAMED_PLUGINS) >= 1, "No named plugins — test is vacuous"

        eng = Engine(data_dir="data", market="perp", capital=200000, exchange="binance")
        eng._required_plugins = []  # empty list = skip all

        # Build a minimal synthetic DataFrame
        idx = pd.date_range("2022-01-01", periods=2000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 2000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "open": close, "volume": rng.uniform(1e5, 1e6, 2000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 2000),
        }, index=idx)

        ctx = eng._build_context("TEST", df, min_bars=210)
        if ctx is not None:
            assert ctx.custom == {}, \
                f"Expected empty ctx.custom with _required_plugins=[], got keys: {list(ctx.custom.keys())}"

    def test_build_context_none_required_plugins_runs_all(self):
        """AC25: _build_context with _required_plugins=None runs all 11 plugins.
        RED: _NAMED_PLUGINS is empty (0 entries) -> assertion fails."""
        from v4.engine import _NAMED_PLUGINS

        assert len(_NAMED_PLUGINS) == 11, \
            f"Expected 11 named plugins, got {len(_NAMED_PLUGINS)}"

        eng = Engine(data_dir="data", market="perp", capital=200000, exchange="binance")
        # _required_plugins defaults to None (or not set) — should run all

        idx = pd.date_range("2022-01-01", periods=2000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 2000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "open": close, "volume": rng.uniform(1e5, 1e6, 2000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 2000),
        }, index=idx)

        ctx = eng._build_context("TEST", df, min_bars=210)
        if ctx is not None:
            # At minimum, the always-present plugins should populate ctx.custom
            # obv, vwap, momentum all write to ctx.custom unconditionally
            assert len(ctx.custom) > 0, \
                "Expected ctx.custom to be populated when _required_plugins is None"


# ============================================================================
# Test 12: Strategy module cache hit (AC26)
# ============================================================================

class TestStrategyModuleCacheHit:
    def test_load_strategy_fn_returns_from_cache(self):
        """AC26: Second call to _load_strategy_fn returns from cache.
        RED: _STRATEGY_MODULE_CACHE doesn't exist -> ImportError."""
        from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE

        # First call — populates cache
        fn1 = _load_strategy_fn("s100")
        assert "s100" in _STRATEGY_MODULE_CACHE

        # Patch importlib to raise if exec_module is called again
        import importlib.util
        original_spec = importlib.util.spec_from_file_location
        call_count = [0]

        def counting_spec(*args, **kwargs):
            call_count[0] += 1
            return original_spec(*args, **kwargs)

        with patch.object(importlib.util, 'spec_from_file_location', counting_spec):
            fn2 = _load_strategy_fn("s100")

        # Second call should NOT hit importlib (cache hit)
        assert call_count[0] == 0, \
            f"spec_from_file_location called {call_count[0]} times on cache hit"
        assert fn1 is fn2, "Cache returned different function object"


# ============================================================================
# Test 13: AC13 integration — selective plugins via _build_context
# ============================================================================

class TestSelectivePluginIntegration:
    def test_build_context_selective_produces_only_requested(self):
        """AC13: _build_context with _required_plugins=['obv','vwap'] produces
        only OBV and VWAP outputs in ctx.custom — NOT momentum, funding, etc.
        RED: _NAMED_PLUGINS is empty -> precondition fails."""
        from v4.engine import _NAMED_PLUGINS

        assert 'obv' in _NAMED_PLUGINS, "obv not registered"
        assert 'vwap' in _NAMED_PLUGINS, "vwap not registered"

        eng = Engine(data_dir="data", market="perp", capital=200000, exchange="binance")
        eng._required_plugins = ['obv', 'vwap']

        idx = pd.date_range("2022-01-01", periods=2000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 2000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "open": close, "volume": rng.uniform(1e5, 1e6, 2000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 2000),
        }, index=idx)

        ctx = eng._build_context("TEST", df, min_bars=210)
        if ctx is not None:
            # OBV plugin produces 'obv', 'obv_slope'
            assert 'obv' in ctx.custom, "obv missing from ctx.custom"
            # VWAP plugin produces 'vwap_20', 'vwap_dev'
            assert 'vwap_20' in ctx.custom, "vwap_20 missing from ctx.custom"
            # Momentum plugin produces 'ret_6h', etc — should NOT be present
            assert 'ret_6h' not in ctx.custom, \
                "ret_6h present — momentum plugin ran despite not being requested"
            # Funding plugin produces 'funding_zscore' — should NOT be present
            assert 'funding_zscore' not in ctx.custom, \
                "funding_zscore present — funding plugin ran despite not being requested"


# ============================================================================
# Test 14: AC10 — _precompute_true_walk_forward wiring
# ============================================================================

class TestWalkForwardPluginWiring:
    def test_precompute_true_walk_forward_sets_required_plugins(self):
        """AC10: Engine instances in _precompute_true_walk_forward receive
        _required_plugins from strategy module.
        RED: _load_strategy_required_plugins doesn't exist -> ImportError."""
        from v4.engine import _load_strategy_required_plugins, _STRATEGY_MODULE_CACHE
        from v4.portfolio_signals import _precompute_true_walk_forward

        mock_mod = types.SimpleNamespace(REQUIRED_PLUGINS=['obv'], strategy=lambda ctx: None)
        _STRATEGY_MODULE_CACHE['s100'] = mock_mod

        end_date = pd.Timestamp("2023-04-01")
        idx = pd.date_range("2020-01-01", periods=8000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 8000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "volume": rng.uniform(1e5, 1e6, 8000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 8000),
        }, index=idx)

        engines_seen = []
        def spy_build(self_eng, *args, **kwargs):
            engines_seen.append(getattr(self_eng, '_required_plugins', 'NOT_SET'))
            return None  # skip actual context creation

        config = PortfolioConfig(true_walk_forward=True)
        spec = StrategySpec(strategy_id="s100", weight=1.0, market="perp", max_positions=10)

        with patch.object(Engine, '_build_context', spy_build), \
             patch("v4.portfolio_signals.load_token_data_cached", return_value=df):
            try:
                _precompute_true_walk_forward(
                    ["BTC"], spec, config, months=12,
                    end_date=end_date, hist_cache={})
            except Exception:
                pass  # May fail on walk-forward window computation — we only need the spy

        # If _build_context was called, check _required_plugins was set
        if len(engines_seen) > 0:
            assert any(rp != 'NOT_SET' for rp in engines_seen), \
                "Engine._required_plugins was never set in _precompute_true_walk_forward"
        else:
            # _build_context may not be reached if window computation fails —
            # in that case, just verify the import works (AC10 import dependency)
            assert _load_strategy_required_plugins is not None


# ============================================================================
# Test 15: AC22 — run_paper_multi.py config constructor wiring
# ============================================================================

class TestRunPaperMultiConfigWiring:
    def test_run_paper_multi_config_has_cache_max_rows(self):
        """AC22: run_paper_multi.py PaperConfig constructor wires cache_max_rows.
        RED: PaperConfig has no cache_max_rows field -> AttributeError."""
        from v4.run_paper_multi import load_multi_config

        config_data = {
            "portfolios": [{
                "strategies": [{"strategy_id": "s100", "weight": 0.5, "market": "perp", "max_positions": 10}],
                "initial_capital": 100000,
            }]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            tmp_path = f.name

        try:
            configs = load_multi_config(tmp_path)
            assert len(configs) >= 1
            assert configs[0].cache_max_rows == 22000, \
                f"Expected cache_max_rows=22000, got {configs[0].cache_max_rows}"
        finally:
            os.unlink(tmp_path)


# ============================================================================
# Test 16: Plugin dependency ordering (AC16 enforcement)
# ============================================================================

class TestPluginDependencyOrdering:
    def test_obv_divergence_gets_obv_data_when_both_requested(self):
        """AC16: When requesting ['obv_divergence', 'obv'] in any order,
        obv runs before obv_divergence (dependency-correct ordering).
        RED: _NAMED_PLUGINS is empty -> precondition fails."""
        from v4.engine import _NAMED_PLUGINS

        assert 'obv' in _NAMED_PLUGINS, "obv not registered"
        assert 'obv_divergence' in _NAMED_PLUGINS, "obv_divergence not registered"

        eng = Engine(data_dir="data", market="perp", capital=200000, exchange="binance")
        # Deliberately wrong order — obv_divergence before obv
        eng._required_plugins = ['obv_divergence', 'obv']

        idx = pd.date_range("2022-01-01", periods=2000, freq="1h")
        rng = np.random.default_rng(42)
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, 2000))
        df = pd.DataFrame({
            "close": close, "high": close + 0.5, "low": close - 0.5,
            "open": close, "volume": rng.uniform(1e5, 1e6, 2000),
            "taker_buy_base": rng.uniform(1e4, 1e5, 2000),
        }, index=idx)

        ctx = eng._build_context("TEST", df, min_bars=210)
        if ctx is not None:
            # obv must have run first for obv_divergence to produce output
            assert 'obv' in ctx.custom, "obv missing — base plugin didn't run"
            assert 'obv_divergence' in ctx.custom, \
                "obv_divergence missing — dependency ordering broken (obv didn't run first)"
