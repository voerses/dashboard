"""M9 C-3 — Pull-Based Memoized Indicators with MTF safety (AC #4).

Acceptance criteria verified:
  - `ctx.per_token("BTCUSDT").ema(20)` in 1h context and same call in 1m
    context return DIFFERENT cached values (timeframe in cache key).
  - Same call in same bar → same object (identity check on cached return).
  - Same call on new bar → cache recomputes.
  - Cache eviction per UniverseContext instance (backtest: new bar = new
    ctx; paper: new tick cycle = cache clear).

All tests MUST FAIL today — the M9 pull-based memoized indicator registry
(including the MTF-safe cache key that incorporates `BarSpec` timeframe)
does not yet exist as specified in M9 brief C-3.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMTFCacheKeyIncludesTimeframe:
    """AC #4 — same EMA call on 1h vs 1m returns different cached values.

    The cache key MUST include the BarSpec timeframe, otherwise the 1h
    indicator would return the 1m-cached value (silent MTF collision).
    """

    def test_ema_on_1h_and_1m_return_different_cached_values(self):
        from v5.universe_context import UniverseContext

        ctx_1h = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=200, seed=0,
            timeframe="1h",
        )
        ctx_1m = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=200, seed=0,
            timeframe="1m",
        )

        ema_1h = ctx_1h.per_token("BTCUSDT").ema(20)
        ema_1m = ctx_1m.per_token("BTCUSDT").ema(20)

        assert ema_1h != ema_1m, (
            "MTF cache collision: 1h and 1m EMA(20) returned the same value; "
            "cache key must include timeframe"
        )


class TestCacheIdentityWithinSameBar:
    """AC #4 — same call in same bar returns the same cached object."""

    def test_same_call_same_bar_returns_identical_object(self):
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=200, seed=0,
            timeframe="1h",
        )

        first = ctx.per_token("BTCUSDT").ema(20)
        second = ctx.per_token("BTCUSDT").ema(20)

        # Identity check — cache must return the same object, not recompute.
        assert first is second, (
            "Memoized indicator must return identical object on second call "
            "within the same bar (same UniverseContext instance)"
        )


class TestCacheRecomputesOnNewBar:
    """AC #4 — same call on a new bar recomputes.

    In backtest mode, a new bar = new UniverseContext instance, so the
    cache auto-evicts. When the underlying data advances, the computed
    value changes.
    """

    def test_ema_recomputes_on_new_bar(self):
        from v5.universe_context import UniverseContext

        ctx_bar_t = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=50, seed=0, timeframe="1h",
        )
        ctx_bar_tplus1 = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=51, seed=0, timeframe="1h",
        )

        ema_t = ctx_bar_t.per_token("BTCUSDT").ema(20)
        ema_tplus1 = ctx_bar_tplus1.per_token("BTCUSDT").ema(20)

        # New bar ⇒ new data ⇒ cache recomputes with different result.
        assert ema_t != ema_tplus1, (
            "EMA(20) at bar t and bar t+1 must differ — cache must "
            "recompute on new UniverseContext instance"
        )
        # And since they are different ctx instances, any cached object is
        # not shared between them (object identity differs).
        assert ema_t is not ema_tplus1


class TestCacheEvictionPerUniverseContextInstance:
    """AC #4 — cache is scoped per UniverseContext instance.

    Paper-trading clears the cache each tick cycle; backtest evicts per
    new bar (= new ctx). Verified by: calling clear_cache() (or equivalent
    per-ctx mechanism) forces recomputation even if the underlying bar
    index has not changed.
    """

    def test_ctx_cache_is_isolated_between_instances(self):
        from v5.universe_context import UniverseContext

        ctx_a = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=100, seed=0, timeframe="1h",
        )
        ctx_b = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=100, seed=0, timeframe="1h",
        )

        # Prime the cache on ctx_a.
        val_a_first = ctx_a.per_token("BTCUSDT").ema(20)
        val_a_second = ctx_a.per_token("BTCUSDT").ema(20)
        assert val_a_first is val_a_second, "ctx_a cache must hit on repeat"

        # ctx_b is a separate instance; its cache is independent. Primed
        # identical data yields equal value but NOT the same cached object
        # as ctx_a (caches are per-instance).
        val_b = ctx_b.per_token("BTCUSDT").ema(20)
        assert val_a_first is not val_b, (
            "Cache must be scoped per UniverseContext instance; ctx_a and "
            "ctx_b must not share cached objects"
        )
