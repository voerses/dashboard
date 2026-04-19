"""M7 — Per-bar regime detector with context-scoped memoization (AC-Reg2).

Cache key: `(id(ctx), bar_idx)`. Two strategies sharing the same UniverseContext
compute the regime ONCE per bar; a fresh UniverseContext starts with an empty
cache (WF fold boundary → fresh ctx → fresh memo).

Design §13 L2: `universe_ctx_id` is `id(ctx)`. Per design §2.8 ID2, the
UniverseContext outer container is frozen so `id(ctx)` is stable across its
lifetime. Caches do NOT cross fold boundaries because WalkForwardRunner builds
a new ctx per fold (AC-V1).
"""
from __future__ import annotations

from typing import Any, Optional


# Per-ctx call counter (test observability)
_call_counts: dict[int, int] = {}

# Per-ctx regime cache keyed by (id(ctx), bar_idx)
_cache: dict[tuple[int, int], Any] = {}


def _ctx_id(ctx) -> int:
    """Canonical context identity.

    Prefers the monotonic `ctx_uid` stamped on `_lifecycle_config` at
    `UniverseContext.build_test()` time (process-wide unique, never
    reused). Falls back to `id(ctx)` per design §13 L2 when `ctx_uid`
    isn't available (e.g., caller built ctx directly).

    History: `id()` alone caused full-suite flakes — Python recycles ids
    after GC, so a fresh ctx could inherit a prior ctx's cached call
    count via id collision (observed 2026-04-19 full-suite run).
    """
    cfg = getattr(ctx, "_lifecycle_config", None)
    if isinstance(cfg, dict) and "ctx_uid" in cfg:
        return int(cfg["ctx_uid"])
    return id(ctx)


def _regime_call_count(ctx) -> int:
    """Test hook — return how many times `detect_regime` has been called
    for this ctx. Resets automatically when ctx is a fresh instance."""
    return _call_counts.get(_ctx_id(ctx), 0)


def detect_regime(ctx, bar_idx: int, *, compute_fn=None) -> Any:
    """Per-bar regime detection with memoization.

    Returns the regime label (int/str, impl-dependent). `compute_fn` is an
    optional callable that computes the regime — defaults to a stub that
    returns a bar-index-derived integer so tests see a stable non-None value.

    AC-Reg2: two strategies calling `detect_regime(ctx, 40)` compute ONCE.
    """
    cid = _ctx_id(ctx)
    key = (cid, bar_idx)
    if key in _cache:
        return _cache[key]
    # Cache miss — compute + record call
    _call_counts[cid] = _call_counts.get(cid, 0) + 1
    if compute_fn is None:
        # Default: a deterministic-but-non-trivial regime derived from bar_idx
        # (0=range, 1=up, 2=down, 3=crisis). Real strategies supply their own
        # compute_fn (e.g., v4.regimes.detect_crisis or s523c-style).
        result = (bar_idx // 24) % 4
    else:
        result = compute_fn(ctx, bar_idx)
    _cache[key] = result
    return result
