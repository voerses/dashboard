"""M7 — Pull-based memoized IndicatorCache + .cache_key() + lambda rejection (AC-S3).

All tests MUST FAIL today — v5.indicators does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestIndicatorCacheImport:
    """AC-S3 — v5.indicators.IndicatorCache exists."""

    def test_indicator_cache_importable(self):
        from v5.indicators import IndicatorCache  # noqa: F401


class TestPullBasedScalarReturn:
    """AC-S3 — per_token(t).<ind>(...) returns scalar at bar_idx."""

    def test_ema_returns_scalar_not_array(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        val = ctx.data.per_token("BTC").ema(n=20, col="close")
        assert isinstance(val, float) or np.isscalar(val), (
            f"AC-S3: ema(...) must return scalar, got {type(val).__name__}"
        )

    def test_sma_returns_scalar(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        assert np.isscalar(ctx.data.per_token("BTC").sma(n=10, col="close"))


class TestCacheKeyMemoization:
    """AC-S3 — memoized by (token, qualname, frozen_params, bar_idx)."""

    def test_same_call_twice_hits_cache(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        cache = ctx.data.indicator_cache
        v1 = ctx.data.per_token("BTC").ema(n=20, col="close")
        hits_before = cache.stats.hits
        v2 = ctx.data.per_token("BTC").ema(n=20, col="close")
        assert v1 == v2
        assert cache.stats.hits == hits_before + 1

    def test_different_params_miss_cache(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        cache = ctx.data.indicator_cache
        ctx.data.per_token("BTC").ema(n=20, col="close")
        misses_before = cache.stats.misses
        ctx.data.per_token("BTC").ema(n=21, col="close")
        assert cache.stats.misses == misses_before + 1

    def test_different_token_miss_cache(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC", "ETH"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        cache = ctx.data.indicator_cache
        ctx.data.per_token("BTC").ema(n=20, col="close")
        misses_before = cache.stats.misses
        ctx.data.per_token("ETH").ema(n=20, col="close")
        assert cache.stats.misses == misses_before + 1

    def test_different_bar_idx_miss_cache(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        cache = ctx.data.indicator_cache
        ctx.data.per_token("BTC").ema(n=20, col="close")
        misses_before = cache.stats.misses
        ctx.seek_bar(idx=51)
        ctx.data.per_token("BTC").ema(n=20, col="close")
        assert cache.stats.misses == misses_before + 1


class TestNonScalarParamRejection:
    """AC-S3 — stdlib indicators reject non-scalar params."""

    def test_numpy_array_param_raises_value_error(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        with pytest.raises(ValueError):
            ctx.data.per_token("BTC").ema(n=np.array([20]), col="close")

    def test_list_param_raises_value_error(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        with pytest.raises(ValueError):
            ctx.data.per_token("BTC").ema(n=[20, 21], col="close")


def _sample_named_indicator(arr, n):
    """Module-level function with no free variables — not a closure."""
    return arr[-n:].mean()


class TestLambdaRejection:
    """AC-S3 — lambdas / closures rejected at registration."""

    def test_lambda_rejected_with_named_function_error(self):
        from v5.indicators import IndicatorCache
        cache = IndicatorCache()
        with pytest.raises(ValueError, match="named function"):
            cache.register(lambda arr, n: arr[-n:].mean())

    def test_closure_rejected(self):
        from v5.indicators import IndicatorCache
        cache = IndicatorCache()
        scale = 2.0

        def my_ind(arr, n):
            return arr[-n:].mean() * scale

        with pytest.raises(ValueError, match="named function"):
            cache.register(my_ind)

    def test_named_module_function_accepted(self):
        from v5.indicators import IndicatorCache
        IndicatorCache().register(_sample_named_indicator)


class TestCacheKeyMethodContract:
    """AC-S3 — custom indicators with non-scalar params use .cache_key()."""

    def test_custom_indicator_without_cache_key_raises(self):
        from v5.indicators import IndicatorCache
        cache = IndicatorCache()

        class _NumpyParam:
            data = np.array([1, 2, 3])

        cache.register(_sample_named_indicator)
        with pytest.raises(ValueError, match="cache_key"):
            cache.compute(
                _sample_named_indicator, token="BTC", bar_idx=10,
                arr=np.array([1.0]), n=_NumpyParam(),
            )

    def test_custom_indicator_with_cache_key_succeeds(self):
        from v5.indicators import IndicatorCache
        cache = IndicatorCache()

        class _WithCacheKey:
            data = np.array([1, 2, 3])

            def cache_key(self):
                return ("np_arr", 1, 2, 3)

        cache.register(_sample_named_indicator)
        cache.compute(
            _sample_named_indicator, token="BTC", bar_idx=10,
            arr=np.array([1.0, 2.0, 3.0]), n=_WithCacheKey(),
        )


class TestCacheClearedOnReset:
    """AC-S3 — cache cleared on_reset() at fold boundary."""

    def test_cache_cleared_on_strategy_reset(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        cache = ctx.data.indicator_cache
        ctx.data.per_token("BTC").ema(n=20, col="close")
        assert cache.stats.entries > 0
        cache.reset()
        assert cache.stats.entries == 0
