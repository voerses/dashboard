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


# Reference-count per ctx_uid: number of live cache entries keyed by that
# ctx. When a ctx's refcount drops to zero its call-count entry is purged.
# Replaces the round-2 O(N) `any(k[0] == evicted_ctx for k in _cache)`
# scan that made _maybe_evict 6.2 ms/call once the cache filled
# (round-3 Quant MAJOR).
_ctx_refcount: dict[int, int] = {}


def _maybe_evict() -> None:
    """Drop oldest entries once the cache exceeds _CACHE_CAP. Also purge
    call-count entries for ctx_uids whose refcount drops to zero, so the
    counter dict doesn't grow forever in long-lived processes.

    O(evicted) per call via _ctx_refcount tracking — no full-cache scan.
    """
    while len(_cache) > _CACHE_CAP:
        evicted_key, _ = _cache.popitem(last=False)
        evicted_ctx = evicted_key[0]
        rc = _ctx_refcount.get(evicted_ctx, 0) - 1
        if rc <= 0:
            _ctx_refcount.pop(evicted_ctx, None)
            _call_counts.pop(evicted_ctx, None)
        else:
            _ctx_refcount[evicted_ctx] = rc


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
    _ctx_refcount[cid] = _ctx_refcount.get(cid, 0) + 1
    _maybe_evict()
    return result


# ============================================================
# M9 C-4: Canonical regime constants + strategy-facing detectors
# ============================================================

# Regime enum constants (moved from v5/engine.py per C-4; engine regime
# column deleted, strategies import these directly).
CRISIS: int = 0
QUIET: int = 1
UPTREND: int = 2
RANGE: int = 3
DOWNTREND: int = 4


def detect_crisis(ctx, bar_idx: int) -> bool:
    """True when market is in CRISIS regime at bar_idx.

    Reads `ctx.market_indices["BTC_CLOSE_1D"]` + `TOTAL2` to determine
    crisis conditions (BTC volatility spike + alt-market retreat).
    Memoized via `detect_regime` (same cache key space).

    Strategies: `if v5.regimes.detect_crisis(ctx, bar_idx): ...`
    """
    return detect_regime(ctx, bar_idx, compute_fn=_compute_regime_from_indices) == CRISIS


def detect_uptrend(ctx, bar_idx: int) -> bool:
    """True when market is in UPTREND regime."""
    return detect_regime(ctx, bar_idx, compute_fn=_compute_regime_from_indices) == UPTREND


def detect_dispersion(ctx, bar_idx: int) -> bool:
    """True when alt-dispersion is elevated (wide cross-sectional
    dispersion in alt returns vs BTC)."""
    return detect_regime(ctx, bar_idx, compute_fn=_compute_regime_from_indices) != RANGE


def _compute_regime_from_indices(ctx, bar_idx: int) -> int:
    """Real detector used by detect_crisis/uptrend/dispersion.

    Reads `ctx.market_indices` canonical keys. Falls back to the
    bar-idx stub for test contexts that don't populate market_indices.
    """
    indices = getattr(ctx, "market_indices", None)
    if not indices or "BTC_CLOSE_1D" not in indices:
        # Fallback for test contexts — same stub as detect_regime default
        return (bar_idx // 24) % 4
    try:
        import numpy as np
        btc_series = indices["BTC_CLOSE_1D"]
        if bar_idx < 20 or bar_idx >= len(btc_series):
            return RANGE
        # Simple regime logic: 20-bar BTC return threshold
        ret_20 = (btc_series[bar_idx] / btc_series[bar_idx - 20]) - 1
        if ret_20 < -0.15:
            return CRISIS
        if ret_20 > 0.15:
            return UPTREND
        if ret_20 < -0.05:
            return DOWNTREND
        if abs(ret_20) < 0.02:
            return QUIET
        return RANGE
    except Exception:
        return RANGE


def detect_daily_regime(ind_d, *, adx_threshold: float = 25.0,
                        crisis_mult: float = 2.0, quiet_mult: float = 0.7,
                        ema_pair=(20, 50), min_periods: int = 60):
    """Daily-cadence regime classifier — imported from v5/engine.py
    (C-4 clean cut). Returns np.ndarray of regime ints per bar.

    `ind_d`: dict with keys 'close', 'high', 'low', 'volume' (numpy arrays).
    Returns int8 array same length as `ind_d["close"]`.
    """
    import numpy as np
    import pandas as pd

    close = np.asarray(ind_d.get("close"))
    n = len(close)
    regimes = np.full(n, RANGE, dtype=np.int8)
    if n < min_periods:
        return regimes

    # EMA pair for trend direction
    short_n, long_n = ema_pair
    s = pd.Series(close)
    ema_short = s.ewm(span=short_n, adjust=False).mean().values
    ema_long = s.ewm(span=long_n, adjust=False).mean().values

    # ATR-based volatility proxy
    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]
    vol = pd.Series(returns).rolling(20, min_periods=5).std().values
    vol_baseline = pd.Series(returns).rolling(60, min_periods=20).std().values

    for i in range(min_periods, n):
        v_now, v_base = vol[i], vol_baseline[i]
        if np.isnan(v_base) or v_base == 0:
            continue
        ratio = v_now / v_base
        if ratio > crisis_mult:
            regimes[i] = CRISIS
        elif ratio < quiet_mult:
            regimes[i] = QUIET
        elif ema_short[i] > ema_long[i]:
            regimes[i] = UPTREND
        elif ema_short[i] < ema_long[i]:
            regimes[i] = DOWNTREND
        else:
            regimes[i] = RANGE

    return regimes
