"""M7 — v5/regimes.py per-bar memoized regime utility (AC-Reg2).

Reviewer H6 fix: AC-Reg2 had no task and no test. This file covers the
optional utility's contract: cache keyed per (bar_idx, universe_ctx_id) so
multiple strategies on the same UniverseContext share a single computation.

All tests MUST FAIL today — v5.regimes does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestRegimesModule:
    """AC-Reg2 — v5/regimes.py module + canonical detector."""

    def test_regimes_module_importable(self):
        import v5.regimes  # noqa: F401

    def test_detect_regime_function_exists(self):
        """Canonical per-bar regime detector callable."""
        from v5.regimes import detect_regime
        assert callable(detect_regime)


class TestPerBarMemoization:
    """AC-Reg2 — memoization key (bar_idx, universe_ctx_id)."""

    def test_repeated_call_same_bar_same_ctx_cache_hits(self):
        from v5.regimes import detect_regime
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)

        # First call — miss
        r1 = detect_regime(ctx, bar_idx=40)
        # Second call — same bar, same ctx — must return exact same value (memo hit)
        r2 = detect_regime(ctx, bar_idx=40)
        assert r1 is r2 or r1 == r2, (
            "AC-Reg2: repeated detect_regime(ctx, bar_idx) must return cached result"
        )

    def test_call_count_not_duplicated_across_strategies(self):
        """Two strategies on same UniverseContext: detector computes ONCE per bar."""
        from v5.regimes import detect_regime, _regime_call_count
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        baseline = _regime_call_count(ctx)

        # Strategy A invokes
        detect_regime(ctx, bar_idx=30)
        # Strategy B invokes — same (ctx, bar_idx) → should hit cache
        detect_regime(ctx, bar_idx=30)

        delta = _regime_call_count(ctx) - baseline
        assert delta == 1, (
            f"AC-Reg2: two calls at same (ctx, bar_idx) must compute once; "
            f"got {delta} computations"
        )


class TestKeyedByContextIdentity:
    """AC-Reg2 — cache key uses universe_ctx_id (id(ctx)), NOT token or content."""

    def test_two_distinct_contexts_do_not_share_cache(self):
        from v5.regimes import detect_regime
        from v5.universe_context import UniverseContext

        ctx_a = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        ctx_b = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        # Same seed + tokens but distinct UniverseContext instances → separate cache

        ra = detect_regime(ctx_a, bar_idx=20)
        rb = detect_regime(ctx_b, bar_idx=20)
        # Values may be equal (same seed) but must be INDEPENDENTLY computed
        # (verify via mock or counter — not just equality)
        from v5.regimes import _regime_call_count
        calls_a = _regime_call_count(ctx_a)
        calls_b = _regime_call_count(ctx_b)
        assert calls_a >= 1 and calls_b >= 1, (
            "AC-Reg2: each UniverseContext gets its own cache; no cross-ctx sharing"
        )


class TestCacheClearedOnReset:
    """AC-Reg2 — cache cleared on WF fold boundary (fresh ctx per fold)."""

    def test_fresh_context_starts_empty_cache(self):
        from v5.regimes import detect_regime, _regime_call_count
        from v5.universe_context import UniverseContext

        # Fold 1
        ctx_fold1 = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0, fold_id=0)
        detect_regime(ctx_fold1, bar_idx=40)
        assert _regime_call_count(ctx_fold1) == 1

        # Fold 2 — fresh ctx
        ctx_fold2 = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0, fold_id=1)
        assert _regime_call_count(ctx_fold2) == 0, (
            "AC-Reg2: new UniverseContext must start with empty regime cache"
        )
