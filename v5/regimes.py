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

from collections import OrderedDict
from typing import Any, Optional


# Bounded per-ctx regime cache. Keying is (ctx_uid, bar_idx); entries
# are inserted in LRU order so reaching _CACHE_CAP evicts the oldest.
# Unbounded growth (pre-fix) would let the module-level cache accumulate
# entries forever across folds — the exact invariant v5/strategy_loader
# rejects in strategies. Per-ctx call counts live in a parallel dict
# that purges entries whose parent cache has been evicted entirely.
_CACHE_CAP: int = 1 << 16  # ~65k entries; ≈ 100 folds × 650 bars ceiling
_cache: "OrderedDict[tuple[int, int], Any]" = OrderedDict()

# Per-ctx call counter (test observability). Decays with _cache evictions.
_call_counts: dict[int, int] = {}


def _maybe_evict() -> None:
    """Drop oldest entries once the cache exceeds _CACHE_CAP. Also purge
    call-count entries for ctx_uids with no remaining cache rows so the
    counter dict doesn't grow forever in long-lived processes."""
    while len(_cache) > _CACHE_CAP:
        evicted_key, _ = _cache.popitem(last=False)
        evicted_ctx = evicted_key[0]
        if not any(k[0] == evicted_ctx for k in _cache):
            _call_counts.pop(evicted_ctx, None)


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
        # Move to end — LRU touch
        _cache.move_to_end(key)
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
    _maybe_evict()
    return result
